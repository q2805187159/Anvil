from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from anvil.agents import ThreadMetadataView


class StoreBackend(str, Enum):
    IN_MEMORY = "in_memory"
    SQLITE = "sqlite"


class Store(Protocol):
    backend: StoreBackend
    is_durable: bool

    def put_thread_metadata(self, metadata: "ThreadMetadataView") -> "ThreadMetadataView": ...

    def get_thread_metadata(self, thread_id: str) -> "ThreadMetadataView | None": ...

    def delete_thread(self, thread_id: str) -> None: ...

    def list_threads(self) -> list["ThreadMetadataView"]: ...

    def reset(self) -> None: ...

    def close(self) -> None: ...


def normalize_sqlite_path(sqlite_path: str | Path) -> Path:
    return Path(sqlite_path).expanduser().resolve()


def thread_metadata_recency_sort_key(metadata: "ThreadMetadataView") -> tuple[float, str]:
    activity_at = metadata.last_message_at or metadata.updated_at
    return (-activity_at.timestamp(), metadata.thread_id)
