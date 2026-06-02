from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SubagentTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    FAILED_RECOVERY = "failed_recovery"


class SubagentTaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    batch_id: str | None = None
    parent_thread_id: str
    parent_run_id: str | None = None
    child_thread_id: str | None = None
    child_run_id: str | None = None
    trace_id: str | None = None
    prompt_preview: str | None = None
    status: SubagentTaskStatus = SubagentTaskStatus.QUEUED
    assigned_profile: str = "general"
    delegation_depth: int = 0
    workspace_mode: str = "inherited_parent_workspace"
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    timeout_at: datetime | None = None
    cancel_requested: bool = False
    requested_tool_names: tuple[str, ...] = ()
    allowed_tool_names: tuple[str, ...] = ()
    depends_on_task_ids: tuple[str, ...] = ()
    error: str | None = None


class SubagentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: SubagentTaskStatus
    summary: str
    child_thread_id: str | None = None
    child_run_id: str | None = None
    artifacts: tuple[dict[str, Any], ...] = ()
    messages: tuple[dict[str, Any], ...] = ()
    recent_tool_activity: tuple[dict[str, Any], ...] = ()
    approval_payload: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    trace_id: str | None = None


class SubagentEventType(str, Enum):
    JOB_SUBMITTED = "job_submitted"
    JOB_STARTED = "job_started"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MODEL_RESPONSE = "model_response"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_CANCELLED = "job_cancelled"
    JOB_TIMED_OUT = "job_timed_out"
    JOB_INTERRUPTED = "job_interrupted"


class SubagentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    parent_thread_id: str
    parent_run_id: str | None = None
    event_type: SubagentEventType
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)
