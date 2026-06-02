from __future__ import annotations

from fnmatch import fnmatch
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from anvil.config import EffectiveConfig, MemoryConfig, MemoryPlatformConfig
from anvil.memory import FileMemoryStore, MemoryState

from .archive import SqliteSessionArchive
from .automation import MemoryAutomationQueue
from .candidates import MemoryCandidate, MemoryCandidateExtractor
from .contracts import (
    ArchiveSearchResult,
    CuratedEntry,
    MemoryConflict,
    MemoryAuditEntry,
    MemoryGovernanceBatchResult,
    MemoryGovernancePlanItem,
    MemoryGovernanceResult,
    MemoryHealthReport,
    MemoryMaintenanceAutomationResult,
    MemoryMaintenanceRun,
    MemoryCandidateAuditEntry,
    MemoryOnboardingFile,
    MemoryOnboardingResult,
    MemoryPollutionMarker,
    ProfileFacet,
    ProfileFacetAuditEntry,
    ProfileFacetGovernanceResult,
    ProfileFacetPolicySnapshot,
    ProfileFacetRebuildResult,
    MemoryPlatformOverview,
    MemoryProviderTestResult,
    MemoryRecallBenchmarkCase,
    MemoryRecallBenchmarkReport,
    MemoryRecallBenchmarkRun,
    MemoryRecallBenchmarkSuite,
    MemoryRetentionView,
    MemoryStalenessView,
    MemoryTrace,
    MemoryWriteEvent,
    MemoryFlushResult,
    MemoryReviewItem,
    RecallResult,
    ReflectionJob,
    ReflectionRunResult,
    utc_now,
)
from .benchmark import run_recall_benchmark
from .curated import CuratedStoreManager, JsonCuratedStoreRepository
from .flush import MemoryFlushService
from .guard import MemoryGuard
from .health import build_memory_health_report
from .llm_update import LLMMemoryUpdateService, StructuredMemoryUpdate, memory_candidate_quality
from .provider_runtime import ProviderRuntime
from .providers import ProviderRegistry
from .profile_facets import apply_profile_facet_budgets, apply_profile_user_state, build_profile_facet, profile_facet_policy_from_config
from .pollution import MemoryPollutionStore
from .prompt_snapshots import PromptSnapshotStore
from .recall import RecallPlanner, SessionSearchService
from .resolution import MemoryResolutionService
from .retention import retention_metrics
from .reflection import ReflectionScheduler
from .reflection_service import ReflectionService
from .retrieval_index import RetrievalIndexStore
from .review import MemoryReviewQueue
from .scrubber import MemorySecretScrubber
from .session_snapshot import MemorySessionSnapshot, MemorySessionSnapshotStore
from .signals import MemorySignalDetector
from .summarizer import FocusedSessionSummaryService
from .trace import MemoryTraceStore
from .update_queue import MemoryUpdateBatch, MemoryUpdateQueue
from .write_service import MemoryWriteService
from anvil.runtime.token_budget import TokenBudgetService


def _layer_for_store_id(store_id: str) -> str:
    if store_id == "user_profile":
        return "user"
    return "workspace"


def _retention_view_for_entry(entry: CuratedEntry) -> MemoryRetentionView:
    metrics = retention_metrics(entry)
    return MemoryRetentionView(
        memory_id=entry.memory_id or entry.entry_id,
        store_id=entry.store_id,
        layer_id=entry.layer_id or _layer_for_store_id(entry.store_id),
        tier=metrics["tier"],
        retention_score=metrics["retention_score"],
        salience=metrics["salience"],
        temporal_decay=metrics["temporal_decay"],
        reinforcement_boost=metrics["reinforcement_boost"],
        access_count=metrics["access_count"],
        last_accessed_at=metrics["last_accessed_at"],
        created_at=entry.created_at,
        status=entry.status,
    )


def _memory_governance_action_for_stale(*, policy: str, stale: MemoryStalenessView, entry: CuratedEntry) -> str | None:
    now = utc_now()
    expired = entry.expires_at is not None and entry.expires_at <= now
    if policy in {"archive", "archive_expired"}:
        return "archive" if expired or stale.stale_score >= 0.85 else "review"
    if policy in {"reinforce", "protect"}:
        return "reinforce" if stale.salience >= 0.25 or stale.access_count > 0 else "review"
    if policy in {"review", "human_review"}:
        return "review"
    if expired:
        return "archive"
    if stale.retention_score < 0.18 and stale.salience < 0.25 and stale.access_count == 0:
        return "archive"
    if stale.retention_score < 0.32 or stale.stale_score >= 0.74:
        return "review"
    if stale.salience >= 0.45 or stale.access_count > 0:
        return "reinforce"
    return "refresh"


def _memory_governance_reason(*, policy: str, stale: MemoryStalenessView, entry: CuratedEntry, action: str) -> str:
    if action == "archive" and entry.expires_at is not None:
        return f"{policy} policy archived expired memory"
    if action == "archive":
        return f"{policy} policy archived low-retention inactive memory"
    if action == "review":
        return f"{policy} policy queued stale memory for review"
    if action == "reinforce":
        return f"{policy} policy reinforced useful stale memory"
    return f"{policy} policy refreshed memory access metadata"


def _action_priority(action: str) -> int:
    return {"archive": 0, "review": 1, "reinforce": 2, "refresh": 3}.get(action, 9)


