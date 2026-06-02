from __future__ import annotations

from datetime import datetime

from .contracts import SubagentResult, SubagentTaskRecord, SubagentTaskStatus


class InMemorySubagentRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, SubagentTaskRecord] = {}
        self._results: dict[str, SubagentResult] = {}

    def add_task(self, task: SubagentTaskRecord) -> None:
        self._tasks[task.task_id] = task.model_copy(deep=True)

    def update_task(self, task: SubagentTaskRecord) -> None:
        self._tasks[task.task_id] = task.model_copy(deep=True)

    def get_task(self, task_id: str) -> SubagentTaskRecord | None:
        task = self._tasks.get(task_id)
        return task.model_copy(deep=True) if task is not None else None

    def list_tasks(self) -> tuple[SubagentTaskRecord, ...]:
        return tuple(self._tasks[task_id].model_copy(deep=True) for task_id in sorted(self._tasks))

    def active_count(self) -> int:
        return sum(
            1
            for task in self._tasks.values()
            if task.status in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}
        )

    def put_result(self, result: SubagentResult) -> None:
        self._results[result.task_id] = result.model_copy(deep=True)

    def get_result(self, task_id: str) -> SubagentResult | None:
        result = self._results.get(task_id)
        return result.model_copy(deep=True) if result is not None else None

    def terminalize_task(
        self,
        task_id: str,
        *,
        status: SubagentTaskStatus,
        summary: str = "",
        error: str | None = None,
        completed_at: datetime | None = None,
    ) -> SubagentTaskRecord:
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"unknown task id: {task_id}")

        task = task.model_copy(deep=True)
        task.status = status
        task.error = error
        task.completed_at = completed_at
        self._tasks[task_id] = task
        self._results[task_id] = SubagentResult(
            task_id=task.task_id,
            status=status,
            summary=summary,
            error=error,
            started_at=task.started_at,
            completed_at=completed_at,
            trace_id=task.trace_id,
        )
        return task.model_copy(deep=True)

    def delete_for_parent_thread(self, parent_thread_id: str) -> int:
        task_ids = [task_id for task_id, task in self._tasks.items() if task.parent_thread_id == parent_thread_id]
        for task_id in task_ids:
            self._tasks.pop(task_id, None)
            self._results.pop(task_id, None)
        return len(task_ids)
