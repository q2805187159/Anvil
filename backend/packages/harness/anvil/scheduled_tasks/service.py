from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Callable
from uuid import uuid4

from anvil.memory_platform.scrubber import MemorySecretScrubber

from .contracts import (
    ScheduledTask,
    ScheduledTaskAutomationRunResult,
    ScheduledTaskAutomationStatus,
    ScheduledTaskCreateRequest,
    ScheduledTaskExecution,
    ScheduledTaskRunResult,
    ScheduledTaskSchedule,
    ScheduledTaskScheduleKind,
    ScheduledTaskStatus,
    ScheduledTaskUpdateRequest,
    utc_now,
)


TaskExecutor = Callable[[ScheduledTask], ScheduledTaskExecution]


_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_previous_instructions", re.compile(r"(?i)\b(ignore|override|forget)\b.{0,80}\b(previous|system|developer)\b.{0,40}\binstructions?\b")),
    ("system_prompt_exfiltration", re.compile(r"(?i)\b(show|print|reveal|dump|exfiltrate)\b.{0,80}\b(system prompt|developer message|hidden instructions?)\b")),
    ("secret_exfiltration", re.compile(r"(?i)\b(print|dump|exfiltrate|send)\b.{0,80}\b(api[_-]?key|token|secret|credential|password)\b")),
    ("recursive_scheduler", re.compile(r"(?i)\b(create|schedule|add)\b.{0,40}\b(cron|scheduled task|automation)\b")),
)


class ScheduledTaskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def load(self) -> tuple[dict[str, ScheduledTask], dict[str, list[ScheduledTaskExecution]]]:
        if not self.path.exists():
            return {}, {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        tasks = {
            item["task_id"]: ScheduledTask.model_validate(item)
            for item in payload.get("tasks", [])
        }
        executions: dict[str, list[ScheduledTaskExecution]] = {}
        for item in payload.get("executions", []):
            execution = ScheduledTaskExecution.model_validate(item)
            executions.setdefault(execution.task_id, []).append(execution)
        return tasks, executions

    def save(
        self,
        tasks: dict[str, ScheduledTask],
        executions: dict[str, list[ScheduledTaskExecution]],
    ) -> None:
        payload = {
            "tasks": [task.model_dump(mode="json") for task in tasks.values()],
            "executions": [
                execution.model_dump(mode="json")
                for task_executions in executions.values()
                for execution in task_executions[-25:]
            ],
        }
        with self._lock:
            self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class ScheduledTaskService:
    def __init__(
        self,
        *,
        store: ScheduledTaskStore,
        executor: TaskExecutor | None = None,
        enabled: bool = True,
        prompt_safety_scan_enabled: bool = True,
    ) -> None:
        self.store = store
        self.executor = executor
        self.enabled = enabled
        self.prompt_safety_scan_enabled = prompt_safety_scan_enabled
        self._lock = Lock()
        self._tasks, self._executions = self.store.load()

    def list_tasks(self, *, include_disabled: bool = True) -> tuple[ScheduledTask, ...]:
        tasks = self._tasks.values()
        if not include_disabled:
            tasks = [task for task in tasks if task.enabled]
        return tuple(sorted(tasks, key=lambda item: (item.next_run_at or datetime.max.replace(tzinfo=timezone.utc), item.name)))

    def list_executions(self, task_id: str | None = None, *, limit: int = 50) -> tuple[ScheduledTaskExecution, ...]:
        if task_id is not None:
            executions = list(self._executions.get(task_id, ()))
        else:
            executions = [execution for values in self._executions.values() for execution in values]
        executions.sort(key=lambda item: item.started_at, reverse=True)
        return tuple(executions[: max(limit, 0)])

    def automation_status(
        self,
        *,
        now: datetime | None = None,
        tick_seconds: int = 60,
        max_due_per_tick: int = 3,
        recent_limit: int = 5,
    ) -> ScheduledTaskAutomationStatus:
        now = now or utc_now()
        tasks = self.list_tasks(include_disabled=True)
        enabled_tasks = [task for task in tasks if task.enabled]
        due_tasks = [task for task in enabled_tasks if self._is_due(task, now)]
        running_tasks = [task for task in tasks if task.status is ScheduledTaskStatus.RUNNING]
        failed_tasks = [task for task in tasks if task.status is ScheduledTaskStatus.FAILED]
        recent = self.list_executions(limit=recent_limit)
        latest_execution = recent[0] if recent else None
        last_run_at = max((task.last_run_at for task in tasks if task.last_run_at is not None), default=None)
        if latest_execution is not None and (
            last_run_at is None or latest_execution.started_at > last_run_at
        ):
            last_run_at = latest_execution.started_at
        next_run_at = min((task.next_run_at for task in enabled_tasks if task.next_run_at is not None), default=None)
        return ScheduledTaskAutomationStatus(
            enabled=bool(self.enabled),
            tick_seconds=max(int(tick_seconds), 10),
            max_due_per_tick=max(int(max_due_per_tick), 1),
            task_count=len(tasks),
            enabled_task_count=len(enabled_tasks),
            due_count=len(due_tasks),
            running_count=len(running_tasks),
            failed_count=len(failed_tasks),
            next_run_at=next_run_at,
            last_run_at=last_run_at,
            last_execution_id=latest_execution.execution_id if latest_execution is not None else None,
            last_status=latest_execution.status if latest_execution is not None else None,
            last_error=latest_execution.error if latest_execution is not None else None,
            recent_executions=recent,
            reason="ready" if self.enabled else "disabled",
        )

    def run_automation_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 3,
        tick_seconds: int = 60,
    ) -> ScheduledTaskAutomationRunResult:
        if not self.enabled:
            status = self.automation_status(
                now=now,
                tick_seconds=tick_seconds,
                max_due_per_tick=limit,
            )
            return ScheduledTaskAutomationRunResult(status=status, reason="disabled")
        before_due = self.automation_status(
            now=now,
            tick_seconds=tick_seconds,
            max_due_per_tick=limit,
            recent_limit=0,
        ).due_count
        results = self.tick(now=now, limit=limit)
        ran_count = sum(1 for result in results if result.ran)
        status = self.automation_status(
            now=utc_now(),
            tick_seconds=tick_seconds,
            max_due_per_tick=limit,
        )
        reason = "ok" if ran_count else "not_due"
        return ScheduledTaskAutomationRunResult(
            status=status,
            ran_count=ran_count,
            skipped_count=max(before_due - ran_count, 0),
            results=results,
            reason=reason,
        )

    def get_task(self, task_id: str) -> ScheduledTask:
        return self._tasks[task_id]

    def create_task(self, request: ScheduledTaskCreateRequest) -> ScheduledTask:
        self._validate_prompt(request.prompt)
        task_id = request.task_id or f"task-{uuid4().hex[:12]}"
        if task_id in self._tasks:
            raise ValueError(f"scheduled task '{task_id}' already exists")
        schedule = parse_schedule(request.schedule)
        task = ScheduledTask(
            task_id=task_id,
            name=request.name,
            prompt=MemorySecretScrubber().scrub(request.prompt).text,
            schedule=schedule,
            enabled=request.enabled,
            thread_id=request.thread_id,
            execution_mode=request.execution_mode,
            selected_model=request.selected_model,
            selected_profile=request.selected_profile,
            selected_reasoning_effort=request.selected_reasoning_effort,
            promoted_capabilities=request.promoted_capabilities,
            max_runs=request.max_runs,
            delivery=request.delivery,
            metadata=request.metadata,
        )
        task = self._schedule_next(task)
        self._tasks[task.task_id] = task
        self._save()
        return task

    def update_task(self, task_id: str, request: ScheduledTaskUpdateRequest) -> ScheduledTask:
        task = self._tasks[task_id]
        updates = {
            key: value
            for key, value in request.model_dump(exclude_unset=True).items()
            if value is not None
        }
        if "prompt" in updates and updates["prompt"] is not None:
            self._validate_prompt(str(updates["prompt"]))
            updates["prompt"] = MemorySecretScrubber().scrub(str(updates["prompt"])).text
        if "schedule" in updates and updates["schedule"] is not None:
            updates["schedule"] = parse_schedule(str(updates["schedule"]))
        updates["updated_at"] = utc_now()
        task = task.model_copy(update=updates)
        task = self._schedule_next(task)
        self._tasks[task.task_id] = task
        self._save()
        return task

    def pause_task(self, task_id: str) -> ScheduledTask:
        task = self._tasks[task_id].model_copy(update={"enabled": False, "updated_at": utc_now()})
        self._tasks[task_id] = task
        self._save()
        return task

    def resume_task(self, task_id: str) -> ScheduledTask:
        task = self._tasks[task_id].model_copy(update={"enabled": True, "updated_at": utc_now()})
        task = self._schedule_next(task)
        self._tasks[task_id] = task
        self._save()
        return task

    def remove_task(self, task_id: str) -> ScheduledTask:
        task = self._tasks.pop(task_id)
        self._executions.pop(task_id, None)
        self._save()
        return task

    def run_task(self, task_id: str, *, force: bool = False) -> ScheduledTaskRunResult:
        task = self._tasks[task_id]
        if not force and not self._is_due(task, utc_now()):
            return ScheduledTaskRunResult(task=task, ran=False, reason="not_due")
        return self._run_task(task)

    def tick(self, *, now: datetime | None = None, limit: int = 3) -> tuple[ScheduledTaskRunResult, ...]:
        if not self.enabled:
            return ()
        now = now or utc_now()
        results: list[ScheduledTaskRunResult] = []
        for task in self.list_tasks(include_disabled=False):
            if len(results) >= limit:
                break
            if self._is_due(task, now):
                results.append(self._run_task(task))
        return tuple(results)

    def _run_task(self, task: ScheduledTask) -> ScheduledTaskRunResult:
        if self.executor is None:
            return ScheduledTaskRunResult(task=task, ran=False, reason="executor_unavailable")
        started = utc_now()
        running = task.model_copy(
            update={
                "last_run_at": started,
                "last_status": ScheduledTaskStatus.RUNNING.value,
                "last_error": None,
                "updated_at": started,
            }
        )
        self._tasks[task.task_id] = running
        self._save()
        try:
            execution = self.executor(running)
        except Exception as exc:  # noqa: BLE001
            execution = ScheduledTaskExecution(
                execution_id=f"exec-{uuid4().hex[:12]}",
                task_id=task.task_id,
                thread_id=task.thread_id or f"scheduled-{task.task_id}",
                status=ScheduledTaskStatus.FAILED.value,
                started_at=started,
                completed_at=utc_now(),
                error=str(exc),
            )

        completed_at = execution.completed_at or utc_now()
        run_count = running.run_count + 1
        status = ScheduledTaskStatus.COMPLETED.value if execution.status == "completed" else execution.status
        next_task = running.model_copy(
            update={
                "run_count": run_count,
                "last_run_at": started,
                "last_status": status,
                "last_error": execution.error,
                "last_execution_id": execution.execution_id,
                "updated_at": completed_at,
            }
        )
        if next_task.max_runs is not None and run_count >= next_task.max_runs:
            next_task = next_task.model_copy(update={"enabled": False, "next_run_at": None})
        else:
            next_task = self._schedule_next(next_task, after=completed_at)
        self._tasks[task.task_id] = next_task
        self._executions.setdefault(task.task_id, []).append(execution)
        self._save()
        return ScheduledTaskRunResult(task=next_task, execution=execution, ran=True)

    def _is_due(self, task: ScheduledTask, now: datetime) -> bool:
        if not task.enabled:
            return False
        if task.max_runs is not None and task.run_count >= task.max_runs:
            return False
        if task.next_run_at is None:
            return False
        return task.next_run_at <= now

    def _schedule_next(self, task: ScheduledTask, *, after: datetime | None = None) -> ScheduledTask:
        if not task.enabled:
            return task
        next_run_at = compute_next_run(task.schedule, after or utc_now(), last_run_at=task.last_run_at)
        return task.model_copy(update={"next_run_at": next_run_at})

    def _validate_prompt(self, prompt: str) -> None:
        text = str(prompt or "")
        if not text.strip():
            raise ValueError("scheduled task prompt is required")
        if not self.prompt_safety_scan_enabled:
            return
        for rule_id, pattern in _PROMPT_INJECTION_PATTERNS:
            if pattern.search(text):
                raise ValueError(f"scheduled task prompt rejected by safety rule: {rule_id}")

    def _save(self) -> None:
        with self._lock:
            self.store.save(self._tasks, self._executions)


