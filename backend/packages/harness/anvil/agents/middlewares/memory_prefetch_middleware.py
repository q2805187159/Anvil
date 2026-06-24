from __future__ import annotations

import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from anvil.agents.lead_agent.prompt import PromptInjectionView, PromptSection
from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState, MemoryInjectionDiagnostics
from anvil.memory.contracts import sanitize_memory_context_text
from anvil.memory.hcms_v2 import memory_injection_view_v2_from_legacy, memory_injection_view_v2_to_blocks
from anvil.runtime.context_v2 import (
    AttentionBudget,
    CompressionPolicy,
    ContextBlock,
    ContextAssemblyTrace,
    ContextSource,
    ContextSourceKind,
    EvidenceRef,
    InjectionPolicy,
    ContextBlockTrace,
    capability_bundle_to_blocks,
    prompt_injection_view_to_blocks,
    prompt_section_to_block,
    prompt_snapshot_to_blocks,
    stable_context_id,
    stable_prompt_hash,
)
from anvil.runtime.state_v2 import EventLog, RuntimeEventBus, TurnPipeline, TurnPipelineInput
from anvil.runtime.token_budget import TokenBudgetService


_CONTEXT_V2_MEMORY_CONTEXT_MODES = {
    "",
    "context_v2",
    "context_v2_only",
    "runtime_context_v2",
    "block_assembly",
}
_LEGACY_MEMORY_CONTEXT_MODES = {
    "legacy",
    "legacy_append",
    "legacy_prompt_append",
    "memory_context",
    "memory_prompt",
    "prompt_append",
    "v1",
}
_DISABLED_MEMORY_CONTEXT_MODES = {"0", "disabled", "false", "no", "none", "off"}
_MEMORY_FENCE_TAG_PATTERN = re.compile(r"</?memory(?:_[a-z0-9_-]+)?(?:\s+[^>]*)?>", re.IGNORECASE)


class MemoryPrefetchMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def before_model(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        memory_manager = runtime.context.memory_manager
        if memory_manager is not None:
            query = ""
            for message in reversed(state_obj.messages):
                if getattr(message, "type", "") == "human":
                    content = getattr(message, "content", "")
                    query = content if isinstance(content, str) else str(content)
                    break
            try:
                recall = memory_manager.prefetch_recall(
                    thread_id=runtime.context.thread_id,
                    query=query,
                )
            except Exception as exc:
                recall = None
                runtime.context.memory_injection_diagnostics = MemoryInjectionDiagnostics(
                    source="memory_manager",
                    status="error",
                    query_tokens=TokenBudgetService().count_text(query),
                    token_budget=getattr(memory_manager.config.recall, "turn_recall_token_budget", 900),
                    error_type=exc.__class__.__name__,
                ).model_dump(mode="json")
            if recall is not None:
                memory_payload = recall.render_turn_block()
                if memory_payload:
                    memory_context = (
                        "Dynamic memory recall is bounded fact lookup for this turn. "
                        "Use it as evidence hints and prefer current user instructions when they conflict.\n"
                        f"{memory_payload}"
                    )
                else:
                    memory_context = ""
                token_budget = TokenBudgetService()
                max_tokens = getattr(memory_manager.config.recall, "turn_recall_token_budget", 900)
                original_memory_context = memory_context
                rendered_tokens_before = token_budget.count_text(original_memory_context)
                memory_context = token_budget.truncate_text(
                    original_memory_context,
                    max_tokens=max_tokens,
                )
                diagnostics = _recall_diagnostics(
                    recall,
                    query=query,
                    token_budget=max_tokens,
                    rendered_tokens_before_truncation=rendered_tokens_before,
                    rendered_tokens=token_budget.count_text(memory_context),
                    truncated=memory_context != original_memory_context,
                )
                legacy_prompt_append_requested = _legacy_memory_prompt_append_enabled(runtime.context)
                if _memory_context_v2_enabled(runtime.context) or legacy_prompt_append_requested:
                    context_v2_memory_blocks = _recall_context_v2_memory_blocks(
                        recall,
                        query=query,
                        token_budget=token_budget,
                        memory_context=memory_context,
                    )
                    diagnostics_payload = diagnostics.model_dump(mode="json")
                    diagnostics_payload["injection_mode"] = "context_v2"
                    diagnostics_payload["context_v2_block_count"] = len(context_v2_memory_blocks)
                    if legacy_prompt_append_requested:
                        diagnostics_payload["requested_injection_mode"] = (
                            _memory_context_mode(runtime.context) or "legacy_prompt_append"
                        )
                        diagnostics_payload["legacy_prompt_append_migrated"] = True
                    runtime.context.context_v2_memory_blocks = context_v2_memory_blocks
                    runtime.context.memory_context = None
                    runtime.context.memory_injection_diagnostics = diagnostics_payload
                    return {
                        "memory_snapshot_id": recall.snapshot_fingerprint,
                        "context_v2_memory_blocks": context_v2_memory_blocks,
                        "memory_injection_diagnostics": diagnostics_payload,
                    }
                diagnostics_payload = diagnostics.model_dump(mode="json")
                runtime.context.memory_injection_diagnostics = diagnostics_payload
                runtime.context.memory_context = None
                return {"memory_injection_diagnostics": diagnostics_payload}

        return None

    def wrap_model_call(self, request, handler):
        if _memory_context_v2_enabled(request.runtime.context):
            request = _inject_context_v2_memory_blocks(request)
            return handler(request)
        if _legacy_memory_prompt_append_enabled(request.runtime.context) and request.runtime.context.memory_context:
            diagnostics = dict(getattr(request.runtime.context, "memory_injection_diagnostics", {}) or {})
            diagnostics.update(
                {
                    "injection_mode": "context_v2",
                    "requested_injection_mode": _memory_context_mode(request.runtime.context)
                    or "legacy_prompt_append",
                    "legacy_prompt_append_migrated": True,
                    "legacy_prompt_append_suppressed": True,
                }
            )
            request.runtime.context.memory_injection_diagnostics = diagnostics
            request.runtime.context.memory_context = None
        return handler(request)


def _inject_context_v2_memory_blocks(request):
    context = request.runtime.context
    raw_blocks = list(getattr(context, "context_v2_memory_blocks", ()) or ())
    prompt_snapshot = getattr(context, "prompt_snapshot", None)
    if not raw_blocks and prompt_snapshot is None:
        return request
    try:
        token_budget = TokenBudgetService()
        thread_id = _context_thread_id(context)
        run_id = getattr(context, "run_id", None)
        system_prompt = request.system_prompt or ""
        user_text = _request_user_text(request, context)
        memory_blocks = [
            item if isinstance(item, ContextBlock) else ContextBlock.model_validate(item)
            for item in raw_blocks
        ]
        extra_blocks = [
            *_runtime_context_prompt_blocks(context, system_prompt, token_budget=token_budget),
            *_runtime_context_capability_blocks(context, query=user_text, token_budget=token_budget),
            *memory_blocks,
        ]
        stable_memory_blocks = [
            block
            for block in extra_blocks
            if (
                block.source.kind.value == "memory"
                and block.metadata.get("legacy_section") == "memory_snapshot"
            )
        ]
        total_memory_candidate_count = sum(
            1
            for block in extra_blocks
            if block.source.kind.value == "memory"
            and block.block_type
            in {
                "memory",
                "semantic_fact",
                "episodic_summary",
                "procedural_hint",
                "wisdom_warning",
                "retrieved_memory",
            }
        )
        event_log = getattr(context, "event_log", None) or EventLog(thread_id=thread_id)
        event_bus = getattr(context, "runtime_event_bus", None) or RuntimeEventBus(event_log=event_log)
        _set_context_attr_if_missing(context, "event_log", event_log)
        _set_context_attr_if_missing(context, "runtime_event_bus", event_bus)

        pipeline_result = TurnPipeline(event_bus=event_bus, token_budget=token_budget).prepare_llm_context(
            TurnPipelineInput(
                thread_id=thread_id,
                run_id=run_id,
                turn_id=_context_turn_id(context, user_text=user_text, system_prompt=system_prompt),
                user_text=user_text,
                goal_stack=getattr(context, "goal_stack", None),
                salience_route=getattr(context, "salience_route", None),
                workspace_state=getattr(context, "workspace_state", None),
                tool_result_store=getattr(context, "tool_result_store", None),
                review_inbox=getattr(context, "review_inbox", None),
                extra_blocks=extra_blocks,
                budget=_runtime_context_budget(context),
                metadata={
                    "source": "memory_prefetch",
                    "injection_mode": "context_v2",
                    "actual_prompt_mode": "runtime_context_v2",
                    "legacy_system_prompt_hash": stable_prompt_hash(system_prompt),
                    "prompt_snapshot_id": getattr(getattr(context, "prompt_snapshot", None), "snapshot_id", None),
                    "capability_bundle_fingerprint": getattr(
                        getattr(context, "capability_bundle", None),
                        "fingerprint",
                        None,
                    ),
                },
            )
        )
    except Exception as exc:
        fallback_prompt, fallback_trace = _runtime_context_v2_emergency_fallback(
            context,
            request.system_prompt or "",
            error=exc,
            token_budget=TokenBudgetService(),
        )
        actual_system_prompt_hash = stable_prompt_hash(fallback_prompt)
        diagnostics = dict(getattr(context, "memory_injection_diagnostics", {}) or {})
        diagnostics.update(
            {
                "injection_mode": "context_v2",
                "context_v2_assembly_error": exc.__class__.__name__,
                "context_v2_emergency_fallback": True,
            }
        )
        context.memory_injection_diagnostics = diagnostics
        runtime_context = dict(getattr(context, "context_v2", {}) or {})
        trace_payload = fallback_trace.model_dump(mode="json")
        runtime_context.update(
            {
                "enabled": True,
                "diagnostic_only": False,
                "fallback_used": True,
                "emergency_fallback_used": True,
                "candidate_block_count": len(fallback_trace.candidate_block_ids),
                "selected_block_count": len(fallback_trace.selected_block_ids),
                "rendered_context_hash": actual_system_prompt_hash,
                "actual_system_prompt_hash": actual_system_prompt_hash,
                "legacy_system_prompt_hash": stable_prompt_hash(request.system_prompt or ""),
                "actual_prompt_mode": "runtime_context_v2_emergency_fallback",
                "assembled_context_token_count": fallback_trace.total_tokens,
                "trace": trace_payload,
                "memory_prefetch_trace": trace_payload,
                "memory_prefetch_fallback_used": True,
                "memory_prefetch_context_hash": fallback_trace.prompt_hash,
                "turn_pipeline": {
                    "enabled": True,
                    "fallback_used": True,
                    "event_count": 0,
                    "event_types": (),
                    "event_refs": (),
                    "error_type": exc.__class__.__name__,
                },
            }
        )
        context.context_v2 = runtime_context
        _sync_runtime_assembly_snapshot(request.runtime, runtime_context, diagnostics)
        return request.override(system_message=SystemMessage(content=fallback_prompt))

    assembled = pipeline_result.assembled_context
    actual_system_prompt_hash = stable_prompt_hash(assembled.rendered_context)
    assembled.trace.metadata["actual_system_prompt_hash"] = actual_system_prompt_hash
    trace_payload = assembled.trace.model_dump(mode="json")
    event_payload = [
        event.model_dump(mode="json")
        for event in _context_event_log_events(context, fallback=pipeline_result.events)
    ]
    turn_state_payload = pipeline_result.turn_state.model_dump(mode="json")
    turn_pipeline_payload = {
        "enabled": True,
        "turn_state": turn_state_payload,
        "event_count": len(event_payload),
        "event_types": [event["event_type"] for event in event_payload],
        "event_refs": [event["event_id"] for event in event_payload],
    }
    runtime_context = dict(getattr(context, "context_v2", {}) or {})
    runtime_context.update(
        {
            "enabled": True,
            "diagnostic_only": False,
            "fallback_used": bool(assembled.fallback_used),
            "candidate_block_count": len(pipeline_result.candidate_blocks),
            "selected_block_count": len(assembled.blocks),
            "hcms_v2_memory_candidate_count": len(memory_blocks),
            "hcms_v2_memory_block_ids": [block.block_id for block in memory_blocks],
            "stable_memory_block_ids": [block.block_id for block in stable_memory_blocks],
            "total_memory_candidate_count": total_memory_candidate_count,
            "candidate_block_titles": [block.title for block in pipeline_result.candidate_blocks],
            "selected_block_titles": [block.title for block in assembled.blocks],
            "rendered_context_hash": actual_system_prompt_hash,
            "actual_system_prompt_hash": actual_system_prompt_hash,
            "legacy_system_prompt_hash": stable_prompt_hash(request.system_prompt or ""),
            "actual_prompt_mode": "runtime_context_v2",
            "assembled_context_token_count": assembled.trace.total_tokens,
            "trace": trace_payload,
            "memory_prefetch_trace": trace_payload,
            "memory_prefetch_fallback_used": bool(assembled.fallback_used),
            "memory_prefetch_context_hash": assembled.trace.prompt_hash,
            "turn_pipeline": turn_pipeline_payload,
            "turn_state": turn_state_payload,
            "event_log": event_payload,
        }
    )
    context.context_v2 = runtime_context
    diagnostics = dict(getattr(context, "memory_injection_diagnostics", {}) or {})
    diagnostics.update(
        {
            "injection_mode": "context_v2",
            "context_v2_block_count": len(memory_blocks),
            "context_v2_candidate_block_count": len(pipeline_result.candidate_blocks),
            "context_v2_selected_memory_count": len(trace_payload.get("selected_memory", ())),
        }
    )
    context.memory_injection_diagnostics = diagnostics
    _sync_runtime_assembly_snapshot(request.runtime, runtime_context, diagnostics)
    return request.override(system_message=SystemMessage(content=assembled.rendered_context))


def _memory_context_v2_enabled(context: LeadAgentContext) -> bool:
    mode = _memory_context_mode(context)
    if mode in _DISABLED_MEMORY_CONTEXT_MODES:
        return False
    if getattr(context, "context_v2_memory_blocks", None):
        return True
    if bool(getattr(context, "context_v2_memory_enabled", False)):
        return True
    if mode in _LEGACY_MEMORY_CONTEXT_MODES:
        return False
    return mode in _CONTEXT_V2_MEMORY_CONTEXT_MODES or bool(mode)


def _legacy_memory_prompt_append_enabled(context: LeadAgentContext) -> bool:
    return _memory_context_mode(context) in _LEGACY_MEMORY_CONTEXT_MODES


def _memory_context_mode(context: LeadAgentContext) -> str:
    return str(getattr(context, "memory_context_mode", "") or "").strip().lower().replace("-", "_")


def _recall_context_v2_memory_blocks(
    recall,
    *,
    query: str,
    token_budget: TokenBudgetService,
    memory_context: str = "",
) -> list[dict[str, Any]]:
    injection = getattr(recall, "injection", None)
    if injection is not None:
        view = memory_injection_view_v2_from_legacy(injection, query=query, token_budget=token_budget)
        blocks = [
            block.model_dump(mode="json")
            for block in memory_injection_view_v2_to_blocks(view, token_budget=token_budget)
        ]
        if blocks:
            return blocks
    fallback_block = _legacy_recall_memory_context_to_block(
        recall,
        query=query,
        memory_context=memory_context,
        token_budget=token_budget,
    )
    return [fallback_block.model_dump(mode="json")] if fallback_block is not None else []


def _legacy_recall_memory_context_to_block(
    recall,
    *,
    query: str,
    memory_context: str,
    token_budget: TokenBudgetService,
) -> ContextBlock | None:
    content = _strip_memory_fence_tags(memory_context).strip()
    if not content:
        return None
    snapshot_id = str(getattr(recall, "snapshot_fingerprint", "") or "")
    namespace = str(getattr(recall, "namespace", "") or "memory")
    block_id = stable_context_id("memory-recall", namespace, snapshot_id, query, content)
    evidence_refs = _recall_evidence_refs(recall, fallback_ref=block_id)
    return ContextBlock(
        block_id=block_id,
        block_type="retrieved_memory",
        source=ContextSource(
            kind=ContextSourceKind.MEMORY,
            name=namespace,
            ref=snapshot_id or None,
            metadata={"legacy_prompt_append_migrated": True},
        ),
        title="Memory Recall",
        content=content,
        token_cost=token_budget.count_text(content),
        priority=0.76,
        salience=0.72,
        confidence=_recall_confidence(recall),
        position_hint="memory:retrieved",
        evidence_refs=evidence_refs,
        privacy_level="internal",
        injection_policy=InjectionPolicy(allow=True, protected=False, reason="legacy_recall_migrated_to_context_v2"),
        compression_policy=CompressionPolicy(
            allow_compression=True,
            allow_reference=True,
            ref=f"memory_recall:{snapshot_id or block_id}",
            summary="Legacy recall text migrated into Runtime Context V2.",
        ),
        tags=("legacy_prompt_append_migrated",),
        metadata={
            "namespace": namespace,
            "memory_snapshot_id": snapshot_id or None,
            "legacy_prompt_append_migrated": True,
        },
    )


def _strip_memory_fence_tags(value: str) -> str:
    text = _MEMORY_FENCE_TAG_PATTERN.sub("", str(value or ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return sanitize_memory_context_text(text)


def _recall_evidence_refs(recall, *, fallback_ref: str) -> tuple[EvidenceRef, ...]:
    refs: list[EvidenceRef] = []
    for index, item in enumerate(getattr(recall, "evidence", ()) or ()):
        ref_id = str(
            getattr(item, "evidence_id", None)
            or getattr(item, "source_id", None)
            or stable_context_id("memory-evidence", fallback_ref, index)
        )
        refs.append(
            EvidenceRef(
                ref_id=ref_id,
                source_kind=str(getattr(item, "source_kind", "") or "memory"),
                source_id=str(getattr(item, "source_id", "") or "") or None,
                confidence=_numeric_score(getattr(item, "score", None), default=0.5),
                metadata={"reason": str(getattr(item, "reason", "") or "")},
            )
        )
    if refs:
        return tuple(refs[:8])
    return (
        EvidenceRef(
            ref_id=fallback_ref,
            source_kind="legacy_memory_recall",
            source_id=str(getattr(recall, "snapshot_fingerprint", "") or "") or None,
            confidence=_recall_confidence(recall),
        ),
    )


def _recall_confidence(recall) -> float:
    evidence = tuple(getattr(recall, "evidence", ()) or ())
    if not evidence:
        return 0.55
    scores = [_numeric_score(getattr(item, "score", None), default=0.55) for item in evidence]
    return round(sum(scores) / len(scores), 4)


def _numeric_score(value: Any, *, default: float) -> float:
    try:
        numeric = float(default if value is None else value)
    except (TypeError, ValueError):
        numeric = default
    return min(max(numeric, 0.0), 1.0)


def _runtime_context_prompt_blocks(
    context: LeadAgentContext,
    system_prompt: str,
    *,
    token_budget: TokenBudgetService,
) -> list[ContextBlock]:
    prompt_snapshot = getattr(context, "prompt_snapshot", None)
    if prompt_snapshot is not None:
        namespace = str(getattr(context, "memory_namespace", None) or "default")
        injections = PromptInjectionView(
            request_context=getattr(context, "request_context", None),
            upload_context=getattr(context, "upload_context", None),
            approval_context=getattr(context, "approval_context", None),
            plan_context=_plan_context_for_context(context),
            memory_context=None,
            promoted_capabilities=tuple(getattr(context, "promoted_capabilities", ()) or ()),
        )
        return [
            *prompt_snapshot_to_blocks(prompt_snapshot, token_budget=token_budget),
            *prompt_injection_view_to_blocks(
                injections,
                namespace=namespace,
                token_budget=token_budget,
            ),
        ]

    stripped_prompt = _strip_legacy_memory_sections(system_prompt).strip()
    if not stripped_prompt:
        return []
    return [
        prompt_section_to_block(
            PromptSection(name="legacy_system_prompt", content=stripped_prompt),
            stable=True,
            token_budget=token_budget,
            metadata={
                "source": "legacy_system_prompt",
                "legacy_system_prompt_hash": stable_prompt_hash(system_prompt),
            },
        )
    ]


def _runtime_context_capability_blocks(
    context: LeadAgentContext,
    *,
    query: str,
    token_budget: TokenBudgetService,
) -> list[ContextBlock]:
    capability_bundle = getattr(context, "capability_bundle", None)
    if capability_bundle is None:
        return []
    return capability_bundle_to_blocks(
        capability_bundle,
        top_k=12,
        query=query,
        token_budget=token_budget,
    )


def _runtime_context_budget(context: LeadAgentContext) -> AttentionBudget:
    existing = getattr(context, "context_v2", {}) or {}
    trace = existing.get("trace", {}) if isinstance(existing, dict) else {}
    budget = trace.get("budget", {}) if isinstance(trace, dict) else {}
    try:
        max_context_tokens = int(budget.get("max_context_tokens") or 32768)
    except (TypeError, ValueError):
        max_context_tokens = 32768
    try:
        reserved_response_tokens = int(budget.get("reserved_response_tokens") or 0)
    except (TypeError, ValueError):
        reserved_response_tokens = 0
    return AttentionBudget(
        max_context_tokens=max_context_tokens,
        reserved_response_tokens=reserved_response_tokens,
    )


def _runtime_context_v2_emergency_fallback(
    context: LeadAgentContext,
    system_prompt: str,
    *,
    error: Exception,
    token_budget: TokenBudgetService,
) -> tuple[str, ContextAssemblyTrace]:
    stripped_prompt = sanitize_memory_context_text(_strip_legacy_memory_sections(system_prompt)).strip()
    if not stripped_prompt:
        stripped_prompt = "Runtime Context V2 emergency fallback: no reusable system prompt was available."
    content = token_budget.truncate_text(stripped_prompt, max_tokens=1800, max_chars=9000)
    block_id = stable_context_id("prompt-emergency", _context_thread_id(context), content)
    trace_id = stable_context_id("ctx-trace-emergency", _context_thread_id(context), block_id)
    token_count = token_budget.count_text(content)
    rendered = "\n".join(
        [
            '<runtime_context_v2 version="p0">',
            '  <context_diagnostics mode="emergency_fallback" '
            f'error_type="{_xml_escape(error.__class__.__name__)}" />',
            f'  <context_block id="{_xml_escape(block_id)}" type="system_prompt" '
            'source="legacy_system_prompt" protected="true">',
            _indent_xml_text(content, spaces=4),
            "  </context_block>",
            "</runtime_context_v2>",
        ]
    )
    prompt_hash = stable_prompt_hash(rendered)
    block_trace = ContextBlockTrace(
        block_id=block_id,
        block_type="system_prompt",
        source_kind=ContextSourceKind.PROMPT.value,
        token_cost=token_count,
        selected=True,
        reason="runtime_context_v2_emergency_fallback",
        score=1.0,
    )
    trace = ContextAssemblyTrace(
        trace_id=trace_id,
        prompt_hash=prompt_hash,
        candidate_block_ids=(block_id,),
        selected_block_ids=(block_id,),
        layer_token_usage={"system_prompt": token_count},
        block_traces=(block_trace,),
        total_tokens=token_count,
        budget=_runtime_context_budget(context),
        metadata={
            "source": "memory_prefetch",
            "actual_prompt_mode": "runtime_context_v2_emergency_fallback",
            "error_type": error.__class__.__name__,
            "legacy_system_prompt_hash": stable_prompt_hash(system_prompt),
        },
    )
    return rendered, trace


def _xml_escape(value: object) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _indent_xml_text(value: str, *, spaces: int) -> str:
    indent = " " * spaces
    lines = str(value or "").splitlines() or [""]
    return "\n".join(f"{indent}{_xml_escape(line)}" for line in lines)


def _request_user_text(request, context: LeadAgentContext) -> str:
    messages = list(getattr(request, "messages", ()) or ())
    for message in reversed(messages):
        if getattr(message, "type", "") == "human":
            content = getattr(message, "content", "")
            return content if isinstance(content, str) else str(content)
    request_context = getattr(context, "request_context", None)
    if request_context:
        return str(request_context)
    return "runtime context v2 memory prefetch"


def _context_thread_id(context: LeadAgentContext) -> str:
    return str(getattr(context, "thread_id", "") or "thread")


def _context_turn_id(
    context: LeadAgentContext,
    *,
    user_text: str,
    system_prompt: str,
) -> str:
    run_id = getattr(context, "run_id", None)
    if run_id:
        return str(run_id)
    return stable_context_id(
        "turn",
        _context_thread_id(context),
        user_text,
        stable_prompt_hash(system_prompt),
    )


def _plan_context_for_context(context: LeadAgentContext) -> str | None:
    if not getattr(context, "is_plan_mode", False):
        return None
    if getattr(context, "approval_context", None):
        return "A previously proposed plan has been approved. Continue from the current todo list and execute the work."
    return (
        "Plan mode is active. This turn is for planning first. "
        "Produce a concise execution plan, update the todo list with write_todos, "
        "and stop after presenting the plan. Do not start implementation or destructive tool execution "
        "until the user explicitly confirms the plan."
    )


def _strip_legacy_memory_sections(system_prompt: str) -> str:
    stripped = str(system_prompt or "")
    for tag in ("memory_context", "memory_recall"):
        stripped = re.sub(
            rf"\n{{0,2}}<{tag}>.*?</{tag}>\n{{0,2}}",
            "\n\n",
            stripped,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


def _context_event_log_events(context: LeadAgentContext, *, fallback) -> tuple[Any, ...]:
    event_log = getattr(context, "event_log", None)
    events = getattr(event_log, "events", None)
    if events is not None:
        return tuple(events)
    return tuple(fallback or ())


def _set_context_attr_if_missing(context: LeadAgentContext, name: str, value: Any) -> None:
    if getattr(context, name, None) is None:
        try:
            setattr(context, name, value)
        except Exception:
            return


def _sync_runtime_assembly_snapshot(runtime, context_v2: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    snapshot = getattr(runtime, "assembly_snapshot", None)
    if snapshot is None:
        return
    try:
        snapshot.context_v2 = dict(context_v2)
    except Exception:
        return
    try:
        snapshot.memory_injection_diagnostics = dict(diagnostics)
    except Exception:
        return


def _recall_diagnostics(
    recall,
    *,
    query: str,
    token_budget: int | None,
    rendered_tokens_before_truncation: int,
    rendered_tokens: int,
    truncated: bool,
) -> MemoryInjectionDiagnostics:
    store_counts: dict[str, int] = {}
    for entry in getattr(recall, "memory_matches", ()) or ():
        store_id = str(getattr(entry, "store_id", "") or "unknown")
        store_counts[store_id] = store_counts.get(store_id, 0) + 1
    source_kind_counts: dict[str, int] = {}
    for item in getattr(recall, "evidence", ()) or ():
        source_kind = str(getattr(item, "source_kind", "") or "unknown")
        source_kind_counts[source_kind] = source_kind_counts.get(source_kind, 0) + 1
    has_payload = any(
        (
            getattr(recall, "summary", None),
            getattr(recall, "memory_matches", ()),
            getattr(recall, "archive_hits", ()),
            getattr(recall, "engine_notes", ()),
            getattr(recall, "evidence", ()),
        )
    )
    return MemoryInjectionDiagnostics(
        source="memory_manager",
        status="injected" if rendered_tokens > 0 and has_payload else "empty",
        snapshot_id=getattr(recall, "snapshot_fingerprint", None),
        query_tokens=TokenBudgetService().count_text(query),
        memory_match_count=len(getattr(recall, "memory_matches", ()) or ()),
        archive_hit_count=len(getattr(recall, "archive_hits", ()) or ()),
        evidence_count=len(getattr(recall, "evidence", ()) or ()),
        engine_note_count=len(getattr(recall, "engine_notes", ()) or ()),
        rendered_tokens_before_truncation=rendered_tokens_before_truncation,
        rendered_tokens=rendered_tokens,
        token_budget=token_budget,
        truncated=truncated,
        store_counts=dict(sorted(store_counts.items())),
        source_kind_counts=dict(sorted(source_kind_counts.items())),
    )
