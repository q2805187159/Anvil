from __future__ import annotations

from pathlib import Path

from .base import Checkpointer, CheckpointerBackend
from .in_memory import InMemoryCheckpointer
from .sqlite import SqliteCheckpointer


_CHECKPOINTER_CACHE: dict[tuple[str, str | None], Checkpointer] = {}


def create_checkpointer(
    backend: CheckpointerBackend | str = CheckpointerBackend.IN_MEMORY,
    sqlite_path: str | Path | None = None,
) -> Checkpointer:
    backend_kind = CheckpointerBackend(backend)
    if backend_kind is CheckpointerBackend.IN_MEMORY:
        return InMemoryCheckpointer()
    if sqlite_path is None:
        raise ValueError("sqlite_path is required for sqlite checkpointer")
    return SqliteCheckpointer(sqlite_path)


def get_cached_checkpointer(
    backend: CheckpointerBackend | str = CheckpointerBackend.IN_MEMORY,
    sqlite_path: str | Path | None = None,
) -> Checkpointer:
    backend_kind = CheckpointerBackend(backend)
    key = (backend_kind.value, str(Path(sqlite_path).resolve()) if sqlite_path is not None else None)
    if key not in _CHECKPOINTER_CACHE:
        _CHECKPOINTER_CACHE[key] = create_checkpointer(backend_kind, sqlite_path=sqlite_path)
    return _CHECKPOINTER_CACHE[key]


def reset_checkpointer_cache() -> None:
    for checkpointer in _CHECKPOINTER_CACHE.values():
        checkpointer.close()
    _CHECKPOINTER_CACHE.clear()


__all__ = [
    "Checkpointer",
    "CheckpointerBackend",
    "InMemoryCheckpointer",
    "SqliteCheckpointer",
    "create_checkpointer",
    "get_cached_checkpointer",
    "reset_checkpointer_cache",
]
