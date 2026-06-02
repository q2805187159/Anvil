from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
import queue
import threading
from typing import Any

from .models import RunStreamEvent


_SENTINEL = object()


@dataclass
class _BackgroundRun:
    key: str
    factory: Callable[[], Iterator[RunStreamEvent]]
    max_buffer: int
    on_done: Callable[[str], None] | None = None
    buffer: list[RunStreamEvent] = field(default_factory=list)
    subscribers: list[tuple[queue.Queue[Any], str | None]] = field(default_factory=list)
    done: bool = False
    cancel_requested: bool = False
    cancel_reason: str | None = None
    started: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _start_thread(self) -> None:
        thread = threading.Thread(target=self._consume, name=f"anvil-stream-{self.key}", daemon=True)
        thread.start()

    def subscribe(self, *, last_event_id: str | None = None) -> Iterator[RunStreamEvent]:
        subscriber: queue.Queue[Any] = queue.Queue()
        should_start = False
        with self.lock:
            snapshot, cursor_before_buffer = _snapshot_after_cursor(self.buffer, last_event_id)
            done = self.done
            if not done and not cursor_before_buffer:
                self.subscribers.append((subscriber, last_event_id))
                if not self.started:
                    self.started = True
                    should_start = True

        if should_start:
            self._start_thread()

        for event in snapshot:
            yield event
        if done or cursor_before_buffer:
            return

        try:
            while True:
                item = subscriber.get()
                if item is _SENTINEL:
                    return
                yield item
        finally:
            with self.lock:
                self.subscribers = [
                    (registered_subscriber, cursor)
                    for registered_subscriber, cursor in self.subscribers
                    if registered_subscriber is not subscriber
                ]

    def _consume(self) -> None:
        try:
            for event in self.factory():
                with self.lock:
                    if self.cancel_requested:
                        break
                self._publish(event)
        finally:
            with self.lock:
                self.done = True
                subscribers = list(self.subscribers)
                self.subscribers.clear()
        for subscriber, _cursor in subscribers:
            subscriber.put(_SENTINEL)
        if self.on_done is not None:
            self.on_done(self.key)

    def _publish(self, event: RunStreamEvent) -> None:
        with self.lock:
            if self.cancel_requested:
                return
            self.buffer.append(event)
            if len(self.buffer) > self.max_buffer:
                self.buffer = self.buffer[-self.max_buffer :]
            subscribers = list(self.subscribers)
        for subscriber, last_event_id in subscribers:
            if _event_after_cursor(event, last_event_id):
                subscriber.put(event)


class BackgroundRunStreamManager:
    def __init__(self, *, max_buffer: int = 800, max_completed_runs: int = 128) -> None:
        self.max_buffer = max_buffer
        self.max_completed_runs = max(0, max_completed_runs)
        self._runs: dict[str, _BackgroundRun] = {}
        self._completed_run_keys: list[str] = []
        self._lock = threading.Lock()
        self._closed = False

    def stream(
        self,
        key: str,
        factory: Callable[[], Iterator[RunStreamEvent]],
        *,
        last_event_id: str | None = None,
    ) -> Iterator[RunStreamEvent]:
        if last_event_id is not None:
            with self._lock:
                existing = self._runs.get(key)
            if existing is None:
                return iter(())
        run = self._get_or_start(key, factory, replay_existing_done=last_event_id is not None)
        return run.subscribe(last_event_id=last_event_id)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            runs = list(self._runs.values())
            self._runs.clear()
            self._completed_run_keys.clear()
        for run in runs:
            with run.lock:
                run.cancel_requested = True
                run.cancel_reason = run.cancel_reason or "Stream manager closed"
                run.done = True
                subscribers = list(run.subscribers)
                run.subscribers.clear()
            for subscriber, _cursor in subscribers:
                subscriber.put(_SENTINEL)

    def request_interrupt(self, key: str, *, reason: str = "Interrupted by user") -> bool:
        with self._lock:
            run = self._runs.get(key)
        if run is None or run.done:
            return False
        with run.lock:
            run.cancel_requested = True
            run.cancel_reason = reason
            run.done = True
            subscribers = list(run.subscribers)
            run.subscribers.clear()
        for subscriber, _cursor in subscribers:
            subscriber.put(_SENTINEL)
        self._remember_completed_run(key)
        return True

    def is_interrupt_requested(self, key: str) -> bool:
        with self._lock:
            run = self._runs.get(key)
        return bool(run is not None and run.cancel_requested)

    def interrupt_reason(self, key: str) -> str | None:
        with self._lock:
            run = self._runs.get(key)
        return run.cancel_reason if run is not None else None

    def _get_or_start(
        self,
        key: str,
        factory: Callable[[], Iterator[RunStreamEvent]],
        *,
        replay_existing_done: bool = False,
    ) -> _BackgroundRun:
        with self._lock:
            existing = self._runs.get(key)
            if existing is not None and (not existing.done or replay_existing_done):
                return existing
            if self._closed:
                raise RuntimeError("stream manager is closed")
            if existing is not None:
                self._forget_completed_run_key(key)
            run = _BackgroundRun(
                key=key,
                factory=factory,
                max_buffer=self.max_buffer,
                on_done=self._remember_completed_run,
            )
            self._runs[key] = run
        return run

    def _remember_completed_run(self, key: str) -> None:
        with self._lock:
            run = self._runs.get(key)
            if run is None or not run.done:
                return
            self._forget_completed_run_key(key)
            self._completed_run_keys.append(key)
            while len(self._completed_run_keys) > self.max_completed_runs:
                expired_key = self._completed_run_keys.pop(0)
                expired_run = self._runs.get(expired_key)
                if expired_run is not None and expired_run.done:
                    del self._runs[expired_key]

    def _forget_completed_run_key(self, key: str) -> None:
        try:
            self._completed_run_keys.remove(key)
        except ValueError:
            pass


