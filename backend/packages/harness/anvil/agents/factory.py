from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from anvil.agents.features import RuntimeFeatureSet
from anvil.agents.features import Next, Prev
from anvil.agents.features import resolve_feature_set
from anvil.agents.thread_state import ThreadExecutionMode
from anvil.agents.lead_agent.prompt import (
    PromptInjectionView,
    PromptSnapshot,
    build_runtime_path_context,
    build_prompt_snapshot,
    build_turn_injection_view,
    compose_system_prompt,
    prompt_snapshot_cache_stats,
)
from anvil.agents.lead_agent.context_files import build_project_context_snapshot
from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState
from anvil.agents.middlewares import (
    ApprovalMiddleware,
    ClarificationMiddleware,
    CompactionMiddleware,
    DanglingToolCallMiddleware,
    DeferredToolFilterMiddleware,
    GuardrailMiddleware,
    JITContextMiddleware,
    LLMErrorHandlingMiddleware,
    LoopDetectionMiddleware,
    MemoryCaptureMiddleware,
    MemoryPrefetchMiddleware,
    SandboxMiddleware,
    SandboxAuditMiddleware,
    SubagentLimitMiddleware,
    SummarizationMiddleware,
    TitleMiddleware,
    # TimingMiddleware,  # Temporarily disabled
    TodoMiddleware,
    ThreadDataMiddleware,
    TokenUsageMiddleware,
    ToolErrorMiddleware,
    ToolErrorHandlingMiddleware,
    ToolOutputBudgetMiddleware,
    ToolVisibilityMiddleware,
    UploadsMiddleware,
    ViewImageMiddleware,
)
from anvil.agents.model_factory import create_chat_model
from anvil.agents.runtime_snapshot import RuntimeAssemblySnapshot
from anvil.config import ConfigResolutionResult, ResolvedModelRoute
from anvil.extensions import ExtensionsService
from anvil.memory import DebouncedMemoryQueue, FileMemoryStore, HeuristicMemoryUpdater, MemoryService
from anvil.memory_platform import MemoryManager
from anvil.runtime.approvals import ApprovalService, NetworkApprovalService
from anvil.runtime.checkpointers import Checkpointer
from anvil.runtime.store import Store
from anvil.runtime.tool_registry import CapabilityAssemblyService, CapabilityBundle, ToolRegistry
from anvil.sandbox import PathService
from anvil.subagents import SubagentService
from anvil.skills import SkillsService


def clone_chat_model_override_for_subagent(chat_model_override: BaseChatModel | None) -> BaseChatModel | None:
    if chat_model_override is None:
        return None
    copy_method = getattr(chat_model_override, "model_copy", None)
    if callable(copy_method):
        try:
            return copy_method(deep=True)
        except Exception:
            pass
    try:
        return deepcopy(chat_model_override)
    except Exception:
        return chat_model_override


@dataclass
class LeadAgentRuntime:
    agent: Any
    resolved_route: ResolvedModelRoute
    assembly_snapshot: RuntimeAssemblySnapshot
    prompt_snapshot: PromptSnapshot
    prompt_injection_view: PromptInjectionView
    system_prompt: str
    middleware_chain: list[Any]
    tools: list[Any]
    tool_registry: ToolRegistry
    capability_bundle: CapabilityBundle
    chat_model: BaseChatModel
    context: LeadAgentContext
    feature_set: RuntimeFeatureSet
    checkpointer: Checkpointer
    store: Store


