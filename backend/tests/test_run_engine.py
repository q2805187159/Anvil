from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from anvil import ApprovalDecision, RuntimeFeatureSet
from anvil.agents import RecentToolActivity, ThreadLifecycleStatus
from anvil.agents.lead_agent.context_files import reset_project_context_snapshot_cache
from anvil.agents.middlewares.summarization_middleware import SummarizationMiddleware
from anvil.agents.lead_agent.prompt import reset_prompt_snapshot_cache, reset_runtime_path_context_cache
from anvil.config import EffectiveConfig, SkillsConfig
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.runs import RunEngine, RunRequest
from anvil.runtime.serialization import serialize_messages
from anvil.runtime.store import StoreBackend, create_store
from anvil.skills import SkillsService
from anvil.sandbox import PathService
from anvil.config import ConfigLayer, ConfigLayerKind
from anvil.subagents import SubagentService
from fake_models import BindableFakeMessagesListChatModel


class StaticMemoryManager:
    def __init__(self, *, content: str = "", fingerprint: str = "memory-snapshot") -> None:
        self._snapshot = SimpleNamespace(content=content, fingerprint=fingerprint)

    def get_or_create_session_snapshot(self, *, thread_id: str) -> SimpleNamespace:
        return self._snapshot


def base_layers() -> list[ConfigLayer]:
    return [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    }
                },
            },
        )
    ]


def usage_layers() -> list[ConfigLayer]:
    return [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                        "context_window_tokens": 100000,
                        "auto_compact_threshold_tokens": 80000,
                    }
                },
                "token_usage": {
                    "enabled": True,
                    "pricing": {
                        "openai/gpt-5.4": {
                            "input_cost_per_million": 1.0,
                            "output_cost_per_million": 10.0,
                            "source": "test-pricing",
                        }
                    },
                },
            },
        )
    ]


def summarization_usage_layers() -> list[ConfigLayer]:
    layers = usage_layers()
    layers[0].data["summarization"] = {
        "enabled": True,
        "token_threshold": 20,
        "keep_recent_turns": 2,
    }
    return layers


def summarization_only_features() -> RuntimeFeatureSet:
    return RuntimeFeatureSet(
        thread_data=False,
        uploads=False,
        sandboxing=False,
        dangling_tool_calls=False,
        llm_error_handling=False,
        guardrails=False,
        sandbox_audit=False,
        tool_error_shaping=False,
        tool_output_budget=False,
        tool_visibility=False,
        deferred_tool_filter=False,
        plan_mode=False,
        title=False,
        token_usage=False,
        loop_detection=False,
        clarification=False,
        summarization=True,
        jit_context=False,
        compaction=False,
    )


def low_threshold_summarization_layers() -> list[ConfigLayer]:
    layers = usage_layers()
    layers[0].data["models"]["openai"]["auto_compact_threshold_tokens"] = 20
    layers.append(
        ConfigLayer(
            name="low-threshold-summarization",
            kind=ConfigLayerKind.REQUEST,
            data={
                "summarization": {
                    "enabled": True,
                    "token_threshold": 20,
                    "keep_recent_turns": 2,
                },
            },
        )
    )
    return layers


def context_layers() -> list[ConfigLayer]:
    layers = base_layers()
    layers[0].data["context_files"] = {
        "enabled": True,
        "recursive_agents": True,
        "recursive_names": ["AGENTS.md", "CODEX.md"],
        "max_files": 10,
        "max_chars": 2000,
    }
    return layers


def full_access_layers(contract_tmp_path) -> list[ConfigLayer]:
    return [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    }
                },
                "extensions": {
                    "mcp_servers": {
                        "github": {
                            "enabled": True,
                            "transport_kind": "stdio",
                            "connection_config": {
                                "inline_tools": [
                                    {
                                        "name": "ext_search",
                                        "display_name": "External Search",
                                        "capability_group": "research",
                                        "deferred": True,
                                    }
                                ]
                            },
                        }
                    }
                },
            },
        )
    ]


def budgeted_extension_layers(contract_tmp_path) -> list[ConfigLayer]:
    layers = full_access_layers(contract_tmp_path)
    layers[0].data["extensions"]["mcp_servers"]["github"]["connection_config"]["inline_tools"] = [
        {
            "name": "large_external",
            "display_name": "Large External",
            "capability_group": "research",
            "schema": {"properties": {f"field_{index}": {"type": "string"} for index in range(120)}},
        }
    ]
    layers[0].data["tool_visibility_budget"] = {
        "enabled": True,
        "visible_schema_token_budget": 20,
    }
    return layers


class WaitForDelegatedTaskChatModel(BaseChatModel):
    def __init__(self, *, final_message: str) -> None:
        super().__init__()
        object.__setattr__(self, "_step", 0)
        object.__setattr__(self, "_final_message", final_message)

    @property
    def _llm_type(self) -> str:
        return "wait-for-delegated-task"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:  # type: ignore[override]
        step = getattr(self, "_step")
        if step == 0:
            object.__setattr__(self, "_step", 1)
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "delegated_task",
                        "args": {"prompt": "Create /mnt/user-data/workspace/hello.md with hello"},
                        "id": "delegate_1",
                        "type": "tool_call",
                    }
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])
        if step == 1:
            task_id = None
            for message in reversed(messages):
                if getattr(message, "type", None) != "tool":
                    continue
                if getattr(message, "name", None) != "delegated_task":
                    continue
                try:
                    payload = json.loads(str(message.content))
                except Exception:
                    payload = {}
                task_id = payload.get("task_id")
                if task_id:
                    break
            object.__setattr__(self, "_step", 2)
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "subagent",
                        "args": {"action": "wait", "task_id": task_id, "timeout_seconds": 5},
                        "id": "subagent_wait_1",
                        "type": "tool_call",
                    }
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])
        result_payload = {}
        for message in reversed(messages):
            if getattr(message, "type", None) != "tool":
                continue
            if getattr(message, "name", None) != "subagent":
                continue
            try:
                result_payload = json.loads(str(message.content))
            except Exception:
                result_payload = {}
            break
        status = result_payload.get("status", "unknown")
        summary = result_payload.get("summary")
        error = result_payload.get("error")
        synthesized = getattr(self, "_final_message")
        if status == "completed" and summary:
            synthesized = f"Subagent completed: {summary}"
        elif status in {"failed", "timed_out", "cancelled", "interrupted"} and error:
            synthesized = f"Subagent failed: {error}"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=synthesized))])


class DelegationRoundTripChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "delegation-round-trip"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:  # type: ignore[override]
        human_messages = [
            str(getattr(message, "content", ""))
            for message in messages
            if getattr(message, "type", None) == "human"
        ]
        tool_messages = [message for message in messages if getattr(message, "type", None) == "tool"]
        latest_human = human_messages[-1] if human_messages else ""

        if latest_human.startswith("Create /mnt/user-data/workspace/hello.md with hello"):
            if not any(getattr(message, "name", None) == "write_file" for message in tool_messages):
                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "write_file",
                                        "args": {"path": "/mnt/user-data/workspace/hello.md", "content": "hello\n"},
                                        "id": "child_write_1",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        )
                    ]
                )
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="created hello.md"))])

        delegated_tool_message = next(
            (message for message in reversed(tool_messages) if getattr(message, "name", None) == "delegated_task"),
            None,
        )
        if delegated_tool_message is None:
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "delegated_task",
                                    "args": {
                                        "prompt": "Create /mnt/user-data/workspace/hello.md with hello",
                                        "requested_tool_names": ["write_file"],
                                    },
                                    "id": "delegate_round_trip_1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )

        subagent_tool_message = next(
            (message for message in reversed(tool_messages) if getattr(message, "name", None) == "subagent"),
            None,
        )
        if subagent_tool_message is None:
            payload = json.loads(str(delegated_tool_message.content))
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "subagent",
                                    "args": {"action": "wait", "task_id": payload["task_id"], "timeout_seconds": 5},
                                    "id": "delegate_round_trip_wait",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )

        payload = json.loads(str(subagent_tool_message.content))
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=f"Subagent completed: {payload.get('summary') or payload.get('status')}"
                    )
                )
            ]
        )


class FailingChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "failing-chat-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:  # type: ignore[override]
        raise RuntimeError("provider failed during skill-guided run")


def test_run_engine_succeeds_without_tools(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-1",
            user_message="say hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )

    assert result.thread_state.lifecycle.status == ThreadLifecycleStatus.COMPLETED
    assert result.thread_state.execution.active_model == "openai"
    assert result.metadata_view.thread_id == "thread-1"


def test_run_engine_persists_runtime_assembly_prompt_cache_delta(contract_tmp_path) -> None:
    reset_prompt_snapshot_cache(max_entries=8)
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    first = RunEngine().run(
        RunRequest(
            thread_id="thread-runtime-assembly-cache",
            user_message="say hello",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )
    second = RunEngine().run(
        RunRequest(
            thread_id="thread-runtime-assembly-cache",
            user_message="say hello again",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            request_context="turn-local runtime context",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello again")]
            ),
        )
    )

    first_cache_delta = first.thread_state.execution.runtime_assembly_snapshot["prompt"]["cache_delta"]
    second_cache_delta = second.thread_state.execution.runtime_assembly_snapshot["prompt"]["cache_delta"]
    second_prompt = second.thread_state.execution.runtime_assembly_snapshot["prompt"]
    persisted = checkpointer.get_thread_state("thread-runtime-assembly-cache")

    assert first_cache_delta["misses"] == 1
    assert first_cache_delta["writes"] == 1
    assert second_cache_delta["hits"] == 1
    assert second_cache_delta["misses"] == 0
    assert second_prompt["stable_prompt_tokens"] > 0
    assert second_prompt["volatile_prompt_tokens"] > 0
    assert second_prompt["stable_section_tokens"]["role_and_intent"] > 0
    assert second_prompt["volatile_section_tokens"]["request_context"] > 0
    assert persisted is not None
    assert persisted.execution.runtime_assembly_snapshot["prompt"]["cache_delta"]["hits"] == 1


def test_run_engine_persists_runtime_assembly_diff_between_runs(contract_tmp_path) -> None:
    reset_prompt_snapshot_cache(max_entries=8)
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    engine = RunEngine()
    first = engine.run(
        RunRequest(
            thread_id="thread-runtime-assembly-diff",
            user_message="say hello",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )
    second = engine.run(
        RunRequest(
            thread_id="thread-runtime-assembly-diff",
            user_message="say hello again",
            config_layers=base_layers(),
            feature_set=RuntimeFeatureSet(clarification=False),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello again")]
            ),
        )
    )

    first_diff = first.thread_state.execution.runtime_assembly_diff
    second_diff = second.thread_state.execution.runtime_assembly_diff

    assert first_diff["baseline"] == "none"
    assert first_diff["changed"] is False
    assert second_diff["baseline"] == "previous_run"
    assert second_diff["changed"] is True
    assert "middleware_names" in second_diff["changed_paths"]
    assert "enabled_feature_flags" in second_diff["changed_paths"]
    assert "disabled_feature_flags" in second_diff["changed_paths"]
    assert "cache_delta" not in repr(second_diff)
    assert "volatile_section_tokens" not in repr(second_diff)


def test_run_engine_persists_capability_assembly_diagnostics(contract_tmp_path) -> None:
    result = RunEngine().run(
        RunRequest(
            thread_id="thread-capability-diagnostics",
            user_message="search external code",
            config_layers=budgeted_extension_layers(contract_tmp_path),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(extensions=True),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )

    diagnostics = result.thread_state.execution.runtime_assembly_snapshot["capabilities"]["assembly_diagnostics"]
    assert diagnostics["visible_tool_count"] >= 1
    assert diagnostics["deferred_tool_count"] >= 1
    assert diagnostics["visible_schema_token_budget"] == 20
    assert diagnostics["schema_deferred_tool_count"] >= 1
    assert diagnostics["visible_schema_tokens"] >= 1
    assert diagnostics["deferred_by_source_kind"]["mcp"] >= 1
    assert diagnostics["assembly_stage_durations_ms"]["runtime_tools"] >= 0
    assert diagnostics["assembly_stage_durations_ms"]["final_bundle"] >= 0
    assert diagnostics["assembly_stage_durations_ms"]["total"] >= 0
    assert diagnostics["slowest_assembly_stage"] in diagnostics["assembly_stage_durations_ms"]
    assert diagnostics["slowest_assembly_stage"] != "total"
    assert diagnostics["slowest_assembly_stage_duration_ms"] == diagnostics["assembly_stage_durations_ms"][diagnostics["slowest_assembly_stage"]]
    assert diagnostics["skills_discovery_stage_durations_ms"]["total"] >= 0
    assert diagnostics["skills_discovery_manifest_count"] >= 0
    assert diagnostics["skills_discovery_enabled_count"] >= 0
    assert diagnostics["slowest_skills_discovery_stage"] in diagnostics["skills_discovery_stage_durations_ms"]
    assert diagnostics["slowest_skills_discovery_stage"] != "total"


def test_run_engine_persists_project_context_manifest(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path / "threads")
    workspace = path_service.thread_workspace_dir("thread-context-manifest")
    nested = workspace / "packages" / "api"
    nested.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Root context.\n", encoding="utf-8")
    (nested / "CODEX.md").write_text("Nested context.\n", encoding="utf-8")

    result = RunEngine().run(
        RunRequest(
            thread_id="thread-context-manifest",
            user_message="say hello",
            config_layers=context_layers(),
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )

    manifest = result.thread_state.prompt_snapshot.project_context_files
    assert result.thread_state.prompt_snapshot.project_context_fingerprint is not None
    assert {item["relative_path"] for item in manifest} == {"AGENTS.md", "packages/api/CODEX.md"}
    assert any(item["applies_to"] == "/mnt/user-data/workspace/packages/api" for item in manifest)


def test_run_engine_runtime_assembly_tracks_project_context_cache_status(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path / "threads")
    workspace = path_service.thread_workspace_dir("thread-context-cache")
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Root context.\n", encoding="utf-8")
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    engine = RunEngine()

    first = engine.run(
        RunRequest(
            thread_id="thread-context-cache",
            user_message="say hello",
            config_layers=context_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )
    second = engine.run(
        RunRequest(
            thread_id="thread-context-cache",
            user_message="say hello again",
            config_layers=context_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello again")]
            ),
        )
    )

    first_prompt = first.thread_state.execution.runtime_assembly_snapshot["prompt"]
    second_prompt = second.thread_state.execution.runtime_assembly_snapshot["prompt"]
    second_diff = second.thread_state.execution.runtime_assembly_diff
    assert first_prompt["project_context_cache_status"] == "miss"
    assert second_prompt["project_context_cache_status"] == "hit"
    assert second_prompt["project_context_fingerprint"] == first_prompt["project_context_fingerprint"]
    assert second_prompt["project_context_file_count"] == 1
    assert second_prompt["project_context_truncated_file_count"] == 0
    assert second_prompt["project_context_total_chars"] > 0
    assert "project_context_cache_status" not in repr(second_diff)
    assert "project_context_file_count" not in repr(second_diff)


def test_run_engine_runtime_assembly_tracks_runtime_path_cache_status(contract_tmp_path) -> None:
    reset_runtime_path_context_cache(max_entries=8)
    path_service = PathService(contract_tmp_path / "threads")
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    engine = RunEngine()

    first = engine.run(
        RunRequest(
            thread_id="thread-runtime-path-cache",
            user_message="say hello",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )
    second = engine.run(
        RunRequest(
            thread_id="thread-runtime-path-cache",
            user_message="say hello again",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello again")]
            ),
        )
    )

    first_prompt = first.thread_state.execution.runtime_assembly_snapshot["prompt"]
    second_prompt = second.thread_state.execution.runtime_assembly_snapshot["prompt"]
    second_diff = second.thread_state.execution.runtime_assembly_diff
    assert first_prompt["runtime_path_cache_status"] == "miss"
    assert second_prompt["runtime_path_cache_status"] == "hit"
    assert second_prompt["runtime_path_fingerprint"] == first_prompt["runtime_path_fingerprint"]
    assert second_prompt["runtime_path_root_count"] >= 1
    assert second_prompt["runtime_path_host_bridge_count"] >= 0
    assert "runtime_path_cache_status" not in repr(second_diff)
    assert "runtime_path_root_count" not in repr(second_diff)


def test_run_engine_records_failure_feedback_for_mentioned_workspace_skill(
    contract_tmp_path,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    governance_root = contract_tmp_path / "governance"
    config_layers = [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    }
                },
                "skills_config": {
                    "enabled": True,
                    "governance_root": str(governance_root),
                },
            },
        )
    ]
    SkillsService().manage_curator(
        config=EffectiveConfig(
            skills_config=SkillsConfig(
                enabled=True,
                governance_root=str(governance_root),
            )
        ),
        action="create",
        skill_id="agent-auto-feedback",
        title="Agent Auto Feedback",
        summary="Exercise automatic runtime feedback.",
        body="Use when runtime failures should update skill feedback.",
    )

    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-skill-failure-feedback",
            user_message="Use $agent-auto-feedback and fail",
            request_context="Use $agent-auto-feedback for this run.",
            config_layers=config_layers,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(skills=True, capability_mentions=True),
            chat_model_override=FailingChatModel(),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.FAILED
    engine.wait_for_background_tasks(timeout_seconds=5)
    usage = SkillsService().curator.usage_snapshot(
        config=EffectiveConfig(
            skills_config=SkillsConfig(
                enabled=True,
                governance_root=str(governance_root),
            )
        )
    )
    item = usage["agent-auto-feedback"]
    assert item["use_count"] == 1
    assert item["feedback_count"] == 1
    assert item["failure_count"] == 1
    assert item["last_feedback"]["outcome"] == "failure"
    assert item["last_feedback"]["source"] == "runtime_failure"
    assert item["last_feedback"]["confidence"] == 0.7
    assert item["feedback_by_source"]["runtime_failure"] == 1
    assert item["confidence_totals"]["failure"] == 0.7
    assert "provider failed" in item["last_feedback"]["rationale"]


