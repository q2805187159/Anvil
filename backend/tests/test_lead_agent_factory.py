from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from anvil.agents.factory import create_harness_agent, _build_hcms_structured_update_provider
from anvil.agents.features import RuntimeFeatureSet
from anvil.agents.lead_agent.prompt import reset_prompt_snapshot_cache
from anvil.agents.lead_agent.prompt import reset_runtime_path_context_cache
from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService, ModelRouteRequest, resolve_model_route
from anvil.runtime.context_v2 import (
    capability_bundle_to_blocks,
    context_v2_evaluation_record_from_snapshot,
    stable_prompt_hash,
)
from anvil.extensions import ExtensionsService
from anvil.runtime.state_v2 import ConflictAlert
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.store import StoreBackend, create_store
from anvil.runtime.tool_registry import (
    CapabilityAssemblyService,
    SkillSelectionFeedback,
    ToolRegistryEntry,
    ToolSourceKind,
)
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


def make_hcms_structured_updater_config_result() -> object:
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
                        "memory_updater": {
                            "name": "memory_updater",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4-mini",
                        },
                    },
                    "hcms": {
                        "enabled": True,
                        "updater": {
                            "mode": "structured",
                            "model_name": "memory_updater",
                        },
                    },
                },
            )
        ]
    )


def make_context_v2_memory_config_result() -> object:
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
                    "hcms": {
                        "enabled": True,
                        "storage_backend": "filesystem",
                        "recall": {
                            "injection_mode": "runtime-context-v2",
                        },
                    },
                },
            )
        ]
    )


def make_default_hcms_memory_config_result() -> object:
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
                    "hcms": {"enabled": True},
                },
            )
        ]
    )


