from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from anvil.agents.factory import LeadAgentRuntime, create_harness_agent
from anvil.agents.features import RuntimeFeatureSet
from anvil.agents.thread_state import ThreadExecutionMode
from anvil.config import (
    ConfigResolutionResult,
    ModelRouteRequest,
    RequiredModelCapabilities,
    resolve_model_route,
)
from anvil.extensions import ExtensionsService
from anvil.runtime.checkpointers import Checkpointer
from anvil.runtime.store import Store
from anvil.runtime.tool_registry import CapabilityAssemblyService
from anvil.sandbox import PathService, create_sandbox_provider
from anvil.skills import SkillsService


def make_lead_agent(
    *,
    config_result: ConfigResolutionResult,
    path_service: PathService,
    checkpointer: Checkpointer,
    store: Store,
    thread_id: str,
    feature_set: RuntimeFeatureSet | None = None,
    route_request: ModelRouteRequest | None = None,
    request_context: str | None = None,
    turn_user_text: str | None = None,
    approval_context: str | None = None,
    upload_context: str | None = None,
    is_plan_mode: bool = False,
    execution_mode: ThreadExecutionMode = ThreadExecutionMode.AGENT,
    reasoning_effort_override: str | None = None,
    promoted_capabilities: tuple[str, ...] = (),
    parent_visible_tool_names: tuple[str, ...] | None = None,
    run_id: str | None = None,
    subagent_service: Any | None = None,
    process_service: Any | None = None,
    scheduled_task_service: Any | None = None,
    memory_manager: Any | None = None,
    skills_service: SkillsService | None = None,
    extensions_service: ExtensionsService | None = None,
    capability_assembly_service: CapabilityAssemblyService | None = None,
    tracing_service: Any | None = None,
    run_trace_id: str | None = None,
    runtime_phase_marker: Callable[[str], None] | None = None,
    recent_upload_filenames: tuple[str, ...] = (),
    chat_model_override: BaseChatModel | None = None,
    approval_session_grants: tuple[str, ...] = (),
) -> LeadAgentRuntime:
    feature_set = feature_set or RuntimeFeatureSet()
    route_request = route_request or ModelRouteRequest(
        subsystem="lead_agent",
        required_capabilities=RequiredModelCapabilities(
            tool_calling=execution_mode is not ThreadExecutionMode.CHAT
        ),
    )
    resolved_route = resolve_model_route(config_result.effective_config, route_request)
    if runtime_phase_marker is not None:
        runtime_phase_marker("model_route_resolved")
    if reasoning_effort_override is not None:
        resolved_route = resolved_route.model_copy(update={"reasoning_effort": reasoning_effort_override})
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    if runtime_phase_marker is not None:
        runtime_phase_marker("sandbox_provider_created")

    return create_harness_agent(
        config_result=config_result,
        resolved_route=resolved_route,
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        sandbox_provider=sandbox_provider,
        feature_set=feature_set,
        thread_id=thread_id,
        request_context=request_context,
        turn_user_text=turn_user_text,
        approval_context=approval_context,
        upload_context=upload_context,
        is_plan_mode=is_plan_mode,
        execution_mode=execution_mode,
        promoted_capabilities=promoted_capabilities,
        parent_visible_tool_names=parent_visible_tool_names,
        run_id=run_id,
        subagent_service=subagent_service,
        process_service=process_service,
        scheduled_task_service=scheduled_task_service,
        memory_manager=memory_manager,
        skills_service=skills_service,
        extensions_service=extensions_service,
        capability_assembly_service=capability_assembly_service,
        tracing_service=tracing_service,
        run_trace_id=run_trace_id,
        runtime_phase_marker=runtime_phase_marker,
        recent_upload_filenames=recent_upload_filenames,
        chat_model_override=chat_model_override,
        approval_session_grants=approval_session_grants,
    )
