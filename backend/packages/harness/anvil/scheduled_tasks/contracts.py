from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduledTaskScheduleKind(str, Enum):
    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


class ScheduledTaskStatus(str, Enum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    DISABLED = "disabled"


class ScheduledTaskSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ScheduledTaskScheduleKind
    display: str
    interval_seconds: int | None = None
    cron: str | None = None
    run_at: datetime | None = None


class ScheduledTaskExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    task_id: str
    thread_id: str
    run_id: str | None = None
    status: str
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    summary: str = ""
    error: str | None = None
    output_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScheduledTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    name: str
    prompt: str
    schedule: ScheduledTaskSchedule
    enabled: bool = True
    system_managed: bool = False
    thread_id: str | None = None
    execution_mode: str = "agent"
    selected_model: str | None = None
    selected_profile: str | None = None
    selected_reasoning_effort: str | None = None
    promoted_capabilities: tuple[str, ...] = ()
    max_runs: int | None = None
    run_count: int = 0
    missed_run_policy: str = "skip"
    delivery: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_execution_id: str | None = None

    @property
    def status(self) -> ScheduledTaskStatus:
        if not self.enabled:
            return ScheduledTaskStatus.PAUSED
        if self.max_runs is not None and self.run_count >= self.max_runs:
            return ScheduledTaskStatus.COMPLETED
        if self.last_status == ScheduledTaskStatus.RUNNING.value:
            return ScheduledTaskStatus.RUNNING
        if self.last_status == ScheduledTaskStatus.FAILED.value:
            return ScheduledTaskStatus.FAILED
        return ScheduledTaskStatus.SCHEDULED


class ScheduledTaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    name: str
    prompt: str
    schedule: str
    enabled: bool = True
    thread_id: str | None = None
    execution_mode: str = "agent"
    selected_model: str | None = None
    selected_profile: str | None = None
    selected_reasoning_effort: str | None = None
    promoted_capabilities: tuple[str, ...] = ()
    max_runs: int | None = None
    delivery: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScheduledTaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    prompt: str | None = None
    schedule: str | None = None
    enabled: bool | None = None
    thread_id: str | None = None
    execution_mode: str | None = None
    selected_model: str | None = None
    selected_profile: str | None = None
    selected_reasoning_effort: str | None = None
    promoted_capabilities: tuple[str, ...] | None = None
    max_runs: int | None = None
    delivery: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class ScheduledTaskRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: ScheduledTask
    execution: ScheduledTaskExecution | None = None
    ran: bool
    reason: str | None = None


class ScheduledTaskAutomationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    tick_seconds: int = 60
    max_due_per_tick: int = 3
    task_count: int = 0
    enabled_task_count: int = 0
    due_count: int = 0
    running_count: int = 0
    failed_count: int = 0
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_execution_id: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    recent_executions: tuple[ScheduledTaskExecution, ...] = ()
    reason: str = "ready"


class ScheduledTaskAutomationRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ScheduledTaskAutomationStatus
    ran_count: int = 0
    skipped_count: int = 0
    results: tuple[ScheduledTaskRunResult, ...] = ()
    reason: str = "ok"


class ScheduledTaskList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: tuple[ScheduledTask, ...] = ()
    executions: tuple[ScheduledTaskExecution, ...] = ()
