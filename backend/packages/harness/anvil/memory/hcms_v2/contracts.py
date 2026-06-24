from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def bounded_score(value: float | int | None, *, default: float = 0.5) -> float:
    try:
        numeric = float(default if value is None else value)
    except (TypeError, ValueError):
        numeric = default
    if not math.isfinite(numeric):
        numeric = default
    return round(min(max(numeric, 0.0), 1.0), 4)


def stable_hcms_id(prefix: str, *parts: object, size: int = 12) -> str:
    seed = "\0".join(str(part or "") for part in parts)
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()
    return f"{prefix}_{digest[:size]}"


def normalize_claim_text(*parts: object) -> str:
    text = " ".join(str(part or "") for part in parts)
    text = re.sub(r"\s+", " ", text.strip().lower())
    return text


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(default_factory=lambda: stable_hcms_id("ev_v2", utc_now().isoformat()))
    observation_id: str
    source_uri: str | None = None
    source_label: str = "runtime"
    start_offset: int | None = None
    end_offset: int | None = None
    quoted_text_hash: str | None = None
    excerpt: str = ""
    trust_score: float = 0.7
    timestamp: datetime = Field(default_factory=utc_now)
    collector: str = "runtime"

    @field_validator("trust_score")
    @classmethod
    def _bound_trust_score(cls, value: float) -> float:
        return bounded_score(value, default=0.7)

    @field_validator("excerpt")
    @classmethod
    def _bound_excerpt(cls, value: str) -> str:
        return str(value or "")[:600]


class ObservationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(default_factory=lambda: stable_hcms_id("obs_v2", utc_now().isoformat()))
    namespace: str
    thread_id: str | None = None
    run_id: str | None = None
    event_id: str | None = None
    observation_type: str
    source_kind: str
    source_id: str | None = None
    content: str
    content_ref: str | None = None
    source_spans: list[EvidenceSpan] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)
    task_id: str | None = None
    goal_id: str | None = None
    workspace_refs: list[str] = Field(default_factory=list)
    trust_level: str = "trusted"
    privacy_level: str = "project"
    redaction_state: str = "raw"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaimScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: str = "project"
    scope_key: str | None = None
    path_prefix: str | None = None
    valid_for_models: list[str] = Field(default_factory=list)


class ClaimValidity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = "provisional"
    reason: str | None = None
    reviewed_by: str | None = None


class TemporalValidity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime = Field(default_factory=utc_now)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    recency_weight: float = 1.0

    @field_validator("recency_weight")
    @classmethod
    def _bound_recency_weight(cls, value: float) -> float:
        return bounded_score(value, default=1.0)


class ClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str | None = None
    namespace: str
    claim_type: str = "fact"
    subject: str
    predicate: str
    object_value: str
    normalized_text: str = ""
    human_text: str = ""
    scope: ClaimScope = Field(default_factory=ClaimScope)
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    confidence: float = 0.5
    validity: ClaimValidity = Field(default_factory=ClaimValidity)
    contradiction_state: str = "none"
    source_priority: int = 50
    temporal_validity: TemporalValidity = Field(default_factory=TemporalValidity)
    freshness: float = 1.0
    salience: float = 0.5
    privacy_level: str = "project"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: list[str] = Field(default_factory=list)
    related_claims: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence", "freshness", "salience")
    @classmethod
    def _bound_scores(cls, value: float) -> float:
        return bounded_score(value)

    @model_validator(mode="after")
    def _derive_identity(self) -> "ClaimRecord":
        normalized = self.normalized_text or normalize_claim_text(self.subject, self.predicate, self.object_value)
        self.normalized_text = normalized
        if not self.human_text:
            self.human_text = f"{self.subject} {self.predicate} {self.object_value}".strip()
        if not self.claim_id:
            self.claim_id = stable_hcms_id(
                "claim_v2",
                self.namespace,
                self.scope.scope_type,
                self.scope.scope_key,
                normalized,
                size=16,
            )
        return self


class ForgettingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decay_model: str = "ebbinghaus"
    base_decay_rate: float = 0.03
    access_reinforcement: float = 0.0
    success_reinforcement: float = 0.0
    user_importance_boost: float = 0.0
    project_relevance_boost: float = 0.0
    conflict_penalty: float = 0.0
    stale_penalty: float = 0.0
    retrievability: float = 1.0
    archive_before_delete: bool = True
    next_review_at: datetime | None = None

    @field_validator(
        "base_decay_rate",
        "access_reinforcement",
        "success_reinforcement",
        "user_importance_boost",
        "project_relevance_boost",
        "conflict_penalty",
        "stale_penalty",
        "retrievability",
    )
    @classmethod
    def _bound_scores(cls, value: float) -> float:
        return bounded_score(value, default=0.0)


class ConsolidatedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    namespace: str
    layer: str
    category: str
    title: str
    summary: str
    claims: list[str] = Field(default_factory=list)
    canonical_content: str
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    confidence: float = 0.5
    salience: float = 0.5
    stability: float = 0.5
    access_count: int = 0
    last_accessed_at: datetime | None = None
    lifecycle_state: str = "active"
    forgetting_profile: ForgettingProfile = Field(default_factory=ForgettingProfile)
    conflict_refs: list[str] = Field(default_factory=list)
    version: int = 1
    parent_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence", "salience", "stability")
    @classmethod
    def _bound_scores(cls, value: float) -> float:
        return bounded_score(value)


class ConflictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str | None = None
    namespace: str
    claim_ids: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    conflict_type: str
    severity: str = "medium"
    status: str = "open"
    detected_at: datetime = Field(default_factory=utc_now)
    detection_method: str = "rule"
    explanation: str
    preferred_claim_id: str | None = None
    resolution_policy: str | None = None
    review_inbox_id: str | None = None
    injection_policy: str = "inject_warning"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _derive_identity(self) -> "ConflictRecord":
        if not self.conflict_id:
            self.conflict_id = stable_hcms_id(
                "conflict_v2",
                self.namespace,
                self.conflict_type,
                ",".join(sorted(self.claim_ids)),
                ",".join(sorted(self.memory_ids)),
                size=16,
            )
        if not self.review_inbox_id and self.status in {"open", "needs_review"}:
            self.review_inbox_id = stable_hcms_id("review_v2", self.conflict_id, size=12)
        return self


class ProcedureStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    description: str
    capability_refs: list[str] = Field(default_factory=list)
    expected_observation: str | None = None
    fallback: str | None = None


class ProcedurePattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    procedure_id: str
    namespace: str
    title: str
    trigger_conditions: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    ordered_steps: list[ProcedureStep] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    related_skills: list[str] = Field(default_factory=list)
    success_evidence: list[EvidenceSpan] = Field(default_factory=list)
    failure_recovery_notes: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    usage_count: int = 0
    success_rate: float = 0.0
    last_used_at: datetime | None = None
    promotion_state: str = "candidate"

    @field_validator("confidence", "success_rate")
    @classmethod
    def _bound_scores(cls, value: float) -> float:
        return bounded_score(value)


class WisdomInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str
    namespace: str
    insight_type: str
    statement: str
    applicability: list[str] = Field(default_factory=list)
    supporting_memories: list[str] = Field(default_factory=list)
    supporting_traces: list[str] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    review_state: str = "candidate"
    injection_policy: str = "planning_only"

    @field_validator("confidence")
    @classmethod
    def _bound_confidence(cls, value: float) -> float:
        return bounded_score(value)


class CapabilityUsageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage_id: str
    capability_id: str
    capability_kind: str
    tool_name: str | None = None
    skill_ids: list[str] = Field(default_factory=list)
    mcp_server_id: str | None = None
    turn_id: str
    goal_id: str | None = None
    input_summary: str = ""
    output_summary: str = ""
    status: str = "unknown"
    latency_ms: int | None = None
    error_type: str | None = None
    verification_signal: str | None = None
    context_block_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("usage_id", "capability_id", "capability_kind", "turn_id", "status")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("value must not be empty")
        return text[:200]

    @field_validator("tool_name", "mcp_server_id", "goal_id", "error_type", "verification_signal")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value or "").strip()
        return text[:200] if text else None

    @field_validator("input_summary", "output_summary")
    @classmethod
    def _bound_summaries(cls, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())[:1200]

    @field_validator("skill_ids", "context_block_refs")
    @classmethod
    def _bound_string_lists(cls, value: list[str]) -> list[str]:
        return [str(item).strip()[:240] for item in value[:12] if str(item or "").strip()]

    @field_validator("latency_ms")
    @classmethod
    def _bound_latency(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return max(int(value), 0)


class ProcedureWisdomMiningResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage_id: str
    procedure: ProcedurePattern | None = None
    wisdom: WisdomInsight | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class ProcedureWisdomMiningBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    event_count: int = 0
    results: list[ProcedureWisdomMiningResult] = Field(default_factory=list)
    procedural_memories: list[ConsolidatedMemory] = Field(default_factory=list)
    wisdom_memories: list[ConsolidatedMemory] = Field(default_factory=list)
    persisted_memory_ids: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class HCMSV2ConsolidationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    namespace: str
    mode: str
    status: str = "scheduled"
    target_layer: str
    capture_envelope_id: str
    observation_id: str
    source_memory_ids: list[str] = Field(default_factory=list)
    runtime_event_ids: list[str] = Field(default_factory=list)
    content_hash: str
    priority: float = 0.5
    reason: str = ""
    due_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
    replay_refs: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("mode", "status", "target_layer", "capture_envelope_id", "observation_id", "content_hash")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("value must not be empty")
        return text[:240]

    @field_validator("reason")
    @classmethod
    def _bound_reason(cls, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())[:500]

    @field_validator("source_memory_ids", "runtime_event_ids")
    @classmethod
    def _bound_refs(cls, value: list[str]) -> list[str]:
        return [str(item).strip()[:240] for item in value[:24] if str(item or "").strip()]

    @field_validator("priority")
    @classmethod
    def _bound_priority(cls, value: float) -> float:
        return bounded_score(value)

    @model_validator(mode="after")
    def _derive_identity(self) -> "HCMSV2ConsolidationTask":
        if not self.task_id:
            self.task_id = stable_hcms_id(
                "consolidation_task_v2",
                self.namespace,
                self.mode,
                self.target_layer,
                self.capture_envelope_id,
                self.observation_id,
                ",".join(self.source_memory_ids),
                size=16,
            )
        return self


class HCMSV2ConsolidationSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: str | None = None
    namespace: str
    capture_envelope_id: str
    observation_id: str
    fast_task: HCMSV2ConsolidationTask
    slow_task: HCMSV2ConsolidationTask
    created_at: datetime = Field(default_factory=utc_now)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("namespace", "capture_envelope_id", "observation_id")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("value must not be empty")
        return text[:240]

    @model_validator(mode="after")
    def _derive_identity(self) -> "HCMSV2ConsolidationSchedule":
        if not self.schedule_id:
            self.schedule_id = stable_hcms_id(
                "consolidation_schedule_v2",
                self.namespace,
                self.capture_envelope_id,
                self.observation_id,
                self.fast_task.task_id,
                self.slow_task.task_id,
                size=16,
            )
        return self


class HCMSV2ConsolidationReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replay_id: str | None = None
    namespace: str
    schedule_id: str
    task_id: str
    status: str = "completed"
    target_layer: str
    capture_envelope_id: str
    observation_id: str
    source_memory_ids: list[str] = Field(default_factory=list)
    runtime_event_ids: list[str] = Field(default_factory=list)
    claims: list[ClaimRecord] = Field(default_factory=list)
    consolidated_memories: list[ConsolidatedMemory] = Field(default_factory=list)
    replay_phase_coverage: dict[str, bool] = Field(default_factory=dict)
    replay_missing_phases: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("namespace", "schedule_id", "task_id", "status", "target_layer", "capture_envelope_id", "observation_id")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("value must not be empty")
        return text[:240]

    @field_validator("source_memory_ids", "runtime_event_ids", "replay_missing_phases")
    @classmethod
    def _bound_refs(cls, value: list[str]) -> list[str]:
        return [str(item).strip()[:240] for item in value[:24] if str(item or "").strip()]

    @model_validator(mode="after")
    def _derive_identity(self) -> "HCMSV2ConsolidationReplayResult":
        if not self.replay_id:
            self.replay_id = stable_hcms_id(
                "consolidation_replay_v2",
                self.namespace,
                self.schedule_id,
                self.task_id,
                self.observation_id,
                ",".join(self.source_memory_ids),
                ",".join(self.runtime_event_ids),
                size=16,
            )
        return self


class MemorySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    memory_id: str | None = None
    claim_id: str | None = None
    layer: str
    category: str
    content: str
    score: float = 0.5
    raw_scores: dict[str, float] = Field(default_factory=dict)
    salience_score: float = 0.5
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    confidence: float = 0.5
    conflict_state: str = "none"
    privacy_level: str = "project"
    freshness: float = 1.0
    token_cost: int = 0
    explanation: str = ""
    source_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("score", "salience_score", "confidence", "freshness")
    @classmethod
    def _bound_scores(cls, value: float) -> float:
        return bounded_score(value)


class MemoryInjectionViewV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    query: str = ""
    intent_profile_ref: str | None = None
    goal_stack_ref: str | None = None
    sensory_results: list[MemorySearchResult] = Field(default_factory=list)
    working_results: list[MemorySearchResult] = Field(default_factory=list)
    semantic_results: list[MemorySearchResult] = Field(default_factory=list)
    episodic_results: list[MemorySearchResult] = Field(default_factory=list)
    procedural_results: list[MemorySearchResult] = Field(default_factory=list)
    wisdom_results: list[MemorySearchResult] = Field(default_factory=list)
    conflict_warnings: list[ConflictRecord] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class RuntimeEventRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: str
    source_ref: str | None = None
    payload_summary: str = ""
    payload_ref: str | None = None
    actor: str = "runtime"
    privacy_level: str = "project"
    trust_level: str = "local_runtime"
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaptureEnvelopeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope_id: str
    namespace: str
    thread_id: str
    run_id: str | None = None
    turn_id: str
    trace_id: str | None = None
    user_message_refs: list[str] = Field(default_factory=list)
    runtime_events: list[RuntimeEventRef] = Field(default_factory=list)
    tool_result_refs: list[str] = Field(default_factory=list)
    workspace_state_ref: str | None = None
    goal_stack_ref: str | None = None
    capability_usage_refs: list[str] = Field(default_factory=list)
    explicit_corrections: list[str] = Field(default_factory=list)
    positive_reinforcement: list[str] = Field(default_factory=list)
    capture_reason: str = "runtime_event"
    salience_seed: float = 0.5
    privacy_level: str = "project"
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("salience_seed")
    @classmethod
    def _bound_salience_seed(cls, value: float) -> float:
        return bounded_score(value)


class MemoryGuardDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    source_ref: str
    action: str
    reasons: list[str] = Field(default_factory=list)
    detected_secrets: list[str] = Field(default_factory=list)
    trust_score: float = 0.5
    sanitized_content: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("trust_score")
    @classmethod
    def _bound_trust_score(cls, value: float) -> float:
        return bounded_score(value)


__all__ = [
    "CaptureEnvelopeV2",
    "CapabilityUsageEvent",
    "ClaimRecord",
    "ClaimScope",
    "ClaimValidity",
    "ConflictRecord",
    "ConsolidatedMemory",
    "EvidenceSpan",
    "ForgettingProfile",
    "HCMSV2ConsolidationReplayResult",
    "HCMSV2ConsolidationSchedule",
    "HCMSV2ConsolidationTask",
    "MemoryGuardDecision",
    "MemoryInjectionViewV2",
    "MemorySearchResult",
    "ObservationRecord",
    "ProcedurePattern",
    "ProcedureStep",
    "ProcedureWisdomMiningBatch",
    "ProcedureWisdomMiningResult",
    "RuntimeEventRef",
    "TemporalValidity",
    "WisdomInsight",
    "bounded_score",
    "normalize_claim_text",
    "stable_hcms_id",
    "utc_now",
]
