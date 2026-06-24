from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from anvil.memory.contracts import (
    Evidence,
    MemoryCaptureEnvelope,
    MemoryCategory,
    MemoryInjectionView,
    RetrievalResult,
    sanitize_memory_context_text,
)
from anvil.memory.scrubber import MemorySecretScrubber
from anvil.runtime.context_v2 import (
    CompressionPolicy,
    ContextBlock,
    ContextSource,
    ContextSourceKind,
    EvidenceRef,
    InjectionPolicy,
    stable_context_id,
)
from anvil.runtime.state_v2 import ConflictAlert
from anvil.runtime.token_budget import TokenBudgetService

from .contracts import (
    CaptureEnvelopeV2,
    CapabilityUsageEvent,
    ConflictRecord,
    ConsolidatedMemory,
    EvidenceSpan,
    MemoryInjectionViewV2,
    MemorySearchResult,
    ObservationRecord,
    ProcedurePattern,
    ProcedureStep,
    ProcedureWisdomMiningBatch,
    ProcedureWisdomMiningResult,
    RuntimeEventRef,
    WisdomInsight,
    bounded_score,
    stable_hcms_id,
)


def capture_envelope_v2_from_legacy(envelope: MemoryCaptureEnvelope) -> CaptureEnvelopeV2:
    metadata = dict(envelope.metadata or {})
    run_id = _optional_str(metadata.get("run_id"))
    turn_id = _optional_str(metadata.get("turn_id")) or stable_hcms_id(
        "turn",
        envelope.thread_id,
        envelope.timestamp.isoformat(),
        *envelope.user_messages,
        size=10,
    )
    runtime_events = [_runtime_event_ref_from_payload(item) for item in _list_of_mappings(metadata.get("runtime_event_refs"))]
    if not runtime_events:
        runtime_events = [
            RuntimeEventRef(
                event_id=stable_hcms_id("event", envelope.thread_id, index, text, size=12),
                event_type="user_message",
                source_ref=stable_hcms_id("msg", envelope.thread_id, index, text, size=12),
                payload_summary=str(text)[:240],
                actor="user",
                timestamp=envelope.timestamp,
            )
            for index, text in enumerate(envelope.user_messages)
        ]

    user_message_refs = [
        str(item.source_ref or item.event_id)
        for item in runtime_events
        if item.event_type == "user_message"
    ]
    if not user_message_refs:
        user_message_refs = [
            stable_hcms_id("msg", envelope.thread_id, index, text, size=12)
            for index, text in enumerate(envelope.user_messages)
        ]

    salience_seed = 0.25
    if envelope.user_messages:
        salience_seed += 0.2
    if envelope.final_assistant_messages:
        salience_seed += 0.1
    if envelope.explicit_corrections:
        salience_seed += 0.35
    if envelope.positive_reinforcement:
        salience_seed += 0.2

    envelope_id = stable_hcms_id(
        "capture_v2",
        envelope.memory_namespace,
        envelope.thread_id,
        run_id,
        turn_id,
        envelope.trace_id,
        *envelope.user_messages,
        *envelope.explicit_corrections,
        size=16,
    )
    return CaptureEnvelopeV2(
        envelope_id=envelope_id,
        namespace=envelope.memory_namespace,
        thread_id=envelope.thread_id,
        run_id=run_id,
        turn_id=turn_id,
        trace_id=envelope.trace_id,
        user_message_refs=user_message_refs,
        runtime_events=runtime_events,
        tool_result_refs=_string_list(metadata.get("tool_result_refs")),
        workspace_state_ref=_optional_str(metadata.get("workspace_state_ref")),
        goal_stack_ref=_optional_str(metadata.get("goal_stack_ref")),
        capability_usage_refs=_string_list(metadata.get("capability_usage_refs")),
        explicit_corrections=list(envelope.explicit_corrections),
        positive_reinforcement=list(envelope.positive_reinforcement),
        capture_reason="legacy_memory_capture",
        salience_seed=bounded_score(salience_seed),
        privacy_level=str(metadata.get("privacy_level") or "project"),
        created_at=envelope.timestamp,
        metadata={
            **metadata,
            "legacy_thread_id": envelope.thread_id,
            "legacy_message_counts": {
                "user": len(envelope.user_messages),
                "assistant": len(envelope.final_assistant_messages),
                "corrections": len(envelope.explicit_corrections),
                "reinforcement": len(envelope.positive_reinforcement),
            },
        },
    )


def runtime_event_to_capture_envelope(event: Any, *, namespace: str = "global/default") -> CaptureEnvelopeV2:
    event_id = str(_get(event, "event_id") or _get(event, "id") or stable_hcms_id("event", event, size=12))
    event_type = str(_get(event, "event_type") or _get(event, "type") or "runtime_event")
    thread_id = str(_get(event, "thread_id") or "default")
    run_id = _optional_str(_get(event, "run_id"))
    turn_id = str(_get(event, "turn_id") or stable_hcms_id("turn", thread_id, event_id, size=10))
    payload_summary = sanitize_memory_context_text(_event_payload_text(event))[:240]
    source_ref = _optional_str(_get(event, "source_ref"))
    runtime_event = RuntimeEventRef(
        event_id=event_id,
        event_type=event_type,
        source_ref=source_ref,
        payload_summary=payload_summary,
        payload_ref=_optional_str(_get(event, "payload_ref")),
        actor=str(_get(event, "actor") or "runtime"),
        privacy_level=str(_get(event, "privacy_level") or "project"),
        trust_level=str(_get(event, "trust_level") or "local_runtime"),
    )
    return CaptureEnvelopeV2(
        envelope_id=stable_hcms_id("capture_v2", namespace, thread_id, run_id, turn_id, event_id, size=16),
        namespace=namespace,
        thread_id=thread_id,
        run_id=run_id,
        turn_id=turn_id,
        trace_id=_optional_str(_get(event, "trace_id")),
        user_message_refs=[source_ref or event_id] if event_type == "user_message" else [],
        runtime_events=[runtime_event],
        tool_result_refs=_string_list(_get(event, "tool_result_refs")),
        workspace_state_ref=_optional_str(_get(event, "workspace_state_ref")),
        goal_stack_ref=_optional_str(_get(event, "goal_stack_ref")),
        capability_usage_refs=_string_list(_get(event, "capability_usage_refs")),
        capture_reason="runtime_event",
        salience_seed=0.65 if event_type in {"user_message", "tool_result", "state_change"} else 0.45,
        privacy_level=runtime_event.privacy_level,
        metadata={"source_event_id": event_id, "source_event_type": event_type},
    )


