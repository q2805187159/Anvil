from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass
class SystemEvent:
    event: str
    system_version: int
    data: dict[str, Any]


class SystemEventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[SystemEvent]] = set()
        self._version = 0
        self._lock = Lock()

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._version += 1
            version = self._version
        payload = SystemEvent(event=event, system_version=version, data={**data, "system_version": version})
        stale: list[asyncio.Queue[SystemEvent]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)

    def subscribe(self) -> asyncio.Queue[SystemEvent]:
        queue: asyncio.Queue[SystemEvent] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[SystemEvent]) -> None:
        self._subscribers.discard(queue)

    def current_version(self) -> int:
        with self._lock:
            return self._version