def make_legacy_prompt_append_memory_config_result() -> object:
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
                    "hcms": {
                        "enabled": True,
                        "recall": {"injection_mode": "legacy"},
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


def test_hcms_structured_update_provider_uses_configured_internal_model(monkeypatch) -> None:
    config_result = make_hcms_structured_updater_config_result()
    invoked: list[object] = []

    class FakeUpdaterModel:
        def invoke(self, messages, config=None):
            invoked.append((messages, config))
            return AIMessage(content='{"newFacts": [], "updates": [], "removals": []}')

    def fake_create_chat_model(model_config, **kwargs):
        assert model_config.name == "memory_updater"
        assert kwargs["thinking_enabled"] is False
        return FakeUpdaterModel()

    monkeypatch.setattr("anvil.agents.factory.create_chat_model", fake_create_chat_model)

    provider = _build_hcms_structured_update_provider(
        effective_config=config_result.effective_config,
        tracing_service=None,
    )
    assert provider is not None
    response = provider(None, None, "HCMS prompt body")

    assert response == '{"newFacts": [], "updates": [], "removals": []}'
    assert invoked
    messages, runnable_config = invoked[0]
    assert messages[0].content == "HCMS prompt body"
    assert "anvil_internal_hcms_updater" in runnable_config["tags"]


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


def test_hcms_recall_context_v2_mode_flows_into_lead_agent_context(contract_tmp_path) -> None:
    config_result = make_context_v2_memory_config_result()
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
        thread_id="thread-context-v2-memory",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    assert config_result.effective_config.hcms.recall.injection_mode == "context_v2"
    assert runtime.feature_set.memory is True
    assert runtime.feature_set.memory_prefetch is True
    assert runtime.context.memory_context_mode == "context_v2"


def test_hcms_recall_defaults_to_context_v2_with_explicit_legacy_fallback(contract_tmp_path) -> None:
    config_result = make_default_hcms_memory_config_result()
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
        thread_id="thread-default-context-v2-memory",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    assert config_result.effective_config.hcms.recall.injection_mode == "context_v2"
    assert runtime.context.memory_context_mode == "context_v2"

    legacy_result = make_legacy_prompt_append_memory_config_result()
    assert legacy_result.effective_config.hcms.recall.injection_mode == "legacy_prompt_append"


@pytest.mark.parametrize(
    ("config_factory", "expected_mode"),
    (
        (make_context_v2_memory_config_result, "context_v2"),
        (make_legacy_prompt_append_memory_config_result, "legacy_prompt_append"),
    ),
)
def test_stable_memory_snapshot_is_not_direct_system_prompt_append_for_hcms_modes(
    contract_tmp_path,
    config_factory,
    expected_mode: str,
) -> None:
    config_result = config_factory()
    route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(subsystem="lead_agent"),
    )
    sentinel_memory = "ROUND41_DIRECT_MEMORY_SENTINEL should only appear through ContextBlock trace"

    runtime = create_harness_agent(
        config_result=config_result,
        resolved_route=route,
        path_service=PathService(contract_tmp_path / "threads"),
        checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
        store=create_store(StoreBackend.IN_MEMORY),
        sandbox_provider=create_sandbox_provider(config_result.effective_config),
        feature_set=RuntimeFeatureSet(memory=True),
        thread_id="thread-context-v2-stable-memory",
        memory_manager=StaticMemoryManager(
            content=sentinel_memory,
            fingerprint="stable-memory-v1",
        ),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    stable_section_names = [
        section.name for section in runtime.prompt_snapshot.stable_sections
    ]
    assert "memory_snapshot" in stable_section_names
    assert runtime.prompt_snapshot.snapshot_key.memory_snapshot_fingerprint == "stable-memory-v1"
    assert runtime.context.memory_context_mode == expected_mode

    assert "<memory_snapshot>" not in runtime.system_prompt
    assert sentinel_memory not in runtime.system_prompt
    assert runtime.assembly_snapshot.context_v2["actual_system_prompt_hash"] == (
        stable_prompt_hash(runtime.system_prompt)
    )

    context_v2 = runtime.assembly_snapshot.context_v2
    assert "memory_snapshot" in context_v2["candidate_block_titles"]
    memory_block_traces = [
        trace
        for trace in context_v2["trace"]["block_traces"]
        if trace["source_kind"] == "memory" and trace["block_type"] == "memory"
    ]
    assert memory_block_traces
    selected_memory_ids = set(context_v2["trace"]["selected_memory"])
    assert {
        trace["block_id"] for trace in memory_block_traces
        if trace["selected"]
    }.issubset(selected_memory_ids)
    assert context_v2["trace"]["layer_token_usage"]["memory"] > 0


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
    assert snapshot.context_v2["enabled"] is True
    assert snapshot.context_v2["diagnostic_only"] is True
    assert snapshot.context_v2["fallback_used"] is False
    assert snapshot.context_v2["candidate_block_count"] >= snapshot.context_v2["selected_block_count"] > 0
    assert "role_and_intent" in snapshot.context_v2["selected_block_titles"]
    assert "request_context" in snapshot.context_v2["candidate_block_titles"]
    context_trace = snapshot.context_v2["trace"]
    assert context_trace["prompt_hash"]
    assert context_trace["selected_block_ids"]
    assert context_trace["layer_token_usage"]["prompt"] > 0
    expected_capability_blocks = capability_bundle_to_blocks(
        runtime.capability_bundle,
        top_k=12,
        query="turn-local context",
    )
    assert context_trace["selected_capabilities"] == [
        block.metadata["tool_name"]
        for block in expected_capability_blocks
        if block.block_type == "capability"
    ]
    assert context_trace["selected_tools"] == context_trace["selected_capabilities"]
    assert context_trace["selected_mcp_tools"] == []
    assert isinstance(context_trace["selected_skills"], list)
    assert isinstance(context_trace["selected_memory"], list)
    assert isinstance(context_trace["selected_workspace"], list)
    assert isinstance(context_trace["selected_events"], list)
    assert isinstance(context_trace["selected_tool_results"], list)
    assert isinstance(context_trace["selected_tool_result_refs"], list)
    assert runtime.context.context_v2["trace"]["prompt_hash"] == context_trace["prompt_hash"]
    assert snapshot.context_v2["actual_prompt_mode"] == "runtime_context_v2"
    assert snapshot.context_v2["actual_system_prompt_hash"] == stable_prompt_hash(runtime.system_prompt)
    assert context_trace["metadata"]["actual_system_prompt_hash"] == (
        snapshot.context_v2["actual_system_prompt_hash"]
    )
    assert snapshot.model_dump(mode="json")["context_v2"]["trace"]["prompt_hash"] == (
        context_trace["prompt_hash"]
    )
    evaluation_record = context_v2_evaluation_record_from_snapshot(snapshot.model_dump(mode="json"))
    assert evaluation_record is not None
    assert evaluation_record.trace_id == context_trace["trace_id"]
    assert evaluation_record.prompt_hash == context_trace["prompt_hash"]
    assert evaluation_record.actual_prompt_mode == "runtime_context_v2"
    assert evaluation_record.actual_system_prompt_hash == snapshot.context_v2["actual_system_prompt_hash"]
    assert evaluation_record.diagnostic_only is True
    assert evaluation_record.selected_tools == context_trace["selected_tools"]
    assert evaluation_record.selected_workspace == context_trace["selected_workspace"]


def test_runtime_assembly_snapshot_includes_turn_pipeline_state_and_event_log(contract_tmp_path) -> None:
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
        thread_id="thread-turn-pipeline",
        run_id="run-turn-pipeline",
        request_context="Assemble a traceable runtime turn.",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    context_v2 = runtime.assembly_snapshot.context_v2
    turn_pipeline = context_v2["turn_pipeline"]
    turn_state = turn_pipeline["turn_state"]

    assert turn_pipeline["enabled"] is True
    assert turn_pipeline["event_types"] == [
        "user_message_received",
        "context_assembled",
    ]
    assert turn_state["thread_id"] == "thread-turn-pipeline"
    assert turn_state["run_id"] == "run-turn-pipeline"
    assert turn_state["phase_statuses"]["intake"] == "completed"
    assert turn_state["phase_statuses"]["context_assembly"] == "completed"
    assert turn_state["context_trace_id"] == context_v2["trace"]["trace_id"]
    assert context_v2["trace"]["metadata"]["pipeline"] == "runtime_context_v2_turn_pipeline"
    assert context_v2["trace"]["metadata"]["actual_prompt_mode"] == "runtime_context_v2"
    assert context_v2["diagnostic_only"] is True
    assert context_v2["actual_prompt_mode"] == "runtime_context_v2"

    assert runtime.context.workspace_state is not None
    assert runtime.context.tool_result_store is not None
    assert runtime.context.event_log is not None
    assert runtime.context.runtime_event_bus is not None
    assert runtime.context.context_v2["turn_pipeline"]["turn_state"]["context_trace_id"] == (
        context_v2["trace"]["trace_id"]
    )
    assert [event.event_type for event in runtime.context.event_log.events] == (
        turn_pipeline["event_types"]
    )


def test_runtime_assembly_snapshot_includes_review_inbox_warning_blocks(contract_tmp_path) -> None:
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
        thread_id="thread-review-inbox",
        run_id="run-review-inbox",
        request_context="Assemble runtime conflict warning blocks.",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    assert runtime.context.review_inbox is not None
    item = runtime.context.review_inbox.add_alert(
        ConflictAlert(
            alert_id="alert-runtime-1",
            conflict_id="conflict-runtime-1",
            severity="high",
            affected_claims=["claim-old", "claim-new"],
            affected_memories=["mem-legacy"],
            preferred_claim_id="claim-new",
            unresolved_reason="Legacy direct memory append conflicts with Runtime Context V2 budget competition.",
            injection_policy="inject_warning",
            review_inbox_id="review-runtime-1",
            conflict_type="contradiction",
        )
    )

    updated = type(runtime.assembly_snapshot).from_runtime_parts(
        thread_id=runtime.context.thread_id,
        run_id=runtime.context.run_id,
        execution_mode=runtime.context.execution_mode,
        config_fingerprint=config_result.fingerprint,
        resolved_route=runtime.resolved_route,
        prompt_snapshot=runtime.prompt_snapshot,
        prompt_injection_view=runtime.prompt_injection_view,
        project_context_snapshot=None,
        runtime_path_snapshot=None,
        capability_bundle=runtime.capability_bundle,
        middleware_chain=runtime.middleware_chain,
        feature_set=runtime.feature_set,
        system_prompt=runtime.system_prompt,
        workspace_state=runtime.context.workspace_state,
        tool_result_store=runtime.context.tool_result_store,
        review_inbox=runtime.context.review_inbox,
        event_log=runtime.context.event_log,
        runtime_event_bus=runtime.context.runtime_event_bus,
        turn_user_text="Assemble runtime conflict warning blocks.",
    )

    context_v2 = updated.context_v2

    assert "Runtime Conflict Warning" in context_v2["candidate_block_titles"]
    assert "Runtime Conflict Warning" in context_v2["selected_block_titles"]
    assert context_v2["trace"]["selected_events"] == [item.review_inbox_id]
    assert item.review_inbox_id in context_v2["turn_pipeline"]["turn_state"]["review_inbox_refs"]
    warning_trace = next(
        block_trace
        for block_trace in context_v2["trace"]["block_traces"]
        if block_trace["block_type"] == "runtime_warning"
    )
    assert warning_trace["selected"] is True


def test_runtime_assembly_snapshot_includes_goal_stack_salience_route(contract_tmp_path) -> None:
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
        thread_id="thread-goal-stack",
        run_id="run-goal-stack",
        request_context="Route active goal into HCMS retrieval.",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    context_v2 = runtime.assembly_snapshot.context_v2

    assert runtime.context.goal_stack is not None
    assert runtime.context.salience_route is not None
    assert context_v2["salience_route"]["goal_stack_ref"] == runtime.context.goal_stack.stack_id
    assert context_v2["salience_route"]["active_goal_id"] == runtime.context.goal_stack.active_goal_id
    assert "current_query=Route active goal into HCMS retrieval." in (
        context_v2["salience_route"]["memory_query"]
    )
    assert "GoalStack" in context_v2["candidate_block_titles"]
    assert "GoalStack" in context_v2["selected_block_titles"]
    assert context_v2["turn_pipeline"]["turn_state"]["goal_stack_ref"] == (
        runtime.context.goal_stack.stack_id
    )
    goal_trace = next(
        block_trace
        for block_trace in context_v2["trace"]["block_traces"]
        if block_trace["block_type"] == "goal_stack"
    )
    assert goal_trace["selected"] is True


def test_lead_agent_context_records_skill_selection_feedback_in_capability_registry(contract_tmp_path) -> None:
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
        thread_id="thread-skill-feedback",
        run_id="run-skill-feedback",
        request_context="Use HCMS runtime skill feedback.",
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="hello")]
        ),
    )

    registry = runtime.context.tool_registry
    assert registry is not None
    registered = registry.register(
        ToolRegistryEntry(
            name="hcms_runtime_skill",
            display_name="HCMS Runtime Skill",
            source_kind=ToolSourceKind.SKILL,
            source_id="skill-hcms-runtime",
            capability_group="skills",
            summary="Assemble HCMS runtime context.",
        )
    )

    decision = runtime.context.record_skill_selection_feedback(
        SkillSelectionFeedback(
            skill_id="skill-hcms-runtime",
            turn_id="turn-skill-feedback-1",
            selected=True,
            injected=True,
            used_by_llm=True,
            outcome="success",
            latency_ms=37,
            context_block_refs=["skill:block:runtime"],
        )
    )

    updated = {
        entry.name: entry
        for entry in registry.entries()
    }[registered.name]
    stats = updated.provenance["skill_selection_feedback"]

    assert decision.updated is True
    assert decision.capability_ids == (registered.capability_id,)
    assert decision.feedback_count == 1
    assert decision.success_count == 1
    assert stats["feedback_count"] == 1
    assert stats["success_count"] == 1
    assert stats["last_outcome"] == "success"
    assert stats["last_context_block_refs"] == ["skill:block:runtime"]
    assert runtime.context.skill_selection_feedback_decisions[-1] == decision


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


def test_runtime_assembly_snapshot_diff_reports_nested_model_paths_and_vision_tool_additions(contract_tmp_path) -> None:
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
    assert diff.added == {
        "capabilities.discovered_tool_names": ("view_image",),
        "capabilities.visible_tool_names": ("view_image",),
    }
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