def observation_record_from_runtime_event(event: Any, *, namespace: str = "global/default") -> ObservationRecord:
    event_id = str(_get(event, "event_id") or _get(event, "id") or stable_hcms_id("event", event, size=12))
    event_type = str(_get(event, "event_type") or _get(event, "type") or "runtime_event")
    source_ref = _optional_str(_get(event, "source_ref") or _get(event, "source_id"))
    raw_content = _event_payload_text(event)
    scrubbed = MemorySecretScrubber().scrub(raw_content)
    content = sanitize_memory_context_text(raw_content)
    observation_id = stable_hcms_id("obs_v2", namespace, event_id, content, size=16)
    metadata = {
        "source_event_id": event_id,
        "source_event_type": event_type,
        "actor": str(_get(event, "actor") or "runtime"),
    }
    if scrubbed.rule_ids:
        metadata["detected_secret_rule_ids"] = list(scrubbed.rule_ids)
    evidence = EvidenceSpan(
        evidence_id=stable_hcms_id("ev_v2", observation_id, "payload", size=16),
        observation_id=observation_id,
        source_label=event_type,
        excerpt=content[:600],
        quoted_text_hash=stable_context_id("event-payload", content),
        trust_score=0.35 if str(_get(event, "trust_level") or "").lower() in {"external", "untrusted"} else 0.75,
        collector="runtime_event",
    )
    return ObservationRecord(
        observation_id=observation_id,
        namespace=namespace,
        thread_id=_optional_str(_get(event, "thread_id")),
        run_id=_optional_str(_get(event, "run_id")),
        event_id=event_id,
        observation_type=event_type,
        source_kind=str(_get(event, "source_kind") or _get(event, "actor") or event_type),
        source_id=source_ref,
        content=content,
        content_ref=_optional_str(_get(event, "payload_ref") or _get(event, "content_ref")),
        source_spans=[evidence],
        task_id=_optional_str(_get(event, "task_id")),
        goal_id=_optional_str(_get(event, "goal_id")),
        workspace_refs=_string_list(_get(event, "workspace_refs")),
        trust_level=str(_get(event, "trust_level") or "local_runtime"),
        privacy_level=str(_get(event, "privacy_level") or "project"),
        redaction_state="redacted" if scrubbed.rule_ids else "raw",
        metadata=metadata,
    )


def workspace_state_to_working_memory(
    workspace_state: Any,
    *,
    namespace: str = "global/default",
    token_budget: TokenBudgetService | None = None,
) -> ConsolidatedMemory:
    counter = token_budget or TokenBudgetService()
    workspace_id = str(_get(workspace_state, "workspace_id") or stable_hcms_id("workspace", workspace_state, size=12))
    thread_id = _optional_str(_get(workspace_state, "thread_id"))
    project_root = _optional_str(_get(workspace_state, "project_root"))
    active_files = _string_list(_get(workspace_state, "active_files"))[:12]
    variables = _workspace_variables(_get(workspace_state, "variables"))[:12]
    intermediate_results = _workspace_intermediate_results(_get(workspace_state, "intermediate_results"))[-8:]

    lines = [f"workspace_id={workspace_id}"]
    if thread_id:
        lines.append(f"thread_id={thread_id}")
    if project_root:
        lines.append(f"project_root={project_root}")
    if active_files:
        lines.append("active_files:")
        lines.extend(f"- {path}" for path in active_files)
    if variables:
        lines.append("variables:")
        lines.extend(f"- {key}={value}" for key, value in variables)
    if intermediate_results:
        lines.append("intermediate_results:")
        for item in intermediate_results:
            raw_ref = f" raw_ref={item['raw_ref']}" if item["raw_ref"] else ""
            result_ref = f" result_ref={item['result_ref']}" if item["result_ref"] else ""
            lines.append(
                f"- result_id={item['tool_result_id']} tool={item['tool_name']} "
                f"status={item['status']}{result_ref}{raw_ref} summary={item['summary']}"
            )

    canonical_content = counter.truncate_text(
        sanitize_memory_context_text("\n".join(lines)),
        max_tokens=240,
        max_chars=1800,
    )
    result_summaries = [item["summary"] for item in intermediate_results if item["summary"]]
    summary_parts = [f"Workspace state {workspace_id}"]
    if active_files:
        summary_parts.append(f"active_files={', '.join(active_files[:3])}")
    if variables:
        summary_parts.append("variables=" + ", ".join(f"{key}={value}" for key, value in variables[:3]))
    if result_summaries:
        summary_parts.append("recent_results=" + "; ".join(result_summaries[:3]))
    summary = counter.truncate_text(
        sanitize_memory_context_text(" | ".join(summary_parts)),
        max_tokens=100,
        max_chars=700,
    )
    observation_id = stable_hcms_id("obs_v2", namespace, workspace_id, "working", size=16)
    evidence = EvidenceSpan(
        evidence_id=stable_hcms_id("ev_v2", observation_id, "workspace_state", size=16),
        observation_id=observation_id,
        source_uri=f"workspace://{workspace_id}",
        source_label="workspace_state",
        excerpt=canonical_content[:600],
        quoted_text_hash=stable_context_id("workspace-state", canonical_content),
        trust_score=0.75,
        collector="workspace_state",
    )
    return ConsolidatedMemory(
        memory_id=stable_hcms_id("mem_v2", namespace, "working", workspace_id, canonical_content, size=16),
        namespace=namespace,
        layer="working",
        category="workspace_state",
        title=f"Workspace state {workspace_id}",
        summary=summary,
        canonical_content=canonical_content,
        evidence=[evidence],
        confidence=0.75,
        salience=0.7 if intermediate_results else 0.55,
        stability=0.35,
        metadata={
            "workspace_state_ref": workspace_id,
            "thread_id": thread_id,
            "project_root": project_root,
            "active_file_count": len(active_files),
            "variable_count": len(variables),
            "intermediate_result_count": len(intermediate_results),
            "source": "workspace_state",
        },
    )


