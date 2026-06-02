from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from .contracts import ArchiveTurnRecord


@dataclass(frozen=True)
class MemoryUpdateBatch:
    thread_id: str
    turns: tuple[ArchiveTurnRecord, ...]


class MemoryUpdateQueue:
    """Thread-batched post-turn updater queue.

    The queue is deliberately deterministic: production callers may enqueue after
    each turn, while high-value signals and lifecycle hooks can force a drain.
    """

    def __init__(self, *, max_batch_turns: int = 8, min_batch_turns: int = 4, debounce_seconds: float = 0.0, enabled: bool = True) -> None:
        self.max_batch_turns = max(max_batch_turns, 1)
        self.min_batch_turns = max(min_batch_turns, 1)
        self.debounce_seconds = max(float(debounce_seconds or 0.0), 0.0)
        self.enabled = enabled
        self._pending: dict[str, list[ArchiveTurnRecord]] = defaultdict(list)
        self._lock = Lock()

    def enqueue(self, record: ArchiveTurnRecord) -> None:
        if not self.enabled:
            return
        with self._lock:
            bucket = self._pending[record.thread_id]
            bucket.append(record)
            if len(bucket) > self.max_batch_turns:
                del bucket[:-self.max_batch_turns]

    def pending_count(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._pending.values())

    def drain(self, handler: Callable[[MemoryUpdateBatch], None], *, thread_id: str | None = None, force: bool = True) -> int:
        batches = self._pop_batches(thread_id=thread_id, force=force)
        for batch in batches:
            handler(batch)
        return sum(len(batch.turns) for batch in batches)

    def _pop_batches(self, *, thread_id: str | None = None, force: bool) -> tuple[MemoryUpdateBatch, ...]:
        with self._lock:
            if thread_id is None:
                items: dict[str, list[ArchiveTurnRecord]] = {}
                for key, turns in list(self._pending.items()):
                    if self._ready_to_drain(key, turns, force=force):
                        items[key] = list(turns)
                        del self._pending[key]
            else:
                turns = self._pending.get(thread_id, [])
                if turns and self._ready_to_drain(thread_id, turns, force=force):
                    self._pending.pop(thread_id, None)
                    items = {thread_id: list(turns)}
                else:
                    items = {}
        return tuple(
            MemoryUpdateBatch(thread_id=key, turns=tuple(value))
            for key, value in items.items()
            if value
        )

    def _ready_to_drain(self, thread_id: str, turns: list[ArchiveTurnRecord], *, force: bool) -> bool:
        if force:
            return True
        if len(turns) >= self.min_batch_turns:
            return True
        return False
