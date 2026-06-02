# BOUNDARY: agent-runtime-only
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages

from anvil.agents import SandboxState, ThreadDataState
from anvil.agents.user_interaction import UserInteractionRequest
from anvil.runtime.approvals import ApprovalDecision, ApprovalRequest
from anvil.runtime.tool_registry.contracts import CapabilityBundle

if TYPE_CHECKING:
    from anvil.sandbox.path_service import PathService


class TodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    content: str
    status: str = "pending"
    created_at: str
    depends_on: list[str] = Field(default_factory=list)
    # Constraints:
    # - status transitions: pending -> done|skipped and done -> pending are allowed
    # - skipped todos do not block dependents
    # - circular depends_on chains are rejected by TodoMiddleware
    # - content is descriptive only; execution remains model-directed


class MemoryInjectionDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = "none"
    status: str = "not_used"
    snapshot_id: str | None = None
    query_tokens: int = 0
    curated_match_count: int = 0
    archive_hit_count: int = 0
    evidence_count: int = 0
    provider_note_count: int = 0
    summary_present: bool = False
    rendered_tokens_before_truncation: int = 0
    rendered_tokens: int = 0
    token_budget: int | None = None
    truncated: bool = False
    error_type: str | None = None
    store_counts: dict[str, int] = Field(default_factory=dict)
    source_kind_counts: dict[str, int] = Field(default_factory=dict)


class LeadAgentState(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)
    thread_data: ThreadDataState | None = None
    sandbox_state: SandboxState | None = None
    visible_tool_names: list[str] = Field(default_factory=list)
    deferred_tool_names: list[str] = Field(default_factory=list)
    capability_bundle_fingerprint: str | None = None
    enabled_skill_ids: list[str] = Field(default_factory=list)
    pending_approval: ApprovalDecision | None = None
    approval_request: ApprovalRequest | None = None
    approval_request_reason: str | None = None
    clarification_requested: bool = False
    clarification_prompt: str | None = None
    pending_user_interaction: UserInteractionRequest | None = None
    prompt_snapshot_id: str | None = None
    memory_snapshot_id: str | None = None
    memory_context: str | None = None
    memory_injection_diagnostics: dict[str, Any] = Field(default_factory=dict)
    upload_context: str | None = None
    uploaded_files: list[dict[str, Any]] = Field(default_factory=list)
    token_usage: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None
    summary: str | None = None
    active_subagent_tasks: list[dict[str, Any]] = Field(default_factory=list)
    todos: list[TodoItem] = Field(default_factory=list)
    viewed_images: list[str] = Field(default_factory=list)
    loop_iteration_count: int = 0
    summarization_triggered: bool = False
    compaction_diagnostics: dict[str, Any] = Field(default_factory=dict)
    compaction_level: int = 0
    compaction_level_label: str | None = None
    compaction_reason: str | None = None
    compaction_input_tokens: int | None = None
    compaction_summary_tokens: int | None = None
    compaction_keep_recent_turns: int | None = None
    emergency_summarize_triggered: bool = False
    emergency_summarize_reason: str | None = None
    stream_interrupted: bool = False
    stream_partial_content: str | None = None
    interrupted_stream: bool = False
    interrupted_stream_reason: str | None = None
    turn_index: int = 0


@dataclass
class LeadAgentContext:
    thread_id: str
    path_service: PathService
    sandbox_provider: Any
    capability_bundle: CapabilityBundle
    run_id: str | None = None
    active_model_name: str | None = None
    active_reasoning_effort: str | None = None
    request_context: str | None = None
    approval_context: str | None = None
    execution_mode: str = "agent"
    upload_context: str | None = None
    memory_context: str | None = None
    memory_injection_diagnostics: dict[str, Any] = field(default_factory=dict)
    todo_context: str | None = None
    summary_context: str | None = None
    view_image_context: str | None = None
    memory_namespace: str | None = None
    promoted_capabilities: tuple[str, ...] = ()
    enabled_skill_ids: tuple[str, ...] = ()
    extension_statuses: tuple[str, ...] = ()
    thread_data: ThreadDataState | None = None
    initial_uploaded_files: tuple[dict[str, Any], ...] = ()
    recent_upload_filenames: tuple[str, ...] = ()
    existing_thread_title: str | None = None
    current_title: str | None = None
    sandbox_handle: Any | None = None
    parent_visible_tool_names: tuple[str, ...] | None = None
    tool_registry: Any | None = None
    memory_service: Any | None = None
    memory_manager: Any | None = None
    skills_service: Any | None = None
    extensions_service: Any | None = None
    subagent_service: Any | None = None
    process_service: Any | None = None
    scheduled_task_service: Any | None = None
    approval_service: Any | None = None
    capability_service: Any | None = None
    tracing_service: Any | None = None
    run_trace_id: str | None = None
    promotion_state: set[str] = field(default_factory=set)
    config_result: Any | None = None
    feature_set: Any | None = None
    prompt_snapshot: Any | None = None
    project_context_files: tuple[dict[str, Any], ...] = ()
    project_context_fingerprint: str | None = None
    runtime_path_fingerprint: str | None = None
    runtime_path_cache_status: str | None = None
    is_plan_mode: bool = False
    thread_runtime_capabilities: dict[str, Any] = field(default_factory=dict)
    model_fallback_history: list[dict[str, Any]] = field(default_factory=list)
    emergency_summarize_triggered: bool = False
    emergency_summarize_reason: str | None = None
    summarization_triggered: bool = False
    compaction_diagnostics: dict[str, Any] = field(default_factory=dict)
    compaction_level: int = 0
    compaction_level_label: str | None = None
    compaction_reason: str | None = None
    compaction_input_tokens: int | None = None
    compaction_summary_tokens: int | None = None
    compaction_keep_recent_turns: int | None = None
    interrupted_stream: bool = False
    interrupted_stream_reason: str | None = None
    stream_partial_content: str | None = None
    run_phase_timings: dict[str, Any] = field(default_factory=dict)
    runtime_assembly_diff: dict[str, Any] = field(default_factory=dict)
