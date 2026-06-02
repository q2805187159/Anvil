from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from anvil.agents import ThreadState


class CheckpointerBackend(str, Enum):
    IN_MEMORY = "in_memory"
    SQLITE = "sqlite"


class Checkpointer(Protocol):
    backend: CheckpointerBackend
    is_durable: bool

    def put_thread_state(self, state: "ThreadState") -> "ThreadState": ...

    def get_thread_state(self, thread_id: str) -> "ThreadState | None": ...

    def delete_thread(self, thread_id: str) -> None: ...

    def list_thread_ids(self) -> list[str]: ...

    def reset(self) -> None: ...

    def close(self) -> None: ...


def normalize_sqlite_path(sqlite_path: str | Path) -> Path:
    return Path(sqlite_path).expanduser().resolve()
