from __future__ import annotations

import sys
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from anvil.agents.factory import create_harness_agent
from anvil.agents.features import RuntimeFeatureSet
from anvil.agents.lead_agent.prompt import reset_prompt_snapshot_cache
from anvil.agents.lead_agent.prompt import reset_runtime_path_context_cache
from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService, ModelRouteRequest, resolve_model_route
from anvil.extensions import ExtensionsService
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.store import StoreBackend, create_store
from anvil.runtime.tool_registry import CapabilityAssemblyService
from anvil.sandbox import PathService, create_sandbox_provider
from anvil.skills import SkillsService
from fake_models import BindableFakeMessagesListChatModel


def make_config_result() -> object:
    service = ConfigService()
    return service.resolve(
        [
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
    )


def make_multi_model_config_result() -> object:
    service = ConfigService()
    return service.resolve(
        [
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
                        },
                        "vision": {
                            "name": "vision",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4-vision",
                            "supports_vision": True,
                        },
                    },
                },
            )
        ]
    )


class StaticMemoryManager:
    def __init__(self, *, content: str, fingerprint: str) -> None:
        self._snapshot = SimpleNamespace(content=content, fingerprint=fingerprint)

    def get_or_create_session_snapshot(self, *, thread_id: str) -> SimpleNamespace:
        return self._snapshot