def create_harness_agent(
    *,
    config_result: ConfigResolutionResult,
    resolved_route: ResolvedModelRoute,
    path_service: PathService,
    checkpointer: Checkpointer,
    store: Store,
    sandbox_provider: Any,
    feature_set: RuntimeFeatureSet,
    thread_id: str,
    request_context: str | None = None,
    approval_context: str | None = None,
    upload_context: str | None = None,
    is_plan_mode: bool = False,
    execution_mode: ThreadExecutionMode = ThreadExecutionMode.AGENT,
    promoted_capabilities: tuple[str, ...] = (),
    parent_visible_tool_names: tuple[str, ...] | None = None,
    run_id: str | None = None,
    subagent_service: SubagentService | None = None,
    process_service: Any | None = None,
    scheduled_task_service: Any | None = None,
    memory_manager: MemoryManager | None = None,
    skills_service: SkillsService | None = None,
    extensions_service: ExtensionsService | None = None,
    capability_assembly_service: CapabilityAssemblyService | None = None,
    tracing_service: Any | None = None,
    run_trace_id: str | None = None,
    runtime_phase_marker: Callable[[str], None] | None = None,
    recent_upload_filenames: tuple[str, ...] = (),
    chat_model_override: BaseChatModel | None = None,
    approval_session_grants: tuple[str, ...] = (),
    middleware: list[Any] | None = None,
    extra_middlewares: list[Any] | None = None,
) -> LeadAgentRuntime:
    def mark_phase(phase: str) -> None:
        if runtime_phase_marker is not None:
            runtime_phase_marker(phase)

    mark_phase("factory_started")
    effective_config = config_result.effective_config
    feature_set = resolve_feature_set(feature_set, effective_config)
    mark_phase("factory_feature_set_resolved")

    legacy_memory_enabled = effective_config.memory.enabled and not effective_config.memory_platform.enabled
    memory_service = None
    if feature_set.memory and legacy_memory_enabled:
        memory_store_path = effective_config.memory.store_path
        if memory_store_path is None:
            memory_store_path = str(path_service.base_root.parent / "memory")
        memory_service = MemoryService(
            store=FileMemoryStore(memory_store_path),
            queue=DebouncedMemoryQueue(),
            updater=HeuristicMemoryUpdater(max_facts=effective_config.memory.max_facts),
            max_facts=effective_config.memory.max_facts,
            injection_token_budget=effective_config.memory.injection_token_budget,
        )

    if memory_manager is None and feature_set.memory:
        platform_config = effective_config.memory_platform
        if not platform_config.enabled and effective_config.memory.enabled:
            platform_config = platform_config.from_legacy_memory(effective_config.memory)
        memory_manager = MemoryManager.from_config(
            config=platform_config,
            base_path=path_service.base_root.parent / "memory-platform",
            legacy_store_path=effective_config.memory.store_path,
        )
    mark_phase("factory_memory_services_ready")

    skills_service = (skills_service or SkillsService()) if feature_set.skills else None
    extensions_service = (extensions_service or ExtensionsService()) if feature_set.extensions else None
    if feature_set.subagents and subagent_service is None:
        def _default_subagent_runner_factory(*, task, prompt, config_result, allowed_tool_names, execution_mode=None):
            def _runner() -> str:
                from anvil.agents.lead_agent.agent import make_lead_agent

                child_feature_set = feature_set.model_copy(
                    update={
                        "memory_prefetch": False,
                        "subagents": False,
                    }
                )
                child_runtime = make_lead_agent(
                    config_result=config_result,
                    path_service=path_service,
                    checkpointer=checkpointer,
                    store=store,
                    thread_id=thread_id,
                    feature_set=child_feature_set,
                    request_context=f"Delegated task: {prompt}",
                    approval_context=approval_context,
                    upload_context=upload_context,
                    is_plan_mode=is_plan_mode,
                    promoted_capabilities=tuple(allowed_tool_names),
                    parent_visible_tool_names=allowed_tool_names,
                    run_id=task.task_id,
                    process_service=process_service,
                    scheduled_task_service=scheduled_task_service,
                    chat_model_override=clone_chat_model_override_for_subagent(chat_model_override),
                )
                result = child_runtime.agent.invoke(
                    {"messages": [HumanMessage(content=prompt)]},
                    context=child_runtime.context,
                )
                if isinstance(result, dict) and result.get("pending_approval") is not None:
                    reason = str(
                        result.get("approval_request_reason")
                        or result.get("approval_request", {}).get("reason")
                        if isinstance(result.get("approval_request"), dict)
                        else "subagent execution requires approval"
                    )
                    raise RuntimeError(reason)
                messages = result.get("messages", []) if isinstance(result, dict) else []
                if messages:
                    last_message = messages[-1]
                    content = getattr(last_message, "content", "")
                    if isinstance(content, str):
                        return content
                return f"Delegated task completed: {prompt[:120]}"

            return _runner

        subagent_service = SubagentService(default_runner_factory=_default_subagent_runner_factory)
    approval_service = ApprovalService(
        network_service=NetworkApprovalService() if feature_set.network_approval_service else None,
        skip_tool_approvals=execution_mode is ThreadExecutionMode.FULL_ACCESS,
        guardrails_config=effective_config.guardrails,
        session_grants=approval_session_grants,
    ) if feature_set.guardrails else None
    mark_phase("factory_approval_service_ready")

    capability_service = capability_assembly_service or CapabilityAssemblyService(
        skills_service=skills_service,
        extensions_service=extensions_service,
        subagent_service=subagent_service,
        process_service=process_service,
        scheduled_task_service=scheduled_task_service,
    )
    mark_phase("capability_assembly_started")
    if execution_mode is ThreadExecutionMode.CHAT:
        registry = ToolRegistry()
        capability_bundle = registry.build_bundle(
            effective_config_fingerprint=config_result.fingerprint,
        )
        mark_phase("capability_assembly_completed")
    else:
        assembly_result = capability_service.assemble(
            sandbox_provider=sandbox_provider,
            path_service=path_service,
            thread_id=thread_id,
            memory_manager=memory_manager,
            config_result=config_result,
            feature_set=feature_set,
            execution_mode=execution_mode or ThreadExecutionMode.AGENT,
            request_context=request_context,
            promoted_capabilities=promoted_capabilities,
            parent_visible_tool_names=parent_visible_tool_names,
            promote_all_deferred=execution_mode is ThreadExecutionMode.FULL_ACCESS,
            tracing_service=tracing_service,
            run_trace_id=run_trace_id,
            run_id=run_id,
            resolved_route=resolved_route,
        )
        registry = assembly_result.registry
        capability_bundle = assembly_result.bundle
        promoted_capabilities = assembly_result.mention_resolution.promoted_tool_names or promoted_capabilities
        mark_phase("capability_assembly_completed")

    if memory_manager is not None and hasattr(memory_manager, "get_or_create_session_snapshot"):
        session_memory_snapshot = memory_manager.get_or_create_session_snapshot(thread_id=thread_id)
        memory_snapshot = session_memory_snapshot.content
        memory_snapshot_fingerprint = session_memory_snapshot.fingerprint
    else:
        memory_snapshot = memory_manager.render_stable_snapshot() if memory_manager is not None else ""
        memory_snapshot_fingerprint = memory_manager.stable_snapshot_fingerprint() if memory_manager is not None else None
    mark_phase("memory_snapshot_loaded")
    project_context_snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id=thread_id,
        config=effective_config.context_files,
    )
    mark_phase("project_context_loaded")
    runtime_path_snapshot = build_runtime_path_context(
        path_service=path_service,
        thread_id=thread_id,
    )
    mark_phase("runtime_path_context_built")
    prompt_cache_before = prompt_snapshot_cache_stats()
    prompt_snapshot = build_prompt_snapshot(
        config_fingerprint=config_result.fingerprint,
        capability_bundle=capability_bundle,
        feature_set=feature_set,
        memory_namespace=effective_config.memory.namespace if legacy_memory_enabled else None,
        memory_snapshot=memory_snapshot,
        memory_snapshot_fingerprint=memory_snapshot_fingerprint,
        project_context=project_context_snapshot.rendered if project_context_snapshot.has_content else None,
        project_context_fingerprint=project_context_snapshot.fingerprint,
        runtime_path_context=runtime_path_snapshot.rendered,
        runtime_path_fingerprint=runtime_path_snapshot.fingerprint,
        delegation_max_concurrency=effective_config.subagents.max_concurrency if feature_set.subagents else None,
        delegation_max_depth=effective_config.subagents.max_depth if feature_set.subagents else None,
    )
    prompt_cache_after = prompt_snapshot_cache_stats()
    mark_phase("prompt_snapshot_built")
    prompt_injection_view = build_turn_injection_view(
        request_context=request_context,
        upload_context=upload_context,
        approval_context=approval_context,
        plan_context=(
            "Plan mode is active. This turn is for planning first. "
            "Produce a concise execution plan, update the todo list with write_todos, "
            "and stop after presenting the plan. Do not start implementation or destructive tool execution "
            "until the user explicitly confirms the plan."
            if is_plan_mode and not approval_context
            else "A previously proposed plan has been approved. Continue from the current todo list and execute the work."
            if is_plan_mode and approval_context
            else None
        ),
        promoted_capabilities=promoted_capabilities,
    )
    mark_phase("turn_injection_built")
    system_prompt = compose_system_prompt(prompt_snapshot, prompt_injection_view)
    mark_phase("system_prompt_composed")
    context = LeadAgentContext(
        thread_id=thread_id,
        run_id=run_id,
        active_model_name=resolved_route.model_name,
        active_reasoning_effort=resolved_route.reasoning_effort,
        path_service=path_service,
        sandbox_provider=sandbox_provider,
        capability_bundle=capability_bundle,
        request_context=request_context,
        approval_context=approval_context,
        execution_mode=execution_mode.value,
        upload_context=upload_context,
        promoted_capabilities=promoted_capabilities,
        memory_context=None,
        memory_namespace=effective_config.memory.namespace if legacy_memory_enabled else None,
        enabled_skill_ids=capability_bundle.enabled_skill_ids,
        extension_statuses=capability_bundle.effective_extension_sources,
        initial_uploaded_files=tuple(),
        recent_upload_filenames=recent_upload_filenames,
        existing_thread_title=None,
        current_title=None,
        sandbox_handle=None,
        memory_service=memory_service,
        memory_manager=memory_manager,
        tool_registry=registry,
        skills_service=skills_service,
        extensions_service=extensions_service,
        subagent_service=subagent_service,
        process_service=process_service,
        scheduled_task_service=scheduled_task_service,
        approval_service=approval_service,
        capability_service=capability_service,
        parent_visible_tool_names=parent_visible_tool_names,
        tracing_service=tracing_service,
        run_trace_id=run_trace_id,
        promotion_state=assembly_result.promotion_state if execution_mode is not ThreadExecutionMode.CHAT else set(),
        config_result=config_result,
        feature_set=feature_set,
        prompt_snapshot=prompt_snapshot,
        project_context_files=tuple(
            {
                "virtual_path": item.virtual_path,
                "relative_path": item.relative_path,
                "applies_to": item.applies_to,
                "scope": item.scope,
                "truncated": item.truncated,
            }
            for item in project_context_snapshot.files
        ),
        project_context_fingerprint=project_context_snapshot.fingerprint,
        runtime_path_fingerprint=runtime_path_snapshot.fingerprint,
        runtime_path_cache_status=runtime_path_snapshot.cache_status,
        is_plan_mode=is_plan_mode,
    )
    mark_phase("lead_context_built")
    middleware_chain = build_middleware_chain(
        feature_set,
        middleware=middleware,
        extra_middlewares=extra_middlewares,
        subagent_limit_max_concurrency=effective_config.subagents.max_concurrency,
        effective_config=effective_config,
    )
    mark_phase("middleware_chain_built")
    tools = [entry.handler for entry in capability_bundle.visible_tools if entry.handler is not None]
    chat_model = create_chat_model(
        config_result.effective_config.models[resolved_route.model_name],
        reasoning_effort_override=resolved_route.reasoning_effort,
        model_override=chat_model_override,
        tracing_service=tracing_service,
    )
    mark_phase("chat_model_created")

    agent = create_agent(
        model=chat_model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware_chain,
        state_schema=LeadAgentState,
        context_schema=LeadAgentContext,
        name="anvil_lead_agent",
    )
    mark_phase("langgraph_agent_created")
    assembly_snapshot = RuntimeAssemblySnapshot.from_runtime_parts(
        thread_id=thread_id,
        run_id=run_id,
        execution_mode=execution_mode.value,
        config_fingerprint=config_result.fingerprint,
        resolved_route=resolved_route,
        prompt_snapshot=prompt_snapshot,
        prompt_injection_view=prompt_injection_view,
        project_context_snapshot=project_context_snapshot,
        runtime_path_snapshot=runtime_path_snapshot,
        capability_bundle=capability_bundle,
        middleware_chain=middleware_chain,
        feature_set=feature_set,
        prompt_cache_before=prompt_cache_before,
        prompt_cache_after=prompt_cache_after,
        service_flags={
            "approval_service": approval_service is not None,
            "extensions_service": extensions_service is not None,
            "legacy_memory_service": memory_service is not None,
            "memory_manager": memory_manager is not None,
            "process_service": process_service is not None,
            "scheduled_task_service": scheduled_task_service is not None,
            "skills_service": skills_service is not None,
            "subagent_service": subagent_service is not None,
            "tracing_service": tracing_service is not None,
        },
    )
    mark_phase("assembly_snapshot_built")

    return LeadAgentRuntime(
        agent=agent,
        resolved_route=resolved_route,
        assembly_snapshot=assembly_snapshot,
        prompt_snapshot=prompt_snapshot,
        prompt_injection_view=prompt_injection_view,
        system_prompt=system_prompt,
        middleware_chain=middleware_chain,
        tools=tools,
        tool_registry=registry,
        capability_bundle=capability_bundle,
        chat_model=chat_model,
        context=context,
        feature_set=feature_set,
        checkpointer=checkpointer,
        store=store,
    )