def _event_after_cursor(event: RunStreamEvent, last_event_id: str | None) -> bool:
    if not last_event_id:
        return True
    event_id = event.event_id or (str(event.data.get("event_id")) if event.data.get("event_id") is not None else None)
    if event_id and event_id == last_event_id:
        return False
    cursor_run_id = _run_id_from_event_id(last_event_id)
    event_run_id = _event_run_id(event, event_id)
    if cursor_run_id and event_run_id and cursor_run_id != event_run_id:
        return True
    cursor_sequence = _sequence_from_event_id(last_event_id)
    event_sequence = event.sequence
    if event_sequence is None and event.data.get("sequence") is not None:
        try:
            event_sequence = int(event.data["sequence"])
        except (TypeError, ValueError):
            event_sequence = None
    if cursor_sequence is not None and event_sequence is not None:
        return event_sequence > cursor_sequence
    return True


def _snapshot_after_cursor(buffer: list[RunStreamEvent], last_event_id: str | None) -> tuple[list[RunStreamEvent], bool]:
    if not last_event_id:
        return list(buffer), False
    if _cursor_before_buffer(buffer, last_event_id):
        return [], True
    return [event for event in buffer if _event_after_cursor(event, last_event_id)], False


def _cursor_before_buffer(buffer: list[RunStreamEvent], last_event_id: str) -> bool:
    cursor_run_id = _run_id_from_event_id(last_event_id)
    cursor_sequence = _sequence_from_event_id(last_event_id)
    if cursor_run_id is None or cursor_sequence is None:
        return False
    matching_sequences: list[int] = []
    for event in buffer:
        event_id = event.event_id or (str(event.data.get("event_id")) if event.data.get("event_id") is not None else None)
        if _event_run_id(event, event_id) != cursor_run_id:
            continue
        event_sequence = event.sequence
        if event_sequence is None and event.data.get("sequence") is not None:
            try:
                event_sequence = int(event.data["sequence"])
            except (TypeError, ValueError):
                event_sequence = None
        if event_sequence is not None:
            matching_sequences.append(event_sequence)
    return bool(matching_sequences and cursor_sequence < min(matching_sequences))


def _sequence_from_event_id(event_id: str) -> int | None:
    suffix = event_id.rsplit(":", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return None


def _run_id_from_event_id(event_id: str) -> str | None:
    if ":" not in event_id:
        return None
    prefix = event_id.rsplit(":", 1)[0]
    return prefix or None


def _event_run_id(event: RunStreamEvent, event_id: str | None) -> str | None:
    value = event.data.get("run_id")
    if isinstance(value, str) and value:
        return value
    if event_id:
        return _run_id_from_event_id(event_id)
    return None
