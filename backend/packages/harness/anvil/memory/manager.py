from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from anvil.config import HCMSRuntimeConfig
from anvil.memory.hcms_v2.adapters import capture_envelope_v2_from_legacy

from .compiler import HeuristicMemoryUpdater
from .contracts import (
    Memory,
    MemoryCategory,
    MemoryCaptureEnvelope,
    MemoryInjectionView,
    MemoryLifecycleState,
    MemoryState,
    MemoryVersionRecord,
    RetrievalConfig,
    RetrievalResult,
    sanitize_memory_context_text,
    stable_id,
    utc_now,
)
from .queue import DebouncedMemoryQueue
from .retrieval import FourStreamRetriever
from .service import MemoryService
from .store import FileMemoryStore
from .storage import HybridMemoryBackend, StorageError
from .updater import RuleBasedMemoryUpdater, StructuredMemoryUpdater, StructuredUpdateProvider


@dataclass(frozen=True)
class MemorySessionSnapshot:
    thread_id: str
    content: str
    fingerprint: str
    snapshot_id: str


@dataclass(frozen=True)
class CursorMemoryRuleExport:
    path: str
    relative_path: str
    fingerprint: str
    memory_count: int
    total_chars: int
    truncated: bool

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.relative_path,
            "fingerprint": self.fingerprint,
            "memory_count": self.memory_count,
            "total_chars": self.total_chars,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class MemoryTraceRecord:
    trace_id: str
    thread_id: str | None
    query: str | None
    trace_kind: str
    target_id: str | None = None
    engine_notes: tuple[str, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    created_at: Any = field(default_factory=utc_now)

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "thread_id": self.thread_id,
            "query": self.query,
            "trace_kind": self.trace_kind,
            "target_id": self.target_id,
            "engine_notes": list(self.engine_notes),
            "evidence": list(self.evidence),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class HCMSStoreView:
    store_id: str
    display_name: str
    max_chars: int
    injection_chars: int
    usage_chars: int
    entry_count: int
    summary: str
    updated_at: Any
    max_tokens: int | None = None
    injection_tokens: int | None = None
    effective_max_tokens: int | None = None
    effective_injection_tokens: int | None = None
    budget_source: str = "hcms"
    actual_injection_tokens: int = 0
    actual_injection_chars: int = 0
    usage_tokens: int = 0
    summary_sections: dict[str, dict[str, str]] = field(default_factory=dict)
    snapshot_status: str = "live"


@dataclass(frozen=True)
class HCMSQualityIssue:
    kind: str
    severity: str
    message: str
    target_id: str | None = None
    recommendation: str = ""

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "issue_id": stable_id("issue", self.kind, self.target_id, self.message, size=12),
            "severity": self.severity,
            "kind": self.kind,
            "store_id": "workspace",
            "layer_id": None,
            "memory_id": self.target_id,
            "related_memory_ids": (),
            "message": self.message,
            "recommendation": self.recommendation,
            "score": 0.0,
        }


@dataclass(frozen=True)
class HCMSStoreHealth:
    store_id: str
    entry_count: int
    low_confidence_count: int = 0
    missing_evidence_count: int = 0
    duplicate_cluster_count: int = 0
    active_count: int = 0
    inactive_count: int = 0
    low_salience_count: int = 0
    conflict_count: int = 0
    stale_count: int = 0
    accessed_count: int = 0
    hot_count: int = 0
    warm_count: int = 0
    cold_count: int = 0
    retention_average: float = 0.0
    injection_token_pressure: float = 0.0
    quality_score: float = 1.0
    status: str = "healthy"
    layer_id: str | None = None
    issues: tuple[HCMSQualityIssue, ...] = ()

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "layer_id": self.layer_id,
            "status": self.status,
            "entry_count": self.entry_count,
            "active_count": self.active_count,
            "inactive_count": self.inactive_count,
            "low_confidence_count": self.low_confidence_count,
            "low_salience_count": self.low_salience_count,
            "missing_evidence_count": self.missing_evidence_count,
            "duplicate_cluster_count": self.duplicate_cluster_count,
            "conflict_count": self.conflict_count,
            "stale_count": self.stale_count,
            "accessed_count": self.accessed_count,
            "hot_count": self.hot_count,
            "warm_count": self.warm_count,
            "cold_count": self.cold_count,
            "retention_average": self.retention_average,
            "injection_token_pressure": self.injection_token_pressure,
            "quality_score": self.quality_score,
            "issues": [issue.model_dump(mode=mode) for issue in self.issues],
        }


@dataclass(frozen=True)
class HCMSHealthReport:
    status: str
    quality_score: float
    archive_turn_count: int
    observation_queue_count: int
    conflict_count: int
    stale_count: int
    engine_count: int
    engine_health: dict[str, str]
    stores: tuple[HCMSStoreHealth, ...]
    issues: tuple[HCMSQualityIssue, ...]
    recommendations: tuple[str, ...]
    generated_at: Any
    diagnostics: tuple[str, ...] = ()

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "status": self.status,
            "quality_score": self.quality_score,
            "archive_turn_count": self.archive_turn_count,
            "observation_queue_count": self.observation_queue_count,
            "conflict_count": self.conflict_count,
            "stale_count": self.stale_count,
            "engine_count": self.engine_count,
            "engine_health": dict(self.engine_health),
            "stores": [store.model_dump(mode=mode) for store in self.stores],
            "issues": [issue.model_dump(mode=mode) for issue in self.issues],
            "recommendations": list(self.recommendations),
            "diagnostics": list(self.diagnostics),
            "generated_at": self.generated_at,
        }

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, default=str)


@dataclass(frozen=True)
class HCMSEngineManifest:
    engine_id: str = "hcms"
    display_name: str = "HCMS"
    kind: str = "hcms"
    origin: str = "builtin"
    family: str = "local"
    description: str = "Hyper-Converged Memory System"
    active: bool = True
    configured: bool = True
    available: bool = True
    supports_prefetch: bool = True
    supports_sync: bool = True
    supports_index: bool = True
    supports_reflection: bool = True
    supports_explain: bool = True
    supports_archive_search: bool = True
    roles: list[str] = field(default_factory=lambda: ["recall", "capture", "causal"])
    health: str = "healthy"
    diagnostics: list[str] = field(default_factory=list)
    last_sync_at: Any | None = None


class ReflectionScheduleKind(str, Enum):
    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


@dataclass(frozen=True)
class ReflectionJob:
    job_id: str
    name: str
    schedule_kind: ReflectionScheduleKind
    target_store_id: str
    template: str
    instructions: str | None = None
    source_query: str | None = None
    interval_seconds: int | None = None
    cron: str | None = None
    enabled: bool = True
    system_managed: bool = False
    last_run_at: Any | None = None
    next_run_at: Any | None = None
    last_status: str | None = None


@dataclass(frozen=True)
class ReflectionRunResult:
    job_id: str
    status: str
    entries_written: int = 0
    archive_hits: int = 0
    summary: str = ""
    written_entries: tuple[Memory, ...] = ()


@dataclass(frozen=True)
class MemoryRecallBenchmarkCase:
    case_id: str
    query: str
    thread_id: str = "benchmark"
    expected_terms: tuple[str, ...] = ()
    expected_memory_ids: tuple[str, ...] = ()
    expected_archive_thread_ids: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    forbidden_memory_ids: tuple[str, ...] = ()
    min_score: float = 0.0

    @classmethod
    def model_validate(cls, payload: Any) -> "MemoryRecallBenchmarkCase":
        data = payload if isinstance(payload, dict) else payload.model_dump(mode="json")
        return cls(
            case_id=str(data.get("case_id") or data.get("id") or stable_id("case", data.get("query"), size=10)),
            query=str(data.get("query") or ""),
            thread_id=str(data.get("thread_id") or "benchmark"),
            expected_terms=tuple(str(item) for item in data.get("expected_terms", ()) or ()),
            expected_memory_ids=tuple(str(item) for item in data.get("expected_memory_ids", ()) or ()),
            expected_archive_thread_ids=tuple(str(item) for item in data.get("expected_archive_thread_ids", ()) or ()),
            forbidden_terms=tuple(str(item) for item in data.get("forbidden_terms", ()) or ()),
            forbidden_memory_ids=tuple(str(item) for item in data.get("forbidden_memory_ids", ()) or ()),
            min_score=float(data.get("min_score") or 0.0),
        )

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "query": self.query,
            "thread_id": self.thread_id,
            "expected_terms": list(self.expected_terms),
            "expected_memory_ids": list(self.expected_memory_ids),
            "expected_archive_thread_ids": list(self.expected_archive_thread_ids),
            "forbidden_terms": list(self.forbidden_terms),
            "forbidden_memory_ids": list(self.forbidden_memory_ids),
            "min_score": self.min_score,
        }


@dataclass(frozen=True)
class MemoryRecallBenchmarkSuite:
    suite_id: str
    name: str = ""
    description: str = ""
    cases: tuple[MemoryRecallBenchmarkCase, ...] = ()
    tags: tuple[str, ...] = ()
    enabled: bool = True
    latest_run_id: str | None = None
    latest_score: float | None = None
    latest_passed: bool | None = None
    latest_run_at: Any | None = None
    source: str = "hcms"
    created_at: Any | None = None
    updated_at: Any | None = None

    @classmethod
    def model_validate(cls, payload: Any) -> "MemoryRecallBenchmarkSuite":
        data = payload if isinstance(payload, dict) else payload.model_dump(mode="json")
        return cls(
            suite_id=str(data.get("suite_id") or stable_id("suite", data.get("name"), size=10)),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            cases=tuple(MemoryRecallBenchmarkCase.model_validate(item) for item in data.get("cases", ()) or ()),
            tags=tuple(str(item) for item in data.get("tags", ()) or ()),
            enabled=bool(data.get("enabled", True)),
            source=str(data.get("source") or "hcms"),
            created_at=utc_now(),
            updated_at=utc_now(),
        )

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "name": self.name,
            "description": self.description,
            "cases": [case.model_dump(mode=mode) for case in self.cases],
            "tags": list(self.tags),
            "enabled": self.enabled,
            "latest_run_id": self.latest_run_id,
            "latest_score": self.latest_score,
            "latest_passed": self.latest_passed,
            "latest_run_at": self.latest_run_at,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MemoryRecallBenchmarkCaseResult:
    case_id: str
    query: str
    passed: bool
    score: float
    returned_memory_ids: tuple[str, ...] = ()
    expected_memory_ids: tuple[str, ...] = ()
    forbidden_memory_ids: tuple[str, ...] = ()
    recall_hits: int = 0
    expected_count: int = 0
    false_positive_count: int = 0
    evidence_count: int = 0
    top_evidence: tuple[Any, ...] = ()
    missing_expectations: tuple[str, ...] = ()
    false_positives: tuple[str, ...] = ()
    summary: str = ""

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "query": self.query,
            "passed": self.passed,
            "score": self.score,
            "returned_memory_ids": list(self.returned_memory_ids),
            "expected_memory_ids": list(self.expected_memory_ids),
            "forbidden_memory_ids": list(self.forbidden_memory_ids),
            "recall_hits": self.recall_hits,
            "expected_count": self.expected_count,
            "false_positive_count": self.false_positive_count,
            "evidence_count": self.evidence_count,
            "top_evidence": list(self.top_evidence),
            "missing_expectations": list(self.missing_expectations),
            "false_positives": list(self.false_positives),
            "summary": self.summary,
        }