def tool_result_record_to_episodic_memory(
    record: Any,
    *,
    namespace: str = "global/default",
    token_budget: TokenBudgetService | None = None,
) -> ConsolidatedMemory:
    counter = token_budget or TokenBudgetService()
    record_metadata_value = _get(record, "metadata")
    record_metadata = record_metadata_value if isinstance(record_metadata_value, Mapping) else {}
    summary_text = sanitize_memory_context_text(str(_get(record, "summary") or ""))
    tool_name = _optional_str(_get(record, "tool_name")) or "tool"
    status = _optional_str(_get(record, "status")) or "unknown"
    result_id = _optional_str(_get(record, "result_id")) or stable_hcms_id(
        "tool_result",
        namespace,
        tool_name,
        _optional_str(_get(record, "turn_id")),
        summary_text,
        size=16,
    )
    tool_call_id = _optional_str(_get(record, "tool_call_id"))
    capability_id = _optional_str(_get(record, "capability_id"))
    run_id = _optional_str(_get(record, "run_id"))
    turn_id = _optional_str(_get(record, "turn_id"))
    raw_ref = _optional_str(_get(record, "raw_ref"))
    workspace_ref = _optional_str(_get(record, "workspace_ref"))
    raw_size_chars = _optional_int(_get(record, "raw_size_chars")) or 0
    summary_size_chars = _optional_int(_get(record, "summary_size_chars")) or len(summary_text)
    compacted = _boolish(_get(record, "compacted"))

    bounded_summary = counter.truncate_text(
        summary_text or f"{tool_name} produced a {status} result.",
        max_tokens=120,
        max_chars=900,
    )
    canonical_lines = [
        f"tool_result_id={result_id}",
        f"tool={tool_name}",
        f"status={status}",
    ]
    if tool_call_id:
        canonical_lines.append(f"tool_call_id={tool_call_id}")
    if capability_id:
        canonical_lines.append(f"capability_id={capability_id}")
    if run_id:
        canonical_lines.append(f"run_id={run_id}")
    if turn_id:
        canonical_lines.append(f"turn_id={turn_id}")
    if workspace_ref:
        canonical_lines.append(f"workspace_ref={workspace_ref}")
    if raw_ref:
        canonical_lines.append(f"raw_ref={raw_ref}")
    canonical_lines.append(f"compacted={compacted}")
    canonical_lines.append("summary:")
    canonical_lines.append(bounded_summary)
    canonical_content = counter.truncate_text(
        sanitize_memory_context_text("\n".join(canonical_lines)),
        max_tokens=240,
        max_chars=1800,
    )
    evidence_excerpt = counter.truncate_text(
        sanitize_memory_context_text(f"tool={tool_name} status={status} summary={bounded_summary}"),
        max_tokens=80,
        max_chars=600,
    )
    observation_id = stable_hcms_id("obs_v2", namespace, result_id, "tool_result", size=16)
    evidence = EvidenceSpan(
        evidence_id=stable_hcms_id("ev_v2", observation_id, result_id, size=16),
        observation_id=observation_id,
        source_uri=f"runtime://tool-result/{result_id}",
        source_label="tool_result",
        excerpt=evidence_excerpt,
        quoted_text_hash=stable_context_id("tool-result", evidence_excerpt),
        trust_score=_tool_result_trust_score(status),
        collector="tool_result_store",
    )
    return ConsolidatedMemory(
        memory_id=stable_hcms_id("mem_v2", namespace, "episodic", result_id, canonical_content, size=16),
        namespace=namespace,
        layer="episodic",
        category="tool_result",
        title=f"Tool result {tool_name} {status}",
        summary=counter.truncate_text(
            sanitize_memory_context_text(f"{tool_name} {status}: {bounded_summary}"),
            max_tokens=100,
            max_chars=700,
        ),
        canonical_content=canonical_content,
        evidence=[evidence],
        confidence=_tool_result_confidence(status),
        salience=0.68 if raw_ref or compacted else 0.56,
        stability=0.3,
        metadata={
            "source": "tool_result_record",
            "tool_result_id": result_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "capability_id": capability_id,
            "run_id": run_id,
            "turn_id": turn_id,
            "status": status,
            "raw_ref": raw_ref,
            "workspace_ref": workspace_ref,
            "raw_externalized": bool(raw_ref),
            "compacted": compacted,
            "raw_size_chars": raw_size_chars,
            "summary_size_chars": summary_size_chars,
            "original_chars": _optional_int(record_metadata.get("original_chars")),
            "original_tokens_approx": _optional_int(record_metadata.get("original_tokens_approx")),
            "output_compaction_profile": _optional_str(record_metadata.get("output_compaction_profile")),
        },
    )


def capability_usage_event_from_runtime_event(event: Any) -> CapabilityUsageEvent | None:
    """Normalize a runtime/tool/capability event into the HCMS V2 mining contract."""

    metadata_value = _get(event, "metadata")
    metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
    event_type = str(_get(event, "event_type") or _get(event, "type") or metadata.get("event_type") or "").strip().lower()
    source_kind = str(_get(event, "source_kind") or metadata.get("source_kind") or "").strip().lower()

    tool_name = _optional_str(metadata.get("tool_name") or _get(event, "tool_name"))
    mcp_server_id = _optional_str(metadata.get("mcp_server_id") or metadata.get("server_id") or _get(event, "mcp_server_id"))
    skill_ids = _unique_strings(
        [
            *_string_list(metadata.get("skill_ids") or metadata.get("skills") or _get(event, "skill_ids")),
            _optional_str(metadata.get("skill_id") or _get(event, "skill_id")),
        ]
    )
    explicit_capability_id = _optional_str(
        metadata.get("capability_id")
        or metadata.get("resource_id")
        or _get(event, "capability_id")
    )
    capability_event_types = {
        "capability_usage",
        "capability_used",
        "capability_result",
        "tool_result",
        "tool_usage",
        "tool_call",
        "skill_usage",
        "skill_result",
        "mcp_usage",
        "mcp_result",
    }
    capability_source_kinds = {"capability", "tool", "skill", "mcp"}
    has_capability_signal = bool(explicit_capability_id or tool_name or mcp_server_id or skill_ids)
    looks_like_capability = (
        event_type in capability_event_types
        or source_kind in capability_source_kinds
        or has_capability_signal
    )
    if not looks_like_capability:
        return None

    capability_id = explicit_capability_id
    if capability_id is None and tool_name:
        capability_id = f"tool:{tool_name}"
    if capability_id is None and mcp_server_id:
        capability_id = f"mcp:{mcp_server_id}"
    if capability_id is None and skill_ids:
        capability_id = f"skill:{skill_ids[0]}"
    if capability_id is None and (event_type in capability_event_types or source_kind in capability_source_kinds):
        capability_id = _optional_str(_get(event, "source_ref"))
    if capability_id is None:
        return None

    capability_kind = _optional_str(metadata.get("capability_kind") or _get(event, "capability_kind"))
    if capability_kind is None:
        if mcp_server_id or source_kind == "mcp":
            capability_kind = "mcp"
        elif skill_ids or source_kind == "skill":
            capability_kind = "skill"
        elif tool_name or source_kind == "tool" or event_type in {"tool_result", "tool_usage", "tool_call"}:
            capability_kind = "tool"
        else:
            capability_kind = "capability"

    turn_id = _optional_str(metadata.get("turn_id") or _get(event, "turn_id")) or stable_hcms_id(
        "turn",
        _get(event, "thread_id"),
        _get(event, "run_id"),
        _get(event, "event_id"),
        capability_id,
        size=12,
    )
    usage_id = _optional_str(
        metadata.get("usage_id")
        or metadata.get("capability_usage_id")
        or _get(event, "usage_id")
    ) or stable_hcms_id(
        "usage_v2",
        _get(event, "thread_id"),
        _get(event, "run_id"),
        turn_id,
        _get(event, "event_id"),
        capability_id,
        size=16,
    )
    status = _optional_str(metadata.get("status") or _get(event, "status")) or _status_from_event_type(event_type)
    input_summary = _optional_str(
        metadata.get("input_summary")
        or metadata.get("request_summary")
        or metadata.get("arguments_summary")
        or _get(event, "input_summary")
    ) or ""
    output_summary = _optional_str(
        metadata.get("output_summary")
        or metadata.get("result_summary")
        or metadata.get("summary")
        or _get(event, "output_summary")
        or _event_payload_text(event)
    ) or ""
    context_block_refs = _unique_strings(
        [
            *_string_list(metadata.get("context_block_refs") or _get(event, "context_block_refs")),
            *_string_list(metadata.get("selected_block_ids") or _get(event, "selected_block_ids")),
        ]
    )
    payload: dict[str, Any] = {
        "usage_id": usage_id,
        "capability_id": capability_id,
        "capability_kind": capability_kind,
        "tool_name": tool_name,
        "skill_ids": skill_ids,
        "mcp_server_id": mcp_server_id,
        "turn_id": turn_id,
        "goal_id": _optional_str(metadata.get("goal_id") or _get(event, "goal_id") or _get(event, "goal_stack_ref")),
        "input_summary": input_summary,
        "output_summary": output_summary,
        "status": status,
        "latency_ms": _optional_int(metadata.get("latency_ms") or _get(event, "latency_ms")),
        "error_type": _optional_str(metadata.get("error_type") or _get(event, "error_type")),
        "verification_signal": _optional_str(metadata.get("verification_signal") or _get(event, "verification_signal")),
        "context_block_refs": context_block_refs,
    }
    created_at = _get(event, "timestamp") or metadata.get("created_at")
    if created_at is not None:
        payload["created_at"] = created_at
    return CapabilityUsageEvent.model_validate(payload)


