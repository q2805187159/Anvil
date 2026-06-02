from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProcessSessionStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    KILLED = "killed"
    INTERRUPTED = "interrupted"


class TerminalBackendKind(str, Enum):
    LOCAL = "local"
    DOCKER = "docker"
    SSH = "ssh"
    SINGULARITY = "singularity"
    MODAL = "modal"
    DAYTONA = "daytona"
    VERCEL = "vercel"


class TerminalBackendCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TerminalBackendKind = TerminalBackendKind.LOCAL
    backend_id: str = "local"
    label: str = "Local shell"
    interactive: bool = True
    persistent_sessions: bool = True
    pty: bool = False
    stdin: bool = True
    incremental_log: bool = True
    interrupt: bool = True
    remote: bool = False
    isolated: bool = False
    configured: bool = True
    executable: bool = True
    launch_mode: str = "local_process"
    workspace_sync: str = "local"
    required_config: list[str] = Field(default_factory=list)
    missing_config: list[str] = Field(default_factory=list)
    required_executables: list[str] = Field(default_factory=list)
    missing_executables: list[str] = Field(default_factory=list)
    env_passthrough: list[str] = Field(default_factory=list)
    env_prefix_passthrough: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TerminalBackendMount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_path: str
    container_path: str
    read_only: bool = False


class TerminalBackendSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TerminalBackendKind = TerminalBackendKind.LOCAL
    backend_id: str = "local"
    label: str | None = None
    command_prefix: list[str] = Field(default_factory=list)
    default_cwd: str | None = None
    env: dict[str, Any] = Field(default_factory=dict)
    env_passthrough: list[str] = Field(default_factory=list)
    env_prefix_passthrough: list[str] = Field(default_factory=list)
    timeout_seconds: int | None = None
    lifetime_seconds: int | None = None
    image: str | None = None
    host: str | None = None
    username: str | None = None
    sandbox_id: str | None = None
    app: str | None = None
    runtime: str | None = None
    working_dir: str | None = None
    resource_limits: dict[str, str] = Field(default_factory=dict)
    sync: dict[str, Any] = Field(default_factory=dict)
    mounts: list[TerminalBackendMount] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProcessInputEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_preview: str = ""
    submitted: bool = False
    byte_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class ProcessSessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    thread_id: str
    command: str
    cwd: str
    backend: TerminalBackendKind = TerminalBackendKind.LOCAL
    backend_id: str = "local"
    backend_label: str = "Local shell"
    interactive: bool = True
    pty: bool = False
    pid: int | None = None
    status: ProcessSessionStatus
    exit_code: int | None = None
    detached: bool = False
    log_cursor: int = 0
    stdin_closed: bool = False
    last_stdin_at: datetime | None = None
    last_signal: str | None = None
    last_signal_at: datetime | None = None
    columns: int | None = None
    rows: int | None = None
    input_history: list[ProcessInputEvent] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    log_path: str
    last_output: str = ""


class ProcessLogView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: ProcessSessionStatus
    output: str
    total_lines: int
    showing: str
    next_offset: int
    start_offset: int = 0
    backend: TerminalBackendKind = TerminalBackendKind.LOCAL
    incremental: bool = True
