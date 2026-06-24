from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ContextAssemblyTrace


REPLAY_REQUIRED_PHASES = (
    "action_dispatch",
    "observation_handling",
    "state_update",
    "maintenance_scheduling",
)


class ContextAssemblyEvaluationRecord(BaseModel):
    """Evaluation-safe summary of a Runtime Context V2 assembly trace."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    trace_id: str | None = None
    prompt_hash: str | None = None
    actual_prompt_mode: str | None = None
    actual_system_prompt_hash: str | None = None
    diagnostic_only: bool = False
    fallback_used: bool = False
    candidate_block_count: int = 0
    selected_block_count: int = 0
    dropped_block_count: int = 0
    compressed_block_count: int = 0
    deferred_block_count: int = 0
    total_tokens: int = 0
    max_context_tokens: int | None = None
    reserved_response_tokens: int | None = None
    hard_context_tokens: int | None = None
    layer_token_usage: dict[str, int] = Field(default_factory=dict)
    source_kind_counts: dict[str, int] = Field(default_factory=dict)
    block_type_counts: dict[str, int] = Field(default_factory=dict)
    drop_reason_counts: dict[str, int] = Field(default_factory=dict)
    selected_tools: list[str] = Field(default_factory=list)
    selected_mcp_tools: list[str] = Field(default_factory=list)
    selected_skills: list[str] = Field(default_factory=list)
    selected_memory: list[str] = Field(default_factory=list)
    selected_workspace: list[str] = Field(default_factory=list)
    selected_events: list[str] = Field(default_factory=list)
    selected_tool_results: list[str] = Field(default_factory=list)
    selected_tool_result_refs: list[str] = Field(default_factory=list)
    runtime_event_count: int = 0
    runtime_event_counts: dict[str, int] = Field(default_factory=dict)
    runtime_event_refs: list[str] = Field(default_factory=list)
    runtime_event_trace_ids: list[str] = Field(default_factory=list)
    runtime_tool_result_refs: list[str] = Field(default_factory=list)
    runtime_workspace_refs: list[str] = Field(default_factory=list)
    runtime_memory_refs: list[str] = Field(default_factory=list)
    replay_phase_coverage: dict[str, bool] = Field(default_factory=dict)
    replay_missing_phases: list[str] = Field(default_factory=list)
    trace_replay_ready: bool = False
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class EvaluationCase(BaseModel):
    """One replayed assembly trace in an evaluation run."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    trace_id: str | None = None
    prompt_hash: str | None = None
    record: ContextAssemblyEvaluationRecord
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class EvaluationRun(BaseModel):
    """Bounded, replay-safe aggregate over context assembly traces."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    suite_id: str
    case_count: int = 0
    cases: tuple[EvaluationCase, ...] = ()
    metrics: dict[str, int | float] = Field(default_factory=dict)
    layer_token_usage: dict[str, int] = Field(default_factory=dict)
    source_kind_counts: dict[str, int] = Field(default_factory=dict)
    block_type_counts: dict[str, int] = Field(default_factory=dict)
    drop_reason_counts: dict[str, int] = Field(default_factory=dict)
    selected_tools: list[str] = Field(default_factory=list)
    selected_mcp_tools: list[str] = Field(default_factory=list)
    selected_skills: list[str] = Field(default_factory=list)
    selected_memory: list[str] = Field(default_factory=list)
    selected_workspace: list[str] = Field(default_factory=list)
    selected_events: list[str] = Field(default_factory=list)
    selected_tool_results: list[str] = Field(default_factory=list)
    selected_tool_result_refs: list[str] = Field(default_factory=list)
    runtime_event_count: int = 0
    runtime_event_counts: dict[str, int] = Field(default_factory=dict)
    runtime_event_refs: list[str] = Field(default_factory=list)
    runtime_event_trace_ids: list[str] = Field(default_factory=list)
    runtime_tool_result_refs: list[str] = Field(default_factory=list)
    runtime_workspace_refs: list[str] = Field(default_factory=list)
    runtime_memory_refs: list[str] = Field(default_factory=list)
    replay_phase_coverage: dict[str, bool] = Field(default_factory=dict)
    replay_missing_phases: list[str] = Field(default_factory=list)
    trace_replay_ready: bool = False
    trace_replay_matrix: list[dict[str, Any]] = Field(default_factory=list)
    ablation_flags: dict[str, bool] = Field(default_factory=dict)
    ablation_variant_metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class ContextEvaluationSuite(BaseModel):
    """Minimal Runtime Context V2 trace replay and aggregation suite."""

    model_config = ConfigDict(extra="forbid")

    suite_id: str = "context-v2"

    def evaluate_traces(
        self,
        traces: list[ContextAssemblyTrace | Mapping[str, Any]]
        | tuple[ContextAssemblyTrace | Mapping[str, Any], ...],
        *,
        run_id: str | None = None,
        ablation_flags: Mapping[str, bool] | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> EvaluationRun:
        records = [context_assembly_trace_to_evaluation_record(trace) for trace in traces]
        return self.evaluate_records(
            records,
            run_id=run_id,
            ablation_flags=ablation_flags,
            diagnostics=diagnostics,
        )

    def evaluate_records(
        self,
        records: list[ContextAssemblyEvaluationRecord]
        | tuple[ContextAssemblyEvaluationRecord, ...],
        *,
        run_id: str | None = None,
        ablation_flags: Mapping[str, bool] | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> EvaluationRun:
        record_list = list(records)
        cases = tuple(
            EvaluationCase(
                case_id=f"{run_id or 'context_eval_run'}:{index + 1}",
                trace_id=record.trace_id,
                prompt_hash=record.prompt_hash,
                record=record,
                diagnostics=_case_diagnostics(record),
            )
            for index, record in enumerate(record_list)
        )
        metrics = _aggregate_metrics(record_list)
        runtime_event_counts = _merge_int_mappings(record.runtime_event_counts for record in record_list)
        replay_phase_coverage = _aggregate_replay_phase_coverage(record_list)
        replay_missing_phases = [phase for phase, covered in replay_phase_coverage.items() if not covered]
        trace_replay_matrix = _trace_replay_matrix(cases)
        ablation_flags_payload = {
            str(key): bool(value)
            for key, value in _mapping(ablation_flags).items()
        }
        diagnostics_payload = {
            str(key): value
            for key, value in _mapping(diagnostics).items()
        }
        return EvaluationRun(
            run_id=run_id or "context_eval_run",
            suite_id=self.suite_id,
            case_count=len(cases),
            cases=cases,
            metrics=metrics,
            layer_token_usage=_merge_int_mappings(record.layer_token_usage for record in record_list),
            source_kind_counts=_merge_int_mappings(record.source_kind_counts for record in record_list),
            block_type_counts=_merge_int_mappings(record.block_type_counts for record in record_list),
            drop_reason_counts=_merge_int_mappings(record.drop_reason_counts for record in record_list),
            selected_tools=_unique_strings(record.selected_tools for record in record_list),
            selected_mcp_tools=_unique_strings(record.selected_mcp_tools for record in record_list),
            selected_skills=_unique_strings(record.selected_skills for record in record_list),
            selected_memory=_unique_strings(record.selected_memory for record in record_list),
            selected_workspace=_unique_strings(record.selected_workspace for record in record_list),
            selected_events=_unique_strings(record.selected_events for record in record_list),
            selected_tool_results=_unique_strings(record.selected_tool_results for record in record_list),
            selected_tool_result_refs=_unique_strings(record.selected_tool_result_refs for record in record_list),
            runtime_event_count=sum(record.runtime_event_count for record in record_list),
            runtime_event_counts=runtime_event_counts,
            runtime_event_refs=_unique_strings(record.runtime_event_refs for record in record_list),
            runtime_event_trace_ids=_unique_strings(record.runtime_event_trace_ids for record in record_list),
            runtime_tool_result_refs=_unique_strings(record.runtime_tool_result_refs for record in record_list),
            runtime_workspace_refs=_unique_strings(record.runtime_workspace_refs for record in record_list),
            runtime_memory_refs=_unique_strings(record.runtime_memory_refs for record in record_list),
            replay_phase_coverage=replay_phase_coverage,
            replay_missing_phases=replay_missing_phases,
            trace_replay_ready=bool(record_list) and all(record.trace_replay_ready for record in record_list),
            trace_replay_matrix=trace_replay_matrix,
            ablation_flags=ablation_flags_payload,
            ablation_variant_metrics=_ablation_variant_metrics(
                record_list,
                metrics,
                ablation_flags_payload,
                diagnostics_payload,
            ),
            diagnostics=diagnostics_payload,
        )

    def evaluate_snapshot(
        self,
        runtime_assembly_snapshot: Mapping[str, Any],
        *,
        run_id: str | None = None,
        ablation_flags: Mapping[str, bool] | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> EvaluationRun | None:
        record = context_v2_evaluation_record_from_snapshot(runtime_assembly_snapshot)
        if record is None:
            return None
        return self.evaluate_records(
            [record],
            run_id=run_id or record.trace_id or "context_eval_run",
            ablation_flags=ablation_flags,
            diagnostics=diagnostics,
        )

    def evaluate_turn_pipeline_result(
        self,
        turn_pipeline_result: Any,
        *,
        event_log: Any | None = None,
        run_id: str | None = None,
        ablation_flags: Mapping[str, bool] | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> EvaluationRun:
        return context_v2_evaluation_run_from_turn_pipeline_result(
            turn_pipeline_result,
            event_log=event_log,
            suite_id=self.suite_id,
            run_id=run_id,
            ablation_flags=ablation_flags,
            diagnostics=diagnostics,
        )

    def evaluate_hcms_consolidation_replays(
        self,
        replays: list[Any] | tuple[Any, ...],
        *,
        run_id: str | None = None,
        ablation_flags: Mapping[str, bool] | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> EvaluationRun:
        records = [
            hcms_v2_consolidation_replay_to_evaluation_record(
                replay,
                diagnostics=diagnostics,
            )
            for replay in replays
        ]
        return self.evaluate_records(
            records,
            run_id=run_id,
            ablation_flags=ablation_flags,
            diagnostics=diagnostics,
        )


def context_assembly_trace_to_evaluation_record(
    trace: ContextAssemblyTrace | Mapping[str, Any],
    *,
    fallback_used: bool = False,
    actual_prompt_mode: str | None = None,
    actual_system_prompt_hash: str | None = None,
    diagnostic_only: bool = False,
    runtime_replay: Mapping[str, Any] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> ContextAssemblyEvaluationRecord:
    payload = trace.model_dump(mode="json") if isinstance(trace, ContextAssemblyTrace) else dict(trace)
    budget = _mapping(payload.get("budget"))
    block_traces = _mapping_list(payload.get("block_traces"))
    drop_decisions = _mapping_list(payload.get("drop_decisions"))
    replay = _mapping(runtime_replay)
    return ContextAssemblyEvaluationRecord(
        trace_id=_str_or_none(payload.get("trace_id")),
        prompt_hash=_str_or_none(payload.get("prompt_hash")),
        actual_prompt_mode=actual_prompt_mode,
        actual_system_prompt_hash=actual_system_prompt_hash,
        diagnostic_only=bool(diagnostic_only),
        fallback_used=bool(fallback_used),
        candidate_block_count=len(_strings(payload.get("candidate_block_ids"))),
        selected_block_count=len(_strings(payload.get("selected_block_ids"))),
        dropped_block_count=len(_strings(payload.get("dropped_block_ids"))),
        compressed_block_count=len(_strings(payload.get("compressed_block_ids"))),
        deferred_block_count=len(_strings(payload.get("deferred_block_ids"))),
        total_tokens=_int_or_zero(payload.get("total_tokens")),
        max_context_tokens=_int_or_none(budget.get("max_context_tokens")),
        reserved_response_tokens=_int_or_none(budget.get("reserved_response_tokens")),
        hard_context_tokens=_hard_context_tokens(budget),
        layer_token_usage=_int_mapping(payload.get("layer_token_usage")),
        source_kind_counts=_count_field(block_traces, "source_kind"),
        block_type_counts=_count_field(block_traces, "block_type"),
        drop_reason_counts=_count_field(drop_decisions, "reason"),
        selected_tools=_strings(payload.get("selected_tools") or payload.get("selected_capabilities")),
        selected_mcp_tools=_strings(payload.get("selected_mcp_tools")),
        selected_skills=_strings(payload.get("selected_skills")),
        selected_memory=_strings(payload.get("selected_memory")),
        selected_workspace=_strings(payload.get("selected_workspace")),
        selected_events=_strings(payload.get("selected_events")),
        selected_tool_results=_strings(payload.get("selected_tool_results")),
        selected_tool_result_refs=_strings(payload.get("selected_tool_result_refs")),
        runtime_event_count=_int_or_zero(replay.get("runtime_event_count")),
        runtime_event_counts=_int_mapping(replay.get("runtime_event_counts")),
        runtime_event_refs=_strings(replay.get("runtime_event_refs")),
        runtime_event_trace_ids=_strings(replay.get("runtime_event_trace_ids")),
        runtime_tool_result_refs=_strings(replay.get("runtime_tool_result_refs")),
        runtime_workspace_refs=_strings(replay.get("runtime_workspace_refs")),
        runtime_memory_refs=_strings(replay.get("runtime_memory_refs")),
        replay_phase_coverage=_bool_mapping(replay.get("replay_phase_coverage")),
        replay_missing_phases=_strings(replay.get("replay_missing_phases")),
        trace_replay_ready=bool(replay.get("trace_replay_ready")),
        diagnostics={str(key): value for key, value in _mapping(diagnostics).items()},
    )


def context_v2_evaluation_record_from_snapshot(
    runtime_assembly_snapshot: Mapping[str, Any],
) -> ContextAssemblyEvaluationRecord | None:
    snapshot = _mapping(runtime_assembly_snapshot)
    context_v2 = _mapping(snapshot.get("context_v2"))
    if not context_v2 or not bool(context_v2.get("enabled")):
        return None
    trace = _mapping(context_v2.get("trace"))
    if not trace:
        return None
    replay = _runtime_replay_from_context_v2(context_v2)
    return context_assembly_trace_to_evaluation_record(
        trace,
        fallback_used=bool(context_v2.get("fallback_used")),
        actual_prompt_mode=_str_or_none(context_v2.get("actual_prompt_mode")),
        actual_system_prompt_hash=_str_or_none(context_v2.get("actual_system_prompt_hash")),
        diagnostic_only=bool(context_v2.get("diagnostic_only")),
        runtime_replay=replay,
        diagnostics=_mapping(context_v2.get("diagnostics")),
    )


def context_v2_evaluation_run_from_snapshot(
    runtime_assembly_snapshot: Mapping[str, Any],
    *,
    suite_id: str = "context-v2",
    run_id: str | None = None,
    ablation_flags: Mapping[str, bool] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> EvaluationRun | None:
    return ContextEvaluationSuite(suite_id=suite_id).evaluate_snapshot(
        runtime_assembly_snapshot,
        run_id=run_id,
        ablation_flags=ablation_flags,
        diagnostics=diagnostics,
    )


def context_v2_evaluation_run_from_turn_pipeline_result(
    turn_pipeline_result: Any,
    *,
    event_log: Any | None = None,
    suite_id: str = "context-v2",
    run_id: str | None = None,
    ablation_flags: Mapping[str, bool] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> EvaluationRun:
    trace = _turn_pipeline_trace(turn_pipeline_result)
    replay = _runtime_replay_from_events(
        _event_items(event_log)
        or _event_items(getattr(turn_pipeline_result, "events", None))
    )
    merged_diagnostics = {
        "bridge": "turn_pipeline_result",
        **{str(key): value for key, value in _mapping(diagnostics).items()},
    }
    assembled_context = getattr(turn_pipeline_result, "assembled_context", None)
    record = context_assembly_trace_to_evaluation_record(
        trace,
        fallback_used=bool(getattr(assembled_context, "fallback_used", False)),
        runtime_replay=replay,
        diagnostics=merged_diagnostics,
    )
    return ContextEvaluationSuite(suite_id=suite_id).evaluate_records(
        [record],
        run_id=run_id or record.trace_id or "context_eval_run",
        ablation_flags=ablation_flags,
        diagnostics=merged_diagnostics,
    )


def hcms_v2_consolidation_replay_to_evaluation_record(
    replay: Any,
    *,
    diagnostics: Mapping[str, Any] | None = None,
) -> ContextAssemblyEvaluationRecord:
    """Project an HCMS V2 slow consolidation replay into Evaluation Suite metrics."""

    payload = _mapping(replay)
    consolidated_memories = _mapping_list(payload.get("consolidated_memories"))
    replay_refs = _hcms_v2_replay_refs(consolidated_memories)
    source_memory_ids = _strings(payload.get("source_memory_ids"))
    consolidated_memory_ids = _strings([memory.get("memory_id") for memory in consolidated_memories])
    runtime_event_refs = _strings(payload.get("runtime_event_ids"))
    runtime_event_count = len(runtime_event_refs) or (1 if payload else 0)
    replay_phase_coverage = _bool_mapping(payload.get("replay_phase_coverage"))
    missing_phases = _strings(payload.get("replay_missing_phases"))
    status = _str_or_none(payload.get("status")) or "unknown"
    slow_consolidation_ready = bool(
        status == "completed"
        and not missing_phases
        and replay_phase_coverage
        and consolidated_memory_ids
    )
    replay_phase_coverage["slow_consolidation"] = slow_consolidation_ready
    if not slow_consolidation_ready and "slow_consolidation" not in missing_phases:
        missing_phases.append("slow_consolidation")
    diagnostics_payload = {
        "source": "hcms_v2_consolidation_replay",
        "replay_id": _str_or_none(payload.get("replay_id")),
        "schedule_id": _str_or_none(payload.get("schedule_id")),
        "task_id": _str_or_none(payload.get("task_id")),
        "target_layer": _str_or_none(payload.get("target_layer")),
        "status": status,
        "consolidated_memory_count": len(consolidated_memories),
        "source_memory_count": len(source_memory_ids),
        "runtime_event_count": runtime_event_count,
        "replay_refs": _compact_replay_refs(replay_refs),
        **{str(key): value for key, value in _mapping(diagnostics).items()},
    }
    return ContextAssemblyEvaluationRecord(
        trace_id=_str_or_none(payload.get("replay_id")),
        runtime_event_count=runtime_event_count,
        runtime_event_counts={"hcms_v2_slow_consolidation": runtime_event_count} if runtime_event_count else {},
        runtime_event_refs=runtime_event_refs,
        runtime_tool_result_refs=_strings(replay_refs.get("tool_result_refs")),
        runtime_workspace_refs=_strings(replay_refs.get("workspace_refs")),
        runtime_memory_refs=_unique_strings([source_memory_ids, consolidated_memory_ids]),
        replay_phase_coverage=dict(sorted(replay_phase_coverage.items())),
        replay_missing_phases=missing_phases,
        trace_replay_ready=slow_consolidation_ready,
        diagnostics=diagnostics_payload,
    )


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            payload = dump(mode="json")
        except TypeError:
            payload = dump()
        if isinstance(payload, Mapping):
            return dict(payload)
    return {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value if str(item).strip()]


def _int_mapping(value: Any) -> dict[str, int]:
    mapping = _mapping(value)
    result: dict[str, int] = {}
    for key, item in mapping.items():
        numeric = _int_or_none(item)
        if numeric is not None:
            result[str(key)] = numeric
    return dict(sorted(result.items()))


def _bool_mapping(value: Any) -> dict[str, bool]:
    mapping = _mapping(value)
    return {str(key): bool(item) for key, item in sorted(mapping.items(), key=lambda item: str(item[0]))}


def _merge_int_mappings(values: Any) -> dict[str, int]:
    merged: Counter[str] = Counter()
    for value in values:
        merged.update(_int_mapping(value))
    return dict(sorted(merged.items()))


def _unique_strings(values: Any) -> list[str]:
    unique: dict[str, None] = {}
    for value in values:
        items = [value] if isinstance(value, str) else _strings(value)
        for item in items:
            unique.setdefault(item, None)
    return list(unique)


def _aggregate_metrics(records: list[ContextAssemblyEvaluationRecord]) -> dict[str, int | float]:
    replay_phase_coverage = _aggregate_replay_phase_coverage(records)
    replay_missing_phases = [phase for phase, covered in replay_phase_coverage.items() if not covered]
    metrics: dict[str, int | float] = {
        "trace_count": len(records),
        "candidate_block_count": sum(record.candidate_block_count for record in records),
        "selected_block_count": sum(record.selected_block_count for record in records),
        "dropped_block_count": sum(record.dropped_block_count for record in records),
        "compressed_block_count": sum(record.compressed_block_count for record in records),
        "deferred_block_count": sum(record.deferred_block_count for record in records),
        "total_tokens": sum(record.total_tokens for record in records),
        "fallback_count": sum(1 for record in records if record.fallback_used),
        "diagnostic_only_count": sum(1 for record in records if record.diagnostic_only),
        "runtime_event_count": sum(record.runtime_event_count for record in records),
        "runtime_event_type_count": len(_merge_int_mappings(record.runtime_event_counts for record in records)),
        "trace_replay_case_count": sum(1 for record in records if record.runtime_event_count > 0),
        "replay_ready_count": sum(1 for record in records if record.trace_replay_ready),
        "replay_missing_case_count": sum(
            1 for record in records if record.runtime_event_count > 0 and not record.trace_replay_ready
        ),
        "replay_unavailable_case_count": sum(1 for record in records if record.runtime_event_count == 0),
        "replay_case_missing_phase_count": sum(len(record.replay_missing_phases) for record in records),
        "replay_required_phase_count": len(replay_phase_coverage),
        "replay_covered_phase_count": sum(1 for covered in replay_phase_coverage.values() if covered),
        "replay_missing_phase_count": len(replay_missing_phases),
    }
    drop_reason_counts = _merge_int_mappings(record.drop_reason_counts for record in records)
    metrics["reference_only_count"] = drop_reason_counts.get("reference_only", 0)
    metrics["max_total_tokens"] = max((record.total_tokens for record in records), default=0)
    metrics["average_total_tokens"] = round(
        metrics["total_tokens"] / len(records),
        4,
    ) if records else 0
    hard_context_values = [
        record.hard_context_tokens
        for record in records
        if record.hard_context_tokens is not None
    ]
    if hard_context_values:
        metrics["max_hard_context_tokens"] = max(hard_context_values)
        metrics["token_overhead_ratio"] = round(
            metrics["total_tokens"] / max(sum(hard_context_values), 1),
            4,
        )
    else:
        metrics["token_overhead_ratio"] = 0
    return metrics


def _ablation_variant_metrics(
    records: list[ContextAssemblyEvaluationRecord],
    metrics: Mapping[str, int | float],
    ablation_flags: Mapping[str, bool],
    diagnostics: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if not ablation_flags:
        return {}

    token_overhead_ratio = _float_or_none(metrics.get("token_overhead_ratio")) or 0.0
    total_tokens = int(metrics.get("total_tokens") or 0)
    trace_count = int(metrics.get("trace_count") or 0)
    default_quality_score = _default_quality_score(records, metrics)
    default_latency_ms = _diagnostic_float(diagnostics, "latency_ms", "average_latency_ms") or 0.0
    variants: dict[str, dict[str, Any]] = {}
    for flag, enabled in sorted(ablation_flags.items()):
        quality_score = _diagnostic_float(diagnostics, f"{flag}_quality_score")
        if quality_score is None:
            quality_score = _diagnostic_float(diagnostics, "quality_score")
        if quality_score is None:
            quality_score = default_quality_score
        latency_ms = _diagnostic_float(diagnostics, f"{flag}_latency_ms")
        if latency_ms is None:
            latency_ms = default_latency_ms
        variant: dict[str, Any] = {
            "enabled": bool(enabled),
            "trace_count": trace_count,
            "quality_score": round(float(quality_score), 4),
            "latency_ms": round(float(latency_ms or 0.0), 3),
            "token_overhead_ratio": round(float(token_overhead_ratio), 4),
            "total_tokens": total_tokens,
        }
        quality_delta = _diagnostic_float(diagnostics, f"{flag}_quality_delta")
        if quality_delta is not None:
            variant["quality_delta"] = round(quality_delta, 4)
        latency_delta = _diagnostic_float(diagnostics, f"{flag}_latency_delta_ms")
        if latency_delta is not None:
            variant["latency_delta_ms"] = round(latency_delta, 3)
        token_cost = _diagnostic_float(
            diagnostics,
            f"{flag}_token_cost_tokens",
            f"{flag}_token_overhead_tokens",
        )
        if token_cost is not None:
            variant["token_cost_tokens"] = int(token_cost)
        variants[str(flag)] = variant
    return variants


def _default_quality_score(
    records: list[ContextAssemblyEvaluationRecord],
    metrics: Mapping[str, int | float],
) -> float:
    values = [
        value
        for record in records
        if (value := _float_or_none(record.diagnostics.get("context_usefulness"))) is not None
    ]
    if values:
        return round(sum(values) / len(values), 4)
    trace_count = int(metrics.get("trace_count") or 0)
    if not trace_count:
        return 0.0
    replay_ready_count = int(metrics.get("replay_ready_count") or 0)
    return round(replay_ready_count / trace_count, 4)


def _diagnostic_float(diagnostics: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in diagnostics:
            value = _float_or_none(diagnostics.get(key))
            if value is not None:
                return value
    return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _case_diagnostics(record: ContextAssemblyEvaluationRecord) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "trace_replay_ready": bool(record.trace_replay_ready),
    }
    if record.runtime_event_count:
        diagnostics["runtime_event_count"] = record.runtime_event_count
    if record.replay_missing_phases:
        diagnostics["replay_missing_phases"] = list(record.replay_missing_phases)
    if record.replay_phase_coverage:
        diagnostics["replay_phase_coverage"] = dict(record.replay_phase_coverage)
    return diagnostics


def _trace_replay_matrix(cases: tuple[EvaluationCase, ...]) -> list[dict[str, Any]]:
    matrix: list[dict[str, Any]] = []
    for case in cases:
        record = case.record
        entry: dict[str, Any] = {
            "case_id": case.case_id,
            "trace_id": record.trace_id,
            "prompt_hash": record.prompt_hash,
            "trace_replay_ready": bool(record.trace_replay_ready),
            "runtime_event_count": record.runtime_event_count,
            "runtime_event_counts": dict(record.runtime_event_counts),
            "runtime_event_refs": list(record.runtime_event_refs),
            "runtime_event_trace_ids": list(record.runtime_event_trace_ids),
            "runtime_tool_result_refs": list(record.runtime_tool_result_refs),
            "runtime_workspace_refs": list(record.runtime_workspace_refs),
            "runtime_memory_refs": list(record.runtime_memory_refs),
            "replay_phase_coverage": dict(record.replay_phase_coverage),
            "replay_missing_phases": list(record.replay_missing_phases),
        }
        blocker = _trace_replay_blocker(record)
        if blocker:
            entry["replay_blocker"] = blocker
        matrix.append(_compact_replay_matrix_entry(entry))
    return matrix


def _trace_replay_blocker(record: ContextAssemblyEvaluationRecord) -> str | None:
    if record.trace_replay_ready:
        return None
    if record.replay_missing_phases:
        return "missing_phases"
    if not record.runtime_event_count:
        return "no_runtime_events"
    if not record.replay_phase_coverage:
        return "phase_coverage_unavailable"
    return "not_ready"


def _compact_replay_matrix_entry(entry: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in entry.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        compacted[key] = value
    return compacted


def _aggregate_replay_phase_coverage(records: list[ContextAssemblyEvaluationRecord]) -> dict[str, bool]:
    coverages = [record.replay_phase_coverage for record in records if record.replay_phase_coverage]
    if not coverages:
        return {}
    phases = sorted({phase for coverage in coverages for phase in coverage})
    return {phase: any(bool(coverage.get(phase)) for coverage in coverages) for phase in phases}


def _runtime_replay_from_context_v2(context_v2: Mapping[str, Any]) -> dict[str, Any]:
    event_log = _runtime_event_log(context_v2)
    events = event_log["events"]
    event_types = event_log["event_types"]
    event_type_values = [
        _str_or_none(event.get("event_type"))
        for event in events
        if _str_or_none(event.get("event_type")) is not None
    ]
    if not event_type_values:
        event_type_values = event_types
    event_counts = Counter(event_type_values)
    replay_phase_coverage = {
        phase: bool(event_counts.get(phase))
        for phase in REPLAY_REQUIRED_PHASES
    } if event_type_values else {}
    replay_missing_phases = [
        phase for phase, covered in replay_phase_coverage.items() if not covered
    ]
    runtime_event_count = len(events) if events else len(event_type_values)
    return {
        "runtime_event_count": runtime_event_count,
        "runtime_event_counts": dict(sorted(event_counts.items())),
        "runtime_event_refs": _unique_strings(
            [event.get("event_id") for event in events if event.get("event_id")]
        ),
        "runtime_event_trace_ids": _unique_strings(
            [event.get("trace_id") for event in events if event.get("trace_id")]
        ),
        "runtime_tool_result_refs": _unique_event_refs(events, "tool_result_refs"),
        "runtime_workspace_refs": _unique_event_refs(events, "workspace_refs"),
        "runtime_memory_refs": _unique_event_refs(events, "memory_refs"),
        "replay_phase_coverage": replay_phase_coverage,
        "replay_missing_phases": replay_missing_phases,
        "trace_replay_ready": bool(runtime_event_count and replay_phase_coverage and not replay_missing_phases),
    }


def _runtime_replay_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_type_values = [
        _str_or_none(event.get("event_type"))
        for event in events
        if _str_or_none(event.get("event_type")) is not None
    ]
    event_counts = Counter(event_type_values)
    replay_phase_coverage = {
        phase: bool(event_counts.get(phase))
        for phase in REPLAY_REQUIRED_PHASES
    } if event_type_values else {}
    replay_missing_phases = [
        phase for phase, covered in replay_phase_coverage.items() if not covered
    ]
    return {
        "runtime_event_count": len(events),
        "runtime_event_counts": dict(sorted(event_counts.items())),
        "runtime_event_refs": _unique_strings(
            [event.get("event_id") for event in events if event.get("event_id")]
        ),
        "runtime_event_trace_ids": _unique_strings(
            [event.get("trace_id") for event in events if event.get("trace_id")]
        ),
        "runtime_tool_result_refs": _unique_event_refs(events, "tool_result_refs"),
        "runtime_workspace_refs": _unique_event_refs(events, "workspace_refs"),
        "runtime_memory_refs": _unique_event_refs(events, "memory_refs"),
        "replay_phase_coverage": replay_phase_coverage,
        "replay_missing_phases": replay_missing_phases,
        "trace_replay_ready": bool(events and replay_phase_coverage and not replay_missing_phases),
    }


def _event_items(source: Any) -> list[dict[str, Any]]:
    if source is None:
        return []
    if isinstance(source, Mapping):
        if "events" in source:
            return _mapping_list(source.get("events"))
        return []
    events = getattr(source, "events", source)
    if not isinstance(events, list | tuple):
        return []
    items: list[dict[str, Any]] = []
    for event in events:
        if isinstance(event, Mapping):
            items.append(dict(event))
        elif hasattr(event, "model_dump"):
            items.append(event.model_dump(mode="json"))
    return items


def _turn_pipeline_trace(turn_pipeline_result: Any) -> ContextAssemblyTrace | Mapping[str, Any]:
    assembled_context = getattr(turn_pipeline_result, "assembled_context", None)
    trace = getattr(assembled_context, "trace", None)
    if trace is None and isinstance(turn_pipeline_result, Mapping):
        trace = _mapping(_mapping(turn_pipeline_result.get("assembled_context")).get("trace"))
    if trace is None:
        raise ValueError("turn_pipeline_result must include assembled_context.trace")
    return trace


def _runtime_event_log(context_v2: Mapping[str, Any]) -> dict[str, Any]:
    runtime_state = _mapping(context_v2.get("runtime_state"))
    event_log = runtime_state.get("event_log")
    if isinstance(event_log, Mapping):
        return {
            "events": _mapping_list(event_log.get("events")),
            "event_types": _strings(event_log.get("event_types")),
        }
    if isinstance(event_log, list | tuple):
        return {
            "events": _mapping_list(event_log),
            "event_types": [],
        }
    context_event_log = context_v2.get("event_log")
    if isinstance(context_event_log, list | tuple):
        return {
            "events": _mapping_list(context_event_log),
            "event_types": [],
        }
    turn_pipeline = _mapping(context_v2.get("turn_pipeline"))
    return {
        "events": [],
        "event_types": _strings(turn_pipeline.get("event_types")),
    }


def _unique_event_refs(events: list[dict[str, Any]], field: str) -> list[str]:
    return _unique_strings(event.get(field) for event in events)


def _hcms_v2_replay_refs(consolidated_memories: list[dict[str, Any]]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for memory in consolidated_memories:
        metadata = _mapping(memory.get("metadata"))
        for key, value in _mapping(metadata.get("replay_refs")).items():
            refs[str(key)] = value
    return refs


def _compact_replay_refs(replay_refs: Mapping[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in replay_refs.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, list | tuple):
            compacted[str(key)] = _strings(value)[:12]
        elif isinstance(value, Mapping):
            compacted[str(key)] = {
                str(child_key): str(child_value)[:240]
                for child_key, child_value in value.items()
                if child_value is not None and str(child_value).strip()
            }
        else:
            compacted[str(key)] = str(value)[:240]
    return compacted


def _count_field(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(str(item.get(field) or "unknown") for item in items)
    counts.pop("", None)
    return dict(sorted(counts.items()))


def _hard_context_tokens(budget: Mapping[str, Any]) -> int | None:
    max_context = _int_or_none(budget.get("max_context_tokens"))
    reserved = _int_or_none(budget.get("reserved_response_tokens"))
    if max_context is None or reserved is None:
        return None
    return max(max_context - reserved, 1)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    return _int_or_none(value) or 0


def _str_or_none(value: Any) -> str | None:
    text = "" if value is None else str(value)
    return text or None


__all__ = [
    "ContextAssemblyEvaluationRecord",
    "ContextEvaluationSuite",
    "EvaluationCase",
    "EvaluationRun",
    "context_assembly_trace_to_evaluation_record",
    "context_v2_evaluation_record_from_snapshot",
    "context_v2_evaluation_run_from_snapshot",
    "context_v2_evaluation_run_from_turn_pipeline_result",
    "hcms_v2_consolidation_replay_to_evaluation_record",
]
