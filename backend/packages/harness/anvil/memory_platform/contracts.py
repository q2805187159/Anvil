from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .scrubber import MemorySecretScrubber


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


_MEMORY_FENCE_PATTERN = re.compile(r"</?memory(?:_[a-z0-9_-]+)?\s*>", re.IGNORECASE)


def sanitize_memory_context_text(value: Any) -> str:
    text = "" if value is None else str(value)
    scrubbed = MemorySecretScrubber().scrub(text).text
    return _MEMORY_FENCE_PATTERN.sub(lambda match: match.group(0).replace("<", "[").replace(">", "]"), scrubbed)


class CuratedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    memory_id: str | None = None
    store_id: str
    layer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    source_ref: str | None = None
    content: str
    category: str = "note"
    source_kind: str = "manual"
    priority: float = 0.5
    confidence: float = 0.5
    salience: float = 0.5
    last_accessed_at: datetime | None = None
    evidence_refs: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    expires_at: datetime | None = None
    status: str = "active"
    write_policy: str = "manual"
    write_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CuratedStoreState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: str
    display_name: str
    max_chars: int
    injection_chars: int
    max_tokens: int | None = None
    injection_tokens: int | None = None
    budget_source: str = "stored"
    category_bias: str
    summary: str = ""
    summary_sections: dict[str, dict[str, str]] = Field(default_factory=dict)
    entries: list[CuratedEntry] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class CuratedStoreView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: str
    display_name: str
    max_chars: int
    injection_chars: int
    max_tokens: int | None = None
    injection_tokens: int | None = None
    effective_max_tokens: int
    effective_injection_tokens: int
    budget_source: str = "stored"
    actual_injection_tokens: int = 0
    actual_injection_chars: int = 0
    usage_chars: int
    usage_tokens: int = 0
    entry_count: int
    summary: str
    summary_sections: dict[str, dict[str, str]] = Field(default_factory=dict)
    snapshot_status: str = "live"
    updated_at: datetime


class MemoryWriteEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    store_id: str
    content: str
    category: str
    thread_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)


class ArchiveTurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archive_id: str
    thread_id: str
    user_content: str
    assistant_content: str
    status: str
    created_at: datetime = Field(default_factory=utc_now)


class MemoryPollutionMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marker_id: str
    thread_id: str
    source_kind: str = "external"
    source_id: str | None = None
    tool_name: str | None = None
    reason: str = ""
    evidence_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ArchiveSearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archive_id: str
    thread_id: str
    score: float
    excerpt: str
    created_at: datetime


class ArchiveSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    hits: tuple[ArchiveSearchHit, ...] = ()
    provider_notes: tuple[str, ...] = ()


class RecallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    query: str
    snapshot_fingerprint: str
    stable_snapshot: str = ""
    summary: str = ""
    curated_matches: tuple[CuratedEntry, ...] = ()
    archive_hits: tuple[ArchiveSearchHit, ...] = ()
    provider_notes: tuple[str, ...] = ()
    evidence: tuple["RecallEvidence", ...] = ()
    actual_injection_tokens: int = 0
    actual_injection_chars: int = 0

    def render_turn_block(self) -> str:
        if not self.curated_matches and not self.archive_hits and not self.provider_notes and not self.summary:
            return ""

        sections: list[str] = [f"query={sanitize_memory_context_text(self.query)}"]
        if self.summary:
            sections.append("summary:")
            sections.append(sanitize_memory_context_text(self.summary))
        if self.curated_matches:
            sections.append("curated_matches:")
            sections.extend(
                f"- [{entry.store_id}] {sanitize_memory_context_text(entry.content)}"
                for entry in self.curated_matches
            )
        if self.archive_hits:
            sections.append("archive_matches:")
            sections.extend(
                f"- [{hit.thread_id}] {sanitize_memory_context_text(hit.excerpt)}"
                for hit in self.archive_hits
            )
        if self.evidence:
            sections.append("evidence:")
            sections.extend(
                f"- [{item.source_kind}:{item.source_id}] {sanitize_memory_context_text(item.reason)} (score={item.score:.3f})"
                for item in self.evidence
            )
        if self.provider_notes:
            sections.append("provider_notes:")
            sections.extend(f"- {sanitize_memory_context_text(note)}" for note in self.provider_notes)
        return "<memory_recall>\n" + "\n".join(sections) + "\n</memory_recall>"


class RecallEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_kind: str
    source_id: str
    layer_id: str | None = None
    memory_id: str | None = None
    archive_id: str | None = None
    thread_id: str | None = None
    score: float = 0.0
    match_score: float | None = None
    rerank_score: float | None = None
    recency_score: float | None = None
    final_score: float | None = None
    dropped_reason: str | None = None
    reason: str = ""
    excerpt: str = ""


class RecallPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    thread_id: str
    summary: str = ""
    stable_snapshot: str = ""
    evidence: tuple[RecallEvidence, ...] = ()
    curated_matches: tuple[CuratedEntry, ...] = ()
    archive_hits: tuple[ArchiveSearchHit, ...] = ()
    provider_notes: tuple[str, ...] = ()


class SessionSearchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    title: str | None = None
    summary: str
    evidence: tuple[RecallEvidence, ...] = ()
    archive_hits: tuple[ArchiveSearchHit, ...] = ()
    latest_prompt_snapshot_id: str | None = None


class MemoryTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    thread_id: str | None = None
    query: str | None = None
    trace_kind: str = "recall"
    target_id: str | None = None
    provider_notes: tuple[str, ...] = ()
    evidence: tuple[RecallEvidence, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class MemoryRecallBenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    query: str
    thread_id: str = "benchmark"
    expected_terms: tuple[str, ...] = ()
    expected_memory_ids: tuple[str, ...] = ()
    expected_archive_thread_ids: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    forbidden_memory_ids: tuple[str, ...] = ()
    min_score: float = 0.6


class MemoryRecallBenchmarkCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    query: str
    passed: bool
    score: float = 0.0
    recall_hits: int = 0
    expected_count: int = 0
    false_positive_count: int = 0
    evidence_count: int = 0
    top_evidence: tuple[RecallEvidence, ...] = ()
    missing_expectations: tuple[str, ...] = ()
    false_positives: tuple[str, ...] = ()
    summary: str = ""


class MemoryRecallBenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str = "ad_hoc"
    passed: bool = True
    score: float = 1.0
    case_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    recall_hit_rate: float = 1.0
    false_positive_rate: float = 0.0
    average_evidence_count: float = 0.0
    cases: tuple[MemoryRecallBenchmarkCaseResult, ...] = ()
    recommendations: tuple[str, ...] = ()
    generated_at: datetime = Field(default_factory=utc_now)


class MemoryRecallBenchmarkSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str
    name: str = ""
    description: str = ""
    cases: tuple[MemoryRecallBenchmarkCase, ...] = ()
    tags: tuple[str, ...] = ()
    enabled: bool = True
    source: str = "ops"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    latest_run_id: str | None = None
    latest_score: float | None = None
    latest_passed: bool | None = None
    latest_run_at: datetime | None = None


class MemoryRecallBenchmarkRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    suite_id: str
    suite_name: str = ""
    source: str = "ops"
    report: MemoryRecallBenchmarkReport
    created_at: datetime = Field(default_factory=utc_now)


class MemoryConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    memory_id: str
    conflicting_memory_id: str
    reason: str
    created_at: datetime = Field(default_factory=utc_now)
    resolved: bool = False
    recommended_action: str | None = None
    memory_content: str | None = None
    conflicting_content: str | None = None


class MemoryStalenessView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    layer_id: str
    stale_score: float
    reason: str
    last_accessed_at: datetime | None = None
    expires_at: datetime | None = None
    retention_score: float = 0.0
    tier: str = "cold"
    access_count: int = 0
    reinforcement_boost: float = 0.0
    temporal_decay: float = 0.0
    salience: float = 0.0


class MemoryRetentionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    store_id: str
    layer_id: str | None = None
    tier: str = "cold"
    retention_score: float = 0.0
    salience: float = 0.0
    temporal_decay: float = 0.0
    reinforcement_boost: float = 0.0
    access_count: int = 0
    last_accessed_at: datetime | None = None
    created_at: datetime
    status: str = "active"


class MemoryQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    severity: str = "info"
    kind: str
    store_id: str | None = None
    layer_id: str | None = None
    memory_id: str | None = None
    related_memory_ids: tuple[str, ...] = ()
    message: str
    recommendation: str | None = None
    score: float = 0.0


class MemoryStoreHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: str
    layer_id: str | None = None
    status: str = "healthy"
    entry_count: int = 0
    active_count: int = 0
    inactive_count: int = 0
    low_confidence_count: int = 0
    low_salience_count: int = 0
    missing_evidence_count: int = 0
    duplicate_cluster_count: int = 0
    conflict_count: int = 0
    stale_count: int = 0
    accessed_count: int = 0
    hot_count: int = 0
    warm_count: int = 0
    cold_count: int = 0
    retention_average: float = 0.0
    injection_token_pressure: float = 0.0
    quality_score: float = 1.0
    issues: tuple[MemoryQualityIssue, ...] = ()


class MemoryHealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "healthy"
    quality_score: float = 1.0
    archive_turn_count: int = 0
    pending_review_count: int = 0
    conflict_count: int = 0
    stale_count: int = 0
    provider_count: int = 0
    provider_health: dict[str, str] = Field(default_factory=dict)
    stores: tuple[MemoryStoreHealth, ...] = ()
    issues: tuple[MemoryQualityIssue, ...] = ()
    recommendations: tuple[str, ...] = ()
    generated_at: datetime = Field(default_factory=utc_now)


class MemoryReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    layer_id: str
    store_id: str
    action: str = "add"
    content: str
    category: str = "note"
    priority: float = 0.5
    confidence: float = 0.5
    salience: float = 0.5
    evidence_refs: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    rationale: str | None = None
    status: str = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MemoryOnboardingFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    kind: str = "project_entry"
    size_chars: int = 0
    included_chars: int = 0
    truncated: bool = False
    content_preview: str = ""


class MemoryOnboardingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool = True
    status: str = "skipped"
    reason: str | None = None
    workspace_path: str = ""
    thread_id: str | None = None
    store_id: str = "runtime_memory"
    layer_id: str = "workspace"
    category: str = "project_context"
    files: tuple[MemoryOnboardingFile, ...] = ()
    review_ids: tuple[str, ...] = ()
    written_memory_ids: tuple[str, ...] = ()
    stable_snapshot_refresh_recommended: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class MemoryGovernanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    memory_id: str
    store_id: str | None = None
    entry_id: str | None = None
    status: str = "ok"
    message: str = ""
    entry: CuratedEntry | None = None
    review_item: MemoryReviewItem | None = None
    before_retention: MemoryRetentionView | None = None
    after_retention: MemoryRetentionView | None = None


class ProfileFacet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facet_id: str
    source_memory_id: str
    entry_id: str
    store_id: str = "user_profile"
    class_id: str = "style"
    key: str = ""
    value: str
    source_category: str = "note"
    evidence_refs: tuple[str, ...] = ()
    confidence: float = 0.0
    salience: float = 0.0
    priority: float = 0.0
    stability_score: float = 0.0
    state: str = "candidate"
    user_state: str = "auto"
    prompt_visible: bool = False
    source_polluted: bool = False
    pollution_reasons: tuple[str, ...] = ()
    reason: str = ""
    last_seen_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProfileFacetPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_threshold: float = 1.5
    provisional_threshold: float = 0.7
    candidate_threshold: float = 0.4
    require_review_classes: tuple[str, ...] = ()
    class_budgets: dict[str, int] = Field(default_factory=dict)
    default_class_budget: int = 5
    max_facets: int = 80
    pollution_requires_review: bool = True


class ProfileFacetAuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    action: str
    facet_id: str
    source_memory_id: str | None = None
    before_state: str | None = None
    after_state: str | None = None
    before_user_state: str | None = None
    after_user_state: str | None = None
    reason: str | None = None
    source: str = "ops"
    created_at: datetime = Field(default_factory=utc_now)


class ProfileFacetGovernanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    facet: ProfileFacet
    status: str = "ok"
    message: str = ""
    audit_entry: ProfileFacetAuditEntry | None = None


class ProfileFacetRebuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "completed"
    source: str = "ops"
    facet_count: int = 0
    updated_count: int = 0
    facets: tuple[ProfileFacet, ...] = ()
    audit_entry: ProfileFacetAuditEntry | None = None


class MemoryGovernancePlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    store_id: str
    entry_id: str
    layer_id: str | None = None
    action: str
    reason: str
    tier: str = "cold"
    stale_score: float = 0.0
    retention_score: float = 0.0
    salience: float = 0.0
    access_count: int = 0
    last_accessed_at: datetime | None = None
    expires_at: datetime | None = None


class MemoryGovernanceBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: str = "balanced"
    layer_id: str | None = None
    dry_run: bool = True
    candidate_count: int = 0
    executed_count: int = 0
    skipped_count: int = 0
    items: tuple[MemoryGovernancePlanItem, ...] = ()
    results: tuple[MemoryGovernanceResult, ...] = ()
    errors: tuple[str, ...] = ()


class MemoryMaintenanceRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str = "completed"
    dry_run: bool = True
    policy: str = "balanced"
    layer_id: str | None = None
    source: str = "ops"
    update_queue_pending: int = 0
    update_queue_drained: int = 0
    reflection_jobs_due: int = 0
    reflection_jobs_run: int = 0
    reflection_entries_written: int = 0
    governance: MemoryGovernanceBatchResult = Field(default_factory=MemoryGovernanceBatchResult)
    health_before: MemoryHealthReport | None = None
    health_after: MemoryHealthReport | None = None
    actions_executed: dict[str, int] = Field(default_factory=dict)
    skipped_actions: dict[str, int] = Field(default_factory=dict)
    errors: tuple[str, ...] = ()
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)


class MemoryMaintenanceAutomationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ran: bool = False
    reason: str = "not_due"
    next_run_at: datetime | None = None
    report: MemoryMaintenanceRun | None = None


class MemoryFlushResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = None
    candidates_seen: int = 0
    entries_written: int = 0
    review_items_created: int = 0
    entries_skipped: int = 0
    facts_removed: int = 0
    errors: tuple[str, ...] = ()
    written_memory_ids: tuple[str, ...] = ()
    review_ids: tuple[str, ...] = ()
    candidate_audit: tuple["MemoryCandidateAuditEntry", ...] = ()


class MemoryCandidateAuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    action: str
    reason: str
    layer_id: str | None = None
    store_id: str | None = None
    category: str = "note"
    candidate_preview: str = ""
    quality_score: float = 0.0
    quality_decision: str = "unknown"
    blockers: tuple[str, ...] = ()
    confidence: float = 0.0
    salience: float = 0.0
    priority: float = 0.0
    evidence_count: int = 0
    evidence_refs: tuple[str, ...] = ()
    source_thread_id: str | None = None
    source_polluted: bool = False
    pollution_reasons: tuple[str, ...] = ()
    target_id: str | None = None
    supersedes: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class ReflectionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    job_id: str
    target_store_id: str
    layer_id: str
    content: str
    category: str
    priority: float = 0.5
    evidence_refs: tuple[str, ...] = ()
    write_reason: str | None = None
    source_query: str | None = None
    proposed_conflicts: tuple[str, ...] = ()


class MemoryPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: str
    error_code: str | None = None
    sanitized_content: str | None = None
    duplicate_of: str | None = None
    near_duplicates: tuple[str, ...] = ()
    matched_rules: tuple[str, ...] = ()


class ReflectionScheduleKind(str, Enum):
    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


class ReflectionJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    name: str
    schedule_kind: ReflectionScheduleKind
    target_store_id: str
    enabled: bool = True
    system_managed: bool = False
    template: str = "custom"
    instructions: str | None = None
    source_query: str | None = None
    interval_seconds: int | None = None
    cron: str | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None


class ReflectionRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: str
    entries_written: int = 0
    archive_hits: int = 0
    summary: str = ""
    written_entries: tuple[CuratedEntry, ...] = ()
    artifacts: tuple[ReflectionArtifact, ...] = ()


class MemoryProviderManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    display_name: str
    kind: str = "local_curated"
    origin: str = "builtin"
    family: str
    description: str
    active: bool = False
    configured: bool = True
    available: bool = True
    supports_prefetch: bool = True
    supports_sync: bool = True
    supports_index: bool = True
    supports_reflection: bool = True
    supports_explain: bool = True
    supports_archive_search: bool = True
    roles: tuple[str, ...] = ()
    health: str = "unknown"
    diagnostics: tuple[str, ...] = ()
    last_sync_at: datetime | None = None


class MemoryPlatformOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_provider_id: str | None = None
    runtime_mode: str = "memory_platform"
    legacy_capture_enabled: bool = False
    migration_status: dict[str, Any] = Field(default_factory=dict)
    store_count: int = 0
    archive_turn_count: int = 0
    reflection_job_count: int = 0
    stores: tuple[CuratedStoreView, ...] = ()


class MemoryProviderTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    ok: bool
    health: str = "unknown"
    diagnostics: tuple[str, ...] = ()


class MemoryAuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    kind: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class CuratedStoreRepository(Protocol):
    def load_store(self, store_id: str) -> CuratedStoreState: ...

    def save_store(self, state: CuratedStoreState) -> None: ...

    def list_store_ids(self) -> list[str]: ...


class ArchiveRepository(Protocol):
    def record_turn(self, thread_id: str, user_content: str, assistant_content: str, status: str) -> ArchiveTurnRecord: ...

    def search(self, query: str, limit: int = 5) -> ArchiveSearchResult: ...

    def list_thread_turns(self, thread_id: str, limit: int = 5) -> tuple[ArchiveTurnRecord, ...]: ...

    def count(self) -> int: ...


class MemoryProvider(Protocol):
    def manifest(self) -> MemoryProviderManifest: ...

    def system_prompt_block(self) -> str: ...

    def prefetch(self, query: str, *, thread_id: str, archive: ArchiveSearchResult, curated_matches: tuple[CuratedEntry, ...]) -> tuple[str, ...]: ...

    def queue_prefetch(self, query: str, *, thread_id: str) -> None: ...

    def sync_turn(self, record: ArchiveTurnRecord) -> None: ...

    def index_write(self, *, entry: CuratedEntry | None = None, record: ArchiveTurnRecord | None = None) -> tuple[str, ...]: ...

    def on_session_end(self, *, thread_id: str, messages: list[dict[str, Any]], reason: str = "session_end", allow_network: bool = True) -> tuple[str, ...]: ...

    def on_pre_compact(self, messages: list[dict[str, Any]]) -> str: ...

    def on_delegation(self, *, parent_thread_id: str, task: dict[str, Any], result: dict[str, Any], status: str) -> tuple[str, ...]: ...

    def test(self) -> MemoryProviderTestResult: ...

    def explain(self, *, query: str, evidence: tuple[RecallEvidence, ...]) -> tuple[str, ...]: ...

    def on_memory_write(self, event: MemoryWriteEvent) -> None: ...

    def shutdown(self, *, allow_network: bool = False) -> None: ...


RecallResult.model_rebuild()
ReflectionRunResult.model_rebuild()