def test_run_engine_skips_success_feedback_without_visible_tool_evidence_for_mentioned_workspace_skill(
    contract_tmp_path,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    governance_root = contract_tmp_path / "governance"
    config_layers = [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    }
                },
                "skills_config": {
                    "enabled": True,
                    "governance_root": str(governance_root),
                },
            },
        )
    ]
    SkillsService().manage_curator(
        config=EffectiveConfig(
            skills_config=SkillsConfig(
                enabled=True,
                governance_root=str(governance_root),
            )
        ),
        action="create",
        skill_id="agent-auto-success",
        title="Agent Auto Success",
        summary="Exercise automatic runtime success feedback.",
        body="Use when successful runtime completion should update skill feedback.",
    )

    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-skill-success-feedback",
            user_message="Use $agent-auto-success and complete",
            request_context="Use $agent-auto-success for this run.",
            config_layers=config_layers,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(skills=True, capability_mentions=True),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="completed with the loaded skill")]
            ),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    engine.wait_for_background_tasks(timeout_seconds=5)
    assert result.runtime.context.capability_bundle.mentioned_skill_ids == ("agent-auto-success",)
    usage = SkillsService().curator.usage_snapshot(
        config=EffectiveConfig(
            skills_config=SkillsConfig(
                enabled=True,
                governance_root=str(governance_root),
            )
        )
    )
    item = usage["agent-auto-success"]
    assert item["use_count"] == 1
    assert item["feedback_count"] == 0
    assert item["success_count"] == 0


def test_run_engine_does_not_record_success_feedback_without_loaded_workspace_skill(
    contract_tmp_path,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    governance_root = contract_tmp_path / "governance"
    config_layers = [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    }
                },
                "skills_config": {
                    "enabled": True,
                    "governance_root": str(governance_root),
                },
            },
        )
    ]
    SkillsService().manage_curator(
        config=EffectiveConfig(
            skills_config=SkillsConfig(
                enabled=True,
                governance_root=str(governance_root),
            )
        ),
        action="create",
        skill_id="agent-not-loaded",
        title="Agent Not Loaded",
        summary="Exercise success feedback isolation.",
        body="Use when success feedback should require an actual skill mention.",
    )

    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-no-skill-success-feedback",
            user_message="complete without mentioning any skill",
            config_layers=config_layers,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(skills=True),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="completed without skill content")]
            ),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    engine.wait_for_background_tasks(timeout_seconds=5)
    usage = SkillsService().curator.usage_snapshot(
        config=EffectiveConfig(
            skills_config=SkillsConfig(
                enabled=True,
                governance_root=str(governance_root),
            )
        )
    )
    assert usage["agent-not-loaded"]["use_count"] == 0
    assert usage["agent-not-loaded"]["feedback_count"] == 0


