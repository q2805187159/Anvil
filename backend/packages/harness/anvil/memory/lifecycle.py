from __future__ import annotations

from .compiler import KnowledgeCompiler
from .contracts import ForgettingConfig, MemoryLifecycleState, MemoryState, MemoryVersionRecord, utc_now


class MemoryLifecycleManager:
    def __init__(self, config: ForgettingConfig | None = None) -> None:
        self.config = config or ForgettingConfig()
        self.compiler = KnowledgeCompiler()

    def apply_forgetting(self, state: MemoryState, *, limit: int | None = None) -> tuple[str, ...]:
        if not self.config.enable_auto_forgetting:
            return ()
        now = utc_now()
        changed: list[str] = []
        candidates = [
            memory
            for memory in state.memories
            if memory.state == MemoryLifecycleState.ACTIVE
            and (
                (memory.forget_after is not None and memory.forget_after <= now)
                or memory.compute_retention_score(now=now, decay_lambda=self.config.decay_lambda) < self.config.retention_threshold
            )
        ]
        candidates.sort(key=lambda item: item.compute_retention_score(now=now, decay_lambda=self.config.decay_lambda))
        for memory in candidates[: limit or len(candidates)]:
            self.compiler.archive_memory(state, memory.memory_id, reason="auto_forget")
            changed.append(memory.memory_id)

        remaining_slots = None if limit is None else max(limit - len(changed), 0)
        newly_archived = set(changed)
        expired_cold = [
            memory
            for memory in state.memories
            if memory.state in {MemoryLifecycleState.ARCHIVED, MemoryLifecycleState.FORGOTTEN}
            and memory.memory_id not in newly_archived
            and (now - memory.created_at).days > self.config.low_importance_ttl_days
            and memory.compute_retention_score(now=now, decay_lambda=self.config.decay_lambda) < self.config.retention_threshold
        ]
        expired_cold.sort(key=lambda item: item.compute_retention_score(now=now, decay_lambda=self.config.decay_lambda))
        delete_candidates = expired_cold if remaining_slots is None else expired_cold[:remaining_slots]
        for memory in delete_candidates:
            state.versions.append(
                MemoryVersionRecord(
                    memory_id=memory.memory_id,
                    version=memory.version + 1,
                    parent_id=f"{memory.memory_id}@v{memory.version}",
                    content=memory.content,
                    summary=memory.summary,
                    reason="auto_delete_expired_cold",
                    metadata=memory.version_metadata(),
                )
            )
            state.memories = [item for item in state.memories if item.memory_id != memory.memory_id]
            state.relations = [
                relation
                for relation in state.relations
                if memory.memory_id not in {relation.source_memory_id, relation.target_memory_id}
            ]
            state.causal_edges = [
                edge
                for edge in state.causal_edges
                if memory.memory_id not in {edge.source_event, edge.target_event}
            ]
            changed.append(memory.memory_id)
        if changed:
            state.updated_at = now
        return tuple(changed)

    def archive(self, state: MemoryState, memory_id: str, *, reason: str = "manual_archive"):
        return self.compiler.archive_memory(state, memory_id, reason=reason)

    def restore(self, state: MemoryState, memory_id: str, *, reason: str = "manual_restore"):
        return self.compiler.restore_memory(state, memory_id, reason=reason)

    def forget(self, state: MemoryState, memory_id: str, *, reason: str = "manual_forget"):
        return self.compiler.forget_memory(state, memory_id, reason=reason)
