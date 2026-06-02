from __future__ import annotations

from anvil.agents import ThreadMetadataView

from .base import StoreBackend, thread_metadata_recency_sort_key


class InMemoryStore:
    backend = StoreBackend.IN_MEMORY
    is_durable = False

    def __init__(self) -> None:
        self._metadata_by_thread: dict[str, ThreadMetadataView] = {}

    def put_thread_metadata(self, metadata: ThreadMetadataView) -> ThreadMetadataView:
        self._metadata_by_thread[metadata.thread_id] = metadata.model_copy(deep=True)
        return metadata

    def get_thread_metadata(self, thread_id: str) -> ThreadMetadataView | None:
        metadata = self._metadata_by_thread.get(thread_id)
        return metadata.model_copy(deep=True) if metadata is not None else None

    def delete_thread(self, thread_id: str) -> None:
        self._metadata_by_thread.pop(thread_id, None)

    def list_threads(self) -> list[ThreadMetadataView]:
        return [
            metadata.model_copy(deep=True)
            for metadata in sorted(self._metadata_by_thread.values(), key=thread_metadata_recency_sort_key)
        ]

    def reset(self) -> None:
        self._metadata_by_thread.clear()

    def close(self) -> None:
        self.reset()
