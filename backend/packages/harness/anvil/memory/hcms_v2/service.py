from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from anvil.memory.contracts import RetrievalResult, sanitize_memory_context_text
from anvil.runtime.context_v2 import ContextBlock
from anvil.runtime.token_budget import TokenBudgetService
from pydantic import BaseModel, ConfigDict, Field

from .adapters import (
    capability_usage_event_from_runtime_event,
    capability_usage_events_to_procedure_wisdom_batch,
    memory_injection_view_v2_to_blocks,
    memory_search_result_from_retrieval_result,
    observation_record_from_runtime_event,
    runtime_event_to_capture_envelope,
    tool_result_record_to_episodic_memory,
    workspace_state_to_working_memory,
)
from .conflict import ConflictLedger
from .contracts import (
    CaptureEnvelopeV2,
    ConflictRecord,
    ClaimRecord,
    ClaimScope,
    MemoryGuardDecision,
    MemoryInjectionViewV2,
    MemorySearchResult,
    ObservationRecord,
    CapabilityUsageEvent,
    ConsolidatedMemory,
    EvidenceSpan,
    ForgettingProfile,
    HCMSV2ConsolidationReplayResult,
    HCMSV2ConsolidationSchedule,
    HCMSV2ConsolidationTask,
    ProcedureWisdomMiningBatch,
    bounded_score,
    stable_hcms_id,
    utc_now,
)
from .guard import MemoryGuard


