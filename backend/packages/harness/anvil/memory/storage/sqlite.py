from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ..contracts import Memory


class SQLiteMemoryIndex:
    """SQLite metadata and optional FTS5 index for HCMS memories."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fts_enabled = False
        self._initialize()

    def upsert(self, namespace: str, memory: Memory, *, markdown_path: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    namespace, memory_id, version, category, state, confidence, salience,
                    summary, content, tags, updated_at, markdown_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, memory_id) DO UPDATE SET
                    version=excluded.version,
                    category=excluded.category,
                    state=excluded.state,
                    confidence=excluded.confidence,
                    salience=excluded.salience,
                    summary=excluded.summary,
                    content=excluded.content,
                    tags=excluded.tags,
                    updated_at=excluded.updated_at,
                    markdown_path=excluded.markdown_path
                """,
                (
                    namespace,
                    memory.memory_id,
                    memory.version,
                    memory.category.value,
                    memory.state.value,
                    memory.confidence,
                    memory.salience,
                    memory.summary,
                    memory.content,
                    " ".join(memory.tags),
                    memory.updated_at.isoformat(),
                    markdown_path,
                ),
            )
            if self._fts_enabled:
                conn.execute("DELETE FROM memory_fts WHERE namespace = ? AND memory_id = ?", (namespace, memory.memory_id))
                conn.execute(
                    "INSERT INTO memory_fts(namespace, memory_id, summary, content, tags) VALUES (?, ?, ?, ?, ?)",
                    (namespace, memory.memory_id, memory.summary, memory.content, " ".join(memory.tags)),
                )

    def delete(self, namespace: str, memory_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memories WHERE namespace = ? AND memory_id = ?", (namespace, memory_id))
            if self._fts_enabled:
                conn.execute("DELETE FROM memory_fts WHERE namespace = ? AND memory_id = ?", (namespace, memory_id))

    def search_ids(self, namespace: str, query: str, *, limit: int = 20) -> tuple[str, ...]:
        limit = max(1, min(int(limit), 100))
        terms = _terms(query)
        if not terms:
            return ()
        with self._connect() as conn:
            if self._fts_enabled:
                match = " OR ".join(f'"{term}"' for term in terms)
                try:
                    rows = conn.execute(
                        """
                        SELECT memory_id
                        FROM memory_fts
                        WHERE namespace = ? AND memory_fts MATCH ?
                        LIMIT ?
                        """,
                        (namespace, match, limit),
                    ).fetchall()
                    if rows:
                        return tuple(str(row[0]) for row in rows)
                except sqlite3.OperationalError:
                    pass
            like_terms = [f"%{term}%" for term in terms]
            clauses = " OR ".join(["summary LIKE ? OR content LIKE ? OR tags LIKE ?" for _ in like_terms])
            params: list[object] = [namespace]
            for term in like_terms:
                params.extend([term, term, term])
            params.append(limit)
            rows = conn.execute(
                f"""
                SELECT memory_id
                FROM memories
                WHERE namespace = ? AND ({clauses})
                ORDER BY confidence DESC, salience DESC, updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return tuple(str(row[0]) for row in rows)

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    namespace TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    state TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    salience REAL NOT NULL,
                    summary TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    PRIMARY KEY(namespace, memory_id)
                )
                """
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                    USING fts5(namespace UNINDEXED, memory_id UNINDEXED, summary, content, tags)
                    """
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn


def _terms(query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"[A-Za-z0-9_]{2,}", str(query or "").lower())))
