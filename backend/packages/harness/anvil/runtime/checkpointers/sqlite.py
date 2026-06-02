from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock

from anvil.agents import ThreadState

from .base import CheckpointerBackend, normalize_sqlite_path


class SqliteCheckpointer:
    backend = CheckpointerBackend.SQLITE
    is_durable = True

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = normalize_sqlite_path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS thread_checkpoints (
                    thread_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL
                )
                """
            )
            self._connection.commit()

    def put_thread_state(self, state: ThreadState) -> ThreadState:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO thread_checkpoints(thread_id, state_json)
                VALUES (?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET state_json = excluded.state_json
                """,
                (state.identity.thread_id, state.model_dump_json()),
            )
            self._connection.commit()
        return state

    def get_thread_state(self, thread_id: str) -> ThreadState | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT state_json FROM thread_checkpoints WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        return ThreadState.model_validate_json(row[0])

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM thread_checkpoints WHERE thread_id = ?",
                (thread_id,),
            )
            self._connection.commit()

    def list_thread_ids(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT thread_id FROM thread_checkpoints ORDER BY thread_id"
            ).fetchall()
        return [row[0] for row in rows]

    def reset(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM thread_checkpoints")
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()
