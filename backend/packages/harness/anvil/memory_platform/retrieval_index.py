from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from threading import Lock

from .contracts import ArchiveTurnRecord, CuratedEntry


def _query_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[\w\-/]{2,}", query, flags=re.UNICODE) if term.strip()]


def _fts_query(query: str) -> str:
    terms = _query_terms(query)
    if not terms:
        return ""
    return " OR ".join(_quote_fts_term(term) for term in terms[:12])


def _quote_fts_term(term: str) -> str:
    escaped = term.replace('"', '""')
    return f'"{escaped}"'


def _like_query(query: str) -> str:
    return f"%{query.strip()}%"


def _score_from_rank(rank: float) -> float:
    if rank < 0:
        return abs(rank)
    return 1.0 / (1.0 + rank)


class RetrievalIndexStore:
    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path).expanduser().resolve()
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._fts_available = True
        self._initialize()

    def close(self) -> None:
        self._conn.close()

    def upsert_memory_entry(self, entry: CuratedEntry) -> None:
        memory_id = entry.memory_id or entry.entry_id
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_index (memory_id, store_id, layer_id, thread_id, content, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    store_id = excluded.store_id,
                    layer_id = excluded.layer_id,
                    thread_id = excluded.thread_id,
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (
                    memory_id,
                    entry.store_id,
                    entry.layer_id or "",
                    entry.thread_id,
                    entry.content,
                    entry.updated_at.isoformat(),
                ),
            )
            if self._fts_available:
                self._conn.execute("DELETE FROM memory_index_fts WHERE memory_id = ?", (memory_id,))
                self._conn.execute(
                    """
                    INSERT INTO memory_index_fts (memory_id, store_id, layer_id, thread_id, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (memory_id, entry.store_id, entry.layer_id or "", entry.thread_id, entry.content),
                )
            self._conn.commit()

    def delete_memory_entry(self, memory_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM memory_index WHERE memory_id = ?", (memory_id,))
            if self._fts_available:
                self._conn.execute("DELETE FROM memory_index_fts WHERE memory_id = ?", (memory_id,))
            self._conn.commit()

    def upsert_archive_turn(self, record: ArchiveTurnRecord) -> None:
        content = f"{record.user_content}\n{record.assistant_content}".strip()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO archive_index (archive_id, thread_id, content, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(archive_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (
                    record.archive_id,
                    record.thread_id,
                    content,
                    record.created_at.isoformat(),
                ),
            )
            if self._fts_available:
                self._conn.execute("DELETE FROM archive_index_fts WHERE archive_id = ?", (record.archive_id,))
                self._conn.execute(
                    """
                    INSERT INTO archive_index_fts (archive_id, thread_id, content)
                    VALUES (?, ?, ?)
                    """,
                    (record.archive_id, record.thread_id, content),
                )
            self._conn.commit()

    def search_memory(self, query: str, *, limit: int = 5) -> list[dict[str, object]]:
        normalized = query.strip()
        if not normalized or normalized == "*":
            return []
        with self._lock:
            rows = self._search_memory_rows(normalized, limit=max(limit, 1))
        return [
            {
                "memory_id": row["memory_id"],
                "store_id": row["store_id"],
                "layer_id": row["layer_id"] or None,
                "thread_id": row["thread_id"],
                "content": row["content"],
                "score": _score_from_rank(float(row["rank_score"])),
            }
            for row in rows
        ]

    def search_archive(self, query: str, *, limit: int = 5, exclude_thread_id: str | None = None) -> list[dict[str, object]]:
        normalized = query.strip()
        if not normalized or normalized == "*":
            return []
        with self._lock:
            rows = self._search_archive_rows(normalized, limit=max(limit, 1), exclude_thread_id=exclude_thread_id)
        return [
            {
                "archive_id": row["archive_id"],
                "thread_id": row["thread_id"],
                "content": row["content"],
                "score": _score_from_rank(float(row["rank_score"])),
            }
            for row in rows
        ]

    def _search_memory_rows(self, query: str, *, limit: int) -> list[sqlite3.Row]:
        fts_query = _fts_query(query)
        if self._fts_available and fts_query:
            try:
                return list(
                    self._conn.execute(
                        """
                        SELECT m.memory_id, m.store_id, m.layer_id, m.thread_id, m.content,
                               bm25(memory_index_fts) AS rank_score
                        FROM memory_index_fts
                        JOIN memory_index AS m USING (memory_id)
                        WHERE memory_index_fts MATCH ?
                        ORDER BY rank_score ASC
                        LIMIT ?
                        """,
                        (fts_query, limit),
                    )
                )
            except sqlite3.OperationalError:
                pass
        return list(
            self._conn.execute(
                """
                SELECT memory_id, store_id, layer_id, thread_id, content, 1.0 AS rank_score
                FROM memory_index
                WHERE content LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (_like_query(query), limit),
            )
        )

    def _search_archive_rows(self, query: str, *, limit: int, exclude_thread_id: str | None) -> list[sqlite3.Row]:
        fts_query = _fts_query(query)
        exclude_clause = "AND a.thread_id != ?" if exclude_thread_id is not None else ""
        if self._fts_available and fts_query:
            try:
                params: tuple[object, ...] = (fts_query, exclude_thread_id, limit) if exclude_thread_id is not None else (fts_query, limit)
                return list(
                    self._conn.execute(
                        f"""
                        SELECT a.archive_id, a.thread_id, a.content, bm25(archive_index_fts) AS rank_score
                        FROM archive_index_fts
                        JOIN archive_index AS a USING (archive_id)
                        WHERE archive_index_fts MATCH ? {exclude_clause}
                        ORDER BY rank_score ASC
                        LIMIT ?
                        """,
                        params,
                    )
                )
            except sqlite3.OperationalError:
                pass

        params = (_like_query(query), exclude_thread_id, limit) if exclude_thread_id is not None else (_like_query(query), limit)
        return list(
            self._conn.execute(
                f"""
                SELECT archive_id, thread_id, content, 1.0 AS rank_score
                FROM archive_index AS a
                WHERE content LIKE ? {exclude_clause}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            )
        )

    def _initialize(self) -> None:
        with self._lock:
            self._migrate_legacy_vector_tables()
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_index (
                    memory_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    layer_id TEXT,
                    thread_id TEXT,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS archive_index (
                    archive_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            try:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_index_fts
                    USING fts5(memory_id UNINDEXED, store_id UNINDEXED, layer_id UNINDEXED, thread_id UNINDEXED, content, tokenize='unicode61')
                    """
                )
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS archive_index_fts
                    USING fts5(archive_id UNINDEXED, thread_id UNINDEXED, content, tokenize='unicode61')
                    """
                )
                self._rebuild_fts_tables()
            except sqlite3.OperationalError:
                self._fts_available = False
            self._conn.commit()

    def _migrate_legacy_vector_tables(self) -> None:
        if "vector_json" in self._table_columns("memory_index"):
            rows = list(
                self._conn.execute(
                    "SELECT memory_id, store_id, layer_id, thread_id, content, updated_at FROM memory_index"
                )
            )
            self._conn.execute("DROP TABLE memory_index")
            self._conn.execute(
                """
                CREATE TABLE memory_index (
                    memory_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    layer_id TEXT,
                    thread_id TEXT,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO memory_index (memory_id, store_id, layer_id, thread_id, content, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(row["memory_id"], row["store_id"], row["layer_id"], row["thread_id"], row["content"], row["updated_at"]) for row in rows],
            )
        if "vector_json" in self._table_columns("archive_index"):
            rows = list(self._conn.execute("SELECT archive_id, thread_id, content, updated_at FROM archive_index"))
            self._conn.execute("DROP TABLE archive_index")
            self._conn.execute(
                """
                CREATE TABLE archive_index (
                    archive_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO archive_index (archive_id, thread_id, content, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                [(row["archive_id"], row["thread_id"], row["content"], row["updated_at"]) for row in rows],
            )

    def _rebuild_fts_tables(self) -> None:
        self._conn.execute("DELETE FROM memory_index_fts")
        self._conn.execute(
            """
            INSERT INTO memory_index_fts (memory_id, store_id, layer_id, thread_id, content)
            SELECT memory_id, store_id, layer_id, thread_id, content FROM memory_index
            """
        )
        self._conn.execute("DELETE FROM archive_index_fts")
        self._conn.execute(
            """
            INSERT INTO archive_index_fts (archive_id, thread_id, content)
            SELECT archive_id, thread_id, content FROM archive_index
            """
        )

    def _table_columns(self, table_name: str) -> set[str]:
        rows = list(self._conn.execute(f"PRAGMA table_info({table_name})"))
        return {str(row["name"]) for row in rows}
