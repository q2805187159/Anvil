from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from .contracts import SubagentResult, SubagentTaskRecord, SubagentTaskStatus


class SqliteSubagentRegistry:
    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._closed = False
        if not self.sqlite_path.exists():
            self.sqlite_path.write_text(json.dumps({"tasks": {}, "results": {}}, ensure_ascii=False), encoding="utf-8")

    def close(self) -> None:
        self._closed = True

    def add_task(self, task: SubagentTaskRecord) -> None:
        self.update_task(task)

    def update_task(self, task: SubagentTaskRecord) -> None:
        with self._lock:
            payload = self._load_unlocked()
            payload["tasks"][task.task_id] = task.model_dump(mode="json")
            self._save_unlocked(payload)

    def get_task(self, task_id: str) -> SubagentTaskRecord | None:
        payload = self._load()
        raw = payload["tasks"].get(task_id)
        return SubagentTaskRecord.model_validate(raw) if raw is not None else None

    def list_tasks(self) -> tuple[SubagentTaskRecord, ...]:
        payload = self._load()
        return tuple(
            SubagentTaskRecord.model_validate(payload["tasks"][task_id])
            for task_id in sorted(payload["tasks"])
        )

    def active_count(self) -> int:
        return sum(
            1
            for task in self.list_tasks()
            if task.status in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}
        )

    def put_result(self, result: SubagentResult) -> None:
        with self._lock:
            payload = self._load_unlocked()
            payload["results"][result.task_id] = result.model_dump(mode="json")
            self._save_unlocked(payload)

    def get_result(self, task_id: str) -> SubagentResult | None:
        payload = self._load()
        raw = payload["results"].get(task_id)
        return SubagentResult.model_validate(raw) if raw is not None else None

    def terminalize_task(
        self,
        task_id: str,
        *,
        status: SubagentTaskStatus,
        summary: str = "",
        error: str | None = None,
        completed_at: datetime | None = None,
    ) -> SubagentTaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task id: {task_id}")

        task.status = status
        task.error = error
        task.completed_at = completed_at
        self.update_task(task)
        self.put_result(
            SubagentResult(
                task_id=task.task_id,
                status=status,
                summary=summary,
                error=error,
                started_at=task.started_at,
                completed_at=completed_at,
                trace_id=task.trace_id,
            )
        )
        return task.model_copy(deep=True)

    def delete_for_parent_thread(self, parent_thread_id: str) -> int:
        with self._lock:
            payload = self._load_unlocked()
            task_ids = [
                task_id
                for task_id, task in payload["tasks"].items()
                if task.get("parent_thread_id") == parent_thread_id
            ]
            for task_id in task_ids:
                payload["tasks"].pop(task_id, None)
                payload["results"].pop(task_id, None)
            self._save_unlocked(payload)
        return len(task_ids)

    def _load(self) -> dict[str, dict[str, dict]]:
        with self._lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> dict[str, dict[str, dict]]:
        if not self.sqlite_path.exists():
            return {"tasks": {}, "results": {}}
        return json.loads(self.sqlite_path.read_text(encoding="utf-8") or '{"tasks": {}, "results": {}}')

    def _save_unlocked(self, payload: dict[str, dict[str, dict]]) -> None:
        self.sqlite_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