class HCMSV2ForgettingFeedbackResult(BaseModel):
    """Export-safe result for context feedback applied to HCMS V2 forgetting profiles."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    memories: tuple[ConsolidatedMemory, ...] = Field(default_factory=tuple, exclude=True)
    updated_memory_ids: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class HCMSV2CaptureResult:
    envelope: CaptureEnvelopeV2
    observation: ObservationRecord
    guard_decision: MemoryGuardDecision


@dataclass(frozen=True)
class HCMSV2ToolResultCaptureResult:
    episodic_memory: ConsolidatedMemory
    workspace_memory: ConsolidatedMemory | None = None
    diagnostics: dict[str, object] | None = None


class HCMSV2RuntimeBridge:
    """Additive HCMS V2 seam for Batch B capture, retrieval, and context injection."""

    def __init__(
        self,
        *,
        memory_guard: MemoryGuard | None = None,
        conflict_ledger: ConflictLedger | None = None,
        token_budget: TokenBudgetService | None = None,
    ) -> None:
        self.memory_guard = memory_guard or MemoryGuard()
        self.conflict_ledger = conflict_ledger or ConflictLedger()
        self.token_budget = token_budget or TokenBudgetService()

    def capture_runtime_event(self, event: object, *, namespace: str = "global/default") -> HCMSV2CaptureResult:
        envelope = runtime_event_to_capture_envelope(event, namespace=namespace)
        observation = observation_record_from_runtime_event(event, namespace=namespace)
        decision = self.memory_guard.inspect_observation(observation)
        guarded_observation = _apply_guard_decision(observation, decision)
        guarded_envelope = envelope.model_copy(
            update={
                "privacy_level": guarded_observation.privacy_level,
                "metadata": {
                    **envelope.metadata,
                    "guard_decision_id": decision.decision_id,
                    "guard_action": decision.action,
                    "guard_reasons": list(decision.reasons),
                    "observation_id": guarded_observation.observation_id,
                },
            }
        )
        return HCMSV2CaptureResult(
            envelope=guarded_envelope,
            observation=guarded_observation,
            guard_decision=decision,
        )

    def search_result_from_retrieval_result(
        self,
        result: RetrievalResult,
        *,
        namespace: str = "global/default",
    ) -> MemorySearchResult:
        return memory_search_result_from_retrieval_result(
            result,
            namespace=namespace,
            token_budget=self.token_budget,
        )

    def injection_view_from_retrieval_results(
        self,
        results: list[RetrievalResult] | tuple[RetrievalResult, ...],
        *,
        namespace: str = "global/default",
        query: str = "",
        conflicts: list[ConflictRecord] | tuple[ConflictRecord, ...] = (),
    ) -> MemoryInjectionViewV2:
        converted = [self.search_result_from_retrieval_result(result, namespace=namespace) for result in results]
        sensory: list[MemorySearchResult] = []
        working: list[MemorySearchResult] = []
        semantic: list[MemorySearchResult] = []
        episodic: list[MemorySearchResult] = []
        procedural: list[MemorySearchResult] = []
        wisdom: list[MemorySearchResult] = []

        for result in converted:
            layer = result.layer.lower()
            if layer == "sensory":
                sensory.append(result)
            elif layer == "working":
                working.append(result)
            elif layer == "episodic":
                episodic.append(result)
            elif layer in {"procedural", "procedure"}:
                procedural.append(result)
            elif layer == "wisdom":
                wisdom.append(result)
            else:
                semantic.append(result)

        return MemoryInjectionViewV2(
            namespace=namespace,
            query=query,
            sensory_results=sensory,
            working_results=working,
            semantic_results=semantic,
            episodic_results=episodic,
            procedural_results=procedural,
            wisdom_results=wisdom,
            conflict_warnings=list(conflicts),
            diagnostics={
                "source": "hcms_v2_runtime_bridge",
                "retrieval_result_count": len(results),
                "converted_result_count": len(converted),
                "conflict_warning_count": len(conflicts),
                "layer_counts": {
                    "sensory": len(sensory),
                    "working": len(working),
                    "episodic": len(episodic),
                    "semantic": len(semantic),
                    "procedural": len(procedural),
                    "wisdom": len(wisdom),
                },
            },
        )

    def context_blocks_from_injection_view(self, view: MemoryInjectionViewV2) -> list[ContextBlock]:
        return memory_injection_view_v2_to_blocks(view, token_budget=self.token_budget)

    def context_blocks_from_retrieval_results(
        self,
        results: list[RetrievalResult] | tuple[RetrievalResult, ...],
        *,
        namespace: str = "global/default",
        query: str = "",
        conflicts: list[ConflictRecord] | tuple[ConflictRecord, ...] = (),
    ) -> list[ContextBlock]:
        view = self.injection_view_from_retrieval_results(
            results,
            namespace=namespace,
            query=query,
            conflicts=conflicts,
        )
        return self.context_blocks_from_injection_view(view)

    def mine_capability_usage_events(
        self,
        events: list[CapabilityUsageEvent] | tuple[CapabilityUsageEvent, ...],
        *,
        namespace: str = "global/default",
    ) -> ProcedureWisdomMiningBatch:
        return capability_usage_events_to_procedure_wisdom_batch(
            events,
            namespace=namespace,
            token_budget=self.token_budget,
        )

    def capture_tool_result_record(
        self,
        record: object,
        *,
        workspace_state: object | None = None,
        namespace: str = "global/default",
    ) -> HCMSV2ToolResultCaptureResult:
        episodic_memory = tool_result_record_to_episodic_memory(
            record,
            namespace=namespace,
            token_budget=self.token_budget,
        )
        workspace_memory = (
            workspace_state_to_working_memory(
                workspace_state,
                namespace=namespace,
                token_budget=self.token_budget,
            )
            if workspace_state is not None
            else None
        )
        return HCMSV2ToolResultCaptureResult(
            episodic_memory=episodic_memory,
            workspace_memory=workspace_memory,
            diagnostics={
                "source": "tool_result_record",
                "namespace": namespace,
                "workspace_memory_generated": workspace_memory is not None,
            },
        )

    def apply_context_feedback_to_forgetting(
        self,
        memories: list[ConsolidatedMemory] | tuple[ConsolidatedMemory, ...],
        evaluation_run: object,
        *,
        workspace_ref: str | None = None,
    ) -> HCMSV2ForgettingFeedbackResult:
        return adaptive_forgetting_profiles_from_evaluation_run(
            memories,
            evaluation_run,
            workspace_ref=workspace_ref,
        )

    def schedule_capture_consolidation(
        self,
        capture: HCMSV2CaptureResult,
        *,
        persisted_memory_id: str,
    ) -> HCMSV2ConsolidationSchedule:
        return hcms_v2_consolidation_schedule_from_capture(
            capture,
            persisted_memory_id=persisted_memory_id,
        )

    def replay_slow_consolidation(
        self,
        capture: HCMSV2CaptureResult,
        *,
        schedule: HCMSV2ConsolidationSchedule,
    ) -> HCMSV2ConsolidationReplayResult:
        return hcms_v2_replay_slow_consolidation(
            capture,
            schedule=schedule,
            token_budget=self.token_budget,
        )


def hcms_v2_consolidation_schedule_from_capture(
    capture: HCMSV2CaptureResult,
    *,
    persisted_memory_id: str,
) -> HCMSV2ConsolidationSchedule:
    """Build a replay-safe fast/slow consolidation schedule for a runtime capture."""

    envelope = capture.envelope
    observation = capture.observation
    namespace = str(envelope.namespace or observation.namespace or "global/default")
    runtime_event_ids = [event.event_id for event in envelope.runtime_events]
    content_hash = stable_hcms_id(
        "content_hash_v2",
        namespace,
        envelope.envelope_id,
        observation.observation_id,
        observation.content,
        observation.content_ref,
        size=16,
    )
    replay_refs = {
        "payload_ref": _str_or_none(envelope.runtime_events[0].payload_ref if envelope.runtime_events else observation.content_ref),
        "content_ref": _str_or_none(observation.content_ref),
        "source_ref": _str_or_none(envelope.runtime_events[0].source_ref if envelope.runtime_events else observation.source_id),
        "tool_result_refs": list(envelope.tool_result_refs),
        "workspace_refs": list(observation.workspace_refs),
        "goal_stack_ref": _str_or_none(envelope.goal_stack_ref),
        "capability_usage_refs": list(envelope.capability_usage_refs),
    }
    fast_task = HCMSV2ConsolidationTask(
        namespace=namespace,
        mode="fast",
        status="completed",
        target_layer="episodic",
        capture_envelope_id=envelope.envelope_id,
        observation_id=observation.observation_id,
        source_memory_ids=[persisted_memory_id],
        runtime_event_ids=runtime_event_ids,
        content_hash=content_hash,
        priority=bounded_score(envelope.salience_seed),
        reason="runtime_event_fast_capture",
        due_at=observation.timestamp,
        replay_refs=replay_refs,
        diagnostics={
            "guard_action": capture.guard_decision.action,
            "guard_trust_score": capture.guard_decision.trust_score,
        },
    )
    slow_target_layer = _slow_consolidation_target_layer(observation.observation_type)
    slow_task = HCMSV2ConsolidationTask(
        namespace=namespace,
        mode="slow",
        status="scheduled",
        target_layer=slow_target_layer,
        capture_envelope_id=envelope.envelope_id,
        observation_id=observation.observation_id,
        source_memory_ids=[persisted_memory_id],
        runtime_event_ids=runtime_event_ids,
        content_hash=content_hash,
        priority=bounded_score(max(envelope.salience_seed, capture.guard_decision.trust_score)),
        reason="runtime_event_slow_consolidation",
        due_at=observation.timestamp,
        replay_refs=replay_refs,
        diagnostics={
            "candidate_layers": _slow_consolidation_candidate_layers(observation.observation_type),
            "event_type": observation.observation_type,
            "thread_id": observation.thread_id or envelope.thread_id,
            "run_id": observation.run_id or envelope.run_id,
        },
    )
    return HCMSV2ConsolidationSchedule(
        namespace=namespace,
        capture_envelope_id=envelope.envelope_id,
        observation_id=observation.observation_id,
        fast_task=fast_task,
        slow_task=slow_task,
        diagnostics={
            "source": "hcms_v2_runtime_capture",
            "persisted_memory_id": persisted_memory_id,
            "runtime_event_count": len(runtime_event_ids),
            "slow_target_layer": slow_target_layer,
        },
    )


def hcms_v2_replay_slow_consolidation(
    capture: HCMSV2CaptureResult,
    *,
    schedule: HCMSV2ConsolidationSchedule,
    token_budget: TokenBudgetService | None = None,
) -> HCMSV2ConsolidationReplayResult:
    """Replay a scheduled slow consolidation task without reading raw artifacts."""

    counter = token_budget or TokenBudgetService()
    task = schedule.slow_task
    consolidated_memory = _slow_consolidation_memory_from_replay(
        capture,
        schedule=schedule,
        task=task,
        token_budget=counter,
    )
    claim = _slow_consolidation_claim_from_memory(
        consolidated_memory,
        capture=capture,
        schedule=schedule,
        task=task,
    )
    if claim.claim_id:
        consolidated_memory = consolidated_memory.model_copy(
            update={
                "claims": [claim.claim_id],
                "metadata": {
                    **consolidated_memory.metadata,
                    "claim_id": claim.claim_id,
                    "claim_ids": [claim.claim_id],
                },
            }
        )
    replay_phase_coverage = {
        "capture_envelope": bool(capture.envelope.envelope_id == schedule.capture_envelope_id),
        "observation": bool(capture.observation.observation_id == schedule.observation_id),
        "source_memory": bool(task.source_memory_ids),
        "consolidated_memory": consolidated_memory is not None,
    }
    missing_phases = [phase for phase, covered in replay_phase_coverage.items() if not covered]
    return HCMSV2ConsolidationReplayResult(
        namespace=schedule.namespace,
        schedule_id=str(schedule.schedule_id or ""),
        task_id=str(task.task_id or ""),
        status="completed" if not missing_phases else "partial",
        target_layer=task.target_layer,
        capture_envelope_id=schedule.capture_envelope_id,
        observation_id=schedule.observation_id,
        source_memory_ids=list(task.source_memory_ids),
        runtime_event_ids=list(task.runtime_event_ids),
        claims=[claim],
        consolidated_memories=[consolidated_memory] if consolidated_memory is not None else [],
        replay_phase_coverage=replay_phase_coverage,
        replay_missing_phases=missing_phases,
        diagnostics={
            "source": "hcms_v2_slow_consolidation_replay",
            "mode": task.mode,
            "target_layer": task.target_layer,
            "content_hash": task.content_hash,
            "replay_ref_count": len(_mapping(task.replay_refs)),
            "claim_count": 1,
        },
    )


def _slow_consolidation_memory_from_replay(
    capture: HCMSV2CaptureResult,
    *,
    schedule: HCMSV2ConsolidationSchedule,
    task: HCMSV2ConsolidationTask,
    token_budget: TokenBudgetService,
) -> ConsolidatedMemory:
    observation = capture.observation
    namespace = str(task.namespace or schedule.namespace or observation.namespace or "global/default")
    replay_refs = _safe_replay_refs(task.replay_refs)
    summary = token_budget.truncate_text(
        sanitize_memory_context_text(observation.content or task.reason or observation.observation_type),
        max_tokens=80,
        max_chars=500,
    )
    if not summary:
        summary = f"{observation.observation_type} runtime observation"
    canonical_content = _slow_consolidation_canonical_content(
        observation=observation,
        task=task,
        schedule=schedule,
        summary=summary,
        replay_refs=replay_refs,
        token_budget=token_budget,
    )
    source_uri = _str_or_none(replay_refs.get("payload_ref") or replay_refs.get("content_ref") or observation.content_ref)
    evidence = EvidenceSpan(
        evidence_id=stable_hcms_id(
            "ev_v2",
            namespace,
            "slow_consolidation",
            task.task_id,
            observation.observation_id,
            size=16,
        ),
        observation_id=observation.observation_id,
        source_uri=source_uri,
        source_label=f"hcms_v2_slow_consolidation:{observation.observation_type}",
        quoted_text_hash=stable_hcms_id("quote_v2", task.content_hash, observation.observation_id, size=16),
        excerpt=token_budget.truncate_text(summary, max_tokens=60, max_chars=360),
        trust_score=capture.guard_decision.trust_score,
        timestamp=observation.timestamp,
        collector="hcms_v2_slow_consolidation",
    )
    category = _slow_consolidation_category(task.target_layer, observation.observation_type)
    phase_coverage = {
        "capture_envelope": bool(capture.envelope.envelope_id == schedule.capture_envelope_id),
        "observation": bool(observation.observation_id == schedule.observation_id),
        "source_memory": bool(task.source_memory_ids),
        "consolidated_memory": True,
    }
    return ConsolidatedMemory(
        memory_id=stable_hcms_id(
            "mem_v2",
            namespace,
            "slow_consolidation",
            task.target_layer,
            task.task_id,
            task.content_hash,
            size=16,
        ),
        namespace=namespace,
        layer=task.target_layer,
        category=category,
        title=_slow_consolidation_title(task.target_layer, observation.observation_type),
        summary=summary,
        canonical_content=canonical_content,
        evidence=[evidence],
        confidence=bounded_score(capture.guard_decision.trust_score, default=0.6),
        salience=bounded_score(task.priority),
        stability=0.64,
        metadata={
            "source": "hcms_v2_slow_consolidation_replay",
            "hcms_v2": True,
            "hcms_v2_consolidation_schedule_id": schedule.schedule_id,
            "hcms_v2_slow_consolidation_task_id": task.task_id,
            "capture_envelope_id": schedule.capture_envelope_id,
            "observation_id": schedule.observation_id,
            "source_memory_ids": list(task.source_memory_ids),
            "runtime_event_ids": list(task.runtime_event_ids),
            "content_hash": task.content_hash,
            "replay_refs": replay_refs,
            "replay_phase_coverage": phase_coverage,
            "tool_result_refs": _ordered_strings(replay_refs.get("tool_result_refs")),
            "workspace_refs": _ordered_strings(replay_refs.get("workspace_refs")),
            "capability_usage_refs": _ordered_strings(replay_refs.get("capability_usage_refs")),
        },
    )


def _slow_consolidation_claim_from_memory(
    memory: ConsolidatedMemory,
    *,
    capture: HCMSV2CaptureResult,
    schedule: HCMSV2ConsolidationSchedule,
    task: HCMSV2ConsolidationTask,
) -> ClaimRecord:
    observation = capture.observation
    thread_scope = observation.thread_id or capture.envelope.thread_id or "global"
    subject = sanitize_memory_context_text(
        f"{task.target_layer}:{observation.source_kind}:{observation.source_id or observation.event_id}"
    )[:240]
    predicate = "summarizes"
    object_value = sanitize_memory_context_text(memory.summary or observation.observation_type)[:800]
    return ClaimRecord(
        namespace=memory.namespace,
        claim_type=memory.category or "runtime_observation",
        subject=subject,
        predicate=predicate,
        object_value=object_value,
        human_text=object_value,
        scope=ClaimScope(scope_type="thread", scope_key=thread_scope),
        evidence=list(memory.evidence),
        confidence=memory.confidence,
        freshness=1.0,
        salience=memory.salience,
        privacy_level=observation.privacy_level,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        metadata={
            "source": "hcms_v2_slow_consolidation_replay",
            "memory_id": memory.memory_id,
            "target_layer": task.target_layer,
            "capture_envelope_id": schedule.capture_envelope_id,
            "observation_id": schedule.observation_id,
            "hcms_v2_consolidation_schedule_id": schedule.schedule_id,
            "hcms_v2_slow_consolidation_task_id": task.task_id,
            "runtime_event_ids": list(task.runtime_event_ids),
            "source_memory_ids": list(task.source_memory_ids),
        },
    )


def adaptive_forgetting_profiles_from_evaluation_run(
    memories: list[ConsolidatedMemory] | tuple[ConsolidatedMemory, ...],
    evaluation_run: object,
    *,
    workspace_ref: str | None = None,
) -> HCMSV2ForgettingFeedbackResult:
    """Apply bounded context usefulness feedback to HCMS V2 forgetting profiles."""

    run_payload = _object_payload(evaluation_run)
    now = utc_now()
    selected_refs = _strings(run_payload.get("selected_memory"))
    runtime_workspace_refs = set(_strings(run_payload.get("runtime_workspace_refs")))
    if workspace_ref:
        runtime_workspace_refs.add(str(workspace_ref))
    run_diagnostics = _diagnostics_from_evaluation_run(evaluation_run)
    stale_refs = _strings(run_diagnostics.get("stale_memory_ids"))
    conflicted_refs = _strings(run_diagnostics.get("conflicted_memory_ids"))
    usefulness = _float_score(run_diagnostics.get("context_usefulness"), default=0.0)
    positive_feedback = _is_positive_feedback(run_payload, run_diagnostics)

    updated: list[ConsolidatedMemory] = []
    updated_ids: list[str] = []
    selected_count = 0
    penalized_count = 0
    for memory in memories:
        ref_candidates = _memory_ref_candidates(memory)
        selected_in_context = bool(ref_candidates & selected_refs)
        stale_penalized = bool(ref_candidates & stale_refs) or bool(memory.metadata.get("stale"))
        conflict_penalized = bool(ref_candidates & conflicted_refs) or bool(memory.conflict_refs)
        project_relevant = bool(runtime_workspace_refs & _memory_project_refs(memory))

        next_profile = _updated_forgetting_profile(
            memory.forgetting_profile,
            selected_in_context=selected_in_context,
            positive_feedback=positive_feedback,
            project_relevant=project_relevant,
            conflict_penalized=conflict_penalized,
            stale_penalized=stale_penalized,
            context_usefulness=usefulness,
        )
        changed = (
            selected_in_context
            or stale_penalized
            or conflict_penalized
            or next_profile.model_dump(mode="json") != memory.forgetting_profile.model_dump(mode="json")
        )
        if selected_in_context:
            selected_count += 1
        if stale_penalized or conflict_penalized:
            penalized_count += 1
        if changed:
            updated_ids.append(memory.memory_id)
            metadata = dict(memory.metadata)
            metadata["hcms_v2_forgetting_feedback"] = {
                "run_id": _str_or_none(run_payload.get("run_id")),
                "selected_in_context": selected_in_context,
                "success_reinforced": selected_in_context and positive_feedback,
                "project_relevance_reinforced": selected_in_context and project_relevant,
                "conflict_penalized": conflict_penalized,
                "stale_penalized": stale_penalized,
                "context_usefulness": usefulness,
            }
            memory = memory.model_copy(
                update={
                    "access_count": memory.access_count + (1 if selected_in_context else 0),
                    "last_accessed_at": now if selected_in_context else memory.last_accessed_at,
                    "forgetting_profile": next_profile,
                    "updated_at": now,
                    "metadata": metadata,
                }
            )
        updated.append(memory)

    return HCMSV2ForgettingFeedbackResult(
        memories=tuple(updated),
        updated_memory_ids=updated_ids,
        diagnostics={
            "source": "context_evaluation_feedback",
            "run_id": _str_or_none(run_payload.get("run_id")),
            "suite_id": _str_or_none(run_payload.get("suite_id")),
            "selected_memory_count": selected_count,
            "penalized_memory_count": penalized_count,
            "memory_count": len(memories),
            "updated_memory_count": len(updated_ids),
            "ablation_flags": _bool_mapping(run_payload.get("ablation_flags")),
            "workspace_refs": sorted(runtime_workspace_refs),
        },
    )


class CapabilityUsageMiningSubscriber:
    """RuntimeEventBus subscriber that feeds capability/tool usage into HCMS V2 mining."""

    def __init__(
        self,
        *,
        bridge: HCMSV2RuntimeBridge | None = None,
        namespace: str = "global/default",
    ) -> None:
        self.bridge = bridge or HCMSV2RuntimeBridge()
        self.namespace = namespace
        self.mined_batches: list[ProcedureWisdomMiningBatch] = []
        self._processed_usage_ids: set[str] = set()
        self.diagnostics: dict[str, int] = {
            "seen_event_count": 0,
            "mined_event_count": 0,
            "skipped_duplicate_count": 0,
            "skipped_non_capability_count": 0,
        }

    def __call__(self, event: object) -> ProcedureWisdomMiningBatch | None:
        self.diagnostics["seen_event_count"] += 1
        usage = capability_usage_event_from_runtime_event(event)
        if usage is None:
            self.diagnostics["skipped_non_capability_count"] += 1
            return None
        if usage.usage_id in self._processed_usage_ids:
            self.diagnostics["skipped_duplicate_count"] += 1
            return None

        batch = self.bridge.mine_capability_usage_events([usage], namespace=self.namespace)
        self._processed_usage_ids.add(usage.usage_id)
        self.mined_batches.append(batch)
        self.diagnostics["mined_event_count"] += 1
        _record_mined_capability_usage(event, usage=usage, batch=batch)
        return batch


def _record_mined_capability_usage(
    event: object,
    *,
    usage: CapabilityUsageEvent,
    batch: ProcedureWisdomMiningBatch,
) -> None:
    refs = getattr(event, "capability_usage_refs", None)
    if isinstance(refs, list) and usage.usage_id not in refs:
        refs.append(usage.usage_id)

    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return
    procedure_memory_ids = [memory.memory_id for memory in batch.procedural_memories]
    wisdom_memory_ids = [memory.memory_id for memory in batch.wisdom_memories]
    metadata["hcms_v2_procedure_wisdom_mined"] = {
        "usage_id": usage.usage_id,
        "namespace": batch.namespace,
        "procedure_memory_ids": procedure_memory_ids,
        "wisdom_memory_ids": wisdom_memory_ids,
        "result_count": len(batch.results),
    }
    mined_memory_ids = metadata.setdefault("hcms_v2_mined_memory_ids", [])
    if isinstance(mined_memory_ids, list):
        for memory_id in [*procedure_memory_ids, *wisdom_memory_ids]:
            if memory_id not in mined_memory_ids:
                mined_memory_ids.append(memory_id)


def _updated_forgetting_profile(
    profile: ForgettingProfile,
    *,
    selected_in_context: bool,
    positive_feedback: bool,
    project_relevant: bool,
    conflict_penalized: bool,
    stale_penalized: bool,
    context_usefulness: float,
) -> ForgettingProfile:
    access_boost = 0.04 if selected_in_context else 0.0
    success_boost = 0.06 + (context_usefulness * 0.04) if selected_in_context and positive_feedback else 0.0
    project_boost = 0.05 if selected_in_context and project_relevant else 0.0
    conflict_penalty = 0.08 if conflict_penalized else 0.0
    stale_penalty = 0.06 if stale_penalized else 0.0
    retrievability_delta = access_boost + success_boost + project_boost - conflict_penalty - stale_penalty
    return profile.model_copy(
        update={
            "access_reinforcement": bounded_score(profile.access_reinforcement + access_boost),
            "success_reinforcement": bounded_score(profile.success_reinforcement + success_boost),
            "project_relevance_boost": bounded_score(profile.project_relevance_boost + project_boost),
            "conflict_penalty": bounded_score(profile.conflict_penalty + conflict_penalty),
            "stale_penalty": bounded_score(profile.stale_penalty + stale_penalty),
            "retrievability": bounded_score(profile.retrievability + retrievability_delta),
            "archive_before_delete": True,
        }
    )


def _diagnostics_from_evaluation_run(evaluation_run: object) -> dict[str, Any]:
    payload = _object_payload(evaluation_run)
    diagnostics = dict(_mapping(payload.get("diagnostics")))
    for case in _sequence(payload.get("cases")):
        case_payload = _object_payload(case)
        diagnostics.update(_mapping(case_payload.get("diagnostics")))
        record_payload = _object_payload(case_payload.get("record"))
        diagnostics.update(_mapping(record_payload.get("diagnostics")))
    return diagnostics


def _object_payload(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            payload = dump(mode="python")
        except TypeError:
            payload = dump()
        if isinstance(payload, Mapping):
            return {str(key): item for key, item in payload.items()}
    return {}


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _sequence(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if str(item)}
    return set()


def _ordered_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return []


def _bool_mapping(value: object) -> dict[str, bool]:
    return {key: bool(item) for key, item in _mapping(value).items()}


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _float_score(value: object, *, default: float) -> float:
    try:
        return bounded_score(float(value))
    except (TypeError, ValueError):
        return default


def _is_positive_feedback(run_payload: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> bool:
    if str(diagnostics.get("user_satisfaction_proxy", "")).lower() in {"positive", "success", "satisfied"}:
        return True
    event_counts = _mapping(run_payload.get("runtime_event_counts"))
    return any(str(name).endswith("succeeded") or str(name) == "tool_succeeded" for name in event_counts)


def _slow_consolidation_target_layer(event_type: object) -> str:
    normalized = str(event_type or "").strip().lower()
    if normalized in {"workspace_state", "state_change", "workspace_update"}:
        return "working"
    return "semantic"


def _slow_consolidation_candidate_layers(event_type: object) -> list[str]:
    normalized = str(event_type or "").strip().lower()
    if normalized in {"capability_usage", "capability_result", "skill_usage", "skill_result", "mcp_usage", "mcp_result"}:
        return ["semantic", "procedural", "wisdom"]
    if normalized in {"workspace_state", "state_change", "workspace_update"}:
        return ["working", "semantic"]
    if normalized in {"tool_result", "tool_usage", "tool_call"}:
        return ["semantic", "episodic"]
    return ["semantic"]


def _slow_consolidation_category(target_layer: object, event_type: object) -> str:
    layer = str(target_layer or "").strip().lower()
    normalized = str(event_type or "").strip().lower()
    if layer == "working":
        return "workspace_state"
    if layer in {"procedural", "procedure"}:
        return "runtime_procedure"
    if layer == "wisdom":
        return "runtime_wisdom"
    if normalized in {"tool_result", "tool_usage", "tool_call"}:
        return "runtime_observation"
    return "runtime_observation"


def _slow_consolidation_title(target_layer: object, event_type: object) -> str:
    layer = str(target_layer or "semantic").strip().lower() or "semantic"
    normalized = str(event_type or "runtime_event").strip().lower() or "runtime_event"
    return f"Slow consolidated {layer} memory from {normalized}"


def _safe_replay_refs(value: object) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key, item in _mapping(value).items():
        safe_key = sanitize_memory_context_text(key)[:80]
        if isinstance(item, str) or item is None:
            refs[safe_key] = _str_or_none(sanitize_memory_context_text(item)) if item is not None else None
        elif isinstance(item, (list, tuple, set)):
            refs[safe_key] = [sanitize_memory_context_text(entry)[:240] for entry in item if str(entry or "").strip()]
        elif isinstance(item, Mapping):
            refs[safe_key] = {
                sanitize_memory_context_text(child_key)[:80]: sanitize_memory_context_text(child_value)[:240]
                for child_key, child_value in item.items()
            }
        else:
            refs[safe_key] = sanitize_memory_context_text(item)[:240]
    return refs


def _slow_consolidation_canonical_content(
    *,
    observation: ObservationRecord,
    task: HCMSV2ConsolidationTask,
    schedule: HCMSV2ConsolidationSchedule,
    summary: str,
    replay_refs: Mapping[str, Any],
    token_budget: TokenBudgetService,
) -> str:
    lines = [
        f"layer: {task.target_layer}",
        f"event_type: {observation.observation_type}",
        f"summary: {summary}",
        f"content_hash: {task.content_hash}",
        f"schedule_id: {schedule.schedule_id}",
        f"task_id: {task.task_id}",
    ]
    if task.source_memory_ids:
        lines.append(f"source_memory_ids: {', '.join(task.source_memory_ids)}")
    if task.runtime_event_ids:
        lines.append(f"runtime_event_ids: {', '.join(task.runtime_event_ids)}")
    for ref_key in (
        "payload_ref",
        "content_ref",
        "source_ref",
        "goal_stack_ref",
        "tool_result_refs",
        "workspace_refs",
        "capability_usage_refs",
    ):
        ref_value = replay_refs.get(ref_key)
        if ref_value:
            if isinstance(ref_value, list):
                lines.append(f"{ref_key}: {', '.join(str(item) for item in ref_value)}")
            else:
                lines.append(f"{ref_key}: {ref_value}")
    return token_budget.truncate_text(
        sanitize_memory_context_text("\n".join(lines)),
        max_tokens=220,
        max_chars=1400,
    )


def _memory_ref_candidates(memory: ConsolidatedMemory) -> set[str]:
    refs = {memory.memory_id, *memory.claims, *memory.conflict_refs}
    for key in ("memory_id", "result_id", "claim_id", "source_ref", "block_id"):
        value = memory.metadata.get(key)
        if value:
            refs.add(str(value))
    return {ref for ref in refs if ref}


def _memory_project_refs(memory: ConsolidatedMemory) -> set[str]:
    refs = _strings(memory.metadata.get("project_refs"))
    for key in ("workspace_ref", "workspace_id", "project_root"):
        value = memory.metadata.get(key)
        if value:
            refs.add(str(value))
    return refs


def _apply_guard_decision(observation: ObservationRecord, decision: MemoryGuardDecision) -> ObservationRecord:
    privacy_level = observation.privacy_level
    trust_level = observation.trust_level
    redaction_state = observation.redaction_state
    if decision.action == "quarantine":
        privacy_level = "quarantine"
        trust_level = "untrusted"
        redaction_state = "guarded"
    elif decision.action == "allow_no_inject":
        trust_level = "untrusted"
    elif decision.action == "redact":
        redaction_state = "redacted"

    return observation.model_copy(
        update={
            "content": decision.sanitized_content or observation.content,
            "privacy_level": privacy_level,
            "trust_level": trust_level,
            "redaction_state": redaction_state,
            "metadata": {
                **observation.metadata,
                "guard_decision_id": decision.decision_id,
                "guard_action": decision.action,
                "guard_reasons": list(decision.reasons),
                "guard_trust_score": decision.trust_score,
            },
        }
    )


__all__ = [
    "CapabilityUsageMiningSubscriber",
    "HCMSV2ForgettingFeedbackResult",
    "HCMSV2CaptureResult",
    "HCMSV2ConsolidationReplayResult",
    "HCMSV2RuntimeBridge",
    "HCMSV2ToolResultCaptureResult",
    "adaptive_forgetting_profiles_from_evaluation_run",
    "hcms_v2_consolidation_schedule_from_capture",
    "hcms_v2_replay_slow_consolidation",
]