def capability_usage_event_to_procedure_and_wisdom(
    event: CapabilityUsageEvent,
    *,
    namespace: str = "global/default",
    token_budget: TokenBudgetService | None = None,
) -> ProcedureWisdomMiningResult:
    counter = token_budget or TokenBudgetService()
    label = event.tool_name or event.mcp_server_id or event.capability_id
    success = _capability_usage_success(event.status, event.verification_signal)
    output_summary, output_truncated = _safe_capability_output_summary(event, counter)
    input_summary = counter.truncate_text(
        sanitize_memory_context_text(event.input_summary),
        max_tokens=48,
        max_chars=320,
    )
    verification = _optional_str(event.verification_signal) or "unverified"
    status_line = f"status={event.status} verification={verification}"
    evidence_excerpt = counter.truncate_text(
        sanitize_memory_context_text(
            f"{status_line} capability={event.capability_id} input={input_summary}"
        ),
        max_tokens=80,
        max_chars=600,
    )
    capability_refs = _unique_strings(
        [
            event.capability_id,
            f"tool:{event.tool_name}" if event.tool_name else None,
            f"mcp:{event.mcp_server_id}" if event.mcp_server_id else None,
            *(f"skill:{skill_id}" for skill_id in event.skill_ids),
        ]
    )
    evidence = EvidenceSpan(
        evidence_id=stable_hcms_id("ev_v2", event.usage_id, event.capability_id, event.status, size=16),
        observation_id=event.usage_id,
        source_uri="runtime://capability-usage/" + event.usage_id,
        source_label="capability_usage",
        excerpt=evidence_excerpt,
        quoted_text_hash=stable_context_id("capability-usage", evidence_excerpt),
        trust_score=0.8 if success else 0.45,
        timestamp=event.created_at,
        collector="procedure_wisdom_miner",
    )
    procedure = ProcedurePattern(
        procedure_id=stable_hcms_id("proc_v2", namespace, event.capability_id, event.capability_kind, label, size=16),
        namespace=namespace,
        title=f"Use {label} for {event.capability_kind}",
        trigger_conditions=[item for item in [input_summary, event.goal_id] if item],
        task_types=[event.capability_kind],
        ordered_steps=[
            ProcedureStep(
                step_id=stable_hcms_id("step_v2", event.usage_id, event.capability_id, size=16),
                description=counter.truncate_text(
                    sanitize_memory_context_text(f"Use {label} for {event.capability_kind}: {input_summary}"),
                    max_tokens=64,
                    max_chars=420,
                ),
                capability_refs=capability_refs,
                expected_observation=status_line if success else None,
                fallback=f"Review {event.error_type or event.status} before reusing this pattern" if not success else None,
            )
        ],
        allowed_tools=[event.tool_name] if event.tool_name else [],
        related_skills=list(event.skill_ids),
        success_evidence=[evidence] if success else [],
        failure_recovery_notes=[] if success else [status_line],
        confidence=0.72 if success else 0.42,
        usage_count=1,
        success_rate=1.0 if success else 0.0,
        last_used_at=event.created_at,
    )
    wisdom_statement = counter.truncate_text(
        sanitize_memory_context_text(
            f"{label} returned {event.status} for {event.capability_kind}; "
            f"verification={verification}; output={output_summary}"
        ),
        max_tokens=64,
        max_chars=500,
    )
    wisdom = WisdomInsight(
        insight_id=stable_hcms_id("wis_v2", namespace, event.usage_id, event.capability_id, size=16),
        namespace=namespace,
        insight_type="capability_usage_success" if success else "capability_usage_failure",
        statement=wisdom_statement,
        applicability=_unique_strings([event.capability_kind, event.tool_name, event.mcp_server_id, *event.skill_ids]),
        supporting_traces=[event.usage_id],
        counterexamples=[] if success else [event.usage_id],
        confidence=0.68 if success else 0.42,
        review_state="candidate",
        injection_policy="planning_only",
    )
    return ProcedureWisdomMiningResult(
        usage_id=event.usage_id,
        procedure=procedure,
        wisdom=wisdom,
        diagnostics={
            "source": "capability_usage_event",
            "status": event.status,
            "verification_signal": event.verification_signal,
            "output_truncated": output_truncated,
            "output_summary_hash": stable_context_id("capability-output", event.output_summary),
            "context_block_ref_count": len(event.context_block_refs),
            "latency_ms": event.latency_ms,
        },
    )