def _append_feature(
    chain: list[Any],
    spec: bool | Any,
    default_factory,
) -> None:
    if spec is False:
        return
    if spec is True:
        chain.append(default_factory())
        return
    chain.append(spec)


def _ensure_clarification_last(chain: list[Any]) -> None:
    clarification_indexes = [
        index for index, middleware in enumerate(chain)
        if isinstance(middleware, ClarificationMiddleware)
    ]
    if not clarification_indexes:
        return
    clarification = chain.pop(clarification_indexes[-1])
    chain.append(clarification)


def _insert_extra_middlewares(chain: list[Any], extra_middlewares: list[Any]) -> None:
    next_targets: dict[type[Any], type[Any]] = {}
    prev_targets: dict[type[Any], type[Any]] = {}
    anchored: list[tuple[Any, str, type[Any]]] = []
    unanchored: list[Any] = []

    for middleware in extra_middlewares:
        next_anchor = getattr(type(middleware), "_next_anchor", None)
        prev_anchor = getattr(type(middleware), "_prev_anchor", None)
        if next_anchor and prev_anchor:
            raise ValueError(f"{type(middleware).__name__} cannot declare both @Next and @Prev anchors")
        if next_anchor:
            if next_anchor in next_targets or next_anchor in prev_targets:
                raise ValueError(f"Conflicting @Next/@Prev anchor for {next_anchor.__name__}")
            next_targets[next_anchor] = type(middleware)
            anchored.append((middleware, "next", next_anchor))
        elif prev_anchor:
            if prev_anchor in next_targets or prev_anchor in prev_targets:
                raise ValueError(f"Conflicting @Next/@Prev anchor for {prev_anchor.__name__}")
            prev_targets[prev_anchor] = type(middleware)
            anchored.append((middleware, "prev", prev_anchor))
        else:
            unanchored.append(middleware)

    clarification_index = next(
        (index for index, middleware in enumerate(chain) if isinstance(middleware, ClarificationMiddleware)),
        len(chain),
    )
    for middleware in unanchored:
        chain.insert(clarification_index, middleware)
        clarification_index += 1

    pending = list(anchored)
    max_rounds = len(pending) + 1
    for _ in range(max_rounds):
        if not pending:
            break
        next_round: list[tuple[Any, str, type[Any]]] = []
        for middleware, direction, anchor in pending:
            anchor_index = next(
                (index for index, item in enumerate(chain) if isinstance(item, anchor)),
                None,
            )
            if anchor_index is None:
                next_round.append((middleware, direction, anchor))
                continue
            insert_at = anchor_index + 1 if direction == "next" else anchor_index
            chain.insert(insert_at, middleware)
        if len(next_round) == len(pending):
            unresolved = ", ".join(type(middleware).__name__ for middleware, _, _ in next_round)
            raise ValueError(f"Unresolved middleware anchors: {unresolved}")
        pending = next_round


