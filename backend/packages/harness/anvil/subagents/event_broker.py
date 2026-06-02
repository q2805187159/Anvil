from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock

from .contracts import SubagentEvent, SubagentEventType


_CRITICAL_EVENT_TYPES = {
    SubagentEventType.JOB_COMPLETED,
    SubagentEventType.JOB_FAILED,
    SubagentEventType.JOB_CANCELLED,
    SubagentEventType.JOB_TIMED_OUT,
    SubagentEventType.JOB_INTERRUPTED,
}


class SubagentEventBroker:
    """Thread-safe per-parent-run event broker for subagent lifecycle updates."""

    def __init__(self, *, max_events_per_parent: int = 100) -> None:
        self.max_events_per_parent = max(1, max_events_per_parent)
        self._lock = Lock()
        self._queues: dict[tuple[str, str | None], deque[SubagentEvent]] = defaultdict(deque)

    def publish(self, event: SubagentEvent) -> None:
        key = (event.parent_thread_id, event.parent_run_id)
        with self._lock:
            queue = self._queues[key]
            queue.append(event)
            self._trim_queue(queue)

    def drain(self, *, parent_thread_id: str, parent_run_id: str | None) -> list[SubagentEvent]:
        key = (parent_thread_id, parent_run_id)
        with self._lock:
            queue = self._queues.get(key)
            if not queue:
                return []
            drained = list(queue)
            queue.clear()
            if not queue:
                self._queues.pop(key, None)
            return drained

    def _trim_queue(self, queue: deque[SubagentEvent]) -> None:
        while len(queue) > self.max_events_per_parent:
            drop_index = next(
                (index for index, item in enumerate(queue) if item.event_type not in _CRITICAL_EVENT_TYPES),
                0,
            )
            del queue[drop_index]