def capability_usage_events_to_procedure_wisdom_batch(
    events: list[CapabilityUsageEvent] | tuple[CapabilityUsageEvent, ...],
    *,
    namespace: str = "global/default",
    token_budget: TokenBudgetService | None = None,
) -> ProcedureWisdomMiningBatch:
    counter = token_budget or TokenBudgetService()
    normalized = [
        event if isinstance(event, CapabilityUsageEvent) else CapabilityUsageEvent.model_validate(event)
        for event in events
    ]
    results: list[ProcedureWisdomMiningResult] = []
    procedural_memories: list[ConsolidatedMemory] = []
    wisdom_memories: list[ConsolidatedMemory] = []
    for event in normalized:
        mined = capability_usage_event_to_procedure_and_wisdom(
            event,
            namespace=namespace,
            token_budget=counter,
        )
        results.append(mined)
        if mined.procedure is not None:
            procedural_memories.append(
                procedure_pattern_to_consolidated_memory(
                    mined.procedure,
                    source_event=event,
                    diagnostics=mined.diagnostics,
                    token_budget=counter,
                )
            )
        if mined.wisdom is not None:
            wisdom_memories.append(
                wisdom_insight_to_consolidated_memory(
                    mined.wisdom,
                    source_event=event,
                    diagnostics=mined.diagnostics,
                    token_budget=counter,
                )
            )
    return ProcedureWisdomMiningBatch(
        namespace=namespace,
        event_count=len(normalized),
        results=results,
        procedural_memories=procedural_memories,
        wisdom_memories=wisdom_memories,
        diagnostics={
            "source": "capability_usage_events",
            "result_count": len(results),
            "procedure_count": len(procedural_memories),
            "wisdom_count": len(wisdom_memories),
            "usage_ids": [event.usage_id for event in normalized],
        },
    )


def procedure_pattern_to_consolidated_memory(
    procedure: ProcedurePattern,
    *,
    source_event: CapabilityUsageEvent | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    token_budget: TokenBudgetService | None = None,
) -> ConsolidatedMemory:
    counter = token_budget or TokenBudgetService()
    step_lines = [
        f"{index + 1}. {step.description}"
        for index, step in enumerate(procedure.ordered_steps[:8])
        if step.description
    ]
    metadata = _capability_usage_memory_metadata(
        source_event,
        diagnostics=diagnostics,
        extra={
            "hcms_v2_procedure_id": procedure.procedure_id,
            "procedure_promotion_state": procedure.promotion_state,
            "procedure_success_rate": procedure.success_rate,
            "procedure_usage_count": procedure.usage_count,
        },
    )
    canonical = counter.truncate_text(
        sanitize_memory_context_text(
            "\n".join(
                item
                for item in [
                    f"Procedure: {procedure.title}",
                    f"Trigger: {'; '.join(procedure.trigger_conditions[:4])}",
                    f"Task types: {', '.join(procedure.task_types[:6])}",
                    f"Allowed tools: {', '.join(procedure.allowed_tools[:8])}",
                    f"Related skills: {', '.join(procedure.related_skills[:8])}",
                    "Steps:",
                    *step_lines,
                ]
                if item
            )
        ),
        max_tokens=260,
        max_chars=1800,
    )
    summary = counter.truncate_text(
        sanitize_memory_context_text(
            f"{procedure.title}; tools={', '.join(procedure.allowed_tools[:4]) or 'none'}; "
            f"skills={', '.join(procedure.related_skills[:4]) or 'none'}; "
            f"success_rate={procedure.success_rate:.2f}"
        ),
        max_tokens=80,
        max_chars=500,
    )
    evidence = list(procedure.success_evidence[:4])
    if not evidence and source_event is not None:
        evidence = [_capability_usage_evidence_span(source_event, collector="procedure_wisdom_miner")]
    return ConsolidatedMemory(
        memory_id=stable_hcms_id("mem_v2", procedure.namespace, "procedural", procedure.procedure_id, size=16),
        namespace=procedure.namespace,
        layer="procedural",
        category="procedure",
        title=procedure.title,
        summary=summary,
        canonical_content=canonical,
        evidence=evidence,
        confidence=procedure.confidence,
        salience=0.78 if procedure.success_rate > 0 else 0.48,
        stability=0.42,
        metadata=metadata,
    )


def wisdom_insight_to_consolidated_memory(
    wisdom: WisdomInsight,
    *,
    source_event: CapabilityUsageEvent | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    token_budget: TokenBudgetService | None = None,
) -> ConsolidatedMemory:
    counter = token_budget or TokenBudgetService()
    metadata = _capability_usage_memory_metadata(
        source_event,
        diagnostics=diagnostics,
        extra={
            "hcms_v2_wisdom_id": wisdom.insight_id,
            "insight_type": wisdom.insight_type,
            "review_state": wisdom.review_state,
            "wisdom_injection_policy": wisdom.injection_policy,
        },
    )
    canonical = counter.truncate_text(
        sanitize_memory_context_text(
            "\n".join(
                item
                for item in [
                    f"Wisdom: {wisdom.statement}",
                    f"Applicability: {', '.join(wisdom.applicability[:8])}",
                    f"Supporting traces: {', '.join(wisdom.supporting_traces[:8])}",
                    f"Counterexamples: {', '.join(wisdom.counterexamples[:8])}",
                    f"Injection policy: {wisdom.injection_policy}",
                ]
                if item
            )
        ),
        max_tokens=220,
        max_chars=1600,
    )
    summary = counter.truncate_text(
        sanitize_memory_context_text(wisdom.statement),
        max_tokens=80,
        max_chars=500,
    )
    evidence = [_capability_usage_evidence_span(source_event, collector="procedure_wisdom_miner")] if source_event else []
    return ConsolidatedMemory(
        memory_id=stable_hcms_id("mem_v2", wisdom.namespace, "wisdom", wisdom.insight_id, size=16),
        namespace=wisdom.namespace,
        layer="wisdom",
        category="error_pattern" if "failure" in wisdom.insight_type else "wisdom",
        title=f"Wisdom insight {wisdom.insight_id}",
        summary=summary,
        canonical_content=canonical,
        evidence=evidence,
        confidence=wisdom.confidence,
        salience=0.82 if "failure" in wisdom.insight_type else 0.62,
        stability=0.36,
        metadata=metadata,
    )