def parse_schedule(value: str) -> ScheduledTaskSchedule:
    original = str(value or "").strip()
    if not original:
        raise ValueError("schedule is required")
    lowered = original.lower()
    if lowered.startswith("every "):
        seconds = parse_duration_seconds(original[6:].strip())
        return ScheduledTaskSchedule(
            kind=ScheduledTaskScheduleKind.INTERVAL,
            display=original,
            interval_seconds=seconds,
        )
    if _looks_like_cron(original):
        validate_cron(original)
        return ScheduledTaskSchedule(
            kind=ScheduledTaskScheduleKind.CRON,
            display=original,
            cron=original,
        )
    if "T" in original or re.match(r"^\d{4}-\d{2}-\d{2}", original):
        run_at = datetime.fromisoformat(original.replace("Z", "+00:00"))
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        return ScheduledTaskSchedule(
            kind=ScheduledTaskScheduleKind.ONCE,
            display=original,
            run_at=run_at.astimezone(timezone.utc),
        )
    seconds = parse_duration_seconds(original)
    return ScheduledTaskSchedule(
        kind=ScheduledTaskScheduleKind.INTERVAL,
        display=original,
        interval_seconds=seconds,
    )


def parse_duration_seconds(value: str) -> int:
    text = str(value or "").strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)?", text)
    if not match:
        raise ValueError(f"invalid duration '{value}'")
    amount = float(match.group(1))
    unit = match.group(2) or "minutes"
    multiplier = 1
    if unit.startswith("m"):
        multiplier = 60
    elif unit.startswith("h"):
        multiplier = 60 * 60
    elif unit.startswith("d"):
        multiplier = 24 * 60 * 60
    return max(int(amount * multiplier), 1)