@dataclass(frozen=True)
class MemoryRecallBenchmarkReport:
    suite_id: str
    score: float
    passed: bool
    cases: tuple[MemoryRecallBenchmarkCaseResult, ...]
    evidence_limit: int
    run_id: str = ""
    created_at: Any | None = None
    generated_at: Any | None = None

    @property
    def case_count(self) -> int:
        return len(self.cases)

    @property
    def passed_count(self) -> int:
        return sum(1 for item in self.cases if item.passed)

    @property
    def failed_count(self) -> int:
        return self.case_count - self.passed_count

    @property
    def recall_hit_rate(self) -> float:
        return self.score

    @property
    def false_positive_rate(self) -> float:
        total = sum(item.false_positive_count for item in self.cases)
        evidence = sum(max(item.evidence_count, 1) for item in self.cases) or 1
        return round(total / evidence, 4)

    @property
    def average_evidence_count(self) -> float:
        return round(sum(item.evidence_count for item in self.cases) / len(self.cases), 4) if self.cases else 0.0

    @property
    def recommendations(self) -> tuple[str, ...]:
        return () if self.passed else ("Improve HCMS recall fixtures or memory capture before claiming Recall@10 quality.",)

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "score": self.score,
            "passed": self.passed,
            "cases": [case.model_dump(mode=mode) for case in self.cases],
            "evidence_limit": self.evidence_limit,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "generated_at": self.generated_at,
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "recall_hit_rate": self.recall_hit_rate,
            "false_positive_rate": self.false_positive_rate,
            "average_evidence_count": self.average_evidence_count,
            "recommendations": list(self.recommendations),
        }

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, default=str)


@dataclass(frozen=True)
class MemoryRecallBenchmarkRun:
    run_id: str
    suite_id: str
    report: MemoryRecallBenchmarkReport
    source: str = "hcms"
    created_at: Any | None = None

    @property
    def score(self) -> float:
        return self.report.score

    @property
    def passed(self) -> bool:
        return self.report.passed

    @property
    def suite_name(self) -> str:
        return self.suite_id

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "suite_id": self.suite_id,
            "suite_name": self.suite_name,
            "score": self.score,
            "passed": self.passed,
            "report": self.report.model_dump(mode=mode),
            "source": self.source,
            "created_at": self.created_at,
        }

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, default=str)


@dataclass(frozen=True)
class HCMSRecallResult:
    thread_id: str
    query: str
    snapshot_fingerprint: str
    stable_snapshot: str
    injection: MemoryInjectionView
    results: tuple[RetrievalResult, ...]
    actual_injection_tokens: int = 0
    actual_injection_chars: int = 0

    @property
    def summary(self) -> str:
        return self.injection.summary

    @property
    def memory_matches(self) -> tuple[Memory, ...]:
        return tuple(result.memory for result in self.results if result.memory is not None)

    @property
    def archive_hits(self) -> tuple[object, ...]:
        return ()

    @property
    def engine_notes(self) -> tuple[str, ...]:
        return ("HCMS four-stream recall active",) if self.results else ()

    @property
    def evidence(self) -> tuple[RetrievalResult, ...]:
        return self.results

    def render_turn_block(self) -> str:
        return self.injection.render_fenced()


