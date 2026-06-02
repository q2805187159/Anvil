from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock

from .contracts import MemoryTrace


class MemoryTraceStore:
    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path).expanduser().resolve()
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self._conn.close()

    def record(self, trace: MemoryTrace) -> MemoryTrace:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memory_traces (
                    trace_id,
                    thread_id,
                    query,
                    trace_kind,
                    target_id,
                    provider_notes_json,
                    evidence_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.trace_id,
                    trace.thread_id,
                    trace.query,
                    trace.trace_kind,
                    trace.target_id,
                    json.dumps(list(trace.provider_notes), ensure_ascii=False),
                    json.dumps([item.model_dump(mode="json") for item in trace.evidence], ensure_ascii=False, default=str),
                    trace.created_at.isoformat(),
                ),
            )
            self._conn.commit()
        return trace

    def list_traces(
        self,
        *,
        thread_id: str | None = None,
        target_id: str | None = None,
        limit: int = 20,
    ) -> tuple[MemoryTrace, ...]:
        clauses: list[str] = []
        params: list[object] = []
        if thread_id is not None:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        if target_id is not None:
            clauses.append("target_id = ?")
            params.append(target_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM memory_traces
            {where}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(max(limit, 1))
        with self._lock:
            rows = list(self._conn.execute(sql, params))
        return tuple(self._row_to_trace(row) for row in rows)

    def _row_to_trace(self, row: sqlite3.Row) -> MemoryTrace:
        evidence_payload = json.loads(row["evidence_json"] or "[]")
        provider_notes = tuple(json.loads(row["provider_notes_json"] or "[]"))
        return MemoryTrace.model_validate(
            {
                "trace_id": row["trace_id"],
                "thread_id": row["thread_id"],
                "query": row["query"],
                "trace_kind": row["trace_kind"],
                "target_id": row["target_id"],
                "provider_notes": provider_notes,
                "evidence": evidence_payload,
                "created_at": row["created_at"],
            }
        )

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_traces (
                    trace_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    query TEXT,
                    trace_kind TEXT NOT NULL,
                    target_id TEXT,
                    provider_notes_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()