def compute_next_run(
    schedule: ScheduledTaskSchedule,
    after: datetime,
    *,
    last_run_at: datetime | None = None,
) -> datetime | None:
    anchor = after.astimezone(timezone.utc)
    if schedule.kind == ScheduledTaskScheduleKind.ONCE:
        if last_run_at is not None:
            return None
        return schedule.run_at or anchor
    if schedule.kind == ScheduledTaskScheduleKind.INTERVAL:
        return anchor + timedelta(seconds=schedule.interval_seconds or 3600)
    return next_cron(schedule.cron or "0 0 * * *", anchor)


def validate_cron(expr: str) -> None:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron schedule requires five fields")
    minute_spec, hour_spec, day_spec, month_spec, weekday_spec = parts
    _validate_field(minute_spec, 0, 59)
    _validate_field(hour_spec, 0, 23)
    _validate_field(day_spec, 1, 31)
    _validate_field(month_spec, 1, 12)
    _validate_field(weekday_spec, 0, 6)


def next_cron(expr: str, after: datetime) -> datetime:
    validate_cron(expr)
    minute_spec, hour_spec, day_spec, month_spec, weekday_spec = expr.split()
    candidate = after.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(60 * 24 * 370):
        if (
            _matches_field(candidate.minute, minute_spec)
            and _matches_field(candidate.hour, hour_spec)
            and _matches_field(candidate.day, day_spec)
            and _matches_field(candidate.month, month_spec)
            and _matches_field(candidate.weekday(), weekday_spec)
        ):
            return candidate
        candidate += timedelta(minutes=1)
    return after + timedelta(days=1)


def _looks_like_cron(value: str) -> bool:
    return len(value.split()) == 5


def _validate_field(spec: str, minimum: int, maximum: int) -> None:
    for part in spec.split(","):
        if part == "*":
            continue
        if part.startswith("*/"):
            step = int(part[2:])
            if step <= 0:
                raise ValueError(f"invalid cron step '{part}'")
            continue
        range_part, _, step_part = part.partition("/")
        if step_part:
            step = int(step_part)
            if step <= 0:
                raise ValueError(f"invalid cron step '{part}'")
        if "-" in range_part:
            start_text, end_text = range_part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise ValueError(f"cron field '{part}' range start is greater than end")
            if start < minimum or end > maximum:
                raise ValueError(f"cron field '{part}' out of range {minimum}-{maximum}")
            continue
        value = int(range_part)
        if value < minimum or value > maximum:
            raise ValueError(f"cron field '{part}' out of range {minimum}-{maximum}")


def _matches_field(value: int, spec: str) -> bool:
    if spec == "*":
        return True
    allowed: set[int] = set()
    for part in spec.split(","):
        if part == "*":
            return True
        if part.startswith("*/"):
            step = int(part[2:])
            if value % step == 0:
                return True
            continue
        range_part, _, step_part = part.partition("/")
        step = int(step_part) if step_part else 1
        if "-" in range_part:
            start_text, end_text = range_part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start <= value <= end and (value - start) % step == 0:
                return True
            continue
        allowed.add(int(range_part))
    return value in allowed
