# BOUNDARY: durable-thread
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from anvil.runtime.approvals import ApprovalDecision, ApprovalRequest, NetworkApprovalDecision, PermissionGrant


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def merge_unique_strings(existing: list[str] | None, new: list[str] | None) -> list[str]:
    if not existing:
        return list(dict.fromkeys(new or []))
    if not new:
        return list(existing)
    return list(dict.fromkeys([*existing, *new]))


def merge_mapping(existing: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    if not existing:
        return dict(new or {})
    if not new:
        return dict(existing)
    return {**existing, **new}


class ThreadLifecycleStatus(str, Enum):
    NEW = "new"
    READY = "ready"
    RUNNING = "running"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ARCHIVED = "archived"


class ThreadExecutionMode(str, Enum):
    CHAT = "chat"
    AGENT = "agent"
    FULL_ACCESS = "full_access"


class SandboxState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox_id: str | None = None
    sandbox_mode: str | None = None


class ThreadDataState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_path: str | None = None
    uploads_path: str | None = None
    outputs_path: str | None = None
    external_agent_workspace_root: str | None = None
    workspace_mode: str | None = None
    workspace_root: str | None = None


class ThreadIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    run_id: str | None = None
    parent_thread_id: str | None = None


class ThreadLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ThreadLifecycleStatus = ThreadLifecycleStatus.NEW
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    last_error: str | None = None


class ThreadConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    queued_followups: list[dict[str, Any]] = Field(default_factory=list)
    active_followup_dispatch: dict[str, Any] | None = None
    pending_user_interaction: dict[str, Any] | None = None
    title: str | None = None
    summary: str | None = None
    last_message_at: datetime | None = None


class ArchivedSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_id: str
    summary_text: str
    covers_turn_range: tuple[int, int]
    token_count: int
    created_at: datetime = Field(default_factory=utc_now)
    prompt_snapshot_id: str
    compaction_level: int = 0
    compaction_level_label: str | None = None
    compaction_reason: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class ThreadPlanningState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_snapshot: list[dict[str, Any]] = Field(default_factory=list)


class PromptSnapshotRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str | None = None
    snapshot_hash: str | None = None
    created_at: datetime | None = None
    project_context_fingerprint: str | None = None
    project_context_files: list[dict[str, Any]] = Field(default_factory=list)


class ThreadExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_mode: ThreadExecutionMode = ThreadExecutionMode.AGENT
    is_plan_mode: bool = False
    selected_model: str | None = None
    selected_profile: str | None = None
    selected_reasoning_effort: str | None = None
    active_model: str | None = None
    reasoning_effort: str | None = None
    sandbox_state: SandboxState | None = None
    cancellation_requested: bool = False
    token_usage: dict[str, Any] = Field(default_factory=dict)
    context_window_usage: dict[str, Any] = Field(default_factory=dict)
    runtime_phase_timings: dict[str, Any] = Field(default_factory=dict)
    runtime_assembly_snapshot: dict[str, Any] = Field(default_factory=dict)
    runtime_assembly_diff: dict[str, Any] = Field(default_factory=dict)
    model_fallback_history: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list["RunToolCallRecord"] = Field(default_factory=list)
    recent_tool_activity: list["RecentToolActivity"] = Field(default_factory=list)
    last_message_interrupted: bool = False
    last_message_interrupted_reason: str | None = None


class RunToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    block_id: str | None = None
    sequence: int | None = None
    tool_call_id: str | None = None
    name: str | None = None
    display_name: str | None = None
    source_kind: str | None = None
    source_id: str | None = None
    capability_group: str | None = None
    tool_execution_mode: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any | None = None
    status: str | None = None
    is_error: bool = False
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    visibility: str = "chat"


class RecentToolActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str | None = None
    message_id: str | None = None
    name: str | None = None
    display_name: str | None = None
    source_kind: str | None = None
    source_id: str | None = None
    capability_group: str | None = None
    tool_execution_mode: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    status: str | None = None
    result_text: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None


class ThreadArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_artifacts: list[str] = Field(default_factory=list)
    uploaded_files: list[dict[str, Any]] = Field(default_factory=list)
    presented_artifacts: list[str] = Field(default_factory=list)


class ThreadCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible_tool_names: list[str] = Field(default_factory=list)
    deferred_tool_names: list[str] = Field(default_factory=list)
    enabled_skill_ids: list[str] = Field(default_factory=list)
    capability_bundle_fingerprint: str | None = None


class ThreadMemoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_namespace: str | None = None
    injected_memory_snapshot_id: str | None = None
    procedure_learning_runs: list[str] = Field(default_factory=list)
    procedure_learning_signatures: list[str] = Field(default_factory=list)


class ThreadApprovals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending_approval: ApprovalDecision | None = None
    approval_request: ApprovalRequest | None = None
    granted_permissions: list[PermissionGrant] = Field(default_factory=list)
    granted_network_permissions: list[NetworkApprovalDecision] = Field(default_factory=list)
    session_approval_grants: list[str] = Field(default_factory=list)
    recent_approval_events: list["RecentApprovalEvent"] = Field(default_factory=list)


class RecentApprovalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    decision: str
    reason: str | None = None
    action_kind: str | None = None
    requested_permissions: list[str] = Field(default_factory=list)
    scope_options: list[str] = Field(default_factory=list)
    status: str = "requested"
    execution_mode: ThreadExecutionMode | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class ThreadDelegation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_subagent_tasks: list[dict[str, Any]] = Field(default_factory=list)
    delegation_depth: int = 0
    trace_id: str | None = None


class ThreadState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: ThreadIdentity
    lifecycle: ThreadLifecycle = Field(default_factory=ThreadLifecycle)
    conversation: ThreadConversation = Field(default_factory=ThreadConversation)
    execution: ThreadExecution = Field(default_factory=ThreadExecution)
    thread_data: ThreadDataState = Field(default_factory=ThreadDataState)
    artifacts: ThreadArtifacts = Field(default_factory=ThreadArtifacts)
    capabilities: ThreadCapabilities = Field(default_factory=ThreadCapabilities)
    memory: ThreadMemoryState = Field(default_factory=ThreadMemoryState)
    approvals: ThreadApprovals = Field(default_factory=ThreadApprovals)
    delegation: ThreadDelegation = Field(default_factory=ThreadDelegation)
    planning: ThreadPlanningState = Field(default_factory=ThreadPlanningState)
    archived_summaries: list[ArchivedSummary] = Field(default_factory=list)
    prompt_snapshot: PromptSnapshotRef = Field(default_factory=PromptSnapshotRef)
    durable_subagent_job_history: list[dict[str, Any]] = Field(default_factory=list)


class ThreadMetadataView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    title: str | None = None
    status: ThreadLifecycleStatus
    updated_at: datetime
    last_message_at: datetime | None = None
    last_user_message_preview: str | None = None
    has_pending_approval: bool = False
    has_active_subagent_tasks: bool = False

    @classmethod
    def from_thread_state(cls, state: ThreadState) -> "ThreadMetadataView":
        last_user_message_preview = None
        for message in reversed(state.conversation.messages):
            if message.get("role") in {"user", "human"}:
                content = message.get("content")
                if _is_internal_loop_guard_content(content):
                    continue
                if isinstance(content, str):
                    last_user_message_preview = content[:120]
                break

        return cls(
            thread_id=state.identity.thread_id,
            title=state.conversation.title,
            status=state.lifecycle.status,
            updated_at=state.lifecycle.updated_at,
            last_message_at=state.conversation.last_message_at,
            last_user_message_preview=last_user_message_preview,
            has_pending_approval=state.approvals.pending_approval is not None,
            has_active_subagent_tasks=bool(state.delegation.active_subagent_tasks),
        )


def _is_internal_loop_guard_content(content: object) -> bool:
    return isinstance(content, str) and content.strip().startswith("[LOOP DETECTED]")


ThreadExecution.model_rebuild()
