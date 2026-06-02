from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from anvil.agents import ThreadExecutionMode


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str | None = None
    kind: str | None = None


class HealthView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    phase: str = "phase8"


class ShellCommandArgumentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    placeholder: str
    required: bool = False
    repeatable: bool = False
    values: list[str] = Field(default_factory=list)


class ShellCommandView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    bare_name: str
    aliases: list[str] = Field(default_factory=list)
    description: str
    category: str
    args_hint: str = ""
    action: str
    scopes: list[str] = Field(default_factory=list)
    keybinding: str | None = None
    stream_output: bool = False
    stateful: bool = False
    arguments: list[ShellCommandArgumentView] = Field(default_factory=list)


class ShellCommandCatalogView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commands: list[ShellCommandView] = Field(default_factory=list)
    groups: dict[str, int] = Field(default_factory=dict)
    default_scope: str = "all"
    total: int = 0


class ThreadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = None
    workspace_root: str | None = None


class ThreadView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    title: str | None = None
    status: str
    updated_at: datetime
    last_message_at: datetime | None = None
    last_user_message_preview: str | None = None
    has_pending_approval: bool = False
    has_active_subagent_tasks: bool = False
    source_kind: str | None = None
    source_label: str | None = None
    channel_badge: str | None = None


class CompactionDiagnosticsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compaction_level: int | None = None
    compaction_level_label: str | None = None
    compaction_reason: str | None = None
    summary_source: str | None = None
    summary_model: str | None = None
    summary_error_type: str | None = None
    has_existing_summary: bool | None = None
    archived_message_count: int = 0
    tool_call_count: int = 0
    tool_result_count: int = 0
    image_block_count: int = 0
    truncated_message_count: int = 0
    pruned_tool_result_count: int = 0
    serialized_chars: int | None = None
    serialized_tokens: int | None = None
    summary_prompt_tokens: int | None = None
    compaction_input_tokens: int | None = None
    compaction_summary_tokens: int | None = None
    compaction_savings_tokens: int | None = None
    keep_recent_turns: int | None = None


class TokenUsageBreakdownView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None


class TokenUsageSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    concrete_model: str | None = None
    provider: str | None = None
    request_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    total: TokenUsageBreakdownView | None = None
    last: TokenUsageBreakdownView | None = None
    estimated_cost_usd: float | None = None
    cost_status: str | None = None
    currency: str | None = None
    pricing_source: str | None = None
    provider_models: list[str] = Field(default_factory=list)


class ArchivedSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_id: str
    summary_text: str
    covers_turn_range: list[int] = Field(default_factory=list)
    token_count: int
    created_at: datetime | None = None
    prompt_snapshot_id: str
    compaction_level: int = 0
    compaction_level_label: str | None = None
    compaction_reason: str | None = None
    diagnostics: CompactionDiagnosticsView | None = None


class TodoSnapshotItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    content: str
    status: str = "pending"
    created_at: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class ContextWindowUsageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    concrete_model: str | None = None
    provider: str | None = None
    context_tokens: int | None = None
    estimated_context_tokens: int | None = None
    context_source: str | None = None
    context_breakdown: dict[str, int] = Field(default_factory=dict)
    context_breakdown_percentages: dict[str, float] = Field(default_factory=dict)
    dominant_context_category: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    request_count: int | None = None
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    usage_ratio: float | None = None
    compact_ratio: float | None = None
    compact_status: str = "unknown"
    summarization_triggered: bool = False
    compaction_level: int = 0
    compaction_level_label: str | None = None
    compaction_reason: str | None = None
    compaction_input_tokens: int | None = None
    compaction_summary_tokens: int | None = None
    compaction_savings_tokens: int | None = None
    compaction_keep_recent_turns: int | None = None
    compaction_diagnostics: CompactionDiagnosticsView | None = None
    estimated_cost_usd: float | None = None
    cost_status: str | None = None
    currency: str | None = None
    message_tokens: int | None = None
    system_tokens: int | None = None
    tool_schema_tokens: int | None = None
    skill_tokens: int | None = None
    memory_tokens: int | None = None
    project_context_tokens: int | None = None
    runtime_path_tokens: int | None = None
    autocompact_buffer_tokens: int | None = None
    free_space_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cache_hit_ratio: float | None = None
    cache_savings_tokens: int | None = None


class PromptCacheDiagnosticsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: int = 0
    misses: int = 0
    writes: int = 0
    evictions: int = 0
    bypasses: int = 0
    size_before: int | None = None
    size_after: int | None = None
    net_size_change: int | None = None
    max_entries: int | None = None
    cumulative_hits: int | None = None
    cumulative_misses: int | None = None
    cumulative_writes: int | None = None
    cumulative_evictions: int | None = None
    cumulative_bypasses: int | None = None
    cumulative_size: int | None = None


class PromptSectionTokenLedgerView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_prompt_tokens: int | None = None
    volatile_prompt_tokens: int | None = None
    stable_section_tokens: dict[str, int] = Field(default_factory=dict)
    volatile_section_tokens: dict[str, int] = Field(default_factory=dict)


class ContextCacheDiagnosticsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_context_cache_status: str | None = None
    project_context_fingerprint: str | None = None
    project_context_file_count: int = 0
    project_context_truncated_file_count: int = 0
    project_context_total_chars: int = 0
    project_context_discovery_scanned_path_count: int = 0
    project_context_discovery_max_scanned_paths: int = 0
    project_context_discovery_scan_truncated: bool = False
    project_context_scope_counts: dict[str, int] = Field(default_factory=dict)
    project_context_applies_to_counts: dict[str, int] = Field(default_factory=dict)
    runtime_path_cache_status: str | None = None
    runtime_path_fingerprint: str | None = None
    runtime_path_root_count: int = 0
    runtime_path_host_bridge_count: int = 0


class CapabilityAssemblyDiagnosticsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovered_tool_count: int = 0
    enabled_tool_count: int = 0
    materialized_tool_count: int = 0
    visible_tool_count: int = 0
    deferred_tool_count: int = 0
    active_promotion_count: int = 0
    visible_schema_token_budget: int | None = None
    visible_schema_tokens: int = 0
    deferred_schema_tokens: int = 0
    total_schema_tokens: int = 0
    visible_schema_budget_remaining_tokens: int | None = None
    schema_compacted_tool_count: int = 0
    schema_deferred_tool_count: int = 0
    action_prefilter_deferred_tool_count: int = 0
    sanitizer_truncated_tool_count: int = 0
    assembly_stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    slowest_assembly_stage: str | None = None
    slowest_assembly_stage_duration_ms: int | None = None
    skills_discovery_cache_hit: bool | None = None
    skills_discovery_watch_enabled: bool | None = None
    skills_discovery_root_count: int = 0
    skills_discovery_manifest_count: int = 0
    skills_discovery_enabled_count: int = 0
    skills_discovery_package_count: int = 0
    skills_discovery_stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    slowest_skills_discovery_stage: str | None = None
    slowest_skills_discovery_stage_duration_ms: int | None = None
    visible_by_source_kind: dict[str, int] = Field(default_factory=dict)
    deferred_by_source_kind: dict[str, int] = Field(default_factory=dict)
    visible_by_group: dict[str, int] = Field(default_factory=dict)
    deferred_by_group: dict[str, int] = Field(default_factory=dict)


class MemoryInjectionDiagnosticsView(BaseModel):
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


class RuntimePhaseTimingMarkView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str
    label: str
    elapsed_ms: int
    duration_since_previous_ms: int


class RunEventLogSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_count: int = 0
    run_count: int | None = None
    last_event_id: str | None = None
    last_sequence: int | None = None
    last_kind: str | None = None
    last_run_id: str | None = None


class RuntimePhaseTimingsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    thread_id: str | None = None
    status: str = "unknown"
    started_at: datetime | None = None
    total_elapsed_ms: int | None = None
    runtime_assembly_elapsed_ms: int | None = None
    model_start_wait_ms: int | None = None
    first_model_event_elapsed_ms: int | None = None
    first_content_delta_elapsed_ms: int | None = None
    first_content_wait_ms: int | None = None
    post_content_elapsed_ms: int | None = None
    completed_elapsed_ms: int | None = None
    marks: list[RuntimePhaseTimingMarkView] = Field(default_factory=list)
    event_log: RunEventLogSummaryView | None = None


class RuntimePathRootView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    virtual_path: str
    kind: str
    description: str
    writable: bool = True
    display_root: str | None = None


class ProjectContextFileView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    virtual_path: str
    relative_path: str
    applies_to: str
    scope: str = "."
    truncated: bool = False