def memory_search_result_from_retrieval_result(
    result: RetrievalResult,
    *,
    namespace: str = "global/default",
    token_budget: TokenBudgetService | None = None,
) -> MemorySearchResult:
    counter = token_budget or TokenBudgetService()
    memory = result.memory
    memory_metadata = dict(getattr(memory, "metadata", {}) if memory is not None else {})
    content = sanitize_memory_context_text(
        result.highlight
        or getattr(memory, "summary", None)
        or getattr(memory, "content", None)
        or result.memory_id
    )
    layer = _memory_layer(memory)
    category = _memory_category(memory)
    evidence = [_evidence_span_from_legacy(item, memory_id=result.memory_id) for item in (memory.evidence if memory else [])]
    privacy_level = str(memory_metadata.get("privacy_level") or "project")
    trust_level = str(memory_metadata.get("trust_level") or "trusted")
    conflict_severity = _optional_str(memory_metadata.get("conflict_severity") or memory_metadata.get("severity"))
    guard_action = _optional_str(memory_metadata.get("guard_action"))
    conflict_state = "unresolved" if getattr(memory, "conflicts_with", ()) else "none"
    source_refs = [result.memory_id]
    if memory is not None:
        source_refs.extend(item.evidence_id for item in memory.evidence[:4])
    return MemorySearchResult(
        result_id=stable_hcms_id("mem_result_v2", namespace, result.memory_id, content, size=16),
        memory_id=result.memory_id,
        claim_id=_optional_str((memory.metadata if memory else {}).get("claim_id")),
        layer=layer,
        category=category,
        content=content,
        score=result.score,
        raw_scores={key: bounded_score(value) for key, value in result.raw_scores.items()},
        salience_score=bounded_score(getattr(memory, "salience", None), default=result.score),
        evidence=evidence,
        confidence=bounded_score(getattr(memory, "confidence", None), default=result.score),
        conflict_state=conflict_state,
        privacy_level=privacy_level,
        freshness=bounded_score((memory.metadata if memory else {}).get("freshness"), default=1.0),
        token_cost=counter.count_text(content),
        explanation=result.explanation or "",
        source_refs=list(dict.fromkeys(source_refs)),
        metadata={
            "namespace": namespace,
            "highlight": result.highlight,
            "ranks": dict(result.ranks),
            "memory_state": getattr(getattr(memory, "state", None), "value", None),
            "trust_level": trust_level,
            "guard_action": guard_action,
            "severity": conflict_severity,
            "conflicts_with": list(getattr(memory, "conflicts_with", ()) or ()),
        },
    )