def test_run_engine_records_success_feedback_only_with_visible_tool_evidence_for_mentioned_workspace_skill(
    contract_tmp_path,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    governance_root = contract_tmp_path / "governance"
    config_layers = [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    }
                },
                "skills_config": {
                    "enabled": True,
                    "governance_root": str(governance_root),
                },
            },
        )
    ]
    SkillsService().manage_curator(
        config=EffectiveConfig(
            skills_config=SkillsConfig(
                enabled=True,
                governance_root=str(governance_root),
            )
        ),
        action="create",
        skill_id="agent-tool-success",
        title="Agent Tool Success",
        summary="Exercise automatic runtime success feedback with tool evidence.",
        body="Use when successful runtime completion should update skill feedback after visible tool evidence.",
        allowed_tools=["list_dir"],
    )
    path_service = PathService(contract_tmp_path / "threads")
    workspace = path_service.thread_workspace_dir("thread-skill-tool-success-feedback")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "a.txt").write_text("skill evidence\n", encoding="utf-8")

    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-skill-tool-success-feedback",
            user_message="Use $agent-tool-success and inspect a file",
            request_context="Use $agent-tool-success for this run.",
            config_layers=config_layers,
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(skills=True, capability_mentions=True),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="I will inspect the file first.",
                        tool_calls=[
                            {
                                "name": "list_dir",
                                "args": {"path": "/mnt/user-data/workspace"},
                                "id": "call_list_for_skill_feedback",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="completed with visible evidence"),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    engine.wait_for_background_tasks(timeout_seconds=5)
    usage = SkillsService().curator.usage_snapshot(
        config=EffectiveConfig(
            skills_config=SkillsConfig(
                enabled=True,
                governance_root=str(governance_root),
            )
        )
    )
    item = usage["agent-tool-success"]
    assert item["use_count"] == 1
    assert item["feedback_count"] == 1
    assert item["success_count"] == 1
    assert item["last_feedback"]["outcome"] == "success"
    assert item["last_feedback"]["source"] == "runtime_success"
    assert item["last_feedback"]["confidence"] == 0.4
    assert item["feedback_by_source"]["runtime_success"] == 1
    assert "visible tool evidence" in item["last_feedback"]["rationale"]


def test_run_engine_learns_successful_visible_tool_procedure_candidate(
    contract_tmp_path,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    governance_root = contract_tmp_path / "governance"
    config_layers = [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    }
                },
                "skills_config": {
                    "enabled": True,
                    "governance_root": str(governance_root),
                },
            },
        )
    ]
    path_service = PathService(contract_tmp_path / "threads")
    workspace = path_service.thread_workspace_dir("thread-procedure-learning")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "a.txt").write_text("procedure evidence\n", encoding="utf-8")

    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-procedure-learning",
            user_message="Read a file, verify the result, and save this reusable workflow as a procedure.",
            config_layers=config_layers,
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(skills=True, capability_mentions=True),
            promoted_capabilities=("list_dir", "search_files"),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="I will inspect the file first.",
                        tool_calls=[
                            {
                                "name": "list_dir",
                                "args": {"path": "/mnt/user-data/workspace"},
                                "id": "call_list_for_proc",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(
                        content="Now I will search for related evidence.",
                        tool_calls=[
                            {
                                "name": "search_files",
                                "args": {"path": "/mnt/user-data/workspace", "pattern": "procedure"},
                                "id": "call_search_for_proc",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="The file was read and related evidence was found."),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    engine.wait_for_background_tasks(timeout_seconds=5)
    procedures = SkillsService().manage_curator(
        config=EffectiveConfig(
            skills_config=SkillsConfig(
                enabled=True,
                governance_root=str(governance_root),
            )
        ),
        action="procedures",
    )
    assert procedures["counts"]["total"] == 1
    candidate = procedures["items"][0]
    assert candidate["frequency"] == 1
    assert set(candidate["allowed_tools"]) == {"list_dir", "search_files"}
    assert candidate["last_outcome"]["source"] == "runtime_success"
    assert candidate["outcome_health"]["success_count"] == 1
    assert candidate["quality"]["tool_count"] == 2
    assert candidate["quality"]["quality_score"] < 0.58
    assert candidate["promotion_readiness"]["promotable"] is False
    assert "needs_repetition" in candidate["promotion_readiness"]["blockers"]
    assert "weak_quality" in candidate["promotion_readiness"]["blockers"]
    assert candidate["source_refs"][0].startswith("thread:thread-procedure-learning/run:")
    assert "Narrow the target files first" in "\n".join(candidate["steps"])


def test_run_engine_procedure_learning_classifies_code_impact_as_focused_analysis(contract_tmp_path) -> None:
    engine = RunEngine()

    assert engine._procedure_step_for_tool("code_impact") == "Use focused code analysis before broad file reads or edits."


def test_run_engine_chat_execution_mode_disables_visible_tools(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-chat",
            user_message="say hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            execution_mode="chat",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )

    assert result.thread_state.execution.execution_mode.value == "chat"
    assert result.thread_state.capabilities.visible_tool_names == []
    assert result.thread_state.execution.recent_tool_activity == []


def test_run_engine_full_access_promotes_deferred_tools(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-full-access",
            user_message="say hello",
            config_layers=full_access_layers(contract_tmp_path),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(extensions=True),
            execution_mode="full_access",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )

    assert result.thread_state.execution.execution_mode.value == "full_access"
    assert "ext_search" in result.thread_state.capabilities.visible_tool_names


def test_run_engine_succeeds_with_controlled_file_tool(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-2",
            user_message="list files",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_dir",
                                "args": {"path": "/mnt/user-data/workspace"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status == ThreadLifecycleStatus.COMPLETED
    assert result.thread_state.capabilities.visible_tool_names == [
        "ask_clarification",
        "browser_back",
        "browser_cdp",
        "browser_click",
        "browser_close",
        "browser_console",
        "browser_dialog",
        "browser_get_images",
        "browser_navigate",
        "browser_press",
        "browser_screenshot",
        "browser_scroll",
        "browser_snapshot",
        "browser_type",
        "browser_vision",
        "calendar_create_event",
        "calendar_delete_event",
        "calendar_free_busy",
        "calendar_list_events",
        "calendar_update_event",
        "capability_search",
        "code_definition",
        "code_doc_graph",
        "code_file_summary",
        "code_focus",
        "code_health",
        "code_impact",
        "code_map",
        "code_pattern_scan",
        "code_references",
        "code_security_scan",
        "code_semantic_index",
        "code_symbol_search",
        "code_symbols",
        "delete_path",
        "export_document",
        "extract_document",
        "file_info",
        "glob_files",
        "gmail_create_draft",
        "gmail_labels",
        "gmail_read",
        "gmail_search",
        "gmail_send",
        "grep_files",
        "image_search",
        "js_repl",
        "list_dir",
        "make_dir",
        "mcp_get_prompt",
        "mcp_list_prompts",
        "mcp_list_resources",
        "mcp_manage",
        "mcp_read_resource",
        "memory",
        "memory_trace",
        "move_path",
        "patch_file",
        "process",
        "read_file",
        "run_command",
        "scheduled_task",
        "search_files",
        "session_search",
        "skill_content",
        "skill_files",
        "skill_manage",
        "skill_read_file",
        "skill_view",
        "skills_list",
        "speech_to_text",
        "text_to_speech",
        "tool_catalog",
        "tool_view",
        "toolset_catalog",
        "toolset_view",
        "web_crawl",
        "web_extract",
        "web_fetch",
        "web_search",
        "write_file",
        "write_todos",
    ]
    assert len(result.thread_state.conversation.messages) >= 3


def test_run_engine_aggregates_model_usage_across_tool_loop(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-token-usage",
            user_message="list files",
            config_layers=usage_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_dir",
                                "args": {"path": "/mnt/user-data/workspace"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                        usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
                    ),
                    AIMessage(
                        content="done",
                        usage_metadata={"input_tokens": 120, "output_tokens": 20, "total_tokens": 140},
                    ),
                ]
            ),
        )
    )

    usage = result.thread_state.execution.token_usage
    context = result.thread_state.execution.context_window_usage
    assert usage["request_count"] == 2
    assert usage["input_tokens"] == 220
    assert usage["output_tokens"] == 30
    assert usage["total_tokens"] == 250
    assert usage["total"]["input_tokens"] == 220
    assert usage["last"]["input_tokens"] == 120
    assert usage["last"]["output_tokens"] == 20
    assert usage["cost_status"] == "estimated"
    assert usage["estimated_cost_usd"] == 0.00052
    assert context["request_count"] == 2
    assert context["total_tokens"] == 250
    assert context["context_tokens"] == context["estimated_context_tokens"]
    assert context["context_source"] == "estimated"
    assert context["input_tokens"] == 220
    assert context["output_tokens"] == 30
    assert context["estimated_cost_usd"] == 0.00052
    assert context["compact_status"] == "below_threshold"
    assert context["compaction_level"] == 0
    assert context["compaction_level_label"] == "none"
    assert context["estimated_context_tokens"] >= 1
    assert context["message_tokens"] >= 1
    assert context["system_tokens"] >= 1
    assert context["tool_schema_tokens"] >= 1
    assert context["context_breakdown"]["messages"] == context["message_tokens"]
    assert context["context_breakdown"]["system"] == context["system_tokens"]
    assert context["context_breakdown"]["tool_schemas"] == context["tool_schema_tokens"]
    assert context["context_breakdown_percentages"]["messages"] > 0
    assert context["dominant_context_category"] in context["context_breakdown"]
    assert context["cache_hit_ratio"] is None
    assert context["cache_savings_tokens"] is None
    assert context["autocompact_buffer_tokens"] == max(80000 - context["context_tokens"], 0)
    assert context["free_space_tokens"] == max(100000 - context["context_tokens"], 0)


def test_run_engine_keeps_context_estimate_separate_from_provider_usage_fields(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-context-estimate-only",
            user_message="hello",
            config_layers=usage_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello back")]),
        )
    )

    usage = result.thread_state.execution.token_usage
    context = result.thread_state.execution.context_window_usage
    assert usage == {}
    assert context["context_tokens"] >= 1
    assert context["context_source"] == "estimated"
    assert context["input_tokens"] is None
    assert context["output_tokens"] is None
    assert context["total_tokens"] is None
    assert context["compact_status"] == "below_threshold"


def test_run_engine_context_window_reports_cache_hit_rate(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-cache-token-usage",
            user_message="hello",
            config_layers=usage_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello back")]),
        )
    )
    usage = {
        "total": {
            "input_tokens": 120,
            "output_tokens": 20,
            "total_tokens": 140,
            "cache_read_tokens": 90,
            "cache_write_tokens": 30,
        },
        "request_count": 1,
    }

    context = engine._build_context_window_usage(token_usage=usage, runtime=result.runtime, messages=[])  # noqa: SLF001

    assert context["cache_read_tokens"] == 90
    assert context["cache_write_tokens"] == 30
    assert context["cache_hit_ratio"] == 0.75
    assert context["cache_savings_tokens"] == 90


def test_run_engine_context_window_reads_nested_token_usage_breakdowns(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-nested-token-usage",
            user_message="hello",
            config_layers=usage_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello back")]),
        )
    )
    usage = {
        "total": {"input_tokens": 33, "output_tokens": 7, "total_tokens": 40},
        "last": {"input_tokens": 33, "output_tokens": 7, "total_tokens": 40},
        "request_count": 1,
    }

    context = engine._build_context_window_usage(token_usage=usage, runtime=result.runtime, messages=[])  # noqa: SLF001

    assert context["input_tokens"] == 33
    assert context["output_tokens"] == 7
    assert context["total_tokens"] == 40
    assert context["context_tokens"] == context["estimated_context_tokens"]
    assert context["context_source"] == "estimated"


def test_run_engine_context_window_falls_back_to_last_input_not_cumulative_total(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-provider-fallback-context",
            user_message="hello",
            config_layers=usage_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello back")]),
        )
    )
    usage = {
        "total": {"input_tokens": 80_000, "output_tokens": 2_000, "total_tokens": 82_000},
        "last": {"input_tokens": 700, "output_tokens": 20, "total_tokens": 720},
        "input_tokens": 80_000,
        "output_tokens": 2_000,
        "total_tokens": 82_000,
        "request_count": 12,
    }

    context = engine._build_context_window_usage(token_usage=usage, runtime=result.runtime, messages=None)  # noqa: SLF001

    assert context["context_tokens"] == 700
    assert context["estimated_context_tokens"] is None
    assert context["total_tokens"] == 82_000
    assert context["context_source"] == "provider_last_input"