class ThreadStateView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    run_id: str | None = None
    status: str
    execution_mode: ThreadExecutionMode = ThreadExecutionMode.AGENT
    is_plan_mode: bool = False
    selected_model: str | None = None
    selected_profile: str | None = None
    selected_reasoning_effort: str | None = None
    effective_model: str | None = None
    title: str | None = None
    summary: str | None = None
    todo_snapshot: list[TodoSnapshotItemView] = Field(default_factory=list)
    archived_summaries: list[ArchivedSummaryView] = Field(default_factory=list)
    prompt_snapshot_id: str | None = None
    prompt_snapshot_hash: str | None = None
    project_context_fingerprint: str | None = None
    project_context_files: list[ProjectContextFileView] = Field(default_factory=list)
    active_model: str | None = None
    reasoning_effort: str | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)
    token_usage_summary: TokenUsageSummaryView | None = None
    context_window_usage: ContextWindowUsageView | None = None
    prompt_cache_diagnostics: PromptCacheDiagnosticsView | None = None
    prompt_section_token_ledger: PromptSectionTokenLedgerView | None = None
    context_cache_diagnostics: ContextCacheDiagnosticsView | None = None
    capability_assembly_diagnostics: CapabilityAssemblyDiagnosticsView | None = None
    memory_injection_diagnostics: MemoryInjectionDiagnosticsView | None = None
    compaction_diagnostics: CompactionDiagnosticsView | None = None
    runtime_phase_timings: RuntimePhaseTimingsView | None = None
    last_message_interrupted: bool = False
    last_message_interrupted_reason: str | None = None
    approval_policy_summary: str | None = None
    allowed_local_actions: list[str] = Field(default_factory=list)
    requires_approval_actions: list[str] = Field(default_factory=list)
    restricted_actions: list[str] = Field(default_factory=list)
    visible_tool_names: list[str] = Field(default_factory=list)
    deferred_tool_names: list[str] = Field(default_factory=list)
    enabled_skill_ids: list[str] = Field(default_factory=list)
    memory_namespace: str | None = None
    injected_memory_snapshot_id: str | None = None
    has_pending_approval: bool = False
    pending_approval_reason: str | None = None
    pending_user_interaction: "UserInteractionRequestView | None" = None
    output_artifacts: list["ArtifactRefView"] = Field(default_factory=list)
    uploaded_files: list["ArtifactRefView"] = Field(default_factory=list)
    presented_artifacts: list["ArtifactRefView"] = Field(default_factory=list)
    workspace_mode: str = "thread"
    workspace_root: str | None = None
    resolved_workspace_path: str | None = None
    uploads_path: str | None = None
    outputs_path: str | None = None
    runtime_path_roots: list["RuntimePathRootView"] = Field(default_factory=list)
    active_subagent_task_ids: list[str] = Field(default_factory=list)
    subagent_tasks: list["SubagentTaskView"] = Field(default_factory=list)
    process_sessions: list["ProcessSessionView"] = Field(default_factory=list)
    durable_subagent_job_history: list["SubagentEventView"] = Field(default_factory=list)
    tool_calls: list["ToolCallRecordView"] = Field(default_factory=list)
    recent_tool_activity: list["ToolActivityView"] = Field(default_factory=list)
    recent_approval_events: list["ApprovalEventView"] = Field(default_factory=list)
    runtime_operator_status: "RuntimeOperatorStatusView" = Field(default_factory=lambda: RuntimeOperatorStatusView())
    last_error: str | None = None
    runtime_capabilities: "RuntimeCapabilitiesView" = Field(default_factory=lambda: RuntimeCapabilitiesView())
    queued_followups: list["QueuedFollowUpView"] = Field(default_factory=list)
    active_followup_dispatch: "QueuedFollowUpDispatchView | None" = None


class MessageContentBlockView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    text: str = ""
    url: str | None = None
    mime_type: str | None = None
    name: str | None = None
    artifact_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReasoningView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    block_count: int = 0
    duration_ms: int | None = None


class ToolCallView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str | None = None
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
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any | None = None
    is_error: bool = False
    visibility: str = "chat"
    sequence: int | None = None


class ToolActivityView(BaseModel):
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


class ToolCallRecordView(BaseModel):
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


class RuntimeTimelineItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    kind: str
    status: str
    title: str
    detail: str | None = None
    timestamp: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    source_id: str | None = None
    source_kind: str | None = None
    hidden: bool = False


class RuntimeOperatorStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "idle"
    active_tool_count: int = 0
    completed_tool_count: int = 0
    failed_tool_count: int = 0
    pending_approval_count: int = 0
    running_process_count: int = 0
    active_subagent_count: int = 0
    latest_activity: str | None = None
    latest_activity_at: datetime | None = None
    runtime_phase_timings: RuntimePhaseTimingsView | None = None
    timeline: list[RuntimeTimelineItemView] = Field(default_factory=list)


class DocumentOutlineEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    line: int | None = None
    truncated: bool = False


class CompanionArtifactView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    label: str
    artifact_url: str | None = None
    virtual_path: str | None = None
    provider: str | None = None
    internal: bool = False
    source_scope: str | None = None


class DocumentExtractionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    provider: str | None = None
    ocr_provider: str | None = None
    page_count: int | None = None
    text_layer_present: bool | None = None
    diagnostics: list[str] = Field(default_factory=list)


class ArtifactRefView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    label: str
    artifact_url: str | None = None
    virtual_path: str | None = None
    source_scope: str | None = None
    internal: bool = False
    extension: str | None = None
    markdown_file: str | None = None
    markdown_virtual_path: str | None = None
    markdown_artifact_url: str | None = None
    companions: list[CompanionArtifactView] = Field(default_factory=list)
    extraction: DocumentExtractionView | None = None
    outline: list[DocumentOutlineEntryView] = Field(default_factory=list)
    outline_preview: list[str] = Field(default_factory=list)
    converter_used: str | None = None
    ocr_used: bool = False
    conversion_error: str | None = None


class ApprovalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    reason: str | None = None
    action_kind: str | None = None
    request_id: str | None = None
    requested_permissions: list[str] = Field(default_factory=list)
    scope_options: list[str] = Field(default_factory=list)


class ApprovalEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    decision: str
    reason: str | None = None
    action_kind: str | None = None
    requested_permissions: list[str] = Field(default_factory=list)
    scope_options: list[str] = Field(default_factory=list)
    status: str
    execution_mode: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class UserInteractionOptionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str | None = None
    recommended: bool = False
    disabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class UserInteractionFieldView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    description: str | None = None
    selection_mode: Literal["single", "multiple", "text"] = "single"
    options: list[UserInteractionOptionView] = Field(default_factory=list)
    min_selections: int = 1
    max_selections: int | None = 1
    allow_custom: bool = False
    custom_label: str | None = None
    placeholder: str | None = None
    required: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class UserInteractionRequestView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    kind: str = "choice"
    title: str | None = None
    question: str
    description: str | None = None
    selection_mode: Literal["single", "multiple", "text"] = "single"
    options: list[UserInteractionOptionView] = Field(default_factory=list)
    min_selections: int = 1
    max_selections: int | None = 1
    allow_custom: bool = False
    custom_label: str | None = None
    placeholder: str | None = None
    required: bool = True
    source_tool_name: str = "ask_clarification"
    fields: list[UserInteractionFieldView] = Field(default_factory=list)


class UserInteractionFieldResponseView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    selected_option_ids: list[str] = Field(default_factory=list)
    custom_response: str | None = None
    free_text: str | None = None


class UserInteractionSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    selected_option_ids: list[str] = Field(default_factory=list)
    custom_response: str | None = None
    free_text: str | None = None
    field_responses: list[UserInteractionFieldResponseView] = Field(default_factory=list)


class MessageStepView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    message_id: str
    type: str
    title: str
    action: str | None = None
    status: str
    duration: str | None = None
    duration_ms: int | None = None
    payload: str = ""
    language: str = "text"
    tool_name: str | None = None
    tool_call_id: str | None = None
    order: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    visibility: str = "chat"
    block_id: str | None = None
    sequence: int | None = None


class MessageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    client_message_id: str | None = None
    role: str
    content: str
    steps: list[MessageStepView] = Field(default_factory=list)
    content_blocks: list[MessageContentBlockView] = Field(default_factory=list)
    reasoning: ReasoningView | None = None
    tool_calls: list[ToolCallView] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    status: str | None = None
    stream_status: str | None = None
    artifact_refs: list[ArtifactRefView] = Field(default_factory=list)
    approval: ApprovalView | None = None


class MessageWindowView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    offset: int = 0
    limit: int | None = None
    returned: int = 0
    has_more_before: bool = False
    has_more_after: bool = False
    truncated: bool = False
    start_message_id: str | None = None
    end_message_id: str | None = None


class StreamCapabilitiesView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supports_step_chain: bool = True
    supports_message_delta: bool = False
    supports_reasoning_delta: bool = False
    supports_structured_events: bool = False


class RuntimeCapabilitiesView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summarization_enabled: bool = False
    plan_mode_enabled: bool = False
    view_image_enabled: bool = False
    memory_enabled: bool = False
    skills_count: int = 0
    mcp_servers_connected: int = 0
    sandbox_mode: str = "unsupported"
    supported_sandbox_modes: list[str] = Field(default_factory=list)
    isolated_sandbox_supported: bool = False
    guardrails_enabled: bool = False