def memory_search_result_to_context_block(
    result: MemorySearchResult,
    *,
    token_budget: TokenBudgetService | None = None,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    token_cost = result.token_cost or counter.count_text(result.content)
    trust_level = str(result.metadata.get("trust_level") or "trusted").lower()
    guard_action = str(result.metadata.get("guard_action") or "").lower()
    severe_conflict = result.conflict_state in {"unresolved", "disputed"} and result.metadata.get("severity") in {
        "high",
        "critical",
    }
    unsafe_trust = trust_level in {"external", "untrusted", "quarantined"}
    suppressed = (
        result.privacy_level in {"secret", "quarantine"}
        or guard_action in {"quarantine", "allow_no_inject"}
        or unsafe_trust
        or severe_conflict
    )
    suppression_reason = "conflict_ledger_suppressed" if severe_conflict else "memory_guard_suppressed"
    evidence_refs = tuple(
        EvidenceRef(
            ref_id=item.evidence_id,
            source_kind="memory_evidence",
            source_id=item.observation_id,
            span=item.excerpt,
            confidence=item.trust_score,
            metadata={"source_label": item.source_label, "source_uri": item.source_uri},
        )
        for item in result.evidence
    )
    return ContextBlock(
        block_id=result.result_id,
        block_type=_context_block_type(result),
        source=ContextSource(
            kind=ContextSourceKind.MEMORY,
            name=result.layer,
            ref=result.memory_id or result.claim_id or result.result_id,
            trust_level=_context_trust_level(result),
            metadata={
                "memory_id": result.memory_id,
                "claim_id": result.claim_id,
                "category": result.category,
                "source_refs": result.source_refs,
            },
        ),
        title=_context_title(result),
        content=result.content,
        token_cost=token_cost,
        priority=result.score,
        salience=result.salience_score,
        confidence=result.confidence,
        position_hint=f"memory:{result.layer}",
        evidence_refs=evidence_refs,
        conflict_state=result.conflict_state,
        privacy_level=result.privacy_level,
        injection_policy=InjectionPolicy(
            allow=not suppressed,
            reason=suppression_reason if suppressed else None,
            requires_warning=result.conflict_state in {"suspected", "unresolved", "disputed"},
        ),
        compression_policy=CompressionPolicy(
            allow_compression=True,
            allow_reference=True,
            min_tokens=24,
            summary=result.explanation or result.content[:240],
            ref=result.memory_id or result.claim_id,
        ),
        tags=tuple(filter(None, ("memory", result.layer, result.category))),
        metadata={
            **result.metadata,
            "result_id": result.result_id,
            "memory_id": result.memory_id,
            "claim_id": result.claim_id,
            "raw_scores": result.raw_scores,
            "source_refs": result.source_refs,
            "freshness": result.freshness,
        },
    )


def memory_injection_view_v2_to_blocks(
    view: MemoryInjectionViewV2,
    *,
    token_budget: TokenBudgetService | None = None,
) -> list[ContextBlock]:
    blocks: list[ContextBlock] = []
    for result in (
        *view.sensory_results,
        *view.working_results,
        *view.semantic_results,
        *view.episodic_results,
        *view.procedural_results,
        *view.wisdom_results,
    ):
        blocks.append(memory_search_result_to_context_block(result, token_budget=token_budget))
    blocks.extend(conflict_record_to_warning_block(conflict, token_budget=token_budget) for conflict in view.conflict_warnings)
    return blocks


def memory_injection_view_v2_from_legacy(
    view: MemoryInjectionView,
    *,
    query: str = "",
    token_budget: TokenBudgetService | None = None,
) -> MemoryInjectionViewV2:
    counter = token_budget or TokenBudgetService()
    results: list[MemorySearchResult] = []
    for index, fact in enumerate(view.facts):
        content = sanitize_memory_context_text(fact)
        result_id = stable_hcms_id("mem_result_v2", view.namespace, index, content, size=16)
        evidence = []
        if index < len(view.evidence):
            evidence.append(
                EvidenceSpan(
                    evidence_id=stable_hcms_id("ev_v2", view.namespace, index, view.evidence[index], size=12),
                    observation_id=stable_hcms_id("obs_v2", view.namespace, index, view.evidence[index], size=12),
                    source_label="legacy_memory_injection",
                    excerpt=sanitize_memory_context_text(view.evidence[index])[:240],
                    trust_score=view.confidence,
                )
            )
        results.append(
            MemorySearchResult(
                result_id=result_id,
                layer="semantic",
                category="legacy_fact",
                content=content,
                score=0.65,
                salience_score=0.65,
                evidence=evidence,
                confidence=view.confidence,
                token_cost=counter.count_text(content),
                explanation="legacy MemoryInjectionView fact",
                source_refs=[view.namespace],
                metadata={"legacy_index": index},
            )
        )
    return MemoryInjectionViewV2(
        namespace=view.namespace,
        query=query,
        semantic_results=results,
        diagnostics={
            "source": "legacy_memory_injection_view",
            "fact_count": len(view.facts),
            "evidence_count": len(view.evidence),
            "summary_tokens": counter.count_text(view.summary),
        },
    )


def conflict_record_to_warning_block(
    conflict: ConflictRecord,
    *,
    token_budget: TokenBudgetService | None = None,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    content = "\n".join(
        [
            f"conflict_id={conflict.conflict_id}",
            f"severity={conflict.severity}",
            f"status={conflict.status}",
            f"policy={conflict.injection_policy}",
            f"claims={', '.join(conflict.claim_ids)}",
            f"memories={', '.join(conflict.memory_ids)}",
            f"explanation={conflict.explanation}",
        ]
    )
    return ContextBlock(
        block_id=str(conflict.conflict_id),
        block_type="conflict_warning",
        source=ContextSource(
            kind=ContextSourceKind.MEMORY,
            name="conflict_ledger",
            ref=str(conflict.conflict_id),
            trust_level="runtime",
            metadata={"review_inbox_id": conflict.review_inbox_id},
        ),
        title="Memory Conflict Warning",
        content=content,
        token_cost=counter.count_text(content),
        priority=0.95 if conflict.severity in {"high", "critical"} else 0.7,
        salience=0.9,
        confidence=0.9,
        position_hint="memory:warning",
        conflict_state="unresolved" if conflict.status in {"open", "needs_review"} else conflict.status,
        privacy_level="project",
        injection_policy=InjectionPolicy(allow=True, protected=True, requires_warning=True, reason=conflict.injection_policy),
        compression_policy=CompressionPolicy(allow_compression=False, allow_reference=True, ref=conflict.review_inbox_id),
        tags=("memory", "conflict", conflict.severity),
        metadata={
            "conflict_id": conflict.conflict_id,
            "claim_ids": conflict.claim_ids,
            "memory_ids": conflict.memory_ids,
            "review_inbox_id": conflict.review_inbox_id,
            "injection_policy": conflict.injection_policy,
        },
    )


def conflict_record_to_alert(conflict: ConflictRecord) -> ConflictAlert:
    return ConflictAlert(
        alert_id=stable_context_id("conflict-alert", conflict.conflict_id, conflict.status, conflict.injection_policy),
        conflict_id=str(conflict.conflict_id),
        severity=conflict.severity,
        affected_claims=list(conflict.claim_ids),
        affected_memories=list(conflict.memory_ids),
        preferred_claim_id=conflict.preferred_claim_id,
        unresolved_reason=conflict.explanation,
        injection_policy=conflict.injection_policy,
        review_inbox_id=conflict.review_inbox_id,
        status=conflict.status,
        conflict_type=conflict.conflict_type,
        created_at=conflict.detected_at,
        metadata={
            "namespace": conflict.namespace,
            "detection_method": conflict.detection_method,
            "resolution_policy": conflict.resolution_policy,
            **dict(conflict.metadata),
        },
    )


def _evidence_span_from_legacy(evidence: Evidence, *, memory_id: str) -> EvidenceSpan:
    excerpt = sanitize_memory_context_text(evidence.content)[:240]
    return EvidenceSpan(
        evidence_id=evidence.evidence_id,
        observation_id=str(evidence.source_id or memory_id),
        source_label=evidence.type.value,
        excerpt=excerpt,
        quoted_text_hash=stable_context_id("evidence-text", excerpt),
        trust_score=evidence.weight,
        timestamp=evidence.timestamp,
        collector="legacy_hcms",
    )


def _runtime_event_ref_from_payload(payload: Mapping[str, Any]) -> RuntimeEventRef:
    return RuntimeEventRef(
        event_id=str(payload.get("event_id") or stable_hcms_id("event", payload, size=12)),
        event_type=str(payload.get("event_type") or "runtime_event"),
        source_ref=_optional_str(payload.get("source_ref")),
        payload_summary=str(payload.get("payload_summary") or "")[:240],
        payload_ref=_optional_str(payload.get("payload_ref")),
        actor=str(payload.get("actor") or "runtime"),
        privacy_level=str(payload.get("privacy_level") or "project"),
        trust_level=str(payload.get("trust_level") or "local_runtime"),
        metadata=dict(payload.get("metadata") or {}),
    )


def _context_block_type(result: MemorySearchResult) -> str:
    layer = result.layer.lower()
    category = result.category.lower()
    if layer == "sensory":
        return "sensory_observation"
    if layer == "working":
        return "working_memory"
    if layer == "episodic":
        return "episodic_summary"
    if layer in {"procedural", "procedure"} or category in {"procedure", "pattern"}:
        return "procedural_hint"
    if layer == "wisdom" or category in {"error_pattern", "wisdom"}:
        return "wisdom_warning"
    if layer in {"semantic", "knowledge"} or category in {"knowledge", "project_convention"}:
        return "semantic_fact"
    return "retrieved_memory"


def _context_title(result: MemorySearchResult) -> str:
    label = result.category.replace("_", " ").title()
    if result.memory_id:
        return f"{label} {result.memory_id}"
    if result.claim_id:
        return f"{label} {result.claim_id}"
    return label


def _context_trust_level(result: MemorySearchResult) -> str:
    if result.privacy_level == "quarantine" or str(result.metadata.get("guard_action") or "").lower() == "quarantine":
        return "quarantined"
    trust_level = str(result.metadata.get("trust_level") or "trusted").lower()
    if trust_level in {"external", "untrusted", "quarantined"}:
        return trust_level
    return "trusted"


def _memory_layer(memory: Any) -> str:
    if memory is None:
        return "semantic"
    raw = str(memory.metadata.get("layer") or memory.metadata.get("hcms_layer") or "").strip().lower()
    if raw:
        return raw
    category = memory.category
    if category in {MemoryCategory.PROCEDURE, MemoryCategory.PATTERN}:
        return "procedural"
    if category in {MemoryCategory.ERROR_PATTERN, MemoryCategory.DECISION}:
        return "wisdom"
    if category in {MemoryCategory.CONTEXT, MemoryCategory.GOAL}:
        return "episodic"
    return "semantic"


def _memory_category(memory: Any) -> str:
    if memory is None:
        return "unknown"
    value = getattr(memory, "category", "note")
    return str(getattr(value, "value", value) or "note")


def _get(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _event_payload_text(event: Any) -> str:
    payload = (
        _get(event, "payload_summary")
        or _get(event, "summary")
        or _get(event, "message")
        or _get(event, "content")
        or _get(event, "output")
        or _get(event, "payload")
        or ""
    )
    return str(payload or "")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off", ""}:
            return False
    return bool(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _workspace_variables(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, Mapping):
        return []
    pairs: list[tuple[str, str]] = []
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        text = str(item or "").strip()
        if text:
            pairs.append((str(key), sanitize_memory_context_text(text)[:240]))
    return pairs


def _workspace_intermediate_results(value: Any) -> list[dict[str, str | None]]:
    if not isinstance(value, list | tuple):
        return []
    counter = TokenBudgetService()
    results: list[dict[str, str | None]] = []
    for item in value:
        summary = sanitize_memory_context_text(str(_get(item, "summary") or ""))
        results.append(
            {
                "tool_result_id": _optional_str(_get(item, "tool_result_id")) or _optional_str(_get(item, "result_ref")) or "unknown",
                "result_ref": _optional_str(_get(item, "result_ref")),
                "tool_name": _optional_str(_get(item, "tool_name")) or "tool",
                "status": _optional_str(_get(item, "status")) or "unknown",
                "raw_ref": _optional_str(_get(item, "raw_ref")),
                "summary": counter.truncate_text(summary, max_tokens=80, max_chars=500),
            }
        )
    return results


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _status_from_event_type(event_type: str) -> str:
    text = str(event_type or "").strip().lower()
    if any(marker in text for marker in ("success", "succeeded", "passed", "result")):
        return "success"
    if any(marker in text for marker in ("fail", "error", "exception")):
        return "failure"
    return "unknown"


def _tool_result_confidence(status: str) -> float:
    status_value = str(status or "").strip().lower()
    if status_value in {"success", "succeeded", "passed", "completed", "ok"}:
        return 0.76
    if status_value in {"failure", "failed", "error", "exception", "timeout", "cancelled"}:
        return 0.62
    return 0.55


def _tool_result_trust_score(status: str) -> float:
    status_value = str(status or "").strip().lower()
    if status_value in {"success", "succeeded", "passed", "completed", "ok"}:
        return 0.78
    if status_value in {"failure", "failed", "error", "exception", "timeout", "cancelled"}:
        return 0.6
    return 0.52


def _capability_usage_success(status: str, verification_signal: str | None) -> bool:
    status_value = str(status or "").strip().lower()
    verification = str(verification_signal or "").strip().lower()
    return status_value in {"success", "succeeded", "passed"} or verification in {
        "success",
        "passed",
        "tests_passed",
        "verified",
    }


def _safe_capability_output_summary(
    event: CapabilityUsageEvent,
    counter: TokenBudgetService,
) -> tuple[str, bool]:
    output = sanitize_memory_context_text(event.output_summary)
    if not output:
        return "no output summary", False
    if len(output) > 240 or counter.count_text(output) > 40:
        return f"externalized summary ref={stable_context_id('capability-output', output)}", True
    return counter.truncate_text(output, max_tokens=40, max_chars=240), False


def _capability_usage_memory_metadata(
    event: CapabilityUsageEvent | None,
    *,
    diagnostics: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "procedure_wisdom_miner",
        "source_kind": "capability_usage",
    }
    if event is not None:
        metadata.update(
            {
                "hcms_v2_capability_usage_id": event.usage_id,
                "capability_id": event.capability_id,
                "capability_kind": event.capability_kind,
                "tool_name": event.tool_name,
                "mcp_server_id": event.mcp_server_id,
                "skill_ids": list(event.skill_ids),
                "context_block_refs": list(event.context_block_refs),
                "turn_id": event.turn_id,
                "goal_id": event.goal_id,
                "status": event.status,
                "verification_signal": event.verification_signal,
                "error_type": event.error_type,
                "latency_ms": event.latency_ms,
                "created_at": event.created_at.isoformat(),
            }
        )
    if diagnostics:
        metadata["diagnostics"] = {
            key: value
            for key, value in diagnostics.items()
            if key
            in {
                "source",
                "status",
                "verification_signal",
                "output_truncated",
                "output_summary_hash",
                "context_block_ref_count",
                "latency_ms",
            }
        }
    if extra:
        metadata.update(dict(extra))
    return metadata


def _capability_usage_evidence_span(
    event: CapabilityUsageEvent,
    *,
    collector: str,
) -> EvidenceSpan:
    success = _capability_usage_success(event.status, event.verification_signal)
    counter = TokenBudgetService()
    evidence_excerpt = counter.truncate_text(
        sanitize_memory_context_text(
            f"status={event.status} verification={event.verification_signal or 'unverified'} "
            f"capability={event.capability_id} input={event.input_summary}"
        ),
        max_tokens=80,
        max_chars=600,
    )
    return EvidenceSpan(
        evidence_id=stable_hcms_id("ev_v2", event.usage_id, event.capability_id, event.status, size=16),
        observation_id=event.usage_id,
        source_uri="runtime://capability-usage/" + event.usage_id,
        source_label="capability_usage",
        excerpt=evidence_excerpt,
        quoted_text_hash=stable_context_id("capability-usage", evidence_excerpt),
        trust_score=0.8 if success else 0.45,
        timestamp=event.created_at,
        collector=collector,
    )


def _unique_strings(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


__all__ = [
    "capability_usage_events_to_procedure_wisdom_batch",
    "capability_usage_event_from_runtime_event",
    "capability_usage_event_to_procedure_and_wisdom",
    "capture_envelope_v2_from_legacy",
    "conflict_record_to_alert",
    "conflict_record_to_warning_block",
    "memory_injection_view_v2_from_legacy",
    "memory_injection_view_v2_to_blocks",
    "memory_search_result_from_retrieval_result",
    "memory_search_result_to_context_block",
    "observation_record_from_runtime_event",
    "procedure_pattern_to_consolidated_memory",
    "runtime_event_to_capture_envelope",
    "tool_result_record_to_episodic_memory",
    "wisdom_insight_to_consolidated_memory",
    "workspace_state_to_working_memory",
]