def test_run_engine_context_window_counts_turn_injection_sections(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-turn-injection-context",
            user_message="hello",
            config_layers=usage_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            execution_mode="chat",
            memory_manager=StaticMemoryManager(),
            request_context="Turn-local request note that should be visible to the model.",
            upload_context="<attached_files>\n- /mnt/user-data/uploads/spec.md\n</attached_files>",
            approval_context="The user approved this plan for the current turn.",
            is_plan_mode=True,
            promoted_capabilities=("search_files", "read_file"),
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello back")]),
        )
    )

    context = result.thread_state.execution.context_window_usage

    assert context["context_source"] == "estimated"
    assert context["context_breakdown"]["request_context"] >= 1
    assert context["context_breakdown"]["upload_context"] >= 1
    assert context["context_breakdown"]["approval_context"] >= 1
    assert context["context_breakdown"]["plan_context"] >= 1
    assert context["context_breakdown"]["promoted_capabilities"] >= 1
    assert context["context_breakdown_percentages"]["request_context"] > 0
    assert context["dominant_context_category"] in context["context_breakdown"]


def test_run_engine_context_window_counts_middleware_injected_context(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-middleware-context",
            user_message="hello",
            config_layers=usage_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            execution_mode="chat",
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello back")]),
        )
    )
    result.runtime.context.memory_context = "<memory_context>\nUser prefers concise reports.\n</memory_context>"
    result.runtime.context.summary_context = "Earlier turns discussed the Anvil runtime contract."
    result.runtime.context.todo_context = "- [PENDING] Verify context accounting (todo-1)"
    result.runtime.context.view_image_context = (
        "Images returned by view_image are attached below for visual analysis."
    )

    context = engine._build_context_window_usage(  # noqa: SLF001 - regression covers runtime context accounting.
        token_usage={},
        runtime=result.runtime,
        messages=[],
    )

    assert context["context_breakdown"]["memory_context"] >= 1
    assert context["context_breakdown"]["conversation_summary"] >= 1
    assert context["context_breakdown"]["todo_state"] >= 1
    assert context["context_breakdown"]["view_image_context"] >= 1
    assert context["context_breakdown_percentages"]["memory_context"] > 0
    assert context["estimated_context_tokens"] == context["context_tokens"]


