from __future__ import annotations

from typing import Protocol

from ..contracts import Memory, MemoryState, MemoryVersionRecord


class HCMSError(RuntimeError):
    """Base error for HCMS public APIs."""


class StorageError(HCMSError):
    """Raised when durable memory storage cannot complete an operation."""


class MemoryNotFoundError(StorageError, KeyError):
    """Raised when a requested memory id does not exist in a namespace."""


class VersionConflictError(StorageError):
    """Raised when optimistic version checks detect concurrent updates."""


class StorageBackend(Protocol):
    """Direct memory CRUD contract used by HCMS storage implementations."""

    def save_memory(self, namespace: str, memory: Memory, *, expected_version: int | None = None) -> Memory: ...

    def get_memory(self, namespace: str, memory_id: str) -> Memory: ...

    def list_memories(self, namespace: str) -> tuple[Memory, ...]: ...

    def delete_memory(self, namespace: str, memory_id: str) -> None: ...

    def search_memories(self, namespace: str, query: str, *, limit: int = 20) -> tuple[Memory, ...]: ...

    def append_version(self, namespace: str, record: MemoryVersionRecord) -> None: ...

    def history(self, namespace: str, memory_id: str) -> tuple[MemoryVersionRecord, ...]: ...


class NamespaceStateBackend(Protocol):
    """Compatibility contract for agent runtime state stores."""

    def load(self, namespace: str) -> MemoryState: ...

    def save(self, namespace: str, memory_state: MemoryState) -> None: ...

    def invalidate(self, namespace: str) -> None: ...

    def list_namespaces(self) -> list[str]: ...
