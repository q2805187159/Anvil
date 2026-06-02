from __future__ import annotations

from pathlib import Path

from .base import Store, StoreBackend
from .in_memory import InMemoryStore
from .sqlite import SqliteStore


_STORE_CACHE: dict[tuple[str, str | None], Store] = {}


def create_store(
    backend: StoreBackend | str = StoreBackend.IN_MEMORY,
    sqlite_path: str | Path | None = None,
) -> Store:
    backend_kind = StoreBackend(backend)
    if backend_kind is StoreBackend.IN_MEMORY:
        return InMemoryStore()
    if sqlite_path is None:
        raise ValueError("sqlite_path is required for sqlite store")
    return SqliteStore(sqlite_path)


def get_cached_store(
    backend: StoreBackend | str = StoreBackend.IN_MEMORY,
    sqlite_path: str | Path | None = None,
) -> Store:
    backend_kind = StoreBackend(backend)
    key = (backend_kind.value, str(Path(sqlite_path).resolve()) if sqlite_path is not None else None)
    if key not in _STORE_CACHE:
        _STORE_CACHE[key] = create_store(backend_kind, sqlite_path=sqlite_path)
    return _STORE_CACHE[key]


def reset_store_cache() -> None:
    for store in _STORE_CACHE.values():
        store.close()
    _STORE_CACHE.clear()


__all__ = [
    "Store",
    "StoreBackend",
    "InMemoryStore",
    "SqliteStore",
    "create_store",
    "get_cached_store",
    "reset_store_cache",
]
