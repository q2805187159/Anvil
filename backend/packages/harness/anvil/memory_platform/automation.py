from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryAutomationTask:
    name: str
    fn: Callable[[], None]


class MemoryAutomationQueue:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._queue: queue.Queue[MemoryAutomationTask | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._closed = threading.Event()

    def submit(self, name: str, fn: Callable[[], None]) -> None:
        if not self.enabled:
            try:
                fn()
            except Exception:
                return
            return
        self._ensure_started()
        self._queue.put(MemoryAutomationTask(name=name, fn=fn))

    def flush(self) -> None:
        self._queue.join()

    def close(self) -> None:
        self._closed.set()
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=2.0)
            self._thread = None

    def _ensure_started(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="anvil-memory-automation", daemon=True)
        self._thread.start()
        self._started.wait(timeout=1.0)

    def _run(self) -> None:
        self._started.set()
        while not self._closed.is_set():
            task = self._queue.get()
            try:
                if task is None:
                    return
                try:
                    task.fn()
                except Exception:
                    continue
            finally:
                self._queue.task_done()
