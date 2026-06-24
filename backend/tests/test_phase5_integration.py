from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage

from anvil.agents.features import RuntimeFeatureSet
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.runs import RunEngine, RunRequest
from anvil.runtime.store import StoreBackend, create_store
from anvil.sandbox import PathService
from anvil.config import ConfigLayer, ConfigLayerKind
from fake_models import BindableFakeMessagesListChatModel


def write_skill(root: Path, slug: str, title: str, body: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


def config_layers(contract_tmp_path):
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "demo-skill", "Demo Skill", "Use the demo workflow")
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
                "hcms": {"enabled": True},
                "skills_config": {
                    "enabled": True,
                    "external_dirs": [str(skills_root)],
                    "enabled_ids": ["demo-skill"],
                },
                "subagents": {"enabled": True},
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
                "guardrails": {"enabled": True},
            },
        )
    ]


def test_phase5_runtime_integration_supports_memory_skills_and_typed_approval(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-5",
            user_message="write a file and use $demo-skill with @ext_search",
            request_context="Use $demo-skill and @ext_search for this turn.",
            config_layers=config_layers(contract_tmp_path),
            feature_set=RuntimeFeatureSet(
                memory=True,
                memory_prefetch=True,
                skills=True,
                capability_mentions=True,
                extensions=True,
                subagents=True,
                guardrails=True,
            ),
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
                    )
                ]
            ),
        )
    )

    assert result.runtime.context.memory_context is None
    assert result.runtime.context.memory_context_mode == "context_v2"
    assert result.runtime.capability_bundle.enabled_skill_ids == ("demo-skill",)
    assert "delegated_task" in [entry.name for entry in result.runtime.capability_bundle.visible_tools]
    assert "ext_search" in [entry.name for entry in result.runtime.capability_bundle.visible_tools]
    assert "capability_search" in [entry.name for entry in result.runtime.capability_bundle.visible_tools]
    assert result.thread_state.lifecycle.status.value == "awaiting_approval"
    assert result.thread_state.approvals.pending_approval is not None
    assert result.runtime.context.memory_service.queue.pending_count() == 0
    stored = result.runtime.context.memory_service.store.load("global/default")
    assert any(memory.source_thread_id == "thread-5" for memory in stored.memories)


def test_phase5_memory_capture_processes_default_hcms_manager_on_success(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-6",
            user_message="say hello and remember this preference",
            request_context="Remember that the user prefers concise updates.",
            config_layers=config_layers(contract_tmp_path),
            feature_set=RuntimeFeatureSet(
                memory=True,
                memory_prefetch=True,
                skills=True,
                capability_mentions=False,
                extensions=False,
                subagents=False,
                guardrails=False,
            ),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello")]
            ),
        )
    )

    assert result.runtime.context.memory_namespace == "global/default"
    assert "global/default" in result.runtime.context.memory_service.store.list_namespaces()
    assert result.runtime.context.memory_service.queue.pending_count() == 0
    stored = result.runtime.context.memory_service.store.load("global/default")
    assert any("remember this preference" in memory.content for memory in stored.memories)


def test_phase5_hcms_default_manager_records_and_prefetches_between_turns(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    features = RuntimeFeatureSet(
        memory=True,
        memory_prefetch=True,
        memory_capture=True,
        skills=False,
        capability_mentions=False,
        extensions=False,
        subagents=False,
        guardrails=False,
    )

    first = engine.run(
        RunRequest(
            thread_id="thread-hcms-default-e2e",
            user_message="Remember: Northstar deploys with canary verification because full rollouts failed.",
            config_layers=config_layers(contract_tmp_path),
            feature_set=features,
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="Recorded the Northstar deployment memory.")]
            ),
        )
    )

    stored = first.runtime.context.memory_service.store.load("global/default")
    assert any("Northstar" in memory.content for memory in stored.memories)
    assert sum(1 for memory in stored.memories if "Northstar" in memory.content) == 1
    assert first.runtime.context.memory_service.queue.pending_count() == 0
    assert first.runtime.context.memory_capture_processed is True
    assert first.runtime.context.memory_capture_processed_count == 1

    second = engine.run(
        RunRequest(
            thread_id="thread-hcms-default-e2e",
            user_message="Why does Northstar use canary verification?",
            config_layers=config_layers(contract_tmp_path),
            feature_set=features,
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="Because prior full rollouts failed.")]
            ),
        )
    )

    assert second.runtime.context.memory_context is None
    assert second.runtime.context.memory_context_mode == "context_v2"
    blocks = second.runtime.context.context_v2_memory_blocks
    assert blocks
    block_text = "\n".join(block["content"] for block in blocks)
    assert "Northstar" in block_text
    assert "canary verification" in block_text
    diagnostics = second.runtime.context.memory_injection_diagnostics
    assert diagnostics["source"] == "memory_manager"
    assert diagnostics["status"] == "injected"
    assert diagnostics["injection_mode"] == "context_v2"
    assert diagnostics["evidence_count"] >= 1
    assert diagnostics["context_v2_block_count"] == len(blocks)
    context_v2 = second.runtime.context.context_v2
    assert context_v2["actual_prompt_mode"] == "runtime_context_v2"
    assert context_v2["hcms_v2_memory_candidate_count"] == len(blocks)
    assert context_v2["trace"]["selected_memory"]


def test_phase5_memory_capture_enqueues_on_failed_turn(contract_tmp_path) -> None:
    class BrokenModel:
        def bind_tools(self, *args, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            raise RuntimeError("boom")

    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-7",
            user_message="remember that concise summaries are preferred",
            request_context="Remember the user's preference for concise summaries.",
            config_layers=config_layers(contract_tmp_path),
            feature_set=RuntimeFeatureSet(
                memory=True,
                memory_prefetch=True,
                skills=True,
                capability_mentions=False,
                extensions=False,
                subagents=False,
                guardrails=False,
            ),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BrokenModel(),
        )
    )

    assert result.thread_state.lifecycle.status.value == "failed"
    assert result.runtime.context.memory_service.queue.pending_count() == 0
    stored = result.runtime.context.memory_service.store.load("global/default")
    assert any("concise summaries are preferred" in memory.content for memory in stored.memories)