def test_create_harness_agent_builds_runnable_without_app_imports(contract_tmp_path) -> None:
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    runtime = create_harness_agent(
        config_result=config_result,
        resolved_route=route,
        path_service=PathService(contract_tmp_path / "threads"),
        checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
        store=create_store(StoreBackend.IN_MEMORY),
        sandbox_provider=create_sandbox_provider(config_result.effective_config),
        feature_set=RuntimeFeatureSet(),
        thread_id="thread-1",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    assert runtime.agent is not None
    assert runtime.prompt_snapshot.snapshot_id == runtime.context.capability_bundle.fingerprint or runtime.prompt_snapshot.snapshot_id != ""
    assert "backend.app" not in sys.modules
    assert all(not name.startswith("backend.app.") for name in sys.modules)


def test_routed_model_choice_flows_into_runtime(contract_tmp_path) -> None:
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    runtime = create_harness_agent(
        config_result=config_result,
        resolved_route=route,
        path_service=PathService(contract_tmp_path / "threads"),
        checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
        store=create_store(StoreBackend.IN_MEMORY),
        sandbox_provider=create_sandbox_provider(config_result.effective_config),
        feature_set=RuntimeFeatureSet(),
        thread_id="thread-1",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    assert runtime.resolved_route.model_name == "openai"


def test_runtime_assembly_snapshot_explains_factory_output(contract_tmp_path) -> None:
    reset_prompt_snapshot_cache(max_entries=8)
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    runtime = create_harness_agent(
        config_result=config_result,
        resolved_route=route,
        path_service=PathService(contract_tmp_path / "threads"),
        checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
        store=create_store(StoreBackend.IN_MEMORY),
        sandbox_provider=create_sandbox_provider(config_result.effective_config),
        feature_set=RuntimeFeatureSet(),
        thread_id="thread-snapshot",
        request_context="turn-local context",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    snapshot = runtime.assembly_snapshot

    assert snapshot.thread_id == "thread-snapshot"
    assert snapshot.execution_mode == "agent"
    assert snapshot.config_fingerprint == config_result.fingerprint
    assert snapshot.model.model_name == "openai"
    assert snapshot.model.provider == "openai"
    assert snapshot.prompt.snapshot_id == runtime.prompt_snapshot.snapshot_id
    assert snapshot.prompt.prompt_hash == runtime.prompt_snapshot.snapshot_key.digest()
    assert snapshot.prompt.stable_section_names == tuple(
        section.name for section in runtime.prompt_snapshot.stable_sections
    )
    assert snapshot.prompt.volatile_section_names == ("request_context",)
    assert snapshot.prompt.stable_prompt_tokens > 0
    assert snapshot.prompt.volatile_prompt_tokens > 0
    assert snapshot.prompt.stable_section_tokens["role_and_intent"] > 0
    assert snapshot.prompt.volatile_section_tokens == {"request_context": snapshot.prompt.volatile_prompt_tokens}
    assert snapshot.prompt.cache["max_entries"] == 8
    assert snapshot.prompt.cache["size"] >= 1
    assert snapshot.prompt.cache["misses"] >= 1
    assert snapshot.prompt.cache_delta["misses"] == 1
    assert snapshot.prompt.cache_delta["writes"] == 1
    assert snapshot.prompt.cache_delta["hits"] == 0
    assert snapshot.prompt.cache_delta["bypasses"] == 0
    assert snapshot.prompt.cache_delta["size_before"] == 0
    assert snapshot.prompt.cache_delta["size_after"] >= 1
    assert snapshot.capabilities.fingerprint == runtime.capability_bundle.fingerprint
    assert snapshot.capabilities.visible_tool_names == tuple(
        entry.name for entry in runtime.capability_bundle.visible_tools
    )
    assert snapshot.middleware_names == tuple(
        middleware.name for middleware in runtime.middleware_chain
    )
    assert "thread_data" in snapshot.enabled_feature_flags
    assert "memory" in snapshot.disabled_feature_flags
    assert snapshot.service_flags["approval_service"] is True
    assert snapshot.service_flags["skills_service"] is True
    assert snapshot.model_dump(mode="json")["prompt"]["volatile_section_names"] == [
        "request_context"
    ]
    assert "turn-local context" not in repr(snapshot.prompt.model_dump(mode="json"))


def test_runtime_assembly_snapshot_records_run_specific_prompt_cache_hits(contract_tmp_path) -> None:
    reset_prompt_snapshot_cache(max_entries=8)
    reset_runtime_path_context_cache(max_entries=8)
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    common = {
        "config_result": config_result,
        "resolved_route": route,
        "path_service": PathService(contract_tmp_path / "threads"),
        "checkpointer": create_checkpointer(CheckpointerBackend.IN_MEMORY),
        "store": create_store(StoreBackend.IN_MEMORY),
        "sandbox_provider": create_sandbox_provider(config_result.effective_config),
        "feature_set": RuntimeFeatureSet(),
        "thread_id": "thread-cache-delta",
        "chat_model_override": BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    }

    first = create_harness_agent(**common)
    second = create_harness_agent(
        **common,
        request_context="volatile turn text should not change the stable prompt cache key",
    )

    assert first.assembly_snapshot.prompt.cache_delta["misses"] == 1
    assert first.assembly_snapshot.prompt.cache_delta["writes"] == 1
    assert first.assembly_snapshot.prompt.runtime_path_cache_status == "miss"
    assert second.assembly_snapshot.prompt.cache_delta["hits"] == 1
    assert second.assembly_snapshot.prompt.cache_delta["misses"] == 0
    assert second.assembly_snapshot.prompt.cache_delta["writes"] == 0
    assert second.assembly_snapshot.prompt.runtime_path_cache_status == "hit"
    assert second.assembly_snapshot.prompt.runtime_path_fingerprint == first.assembly_snapshot.prompt.runtime_path_fingerprint
    assert second.assembly_snapshot.prompt.volatile_section_tokens["request_context"] > 0
    assert second.assembly_snapshot.prompt.cache["hits"] >= 1
    assert second.assembly_snapshot.prompt.cache["misses"] >= 1


def test_create_harness_agent_reuses_injected_capability_services(contract_tmp_path) -> None:
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    skills_service = SkillsService()
    extensions_service = ExtensionsService()
    capability_assembly_service = CapabilityAssemblyService(
        skills_service=skills_service,
        extensions_service=extensions_service,
    )

    runtime = create_harness_agent(
        config_result=config_result,
        resolved_route=route,
        path_service=PathService(contract_tmp_path / "threads"),
        checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
        store=create_store(StoreBackend.IN_MEMORY),
        sandbox_provider=create_sandbox_provider(config_result.effective_config),
        feature_set=RuntimeFeatureSet(skills=True, extensions=True),
        thread_id="thread-shared-runtime-services",
        skills_service=skills_service,
        extensions_service=extensions_service,
        capability_assembly_service=capability_assembly_service,
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    assert runtime.context.skills_service is skills_service
    assert runtime.context.extensions_service is extensions_service
    assert runtime.context.capability_service is capability_assembly_service
    assert runtime.assembly_snapshot.service_flags["skills_service"] is True
    assert runtime.assembly_snapshot.service_flags["extensions_service"] is True


def test_runtime_assembly_snapshot_diff_surfaces_contract_changes(contract_tmp_path) -> None:
    reset_prompt_snapshot_cache(max_entries=8)
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    common = {
        "config_result": config_result,
        "resolved_route": route,
        "path_service": PathService(contract_tmp_path / "threads"),
        "checkpointer": create_checkpointer(CheckpointerBackend.IN_MEMORY),
        "store": create_store(StoreBackend.IN_MEMORY),
        "sandbox_provider": create_sandbox_provider(config_result.effective_config),
        "thread_id": "thread-snapshot-diff",
        "chat_model_override": BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    }
    baseline = create_harness_agent(
        **common,
        feature_set=RuntimeFeatureSet(),
    )
    changed = create_harness_agent(
        **common,
        feature_set=RuntimeFeatureSet(clarification=False),
    )

    diff = baseline.assembly_snapshot.diff(changed.assembly_snapshot)

    assert diff.changed is True
    assert diff.changed_paths == (
        "middleware_names",
        "enabled_feature_flags",
        "disabled_feature_flags",
    )
    assert "ClarificationMiddleware" in diff.changes["middleware_names"]["before"]
    assert "ClarificationMiddleware" not in diff.changes["middleware_names"]["after"]
    assert "clarification" in diff.changes["enabled_feature_flags"]["before"]
    assert "clarification" not in diff.changes["enabled_feature_flags"]["after"]
    assert "clarification" in diff.changes["disabled_feature_flags"]["after"]
    assert diff.removed["middleware_names"] == ("ClarificationMiddleware",)
    assert diff.removed["enabled_feature_flags"] == ("clarification",)
    assert diff.added["disabled_feature_flags"] == ("clarification",)


def test_runtime_assembly_snapshot_diff_ignores_prompt_cache_diagnostics(contract_tmp_path) -> None:
    reset_prompt_snapshot_cache(max_entries=8)
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    common = {
        "config_result": config_result,
        "resolved_route": route,
        "path_service": PathService(contract_tmp_path / "threads"),
        "checkpointer": create_checkpointer(CheckpointerBackend.IN_MEMORY),
        "store": create_store(StoreBackend.IN_MEMORY),
        "sandbox_provider": create_sandbox_provider(config_result.effective_config),
        "feature_set": RuntimeFeatureSet(),
        "thread_id": "thread-cache-diff",
        "chat_model_override": BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    }
    baseline = create_harness_agent(**common)
    repeated = create_harness_agent(**common)

    diff = baseline.assembly_snapshot.diff(repeated.assembly_snapshot)

    assert baseline.assembly_snapshot.prompt.cache_delta["misses"] == 1
    assert repeated.assembly_snapshot.prompt.cache_delta["hits"] == 1
    assert diff.changed is False
    assert diff.changed_paths == ()


def test_runtime_assembly_snapshot_diff_ignores_prompt_token_ledger(contract_tmp_path) -> None:
    reset_prompt_snapshot_cache(max_entries=8)
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    common = {
        "config_result": config_result,
        "resolved_route": route,
        "path_service": PathService(contract_tmp_path / "threads"),
        "checkpointer": create_checkpointer(CheckpointerBackend.IN_MEMORY),
        "store": create_store(StoreBackend.IN_MEMORY),
        "sandbox_provider": create_sandbox_provider(config_result.effective_config),
        "feature_set": RuntimeFeatureSet(),
        "thread_id": "thread-token-ledger-diff",
        "chat_model_override": BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    }
    baseline = create_harness_agent(
        **common,
        request_context="short turn context",
    )
    changed = create_harness_agent(
        **common,
        request_context="longer turn context " * 20,
    )

    diff = baseline.assembly_snapshot.diff(changed.assembly_snapshot)
    diff_payload = repr(diff.model_dump(mode="json"))

    assert baseline.assembly_snapshot.prompt.volatile_section_names == ("request_context",)
    assert changed.assembly_snapshot.prompt.volatile_section_names == ("request_context",)
    assert changed.assembly_snapshot.prompt.volatile_prompt_tokens > baseline.assembly_snapshot.prompt.volatile_prompt_tokens
    assert diff.changed is False
    assert "volatile_section_tokens" not in diff_payload
    assert "longer turn context" not in diff_payload


def test_runtime_assembly_snapshot_diff_ignores_capability_assembly_timing(contract_tmp_path) -> None:
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    common = {
        "config_result": config_result,
        "resolved_route": route,
        "path_service": PathService(contract_tmp_path / "threads"),
        "checkpointer": create_checkpointer(CheckpointerBackend.IN_MEMORY),
        "store": create_store(StoreBackend.IN_MEMORY),
        "sandbox_provider": create_sandbox_provider(config_result.effective_config),
        "feature_set": RuntimeFeatureSet(),
        "thread_id": "thread-capability-timing-diff",
        "chat_model_override": BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    }

    baseline = create_harness_agent(**common)
    changed = create_harness_agent(**common)
    changed.assembly_snapshot.capabilities.assembly_diagnostics["assembly_stage_durations_ms"] = {
        "runtime_tools": 1,
        "final_bundle": 99,
        "total": 120,
    }
    changed.assembly_snapshot.capabilities.assembly_diagnostics["slowest_assembly_stage"] = "final_bundle"
    changed.assembly_snapshot.capabilities.assembly_diagnostics["slowest_assembly_stage_duration_ms"] = 99

    diff = baseline.assembly_snapshot.diff(changed.assembly_snapshot)

    assert diff.changed is False
    assert diff.changed_paths == ()


def test_runtime_assembly_snapshot_diff_reports_nested_model_paths(contract_tmp_path) -> None:
    config_result = make_multi_model_config_result()
    openai_route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent", request_override_model="openai"),
    )
    vision_route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent", request_override_model="vision"),
    )
    common = {
        "config_result": config_result,
        "path_service": PathService(contract_tmp_path / "threads"),
        "checkpointer": create_checkpointer(CheckpointerBackend.IN_MEMORY),
        "store": create_store(StoreBackend.IN_MEMORY),
        "sandbox_provider": create_sandbox_provider(config_result.effective_config),
        "feature_set": RuntimeFeatureSet(),
        "thread_id": "thread-snapshot-model-diff",
        "chat_model_override": BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    }
    baseline = create_harness_agent(
        **common,
        resolved_route=openai_route,
    )
    changed = create_harness_agent(
        **common,
        resolved_route=vision_route,
    )

    diff = baseline.assembly_snapshot.diff(changed.assembly_snapshot)

    assert diff.changed is True
    assert "model.model_name" in diff.changed_paths
    assert "model.capabilities.vision" in diff.changed_paths
    assert diff.changes["model.model_name"] == {"before": "openai", "after": "vision"}
    assert diff.changes["model.capabilities.vision"] == {"before": False, "after": True}
    assert diff.added == {}
    assert diff.removed == {}


def test_runtime_assembly_snapshot_diff_tracks_volatile_prompt_sections_without_content(contract_tmp_path) -> None:
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    common = {
        "config_result": config_result,
        "resolved_route": route,
        "path_service": PathService(contract_tmp_path / "threads"),
        "checkpointer": create_checkpointer(CheckpointerBackend.IN_MEMORY),
        "store": create_store(StoreBackend.IN_MEMORY),
        "sandbox_provider": create_sandbox_provider(config_result.effective_config),
        "feature_set": RuntimeFeatureSet(),
        "thread_id": "thread-snapshot-prompt-diff",
        "chat_model_override": BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    }
    baseline = create_harness_agent(**common)
    changed = create_harness_agent(
        **common,
        request_context="sensitive request context",
        upload_context="sensitive upload context",
        approval_context="sensitive approval context",
    )

    diff = baseline.assembly_snapshot.diff(changed.assembly_snapshot)
    diff_payload = repr(diff.model_dump(mode="json"))

    assert baseline.assembly_snapshot.prompt.prompt_hash == changed.assembly_snapshot.prompt.prompt_hash
    assert diff.changed_paths == ("prompt.volatile_section_names",)
    assert diff.added["prompt.volatile_section_names"] == (
        "request_context",
        "upload_context",
        "approval_context",
    )
    assert "sensitive request context" not in diff_payload
    assert "sensitive upload context" not in diff_payload
    assert "sensitive approval context" not in diff_payload


def test_runtime_assembly_snapshot_diff_tracks_stable_prompt_hash_without_memory_content(contract_tmp_path) -> None:
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    common = {
        "config_result": config_result,
        "resolved_route": route,
        "path_service": PathService(contract_tmp_path / "threads"),
        "checkpointer": create_checkpointer(CheckpointerBackend.IN_MEMORY),
        "store": create_store(StoreBackend.IN_MEMORY),
        "sandbox_provider": create_sandbox_provider(config_result.effective_config),
        "feature_set": RuntimeFeatureSet(memory=True),
        "thread_id": "thread-snapshot-memory-diff",
        "chat_model_override": BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    }
    baseline = create_harness_agent(
        **common,
        memory_manager=StaticMemoryManager(
            content="sensitive baseline memory",
            fingerprint="memory-v1",
        ),
    )
    changed = create_harness_agent(
        **common,
        memory_manager=StaticMemoryManager(
            content="sensitive changed memory",
            fingerprint="memory-v2",
        ),
    )

    diff = baseline.assembly_snapshot.diff(changed.assembly_snapshot)
    diff_payload = repr(diff.model_dump(mode="json"))

    assert diff.changed is True
    assert "prompt.snapshot_id" in diff.changed_paths
    assert "prompt.prompt_hash" in diff.changed_paths
    assert "prompt.stable_section_names" not in diff.changed_paths
    assert "sensitive baseline memory" not in diff_payload
    assert "sensitive changed memory" not in diff_payload


def test_create_harness_agent_injects_workspace_context_files(contract_tmp_path) -> None:
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    path_service = PathService(contract_tmp_path / "threads")
    workspace = path_service.thread_workspace_dir("thread-context")
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Run focused tests for changed modules.\n", encoding="utf-8")

    runtime = create_harness_agent(
        config_result=config_result,
        resolved_route=route,
        path_service=path_service,
        checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
        store=create_store(StoreBackend.IN_MEMORY),
        sandbox_provider=create_sandbox_provider(config_result.effective_config),
        feature_set=RuntimeFeatureSet(),
        thread_id="thread-context",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    assert "project_context_files" in [
        section.name for section in runtime.prompt_snapshot.stable_sections
    ]
    assert "Run focused tests for changed modules." in runtime.system_prompt
    assert runtime.prompt_snapshot.snapshot_key.project_context_fingerprint is not None
    assert runtime.context.project_context_files == (
        {
            "virtual_path": "/mnt/user-data/workspace/AGENTS.md",
            "relative_path": "AGENTS.md",
            "applies_to": "/mnt/user-data/workspace",
            "scope": ".",
            "truncated": False,
        },
    )
    assert runtime.assembly_snapshot.prompt.project_context_files == runtime.context.project_context_files
    assert runtime.assembly_snapshot.prompt.project_context_fingerprint == runtime.context.project_context_fingerprint


def test_runtime_assembly_snapshot_diff_summarizes_nested_tool_list_changes(contract_tmp_path) -> None:
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    common = {
        "config_result": config_result,
        "resolved_route": route,
        "path_service": PathService(contract_tmp_path / "threads"),
        "checkpointer": create_checkpointer(CheckpointerBackend.IN_MEMORY),
        "store": create_store(StoreBackend.IN_MEMORY),
        "sandbox_provider": create_sandbox_provider(config_result.effective_config),
        "feature_set": RuntimeFeatureSet(),
        "thread_id": "thread-snapshot-tool-diff",
        "chat_model_override": BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    }
    baseline = create_harness_agent(**common)
    narrowed = create_harness_agent(
        **common,
        parent_visible_tool_names=("read_file",),
    )

    diff = baseline.assembly_snapshot.diff(narrowed.assembly_snapshot)

    assert diff.changed is True
    assert "capabilities.visible_tool_names" in diff.changed_paths
    assert "write_file" in diff.removed["capabilities.visible_tool_names"]
    assert "read_file" not in diff.removed["capabilities.visible_tool_names"]
    assert "read_file" in diff.changes["capabilities.visible_tool_names"]["after"]


def test_parent_visible_tool_allowlist_restricts_child_runtime_tools(contract_tmp_path) -> None:
    config_result = make_config_result()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    runtime = create_harness_agent(
        config_result=config_result,
        resolved_route=route,
        path_service=PathService(contract_tmp_path / "threads"),
        checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
        store=create_store(StoreBackend.IN_MEMORY),
        sandbox_provider=create_sandbox_provider(config_result.effective_config),
        feature_set=RuntimeFeatureSet(),
        thread_id="thread-child",
        parent_visible_tool_names=("read_file",),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    assert [entry.name for entry in runtime.context.capability_bundle.visible_tools] == [
        "ask_clarification",
        "capability_search",
        "read_file",
        "skill_content",
        "skill_files",
        "skill_read_file",
        "skill_view",
        "skills_list",
        "tool_catalog",
        "tool_view",
        "toolset_catalog",
        "toolset_view",
        "write_todos",
    ]
