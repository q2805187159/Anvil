from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock

from anvil.agents import ThreadMetadataView

from .base import StoreBackend, normalize_sqlite_path, thread_metadata_recency_sort_key


class SqliteStore:
    backend = StoreBackend.SQLITE
    is_durable = True

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = normalize_sqlite_path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS thread_metadata (
                    thread_id TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            self._connection.commit()

    def put_thread_metadata(self, metadata: ThreadMetadataView) -> ThreadMetadataView:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO thread_metadata(thread_id, metadata_json)
                VALUES (?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET metadata_json = excluded.metadata_json
                """,
                (metadata.thread_id, metadata.model_dump_json()),
            )
            self._connection.commit()
        return metadata

    def get_thread_metadata(self, thread_id: str) -> ThreadMetadataView | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT metadata_json FROM thread_metadata WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        return ThreadMetadataView.model_validate_json(row[0])

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM thread_metadata WHERE thread_id = ?",
                (thread_id,),
            )
            self._connection.commit()

    def list_threads(self) -> list[ThreadMetadataView]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT metadata_json FROM thread_metadata"
            ).fetchall()
        return sorted(
            [ThreadMetadataView.model_validate_json(row[0]) for row in rows],
            key=thread_metadata_recency_sort_key,
        )

    def reset(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM thread_metadata")
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()
