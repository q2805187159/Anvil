from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .contracts import ArchiveRepository, ArchiveSearchHit, ArchiveSearchResult, ArchiveTurnRecord, utc_now


class SqliteSessionArchive(ArchiveRepository):
    def __init__(self, sqlite_path: str | Path, *, fts_enabled: bool = True) -> None:
        self.sqlite_path = Path(sqlite_path).expanduser().resolve()
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._fts_enabled = fts_enabled
        self._lock = Lock()
        self._conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def record_turn(self, thread_id: str, user_content: str, assistant_content: str, status: str) -> ArchiveTurnRecord:
        created_at = utc_now()
        archive_id = f"archive-{uuid4().hex[:16]}"
        combined = f"{user_content}\n{assistant_content}".strip()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO archive_turns (archive_id, thread_id, user_content, assistant_content, status, created_at, combined_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    archive_id,
                    thread_id,
                    user_content,
                    assistant_content,
                    status,
                    created_at.isoformat(),
                    combined,
                ),
            )
            if self._fts_enabled:
                self._conn.execute(
                    "INSERT INTO archive_turns_fts (archive_id, thread_id, combined_text) VALUES (?, ?, ?)",
                    (archive_id, thread_id, combined),
                )
            self._conn.commit()
        return ArchiveTurnRecord(
            archive_id=archive_id,
            thread_id=thread_id,
            user_content=user_content,
            assistant_content=assistant_content,
            status=status,
            created_at=created_at,
        )

    def search(self, query: str, limit: int = 5) -> ArchiveSearchResult:
        normalized = query.strip()
        if not normalized:
            return ArchiveSearchResult(query=query, hits=())

        rows: list[sqlite3.Row]
        with self._lock:
            if normalized == "*":
                rows = list(
                    self._conn.execute(
                        """
                        SELECT archive_id, thread_id, created_at, combined_text, 1.0 AS score
                        FROM archive_turns
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    )
                )
                hits = tuple(
                    ArchiveSearchHit(
                        archive_id=row["archive_id"],
                        thread_id=row["thread_id"],
                        score=float(row["score"]),
                        excerpt=(row["combined_text"] or "")[:240],
                        created_at=_parse_dt(row["created_at"]),
                    )
                    for row in rows
                )
                return ArchiveSearchResult(query=query, hits=hits)
            try:
                if self._fts_enabled:
                    rows = list(
                        self._conn.execute(
                            """
                            SELECT t.archive_id, t.thread_id, t.created_at, t.combined_text,
                                   bm25(archive_turns_fts) AS score
                            FROM archive_turns_fts
                            JOIN archive_turns AS t USING (archive_id)
                            WHERE archive_turns_fts MATCH ?
                            ORDER BY score
                            LIMIT ?
                            """,
                            (normalized, limit),
                        )
                    )
                else:
                    raise sqlite3.OperationalError("fts disabled")
            except sqlite3.OperationalError:
                like = f"%{normalized}%"
                rows = list(
                    self._conn.execute(
                        """
                        SELECT archive_id, thread_id, created_at, combined_text, 1.0 AS score
                        FROM archive_turns
                        WHERE combined_text LIKE ?
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (like, limit),
                    )
                )

        hits = tuple(
            ArchiveSearchHit(
                archive_id=row["archive_id"],
                thread_id=row["thread_id"],
                score=float(row["score"]),
                excerpt=(row["combined_text"] or "")[:240],
                created_at=_parse_dt(row["created_at"]),
            )
            for row in rows
        )
        return ArchiveSearchResult(query=query, hits=hits)

    def count(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM archive_turns")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def list_thread_turns(self, thread_id: str, limit: int = 5) -> tuple[ArchiveTurnRecord, ...]:
        with self._lock:
            rows = list(
                self._conn.execute(
                    """
                    SELECT archive_id, thread_id, user_content, assistant_content, status, created_at
                    FROM archive_turns
                    WHERE thread_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (thread_id, max(limit, 1)),
                )
            )
        return tuple(
            ArchiveTurnRecord(
                archive_id=row["archive_id"],
                thread_id=row["thread_id"],
                user_content=row["user_content"],
                assistant_content=row["assistant_content"],
                status=row["status"],
                created_at=_parse_dt(row["created_at"]),
            )
            for row in rows
        )

    def delete_thread(self, thread_id: str) -> int:
        with self._lock:
            rows = self._conn.execute(
                "SELECT archive_id FROM archive_turns WHERE thread_id = ?",
                (thread_id,),
            ).fetchall()
            archive_ids = [row["archive_id"] for row in rows]
            self._conn.execute("DELETE FROM archive_turns WHERE thread_id = ?", (thread_id,))
            if self._fts_enabled and archive_ids:
                self._conn.executemany(
                    "DELETE FROM archive_turns_fts WHERE archive_id = ?",
                    [(archive_id,) for archive_id in archive_ids],
                )
            self._conn.commit()
        return len(archive_ids)

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS archive_turns (
                    archive_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    user_content TEXT NOT NULL,
                    assistant_content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    combined_text TEXT NOT NULL
                )
                """
            )
            if self._fts_enabled:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS archive_turns_fts
                    USING fts5(archive_id, thread_id, combined_text)
                    """
                )
            self._conn.commit()


def _parse_dt(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