def build_middleware_chain(
    feature_set: RuntimeFeatureSet,
    *,
    middleware: list[Any] | None = None,
    extra_middlewares: list[Any] | None = None,
    subagent_limit_max_concurrency: int | None = None,
    effective_config: Any = None,
) -> list[Any]:
    if middleware is None:
        middleware = list(feature_set.middleware or ())
    if middleware:
        chain = list(middleware)
        _ensure_clarification_last(chain)
        return chain

    memory_capture_spec = feature_set.memory_capture

    subagent_limit_spec = feature_set.subagent_limit
    if subagent_limit_spec is False and feature_set.subagents and "subagent_limit" not in feature_set.model_fields_set:
        subagent_limit_spec = True

    middlewares: list[Any] = []
    # Add timing middleware first to track all operations
    # middlewares.append(TimingMiddleware())  # Temporarily disabled

    _append_feature(middlewares, feature_set.thread_data, ThreadDataMiddleware)
    _append_feature(middlewares, feature_set.uploads, UploadsMiddleware)
    _append_feature(middlewares, feature_set.sandboxing, SandboxMiddleware)

    _append_feature(middlewares, feature_set.dangling_tool_calls, DanglingToolCallMiddleware)
    _append_feature(middlewares, feature_set.llm_error_handling, LLMErrorHandlingMiddleware)
    _append_feature(middlewares, feature_set.guardrails, GuardrailMiddleware)
    _append_feature(middlewares, feature_set.sandbox_audit, SandboxAuditMiddleware)
    _append_feature(middlewares, feature_set.tool_error_shaping, ToolErrorHandlingMiddleware)
    _append_feature(middlewares, feature_set.tool_output_budget, ToolOutputBudgetMiddleware)

    _append_feature(middlewares, feature_set.summarization, SummarizationMiddleware)
    _append_feature(middlewares, feature_set.plan_mode, TodoMiddleware)
    _append_feature(middlewares, feature_set.token_usage, TokenUsageMiddleware)
    _append_feature(middlewares, feature_set.title, TitleMiddleware)
    _append_feature(middlewares, feature_set.memory_prefetch, MemoryPrefetchMiddleware)

    # Add JIT context middleware before compaction
    # JIT loads minimal context, compaction compresses history
    if feature_set.jit_context and effective_config:
        if feature_set.jit_context is True:
            middlewares.append(JITContextMiddleware(config=effective_config.jit_context))
        else:
            middlewares.append(feature_set.jit_context)

    # Add compaction middleware after memory prefetch, before model call
    # This ensures memory is loaded before compaction and compacted context is used for model
    if feature_set.compaction and effective_config:
        if feature_set.compaction is True:
            middlewares.append(CompactionMiddleware(config=effective_config.compaction))
        else:
            middlewares.append(feature_set.compaction)

    _append_feature(middlewares, memory_capture_spec, MemoryCaptureMiddleware)
    _append_feature(middlewares, feature_set.view_image, ViewImageMiddleware)
    _append_feature(middlewares, feature_set.tool_visibility, ToolVisibilityMiddleware)
    _append_feature(middlewares, feature_set.deferred_tool_filter, DeferredToolFilterMiddleware)
    if subagent_limit_spec is not False:
        spec = subagent_limit_spec
        if spec is True:
            middlewares.append(SubagentLimitMiddleware(max_concurrent=subagent_limit_max_concurrency or 3))
        elif spec is not False:
            middlewares.append(spec)
    if feature_set.loop_detection is not False:
        loop_config = effective_config.loop_detection if effective_config is not None else None
        if loop_config is not None and not loop_config.enabled and feature_set.loop_detection is True:
            pass
        elif feature_set.loop_detection is True:
            middlewares.append(
                LoopDetectionMiddleware(
                    warn_threshold=loop_config.warn_threshold if loop_config is not None else 12,
                    hard_limit=loop_config.hard_limit if loop_config is not None else 24,
                    window_size=loop_config.window_size if loop_config is not None else 80,
                    max_tracked_runs=loop_config.max_tracked_runs if loop_config is not None else 200,
                )
            )
        else:
            middlewares.append(feature_set.loop_detection)

    _append_feature(middlewares, feature_set.clarification, ClarificationMiddleware)
    extra = list(feature_set.extra_middlewares)
    if extra_middlewares:
        extra.extend(extra_middlewares)
    if extra:
        _insert_extra_middlewares(middlewares, extra)
    _ensure_clarification_last(middlewares)
    return middlewares