def test_run_engine_context_window_uses_compacted_message_window(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-compacted-context-window",
            user_message="hello",
            config_layers=summarization_usage_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            execution_mode="chat",
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello back")]),
        )
    )
    long_prefix = "older context " * 400
    short_tail = "recent turn"
    messages = [
        HumanMessage(content=long_prefix),
        AIMessage(content=long_prefix),
        HumanMessage(content=short_tail),
        AIMessage(content=short_tail),
    ]
    result.runtime.context.summary_context = "Compacted summary for older turns."

    compacted = engine._build_context_window_usage(  # noqa: SLF001 - regression covers compacted context accounting.
        token_usage={},
        runtime=result.runtime,
        messages=messages,
    )

    assert compacted["context_breakdown"]["conversation_summary"] >= 1
    assert compacted["context_breakdown"]["messages"] < _estimated_tokens_for_text(long_prefix)
    assert compacted["compact_status"] == "compacted"
    assert compacted["compaction_level"] == 1
    assert compacted["compaction_level_label"] == "summary"
    assert compacted["compaction_summary_tokens"] == compacted["context_breakdown"]["conversation_summary"]
    assert compacted["compaction_keep_recent_turns"] == 2
    result.runtime.context.summary_context = None
    full = engine._build_context_window_usage(  # noqa: SLF001
        token_usage={},
        runtime=result.runtime,
        messages=messages,
    )
    assert full["context_breakdown"]["messages"] > compacted["context_breakdown"]["messages"]


def test_run_engine_reports_recursive_compaction_metadata(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-recursive-compaction",
            user_message="hello",
            config_layers=summarization_usage_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            execution_mode="chat",
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello back")]),
        )
    )
    messages = [
        HumanMessage(content="older context " * 300),
        AIMessage(content="older response " * 300),
        HumanMessage(content="recent turn"),
        AIMessage(content="recent answer"),
    ]
    result.runtime.context.summary_context = "Prior summary."
    result.runtime.context.summarization_triggered = True
    result.runtime.context.compaction_level = 2
    result.runtime.context.compaction_level_label = "recursive_summary"
    result.runtime.context.compaction_reason = "token_threshold_exceeded"
    result.runtime.context.compaction_input_tokens = 3200
    result.runtime.context.compaction_summary_tokens = 40
    result.runtime.context.compaction_keep_recent_turns = 2

    context = engine._build_context_window_usage(  # noqa: SLF001 - regression covers level telemetry.
        token_usage={},
        runtime=result.runtime,
        messages=messages,
    )

    assert context["compact_status"] == "compacted"
    assert context["compaction_level"] == 2
    assert context["compaction_level_label"] == "recursive_summary"
    assert context["compaction_reason"] == "token_threshold_exceeded"
    assert context["compaction_input_tokens"] == 3200
    assert context["compaction_summary_tokens"] == 40
    assert context["compaction_keep_recent_turns"] == 2
    assert context["compaction_savings_tokens"] == max(3200 - context["context_tokens"], 0)


def test_summarization_middleware_records_recursive_compaction_level(contract_tmp_path) -> None:
    engine = RunEngine()
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    first = engine.run(
        RunRequest(
            thread_id="thread-real-recursive-compaction",
            user_message="first turn " + ("older context " * 80),
            config_layers=low_threshold_summarization_layers(),
            feature_set=summarization_only_features(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            execution_mode="chat",
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="first response")]),
        )
    )
    state = first.thread_state.model_copy(deep=True)
    state.conversation.summary = "Existing summary from the previous compacted turn."
    archived_messages = [
        HumanMessage(content="archived older turn " + ("older context " * 120)),
        AIMessage(content="archived older answer " + ("older answer " * 120)),
    ]
    state.conversation.messages.extend(
        json.loads(json.dumps(serialize_messages(archived_messages), ensure_ascii=False))
    )
    checkpointer.put_thread_state(state)

    second = engine.run(
        RunRequest(
            thread_id="thread-real-recursive-compaction",
            user_message="second turn " + ("more older context " * 220),
            config_layers=low_threshold_summarization_layers(),
            feature_set=summarization_only_features(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            execution_mode="chat",
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="second response")]),
        )
    )

    context = second.thread_state.execution.context_window_usage
    assert second.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert context["compact_status"] == "compacted"
    assert context["summarization_triggered"] is True
    assert context["compaction_level"] == 2
    assert context["compaction_level_label"] == "recursive_summary"
    assert context["compaction_reason"] == "token_threshold_exceeded"
    assert context["compaction_input_tokens"] >= 20
    assert context["compaction_summary_tokens"] >= 1
    assert context["compaction_keep_recent_turns"] == 2
    diagnostics = context["compaction_diagnostics"]
    assert diagnostics["summary_source"] in {"fallback", "model"}
    assert diagnostics["archived_message_count"] >= 1
    assert diagnostics["serialized_tokens"] >= 1
    assert diagnostics["compaction_level"] == 2
    assert second.thread_state.archived_summaries[-1].diagnostics["compaction_level"] == 2


