from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from .contracts import (
    ArchiveTurnRecord,
    CuratedEntry,
    MemoryConflict,
    MemoryPolicyDecision,
    MemoryRetentionView,
    MemoryStalenessView,
    MemoryTrace,
    MemoryWriteEvent,
    utc_now,
)
from .curated import CuratedStoreManager
from .guard import MemoryGuard
from .provider_runtime import ProviderRuntime
from .retention import retention_metrics
from .retrieval_index import RetrievalIndexStore
from .trace import MemoryTraceStore


def _layer_for_store(store_id: str) -> str:
    if store_id == "user_profile":
        return "user"
    return "workspace"


class MemoryWriteService:
    def __init__(
        self,
        *,
        curated_store_manager: CuratedStoreManager,
        guard: MemoryGuard,
        retrieval_index: RetrievalIndexStore,
        provider_runtime: ProviderRuntime,
        trace_store: MemoryTraceStore,
    ) -> None:
        self.curated_store_manager = curated_store_manager
        self.guard = guard
        self.retrieval_index = retrieval_index
        self.provider_runtime = provider_runtime
        self.trace_store = trace_store

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
        user_id: str | None = None,
        workspace_id: str | None = None,
        source_ref: str | None = None,
        confidence: float = 0.5,
        salience: float = 0.5,
        evidence_refs: tuple[str, ...] = (),
        supersedes: tuple[str, ...] = (),
        status: str = "active",
        write_policy: str = "manual",
        write_reason: str | None = None,
    ) -> CuratedEntry:
        layer_id = _layer_for_store(store_id)
        existing_entries = self.curated_store_manager.list_entries(store_id)
        supersede_ids = set(supersedes)
        guard_entries = tuple(
            entry
            for entry in existing_entries
            if entry.entry_id not in supersede_ids and (entry.memory_id or "") not in supersede_ids
        )
        decision = self.guard.evaluate_write(
            layer_id=layer_id,
            action="add",
            content=content,
            existing_entries=guard_entries,
        )
        if not decision.allowed:
            raise ValueError(decision.reason)
        sanitized_content = decision.sanitized_content or content
        conflicts = self.guard.detect_conflicts(candidate_content=sanitized_content, existing_entries=guard_entries)
        now = utc_now()
        entry_metadata = dict(metadata or {})
        if decision.matched_rules:
            entry_metadata["redacted_rules"] = list(decision.matched_rules)
        entry = self.curated_store_manager.create_entry(
            store_id,
            content=sanitized_content,
            category=category,
            source_kind=source_kind,
            priority=priority,
            metadata=entry_metadata,
            memory_id=f"memory-{uuid4().hex[:16]}",
            layer_id=layer_id,
            thread_id=thread_id,
            user_id=user_id,
            workspace_id=workspace_id,
            source_ref=source_ref,
            confidence=confidence,
            salience=salience,
            last_accessed_at=now,
            evidence_refs=evidence_refs,
            supersedes=supersedes,
            conflicts_with=conflicts,
            expires_at=(now + timedelta(days=30)) if source_kind == "reflection" else None,
            status=status,
            write_policy=write_policy,
            write_reason=write_reason or decision.reason,
        )
        for superseded_id in supersedes:
            self._mark_superseded(store_id=store_id, entry_id_or_memory_id=superseded_id)
        self.retrieval_index.upsert_memory_entry(entry)
        self.provider_runtime.index_write(entry=entry)
        self.provider_runtime.on_memory_write(
            MemoryWriteEvent(
                action="add",
                store_id=entry.store_id,
                content=entry.content,
                category=entry.category,
                thread_id=entry.thread_id,
                metadata=entry.metadata,
            )
        )
        self.trace_store.record(
            MemoryTrace(
                trace_id=f"trace-{uuid4().hex[:16]}",
                thread_id=thread_id,
                trace_kind="write",
                target_id=entry.memory_id or entry.entry_id,
                provider_notes=(),
                evidence=(),
            )
        )
        return entry

    def update_entry(
        self,
        store_id: str,
        entry_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        priority: float | None = None,
        metadata: dict | None = None,
        confidence: float | None = None,
        salience: float | None = None,
        evidence_refs: tuple[str, ...] | None = None,
        supersedes: tuple[str, ...] | None = None,
        status: str | None = None,
        write_policy: str | None = None,
        write_reason: str | None = None,
    ) -> CuratedEntry:
        existing_entries = self.curated_store_manager.list_entries(store_id)
        current = next((entry for entry in existing_entries if entry.entry_id == entry_id), None)
        if current is None:
            raise KeyError(entry_id)
        next_content = content if content is not None else current.content
        layer_id = current.layer_id or _layer_for_store(store_id)
        decision = self.guard.evaluate_write(
            layer_id=layer_id,
            action="replace",
            content=next_content,
            existing_entries=existing_entries,
            current_entry_id=entry_id,
        )
        if not decision.allowed:
            raise ValueError(decision.reason)
        sanitized_content = decision.sanitized_content or next_content
        conflicts = self.guard.detect_conflicts(
            candidate_content=sanitized_content,
            existing_entries=existing_entries,
            current_entry_id=entry_id,
        )
        updated = self.curated_store_manager.update_entry(
            store_id,
            entry_id,
            content=sanitized_content if content is not None else None,
            category=category,
            priority=priority,
            metadata=metadata,
            confidence=confidence,
            salience=salience,
            last_accessed_at=utc_now(),
            evidence_refs=evidence_refs,
            supersedes=supersedes if supersedes is not None else current.supersedes,
            conflicts_with=conflicts,
            status=status,
            write_policy=write_policy,
            write_reason=write_reason or decision.reason,
        )
        for superseded_id in supersedes or ():
            self._mark_superseded(store_id=store_id, entry_id_or_memory_id=superseded_id)
        if updated.status in {"superseded", "rejected", "archived"}:
            self.retrieval_index.delete_memory_entry(updated.memory_id or updated.entry_id)
        else:
            self.retrieval_index.upsert_memory_entry(updated)
            self.provider_runtime.index_write(entry=updated)
        self.provider_runtime.on_memory_write(
            MemoryWriteEvent(
                action="remove" if updated.status in {"superseded", "rejected", "archived"} else "replace",
                store_id=updated.store_id,
                content=updated.content,
                category=updated.category,
                thread_id=updated.thread_id,
                metadata=updated.metadata,
            )
        )
        return updated

    def find_entry(self, memory_id: str, *, include_inactive: bool = True) -> tuple[str, CuratedEntry] | None:
        for store in self.curated_store_manager.list_stores():
            for entry in self.curated_store_manager.list_entries(store.store_id):
                if not include_inactive and entry.status in {"superseded", "rejected", "archived"}:
                    continue
                if entry.entry_id == memory_id or entry.memory_id == memory_id:
                    return store.store_id, entry
        return None

    def delete_entry(self, store_id: str, entry_id: str) -> CuratedEntry | None:
        existing_entries = self.curated_store_manager.list_entries(store_id)
        current = next((entry for entry in existing_entries if entry.entry_id == entry_id), None)
        self.curated_store_manager.delete_entry(store_id, entry_id)
        if current is not None:
            self.retrieval_index.delete_memory_entry(current.memory_id or current.entry_id)
            self.provider_runtime.on_memory_write(
                MemoryWriteEvent(
                    action="remove",
                    store_id=current.store_id,
                    content=current.content,
                    category=current.category,
                    thread_id=current.thread_id,
                    metadata=current.metadata,
                )
            )
        return current

    def resolve_conflict(self, conflict_id: str, *, action: str = "keep_both") -> MemoryConflict:
        normalized_action = action.strip().lower()
        target = next((item for item in self.list_conflicts() if item.conflict_id == conflict_id), None)
        if target is None:
            raise KeyError(conflict_id)
        if normalized_action in {"supersede_conflicting", "keep_memory"}:
            self._mark_superseded_any(target.conflicting_memory_id)
        elif normalized_action in {"supersede_memory", "keep_conflicting"}:
            self._mark_superseded_any(target.memory_id)
        elif normalized_action in {"keep_both", "resolved"}:
            self._clear_conflict_relationship(target)
        else:
            raise ValueError(f"unsupported conflict resolution action '{action}'")
        return target.model_copy(update={"resolved": True, "recommended_action": normalized_action})

    def index_archive_turn(self, record: ArchiveTurnRecord) -> None:
        self.retrieval_index.upsert_archive_turn(record)
        self.provider_runtime.index_write(record=record)

    def list_conflicts(self) -> tuple[MemoryConflict, ...]:
        conflicts: list[MemoryConflict] = []
        all_entries: list[CuratedEntry] = []
        for store in self.curated_store_manager.list_stores():
            all_entries.extend(
                entry
                for entry in self.curated_store_manager.list_entries(store.store_id)
                if entry.status not in {"superseded", "rejected", "archived"}
            )
        for entry in all_entries:
            for conflicting_id in entry.conflicts_with:
                conflicts.append(
                    MemoryConflict(
                        conflict_id=f"conflict-{entry.memory_id or entry.entry_id}-{conflicting_id}",
                        memory_id=entry.memory_id or entry.entry_id,
                        conflicting_memory_id=conflicting_id,
                        reason="conflicting memory content detected",
                        recommended_action="review",
                        memory_content=entry.content,
                        conflicting_content=self._content_for_memory_id(conflicting_id, all_entries),
                    )
                )
        return tuple(conflicts)

    def record_access(self, memory_id: str, *, source: str = "recall") -> CuratedEntry | None:
        for store in self.curated_store_manager.list_stores():
            for entry in self.curated_store_manager.list_entries(store.store_id):
                if entry.status in {"superseded", "rejected", "archived"}:
                    continue
                if entry.entry_id == memory_id or entry.memory_id == memory_id:
                    return self.curated_store_manager.touch_entry(store.store_id, memory_id, source=source)
        return None

    def list_retention(self) -> tuple[MemoryRetentionView, ...]:
        now = utc_now()
        items: list[MemoryRetentionView] = []
        for store in self.curated_store_manager.list_stores():
            for entry in self.curated_store_manager.list_entries(store.store_id):
                if entry.status in {"superseded", "rejected", "archived"}:
                    continue
                metrics = retention_metrics(entry, now=now)
                items.append(
                    MemoryRetentionView(
                        memory_id=entry.memory_id or entry.entry_id,
                        store_id=entry.store_id,
                        layer_id=entry.layer_id or _layer_for_store(entry.store_id),
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
                )
        items.sort(key=lambda item: (item.retention_score, item.last_accessed_at or item.created_at))
        return tuple(items)

    def list_staleness(self) -> tuple[MemoryStalenessView, ...]:
        now = utc_now()
        stale: list[MemoryStalenessView] = []
        for store in self.curated_store_manager.list_stores():
            for entry in self.curated_store_manager.list_entries(store.store_id):
                if entry.status in {"superseded", "rejected", "archived"}:
                    continue
                metrics = retention_metrics(entry, now=now)
                age_days = (now - (entry.last_accessed_at or entry.updated_at)).days
                expired = entry.expires_at is not None and entry.expires_at <= now
                if not expired and age_days < 7 and metrics["tier"] != "cold":
                    continue
                stale_score = max(
                    min(1.0, age_days / 30 if age_days > 0 else 0.0),
                    1.0 - float(metrics["retention_score"]),
                )
                reason = "expired memory" if expired else "memory has low retention or has not been accessed recently"
                stale.append(
                    MemoryStalenessView(
                        memory_id=entry.memory_id or entry.entry_id,
                        layer_id=entry.layer_id or _layer_for_store(entry.store_id),
                        stale_score=stale_score,
                        reason=reason,
                        last_accessed_at=entry.last_accessed_at,
                        expires_at=entry.expires_at,
                        retention_score=metrics["retention_score"],
                        tier=metrics["tier"],
                        access_count=metrics["access_count"],
                        reinforcement_boost=metrics["reinforcement_boost"],
                        temporal_decay=metrics["temporal_decay"],
                        salience=metrics["salience"],
                    )
                )
        stale.sort(key=lambda item: item.stale_score, reverse=True)
        return tuple(stale)

    def _mark_superseded(self, *, store_id: str, entry_id_or_memory_id: str) -> None:
        for entry in self.curated_store_manager.list_entries(store_id):
            if entry.entry_id == entry_id_or_memory_id or entry.memory_id == entry_id_or_memory_id:
                self.curated_store_manager.update_entry(
                    store_id,
                    entry.entry_id,
                    status="superseded",
                    write_policy="supersede",
                    write_reason="superseded by newer higher-confidence memory",
                )
                self.retrieval_index.delete_memory_entry(entry.memory_id or entry.entry_id)
                return

    def _mark_superseded_any(self, entry_id_or_memory_id: str) -> None:
        for store in self.curated_store_manager.list_stores():
            self._mark_superseded(store_id=store.store_id, entry_id_or_memory_id=entry_id_or_memory_id)

    def _clear_conflict_relationship(self, target: MemoryConflict) -> None:
        ids_to_clear = {target.memory_id, target.conflicting_memory_id}
        for store in self.curated_store_manager.list_stores():
            for entry in self.curated_store_manager.list_entries(store.store_id):
                own_ids = {entry.entry_id, entry.memory_id or ""}
                if own_ids.isdisjoint(ids_to_clear) and not (set(entry.conflicts_with) & ids_to_clear):
                    continue
                next_conflicts = tuple(conflict_id for conflict_id in entry.conflicts_with if conflict_id not in ids_to_clear)
                if next_conflicts != entry.conflicts_with:
                    self.curated_store_manager.update_entry(
                        store.store_id,
                        entry.entry_id,
                        conflicts_with=next_conflicts,
                        write_policy="resolve_conflict",
                        write_reason="conflict reviewed and kept as compatible",
                    )

    def _content_for_memory_id(self, memory_id: str, entries: list[CuratedEntry]) -> str | None:
        for entry in entries:
            if entry.memory_id == memory_id or entry.entry_id == memory_id:
                return entry.content
        return None