class ThreadDetailView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread: ThreadView
    state: ThreadStateView
    messages: list[MessageView] = Field(default_factory=list)
    message_window: MessageWindowView = Field(default_factory=MessageWindowView)
    pending_approval: ApprovalView | None = None
    pending_user_interaction: UserInteractionRequestView | None = None
    stream_capabilities: StreamCapabilitiesView = Field(default_factory=StreamCapabilitiesView)


class ThreadSettingsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    execution_mode: ThreadExecutionMode
    selected_model: str | None = None
    selected_profile: str | None = None
    selected_reasoning_effort: str | None = None
    is_plan_mode: bool = False
    workspace_root: str | None = None
    workspace_mode: str = "thread"
    anvil_home: str | None = None
    anvil_profile: str | None = None
    anvil_profile_home: str | None = None
    resolved_workspace_path: str | None = None
    runtime_path_roots: list["RuntimePathRootView"] = Field(default_factory=list)


class ThreadSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_mode: ThreadExecutionMode | None = None
    selected_model: str | None = None
    selected_profile: str | None = None
    selected_reasoning_effort: str | None = None
    is_plan_mode: bool | None = None
    workspace_root: str | None = None


class TrajectoryCompressionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    max_turns: int | None = None
    keep_first_turns: int | None = None
    keep_last_turns: int | None = None
    max_message_chars: int | None = None
    max_tool_result_chars: int | None = None
    max_metadata_chars: int | None = None


class TrajectoryExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str | None = None
    include_system: bool | None = None
    include_tools: bool | None = None
    include_tool_args: bool | None = None
    include_metadata: bool | None = None
    include_reasoning: bool | None = None
    include_parsed_tool_calls: bool | None = None
    include_hidden_steps: bool | None = None
    include_artifacts: bool | None = None
    include_approvals: bool | None = None
    include_token_usage: bool | None = None
    scrub_secrets: bool | None = None
    compression: TrajectoryCompressionRequest | None = None


class TrajectoryBatchExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_ids: list[str] = Field(default_factory=list)
    output_path: str | None = None
    write_jsonl: bool | None = None
    include_entries: bool | None = None
    learn_procedures: bool = False
    min_quality_status: str | None = None
    options: TrajectoryExportRequest | None = None


class TrajectoryTurnView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_: str = Field(alias="from")
    value: str
    message_id: str | None = None
    role: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolUsageStatsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = 0
    success_count: int = 0
    error_count: int = 0
    running_count: int = 0
    total_duration_ms: int = 0


class TrajectoryStatsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_count: int = 0
    exported_turn_count: int = 0
    original_turn_count: int = 0
    omitted_turn_count: int = 0
    user_turns: int = 0
    assistant_turns: int = 0
    system_turns: int = 0
    tool_turns: int = 0
    tool_call_count: int = 0
    tool_success_count: int = 0
    tool_error_count: int = 0
    approval_count: int = 0
    artifact_count: int = 0
    completed: bool = False
    interrupted: bool = False
    token_usage: dict[str, Any] = Field(default_factory=dict)
    tool_stats: dict[str, ToolUsageStatsView] = Field(default_factory=dict)


class TrajectoryQualityIssueView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    message: str
    turn_index: int | None = None
    message_id: str | None = None
    tool_name: str | None = None


class TrajectoryQualityReportView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "passed"
    score: float = 1.0
    issues: list[TrajectoryQualityIssueView] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class TrajectoryExportView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    thread_id: str
    run_id: str | None = None
    timestamp: datetime
    model: str | None = None
    completed: bool = False
    conversations: list[TrajectoryTurnView] = Field(default_factory=list)
    stats: TrajectoryStatsView = Field(default_factory=TrajectoryStatsView)
    quality: TrajectoryQualityReportView = Field(default_factory=TrajectoryQualityReportView)
    metadata: dict[str, Any] = Field(default_factory=dict)
    format: str = "anvil"


class TrajectoryBatchManifestView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    format: str = "anvil"
    jsonl_path: str | None = None
    manifest_path: str | None = None
    exported_count: int = 0
    skipped_count: int = 0
    thread_ids: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TrajectoryBatchExportView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exported_count: int
    skipped_count: int = 0
    path: str | None = None
    format: str = "anvil"
    entries: list[TrajectoryExportView] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    manifest: TrajectoryBatchManifestView


class EvaluationReportOptionsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_conversation_preview: bool | None = None
    max_preview_chars: int | None = None
    max_tool_result_chars: int | None = None
    scrub_secrets: bool | None = None
    include_markdown: bool | None = None


class EvaluationReportRequestView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_ids: list[str] = Field(default_factory=list)
    options: EvaluationReportOptionsView | None = None
    evaluator_results: dict[str, "EvaluationReportEvaluatorResultView"] = Field(default_factory=dict)
    write_markdown: bool = False
    output_path: str | None = None


class EvaluationReportEvaluatorResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluator: str = "external"
    score: float | None = None
    passed: bool | None = None
    max_score: float | None = None
    task_id: str | None = None
    run_id: str | None = None
    duration_ms: int | None = None
    summary: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EvaluationReportRuntimeSectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    model: str | None = None
    execution_mode: str | None = None
    reasoning_effort: str | None = None
    runtime_phase_timings: dict[str, Any] = Field(default_factory=dict)
    runtime_phase_diagnostics: dict[str, Any] = Field(default_factory=dict)
    runtime_assembly_snapshot: dict[str, Any] = Field(default_factory=dict)
    runtime_assembly_diff: dict[str, Any] = Field(default_factory=dict)
    context_window_usage: dict[str, Any] = Field(default_factory=dict)
    token_usage: dict[str, Any] = Field(default_factory=dict)
    model_fallback_history: list[dict[str, Any]] = Field(default_factory=list)


class EvaluationReportToolCallView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str | None = None
    message_id: str | None = None
    name: str | None = None
    display_name: str | None = None
    capability_group: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    result_text: str | None = None


class EvaluationReportStepView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    message_id: str | None = None
    type: str
    title: str | None = None
    status: str | None = None
    visibility: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    duration_ms: int | None = None
    order: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    payload_preview: str | None = None
    action_preview: str | None = None
    error_preview: str | None = None


class EvaluationReportStepChainSectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    returned: int = 0
    truncated: bool = False
    visible_step_count: int = 0
    hidden_step_count: int = 0
    open_step_count: int = 0
    error_step_count: int = 0
    type_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    visibility_counts: dict[str, int] = Field(default_factory=dict)
    items: list[EvaluationReportStepView] = Field(default_factory=list)


class EvaluationReportMemorySectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str | None = None
    injected_memory_snapshot_id: str | None = None
    procedure_learning_runs: list[str] = Field(default_factory=list)
    procedure_learning_signatures: list[str] = Field(default_factory=list)


class EvaluationReportCapabilitySectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible_tool_names: list[str] = Field(default_factory=list)
    deferred_tool_names: list[str] = Field(default_factory=list)
    enabled_skill_ids: list[str] = Field(default_factory=list)
    capability_bundle_fingerprint: str | None = None


class EvaluationReportIssueView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    message: str


class EvaluationThreadReportView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    thread_id: str
    run_id: str | None = None
    generated_at: datetime
    title: str | None = None
    task_preview: str | None = None
    final_answer_preview: str | None = None
    outcome: str
    score: float
    evaluator: EvaluationReportEvaluatorResultView | None = None
    runtime: EvaluationReportRuntimeSectionView
    trajectory_quality: TrajectoryQualityReportView = Field(default_factory=TrajectoryQualityReportView)
    stats: TrajectoryStatsView = Field(default_factory=TrajectoryStatsView)
    tool_calls: list[EvaluationReportToolCallView] = Field(default_factory=list)
    step_chain: EvaluationReportStepChainSectionView = Field(default_factory=EvaluationReportStepChainSectionView)
    memory: EvaluationReportMemorySectionView = Field(default_factory=EvaluationReportMemorySectionView)
    capabilities: EvaluationReportCapabilitySectionView = Field(default_factory=EvaluationReportCapabilitySectionView)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    hidden_bug_risks: list[EvaluationReportIssueView] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    markdown: str | None = None


class EvaluationBatchReportView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    generated_at: datetime
    thread_reports: list[EvaluationThreadReportView] = Field(default_factory=list)
    missing_thread_ids: list[str] = Field(default_factory=list)
    score: float = 0.0
    markdown_path: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    markdown: str | None = None


class RunRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    client_message_id: str | None = None
    execution_mode: ThreadExecutionMode = ThreadExecutionMode.AGENT
    selected_model: str | None = None
    selected_reasoning_effort: str | None = None
    profile: str | None = None
    request_context: str | None = None
    approval_context: str | None = None
    upload_context: str | None = None
    uploaded_filenames: list[str] = Field(default_factory=list)
    promoted_capabilities: list[str] = Field(default_factory=list)
    is_plan_mode: bool | None = None
    followup_dispatch_id: str | None = None


QueuedFollowUpMode = Literal["followup", "guidance"]


class QueuedFollowUpCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    mode: QueuedFollowUpMode = "followup"
    insert_position: Literal["front", "back"] = "back"
    execution_mode: ThreadExecutionMode | None = None
    selected_model: str | None = None
    selected_reasoning_effort: str | None = None
    profile: str | None = None
    upload_context: str | None = None
    uploaded_filenames: list[str] = Field(default_factory=list)
    uploaded_file_refs: list["ArtifactRefView"] = Field(default_factory=list)
    promoted_capabilities: list[str] = Field(default_factory=list)
    is_plan_mode: bool | None = None


class QueuedFollowUpUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str | None = None
    mode: QueuedFollowUpMode | None = None


class QueuedFollowUpView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_id: str
    thread_id: str
    message: str
    mode: QueuedFollowUpMode = "followup"
    status: str = "queued"
    created_at: datetime
    updated_at: datetime
    execution_mode: ThreadExecutionMode | None = None
    selected_model: str | None = None
    selected_reasoning_effort: str | None = None
    profile: str | None = None
    upload_context: str | None = None
    uploaded_filenames: list[str] = Field(default_factory=list)
    uploaded_file_refs: list["ArtifactRefView"] = Field(default_factory=list)
    promoted_capabilities: list[str] = Field(default_factory=list)
    is_plan_mode: bool | None = None
    dispatch_id: str | None = None


class QueuedFollowUpDispatchView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: str
    queue_id: str
    started_at: datetime
    status: Literal["dispatching"] = "dispatching"


class MessageEditResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    execution_mode: ThreadExecutionMode | None = None
    selected_model: str | None = None
    selected_reasoning_effort: str | None = None
    profile: str | None = None
    request_context: str | None = None
    approval_context: str | None = None
    upload_context: str | None = None
    promoted_capabilities: list[str] = Field(default_factory=list)
    is_plan_mode: bool | None = None


class ApprovalResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_context: str = "approved for this turn"
    selected_model: str | None = None
    selected_reasoning_effort: str | None = None
    profile: str | None = None
    request_context: str | None = None
    upload_context: str | None = None
    promoted_capabilities: list[str] = Field(default_factory=list)
    is_plan_mode: bool | None = None


class UserInteractionResumeRequest(UserInteractionSubmitRequest):
    selected_model: str | None = None
    selected_reasoning_effort: str | None = None
    profile: str | None = None
    request_context: str | None = None
    upload_context: str | None = None
    promoted_capabilities: list[str] = Field(default_factory=list)
    is_plan_mode: bool | None = None


class ApprovalCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = "Approval cancelled by user"


class ThreadDeleteView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    deleted: bool = True


class RunAcceptedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    status: str


class RunCompletedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    status: str
    assistant_message: str | None = None
    last_error: str | None = None
    thread: ThreadView
    state: ThreadStateView


class RunStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str
    data: dict[str, Any]
    event_id: str | None = None
    sequence: int | None = None
    message_id: str | None = None
    block_id: str | None = None
    visibility: str | None = None
    source: str | None = None


class RunEventReplayView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    run_id: str | None = None
    after_sequence: int | None = None
    next_cursor: int
    has_more: bool = False
    events: list[RunStreamEvent] = Field(default_factory=list)


class SystemEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str
    system_version: int | None = None
    data: dict[str, Any]


class UploadItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    kind: str
    virtual_path: str
    artifact_url: str
    source_scope: str | None = None
    internal: bool = False
    extension: str | None = None
    markdown_file: str | None = None
    markdown_virtual_path: str | None = None
    markdown_artifact_url: str | None = None
    companions: list[CompanionArtifactView] = Field(default_factory=list)
    extraction: DocumentExtractionView | None = None
    outline: list[DocumentOutlineEntryView] = Field(default_factory=list)
    outline_preview: list[str] = Field(default_factory=list)
    converter_used: str | None = None
    ocr_used: bool = False
    conversion_error: str | None = None


class UploadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    files: list[UploadItemView] = Field(default_factory=list)


class ModelView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str | None = None
    description: str | None = None
    available: bool = True
    source: str | None = None
    use: str | None = None
    provider: str
    provider_kind: str | None = None
    model_name: str | None = None
    default_model: str | None = None
    selected_model: str | None = None
    model_catalog: list[str] = Field(default_factory=list)
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    model_context_windows: dict[str, int] = Field(default_factory=dict)
    model_auto_compact_thresholds: dict[str, int] = Field(default_factory=dict)
    base_url: str | None = None
    api_key_env: str | None = None
    default_reasoning_effort: str | None = None
    supports_tool_calling: bool = True
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    supports_vision: bool = False
    supports_image_generation: bool = False
    timeout: float | None = None
    request_timeout: float | None = None
    default_request_timeout: float | None = None
    max_retries: int | None = None
    use_responses_api: bool | None = None
    output_version: str | None = None
    image_generation: dict[str, Any] | None = None
    diagnostics: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    internal_task_default: bool = False
    internal_task_selected_model: str | None = None
    internal_task_available: bool | None = None
    internal_task_health: str | None = None
    internal_task_health_checked_at: str | None = None
    internal_task_health_message: str | None = None


class ModelSelectionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str
    default_reasoning_effort: str | None = None
    internal_task_default: bool | None = None


class ModelSelectionUpdateView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    selected_model: str
    default_reasoning_effort: str | None = None
    internal_task_default: bool = False
    config_path: str
    config_fingerprint: str
    model: ModelView


class ModelHealthCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str | None = None
    subsystem: str = "background_tasks"


class ModelHealthCheckView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    model_name: str
    subsystem: str
    ok: bool
    status: str
    message: str | None = None
    checked_at: str
    latency_ms: int | None = None
    config_fingerprint: str


class ModelProviderPresetView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    display_name: str
    description: str | None = None
    base_url: str | None = None
    api_key_env: str
    provider_kind: str | None = None
    use: str | None = None
    model_catalog: list[str] = Field(default_factory=list)
    default_model: str | None = None
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    default_reasoning_effort: str | None = None
    supports_tool_calling: bool = True
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    supports_vision: bool = False
    supports_image_generation: bool = False
    defaults: dict[str, Any] = Field(default_factory=dict)


class ModelProviderUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    name: str | None = None
    display_name: str | None = None
    description: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    models: list[str] = Field(default_factory=list)
    default_model: str | None = None
    default_reasoning_effort: str | None = None
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    timeout: float | None = None
    request_timeout: float | None = None
    default_request_timeout: float | None = None
    max_retries: int | None = None
    use_responses_api: bool | None = None
    output_version: str | None = None
    default_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    provider_settings: dict[str, Any] | None = None
    when_thinking_enabled: dict[str, Any] | None = None
    when_thinking_disabled: dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None
    image_generation: dict[str, Any] | None = None
    supports_tool_calling: bool | None = None
    supports_thinking: bool | None = None
    supports_reasoning_effort: bool | None = None
    supports_vision: bool | None = None
    supports_image_generation: bool | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class ModelProviderUpsertView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider: str
    config_path: str
    dotenv_path: str | None = None
    config_fingerprint: str
    model: ModelView


class ModelProviderDeleteView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    deleted: bool
    config_path: str
    config_fingerprint: str


class ConfigOverviewMetricView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    enabled: int | None = None
    ready: int | None = None
    available: int | None = None
    disabled: int | None = None
    issue_count: int | None = None
    quality_score: float | None = None
    status: str | None = None
    source_counts: dict[str, int] = Field(default_factory=dict)
    enabled_source_counts: dict[str, int] = Field(default_factory=dict)


class ConfigOverviewView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    config_fingerprint: str
    models: ConfigOverviewMetricView = Field(default_factory=ConfigOverviewMetricView)
    tools: ConfigOverviewMetricView = Field(default_factory=ConfigOverviewMetricView)
    skills: ConfigOverviewMetricView = Field(default_factory=ConfigOverviewMetricView)
    memory: ConfigOverviewMetricView = Field(default_factory=ConfigOverviewMetricView)
    mcp: ConfigOverviewMetricView = Field(default_factory=ConfigOverviewMetricView)
    plugins: ConfigOverviewMetricView = Field(default_factory=ConfigOverviewMetricView)
    scheduled: ConfigOverviewMetricView = Field(default_factory=ConfigOverviewMetricView)


class SkillValidationIssueView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    message: str
    skill_id: str | None = None
    source_root: str | None = None
    path: str | None = None
    field: str | None = None


class SkillFileEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str
    size_bytes: int
    is_binary: bool = False


class SkillView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    title: str
    summary: str
    name: str | None = None
    description: str | None = None
    version: str | None = None
    trust: str | None = None
    allowed_tools: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    readiness: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    platforms: list[str] = Field(default_factory=list)
    related_skills: list[str] = Field(default_factory=list)
    asset_paths: list[str] = Field(default_factory=list)
    template_paths: list[str] = Field(default_factory=list)
    script_paths: list[str] = Field(default_factory=list)
    reference_paths: list[str] = Field(default_factory=list)
    file_index_scanned_path_count: int = 0
    file_index_max_scanned_paths: int = 0
    file_index_scan_truncated: bool = False
    package: dict[str, Any] | None = None
    enabled: bool = True
    valid: bool = True
    issues: list[SkillValidationIssueView] = Field(default_factory=list)
    issue_counts: dict[str, int] = Field(default_factory=dict)
    body_preview: str = ""
    path: str
    source_root: str | None = None
    source_scope: str | None = None
    read_only: bool = False
    can_uninstall: bool = False


class SkillListItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    title: str
    summary: str
    allowed_tools: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    enabled: bool = True
    valid: bool = True
    issue_counts: dict[str, int] = Field(default_factory=dict)
    body_preview: str = ""
    source_scope: str | None = None
    trust: str | None = None
    version: str | None = None
    read_only: bool = False
    can_uninstall: bool = False


class SkillManageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    skill_id: str | None = None


class SkillCuratorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = "report"
    skill_id: str | None = None
    title: str | None = None
    summary: str | None = None
    body: str | None = None
    rationale: str | None = None
    tags: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    file_path: str | None = None
    content: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    absorbed_into: str | None = None
    revision: str | None = None
    outcome: str | None = None
    feedback_source: str | None = None
    confidence: float | None = None
    trigger: str | None = None
    steps: list[str] = Field(default_factory=list)
    expected_outcome: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    source_ref: str | None = None
    procedure_id: str | None = None
    dry_run: bool = False
    force: bool = False


class SkillCuratorAutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_run: bool = False


class SkillCuratorAutomationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    last_run_at: str | None = None
    last_status: str | None = None
    last_reason: str | None = None
    last_run_id: str | None = None
    last_counts: dict[str, Any] = Field(default_factory=dict)
    last_recommendation_count: int = 0
    last_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    next_run_at: str | None = None
    schedule: str = "interval"
    auto_merge: bool = True
    pin_protection: bool = True
    interval_seconds: int = 21600
    tick_seconds: int = 60
    min_idle_hours: float = 0
    dry_run: bool = False
    force: bool = False


class SkillCuratorAutomationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ran: bool = False
    reason: str = "not_due"
    next_run_at: str | None = None
    report: dict[str, Any] | None = None


class SkillCuratorMaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True
    force: bool = False
    source: str = "ops"


class SkillContentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    title: str
    path: str
    source_root: str
    body: str
    body_preview: str = ""
    file_count: int = 0


class SkillFileIndexView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    path: str
    source_root: str
    files: list[SkillFileEntryView] = Field(default_factory=list)
    scanned_path_count: int = 0
    max_scanned_paths: int = 0
    scan_truncated: bool = False


class SkillFileReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    max_bytes: int = 64_000


class SkillFileReadView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    relative_path: str
    path: str
    source_root: str
    kind: str
    is_binary: bool = False
    encoding: str = "utf-8"
    content: str = ""
    truncated: bool = False
    size_bytes: int = 0


class MemoryStoreView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: str
    display_name: str
    max_chars: int
    injection_chars: int
    max_tokens: int | None = None
    injection_tokens: int | None = None
    effective_max_tokens: int
    effective_injection_tokens: int
    budget_source: str = "stored"
    actual_injection_tokens: int = 0
    actual_injection_chars: int = 0
    usage_chars: int
    usage_tokens: int = 0
    entry_count: int
    summary: str
    summary_sections: dict[str, dict[str, str]] = Field(default_factory=dict)
    snapshot_status: str = "live"
    updated_at: datetime


class MemoryLayerId(str, Enum):
    SESSION = "session"
    USER = "user"
    WORKSPACE = "workspace"


class MemoryLayerView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: MemoryLayerId
    display_name: str
    description: str
    writable: bool
    entry_count: int = 0
    store_id: str | None = None
    summary: str | None = None


class MemoryEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    memory_id: str | None = None
    store_id: str
    layer_id: str | None = None
    content: str
    category: str
    source_kind: str
    priority: float
    confidence: float = 0.5
    salience: float = 0.5
    last_accessed_at: datetime | None = None
    evidence_refs: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    expires_at: datetime | None = None
    effective_score: float = 0.0
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class MemoryEntryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    category: str = "note"
    source_kind: str = "manual"
    priority: float = 0.5
    confidence: float = 0.5
    salience: float = 0.5
    evidence_refs: tuple[str, ...] = ()


class MemoryEntryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    category: str | None = None
    priority: float | None = None
    confidence: float | None = None
    salience: float | None = None
    status: str | None = None


class MemoryProviderView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    display_name: str
    kind: str = "local_curated"
    origin: str = "builtin"
    family: str
    description: str
    active: bool
    configured: bool
    available: bool
    supports_prefetch: bool
    supports_sync: bool
    supports_index: bool = True
    supports_reflection: bool
    supports_explain: bool = True
    supports_archive_search: bool
    roles: list[str] = Field(default_factory=list)
    health: str = "unknown"
    diagnostics: list[str] = Field(default_factory=list)
    last_sync_at: datetime | None = None


class MemoryProviderTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    ok: bool
    health: str = "unknown"
    diagnostics: list[str] = Field(default_factory=list)


class MemoryArchiveSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int = 5


class SessionSearchScope(str, Enum):
    ALL = "all"
    EXCLUDE_CURRENT = "exclude_current"
    CURRENT = "current"


class SessionSearchMode(str, Enum):
    RECENT = "recent"
    SEARCH = "search"
    SUMMARIZE = "summarize"


class SessionSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = ""
    thread_id: str | None = None
    limit: int = 5
    scope: SessionSearchScope = SessionSearchScope.EXCLUDE_CURRENT
    mode: SessionSearchMode = SessionSearchMode.SUMMARIZE


class MemoryArchiveSearchHitView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archive_id: str
    thread_id: str
    score: float
    excerpt: str
    created_at: datetime


class MemoryArchiveSearchResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    hits: list[MemoryArchiveSearchHitView] = Field(default_factory=list)
    provider_notes: list[str] = Field(default_factory=list)


class PromptSnapshotMetadataView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    prompt_hash: str
    skills_fingerprint: str | None = None
    memory_fingerprint: str | None = None
    config_fingerprint: str
    created_at: str


class SessionTurnView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archive_id: str
    thread_id: str
    user_content: str
    assistant_content: str
    status: str
    created_at: datetime


class SessionMemoryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: MemoryLayerId = MemoryLayerId.SESSION
    thread_id: str
    memory_namespace: str | None = None
    injected_memory_snapshot_id: str | None = None
    archive_turn_count: int = 0
    recent_turns: list[SessionTurnView] = Field(default_factory=list)
    latest_prompt_snapshot: PromptSnapshotMetadataView | None = None
    session_summary: str = ""


class RecallEvidenceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_kind: str
    source_id: str
    layer_id: str | None = None
    memory_id: str | None = None
    archive_id: str | None = None
    thread_id: str | None = None
    score: float = 0.0
    match_score: float | None = None
    rerank_score: float | None = None
    recency_score: float | None = None
    final_score: float | None = None
    dropped_reason: str | None = None
    reason: str = ""
    excerpt: str = ""


class SessionSearchThreadGroupView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    hit_count: int = 0
    summary: str = ""
    excerpts: list[str] = Field(default_factory=list)
    latest_created_at: datetime | None = None
    hits: list[MemoryArchiveSearchHitView] = Field(default_factory=list)
    evidence: list[RecallEvidenceView] = Field(default_factory=list)
    latest_prompt_snapshot: PromptSnapshotMetadataView | None = None


class SessionSearchResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    thread_id: str | None = None
    scope: SessionSearchScope
    groups: list[SessionSearchThreadGroupView] = Field(default_factory=list)
    provider_notes: list[str] = Field(default_factory=list)
    current_thread_snapshot: PromptSnapshotMetadataView | None = None


class MemoryOverviewView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_provider_id: str | None = None
    runtime_mode: str = "memory_platform"
    legacy_capture_enabled: bool = False
    migration_status: dict[str, Any] = Field(default_factory=dict)
    store_count: int
    archive_turn_count: int
    reflection_job_count: int
    stores: list[MemoryStoreView] = Field(default_factory=list)
    layers: list[MemoryLayerView] = Field(default_factory=list)


class MemoryTraceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = None
    target_id: str | None = None
    limit: int = 20


class MemoryTraceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    thread_id: str | None = None
    query: str | None = None
    trace_kind: str
    target_id: str | None = None
    provider_notes: list[str] = Field(default_factory=list)
    evidence: list[RecallEvidenceView] = Field(default_factory=list)
    created_at: datetime


class MemoryTraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MemoryTraceView] = Field(default_factory=list)


class MemoryRecallBenchmarkCaseView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    query: str
    thread_id: str = "benchmark"
    expected_terms: list[str] = Field(default_factory=list)
    expected_memory_ids: list[str] = Field(default_factory=list)
    expected_archive_thread_ids: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    forbidden_memory_ids: list[str] = Field(default_factory=list)
    min_score: float = 0.6


class MemoryRecallBenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str = "ad_hoc"
    cases: list[MemoryRecallBenchmarkCaseView] = Field(default_factory=list)
    evidence_limit: int = 5


class MemoryRecallBenchmarkCaseResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    query: str
    passed: bool
    score: float = 0.0
    recall_hits: int = 0
    expected_count: int = 0
    false_positive_count: int = 0
    evidence_count: int = 0
    top_evidence: list[RecallEvidenceView] = Field(default_factory=list)
    missing_expectations: list[str] = Field(default_factory=list)
    false_positives: list[str] = Field(default_factory=list)
    summary: str = ""


class MemoryRecallBenchmarkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str = "ad_hoc"
    passed: bool = True
    score: float = 1.0
    case_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    recall_hit_rate: float = 1.0
    false_positive_rate: float = 0.0
    average_evidence_count: float = 0.0
    cases: list[MemoryRecallBenchmarkCaseResultView] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    generated_at: datetime


class MemoryRecallBenchmarkSuiteView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str
    name: str = ""
    description: str = ""
    cases: list[MemoryRecallBenchmarkCaseView] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    source: str = "ops"
    created_at: datetime
    updated_at: datetime
    latest_run_id: str | None = None
    latest_score: float | None = None
    latest_passed: bool | None = None
    latest_run_at: datetime | None = None


class MemoryRecallBenchmarkSuiteUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str
    name: str = ""
    description: str = ""
    cases: list[MemoryRecallBenchmarkCaseView] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    source: str = "ops"


class MemoryRecallBenchmarkSuiteListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MemoryRecallBenchmarkSuiteView] = Field(default_factory=list)


class MemoryRecallBenchmarkRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_limit: int = 5
    source: str = "ops"
    record: bool = True


class MemoryRecallBenchmarkRunView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    suite_id: str
    suite_name: str = ""
    source: str = "ops"
    report: MemoryRecallBenchmarkResponse
    created_at: datetime


class MemoryRecallBenchmarkRunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MemoryRecallBenchmarkRunView] = Field(default_factory=list)


class MemoryConflictView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    memory_id: str
    conflicting_memory_id: str
    reason: str
    created_at: datetime
    resolved: bool = False
    recommended_action: str | None = None
    memory_content: str | None = None
    conflicting_content: str | None = None


class MemoryConflictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MemoryConflictView] = Field(default_factory=list)


class MemoryStalenessEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    layer_id: str
    stale_score: float
    reason: str
    last_accessed_at: datetime | None = None
    expires_at: datetime | None = None
    retention_score: float = 0.0
    tier: str = "cold"
    access_count: int = 0
    reinforcement_boost: float = 0.0
    temporal_decay: float = 0.0
    salience: float = 0.0


class MemoryStalenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MemoryStalenessEntryView] = Field(default_factory=list)


class MemoryRetentionEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    store_id: str
    layer_id: str | None = None
    tier: str = "cold"
    retention_score: float = 0.0
    salience: float = 0.0
    temporal_decay: float = 0.0
    reinforcement_boost: float = 0.0
    access_count: int = 0
    last_accessed_at: datetime | None = None
    created_at: datetime
    status: str = "active"


class MemoryQualityIssueView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    severity: str = "info"
    kind: str
    store_id: str | None = None
    layer_id: str | None = None
    memory_id: str | None = None
    related_memory_ids: tuple[str, ...] = ()
    message: str
    recommendation: str | None = None
    score: float = 0.0


class MemoryStoreHealthView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: str
    layer_id: str | None = None
    status: str = "healthy"
    entry_count: int = 0
    active_count: int = 0
    inactive_count: int = 0
    low_confidence_count: int = 0
    low_salience_count: int = 0
    missing_evidence_count: int = 0
    duplicate_cluster_count: int = 0
    conflict_count: int = 0
    stale_count: int = 0
    accessed_count: int = 0
    hot_count: int = 0
    warm_count: int = 0
    cold_count: int = 0
    retention_average: float = 0.0
    injection_token_pressure: float = 0.0
    quality_score: float = 1.0
    issues: list[MemoryQualityIssueView] = Field(default_factory=list)


class MemoryHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "healthy"
    quality_score: float = 1.0
    archive_turn_count: int = 0
    pending_review_count: int = 0
    conflict_count: int = 0
    stale_count: int = 0
    provider_count: int = 0
    provider_health: dict[str, str] = Field(default_factory=dict)
    stores: list[MemoryStoreHealthView] = Field(default_factory=list)
    issues: list[MemoryQualityIssueView] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    generated_at: datetime


class MemoryReviewItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    layer_id: str
    store_id: str
    action: str
    content: str
    category: str
    priority: float
    confidence: float
    salience: float
    evidence_refs: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    rationale: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class MemoryReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MemoryReviewItemView] = Field(default_factory=list)


class MemoryGovernanceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    reason: str | None = None
    source: str = "ops"


class MemoryGovernanceActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    memory_id: str
    store_id: str | None = None
    entry_id: str | None = None
    status: str = "ok"
    message: str = ""
    entry: MemoryEntryView | None = None
    review_item: MemoryReviewItemView | None = None
    before_retention: MemoryRetentionEntryView | None = None
    after_retention: MemoryRetentionEntryView | None = None


class MemoryGovernancePlanItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    store_id: str
    entry_id: str
    layer_id: str | None = None
    action: str
    reason: str
    tier: str = "cold"
    stale_score: float = 0.0
    retention_score: float = 0.0
    salience: float = 0.0
    access_count: int = 0
    last_accessed_at: datetime | None = None
    expires_at: datetime | None = None


class MemoryGovernanceBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: str = "balanced"
    layer_id: str | None = None
    limit: int = 20
    dry_run: bool = True
    source: str = "ops"


class MemoryGovernanceBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: str = "balanced"
    layer_id: str | None = None
    dry_run: bool = True
    candidate_count: int = 0
    executed_count: int = 0
    skipped_count: int = 0
    items: list[MemoryGovernancePlanItemView] = Field(default_factory=list)
    results: list[MemoryGovernanceActionResponse] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ProfileFacetView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facet_id: str
    source_memory_id: str
    entry_id: str
    store_id: str = "user_profile"
    class_id: str
    key: str
    value: str
    source_category: str
    evidence_refs: tuple[str, ...] = ()
    confidence: float = 0.0
    salience: float = 0.0
    priority: float = 0.0
    stability_score: float = 0.0
    state: str
    user_state: str
    prompt_visible: bool = False
    source_polluted: bool = False
    pollution_reasons: tuple[str, ...] = ()
    reason: str = ""
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProfileFacetPolicyView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_threshold: float = 1.5
    provisional_threshold: float = 0.7
    candidate_threshold: float = 0.4
    require_review_classes: tuple[str, ...] = ()
    class_budgets: dict[str, int] = Field(default_factory=dict)
    default_class_budget: int = 5
    max_facets: int = 80
    pollution_requires_review: bool = True


class ProfileFacetAuditEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    action: str
    facet_id: str
    source_memory_id: str | None = None
    before_state: str | None = None
    after_state: str | None = None
    before_user_state: str | None = None
    after_user_state: str | None = None
    reason: str | None = None
    source: str = "ops"
    created_at: datetime


class ProfileFacetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: ProfileFacetPolicyView = Field(default_factory=ProfileFacetPolicyView)
    items: list[ProfileFacetView] = Field(default_factory=list)


class ProfileFacetGovernanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    reason: str | None = None
    source: str = "ops"


class ProfileFacetGovernanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    facet: ProfileFacetView
    status: str = "ok"
    message: str = ""
    audit_entry: ProfileFacetAuditEntryView | None = None


class ProfileFacetRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = "ops"


class ProfileFacetRebuildResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "completed"
    source: str = "ops"
    facet_count: int = 0
    updated_count: int = 0
    facets: list[ProfileFacetView] = Field(default_factory=list)
    audit_entry: ProfileFacetAuditEntryView | None = None


class ProfileFacetAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProfileFacetAuditEntryView] = Field(default_factory=list)


class MemoryMaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True
    policy: str | None = None
    layer_id: str | None = None
    limit: int | None = None
    source: str = "ops"
    run_reflection_due_jobs: bool | None = None


class MemoryMaintenanceAutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_run: bool = False


class MemoryMaintenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str = "completed"
    dry_run: bool = True
    policy: str = "balanced"
    layer_id: str | None = None
    source: str = "ops"
    update_queue_pending: int = 0
    update_queue_drained: int = 0
    reflection_jobs_due: int = 0
    reflection_jobs_run: int = 0
    reflection_entries_written: int = 0
    governance: MemoryGovernanceBatchResponse = Field(default_factory=MemoryGovernanceBatchResponse)
    health_before: MemoryHealthResponse | None = None
    health_after: MemoryHealthResponse | None = None
    actions_executed: dict[str, int] = Field(default_factory=dict)
    skipped_actions: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime


class MemoryMaintenanceAutomationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    last_run_at: str | None = None
    last_status: str | None = None
    last_reason: str | None = None
    last_run_id: str | None = None
    last_counts: dict[str, Any] = Field(default_factory=dict)
    last_error_count: int = 0
    last_errors: list[str] = Field(default_factory=list)
    next_run_at: str | None = None
    tick_seconds: int = 300
    interval_seconds: int = 21600
    min_idle_seconds: int = 0
    dry_run: bool = True
    execute: bool = False
    policy: str = "balanced"
    layer_id: str | None = None
    limit: int = 12
    run_reflection_due_jobs: bool = True


class MemoryMaintenanceAutomationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ran: bool = False
    reason: str = "not_due"
    next_run_at: datetime | None = None
    report: MemoryMaintenanceResponse | None = None


class MemoryFlushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = None


class MemoryOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_path: str | None = None
    thread_id: str | None = None
    force: bool = False
    source: str = "ops"


class MemoryOnboardingFileView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    kind: str = "project_entry"
    size_chars: int = 0
    included_chars: int = 0
    truncated: bool = False
    content_preview: str = ""


class MemoryOnboardingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool = True
    status: str = "skipped"
    reason: str | None = None
    workspace_path: str = ""
    thread_id: str | None = None
    store_id: str = "runtime_memory"
    layer_id: str = "workspace"
    category: str = "project_context"
    files: tuple[MemoryOnboardingFileView, ...] = ()
    review_ids: tuple[str, ...] = ()
    written_memory_ids: tuple[str, ...] = ()
    stable_snapshot_refresh_recommended: bool = False
    created_at: datetime


class MemoryCandidateAuditEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    action: str
    reason: str
    layer_id: str | None = None
    store_id: str | None = None
    category: str = "note"
    candidate_preview: str = ""
    quality_score: float = 0.0
    quality_decision: str = "unknown"
    blockers: tuple[str, ...] = ()
    confidence: float = 0.0
    salience: float = 0.0
    priority: float = 0.0
    evidence_count: int = 0
    evidence_refs: tuple[str, ...] = ()
    source_thread_id: str | None = None
    source_polluted: bool = False
    pollution_reasons: tuple[str, ...] = ()
    target_id: str | None = None
    supersedes: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    created_at: datetime


class MemoryPollutionMarkerView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marker_id: str
    thread_id: str
    source_kind: str = "external"
    source_id: str | None = None
    tool_name: str | None = None
    reason: str = ""
    evidence_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SelfUpgradeBacklogItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    domain: str
    severity: str = "watch"
    title: str
    summary: str = ""
    metric: str | None = None
    count: int = 0
    recommendation: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelfUpgradeDomainHealthView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_id: str
    label: str
    status: str = "healthy"
    score: float = 1.0
    enabled: bool = True
    metrics: dict[str, int | float | str | bool] = Field(default_factory=dict)
    issues: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()


class SelfUpgradeHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "self_upgrade_health"
    status: str = "healthy"
    score: float = 1.0
    fingerprint: str = "self-upgrade"
    domains: tuple[SelfUpgradeDomainHealthView, ...] = ()
    backlog: tuple[SelfUpgradeBacklogItemView, ...] = ()
    recommendations: tuple[str, ...] = ()
    generated_at: datetime


class MemoryFlushResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = None
    candidates_seen: int = 0
    entries_written: int = 0
    review_items_created: int = 0
    entries_skipped: int = 0
    facts_removed: int = 0
    errors: tuple[str, ...] = ()
    written_memory_ids: tuple[str, ...] = ()
    review_ids: tuple[str, ...] = ()
    candidate_audit: tuple[MemoryCandidateAuditEntryView, ...] = ()


class MemoryReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class MemoryReviewBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: list[str] = Field(default_factory=list)
    reject: list[str] = Field(default_factory=list)


class MemoryReviewBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class MemoryAdminExportView(BaseModel):
    model_config = ConfigDict(extra="allow")

    stores: dict[str, Any] = Field(default_factory=dict)
    review_queue: list[dict[str, Any]] = Field(default_factory=list)
    providers: list[dict[str, Any]] = Field(default_factory=list)
    archive_turn_count: int = 0


class MemoryAdminImportRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    stores: dict[str, Any] = Field(default_factory=dict)
    review_queue: list[dict[str, Any]] = Field(default_factory=list)


class MemoryAdminImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries_imported: int = 0
    review_items_created: int = 0


class MemoryAdminAuditView(BaseModel):
    model_config = ConfigDict(extra="allow")

    snapshot: dict[str, Any] = Field(default_factory=dict)
    pending_review_count: int = 0
    conflict_count: int = 0
    staleness_count: int = 0
    health: dict[str, Any] = Field(default_factory=dict)
    providers: list[dict[str, Any]] = Field(default_factory=list)
    candidate_audit: list[MemoryCandidateAuditEntryView] = Field(default_factory=list)
    pollution_markers: list[MemoryPollutionMarkerView] = Field(default_factory=list)
    recall_benchmark_suites: list[MemoryRecallBenchmarkSuiteView] = Field(default_factory=list)
    recall_benchmark_runs: list[MemoryRecallBenchmarkRunView] = Field(default_factory=list)


class MemoryConflictResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = "keep_both"


class MemoryProviderAdminResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MemoryProviderView] = Field(default_factory=list)


class ReflectionJobAdminResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReflectionJobView] = Field(default_factory=list)


class ReflectionJobView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    name: str
    schedule_kind: str
    target_store_id: str
    enabled: bool
    system_managed: bool
    template: str
    instructions: str | None = None
    source_query: str | None = None
    interval_seconds: int | None = None
    cron: str | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None


class ReflectionJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    name: str
    schedule_kind: str
    target_store_id: str = "runtime_memory"
    template: str = "custom"
    instructions: str | None = None
    source_query: str | None = None
    interval_seconds: int | None = None
    cron: str | None = None


class ReflectionJobRunView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: str
    entries_written: int
    archive_hits: int
    summary: str
    written_entries: list[MemoryEntryView] = Field(default_factory=list)


class ScheduledTaskScheduleView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    display: str
    interval_seconds: int | None = None
    cron: str | None = None
    run_at: datetime | None = None


class ScheduledTaskView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    name: str
    prompt: str
    schedule: ScheduledTaskScheduleView
    enabled: bool
    status: str
    system_managed: bool = False
    thread_id: str | None = None
    execution_mode: str = "agent"
    selected_model: str | None = None
    selected_profile: str | None = None
    selected_reasoning_effort: str | None = None
    promoted_capabilities: list[str] = Field(default_factory=list)
    max_runs: int | None = None
    run_count: int = 0
    missed_run_policy: str = "skip"
    delivery: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_execution_id: str | None = None


class ScheduledTaskExecutionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    task_id: str
    thread_id: str
    run_id: str | None = None
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    summary: str = ""
    error: str | None = None
    output_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    promoted_capabilities: list[str] = Field(default_factory=list)
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
    promoted_capabilities: list[str] | None = None
    max_runs: int | None = None
    delivery: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class ScheduledTaskRunView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: ScheduledTaskView
    execution: ScheduledTaskExecutionView | None = None
    ran: bool
    reason: str | None = None


class ScheduledTaskAutomationStatusResponse(BaseModel):
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
    recent_executions: list[ScheduledTaskExecutionView] = Field(default_factory=list)
    reason: str = "ready"


class ScheduledTaskAutomationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ScheduledTaskAutomationStatusResponse
    ran_count: int = 0
    skipped_count: int = 0
    results: list[ScheduledTaskRunView] = Field(default_factory=list)
    reason: str = "ok"


class ScheduledTaskAdminResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ScheduledTaskView] = Field(default_factory=list)


class ScheduledTaskExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ScheduledTaskExecutionView] = Field(default_factory=list)


class CapabilityDependencyView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str
    required: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class TypedApprovalPolicyView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "runtime"
    risk_category: str | None = None
    requires_network: bool = False
    scope: str | None = None


class CapabilityHealthView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "unknown"
    message: str | None = None
    checked_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilityResourceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    title: str
    description: str = ""
    server_id: str | None = None
    path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    discovery_source: str = "inline_fallback"
    supports_read: bool = False
    uri: str | None = None
    mime_type: str | None = None


class CapabilityPromptView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str
    title: str
    description: str = ""
    server_id: str | None = None
    arguments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    discovery_source: str = "inline_fallback"
    supports_render: bool = False
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ExtensionStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: str
    source_kind: str = "mcp"
    status: str
    description: str = ""
    error: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    transport_kind: str | None = None
    startup_policy: str | None = None
    refresh_policy: str | None = None
    enabled: bool = False
    tool_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0
    connected: bool = False
    ready: bool = False
    auth_required: bool = False
    refresh_owner: str | None = None
    last_started_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    backoff_until: datetime | None = None
    reconnect_count: int = 0
    diagnostics: list[str] = Field(default_factory=list)
    discovery_source: str = "inline_fallback"
    metadata: dict[str, Any] = Field(default_factory=dict)
    config_source: str | None = None


class McpServerView(ExtensionStatusView):
    model_config = ConfigDict(extra="forbid")


class McpConfigOverviewView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_path: str
    server_count: int = 0
    enabled_count: int = 0
    ready_count: int = 0
    auth_required_count: int = 0
    disabled_count: int = 0
    failed_count: int = 0
    hidden_from_model_count: int = 0


class McpServerBatchUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_text: str


class McpServerBatchUpsertView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    config_path: str
    upserted: list[str] = Field(default_factory=list)
    servers: list[McpServerView] = Field(default_factory=list)
    reload: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)


