from __future__ import annotations

from collections import OrderedDict

from .contracts import MemoryCaptureEnvelope


class DebouncedMemoryQueue:
    def __init__(self) -> None:
        self._pending: "OrderedDict[tuple[str, str], MemoryCaptureEnvelope]" = OrderedDict()

    def enqueue(self, envelope: MemoryCaptureEnvelope) -> None:
        key = (envelope.thread_id, envelope.memory_namespace)
        if key in self._pending:
            self._pending.pop(key)
        self._pending[key] = envelope

    def get_pending(self, namespace: str | None = None) -> list[MemoryCaptureEnvelope]:
        items = list(self._pending.values())
        if namespace is None:
            return [item.model_copy(deep=True) for item in items]
        return [item.model_copy(deep=True) for item in items if item.memory_namespace == namespace]

    def pop_next(self, namespace: str | None = None) -> MemoryCaptureEnvelope | None:
        for key, envelope in list(self._pending.items()):
            if namespace is not None and envelope.memory_namespace != namespace:
                continue
            self._pending.pop(key)
            return envelope
        return None
