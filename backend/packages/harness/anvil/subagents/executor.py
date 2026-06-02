from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

from .contracts import SubagentEvent, SubagentEventType, SubagentResult, SubagentTaskRecord, SubagentTaskStatus
from .registry import InMemorySubagentRegistry


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SubagentExecutor:
    def __init__(self, *, registry: InMemorySubagentRegistry, max_workers: int = 3, tracing_service=None, event_broker=None, event_persister=None) -> None:
        self.registry = registry
        self.tracing_service = tracing_service
        self.event_broker = event_broker
        self.event_persister = event_persister
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="anvil-subagent")

    def submit(
        self,
        task: SubagentTaskRecord,
        runner: Callable[[], str | SubagentResult | dict[str, object]],
    ) -> Future[str]:
        self.registry.add_task(task)
        future = self._pool.submit(self._run_task, task.task_id, runner)
        return future

    def _run_task(self, task_id: str, runner: Callable[[], str | SubagentResult | dict[str, object]]) -> str:
        task = self.registry.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task id: {task_id}")
        if task.status in {
            SubagentTaskStatus.CANCELLED,
            SubagentTaskStatus.TIMED_OUT,
        }:
            return ""

        task.status = SubagentTaskStatus.RUNNING
        task.started_at = utc_now()
        self.registry.update_task(task)
        self._publish_event(
            task,
            event_type=SubagentEventType.JOB_STARTED,
            payload={"status": task.status.value},
        )

        try:
            raw_result = runner()
            normalized_result = self._normalize_runner_result(task=task, raw_result=raw_result)
            latest = self.registry.get_task(task_id)
            if latest is not None and latest.status in {
                SubagentTaskStatus.CANCELLED,
                SubagentTaskStatus.TIMED_OUT,
            }:
                return normalized_result.summary

            task.status = normalized_result.status
            task.error = normalized_result.error
            task.completed_at = utc_now()
            self.registry.update_task(task)
            self.registry.put_result(
                SubagentResult(
                    task_id=task.task_id,
                    status=normalized_result.status,
                    summary=normalized_result.summary,
                    child_thread_id=normalized_result.child_thread_id or task.child_thread_id,
                    child_run_id=normalized_result.child_run_id or task.child_run_id,
                    artifacts=normalized_result.artifacts,
                    messages=normalized_result.messages,
                    recent_tool_activity=normalized_result.recent_tool_activity,
                    approval_payload=normalized_result.approval_payload,
                    error=normalized_result.error,
                    started_at=task.started_at,
                    completed_at=task.completed_at,
                    trace_id=task.trace_id,
                )
            )
            event_type = self._terminal_event_type(normalized_result.status)
            self._publish_event(
                task,
                event_type=event_type,
                payload={
                    "status": task.status.value,
                    "summary": normalized_result.summary,
                    "child_thread_id": normalized_result.child_thread_id or task.child_thread_id,
                    "child_run_id": normalized_result.child_run_id or task.child_run_id,
                    "error": normalized_result.error,
                },
            )
            if self.tracing_service is not None:
                self.tracing_service.subagent_finished(
                    parent_trace_id=task.trace_id,
                    task_id=task.task_id,
                    status=task.status.value,
                    error=normalized_result.error,
                )
            return normalized_result.summary
        except Exception as exc:  # noqa: BLE001
            latest = self.registry.get_task(task_id)
            if latest is not None and latest.status in {
                SubagentTaskStatus.CANCELLED,
                SubagentTaskStatus.TIMED_OUT,
            }:
                raise
            task.status = SubagentTaskStatus.FAILED
            task.error = str(exc)
            task.completed_at = utc_now()
            self.registry.update_task(task)
            self.registry.put_result(
                SubagentResult(
                    task_id=task.task_id,
                    status=task.status,
                    summary="",
                    error=str(exc),
                    started_at=task.started_at,
                    completed_at=task.completed_at,
                    trace_id=task.trace_id,
                )
            )
            self._publish_event(
                task,
                event_type=SubagentEventType.JOB_FAILED,
                payload={
                    "status": task.status.value,
                    "error": str(exc),
                },
            )
            if self.tracing_service is not None:
                self.tracing_service.subagent_finished(
                    parent_trace_id=task.trace_id,
                    task_id=task.task_id,
                    status=task.status.value,
                    error=str(exc),
                )
            raise

    def default_timeout_at(self, timeout_seconds: int) -> datetime:
        return utc_now() + timedelta(seconds=timeout_seconds)

    def _publish_event(
        self,
        task: SubagentTaskRecord,
        *,
        event_type: SubagentEventType,
        payload: dict[str, object],
    ) -> None:
        event = SubagentEvent(
            job_id=task.task_id,
            parent_thread_id=task.parent_thread_id,
            parent_run_id=task.parent_run_id,
            event_type=event_type,
            payload={
                "task_id": task.task_id,
                "batch_id": task.batch_id,
                "prompt_preview": task.prompt_preview,
                "child_thread_id": task.child_thread_id,
                "child_run_id": task.child_run_id,
                "status": task.status.value,
                "started_at": task.started_at.isoformat() if task.started_at is not None else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at is not None else None,
                **dict(payload),
            },
        )
        if self.event_broker is not None:
            self.event_broker.publish(event)
        if self.event_persister is not None:
            self.event_persister(event)

    def _normalize_runner_result(
        self,
        *,
        task: SubagentTaskRecord,
        raw_result: str | SubagentResult | dict[str, object],
    ) -> SubagentResult:
        if isinstance(raw_result, SubagentResult):
            return raw_result
        if isinstance(raw_result, str):
            return SubagentResult(
                task_id=task.task_id,
                status=SubagentTaskStatus.COMPLETED,
                summary=raw_result,
                child_thread_id=task.child_thread_id,
                child_run_id=task.child_run_id,
                trace_id=task.trace_id,
            )
        if isinstance(raw_result, dict):
            payload = dict(raw_result)
            payload.setdefault("task_id", task.task_id)
            payload.setdefault("status", SubagentTaskStatus.COMPLETED)
            payload.setdefault("summary", "")
            payload.setdefault("child_thread_id", task.child_thread_id)
            payload.setdefault("child_run_id", task.child_run_id)
            payload.setdefault("trace_id", task.trace_id)
            return SubagentResult.model_validate(payload)
        raise TypeError(f"unsupported subagent runner result type: {type(raw_result).__name__}")

    def _terminal_event_type(self, status: SubagentTaskStatus) -> SubagentEventType:
        if status is SubagentTaskStatus.COMPLETED:
            return SubagentEventType.JOB_COMPLETED
        if status is SubagentTaskStatus.CANCELLED:
            return SubagentEventType.JOB_CANCELLED
        if status is SubagentTaskStatus.TIMED_OUT:
            return SubagentEventType.JOB_TIMED_OUT
        if status is SubagentTaskStatus.INTERRUPTED:
            return SubagentEventType.JOB_INTERRUPTED
        return SubagentEventType.JOB_FAILED