class MemoryManager:
    """HCMS-only manager for agent runtime, middleware, and tools."""

    def __init__(self, *, service: MemoryService, state_root: str | Path, config: HCMSRuntimeConfig | None = None) -> None:
        self.hcms_service = service
        self.config = config or HCMSRuntimeConfig()
        self.state_root = Path(state_root).expanduser().resolve()
        self._prompt_snapshots: dict[str, dict[str, Any]] = {}
        self._traces: list[MemoryTraceRecord] = []
        self._session_turns: list[dict[str, Any]] = []
        self._pending_conflict_alerts: list[object] = []
        self._reflection_jobs: dict[str, ReflectionJob] = {
            "hcms-default-reflection": ReflectionJob(
                job_id="hcms-default-reflection",
                name="HCMS continuity reflection",
                schedule_kind=ReflectionScheduleKind.INTERVAL,
                target_store_id="hcms_workspace",
                template="Summarize durable project continuity and unresolved causal links.",
                interval_seconds=6 * 60 * 60,
                system_managed=True,
                next_run_at=None,
                last_status="idle",
            )
        }
        self._benchmark_suites: dict[str, MemoryRecallBenchmarkSuite] = {}
        self._benchmark_runs: list[MemoryRecallBenchmarkRun] = []

    def shutdown(self) -> None:
        """Flush pending HCMS work and release backend resources when present."""
        self.hcms_service.process_pending("global/default", force=True)
        for target in (self.hcms_service.queue, self.hcms_service.store):
            close = getattr(target, "close", None) or getattr(target, "shutdown", None)
            if callable(close):
                close()

    @classmethod
    def from_config(
        cls,
        *,
        config: HCMSRuntimeConfig,
        base_path: str | Path,
        effective_config: object | None = None,
        structured_update_provider: StructuredUpdateProvider | None = None,
    ) -> "MemoryManager":
        root = Path(base_path).expanduser().resolve()
        max_facts = int(getattr(config, "max_facts", 12) or 12)
        recall_config = getattr(config, "recall", None)
        max_candidates = int(getattr(recall_config, "max_candidates", max_facts) or max_facts)
        max_facts = max(1, max_candidates)
        injection_budget = int(getattr(recall_config, "turn_recall_token_budget", 900) or 900)
        max_evidence = int(getattr(recall_config, "max_evidence", 6) or 6)
        min_relevance_score = float(getattr(recall_config, "min_relevance_score", 0.0) or 0.0)
        retrieval_config = RetrievalConfig(
            default_limit=max_facts,
            max_limit=max(max_facts, int(getattr(recall_config, "max_candidates", max_facts) or max_facts)),
            bm25_weight=float(getattr(recall_config, "bm25_weight", 0.3) or 0.0),
            vector_weight=float(getattr(recall_config, "vector_weight", 0.4) or 0.0),
            graph_weight=float(getattr(recall_config, "graph_weight", 0.2) or 0.0),
            temporal_weight=float(getattr(recall_config, "temporal_weight", 0.1) or 0.0),
            rrf_k=int(getattr(recall_config, "rrf_k", 60) or 60),
            enable_adaptive_weights=bool(getattr(recall_config, "enable_adaptive_weights", True)),
            enable_cache=bool(getattr(recall_config, "enable_cache", True)),
            cache_ttl=int(getattr(recall_config, "cache_ttl", 300) or 0),
            cache_max_entries=int(getattr(recall_config, "cache_max_entries", 100) or 0),
            enable_mmr=bool(getattr(recall_config, "enable_mmr", True)),
            mmr_lambda=float(getattr(recall_config, "mmr_lambda", 0.72) or 0.72),
        )
        update_config = getattr(config, "update_queue", None)
        debounce_seconds = float(getattr(update_config, "debounce_seconds", 0.0) or 0.0)
        min_window_seconds = float(getattr(update_config, "min_window_seconds", 5.0) or 5.0)
        default_window_seconds = float(getattr(update_config, "default_window_seconds", 30.0) or 30.0)
        max_window_seconds = float(getattr(update_config, "max_window_seconds", 60.0) or 60.0)
        if (
            debounce_seconds != 1.5
            and min_window_seconds == 5.0
            and default_window_seconds == 30.0
            and max_window_seconds == 60.0
        ):
            min_window_seconds = debounce_seconds
            default_window_seconds = debounce_seconds
            max_window_seconds = debounce_seconds
        queue = DebouncedMemoryQueue(
            enabled=bool(getattr(update_config, "enabled", True)),
            debounce_seconds=debounce_seconds,
            min_window_seconds=min_window_seconds,
            default_window_seconds=default_window_seconds,
            max_window_seconds=max_window_seconds,
            min_batch_turns=int(getattr(update_config, "min_batch_turns", 4) or 4),
            max_batch_turns=int(getattr(update_config, "max_batch_turns", 8) or 8),
        )
        store = _store_from_config(config, root=root)
        hcms_updater = _updater_from_config(
            config,
            max_facts=max_facts,
            structured_update_provider=structured_update_provider,
        )
        service = MemoryService(
            store=store,
            queue=queue,
            updater=hcms_updater,
            max_facts=max_facts,
            injection_token_budget=injection_budget,
            max_evidence=max_evidence,
            min_relevance_score=min_relevance_score,
            retriever=FourStreamRetriever(retrieval_config),
        )
        return cls(service=service, state_root=root, config=config)

    def record_turn(
        self,
        *,
        thread_id: str,
        user_content: str,
        assistant_content: str,
        status: str = "completed",
        source_metadata: dict[str, Any] | None = None,
        capture: bool = True,
    ) -> None:
        now = utc_now()
        self._session_turns.append(
            {
                "archive_id": stable_id("turn", thread_id, user_content, assistant_content, now.isoformat(), size=16),
                "thread_id": thread_id,
                "user_content": user_content or "",
                "assistant_content": assistant_content or "",
                "status": status or "completed",
                "created_at": now,
            }
        )
        self._session_turns = self._session_turns[-1000:]
        if not capture:
            return
        messages = [
            HumanMessage(content=user_content or ""),
            AIMessage(content=assistant_content or status or ""),
        ]
        normalized_status = (status or "completed").strip().lower()
        blocked_statuses = {
            "awaiting_approval",
            "awaiting_clarification",
            "blocked",
            "interrupted",
            "paused",
            "requires_action",
            "waiting",
        }
        failed_statuses = {"error", "failed"}
        envelope = self.hcms_service.build_capture_envelope(
            thread_id=thread_id,
            namespace="global/default",
            messages=messages,
            trace_id=thread_id,
            blocked=normalized_status in blocked_statuses,
            failed=normalized_status in failed_statuses,
        )
        envelope = envelope.model_copy(update={"metadata": {**envelope.metadata, **dict(source_metadata or {})}})
        if self.hcms_service.has_capture_signal(envelope):
            self.hcms_service.enqueue_capture(envelope)
            should_force = (
                normalized_status in blocked_statuses
                or normalized_status in failed_statuses
                or self.hcms_service.should_process_capture_immediately(envelope)
            )
            processed = self.hcms_service.process_pending(
                "global/default",
                force=should_force,
            )
            capture_v2 = capture_envelope_v2_from_legacy(envelope)
            self._record_trace(
                thread_id=thread_id,
                query=user_content,
                trace_kind="hcms_capture" if processed else "hcms_capture_pending",
                engine_notes=("HCMS turn capture processed",) if processed else ("HCMS turn capture queued for debounce",),
                evidence=tuple(
                    {
                        "capture_envelope_id": capture_v2.envelope_id,
                        "event_id": runtime_event.event_id,
                        "event_type": runtime_event.event_type,
                        "source_id": runtime_event.source_ref or runtime_event.event_id,
                        "thread_id": capture_v2.thread_id,
                        "score": capture_v2.salience_seed,
                        "guard_action": "allow",
                    }
                    for runtime_event in capture_v2.runtime_events[: max(1, self.hcms_service.max_evidence)]
                ),
            )

    def capture_runtime_event(self, event: object, *, namespace: str = "global/default"):
        capture = self.hcms_service.capture_runtime_event_v2(event, namespace=namespace)
        self._record_runtime_event_capture_trace(capture)
        return capture

    def capture_runtime_events(self, events: Iterable[object], *, namespace: str = "global/default"):
        captures = tuple(self.hcms_service.capture_runtime_events_v2(events, namespace=namespace))
        for capture in captures:
            self._record_runtime_event_capture_trace(capture)
        return captures

    def _record_runtime_event_capture_trace(self, capture: object) -> None:
        persisted_memory_id = capture.envelope.metadata.get("persisted_memory_id")
        self._record_trace(
            thread_id=capture.observation.thread_id or capture.envelope.thread_id,
            query=capture.observation.content[:240],
            trace_kind="hcms_v2_runtime_event_capture",
            target_id=str(persisted_memory_id) if persisted_memory_id else None,
            engine_notes=("HCMS V2 runtime event captured as episodic memory",),
            evidence=(
                {
                    "capture_envelope_id": capture.envelope.envelope_id,
                    "observation_id": capture.observation.observation_id,
                    "event_id": capture.observation.event_id,
                    "event_type": capture.observation.observation_type,
                    "tool_result_refs": list(capture.envelope.tool_result_refs),
                    "workspace_refs": list(capture.observation.workspace_refs),
                    "guard_action": capture.guard_decision.action,
                    "hcms_v2_slow_consolidation_status": capture.envelope.metadata.get(
                        "hcms_v2_slow_consolidation_status"
                    ),
                    "hcms_v2_slow_consolidated_memory_ids": list(
                        capture.envelope.metadata.get("hcms_v2_slow_consolidated_memory_ids") or []
                    ),
                    "hcms_v2_slow_consolidation_claim_ids": list(
                        capture.envelope.metadata.get("hcms_v2_slow_consolidation_claim_ids") or []
                    ),
                },
            ),
        )

    def mine_capability_usage_events(self, events: Iterable[object], *, namespace: str = "global/default"):
        event_list = list(events or [])
        batch = self.hcms_service.mine_capability_usage_events_v2(event_list, namespace=namespace)
        thread_id = None
        for event in event_list:
            thread_id = str(getattr(event, "thread_id", "") or "") or None
            if thread_id:
                break
        persisted_ids = list(getattr(batch, "persisted_memory_ids", []) or [])
        self._record_trace(
            thread_id=thread_id,
            query="capability usage mining",
            trace_kind="hcms_v2_capability_usage_mining",
            target_id=",".join(persisted_ids) if persisted_ids else None,
            engine_notes=("HCMS V2 procedure/wisdom mining processed runtime capability events",),
            evidence=(
                {
                    "namespace": getattr(batch, "namespace", namespace),
                    "candidate_event_count": len(event_list),
                    "event_count": getattr(batch, "event_count", 0),
                    "result_count": len(list(getattr(batch, "results", []) or [])),
                    "procedural_memory_count": len(list(getattr(batch, "procedural_memories", []) or [])),
                    "wisdom_memory_count": len(list(getattr(batch, "wisdom_memories", []) or [])),
                    "persisted_memory_ids": persisted_ids,
                    "diagnostics": dict(getattr(batch, "diagnostics", {}) or {}),
                },
            ),
        )
        return batch

    def sync_workspace_state(self, workspace_state: object, *, namespace: str = "global/default") -> Memory | None:
        memory = self.hcms_service.sync_workspace_state_v2(workspace_state, namespace=namespace)
        if memory is None:
            return None
        self._record_trace(
            thread_id=memory.source_thread_id,
            query=memory.summary,
            trace_kind="hcms_v2_workspace_state_sync",
            target_id=memory.memory_id,
            engine_notes=("HCMS V2 workspace state synced as working memory",),
            evidence=(
                {
                    "memory_id": memory.memory_id,
                    "workspace_state_ref": memory.metadata.get("workspace_state_ref"),
                    "layer_id": memory.metadata.get("layer_id"),
                    "active_file_count": memory.metadata.get("active_file_count"),
                    "variable_count": memory.metadata.get("variable_count"),
                    "intermediate_result_count": memory.metadata.get("intermediate_result_count"),
                },
            ),
        )
        return memory

    def queue_conflict_alerts(self, conflicts: Iterable[object]) -> tuple[object, ...]:
        by_id = {
            str(getattr(conflict, "conflict_id", "") or ""): conflict
            for conflict in self._pending_conflict_alerts
            if str(getattr(conflict, "conflict_id", "") or "")
        }
        for conflict in conflicts or ():
            conflict_id = str(getattr(conflict, "conflict_id", "") or "")
            if not conflict_id:
                continue
            by_id[conflict_id] = conflict
        self._pending_conflict_alerts = list(by_id.values())[-128:]
        return tuple(self._pending_conflict_alerts)

    def sync_conflict_alerts(
        self,
        review_inbox: object,
        *,
        conflicts: Iterable[object] | None = None,
        namespace: str = "global/default",
    ) -> tuple[object, ...]:
        from .hcms_v2.adapters import conflict_record_to_alert

        add_alert = getattr(review_inbox, "add_alert", None)
        if not callable(add_alert):
            return ()

        pending_source = conflicts if conflicts is not None else tuple(self._pending_conflict_alerts)
        candidates = [
            conflict
            for conflict in pending_source or ()
            if _should_sync_conflict_alert(conflict, namespace=namespace)
        ]
        synced: list[object] = []
        failed_count = 0
        for conflict in candidates:
            try:
                synced.append(add_alert(conflict_record_to_alert(conflict)))
            except Exception:
                failed_count += 1

        if conflicts is None and candidates and failed_count == 0:
            synced_ids = {str(getattr(conflict, "conflict_id", "") or "") for conflict in candidates}
            self._pending_conflict_alerts = [
                conflict
                for conflict in self._pending_conflict_alerts
                if str(getattr(conflict, "conflict_id", "") or "") not in synced_ids
            ]

        diagnostics = {
            "status": "synced" if synced else ("failed" if failed_count else "skipped_no_conflicts"),
            "namespace": namespace,
            "candidate_count": len(candidates),
            "synced_count": len(synced),
            "failed_count": failed_count,
            "pending_count": len(self._pending_conflict_alerts),
            "conflict_ids": [str(getattr(item, "conflict_id", "") or "") for item in synced[-8:]],
            "review_inbox_ids": [str(getattr(item, "review_inbox_id", "") or "") for item in synced[-8:]],
        }
        inbox_diagnostics = getattr(review_inbox, "diagnostics", None)
        if isinstance(inbox_diagnostics, dict):
            inbox_diagnostics["hcms_conflict_alert_sync"] = diagnostics

        self._record_trace(
            thread_id=getattr(review_inbox, "thread_id", None),
            query=f"conflict_alert_sync namespace={namespace}",
            trace_kind="hcms_v2_conflict_alert_sync",
            target_id=getattr(review_inbox, "inbox_id", None),
            engine_notes=("HCMS V2 conflict alerts synced to runtime review inbox",),
            evidence=tuple(
                {
                    "conflict_id": getattr(item, "conflict_id", None),
                    "review_inbox_id": getattr(item, "review_inbox_id", None),
                    "alert_id": getattr(item, "alert_id", None),
                    "severity": getattr(item, "severity", None),
                    "injection_policy": getattr(item, "injection_policy", None),
                }
                for item in synced[-8:]
            ),
        )
        return tuple(synced)

    def prefetch_recall(self, *, thread_id: str, query: str) -> HCMSRecallResult:
        namespace = "global/default"
        snapshot = self.get_or_create_session_snapshot(thread_id=thread_id)
        results = tuple(self.hcms_service.search(namespace, query, limit=self.hcms_service.max_facts))
        injection = self.hcms_service.build_injection_view(namespace, query=query)
        rendered = injection.render_fenced()
        recall = HCMSRecallResult(
            thread_id=thread_id,
            query=query,
            snapshot_fingerprint=snapshot.fingerprint,
            stable_snapshot=snapshot.content,
            injection=injection,
            results=results,
            actual_injection_tokens=max(len(rendered) // 4, 1) if rendered else 0,
            actual_injection_chars=len(rendered),
        )
        self._record_trace(
            thread_id=thread_id,
            query=query,
            trace_kind="hcms_recall",
            evidence=tuple(_evidence_payload(result, thread_id=thread_id) for result in results),
        )
        return recall

    def render_stable_snapshot(self) -> str:
        return _render_memory_injection_snapshot(self.hcms_service.build_injection_view("global/default"))

    def stable_snapshot_fingerprint(self) -> str:
        return _fingerprint(self.render_stable_snapshot())

    def render_cursor_memory_rule(self, *, max_entries: int = 12, max_chars: int = 4000) -> str:
        state = self.hcms_service.prefetch("global/default")
        active = sorted(
            state.active_memories(),
            key=lambda memory: (memory.salience, memory.confidence, memory.updated_at),
            reverse=True,
        )[: max(1, int(max_entries))]
        lines = [
            "# HCMS Memory",
            "",
            "This file is generated from Anvil HCMS durable memory for Cursor-compatible project context loading.",
            "Treat these records as evidence hints. Current user instructions and scoped AGENTS.md files take precedence.",
            "",
            "## Summary",
            "",
            sanitize_memory_context_text(state.summary.summary or self.render_stable_snapshot()),
            "",
            "## Active Memories",
        ]
        for memory in active:
            evidence = "; ".join(item.content for item in memory.evidence[:2])
            lines.extend(
                [
                    "",
                    f"- id: {memory.memory_id}",
                    f"  layer: {memory.layer_id}",
                    f"  category: {memory.category.value}",
                    f"  version: {memory.version}",
                    f"  state: {memory.state.value}",
                    f"  confidence: {memory.confidence:.3f}",
                    f"  salience: {memory.salience:.3f}",
                    f"  source_thread_id: {memory.source_thread_id or 'unknown'}",
                    f"  source_type: {memory.source_type.value}",
                    f"  updated_at: {memory.updated_at.isoformat()}",
                    f"  summary: {sanitize_memory_context_text(memory.summary or memory.content[:180])}",
                ]
            )
            if memory.parent_id:
                lines.append(f"  parent_id: {memory.parent_id}")
            if memory.supersedes:
                lines.append(f"  supersedes: {', '.join(memory.supersedes)}")
            if memory.evidence:
                lines.append(f"  evidence_ids: {', '.join(item.evidence_id for item in memory.evidence[:6])}")
            if evidence:
                lines.append(f"  evidence: {sanitize_memory_context_text(evidence)}")
            if memory.reasoning:
                lines.append(f"  reasoning: {sanitize_memory_context_text(memory.reasoning)}")

        rendered = "\n".join(lines).strip() + "\n"
        if len(rendered) > max_chars:
            return rendered[: max(0, int(max_chars))].rstrip() + "\n"
        return rendered

    def export_cursor_memory_rule(
        self,
        *,
        workspace_root: str | Path,
        relative_path: str = ".cursor/rules/hcms-memory.md",
        max_entries: int = 12,
        max_chars: int = 4000,
    ) -> CursorMemoryRuleExport:
        root = Path(workspace_root).expanduser().resolve()
        normalized_relative = Path(str(relative_path).replace("\\", "/"))
        if normalized_relative.is_absolute() or ".." in normalized_relative.parts:
            raise ValueError("relative_path must stay within the workspace root")
        target = (root / normalized_relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("relative_path must stay within the workspace root") from exc

        content = self.render_cursor_memory_rule(max_entries=max_entries, max_chars=max_chars)
        full_content = self.render_cursor_memory_rule(max_entries=max_entries, max_chars=max_chars * 4)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(target)
        return CursorMemoryRuleExport(
            path=str(target),
            relative_path=normalized_relative.as_posix(),
            fingerprint=_fingerprint(content),
            memory_count=len(self.hcms_service.prefetch("global/default").active_memories()),
            total_chars=len(content),
            truncated=len(content) < len(full_content),
        )

    def get_or_create_session_snapshot(self, *, thread_id: str, refresh: bool = False, reason: str = "first_run") -> MemorySessionSnapshot:
        content = self.render_stable_snapshot()
        fingerprint = _fingerprint(content)
        snapshot_id = stable_id("snapshot", thread_id, fingerprint, size=16)
        return MemorySessionSnapshot(
            thread_id=thread_id,
            content=content,
            fingerprint=fingerprint,
            snapshot_id=snapshot_id,
        )

    def record_prompt_snapshot(
        self,
        *,
        thread_id: str,
        snapshot_id: str,
        prompt_hash: str,
        prompt_text: str,
        skills_fingerprint: str | None,
        memory_fingerprint: str | None,
        config_fingerprint: str,
    ) -> dict[str, object]:
        payload = {
            "thread_id": thread_id,
            "snapshot_id": snapshot_id,
            "prompt_hash": prompt_hash,
            "prompt_text": prompt_text,
            "skills_fingerprint": skills_fingerprint,
            "memory_fingerprint": memory_fingerprint,
            "config_fingerprint": config_fingerprint,
            "created_at": utc_now().isoformat(),
        }
        self._prompt_snapshots[snapshot_id] = payload
        return payload

    def hcms_search(self, *, query: str, limit: int = 10) -> dict[str, Any]:
        started = utc_now()
        results = self.hcms_service.search("global/default", query, limit=limit)
        latency_ms = max((utc_now() - started).total_seconds() * 1000, 0.0)
        return {
            "items": [_result_payload(result) for result in results],
            "metrics": {
                "last_latency_ms": round(latency_ms, 3),
                "recall_count": len(results),
                "recall_hit_rate": 1.0 if results else 0.0,
            },
            "engine_notes": ["HCMS four-stream recall active"],
        }

    def list_hcms_memories(
        self,
        *,
        query: str | None = None,
        state: str | None = None,
        category: str | None = None,
        layer_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        normalized_query = str(query or "").strip().lower()
        normalized_state = str(state or "all").strip().lower().replace("-", "_")
        normalized_category = str(category or "").strip().lower().replace("-", "_")
        normalized_layer = str(layer_id or "all").strip().lower().replace("-", "_")
        if normalized_layer in {"", "all", "*", "hcms"}:
            layer_filter = None
        else:
            try:
                layer_filter = _canonical_layer(normalized_layer)
            except KeyError as exc:
                raise ValueError(f"Unknown HCMS memory layer: {layer_id}") from exc

        limit = max(1, min(int(limit or 50), 100))
        offset = max(0, int(offset or 0))
        memories = sorted(
            self.hcms_service.prefetch("global/default").memories,
            key=lambda memory: (memory.updated_at, memory.created_at, memory.memory_id),
            reverse=True,
        )

        def matches(memory: Memory) -> bool:
            if normalized_state not in {"", "all", "*"} and memory.state.value != normalized_state:
                return False
            if normalized_category and memory.category.value != normalized_category:
                return False
            if layer_filter is not None and _layer_for_memory(memory) != layer_filter:
                return False
            if normalized_query:
                haystack = " ".join(
                    [
                        memory.memory_id,
                        memory.summary,
                        memory.content,
                        memory.category.value,
                        memory.state.value,
                        " ".join(memory.tags),
                        " ".join(memory.entities),
                        " ".join(memory.concepts),
                    ]
                ).lower()
                terms = [term for term in normalized_query.split() if term]
                if terms and not all(term in haystack for term in terms):
                    return False
            return True

        filtered = [memory for memory in memories if matches(memory)]
        page = filtered[offset : offset + limit]
        return {
            "items": [memory.model_dump(mode="json") for memory in page],
            "total": len(filtered),
            "limit": limit,
            "offset": offset,
            "query": normalized_query or None,
            "state": normalized_state if normalized_state not in {"", "*"} else "all",
            "category": normalized_category or None,
            "layer_id": layer_filter or "all",
            "engine_notes": ["HCMS memory list"],
        }

    def hcms_why(self, *, query: str, limit: int = 3) -> dict[str, Any]:
        paths = self.hcms_service.why("global/default", query, limit=limit)
        return {
            "paths": [path.model_dump(mode="json") for path in paths],
            "engine_notes": ["HCMS causal reasoning active"],
        }

    def hcms_memory(self, *, memory_id: str) -> dict[str, Any]:
        memory = self._require_memory(memory_id)
        return {
            "memory": memory.model_dump(mode="json"),
            "engine_notes": ["HCMS memory detail"],
        }

    def delete_hcms_memory(self, *, memory_id: str) -> dict[str, Any]:
        self.hcms_service.delete_memory("global/default", memory_id)
        return {
            "memory_id": memory_id,
            "status": "deleted",
            "deleted": True,
            "engine_notes": ["HCMS memory deleted"],
        }

    def hcms_relations(self, *, memory_id: str) -> dict[str, Any]:
        memory = self._require_memory(memory_id)
        state = self.hcms_service.prefetch("global/default")
        memories = {item.memory_id: item for item in state.memories}
        relations = [
            relation
            for relation in state.relations
            if memory_id in {relation.source_memory_id, relation.target_memory_id}
        ]
        relation_payloads = []
        for relation in relations:
            payload = relation.model_dump(mode="json")
            source_memory = memories.get(relation.source_memory_id)
            target_memory = memories.get(relation.target_memory_id)
            payload["source_memory"] = source_memory.model_dump(mode="json") if source_memory is not None else None
            payload["target_memory"] = target_memory.model_dump(mode="json") if target_memory is not None else None
            relation_payloads.append(payload)
        return {
            "memory_id": memory.memory_id,
            "relations": relation_payloads,
            "engine_notes": ["HCMS relation graph"],
        }

    def hcms_counterfactual(self, *, query: str, avoid: str = "", limit: int = 5) -> dict[str, Any]:
        result = self.hcms_service.counterfactual("global/default", query, avoid=avoid, limit=limit)
        return result.model_dump(mode="json")

    def hcms_history(self, *, memory_id: str) -> dict[str, Any]:
        self._require_memory(memory_id)
        versions = self.hcms_service.history("global/default", memory_id)
        if not versions:
            try:
                memory = next(item for item in self.hcms_service.prefetch("global/default").memories if item.memory_id == memory_id)
            except StopIteration:
                memory = None
            if memory is not None:
                versions = (
                    MemoryVersionRecord(
                        memory_id=memory.memory_id,
                        version=memory.version,
                        parent_id=memory.parent_id,
                            content=memory.content,
                            summary=memory.summary,
                            reason="current",
                            metadata=memory.version_metadata(),
                        ),
                    )
        return {"memory_id": memory_id, "versions": [version.model_dump(mode="json") for version in versions]}

    def hcms_diff(self, *, memory_id: str) -> dict[str, Any]:
        self._require_memory(memory_id)
        return {
            **self.hcms_service.diff_details("global/default", memory_id),
            "engine_notes": ["HCMS Git-like version diff"],
        }

    def create_layer_entry(
        self,
        layer_id: str,
        *,
        content: str,
        category: str = "note",
        source_kind: str = "manual",
        priority: float = 0.5,
        metadata: dict | None = None,
        thread_id: str | None = None,
        source_ref: str | None = None,
        confidence: float = 0.5,
        salience: float = 0.5,
        evidence_refs: tuple[str, ...] = (),
    ) -> Memory:
        canonical_layer = _canonical_layer(layer_id)
        if canonical_layer == "session":
            raise ValueError("session layer is read-only; use action='inspect' or the session_search tool.")
        store_id = _store_id_for_layer(canonical_layer)
        memory = self.hcms_service.create_memory(
            "global/default",
            content=content,
            category=_category_for_layer(canonical_layer, category),
            confidence=confidence,
            salience=salience if salience is not None else priority,
            source_thread_id=thread_id,
            evidence_text="; ".join(evidence_refs) if evidence_refs else source_ref,
            metadata={
                **dict(metadata or {}),
                "layer_id": canonical_layer,
                "store_id": store_id,
                "source_kind": source_kind,
                "priority": priority,
                "category_label": category,
            },
        )
        self._record_trace(
            thread_id=thread_id,
            query=content,
            trace_kind="hcms_write",
            target_id=memory.memory_id,
            engine_notes=("HCMS memory created",),
            evidence=(_memory_evidence_payload(memory, thread_id=thread_id, score=1.0, reason="memory write"),),
        )
        return memory

    def update_layer_entry(
        self,
        layer_id: str,
        entry_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        priority: float | None = None,
        confidence: float | None = None,
        salience: float | None = None,
        evidence_refs: tuple[str, ...] = (),
        status: str | None = None,
    ) -> Memory:
        canonical_layer = _canonical_layer(layer_id)
        if canonical_layer == "session":
            raise ValueError("session layer is read-only; use action='inspect' or the session_search tool.")
        if status is not None:
            normalized_status = str(status or "").strip().lower()
            if normalized_status == "archived":
                return self.hcms_service.archive_memory("global/default", entry_id)
            if normalized_status in {"forgotten", "deleted"}:
                return self.hcms_service.forget_memory("global/default", entry_id)
            if normalized_status == "active":
                return self.hcms_service.restore_memory("global/default", entry_id)
        return self.hcms_service.update_memory(
            "global/default",
            entry_id,
            content=content,
            category=_category_for_layer(canonical_layer, category or "note") if category else None,
            confidence=confidence,
            salience=salience if salience is not None else priority,
            evidence_refs=evidence_refs,
        )

    def delete_layer_entry(self, layer_id: str, entry_id: str) -> None:
        canonical_layer = _canonical_layer(layer_id)
        if canonical_layer == "session":
            raise ValueError("session layer is read-only; use action='inspect' or the session_search tool.")
        self.hcms_service.delete_memory("global/default", entry_id)

    def create_entry(self, store_id: str, **kwargs) -> Memory:
        return self.create_layer_entry(_layer_for_store_or_layer(store_id), **kwargs)

    def update_entry(self, store_id: str, entry_id: str, **kwargs) -> Memory:
        return self.update_layer_entry(_layer_for_store_or_layer(store_id), entry_id, **kwargs)

    def delete_entry(self, store_id: str, entry_id: str) -> None:
        self.delete_layer_entry(_layer_for_store_or_layer(store_id), entry_id)

    def list_layer_entries(self, layer_id: str) -> tuple[Memory, ...]:
        normalized = _canonical_layer(layer_id)
        if normalized == "session":
            raise ValueError("session layer does not expose durable entries")
        state = self.hcms_service.prefetch("global/default")
        if normalized in {"all", "*"}:
            return tuple(state.active_memories())
        return tuple(
            memory
            for memory in state.active_memories()
            if str(memory.metadata.get("layer_id") or _layer_for_memory(memory)) == normalized
        )

    def list_entries(self, store_id: str) -> tuple[Memory, ...]:
        return self.list_layer_entries(_layer_for_store_or_layer(store_id))

    def list_stores(self) -> tuple[HCMSStoreView, ...]:
        state = self.hcms_service.prefetch("global/default")
        return (
            self._store_view(state=state, layer_id="user", store_id="hcms_user", display_name="HCMS User Layer"),
            self._store_view(state=state, layer_id="workspace", store_id="hcms_workspace", display_name="HCMS Workspace Layer"),
        )

    def overview(self):
        stores = self.list_stores()
        return _SimpleObject(
            enabled=True,
            runtime_mode="hcms",
            stores=stores,
            engines=self.list_engines(),
            active_engine_id="hcms",
            archive_turn_count=len(self._session_turns),
            trace_count=len(self._traces),
            reflection_job_count=len(self._reflection_jobs),
            store_count=len(stores),
            migration_status={
                "storage_contract": "hcms_only",
                "store_budget_sources": {store.store_id: store.budget_source for store in stores},
            },
        )

    def _store_view(self, *, state: MemoryState, layer_id: str, store_id: str, display_name: str) -> HCMSStoreView:
        active = [
            memory
            for memory in state.active_memories()
            if str(memory.metadata.get("layer_id") or _layer_for_memory(memory)) == layer_id
        ]
        usage = sum(len(memory.content) for memory in active)
        summary = " | ".join(memory.summary or memory.content[:80] for memory in active[:5])
        return HCMSStoreView(
            store_id=store_id,
            display_name=display_name,
            max_chars=120_000,
            injection_chars=min(usage, self.hcms_service.injection_token_budget * 4),
            max_tokens=30_000,
            injection_tokens=self.hcms_service.injection_token_budget,
            effective_max_tokens=30_000,
            effective_injection_tokens=self.hcms_service.injection_token_budget,
            usage_chars=usage,
            usage_tokens=max(usage // 4, 1) if usage else 0,
            entry_count=len(active),
            summary=summary or state.summary.summary,
            updated_at=state.updated_at,
        )

    def list_engines(self) -> tuple[HCMSEngineManifest, ...]:
        diagnostics = self._state_diagnostics()
        health = "degraded" if diagnostics else "healthy"
        return (HCMSEngineManifest(health=health, diagnostics=list(diagnostics)),)

    def activate_engine(self, engine_id: str) -> HCMSEngineManifest:
        if engine_id != "hcms":
            raise KeyError(engine_id)
        return HCMSEngineManifest()

    def test_engine(self, engine_id: str):
        if engine_id != "hcms":
            raise KeyError(engine_id)
        diagnostics = self._state_diagnostics()
        health = "degraded" if diagnostics else "healthy"
        return _SimpleObject(engine_id="hcms", ok=not diagnostics, health=health, diagnostics=list(diagnostics))

    def reload_engines(self, **kwargs) -> tuple[HCMSEngineManifest, ...]:
        return self.list_engines()

    def health_report(self) -> HCMSHealthReport:
        state = self.hcms_service.prefetch("global/default")
        active = state.active_memories()
        low_confidence = [memory for memory in active if memory.confidence < 0.5]
        missing_evidence = [memory for memory in active if not memory.evidence]
        diagnostics = _format_diagnostics(state)
        issues: list[HCMSQualityIssue] = []
        for memory in low_confidence[:10]:
            issues.append(
                HCMSQualityIssue(
                    kind="low_confidence",
                    severity="watch",
                    message="HCMS memory has low confidence.",
                    target_id=memory.memory_id,
                    recommendation="Reinforce or archive the memory.",
                )
            )
        for memory in missing_evidence[:10]:
            issues.append(
                HCMSQualityIssue(
                    kind="missing_evidence",
                    severity="watch",
                    message="HCMS memory has no evidence.",
                    target_id=memory.memory_id,
                    recommendation="Attach evidence or demote the memory.",
                )
            )
        for diagnostic in diagnostics[:10]:
            issues.append(
                HCMSQualityIssue(
                    kind="hcms_diagnostic",
                    severity="degraded",
                    message=diagnostic,
                    recommendation="Inspect HCMS diagnostics and recent memory capture/retrieval failures.",
                )
            )
        score = max(0.0, 1.0 - min(len(issues) * 0.05, 0.5))
        status = "degraded" if diagnostics else ("healthy" if not issues else "watch")
        return HCMSHealthReport(
            status=status,
            quality_score=round(score, 4),
            archive_turn_count=0,
            observation_queue_count=self.hcms_service.queue.pending_count(),
            conflict_count=0,
            stale_count=len(self.list_staleness()),
            engine_count=1,
            engine_health={"hcms": "degraded" if diagnostics else "healthy"},
            stores=(
                self._store_health(state=state, layer_id="user", store_id="hcms_user"),
                self._store_health(state=state, layer_id="workspace", store_id="hcms_workspace"),
            ),
            issues=tuple(issues),
            recommendations=tuple(dict.fromkeys(issue.recommendation for issue in issues if issue.recommendation)),
            diagnostics=tuple(diagnostics),
            generated_at=utc_now(),
        )

    def _state_diagnostics(self) -> tuple[str, ...]:
        try:
            return tuple(_format_diagnostics(self.hcms_service.prefetch("global/default")))
        except Exception as exc:
            return (f"health:state_load_failed:{exc.__class__.__name__}:x1",)

    def _store_health(self, *, state: MemoryState, layer_id: str, store_id: str) -> HCMSStoreHealth:
        active = [
            memory
            for memory in state.active_memories()
            if str(memory.metadata.get("layer_id") or _layer_for_memory(memory)) == layer_id
        ]
        inactive = [
            memory
            for memory in state.memories
            if str(memory.metadata.get("layer_id") or _layer_for_memory(memory)) == layer_id
            and memory.state != MemoryLifecycleState.ACTIVE
        ]
        low_confidence = [memory for memory in active if memory.confidence < 0.5]
        low_salience = [memory for memory in active if memory.salience < 0.25]
        missing_evidence = [memory for memory in active if not memory.evidence]
        layer_issues = [
            issue
            for issue in self.health_report_issues_for_layer(active)
        ]
        quality = max(0.0, 1.0 - min((len(low_confidence) + len(low_salience) + len(missing_evidence)) * 0.05, 0.5))
        status = "healthy" if quality >= 0.95 else "watch"
        return HCMSStoreHealth(
            store_id=store_id,
            entry_count=len(active),
            active_count=len(active),
            inactive_count=len(inactive),
            low_confidence_count=len(low_confidence),
            low_salience_count=len(low_salience),
            missing_evidence_count=len(missing_evidence),
            duplicate_cluster_count=0,
            layer_id=layer_id,
            accessed_count=sum(1 for memory in active if memory.access_count > 0),
            retention_average=round(sum(memory.compute_retention_score() for memory in active) / len(active), 4) if active else 0.0,
            quality_score=round(quality, 4),
            status=status,
            issues=tuple(layer_issues),
        )

    def health_report_issues_for_layer(self, active: list[Memory]) -> tuple[HCMSQualityIssue, ...]:
        issues: list[HCMSQualityIssue] = []
        for memory in active[:10]:
            if memory.confidence < 0.5:
                issues.append(
                    HCMSQualityIssue(
                        kind="low_confidence",
                        severity="watch",
                        message="HCMS memory has low confidence.",
                        target_id=memory.memory_id,
                        recommendation="Reinforce or archive the memory.",
                    )
                )
            if not memory.evidence:
                issues.append(
                    HCMSQualityIssue(
                        kind="missing_evidence",
                        severity="watch",
                        message="HCMS memory has no evidence.",
                        target_id=memory.memory_id,
                        recommendation="Attach evidence or demote the memory.",
                    )
                )
        return tuple(issues)

    def list_conflicts(self) -> tuple[object, ...]:
        return ()

    def resolve_conflict(self, conflict_id: str, *, action: str = "keep_both"):
        raise KeyError(conflict_id)

    def list_staleness(self) -> tuple[object, ...]:
        state = self.hcms_service.prefetch("global/default")
        stale = []
        for memory in state.active_memories():
            retention = memory.compute_retention_score()
            if retention >= 0.15:
                continue
            stale.append(
                _SimpleObject(
                    memory_id=memory.memory_id,
                    layer_id=memory.layer_id,
                    stale_score=round(1.0 - retention, 4),
                    reason="low HCMS retention score",
                    last_accessed_at=memory.accessed_at,
                    expires_at=memory.forget_after,
                    retention_score=retention,
                    tier="cold",
                    access_count=memory.access_count,
                    reinforcement_boost=0.0,
                    temporal_decay=0.0,
                    salience=memory.salience,
                )
            )
        return tuple(stale)

    def list_retention(self) -> tuple[object, ...]:
        state = self.hcms_service.prefetch("global/default")
        return tuple(
            _SimpleObject(
                memory_id=memory.memory_id,
                layer_id=memory.layer_id,
                tier="active",
                retention_score=memory.compute_retention_score(),
                salience=memory.salience,
                temporal_decay=0.0,
                reinforcement_boost=0.0,
                access_count=memory.access_count,
                last_accessed_at=memory.accessed_at,
                created_at=memory.created_at,
                status=memory.state.value,
            )
            for memory in state.active_memories()
        )

    def govern_memory(self, memory_id: str, *, action: str, reason: str | None = None, source: str = "ops"):
        normalized = str(action or "").strip().lower()
        before = self._retention_view(self._require_memory(memory_id))
        if normalized == "archive":
            memory = self.hcms_service.archive_memory("global/default", memory_id)
        elif normalized in {"forget", "remove"}:
            memory = self.hcms_service.forget_memory("global/default", memory_id)
        elif normalized in {"restore", "refresh"}:
            memory = self.hcms_service.restore_memory("global/default", memory_id)
        elif normalized == "reinforce":
            state = self.hcms_service.store.load("global/default")
            memory = next((item for item in state.memories if item.memory_id == memory_id), None)
            if memory is None:
                raise KeyError(memory_id)
            memory.access_count += 1
            memory.accessed_at = utc_now()
            memory.salience = min(1.0, memory.salience + 0.05)
            memory.updated_at = utc_now()
            self.hcms_service.store.save("global/default", state)
        elif normalized == "review":
            memory = self._require_memory(memory_id)
        else:
            raise ValueError(f"unsupported HCMS governance action '{action}'")
        after = self._retention_view(memory)
        quality_issue = None
        if normalized == "review":
            quality_issue = HCMSQualityIssue(
                kind="quality_review",
                severity="watch",
                message="HCMS memory was marked for quality inspection.",
                target_id=memory.memory_id,
                recommendation=reason or "Inspect confidence, evidence, and retention before changing lifecycle state.",
            )
        return _SimpleObject(
            memory_id=memory.memory_id,
            entry_id=memory.entry_id,
            store_id=memory.store_id,
            action=normalized,
            status=memory.state.value,
            message=reason or "",
            entry=memory,
            quality_issue=quality_issue,
            before_retention=before,
            after_retention=after,
            source=source,
        )

    def plan_memory_governance(self, **kwargs):
        return self._memory_governance_batch(dry_run=True, **kwargs)

    def execute_memory_governance(self, **kwargs):
        return self._memory_governance_batch(dry_run=False, **kwargs)

    def _memory_governance_batch(self, *, dry_run: bool, **kwargs):
        policy = str(kwargs.get("policy") or "balanced").strip().lower().replace("-", "_")
        layer_id = kwargs.get("layer_id")
        normalized_layer = _canonical_layer(layer_id or "all")
        limit = max(1, min(int(kwargs.get("limit") or 20), 100))
        entries = self.list_layer_entries(normalized_layer)
        candidates = sorted(entries, key=lambda item: item.compute_retention_score())[:limit]
        planned = tuple(self._governance_plan_item(memory, policy=policy) for memory in candidates)
        items, skipped_actions = self._apply_governance_action_caps(planned, **kwargs)
        results = []
        errors = []
        if not dry_run:
            for item in items:
                try:
                    results.append(
                        self.govern_memory(
                            item.memory_id,
                            action=item.action,
                            reason=item.reason,
                            source=str(kwargs.get("source") or "ops"),
                        )
                    )
                except Exception as exc:
                    errors.append(f"{item.memory_id}: {exc}")
        return _SimpleObject(
            policy=policy,
            layer_id=None if normalized_layer == "all" else normalized_layer,
            dry_run=dry_run,
            candidate_count=len(items),
            executed_count=len(results),
            skipped_count=len(items) - len(results) if dry_run else 0,
            items=items,
            results=tuple(results),
            skipped_actions=skipped_actions,
            errors=tuple(errors),
        )

    def _apply_governance_action_caps(self, items: tuple[object, ...], **kwargs):
        caps = {
            "archive": max(0, int(kwargs.get("max_archive_per_run", 100) or 0)),
            "review": max(0, int(kwargs.get("max_quality_inspections_per_run", 100) or 0)),
            "reinforce": max(0, int(kwargs.get("max_reinforce_per_run", 100) or 0)),
        }
        aliases = {"remove": "archive", "forget": "archive", "quality_review": "review"}
        kept = []
        seen = {"archive": 0, "review": 0, "reinforce": 0}
        skipped: dict[str, int] = {}
        for item in items:
            action = str(getattr(item, "action", "") or "").strip().lower()
            bucket = aliases.get(action, action)
            if bucket in caps:
                if seen[bucket] >= caps[bucket]:
                    skipped[bucket] = skipped.get(bucket, 0) + 1
                    continue
                seen[bucket] += 1
            kept.append(item)
        return tuple(kept), skipped

    def run_maintenance(self, **kwargs):
        started = utc_now()
        maintenance = self._maintenance_config()
        execute = bool(getattr(maintenance, "execute", True))
        dry_run = bool(kwargs["dry_run"]) if "dry_run" in kwargs else not execute
        policy = str(kwargs.get("policy") or getattr(maintenance, "policy", "balanced") or "balanced")
        layer_id = kwargs.get("layer_id") if "layer_id" in kwargs else getattr(maintenance, "layer_id", None)
        limit = kwargs.get("limit") if "limit" in kwargs else getattr(maintenance, "limit", 12)
        include_health = bool(getattr(maintenance, "include_health", True))
        pending = self.hcms_service.queue.pending_count()
        if not bool(getattr(maintenance, "enabled", True)):
            governance = _empty_governance(policy=policy, layer_id=layer_id, dry_run=dry_run)
            return _SimpleObject(
                run_id=f"maintenance-{uuid4().hex[:12]}",
                status="skipped",
                skipped_reason="disabled",
                dry_run=dry_run,
                policy=policy,
                layer_id=layer_id,
                source=str(kwargs.get("source") or "ops"),
                update_queue_pending=pending,
                update_queue_drained=0,
                reflection_jobs_due=0,
                reflection_jobs_run=0,
                reflection_entries_written=0,
                governance=governance,
                health_before=None,
                health_after=None,
                actions_executed={},
                skipped_actions={},
                errors=tuple(),
                started_at=started,
                finished_at=utc_now(),
            )
        processed = self.hcms_service.process_pending("global/default", force=not dry_run)
        health_before = self.health_report() if include_health else None
        governance = self.plan_memory_governance(
            policy=policy,
            layer_id=layer_id,
            limit=limit or 12,
            max_archive_per_run=getattr(maintenance, "max_archive_per_run", 100),
            max_quality_inspections_per_run=getattr(maintenance, "max_quality_inspections_per_run", 100),
            max_reinforce_per_run=getattr(maintenance, "max_reinforce_per_run", 100),
        ) if dry_run else self.execute_memory_governance(
            policy=policy,
            layer_id=layer_id,
            limit=limit or 12,
            source=kwargs.get("source", "ops"),
            max_archive_per_run=getattr(maintenance, "max_archive_per_run", 100),
            max_quality_inspections_per_run=getattr(maintenance, "max_quality_inspections_per_run", 100),
            max_reinforce_per_run=getattr(maintenance, "max_reinforce_per_run", 100),
        )
        actions_executed = _count_governance_actions(governance.results if not dry_run else ())
        skipped_actions = getattr(governance, "skipped_actions", {})
        health_after = self.health_report() if include_health else None
        return _SimpleObject(
            run_id=f"maintenance-{uuid4().hex[:12]}",
            status="completed",
            dry_run=dry_run,
            policy=policy,
            layer_id=layer_id,
            source=str(kwargs.get("source") or "ops"),
            update_queue_pending=pending,
            update_queue_drained=processed,
            reflection_jobs_due=0,
            reflection_jobs_run=0,
            reflection_entries_written=0,
            governance=governance,
            health_before=health_before,
            health_after=health_after,
            actions_executed=actions_executed,
            skipped_actions=skipped_actions,
            errors=tuple(),
            started_at=started,
            finished_at=utc_now(),
        )

    def maintenance_automation_status(self):
        maintenance = self._maintenance_config()
        execute = bool(getattr(maintenance, "execute", True))
        enabled = bool(getattr(maintenance, "enabled", True)) and bool(getattr(maintenance, "automation_enabled", True))
        return {
            "enabled": enabled,
            "last_run_at": None,
            "last_status": None,
            "last_reason": None,
            "last_run_id": None,
            "last_counts": {},
            "last_error_count": 0,
            "last_errors": [],
            "next_run_at": None,
            "tick_seconds": int(getattr(maintenance, "tick_seconds", 300) or 300),
            "interval_seconds": int(getattr(maintenance, "interval_seconds", 21600) or 21600),
            "min_idle_seconds": int(getattr(maintenance, "min_idle_seconds", 0) or 0),
            "dry_run": not execute,
            "execute": execute,
            "policy": str(getattr(maintenance, "policy", "balanced") or "balanced"),
            "layer_id": getattr(maintenance, "layer_id", None),
            "limit": int(getattr(maintenance, "limit", 12) or 12),
            "run_reflection_due_jobs": bool(getattr(maintenance, "run_reflection_due_jobs", True)),
        }

    def run_maintenance_automation_if_due(self):
        status = self.maintenance_automation_status()
        if not status["enabled"]:
            return _SimpleDump({"ran": False, "reason": "disabled", "errors": [], "status": status})
        return _SimpleDump({"ran": False, "reason": "not_due", "errors": []})

    def _maintenance_config(self):
        return getattr(self.config, "maintenance", None) or HCMSRuntimeConfig().maintenance

    def export_admin(self) -> dict[str, Any]:
        state = self.hcms_service.prefetch("global/default")
        health = self.health_report().model_dump(mode="json")
        return {
            "hcms": state.model_dump(mode="json"),
            "quality_issues": health.get("issues", []),
            "archive_turn_count": len(self._session_turns),
        }

    def import_admin(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"memories_imported": 0, "quality_issues_imported": 0, "status": "hcms_import_not_required"}

    def audit_admin(self):
        return _SimpleDump({"issues": [], "status": "ok"})

    def onboard_workspace(self, **kwargs):
        return _SimpleDump({
            "accepted": True,
            "status": "hcms_ready",
            "workspace_path": str(kwargs.get("workspace_path") or ""),
            "created_at": utc_now(),
        })

    def on_session_end(self, *, thread_id: str, messages: list[dict[str, Any]] | None = None, reason: str = "session_end", allow_network: bool = True):
        return self.flush_memory(thread_id=thread_id)

    def search_archive(self, query: str, limit: int = 5):
        hits = []
        for turn in self._rank_session_turns(query=query, current_thread_id=None, scope="all", limit=limit):
            hits.append(
                _SimpleObject(
                    archive_id=turn["archive_id"],
                    thread_id=turn["thread_id"],
                    score=1.0,
                    excerpt=_turn_excerpt(turn),
                    created_at=turn["created_at"],
                )
            )
        return _SimpleObject(query=query, hits=tuple(hits), engine_notes=("HCMS session archive search",))

    def clear_thread_runtime_artifacts(self, thread_id: str) -> None:
        return None

    def get_session_memory(self, *, thread_id: str, memory_namespace: str | None = None, injected_memory_snapshot_id: str | None = None, limit: int = 5) -> dict[str, Any]:
        turns = [turn for turn in self._session_turns if turn["thread_id"] == thread_id][-max(1, limit):]
        snapshot = self._prompt_snapshots.get(injected_memory_snapshot_id or "")
        if snapshot is None:
            thread_snapshots = [item for item in self._prompt_snapshots.values() if item.get("thread_id") == thread_id]
            if thread_snapshots:
                thread_snapshots.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
                snapshot = thread_snapshots[0]
        return {
            "thread_id": thread_id,
            "memory_namespace": memory_namespace or "global/default",
            "injected_memory_snapshot_id": injected_memory_snapshot_id,
            "archive_turn_count": len(turns),
            "recent_turns": turns,
            "latest_prompt_snapshot": snapshot,
            "session_summary": " ".join(_turn_excerpt(turn) for turn in turns) or self.render_stable_snapshot(),
        }

    def flush_memory(self, *, thread_id: str | None = None, messages: list[dict[str, Any]] | None = None, force: bool = True):
        processed = self.hcms_service.process_pending("global/default", force=force)
        return _SimpleDump({
            "thread_id": thread_id,
            "observations_processed": processed,
            "entries_written": processed,
            "quality_issues_created": 0,
            "entries_skipped": 0,
            "facts_removed": 0,
            "errors": (),
            "written_memory_ids": (),
            "quality_issue_ids": (),
            "candidate_audit": (),
        })

    def search_sessions(self, *, query: str, current_thread_id: str | None = None, scope: str = "exclude_current", limit: int = 5, mode: str = "summarize") -> dict[str, Any]:
        turns = self._rank_session_turns(query=query, current_thread_id=current_thread_id, scope=scope, limit=limit)
        groups = []
        for turn in turns:
            excerpt = _turn_excerpt(turn)
            groups.append(
                {
                    "thread_id": turn["thread_id"],
                    "hit_count": 1,
                    "summary": excerpt,
                    "excerpts": [excerpt],
                    "latest_created_at": turn["created_at"],
                    "hits": [
                        {
                            "archive_id": turn["archive_id"],
                            "thread_id": turn["thread_id"],
                            "score": 1.0,
                            "excerpt": excerpt,
                            "created_at": turn["created_at"],
                        }
                    ],
                    "evidence": [
                        {
                            "evidence_id": stable_id("session-evidence", turn["archive_id"], size=12),
                            "source_kind": "session_archive",
                            "source_id": turn["archive_id"],
                            "thread_id": turn["thread_id"],
                            "score": 1.0,
                            "match_score": 1.0,
                            "rerank_score": 1.0,
                            "recency_score": 1.0,
                            "final_score": 1.0,
                            "reason": "HCMS session archive match",
                            "excerpt": excerpt,
                        }
                    ],
                    "latest_prompt_snapshot": None,
                }
            )
        return {
            "query": query,
            "thread_id": current_thread_id,
            "scope": scope,
            "groups": groups,
            "engine_notes": [f"HCMS session search mode={mode}"],
            "current_thread_snapshot": None,
        }

    def list_traces(self, *, thread_id: str | None = None, target_id: str | None = None, limit: int = 10) -> tuple[MemoryTraceRecord, ...]:
        items = self._traces
        if thread_id is not None:
            items = [item for item in items if item.thread_id == thread_id]
        if target_id is not None:
            items = [item for item in items if item.target_id == target_id]
        return tuple(items[-max(1, limit):])

    def mark_thread_memory_polluted(self, **kwargs) -> None:
        return None

    def list_memory_pollution_markers(self, *, thread_id: str | None = None, limit: int = 100) -> tuple[object, ...]:
        return ()

    def list_reflection_jobs(self) -> tuple[ReflectionJob, ...]:
        return tuple(self._reflection_jobs.values())

    def create_reflection_job(self, job: ReflectionJob) -> ReflectionJob:
        self._reflection_jobs[job.job_id] = job
        return job

    def pause_reflection_job(self, job_id: str) -> ReflectionJob:
        job = self._require_reflection_job(job_id)
        updated = ReflectionJob(**{**job.__dict__, "enabled": False})
        self._reflection_jobs[job_id] = updated
        return updated

    def resume_reflection_job(self, job_id: str) -> ReflectionJob:
        job = self._require_reflection_job(job_id)
        updated = ReflectionJob(**{**job.__dict__, "enabled": True})
        self._reflection_jobs[job_id] = updated
        return updated

    def remove_reflection_job(self, job_id: str) -> ReflectionJob:
        if job_id not in self._reflection_jobs:
            raise KeyError(job_id)
        return self._reflection_jobs.pop(job_id)

    def run_reflection_job(self, job_id: str) -> ReflectionRunResult:
        job = self._require_reflection_job(job_id)
        query = job.source_query or job.instructions or job.name
        results = self.hcms_service.search("global/default", query, limit=5)
        return ReflectionRunResult(
            job_id=job_id,
            status="completed",
            archive_hits=len(results),
            summary=f"HCMS reflection inspected {len(results)} related memories.",
            written_entries=(),
        )

    def recall_benchmark(self, *, suite_id: str, cases: tuple[MemoryRecallBenchmarkCase, ...], evidence_limit: int = 10) -> MemoryRecallBenchmarkReport:
        results: list[MemoryRecallBenchmarkCaseResult] = []
        for case in cases:
            recalled = self.hcms_service.search("global/default", case.query, limit=evidence_limit)
            returned_ids = tuple(result.memory_id for result in recalled)
            expected = set(case.expected_memory_ids)
            forbidden = set(case.forbidden_memory_ids)
            returned_text = "\n".join(
                " ".join(
                    part
                    for part in (
                        result.memory_id,
                        result.explanation or "",
                        result.memory.content if result.memory is not None else "",
                        result.memory.summary if result.memory is not None else "",
                    )
                    if part
                )
                for result in recalled
            ).lower()
            false_positives = tuple(item for item in returned_ids if item in forbidden)
            missing_ids = tuple(item for item in expected if item not in returned_ids)
            missing_terms = tuple(term for term in case.expected_terms if str(term or "").lower() not in returned_text)
            missing = (*missing_ids, *missing_terms)
            expected_count = len(expected) + len(case.expected_terms)
            recall_hits = (len(set(returned_ids) & expected) if expected else 0) + (len(case.expected_terms) - len(missing_terms))
            passed = (expected_count == 0 or not missing) and not false_positives
            score = 1.0 if passed else (round(recall_hits / expected_count, 4) if expected_count else 0.0)
            top_evidence = tuple(_evidence_payload(result, thread_id=case.thread_id) for result in recalled[:evidence_limit])
            results.append(
                MemoryRecallBenchmarkCaseResult(
                    case_id=case.case_id,
                    query=case.query,
                    passed=passed,
                    score=score,
                    returned_memory_ids=returned_ids,
                    expected_memory_ids=case.expected_memory_ids,
                    forbidden_memory_ids=case.forbidden_memory_ids,
                    recall_hits=recall_hits if expected_count else len(returned_ids),
                    expected_count=expected_count,
                    false_positive_count=len(false_positives),
                    evidence_count=len(recalled),
                    top_evidence=top_evidence,
                    missing_expectations=missing,
                    false_positives=false_positives,
                    summary=f"HCMS returned {len(recalled)} memories for query.",
                )
            )
        score = sum(item.score for item in results) / len(results) if results else 0.0
        now = utc_now()
        return MemoryRecallBenchmarkReport(
            suite_id=suite_id,
            score=round(score, 4),
            passed=score >= 0.85,
            cases=tuple(results),
            evidence_limit=evidence_limit,
            run_id=f"benchmark-{uuid4().hex[:12]}",
            created_at=now,
            generated_at=now,
        )

    def list_recall_benchmark_suites(self) -> tuple[MemoryRecallBenchmarkSuite, ...]:
        return tuple(self._benchmark_suites.values())

    def upsert_recall_benchmark_suite(self, suite: MemoryRecallBenchmarkSuite, *, source: str = "hcms") -> MemoryRecallBenchmarkSuite:
        updated = MemoryRecallBenchmarkSuite(**{**suite.__dict__, "source": source})
        if updated.created_at is None or updated.updated_at is None:
            now = utc_now()
            updated = MemoryRecallBenchmarkSuite(
                **{
                    **updated.__dict__,
                    "created_at": updated.created_at or now,
                    "updated_at": now,
                }
            )
        self._benchmark_suites[updated.suite_id] = updated
        return updated

    def delete_recall_benchmark_suite(self, suite_id: str) -> MemoryRecallBenchmarkSuite:
        if suite_id not in self._benchmark_suites:
            raise KeyError(suite_id)
        return self._benchmark_suites.pop(suite_id)

    def run_recall_benchmark_suite(self, suite_id: str, *, evidence_limit: int = 10, source: str = "hcms", record: bool = True) -> MemoryRecallBenchmarkRun:
        suite = self._benchmark_suites.get(suite_id)
        if suite is None:
            raise KeyError(suite_id)
        report = self.recall_benchmark(suite_id=suite_id, cases=suite.cases, evidence_limit=evidence_limit)
        run = MemoryRecallBenchmarkRun(
            run_id=report.run_id,
            suite_id=suite_id,
            report=report,
            source=source,
            created_at=utc_now(),
        )
        if record:
            self._benchmark_runs.append(run)
        self._benchmark_suites[suite_id] = MemoryRecallBenchmarkSuite(
            **{
                **suite.__dict__,
                "latest_run_id": run.run_id,
                "latest_score": report.score,
                "latest_passed": report.passed,
                "latest_run_at": run.created_at,
                "updated_at": utc_now(),
            }
        )
        return run

    def list_recall_benchmark_runs(self, *, suite_id: str | None = None, limit: int = 20) -> tuple[MemoryRecallBenchmarkRun, ...]:
        runs = self._benchmark_runs
        if suite_id is not None:
            runs = [run for run in runs if run.suite_id == suite_id]
        return tuple(runs[-max(1, limit):])

    def _require_reflection_job(self, job_id: str) -> ReflectionJob:
        if job_id not in self._reflection_jobs:
            raise KeyError(job_id)
        return self._reflection_jobs[job_id]

    def _record_trace(
        self,
        *,
        thread_id: str | None,
        query: str | None,
        trace_kind: str,
        target_id: str | None = None,
        engine_notes: tuple[str, ...] = (),
        evidence: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self._traces.append(
            MemoryTraceRecord(
                trace_id=f"trace-{uuid4().hex[:16]}",
                thread_id=thread_id,
                query=query,
                trace_kind=trace_kind,
                target_id=target_id,
                engine_notes=engine_notes,
                evidence=evidence,
            )
        )
        self._traces = self._traces[-500:]

    def _require_memory(self, memory_id: str) -> Memory:
        for memory in self.hcms_service.prefetch("global/default").memories:
            if memory.memory_id == memory_id:
                return memory
        raise KeyError(memory_id)

    def _retention_view(self, memory: Memory):
        retention = memory.compute_retention_score()
        return _SimpleObject(
            memory_id=memory.memory_id,
            layer_id=memory.layer_id,
            tier="active" if retention >= 0.35 else "cold",
            retention_score=retention,
            salience=memory.salience,
            temporal_decay=0.0,
            reinforcement_boost=min(memory.access_count * 0.02, 0.2),
            access_count=memory.access_count,
            last_accessed_at=memory.accessed_at,
            created_at=memory.created_at,
            status=memory.state.value,
        )

    def _governance_plan_item(self, memory: Memory, *, policy: str):
        retention = memory.compute_retention_score()
        action = "review" if policy in {"review", "review_memory"} else ("archive" if retention < 0.15 else "reinforce")
        return _SimpleObject(
            memory_id=memory.memory_id,
            store_id=memory.store_id,
            entry_id=memory.entry_id,
            layer_id=memory.layer_id,
            action=action,
            reason=f"HCMS retention score {retention:.3f} under policy {policy}",
            tier="cold" if retention < 0.35 else "active",
            stale_score=round(1.0 - retention, 4),
            retention_score=retention,
            salience=memory.salience,
            access_count=memory.access_count,
            last_accessed_at=memory.accessed_at,
            expires_at=memory.expires_at,
        )

    def _rank_session_turns(self, *, query: str, current_thread_id: str | None, scope: str, limit: int) -> list[dict[str, Any]]:
        normalized_scope = str(scope or "exclude_current").lower()
        query_terms = set(str(query or "").lower().split())
        candidates = []
        seen_threads = set()
        for turn in reversed(self._session_turns):
            thread_id = turn["thread_id"]
            if normalized_scope == "exclude_current" and current_thread_id and thread_id == current_thread_id:
                continue
            if normalized_scope == "current" and current_thread_id and thread_id != current_thread_id:
                continue
            if thread_id in seen_threads:
                continue
            text = _turn_excerpt(turn).lower()
            if query_terms and not any(term.strip(".,:;!?") in text for term in query_terms):
                continue
            seen_threads.add(thread_id)
            candidates.append(turn)
            if len(candidates) >= max(1, limit):
                break
        return candidates


def _should_sync_conflict_alert(conflict: object, *, namespace: str) -> bool:
    conflict_id = str(getattr(conflict, "conflict_id", "") or "").strip()
    if not conflict_id:
        return False
    conflict_namespace = str(getattr(conflict, "namespace", namespace) or namespace)
    if namespace and conflict_namespace != namespace:
        return False
    status = str(getattr(conflict, "status", "") or "").strip().lower()
    if status not in {"open", "needs_review", "unresolved"}:
        return False
    severity = str(getattr(conflict, "severity", "") or "").strip().lower()
    injection_policy = str(getattr(conflict, "injection_policy", "") or "").strip().lower()
    return severity in {"high", "critical"} or injection_policy in {
        "inject_warning",
        "block_fact_injection",
        "warning_only",
    }


class _SimpleDump:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return dict(self.payload)

    def model_dump_json(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, default=str)


class _SimpleObject:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return _dump_nested(dict(self.__dict__), mode=mode)

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, default=str)


def _count_governance_actions(results: tuple[object, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        action = str(getattr(result, "action", "") or "").strip().lower()
        if not action:
            continue
        counts[action] = counts.get(action, 0) + 1
    return counts


def _empty_governance(*, policy: str, layer_id: str | None, dry_run: bool):
    normalized_layer = _canonical_layer(layer_id or "all")
    return _SimpleObject(
        policy=policy,
        layer_id=None if normalized_layer == "all" else normalized_layer,
        dry_run=dry_run,
        candidate_count=0,
        executed_count=0,
        skipped_count=0,
        items=tuple(),
        results=tuple(),
        skipped_actions={},
        errors=tuple(),
    )


def _fingerprint(content: str) -> str:
    return hashlib.sha256(str(content or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def _render_memory_injection_snapshot(injection: MemoryInjectionView) -> str:
    lines = [
        f"namespace={sanitize_memory_context_text(injection.namespace)}",
        f"confidence={injection.confidence:.3f}",
    ]
    summary = sanitize_memory_context_text(injection.summary).strip()
    if summary:
        lines.append(f"summary={summary}")
    _extend_snapshot_list(lines, "facts", injection.facts)
    _extend_snapshot_list(lines, "causal_chains", injection.causal_chains)
    _extend_snapshot_list(lines, "evidence", injection.evidence)
    return "\n".join(line for line in lines if line.strip()).strip()


def _extend_snapshot_list(lines: list[str], label: str, values: Iterable[str]) -> None:
    safe_values = [sanitize_memory_context_text(value).strip() for value in values]
    safe_values = [value for value in safe_values if value]
    if not safe_values:
        return
    lines.append(f"{label}:")
    lines.extend(f"- {value}" for value in safe_values)


def _dump_nested(value: Any, *, mode: str = "python") -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode=mode)
    if isinstance(value, dict):
        return {key: _dump_nested(item, mode=mode) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump_nested(item, mode=mode) for item in value]
    return value


def _format_diagnostics(state: MemoryState) -> list[str]:
    diagnostics: list[str] = []
    for item in state.diagnostics[-20:]:
        stream = f":{item.stream_name}" if item.stream_name else ""
        error = f":{item.error_type}" if item.error_type else ""
        diagnostics.append(f"{item.component}:{item.reason}{stream}{error}:x{item.count}")
    return diagnostics


def _result_payload(result: RetrievalResult) -> dict[str, Any]:
    return {
        "memory_id": result.memory_id,
        "score": result.score,
        "raw_scores": dict(result.raw_scores),
        "ranks": dict(result.ranks),
        "explanation": result.explanation,
        "memory": result.memory.model_dump(mode="json") if result.memory is not None else None,
    }


def _category_for_layer(layer_id: str, category: str) -> str:
    normalized_layer = str(layer_id or "").strip().lower()
    normalized_category = str(category or "note").strip().lower()
    if normalized_layer == "user" and normalized_category == "note":
        return MemoryCategory.PREFERENCE_PROFILE.value
    if normalized_layer == "workspace" and normalized_category == "note":
        return MemoryCategory.PROJECT_CONTEXT.value
    return normalized_category


def _layer_for_memory(memory: Memory) -> str:
    if memory.category in {MemoryCategory.PREFERENCE, MemoryCategory.PREFERENCE_PROFILE, MemoryCategory.CORRECTION}:
        return "user"
    return "workspace"


def _canonical_layer(layer_id: str | None) -> str:
    normalized = str(layer_id or "workspace").strip().lower().replace("-", "_")
    aliases = {
        "": "workspace",
        "all": "all",
        "*": "all",
        "hcms": "all",
        "session": "session",
        "archive": "session",
        "hcms_session": "session",
        "user": "user",
        "hcms_user": "user",
        "workspace": "workspace",
        "project": "workspace",
        "hcms_workspace": "workspace",
    }
    if normalized not in aliases:
        raise KeyError(normalized)
    return aliases[normalized]


def _store_id_for_layer(layer_id: str) -> str:
    normalized = _canonical_layer(layer_id)
    if normalized == "user":
        return "hcms_user"
    if normalized == "workspace":
        return "hcms_workspace"
    if normalized == "session":
        return "hcms_session"
    return "hcms"


def _store_from_config(config: HCMSRuntimeConfig, *, root: Path):
    backend = str(getattr(config, "storage_backend", "hybrid") or "hybrid").strip().lower().replace("-", "_")
    if backend in {"filesystem", "file", "files", "json", "local"}:
        return FileMemoryStore(root / "hcms")
    if backend in {"hybrid", "markdown"}:
        return HybridMemoryBackend(root / "hcms")
    raise StorageError(f"Unsupported HCMS storage backend: {backend}")


def _updater_from_config(
    config: HCMSRuntimeConfig,
    *,
    max_facts: int,
    structured_update_provider: StructuredUpdateProvider | None = None,
):
    updater_config = getattr(config, "updater", None)
    if not bool(getattr(updater_config, "enabled", True)):
        return HeuristicMemoryUpdater(max_facts=max_facts)
    mode = str(getattr(updater_config, "mode", "heuristic") or "heuristic").strip().lower().replace("-", "_")
    threshold = float(getattr(updater_config, "fact_confidence_threshold", 0.82) or 0.82)
    if mode == "structured":
        return StructuredMemoryUpdater(
            confidence_threshold=threshold,
            response_provider=structured_update_provider,
            fallback_to_rules=bool(getattr(updater_config, "fail_open", True)),
        )
    if mode == "rule_based":
        return RuleBasedMemoryUpdater(confidence_threshold=threshold)
    return HeuristicMemoryUpdater(max_facts=max_facts)


def _layer_for_store_or_layer(value: str | None) -> str:
    return _canonical_layer(value)


def _turn_excerpt(turn: dict[str, Any]) -> str:
    user = str(turn.get("user_content") or "").strip()
    assistant = str(turn.get("assistant_content") or "").strip()
    text = " ".join(part for part in (user, assistant) if part)
    return text[:500]


def _memory_evidence_payload(
    memory: Memory,
    *,
    thread_id: str | None,
    score: float,
    reason: str,
) -> dict[str, Any]:
    excerpt = memory.summary or memory.content[:240]
    return {
        "evidence_id": stable_id("hcms-evidence", memory.memory_id, reason, size=12),
        "source_kind": "hcms_memory",
        "source_id": memory.memory_id,
        "layer_id": memory.layer_id,
        "memory_id": memory.memory_id,
        "archive_id": None,
        "thread_id": thread_id or memory.source_thread_id,
        "score": score,
        "match_score": score,
        "rerank_score": score,
        "recency_score": None,
        "final_score": score,
        "dropped_reason": None,
        "reason": reason,
        "excerpt": excerpt,
    }


def _evidence_payload(result: RetrievalResult, *, thread_id: str | None) -> dict[str, Any]:
    memory = result.memory
    if memory is not None:
        return _memory_evidence_payload(memory, thread_id=thread_id, score=result.score, reason=result.explanation or "HCMS recall")
    return {
        "evidence_id": stable_id("hcms-evidence", result.memory_id, result.score, size=12),
        "source_kind": "hcms_recall",
        "source_id": result.memory_id,
        "layer_id": None,
        "memory_id": result.memory_id,
        "archive_id": None,
        "thread_id": thread_id,
        "score": result.score,
        "match_score": result.raw_scores.get("bm25"),
        "rerank_score": result.raw_scores.get("vector"),
        "recency_score": result.raw_scores.get("temporal"),
        "final_score": result.score,
        "dropped_reason": None,
        "reason": result.explanation or "HCMS recall",
        "excerpt": "",
    }
