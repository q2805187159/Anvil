from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from threading import RLock

from .contracts import MemoryCaptureEnvelope, utc_now
from .signals import detect_capture_signals


@dataclass(frozen=True)
class CaptureSignalProfile:
    correction: bool
    reinforcement: bool
    remember: bool
    text_length: int
    strength: float
    window_seconds: float


class DebouncedMemoryQueue:
    """Adaptive debouncer for HCMS observation capture.

    High-signal corrections and explicit remember requests are available
    immediately. Low-signal chatter is coalesced per thread/namespace and is
    released after an adaptive window. This keeps the zero-LLM path cheap while
    preserving urgent updates.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        debounce_seconds: float | None = None,
        min_window_seconds: float = 5.0,
        default_window_seconds: float = 30.0,
        max_window_seconds: float = 60.0,
        min_batch_turns: int = 4,
        max_batch_turns: int = 8,
    ) -> None:
        self.enabled = bool(enabled)
        self.debounce_seconds = max(0.0, float(default_window_seconds if debounce_seconds is None else debounce_seconds))
        self.min_window_seconds = max(0.0, float(min_window_seconds))
        self.default_window_seconds = max(self.min_window_seconds, float(default_window_seconds))
        self.max_window_seconds = max(self.default_window_seconds, float(max_window_seconds))
        self.min_batch_turns = max(1, int(min_batch_turns))
        self.max_batch_turns = max(self.min_batch_turns, int(max_batch_turns))
        self._pending: "OrderedDict[tuple[str, str], MemoryCaptureEnvelope]" = OrderedDict()
        self._ready_at: dict[tuple[str, str], object] = {}
        self._cost_baseline_calls = 0
        self._processed_batches = 0
        self._lock = RLock()
        self._async_lock: asyncio.Lock | None = None

    def enqueue(self, envelope: MemoryCaptureEnvelope) -> None:
        with self._lock:
            self._enqueue_unlocked(envelope)

    async def enqueue_async(self, envelope: MemoryCaptureEnvelope) -> None:
        async with self._async_locked():
            with self._lock:
                self._enqueue_unlocked(envelope)

    def _enqueue_unlocked(self, envelope: MemoryCaptureEnvelope) -> None:
        key = (envelope.thread_id, envelope.memory_namespace)
        if key in self._pending:
            envelope = self._merge_envelopes(self._pending.pop(key), envelope)
        self._pending[key] = envelope
        self._ready_at[key] = envelope.timestamp + timedelta(seconds=self._window_for(envelope))
        self._cost_baseline_calls += 1

    def get_pending(self, namespace: str | None = None) -> list[MemoryCaptureEnvelope]:
        with self._lock:
            return self._get_pending_unlocked(namespace)

    async def get_pending_async(self, namespace: str | None = None) -> list[MemoryCaptureEnvelope]:
        async with self._async_locked():
            with self._lock:
                return self._get_pending_unlocked(namespace)

    def _get_pending_unlocked(self, namespace: str | None = None) -> list[MemoryCaptureEnvelope]:
        items = list(self._pending.values())
        if namespace is None:
            return [item.model_copy(deep=True) for item in items]
        return [item.model_copy(deep=True) for item in items if item.memory_namespace == namespace]

    def pop_next(self, namespace: str | None = None, *, force: bool = True) -> MemoryCaptureEnvelope | None:
        with self._lock:
            return self._pop_next_unlocked(namespace, force=force)

    async def pop_next_async(self, namespace: str | None = None, *, force: bool = True) -> MemoryCaptureEnvelope | None:
        async with self._async_locked():
            with self._lock:
                return self._pop_next_unlocked(namespace, force=force)

    def _pop_next_unlocked(self, namespace: str | None = None, *, force: bool = True) -> MemoryCaptureEnvelope | None:
        now = utc_now()
        for key, envelope in list(self._pending.items()):
            if namespace is not None and envelope.memory_namespace != namespace:
                continue
            ready_at = self._ready_at.get(key)
            if self.enabled and not force and ready_at is not None and ready_at > now:
                continue
            self._pending.pop(key)
            self._ready_at.pop(key, None)
            self._processed_batches += 1
            return envelope
        return None

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    async def pending_count_async(self) -> int:
        async with self._async_locked():
            with self._lock:
                return len(self._pending)

    def should_flush_immediately(self, envelope: MemoryCaptureEnvelope) -> bool:
        """Return whether a capture carries enough signal to bypass the debounce window."""
        if not self.enabled:
            return True
        if self.signal_profile(envelope).window_seconds <= self.min_window_seconds:
            return True
        with self._lock:
            pending = self._pending.get((envelope.thread_id, envelope.memory_namespace))
        if pending is None:
            return False
        return int(pending.metadata.get("coalesced_capture_count", 1) or 1) >= self.max_batch_turns

    def cost_reduction_ratio(self) -> float:
        with self._lock:
            if self._cost_baseline_calls <= 0:
                return 0.0
            actual_batches = self._processed_batches + len(self._pending)
            avoided = max(self._cost_baseline_calls - actual_batches, 0)
            return round(avoided / self._cost_baseline_calls, 4)

    def _merge_envelopes(
        self,
        previous: MemoryCaptureEnvelope,
        incoming: MemoryCaptureEnvelope,
    ) -> MemoryCaptureEnvelope:
        previous_count = int(previous.metadata.get("coalesced_capture_count", 1) or 1)
        incoming_count = int(incoming.metadata.get("coalesced_capture_count", 1) or 1)
        return MemoryCaptureEnvelope(
            thread_id=incoming.thread_id,
            memory_namespace=incoming.memory_namespace,
            user_messages=[*previous.user_messages, *incoming.user_messages],
            final_assistant_messages=[*previous.final_assistant_messages, *incoming.final_assistant_messages],
            explicit_corrections=[*previous.explicit_corrections, *incoming.explicit_corrections],
            positive_reinforcement=[*previous.positive_reinforcement, *incoming.positive_reinforcement],
            timestamp=max(previous.timestamp, incoming.timestamp),
            trace_id=incoming.trace_id or previous.trace_id,
            metadata={
                **previous.metadata,
                **incoming.metadata,
                "coalesced_capture_count": previous_count + incoming_count,
            },
        )

    def signal_profile(self, envelope: MemoryCaptureEnvelope) -> CaptureSignalProfile:
        free_text = _envelope_text(envelope, include_structured=False)
        signal_text = _envelope_text(envelope)
        signal = detect_capture_signals(free_text)
        if envelope.explicit_corrections or envelope.positive_reinforcement:
            signal = detect_capture_signals(
                signal_text,
                correction=signal.correction or bool(envelope.explicit_corrections),
                reinforcement=signal.reinforcement or bool(envelope.positive_reinforcement),
                remember=signal.remember,
                detect_remember=False,
            )
        if signal.correction or signal.remember or signal.strength >= 0.5:
            window = self.min_window_seconds
        elif signal.strength >= 0.3 or len(signal_text) >= 80:
            window = self.default_window_seconds
        else:
            window = self.max_window_seconds
        return CaptureSignalProfile(
            correction=signal.correction,
            reinforcement=signal.reinforcement,
            remember=signal.remember,
            text_length=len(signal_text),
            strength=signal.strength,
            window_seconds=window,
        )

    def _window_for(self, envelope: MemoryCaptureEnvelope) -> float:
        return self.signal_profile(envelope).window_seconds

    def _async_locked(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock


def _envelope_text(envelope: MemoryCaptureEnvelope, *, include_structured: bool = True) -> str:
    parts = [*envelope.user_messages, *envelope.final_assistant_messages]
    if include_structured:
        parts.extend(envelope.explicit_corrections)
        parts.extend(envelope.positive_reinforcement)
    return " ".join(parts).lower()