def _count_actions(actions: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        key = str(action or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _memory_candidate_preview(content: str, *, limit: int = 180) -> str:
    normalized = " ".join(str(content or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _memory_onboarding_preview(content: str, *, limit: int = 260) -> str:
    normalized = "\n".join(line.rstrip() for line in str(content or "").splitlines()).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _normalize_recall_suite_id(value: str, *, default: str = "default") -> str:
    normalized = "".join(
        char.lower() if char.isalnum() else "-"
        for char in str(value or "").strip()
    ).strip("-")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return (normalized or default)[:120]


def _bounded_unique_strings(values: tuple[str, ...] | list[str], *, limit: int, max_chars: int, lowercase: bool = False) -> tuple[str, ...]:
    result: list[str] = []
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value:
            continue
        if lowercase:
            value = value.lower()
        value = value[:max_chars]
        if value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return tuple(result)


def _plugin_memory_provider_configs(effective_config: EffectiveConfig | None) -> tuple[Any, ...]:
    if effective_config is None:
        return ()
    providers: list[Any] = []
    for plugin in effective_config.extensions.plugins.values():
        if not plugin.enabled:
            continue
        providers.extend(plugin.memory_providers)
    return tuple(providers)


class MemoryManager:
    def __init__(
        self,
        *,
        config: MemoryPlatformConfig,
        curated_store_manager: CuratedStoreManager,
        archive: SqliteSessionArchive,
        prompt_snapshot_store: PromptSnapshotStore,
        provider_registry: ProviderRegistry,
        reflection_scheduler: ReflectionScheduler,
        retrieval_index: RetrievalIndexStore,
        trace_store: MemoryTraceStore,
        memory_guard: MemoryGuard,
        provider_runtime: ProviderRuntime,
        write_service: MemoryWriteService,
        recall_planner: RecallPlanner,
        session_search_service: SessionSearchService,
        reflection_service: ReflectionService,
        automation_queue: MemoryAutomationQueue,
        review_queue: MemoryReviewQueue,
        candidate_extractor: MemoryCandidateExtractor,
        llm_update_service: LLMMemoryUpdateService,
        session_snapshot_store: MemorySessionSnapshotStore,
        update_queue: MemoryUpdateQueue,
        signal_detector: MemorySignalDetector,
        resolution_service: MemoryResolutionService,
        token_budget: TokenBudgetService,
        pollution_store: MemoryPollutionStore,
        state_root: str | Path,
    ) -> None:
        self.config = config
        self.curated_store_manager = curated_store_manager
        self.archive = archive
        self.prompt_snapshot_store = prompt_snapshot_store
        self.provider_registry = provider_registry
        self.reflection_scheduler = reflection_scheduler
        self.retrieval_index = retrieval_index
        self.trace_store = trace_store
        self.memory_guard = memory_guard
        self.provider_runtime = provider_runtime
        self.write_service = write_service
        self.recall_planner = recall_planner
        self.session_search_service = session_search_service
        self.reflection_service = reflection_service
        self.automation_queue = automation_queue
        self.review_queue = review_queue
        self.candidate_extractor = candidate_extractor
        self.llm_update_service = llm_update_service
        self.session_snapshot_store = session_snapshot_store
        self.update_queue = update_queue
        self.signal_detector = signal_detector
        self.resolution_service = resolution_service
        self.token_budget = token_budget
        self.pollution_store = pollution_store
        self.state_root = Path(state_root)
        self._profile_facet_policy = profile_facet_policy_from_config(config.profile_facets)
        self.flush_service = MemoryFlushService(
            archive=self.archive,
            candidate_extractor=self.candidate_extractor,
            apply_candidate=self._apply_candidate,
        )

    @classmethod
    def from_config(
        cls,
        *,
        config: MemoryPlatformConfig,
        base_path: str | Path,
        legacy_store_path: str | Path | None = None,
        effective_config: EffectiveConfig | None = None,
    ) -> "MemoryManager":
        root = Path(base_path).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        token_budget = TokenBudgetService()
        resolution_service = MemoryResolutionService(
            auto_accept_confidence=config.review.auto_accept_confidence,
            auto_supersede_confidence=config.review.auto_supersede_confidence,
        )
        profile_facet_policy = profile_facet_policy_from_config(config.profile_facets)
        curated_store_manager = CuratedStoreManager(
            store_configs=config.stores,
            repository=JsonCuratedStoreRepository(root / "curated"),
            token_budget=token_budget,
            resolution_service=resolution_service,
            profile_facet_policy=profile_facet_policy,
        )
        archive = SqliteSessionArchive(
            config.archive.sqlite_path or (root / "archive.sqlite3"),
            fts_enabled=config.archive.fts_enabled,
        )
        prompt_snapshot_store = PromptSnapshotStore(
            config.prompt_snapshot.store_path or (root / "prompt-snapshots"),
            enabled=config.prompt_snapshot.enabled,
            ttl_days=config.prompt_snapshot.ttl_days,
            max_snapshots_per_thread=config.prompt_snapshot.max_snapshots_per_thread,
        )
        provider_registry = ProviderRegistry(
            active_provider_id=config.providers.active_provider_id,
            catalog=config.providers.catalog,
            plugin_providers=_plugin_memory_provider_configs(effective_config),
        )
        provider_runtime = ProviderRuntime(registry=provider_registry)
        retrieval_index = RetrievalIndexStore(root / "retrieval-index.sqlite3")
        trace_store = MemoryTraceStore(root / "memory-trace.sqlite3")
        memory_guard = MemoryGuard()
        automation_queue = MemoryAutomationQueue(enabled=config.enabled)
        write_service = MemoryWriteService(
            curated_store_manager=curated_store_manager,
            guard=memory_guard,
            retrieval_index=retrieval_index,
            provider_runtime=provider_runtime,
            trace_store=trace_store,
        )
        session_search_service = SessionSearchService(
            archive=archive,
            retrieval_index=retrieval_index,
            prompt_snapshot_store=prompt_snapshot_store,
            trace_store=trace_store,
            summary_service=FocusedSessionSummaryService(effective_config=effective_config) if effective_config is not None else None,
        )
        recall_planner = RecallPlanner(
            curated_store_manager=curated_store_manager,
            archive=archive,
            retrieval_index=retrieval_index,
            provider_runtime=provider_runtime,
            trace_store=trace_store,
            token_budget=token_budget,
            resolution_service=resolution_service,
            config=config.recall,
            effective_config=effective_config,
        )
        reflection_scheduler = ReflectionScheduler(
            jobs_path=root / "reflection" / "jobs.json",
            curated_store_manager=curated_store_manager,
            archive=archive,
            tick_seconds=config.reflection.tick_seconds,
            enabled=config.enabled and config.reflection.enabled,
        )
        reflection_service = ReflectionService(
            archive=archive,
            curated_store_manager=curated_store_manager,
            session_search_service=session_search_service,
            write_service=write_service,
        )
        reflection_scheduler.register_executor(reflection_service.run_job)
        candidate_extractor = MemoryCandidateExtractor(max_direct_content_chars=config.review.max_direct_content_chars)
        llm_update_service = LLMMemoryUpdateService(
            config=config.updater,
            fallback_extractor=candidate_extractor,
            effective_config=effective_config,
            token_budget=token_budget,
        )
        manager = cls(
            config=config,
            curated_store_manager=curated_store_manager,
            archive=archive,
            prompt_snapshot_store=prompt_snapshot_store,
            provider_registry=provider_registry,
            reflection_scheduler=reflection_scheduler,
            retrieval_index=retrieval_index,
            trace_store=trace_store,
            memory_guard=memory_guard,
            provider_runtime=provider_runtime,
            write_service=write_service,
            recall_planner=recall_planner,
            session_search_service=session_search_service,
            reflection_service=reflection_service,
            automation_queue=automation_queue,
            review_queue=MemoryReviewQueue(root / "review" / "items.json"),
            candidate_extractor=candidate_extractor,
            llm_update_service=llm_update_service,
            session_snapshot_store=MemorySessionSnapshotStore(
                config.session_snapshot.store_path or (root / "session-snapshots"),
                enabled=config.session_snapshot.enabled,
            ),
            update_queue=MemoryUpdateQueue(
                enabled=config.enabled and config.update_queue.enabled,
                max_batch_turns=config.update_queue.max_batch_turns,
                min_batch_turns=config.update_queue.min_batch_turns,
                debounce_seconds=config.update_queue.debounce_seconds,
            ),
            signal_detector=MemorySignalDetector(),
            resolution_service=resolution_service,
            token_budget=token_budget,
            pollution_store=MemoryPollutionStore(root / "pollution" / "thread-markers.json"),
            state_root=root,
        )
        if config.reflection.auto_register_defaults:
            manager.reflection_scheduler.ensure_default_jobs()
        if legacy_store_path is not None:
            manager._migrate_legacy_store(legacy_store_path)
        if config.enabled and config.reflection.enabled:
            manager.reflection_scheduler.start()
        return manager

    @classmethod
    def from_legacy_memory(
        cls,
        *,
        legacy: MemoryConfig,
        base_path: str | Path,
    ) -> "MemoryManager":
        return cls.from_config(
            config=MemoryPlatformConfig.from_legacy_memory(legacy),
            base_path=base_path,
            legacy_store_path=legacy.store_path,
        )

    def overview(self) -> MemoryPlatformOverview:
        stores = self.list_stores()
        return MemoryPlatformOverview(
            active_provider_id=self.provider_registry.active_provider_id,
            runtime_mode="memory_platform" if self.config.enabled else "legacy",
            legacy_capture_enabled=False,
            migration_status={
                "legacy_store_compatibility": "read_only" if self.config.enabled else "active",
                "store_budget_sources": {store.store_id: store.budget_source for store in stores},
            },
            store_count=len(stores),
            archive_turn_count=self.archive.count(),
            reflection_job_count=len(self.list_reflection_jobs()),
            stores=stores,
        )

    def list_stores(self):
        return self.curated_store_manager.list_stores()

    def list_entries(self, store_id: str):
        return self.curated_store_manager.list_entries(store_id)

    def store_id_for_layer(self, layer_id: str) -> str | None:
        normalized = layer_id.strip().lower()
        if normalized == "session":
            return None
        if normalized == "user":
            return "user_profile"
        if normalized == "workspace":
            return "runtime_memory"
        raise KeyError(layer_id)

    def list_layer_entries(self, layer_id: str):
        store_id = self.store_id_for_layer(layer_id)
        if store_id is None:
            raise ValueError("session layer does not expose durable entries")
        return self.list_entries(store_id)

    def consolidate_layer(self, layer_id: str) -> CuratedEntry:
        store_id = self.store_id_for_layer(layer_id)
        if store_id is None:
            raise ValueError("session layer is not consolidatable")
        entries = self.list_entries(store_id)
        if not entries:
            raise ValueError(f"{layer_id} layer has no entries to consolidate")
        summary_lines = [f"{entry.category}: {entry.content}" for entry in entries[:5]]
        return self.write_service.create_entry(
            store_id,
            content="Consolidated memory: " + " | ".join(summary_lines),
            category="consolidation",
            source_kind="tool_consolidate",
            priority=0.6,
            write_policy="consolidate",
            write_reason=f"consolidated {layer_id} layer entries",
        )

    def create_entry(
        self,
        store_id: str,
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
        supersedes: tuple[str, ...] = (),
        write_policy: str = "manual",
        write_reason: str | None = None,
    ):
        return self.write_service.create_entry(
            store_id,
            content=content,
            category=category,
            source_kind=source_kind,
            priority=priority,
            metadata=metadata,
            thread_id=thread_id,
            source_ref=source_ref,
            confidence=confidence,
            salience=salience,
            evidence_refs=evidence_refs,
            supersedes=supersedes,
            user_id="default",
            workspace_id="default",
            write_policy=write_policy,
            write_reason=write_reason,
        )

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
        supersedes: tuple[str, ...] = (),
    ):
        store_id = self.store_id_for_layer(layer_id)
        if store_id is None:
            raise ValueError("session layer is read-only")
        return self.create_entry(
            store_id,
            content=content,
            category=category,
            source_kind=source_kind,
            priority=priority,
            metadata=metadata,
            thread_id=thread_id,
            source_ref=source_ref,
            confidence=confidence,
            salience=salience,
            evidence_refs=evidence_refs,
            supersedes=supersedes,
        )

    def update_entry(
        self,
        store_id: str,
        entry_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        priority: float | None = None,
        confidence: float | None = None,
        salience: float | None = None,
        evidence_refs: tuple[str, ...] | None = None,
        supersedes: tuple[str, ...] | None = None,
        status: str | None = None,
    ):
        return self.write_service.update_entry(
            store_id,
            entry_id,
            content=content,
            category=category,
            priority=priority,
            confidence=confidence,
            salience=salience,
            evidence_refs=evidence_refs,
            supersedes=supersedes,
            status=status,
        )

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
        status: str | None = None,
    ):
        store_id = self.store_id_for_layer(layer_id)
        if store_id is None:
            raise ValueError("session layer is read-only")
        return self.update_entry(
            store_id,
            entry_id,
            content=content,
            category=category,
            priority=priority,
            confidence=confidence,
            salience=salience,
            status=status,
        )

    def delete_entry(self, store_id: str, entry_id: str) -> None:
        self.write_service.delete_entry(store_id, entry_id)

    def delete_layer_entry(self, layer_id: str, entry_id: str) -> None:
        store_id = self.store_id_for_layer(layer_id)
        if store_id is None:
            raise ValueError("session layer is read-only")
        self.delete_entry(store_id, entry_id)

    def render_stable_snapshot(self) -> str:
        return self.curated_store_manager.render_stable_snapshot()

    def stable_snapshot_fingerprint(self) -> str:
        return self.curated_store_manager.snapshot_fingerprint()

    def list_providers(self):
        return self.provider_runtime.list_providers()

    def activate_provider(self, provider_id: str):
        return self.provider_runtime.activate(provider_id)

    def test_provider(self, provider_id: str) -> MemoryProviderTestResult:
        return self.provider_runtime.test_provider(provider_id)

    def reload_providers(self, *, effective_config: EffectiveConfig | None = None) -> tuple:
        self.provider_registry = ProviderRegistry(
            active_provider_id=self.provider_registry.active_provider_id,
            catalog=self.config.providers.catalog,
            plugin_providers=_plugin_memory_provider_configs(effective_config),
        )
        self.provider_runtime.registry = self.provider_registry
        return self.list_providers()

    def record_turn(
        self,
        *,
        thread_id: str,
        user_content: str,
        assistant_content: str,
        status: str = "completed",
        source_metadata: dict[str, Any] | None = None,
    ) -> None:
        record = self.archive.record_turn(thread_id, user_content, assistant_content, status)
        for marker in self._pollution_markers_from_source_metadata(
            thread_id=thread_id,
            evidence_ref=record.archive_id,
            source_metadata=source_metadata,
        ):
            self.mark_thread_memory_polluted(
                thread_id=marker["thread_id"],
                source_kind=marker["source_kind"],
                source_id=marker.get("source_id"),
                tool_name=marker.get("tool_name"),
                reason=marker["reason"],
                evidence_ref=marker.get("evidence_ref"),
                metadata=marker.get("metadata"),
            )
        self.automation_queue.submit("index_archive_turn", lambda: self.write_service.index_archive_turn(record))
        self.update_queue.enqueue(record)
        should_flush_now = self._has_high_value_update_signal(
            user_content=user_content,
            assistant_content=assistant_content,
            status=status,
        )
        if should_flush_now:
            self.drain_update_queue(thread_id=thread_id, force=True)
        else:
            self.automation_queue.submit(
                "drain_memory_update_queue",
                lambda: self.drain_update_queue(thread_id=thread_id, force=False),
            )
        self.automation_queue.submit("provider_sync_turn", lambda: self.provider_runtime.sync_turn(record))

    def onboard_workspace(
        self,
        *,
        workspace_path: str | Path,
        thread_id: str | None = None,
        force: bool = False,
        source: str = "ops",
    ) -> MemoryOnboardingResult:
        onboarding_config = self.config.onboarding
        root = Path(workspace_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"workspace path '{root}' is not a directory")
        base_result = {
            "workspace_path": str(root),
            "thread_id": thread_id,
            "store_id": onboarding_config.target_store_id,
            "layer_id": onboarding_config.target_layer_id,
            "category": onboarding_config.category,
        }
        if not self.config.enabled:
            return MemoryOnboardingResult(
                **base_result,
                accepted=False,
                status="disabled",
                reason="memory_platform_disabled",
            )
        if not onboarding_config.enabled and not force:
            return MemoryOnboardingResult(
                **base_result,
                accepted=False,
                status="disabled",
                reason="onboarding_disabled",
            )
        if onboarding_config.trigger_when_project_memory_empty and not force and self._has_workspace_onboarding_memory(onboarding_config.category):
            return MemoryOnboardingResult(
                **base_result,
                status="skipped",
                reason="workspace_memory_exists",
            )

        files = self._collect_onboarding_files(root)
        if not files:
            return MemoryOnboardingResult(
                **base_result,
                status="skipped",
                reason="no_onboarding_files",
            )

        content = self._render_onboarding_content(root=root, files=files, source=source)
        evidence_refs = tuple(dict.fromkeys((f"workspace:{root}", *(f"file:{item.relative_path}" for item in files))))
        if onboarding_config.review_first:
            review = self.review_queue.add_item(
                layer_id=onboarding_config.target_layer_id,
                store_id=onboarding_config.target_store_id,
                action="onboarding_bootstrap",
                content=content,
                category=onboarding_config.category,
                priority=onboarding_config.priority,
                confidence=onboarding_config.confidence,
                salience=onboarding_config.salience,
                evidence_refs=evidence_refs,
                rationale=(
                    "Serena-style workspace onboarding bootstrap queued for review; "
                    "approve only if the extracted project commands and architecture facts are durable."
                ),
            )
            return MemoryOnboardingResult(
                **base_result,
                status="review_queued",
                files=files,
                review_ids=(review.review_id,),
                stable_snapshot_refresh_recommended=True,
            )

        entry = self.create_entry(
            onboarding_config.target_store_id,
            content=content,
            category=onboarding_config.category,
            source_kind="onboarding_bootstrap",
            priority=onboarding_config.priority,
            confidence=onboarding_config.confidence,
            salience=onboarding_config.salience,
            evidence_refs=evidence_refs,
            thread_id=thread_id,
            source_ref=f"workspace:{root}",
            metadata={
                "onboarding": {
                    "workspace_path": str(root),
                    "source": source,
                    "files": [item.model_dump(mode="json", exclude={"content_preview"}) for item in files],
                    "stable_snapshot_refresh_recommended": True,
                }
            },
            write_policy="onboarding_bootstrap",
            write_reason="workspace onboarding bootstrap accepted without review",
        )
        return MemoryOnboardingResult(
            **base_result,
            status="written",
            files=files,
            written_memory_ids=(entry.memory_id or entry.entry_id,),
            stable_snapshot_refresh_recommended=True,
        )

    def _has_workspace_onboarding_memory(self, category: str) -> bool:
        project_categories = {
            "architecture",
            "build",
            "deployment",
            "project_context",
            "project_fact",
            "test",
            "workflow",
            "workspace_context",
            str(category or "").strip().lower(),
        }
        for entry in self.list_layer_entries("workspace"):
            if entry.status != "active":
                continue
            if str(entry.category or "").strip().lower() in project_categories:
                return True
        return False

    def _collect_onboarding_files(self, root: Path) -> tuple[MemoryOnboardingFile, ...]:
        config = self.config.onboarding
        selected: list[Path] = []
        seen: set[str] = set()
        for pattern in config.include_patterns:
            normalized_pattern = str(pattern or "").strip().replace("\\", "/")
            if not normalized_pattern:
                continue
            for path in root.glob(normalized_pattern):
                if len(selected) >= config.max_files:
                    break
                try:
                    resolved = path.resolve()
                    relative_path = resolved.relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
                key = relative_path.lower()
                if key in seen or not resolved.is_file() or resolved.is_symlink():
                    continue
                if self._onboarding_path_excluded(relative_path):
                    continue
                seen.add(key)
                selected.append(resolved)
            if len(selected) >= config.max_files:
                break

        files: list[MemoryOnboardingFile] = []
        total_chars = 0
        scrubber = MemorySecretScrubber()
        for path in selected:
            if total_chars >= config.max_total_chars:
                break
            try:
                relative_path = path.relative_to(root).as_posix()
                raw_text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeError, ValueError):
                continue
            scrubbed = scrubber.scrub(raw_text).text
            remaining = max(config.max_total_chars - total_chars, 0)
            included_limit = min(config.max_file_chars, remaining)
            if included_limit <= 0:
                break
            included = scrubbed[:included_limit]
            total_chars += len(included)
            files.append(
                MemoryOnboardingFile(
                    relative_path=relative_path,
                    kind=self._onboarding_file_kind(relative_path),
                    size_chars=len(scrubbed),
                    included_chars=len(included),
                    truncated=len(scrubbed) > len(included),
                    content_preview=_memory_onboarding_preview(included, limit=config.max_file_chars),
                )
            )
        return tuple(files)

    def _onboarding_path_excluded(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/")
        lower = normalized.lower()
        if lower.startswith(".") and lower not in {"agents.md"}:
            return True
        for raw_pattern in self.config.onboarding.exclude_patterns:
            pattern = str(raw_pattern or "").strip().replace("\\", "/").lower()
            if not pattern:
                continue
            if fnmatch(lower, pattern) or fnmatch("/" + lower, pattern):
                return True
        return False

    def _onboarding_file_kind(self, relative_path: str) -> str:
        lower = relative_path.lower()
        if lower == "agents.md":
            return "scoped_instructions"
        if lower.startswith("readme"):
            return "project_readme"
        if lower in {"pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"}:
            return "python_build_test"
        if lower in {"package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock"}:
            return "node_build_test"
        if lower in {"makefile", "justfile", "taskfile.yml"}:
            return "task_runner"
        if lower.startswith("docs/architecture/") or lower.startswith("docs/adr/"):
            return "architecture_doc"
        if lower.startswith("docs/guides/"):
            return "guide_doc"
        return "project_entry"

    def _render_onboarding_content(self, *, root: Path, files: tuple[MemoryOnboardingFile, ...], source: str) -> str:
        lines = [
            "Workspace onboarding bootstrap.",
            f"Workspace: {root}",
            f"Source: {source or 'ops'}",
            "Review this candidate before promoting it to durable workspace memory.",
            "After approval, start a new session or refresh the stable memory snapshot before relying on these facts.",
            "",
            "Files inspected:",
        ]
        for item in files:
            truncated = " (truncated)" if item.truncated else ""
            lines.extend(
                [
                    "",
                    f"## {item.relative_path} [{item.kind}]{truncated}",
                    item.content_preview,
                ]
            )
        return "\n".join(line for line in lines if line is not None).strip()

    def mark_thread_memory_polluted(
        self,
        *,
        thread_id: str,
        source_kind: str,
        source_id: str | None = None,
        tool_name: str | None = None,
        reason: str = "external source used",
        evidence_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryPollutionMarker:
        return self.pollution_store.mark(
            thread_id=thread_id,
            source_kind=source_kind,
            source_id=source_id,
            tool_name=tool_name,
            reason=reason,
            evidence_ref=evidence_ref,
            metadata=metadata,
        )

    def list_memory_pollution_markers(self, *, thread_id: str | None = None, limit: int = 100) -> tuple[MemoryPollutionMarker, ...]:
        return self.pollution_store.list_markers(thread_id=thread_id, limit=limit)

    def prefetch_recall(self, *, thread_id: str, query: str) -> RecallResult:
        self.provider_runtime.queue_prefetch(query=query, thread_id=thread_id)
        snapshot = self.get_or_create_session_snapshot(thread_id=thread_id)
        plan = self.recall_planner.build(
            query=query,
            thread_id=thread_id,
            stable_snapshot=snapshot.content,
        )
        self._record_recall_accesses(plan.curated_matches, plan.evidence)
        rendered_turn_block = RecallResult(
            thread_id=thread_id,
            query=query,
            snapshot_fingerprint=snapshot.fingerprint,
            stable_snapshot=plan.stable_snapshot,
            summary=plan.summary,
            curated_matches=plan.curated_matches,
            archive_hits=plan.archive_hits,
            provider_notes=plan.provider_notes,
            evidence=plan.evidence,
        ).render_turn_block()
        return RecallResult(
            thread_id=thread_id,
            query=query,
            snapshot_fingerprint=snapshot.fingerprint,
            stable_snapshot=plan.stable_snapshot,
            summary=plan.summary,
            curated_matches=plan.curated_matches,
            archive_hits=plan.archive_hits,
            provider_notes=plan.provider_notes,
            evidence=plan.evidence,
            actual_injection_tokens=self.token_budget.count_text(rendered_turn_block) if rendered_turn_block else 0,
            actual_injection_chars=len(rendered_turn_block),
        )

    def get_or_create_session_snapshot(self, *, thread_id: str, refresh: bool = False, reason: str = "first_run") -> MemorySessionSnapshot:
        provider_block = self.provider_runtime.system_prompt_block()
        return self.session_snapshot_store.get_or_create(
            thread_id=thread_id,
            content=self.render_stable_snapshot(),
            provider_block=provider_block,
            refresh=refresh,
            reason=reason,
        )

    def refresh_session_snapshot(self, *, thread_id: str, reason: str = "manual_refresh") -> MemorySessionSnapshot:
        return self.get_or_create_session_snapshot(thread_id=thread_id, refresh=True, reason=reason)

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
        return self.prompt_snapshot_store.record(
            thread_id=thread_id,
            snapshot_id=snapshot_id,
            prompt_hash=prompt_hash,
            prompt_text=prompt_text,
            skills_fingerprint=skills_fingerprint,
            memory_fingerprint=memory_fingerprint,
            config_fingerprint=config_fingerprint,
        )

    def search_archive(self, query: str, limit: int = 5) -> ArchiveSearchResult:
        archive = self.archive.search(query, limit=limit)
        provider_notes = self.provider_runtime.prefetch(query=query, thread_id="archive-search", archive=archive, curated_matches=())
        return archive.model_copy(update={"provider_notes": provider_notes})

    def get_session_memory(
        self,
        *,
        thread_id: str,
        memory_namespace: str | None = None,
        injected_memory_snapshot_id: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        recent_turns = self.archive.list_thread_turns(thread_id, limit=max(limit, 1))
        summaries = self.session_search_service.search(
            query="*",
            current_thread_id=thread_id,
            scope="current",
            limit=1,
            mode="recent",
        )
        return {
            "layer_id": "session",
            "thread_id": thread_id,
            "memory_namespace": memory_namespace,
            "injected_memory_snapshot_id": injected_memory_snapshot_id,
            "archive_turn_count": len(recent_turns),
            "recent_turns": [turn.model_dump(mode="json") for turn in recent_turns],
            "latest_prompt_snapshot": self.prompt_snapshot_store.latest_for_thread(thread_id),
            "session_summary": summaries[0].summary if summaries else "",
        }

    def search_sessions(
        self,
        *,
        query: str,
        current_thread_id: str | None = None,
        scope: str = "exclude_current",
        limit: int = 5,
        mode: str = "summarize",
    ) -> dict[str, Any]:
        normalized_scope = scope.strip().lower()
        summaries = self.session_search_service.search(
            query=query,
            current_thread_id=current_thread_id,
            scope=normalized_scope,
            limit=limit,
            mode=mode,
        )
        groups = []
        for summary in summaries:
            groups.append(
                {
                    "thread_id": summary.thread_id,
                    "summary": summary.summary,
                    "hits": [hit.model_dump(mode="json") for hit in summary.archive_hits],
                    "evidence": [item.model_dump(mode="json") for item in summary.evidence],
                    "hit_count": len(summary.archive_hits),
                    "excerpts": [hit.excerpt for hit in summary.archive_hits],
                    "latest_created_at": summary.archive_hits[0].created_at if summary.archive_hits else None,
                    "latest_prompt_snapshot": self.prompt_snapshot_store.latest_for_thread(summary.thread_id),
                }
            )
        return {
            "query": query,
            "scope": normalized_scope,
            "thread_id": current_thread_id,
            "groups": groups,
            "provider_notes": [],
            "current_thread_snapshot": self.prompt_snapshot_store.latest_for_thread(current_thread_id) if current_thread_id else None,
        }

    def list_traces(self, *, thread_id: str | None = None, target_id: str | None = None, limit: int = 20) -> tuple[MemoryTrace, ...]:
        return self.trace_store.list_traces(thread_id=thread_id, target_id=target_id, limit=limit)

    def recall_benchmark(
        self,
        *,
        cases: tuple[MemoryRecallBenchmarkCase, ...],
        suite_id: str = "ad_hoc",
        evidence_limit: int = 5,
    ) -> MemoryRecallBenchmarkReport:
        return run_recall_benchmark(
            suite_id=suite_id,
            cases=cases,
            evidence_limit=evidence_limit,
            recall=lambda thread_id, query: self.prefetch_recall(thread_id=thread_id, query=query),
        )

    def list_recall_benchmark_suites(self) -> tuple[MemoryRecallBenchmarkSuite, ...]:
        suites = self._load_recall_benchmark_suites()
        return tuple(sorted(suites, key=lambda item: (not item.enabled, item.name.lower(), item.suite_id)))

    def get_recall_benchmark_suite(self, suite_id: str) -> MemoryRecallBenchmarkSuite:
        normalized = _normalize_recall_suite_id(suite_id)
        for suite in self._load_recall_benchmark_suites():
            if suite.suite_id == normalized:
                return suite
        raise KeyError(suite_id)

    def upsert_recall_benchmark_suite(
        self,
        suite: MemoryRecallBenchmarkSuite,
        *,
        source: str = "ops",
    ) -> MemoryRecallBenchmarkSuite:
        normalized_id = _normalize_recall_suite_id(suite.suite_id or suite.name)
        now = utc_now()
        existing = {item.suite_id: item for item in self._load_recall_benchmark_suites()}
        previous = existing.get(normalized_id)
        normalized_cases = tuple(
            case.model_copy(
                update={
                    "case_id": _normalize_recall_suite_id(case.case_id, default=f"case-{index + 1}"),
                    "query": str(case.query or "").strip()[:500],
                    "thread_id": str(case.thread_id or "benchmark").strip()[:120] or "benchmark",
                    "expected_terms": _bounded_unique_strings(case.expected_terms, limit=20, max_chars=160),
                    "expected_memory_ids": _bounded_unique_strings(case.expected_memory_ids, limit=20, max_chars=160),
                    "expected_archive_thread_ids": _bounded_unique_strings(case.expected_archive_thread_ids, limit=20, max_chars=160),
                    "forbidden_terms": _bounded_unique_strings(case.forbidden_terms, limit=20, max_chars=160),
                    "forbidden_memory_ids": _bounded_unique_strings(case.forbidden_memory_ids, limit=20, max_chars=160),
                    "min_score": round(min(max(float(case.min_score), 0.0), 1.0), 4),
                }
            )
            for index, case in enumerate(suite.cases)
            if str(case.query or "").strip()
        )[:100]
        updated = suite.model_copy(
            update={
                "suite_id": normalized_id,
                "name": (suite.name or normalized_id).strip()[:160],
                "description": str(suite.description or "").strip()[:1000],
                "cases": normalized_cases,
                "tags": _bounded_unique_strings(suite.tags, limit=20, max_chars=80, lowercase=True),
                "source": (source or suite.source or "ops").strip()[:80],
                "created_at": previous.created_at if previous is not None else suite.created_at,
                "updated_at": now,
                "latest_run_id": previous.latest_run_id if previous is not None else suite.latest_run_id,
                "latest_score": previous.latest_score if previous is not None else suite.latest_score,
                "latest_passed": previous.latest_passed if previous is not None else suite.latest_passed,
                "latest_run_at": previous.latest_run_at if previous is not None else suite.latest_run_at,
            }
        )
        existing[normalized_id] = updated
        self._save_recall_benchmark_suites(tuple(existing.values()))
        return updated

    def delete_recall_benchmark_suite(self, suite_id: str) -> MemoryRecallBenchmarkSuite:
        normalized = _normalize_recall_suite_id(suite_id)
        suites = list(self._load_recall_benchmark_suites())
        for index, suite in enumerate(suites):
            if suite.suite_id != normalized:
                continue
            deleted = suites.pop(index)
            self._save_recall_benchmark_suites(tuple(suites))
            return deleted
        raise KeyError(suite_id)

    def run_recall_benchmark_suite(
        self,
        suite_id: str,
        *,
        evidence_limit: int = 5,
        source: str = "ops",
        record: bool = True,
    ) -> MemoryRecallBenchmarkRun:
        suite = self.get_recall_benchmark_suite(suite_id)
        if not suite.enabled:
            raise ValueError(f"recall benchmark suite '{suite.suite_id}' is disabled")
        report = self.recall_benchmark(
            suite_id=suite.suite_id,
            cases=suite.cases,
            evidence_limit=evidence_limit,
        )
        run = MemoryRecallBenchmarkRun(
            run_id=f"recall-benchmark-{uuid4().hex[:16]}",
            suite_id=suite.suite_id,
            suite_name=suite.name,
            source=(source or "ops").strip()[:80],
            report=report,
        )
        if record:
            self._record_recall_benchmark_run(run)
        return run

    def list_recall_benchmark_runs(
        self,
        *,
        suite_id: str | None = None,
        limit: int = 20,
    ) -> tuple[MemoryRecallBenchmarkRun, ...]:
        normalized_suite_id = _normalize_recall_suite_id(suite_id) if suite_id else None
        path = self._recall_benchmark_runs_path()
        if not path.exists():
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return ()
        items: list[MemoryRecallBenchmarkRun] = []
        for raw_item in raw_items:
            try:
                item = MemoryRecallBenchmarkRun.model_validate(raw_item)
            except Exception:
                continue
            if normalized_suite_id and item.suite_id != normalized_suite_id:
                continue
            items.append(item)
        items.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(items[: max(1, min(limit, 200))])

    def list_conflicts(self) -> tuple[MemoryConflict, ...]:
        return self.write_service.list_conflicts()

    def resolve_conflict(self, conflict_id: str, *, action: str = "keep_both") -> MemoryConflict:
        return self.write_service.resolve_conflict(conflict_id, action=action)

    def record_memory_access(self, memory_id: str, *, source: str = "recall") -> CuratedEntry | None:
        return self.write_service.record_access(memory_id, source=source)

    def govern_memory(
        self,
        memory_id: str,
        *,
        action: str,
        reason: str | None = None,
        source: str = "ops",
    ) -> MemoryGovernanceResult:
        target = self.write_service.find_entry(memory_id, include_inactive=True)
        if target is None:
            raise KeyError(memory_id)
        store_id, entry = target
        normalized = action.strip().lower().replace("-", "_")
        before = _retention_view_for_entry(entry)
        result_entry: CuratedEntry | None = None
        review_item: MemoryReviewItem | None = None
        message = ""

        if normalized in {"refresh", "touch", "access"}:
            if entry.status in {"superseded", "rejected", "archived"}:
                raise ValueError(f"memory '{memory_id}' is inactive and cannot be refreshed")
            result_entry = self.write_service.record_access(
                memory_id,
                source=f"governance:{source}:refresh",
            )
            message = "memory access metadata refreshed"
        elif normalized == "reinforce":
            if entry.status in {"superseded", "rejected", "archived"}:
                raise ValueError(f"memory '{memory_id}' is inactive and cannot be reinforced")
            touched = self.write_service.record_access(memory_id, source=f"governance:{source}:reinforce")
            if touched is None:
                raise KeyError(memory_id)
            result_entry = self.write_service.update_entry(
                store_id,
                touched.entry_id,
                salience=min(1.0, max(touched.salience, touched.salience + 0.08)),
                confidence=min(1.0, max(touched.confidence, touched.confidence + 0.03)),
                write_policy="governance_reinforce",
                write_reason=reason or "operator reinforced memory retention",
            )
            message = "memory reinforced and re-indexed"
        elif normalized == "archive":
            if entry.status == "archived":
                result_entry = entry
                message = "memory was already archived"
            else:
                result_entry = self.write_service.update_entry(
                    store_id,
                    entry.entry_id,
                    status="archived",
                    write_policy="governance_archive",
                    write_reason=reason or "operator archived stale memory",
                )
                message = "memory archived and removed from recall index"
        elif normalized in {"review", "send_to_review", "queue_review"}:
            if entry.status in {"rejected", "archived"}:
                raise ValueError(f"memory '{memory_id}' is inactive and cannot be queued for review")
            review_item = self.review_queue.add_item(
                layer_id=entry.layer_id or _layer_for_store_id(store_id),
                store_id=store_id,
                action="review_existing",
                content=entry.content,
                category=entry.category,
                priority=entry.priority,
                confidence=entry.confidence,
                salience=entry.salience,
                evidence_refs=tuple(dict.fromkeys((*entry.evidence_refs, entry.memory_id or entry.entry_id))),
                supersedes=(entry.memory_id or entry.entry_id,),
                conflicts_with=entry.conflicts_with,
                rationale=reason or "operator requested review of existing memory",
            )
            result_entry = entry
            message = "memory queued for review"
        else:
            raise ValueError(f"unsupported memory governance action '{action}'")

        after_entry = result_entry or entry
        return MemoryGovernanceResult(
            action=normalized,
            memory_id=after_entry.memory_id or after_entry.entry_id,
            store_id=after_entry.store_id,
            entry_id=after_entry.entry_id,
            status=after_entry.status,
            message=message,
            entry=after_entry,
            review_item=review_item,
            before_retention=before,
            after_retention=_retention_view_for_entry(after_entry),
        )

    def list_profile_facets(self) -> tuple[ProfileFacet, ...]:
        facets: list[ProfileFacet] = []
        for entry in self.curated_store_manager.list_entries("user_profile"):
            if entry.status in {"superseded", "rejected", "archived"}:
                continue
            facets.append(build_profile_facet(entry, policy=self._profile_facet_policy))
        facets.sort(key=lambda item: (item.class_id, -item.stability_score, item.key))
        return apply_profile_facet_budgets(tuple(facets), policy=self._profile_facet_policy)

    def profile_facet_policy(self) -> ProfileFacetPolicySnapshot:
        return self._profile_facet_policy.snapshot()

    def govern_profile_facet(
        self,
        facet_id: str,
        *,
        action: str,
        reason: str | None = None,
        source: str = "ops",
    ) -> ProfileFacetGovernanceResult:
        normalized = action.strip().lower().replace("-", "_")
        target = self._find_profile_facet_target(facet_id)
        if target is None:
            raise KeyError(facet_id)
        entry, before = target
        if normalized == "pin":
            user_state = "pinned"
            message = "profile facet pinned for prompt visibility"
        elif normalized == "unpin":
            user_state = "auto"
            message = "profile facet returned to automatic stability state"
        elif normalized in {"forget", "forgotten"}:
            user_state = "forgotten"
            message = "profile facet marked forgotten and removed from prompt visibility"
        elif normalized == "reset":
            user_state = "auto"
            message = "profile facet reset to automatic stability state"
        else:
            raise ValueError(f"unsupported profile facet action '{action}'")
        updated_metadata = apply_profile_user_state(entry, user_state)
        updated_entry = self.curated_store_manager.update_entry(
            "user_profile",
            entry.entry_id,
            metadata=updated_metadata,
            status=entry.status,
            write_policy=f"profile_facet_{normalized}",
            write_reason=reason or message,
        )
        self.write_service.retrieval_index.upsert_memory_entry(updated_entry)
        after = build_profile_facet(updated_entry, existing=before, policy=self._profile_facet_policy)
        audit = self._record_profile_facet_audit(
            ProfileFacetAuditEntry(
                audit_id=f"profile-facet-{uuid4().hex[:16]}",
                action=normalized,
                facet_id=after.facet_id,
                source_memory_id=after.source_memory_id,
                before_state=before.state,
                after_state=after.state,
                before_user_state=before.user_state,
                after_user_state=after.user_state,
                reason=reason or message,
                source=source,
            )
        )
        return ProfileFacetGovernanceResult(action=normalized, facet=after, message=message, audit_entry=audit)

    def rebuild_profile_facets(self, *, source: str = "ops") -> ProfileFacetRebuildResult:
        before = {facet.facet_id: facet for facet in self.list_profile_facets()}
        facets: list[ProfileFacet] = []
        updated_count = 0
        state = self.curated_store_manager._load_store("user_profile")  # noqa: SLF001 - manager owns curated store state.
        for entry in state.entries:
            if entry.status in {"superseded", "rejected", "archived"}:
                continue
            existing = before.get(build_profile_facet(entry, policy=self._profile_facet_policy).facet_id)
            facet = build_profile_facet(entry, existing=existing, policy=self._profile_facet_policy)
            metadata = dict(entry.metadata or {})
            profile_meta = dict(metadata.get("profile_facet") or {})
            next_profile_meta = {
                **profile_meta,
                "facet_id": facet.facet_id,
                "class_id": facet.class_id,
                "key": facet.key,
                "state": facet.state,
                "stability_score": facet.stability_score,
                "user_state": facet.user_state,
            }
            if metadata.get("profile_facet") != next_profile_meta:
                metadata["profile_facet"] = next_profile_meta
                entry.metadata = metadata
                entry.updated_at = utc_now()
                updated_count += 1
            facets.append(facet)
        self.curated_store_manager._save_store(state)  # noqa: SLF001 - manager owns curated store state.
        facets = list(apply_profile_facet_budgets(tuple(facets), policy=self._profile_facet_policy))
        audit = self._record_profile_facet_audit(
            ProfileFacetAuditEntry(
                audit_id=f"profile-facet-{uuid4().hex[:16]}",
                action="rebuild",
                facet_id="*",
                reason=f"rebuilt {len(facets)} profile facets",
                source=source,
            )
        )
        return ProfileFacetRebuildResult(
            source=source,
            facet_count=len(facets),
            updated_count=updated_count,
            facets=tuple(facets),
            audit_entry=audit,
        )

    def list_profile_facet_audit(self, *, limit: int = 50) -> tuple[ProfileFacetAuditEntry, ...]:
        path = self._profile_facet_audit_path()
        if not path.exists():
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return ()
        items: list[ProfileFacetAuditEntry] = []
        for raw_item in raw_items:
            try:
                items.append(ProfileFacetAuditEntry.model_validate(raw_item))
            except Exception:
                continue
        items.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(items[: max(1, min(limit, 200))])

    def plan_memory_governance(
        self,
        *,
        policy: str = "balanced",
        layer_id: str | None = None,
        limit: int = 20,
    ) -> MemoryGovernanceBatchResult:
        return self._memory_governance_batch(
            policy=policy,
            layer_id=layer_id,
            limit=limit,
            dry_run=True,
        )

    def execute_memory_governance(
        self,
        *,
        policy: str = "balanced",
        layer_id: str | None = None,
        limit: int = 20,
        source: str = "ops",
    ) -> MemoryGovernanceBatchResult:
        return self._memory_governance_batch(
            policy=policy,
            layer_id=layer_id,
            limit=limit,
            dry_run=False,
            source=source,
        )

    def _memory_governance_batch(
        self,
        *,
        policy: str,
        layer_id: str | None,
        limit: int,
        dry_run: bool,
        source: str = "ops",
    ) -> MemoryGovernanceBatchResult:
        normalized_policy = policy.strip().lower().replace("-", "_") or "balanced"
        normalized_layer = layer_id.strip().lower() if isinstance(layer_id, str) and layer_id.strip() else None
        max_items = max(1, min(limit, 100))
        plan_items = self._build_memory_governance_plan(
            policy=normalized_policy,
            layer_id=normalized_layer,
            limit=max_items,
        )
        results: list[MemoryGovernanceResult] = []
        errors: list[str] = []
        if not dry_run:
            for item in plan_items:
                try:
                    results.append(
                        self.govern_memory(
                            item.memory_id,
                            action=item.action,
                            reason=item.reason,
                            source=source,
                        )
                    )
                except Exception as exc:
                    errors.append(f"{item.memory_id}: {exc}")
        return MemoryGovernanceBatchResult(
            policy=normalized_policy,
            layer_id=normalized_layer,
            dry_run=dry_run,
            candidate_count=len(plan_items),
            executed_count=len(results),
            skipped_count=0 if not dry_run else len(plan_items),
            items=tuple(plan_items),
            results=tuple(results),
            errors=tuple(errors),
        )

    def _build_memory_governance_plan(
        self,
        *,
        policy: str,
        layer_id: str | None,
        limit: int,
    ) -> tuple[MemoryGovernancePlanItem, ...]:
        candidates: list[MemoryGovernancePlanItem] = []
        for stale in self.list_staleness():
            if layer_id is not None and stale.layer_id != layer_id:
                continue
            target = self.write_service.find_entry(stale.memory_id, include_inactive=False)
            if target is None:
                continue
            store_id, entry = target
            action = _memory_governance_action_for_stale(policy=policy, stale=stale, entry=entry)
            if action is None:
                continue
            candidates.append(
                MemoryGovernancePlanItem(
                    memory_id=stale.memory_id,
                    store_id=store_id,
                    entry_id=entry.entry_id,
                    layer_id=stale.layer_id,
                    action=action,
                    reason=_memory_governance_reason(policy=policy, stale=stale, entry=entry, action=action),
                    tier=stale.tier,
                    stale_score=stale.stale_score,
                    retention_score=stale.retention_score,
                    salience=stale.salience,
                    access_count=stale.access_count,
                    last_accessed_at=stale.last_accessed_at,
                    expires_at=entry.expires_at,
                )
            )
        candidates.sort(key=lambda item: (_action_priority(item.action), -item.stale_score, item.retention_score))
        return tuple(candidates[:limit])

    def run_maintenance(
        self,
        *,
        dry_run: bool | None = None,
        policy: str | None = None,
        layer_id: str | None = None,
        limit: int | None = None,
        source: str = "ops",
        run_reflection_due_jobs: bool | None = None,
    ) -> MemoryMaintenanceRun:
        started_at = utc_now()
        config = self.config.maintenance
        if not config.enabled:
            return MemoryMaintenanceRun(
                run_id=f"maintenance-{uuid4().hex[:16]}",
                status="disabled",
                dry_run=True if dry_run is None else bool(dry_run),
                policy=policy or config.policy,
                layer_id=layer_id if layer_id is not None else config.layer_id,
                source=source,
                errors=("memory maintenance is disabled by configuration",),
                started_at=started_at,
                finished_at=utc_now(),
            )
        normalized_policy = (policy or config.policy or "balanced").strip().lower().replace("-", "_")
        normalized_layer = (
            layer_id.strip().lower()
            if isinstance(layer_id, str) and layer_id.strip()
            else config.layer_id
        )
        requested_limit = max(1, min(int(limit if limit is not None else config.limit), 100))
        execute = bool(config.execute if dry_run is None else not dry_run)
        health_before = self.health_report() if config.include_health else None
        errors: list[str] = []
        pending_updates = self.update_queue.pending_count()
        drained = self.drain_update_queue() if execute else 0
        reflection_runs: list[ReflectionRunResult] = []
        should_run_reflections = (
            bool(run_reflection_due_jobs)
            if run_reflection_due_jobs is not None
            else bool(config.run_reflection_due_jobs)
        )
        due_reflection_jobs = self._due_reflection_jobs() if should_run_reflections else ()
        if should_run_reflections and execute:
            reflection_runs.extend(self._run_due_reflection_jobs())

        plan = self._build_memory_governance_plan(
            policy=normalized_policy,
            layer_id=normalized_layer,
            limit=requested_limit,
        )
        eligible, skipped = self._filter_maintenance_plan(plan, health_before=health_before)
        results: list[MemoryGovernanceResult] = []
        if execute:
            for item in eligible:
                try:
                    results.append(
                        self.govern_memory(
                            item.memory_id,
                            action=item.action,
                            reason=item.reason,
                            source=source,
                        )
                    )
                except Exception as exc:
                    errors.append(f"{item.memory_id}: {exc}")
        governance = MemoryGovernanceBatchResult(
            policy=normalized_policy,
            layer_id=normalized_layer,
            dry_run=not execute,
            candidate_count=len(eligible),
            executed_count=len(results),
            skipped_count=len(skipped),
            items=tuple(eligible),
            results=tuple(results),
            errors=tuple(errors),
        )
        health_after = self.health_report() if config.include_health else None
        status = "completed"
        if errors:
            status = "partial"
        if not execute and not eligible:
            status = "noop"
        elif execute and not results and not eligible:
            status = "noop"
        return MemoryMaintenanceRun(
            run_id=f"maintenance-{uuid4().hex[:16]}",
            status=status,
            dry_run=not execute,
            policy=normalized_policy,
            layer_id=normalized_layer,
            source=source,
            update_queue_pending=pending_updates,
            update_queue_drained=drained,
            reflection_jobs_due=len(due_reflection_jobs),
            reflection_jobs_run=len(reflection_runs),
            reflection_entries_written=sum(item.entries_written for item in reflection_runs),
            governance=governance,
            health_before=health_before,
            health_after=health_after,
            actions_executed=_count_actions(result.action for result in results),
            skipped_actions=_count_actions(item.action for item in skipped),
            errors=tuple(errors),
            started_at=started_at,
            finished_at=utc_now(),
        )

    def maintenance_automation_status(self) -> dict[str, object]:
        state = self._load_maintenance_automation_state()
        next_run = self._next_maintenance_automation_run_at(state=state)
        return {
            "enabled": bool(self.config.enabled and self.config.maintenance.enabled and self.config.maintenance.automation_enabled),
            "last_run_at": state.get("last_run_at"),
            "last_status": state.get("last_status"),
            "last_reason": state.get("last_reason"),
            "last_run_id": state.get("last_run_id"),
            "last_counts": state.get("last_counts") if isinstance(state.get("last_counts"), dict) else {},
            "last_error_count": int(state.get("last_error_count") or 0),
            "last_errors": state.get("last_errors") if isinstance(state.get("last_errors"), list) else [],
            "next_run_at": next_run.isoformat(),
            "tick_seconds": max(int(self.config.maintenance.tick_seconds), 10),
            "interval_seconds": max(int(self.config.maintenance.interval_seconds), 60),
            "min_idle_seconds": max(int(self.config.maintenance.min_idle_seconds), 0),
            "dry_run": not bool(self.config.maintenance.execute),
            "execute": bool(self.config.maintenance.execute),
            "policy": self.config.maintenance.policy,
            "layer_id": self.config.maintenance.layer_id,
            "limit": self.config.maintenance.limit,
            "run_reflection_due_jobs": bool(self.config.maintenance.run_reflection_due_jobs),
        }

    def run_maintenance_automation_if_due(self, *, force_run: bool = False) -> MemoryMaintenanceAutomationResult:
        if not self.config.enabled:
            return MemoryMaintenanceAutomationResult(ran=False, reason="memory_platform_disabled")
        if not self.config.maintenance.enabled:
            return MemoryMaintenanceAutomationResult(ran=False, reason="maintenance_disabled")
        if not self.config.maintenance.automation_enabled and not force_run:
            return MemoryMaintenanceAutomationResult(ran=False, reason="automation_disabled")
        state = self._load_maintenance_automation_state()
        next_run = self._next_maintenance_automation_run_at(state=state)
        now = utc_now()
        if not force_run and next_run > now:
            return MemoryMaintenanceAutomationResult(ran=False, reason="not_due", next_run_at=next_run)
        if not force_run and self._maintenance_activity_within_idle_window(state=state, now=now):
            return MemoryMaintenanceAutomationResult(ran=False, reason="not_idle", next_run_at=next_run)
        report = self.run_maintenance(
            dry_run=not bool(self.config.maintenance.execute),
            policy=self.config.maintenance.policy,
            layer_id=self.config.maintenance.layer_id,
            limit=self.config.maintenance.limit,
            source="automation",
            run_reflection_due_jobs=self.config.maintenance.run_reflection_due_jobs,
        )
        errors = list(report.errors)
        self._save_maintenance_automation_state(
            {
                "last_run_at": now.isoformat(),
                "last_status": report.status,
                "last_reason": "forced" if force_run else "due",
                "last_run_id": report.run_id,
                "last_counts": {
                    "update_queue_pending": report.update_queue_pending,
                    "update_queue_drained": report.update_queue_drained,
                    "reflection_jobs_due": report.reflection_jobs_due,
                    "reflection_jobs_run": report.reflection_jobs_run,
                    "reflection_entries_written": report.reflection_entries_written,
                    "governance_candidates": report.governance.candidate_count,
                    "governance_executed": report.governance.executed_count,
                    "governance_skipped": report.governance.skipped_count,
                },
                "last_error_count": len(errors),
                "last_errors": errors[:5],
            }
        )
        return MemoryMaintenanceAutomationResult(
            ran=True,
            reason="forced" if force_run else "due",
            next_run_at=self._next_maintenance_automation_run_at(state=self._load_maintenance_automation_state()),
            report=report,
        )

    def _maintenance_automation_state_path(self) -> Path:
        return self.state_root / "maintenance" / "automation.json"

    def _load_maintenance_automation_state(self) -> dict[str, object]:
        path = self._maintenance_automation_state_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_maintenance_automation_state(self, state: dict[str, object]) -> None:
        path = self._maintenance_automation_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _next_maintenance_automation_run_at(self, *, state: dict[str, object]) -> Any:
        last_run_at = self._parse_automation_datetime(state.get("last_run_at"))
        if last_run_at is None:
            return utc_now()
        return last_run_at + self._maintenance_interval_delta()

    def _maintenance_interval_delta(self):
        from datetime import timedelta

        return timedelta(seconds=max(int(self.config.maintenance.interval_seconds), 60))

    def _maintenance_activity_within_idle_window(self, *, state: dict[str, object], now: Any) -> bool:
        min_idle_seconds = max(int(self.config.maintenance.min_idle_seconds), 0)
        if min_idle_seconds <= 0:
            return False
        last_activity = self._parse_automation_datetime(state.get("last_run_at"))
        if last_activity is None:
            return False
        return (now - last_activity).total_seconds() < min_idle_seconds

    def _parse_automation_datetime(self, value: object):
        from datetime import datetime, timezone

        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    def _filter_maintenance_plan(
        self,
        plan: tuple[MemoryGovernancePlanItem, ...],
        *,
        health_before: MemoryHealthReport | None,
    ) -> tuple[tuple[MemoryGovernancePlanItem, ...], tuple[MemoryGovernancePlanItem, ...]]:
        config = self.config.maintenance
        if health_before is not None:
            if health_before.quality_score < config.min_quality_score_for_execute:
                return (), plan
            if health_before.pending_review_count > config.max_pending_review_for_execute:
                return (), plan
        counters: dict[str, int] = {}
        eligible: list[MemoryGovernancePlanItem] = []
        skipped: list[MemoryGovernancePlanItem] = []
        limits = {
            "archive": max(0, int(config.max_archive_per_run)),
            "review": max(0, int(config.max_review_per_run)),
            "reinforce": max(0, int(config.max_reinforce_per_run)),
            "refresh": max(0, int(config.max_reinforce_per_run)),
        }
        for item in plan:
            action = item.action
            current = counters.get(action, 0)
            action_limit = limits.get(action, len(plan))
            if current >= action_limit:
                skipped.append(item)
                continue
            counters[action] = current + 1
            eligible.append(item)
        return tuple(eligible), tuple(skipped)

    def _due_reflection_jobs(self) -> tuple[ReflectionJob, ...]:
        now = utc_now()
        return tuple(
            job
            for job in self.list_reflection_jobs()
            if job.enabled and job.next_run_at is not None and job.next_run_at <= now
        )

    def _run_due_reflection_jobs(self) -> tuple[ReflectionRunResult, ...]:
        results: list[ReflectionRunResult] = []
        for job in self._due_reflection_jobs():
            try:
                results.append(self.run_reflection_job(job.job_id))
            except Exception:
                continue
        return tuple(results)

    def list_retention(self) -> tuple[MemoryRetentionView, ...]:
        return self.write_service.list_retention()

    def list_staleness(self) -> tuple[MemoryStalenessView, ...]:
        return self.write_service.list_staleness()

    def list_review_items(self, *, status: str | None = "pending") -> tuple[MemoryReviewItem, ...]:
        return self.review_queue.list_items(status=status)

    def approve_review_item(self, review_id: str) -> CuratedEntry:
        item = self.review_queue.get(review_id)
        metadata = self._review_item_metadata(item)
        entry = self.create_entry(
            item.store_id,
            content=item.content,
            category=item.category,
            source_kind="review_approved",
            priority=item.priority,
            metadata=metadata,
            confidence=item.confidence,
            salience=item.salience,
            evidence_refs=item.evidence_refs,
            supersedes=item.supersedes,
            write_policy="review",
            write_reason=item.rationale,
        )
        self.review_queue.mark(review_id, "approved")
        return entry

    def reject_review_item(self, review_id: str) -> MemoryReviewItem:
        return self.review_queue.mark(review_id, "rejected")

    def batch_review(self, *, approve: tuple[str, ...] = (), reject: tuple[str, ...] = ()) -> dict[str, Any]:
        approved: list[str] = []
        rejected: list[str] = []
        errors: list[str] = []
        for review_id in approve:
            try:
                entry = self.approve_review_item(review_id)
                approved.append(entry.memory_id or entry.entry_id)
            except Exception as exc:
                errors.append(f"{review_id}: {exc}")
        for review_id in reject:
            try:
                item = self.reject_review_item(review_id)
                rejected.append(item.review_id)
            except Exception as exc:
                errors.append(f"{review_id}: {exc}")
        return {"approved": approved, "rejected": rejected, "errors": errors}

    def _record_recall_accesses(self, curated_matches: tuple[CuratedEntry, ...], evidence: tuple) -> None:
        seen: set[str] = set()
        for entry in curated_matches:
            memory_id = entry.memory_id or entry.entry_id
            if memory_id:
                seen.add(memory_id)
        for item in evidence:
            memory_id = getattr(item, "memory_id", None) or getattr(item, "source_id", None)
            if not memory_id:
                continue
            if getattr(item, "source_kind", "") not in {"memory", "curated_memory", "fts_memory", "provider_memory"}:
                continue
            seen.add(memory_id)
        for memory_id in seen:
            try:
                self.record_memory_access(memory_id, source="recall")
            except Exception:
                continue

    def on_session_end(
        self,
        *,
        thread_id: str,
        messages: list[dict[str, Any]] | None = None,
        reason: str = "session_end",
        allow_network: bool = True,
    ) -> MemoryFlushResult:
        self.drain_update_queue(thread_id=thread_id)
        result = self.flush_memory(thread_id=thread_id, messages=messages)
        if messages is None:
            messages = [
                {
                    "role": "user",
                    "content": turn.user_content,
                    "assistant_content": turn.assistant_content,
                    "status": turn.status,
                    "archive_id": turn.archive_id,
                }
                for turn in self.archive.list_thread_turns(thread_id, limit=12)
            ]
        self.provider_runtime.on_session_end(
            thread_id=thread_id,
            messages=messages,
            reason=reason,
            allow_network=allow_network,
        )
        return result

    def record_delegation_result(
        self,
        *,
        parent_thread_id: str,
        task: dict[str, Any],
        result: dict[str, Any],
        status: str,
    ) -> None:
        task_id = str(task.get("task_id") or task.get("job_id") or "delegation")
        prompt = str(task.get("prompt") or task.get("description") or task.get("task") or "")
        summary = str(result.get("summary") or result.get("error") or "")
        child_thread_id = str(result.get("child_thread_id") or task.get("child_thread_id") or "")
        record = self.archive.record_turn(
            parent_thread_id,
            f"[delegation:{task_id}] {prompt}",
            f"[subagent:{status}] {summary}",
            status,
        )
        self.automation_queue.submit("index_delegation_turn", lambda: self.write_service.index_archive_turn(record))
        self.update_queue.enqueue(record)
        if self._has_high_value_update_signal(
            user_content=record.user_content,
            assistant_content=record.assistant_content,
            status=record.status,
        ):
            self.drain_update_queue(thread_id=parent_thread_id, force=True)
        self.automation_queue.submit(
            "drain_delegation_memory_update_queue",
            lambda: self.drain_update_queue(thread_id=parent_thread_id, force=False),
        )
        self.automation_queue.submit(
            "provider_sync_delegation",
            lambda: self.provider_runtime.on_delegation(
            parent_thread_id=parent_thread_id,
            task={**task, "task_id": task_id, "child_thread_id": child_thread_id},
            result=result,
            status=status,
            ),
        )

    def export_admin(self) -> dict[str, Any]:
        return {
            "stores": {
                store.store_id: {
                    "view": store.model_dump(mode="json"),
                    "entries": [entry.model_dump(mode="json") for entry in self.list_entries(store.store_id)],
                }
                for store in self.list_stores()
            },
            "review_queue": [item.model_dump(mode="json") for item in self.review_queue.list_items(status=None)],
            "providers": [provider.model_dump(mode="json") for provider in self.list_providers()],
            "archive_turn_count": self.archive.count(),
        }

    def import_admin(self, payload: dict[str, Any]) -> dict[str, Any]:
        imported = 0
        reviewed = 0
        stores = payload.get("stores") if isinstance(payload, dict) else None
        if isinstance(stores, dict):
            for store_id, store_payload in stores.items():
                entries = store_payload.get("entries") if isinstance(store_payload, dict) else None
                if not isinstance(entries, list):
                    continue
                for raw_entry in entries:
                    if not isinstance(raw_entry, dict):
                        continue
                    try:
                        entry = CuratedEntry.model_validate(raw_entry)
                        self.create_entry(
                            str(store_id),
                            content=entry.content,
                            category=entry.category,
                            source_kind="admin_import",
                            priority=entry.priority,
                            confidence=entry.confidence,
                            salience=entry.salience,
                            evidence_refs=entry.evidence_refs,
                            supersedes=entry.supersedes,
                            write_policy="import",
                        )
                        imported += 1
                    except Exception:
                        reviewed += 1
        review_payload = payload.get("review_queue") if isinstance(payload, dict) else None
        if isinstance(review_payload, list):
            for raw_item in review_payload:
                if not isinstance(raw_item, dict):
                    continue
                try:
                    item = MemoryReviewItem.model_validate(raw_item)
                    self.review_queue.add_item(
                        layer_id=item.layer_id,
                        store_id=item.store_id,
                        action=item.action,
                        content=item.content,
                        category=item.category,
                        priority=item.priority,
                        confidence=item.confidence,
                        salience=item.salience,
                        evidence_refs=item.evidence_refs,
                        supersedes=item.supersedes,
                        conflicts_with=item.conflicts_with,
                        rationale=item.rationale,
                    )
                    reviewed += 1
                except Exception:
                    continue
        return {"entries_imported": imported, "review_items_created": reviewed}

    def audit_admin(self) -> dict[str, Any]:
        stale = self.list_staleness()
        conflicts = self.list_conflicts()
        review_items = self.list_review_items(status="pending")
        health = self.health_report()
        snapshots = {
            "stable_fingerprint": self.stable_snapshot_fingerprint(),
            "store_count": len(self.list_stores()),
        }
        return {
            "snapshot": snapshots,
            "pending_review_count": len(review_items),
            "conflict_count": len(conflicts),
            "staleness_count": len(stale),
            "health": health.model_dump(mode="json"),
            "providers": [provider.model_dump(mode="json") for provider in self.list_providers()],
            "candidate_audit": [entry.model_dump(mode="json") for entry in self.list_candidate_audit(limit=50)],
            "pollution_markers": [entry.model_dump(mode="json") for entry in self.list_memory_pollution_markers(limit=50)],
            "recall_benchmark_suites": [suite.model_dump(mode="json") for suite in self.list_recall_benchmark_suites()],
            "recall_benchmark_runs": [run.model_dump(mode="json") for run in self.list_recall_benchmark_runs(limit=20)],
        }

    def list_candidate_audit(self, *, limit: int = 50) -> tuple[MemoryCandidateAuditEntry, ...]:
        path = self._candidate_audit_path()
        if not path.exists():
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return ()
        items: list[MemoryCandidateAuditEntry] = []
        for raw_item in raw_items:
            try:
                items.append(MemoryCandidateAuditEntry.model_validate(raw_item))
            except Exception:
                continue
        items.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(items[: max(1, min(limit, 200))])

    def _record_candidate_audit(self, entry: MemoryCandidateAuditEntry) -> MemoryCandidateAuditEntry:
        path = self._candidate_audit_path()
        existing = list(self.list_candidate_audit(limit=200))
        existing.insert(0, entry)
        existing.sort(key=lambda item: item.created_at, reverse=True)
        payload = {"items": [item.model_dump(mode="json") for item in existing[:200]]}
        self._atomic_write_json(path, payload)
        return entry

    def _candidate_audit_path(self) -> Path:
        return self.state_root / "audit" / "candidate-decisions.json"

    def _find_profile_facet_target(self, facet_id: str) -> tuple[CuratedEntry, ProfileFacet] | None:
        normalized = str(facet_id or "").strip()
        if not normalized:
            return None
        for entry in self.curated_store_manager.list_entries("user_profile"):
            if entry.status in {"superseded", "rejected", "archived"}:
                continue
            facet = build_profile_facet(entry, policy=self._profile_facet_policy)
            if normalized in {facet.facet_id, facet.source_memory_id, facet.entry_id}:
                return entry, facet
        return None

    def _record_profile_facet_audit(self, entry: ProfileFacetAuditEntry) -> ProfileFacetAuditEntry:
        path = self._profile_facet_audit_path()
        existing = list(self.list_profile_facet_audit(limit=200))
        existing.insert(0, entry)
        existing.sort(key=lambda item: item.created_at, reverse=True)
        payload = {"items": [item.model_dump(mode="json") for item in existing[:200]]}
        self._atomic_write_json(path, payload)
        return entry

    def _profile_facet_audit_path(self) -> Path:
        return self.state_root / "audit" / "profile-facets.json"

    def _load_recall_benchmark_suites(self) -> tuple[MemoryRecallBenchmarkSuite, ...]:
        path = self._recall_benchmark_suites_path()
        if not path.exists():
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return ()
        suites: list[MemoryRecallBenchmarkSuite] = []
        for raw_item in raw_items:
            try:
                suites.append(MemoryRecallBenchmarkSuite.model_validate(raw_item))
            except Exception:
                continue
        return tuple(suites)

    def _save_recall_benchmark_suites(self, suites: tuple[MemoryRecallBenchmarkSuite, ...]) -> None:
        path = self._recall_benchmark_suites_path()
        payload = {
            "items": [
                item.model_dump(mode="json")
                for item in sorted(suites, key=lambda suite: suite.suite_id)
            ]
        }
        self._atomic_write_json(path, payload)

    def _record_recall_benchmark_run(self, run: MemoryRecallBenchmarkRun) -> None:
        existing = list(self.list_recall_benchmark_runs(limit=200))
        existing.insert(0, run)
        existing.sort(key=lambda item: item.created_at, reverse=True)
        self._atomic_write_json(
            self._recall_benchmark_runs_path(),
            {"items": [item.model_dump(mode="json") for item in existing[:200]]},
        )
        suites = {
            suite.suite_id: suite
            for suite in self._load_recall_benchmark_suites()
        }
        suite = suites.get(run.suite_id)
        if suite is not None:
            suites[run.suite_id] = suite.model_copy(
                update={
                    "latest_run_id": run.run_id,
                    "latest_score": run.report.score,
                    "latest_passed": run.report.passed,
                    "latest_run_at": run.created_at,
                    "updated_at": utc_now(),
                }
            )
            self._save_recall_benchmark_suites(tuple(suites.values()))

    def _recall_benchmark_suites_path(self) -> Path:
        return self.state_root / "benchmarks" / "recall-suites.json"

    def _recall_benchmark_runs_path(self) -> Path:
        return self.state_root / "benchmarks" / "recall-runs.json"

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.stem}.{uuid4().hex}{path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)

    def health_report(self) -> MemoryHealthReport:
        stores = self.list_stores()
        return build_memory_health_report(
            stores=stores,
            entries_by_store={store.store_id: self.list_entries(store.store_id) for store in stores},
            conflicts=self.list_conflicts(),
            staleness=self.list_staleness(),
            pending_review_count=len(self.list_review_items(status="pending")),
            archive_turn_count=self.archive.count(),
            providers=self.list_providers(),
        )

    def flush_memory(self, *, thread_id: str | None = None, messages: list[dict[str, Any]] | None = None) -> MemoryFlushResult:
        drained = self.drain_update_queue(thread_id=thread_id)
        if messages is None and thread_id is not None:
            messages = [
                {
                    "content": turn.user_content,
                    "assistant_content": turn.assistant_content,
                    "status": turn.status,
                    "evidence_ref": turn.archive_id,
                }
                for turn in self.archive.list_thread_turns(thread_id, limit=8)
            ]
        messages = messages or []
        written_ids: list[str] = []
        review_ids: list[str] = []
        candidate_audit: list[MemoryCandidateAuditEntry] = []
        errors: list[str] = []
        candidates_seen = 0
        facts_removed = 0
        skipped = 0
        for message in messages:
            existing_memory_context = self._render_memory_update_context()
            update = self.llm_update_service.extract_turn(
                user_content=str(message.get("content") or ""),
                assistant_content=str(message.get("assistant_content") or ""),
                status=str(message.get("status") or "completed"),
                evidence_ref=str(message.get("evidence_ref") or "") or None,
                existing_memory_context=existing_memory_context,
                signals=self.signal_detector.detect(
                    user_content=str(message.get("content") or ""),
                    assistant_content=str(message.get("assistant_content") or ""),
                    status=str(message.get("status") or "completed"),
                ).as_dict(),
            )
            source_context = self._source_context_for_memory_update(
                thread_id=str(message.get("thread_id") or "") or thread_id,
                evidence_refs=(str(message.get("evidence_ref") or ""),),
            )
            apply_result = self._apply_structured_update(update, source_context=source_context)
            candidates_seen += apply_result["candidates_seen"]
            facts_removed += apply_result["facts_removed"]
            skipped += int(apply_result.get("skipped_count") or 0)
            skipped += len(update.skipped)
            candidate_audit.extend(apply_result.get("candidate_audit") or ())
            errors.extend(update.skipped)
            if update.error:
                errors.append(update.error)
            written_ids.extend(apply_result["written_ids"])
            review_ids.extend(apply_result["review_ids"])
        if drained:
            skipped += 0
        return MemoryFlushResult(
            thread_id=thread_id,
            candidates_seen=candidates_seen,
            entries_written=len(written_ids),
            review_items_created=len(review_ids),
            entries_skipped=skipped,
            facts_removed=facts_removed,
            errors=tuple(errors),
            written_memory_ids=tuple(written_ids),
            review_ids=tuple(review_ids),
            candidate_audit=tuple(candidate_audit),
        )

    def drain_update_queue(self, *, thread_id: str | None = None, force: bool = True) -> int:
        return self.update_queue.drain(self._process_update_batch, thread_id=thread_id, force=force)

    def _process_update_batch(self, batch: MemoryUpdateBatch) -> None:
        for record in batch.turns:
            self._extract_curated_entries_from_turn(record)

    def _has_high_value_update_signal(self, *, user_content: str, assistant_content: str, status: str) -> bool:
        signals = self.signal_detector.detect(
            user_content=user_content,
            assistant_content=assistant_content,
            status=status,
        )
        return signals.correction or signals.reinforcement or signals.error or signals.retry or signals.resolved

    def clear_thread_runtime_artifacts(self, thread_id: str) -> dict[str, int]:
        return {
            "archive_turns_deleted": self.archive.delete_thread(thread_id),
            "prompt_snapshots_deleted": 1 if self.prompt_snapshot_store.delete_for_thread(thread_id) else 0,
            "session_snapshots_deleted": 1 if self.session_snapshot_store.delete(thread_id) else 0,
        }

    def list_reflection_jobs(self) -> tuple[ReflectionJob, ...]:
        return self.reflection_scheduler.list_jobs()

    def create_reflection_job(self, job: ReflectionJob) -> ReflectionJob:
        return self.reflection_scheduler.create_job(job)

    def pause_reflection_job(self, job_id: str) -> ReflectionJob:
        return self.reflection_scheduler.pause_job(job_id)

    def resume_reflection_job(self, job_id: str) -> ReflectionJob:
        return self.reflection_scheduler.resume_job(job_id)

    def remove_reflection_job(self, job_id: str) -> ReflectionJob:
        return self.reflection_scheduler.remove_job(job_id)

    def run_reflection_job(self, job_id: str) -> ReflectionRunResult:
        return self.reflection_scheduler.run_job(job_id)

    def on_pre_compact(self, messages: list[dict]) -> str:
        if not self.config.compaction_hooks.enabled:
            return ""
        self.drain_update_queue()
        self.flush_memory(messages=messages)
        notes: list[str] = []
        if self.config.compaction_hooks.include_archive:
            recent = [message.get("content", "") for message in messages[-3:] if isinstance(message.get("content"), str)]
            if recent:
                notes.append("Recent context before compaction: " + " | ".join(item[:120] for item in recent))
        if self.config.compaction_hooks.include_provider_notes:
            notes.extend(self.provider_runtime.on_pre_compact(messages))
        return "\n".join(notes)

    def shutdown(self) -> None:
        self.drain_update_queue()
        self.automation_queue.close()
        self.reflection_scheduler.stop()
        self.provider_runtime.shutdown(allow_network=False)
        self.retrieval_index.close()
        self.trace_store.close()
        self.archive.close()

    def flush_automation(self) -> None:
        self.automation_queue.flush()

    def _extract_curated_entries_from_turn(self, record) -> None:
        update = self.llm_update_service.extract_turn(
            user_content=record.user_content,
            assistant_content=record.assistant_content,
            status=record.status,
            evidence_ref=record.archive_id,
            existing_memory_context=self._render_memory_update_context(),
            signals=self.signal_detector.detect(
                user_content=record.user_content,
                assistant_content=record.assistant_content,
                status=record.status,
            ).as_dict(),
        )
        self._apply_structured_update(
            update,
            source_context=self._source_context_for_memory_update(
                thread_id=record.thread_id,
                evidence_refs=(record.archive_id,),
            ),
        )

    def _render_memory_update_context(self) -> str:
        lines: list[str] = []
        for store in self.list_stores():
            for entry in self.list_entries(store.store_id):
                if entry.status in {"superseded", "rejected", "archived"}:
                    continue
                memory_id = entry.memory_id or entry.entry_id
                score = self.resolution_service.effective_score(entry)
                content = self.token_budget.truncate_text(entry.content, max_tokens=120)
                lines.append(
                    " ".join(
                        (
                            f"- id={memory_id}",
                            f"entry_id={entry.entry_id}",
                            f"store={entry.store_id}",
                            f"layer={entry.layer_id or _layer_for_store_id(entry.store_id)}",
                            f"category={entry.category}",
                            f"status={entry.status}",
                            f"confidence={entry.confidence:.2f}",
                            f"score={score:.3f}",
                            f"content={content}",
                        )
                    )
                )
        if not lines:
            return ""
        lines.sort()
        return self.token_budget.truncate_text(
            "\n".join(lines),
            max_tokens=max(self.config.updater.max_input_tokens // 3, 500),
        )

    def _apply_structured_update(
        self,
        update: StructuredMemoryUpdate,
        *,
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_context = source_context or {}
        source_polluted = bool(source_context.get("source_polluted"))
        if not source_polluted:
            if update.user_summary_sections:
                self.curated_store_manager.update_summary_sections("user_profile", update.user_summary_sections)
            elif update.user_summary:
                self.curated_store_manager.update_summary("user_profile", update.user_summary)
        if update.history_summary_sections:
            self.curated_store_manager.update_summary_sections("runtime_memory", update.history_summary_sections)
        elif update.history_summary:
            self.curated_store_manager.update_summary("runtime_memory", update.history_summary)
        facts_removed = self._mark_removed_memories(update.facts_to_remove)
        written_ids: list[str] = []
        review_ids: list[str] = []
        candidate_audit: list[MemoryCandidateAuditEntry] = []
        skipped_count = 0
        active_entries = self._active_curated_entries()
        for candidate in update.candidates:
            result, audit = self._apply_candidate(
                candidate,
                active_entries=tuple(active_entries),
                source_context=source_context,
            )
            candidate_audit.append(audit)
            if isinstance(result, CuratedEntry):
                written_ids.append(result.memory_id or result.entry_id)
                active_entries.append(result)
            elif isinstance(result, MemoryReviewItem):
                review_ids.append(result.review_id)
            else:
                skipped_count += 1
        return {
            "candidates_seen": len(update.candidates),
            "facts_removed": facts_removed,
            "written_ids": written_ids,
            "review_ids": review_ids,
            "skipped_count": skipped_count,
            "candidate_audit": candidate_audit,
        }

    def _apply_candidate(
        self,
        candidate: MemoryCandidate,
        *,
        active_entries: tuple[CuratedEntry, ...] | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> tuple[CuratedEntry | MemoryReviewItem | None, MemoryCandidateAuditEntry]:
        source_context = source_context or {}
        store_id = self.store_id_for_layer(candidate.layer_id)
        if store_id is None:
            audit = self._build_candidate_audit(
                candidate,
                action="skip",
                reason="unknown layer/store",
                store_id=None,
                source_context=source_context,
            )
            return None, self._record_candidate_audit(audit)
        source_polluted = self._candidate_source_polluted(candidate, source_context=source_context)
        quality = memory_candidate_quality(candidate)
        if quality["decision"] == "skip":
            audit = self._build_candidate_audit(
                candidate,
                action="skip",
                reason="quality gate skipped candidate",
                store_id=store_id,
                quality=quality,
                source_context=source_context,
            )
            return None, self._record_candidate_audit(audit)
        existing_entries = tuple(
            entry
            for entry in self.list_entries(store_id)
            if entry.status not in {"superseded", "rejected", "archived"}
        )
        duplicate_entries = active_entries if active_entries is not None else self._active_curated_entries()
        duplicate_decision = self.memory_guard.evaluate_write(
            layer_id=candidate.layer_id,
            action="add",
            content=candidate.content,
            existing_entries=duplicate_entries,
        )
        if duplicate_decision.error_code in {"duplicate_entry", "near_duplicate_entry"}:
            audit = self._build_candidate_audit(
                candidate,
                action="skip",
                reason=duplicate_decision.reason or duplicate_decision.error_code or "duplicate memory candidate",
                store_id=store_id,
                quality=quality,
                target_id=duplicate_decision.duplicate_of,
                conflicts=duplicate_decision.near_duplicates,
                source_context=source_context,
            )
            return None, self._record_candidate_audit(audit)
        conflicts = self.memory_guard.detect_conflicts(
            candidate_content=candidate.content,
            existing_entries=existing_entries,
        )
        explicit_supersedes = tuple(dict.fromkeys(candidate.supersedes))
        can_auto_supersede = self.resolution_service.should_auto_supersede(
            confidence=candidate.confidence,
            supersedes=explicit_supersedes,
            conflicts_with=conflicts,
        )
        candidate_supersedes = tuple(dict.fromkeys((*explicit_supersedes, *conflicts))) if can_auto_supersede else ()
        review_supersedes = candidate_supersedes or explicit_supersedes
        supersede_requires_review = bool(explicit_supersedes or conflicts) and not can_auto_supersede
        if (
            not candidate.review_required
            and quality["decision"] == "write"
            and not source_polluted
            and not supersede_requires_review
            and self.resolution_service.should_auto_accept(
                confidence=candidate.confidence,
                evidence_refs=candidate.evidence_refs,
            )
            and (not conflicts or candidate_supersedes)
        ):
            try:
                entry = self.create_entry(
                    store_id,
                    content=candidate.content,
                    category=candidate.category,
                    source_kind="turn_sync",
                    priority=candidate.priority,
                    confidence=candidate.confidence,
                    salience=candidate.salience,
                    metadata=self._candidate_metadata(candidate, source_context=source_context),
                    thread_id=str(source_context.get("thread_id") or "") or None,
                    evidence_refs=candidate.evidence_refs,
                    supersedes=candidate_supersedes,
                    write_policy="auto_extract",
                    write_reason=_rationale_with_redactions(
                        f"{candidate.rationale}; quality={quality['quality_score']:.2f}",
                        candidate.redacted_rules,
                    ),
                )
                audit = self._build_candidate_audit(
                    candidate,
                    action="write",
                    reason="quality and resolution gates accepted candidate",
                    store_id=store_id,
                    quality=quality,
                    target_id=entry.memory_id or entry.entry_id,
                    supersedes=candidate_supersedes,
                    conflicts=conflicts,
                    source_context=source_context,
                )
                return entry, self._record_candidate_audit(audit)
            except ValueError as exc:
                item = self.review_queue.add_item(
                    layer_id=candidate.layer_id,
                    store_id=store_id,
                    action="add",
                    content=candidate.content,
                    category=candidate.category,
                    priority=candidate.priority,
                    confidence=candidate.confidence,
                    salience=candidate.salience,
                    evidence_refs=candidate.evidence_refs,
                    supersedes=review_supersedes,
                    conflicts_with=conflicts,
                    rationale=_rationale_with_redactions(
                        f"{candidate.rationale}; guard requires review: {exc}",
                        candidate.redacted_rules,
                    ),
                )
                audit = self._build_candidate_audit(
                    candidate,
                    action="review",
                    reason=f"guard requires review: {exc}",
                    store_id=store_id,
                    quality=quality,
                    target_id=item.review_id,
                    supersedes=review_supersedes,
                    conflicts=conflicts,
                    source_context=source_context,
                )
                return item, self._record_candidate_audit(audit)
        if quality["decision"] != "review":
            if source_polluted and store_id == "user_profile":
                item = self.review_queue.add_item(
                    layer_id=candidate.layer_id,
                    store_id=store_id,
                    action="add",
                    content=candidate.content,
                    category=candidate.category,
                    priority=candidate.priority,
                    confidence=candidate.confidence,
                    salience=candidate.salience,
                    evidence_refs=candidate.evidence_refs,
                    supersedes=review_supersedes,
                    conflicts_with=conflicts,
                    rationale=_rationale_with_redactions(
                        f"{candidate.rationale}; pollution_guard=requires_review; reasons={','.join(self._pollution_reasons(source_context)) or 'external_source'}",
                        candidate.redacted_rules,
                    ),
                )
                audit = self._build_candidate_audit(
                    candidate,
                    action="review",
                    reason="pollution guard requires review before active profile promotion",
                    store_id=store_id,
                    quality={**quality, "decision": "review", "blockers": [*quality.get("blockers", ()), "source_polluted"]},
                    target_id=item.review_id,
                    supersedes=review_supersedes,
                    conflicts=conflicts,
                    source_context=source_context,
                )
                return item, self._record_candidate_audit(audit)
            audit = self._build_candidate_audit(
                candidate,
                action="skip",
                reason="candidate did not satisfy write or review gates",
                store_id=store_id,
                quality=quality,
                supersedes=review_supersedes,
                conflicts=conflicts,
                source_context=source_context,
            )
            return None, self._record_candidate_audit(audit)
        item = self.review_queue.add_item(
            layer_id=candidate.layer_id,
            store_id=store_id,
            action="add",
            content=candidate.content,
            category=candidate.category,
            priority=candidate.priority,
            confidence=candidate.confidence,
            salience=candidate.salience,
            evidence_refs=candidate.evidence_refs,
            supersedes=review_supersedes,
            conflicts_with=conflicts,
            rationale=_rationale_with_redactions(
                f"{candidate.rationale}; quality={quality['quality_score']:.2f}; blockers={','.join(quality['blockers']) or 'none'}",
                candidate.redacted_rules,
            ),
        )
        audit = self._build_candidate_audit(
            candidate,
            action="review",
            reason="quality gate requires review",
            store_id=store_id,
            quality=quality,
            target_id=item.review_id,
            supersedes=review_supersedes,
            conflicts=conflicts,
            source_context=source_context,
        )
        return item, self._record_candidate_audit(audit)

    def _build_candidate_audit(
        self,
        candidate: MemoryCandidate,
        *,
        action: str,
        reason: str,
        store_id: str | None,
        quality: dict[str, Any] | None = None,
        target_id: str | None = None,
        supersedes: tuple[str, ...] | None = None,
        conflicts: tuple[str, ...] | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> MemoryCandidateAuditEntry:
        quality = quality or memory_candidate_quality(candidate)
        source_context = source_context or {}
        source_polluted = self._candidate_source_polluted(candidate, source_context=source_context)
        pollution_reasons = self._pollution_reasons(source_context) if source_polluted else ()
        return MemoryCandidateAuditEntry(
            audit_id=f"candidate-{uuid4().hex[:16]}",
            action=action,
            reason=reason,
            layer_id=candidate.layer_id,
            store_id=store_id,
            category=candidate.category,
            candidate_preview=_memory_candidate_preview(candidate.content),
            quality_score=float(quality.get("quality_score") or 0.0),
            quality_decision=str(quality.get("decision") or "unknown"),
            blockers=tuple(str(item) for item in (quality.get("blockers") or ())),
            confidence=candidate.confidence,
            salience=candidate.salience,
            priority=candidate.priority,
            evidence_count=len(candidate.evidence_refs),
            evidence_refs=candidate.evidence_refs[:5],
            source_thread_id=str(source_context.get("thread_id") or "") or None,
            source_polluted=source_polluted,
            pollution_reasons=pollution_reasons,
            target_id=target_id,
            supersedes=tuple(dict.fromkeys(supersedes or candidate.supersedes)),
            conflicts_with=tuple(dict.fromkeys(conflicts or ())),
        )

    def _active_curated_entries(self) -> list[CuratedEntry]:
        entries: list[CuratedEntry] = []
        for store in self.list_stores():
            entries.extend(
                entry
                for entry in self.list_entries(store.store_id)
                if entry.status not in {"superseded", "rejected", "archived"}
            )
        return entries

    def _source_context_for_memory_update(
        self,
        *,
        thread_id: str | None,
        evidence_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        normalized_thread_id = str(thread_id or "").strip()
        evidence = tuple(str(item or "").strip() for item in evidence_refs if str(item or "").strip())
        marker = self.pollution_store.first_match(
            thread_id=normalized_thread_id or None,
            evidence_refs=evidence,
        )
        reasons = (marker.reason,) if marker is not None and marker.reason else ()
        return {
            "thread_id": normalized_thread_id or None,
            "evidence_refs": evidence,
            "source_polluted": marker is not None,
            "pollution_reasons": reasons,
            "pollution_marker_ids": (marker.marker_id,) if marker is not None else (),
            "pollution_source_kinds": (marker.source_kind,) if marker is not None else (),
            "pollution_tool_names": (marker.tool_name,) if marker is not None and marker.tool_name else (),
        }

    def _pollution_markers_from_source_metadata(
        self,
        *,
        thread_id: str,
        evidence_ref: str,
        source_metadata: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], ...]:
        if not isinstance(source_metadata, dict):
            return ()
        markers: list[dict[str, Any]] = []
        for raw_item in source_metadata.get("pollution_markers") or ():
            if not isinstance(raw_item, dict):
                continue
            reason = str(raw_item.get("reason") or "").strip()
            source_kind = str(raw_item.get("source_kind") or raw_item.get("source") or "external").strip().lower()
            if not reason:
                reason = "external source used"
            markers.append(
                {
                    "thread_id": thread_id,
                    "source_kind": source_kind or "external",
                    "source_id": str(raw_item.get("source_id") or "").strip() or None,
                    "tool_name": str(raw_item.get("tool_name") or raw_item.get("name") or "").strip() or None,
                    "reason": reason,
                    "evidence_ref": evidence_ref,
                    "metadata": {
                        key: value
                        for key, value in raw_item.items()
                        if key not in {"reason", "source_kind", "source", "source_id", "tool_name", "name"}
                    },
                }
            )
        return tuple(markers[:20])

    def _candidate_source_polluted(self, candidate: MemoryCandidate, *, source_context: dict[str, Any]) -> bool:
        if candidate.layer_id != "user":
            return False
        if bool(source_context.get("source_polluted")):
            return True
        return self.pollution_store.has_pollution(
            thread_id=str(source_context.get("thread_id") or "").strip() or None,
            evidence_refs=candidate.evidence_refs,
        )

    def _pollution_reasons(self, source_context: dict[str, Any]) -> tuple[str, ...]:
        raw_values = source_context.get("pollution_reasons") or ()
        reasons: list[str] = []
        for raw_value in raw_values:
            text = str(raw_value or "").strip()
            if text and text not in reasons:
                reasons.append(text[:160])
        return tuple(reasons[:5])

    def _candidate_metadata(self, candidate: MemoryCandidate, *, source_context: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if not self._candidate_source_polluted(candidate, source_context=source_context):
            return metadata
        reasons = self._pollution_reasons(source_context)
        metadata["source_polluted"] = True
        metadata["pollution_reasons"] = list(reasons)
        metadata["pollution_marker_ids"] = list(source_context.get("pollution_marker_ids") or ())
        metadata["profile_facet"] = {
            "source_polluted": True,
            "pollution_reasons": list(reasons),
        }
        return metadata

    def _review_item_metadata(self, item: MemoryReviewItem) -> dict[str, Any]:
        if item.store_id != "user_profile":
            return {}
        marker = self.pollution_store.first_match(evidence_refs=item.evidence_refs)
        if marker is None:
            return {}
        return {
            "source_polluted": True,
            "pollution_reasons": [marker.reason] if marker.reason else [],
            "pollution_marker_ids": [marker.marker_id],
            "profile_facet": {
                "source_polluted": True,
                "pollution_reasons": [marker.reason] if marker.reason else [],
            },
        }

    def _mark_removed_memories(self, memory_ids: tuple[str, ...]) -> int:
        if not memory_ids:
            return 0
        targets = set(memory_ids)
        updated = 0
        for store in self.list_stores():
            for entry in self.list_entries(store.store_id):
                if entry.entry_id not in targets and (entry.memory_id or "") not in targets:
                    continue
                if entry.status == "superseded":
                    continue
                try:
                    self._mark_superseded(store_id=store.store_id, entry_id_or_memory_id=entry.entry_id)
                    updated += 1
                except Exception:
                    continue
        return updated

    def _mark_superseded(self, *, store_id: str, entry_id_or_memory_id: str) -> None:
        for entry in self.list_entries(store_id):
            if entry.entry_id != entry_id_or_memory_id and entry.memory_id != entry_id_or_memory_id:
                continue
            self.curated_store_manager.update_entry(
                store_id,
                entry.entry_id,
                status="superseded",
                write_policy="supersede",
                write_reason="superseded by structured memory update",
            )
            self.retrieval_index.delete_memory_entry(entry.memory_id or entry.entry_id)
            return

    def _migrate_legacy_store(self, legacy_store_path: str | Path | None) -> None:
        if legacy_store_path is None:
            return
        path = Path(legacy_store_path).expanduser().resolve()
        if not path.exists():
            return
        legacy_store = FileMemoryStore(path)
        for namespace in legacy_store.list_namespaces():
            state = legacy_store.load(namespace)
            self._import_legacy_state(state)

    def _import_legacy_state(self, state: MemoryState) -> None:
        if state.summary.summary:
            self._create_migration_entry(
                "runtime_memory",
                content=f"[legacy-summary:{state.namespace}] {state.summary.summary}",
                category="legacy_summary",
                source_kind="migration",
                priority=0.4,
            )
        for fact in state.facts:
            self._create_migration_entry(
                "runtime_memory",
                content=f"[legacy-fact:{state.namespace}] {fact.content}",
                category=fact.category,
                source_kind="migration",
                priority=fact.confidence,
            )

    def _create_migration_entry(
        self,
        store_id: str,
        *,
        content: str,
        category: str,
        source_kind: str,
        priority: float,
    ) -> None:
        existing_entries = self.list_entries(store_id)
        if any(entry.content.strip() == content.strip() for entry in existing_entries):
            return
        decision = self.memory_guard.evaluate_write(
            layer_id="user" if store_id == "user_profile" else "workspace",
            action="add",
            content=content,
            existing_entries=existing_entries,
        )
        if not decision.allowed:
            return
        self.create_entry(
            store_id,
            content=content,
            category=category,
            source_kind=source_kind,
            priority=priority,
        )


def _rationale_with_redactions(rationale: str, redacted_rules: tuple[str, ...]) -> str:
    if not redacted_rules:
        return rationale
    return "; ".join(
        part
        for part in (
            rationale,
            "redacted memory secrets: " + ", ".join(redacted_rules),
        )
        if part
    )