def test_summarization_middleware_serializes_tools_and_images_for_compaction_diagnostics(contract_tmp_path) -> None:
    engine = RunEngine()
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    state = engine._create_initial_thread_state(  # noqa: SLF001 - builds a durable thread fixture.
        RunRequest(
            thread_id="thread-structured-compaction",
            user_message="seed",
            config_layers=[
                *usage_layers(),
                ConfigLayer(
                    name="structured-compaction-model-threshold",
                    kind=ConfigLayerKind.REQUEST,
                    data={
                        "models": {
                            "openai": {
                                "auto_compact_threshold_tokens": 20,
                            },
                        },
                    },
                ),
                ConfigLayer(
                    name="structured-compaction",
                    kind=ConfigLayerKind.REQUEST,
                    data={
                        "summarization": {
                            "enabled": True,
                            "token_threshold": 20,
                            "keep_recent_turns": 1,
                        },
                    },
                ),
            ],
            feature_set=summarization_only_features(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            execution_mode="chat",
        )
    )
    archived_messages = [
        HumanMessage(
            content=[
                {"type": "text", "text": "Please inspect /mnt/user-data/workspace/app.py"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            ]
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "/mnt/user-data/workspace/app.py"},
                    "id": "tool-read-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content="line 1\n" + ("tool output detail " * 300),
            name="read_file",
            tool_call_id="tool-read-1",
        ),
    ]
    state.conversation.messages = json.loads(json.dumps(serialize_messages(archived_messages), ensure_ascii=False))
    checkpointer.put_thread_state(state)

    result = engine.run(
        RunRequest(
            thread_id="thread-structured-compaction",
            user_message="continue " + ("new context " * 80),
            config_layers=[
                *usage_layers(),
                ConfigLayer(
                    name="structured-compaction-model-threshold",
                    kind=ConfigLayerKind.REQUEST,
                    data={
                        "models": {
                            "openai": {
                                "auto_compact_threshold_tokens": 20,
                            },
                        },
                    },
                ),
                ConfigLayer(
                    name="structured-compaction",
                    kind=ConfigLayerKind.REQUEST,
                    data={
                        "summarization": {
                            "enabled": True,
                            "token_threshold": 20,
                            "keep_recent_turns": 1,
                        },
                    },
                ),
            ],
            feature_set=summarization_only_features(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            execution_mode="chat",
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="continued")]),
        )
    )

    diagnostics = result.thread_state.execution.context_window_usage["compaction_diagnostics"]
    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert diagnostics["summary_source"] in {"fallback", "model"}
    assert diagnostics["tool_call_count"] >= 1
    assert diagnostics["tool_result_count"] >= 1
    assert diagnostics["pruned_tool_result_count"] >= 1
    assert diagnostics["truncated_message_count"] >= 1
    assert diagnostics["serialized_chars"] >= 1
    assert result.thread_state.execution.runtime_assembly_snapshot["compaction_diagnostics"]["tool_call_count"] >= 1
    transcript, image_diagnostics = SummarizationMiddleware()._serialize_for_summary(  # noqa: SLF001
        [
            HumanMessage(
                content=[
                    {"type": "text", "text": "Use this uploaded image."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                ]
            )
        ],
        keep_recent_turns=1,
    )
    assert image_diagnostics["image_block_count"] == 1
    assert "[image block: image/png data omitted]" in transcript


def _estimated_tokens_for_text(value: str) -> int:
    return max((len(value) + 3) // 4, 1)


def test_run_engine_marks_memory_source_polluted_after_external_tool_activity(contract_tmp_path) -> None:
    engine = RunEngine()
    state = engine._create_initial_thread_state(  # noqa: SLF001 - regression covers private runtime handoff.
        RunRequest(
            thread_id="thread-web",
            user_message="search the web",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
        )
    )
    state.execution.recent_tool_activity = [
        RecentToolActivity(
            name="web_search",
            source_kind="builtin",
            source_id="core",
            capability_group="research",
            tool_call_id="call-web",
            status="completed",
        )
    ]

    metadata = engine._memory_source_metadata(state)  # noqa: SLF001 - regression covers private runtime handoff.

    assert metadata["pollution_markers"][0]["tool_name"] == "web_search"
    assert "external information tool" in metadata["pollution_markers"][0]["reason"]


def test_run_engine_supports_root_directory_discovery(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-root-discovery",
            user_message="discover the available runtime directories",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_dir",
                                "args": {"path": "/mnt/user-data"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status == ThreadLifecycleStatus.COMPLETED
    tool_messages = [message for message in result.thread_state.conversation.messages if message.get("role") == "tool"]
    assert tool_messages
    assert '["outputs", "uploads", "workspace"]' in str(tool_messages[-1].get("content"))


def test_run_engine_requests_approval_for_sensitive_tool_without_approval_context(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-3",
            user_message="write a file",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "write_file",
                                "args": {"path": "/mnt/user-data/workspace/example.txt", "content": "hello"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status == ThreadLifecycleStatus.AWAITING_APPROVAL
    assert result.thread_state.approvals.pending_approval == ApprovalDecision.NEEDS_USER_APPROVAL
    assert "filesystem_write" in (result.thread_state.lifecycle.last_error or "")


def test_run_engine_allows_sensitive_tool_with_approval_context(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-4",
            user_message="write a file",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            approval_context="user approved filesystem write for this turn",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "write_file",
                                "args": {"path": "/mnt/user-data/workspace/example.txt", "content": "hello"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status == ThreadLifecycleStatus.COMPLETED
    assert result.thread_state.approvals.pending_approval is None


def test_run_engine_persists_standardized_session_approval_grant_after_resume(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)

    first_result = engine.run(
        RunRequest(
            thread_id="thread-session-approval",
            user_message="write a file",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "write_file",
                                "args": {"path": "/mnt/user-data/workspace/a.txt", "content": "hello"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                ]
            ),
        )
    )
    assert first_result.thread_state.lifecycle.status == ThreadLifecycleStatus.AWAITING_APPROVAL
    assert first_result.thread_state.approvals.approval_request is not None

    resumed = engine.resume_approval(
        thread_id="thread-session-approval",
        config_layers=base_layers(),
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        approval_context="approved, do not ask again in this session",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "/mnt/user-data/workspace/a.txt", "content": "hello"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        ),
    )
    assert resumed.thread_state.lifecycle.status == ThreadLifecycleStatus.COMPLETED
    assert resumed.thread_state.approvals.pending_approval is None
    assert resumed.thread_state.approvals.session_approval_grants == ["filesystem_write"]

    follow_up = engine.run(
        RunRequest(
            thread_id="thread-session-approval",
            user_message="write another file",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            approval_session_grants=tuple(resumed.thread_state.approvals.session_approval_grants),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "write_file",
                                "args": {"path": "/mnt/user-data/workspace/b.txt", "content": "world"},
                                "id": "call_2",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="done again"),
                ]
            ),
        )
    )

    assert follow_up.thread_state.lifecycle.status == ThreadLifecycleStatus.COMPLETED
    assert follow_up.thread_state.approvals.pending_approval is None
    assert follow_up.thread_state.approvals.session_approval_grants == ["filesystem_write"]


def test_run_engine_returns_corrective_filesystem_tool_error_for_invalid_root(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-invalid-path",
            user_message="list the current directory with dot notation",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_dir",
                                "args": {"path": "."},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status == ThreadLifecycleStatus.COMPLETED
    tool_messages = [message for message in result.thread_state.conversation.messages if message.get("role") == "tool"]
    assert tool_messages
    content = str(tool_messages[-1].get("content"))
    assert "Use /mnt/user-data for discovery" in content
    assert "/mnt/user-data/workspace" in content
    assert "Do not use '.', '/', or unlisted host paths" in content


def test_run_engine_can_wait_for_subagent_and_continue_with_result(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")

    def runner_factory(*, task, prompt, config_result, allowed_tool_names):
        def _runner() -> str:
            output = path_service.thread_workspace_dir(task.parent_thread_id) / "hello.md"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("hello\n", encoding="utf-8")
            return "created hello.md"

        return _runner

    service = SubagentService(default_runner_factory=runner_factory)
    result = engine.run(
        RunRequest(
            thread_id="thread-subagent-success",
            user_message="use a subagent to create hello.md",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(subagents=True),
            subagent_service=service,
            chat_model_override=WaitForDelegatedTaskChatModel(final_message="subagent status unavailable"),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    tool_messages = [message for message in result.thread_state.conversation.messages if message.get("role") == "tool"]
    subagent_wait = next(message for message in tool_messages if message.get("name") == "subagent")
    payload = json.loads(str(subagent_wait.get("content")))
    assert payload["status"] == "completed"
    assert payload["summary"] == "created hello.md"
    assert "Subagent completed: created hello.md" in str(result.thread_state.conversation.messages[-1]["content"])
    assert (path_service.thread_workspace_dir("thread-subagent-success") / "hello.md").read_text(encoding="utf-8") == "hello\n"


def test_run_engine_can_wait_for_subagent_failure_and_analyze_reason(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")

    def failing_runner_factory(*, task, prompt, config_result, allowed_tool_names):
        def _runner() -> str:
            raise RuntimeError("disk quota exceeded while writing hello.md")

        return _runner

    service = SubagentService(default_runner_factory=failing_runner_factory)
    result = engine.run(
        RunRequest(
            thread_id="thread-subagent-failure",
            user_message="use a subagent to create hello.md and analyze failure",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(subagents=True),
            subagent_service=service,
            chat_model_override=WaitForDelegatedTaskChatModel(final_message="subagent status unavailable"),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    tool_messages = [message for message in result.thread_state.conversation.messages if message.get("role") == "tool"]
    subagent_wait = next(message for message in tool_messages if message.get("name") == "subagent")
    payload = json.loads(str(subagent_wait.get("content")))
    assert payload["status"] == "failed"
    assert "disk quota exceeded" in payload["error"]
    assert "Subagent failed: disk quota exceeded" in str(result.thread_state.conversation.messages[-1]["content"])


def test_run_engine_default_subagent_runner_requests_parent_approval_for_guarded_child_tools(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")

    result = engine.run(
        RunRequest(
            thread_id="thread-default-subagent-tools",
            user_message="create hello.md using a subagent",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(subagents=True),
            chat_model_override=DelegationRoundTripChatModel(),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.AWAITING_APPROVAL
    assert result.thread_state.approvals.pending_approval == ApprovalDecision.NEEDS_USER_APPROVAL
    assert "delegated" in (result.thread_state.lifecycle.last_error or "")
    assert "filesystem_write" in (result.thread_state.lifecycle.last_error or "")


def test_run_engine_default_subagent_runner_can_create_file_after_parent_approval(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")

    result = engine.run(
        RunRequest(
            thread_id="thread-default-subagent-tools-approved",
            user_message="create hello.md using a subagent",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(subagents=True),
            approval_context="approved for this turn",
            chat_model_override=DelegationRoundTripChatModel(),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    workspace = path_service.thread_workspace_dir("thread-default-subagent-tools-approved")
    assert (workspace / "hello.md").read_text(encoding="utf-8") == "hello\n"
    delegated_tool = next(
        message
        for message in result.thread_state.conversation.messages
        if message.get("role") == "tool" and message.get("name") == "delegated_task"
    )
    delegated_payload = json.loads(str(delegated_tool["content"]))
    assert "write_file" in delegated_payload["allowed_tool_names"]


class PlanModeRoundTripChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "plan-mode-round-trip"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:  # type: ignore[override]
        tool_messages = [message for message in messages if getattr(message, "type", None) == "tool"]
        system_messages = [
            str(getattr(message, "content", ""))
            for message in messages
            if getattr(message, "type", None) == "system"
        ]

        todos_written = any(getattr(message, "name", None) == "write_todos" for message in tool_messages)
        if not todos_written:
            payload = {
                "todos": [
                    {"id": "todo-plan-1", "content": "Inspect the failing module", "status": "pending"},
                    {"id": "todo-plan-2", "content": "Apply the fix after approval", "status": "pending"},
                ],
                "mode": "replace",
            }
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "write_todos",
                                    "args": {"payload": json.dumps(payload)},
                                    "id": "plan_write_todos_1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )

        if any("approval_context" in message for message in system_messages):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Executing the approved plan now."))])

        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=(
                            "Plan:\n"
                            "1. Inspect the failing module.\n"
                            "2. Apply the fix after approval.\n"
                            "3. Verify the result."
                        )
                    )
                )
            ]
        )


def test_run_engine_plan_mode_pauses_for_plan_confirmation(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)

    result = engine.run(
        RunRequest(
            thread_id="thread-plan-mode-confirm",
            user_message="Fix the failing module",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            feature_set=RuntimeFeatureSet(plan_mode=True),
            is_plan_mode=True,
            chat_model_override=PlanModeRoundTripChatModel(),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.AWAITING_APPROVAL
    assert result.thread_state.approvals.pending_approval == ApprovalDecision.NEEDS_USER_APPROVAL
    assert result.thread_state.approvals.approval_request is not None
    assert result.thread_state.approvals.approval_request.action_kind == "plan_confirmation"
    assert result.thread_state.planning.todo_snapshot
    assert "Review the proposed plan" in (result.thread_state.lifecycle.last_error or "")


def test_run_engine_plan_mode_can_resume_after_plan_confirmation(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)

    initial = engine.run(
        RunRequest(
            thread_id="thread-plan-mode-approved",
            user_message="Fix the failing module",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            feature_set=RuntimeFeatureSet(plan_mode=True),
            is_plan_mode=True,
            chat_model_override=PlanModeRoundTripChatModel(),
        )
    )
    assert initial.thread_state.lifecycle.status is ThreadLifecycleStatus.AWAITING_APPROVAL

    resumed = engine.resume_approval(
        thread_id="thread-plan-mode-approved",
        config_layers=base_layers(),
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        approval_context="approved plan for this turn",
        feature_set=RuntimeFeatureSet(plan_mode=True),
        chat_model_override=PlanModeRoundTripChatModel(),
    )

    assert resumed.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert resumed.thread_state.approvals.pending_approval is None
    assert "Executing the approved plan now." in str(resumed.thread_state.conversation.messages[-1]["content"])
