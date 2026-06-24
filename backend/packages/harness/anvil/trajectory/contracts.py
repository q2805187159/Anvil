from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TrajectoryExportFormat(str, Enum):
    ANVIL = "anvil"
    SHAREGPT = "sharegpt"


class TrajectoryQualityStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class TrajectoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_: str = Field(alias="from")
    value: str
    message_id: str | None = None
    role: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrajectoryCompressionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_turns: int | None = 80
    keep_first_turns: int = 2
    keep_last_turns: int = 40
    max_message_chars: int = 12000
    max_tool_result_chars: int = 6000
    max_metadata_chars: int = 4000
    marker_template: str = "[Anvil trajectory compression omitted {omitted_turns} middle turns.]"

    @model_validator(mode="after")
    def normalize_limits(self) -> "TrajectoryCompressionConfig":
        if self.max_turns is not None:
            self.max_turns = max(int(self.max_turns), 1)
        self.keep_first_turns = max(int(self.keep_first_turns), 0)
        self.keep_last_turns = max(int(self.keep_last_turns), 0)
        self.max_message_chars = max(int(self.max_message_chars), 1)
        self.max_tool_result_chars = max(int(self.max_tool_result_chars), 1)
        self.max_metadata_chars = max(int(self.max_metadata_chars), 1)
        return self


class TrajectoryExportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: TrajectoryExportFormat = TrajectoryExportFormat.ANVIL
    include_system: bool = False
    include_tools: bool = True
    include_tool_args: bool = True
    include_metadata: bool = True
    include_reasoning: bool = False
    include_parsed_tool_calls: bool = True
    include_hidden_steps: bool = False
    include_artifacts: bool = True
    include_approvals: bool = True
    include_token_usage: bool = True
    scrub_secrets: bool = True
    compression: TrajectoryCompressionConfig = Field(default_factory=TrajectoryCompressionConfig)


class ToolUsageStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = 0
    success_count: int = 0
    error_count: int = 0
    running_count: int = 0
    total_duration_ms: int = 0


class TrajectoryStats(BaseModel):
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
    tool_stats: dict[str, ToolUsageStats] = Field(default_factory=dict)


class TrajectoryQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    message: str
    turn_index: int | None = None
    message_id: str | None = None
    tool_name: str | None = None


class TrajectoryQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "passed"
    score: float = 1.0
    issues: list[TrajectoryQualityIssue] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class TrajectoryExportEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    thread_id: str
    run_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    model: str | None = None
    completed: bool = False
    conversations: list[TrajectoryTurn] = Field(default_factory=list)
    stats: TrajectoryStats = Field(default_factory=TrajectoryStats)
    quality: TrajectoryQualityReport = Field(default_factory=TrajectoryQualityReport)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_sharegpt_payload(self, *, include_metadata: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "conversations": [
                {"from": turn.from_, "value": turn.value}
                for turn in self.conversations
                if turn.from_ in {"system", "human", "gpt", "tool"}
            ],
        }
        if include_metadata:
            payload["metadata"] = {
                "thread_id": self.thread_id,
                "run_id": self.run_id,
                "timestamp": self.timestamp.isoformat(),
                "model": self.model,
                "completed": self.completed,
                "stats": self.stats.model_dump(mode="json"),
                **self.metadata,
            }
        return payload


class TrajectoryBatchExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exported_count: int
    skipped_count: int = 0
    path: str | None = None
    format: TrajectoryExportFormat = TrajectoryExportFormat.ANVIL
    entries: list[TrajectoryExportEntry] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class TrajectoryBatchExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_ids: list[str] = Field(default_factory=list)
    output_path: str | None = None
    write_jsonl: bool = True
    include_entries: bool = False
    learn_procedures: bool = False
    min_quality_status: TrajectoryQualityStatus = TrajectoryQualityStatus.WARNING
    options: TrajectoryExportOptions = Field(default_factory=TrajectoryExportOptions)


class TrajectoryBatchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    format: TrajectoryExportFormat
    jsonl_path: str | None = None
    manifest_path: str | None = None
    exported_count: int = 0
    skipped_count: int = 0
    thread_ids: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class EvaluationReportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_conversation_preview: bool = True
    max_preview_chars: int = 4000
    max_tool_result_chars: int = 1200
    scrub_secrets: bool = True
    include_markdown: bool = False

    @model_validator(mode="after")
    def normalize_limits(self) -> "EvaluationReportOptions":
        self.max_preview_chars = max(int(self.max_preview_chars), 1)
        self.max_tool_result_chars = max(int(self.max_tool_result_chars), 1)
        return self


class EvaluationReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_ids: list[str] = Field(default_factory=list)
    options: EvaluationReportOptions = Field(default_factory=EvaluationReportOptions)
    evaluator_results: dict[str, "EvaluationReportEvaluatorResult"] = Field(default_factory=dict)
    write_markdown: bool = False
    output_path: str | None = None


class EvaluationReportEvaluatorResult(BaseModel):
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


class EvaluationReportRuntimeSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    model: str | None = None
    execution_mode: str | None = None
    reasoning_effort: str | None = None
    runtime_phase_timings: dict[str, Any] = Field(default_factory=dict)
    runtime_phase_diagnostics: dict[str, Any] = Field(default_factory=dict)
    runtime_assembly_snapshot: dict[str, Any] = Field(default_factory=dict)
    runtime_assembly_diff: dict[str, Any] = Field(default_factory=dict)
    context_v2_evaluation: dict[str, Any] = Field(default_factory=dict)
    context_window_usage: dict[str, Any] = Field(default_factory=dict)
    token_usage: dict[str, Any] = Field(default_factory=dict)
    model_fallback_history: list[dict[str, Any]] = Field(default_factory=list)


class EvaluationReportToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str | None = None
    message_id: str | None = None
    name: str | None = None
    display_name: str | None = None
    capability_group: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    result_text: str | None = None


class EvaluationReportStep(BaseModel):
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


class EvaluationReportStepChainSection(BaseModel):
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
    items: list[EvaluationReportStep] = Field(default_factory=list)


class EvaluationReportMemorySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str | None = None
    injected_memory_snapshot_id: str | None = None
    procedure_learning_runs: list[str] = Field(default_factory=list)
    procedure_learning_signatures: list[str] = Field(default_factory=list)


class EvaluationReportCapabilitySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible_tool_names: list[str] = Field(default_factory=list)
    deferred_tool_names: list[str] = Field(default_factory=list)
    enabled_skill_ids: list[str] = Field(default_factory=list)
    capability_bundle_fingerprint: str | None = None


class EvaluationReportIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    code: str
    message: str


class EvaluationThreadReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    thread_id: str
    run_id: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)
    title: str | None = None
    task_preview: str | None = None
    final_answer_preview: str | None = None
    outcome: str
    score: float
    evaluator: EvaluationReportEvaluatorResult | None = None
    runtime: EvaluationReportRuntimeSection
    trajectory_quality: TrajectoryQualityReport = Field(default_factory=TrajectoryQualityReport)
    stats: TrajectoryStats = Field(default_factory=TrajectoryStats)
    tool_calls: list[EvaluationReportToolCall] = Field(default_factory=list)
    step_chain: EvaluationReportStepChainSection = Field(default_factory=EvaluationReportStepChainSection)
    memory: EvaluationReportMemorySection = Field(default_factory=EvaluationReportMemorySection)
    capabilities: EvaluationReportCapabilitySection = Field(default_factory=EvaluationReportCapabilitySection)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    hidden_bug_risks: list[EvaluationReportIssue] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    markdown: str | None = None


class EvaluationBatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    thread_reports: list[EvaluationThreadReport] = Field(default_factory=list)
    missing_thread_ids: list[str] = Field(default_factory=list)
    score: float = 0.0
    markdown_path: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    markdown: str | None = None