class McpServerDeleteView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    server_id: str
    deleted: bool
    config_path: str
    servers: list[McpServerView] = Field(default_factory=list)
    reload: dict[str, Any] = Field(default_factory=dict)


class McpServerToolsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: str
    status: str
    tool_names: list[str] = Field(default_factory=list)
    tool_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0
    discovery_source: str = "inline_fallback"


class McpResourceContentView(CapabilityResourceView):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None


class McpPromptRenderView(CapabilityPromptView):
    model_config = ConfigDict(extra="forbid")

    rendered: str
    provided_arguments: dict[str, Any] = Field(default_factory=dict)


class McpServerProvenanceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: str
    provenance: str
    description: str = ""
    transport_kind: str
    startup_policy: str | None = None
    refresh_policy: str | None = None
    approval_policy: str | None = None
    tool_prefix: str | None = None
    collision_policy: str | None = None
    tool_allowlist: list[str] = Field(default_factory=list)
    tool_allowlist_active: bool = False
    tool_denylist: list[str] = Field(default_factory=list)
    oauth: dict[str, Any] = Field(default_factory=dict)
    env_resolution: dict[str, Any] = Field(default_factory=dict)
    header_templates: dict[str, Any] = Field(default_factory=dict)
    resource_policy: dict[str, Any] = Field(default_factory=dict)
    prompt_policy: dict[str, Any] = Field(default_factory=dict)
    reconnect_policy: dict[str, Any] = Field(default_factory=dict)
    healthcheck: dict[str, Any] = Field(default_factory=dict)
    connection_config: dict[str, Any] = Field(default_factory=dict)


class LinkPreviewView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    hostname: str
    title: str | None = None
    description: str | None = None
    preview_enabled: bool = False
    preview_status: str = "disabled"


class SubagentEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    parent_thread_id: str
    parent_run_id: str | None = None
    event: str
    timestamp: datetime | None = None
    status: str | None = None
    summary: str | None = None
    error: str | None = None
    tool_name: str | None = None
    display_name: str | None = None
    child_thread_id: str | None = None
    child_run_id: str | None = None


class SubagentMessagePreviewView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content_preview: str = ""
    tool_call_count: int = 0
    tool_result_count: int = 0


class SubagentApprovalSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending_approval: str | None = None
    request_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    reason: str | None = None
    action_kind: str | None = None
    requested_permissions: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    approval_profile: str | None = None
    risk_category: str | None = None
    capability_group: str | None = None


class SubagentToolEvidenceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str | None = None
    message_id: str | None = None
    name: str | None = None
    display_name: str | None = None
    source_kind: str | None = None
    source_id: str | None = None
    capability_group: str | None = None
    tool_execution_mode: str | None = None
    status: str | None = None
    args_keys: list[str] = Field(default_factory=list)
    has_result: bool = False
    result_char_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None


class SubagentTaskView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    batch_id: str | None = None
    parent_thread_id: str
    parent_run_id: str | None = None
    child_thread_id: str | None = None
    child_run_id: str | None = None
    status: str
    assigned_profile: str
    delegation_depth: int
    workspace_mode: str | None = None
    cancel_requested: bool = False
    depends_on_task_ids: tuple[str, ...] = ()
    dependency_state: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    timeout_at: datetime | None = None
    error: str | None = None
    summary: str | None = None
    requested_tool_names: tuple[str, ...] = ()
    allowed_tool_names: tuple[str, ...] = ()
    messages: list[SubagentMessagePreviewView] = Field(default_factory=list)
    recent_tool_activity: list[SubagentToolEvidenceView] = Field(default_factory=list)
    recent_events: list[SubagentEventView] = Field(default_factory=list)
    artifacts: list["ArtifactRefView"] = Field(default_factory=list)
    approval_payload: SubagentApprovalSummaryView | None = None


class SubagentDependencyEdgeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_task_id: str
    target_task_id: str
    status: str
    source_status: str | None = None


class SubagentDependencyGraphView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_thread_id: str
    parent_run_id: str | None = None
    tasks: list[SubagentTaskView] = Field(default_factory=list)
    edges: list[SubagentDependencyEdgeView] = Field(default_factory=list)
    ready_task_ids: list[str] = Field(default_factory=list)
    waiting_task_ids: list[str] = Field(default_factory=list)
    blocked_task_ids: list[str] = Field(default_factory=list)
    missing_dependency_task_ids: list[str] = Field(default_factory=list)


class ProcessInputEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_preview: str = ""
    submitted: bool = False
    byte_count: int = 0
    created_at: datetime


class ProcessSessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    thread_id: str
    command: str
    cwd: str
    backend: str = "local"
    backend_id: str = "local"
    backend_label: str = "Local shell"
    interactive: bool = True
    pty: bool = False
    pid: int | None = None
    status: str
    exit_code: int | None = None
    detached: bool = False
    log_cursor: int = 0
    stdin_closed: bool = False
    last_stdin_at: datetime | None = None
    last_signal: str | None = None
    last_signal_at: datetime | None = None
    columns: int | None = None
    rows: int | None = None
    input_history: list[ProcessInputEventView] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    log_path: str
    last_output: str = ""


class ProcessLogView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: str
    output: str
    total_lines: int
    showing: str
    next_offset: int
    start_offset: int = 0
    backend: str = "local"
    incremental: bool = True


class ProcessStdinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: str
    submit: bool = False


class ProcessResizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: int
    rows: int


class ProcessSpawnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    cwd: str = "/mnt/user-data/workspace"
    env: dict[str, str] = Field(default_factory=dict)


class TerminalBackendCapabilitiesView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "local"
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


class ToolCatalogEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    name: str
    display_name: str
    summary: str
    source_kind: str
    source_id: str
    capability_group: str
    visibility: str
    deferred: bool = False
    stability: str = "stable"
    risk_category: str | None = None
    approval: TypedApprovalPolicyView | None = None
    resources: list[CapabilityResourceView] = Field(default_factory=list)
    prompts: list[CapabilityPromptView] = Field(default_factory=list)
    dependencies: list[CapabilityDependencyView] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    health: CapabilityHealthView = Field(default_factory=CapabilityHealthView)


class PluginView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    enabled: bool = False
    source_path: str | None = None
    skill_roots: list[str] = Field(default_factory=list)
    tool_count: int = 0
    tool_names: list[str] = Field(default_factory=list)
    resources: list[CapabilityResourceView] = Field(default_factory=list)
    prompts: list[CapabilityPromptView] = Field(default_factory=list)
    memory_providers: list[dict[str, Any]] = Field(default_factory=list)
    memory_provider_count: int = 0
    catalog_metadata: dict[str, Any] = Field(default_factory=dict)
    discovery_source: str = "plugin_config"


class PluginCatalogEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    name: str
    description: str = ""
    source: str
    source_kind: str = "unknown"
    version: str | None = None
    author: str | None = None
    homepage: str | None = None
    tags: list[str] = Field(default_factory=list)
    trust_level: str | None = None
    registry_id: str | None = None
    registry_name: str | None = None
    registry_source: str | None = None
    registry_kind: str | None = None
    installed: bool = False
    enabled: bool = False
    installable: bool = True
    skill_count: int = 0
    tool_count: int = 0
    mcp_server_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0
    memory_provider_count: int = 0
    skill_roots: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    memory_providers: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    catalog_metadata: dict[str, Any] = Field(default_factory=dict)
    discovery_source: str = "catalog"


class PluginRegistryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_id: str
    name: str
    source: str
    source_kind: str
    enabled: bool = True
    readonly: bool = False
    trust_level: str | None = None
    entry_count: int = 0
    cached: bool = False
    cache_path: str | None = None
    error: str | None = None
    diagnostics: list[str] = Field(default_factory=list)
    config_path: str | None = None
    last_checked_at: datetime | None = None


class PluginRegistryUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    registry_id: str | None = None
    name: str | None = None
    enabled: bool = True
    trust_level: str | None = None


class PluginRegistryUpsertView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    config_path: str
    registry: PluginRegistryView
    registries: list[PluginRegistryView] = Field(default_factory=list)
    catalog: list[PluginCatalogEntryView] = Field(default_factory=list)


class PluginRegistryDeleteView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    registry_id: str
    deleted: bool
    config_path: str
    registries: list[PluginRegistryView] = Field(default_factory=list)
    catalog: list[PluginCatalogEntryView] = Field(default_factory=list)


class PluginInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    plugin_id: str | None = None
    enable: bool = True
    force: bool = False


class PluginInstallView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    installed: bool
    enabled: bool
    source: str
    path: str
    config_path: str
    skill_roots: list[str] = Field(default_factory=list)
    tool_count: int = 0
    bundled_mcp_servers: list[str] = Field(default_factory=list)
    reload: dict[str, Any] = Field(default_factory=dict)
    plugins: list[PluginView] = Field(default_factory=list)


class McpPromptRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)


ThreadStateView.model_rebuild()
ThreadDetailView.model_rebuild()
ThreadSettingsView.model_rebuild()
