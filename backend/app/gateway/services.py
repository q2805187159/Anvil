from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import status
from fastapi.responses import StreamingResponse
import yaml

from anvil import (
    ConfigLayer,
    ConfigLayerKind,
    create_sandbox_provider,
    RunRequest,
    ThreadMetadataView,
    ThreadState,
    UploadArtifactNotFoundError,
    UploadThreadNotFoundError,
    UploadValidationError,
)
from anvil.config import ConfigMutationError
from anvil.config import env_ref_name, is_env_ref
from anvil.config import llm_provider_preset, llm_provider_presets, load_dotenv_file, read_config_file
from anvil.config.service import apply_internal_task_model_payload, model_selection_options, resolve_internal_task_concrete_model_name, resolve_internal_task_model_name
from anvil.agents.model_factory import create_chat_model
from anvil.agents import ThreadExecutionMode, ThreadLifecycleStatus
from anvil.processes import build_process_env
from anvil.config.loader import (
    build_config_layers_from_file,
    build_mcp_config_layer_from_file,
    default_anvil_config_dir,
    get_repo_root,
    normalize_loaded_config,
    resolve_anvil_profile_home,
    resolve_anvil_profile_name,
    resolve_config_path,
)
from anvil.mcp import (
    delete_mcp_server_from_config_file,
    mcp_server_model_visible,
    parse_mcp_servers_config_text,
    redact_sensitive_config,
    upsert_mcp_servers_in_config_file,
)
from anvil.memory_platform import MemoryRecallBenchmarkCase, MemoryRecallBenchmarkSuite
from anvil.self_upgrade import SelfUpgradeHealthService
from anvil.skills.loader import (
    default_installed_skill_root,
    default_repo_skill_root,
)
from anvil.runtime.runs import EMPTY_FINAL_ASSISTANT_MESSAGE, RunEventEnvelope, RunResult, RunSnapshotProjector, list_run_event_page
from anvil.runtime.serialization import strip_inline_thinking_tags
from anvil.trajectory import (
    EvaluationReportEvaluatorResult,
    EvaluationReportOptions,
    ThreadEvaluationReportBuilder,
    ThreadTrajectoryExporter,
    TrajectoryBatchExportRequest as RuntimeTrajectoryBatchExportRequest,
    TrajectoryBatchExporter,
    TrajectoryCompressionConfig as RuntimeTrajectoryCompressionConfig,
    TrajectoryExportFormat,
    TrajectoryExportOptions,
    TrajectoryQualityStatus,
)

from .deps import AppRuntimeDeps
from .models import (
    ApprovalCancelRequest,
    ApprovalResumeRequest,
    ApprovalEventView,
    ApprovalView,
    ArchivedSummaryView,
    ArtifactRefView,
    CapabilityAssemblyDiagnosticsView,
    CompactionDiagnosticsView,
    ContextCacheDiagnosticsView,
    ContextWindowUsageView,
    CapabilityDependencyView,
    CapabilityHealthView,
    CapabilityPromptView,
    CapabilityResourceView,
    CompanionArtifactView,
    ConfigOverviewMetricView,
    ConfigOverviewView,
    DocumentOutlineEntryView,
    DocumentExtractionView,
    ErrorResponse,
    EvaluationBatchReportView,
    EvaluationReportRequestView,
    EvaluationThreadReportView,
    ExtensionStatusView,
    LinkPreviewView,
    MemoryArchiveSearchHitView,
    MemoryArchiveSearchRequest,
    MemoryArchiveSearchResultView,
    MemoryConflictResolveRequest,
    MemoryConflictResponse,
    MemoryConflictView,
    MemoryGovernanceActionRequest,
    MemoryGovernanceActionResponse,
    MemoryGovernanceBatchRequest,
    MemoryGovernanceBatchResponse,
    MemoryGovernancePlanItemView,
    ProfileFacetAuditEntryView,
    ProfileFacetAuditResponse,
    ProfileFacetGovernanceRequest,
    ProfileFacetGovernanceResponse,
    ProfileFacetListResponse,
    ProfileFacetPolicyView,
    ProfileFacetRebuildRequest,
    ProfileFacetRebuildResponse,
    ProfileFacetView,
    MemoryMaintenanceAutomationRequest,
    MemoryMaintenanceAutomationRunResponse,
    MemoryMaintenanceAutomationStatusResponse,
    MemoryMaintenanceRequest,
    MemoryMaintenanceResponse,
    MemoryOnboardingRequest,
    MemoryOnboardingResponse,
    MemoryLayerId,
    MemoryLayerView,
    MemoryEntryCreateRequest,
    MemoryEntryUpdateRequest,
    MemoryEntryView,
    MemoryProviderAdminResponse,
    MessageContentBlockView,
    MessageEditResendRequest,
    MessageWindowView,
    MessageView,
    MemoryStalenessEntryView,
    MemoryStalenessResponse,
    MemoryTraceRequest,
    MemoryTraceResponse,
    MemoryTraceView,
    MemoryRecallBenchmarkCaseResultView,
    MemoryRecallBenchmarkRequest,
    MemoryRecallBenchmarkResponse,
    MemoryRecallBenchmarkRunListResponse,
    MemoryRecallBenchmarkRunRequest,
    MemoryRecallBenchmarkRunView,
    MemoryRecallBenchmarkSuiteListResponse,
    MemoryRecallBenchmarkSuiteUpsertRequest,
    MemoryRecallBenchmarkSuiteView,
    MemoryOverviewView,
    MemoryFlushRequest,
    MemoryFlushResponse,
    MemoryHealthResponse,
    MemoryInjectionDiagnosticsView,
    MemoryAdminAuditView,
    MemoryAdminExportView,
    MemoryAdminImportRequest,
    MemoryAdminImportResponse,
    MemoryProviderView,
    MemoryProviderTestResponse,
    MemoryRetentionEntryView,
    MemoryReviewDecisionRequest,
    MemoryReviewBatchRequest,
    MemoryReviewBatchResponse,
    MemoryReviewItemView,
    MemoryReviewResponse,
    MemoryQualityIssueView,
    MemoryStoreHealthView,
    MemoryStoreView,
    ModelHealthCheckRequest,
    ModelHealthCheckView,
    ModelSelectionUpdateRequest,
    ModelSelectionUpdateView,
    ModelProviderDeleteView,
    ModelProviderPresetView,
    ModelProviderUpsertRequest,
    ModelProviderUpsertView,
    ModelView,
    McpConfigOverviewView,
    McpServerBatchUpsertRequest,
    McpServerBatchUpsertView,
    McpServerDeleteView,
    McpPromptRenderRequest,
    McpPromptRenderView,
    McpResourceContentView,
    McpServerProvenanceView,
    McpServerToolsView,
    McpServerView,
    ProcessLogView,
    ProcessSessionView,
    PromptCacheDiagnosticsView,
    PromptSectionTokenLedgerView,
    QueuedFollowUpCreateRequest,
    QueuedFollowUpDispatchView,
    QueuedFollowUpUpdateRequest,
    QueuedFollowUpView,
    TerminalBackendCapabilitiesView,
    RecallEvidenceView,
    ReasoningView,
    ReflectionJobAdminResponse,
    ReflectionJobCreateRequest,
    ReflectionJobRunView,
    ReflectionJobView,
    SelfUpgradeBacklogItemView,
    SelfUpgradeDomainHealthView,
    SelfUpgradeHealthResponse,
    RuntimeCapabilitiesView,
    RuntimeOperatorStatusView,
    RuntimePhaseTimingMarkView,
    RuntimePhaseTimingsView,
    RuntimeTimelineItemView,
    RunCompletedView,
    RunEventReplayView,
    RunRequestBody,
    RunStreamEvent,
    ScheduledTaskAdminResponse,
    ScheduledTaskAutomationRunResponse,
    ScheduledTaskAutomationStatusResponse,
    ScheduledTaskCreateRequest,
    ScheduledTaskExecutionResponse,
    ScheduledTaskExecutionView,
    ScheduledTaskRunView,
    ScheduledTaskScheduleView,
    ScheduledTaskUpdateRequest,
    ScheduledTaskView,
    PromptSnapshotMetadataView,
    SessionMemoryView,
    SessionSearchRequest,
    SessionSearchResultView,
    SessionSearchScope,
    SessionSearchThreadGroupView,
    SessionTurnView,
    PluginCatalogEntryView,
    PluginRegistryDeleteView,
    PluginRegistryUpsertRequest,
    PluginRegistryUpsertView,
    PluginRegistryView,
    PluginView,
    PluginInstallRequest,
    PluginInstallView,
    SkillContentView,
    SkillCuratorAutomationRequest,
    SkillCuratorAutomationRunResponse,
    SkillCuratorAutomationStatusResponse,
    SkillCuratorMaintenanceRequest,
    SkillCuratorRequest,
    SkillFileIndexView,
    SkillFileReadView,
    SkillListItemView,
    SkillValidationIssueView,
    SkillView,
    SkillManageRequest,
    StreamCapabilitiesView,
    SubagentEventView,
    SubagentApprovalSummaryView,
    SubagentDependencyEdgeView,
    SubagentDependencyGraphView,
    SubagentMessagePreviewView,
    SubagentToolEvidenceView,
    SubagentTaskView,
    ThreadDetailView,
    ThreadDeleteView,
    ThreadSettingsUpdateRequest,
    ThreadSettingsView,
    ThreadStateView,
    ThreadView,
    TodoSnapshotItemView,
    TokenUsageBreakdownView,
    TokenUsageSummaryView,
    UserInteractionFieldView,
    UserInteractionRequestView,
    UserInteractionResumeRequest,
    UserInteractionSubmitRequest,
    MessageStepView,
    TrajectoryBatchExportRequest,
    TrajectoryBatchExportView,
    TrajectoryExportRequest,
    TrajectoryExportView,
    ToolCatalogEntryView,
    ToolActivityView,
    ToolCallRecordView,
    ToolCallView,
    TypedApprovalPolicyView,
    UploadItemView,
    UploadResult,
    ProcessResizeRequest,
    ProcessSpawnRequest,
    ProcessStdinRequest,
)

ThreadDetailStateScope = Literal["chat", "full"]
ThreadStateSource = Literal["snapshot", "event_log"]
ThreadDetailStateSource = Literal["snapshot", "event_log", "auto"]
DEFAULT_CHAT_MESSAGE_LIMIT = 120

FOLLOWUP_DISPATCH_LEASE_TTL = timedelta(minutes=2)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


TERMINAL_RUNTIME_STATUSES = {"completed", "failed", "cancelled", "interrupted", "timed_out", "archived", "ready"}
ACTIVE_RUNTIME_STATUSES = {"running", "awaiting_approval", "awaiting_clarification"}
ACTIVE_TOOL_STATUSES = {"running", "started", "pending", "needs_approval", "queued"}
FAILED_TOOL_STATUSES = {"error", "failed", "failure"}
HIDDEN_OPERATOR_TOOL_NAMES = {
    "delegate_batch",
    "delegate_cancel",
    "delegate_status",
    "delegated_task",
    "memory",
    "memory_trace",
    "session_search",
    "subagent",
}
HIDDEN_OPERATOR_TOOL_GROUPS = {"memory"}


class GatewayAdapterError(Exception):
    def __init__(self, status_code: int, error: str, detail: str | None = None, kind: str | None = None) -> None:
        super().__init__(detail or error)
        self.status_code = status_code
        self.error = error
        self.detail = detail
        self.kind = kind

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(error=self.error, detail=self.detail, kind=self.kind)


def _gateway_preview_thread_id() -> str:
    return "gateway-capability-preview"


def _build_capability_preview(deps: AppRuntimeDeps):
    cached_preview = getattr(deps, "get_capability_preview", None)
    if callable(cached_preview):
        return cached_preview(lambda: _assemble_capability_preview(deps))
    return _assemble_capability_preview(deps)


def _assemble_capability_preview(deps: AppRuntimeDeps):
    sandbox_provider = create_sandbox_provider(deps.config_result.effective_config)
    return deps.capability_assembly_service.assemble(
        sandbox_provider=sandbox_provider,
        path_service=deps.path_service,
        thread_id=_gateway_preview_thread_id(),
        memory_manager=deps.memory_manager,
        config_result=deps.config_result,
        feature_set=deps.feature_set,
        request_context=None,
        tracing_service=deps.tracing_service,
        live_extensions=False,
    )


def _runtime_shared_services(deps: AppRuntimeDeps) -> dict[str, object]:
    return {
        "subagent_service": deps.subagent_service,
        "process_service": deps.process_service,
        "scheduled_task_service": deps.scheduled_task_service,
        "memory_manager": deps.memory_manager,
        "skills_service": deps.skills_service,
        "extensions_service": deps.extensions_service,
        "capability_assembly_service": deps.capability_assembly_service,
        "tracing_service": deps.tracing_service,
        "run_event_log_store": deps.run_event_log_store,
    }


def _skill_package_payload(result, skill_id: str) -> dict[str, object] | None:
    package = next((item for item in result.packages if item.manifest.skill_id == skill_id), None)
    if package is None:
        return None
    return package.model_dump(mode="json")


def skill_issue_to_view(issue) -> SkillValidationIssueView:
    return SkillValidationIssueView.model_validate(issue.model_dump(mode="json"))


def skill_manifest_to_view(manifest, *, enabled: bool, package: dict[str, object] | None = None) -> SkillView:
    scope = _skill_manifest_runtime_scope(manifest)
    return SkillView(
        skill_id=manifest.skill_id,
        title=manifest.title,
        summary=manifest.summary,
        name=manifest.name,
        description=manifest.description,
        version=manifest.version,
        trust=manifest.trust,
        allowed_tools=manifest.allowed_tools,
        tags=manifest.tags,
        dependencies=[item.model_dump(mode="json") for item in manifest.dependencies],
        readiness=manifest.readiness.model_dump(mode="json"),
        config=dict(manifest.config),
        platforms=list(manifest.platforms),
        related_skills=list(manifest.related_skills),
        asset_paths=list(manifest.asset_paths),
        template_paths=list(manifest.template_paths),
        script_paths=list(manifest.script_paths),
        reference_paths=list(manifest.reference_paths),
        file_index_scanned_path_count=manifest.file_index_scanned_path_count,
        file_index_max_scanned_paths=manifest.file_index_max_scanned_paths,
        file_index_scan_truncated=manifest.file_index_scan_truncated,
        package=package,
        enabled=enabled,
        valid=manifest.valid,
        issues=[skill_issue_to_view(issue) for issue in manifest.issues],
        issue_counts=manifest.to_summary().issue_counts,
        body_preview=manifest.body_preview,
        path=manifest.path,
        source_root=manifest.source_root,
        source_scope=scope["source_scope"],
        read_only=bool(scope["read_only"]),
        can_uninstall=bool(scope["can_uninstall"]),
    )


def skill_manifest_to_list_item(manifest, *, enabled: bool) -> SkillListItemView:
    scope = _skill_manifest_runtime_scope(manifest)
    summary = manifest.to_summary()
    return SkillListItemView(
        skill_id=manifest.skill_id,
        title=manifest.title,
        summary=manifest.summary,
        allowed_tools=manifest.allowed_tools,
        tags=manifest.tags,
        enabled=enabled,
        valid=manifest.valid,
        issue_counts=summary.issue_counts,
        body_preview=manifest.body_preview,
        source_scope=scope["source_scope"],
        trust=manifest.trust,
        version=manifest.version,
        read_only=bool(scope["read_only"]),
        can_uninstall=bool(scope["can_uninstall"]),
    )


def _skill_manifest_runtime_scope(manifest) -> dict[str, object]:
    source_root = Path(str(manifest.source_root or "")).resolve()
    repo_skill_root = default_repo_skill_root().resolve()
    installed_skill_root = default_installed_skill_root().resolve()
    if source_root == installed_skill_root:
        source_scope = "home"
    elif source_root == repo_skill_root:
        source_scope = "bundled_source"
    else:
        source_scope = "plugin"
    read_only = source_scope == "plugin"
    can_uninstall = source_scope == "home" and not read_only
    return {"source_scope": source_scope, "read_only": read_only, "can_uninstall": can_uninstall}


def capability_dependency_to_view(item) -> CapabilityDependencyView:
    return CapabilityDependencyView.model_validate(item.model_dump(mode="json"))


def typed_approval_to_view(item) -> TypedApprovalPolicyView:
    return TypedApprovalPolicyView.model_validate(item.model_dump(mode="json"))


def capability_health_to_view(item) -> CapabilityHealthView:
    return CapabilityHealthView.model_validate(item.model_dump(mode="json"))


def _capability_value(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def capability_resource_to_view(item) -> CapabilityResourceView:
    metadata = dict(_capability_value(item, "metadata", {}) or {})
    return CapabilityResourceView(
        resource_id=str(_capability_value(item, "resource_id", "")),
        title=str(_capability_value(item, "title", "")),
        description=str(_capability_value(item, "description", "")),
        server_id=str(_capability_value(item, "server_id")) if _capability_value(item, "server_id") is not None else None,
        path=str(_capability_value(item, "path")) if _capability_value(item, "path") is not None else None,
        metadata=metadata,
        discovery_source=str(metadata.get("discovery_source") or "inline_fallback"),
        supports_read=bool(metadata.get("supports_read", False)),
        uri=str(metadata.get("uri")) if metadata.get("uri") is not None else None,
        mime_type=str(metadata.get("mime_type")) if metadata.get("mime_type") is not None else None,
    )


def capability_prompt_to_view(item) -> CapabilityPromptView:
    metadata = dict(_capability_value(item, "metadata", {}) or {})
    input_schema = metadata.get("input_schema")
    return CapabilityPromptView(
        prompt_id=str(_capability_value(item, "prompt_id", "")),
        title=str(_capability_value(item, "title", "")),
        description=str(_capability_value(item, "description", "")),
        server_id=str(_capability_value(item, "server_id")) if _capability_value(item, "server_id") is not None else None,
        arguments=[str(value) for value in (_capability_value(item, "arguments", []) or [])],
        metadata=metadata,
        discovery_source=str(metadata.get("discovery_source") or "inline_fallback"),
        supports_render=bool(metadata.get("supports_render", False)),
        input_schema=dict(input_schema) if isinstance(input_schema, dict) else {},
    )


def extension_status_to_view(
    item,
    *,
    deps: AppRuntimeDeps,
    config_prefix: str | None = None,
) -> ExtensionStatusView:
    server = deps.config_result.effective_config.extensions.mcp_servers.get(item.server_id)
    origin_prefix = config_prefix or (
        f"extensions.mcp_servers.{item.server_id}" if item.source_kind == "mcp" else f"extensions.plugins.{item.server_id}"
    )
    return ExtensionStatusView(
        server_id=item.server_id,
        source_kind=item.source_kind,
        status=item.status.value if hasattr(item.status, "value") else str(item.status),
        description=server.description if server is not None else "",
        error=item.error,
        tool_names=[tool.name for tool in item.tools],
        transport_kind=server.transport_kind.value if server is not None else None,
        startup_policy=server.startup_policy if server is not None else None,
        refresh_policy=server.refresh_policy if server is not None else None,
        enabled=server.enabled if server is not None else True,
        tool_count=len(item.tools),
        resource_count=len(item.resources),
        prompt_count=len(item.prompts),
        connected=bool(item.connected),
        ready=bool(getattr(item, "ready", False)),
        auth_required=bool(getattr(item, "auth_required", False)),
        refresh_owner=getattr(item, "refresh_owner", None),
        last_started_at=item.last_started_at,
        last_refreshed_at=item.last_refreshed_at,
        backoff_until=getattr(item, "backoff_until", None),
        reconnect_count=int(item.reconnect_count),
        diagnostics=list(item.diagnostics),
        discovery_source=getattr(item, "discovery_source", "inline_fallback"),
        metadata=dict(getattr(item, "metadata", {}) or {}),
        config_source=_config_origin_source(deps.config_result, origin_prefix),
    )


def tool_catalog_entry_to_view(entry) -> ToolCatalogEntryView:
    return ToolCatalogEntryView(
        capability_id=entry.capability_id,
        name=entry.name,
        display_name=entry.display_name,
        summary=entry.summary,
        source_kind=entry.source_kind.value if hasattr(entry.source_kind, "value") else str(entry.source_kind),
        source_id=entry.source_id,
        capability_group=entry.capability_group,
        visibility=entry.visibility.value if hasattr(entry.visibility, "value") else str(entry.visibility),
        deferred=entry.deferred,
        stability=entry.stability.value if hasattr(entry.stability, "value") else str(entry.stability),
        risk_category=entry.risk_category,
        approval=typed_approval_to_view(entry.approval) if entry.approval is not None else None,
        resources=[capability_resource_to_view(item) for item in entry.resources],
        prompts=[capability_prompt_to_view(item) for item in entry.prompts],
        dependencies=[capability_dependency_to_view(item) for item in entry.dependencies],
        provenance=dict(entry.provenance),
        health=capability_health_to_view(entry.health),
    )


def list_threads(deps: AppRuntimeDeps) -> list[ThreadView]:
    return [thread_metadata_to_view(item) for item in deps.thread_service.list_threads()]


def get_link_preview(_deps: AppRuntimeDeps, url: str) -> LinkPreviewView:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_link_preview_url", f"unsupported url '{url}'")

    return LinkPreviewView(
        url=url,
        hostname=parsed.netloc,
        title=parsed.netloc,
        description="External link preview is currently disabled in safe mode.",
        preview_enabled=False,
        preview_status="disabled",
    )


def create_thread(
    deps: AppRuntimeDeps,
    thread_id: str | None = None,
    workspace_root: str | None = None,
) -> ThreadView:
    thread_id = thread_id or f"thread-{uuid4().hex[:12]}"
    try:
        metadata = deps.thread_service.create_thread(thread_id=thread_id)
    except ValueError:
        raise GatewayAdapterError(status.HTTP_409_CONFLICT, "thread_exists", f"thread '{thread_id}' already exists")
    if workspace_root is not None:
        deps.thread_service.update_thread_settings(
            thread_id,
            workspace_root=workspace_root,
        )
    return thread_metadata_to_view(metadata)


def delete_thread(deps: AppRuntimeDeps, thread_id: str) -> ThreadDeleteView:
    try:
        deps.memory_manager.on_session_end(thread_id=thread_id, reason="thread_delete", allow_network=False)
        metadata = deps.thread_service.delete_thread(thread_id)
    except ValueError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    return ThreadDeleteView(thread_id=metadata.thread_id, deleted=True)


def get_thread_view(deps: AppRuntimeDeps, thread_id: str) -> ThreadView:
    try:
        metadata = deps.thread_service.get_thread_metadata(thread_id)
    except ValueError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    return thread_metadata_to_view(metadata)


def get_thread_state_view(
    deps: AppRuntimeDeps,
    thread_id: str,
    *,
    state_scope: ThreadDetailStateScope = "full",
    state_source: ThreadStateSource = "snapshot",
    run_id: str | None = None,
) -> ThreadStateView:
    try:
        state = deps.thread_service.get_thread_state(thread_id)
    except ValueError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    if state_source == "event_log":
        envelopes = deps.run_event_log_store.list_events(thread_id=thread_id, run_id=run_id)
        if not envelopes:
            raise GatewayAdapterError(
                status.HTTP_404_NOT_FOUND,
                "run_event_log_not_found",
                "run event log for this thread was not found"
                if run_id is None
                else f"run event log for run '{run_id}' was not found",
            )
        state = _project_thread_state_from_run_events(state, envelopes, thread_scoped=run_id is None)
    include_full_state = state_scope == "full"
    artifact_refs = build_canonical_artifact_refs(deps, state.identity.thread_id) if include_full_state else None
    execution_policy = deps.thread_service.build_execution_policy_projection(state)
    subagent_tasks = (
        [subagent_task_to_view(deps, task.task_id) for task in deps.subagent_service.list_tasks(parent_thread_id=thread_id)]
        if include_full_state
        else []
    )
    process_sessions = (
        [
            process_session_to_view(item, path_service=deps.path_service, thread_id=thread_id)
            for item in deps.process_service.list_sessions(thread_id=thread_id)
        ]
        if include_full_state
        else []
    )
    return thread_state_to_view(
        state,
        path_service=deps.path_service,
        artifact_refs=artifact_refs,
        execution_policy=execution_policy,
        runtime_capabilities=build_runtime_capabilities_view(deps) if include_full_state else build_runtime_capabilities_summary_view(deps),
        subagent_tasks=subagent_tasks,
        process_sessions=process_sessions,
        state_scope=state_scope,
    )


def get_thread_detail_view(
    deps: AppRuntimeDeps,
    thread_id: str,
    *,
    message_offset: int | None = None,
    message_limit: int | None = None,
    state_scope: ThreadDetailStateScope = "chat",
    state_source: ThreadDetailStateSource = "auto",
) -> ThreadDetailView:
    try:
        state = deps.thread_service.get_thread_state(thread_id)
    except ValueError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    state = _project_detail_thread_state_from_run_events(deps, state, state_source=state_source)
    execution_policy = deps.thread_service.build_execution_policy_projection(state)
    include_full_state = state_scope == "full"
    resolved_message_limit = _resolve_thread_detail_message_limit(
        state_scope=state_scope,
        message_limit=message_limit,
    )
    artifact_refs = build_canonical_artifact_refs(deps, state.identity.thread_id) if include_full_state else None
    return thread_detail_to_view(
        state,
        path_service=deps.path_service,
        artifact_refs=artifact_refs,
        execution_policy=execution_policy,
        runtime_capabilities=build_runtime_capabilities_summary_view(deps),
        subagent_tasks=[
            subagent_task_to_view(deps, task.task_id)
            for task in deps.subagent_service.list_tasks(parent_thread_id=thread_id)
        ]
        if include_full_state
        else [],
        process_sessions=[
            process_session_to_view(item, path_service=deps.path_service, thread_id=thread_id)
            for item in deps.process_service.list_sessions(thread_id=thread_id)
        ]
        if include_full_state
        else [],
        message_offset=message_offset,
        message_limit=resolved_message_limit,
        state_scope=state_scope,
    )


def _project_detail_thread_state_from_run_events(
    deps: AppRuntimeDeps,
    state: ThreadState,
    *,
    state_source: ThreadDetailStateSource,
) -> ThreadState:
    if state_source == "snapshot":
        return _project_active_thread_state_from_run_events(deps, state)

    thread_scoped = state.lifecycle.status != ThreadLifecycleStatus.RUNNING or not state.identity.run_id
    envelopes = deps.run_event_log_store.list_events(
        thread_id=state.identity.thread_id,
        run_id=None if thread_scoped else state.identity.run_id,
    )
    if envelopes:
        return _project_thread_state_from_run_events(state, envelopes, thread_scoped=thread_scoped)
    if state_source == "event_log":
        raise GatewayAdapterError(
            status.HTTP_404_NOT_FOUND,
            "run_event_log_not_found",
            "run event log for this thread was not found",
        )
    return state


def _project_active_thread_state_from_run_events(deps: AppRuntimeDeps, state: ThreadState) -> ThreadState:
    if state.lifecycle.status != ThreadLifecycleStatus.RUNNING:
        return state
    run_id = state.identity.run_id
    if not run_id:
        return state
    envelopes = deps.run_event_log_store.list_events(thread_id=state.identity.thread_id, run_id=run_id)
    if not envelopes:
        return state
    return _project_thread_state_from_run_events(state, envelopes)


def _project_thread_state_from_run_events(
    state: ThreadState,
    envelopes: list[RunEventEnvelope],
    *,
    thread_scoped: bool = False,
) -> ThreadState:
    projector = RunSnapshotProjector()
    if thread_scoped:
        return projector.project_thread(state, envelopes)
    return projector.project(state, envelopes)


def _resolve_thread_detail_message_limit(
    *,
    state_scope: ThreadDetailStateScope,
    message_limit: int | None,
) -> int | None:
    if message_limit is not None:
        return message_limit
    if state_scope == "chat":
        return DEFAULT_CHAT_MESSAGE_LIMIT
    return None


def get_thread_trajectory_view(
    deps: AppRuntimeDeps,
    thread_id: str,
    body: TrajectoryExportRequest | None = None,
) -> TrajectoryExportView:
    effective_config = deps.effective_config
    if not effective_config.trajectory_export.enabled:
        raise GatewayAdapterError(
            status.HTTP_404_NOT_FOUND,
            "trajectory_export_disabled",
            "trajectory export is disabled in config",
        )
    try:
        state = deps.thread_service.get_thread_state(thread_id)
    except ValueError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")

    options = trajectory_export_options_from_request(effective_config.trajectory_export, body)
    entry = ThreadTrajectoryExporter().export_thread(state, options=options)
    payload = entry.model_dump(mode="json", by_alias=True)
    payload["format"] = options.format.value
    return TrajectoryExportView.model_validate(payload)


def export_trajectory_batch_view(
    deps: AppRuntimeDeps,
    body: TrajectoryBatchExportRequest | None = None,
) -> TrajectoryBatchExportView:
    effective_config = deps.effective_config
    if not effective_config.trajectory_export.enabled:
        raise GatewayAdapterError(
            status.HTTP_404_NOT_FOUND,
            "trajectory_export_disabled",
            "trajectory export is disabled in config",
        )
    body = body or TrajectoryBatchExportRequest()
    options = trajectory_export_options_from_request(effective_config.trajectory_export, body.options)
    export_root = resolve_gateway_trajectory_export_root(deps)
    runtime_request = RuntimeTrajectoryBatchExportRequest(
        thread_ids=list(dict.fromkeys(body.thread_ids)),
        output_path=body.output_path,
        write_jsonl=coalesce(body.write_jsonl, effective_config.trajectory_export.batch_write_jsonl_default),
        include_entries=coalesce(body.include_entries, effective_config.trajectory_export.batch_include_entries_default),
        learn_procedures=body.learn_procedures,
        min_quality_status=trajectory_quality_status_from_request(effective_config.trajectory_export, body.min_quality_status),
        options=options,
    )
    result, manifest = TrajectoryBatchExporter(
        checkpointer=deps.checkpointer,
        export_root=export_root,
        config=effective_config,
        skills_service=deps.skills_service,
    ).export(runtime_request)
    payload = result.model_dump(mode="json", by_alias=True)
    payload["format"] = result.format.value
    payload["entries"] = [
        {**entry.model_dump(mode="json", by_alias=True), "format": options.format.value}
        for entry in result.entries
    ]
    manifest_payload = manifest.model_dump(mode="json")
    manifest_payload["format"] = manifest.format.value
    payload["manifest"] = manifest_payload
    return TrajectoryBatchExportView.model_validate(payload)


def get_thread_evaluation_report_view(
    deps: AppRuntimeDeps,
    thread_id: str,
) -> EvaluationThreadReportView:
    try:
        state = deps.thread_service.get_thread_state(thread_id)
    except ValueError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    report = ThreadEvaluationReportBuilder().build_thread_report(state)
    return EvaluationThreadReportView.model_validate(report.model_dump(mode="json"))


def build_evaluation_batch_report_view(
    deps: AppRuntimeDeps,
    body: EvaluationReportRequestView | None = None,
) -> EvaluationBatchReportView:
    body = body or EvaluationReportRequestView()
    options = evaluation_report_options_from_request(body.options)
    requested_ids = list(dict.fromkeys(body.thread_ids or deps.checkpointer.list_thread_ids()))
    states = [
        state for thread_id in requested_ids
        if (state := deps.checkpointer.get_thread_state(thread_id)) is not None
    ]
    markdown_path = None
    if body.write_markdown:
        markdown_path = resolve_gateway_evaluation_report_path(deps, body.output_path)
    report = ThreadEvaluationReportBuilder().build_batch_report(
        states,
        requested_thread_ids=requested_ids,
        options=options,
        evaluator_results=evaluation_results_from_request(body.evaluator_results),
        markdown_path=markdown_path,
    )
    return EvaluationBatchReportView.model_validate(report.model_dump(mode="json"))


def evaluation_report_options_from_request(body) -> EvaluationReportOptions:
    body = body or {}
    updates = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else dict(body or {})
    return EvaluationReportOptions(**updates)


def evaluation_results_from_request(body: dict[str, object] | None) -> dict[str, EvaluationReportEvaluatorResult]:
    results: dict[str, EvaluationReportEvaluatorResult] = {}
    for thread_id, payload in dict(body or {}).items():
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(exclude_none=True)
        results[str(thread_id)] = EvaluationReportEvaluatorResult.model_validate(payload)
    return results


def resolve_gateway_evaluation_report_path(deps: AppRuntimeDeps, output_path: str | None) -> Path:
    export_root = resolve_gateway_trajectory_export_root(deps)
    if output_path:
        candidate = Path(output_path).expanduser()
        if not candidate.is_absolute():
            candidate = export_root / candidate
        return candidate.resolve()
    return (export_root / f"evaluation-report-{uuid4().hex[:12]}.md").resolve()


def resolve_gateway_trajectory_export_root(deps: AppRuntimeDeps) -> Path:
    configured = Path(str(deps.effective_config.trajectory_export.export_root)).expanduser()
    if configured.is_absolute():
        return configured.resolve()
    return (Path(deps.path_service.base_root).parent / configured).resolve()


def trajectory_export_options_from_request(config, body: TrajectoryExportRequest | None) -> TrajectoryExportOptions:
    body = body or TrajectoryExportRequest()
    compression_updates = {}
    if body.compression is not None:
        compression_updates = body.compression.model_dump(exclude_none=True)
    compression = RuntimeTrajectoryCompressionConfig(
        enabled=coalesce(compression_updates.get("enabled"), config.compression.enabled),
        max_turns=coalesce(compression_updates.get("max_turns"), config.compression.max_turns),
        keep_first_turns=coalesce(compression_updates.get("keep_first_turns"), config.compression.keep_first_turns),
        keep_last_turns=coalesce(compression_updates.get("keep_last_turns"), config.compression.keep_last_turns),
        max_message_chars=coalesce(compression_updates.get("max_message_chars"), config.compression.max_message_chars),
        max_tool_result_chars=coalesce(
            compression_updates.get("max_tool_result_chars"),
            config.compression.max_tool_result_chars,
        ),
        max_metadata_chars=coalesce(compression_updates.get("max_metadata_chars"), config.compression.max_metadata_chars),
    )
    format_value = body.format or config.default_format
    try:
        export_format = TrajectoryExportFormat(format_value)
    except ValueError as exc:
        raise GatewayAdapterError(
            status.HTTP_400_BAD_REQUEST,
            "invalid_trajectory_format",
            f"unsupported trajectory export format '{format_value}'",
        ) from exc
    return TrajectoryExportOptions(
        format=export_format,
        include_system=coalesce(body.include_system, config.include_system),
        include_tools=coalesce(body.include_tools, config.include_tools),
        include_tool_args=coalesce(body.include_tool_args, config.include_tool_args),
        include_metadata=coalesce(body.include_metadata, config.include_metadata),
        include_reasoning=coalesce(body.include_reasoning, config.include_reasoning),
        include_parsed_tool_calls=coalesce(body.include_parsed_tool_calls, config.include_parsed_tool_calls),
        include_hidden_steps=coalesce(body.include_hidden_steps, config.include_hidden_steps),
        include_artifacts=coalesce(body.include_artifacts, config.include_artifacts),
        include_approvals=coalesce(body.include_approvals, config.include_approvals),
        include_token_usage=coalesce(body.include_token_usage, config.include_token_usage),
        scrub_secrets=coalesce(body.scrub_secrets, config.scrub_secrets),
        compression=compression,
    )


def trajectory_quality_status_from_request(config, value: str | None) -> TrajectoryQualityStatus:
    status_value = value or config.batch_min_quality_status_default
    try:
        return TrajectoryQualityStatus(status_value)
    except ValueError as exc:
        raise GatewayAdapterError(
            status.HTTP_400_BAD_REQUEST,
            "invalid_trajectory_quality_status",
            f"unsupported trajectory quality status '{status_value}'",
        ) from exc


def coalesce(value, default):
    return default if value is None else value


def get_thread_settings_view(deps: AppRuntimeDeps, thread_id: str) -> ThreadSettingsView:
    try:
        state = deps.thread_service.get_thread_state(thread_id)
    except ValueError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    return thread_settings_to_view(state, path_service=deps.path_service)


def update_thread_settings(deps: AppRuntimeDeps, thread_id: str, body: ThreadSettingsUpdateRequest) -> ThreadSettingsView:
    try:
        updated = deps.thread_service.update_thread_settings(
            thread_id,
            execution_mode=body.execution_mode,
            selected_model=body.selected_model,
            selected_profile=body.selected_profile,
            selected_reasoning_effort=body.selected_reasoning_effort,
            is_plan_mode=body.is_plan_mode,
            workspace_root=body.workspace_root,
        )
    except ValueError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    return thread_settings_to_view(updated, path_service=deps.path_service)


def edit_latest_user_message_and_run_sync(
    deps: AppRuntimeDeps,
    thread_id: str,
    message_id: str,
    body: MessageEditResendRequest,
) -> RunCompletedView:
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")

    selected_model = body.selected_model or state.execution.selected_model
    selected_profile = body.profile or state.execution.selected_profile
    selected_reasoning_effort = body.selected_reasoning_effort or state.execution.selected_reasoning_effort
    try:
        _rewrite_latest_user_turn(
            deps,
            thread_id=thread_id,
            message_id=message_id,
            content=deps.path_service.translate_user_text_to_runtime(body.message, thread_id=thread_id),
        )
        result = deps.run_engine.run(
            RunRequest(
                thread_id=thread_id,
                user_message="",
                config_layers=deps.config_layers,
                config_result=deps.config_result,
                path_service=deps.path_service,
                checkpointer=deps.checkpointer,
                store=deps.store,
                feature_set=deps.feature_set,
                execution_mode=body.execution_mode or state.execution.execution_mode,
                selected_model=selected_model,
                selected_reasoning_effort=selected_reasoning_effort,
                profile=selected_profile,
                request_context=deps.path_service.translate_user_text_to_runtime(body.request_context, thread_id=thread_id),
                approval_context=deps.path_service.translate_user_text_to_runtime(body.approval_context, thread_id=thread_id),
                upload_context=deps.path_service.translate_user_text_to_runtime(body.upload_context, thread_id=thread_id),
                is_plan_mode=body.is_plan_mode if body.is_plan_mode is not None else state.execution.is_plan_mode,
                promoted_capabilities=tuple(body.promoted_capabilities),
                **_runtime_shared_services(deps),
                include_user_message=False,
                transcript_rewrite_boundary=True,
                chat_model_override=getattr(deps.run_engine, "_chat_model_override", None),
                approval_session_grants=tuple(state.approvals.session_approval_grants),
                cancellation_checker=lambda: deps.stream_run_manager.is_interrupt_requested(f"edit:{thread_id}"),
                cancellation_reason=lambda: deps.stream_run_manager.interrupt_reason(f"edit:{thread_id}"),
            )
        )
        schedule_memory_capture_flush(deps, result)
    except ValueError as exc:
        if str(exc) == "latest_user_message_only":
            raise GatewayAdapterError(status.HTTP_409_CONFLICT, "latest_user_message_only", "Only the latest user message can be edited and resent.") from exc
        raise
    except Exception as exc:  # noqa: BLE001
        raise runtime_unavailable_error(deps, thread_id, exc) from exc
    return run_result_to_view(result)


def iter_edit_latest_user_message_events(
    deps: AppRuntimeDeps,
    thread_id: str,
    message_id: str,
    body: MessageEditResendRequest,
):
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")

    selected_model = body.selected_model or state.execution.selected_model
    selected_profile = body.profile or state.execution.selected_profile
    selected_reasoning_effort = body.selected_reasoning_effort or state.execution.selected_reasoning_effort
    try:
        _rewrite_latest_user_turn(
            deps,
            thread_id=thread_id,
            message_id=message_id,
            content=deps.path_service.translate_user_text_to_runtime(body.message, thread_id=thread_id),
        )
        session = deps.run_engine.run_stream(
            RunRequest(
                thread_id=thread_id,
                user_message="",
                config_layers=deps.config_layers,
                config_result=deps.config_result,
                path_service=deps.path_service,
                checkpointer=deps.checkpointer,
                store=deps.store,
                feature_set=deps.feature_set,
                execution_mode=body.execution_mode or state.execution.execution_mode,
                selected_model=selected_model,
                selected_reasoning_effort=selected_reasoning_effort,
                profile=selected_profile,
                request_context=deps.path_service.translate_user_text_to_runtime(body.request_context, thread_id=thread_id),
                approval_context=deps.path_service.translate_user_text_to_runtime(body.approval_context, thread_id=thread_id),
                upload_context=deps.path_service.translate_user_text_to_runtime(body.upload_context, thread_id=thread_id),
                is_plan_mode=body.is_plan_mode if body.is_plan_mode is not None else state.execution.is_plan_mode,
                promoted_capabilities=tuple(body.promoted_capabilities),
                **_runtime_shared_services(deps),
                include_user_message=False,
                transcript_rewrite_boundary=True,
                chat_model_override=getattr(deps.run_engine, "_chat_model_override", None),
                approval_session_grants=tuple(state.approvals.session_approval_grants),
                cancellation_checker=lambda: deps.stream_run_manager.is_interrupt_requested(f"edit:{thread_id}"),
                cancellation_reason=lambda: deps.stream_run_manager.interrupt_reason(f"edit:{thread_id}"),
            )
        )
        for event in session:
            if event.event == "run_completed" and session.final_result is not None:
                schedule_memory_capture_flush(deps, session.final_result)
                yield RunStreamEvent(
                    event="run_completed",
                    data=deps.path_service.translate_runtime_data_to_virtual(
                        run_completed_stream_payload(
                            session.final_result,
                            event_data=event.data,
                            known_system_version=deps.system_event_bus.current_version(),
                        ),
                        thread_id=thread_id,
                    ),
                )
                continue
            yield RunStreamEvent(
                event=event.event,
                data=stream_event_payload(deps, thread_id, event.data),
            )
    except ValueError as exc:
        if str(exc) == "latest_user_message_only":
            raise GatewayAdapterError(status.HTTP_409_CONFLICT, "latest_user_message_only", "Only the latest user message can be edited and resent.") from exc
        raise


def _rewrite_latest_user_turn(
    deps: AppRuntimeDeps,
    *,
    thread_id: str,
    message_id: str,
    content: str,
) -> None:
    if deps.subagent_service is not None:
        deps.subagent_service.delete_for_parent_thread(thread_id)
    if deps.process_service is not None:
        deps.process_service.delete_for_thread(thread_id)
    if deps.memory_manager is not None:
        deps.memory_manager.clear_thread_runtime_artifacts(thread_id)
    deps.thread_service.rewrite_latest_user_message(
        thread_id,
        message_id=message_id,
        content=content,
    )


def run_thread_sync(deps: AppRuntimeDeps, thread_id: str, body: RunRequestBody) -> RunCompletedView:
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")

    selected_model = body.selected_model or state.execution.selected_model
    selected_profile = body.profile or state.execution.selected_profile
    selected_reasoning_effort = body.selected_reasoning_effort or state.execution.selected_reasoning_effort

    try:
        result = deps.run_engine.run(
            RunRequest(
                thread_id=thread_id,
                user_message=deps.path_service.translate_user_text_to_runtime(body.message, thread_id=thread_id),
                config_layers=deps.config_layers,
                config_result=deps.config_result,
                path_service=deps.path_service,
                checkpointer=deps.checkpointer,
                store=deps.store,
                feature_set=deps.feature_set,
                execution_mode=body.execution_mode,
                selected_model=selected_model,
                selected_reasoning_effort=selected_reasoning_effort,
                profile=selected_profile,
                request_context=deps.path_service.translate_user_text_to_runtime(body.request_context, thread_id=thread_id),
                approval_context=deps.path_service.translate_user_text_to_runtime(body.approval_context, thread_id=thread_id),
                upload_context=deps.path_service.translate_user_text_to_runtime(body.upload_context, thread_id=thread_id),
                client_message_id=body.client_message_id,
                is_plan_mode=body.is_plan_mode if body.is_plan_mode is not None else state.execution.is_plan_mode,
                promoted_capabilities=tuple(body.promoted_capabilities),
                **_runtime_shared_services(deps),
                recent_upload_filenames=tuple(body.uploaded_filenames),
                chat_model_override=getattr(deps.run_engine, "_chat_model_override", None),
                approval_session_grants=tuple(state.approvals.session_approval_grants),
            )
        )
        schedule_memory_capture_flush(deps, result)
    except Exception as exc:  # noqa: BLE001
        raise runtime_unavailable_error(deps, thread_id, exc) from exc
    finally:
        if body.followup_dispatch_id:
            clear_thread_followup_dispatch(deps, thread_id, body.followup_dispatch_id)
    return run_result_to_view(result)


def resume_thread_approval(deps: AppRuntimeDeps, thread_id: str, body: ApprovalResumeRequest) -> RunCompletedView:
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    if state.approvals.pending_approval is None:
        raise GatewayAdapterError(status.HTTP_409_CONFLICT, "approval_not_pending", f"thread '{thread_id}' has no pending approval")

    selected_model = body.selected_model or state.execution.selected_model
    selected_profile = body.profile or state.execution.selected_profile
    selected_reasoning_effort = body.selected_reasoning_effort or state.execution.selected_reasoning_effort

    try:
        result = deps.run_engine.resume_approval(
            thread_id=thread_id,
            config_layers=deps.config_layers,
            config_result=deps.config_result,
            path_service=deps.path_service,
            checkpointer=deps.checkpointer,
            store=deps.store,
            approval_context=deps.path_service.translate_user_text_to_runtime(body.approval_context, thread_id=thread_id),
            feature_set=deps.feature_set,
            selected_model=selected_model,
            selected_reasoning_effort=selected_reasoning_effort,
            profile=selected_profile,
            request_context=deps.path_service.translate_user_text_to_runtime(body.request_context, thread_id=thread_id),
            upload_context=deps.path_service.translate_user_text_to_runtime(body.upload_context, thread_id=thread_id),
            is_plan_mode=body.is_plan_mode if body.is_plan_mode is not None else state.execution.is_plan_mode,
            promoted_capabilities=tuple(body.promoted_capabilities),
            **_runtime_shared_services(deps),
            chat_model_override=getattr(deps.run_engine, "_chat_model_override", None),
        )
        schedule_memory_capture_flush(deps, result)
    except Exception as exc:  # noqa: BLE001
        raise runtime_unavailable_error(deps, thread_id, exc) from exc
    return run_result_to_view(result)


def cancel_thread_approval(deps: AppRuntimeDeps, thread_id: str, body: ApprovalCancelRequest) -> ThreadStateView:
    try:
        updated = deps.thread_service.cancel_pending_approval(thread_id, reason=body.reason)
    except ValueError as exc:
        message = str(exc)
        if "pending approval" in message:
            raise GatewayAdapterError(status.HTTP_409_CONFLICT, "approval_not_pending", message) from exc
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found") from exc

    artifact_refs = build_canonical_artifact_refs(deps, updated.identity.thread_id)
    execution_policy = deps.thread_service.build_execution_policy_projection(updated)
    return thread_state_to_view(
        updated,
        path_service=deps.path_service,
        artifact_refs=artifact_refs,
        execution_policy=execution_policy,
        runtime_capabilities=build_runtime_capabilities_view(deps),
        subagent_tasks=[subagent_task_to_view(deps, task.task_id) for task in deps.subagent_service.list_tasks(parent_thread_id=thread_id)],
        process_sessions=[
            process_session_to_view(item, path_service=deps.path_service, thread_id=thread_id)
            for item in deps.process_service.list_sessions(thread_id=thread_id)
        ],
    )


def resume_thread_user_interaction(deps: AppRuntimeDeps, thread_id: str, body: UserInteractionResumeRequest) -> RunCompletedView:
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    message = build_user_interaction_response_message(state, body, deps=deps)
    selected_model = body.selected_model or state.execution.selected_model
    selected_profile = body.profile or state.execution.selected_profile
    selected_reasoning_effort = body.selected_reasoning_effort or state.execution.selected_reasoning_effort
    try:
        result = deps.run_engine.run(
            RunRequest(
                thread_id=thread_id,
                user_message=deps.path_service.translate_user_text_to_runtime(message, thread_id=thread_id),
                config_layers=deps.config_layers,
                config_result=deps.config_result,
                path_service=deps.path_service,
                checkpointer=deps.checkpointer,
                store=deps.store,
                feature_set=deps.feature_set,
                execution_mode=state.execution.execution_mode,
                selected_model=selected_model,
                selected_reasoning_effort=selected_reasoning_effort,
                profile=selected_profile,
                request_context=deps.path_service.translate_user_text_to_runtime(body.request_context, thread_id=thread_id),
                upload_context=deps.path_service.translate_user_text_to_runtime(body.upload_context, thread_id=thread_id),
                is_plan_mode=body.is_plan_mode if body.is_plan_mode is not None else state.execution.is_plan_mode,
                promoted_capabilities=tuple(body.promoted_capabilities),
                **_runtime_shared_services(deps),
                chat_model_override=getattr(deps.run_engine, "_chat_model_override", None),
                approval_session_grants=tuple(state.approvals.session_approval_grants),
            )
        )
        schedule_memory_capture_flush(deps, result)
    except Exception as exc:  # noqa: BLE001
        raise runtime_unavailable_error(deps, thread_id, exc) from exc
    return run_result_to_view(result)


def stream_thread_user_interaction_events(
    deps: AppRuntimeDeps,
    thread_id: str,
    body: UserInteractionResumeRequest,
):
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    message = build_user_interaction_response_message(state, body, deps=deps)
    yield from iter_thread_run_events(
        deps,
        thread_id,
        message=message,
        execution_mode=state.execution.execution_mode,
        selected_model=body.selected_model,
        selected_reasoning_effort=body.selected_reasoning_effort,
        profile=body.profile,
        request_context=body.request_context,
        upload_context=body.upload_context,
        is_plan_mode=body.is_plan_mode,
        promoted_capabilities=tuple(body.promoted_capabilities),
    )


def build_user_interaction_response_message(
    state: ThreadState,
    body: UserInteractionSubmitRequest,
    *,
    deps: AppRuntimeDeps | None = None,
) -> str:
    interaction = user_interaction_to_view(state.conversation.pending_user_interaction)
    if interaction is None:
        raise GatewayAdapterError(
            status.HTTP_409_CONFLICT,
            "interaction_not_pending",
            f"thread '{state.identity.thread_id}' has no pending user interaction",
        )
    if body.request_id != interaction.request_id:
        raise GatewayAdapterError(
            status.HTTP_409_CONFLICT,
            "interaction_request_mismatch",
            f"pending interaction is '{interaction.request_id}', not '{body.request_id}'",
        )
    field_responses = _normalize_user_interaction_field_responses(interaction, body)
    lines = [
        "[user-interaction-response]",
        f"Request ID: {interaction.request_id}",
        f"Question: {interaction.question}",
    ]
    for item in field_responses:
        field = item["field"]
        selected_option_ids = item["selected_option_ids"]
        custom_response = item["custom_response"]
        lines.append("")
        lines.append(f"Field: {field.label} ({field.field_id})")
        if selected_option_ids:
            lines.append("Selected options:")
            for option_id in selected_option_ids:
                option = item["options_by_id"][option_id]
                lines.append(f"- {option.label} ({option.id})")
        if custom_response:
            label = field.custom_label or ("Free text" if field.selection_mode == "text" else "Custom response")
            lines.append(f"{label}: {custom_response}")
    message = "\n".join(lines)
    if deps is not None:
        updated = state.model_copy(deep=True)
        updated.conversation.pending_user_interaction = None
        updated.lifecycle.last_error = None
        updated.lifecycle.updated_at = utc_now()
        deps.checkpointer.put_thread_state(updated)
        deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
    return message


def _normalize_user_interaction_field_responses(interaction: UserInteractionRequestView, body: UserInteractionSubmitRequest) -> list[dict[str, object]]:
    fields = list(interaction.fields)
    if not fields:
        fields = [
            UserInteractionFieldView(
                field_id="response",
                label=interaction.question,
                description=interaction.description,
                selection_mode=interaction.selection_mode,
                options=interaction.options,
                min_selections=interaction.min_selections,
                max_selections=interaction.max_selections,
                allow_custom=interaction.allow_custom,
                custom_label=interaction.custom_label,
                placeholder=interaction.placeholder,
                required=interaction.required,
            )
        ]
    responses_by_field: dict[str, object] = {}
    for response in body.field_responses:
        if response.field_id in responses_by_field:
            raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "duplicate_interaction_field", f"duplicate response for field: {response.field_id}")
        responses_by_field[response.field_id] = response
    if responses_by_field:
        unknown_field = next((field_id for field_id in responses_by_field if field_id not in {field.field_id for field in fields}), None)
        if unknown_field is not None:
            raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_interaction_field", f"unknown field id: {unknown_field}")
    normalized: list[dict[str, object]] = []
    for index, field in enumerate(fields):
        response = responses_by_field.get(field.field_id)
        if response is None and index == 0 and not responses_by_field:
            selected_option_ids = list(body.selected_option_ids)
            custom_response = (body.custom_response or body.free_text or "").strip()
        elif response is None:
            selected_option_ids = []
            custom_response = ""
        else:
            selected_option_ids = list(getattr(response, "selected_option_ids", []))
            custom_response = (getattr(response, "custom_response", None) or getattr(response, "free_text", None) or "").strip()
        _validate_user_interaction_field_response(field, selected_option_ids=selected_option_ids, custom_response=custom_response)
        normalized.append(
            {
                "field": field,
                "selected_option_ids": selected_option_ids,
                "custom_response": custom_response,
                "options_by_id": {option.id: option for option in field.options},
            }
        )
    return normalized


def _validate_user_interaction_field_response(
    field: UserInteractionFieldView,
    *,
    selected_option_ids: list[str],
    custom_response: str,
) -> None:
    options_by_id = {option.id: option for option in field.options}
    unknown = [option_id for option_id in selected_option_ids if option_id not in options_by_id]
    if unknown:
        raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_interaction_option", f"unknown option id: {unknown[0]}")
    disabled = [option_id for option_id in selected_option_ids if options_by_id[option_id].disabled]
    if disabled:
        raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "disabled_interaction_option", f"option is disabled: {disabled[0]}")
    if field.selection_mode == "single" and len(selected_option_ids) > 1:
        raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "too_many_interaction_choices", f"single-select field '{field.field_id}' accepts one option")
    selection_count = len(selected_option_ids)
    if custom_response and (field.allow_custom or field.selection_mode == "text"):
        selection_count += 1
    if selection_count < field.min_selections:
        raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "too_few_interaction_choices", f"not enough choices were selected for field '{field.field_id}'")
    if field.max_selections is not None and selection_count > field.max_selections:
        raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "too_many_interaction_choices", f"too many choices were selected for field '{field.field_id}'")
    if custom_response and not field.allow_custom and field.selection_mode != "text":
        raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "custom_response_not_allowed", f"custom response is not allowed for field '{field.field_id}'")


def interrupt_thread_run(deps: AppRuntimeDeps, thread_id: str, *, reason: str = "Interrupted by user") -> ThreadStateView:
    try:
        updated = deps.thread_service.request_thread_interrupt(thread_id, reason=reason)
    except ValueError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    for stream_key in (f"run:{thread_id}", f"approval:{thread_id}", f"edit:{thread_id}"):
        deps.stream_run_manager.request_interrupt(stream_key, reason=reason)
    if deps.subagent_service is not None:
        for task in deps.subagent_service.list_tasks(parent_thread_id=thread_id):
            if str(task.status.value) not in TERMINAL_RUNTIME_STATUSES:
                deps.subagent_service.cancel(task.task_id, reason=reason)
    if deps.process_service is not None:
        for session in deps.process_service.list_sessions(thread_id=thread_id):
            if str(session.status.value if hasattr(session.status, "value") else session.status) == "running":
                try:
                    deps.process_service.interrupt(session.session_id)
                except Exception:
                    pass

    artifact_refs = build_canonical_artifact_refs(deps, updated.identity.thread_id)
    execution_policy = deps.thread_service.build_execution_policy_projection(updated)
    return thread_state_to_view(
        updated,
        path_service=deps.path_service,
        artifact_refs=artifact_refs,
        execution_policy=execution_policy,
        runtime_capabilities=build_runtime_capabilities_view(deps),
        subagent_tasks=[
            subagent_task_to_view(deps, task.task_id)
            for task in (deps.subagent_service.list_tasks(parent_thread_id=thread_id) if deps.subagent_service is not None else [])
        ],
        process_sessions=[
            process_session_to_view(item, path_service=deps.path_service, thread_id=thread_id)
            for item in (deps.process_service.list_sessions(thread_id=thread_id) if deps.process_service is not None else [])
        ],
    )


def enqueue_thread_followup(
    deps: AppRuntimeDeps,
    thread_id: str,
    body: QueuedFollowUpCreateRequest,
) -> QueuedFollowUpView:
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    message = body.message.strip()
    if not message:
        raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty_followup", "follow-up message cannot be empty")
    now = utc_now()
    mode = _normalize_followup_mode(body.mode)
    item = {
        "queue_id": f"followup-{uuid4().hex[:12]}",
        "thread_id": thread_id,
        "message": deps.path_service.translate_user_text_to_runtime(message, thread_id=thread_id) or message,
        "mode": mode,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "execution_mode": body.execution_mode.value if body.execution_mode is not None else None,
        "selected_model": body.selected_model,
        "selected_reasoning_effort": body.selected_reasoning_effort,
        "profile": body.profile,
        "upload_context": deps.path_service.translate_user_text_to_runtime(body.upload_context, thread_id=thread_id),
        "uploaded_filenames": list(body.uploaded_filenames),
        "uploaded_file_refs": [item.model_dump(mode="json") for item in body.uploaded_file_refs],
        "promoted_capabilities": list(body.promoted_capabilities),
        "is_plan_mode": body.is_plan_mode,
    }
    updated = state.model_copy(deep=True)
    if body.insert_position == "front":
        updated.conversation.queued_followups.insert(0, item)
    else:
        updated.conversation.queued_followups.append(item)
    if body.insert_position == "front":
        updated.conversation.active_followup_dispatch = None
    updated.lifecycle.updated_at = now
    deps.checkpointer.put_thread_state(updated)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
    return queued_followup_to_view(item, path_service=deps.path_service, thread_id=thread_id)


def update_thread_followup(
    deps: AppRuntimeDeps,
    thread_id: str,
    queue_id: str,
    body: QueuedFollowUpUpdateRequest,
) -> QueuedFollowUpView:
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    updated = state.model_copy(deep=True)
    item = _find_followup(updated.conversation.queued_followups, queue_id)
    if item is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "followup_not_found", f"follow-up '{queue_id}' was not found")
    if body.message is not None:
        message = body.message.strip()
        if not message:
            raise GatewayAdapterError(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty_followup", "follow-up message cannot be empty")
        item["message"] = deps.path_service.translate_user_text_to_runtime(message, thread_id=thread_id) or message
    if body.mode is not None:
        item["mode"] = _normalize_followup_mode(body.mode)
    item["updated_at"] = utc_now()
    updated.lifecycle.updated_at = utc_now()
    deps.checkpointer.put_thread_state(updated)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
    return queued_followup_to_view(item, path_service=deps.path_service, thread_id=thread_id)


def delete_thread_followup(deps: AppRuntimeDeps, thread_id: str, queue_id: str) -> QueuedFollowUpView:
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    updated = state.model_copy(deep=True)
    for index, item in enumerate(updated.conversation.queued_followups):
        if str(item.get("queue_id") or "") != queue_id:
            continue
        removed = updated.conversation.queued_followups.pop(index)
        updated.lifecycle.updated_at = utc_now()
        deps.checkpointer.put_thread_state(updated)
        deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
        return queued_followup_to_view(removed, path_service=deps.path_service, thread_id=thread_id)
    raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "followup_not_found", f"follow-up '{queue_id}' was not found")


def pop_next_thread_followup(deps: AppRuntimeDeps, thread_id: str) -> QueuedFollowUpView | None:
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    state = _recover_stale_followup_dispatch_if_idle(deps, state)
    if state.conversation.active_followup_dispatch is not None:
        raise GatewayAdapterError(
            status.HTTP_409_CONFLICT,
            "thread_followup_dispatch_in_flight",
            f"thread '{thread_id}' already has a queued follow-up dispatch in flight",
        )
    if not _thread_can_dispatch_followup(state):
        raise GatewayAdapterError(
            status.HTTP_409_CONFLICT,
            "thread_not_ready_for_followup",
            f"thread '{thread_id}' is not ready to dispatch queued follow-ups",
        )
    updated = state.model_copy(deep=True)
    if not updated.conversation.queued_followups:
        return None
    selected_index = _next_followup_index(updated.conversation.queued_followups)
    item = updated.conversation.queued_followups.pop(selected_index)
    now = utc_now()
    updated.conversation.active_followup_dispatch = {
        "dispatch_id": f"dispatch-{uuid4().hex[:12]}",
        "queue_id": str(item.get("queue_id") or ""),
        "started_at": now,
        "status": "dispatching",
    }
    updated.lifecycle.updated_at = now
    deps.checkpointer.put_thread_state(updated)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
    return queued_followup_to_view(
        item,
        path_service=deps.path_service,
        thread_id=thread_id,
        dispatch_id=str(updated.conversation.active_followup_dispatch.get("dispatch_id") or ""),
    )


def clear_thread_followup_dispatch(
    deps: AppRuntimeDeps,
    thread_id: str,
    dispatch_id: str | None = None,
) -> None:
    state = deps.checkpointer.get_thread_state(thread_id)
    if state is None or state.conversation.active_followup_dispatch is None:
        return
    active = state.conversation.active_followup_dispatch
    if dispatch_id and str(active.get("dispatch_id") or "") != dispatch_id:
        return
    updated = state.model_copy(deep=True)
    updated.conversation.active_followup_dispatch = None
    updated.lifecycle.updated_at = utc_now()
    deps.checkpointer.put_thread_state(updated)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))


def _recover_stale_followup_dispatch_if_idle(deps: AppRuntimeDeps, state: ThreadState) -> ThreadState:
    active = state.conversation.active_followup_dispatch
    if active is None or not _thread_can_dispatch_followup(state):
        return state
    started_at = _coerce_datetime(active.get("started_at"))
    if utc_now() - started_at <= FOLLOWUP_DISPATCH_LEASE_TTL:
        return state
    updated = state.model_copy(deep=True)
    updated.conversation.active_followup_dispatch = None
    updated.lifecycle.updated_at = utc_now()
    deps.checkpointer.put_thread_state(updated)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
    return updated


def _normalize_followup_mode(mode: object) -> str:
    value = str(mode or "followup").strip().lower()
    if value in {"guidance", "guide", "steer"}:
        return "guidance"
    return "followup"


def _find_followup(items: list[dict[str, object]], queue_id: str) -> dict[str, object] | None:
    for item in items:
        if str(item.get("queue_id") or "") == queue_id:
            return item
    return None


def _next_followup_index(items: list[dict[str, object]]) -> int:
    for index, item in enumerate(items):
        if _normalize_followup_mode(item.get("mode")) == "guidance":
            return index
    return 0


def _thread_can_dispatch_followup(state: ThreadState) -> bool:
    if state.lifecycle.status in {
        ThreadLifecycleStatus.RUNNING,
        ThreadLifecycleStatus.AWAITING_APPROVAL,
        ThreadLifecycleStatus.AWAITING_CLARIFICATION,
    }:
        return False
    if state.approvals.pending_approval is not None:
        return False
    return True


def queued_followup_to_view(
    item: dict[str, object],
    *,
    path_service=None,
    thread_id: str | None = None,
    dispatch_id: str | None = None,
) -> QueuedFollowUpView:
    effective_thread_id = str(item.get("thread_id") or thread_id or "")
    message = str(item.get("message") or "")
    upload_context = item.get("upload_context")
    if path_service is not None and effective_thread_id:
        message = path_service.translate_runtime_text_to_virtual(message, thread_id=effective_thread_id) or message
        if isinstance(upload_context, str):
            upload_context = path_service.translate_runtime_text_to_virtual(upload_context, thread_id=effective_thread_id) or upload_context
    return QueuedFollowUpView(
        queue_id=str(item.get("queue_id") or ""),
        thread_id=effective_thread_id,
        message=message,
        mode=_normalize_followup_mode(item.get("mode")),
        status=str(item.get("status") or "queued"),
        created_at=_coerce_datetime(item.get("created_at")),
        updated_at=_coerce_datetime(item.get("updated_at")),
        execution_mode=_coerce_execution_mode(item.get("execution_mode")),
        selected_model=str(item["selected_model"]) if item.get("selected_model") is not None else None,
        selected_reasoning_effort=str(item["selected_reasoning_effort"]) if item.get("selected_reasoning_effort") is not None else None,
        profile=str(item["profile"]) if item.get("profile") is not None else None,
        upload_context=str(upload_context) if upload_context is not None else None,
        uploaded_filenames=[str(value) for value in item.get("uploaded_filenames", []) if value is not None] if isinstance(item.get("uploaded_filenames"), list) else [],
        uploaded_file_refs=[
            ArtifactRefView.model_validate(value)
            for value in item.get("uploaded_file_refs", [])
            if isinstance(value, dict)
        ]
        if isinstance(item.get("uploaded_file_refs"), list)
        else [],
        promoted_capabilities=[str(value) for value in item.get("promoted_capabilities", []) if value is not None] if isinstance(item.get("promoted_capabilities"), list) else [],
        is_plan_mode=bool(item["is_plan_mode"]) if item.get("is_plan_mode") is not None else None,
        dispatch_id=dispatch_id,
    )


def queued_followup_dispatch_to_view(item: dict[str, object] | None) -> QueuedFollowUpDispatchView | None:
    if not isinstance(item, dict):
        return None
    dispatch_id = str(item.get("dispatch_id") or "")
    queue_id = str(item.get("queue_id") or "")
    if not dispatch_id or not queue_id:
        return None
    return QueuedFollowUpDispatchView(
        dispatch_id=dispatch_id,
        queue_id=queue_id,
        started_at=_coerce_datetime(item.get("started_at")),
        status="dispatching",
    )


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return utc_now()


def _coerce_execution_mode(value: object) -> ThreadExecutionMode | None:
    if value is None:
        return None
    try:
        return ThreadExecutionMode(str(value))
    except ValueError:
        return None


def _run_preparing_event(
    deps: AppRuntimeDeps,
    thread_id: str,
    *,
    execution_mode: ThreadExecutionMode | str,
    selected_model: str | None,
    selected_reasoning_effort: str | None,
    profile: str | None,
    is_plan_mode: bool | None,
    source: str = "gateway",
) -> RunStreamEvent:
    execution_mode_value = execution_mode.value if isinstance(execution_mode, ThreadExecutionMode) else str(execution_mode)
    return RunStreamEvent(
        event="run_preparing",
        data=stream_event_payload(
            deps,
            thread_id,
            {
                "thread_id": thread_id,
                "status": "preparing",
                "phase": "gateway_received",
                "source": source,
                "execution_mode": execution_mode_value,
                "selected_model": selected_model,
                "selected_reasoning_effort": selected_reasoning_effort,
                "profile": profile,
                "is_plan_mode": is_plan_mode,
            },
        ),
    )


def run_event_envelope_to_stream_event(deps: AppRuntimeDeps, envelope: RunEventEnvelope) -> RunStreamEvent:
    event = envelope.to_run_event()
    payload = stream_event_payload(deps, envelope.thread_id, event.data)
    return RunStreamEvent(
        event=event.event,
        data=payload,
        event_id=str(payload.get("event_id")) if payload.get("event_id") is not None else None,
        sequence=int(payload["sequence"]) if payload.get("sequence") is not None else None,
        message_id=str(payload.get("message_id")) if payload.get("message_id") is not None else None,
        block_id=str(payload.get("block_id")) if payload.get("block_id") is not None else None,
        visibility=str(payload.get("visibility")) if payload.get("visibility") is not None else None,
        source=str(payload.get("source")) if payload.get("source") is not None else None,
    )


def list_thread_run_events(
    deps: AppRuntimeDeps,
    thread_id: str,
    *,
    run_id: str | None = None,
    after_sequence: int | None = None,
    limit: int = 100,
) -> RunEventReplayView:
    if deps.checkpointer.get_thread_state(thread_id) is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    if after_sequence is not None and run_id is None:
        raise GatewayAdapterError(
            status.HTTP_400_BAD_REQUEST,
            "run_id_required_for_cursor",
            "run_id is required when replaying run events after a sequence cursor because event sequences are run-local.",
        )
    page = list_run_event_page(
        deps.run_event_log_store,
        thread_id=thread_id,
        run_id=run_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return RunEventReplayView(
        thread_id=thread_id,
        run_id=run_id,
        after_sequence=after_sequence,
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        events=[run_event_envelope_to_stream_event(deps, envelope) for envelope in page.events],
    )


def iter_thread_run_events(
    deps: AppRuntimeDeps,
    thread_id: str,
    *,
    message: str,
    execution_mode: ThreadExecutionMode = ThreadExecutionMode.AGENT,
    selected_model: str | None = None,
    selected_reasoning_effort: str | None = None,
    profile: str | None = None,
    request_context: str | None = None,
    approval_context: str | None = None,
    upload_context: str | None = None,
    is_plan_mode: bool | None = None,
    promoted_capabilities: tuple[str, ...] = (),
    uploaded_filenames: tuple[str, ...] = (),
    followup_dispatch_id: str | None = None,
    client_message_id: str | None = None,
):
    previous_state = deps.checkpointer.get_thread_state(thread_id)
    if previous_state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")

    selected_model = selected_model or previous_state.execution.selected_model
    selected_profile = profile or previous_state.execution.selected_profile
    selected_reasoning_effort = selected_reasoning_effort or previous_state.execution.selected_reasoning_effort
    resolved_plan_mode = is_plan_mode if is_plan_mode is not None else previous_state.execution.is_plan_mode

    yield _run_preparing_event(
        deps,
        thread_id,
        execution_mode=execution_mode,
        selected_model=selected_model,
        selected_reasoning_effort=selected_reasoning_effort,
        profile=selected_profile,
        is_plan_mode=resolved_plan_mode,
    )
    try:
        session = deps.run_engine.run_stream(
            RunRequest(
                thread_id=thread_id,
                user_message=deps.path_service.translate_user_text_to_runtime(message, thread_id=thread_id),
                config_layers=deps.config_layers,
                config_result=deps.config_result,
                path_service=deps.path_service,
                checkpointer=deps.checkpointer,
                store=deps.store,
                feature_set=deps.feature_set,
                execution_mode=execution_mode,
                selected_model=selected_model,
                selected_reasoning_effort=selected_reasoning_effort,
                profile=selected_profile,
                request_context=deps.path_service.translate_user_text_to_runtime(request_context, thread_id=thread_id),
                approval_context=deps.path_service.translate_user_text_to_runtime(approval_context, thread_id=thread_id),
                upload_context=deps.path_service.translate_user_text_to_runtime(upload_context, thread_id=thread_id),
                client_message_id=client_message_id,
                is_plan_mode=resolved_plan_mode,
                promoted_capabilities=promoted_capabilities,
                **_runtime_shared_services(deps),
                recent_upload_filenames=uploaded_filenames,
                chat_model_override=getattr(deps.run_engine, "_chat_model_override", None),
                approval_session_grants=tuple(previous_state.approvals.session_approval_grants),
                cancellation_checker=lambda: deps.stream_run_manager.is_interrupt_requested(f"run:{thread_id}"),
                cancellation_reason=lambda: deps.stream_run_manager.interrupt_reason(f"run:{thread_id}"),
            )
        )
        for event in session:
            if event.event == "run_completed" and session.final_result is not None:
                schedule_memory_capture_flush(deps, session.final_result)
                yield RunStreamEvent(
                    event="run_completed",
                    data=deps.path_service.translate_runtime_data_to_virtual(
                        run_completed_stream_payload(
                            session.final_result,
                            event_data=event.data,
                            known_system_version=deps.system_event_bus.current_version(),
                        ),
                        thread_id=thread_id,
                    ),
                )
                continue
            yield RunStreamEvent(
                event=event.event,
                data=stream_event_payload(deps, thread_id, event.data),
            )
    except GatewayAdapterError:
        raise
    except Exception as exc:  # noqa: BLE001
        yield RunStreamEvent(
            event="run_failed",
            data=stream_event_payload(
                deps,
                thread_id,
                {"thread_id": thread_id, "error": str(exc), "kind": exc.__class__.__name__},
            ),
        )
    finally:
        if followup_dispatch_id:
            clear_thread_followup_dispatch(deps, thread_id, followup_dispatch_id)


def iter_thread_approval_events(
    deps: AppRuntimeDeps,
    thread_id: str,
    *,
    approval_context: str,
    profile: str | None = None,
    request_context: str | None = None,
    upload_context: str | None = None,
    promoted_capabilities: tuple[str, ...] = (),
):
    previous_state = deps.checkpointer.get_thread_state(thread_id)
    if previous_state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    if previous_state.approvals.pending_approval is None:
        raise GatewayAdapterError(status.HTTP_409_CONFLICT, "approval_not_pending", f"thread '{thread_id}' has no pending approval")

    selected_model = previous_state.execution.selected_model
    selected_profile = profile or previous_state.execution.selected_profile
    selected_reasoning_effort = previous_state.execution.selected_reasoning_effort

    try:
        session = deps.run_engine.run_stream(
            RunRequest(
                thread_id=thread_id,
                user_message="",
                config_layers=deps.config_layers,
                config_result=deps.config_result,
                path_service=deps.path_service,
                checkpointer=deps.checkpointer,
                store=deps.store,
                feature_set=deps.feature_set,
                execution_mode=previous_state.execution.execution_mode,
                selected_model=selected_model,
                selected_reasoning_effort=selected_reasoning_effort,
                profile=selected_profile,
                request_context=deps.path_service.translate_user_text_to_runtime(request_context, thread_id=thread_id),
                approval_context=deps.path_service.translate_user_text_to_runtime(approval_context, thread_id=thread_id),
                upload_context=deps.path_service.translate_user_text_to_runtime(upload_context, thread_id=thread_id),
                is_plan_mode=previous_state.execution.is_plan_mode,
                promoted_capabilities=promoted_capabilities,
                **_runtime_shared_services(deps),
                include_user_message=False,
                drop_last_assistant_message=True,
                recent_upload_filenames=(),
                chat_model_override=getattr(deps.run_engine, "_chat_model_override", None),
                approval_session_grants=tuple(previous_state.approvals.session_approval_grants),
                cancellation_checker=lambda: deps.stream_run_manager.is_interrupt_requested(f"approval:{thread_id}"),
                cancellation_reason=lambda: deps.stream_run_manager.interrupt_reason(f"approval:{thread_id}"),
            )
        )
        for event in session:
            if event.event == "run_completed" and session.final_result is not None:
                schedule_memory_capture_flush(deps, session.final_result)
                yield RunStreamEvent(
                    event="run_completed",
                    data=deps.path_service.translate_runtime_data_to_virtual(
                        run_completed_stream_payload(
                            session.final_result,
                            event_data=event.data,
                            known_system_version=deps.system_event_bus.current_version(),
                        ),
                        thread_id=thread_id,
                    ),
                )
                continue
            yield RunStreamEvent(
                event=event.event,
                data=stream_event_payload(deps, thread_id, event.data),
            )
    except GatewayAdapterError:
        raise
    except Exception as exc:  # noqa: BLE001
        yield RunStreamEvent(
            event="run_failed",
            data=stream_event_payload(
                deps,
                thread_id,
                {"thread_id": thread_id, "error": str(exc), "kind": exc.__class__.__name__},
            ),
        )


def stream_thread_run_events(
    deps: AppRuntimeDeps,
    thread_id: str,
    *,
    message: str,
    execution_mode: ThreadExecutionMode = ThreadExecutionMode.AGENT,
    selected_model: str | None = None,
    selected_reasoning_effort: str | None = None,
    profile: str | None = None,
    request_context: str | None = None,
    approval_context: str | None = None,
    upload_context: str | None = None,
    is_plan_mode: bool | None = None,
    promoted_capabilities: tuple[str, ...] = (),
    uploaded_filenames: tuple[str, ...] = (),
    followup_dispatch_id: str | None = None,
    last_event_id: str | None = None,
    client_message_id: str | None = None,
):
    previous_state = deps.checkpointer.get_thread_state(thread_id)
    if previous_state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    def event_factory():
        return iter_thread_run_events(
            deps,
            thread_id,
            message=message,
            execution_mode=execution_mode,
            selected_model=selected_model,
            selected_reasoning_effort=selected_reasoning_effort,
            profile=profile,
            request_context=request_context,
            approval_context=approval_context,
            upload_context=upload_context,
            is_plan_mode=is_plan_mode if is_plan_mode is not None else previous_state.execution.is_plan_mode,
            promoted_capabilities=promoted_capabilities,
            uploaded_filenames=uploaded_filenames,
            followup_dispatch_id=followup_dispatch_id,
            client_message_id=client_message_id,
        )

    stream_key = f"run:{thread_id}"
    for event in deps.stream_run_manager.stream(stream_key, event_factory, last_event_id=last_event_id):
        yield encode_sse(event)


def stream_thread_approval_events(
    deps: AppRuntimeDeps,
    thread_id: str,
    *,
    approval_context: str,
    profile: str | None = None,
    request_context: str | None = None,
    upload_context: str | None = None,
    promoted_capabilities: tuple[str, ...] = (),
):
    def event_factory():
        return iter_thread_approval_events(
            deps,
            thread_id,
            approval_context=approval_context,
            profile=profile,
            request_context=request_context,
            upload_context=upload_context,
            promoted_capabilities=promoted_capabilities,
        )

    stream_key = f"approval:{thread_id}"
    for event in deps.stream_run_manager.stream(stream_key, event_factory):
        yield encode_sse(event)


def upload_files(
    deps: AppRuntimeDeps,
    thread_id: str,
    files: list[tuple[str, bytes]],
) -> UploadResult:
    try:
        results = deps.upload_service.write_files(thread_id, files)
    except UploadThreadNotFoundError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    except UploadValidationError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_filename", str(exc)) from exc

    items = [
        UploadItemView(
            filename=item.filename,
            kind=item.descriptor.kind.value,
            virtual_path=deps.path_service.translate_runtime_text_to_virtual(item.descriptor.virtual_path, thread_id=thread_id) or item.descriptor.virtual_path,
            artifact_url=item.descriptor.artifact_url,
            source_scope=str(item.payload.get("source_scope")) if item.payload.get("source_scope") is not None else None,
            internal=bool(item.payload.get("internal", False)),
            extension=str(item.payload.get("extension")) if item.payload.get("extension") is not None else None,
            markdown_file=str(item.payload.get("markdown_file")) if item.payload.get("markdown_file") is not None else None,
            markdown_virtual_path=translate_companion_path(deps, thread_id, item.payload, "markdown_virtual_path"),
            markdown_artifact_url=str(item.payload.get("markdown_artifact_url")) if item.payload.get("markdown_artifact_url") is not None else None,
            companions=companion_artifacts_from_payload(item.payload.get("companions")),
            extraction=document_extraction_from_payload(item.payload.get("extraction")),
            outline=document_outline_from_payload(item.payload.get("outline")),
            outline_preview=[str(value) for value in item.payload.get("outline_preview", [])] if isinstance(item.payload.get("outline_preview"), list) else [],
            converter_used=str(item.payload.get("converter_used")) if item.payload.get("converter_used") is not None else None,
            ocr_used=bool(item.payload.get("ocr_used", False)),
            conversion_error=str(item.payload.get("conversion_error")) if item.payload.get("conversion_error") is not None else None,
        )
        for item in results
    ]
    return UploadResult(thread_id=thread_id, files=items)


def list_uploads(deps: AppRuntimeDeps, thread_id: str) -> UploadResult:
    try:
        uploaded_files = deps.upload_service.list_uploaded_files(thread_id)
    except UploadThreadNotFoundError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    items = [
        UploadItemView(
            filename=item["filename"],
            kind="uploads",
            virtual_path=deps.path_service.translate_runtime_text_to_virtual(item["virtual_path"], thread_id=thread_id) or item["virtual_path"],
            artifact_url=item["artifact_url"],
            source_scope=str(item.get("source_scope")) if item.get("source_scope") is not None else None,
            internal=bool(item.get("internal", False)),
            extension=str(item.get("extension")) if item.get("extension") is not None else None,
            markdown_file=str(item.get("markdown_file")) if item.get("markdown_file") is not None else None,
            markdown_virtual_path=translate_companion_path(deps, thread_id, item, "markdown_virtual_path"),
            markdown_artifact_url=str(item.get("markdown_artifact_url")) if item.get("markdown_artifact_url") is not None else None,
            companions=companion_artifacts_from_payload(item.get("companions")),
            extraction=document_extraction_from_payload(item.get("extraction")),
            outline=document_outline_from_payload(item.get("outline")),
            outline_preview=[str(value) for value in item.get("outline_preview", [])] if isinstance(item.get("outline_preview"), list) else [],
            converter_used=str(item.get("converter_used")) if item.get("converter_used") is not None else None,
            ocr_used=bool(item.get("ocr_used", False)),
            conversion_error=str(item.get("conversion_error")) if item.get("conversion_error") is not None else None,
        )
        for item in uploaded_files
    ]
    return UploadResult(thread_id=thread_id, files=items)


def get_artifact_content(deps: AppRuntimeDeps, thread_id: str, kind: str, relative_path: str) -> tuple[bytes, str]:
    try:
        _, content, media_type = deps.upload_service.read_artifact(thread_id, kind, relative_path)
    except UploadThreadNotFoundError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    except UploadValidationError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_artifact_path", str(exc)) from exc
    except UploadArtifactNotFoundError:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "artifact_not_found", f"artifact '{relative_path}' was not found")
    return content, media_type


def translate_companion_path(
    deps: AppRuntimeDeps,
    thread_id: str,
    payload: dict[str, object],
    key: str,
) -> str | None:
    raw = payload.get(key)
    if raw is None:
        return None
    translated = deps.path_service.translate_runtime_text_to_virtual(str(raw), thread_id=thread_id)
    return translated or str(raw)


def list_models(deps: AppRuntimeDeps) -> list[ModelView]:
    models = list(deps.config_result.effective_config.models.values())
    internal_task_model = resolve_internal_task_model_name(deps.config_result.effective_config)
    internal_task_concrete_model = resolve_internal_task_concrete_model_name(deps.config_result.effective_config)
    return [
        ModelView(
            name=model.name,
            display_name=model.display_name,
            description=model.description,
            available=_model_api_key_available(model),
            source="config",
            use=model.use,
            provider=model.provider,
            provider_kind=model.provider_kind.value if model.provider_kind is not None else None,
            model_name=model.model_name,
            default_model=model.default_model,
            selected_model=model.selected_model,
            model_catalog=list(model.model_catalog),
            context_window_tokens=model.effective_context_window_tokens(),
            auto_compact_threshold_tokens=model.effective_auto_compact_threshold_tokens(),
            max_tokens=model.max_tokens,
            temperature=model.temperature,
            top_p=model.top_p,
            model_context_windows=dict(model.model_context_windows),
            model_auto_compact_thresholds=dict(model.model_auto_compact_thresholds),
            base_url=model.base_url,
            api_key_env=model.api_key_env,
            default_reasoning_effort=model.default_reasoning_effort,
            supports_tool_calling=model.supports_tool_calling,
            supports_thinking=model.supports_thinking,
            supports_reasoning_effort=model.supports_reasoning_effort,
            supports_vision=model.supports_vision,
            supports_image_generation=model.supports_image_generation,
            timeout=model.request_timeout or model.default_request_timeout or model.timeout,
            request_timeout=model.request_timeout,
            default_request_timeout=model.default_request_timeout,
            max_retries=model.max_retries,
            use_responses_api=model.use_responses_api,
            output_version=model.output_version,
            image_generation=model.image_generation,
            diagnostics=_model_diagnostics(model),
            capabilities=model.capabilities.model_dump(mode="json"),
            internal_task_default=model.name == internal_task_model,
            internal_task_selected_model=internal_task_concrete_model if model.name == internal_task_model else None,
        )
        for model in models
    ]


def list_model_provider_presets() -> list[ModelProviderPresetView]:
    return [
        _model_provider_preset_to_view(name, preset)
        for name, preset in llm_provider_presets().items()
    ]


async def upsert_model_provider(
    deps: AppRuntimeDeps,
    request: ModelProviderUpsertRequest,
) -> ModelProviderUpsertView:
    provider = _normalize_provider_name(request.provider)
    if not provider:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_model_provider", "provider is required")
    name = _normalize_model_provider_name(request.name or provider)
    if not name:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_model_provider", "model provider name is required")

    config_path = _writable_config_path()
    original_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    dotenv_path = config_path.parent / ".env"
    original_dotenv = dotenv_path.read_text(encoding="utf-8") if dotenv_path.exists() else None
    try:
        payload = read_config_file(config_path) if config_path.exists() else _minimal_home_config_payload()
        llm = payload.setdefault("llm", {})
        if not isinstance(llm, dict):
            raise GatewayAdapterError(status.HTTP_409_CONFLICT, "config_file_invalid", "config key 'llm' must be a mapping")
        providers = llm.setdefault("providers", {})
        if not isinstance(providers, dict):
            raise GatewayAdapterError(status.HTTP_409_CONFLICT, "config_file_invalid", "config key 'llm.providers' must be a mapping")
        existing_provider = providers.get(name)
        entry, api_key_env = _build_model_provider_entry(
            request,
            provider=provider,
            name=name,
            existing=existing_provider if isinstance(existing_provider, dict) else None,
        )
        providers[name] = entry
        if not llm.get("default") and not payload.get("default_model"):
            llm["default"] = name
            payload["default_model"] = name
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
        if request.api_key:
            _upsert_dotenv_value(dotenv_path, api_key_env, request.api_key)
            os.environ[api_key_env] = request.api_key
        load_dotenv_file(dotenv_path, override=True)
        _replace_runtime_config_layer(deps, build_config_layers_from_file(config_path)[0])
        reload_view = await admin_reload(deps, scope="config")
    except GatewayAdapterError:
        _restore_config_files(config_path, original_text, dotenv_path, original_dotenv)
        raise
    except Exception:
        _restore_config_files(config_path, original_text, dotenv_path, original_dotenv)
        try:
            await admin_reload(deps, scope="config")
        except Exception:
            pass
        raise

    refreshed_model = next((model for model in list_models(deps) if model.name == name), None)
    if refreshed_model is None:
        raise GatewayAdapterError(status.HTTP_500_INTERNAL_SERVER_ERROR, "model_reload_failed", f"model provider '{name}' disappeared after config reload")
    return ModelProviderUpsertView(
        name=name,
        provider=provider,
        config_path=str(config_path),
        dotenv_path=str(dotenv_path) if request.api_key else None,
        config_fingerprint=str(reload_view.get("config_fingerprint") or deps.config_result.fingerprint),
        model=refreshed_model,
    )


async def delete_model_provider(deps: AppRuntimeDeps, name: str) -> ModelProviderDeleteView:
    provider_name = _normalize_model_provider_name(name)
    if not provider_name:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_model_provider", "model provider name is required")
    config_path = _writable_config_path()
    if not config_path.exists():
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "model_not_found", f"model provider '{provider_name}' was not found")
    original_text = config_path.read_text(encoding="utf-8")
    try:
        payload = read_config_file(config_path)
        llm = payload.get("llm")
        providers = llm.get("providers") if isinstance(llm, dict) else None
        removed = False
        if isinstance(providers, dict) and provider_name in providers:
            del providers[provider_name]
            removed = True
            if llm.get("default") == provider_name:
                llm["default"] = next(iter(providers), None)
            if payload.get("default_model") == provider_name:
                payload["default_model"] = llm.get("default")
        models = payload.get("models")
        if isinstance(models, dict) and provider_name in models:
            del models[provider_name]
            removed = True
            if payload.get("default_model") == provider_name:
                payload["default_model"] = next(iter(models), None)
        if not removed:
            raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "model_not_found", f"model provider '{provider_name}' was not found")
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
        _replace_runtime_config_layer(deps, build_config_layers_from_file(config_path)[0])
        reload_view = await admin_reload(deps, scope="config")
    except GatewayAdapterError:
        config_path.write_text(original_text, encoding="utf-8")
        raise
    except Exception:
        config_path.write_text(original_text, encoding="utf-8")
        try:
            await admin_reload(deps, scope="config")
        except Exception:
            pass
        raise
    return ModelProviderDeleteView(
        name=provider_name,
        deleted=True,
        config_path=str(config_path),
        config_fingerprint=str(reload_view.get("config_fingerprint") or deps.config_result.fingerprint),
    )


async def update_model_selection(
    deps: AppRuntimeDeps,
    name: str,
    request: ModelSelectionUpdateRequest,
) -> ModelSelectionUpdateView:
    provider_name = name.strip()
    selected_model = request.model_name.strip()
    default_reasoning_effort = _normalize_reasoning_effort(request.default_reasoning_effort)
    default_reasoning_effort_provided = "default_reasoning_effort" in request.model_fields_set
    config_path = resolve_config_path(repo_root=get_repo_root())
    if config_path is None:
        raise GatewayAdapterError(
            status.HTTP_409_CONFLICT,
            "config_file_missing",
            "no writable config.yaml was found; create a config file before changing model defaults",
        )

    try:
        if request.internal_task_default is True and not default_reasoning_effort_provided:
            mutation = deps.config_service.write_internal_task_model_selection(
                config_path=config_path,
                effective_config=deps.config_result.effective_config,
                provider_name=provider_name,
                selected_model=selected_model,
            )
        else:
            mutation = deps.config_service.write_model_selection(
                config_path=config_path,
                effective_config=deps.config_result.effective_config,
                provider_name=provider_name,
                selected_model=selected_model,
                default_reasoning_effort=default_reasoning_effort,
                default_reasoning_effort_provided=default_reasoning_effort_provided,
                internal_task_default=bool(request.internal_task_default),
            )
    except ConfigMutationError as exc:
        raise GatewayAdapterError(_config_mutation_status(exc), exc.code, exc.message) from exc

    try:
        reload_view = await admin_reload(deps, scope="config")
    except Exception:
        mutation.rollback()
        try:
            await admin_reload(deps, scope="config")
        except Exception:
            pass
        raise

    refreshed_model = next((model for model in list_models(deps) if model.name == provider_name), None)
    if refreshed_model is None:
        raise GatewayAdapterError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "model_reload_failed",
            f"model provider '{provider_name}' disappeared after config reload",
        )
    return ModelSelectionUpdateView(
        name=provider_name,
        selected_model=selected_model,
        default_reasoning_effort=refreshed_model.default_reasoning_effort,
        internal_task_default=refreshed_model.internal_task_default,
        config_path=str(config_path),
        config_fingerprint=str(reload_view.get("config_fingerprint") or deps.config_result.fingerprint),
        model=refreshed_model,
    )


async def test_model_provider(
    deps: AppRuntimeDeps,
    name: str,
    request: ModelHealthCheckRequest,
) -> ModelHealthCheckView:
    provider_name = name.strip()
    if not provider_name:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_model_provider", "model provider name is required")
    model_config = deps.config_result.effective_config.models.get(provider_name)
    if model_config is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "model_not_found", f"model provider '{provider_name}' was not found")

    selected_model = (request.model_name or model_config.effective_model_name()).strip()
    available = model_selection_options(model_config)
    if available and selected_model not in available:
        raise GatewayAdapterError(
            status.HTTP_400_BAD_REQUEST,
            "invalid_model_selection",
            f"model '{selected_model}' is not configured for provider '{provider_name}'",
        )

    checked_at = utc_now()
    started = checked_at
    try:
        probe_config = model_config.model_copy(update={"model_name": selected_model, "selected_model": selected_model})
        model = create_chat_model(probe_config, thinking_enabled=False)
        response = model.invoke(
            "Reply with exactly OK.",
            config={
                "callbacks": [],
                "tags": ["anvil_internal_model_health"],
                "metadata": {"anvil_internal": True, "anvil_internal_kind": "model_health", "subsystem": request.subsystem},
            },
        )
        content = strip_inline_thinking_tags(str(getattr(response, "content", "") or "")).strip()
        ok = bool(content)
        message = content[:120] if content else "empty response"
    except Exception as exc:
        ok = False
        message = f"{exc.__class__.__name__}: {str(exc)[:180]}"
    finished = utc_now()
    return ModelHealthCheckView(
        name=provider_name,
        model_name=selected_model,
        subsystem=request.subsystem,
        ok=ok,
        status="ready" if ok else "error",
        message=message,
        checked_at=finished.isoformat(),
        latency_ms=max(int((finished - started).total_seconds() * 1000), 0),
        config_fingerprint=deps.config_result.fingerprint,
    )


def _config_overview_skill_source_scope(manifest: object, effective_config: object) -> str:
    source_root_value = getattr(manifest, "source_root", None)
    if source_root_value is None:
        return "unknown"
    source_root = Path(str(source_root_value)).expanduser().resolve()
    repo_skill_root = default_repo_skill_root().resolve()
    installed_skill_root = default_installed_skill_root().resolve()
    skills_config = getattr(effective_config, "skills_config", None)
    external_roots = _resolved_configured_skill_roots(getattr(skills_config, "external_dirs", ()))

    if source_root == installed_skill_root:
        return "home"
    if source_root == repo_skill_root:
        return "bundled_source"
    if source_root in external_roots:
        return "external"
    return "plugin"


def _resolved_configured_skill_roots(values: object) -> set[Path]:
    roots: set[Path] = set()
    if not isinstance(values, (list, tuple, set)):
        return roots
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        roots.add(Path(value).expanduser().resolve())
    return roots


def get_config_overview(deps: AppRuntimeDeps) -> ConfigOverviewView:
    runtime_view_cache = getattr(deps, "get_runtime_view_cache", None)
    if callable(runtime_view_cache):
        return runtime_view_cache("config_overview", lambda: _build_config_overview(deps), ttl_seconds=2.0)
    return _build_config_overview(deps)


def _build_config_overview(deps: AppRuntimeDeps) -> ConfigOverviewView:
    effective_config = deps.config_result.effective_config
    models = effective_config.models.values()
    model_total = 0
    model_available = 0
    for model in models:
        model_total += 1
        if _model_api_key_available(model):
            model_available += 1

    tool_total = 0
    visible_tool_count = 0
    deferred_tool_count = 0
    try:
        assembly = _build_capability_preview(deps)
        catalog_entries = deps.capability_assembly_service.capability_catalog_service.list_entries(
            registry=assembly.registry,
            bundle=assembly.bundle,
        )
        tool_total = len(catalog_entries)
        visible_tool_count = sum(
            1
            for item in catalog_entries
            if (item.visibility.value if hasattr(item.visibility, "value") else str(item.visibility)) == "visible"
        )
        deferred_tool_count = sum(
            1
            for item in catalog_entries
            if item.deferred
            and (item.visibility.value if hasattr(item.visibility, "value") else str(item.visibility)) != "visible"
        )
    except Exception:
        tool_total = 0

    skills_result = deps.skills_service.discover(
        config=effective_config,
        fingerprint=deps.config_result.fingerprint,
    )
    skill_source_counts: dict[str, int] = {}
    enabled_skill_source_counts: dict[str, int] = {}
    enabled_skill_ids = set(skills_result.enabled_ids)
    for manifest in skills_result.all_manifests:
        enabled = manifest.skill_id in enabled_skill_ids
        source_scope = _config_overview_skill_source_scope(manifest, effective_config)
        skill_source_counts[source_scope] = skill_source_counts.get(source_scope, 0) + 1
        if enabled:
            enabled_skill_source_counts[source_scope] = enabled_skill_source_counts.get(source_scope, 0) + 1

    extension_result = deps.extensions_service.discover(
        config=effective_config,
        fingerprint=deps.config_result.fingerprint,
        live=False,
    )
    mcp_items = [
        extension_status_to_view(item, deps=deps)
        for item in extension_result.materializations
        if item.source_kind == "mcp"
    ]
    plugin_items = list_plugins(deps)
    task_items = deps.scheduled_task_service.list_tasks(include_disabled=True)

    memory_status = "unknown"
    memory_quality = None
    memory_issue_count = None
    memory_store_count = 0
    try:
        memory_report = deps.memory_manager.health_report()
        memory_status = str(memory_report.status)
        memory_quality = float(memory_report.quality_score)
        memory_issue_count = len(memory_report.issues)
        memory_store_count = len(memory_report.stores)
    except Exception:
        try:
            memory_overview = deps.memory_manager.overview()
            memory_store_count = int(memory_overview.store_count)
        except Exception:
            memory_store_count = 0

    return ConfigOverviewView(
        status="ok",
        config_fingerprint=deps.config_result.fingerprint,
        models=ConfigOverviewMetricView(
            total=model_total,
            available=model_available,
            disabled=max(model_total - model_available, 0),
        ),
        tools=ConfigOverviewMetricView(
            total=tool_total,
            enabled=visible_tool_count,
            ready=visible_tool_count,
            disabled=deferred_tool_count,
        ),
        skills=ConfigOverviewMetricView(
            total=len(skills_result.all_manifests),
            enabled=len(enabled_skill_ids),
            source_counts=skill_source_counts,
            enabled_source_counts=enabled_skill_source_counts,
        ),
        memory=ConfigOverviewMetricView(
            total=memory_store_count,
            enabled=memory_store_count,
            status=memory_status,
            quality_score=memory_quality,
            issue_count=memory_issue_count,
        ),
        mcp=ConfigOverviewMetricView(
            total=len(mcp_items),
            enabled=sum(1 for item in mcp_items if item.enabled),
            ready=sum(1 for item in mcp_items if item.ready or item.connected or item.status == "ready"),
            disabled=sum(1 for item in mcp_items if not item.enabled),
            issue_count=sum(1 for item in mcp_items if item.auth_required or str(item.status).lower() in {"error", "failed"}),
        ),
        plugins=ConfigOverviewMetricView(
            total=len(plugin_items),
            enabled=sum(1 for item in plugin_items if item.enabled),
        ),
        scheduled=ConfigOverviewMetricView(
            total=len(task_items),
            enabled=sum(1 for item in task_items if task.enabled),
            ready=sum(1 for task in task_items if task.status not in {"running", "failed"}),
        ),
    )


def _config_mutation_status(error: ConfigMutationError) -> int:
    if error.code == "model_not_found":
        return status.HTTP_404_NOT_FOUND
    if error.code in {"model_not_writable", "config_file_invalid"}:
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _normalize_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"", "default", "none"}:
        return None
    if normalized not in {"minimal", "low", "medium", "high", "xhigh"}:
        raise GatewayAdapterError(
            status.HTTP_400_BAD_REQUEST,
            "invalid_reasoning_effort",
            f"reasoning effort '{value}' is not supported",
        )
    return normalized


def _model_provider_preset_to_view(name: str, preset: dict[str, object]) -> ModelProviderPresetView:
    default_model = _first_string(preset.get("default_model")) or _first_model(preset.get("model")) or _first_model(preset.get("model_catalog"))
    return ModelProviderPresetView(
        provider=name,
        display_name=str(preset.get("display_name") or name),
        description=_first_string(preset.get("description")),
        base_url=_first_string(preset.get("base_url") or preset.get("api_base")),
        api_key_env=_default_provider_api_key_env(name, preset),
        provider_kind=_first_string(preset.get("provider_kind")),
        use=_first_string(preset.get("use")),
        model_catalog=_string_list(preset.get("model") if isinstance(preset.get("model"), list) else preset.get("model_catalog")),
        default_model=default_model,
        context_window_tokens=_optional_int(preset.get("context_window_tokens")),
        auto_compact_threshold_tokens=_optional_int(preset.get("auto_compact_threshold_tokens")),
        default_reasoning_effort=_first_string(preset.get("default_reasoning_effort")),
        supports_tool_calling=bool(preset.get("supports_tool_calling", True)),
        supports_thinking=bool(preset.get("supports_thinking", False)),
        supports_reasoning_effort=bool(preset.get("supports_reasoning_effort", False)),
        supports_vision=bool(preset.get("supports_vision", False)),
        supports_image_generation=bool(preset.get("supports_image_generation", False)),
        defaults=redact_sensitive_config(preset) if isinstance(preset, dict) else {},
    )


def _build_model_provider_entry(
    request: ModelProviderUpsertRequest,
    *,
    provider: str,
    name: str,
    existing: dict[str, object] | None = None,
) -> tuple[dict[str, object], str]:
    preset = llm_provider_preset(provider)
    entry: dict[str, object] = {
        key: value
        for key, value in preset.items()
        if key not in {"display_name", "description"} and value is not None
    }
    if existing:
        entry.update({str(key): value for key, value in existing.items()})
    entry["provider"] = provider
    entry["display_name"] = request.display_name or str(entry.get("display_name") or preset.get("display_name") or name)
    if request.description is not None:
        entry["description"] = request.description
    elif existing and existing.get("description") is not None:
        entry["description"] = existing["description"]
    elif preset.get("description") is not None:
        entry["description"] = preset["description"]

    existing_api_key_env = _existing_api_key_env(existing)
    api_key_env = (request.api_key_env or existing_api_key_env or _default_provider_api_key_env(provider, preset)).strip()
    if not api_key_env:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_model_provider", "api_key_env is required")
    entry["api_key_env"] = api_key_env
    entry["api_key"] = f"${{{api_key_env}}}"

    base_url = _clean_string(request.base_url)
    if base_url:
        entry["base_url"] = base_url
        entry["api_base"] = base_url

    models = _dedupe_strings(request.models)
    default_model = _clean_string(request.default_model)
    if default_model and default_model not in models:
        models.insert(0, default_model)
    if not models:
        preset_models = _string_list(preset.get("model") if isinstance(preset.get("model"), list) else preset.get("model_catalog"))
        if preset_models:
            models = preset_models
    if not default_model:
        default_model = models[0] if models else _first_model(preset.get("model")) or _first_model(preset.get("model_catalog"))
    if not default_model:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_model_selection", "at least one model is required")
    if default_model not in models:
        models.insert(0, default_model)
    entry["model"] = models
    entry["model_catalog"] = models
    entry["default_model"] = default_model
    selected_model = _first_string(entry.get("selected_model")) or _first_string(entry.get("model_name")) or default_model
    if selected_model not in models:
        selected_model = default_model
    entry["selected_model"] = selected_model
    entry["model_name"] = selected_model

    optional_updates: dict[str, object | None] = {
        "default_reasoning_effort": _normalize_reasoning_effort(request.default_reasoning_effort),
        "context_window_tokens": request.context_window_tokens,
        "auto_compact_threshold_tokens": request.auto_compact_threshold_tokens,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "timeout": request.timeout,
        "request_timeout": request.request_timeout,
        "default_request_timeout": request.default_request_timeout,
        "max_retries": request.max_retries,
        "use_responses_api": request.use_responses_api,
        "output_version": request.output_version,
        "default_headers": request.default_headers,
        "extra_body": request.extra_body,
        "provider_settings": request.provider_settings,
        "when_thinking_enabled": request.when_thinking_enabled,
        "when_thinking_disabled": request.when_thinking_disabled,
        "thinking": request.thinking,
        "image_generation": request.image_generation,
        "supports_tool_calling": request.supports_tool_calling,
        "supports_thinking": request.supports_thinking,
        "supports_reasoning_effort": request.supports_reasoning_effort,
        "supports_vision": request.supports_vision,
        "supports_image_generation": request.supports_image_generation,
    }
    for key, value in optional_updates.items():
        if value is not None:
            entry[key] = value
    for key, value in request.extra_fields.items():
        if not str(key).strip():
            continue
        entry[str(key)] = value
    return entry, api_key_env


def _existing_api_key_env(existing: dict[str, object] | None) -> str | None:
    if not existing:
        return None
    raw_env = existing.get("api_key_env")
    if isinstance(raw_env, str) and raw_env.strip():
        if is_env_ref(raw_env.strip()):
            return env_ref_name(raw_env.strip())
        return raw_env.strip()
    raw_key = existing.get("api_key")
    if isinstance(raw_key, str) and is_env_ref(raw_key):
        return env_ref_name(raw_key)
    return None


def _minimal_home_config_payload() -> dict[str, object]:
    return {
        "anvil": {"profile": resolve_anvil_profile_name()},
        "llm": {"default": None, "providers": {}},
        "skills_config": {"enabled": True, "watch_enabled": True, "external_dirs": []},
        "mcp_servers": {},
    }


def _restore_config_files(config_path: Path, original_text: str, dotenv_path: Path, original_dotenv: str | None) -> None:
    if original_text:
        config_path.write_text(original_text, encoding="utf-8")
    elif config_path.exists():
        config_path.unlink()
    if original_dotenv is None:
        if dotenv_path.exists():
            dotenv_path.unlink()
    else:
        dotenv_path.write_text(original_dotenv, encoding="utf-8")


def _upsert_dotenv_value(dotenv_path: Path, key: str, value: str) -> None:
    dotenv_path.parent.mkdir(parents=True, exist_ok=True)
    lines = dotenv_path.read_text(encoding="utf-8").splitlines() if dotenv_path.exists() else []
    rendered = f"{key}={_dotenv_quote(value)}"
    replaced = False
    next_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                next_lines.append(rendered)
                replaced = True
                continue
        next_lines.append(line)
    if not replaced:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append(rendered)
    dotenv_path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def _dotenv_quote(value: str) -> str:
    if not value or any(char.isspace() for char in value) or any(char in value for char in ['"', "'", "#"]):
        return json.dumps(value, ensure_ascii=False)
    return value


def _normalize_provider_name(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_")


def _normalize_model_provider_name(value: str | None) -> str:
    normalized = _normalize_provider_name(value)
    return "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in normalized).strip("_.-")


def _default_provider_api_key_env(provider: str, preset: dict[str, object]) -> str:
    api_key = preset.get("api_key")
    if isinstance(api_key, str) and is_env_ref(api_key):
        return env_ref_name(api_key)
    provider_settings = preset.get("provider_settings")
    if isinstance(provider_settings, dict):
        for value in provider_settings.values():
            if isinstance(value, str) and is_env_ref(value):
                return env_ref_name(value)
    return {
        "openai": "OPENAI_API_KEY",
        "openai_responses": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "minimax_cn": "MINIMAX_API_KEY",
        "minimax_global": "MINIMAX_API_KEY",
        "mimo": "MIMO_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "gemini_native": "GEMINI_API_KEY",
        "kimi": "MOONSHOT_API_KEY",
        "qwen": "QWEN_API_KEY",
    }.get(provider, f"{provider.upper()}_API_KEY")


def _clean_string(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _first_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _first_model(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            if str(item).strip():
                return str(item).strip()
    return None


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return _dedupe_strings(value)
    return []


def _dedupe_strings(values: object) -> list[str]:
    result: list[str] = []
    if not isinstance(values, list):
        return result
    for item in values:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _model_api_key_available(model) -> bool:
    if model.api_key and is_env_ref(str(model.api_key)):
        return bool(os.getenv(env_ref_name(str(model.api_key))))
    if model.api_key_env:
        return bool(os.getenv(model.api_key_env))
    return True


def _model_diagnostics(model) -> list[str]:
    if _model_api_key_available(model):
        return []
    if model.api_key and is_env_ref(str(model.api_key)):
        return [f"missing environment variable {env_ref_name(str(model.api_key))}"]
    if model.api_key_env:
        return [f"missing environment variable {model.api_key_env}"]
    return []
def list_skills(deps: AppRuntimeDeps) -> list[SkillListItemView]:
    result = deps.skills_service.discover(
        config=deps.config_result.effective_config,
        fingerprint=deps.config_result.fingerprint,
    )
    enabled_ids = set(result.enabled_ids)
    return [
        skill_manifest_to_list_item(
            manifest,
            enabled=manifest.skill_id in enabled_ids,
        )
        for manifest in result.all_manifests
    ]


def get_skill_view(deps: AppRuntimeDeps, skill_id: str) -> SkillView:
    result = deps.skills_service.discover(
        config=deps.config_result.effective_config,
        fingerprint=deps.config_result.fingerprint,
    )
    manifest = next((item for item in result.all_manifests if item.skill_id == skill_id), None)
    if manifest is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "skill_not_found", f"skill '{skill_id}' was not found")
    return skill_manifest_to_view(
        manifest,
        enabled=skill_id in set(result.enabled_ids),
        package=_skill_package_payload(result, skill_id),
    )


def get_skill_content_view(deps: AppRuntimeDeps, skill_id: str) -> SkillContentView:
    try:
        payload = deps.skills_service.get_skill_content(
            config=deps.config_result.effective_config,
            fingerprint=deps.config_result.fingerprint,
            skill_id=skill_id,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "skill_not_found", str(exc)) from exc
    return SkillContentView.model_validate(payload.model_dump(mode="json"))


def list_skill_files_view(deps: AppRuntimeDeps, skill_id: str) -> SkillFileIndexView:
    try:
        payload = deps.skills_service.list_skill_files(
            config=deps.config_result.effective_config,
            fingerprint=deps.config_result.fingerprint,
            skill_id=skill_id,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "skill_not_found", str(exc)) from exc
    return SkillFileIndexView.model_validate(payload.model_dump(mode="json"))


def read_skill_file_view(
    deps: AppRuntimeDeps,
    skill_id: str,
    *,
    relative_path: str,
    max_bytes: int = 64_000,
) -> SkillFileReadView:
    try:
        payload = deps.skills_service.read_skill_file(
            config=deps.config_result.effective_config,
            fingerprint=deps.config_result.fingerprint,
            skill_id=skill_id,
            relative_path=relative_path,
            max_bytes=max_bytes,
        )
    except ValueError as exc:
        detail = str(exc)
        error = "skill_file_not_found" if "skill file" in detail else "invalid_skill_file_request"
        status_code = status.HTTP_404_NOT_FOUND if error == "skill_file_not_found" or "unknown skill" in detail else status.HTTP_400_BAD_REQUEST
        if "unknown skill" in detail:
            error = "skill_not_found"
        raise GatewayAdapterError(status_code, error, detail) from exc
    return SkillFileReadView.model_validate(payload.model_dump(mode="json"))


async def reload_skills(deps: AppRuntimeDeps) -> dict[str, object]:
    deps.skills_service.cache.invalidate()
    result = deps.skills_service.discover(
        config=deps.config_result.effective_config,
        fingerprint=deps.config_result.fingerprint,
    )
    await deps.system_event_bus.publish(
        "skills_changed",
        {
            "skills_count": len(result.enabled_ids),
            "skills_fingerprint": deps.config_result.fingerprint,
        },
    )
    return {
        "reloaded": True,
        "skills_count": len(result.enabled_ids),
    }


async def manage_skill(deps: AppRuntimeDeps, body: SkillManageRequest) -> dict[str, object]:
    normalized_action = body.action.strip().lower()
    if normalized_action not in {"enable", "disable", "uninstall"}:
        raise GatewayAdapterError(
            status.HTTP_400_BAD_REQUEST,
            "unsupported_skill_management_action",
            f"unsupported skill management action: {body.action}",
        )
    try:
        payload = deps.skills_service.manage(
            config=deps.config_result.effective_config,
            action=normalized_action,
            skill_id=body.skill_id,
        )
    except ValueError as exc:
        detail = str(exc)
        error = "invalid_skill_management_request"
        status_code = status.HTTP_400_BAD_REQUEST
        if "unknown skill" in detail or "not installed" in detail or "no history found" in detail:
            error = "skill_not_found"
            status_code = status.HTTP_404_NOT_FOUND
        raise GatewayAdapterError(status_code, error, detail) from exc

    await deps.system_event_bus.publish(
        "skills_changed",
        {
            "action": normalized_action,
            "skill_id": body.skill_id,
            "skills_fingerprint": deps.config_result.fingerprint,
        },
    )
    return payload


async def manage_skill_curator(deps: AppRuntimeDeps, body: SkillCuratorRequest) -> dict[str, object]:
    try:
        payload = deps.skills_service.manage_curator(
            config=deps.config_result.effective_config,
            action=body.action,
            skill_id=body.skill_id,
            title=body.title,
            summary=body.summary,
            body=body.body,
            rationale=body.rationale,
            tags=body.tags,
            allowed_tools=body.allowed_tools,
            file_path=body.file_path,
            content=body.content,
            old_text=body.old_text,
            new_text=body.new_text,
            absorbed_into=body.absorbed_into,
            revision=body.revision,
            outcome=body.outcome,
            feedback_source=body.feedback_source,
            confidence=body.confidence,
            trigger=body.trigger,
            steps=body.steps,
            expected_outcome=body.expected_outcome,
            evidence_refs=body.evidence_refs,
            source_ref=body.source_ref,
            procedure_id=body.procedure_id,
            dry_run=body.dry_run,
            force=body.force,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_skill_curator_request", str(exc)) from exc

    await deps.system_event_bus.publish(
        "skills_changed",
        {
            "action": body.action.strip().lower(),
            "skill_id": body.skill_id,
            "curator": True,
            "skills_fingerprint": deps.config_result.fingerprint,
        },
    )
    return payload


def get_skill_curator_automation(deps: AppRuntimeDeps) -> SkillCuratorAutomationStatusResponse:
    return SkillCuratorAutomationStatusResponse.model_validate(
        deps.skills_service.curator_automation_status(config=deps.config_result.effective_config)
    )


def get_self_upgrade_health(
    deps: AppRuntimeDeps,
    *,
    candidate_audit_limit: int = 50,
) -> SelfUpgradeHealthResponse:
    report = SelfUpgradeHealthService().report(
        config=deps.config_result.effective_config,
        memory_manager=deps.memory_manager,
        skills_service=deps.skills_service,
        checkpointer=deps.checkpointer,
        trajectory_export_root=resolve_gateway_trajectory_export_root(deps),
        fingerprint=deps.config_result.fingerprint,
        candidate_audit_limit=candidate_audit_limit,
    )
    return self_upgrade_health_to_view(report)


async def run_skill_curator_automation(deps: AppRuntimeDeps, body: SkillCuratorAutomationRequest) -> SkillCuratorAutomationRunResponse:
    if hasattr(deps, "run_skill_curator_automation_sync"):
        result = deps.run_skill_curator_automation_sync(force_run=body.force_run)
    else:
        result = deps.skills_service.run_curator_automation_if_due(
            config=deps.config_result.effective_config,
            force_run=body.force_run,
        )
    payload = {
        "ran": result.ran,
        "reason": result.reason,
        "next_run_at": result.next_run_at,
        "report": result.report,
    }
    if result.ran:
        if hasattr(deps, "_publish_skill_curator_automation"):
            await deps._publish_skill_curator_automation(result)
        else:
            report = result.report or {}
            recommendations = report.get("recommendations") if isinstance(report.get("recommendations"), list) else []
            await deps.system_event_bus.publish(
                "skills_changed",
                {
                    "action": "curator_automation",
                    "curator": True,
                    "skills_fingerprint": deps.config_result.fingerprint,
                    "run_id": report.get("run_id"),
                    "counts": report.get("counts"),
                    "recommendation_count": len(recommendations),
                    "recommendations": recommendations[:5],
                    "next_run_at": result.next_run_at,
                },
            )
    return SkillCuratorAutomationRunResponse.model_validate(payload)


async def run_skill_curator_maintenance(deps: AppRuntimeDeps, body: SkillCuratorMaintenanceRequest) -> dict[str, object]:
    payload = deps.skills_service.run_curator_maintenance(
        config=deps.config_result.effective_config,
        dry_run=body.dry_run,
        force=body.force,
        source=body.source,
    )
    await deps.system_event_bus.publish(
        "skills_changed",
        {
            "action": "curator_maintenance",
            "curator": True,
            "skills_fingerprint": deps.config_result.fingerprint,
            "run_id": payload.get("run_id"),
            "status": payload.get("status"),
            "dry_run": payload.get("dry_run"),
            "counts": payload.get("counts"),
            "executed": payload.get("actions_executed"),
            "skipped": payload.get("skipped_actions"),
        },
    )
    return payload


def list_tools_catalog(
    deps: AppRuntimeDeps,
    *,
    query: str | None = None,
    source_kind: str | None = None,
    capability_group: str | None = None,
) -> list[ToolCatalogEntryView]:
    assembly = _build_capability_preview(deps)
    entries = deps.capability_assembly_service.capability_catalog_service.list_entries(
        registry=assembly.registry,
        bundle=assembly.bundle,
        query=query,
        source_kind=source_kind,
        capability_group=capability_group,
    )
    return [tool_catalog_entry_to_view(entry) for entry in entries]


def get_tool_catalog_entry(deps: AppRuntimeDeps, name_or_capability_id: str) -> ToolCatalogEntryView:
    assembly = _build_capability_preview(deps)
    entry = deps.capability_assembly_service.capability_catalog_service.get_entry(
        registry=assembly.registry,
        bundle=assembly.bundle,
        name_or_capability_id=name_or_capability_id,
    )
    if entry is None:
        raise GatewayAdapterError(
            status.HTTP_404_NOT_FOUND,
            "capability_not_found",
            f"capability '{name_or_capability_id}' was not found",
        )
    return tool_catalog_entry_to_view(entry)


def list_plugins(deps: AppRuntimeDeps) -> list[PluginView]:
    views: list[PluginView] = []
    for item in deps.extensions_service.list_plugins(config=deps.config_result.effective_config):
        views.append(
            PluginView(
                plugin_id=str(item.get("plugin_id")),
                enabled=bool(item.get("enabled", False)),
                source_path=str(item.get("source_path")) if item.get("source_path") is not None else None,
                skill_roots=[str(root) for root in item.get("skill_roots", [])],
                tool_count=int(item.get("tool_count", 0)),
                tool_names=[str(name) for name in item.get("tool_names", []) if str(name).strip()],
                resources=[
                    capability_resource_to_view(
                        {
                            **resource,
                            "resource_id": resource.get("resource_id") or resource.get("name"),
                            "title": resource.get("title") or resource.get("resource_id") or resource.get("name"),
                            "server_id": resource.get("server_id", item.get("plugin_id")),
                            "metadata": {
                                "discovery_source": item.get("discovery_source", "plugin_config"),
                                **dict(resource.get("metadata") or {}),
                            },
                        }
                    )
                    for resource in item.get("resources", [])
                    if str(resource.get("resource_id") or resource.get("name") or "").strip()
                ],
                prompts=[
                    capability_prompt_to_view(
                        {
                            **prompt,
                            "prompt_id": prompt.get("prompt_id") or prompt.get("name"),
                            "title": prompt.get("title") or prompt.get("prompt_id") or prompt.get("name"),
                            "server_id": prompt.get("server_id", item.get("plugin_id")),
                            "metadata": {
                                "discovery_source": item.get("discovery_source", "plugin_config"),
                                **dict(prompt.get("metadata") or {}),
                            },
                        }
                    )
                    for prompt in item.get("prompts", [])
                    if str(prompt.get("prompt_id") or prompt.get("name") or "").strip()
                ],
                memory_providers=[dict(provider) for provider in item.get("memory_providers", []) if isinstance(provider, dict)],
                memory_provider_count=int(item.get("memory_provider_count", 0)),
                catalog_metadata=dict(item.get("catalog_metadata", {})),
                discovery_source=str(item.get("discovery_source") or "plugin_config"),
            )
        )
    return views


def list_plugin_catalog(deps: AppRuntimeDeps) -> list[PluginCatalogEntryView]:
    views: list[PluginCatalogEntryView] = []
    for item in deps.extensions_service.list_plugin_catalog(
        repo_root=_runtime_repo_root(),
        config=deps.config_result.effective_config,
    ):
        views.append(
            PluginCatalogEntryView(
                plugin_id=str(item.get("plugin_id") or ""),
                name=str(item.get("name") or item.get("plugin_id") or ""),
                description=str(item.get("description") or ""),
                source=str(item.get("source") or ""),
                source_kind=str(item.get("source_kind") or "unknown"),
                version=str(item.get("version")) if item.get("version") is not None else None,
                author=str(item.get("author")) if item.get("author") is not None else None,
                homepage=str(item.get("homepage")) if item.get("homepage") is not None else None,
                tags=[str(tag) for tag in item.get("tags", []) if str(tag).strip()],
                trust_level=str(item.get("trust_level")) if item.get("trust_level") is not None else None,
                registry_id=str(item.get("registry_id")) if item.get("registry_id") is not None else None,
                registry_name=str(item.get("registry_name")) if item.get("registry_name") is not None else None,
                registry_source=str(item.get("registry_source")) if item.get("registry_source") is not None else None,
                registry_kind=str(item.get("registry_kind")) if item.get("registry_kind") is not None else None,
                installed=bool(item.get("installed", False)),
                enabled=bool(item.get("enabled", False)),
                installable=bool(item.get("installable", True)),
                skill_count=int(item.get("skill_count", 0)),
                tool_count=int(item.get("tool_count", 0)),
                mcp_server_count=int(item.get("mcp_server_count", 0)),
                resource_count=int(item.get("resource_count", 0)),
                prompt_count=int(item.get("prompt_count", 0)),
                memory_provider_count=int(item.get("memory_provider_count", 0)),
                skill_roots=[str(root) for root in item.get("skill_roots", []) if str(root).strip()],
                tool_names=[str(name) for name in item.get("tool_names", []) if str(name).strip()],
                mcp_servers=[str(server) for server in item.get("mcp_servers", []) if str(server).strip()],
                memory_providers=[str(provider) for provider in item.get("memory_providers", []) if str(provider).strip()],
                permissions=[str(permission) for permission in item.get("permissions", []) if str(permission).strip()],
                catalog_metadata=dict(item.get("catalog_metadata", {})),
                discovery_source=str(item.get("discovery_source") or "catalog"),
            )
        )
    return views


def plugin_registry_to_view(item: dict[str, object]) -> PluginRegistryView:
    return PluginRegistryView(
        registry_id=str(item.get("registry_id") or ""),
        name=str(item.get("name") or item.get("registry_id") or ""),
        source=str(item.get("source") or ""),
        source_kind=str(item.get("source_kind") or "unknown"),
        enabled=bool(item.get("enabled", True)),
        readonly=bool(item.get("readonly", False)),
        trust_level=str(item.get("trust_level")) if item.get("trust_level") is not None else None,
        entry_count=int(item.get("entry_count", 0)),
        cached=bool(item.get("cached", False)),
        cache_path=str(item.get("cache_path")) if item.get("cache_path") is not None else None,
        error=str(item.get("error")) if item.get("error") is not None else None,
        diagnostics=[str(value) for value in item.get("diagnostics", []) if str(value).strip()],
        config_path=str(item.get("config_path")) if item.get("config_path") is not None else None,
        last_checked_at=item.get("last_checked_at") if isinstance(item.get("last_checked_at"), datetime) else None,
    )


def list_plugin_registries(deps: AppRuntimeDeps) -> list[PluginRegistryView]:
    return [
        plugin_registry_to_view(item)
        for item in deps.extensions_service.list_plugin_registries(repo_root=_runtime_repo_root())
    ]


async def upsert_plugin_registry(deps: AppRuntimeDeps, body: PluginRegistryUpsertRequest) -> PluginRegistryUpsertView:
    try:
        result = deps.extensions_service.upsert_plugin_registry(
            repo_root=_runtime_repo_root(),
            source=body.source,
            registry_id=body.registry_id,
            name=body.name,
            enabled=body.enabled,
            trust_level=body.trust_level,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "plugin_registry_failed", str(exc)) from exc
    registry = plugin_registry_to_view(result)
    return PluginRegistryUpsertView(
        status="updated",
        config_path=str(result["config_path"]),
        registry=registry,
        registries=list_plugin_registries(deps),
        catalog=list_plugin_catalog(deps),
    )


async def refresh_plugin_registry(deps: AppRuntimeDeps, registry_id: str) -> PluginRegistryUpsertView:
    try:
        result = deps.extensions_service.refresh_plugin_registry(
            repo_root=_runtime_repo_root(),
            registry_id=registry_id,
        )
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "plugin_registry_not_found", f"plugin registry '{registry_id}' was not found") from exc
    registry = plugin_registry_to_view(result)
    return PluginRegistryUpsertView(
        status="refreshed",
        config_path=str(result.get("config_path") or ""),
        registry=registry,
        registries=list_plugin_registries(deps),
        catalog=list_plugin_catalog(deps),
    )


async def delete_plugin_registry(deps: AppRuntimeDeps, registry_id: str) -> PluginRegistryDeleteView:
    try:
        result = deps.extensions_service.delete_plugin_registry(
            repo_root=_runtime_repo_root(),
            registry_id=registry_id,
        )
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "plugin_registry_not_found", f"plugin registry '{registry_id}' was not found") from exc
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "plugin_registry_failed", str(exc)) from exc
    return PluginRegistryDeleteView(
        status="deleted",
        registry_id=str(result["registry_id"]),
        deleted=bool(result["deleted"]),
        config_path=str(result["config_path"]),
        registries=list_plugin_registries(deps),
        catalog=list_plugin_catalog(deps),
    )


async def install_plugin(deps: AppRuntimeDeps, body: PluginInstallRequest) -> PluginInstallView:
    from anvil.config.loader import build_plugin_config_layer_from_file

    try:
        result = deps.extensions_service.install_plugin(
            repo_root=_runtime_repo_root(),
            source=body.source,
            plugin_id=body.plugin_id,
            enable=body.enable,
            force=body.force,
        )
        plugin_config_path = default_anvil_config_dir(_runtime_repo_root()) / "plugins.json"
        _replace_runtime_config_layer(deps, build_plugin_config_layer_from_file(plugin_config_path))
        reload_result = await admin_reload(deps, scope="all")
        deps.memory_manager.reload_providers(effective_config=deps.effective_config)
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "plugin_install_failed", str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise GatewayAdapterError(status.HTTP_409_CONFLICT, "plugin_install_failed", str(exc)) from exc
    return PluginInstallView(
        plugin_id=str(result["plugin_id"]),
        installed=bool(result["installed"]),
        enabled=bool(result["enabled"]),
        source=str(result["source"]),
        path=str(result["path"]),
        config_path=str(result["config_path"]),
        skill_roots=[str(item) for item in result.get("skill_roots", [])],
        tool_count=int(result.get("tool_count", 0)),
        bundled_mcp_servers=[str(item) for item in result.get("bundled_mcp_servers", [])],
        reload=reload_result,
        plugins=list_plugins(deps),
    )


async def upsert_mcp_servers(deps: AppRuntimeDeps, body: McpServerBatchUpsertRequest) -> McpServerBatchUpsertView:
    try:
        incoming_servers = parse_mcp_servers_config_text(body.config_text)
        normalized = normalize_loaded_config({"mcpServers": incoming_servers})
        deps.config_service.resolve(
            [
                ConfigLayer(
                    name="mcp_config_validation",
                    kind=ConfigLayerKind.USER,
                    data=normalized,
                    source="request",
                )
            ]
        )
        config_path = _writable_mcp_config_path()
        upsert_mcp_servers_in_config_file(config_path, incoming_servers)
        _replace_runtime_config_layer(deps, build_mcp_config_layer_from_file(config_path))
        reload_result = await admin_reload(deps, scope="all")
    except json.JSONDecodeError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_mcp_config", f"invalid JSON: {exc}") from exc
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_mcp_config", str(exc)) from exc
    servers = await list_mcp_servers(deps)
    return McpServerBatchUpsertView(
        status="updated",
        config_path=str(config_path),
        upserted=sorted(str(name) for name in incoming_servers),
        servers=servers,
        reload=reload_result,
    )


async def delete_mcp_server(deps: AppRuntimeDeps, server_id: str) -> McpServerDeleteView:
    normalized_server_id = server_id.strip()
    if not normalized_server_id:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_mcp_server", "MCP server id cannot be empty")
    try:
        config_path = _writable_mcp_config_path()
        delete_mcp_server_from_config_file(config_path, normalized_server_id)
        _replace_runtime_config_layer(deps, build_mcp_config_layer_from_file(config_path))
        reload_result = await admin_reload(deps, scope="all")
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "mcp_server_not_found", f"MCP server '{server_id}' was not found") from exc
    except json.JSONDecodeError as exc:
        raise GatewayAdapterError(status.HTTP_409_CONFLICT, "invalid_mcp_config", f"invalid existing MCP config: {exc}") from exc
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_409_CONFLICT, "invalid_mcp_config", str(exc)) from exc
    servers = await list_mcp_servers(deps)
    return McpServerDeleteView(
        status="deleted",
        server_id=normalized_server_id,
        deleted=True,
        config_path=str(config_path),
        servers=servers,
        reload=reload_result,
    )


async def admin_reload(deps: AppRuntimeDeps, *, scope: str) -> dict[str, object]:
    scope = scope.lower()
    if scope not in {"config", "skills", "mcp", "all"}:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_reload_scope", f"unsupported reload scope '{scope}'")
    payload: dict[str, object] = {"scope": scope, "reloaded": True}
    if scope in {"config", "mcp", "all"}:
        try:
            config_result = deps.config_coordinator.reload()
            deps.config_layers = deps.config_coordinator.config_layers
            deps.config_result = config_result
            deps.effective_config = config_result.effective_config
            payload["config_fingerprint"] = config_result.fingerprint
            await deps.system_event_bus.publish(
                "config_reloaded",
                {"config_fingerprint": config_result.fingerprint, "scope": scope},
            )
        except Exception as exc:  # noqa: BLE001
            await deps.system_event_bus.publish("reload_failed", {"scope": "config", "error": str(exc)})
            raise GatewayAdapterError(status.HTTP_409_CONFLICT, "reload_failed", str(exc)) from exc
    if scope in {"skills", "all"}:
        payload["skills"] = await reload_skills(deps)
    if scope in {"mcp", "all"}:
        deps.extensions_service._cache.clear()
        extensions = deps.extensions_service.discover(
            config=deps.config_result.effective_config,
            fingerprint=deps.config_result.fingerprint,
        )
        payload["mcp_servers_connected"] = len(extensions.effective_mcp_servers)
        await deps.system_event_bus.publish(
            "capabilities_changed",
            {"mcp_servers_connected": len(extensions.effective_mcp_servers)},
        )
    return payload


def _runtime_repo_root() -> Path:
    return get_repo_root()


def _writable_config_path() -> Path:
    return resolve_config_path(repo_root=_runtime_repo_root()) or (resolve_anvil_profile_home() / "config.yaml")


def _writable_mcp_config_path() -> Path:
    return (_runtime_repo_root() / ".anvil" / "mcp.json").resolve()


def _replace_runtime_config_layer(deps: AppRuntimeDeps, layer: ConfigLayer) -> None:
    deps.config_layers = [
        item
        for item in deps.config_layers
        if not (item.name == layer.name and item.source == layer.source)
    ]
    deps.config_layers.append(layer)
    deps.config_coordinator.config_layers = deps.config_layers


async def system_event_stream(deps: AppRuntimeDeps):
    queue = deps.system_event_bus.subscribe()
    try:
        while True:
            event = await queue.get()
            yield f"id: {event.system_version}\n"
            yield f"event: {event.event}\n"
            yield f"data: {json.dumps(event.data, ensure_ascii=False)}\n\n"
    finally:
        deps.system_event_bus.unsubscribe(queue)


async def list_mcp_servers(deps: AppRuntimeDeps) -> list[McpServerView]:
    return [McpServerView.model_validate(item.model_dump(mode="json")) for item in list_extensions(deps, live=False)]


async def get_mcp_config_overview(deps: AppRuntimeDeps) -> McpConfigOverviewView:
    servers = await list_mcp_servers(deps)
    config_path = _writable_mcp_config_path()
    return McpConfigOverviewView(
        config_path=str(config_path),
        server_count=len(servers),
        enabled_count=sum(1 for item in servers if item.enabled),
        ready_count=sum(1 for item in servers if item.ready),
        auth_required_count=sum(1 for item in servers if item.auth_required),
        disabled_count=sum(1 for item in servers if not item.enabled),
        failed_count=sum(1 for item in servers if str(item.status).lower() in {"error", "failed"}),
        hidden_from_model_count=sum(1 for item in servers if not mcp_server_model_visible(item)),
    )


async def get_mcp_server_tools(deps: AppRuntimeDeps, server_id: str) -> McpServerToolsView:
    server = deps.config_result.effective_config.extensions.mcp_servers.get(server_id)
    if server is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "mcp_server_not_found", f"MCP server '{server_id}' was not found")
    status_view = refresh_extension(deps, server_id) if server.refresh_policy == "dynamic" else next(
        (item for item in list_extensions(deps) if item.server_id == server_id),
        None,
    )
    if status_view is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "mcp_server_not_found", f"MCP server '{server_id}' was not found")
    return McpServerToolsView(
        server_id=server_id,
        status=status_view.status,
        tool_names=list(status_view.tool_names),
        tool_count=status_view.tool_count,
        resource_count=status_view.resource_count,
        prompt_count=status_view.prompt_count,
        discovery_source=status_view.discovery_source,
    )


async def refresh_mcp_server(deps: AppRuntimeDeps, server_id: str) -> ExtensionStatusView:
    refreshed = refresh_extension(deps, server_id)
    await deps.system_event_bus.publish(
        "mcp_server_status",
        {"server_id": server_id, "status": refreshed.status, "tool_count": refreshed.tool_count},
    )
    return refreshed


def list_mcp_resources(deps: AppRuntimeDeps, server_id: str | None = None) -> list[CapabilityResourceView]:
    try:
        items = deps.extensions_service.list_resources(
            config=deps.config_result.effective_config,
            fingerprint=deps.config_result.fingerprint,
            server_id=server_id,
            live=False,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "mcp_server_not_found", str(exc)) from exc
    return [capability_resource_to_view(item) for item in items]


def read_mcp_resource(deps: AppRuntimeDeps, server_id: str, resource_id: str) -> McpResourceContentView:
    try:
        payload = deps.extensions_service.read_resource(
            config=deps.config_result.effective_config,
            fingerprint=deps.config_result.fingerprint,
            server_id=server_id,
            resource_id=resource_id,
        )
    except ValueError as exc:
        detail = str(exc)
        error = "mcp_resource_not_found" if "resource" in detail else "mcp_server_not_found"
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, error, detail) from exc
    return McpResourceContentView(
        resource_id=str(payload.get("resource_id")),
        title=str(payload.get("title") or payload.get("resource_id") or ""),
        description=str(payload.get("description") or ""),
        server_id=str(payload.get("server_id")) if payload.get("server_id") is not None else None,
        path=str(payload.get("path")) if payload.get("path") is not None else None,
        metadata=dict(payload.get("metadata") or {}),
        discovery_source=str((payload.get("metadata") or {}).get("discovery_source") or payload.get("discovery_source") or "inline_fallback"),
        supports_read=bool((payload.get("metadata") or {}).get("supports_read", True)),
        uri=str((payload.get("metadata") or {}).get("uri")) if (payload.get("metadata") or {}).get("uri") is not None else None,
        mime_type=str((payload.get("metadata") or {}).get("mime_type")) if (payload.get("metadata") or {}).get("mime_type") is not None else None,
        content=str(payload.get("content")) if payload.get("content") is not None else None,
    )


def list_mcp_prompts(deps: AppRuntimeDeps, server_id: str | None = None) -> list[CapabilityPromptView]:
    try:
        items = deps.extensions_service.list_prompts(
            config=deps.config_result.effective_config,
            fingerprint=deps.config_result.fingerprint,
            server_id=server_id,
            live=False,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "mcp_server_not_found", str(exc)) from exc
    return [capability_prompt_to_view(item) for item in items]


def get_mcp_prompt(
    deps: AppRuntimeDeps,
    server_id: str,
    prompt_id: str,
    arguments: dict[str, object] | None = None,
) -> McpPromptRenderView:
    try:
        payload = deps.extensions_service.get_prompt(
            config=deps.config_result.effective_config,
            fingerprint=deps.config_result.fingerprint,
            server_id=server_id,
            prompt_id=prompt_id,
            arguments=arguments,
        )
    except ValueError as exc:
        detail = str(exc)
        error = "mcp_prompt_not_found" if "prompt" in detail else "mcp_server_not_found"
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, error, detail) from exc
    metadata = dict(payload.get("metadata") or {})
    return McpPromptRenderView(
        prompt_id=str(payload.get("prompt_id")),
        title=str(payload.get("title") or payload.get("prompt_id") or ""),
        description=str(payload.get("description") or ""),
        server_id=str(payload.get("server_id")) if payload.get("server_id") is not None else None,
        arguments=[str(item) for item in payload.get("arguments", [])],
        metadata=metadata,
        discovery_source=str(metadata.get("discovery_source") or payload.get("discovery_source") or "inline_fallback"),
        supports_render=bool(metadata.get("supports_render", True)),
        input_schema=dict(metadata.get("input_schema")) if isinstance(metadata.get("input_schema"), dict) else {},
        rendered=str(payload.get("rendered") or ""),
        provided_arguments=dict(arguments or {}),
    )


async def reconnect_mcp_server(deps: AppRuntimeDeps, server_id: str) -> ExtensionStatusView:
    server = deps.config_result.effective_config.extensions.mcp_servers.get(server_id)
    if server is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "mcp_server_not_found", f"MCP server '{server_id}' was not found")
    refreshed = deps.extensions_service.reconnect_server(
        config=deps.config_result.effective_config,
        fingerprint=deps.config_result.fingerprint,
        server_id=server_id,
    )
    await deps.system_event_bus.publish(
        "mcp_server_status",
        {"server_id": server_id, "status": refreshed.status.value, "tool_count": len(refreshed.tools)},
    )
    return extension_status_to_view(refreshed, deps=deps)


async def get_mcp_server_provenance(deps: AppRuntimeDeps, server_id: str) -> McpServerProvenanceView:
    server = deps.config_result.effective_config.extensions.mcp_servers.get(server_id)
    if server is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "mcp_server_not_found", f"MCP server '{server_id}' was not found")
    return McpServerProvenanceView(
        server_id=server_id,
        provenance="config_file",
        description=server.description,
        transport_kind=server.transport_kind.value,
        startup_policy=server.startup_policy,
        refresh_policy=server.refresh_policy,
        approval_policy=server.approval_policy,
        tool_prefix=server.tool_prefix,
        collision_policy=server.collision_policy,
        tool_allowlist=list(server.tool_allowlist),
        tool_allowlist_active=server.tool_allowlist_active,
        tool_denylist=list(server.tool_denylist),
        oauth=redact_sensitive_config(server.oauth),
        env_resolution=redact_sensitive_config(server.env_resolution),
        header_templates=redact_sensitive_config(server.header_templates),
        resource_policy=redact_sensitive_config(server.resource_policy),
        prompt_policy=redact_sensitive_config(server.prompt_policy),
        reconnect_policy=redact_sensitive_config(server.reconnect_policy),
        healthcheck=redact_sensitive_config(server.healthcheck),
        connection_config=redact_sensitive_config(server.connection_config),
    )


def get_memory_overview(deps: AppRuntimeDeps) -> MemoryOverviewView:
    overview = deps.memory_manager.overview()
    platform_enabled = bool(deps.effective_config.memory_platform.enabled)
    legacy_capture_enabled = bool(deps.feature_set.memory_capture and not platform_enabled)
    return MemoryOverviewView(
        active_provider_id=overview.active_provider_id,
        runtime_mode="memory_platform" if platform_enabled else "legacy",
        legacy_capture_enabled=legacy_capture_enabled,
        migration_status={
            **dict(getattr(overview, "migration_status", {}) or {}),
            "memory_platform_enabled": platform_enabled,
            "legacy_capture_enabled": legacy_capture_enabled,
            "legacy_store_compatibility": "read_only" if platform_enabled else "active",
        },
        store_count=overview.store_count,
        archive_turn_count=overview.archive_turn_count,
        reflection_job_count=overview.reflection_job_count,
        stores=[memory_store_to_view(item) for item in overview.stores],
        layers=list_memory_layers(deps),
    )


def list_memory_stores(deps: AppRuntimeDeps) -> list[MemoryStoreView]:
    return [memory_store_to_view(item) for item in deps.memory_manager.list_stores()]


def list_memory_layers(deps: AppRuntimeDeps) -> list[MemoryLayerView]:
    stores = {item.store_id: item for item in deps.memory_manager.list_stores()}
    return [
        MemoryLayerView(
            layer_id=MemoryLayerId.SESSION,
            display_name="Session Memory",
            description="Read-only archive, frozen prompt snapshot metadata, and per-turn recall evidence.",
            writable=False,
            entry_count=0,
            store_id=None,
            summary="Session recall is derived from archive, transcript, and prompt snapshots.",
        ),
        MemoryLayerView(
            layer_id=MemoryLayerId.USER,
            display_name="User Memory",
            description="Durable user_profile facts: stable user preferences, corrections, and collaboration habits.",
            writable=True,
            entry_count=stores["user_profile"].entry_count if "user_profile" in stores else 0,
            store_id="user_profile",
            summary=stores["user_profile"].summary if "user_profile" in stores else None,
        ),
        MemoryLayerView(
            layer_id=MemoryLayerId.WORKSPACE,
            display_name="Workspace Memory",
            description="Durable runtime_memory facts: global work context, project facts, outcomes, and reflection write-backs.",
            writable=True,
            entry_count=stores["runtime_memory"].entry_count if "runtime_memory" in stores else 0,
            store_id="runtime_memory",
            summary=stores["runtime_memory"].summary if "runtime_memory" in stores else None,
        ),
    ]


def list_memory_store_entries(deps: AppRuntimeDeps, store_id: str) -> list[MemoryEntryView]:
    try:
        entries = deps.memory_manager.list_entries(store_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_store_not_found", f"memory store '{store_id}' was not found") from exc
    return [memory_entry_to_view(entry) for entry in entries]


def list_memory_layer_entries(deps: AppRuntimeDeps, layer_id: str) -> list[MemoryEntryView]:
    try:
        entries = deps.memory_manager.list_layer_entries(layer_id)
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "memory_layer_read_only", str(exc)) from exc
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_layer_not_found", f"memory layer '{layer_id}' was not found") from exc
    return [memory_entry_to_view(entry) for entry in entries]


def create_memory_entry(deps: AppRuntimeDeps, store_id: str, body: MemoryEntryCreateRequest) -> MemoryEntryView:
    try:
        entry = deps.memory_manager.create_entry(
            store_id,
            content=body.content,
            category=body.category,
            source_kind=body.source_kind,
            priority=body.priority,
            confidence=body.confidence,
            salience=body.salience,
            evidence_refs=body.evidence_refs,
        )
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_store_not_found", f"memory store '{store_id}' was not found") from exc
    return memory_entry_to_view(entry)


def create_memory_layer_entry(deps: AppRuntimeDeps, layer_id: str, body: MemoryEntryCreateRequest) -> MemoryEntryView:
    try:
        entry = deps.memory_manager.create_layer_entry(
            layer_id,
            content=body.content,
            category=body.category,
            source_kind=body.source_kind,
            priority=body.priority,
            confidence=body.confidence,
            salience=body.salience,
            evidence_refs=body.evidence_refs,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "memory_layer_read_only", str(exc)) from exc
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_layer_not_found", f"memory layer '{layer_id}' was not found") from exc
    return memory_entry_to_view(entry)


def update_memory_entry(
    deps: AppRuntimeDeps,
    store_id: str,
    entry_id: str,
    body: MemoryEntryUpdateRequest,
) -> MemoryEntryView:
    try:
        entry = deps.memory_manager.update_entry(
            store_id,
            entry_id,
            content=body.content,
            category=body.category,
            priority=body.priority,
            confidence=body.confidence,
            salience=body.salience,
            status=body.status,
        )
    except KeyError as exc:
        kind = "memory_entry_not_found" if str(exc) == entry_id else "memory_store_not_found"
        detail = (
            f"memory entry '{entry_id}' was not found"
            if kind == "memory_entry_not_found"
            else f"memory store '{store_id}' was not found"
        )
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, kind, detail) from exc
    return memory_entry_to_view(entry)


def update_memory_layer_entry(
    deps: AppRuntimeDeps,
    layer_id: str,
    entry_id: str,
    body: MemoryEntryUpdateRequest,
) -> MemoryEntryView:
    try:
        entry = deps.memory_manager.update_layer_entry(
            layer_id,
            entry_id,
            content=body.content,
            category=body.category,
            priority=body.priority,
            confidence=body.confidence,
            salience=body.salience,
            status=body.status,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "memory_layer_read_only", str(exc)) from exc
    except KeyError as exc:
        kind = "memory_entry_not_found" if str(exc) == entry_id else "memory_layer_not_found"
        detail = (
            f"memory entry '{entry_id}' was not found"
            if kind == "memory_entry_not_found"
            else f"memory layer '{layer_id}' was not found"
        )
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, kind, detail) from exc
    return memory_entry_to_view(entry)


def delete_memory_entry(deps: AppRuntimeDeps, store_id: str, entry_id: str) -> dict[str, str]:
    try:
        deps.memory_manager.delete_entry(store_id, entry_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_store_not_found", f"memory store '{store_id}' was not found") from exc
    return {"status": "deleted"}


def delete_memory_layer_entry(deps: AppRuntimeDeps, layer_id: str, entry_id: str) -> dict[str, str]:
    try:
        deps.memory_manager.delete_layer_entry(layer_id, entry_id)
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "memory_layer_read_only", str(exc)) from exc
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_layer_not_found", f"memory layer '{layer_id}' was not found") from exc
    return {"status": "deleted"}


def list_memory_providers(deps: AppRuntimeDeps) -> list[MemoryProviderView]:
    return [memory_provider_to_view(item) for item in deps.memory_manager.list_providers()]


def reload_memory_providers(deps: AppRuntimeDeps) -> list[MemoryProviderView]:
    return [memory_provider_to_view(item) for item in deps.memory_manager.reload_providers(effective_config=deps.effective_config)]


def activate_memory_provider(deps: AppRuntimeDeps, provider_id: str) -> MemoryProviderView:
    try:
        provider = deps.memory_manager.activate_provider(provider_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_provider_not_found", f"memory provider '{provider_id}' was not found") from exc
    return memory_provider_to_view(provider)


def test_memory_provider(deps: AppRuntimeDeps, provider_id: str) -> MemoryProviderTestResponse:
    try:
        result = deps.memory_manager.test_provider(provider_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_provider_not_found", f"memory provider '{provider_id}' was not found") from exc
    return MemoryProviderTestResponse(
        provider_id=result.provider_id,
        ok=result.ok,
        health=result.health,
        diagnostics=list(result.diagnostics),
    )


def search_memory_archive(deps: AppRuntimeDeps, body: MemoryArchiveSearchRequest) -> MemoryArchiveSearchResultView:
    result = deps.memory_manager.search_archive(body.query, limit=body.limit)
    return MemoryArchiveSearchResultView(
        query=result.query,
        hits=[memory_archive_hit_to_view(item) for item in result.hits],
        provider_notes=list(result.provider_notes),
    )


def get_session_memory(deps: AppRuntimeDeps, thread_id: str) -> SessionMemoryView:
    try:
        state = deps.thread_service.get_thread_state(thread_id)
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found") from exc
    payload = deps.memory_manager.get_session_memory(
        thread_id=thread_id,
        memory_namespace=state.memory.memory_namespace,
        injected_memory_snapshot_id=state.memory.injected_memory_snapshot_id,
    )
    return SessionMemoryView(
        layer_id=MemoryLayerId.SESSION,
        thread_id=payload["thread_id"],
        memory_namespace=payload.get("memory_namespace"),
        injected_memory_snapshot_id=payload.get("injected_memory_snapshot_id"),
        archive_turn_count=int(payload.get("archive_turn_count", 0)),
        recent_turns=[SessionTurnView.model_validate(item) for item in payload.get("recent_turns", [])],
        latest_prompt_snapshot=prompt_snapshot_to_view(payload.get("latest_prompt_snapshot")),
        session_summary=str(payload.get("session_summary") or ""),
    )


def search_memory_sessions(deps: AppRuntimeDeps, body: SessionSearchRequest) -> SessionSearchResultView:
    try:
        result = deps.memory_manager.search_sessions(
            query=body.query,
            current_thread_id=body.thread_id,
            scope=body.scope.value,
            limit=body.limit,
            mode=body.mode.value,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_session_search_scope", str(exc)) from exc

    return SessionSearchResultView(
        query=result["query"],
        thread_id=result.get("thread_id"),
        scope=SessionSearchScope(result["scope"]),
        groups=[
            SessionSearchThreadGroupView(
                thread_id=item["thread_id"],
                hit_count=item.get("hit_count", 0),
                summary=str(item.get("summary") or ""),
                excerpts=list(item.get("excerpts", [])),
                latest_created_at=item.get("latest_created_at"),
                hits=[memory_archive_hit_to_view(hit) for hit in item.get("hits", [])],
                evidence=[recall_evidence_to_view(evidence) for evidence in item.get("evidence", [])],
                latest_prompt_snapshot=prompt_snapshot_to_view(item.get("latest_prompt_snapshot")),
            )
            for item in result.get("groups", [])
        ],
        provider_notes=list(result.get("provider_notes", [])),
        current_thread_snapshot=prompt_snapshot_to_view(result.get("current_thread_snapshot")),
    )


def get_memory_overview_vnext(deps: AppRuntimeDeps) -> MemoryOverviewView:
    return get_memory_overview(deps)


def list_memory_user_entries(deps: AppRuntimeDeps) -> list[MemoryEntryView]:
    return list_memory_layer_entries(deps, "user")


def create_memory_user_entry(deps: AppRuntimeDeps, body: MemoryEntryCreateRequest) -> MemoryEntryView:
    return create_memory_layer_entry(deps, "user", body)


def update_memory_user_entry(deps: AppRuntimeDeps, entry_id: str, body: MemoryEntryUpdateRequest) -> MemoryEntryView:
    return update_memory_layer_entry(deps, "user", entry_id, body)


def delete_memory_user_entry(deps: AppRuntimeDeps, entry_id: str) -> dict[str, str]:
    return delete_memory_layer_entry(deps, "user", entry_id)


def list_memory_workspace_entries(deps: AppRuntimeDeps) -> list[MemoryEntryView]:
    return list_memory_layer_entries(deps, "workspace")


def create_memory_workspace_entry(deps: AppRuntimeDeps, body: MemoryEntryCreateRequest) -> MemoryEntryView:
    return create_memory_layer_entry(deps, "workspace", body)


def update_memory_workspace_entry(deps: AppRuntimeDeps, entry_id: str, body: MemoryEntryUpdateRequest) -> MemoryEntryView:
    return update_memory_layer_entry(deps, "workspace", entry_id, body)


def delete_memory_workspace_entry(deps: AppRuntimeDeps, entry_id: str) -> dict[str, str]:
    return delete_memory_layer_entry(deps, "workspace", entry_id)


def get_memory_trace(deps: AppRuntimeDeps, body: MemoryTraceRequest) -> MemoryTraceResponse:
    traces = deps.memory_manager.list_traces(thread_id=body.thread_id, target_id=body.target_id, limit=body.limit)
    return MemoryTraceResponse(items=[memory_trace_to_view(trace) for trace in traces])


def list_memory_admin_providers(deps: AppRuntimeDeps) -> MemoryProviderAdminResponse:
    return MemoryProviderAdminResponse(items=list_memory_providers(deps))


def list_memory_admin_reflections(deps: AppRuntimeDeps) -> ReflectionJobAdminResponse:
    return ReflectionJobAdminResponse(items=list_reflection_jobs(deps))


def list_memory_admin_conflicts(deps: AppRuntimeDeps) -> MemoryConflictResponse:
    return MemoryConflictResponse(items=[memory_conflict_to_view(item) for item in deps.memory_manager.list_conflicts()])


def list_memory_admin_staleness(deps: AppRuntimeDeps) -> MemoryStalenessResponse:
    return MemoryStalenessResponse(items=[memory_staleness_to_view(item) for item in deps.memory_manager.list_staleness()])


def get_memory_admin_health(deps: AppRuntimeDeps) -> MemoryHealthResponse:
    return memory_health_to_view(deps.memory_manager.health_report())


def run_memory_admin_benchmark(deps: AppRuntimeDeps, body: MemoryRecallBenchmarkRequest) -> MemoryRecallBenchmarkResponse:
    cases = tuple(
        MemoryRecallBenchmarkCase.model_validate(item.model_dump(mode="json"))
        for item in body.cases
    )
    report = deps.memory_manager.recall_benchmark(
        suite_id=body.suite_id,
        cases=cases,
        evidence_limit=body.evidence_limit,
    )
    return memory_recall_benchmark_to_view(report)


def list_memory_admin_benchmark_suites(deps: AppRuntimeDeps) -> MemoryRecallBenchmarkSuiteListResponse:
    return MemoryRecallBenchmarkSuiteListResponse(
        items=[memory_recall_benchmark_suite_to_view(item) for item in deps.memory_manager.list_recall_benchmark_suites()]
    )


def upsert_memory_admin_benchmark_suite(
    deps: AppRuntimeDeps,
    body: MemoryRecallBenchmarkSuiteUpsertRequest,
) -> MemoryRecallBenchmarkSuiteView:
    try:
        suite = deps.memory_manager.upsert_recall_benchmark_suite(
            MemoryRecallBenchmarkSuite.model_validate(body.model_dump(mode="json")),
            source=body.source,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_memory_benchmark_suite", str(exc)) from exc
    return memory_recall_benchmark_suite_to_view(suite)


def delete_memory_admin_benchmark_suite(deps: AppRuntimeDeps, suite_id: str) -> MemoryRecallBenchmarkSuiteView:
    try:
        deleted = deps.memory_manager.delete_recall_benchmark_suite(suite_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_benchmark_suite_not_found", f"memory benchmark suite '{suite_id}' was not found") from exc
    return memory_recall_benchmark_suite_to_view(deleted)


def run_memory_admin_benchmark_suite(
    deps: AppRuntimeDeps,
    suite_id: str,
    body: MemoryRecallBenchmarkRunRequest,
) -> MemoryRecallBenchmarkRunView:
    try:
        run = deps.memory_manager.run_recall_benchmark_suite(
            suite_id,
            evidence_limit=body.evidence_limit,
            source=body.source,
            record=body.record,
        )
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_benchmark_suite_not_found", f"memory benchmark suite '{suite_id}' was not found") from exc
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_memory_benchmark_suite", str(exc)) from exc
    return memory_recall_benchmark_run_to_view(run)


def list_memory_admin_benchmark_runs(
    deps: AppRuntimeDeps,
    *,
    suite_id: str | None = None,
    limit: int = 20,
) -> MemoryRecallBenchmarkRunListResponse:
    return MemoryRecallBenchmarkRunListResponse(
        items=[
            memory_recall_benchmark_run_to_view(item)
            for item in deps.memory_manager.list_recall_benchmark_runs(suite_id=suite_id, limit=limit)
        ]
    )


def export_memory_admin(deps: AppRuntimeDeps) -> MemoryAdminExportView:
    return MemoryAdminExportView.model_validate(deps.memory_manager.export_admin())


def import_memory_admin(deps: AppRuntimeDeps, body: MemoryAdminImportRequest) -> MemoryAdminImportResponse:
    result = deps.memory_manager.import_admin(body.model_dump(mode="json"))
    return MemoryAdminImportResponse.model_validate(result)


def audit_memory_admin(deps: AppRuntimeDeps) -> MemoryAdminAuditView:
    return MemoryAdminAuditView.model_validate(deps.memory_manager.audit_admin())


def flush_memory_admin(deps: AppRuntimeDeps, body: MemoryFlushRequest) -> MemoryFlushResponse:
    result = deps.memory_manager.on_session_end(thread_id=body.thread_id, reason="manual_flush", allow_network=True) if body.thread_id else deps.memory_manager.flush_memory()
    return MemoryFlushResponse.model_validate(result.model_dump(mode="json"))


def onboard_memory_workspace(deps: AppRuntimeDeps, body: MemoryOnboardingRequest) -> MemoryOnboardingResponse:
    workspace_path = str(body.workspace_path or deps.effective_config.workspace.root or get_repo_root()).strip()
    if not workspace_path:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "workspace_path_required", "workspace onboarding requires a workspace path")
    try:
        result = deps.memory_manager.onboard_workspace(
            workspace_path=workspace_path,
            thread_id=body.thread_id,
            force=body.force,
            source=body.source,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_workspace_onboarding", str(exc)) from exc
    return MemoryOnboardingResponse.model_validate(result.model_dump(mode="json"))


def list_memory_admin_review(deps: AppRuntimeDeps) -> MemoryReviewResponse:
    return MemoryReviewResponse(items=[memory_review_item_to_view(item) for item in deps.memory_manager.list_review_items(status="pending")])


def approve_memory_review_item(deps: AppRuntimeDeps, review_id: str, body: MemoryReviewDecisionRequest) -> MemoryEntryView:
    try:
        entry = deps.memory_manager.approve_review_item(review_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_review_not_found", f"memory review item '{review_id}' was not found") from exc
    return memory_entry_to_view(entry)


def reject_memory_review_item(deps: AppRuntimeDeps, review_id: str, body: MemoryReviewDecisionRequest) -> MemoryReviewItemView:
    try:
        item = deps.memory_manager.reject_review_item(review_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_review_not_found", f"memory review item '{review_id}' was not found") from exc
    return memory_review_item_to_view(item)


def batch_memory_review(deps: AppRuntimeDeps, body: MemoryReviewBatchRequest) -> MemoryReviewBatchResponse:
    result = deps.memory_manager.batch_review(approve=tuple(body.approve), reject=tuple(body.reject))
    return MemoryReviewBatchResponse.model_validate(result)


def govern_memory_entry(deps: AppRuntimeDeps, memory_id: str, body: MemoryGovernanceActionRequest) -> MemoryGovernanceActionResponse:
    try:
        result = deps.memory_manager.govern_memory(
            memory_id,
            action=body.action,
            reason=body.reason,
            source=body.source,
        )
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_not_found", f"memory '{memory_id}' was not found") from exc
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_memory_governance_action", str(exc)) from exc
    return memory_governance_to_view(result)


def batch_govern_memory(deps: AppRuntimeDeps, body: MemoryGovernanceBatchRequest) -> MemoryGovernanceBatchResponse:
    try:
        if body.dry_run:
            result = deps.memory_manager.plan_memory_governance(
                policy=body.policy,
                layer_id=body.layer_id,
                limit=body.limit,
            )
        else:
            result = deps.memory_manager.execute_memory_governance(
                policy=body.policy,
                layer_id=body.layer_id,
                limit=body.limit,
                source=body.source,
            )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_memory_governance_batch", str(exc)) from exc
    return memory_governance_batch_to_view(result)


def list_profile_facets(deps: AppRuntimeDeps) -> ProfileFacetListResponse:
    return ProfileFacetListResponse(
        policy=profile_facet_policy_to_view(deps.memory_manager.profile_facet_policy()),
        items=[profile_facet_to_view(item) for item in deps.memory_manager.list_profile_facets()],
    )


def govern_profile_facet(deps: AppRuntimeDeps, facet_id: str, body: ProfileFacetGovernanceRequest) -> ProfileFacetGovernanceResponse:
    try:
        result = deps.memory_manager.govern_profile_facet(
            facet_id,
            action=body.action,
            reason=body.reason,
            source=body.source,
        )
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "profile_facet_not_found", f"profile facet '{facet_id}' was not found") from exc
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_profile_facet_action", str(exc)) from exc
    return profile_facet_governance_to_view(result)


def rebuild_profile_facets(deps: AppRuntimeDeps, body: ProfileFacetRebuildRequest) -> ProfileFacetRebuildResponse:
    return profile_facet_rebuild_to_view(deps.memory_manager.rebuild_profile_facets(source=body.source))


def list_profile_facet_audit(deps: AppRuntimeDeps, *, limit: int = 50) -> ProfileFacetAuditResponse:
    return ProfileFacetAuditResponse(items=[profile_facet_audit_to_view(item) for item in deps.memory_manager.list_profile_facet_audit(limit=limit)])


def run_memory_maintenance(deps: AppRuntimeDeps, body: MemoryMaintenanceRequest) -> MemoryMaintenanceResponse:
    try:
        result = deps.memory_manager.run_maintenance(
            dry_run=body.dry_run,
            policy=body.policy,
            layer_id=body.layer_id,
            limit=body.limit,
            source=body.source,
            run_reflection_due_jobs=body.run_reflection_due_jobs,
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_memory_maintenance", str(exc)) from exc
    return memory_maintenance_to_view(result)


def get_memory_maintenance_automation(deps: AppRuntimeDeps) -> MemoryMaintenanceAutomationStatusResponse:
    return MemoryMaintenanceAutomationStatusResponse.model_validate(deps.memory_manager.maintenance_automation_status())


async def run_memory_maintenance_automation(
    deps: AppRuntimeDeps,
    body: MemoryMaintenanceAutomationRequest,
) -> MemoryMaintenanceAutomationRunResponse:
    if hasattr(deps, "run_memory_maintenance_automation_sync"):
        result = deps.run_memory_maintenance_automation_sync(force_run=body.force_run)
        if result.ran and hasattr(deps, "_publish_memory_maintenance_automation"):
            await deps._publish_memory_maintenance_automation(result)
    else:
        result = deps.memory_manager.run_maintenance_automation_if_due(force_run=body.force_run)
    return MemoryMaintenanceAutomationRunResponse(
        ran=bool(result.ran),
        reason=str(result.reason),
        next_run_at=result.next_run_at,
        report=memory_maintenance_to_view(result.report) if result.report is not None else None,
    )


def resolve_memory_conflict(deps: AppRuntimeDeps, conflict_id: str, body: MemoryConflictResolveRequest) -> MemoryConflictView:
    try:
        conflict = deps.memory_manager.resolve_conflict(conflict_id, action=body.action)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "memory_conflict_not_found", f"memory conflict '{conflict_id}' was not found") from exc
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_memory_conflict_resolution", str(exc)) from exc
    return memory_conflict_to_view(conflict)


def list_reflection_jobs(deps: AppRuntimeDeps) -> list[ReflectionJobView]:
    return [reflection_job_to_view(job) for job in deps.memory_manager.list_reflection_jobs()]


def create_reflection_job(deps: AppRuntimeDeps, body: ReflectionJobCreateRequest) -> ReflectionJobView:
    from anvil.memory_platform import ReflectionJob, ReflectionScheduleKind

    job = ReflectionJob(
        job_id=body.job_id,
        name=body.name,
        schedule_kind=ReflectionScheduleKind(body.schedule_kind),
        target_store_id=body.target_store_id,
        template=body.template,
        instructions=body.instructions,
        source_query=body.source_query,
        interval_seconds=body.interval_seconds,
        cron=body.cron,
    )
    created = deps.memory_manager.create_reflection_job(job)
    return reflection_job_to_view(created)


def run_reflection_job(deps: AppRuntimeDeps, job_id: str) -> ReflectionJobRunView:
    try:
        result = deps.memory_manager.run_reflection_job(job_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "reflection_job_not_found", f"reflection job '{job_id}' was not found") from exc
    return ReflectionJobRunView(
        job_id=result.job_id,
        status=result.status,
        entries_written=result.entries_written,
        archive_hits=result.archive_hits,
        summary=result.summary,
        written_entries=[memory_entry_to_view(entry) for entry in result.written_entries],
    )


def pause_reflection_job(deps: AppRuntimeDeps, job_id: str) -> ReflectionJobView:
    try:
        job = deps.memory_manager.pause_reflection_job(job_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "reflection_job_not_found", f"reflection job '{job_id}' was not found") from exc
    return reflection_job_to_view(job)


def resume_reflection_job(deps: AppRuntimeDeps, job_id: str) -> ReflectionJobView:
    try:
        job = deps.memory_manager.resume_reflection_job(job_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "reflection_job_not_found", f"reflection job '{job_id}' was not found") from exc
    return reflection_job_to_view(job)


def remove_reflection_job(deps: AppRuntimeDeps, job_id: str) -> ReflectionJobView:
    try:
        job = deps.memory_manager.remove_reflection_job(job_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "reflection_job_not_found", f"reflection job '{job_id}' was not found") from exc
    return reflection_job_to_view(job)


def list_scheduled_tasks(deps: AppRuntimeDeps, *, include_disabled: bool = True) -> ScheduledTaskAdminResponse:
    return ScheduledTaskAdminResponse(
        items=[scheduled_task_to_view(task) for task in deps.scheduled_task_service.list_tasks(include_disabled=include_disabled)]
    )


def get_scheduled_task_automation(deps: AppRuntimeDeps) -> ScheduledTaskAutomationStatusResponse:
    scheduled_config = deps.effective_config.scheduled_tasks
    return scheduled_task_automation_status_to_view(
        deps.scheduled_task_service.automation_status(
            tick_seconds=scheduled_config.tick_seconds,
            max_due_per_tick=scheduled_config.max_due_per_tick,
        )
    )


async def run_scheduled_task_automation(deps: AppRuntimeDeps) -> ScheduledTaskAutomationRunResponse:
    scheduled_config = deps.effective_config.scheduled_tasks
    result = deps.scheduled_task_service.run_automation_due(
        limit=max(int(scheduled_config.max_due_per_tick), 1),
        tick_seconds=scheduled_config.tick_seconds,
    )
    for item in result.results:
        if not item.ran:
            continue
        await deps.system_event_bus.publish(
            "scheduled_task_run",
            {
                "task_id": item.task.task_id,
                "execution_id": item.execution.execution_id if item.execution else None,
                "status": item.execution.status if item.execution else None,
                "next_run_at": item.task.next_run_at,
            },
        )
    return scheduled_task_automation_run_to_view(result)


def get_scheduled_task(deps: AppRuntimeDeps, task_id: str) -> ScheduledTaskView:
    try:
        task = deps.scheduled_task_service.get_task(task_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "scheduled_task_not_found", f"scheduled task '{task_id}' was not found") from exc
    return scheduled_task_to_view(task)


def create_scheduled_task(deps: AppRuntimeDeps, body: ScheduledTaskCreateRequest) -> ScheduledTaskView:
    from anvil.scheduled_tasks import ScheduledTaskCreateRequest as RuntimeScheduledTaskCreateRequest

    try:
        task = deps.scheduled_task_service.create_task(
            RuntimeScheduledTaskCreateRequest(
                task_id=body.task_id,
                name=body.name,
                prompt=body.prompt,
                schedule=body.schedule,
                enabled=body.enabled,
                thread_id=body.thread_id,
                execution_mode=body.execution_mode or deps.effective_config.scheduled_tasks.default_execution_mode,
                selected_model=body.selected_model,
                selected_profile=body.selected_profile,
                selected_reasoning_effort=body.selected_reasoning_effort,
                promoted_capabilities=tuple(body.promoted_capabilities),
                max_runs=body.max_runs,
                delivery=body.delivery,
                metadata=body.metadata,
            )
        )
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_scheduled_task", str(exc)) from exc
    return scheduled_task_to_view(task)


def update_scheduled_task(deps: AppRuntimeDeps, task_id: str, body: ScheduledTaskUpdateRequest) -> ScheduledTaskView:
    from anvil.scheduled_tasks import ScheduledTaskUpdateRequest as RuntimeScheduledTaskUpdateRequest

    updates = body.model_dump(exclude_unset=True)
    if "promoted_capabilities" in updates and updates["promoted_capabilities"] is not None:
        updates["promoted_capabilities"] = tuple(updates["promoted_capabilities"])
    try:
        task = deps.scheduled_task_service.update_task(
            task_id,
            RuntimeScheduledTaskUpdateRequest(**updates),
        )
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "scheduled_task_not_found", f"scheduled task '{task_id}' was not found") from exc
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_scheduled_task", str(exc)) from exc
    return scheduled_task_to_view(task)


async def run_scheduled_task(deps: AppRuntimeDeps, task_id: str, *, force: bool = True) -> ScheduledTaskRunView:
    try:
        result = deps.scheduled_task_service.run_task(task_id, force=force)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "scheduled_task_not_found", f"scheduled task '{task_id}' was not found") from exc
    if result.ran:
        await deps.system_event_bus.publish(
            "scheduled_task_run",
            {
                "task_id": result.task.task_id,
                "execution_id": result.execution.execution_id if result.execution else None,
                "status": result.execution.status if result.execution else None,
                "next_run_at": result.task.next_run_at,
            },
        )
    return scheduled_task_run_to_view(result)


def pause_scheduled_task(deps: AppRuntimeDeps, task_id: str) -> ScheduledTaskView:
    try:
        task = deps.scheduled_task_service.pause_task(task_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "scheduled_task_not_found", f"scheduled task '{task_id}' was not found") from exc
    return scheduled_task_to_view(task)


def resume_scheduled_task(deps: AppRuntimeDeps, task_id: str) -> ScheduledTaskView:
    try:
        task = deps.scheduled_task_service.resume_task(task_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "scheduled_task_not_found", f"scheduled task '{task_id}' was not found") from exc
    return scheduled_task_to_view(task)


def remove_scheduled_task(deps: AppRuntimeDeps, task_id: str) -> ScheduledTaskView:
    try:
        task = deps.scheduled_task_service.remove_task(task_id)
    except KeyError as exc:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "scheduled_task_not_found", f"scheduled task '{task_id}' was not found") from exc
    return scheduled_task_to_view(task)


def list_scheduled_task_executions(
    deps: AppRuntimeDeps,
    *,
    task_id: str | None = None,
    limit: int = 50,
) -> ScheduledTaskExecutionResponse:
    return ScheduledTaskExecutionResponse(
        items=[
            scheduled_task_execution_to_view(execution)
            for execution in deps.scheduled_task_service.list_executions(task_id=task_id, limit=limit)
        ]
    )


def list_extensions(deps: AppRuntimeDeps, *, live: bool = False) -> list[ExtensionStatusView]:
    result = deps.extensions_service.discover(
        config=deps.config_result.effective_config,
        fingerprint=deps.config_result.fingerprint,
        live=live,
    )
    return [
        extension_status_to_view(item, deps=deps)
        for item in result.materializations
        if item.source_kind == "mcp"
    ]


def refresh_extension(deps: AppRuntimeDeps, server_id: str) -> ExtensionStatusView:
    server = deps.config_result.effective_config.extensions.mcp_servers.get(server_id)
    if server is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "extension_not_found", f"extension '{server_id}' was not found")
    if server.refresh_policy != "dynamic":
        raise GatewayAdapterError(status.HTTP_409_CONFLICT, "refresh_not_enabled", f"extension '{server_id}' does not allow dynamic refresh")

    item = deps.extensions_service.refresh_server(
        config=deps.config_result.effective_config,
        fingerprint=deps.config_result.fingerprint,
        server_id=server_id,
    )
    return extension_status_to_view(item, deps=deps)


def list_subagent_tasks(deps: AppRuntimeDeps, thread_id: str) -> list[SubagentTaskView]:
    if deps.checkpointer.get_thread_state(thread_id) is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    tasks = deps.subagent_service.list_tasks(parent_thread_id=thread_id)
    return [subagent_task_to_view(deps, task.task_id) for task in tasks]


def get_subagent_dependency_graph(
    deps: AppRuntimeDeps,
    thread_id: str,
    *,
    parent_run_id: str | None = None,
) -> SubagentDependencyGraphView:
    parent_state = deps.checkpointer.get_thread_state(thread_id)
    if parent_state is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    graph = deps.subagent_service.build_dependency_graph(parent_thread_id=thread_id, parent_run_id=parent_run_id)
    tasks = [
        subagent_task_to_view_from_runtime(
            deps.subagent_service,
            path_service=deps.path_service,
            parent_thread_id=thread_id,
            task_id=str(node["task_id"]),
            durable_history=parent_state.durable_subagent_job_history,
            dependency_state=str(node.get("dependency_state")) if node.get("dependency_state") is not None else None,
        )
        for node in graph["nodes"]
        if isinstance(node, dict)
    ]
    return SubagentDependencyGraphView(
        parent_thread_id=thread_id,
        parent_run_id=parent_run_id,
        tasks=tasks,
        edges=[SubagentDependencyEdgeView.model_validate(edge) for edge in graph["edges"]],
        ready_task_ids=[str(item) for item in graph["ready_task_ids"]],
        waiting_task_ids=[str(item) for item in graph["waiting_task_ids"]],
        blocked_task_ids=[str(item) for item in graph["blocked_task_ids"]],
        missing_dependency_task_ids=[str(item) for item in graph["missing_dependency_task_ids"]],
    )


def get_subagent_task(deps: AppRuntimeDeps, thread_id: str, task_id: str) -> SubagentTaskView:
    if deps.checkpointer.get_thread_state(thread_id) is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    task = deps.subagent_service.get_task(task_id)
    if task is None or task.parent_thread_id != thread_id:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "subagent_not_found", f"subagent task '{task_id}' was not found")
    return subagent_task_to_view(deps, task.task_id)


def wait_subagent_task(deps: AppRuntimeDeps, thread_id: str, task_id: str, timeout_seconds: int | None = None) -> SubagentTaskView:
    get_subagent_task(deps, thread_id, task_id)
    try:
        deps.subagent_service.wait(task_id, timeout_seconds=timeout_seconds)
    except TimeoutError as exc:
        raise GatewayAdapterError(status.HTTP_408_REQUEST_TIMEOUT, "subagent_wait_timeout", str(exc)) from exc
    return subagent_task_to_view(deps, task_id)


def cancel_subagent_task(deps: AppRuntimeDeps, thread_id: str, task_id: str) -> SubagentTaskView:
    task = deps.subagent_service.get_task(task_id)
    if task is None or task.parent_thread_id != thread_id:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "subagent_not_found", f"subagent task '{task_id}' was not found")
    deps.subagent_service.cancel(task_id, reason="cancelled by user")
    return subagent_task_to_view(deps, task_id)


def list_process_sessions(deps: AppRuntimeDeps, thread_id: str) -> list[ProcessSessionView]:
    if deps.checkpointer.get_thread_state(thread_id) is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    return [process_session_to_view(item, path_service=deps.path_service, thread_id=thread_id) for item in deps.process_service.list_sessions(thread_id=thread_id)]


def get_process_capabilities(deps: AppRuntimeDeps, thread_id: str) -> TerminalBackendCapabilitiesView:
    if deps.checkpointer.get_thread_state(thread_id) is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    return TerminalBackendCapabilitiesView.model_validate(deps.process_service.capabilities().model_dump(mode="json"))


def spawn_process_session(deps: AppRuntimeDeps, thread_id: str, body: ProcessSpawnRequest) -> ProcessSessionView:
    if deps.checkpointer.get_thread_state(thread_id) is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    runtime_cwd = deps.path_service.translate_user_text_to_runtime(body.cwd, thread_id=thread_id) or body.cwd
    runtime_command = deps.path_service.translate_user_text_to_runtime(body.command, thread_id=thread_id) or body.command
    try:
        host_cwd = deps.path_service.resolve_virtual_path(thread_id, runtime_cwd)
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_400_BAD_REQUEST, "invalid_process_cwd", str(exc)) from exc
    capabilities = deps.process_service.capabilities()
    if capabilities.remote or capabilities.isolated:
        launch_cwd = runtime_cwd
        launch_command = runtime_command
    else:
        launch_cwd = str(host_cwd)
        launch_command = deps.path_service.translate_runtime_text_to_host(runtime_command, thread_id=thread_id) or runtime_command
    session = deps.process_service.spawn(
        thread_id=thread_id,
        command=launch_command,
        cwd=launch_cwd,
        env=build_process_env(path_service=deps.path_service, thread_id=thread_id, extra_env=body.env),
    )
    return process_session_to_view(session, path_service=deps.path_service, thread_id=thread_id)


def get_process_session(deps: AppRuntimeDeps, thread_id: str, session_id: str) -> ProcessSessionView:
    if deps.checkpointer.get_thread_state(thread_id) is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "thread_not_found", f"thread '{thread_id}' was not found")
    session = deps.process_service.get_session(session_id)
    if session is None or session.thread_id != thread_id:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "process_not_found", f"process session '{session_id}' was not found")
    return process_session_to_view(session, path_service=deps.path_service, thread_id=thread_id)


def wait_process_session(deps: AppRuntimeDeps, thread_id: str, session_id: str, timeout_seconds: int | None = None) -> ProcessSessionView:
    get_process_session(deps, thread_id, session_id)
    return process_session_to_view(
        deps.process_service.wait(session_id, timeout_seconds=timeout_seconds),
        path_service=deps.path_service,
        thread_id=thread_id,
    )


def kill_process_session(deps: AppRuntimeDeps, thread_id: str, session_id: str) -> ProcessSessionView:
    get_process_session(deps, thread_id, session_id)
    return process_session_to_view(
        deps.process_service.kill(session_id),
        path_service=deps.path_service,
        thread_id=thread_id,
    )


def write_process_stdin(deps: AppRuntimeDeps, thread_id: str, session_id: str, body: ProcessStdinRequest) -> ProcessSessionView:
    get_process_session(deps, thread_id, session_id)
    try:
        session = deps.process_service.write_stdin(session_id, body.data, submit=body.submit)
    except ValueError as exc:
        raise GatewayAdapterError(status.HTTP_409_CONFLICT, "process_stdin_unavailable", str(exc)) from exc
    return process_session_to_view(session, path_service=deps.path_service, thread_id=thread_id)


def close_process_stdin(deps: AppRuntimeDeps, thread_id: str, session_id: str) -> ProcessSessionView:
    get_process_session(deps, thread_id, session_id)
    return process_session_to_view(
        deps.process_service.close_stdin(session_id),
        path_service=deps.path_service,
        thread_id=thread_id,
    )


def interrupt_process_session(deps: AppRuntimeDeps, thread_id: str, session_id: str) -> ProcessSessionView:
    get_process_session(deps, thread_id, session_id)
    return process_session_to_view(
        deps.process_service.interrupt(session_id),
        path_service=deps.path_service,
        thread_id=thread_id,
    )


def resize_process_session(deps: AppRuntimeDeps, thread_id: str, session_id: str, body: ProcessResizeRequest) -> ProcessSessionView:
    get_process_session(deps, thread_id, session_id)
    return process_session_to_view(
        deps.process_service.resize(session_id, columns=body.columns, rows=body.rows),
        path_service=deps.path_service,
        thread_id=thread_id,
    )


def read_process_log(
    deps: AppRuntimeDeps,
    thread_id: str,
    session_id: str,
    offset: int = 0,
    limit: int = 200,
    cursor: int | None = None,
) -> ProcessLogView:
    get_process_session(deps, thread_id, session_id)
    return process_log_to_view(
        deps.process_service.read_log(session_id, offset=offset, limit=limit, cursor=cursor),
        path_service=deps.path_service,
        thread_id=thread_id,
    )


def thread_metadata_to_view(metadata: ThreadMetadataView) -> ThreadView:
    return ThreadView(
        thread_id=metadata.thread_id,
        title=metadata.title,
        status=metadata.status.value,
        updated_at=metadata.updated_at,
        last_message_at=metadata.last_message_at,
        last_user_message_preview=sanitize_thread_preview(metadata.last_user_message_preview),
        has_pending_approval=metadata.has_pending_approval,
        has_active_subagent_tasks=metadata.has_active_subagent_tasks,
        source_kind="web_chat",
        source_label="Web Chat",
        channel_badge="web",
    )


def sanitize_thread_preview(preview: str | None) -> str | None:
    if not isinstance(preview, str):
        return preview
    text = preview.strip()
    if text.startswith("[LOOP DETECTED]") or is_delegation_orchestration_text(text):
        return None
    return preview


def build_runtime_capabilities_view(deps: AppRuntimeDeps) -> RuntimeCapabilitiesView:
    effective_config = deps.config_result.effective_config
    feature_set = deps.feature_set
    extensions = deps.extensions_service.discover(
        config=effective_config,
        fingerprint=deps.config_result.fingerprint,
    )
    supported_modes = ["host_isolated", "isolated"]
    sandbox_mode = effective_config.sandbox_mode.value
    if sandbox_mode == "local":
        sandbox_mode = "host_isolated"
    isolated_supported = shutil.which("docker") is not None
    return RuntimeCapabilitiesView(
        summarization_enabled=bool(feature_set.summarization and effective_config.summarization.enabled),
        plan_mode_enabled=bool(feature_set.plan_mode and effective_config.plan_mode.enabled),
        view_image_enabled=bool(feature_set.view_image),
        memory_enabled=bool(feature_set.memory),
        skills_count=len(deps.skills_service.discover(config=effective_config, fingerprint=deps.config_result.fingerprint).enabled_ids),
        mcp_servers_connected=len(extensions.effective_mcp_servers),
        sandbox_mode=sandbox_mode if sandbox_mode in supported_modes else "unsupported",
        supported_sandbox_modes=supported_modes,
        isolated_sandbox_supported=isolated_supported,
        guardrails_enabled=bool(feature_set.guardrails and effective_config.guardrails.enabled),
    )


def build_runtime_capabilities_summary_view(deps: AppRuntimeDeps) -> RuntimeCapabilitiesView:
    effective_config = deps.config_result.effective_config
    feature_set = deps.feature_set
    supported_modes = ["host_isolated", "isolated"]
    sandbox_mode = effective_config.sandbox_mode.value
    if sandbox_mode == "local":
        sandbox_mode = "host_isolated"
    return RuntimeCapabilitiesView(
        summarization_enabled=bool(feature_set.summarization and effective_config.summarization.enabled),
        plan_mode_enabled=bool(feature_set.plan_mode and effective_config.plan_mode.enabled),
        view_image_enabled=bool(feature_set.view_image),
        memory_enabled=bool(feature_set.memory),
        sandbox_mode=sandbox_mode if sandbox_mode in supported_modes else "unsupported",
        supported_sandbox_modes=supported_modes,
        guardrails_enabled=bool(feature_set.guardrails and effective_config.guardrails.enabled),
    )


def thread_state_to_view(
    state: ThreadState,
    *,
    path_service=None,
    artifact_refs: dict[str, list[ArtifactRefView]] | None = None,
    execution_policy: dict[str, object] | None = None,
    runtime_capabilities: RuntimeCapabilitiesView | None = None,
    subagent_tasks: list[SubagentTaskView] | None = None,
    process_sessions: list[ProcessSessionView] | None = None,
    state_scope: ThreadDetailStateScope = "full",
) -> ThreadStateView:
    include_full_state = state_scope == "full"
    artifact_refs = _resolve_thread_state_artifact_refs(
        artifact_refs,
        state,
        include_full_state=include_full_state,
    )
    execution_policy = execution_policy or {
        "approval_policy_summary": approval_policy_summary_for_state(state),
        "allowed_local_actions": allowed_local_actions_for_state(state),
        "requires_approval_actions": requires_approval_actions_for_state(state),
        "restricted_actions": restricted_actions_for_state(state),
        "pending_approval_reason": state.lifecycle.last_error if state.approvals.pending_approval is not None else None,
    }
    translate = (
        (lambda value: path_service.translate_runtime_data_to_virtual(value, thread_id=state.identity.thread_id))
        if path_service is not None
        else (lambda value: value)
    )
    runtime_path_roots = (
        [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in path_service.visible_runtime_roots(state.identity.thread_id)
        ]
        if include_full_state and path_service is not None
        else []
    )
    project_context_files = _project_context_files_to_view(
        state.prompt_snapshot.project_context_files,
        path_service=path_service,
        thread_id=state.identity.thread_id,
    ) if include_full_state else []
    tool_call_records = [tool_call_record_to_view(item) for item in state.execution.tool_calls] if include_full_state else []
    recent_tool_activity = [tool_activity_to_view(item) for item in state.execution.recent_tool_activity] if include_full_state else []
    recent_approval_events = [approval_event_to_view(item) for item in state.approvals.recent_approval_events] if include_full_state else []
    active_subagent_task_ids = [task.get("task_id", "") for task in state.delegation.active_subagent_tasks]
    runtime_operator_status = (
        build_runtime_operator_status_view(
            state,
            recent_tool_activity=recent_tool_activity,
            recent_approval_events=recent_approval_events,
            subagent_tasks=subagent_tasks or [],
            process_sessions=process_sessions or [],
        )
        if include_full_state
        else RuntimeOperatorStatusView()
    )
    return ThreadStateView(
        thread_id=state.identity.thread_id,
        run_id=state.identity.run_id,
        status=state.lifecycle.status.value,
        execution_mode=state.execution.execution_mode,
        is_plan_mode=state.execution.is_plan_mode,
        selected_model=state.execution.selected_model,
        selected_profile=state.execution.selected_profile,
        selected_reasoning_effort=state.execution.selected_reasoning_effort,
        effective_model=state.execution.active_model,
        title=state.conversation.title,
        summary=state.conversation.summary,
        todo_snapshot=[todo_snapshot_item_to_view(item) for item in state.planning.todo_snapshot] if include_full_state else [],
        archived_summaries=[archived_summary_to_view(item) for item in state.archived_summaries] if include_full_state else [],
        prompt_snapshot_id=state.prompt_snapshot.snapshot_id,
        prompt_snapshot_hash=state.prompt_snapshot.snapshot_hash,
        project_context_fingerprint=state.prompt_snapshot.project_context_fingerprint,
        project_context_files=project_context_files,
        active_model=state.execution.active_model,
        reasoning_effort=state.execution.reasoning_effort,
        token_usage=state.execution.token_usage,
        token_usage_summary=token_usage_summary_to_view(state.execution.token_usage),
        context_window_usage=context_window_usage_to_view(
            state.execution.context_window_usage,
            state.execution.runtime_assembly_snapshot,
        ),
        prompt_cache_diagnostics=prompt_cache_diagnostics_to_view(state.execution.runtime_assembly_snapshot),
        prompt_section_token_ledger=prompt_section_token_ledger_to_view(state.execution.runtime_assembly_snapshot),
        context_cache_diagnostics=context_cache_diagnostics_to_view(state.execution.runtime_assembly_snapshot),
        capability_assembly_diagnostics=capability_assembly_diagnostics_to_view(state.execution.runtime_assembly_snapshot),
        memory_injection_diagnostics=memory_injection_diagnostics_to_view(state.execution.runtime_assembly_snapshot),
        compaction_diagnostics=compaction_diagnostics_to_view(
            state.execution.runtime_assembly_snapshot,
            state.execution.context_window_usage,
        ),
        runtime_phase_timings=runtime_phase_timings_to_view(state.execution.runtime_phase_timings),
        last_message_interrupted=state.execution.last_message_interrupted,
        last_message_interrupted_reason=frontend_safe_interruption_reason(
            state.execution.last_message_interrupted_reason,
        ),
        approval_policy_summary=str(execution_policy["approval_policy_summary"]) if execution_policy.get("approval_policy_summary") is not None else None,
        allowed_local_actions=[str(item) for item in execution_policy.get("allowed_local_actions", [])] if include_full_state else [],
        requires_approval_actions=[str(item) for item in execution_policy.get("requires_approval_actions", [])],
        restricted_actions=[str(item) for item in execution_policy.get("restricted_actions", [])] if include_full_state else [],
        visible_tool_names=state.capabilities.visible_tool_names if include_full_state else [],
        deferred_tool_names=state.capabilities.deferred_tool_names if include_full_state else [],
        enabled_skill_ids=state.capabilities.enabled_skill_ids if include_full_state else [],
        memory_namespace=state.memory.memory_namespace,
        injected_memory_snapshot_id=state.memory.injected_memory_snapshot_id,
        has_pending_approval=state.approvals.pending_approval is not None,
        pending_approval_reason=str(execution_policy["pending_approval_reason"]) if execution_policy.get("pending_approval_reason") is not None else None,
        pending_user_interaction=user_interaction_to_view(state.conversation.pending_user_interaction),
        output_artifacts=translate(artifact_refs["outputs"]),
        uploaded_files=translate(artifact_refs["uploads"]),
        presented_artifacts=translate(artifact_refs["presented"]),
        workspace_mode=state.thread_data.workspace_mode or "thread",
        workspace_root=state.thread_data.workspace_root,
        resolved_workspace_path=state.thread_data.workspace_path,
        uploads_path=state.thread_data.uploads_path,
        outputs_path=state.thread_data.outputs_path,
        runtime_path_roots=runtime_path_roots,
        active_subagent_task_ids=active_subagent_task_ids,
        subagent_tasks=translate(subagent_tasks or []) if include_full_state else [],
        process_sessions=translate(process_sessions or []) if include_full_state else [],
        durable_subagent_job_history=translate([subagent_event_to_view(item) for item in state.durable_subagent_job_history]) if include_full_state else [],
        tool_calls=translate(tool_call_records) if include_full_state else [],
        recent_tool_activity=translate(recent_tool_activity) if include_full_state else [],
        recent_approval_events=translate(recent_approval_events) if include_full_state else [],
        runtime_operator_status=translate(runtime_operator_status) if include_full_state else runtime_operator_status,
        last_error=translate(frontend_safe_lifecycle_error(state.lifecycle.last_error)),
        runtime_capabilities=runtime_capabilities or RuntimeCapabilitiesView(),
        queued_followups=[
            queued_followup_to_view(item, path_service=path_service, thread_id=state.identity.thread_id)
            for item in state.conversation.queued_followups
            if isinstance(item, dict)
        ],
        active_followup_dispatch=queued_followup_dispatch_to_view(state.conversation.active_followup_dispatch),
    )


def frontend_safe_interruption_reason(reason: object) -> str | None:
    if not isinstance(reason, str) or not reason:
        return None
    if reason == EMPTY_FINAL_ASSISTANT_MESSAGE:
        return None
    return reason


def frontend_safe_lifecycle_error(error: object) -> str | None:
    if not isinstance(error, str) or not error:
        return None
    if error == EMPTY_FINAL_ASSISTANT_MESSAGE:
        return None
    return error


def frontend_safe_runtime_activity(activity: object) -> str | None:
    if not isinstance(activity, str) or not activity:
        return None
    if activity == EMPTY_FINAL_ASSISTANT_MESSAGE:
        return None
    return activity


def is_legacy_empty_final_diagnostic_text(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return value == EMPTY_FINAL_ASSISTANT_MESSAGE or value.startswith(EMPTY_FINAL_ASSISTANT_MESSAGE)


def runtime_phase_timings_to_view(payload: dict[str, object] | None) -> RuntimePhaseTimingsView | None:
    if not payload:
        return None
    try:
        return RuntimePhaseTimingsView.model_validate(payload)
    except Exception:
        return None


def archived_summary_to_view(item: object) -> ArchivedSummaryView:
    payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    payload = payload if isinstance(payload, dict) else {}
    diagnostics = payload.get("diagnostics")
    return ArchivedSummaryView(
        summary_id=_string_or_default(payload.get("summary_id"), ""),
        summary_text=_string_or_default(payload.get("summary_text"), ""),
        covers_turn_range=_int_list(payload.get("covers_turn_range")),
        token_count=_int_or_none(payload.get("token_count")) or 0,
        created_at=parse_datetime_or_none(payload.get("created_at")),
        prompt_snapshot_id=_string_or_default(payload.get("prompt_snapshot_id"), ""),
        compaction_level=_int_or_none(payload.get("compaction_level")) or 0,
        compaction_level_label=_optional_string(payload.get("compaction_level_label")),
        compaction_reason=_optional_string(payload.get("compaction_reason")),
        diagnostics=compaction_diagnostics_from_payload(diagnostics if isinstance(diagnostics, dict) else None),
    )


def todo_snapshot_item_to_view(item: object) -> TodoSnapshotItemView:
    payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    payload = payload if isinstance(payload, dict) else {}
    return TodoSnapshotItemView(
        id=_string_or_default(payload.get("id"), ""),
        content=_string_or_default(payload.get("content"), ""),
        status=_string_or_default(payload.get("status"), "pending"),
        created_at=_optional_string(payload.get("created_at")),
        depends_on=_string_list(payload.get("depends_on")),
    )


def token_usage_summary_to_view(payload: dict[str, object] | None) -> TokenUsageSummaryView | None:
    if not isinstance(payload, dict) or not payload:
        return None
    cost = payload.get("cost")
    cost_payload = cost if isinstance(cost, dict) else {}
    total_payload = payload.get("total")
    last_payload = payload.get("last")
    total = token_usage_breakdown_to_view(total_payload if isinstance(total_payload, dict) else payload)
    last = token_usage_breakdown_to_view(last_payload if isinstance(last_payload, dict) else None)
    provider_models = payload.get("provider_models")
    mapped = TokenUsageSummaryView(
        model=_optional_string(payload.get("model")),
        concrete_model=_optional_string(payload.get("concrete_model")),
        provider=_optional_string(payload.get("provider")),
        request_count=_int_or_none(payload.get("request_count")),
        input_tokens=_first_not_none(_int_or_none(payload.get("input_tokens")), total.input_tokens if total is not None else None),
        output_tokens=_first_not_none(_int_or_none(payload.get("output_tokens")), total.output_tokens if total is not None else None),
        total_tokens=_first_not_none(_int_or_none(payload.get("total_tokens")), total.total_tokens if total is not None else None),
        cache_read_tokens=_first_not_none(_int_or_none(payload.get("cache_read_tokens")), total.cache_read_tokens if total is not None else None),
        cache_write_tokens=_first_not_none(_int_or_none(payload.get("cache_write_tokens")), total.cache_write_tokens if total is not None else None),
        reasoning_tokens=_first_not_none(_int_or_none(payload.get("reasoning_tokens")), total.reasoning_tokens if total is not None else None),
        total=total,
        last=last,
        estimated_cost_usd=_first_not_none(_float_or_none(payload.get("estimated_cost_usd")), _float_or_none(cost_payload.get("estimated_cost_usd"))),
        cost_status=_optional_string(payload.get("cost_status")) or _optional_string(cost_payload.get("status")),
        currency=_optional_string(payload.get("currency")) or _optional_string(cost_payload.get("currency")),
        pricing_source=_optional_string(payload.get("pricing_source")) or _optional_string(cost_payload.get("source")),
        provider_models=[
            text
            for item in provider_models
            if (text := _optional_string(item)) is not None
        ] if isinstance(provider_models, list) else [],
    )
    if not any(
        (
            mapped.model,
            mapped.concrete_model,
            mapped.provider,
            mapped.request_count is not None,
            mapped.input_tokens is not None,
            mapped.output_tokens is not None,
            mapped.total_tokens is not None,
            mapped.cache_read_tokens is not None,
            mapped.cache_write_tokens is not None,
            mapped.reasoning_tokens is not None,
            mapped.total is not None,
            mapped.last is not None,
            mapped.estimated_cost_usd is not None,
            mapped.cost_status,
            mapped.currency,
            mapped.pricing_source,
            mapped.provider_models,
        )
    ):
        return None
    return mapped


def token_usage_breakdown_to_view(payload: dict[str, object] | None) -> TokenUsageBreakdownView | None:
    if not isinstance(payload, dict):
        return None
    mapped = TokenUsageBreakdownView(
        input_tokens=_int_or_none(payload.get("input_tokens")),
        output_tokens=_int_or_none(payload.get("output_tokens")),
        total_tokens=_int_or_none(payload.get("total_tokens")),
        cache_read_tokens=_int_or_none(payload.get("cache_read_tokens")),
        cache_write_tokens=_int_or_none(payload.get("cache_write_tokens")),
        reasoning_tokens=_int_or_none(payload.get("reasoning_tokens")),
    )
    if mapped.total_tokens is None:
        if mapped.input_tokens is not None and mapped.output_tokens is not None:
            mapped.total_tokens = mapped.input_tokens + mapped.output_tokens
        elif mapped.input_tokens is not None:
            mapped.total_tokens = mapped.input_tokens
        elif mapped.output_tokens is not None:
            mapped.total_tokens = mapped.output_tokens
    if not any(
        (
            mapped.input_tokens is not None,
            mapped.output_tokens is not None,
            mapped.total_tokens is not None,
            mapped.cache_read_tokens is not None,
            mapped.cache_write_tokens is not None,
            mapped.reasoning_tokens is not None,
        )
    ):
        return None
    return mapped


def context_window_usage_to_view(
    payload: dict[str, object] | None,
    runtime_assembly_snapshot: dict[str, object] | None = None,
) -> ContextWindowUsageView | None:
    if not isinstance(payload, dict) or not payload:
        return None
    sanitized = dict(payload)
    legacy_compaction_diagnostics = compaction_diagnostics_to_view(runtime_assembly_snapshot, payload)
    sanitized["compaction_diagnostics"] = (
        legacy_compaction_diagnostics.model_dump(mode="json", exclude_none=True)
        if legacy_compaction_diagnostics is not None
        else None
    )
    try:
        return ContextWindowUsageView.model_validate(sanitized)
    except Exception:
        return None


def prompt_cache_diagnostics_to_view(payload: dict[str, object] | None) -> PromptCacheDiagnosticsView | None:
    if not isinstance(payload, dict):
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, dict):
        return None
    cache_delta = prompt.get("cache_delta")
    cache = prompt.get("cache")
    if not isinstance(cache_delta, dict) and not isinstance(cache, dict):
        return None
    cache_delta = cache_delta if isinstance(cache_delta, dict) else {}
    cache = cache if isinstance(cache, dict) else {}
    return PromptCacheDiagnosticsView(
        hits=_int_or_none(cache_delta.get("hits")) or 0,
        misses=_int_or_none(cache_delta.get("misses")) or 0,
        writes=_int_or_none(cache_delta.get("writes")) or 0,
        evictions=_int_or_none(cache_delta.get("evictions")) or 0,
        bypasses=_int_or_none(cache_delta.get("bypasses")) or 0,
        size_before=_int_or_none(cache_delta.get("size_before")),
        size_after=_int_or_none(cache_delta.get("size_after")),
        net_size_change=_int_or_none(cache_delta.get("net_size_change")),
        max_entries=_int_or_none(cache_delta.get("max_entries")) or _int_or_none(cache.get("max_entries")),
        cumulative_hits=_int_or_none(cache.get("hits")),
        cumulative_misses=_int_or_none(cache.get("misses")),
        cumulative_writes=_int_or_none(cache.get("writes")),
        cumulative_evictions=_int_or_none(cache.get("evictions")),
        cumulative_bypasses=_int_or_none(cache.get("bypasses")),
        cumulative_size=_int_or_none(cache.get("size")),
    )


def prompt_section_token_ledger_to_view(payload: dict[str, object] | None) -> PromptSectionTokenLedgerView | None:
    if not isinstance(payload, dict):
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, dict):
        return None
    stable_sections = _int_mapping(prompt.get("stable_section_tokens"))
    volatile_sections = _int_mapping(prompt.get("volatile_section_tokens"))
    stable_total = _int_or_none(prompt.get("stable_prompt_tokens"))
    volatile_total = _int_or_none(prompt.get("volatile_prompt_tokens"))
    if stable_total is None and volatile_total is None and not stable_sections and not volatile_sections:
        return None
    return PromptSectionTokenLedgerView(
        stable_prompt_tokens=stable_total,
        volatile_prompt_tokens=volatile_total,
        stable_section_tokens=stable_sections,
        volatile_section_tokens=volatile_sections,
    )


def context_cache_diagnostics_to_view(payload: dict[str, object] | None) -> ContextCacheDiagnosticsView | None:
    if not isinstance(payload, dict):
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, dict):
        return None
    project_context_files = prompt.get("project_context_files")
    files = [item for item in project_context_files if isinstance(item, dict)] if isinstance(project_context_files, list) else []
    project_file_count = _int_or_none(prompt.get("project_context_file_count"))
    if project_file_count is None:
        project_file_count = len(files)
    project_truncated_count = _int_or_none(prompt.get("project_context_truncated_file_count"))
    if project_truncated_count is None:
        project_truncated_count = sum(1 for item in files if bool(item.get("truncated")))
    mapped = ContextCacheDiagnosticsView(
        project_context_cache_status=_optional_string(prompt.get("project_context_cache_status")),
        project_context_fingerprint=_optional_string(prompt.get("project_context_fingerprint")),
        project_context_file_count=project_file_count or 0,
        project_context_truncated_file_count=project_truncated_count or 0,
        project_context_total_chars=_int_or_none(prompt.get("project_context_total_chars")) or 0,
        project_context_discovery_scanned_path_count=_int_or_none(prompt.get("project_context_discovery_scanned_path_count")) or 0,
        project_context_discovery_max_scanned_paths=_int_or_none(prompt.get("project_context_discovery_max_scanned_paths")) or 0,
        project_context_discovery_scan_truncated=bool(prompt.get("project_context_discovery_scan_truncated")),
        project_context_scope_counts=_count_string_field(files, "scope"),
        project_context_applies_to_counts=_count_string_field(files, "applies_to"),
        runtime_path_cache_status=_optional_string(prompt.get("runtime_path_cache_status")),
        runtime_path_fingerprint=_optional_string(prompt.get("runtime_path_fingerprint")),
        runtime_path_root_count=_int_or_none(prompt.get("runtime_path_root_count")) or 0,
        runtime_path_host_bridge_count=_int_or_none(prompt.get("runtime_path_host_bridge_count")) or 0,
    )
    if not any(
        (
            mapped.project_context_cache_status,
            mapped.project_context_fingerprint,
            mapped.project_context_file_count,
            mapped.project_context_truncated_file_count,
            mapped.project_context_total_chars,
            mapped.project_context_discovery_scanned_path_count,
            mapped.project_context_discovery_max_scanned_paths,
            mapped.project_context_discovery_scan_truncated,
            mapped.project_context_scope_counts,
            mapped.project_context_applies_to_counts,
            mapped.runtime_path_cache_status,
            mapped.runtime_path_fingerprint,
            mapped.runtime_path_root_count,
            mapped.runtime_path_host_bridge_count,
        )
    ):
        return None
    return mapped


def capability_assembly_diagnostics_to_view(payload: dict[str, object] | None) -> CapabilityAssemblyDiagnosticsView | None:
    if not isinstance(payload, dict):
        return None
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    diagnostics = capabilities.get("assembly_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    mapped = CapabilityAssemblyDiagnosticsView(
        discovered_tool_count=_int_or_none(diagnostics.get("discovered_tool_count")) or 0,
        enabled_tool_count=_int_or_none(diagnostics.get("enabled_tool_count")) or 0,
        materialized_tool_count=_int_or_none(diagnostics.get("materialized_tool_count")) or 0,
        visible_tool_count=_int_or_none(diagnostics.get("visible_tool_count")) or 0,
        deferred_tool_count=_int_or_none(diagnostics.get("deferred_tool_count")) or 0,
        active_promotion_count=_int_or_none(diagnostics.get("active_promotion_count")) or 0,
        visible_schema_token_budget=_int_or_none(diagnostics.get("visible_schema_token_budget")),
        visible_schema_tokens=_int_or_none(diagnostics.get("visible_schema_tokens")) or 0,
        deferred_schema_tokens=_int_or_none(diagnostics.get("deferred_schema_tokens")) or 0,
        total_schema_tokens=_int_or_none(diagnostics.get("total_schema_tokens")) or 0,
        visible_schema_budget_remaining_tokens=_int_or_none(diagnostics.get("visible_schema_budget_remaining_tokens")),
        schema_compacted_tool_count=_int_or_none(diagnostics.get("schema_compacted_tool_count")) or 0,
        schema_deferred_tool_count=_int_or_none(diagnostics.get("schema_deferred_tool_count")) or 0,
        action_prefilter_deferred_tool_count=_int_or_none(diagnostics.get("action_prefilter_deferred_tool_count")) or 0,
        sanitizer_truncated_tool_count=_int_or_none(diagnostics.get("sanitizer_truncated_tool_count")) or 0,
        assembly_stage_durations_ms=_int_mapping(diagnostics.get("assembly_stage_durations_ms")),
        slowest_assembly_stage=str(diagnostics.get("slowest_assembly_stage") or "").strip() or None,
        slowest_assembly_stage_duration_ms=_int_or_none(diagnostics.get("slowest_assembly_stage_duration_ms")),
        skills_discovery_cache_hit=_optional_bool(diagnostics.get("skills_discovery_cache_hit")),
        skills_discovery_watch_enabled=_optional_bool(diagnostics.get("skills_discovery_watch_enabled")),
        skills_discovery_root_count=_int_or_none(diagnostics.get("skills_discovery_root_count")) or 0,
        skills_discovery_manifest_count=_int_or_none(diagnostics.get("skills_discovery_manifest_count")) or 0,
        skills_discovery_enabled_count=_int_or_none(diagnostics.get("skills_discovery_enabled_count")) or 0,
        skills_discovery_package_count=_int_or_none(diagnostics.get("skills_discovery_package_count")) or 0,
        skills_discovery_stage_durations_ms=_int_mapping(diagnostics.get("skills_discovery_stage_durations_ms")),
        slowest_skills_discovery_stage=str(diagnostics.get("slowest_skills_discovery_stage") or "").strip() or None,
        slowest_skills_discovery_stage_duration_ms=_int_or_none(diagnostics.get("slowest_skills_discovery_stage_duration_ms")),
        visible_by_source_kind=_int_mapping(diagnostics.get("visible_by_source_kind")),
        deferred_by_source_kind=_int_mapping(diagnostics.get("deferred_by_source_kind")),
        visible_by_group=_int_mapping(diagnostics.get("visible_by_group")),
        deferred_by_group=_int_mapping(diagnostics.get("deferred_by_group")),
    )
    if not any(
        (
            mapped.discovered_tool_count,
            mapped.enabled_tool_count,
            mapped.materialized_tool_count,
            mapped.visible_tool_count,
            mapped.deferred_tool_count,
            mapped.visible_schema_tokens,
            mapped.deferred_schema_tokens,
            mapped.total_schema_tokens,
            mapped.schema_compacted_tool_count,
            mapped.schema_deferred_tool_count,
            mapped.action_prefilter_deferred_tool_count,
            mapped.sanitizer_truncated_tool_count,
            mapped.skills_discovery_cache_hit is not None,
            mapped.skills_discovery_watch_enabled is not None,
            mapped.skills_discovery_root_count,
            mapped.skills_discovery_manifest_count,
            mapped.skills_discovery_enabled_count,
            mapped.skills_discovery_package_count,
            mapped.skills_discovery_stage_durations_ms,
            mapped.slowest_skills_discovery_stage,
            mapped.slowest_skills_discovery_stage_duration_ms,
            mapped.visible_by_source_kind,
            mapped.deferred_by_source_kind,
            mapped.visible_by_group,
            mapped.deferred_by_group,
        )
    ):
        return None
    return mapped


def memory_injection_diagnostics_to_view(payload: dict[str, object] | None) -> MemoryInjectionDiagnosticsView | None:
    if not isinstance(payload, dict):
        return None
    diagnostics = payload.get("memory_injection_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    mapped = MemoryInjectionDiagnosticsView(
        source=_string_or_default(diagnostics.get("source"), "none"),
        status=_string_or_default(diagnostics.get("status"), "not_used"),
        snapshot_id=_optional_string(diagnostics.get("snapshot_id")),
        query_tokens=_int_or_none(diagnostics.get("query_tokens")) or 0,
        curated_match_count=_int_or_none(diagnostics.get("curated_match_count")) or 0,
        archive_hit_count=_int_or_none(diagnostics.get("archive_hit_count")) or 0,
        evidence_count=_int_or_none(diagnostics.get("evidence_count")) or 0,
        provider_note_count=_int_or_none(diagnostics.get("provider_note_count")) or 0,
        summary_present=bool(diagnostics.get("summary_present")),
        rendered_tokens_before_truncation=_int_or_none(diagnostics.get("rendered_tokens_before_truncation")) or 0,
        rendered_tokens=_int_or_none(diagnostics.get("rendered_tokens")) or 0,
        token_budget=_int_or_none(diagnostics.get("token_budget")),
        truncated=bool(diagnostics.get("truncated")),
        error_type=_optional_string(diagnostics.get("error_type")),
        store_counts=_int_mapping(diagnostics.get("store_counts")),
        source_kind_counts=_int_mapping(diagnostics.get("source_kind_counts")),
    )
    if not any(
        (
            mapped.source != "none",
            mapped.status != "not_used",
            mapped.snapshot_id,
            mapped.query_tokens,
            mapped.curated_match_count,
            mapped.archive_hit_count,
            mapped.evidence_count,
            mapped.provider_note_count,
            mapped.summary_present,
            mapped.rendered_tokens_before_truncation,
            mapped.rendered_tokens,
            mapped.token_budget is not None,
            mapped.truncated,
            mapped.error_type,
            mapped.store_counts,
            mapped.source_kind_counts,
        )
    ):
        return None
    return mapped


def compaction_diagnostics_to_view(
    payload: dict[str, object] | None,
    context_window_usage: dict[str, object] | None = None,
) -> CompactionDiagnosticsView | None:
    diagnostics: dict[str, object] | None = None
    if isinstance(payload, dict):
        raw_diagnostics = payload.get("compaction_diagnostics")
        if isinstance(raw_diagnostics, dict):
            diagnostics = raw_diagnostics
    if diagnostics is None and isinstance(context_window_usage, dict):
        raw_diagnostics = context_window_usage.get("compaction_diagnostics")
        if isinstance(raw_diagnostics, dict):
            diagnostics = raw_diagnostics
    if not diagnostics:
        return None

    return compaction_diagnostics_from_payload(diagnostics)


def compaction_diagnostics_from_payload(diagnostics: dict[str, object] | None) -> CompactionDiagnosticsView | None:
    if not isinstance(diagnostics, dict) or not diagnostics:
        return None
    mapped = CompactionDiagnosticsView(
        compaction_level=_int_or_none(diagnostics.get("compaction_level")),
        compaction_level_label=_optional_string(diagnostics.get("compaction_level_label")),
        compaction_reason=_optional_string(diagnostics.get("compaction_reason")),
        summary_source=_optional_string(diagnostics.get("summary_source")),
        summary_model=_optional_string(diagnostics.get("summary_model")),
        summary_error_type=_optional_string(diagnostics.get("summary_error_type")),
        has_existing_summary=_optional_bool(diagnostics.get("has_existing_summary")),
        archived_message_count=_int_or_none(diagnostics.get("archived_message_count")) or 0,
        tool_call_count=_int_or_none(diagnostics.get("tool_call_count")) or 0,
        tool_result_count=_int_or_none(diagnostics.get("tool_result_count")) or 0,
        image_block_count=_int_or_none(diagnostics.get("image_block_count")) or 0,
        truncated_message_count=_int_or_none(diagnostics.get("truncated_message_count")) or 0,
        pruned_tool_result_count=_int_or_none(diagnostics.get("pruned_tool_result_count")) or 0,
        serialized_chars=_int_or_none(diagnostics.get("serialized_chars")),
        serialized_tokens=_int_or_none(diagnostics.get("serialized_tokens")),
        summary_prompt_tokens=_int_or_none(diagnostics.get("summary_prompt_tokens")),
        compaction_input_tokens=_int_or_none(diagnostics.get("compaction_input_tokens")),
        compaction_summary_tokens=_int_or_none(diagnostics.get("compaction_summary_tokens")),
        compaction_savings_tokens=_int_or_none(diagnostics.get("compaction_savings_tokens")),
        keep_recent_turns=_int_or_none(diagnostics.get("keep_recent_turns")),
    )
    if not any(
        (
            mapped.compaction_level is not None,
            mapped.compaction_level_label,
            mapped.compaction_reason,
            mapped.summary_source,
            mapped.summary_model,
            mapped.summary_error_type,
            mapped.has_existing_summary is not None,
            mapped.archived_message_count,
            mapped.tool_call_count,
            mapped.tool_result_count,
            mapped.image_block_count,
            mapped.truncated_message_count,
            mapped.pruned_tool_result_count,
            mapped.serialized_chars is not None,
            mapped.serialized_tokens is not None,
            mapped.summary_prompt_tokens is not None,
            mapped.compaction_input_tokens is not None,
            mapped.compaction_summary_tokens is not None,
            mapped.compaction_savings_tokens is not None,
            mapped.keep_recent_turns is not None,
        )
    ):
        return None
    return mapped


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_or_default(value: object, default: str) -> str:
    text = _optional_string(value)
    return text if text is not None else default


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _int_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, item in value.items():
        number = _int_or_none(item)
        if number is None:
            continue
        result[str(key)] = number
    return result


def _int_list(value: object) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[int] = []
    for item in value:
        number = _int_or_none(item)
        if number is None:
            continue
        result.append(number)
    return result


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _optional_string(item)
        if text is None:
            continue
        result.append(text)
    return result


def _count_string_field(items: list[dict[str, object]], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = _optional_string(item.get(field_name))
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _first_not_none[T](first: T | None, fallback: T | None) -> T | None:
    return first if first is not None else fallback


def _default_thread_state_artifact_refs(
    state: ThreadState,
    *,
    include_full_state: bool,
) -> dict[str, list[ArtifactRefView]]:
    if not include_full_state:
        return {"uploads": [], "outputs": [], "presented": []}
    return {
        "uploads": build_upload_artifact_refs(state),
        "outputs": build_output_artifact_refs(state),
        "presented": build_presented_artifact_refs(state),
    }


def _resolve_thread_state_artifact_refs(
    artifact_refs: dict[str, list[ArtifactRefView]] | None,
    state: ThreadState,
    *,
    include_full_state: bool,
) -> dict[str, list[ArtifactRefView]]:
    if artifact_refs is None:
        return _default_thread_state_artifact_refs(
            state,
            include_full_state=include_full_state,
        )
    return {
        "uploads": list(artifact_refs.get("uploads") or []),
        "outputs": list(artifact_refs.get("outputs") or []),
        "presented": list(artifact_refs.get("presented") or []),
    }


def _project_context_files_to_view(items: list[dict[str, object]], *, path_service, thread_id: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in items:
        payload = dict(item)
        raw_virtual_path = str(payload.get("virtual_path") or "")
        virtual_path = raw_virtual_path
        if path_service is not None:
            try:
                virtual_path = path_service.to_virtual_path(thread_id, raw_virtual_path)
            except Exception:
                translated = path_service.translate_runtime_text_to_virtual(raw_virtual_path, thread_id=thread_id)
                if translated and translated != raw_virtual_path:
                    virtual_path = translated
        if not virtual_path.startswith("/mnt/"):
            relative_path = str(payload.get("relative_path") or "").replace("\\", "/").strip("/")
            if relative_path:
                virtual_path = (PurePosixPath("/mnt/user-data/workspace") / relative_path).as_posix()
        if not virtual_path.startswith("/mnt/"):
            normalized_value = raw_virtual_path.replace("\\", "/")
            marker = "/workspace/"
            marker_index = normalized_value.lower().rfind(marker)
            if marker_index >= 0:
                relative_path = normalized_value[marker_index + len(marker) :].strip("/")
                if relative_path:
                    virtual_path = (PurePosixPath("/mnt/user-data/workspace") / relative_path).as_posix()
        payload["virtual_path"] = virtual_path
        result.append(payload)
    return result


def thread_detail_to_view(
    state: ThreadState,
    *,
    path_service=None,
    artifact_refs: dict[str, list[ArtifactRefView]] | None = None,
    execution_policy: dict[str, object] | None = None,
    runtime_capabilities: RuntimeCapabilitiesView | None = None,
    subagent_tasks: list[SubagentTaskView] | None = None,
    process_sessions: list[ProcessSessionView] | None = None,
    message_offset: int | None = None,
    message_limit: int | None = None,
    state_scope: ThreadDetailStateScope = "chat",
) -> ThreadDetailView:
    include_full_state = state_scope == "full"
    visible_message_payloads = [
        payload
        for payload in state.conversation.messages
        if not is_internal_loop_guard_message(payload) and not is_model_only_message(payload)
    ]
    windowed_message_payloads, message_window = slice_message_payload_window(
        visible_message_payloads,
        message_offset=message_offset,
        message_limit=message_limit,
    )
    window_message_ids = {
        str(payload.get("id"))
        for payload in windowed_message_payloads
        if payload.get("id") is not None
    }
    window_tool_call_ids = {
        str(item.get("id"))
        for payload in windowed_message_payloads
        for item in (payload.get("tool_calls") if isinstance(payload.get("tool_calls"), list) else [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    windowed_lookup_payloads = [
        payload
        for payload in windowed_message_payloads
        if str(payload.get("role") or "") != "tool"
        or str(payload.get("tool_call_id") or "") in window_tool_call_ids
    ]
    tool_result_lookup = build_tool_result_lookup(windowed_lookup_payloads)
    windowed_steps = [
        step
        for step in state.conversation.steps
        if isinstance(step, dict)
        and step.get("message_id") is not None
        and str(step.get("message_id")) in window_message_ids
    ]
    steps_by_message = build_message_steps_lookup(windowed_steps)
    windowed_activity_items = [
        item
        for item in state.execution.recent_tool_activity
        if _tool_activity_in_message_window(item, window_message_ids=window_message_ids, window_tool_call_ids=window_tool_call_ids)
    ]
    tool_activity_lookup = build_tool_activity_lookup(
        state.execution.recent_tool_activity if include_full_state else windowed_activity_items
    )
    if artifact_refs is None:
        artifact_refs = (
            {
                "uploads": build_upload_artifact_refs(state),
                "outputs": build_output_artifact_refs(state),
                "presented": build_presented_artifact_refs(state),
            }
            if include_full_state
            else build_window_artifact_refs(state, windowed_message_payloads=windowed_message_payloads)
        )
    messages = [
        thread_message_to_view(
            payload,
            message_index=message_window.offset + index,
            path_service=path_service,
            thread_id=state.identity.thread_id,
            tool_result_lookup=tool_result_lookup,
            tool_activity_lookup=tool_activity_lookup,
            artifact_refs=build_message_artifact_refs(
                payload,
                upload_refs=artifact_refs["uploads"],
                output_refs=artifact_refs["outputs"],
            ),
            pending_approval=state.approvals.pending_approval is not None
            and message_window.offset + index == len(visible_message_payloads) - 1,
            approval_request=state.approvals.approval_request.model_dump(mode="json")
            if state.approvals.approval_request is not None
            else None,
            steps=steps_by_message.get(str(payload.get("id"))) if payload.get("id") is not None else None,
        )
        for index, payload in enumerate(windowed_message_payloads)
    ]
    message_window = message_window.model_copy(
        update={
            "returned": len(messages),
            "start_message_id": messages[0].message_id if messages else None,
            "end_message_id": messages[-1].message_id if messages else None,
        }
    )
    metadata = thread_metadata_to_view(ThreadMetadataView.from_thread_state(state))
    pending_approval = approval_to_view(
        state.approvals.pending_approval.value if state.approvals.pending_approval is not None else None,
        state.approvals.approval_request.model_dump(mode="json")
        if state.approvals.approval_request is not None
        else None,
    )
    return ThreadDetailView(
        thread=metadata,
        state=thread_state_to_view(
            state,
            path_service=path_service,
            artifact_refs=artifact_refs,
            execution_policy=execution_policy,
            runtime_capabilities=runtime_capabilities,
            subagent_tasks=subagent_tasks,
            process_sessions=process_sessions,
            state_scope=state_scope,
        ),
        messages=messages,
        message_window=message_window,
        pending_approval=pending_approval,
        pending_user_interaction=user_interaction_to_view(state.conversation.pending_user_interaction),
        stream_capabilities=StreamCapabilitiesView(
            supports_step_chain=True,
            supports_message_delta=False,
            supports_reasoning_delta=False,
            supports_structured_events=True,
        ),
    )


def run_result_to_view(result: RunResult) -> RunCompletedView:
    assistant_message = None
    for message in reversed(result.thread_state.conversation.messages):
        if message.get("role") == "ai" and isinstance(message.get("content"), str):
            assistant_message = message["content"]
            break

    metadata = thread_metadata_to_view(result.metadata_view)
    assistant_message = result.runtime.context.path_service.translate_runtime_text_to_virtual(
        assistant_message,
        thread_id=result.thread_state.identity.thread_id,
    )
    subagent_tasks = []
    if result.runtime.context.subagent_service is not None:
        subagent_tasks = [
            subagent_task_to_view_from_runtime(
                result.runtime.context.subagent_service,
                path_service=result.runtime.context.path_service,
                parent_thread_id=result.thread_state.identity.thread_id,
                task_id=task.task_id,
                durable_history=result.thread_state.durable_subagent_job_history,
            )
            for task in result.runtime.context.subagent_service.list_tasks(parent_thread_id=result.thread_state.identity.thread_id)
        ]
    state = thread_state_to_view(
        result.thread_state,
        path_service=result.runtime.context.path_service,
        artifact_refs=build_canonical_artifact_refs_for_path_service(
            result.thread_state,
            result.runtime.context.path_service,
        ),
        subagent_tasks=subagent_tasks,
        execution_policy={
            "approval_policy_summary": approval_policy_summary_for_state(result.thread_state),
            "allowed_local_actions": allowed_local_actions_for_state(result.thread_state),
            "requires_approval_actions": requires_approval_actions_for_state(result.thread_state),
            "restricted_actions": restricted_actions_for_state(result.thread_state),
            "pending_approval_reason": result.thread_state.lifecycle.last_error if result.thread_state.approvals.pending_approval is not None else None,
        },
        runtime_capabilities=RuntimeCapabilitiesView(
            summarization_enabled=bool(getattr(result.runtime.feature_set, "summarization", False)),
            plan_mode_enabled=bool(getattr(result.runtime.context, "is_plan_mode", False)),
            view_image_enabled=bool(getattr(result.runtime.feature_set, "view_image", False)),
            memory_enabled=bool(getattr(result.runtime.feature_set, "memory", False)),
            skills_count=len(result.runtime.capability_bundle.enabled_skill_ids),
            mcp_servers_connected=len(result.runtime.capability_bundle.effective_mcp_servers),
            sandbox_mode=(result.runtime.context.sandbox_handle.provider_mode if result.runtime.context.sandbox_handle is not None else "unsupported"),
            supported_sandbox_modes=["host_isolated", "isolated"],
            isolated_sandbox_supported=shutil.which("docker") is not None,
            guardrails_enabled=bool(getattr(result.runtime.feature_set, "guardrails", False)),
        ),
    )
    return RunCompletedView(
        thread_id=result.thread_state.identity.thread_id,
        status=result.thread_state.lifecycle.status.value,
        assistant_message=assistant_message,
        last_error=result.thread_state.lifecycle.last_error,
        thread=metadata,
        state=state,
    )


def run_completed_stream_payload(
    result: RunResult,
    *,
    event_data: dict[str, object],
    known_system_version: int,
) -> dict[str, object]:
    payload = {
        **run_result_to_view(result).model_dump(mode="json"),
        "known_system_version": known_system_version,
    }
    for key in ("run_id", "execution_mode", "stream_status"):
        if event_data.get(key) is not None:
            payload[key] = event_data[key]
    for key in ("event_id", "sequence", "message_id", "block_id", "visibility", "event_log_cursor"):
        if event_data.get(key) is not None:
            payload[key] = event_data[key]
    return payload


def stream_event_payload(
    deps: AppRuntimeDeps,
    thread_id: str,
    data: dict[str, object],
) -> dict[str, object]:
    return deps.path_service.translate_runtime_data_to_virtual(
        {**data, "known_system_version": deps.system_event_bus.current_version()},
        thread_id=thread_id,
    )


def runtime_unavailable_error(
    deps: AppRuntimeDeps,
    thread_id: str,
    exc: Exception,
) -> GatewayAdapterError:
    detail = deps.path_service.translate_runtime_text_to_virtual(str(exc), thread_id=thread_id)
    return GatewayAdapterError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "runtime_unavailable",
        detail or str(exc),
        kind=exc.__class__.__name__,
    )


def thread_settings_to_view(state: ThreadState, *, path_service=None) -> ThreadSettingsView:
    profile_name = resolve_anvil_profile_name()
    profile_home = resolve_anvil_profile_home(profile_name)
    runtime_path_roots = (
        [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in path_service.visible_runtime_roots(state.identity.thread_id)
        ]
        if path_service is not None
        else []
    )
    return ThreadSettingsView(
        thread_id=state.identity.thread_id,
        execution_mode=state.execution.execution_mode,
        selected_model=state.execution.selected_model,
        selected_profile=state.execution.selected_profile,
        selected_reasoning_effort=state.execution.selected_reasoning_effort,
        is_plan_mode=state.execution.is_plan_mode,
        workspace_root=state.thread_data.workspace_root,
        workspace_mode=state.thread_data.workspace_mode or "thread",
        anvil_home=str(default_anvil_config_dir()),
        anvil_profile=profile_name,
        anvil_profile_home=str(profile_home),
        resolved_workspace_path=state.thread_data.workspace_path,
        runtime_path_roots=runtime_path_roots,
    )


def flush_memory_captures(result: RunResult) -> None:
    memory_service = getattr(result.runtime.context, "memory_service", None)
    if memory_service is None:
        return

    namespace = result.runtime.context.memory_namespace or "global/default"
    try:
        memory_service.process_pending(namespace)
    except Exception:
        return


def schedule_memory_capture_flush(deps: AppRuntimeDeps, result: RunResult) -> None:
    memory_service = getattr(result.runtime.context, "memory_service", None)
    if memory_service is None:
        return

    namespace = result.runtime.context.memory_namespace or "global/default"
    deps.run_engine.submit_background_task(
        "legacy-memory-flush",
        lambda: memory_service.process_pending(namespace),
    )


def thread_message_to_view(
    payload: dict[str, object],
    *,
    message_index: int,
    path_service=None,
    thread_id: str | None = None,
    tool_result_lookup: dict[str, str] | None = None,
    tool_activity_lookup: dict[str, ToolActivityView] | None = None,
    artifact_refs: list[ArtifactRefView] | None = None,
    pending_approval: bool = False,
    approval_request: dict[str, object] | None = None,
    steps: list[dict[str, object]] | None = None,
) -> MessageView:
    role = str(payload.get("role", "unknown"))
    translate = (
        (lambda value: path_service.translate_runtime_data_to_virtual(value, thread_id=thread_id))
        if path_service is not None
        else (lambda value: value)
    )
    display_translate = (
        (lambda value: path_service.translate_runtime_data_to_display(value, thread_id=thread_id))
        if path_service is not None
        else translate
    )
    raw_blocks = payload.get("content_blocks")
    content_blocks: list[MessageContentBlockView] = []
    if isinstance(raw_blocks, list):
        content_blocks = [
            MessageContentBlockView(
                type=str(item.get("type", "text")),
                text=str(item.get("text", "")),
                url=_content_block_url(item),
                mime_type=str(item.get("mime_type")) if item.get("mime_type") is not None else None,
                name=str(item.get("name")) if item.get("name") is not None else None,
                artifact_url=str(item.get("artifact_url")) if item.get("artifact_url") is not None else None,
                metadata=dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {},
            )
            for item in raw_blocks
            if isinstance(item, dict)
        ]

    reasoning_texts = [block.text for block in content_blocks if block.type == "thinking" and block.text]
    reasoning = (
        ReasoningView(
            text="\n\n".join(reasoning_texts),
            block_count=len(reasoning_texts),
            duration_ms=int(payload["reasoning_duration_ms"]) if isinstance(payload.get("reasoning_duration_ms"), int) else None,
        )
        if reasoning_texts
        else None
    )

    tool_calls: list[ToolCallView] = []
    raw_tool_calls = payload.get("tool_calls")
    message_id = str(payload.get("id")) if payload.get("id") is not None else None
    if isinstance(raw_tool_calls, list):
        for item in raw_tool_calls:
            if not isinstance(item, dict):
                continue
            item_tool_call_id = str(item.get("id")) if item.get("id") is not None else None
            activity = lookup_tool_activity(
                tool_activity_lookup,
                tool_call_id=item_tool_call_id,
                message_id=message_id,
                name=str(item.get("name")) if item.get("name") is not None else None,
            )
            lookup_result = (
                tool_result_lookup.get(item_tool_call_id)
                if tool_result_lookup is not None and item_tool_call_id is not None
                else None
            )
            result_text = lookup_result if lookup_result is not None else activity.result_text if activity is not None else None
            tool_calls.append(
                ToolCallView(
                    tool_call_id=item_tool_call_id,
                    name=str(item.get("name")) if item.get("name") is not None else None,
                    display_name=activity.display_name if activity is not None else None,
                    source_kind=activity.source_kind if activity is not None else None,
                    source_id=activity.source_id if activity is not None else None,
                    capability_group=activity.capability_group if activity is not None else None,
                    tool_execution_mode=activity.tool_execution_mode if activity is not None else None,
                    args=translate(item.get("args") if isinstance(item.get("args"), dict) else {}),
                    status=activity.status if activity is not None else None,
                    result_text=translate(result_text),
                    started_at=activity.started_at if activity is not None else None,
                    completed_at=activity.completed_at if activity is not None else None,
                    duration_ms=activity.duration_ms if activity is not None else None,
                    input=translate(item.get("args") if isinstance(item.get("args"), dict) else {}),
                    output=translate(result_text),
                    is_error=str(activity.status if activity is not None else "").lower() in {"error", "failed", "failure"},
                    visibility="chat",
                )
            )

    if payload.get("role") == "tool":
        activity = lookup_tool_activity(
            tool_activity_lookup,
            tool_call_id=str(payload.get("tool_call_id")) if payload.get("tool_call_id") is not None else None,
            message_id=message_id,
            name=str(payload.get("name")) if payload.get("name") is not None else None,
        )
        tool_calls.append(
            ToolCallView(
                tool_call_id=str(payload.get("tool_call_id")) if payload.get("tool_call_id") is not None else None,
                name=str(payload.get("name")) if payload.get("name") is not None else None,
                display_name=activity.display_name if activity is not None else None,
                source_kind=activity.source_kind if activity is not None else None,
                source_id=activity.source_id if activity is not None else None,
                capability_group=activity.capability_group if activity is not None else None,
                tool_execution_mode=activity.tool_execution_mode if activity is not None else None,
                status=activity.status if activity is not None else str(payload.get("status")) if payload.get("status") is not None else None,
                result_text=translate(str(payload.get("content", ""))),
                started_at=activity.started_at if activity is not None else None,
                completed_at=activity.completed_at if activity is not None else None,
                duration_ms=activity.duration_ms if activity is not None else None,
                output=translate(str(payload.get("content", ""))),
                is_error=str(activity.status if activity is not None else payload.get("status") or "").lower() in {"error", "failed", "failure"},
                visibility="chat",
            )
        )

    approval = approval_to_view(
        "needs_user_approval" if pending_approval else None,
        approval_request,
    )
    rendered_steps = message_steps_to_views(
        steps or [],
        payload=payload,
        message_id=message_id or f"message-{message_index}",
        reasoning=reasoning,
        tool_calls=tool_calls,
        translate=translate,
    )
    raw_content = str(payload.get("content", ""))
    content = display_translate(raw_content) if role in {"human", "user"} else translate(strip_inline_thinking_tags(raw_content))
    if role in {"ai", "assistant"}:
        if is_legacy_empty_final_diagnostic_text(content):
            content = ""
        content_step_payloads = [
            step.payload
            for step in rendered_steps
            if step.type == "content" and step.payload and step.visibility == "chat"
        ]
        if content_step_payloads:
            content = content_step_payloads[-1]
        elif is_delegation_orchestration_text(content):
            content = ""

    stream_status = message_stream_status(
        payload=payload,
        rendered_steps=rendered_steps,
        pending_approval=pending_approval,
    )
    client_message_id = message_client_message_id(payload)

    return MessageView(
        message_id=message_id or f"message-{message_index}",
        client_message_id=client_message_id,
        role=role,
        content=content,
        steps=rendered_steps,
        content_blocks=translate(content_blocks),
        reasoning=reasoning,
        tool_calls=tool_calls,
        tool_call_id=str(payload.get("tool_call_id")) if payload.get("tool_call_id") is not None else None,
        name=str(payload.get("name")) if payload.get("name") is not None else None,
        status=str(payload.get("status")) if payload.get("status") is not None else None,
        stream_status=stream_status,
        artifact_refs=translate(artifact_refs or []),
        approval=approval,
    )


def message_client_message_id(payload: dict[str, object]) -> str | None:
    additional_kwargs = payload.get("additional_kwargs")
    if not isinstance(additional_kwargs, dict):
        return None
    value = additional_kwargs.get("client_message_id")
    return str(value) if isinstance(value, str) and value else None


def message_stream_status(
    *,
    payload: dict[str, object],
    rendered_steps: list[MessageStepView],
    pending_approval: bool,
) -> str | None:
    raw_status = str(payload.get("status") or "")
    if raw_status == "interrupted":
        return "interrupted"
    role = str(payload.get("role", ""))
    if role not in {"ai", "assistant"} or pending_approval:
        return None
    visible_content_steps = [
        step
        for step in rendered_steps
        if step.type == "content" and step.visibility == "chat" and step.status in {"success", "error"}
    ]
    if visible_content_steps:
        return "complete"
    content = str(payload.get("content") or "").strip()
    if content and not payload.get("tool_calls") and not is_delegation_orchestration_text(content):
        return "complete"
    return None


def is_internal_loop_guard_message(payload: dict[str, object]) -> bool:
    role = str(payload.get("role", ""))
    content = payload.get("content")
    return role in {"user", "human"} and isinstance(content, str) and content.strip().startswith("[LOOP DETECTED]")


def is_delegation_orchestration_text(content: object) -> bool:
    if not isinstance(content, str):
        return False
    text = content.strip()
    if not text:
        return False
    markers = (
        "batch 格式",
        "Agent 已成功启动",
        "已成功启动",
        "让我等待",
        "让我先列出当前活跃",
        "需要指定 task_id",
        "现在开始并行委托",
        "改用单独委托",
    )
    return any(marker in text for marker in markers)


def slice_message_payload_window(
    payloads: list[dict[str, object]],
    *,
    message_offset: int | None,
    message_limit: int | None,
) -> tuple[list[dict[str, object]], MessageWindowView]:
    total = len(payloads)
    if message_limit is None:
        return payloads, MessageWindowView(total=total, offset=0, limit=None, returned=total)

    limit = max(1, min(int(message_limit), 500))
    if message_offset is None:
        offset = max(total - limit, 0)
    else:
        offset = min(max(int(message_offset), 0), total)
    end = min(offset + limit, total)
    windowed = payloads[offset:end]
    return windowed, MessageWindowView(
        total=total,
        offset=offset,
        limit=limit,
        returned=len(windowed),
        has_more_before=offset > 0,
        has_more_after=end < total,
        truncated=offset > 0 or end < total,
    )


def build_message_steps_lookup(steps: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    lookup: dict[str, list[dict[str, object]]] = {}
    for item in steps:
        if not isinstance(item, dict):
            continue
        message_id = item.get("message_id")
        if message_id is None:
            continue
        lookup.setdefault(str(message_id), []).append(item)
    for items in lookup.values():
        items.sort(key=lambda item: int(item.get("order")) if isinstance(item.get("order"), int) else 0)
    return lookup


def message_steps_to_views(
    steps: list[dict[str, object]],
    *,
    payload: dict[str, object],
    message_id: str,
    reasoning: ReasoningView | None,
    tool_calls: list[ToolCallView],
    translate,
) -> list[MessageStepView]:
    internal_delegation_tool_names = {"delegated_task", "delegate_batch", "delegate_status", "delegate_cancel", "subagent"}
    if steps:
        tool_call_lookup = {
            key: tool_call
            for tool_call in tool_calls
            for key in (tool_call.tool_call_id, tool_call.name)
            if key
        }
        rendered: list[MessageStepView] = []
        for index, item in enumerate(steps):
            metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
            step_payload = translate(str(item.get("payload") or ""))
            step_error = translate(str(item["error"])) if item.get("error") is not None else None
            view = MessageStepView(
                step_id=str(item.get("step_id") or f"{message_id}:step:{index}"),
                message_id=str(item.get("message_id") or message_id),
                type=normalize_step_type(item.get("type")),
                title=str(item.get("title") or fallback_step_title(item.get("type"))),
                action=translate(str(item["action"])) if item.get("action") is not None else None,
                status=normalize_step_status(item.get("status")),
                duration=str(item["duration"]) if item.get("duration") is not None else None,
                duration_ms=int(item["duration_ms"]) if isinstance(item.get("duration_ms"), int) else None,
                payload=_sanitize_step_payload(
                    step_type=normalize_step_type(item.get("type")),
                    payload=step_payload,
                ),
                language=normalize_step_language(item.get("language")),
                tool_name=str(item["tool_name"]) if item.get("tool_name") is not None else None,
                tool_call_id=str(item["tool_call_id"]) if item.get("tool_call_id") is not None else None,
                order=int(item["order"]) if isinstance(item.get("order"), int) else index,
                started_at=parse_datetime_or_none(item.get("started_at")),
                completed_at=parse_datetime_or_none(item.get("completed_at")),
                error=step_error,
                metadata=metadata,
                visibility=str(item.get("visibility") or "chat"),
                block_id=str(item.get("block_id") or metadata.get("block_id") or item.get("step_id") or f"{message_id}:step:{index}"),
                sequence=_int_or_none(item.get("sequence") if item.get("sequence") is not None else metadata.get("sequence")),
            )
            if view.type == "content" and (is_legacy_empty_final_diagnostic_text(step_payload) or is_legacy_empty_final_diagnostic_text(step_error)):
                view = view.model_copy(update={"visibility": "hidden", "payload": "", "error": "interrupted"})
            if _should_hide_legacy_orchestration_step(view, internal_delegation_tool_names):
                view = view.model_copy(update={"visibility": "hidden"})
            if view.type == "call":
                related_tool_call = (
                    tool_call_lookup.get(view.tool_call_id or "")
                    or tool_call_lookup.get(view.tool_name or "")
                )
                if related_tool_call is not None:
                    merged_status = _step_status_from_tool_call(related_tool_call)
                    view = view.model_copy(
                        update={
                            "status": merged_status,
                            "payload": view.payload or translate(related_tool_call.result_text or ""),
                            "duration_ms": view.duration_ms if view.duration_ms is not None else related_tool_call.duration_ms,
                            "started_at": view.started_at or related_tool_call.started_at,
                            "completed_at": view.completed_at or related_tool_call.completed_at,
                            "error": view.error or (translate(related_tool_call.result_text or "") if merged_status == "error" else None),
                        }
                    )
            rendered.append(view)
        return rendered

    synthesized: list[MessageStepView] = []
    order = 0
    content = strip_inline_thinking_tags(str(payload.get("content") or ""))
    role = str(payload.get("role") or "")
    tool_names = {str(tool_call.name) for tool_call in tool_calls if tool_call.name}
    has_internal_delegation_tool = bool(tool_names.intersection(internal_delegation_tool_names))
    if reasoning is not None:
        synthesized.append(
            MessageStepView(
                step_id=f"{message_id}:thinking",
                message_id=message_id,
                type="thinking",
                title="已思考",
                status="success",
                duration_ms=reasoning.duration_ms,
                payload=translate(reasoning.text),
                language="text",
                order=order,
                metadata={},
                visibility="chat",
            )
        )
        order += 1
    planning_text_is_internal = bool(tool_calls) and role in {"ai", "assistant"} and bool(content)
    if planning_text_is_internal and (reasoning is None or content not in reasoning.text):
        synthesized.append(
            MessageStepView(
                step_id=f"{message_id}:thinking:tool-plan",
                message_id=message_id,
                type="thinking",
                title="已思考",
                status="success",
                payload=translate(strip_inline_thinking_tags(content)),
                language="text",
                order=order,
                metadata={},
                visibility="hidden" if has_internal_delegation_tool or is_delegation_orchestration_text(content) or tool_calls else "chat",
            )
        )
        order += 1
    for index, tool_call in enumerate(tool_calls):
        synthesized.append(
            MessageStepView(
                step_id=f"{message_id}:call:{tool_call.tool_call_id or tool_call.name or index}",
                message_id=message_id,
                type="call",
                title=f"已运行 {tool_call.display_name or tool_call.name or 'tool'}",
                action=translate(json.dumps(tool_call.args or {}, ensure_ascii=False, indent=2, sort_keys=True)),
                status=_step_status_from_tool_call(tool_call),
                duration_ms=tool_call.duration_ms,
                payload=translate(tool_call.result_text or ""),
                language="json",
                tool_name=tool_call.name,
                tool_call_id=tool_call.tool_call_id,
                order=order,
                started_at=tool_call.started_at,
                completed_at=tool_call.completed_at,
                error=translate(tool_call.result_text or "") if tool_call.status == "error" else None,
                metadata={},
                visibility="hidden" if tool_call.name in internal_delegation_tool_names else "chat",
            )
        )
        order += 1
    if content and role in {"ai", "assistant"} and not planning_text_is_internal:
        internal_orchestration = is_delegation_orchestration_text(content)
        synthesized.append(
            MessageStepView(
                step_id=f"{message_id}:content" if not internal_orchestration else f"{message_id}:thinking:delegation",
                message_id=message_id,
                type="content" if not internal_orchestration else "thinking",
                title="最终回答" if not internal_orchestration else "已处理委托编排",
                status="error" if str(payload.get("status")) == "interrupted" else "success",
                payload=translate(content),
                language="markdown" if not internal_orchestration else "text",
                order=order,
                metadata={},
                visibility="chat" if not internal_orchestration else "hidden",
            )
        )
    return synthesized


def _should_hide_legacy_orchestration_step(view: MessageStepView, internal_delegation_tool_names: set[str]) -> bool:
    if view.visibility != "chat":
        return False
    is_subagent_lifecycle_step = bool(view.metadata.get("subagent_task_id"))
    if (
        view.type == "call"
        and view.tool_name in internal_delegation_tool_names
        and not is_subagent_lifecycle_step
    ):
        return True
    if view.type in {"thinking", "content"} and is_delegation_orchestration_text(view.payload):
        return True
    return False


def _sanitize_step_payload(*, step_type: str, payload: object) -> str:
    text = str(payload or "")
    if step_type == "content":
        return strip_inline_thinking_tags(text)
    return text.replace("<think>", "").replace("</think>", "").strip()


def _step_status_from_tool_call(tool_call: ToolCallView) -> str:
    raw_status = str(tool_call.status or "").lower()
    if raw_status in {"error", "failed", "failure"}:
        return "error"
    if raw_status in {"success", "completed", "complete"} or tool_call.completed_at is not None or tool_call.result_text:
        return "success"
    return "running"


def normalize_step_type(value: object) -> str:
    step_type = str(value or "content")
    return step_type if step_type in {"thinking", "call", "content"} else "content"


def normalize_step_status(value: object) -> str:
    status = str(value or "success")
    if status in {"pending", "running", "success", "error"}:
        return status
    return "success" if status in {"completed", "complete"} else "running"


def normalize_step_language(value: object) -> str:
    language = str(value or "text")
    return language if language in {"shell", "json", "markdown", "text"} else "text"


def fallback_step_title(value: object) -> str:
    step_type = normalize_step_type(value)
    if step_type == "thinking":
        return "Analyzing..."
    if step_type == "call":
        return "已运行工具"
    return "最终回答"


def parse_datetime_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def build_tool_result_lookup(messages: list[dict[str, object]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for payload in messages:
        if payload.get("role") != "tool":
            continue
        tool_call_id = payload.get("tool_call_id")
        if tool_call_id is None:
            continue
        lookup[str(tool_call_id)] = str(payload.get("content", ""))
    return lookup


def build_tool_activity_lookup(items: list[object]) -> dict[str, ToolActivityView]:
    lookup: dict[str, ToolActivityView] = {}
    for item in items:
        activity = tool_activity_to_view(item)
        if activity.tool_call_id is not None:
            lookup[activity.tool_call_id] = activity
        if activity.message_id is not None and activity.name is not None:
            lookup[f"{activity.message_id}:{activity.name}"] = activity
    return lookup


def lookup_tool_activity(
    lookup: dict[str, ToolActivityView] | None,
    *,
    tool_call_id: str | None,
    message_id: str | None,
    name: str | None,
) -> ToolActivityView | None:
    if lookup is None:
        return None
    if tool_call_id is not None and tool_call_id in lookup:
        return lookup[tool_call_id]
    if message_id is not None and name is not None:
        return lookup.get(f"{message_id}:{name}")
    return None


def _content_block_url(item: dict[str, object]) -> str | None:
    if item.get("url") is not None:
        return str(item.get("url"))
    image_url = item.get("image_url")
    if isinstance(image_url, dict) and image_url.get("url") is not None:
        return str(image_url.get("url"))
    if isinstance(image_url, str):
        return image_url
    return None


def _tool_activity_in_message_window(
    item: object,
    *,
    window_message_ids: set[str],
    window_tool_call_ids: set[str],
) -> bool:
    raw_tool_call_id = getattr(item, "tool_call_id", None)
    raw_message_id = getattr(item, "message_id", None)
    if isinstance(item, dict):
        raw_tool_call_id = item.get("tool_call_id")
        raw_message_id = item.get("message_id")
    if raw_tool_call_id is not None and str(raw_tool_call_id) in window_tool_call_ids:
        return True
    if raw_message_id is not None and str(raw_message_id) in window_message_ids:
        return True
    return False


def tool_activity_to_view(item: object) -> ToolActivityView:
    if isinstance(item, ToolActivityView):
        return item
    if hasattr(item, "model_dump"):
        payload = item.model_dump(mode="json")
    elif isinstance(item, dict):
        payload = item
    else:
        payload = {}
    return ToolActivityView.model_validate(payload)


def tool_call_record_to_view(item: object) -> ToolCallRecordView:
    if isinstance(item, ToolCallRecordView):
        return item
    if hasattr(item, "model_dump"):
        payload = item.model_dump(mode="json")
    elif isinstance(item, dict):
        payload = item
    else:
        payload = {}
    return ToolCallRecordView.model_validate(payload)


def build_runtime_operator_status_view(
    state: ThreadState,
    *,
    recent_tool_activity: list[ToolActivityView],
    recent_approval_events: list[ApprovalEventView],
    subagent_tasks: list[SubagentTaskView],
    process_sessions: list[ProcessSessionView],
    limit: int = 20,
) -> RuntimeOperatorStatusView:
    visible_tools = [item for item in recent_tool_activity if not is_hidden_operator_tool(item)]
    active_tool_count = sum(1 for item in visible_tools if normalize_runtime_status(item.status) in ACTIVE_TOOL_STATUSES)
    completed_tool_count = sum(1 for item in visible_tools if normalize_runtime_status(item.status) == "completed")
    failed_tool_count = sum(1 for item in visible_tools if normalize_runtime_status(item.status) in FAILED_TOOL_STATUSES)
    pending_approval_count = sum(
        1 for item in recent_approval_events if normalize_runtime_status(item.status) in {"requested", "pending", "awaiting_approval"}
    )
    running_process_count = sum(1 for item in process_sessions if normalize_runtime_status(item.status) == "running")
    active_subagent_count = sum(
        1 for item in subagent_tasks if normalize_runtime_status(item.status) in {"queued", "running", "failed_recovery"}
    )
    runtime_phase_timings = runtime_phase_timings_to_view(state.execution.runtime_phase_timings)
    timeline = build_runtime_timeline_items(
        runtime_phase_timings=runtime_phase_timings,
        visible_tools=visible_tools,
        approval_events=recent_approval_events,
        subagent_tasks=subagent_tasks,
        process_sessions=process_sessions,
        limit=limit,
    )
    latest = timeline[0] if timeline else None
    status = runtime_operator_status_name(
        state.lifecycle.status.value if hasattr(state.lifecycle.status, "value") else str(state.lifecycle.status),
        active_tool_count=active_tool_count,
        pending_approval_count=pending_approval_count,
        running_process_count=running_process_count,
        active_subagent_count=active_subagent_count,
    )
    latest_activity = latest.title if latest is not None else state.lifecycle.last_error
    return RuntimeOperatorStatusView(
        status=status,
        active_tool_count=active_tool_count,
        completed_tool_count=completed_tool_count,
        failed_tool_count=failed_tool_count,
        pending_approval_count=pending_approval_count,
        running_process_count=running_process_count,
        active_subagent_count=active_subagent_count,
        latest_activity=frontend_safe_runtime_activity(latest_activity),
        latest_activity_at=latest.timestamp if latest is not None else state.lifecycle.updated_at,
        runtime_phase_timings=runtime_phase_timings,
        timeline=timeline,
    )


def is_model_only_message(payload: dict[str, object]) -> bool:
    kwargs = payload.get("additional_kwargs")
    if not isinstance(kwargs, dict):
        return False
    return bool(
        kwargs.get("anvil_model_only")
        or kwargs.get("anvil_view_image_injection")
        or kwargs.get("visibility") == "model_only"
    )


def build_runtime_timeline_items(
    *,
    runtime_phase_timings: RuntimePhaseTimingsView | None = None,
    visible_tools: list[ToolActivityView],
    approval_events: list[ApprovalEventView],
    subagent_tasks: list[SubagentTaskView],
    process_sessions: list[ProcessSessionView],
    limit: int,
) -> list[RuntimeTimelineItemView]:
    items: list[RuntimeTimelineItemView] = []
    if runtime_phase_timings is not None:
        for mark in _runtime_timeline_phase_marks(runtime_phase_timings):
            phase_timestamp = runtime_phase_timings.started_at + timedelta(milliseconds=mark.elapsed_ms)
            items.append(
                RuntimeTimelineItemView(
                    item_id=f"runtime-phase:{runtime_phase_timings.run_id or 'run'}:{mark.phase}",
                    kind="runtime",
                    status=runtime_phase_timings.status,
                    title=mark.label,
                    detail=f"+{mark.elapsed_ms}ms from run start, +{mark.duration_since_previous_ms}ms from previous phase",
                    timestamp=phase_timestamp,
                    started_at=phase_timestamp,
                    duration_ms=mark.duration_since_previous_ms,
                    source_id=runtime_phase_timings.run_id,
                    source_kind=mark.phase,
                )
            )
    for tool in visible_tools:
        items.append(
            RuntimeTimelineItemView(
                item_id=tool.tool_call_id or f"tool:{tool.name or 'unknown'}:{datetime_key(tool.started_at, tool.completed_at)}",
                kind="tool",
                status=normalize_runtime_status(tool.status) or "unknown",
                title=tool.display_name or tool.name or "Tool",
                detail=compact_tool_detail(tool),
                timestamp=tool.completed_at or tool.started_at,
                started_at=tool.started_at,
                completed_at=tool.completed_at,
                duration_ms=tool.duration_ms,
                source_id=tool.tool_call_id or tool.source_id,
                source_kind=tool.source_kind or tool.capability_group,
            )
        )
    for approval in approval_events:
        items.append(
            RuntimeTimelineItemView(
                item_id=approval.request_id or f"approval:{datetime_key(approval.created_at, approval.resolved_at)}",
                kind="approval",
                status=normalize_runtime_status(approval.status) or "requested",
                title=approval.decision or "Approval",
                detail=approval.reason,
                timestamp=approval.resolved_at or approval.created_at,
                started_at=approval.created_at,
                completed_at=approval.resolved_at,
                source_id=approval.request_id,
                source_kind=approval.action_kind,
            )
        )
    for task in subagent_tasks:
        items.append(
            RuntimeTimelineItemView(
                item_id=task.task_id,
                kind="subagent",
                status=normalize_runtime_status(task.status) or "unknown",
                title=f"Subagent {task.task_id}",
                detail=task.summary or task.error or task.assigned_profile,
                timestamp=task.completed_at or task.started_at,
                started_at=task.started_at,
                completed_at=task.completed_at,
                source_id=task.child_thread_id or task.child_run_id or task.task_id,
                source_kind=task.assigned_profile,
            )
        )
    for session in process_sessions:
        items.append(
            RuntimeTimelineItemView(
                item_id=session.session_id,
                kind="process",
                status=normalize_runtime_status(session.status) or "unknown",
                title=f"Process {session.session_id}",
                detail=compact_text(session.command, limit=180),
                timestamp=session.completed_at or session.started_at,
                started_at=session.started_at,
                completed_at=session.completed_at,
                source_id=session.session_id,
                source_kind=session.backend_id,
            )
        )
    return sorted(
        items,
        key=lambda item: (
            0 if item.kind != "runtime" else -1,
            item.timestamp or item.started_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )[:limit]


def _runtime_timeline_phase_marks(runtime_phase_timings: RuntimePhaseTimingsView) -> list[RuntimePhaseTimingMarkView]:
    marks = list(runtime_phase_timings.marks)
    seen_phases = {mark.phase for mark in marks}
    previous_elapsed_ms = marks[-1].elapsed_ms if marks else 0
    extra_marks: list[RuntimePhaseTimingMarkView] = []
    for phase, elapsed_ms in (
        ("first_model_event", runtime_phase_timings.first_model_event_elapsed_ms),
        ("first_content_delta", runtime_phase_timings.first_content_delta_elapsed_ms),
        ("run_completed_emitted", runtime_phase_timings.completed_elapsed_ms),
    ):
        if elapsed_ms is None or phase in seen_phases:
            continue
        extra_marks.append(
            RuntimePhaseTimingMarkView(
                phase=phase,
                label=phase.replace("_", " ").title(),
                elapsed_ms=elapsed_ms,
                duration_since_previous_ms=max(elapsed_ms - previous_elapsed_ms, 0),
            )
        )
        seen_phases.add(phase)
        previous_elapsed_ms = elapsed_ms
    return sorted([*marks, *extra_marks], key=lambda item: item.elapsed_ms)


def runtime_operator_status_name(
    lifecycle_status: str,
    *,
    active_tool_count: int,
    pending_approval_count: int,
    running_process_count: int,
    active_subagent_count: int,
) -> str:
    normalized = normalize_runtime_status(lifecycle_status)
    if pending_approval_count or normalized == "awaiting_approval":
        return "awaiting_approval"
    if normalized == "awaiting_clarification":
        return "awaiting_clarification"
    if active_tool_count or running_process_count or active_subagent_count or normalized in ACTIVE_RUNTIME_STATUSES:
        return "running"
    if normalized in TERMINAL_RUNTIME_STATUSES:
        return normalized
    return "idle"


def is_hidden_operator_tool(tool: ToolActivityView) -> bool:
    name = (tool.name or "").strip()
    capability_group = (tool.capability_group or "").strip()
    return name in HIDDEN_OPERATOR_TOOL_NAMES or capability_group in HIDDEN_OPERATOR_TOOL_GROUPS


def normalize_runtime_status(value: object) -> str:
    if value is None:
        return ""
    status_value = value.value if hasattr(value, "value") else value
    normalized = str(status_value).strip().lower()
    if normalized == "success":
        return "completed"
    if normalized == "failure":
        return "failed"
    return normalized


def compact_tool_detail(tool: ToolActivityView) -> str | None:
    args = tool.args or {}
    path = args.get("path") or args.get("file_path") or args.get("target")
    command = args.get("command") or args.get("cmd")
    query = args.get("query")
    if isinstance(path, str) and path.strip():
        return compact_text(path, limit=180)
    if isinstance(command, str) and command.strip():
        return compact_text(command, limit=180)
    if isinstance(query, str) and query.strip():
        return compact_text(query, limit=180)
    if tool.result_text:
        return compact_text(tool.result_text, limit=180)
    if args:
        try:
            return compact_text(json.dumps(args, ensure_ascii=False, sort_keys=True), limit=180)
        except TypeError:
            return compact_text(str(args), limit=180)
    return None


def compact_text(value: object, *, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(limit - 1, 0)]}…"


def datetime_key(*values: datetime | None) -> str:
    for value in values:
        if value is not None:
            return value.isoformat()
    return "unknown"


def build_upload_artifact_refs(state: ThreadState) -> list[ArtifactRefView]:
    refs: list[ArtifactRefView] = []
    for item in state.artifacts.uploaded_files:
        if not isinstance(item, dict):
            continue
        refs.append(
            ArtifactRefView(
                kind="upload",
                label=str(item.get("filename") or item.get("virtual_path") or "upload"),
                artifact_url=str(item.get("artifact_url")) if item.get("artifact_url") is not None else None,
                virtual_path=str(item.get("virtual_path")) if item.get("virtual_path") is not None else None,
                source_scope=str(item.get("source_scope")) if item.get("source_scope") is not None else None,
                internal=bool(item.get("internal", False)),
                extension=str(item.get("extension")) if item.get("extension") is not None else None,
                markdown_file=str(item.get("markdown_file")) if item.get("markdown_file") is not None else None,
                markdown_virtual_path=str(item.get("markdown_virtual_path")) if item.get("markdown_virtual_path") is not None else None,
                markdown_artifact_url=str(item.get("markdown_artifact_url")) if item.get("markdown_artifact_url") is not None else None,
                companions=companion_artifacts_from_payload(item.get("companions")),
                extraction=document_extraction_from_payload(item.get("extraction")),
                outline=document_outline_from_payload(item.get("outline")),
                outline_preview=[str(value) for value in item.get("outline_preview", [])] if isinstance(item.get("outline_preview"), list) else [],
                converter_used=str(item.get("converter_used")) if item.get("converter_used") is not None else None,
                ocr_used=bool(item.get("ocr_used", False)),
                conversion_error=str(item.get("conversion_error")) if item.get("conversion_error") is not None else None,
            )
        )
    return refs


def build_output_artifact_refs(state: ThreadState) -> list[ArtifactRefView]:
    refs: list[ArtifactRefView] = []
    for relative_path in state.artifacts.output_artifacts:
        descriptor = state.thread_data.outputs_path
        refs.append(
            ArtifactRefView(
                kind="output",
                label=relative_path,
                artifact_url=f"/threads/{state.identity.thread_id}/artifacts/outputs/{relative_path}",
                virtual_path=f"/mnt/user-data/outputs/{relative_path}",
                source_scope="output",
            )
        )
    return refs


def build_presented_artifact_refs(state: ThreadState) -> list[ArtifactRefView]:
    return [
        ArtifactRefView(
            kind="presented",
            label=relative_path,
            artifact_url=None,
            virtual_path=relative_path,
            source_scope="presented",
        )
        for relative_path in state.artifacts.presented_artifacts
    ]


def build_canonical_artifact_refs(
    deps: AppRuntimeDeps,
    thread_id: str,
) -> dict[str, list[ArtifactRefView]]:
    raw_refs = deps.thread_service.build_artifact_refs(thread_id)
    return {
        "uploads": [
            artifact_ref_from_payload(item, kind="upload")
            for item in raw_refs["uploads"]
        ],
        "outputs": [
            artifact_ref_from_payload(item, kind="output")
            for item in raw_refs["outputs"]
        ],
        "presented": [
            ArtifactRefView(
                kind="presented",
                label=str(item),
                artifact_url=None,
                virtual_path=str(item),
                source_scope="presented",
            )
            for item in raw_refs["presented"]
        ],
    }


def build_canonical_artifact_refs_for_path_service(
    state: ThreadState,
    path_service,
) -> dict[str, list[ArtifactRefView]]:
    return {
        "uploads": build_upload_artifact_refs(state),
        "outputs": [
            artifact_ref_from_payload(
                path_service.to_artifact_descriptor(state.identity.thread_id, "outputs", relative_path),
                kind="output",
            )
            for relative_path in state.artifacts.output_artifacts
        ],
        "presented": build_presented_artifact_refs(state),
    }


def build_window_artifact_refs(
    state: ThreadState,
    *,
    windowed_message_payloads: list[dict[str, object]],
) -> dict[str, list[ArtifactRefView]]:
    upload_keys = _message_window_upload_keys(windowed_message_payloads)
    if not upload_keys["filenames"] and not upload_keys["virtual_paths"]:
        return {
            "uploads": [],
            "outputs": [],
            "presented": [],
        }
    upload_refs = [
        artifact_ref_from_payload(item, kind="upload")
        for item in state.artifacts.uploaded_files
        if _upload_payload_matches_any(
            item,
            labels=upload_keys["filenames"],
            virtual_paths=upload_keys["virtual_paths"],
        )
    ]
    return {
        "uploads": upload_refs,
        "outputs": [],
        "presented": [],
    }


def _message_window_upload_keys(windowed_message_payloads: list[dict[str, object]]) -> dict[str, set[str]]:
    filenames: set[str] = set()
    virtual_paths: set[str] = set()
    for payload in windowed_message_payloads:
        if str(payload.get("role", "")) not in {"human", "user"}:
            continue
        additional_kwargs = payload.get("additional_kwargs")
        if not isinstance(additional_kwargs, dict):
            continue
        files = additional_kwargs.get("files")
        if isinstance(files, list):
            for item in files:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename") or item.get("label")
                if filename is not None:
                    filenames.add(str(filename))
                virtual_path = item.get("virtual_path") or item.get("path")
                if virtual_path is not None:
                    virtual_paths.add(str(virtual_path))
        uploaded_filenames = additional_kwargs.get("uploaded_filenames")
        if isinstance(uploaded_filenames, (list, tuple)):
            filenames.update(str(item) for item in uploaded_filenames if item is not None)
    return {"filenames": filenames, "virtual_paths": virtual_paths}


def _upload_payload_matches_any(item: object, *, labels: set[str], virtual_paths: set[str]) -> bool:
    if not isinstance(item, dict):
        return False
    label = str(item.get("filename") or item.get("label") or item.get("virtual_path") or "")
    virtual_path = str(item.get("virtual_path")) if item.get("virtual_path") is not None else None
    return label in labels or (virtual_path is not None and virtual_path in virtual_paths)


def document_outline_from_payload(payload: object) -> list[DocumentOutlineEntryView]:
    if not isinstance(payload, list):
        return []
    entries: list[DocumentOutlineEntryView] = []
    for item in payload:
        if isinstance(item, DocumentOutlineEntryView):
            entries.append(item)
            continue
        if not isinstance(item, dict):
            continue
        entries.append(
            DocumentOutlineEntryView(
                title=str(item.get("title")) if item.get("title") is not None else None,
                line=int(item.get("line")) if item.get("line") is not None else None,
                truncated=bool(item.get("truncated", False)),
            )
        )
    return entries


def companion_artifacts_from_payload(payload: object) -> list[CompanionArtifactView]:
    if not isinstance(payload, list):
        return []
    companions: list[CompanionArtifactView] = []
    for item in payload:
        if isinstance(item, CompanionArtifactView):
            companions.append(item)
            continue
        if not isinstance(item, dict):
            continue
        companions.append(
            CompanionArtifactView(
                kind=str(item.get("kind", "companion")),
                label=str(item.get("label", "companion")),
                artifact_url=str(item.get("artifact_url")) if item.get("artifact_url") is not None else None,
                virtual_path=str(item.get("virtual_path")) if item.get("virtual_path") is not None else None,
                provider=str(item.get("provider")) if item.get("provider") is not None else None,
                internal=bool(item.get("internal", False)),
                source_scope=str(item.get("source_scope")) if item.get("source_scope") is not None else None,
            )
        )
    return companions


def document_extraction_from_payload(payload: object) -> DocumentExtractionView | None:
    if isinstance(payload, DocumentExtractionView):
        return payload
    if not isinstance(payload, dict):
        return None
    return DocumentExtractionView(
        status=str(payload.get("status", "unknown")),
        provider=str(payload.get("provider")) if payload.get("provider") is not None else None,
        ocr_provider=str(payload.get("ocr_provider")) if payload.get("ocr_provider") is not None else None,
        page_count=int(payload.get("page_count")) if payload.get("page_count") is not None else None,
        text_layer_present=bool(payload.get("text_layer_present")) if payload.get("text_layer_present") is not None else None,
        diagnostics=[str(value) for value in payload.get("diagnostics", [])] if isinstance(payload.get("diagnostics"), list) else [],
    )


def artifact_ref_from_payload(payload: object, *, kind: str) -> ArtifactRefView:
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(mode="json")
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {}
    label = str(
        data.get("filename")
        or data.get("relative_path")
        or data.get("label")
        or data.get("virtual_path")
        or kind
    )
    return ArtifactRefView(
        kind=kind,
        label=label,
        artifact_url=str(data.get("artifact_url")) if data.get("artifact_url") is not None else None,
        virtual_path=str(data.get("virtual_path")) if data.get("virtual_path") is not None else None,
        source_scope=str(data.get("source_scope")) if data.get("source_scope") is not None else kind if kind in {"upload", "output", "presented"} else None,
        internal=bool(data.get("internal", False)),
        extension=str(data.get("extension")) if data.get("extension") is not None else None,
        markdown_file=str(data.get("markdown_file")) if data.get("markdown_file") is not None else None,
        markdown_virtual_path=str(data.get("markdown_virtual_path")) if data.get("markdown_virtual_path") is not None else None,
        markdown_artifact_url=str(data.get("markdown_artifact_url")) if data.get("markdown_artifact_url") is not None else None,
        companions=companion_artifacts_from_payload(data.get("companions")),
        extraction=document_extraction_from_payload(data.get("extraction")),
        outline=document_outline_from_payload(data.get("outline")),
        outline_preview=[str(value) for value in data.get("outline_preview", [])] if isinstance(data.get("outline_preview"), list) else [],
        converter_used=str(data.get("converter_used")) if data.get("converter_used") is not None else None,
        ocr_used=bool(data.get("ocr_used", False)),
        conversion_error=str(data.get("conversion_error")) if data.get("conversion_error") is not None else None,
    )


def build_message_artifact_refs(
    payload: dict[str, object],
    *,
    upload_refs: list[ArtifactRefView],
    output_refs: list[ArtifactRefView],
) -> list[ArtifactRefView]:
    role = str(payload.get("role", ""))
    if role == "human":
        return _upload_refs_for_message(payload, upload_refs)
    return []


def _upload_refs_for_message(payload: dict[str, object], upload_refs: list[ArtifactRefView]) -> list[ArtifactRefView]:
    additional_kwargs = payload.get("additional_kwargs")
    if not isinstance(additional_kwargs, dict):
        return []

    filenames: set[str] = set()
    virtual_paths: set[str] = set()
    files = additional_kwargs.get("files")
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename") or item.get("label")
            if filename is not None:
                filenames.add(str(filename))
            virtual_path = item.get("virtual_path") or item.get("path")
            if virtual_path is not None:
                virtual_paths.add(str(virtual_path))

    uploaded_filenames = additional_kwargs.get("uploaded_filenames")
    if isinstance(uploaded_filenames, (list, tuple)):
        filenames.update(str(item) for item in uploaded_filenames if item is not None)

    if not filenames and not virtual_paths:
        return []

    refs: list[ArtifactRefView] = []
    for ref in upload_refs:
        if ref.label in filenames or (ref.virtual_path is not None and ref.virtual_path in virtual_paths):
            refs.append(ref)
    return refs


def approval_to_view(
    decision: str | None,
    approval_request: dict[str, object] | None,
) -> ApprovalView | None:
    if decision is None and approval_request is None:
        return None
    approval_request = approval_request or {}
    requested_permissions = approval_request.get("requested_permissions")
    scope_options = approval_request.get("scope_options")
    return ApprovalView(
        decision=decision or "needs_user_approval",
        reason=str(approval_request.get("reason")) if approval_request.get("reason") is not None else None,
        action_kind=str(approval_request.get("action_kind")) if approval_request.get("action_kind") is not None else None,
        request_id=str(approval_request.get("request_id")) if approval_request.get("request_id") is not None else None,
        requested_permissions=[str(item) for item in requested_permissions] if isinstance(requested_permissions, list) else [],
        scope_options=[str(item) for item in scope_options] if isinstance(scope_options, (list, tuple)) else [],
    )


def approval_event_to_view(item: object) -> ApprovalEventView:
    if isinstance(item, ApprovalEventView):
        return item
    if hasattr(item, "model_dump"):
        payload = item.model_dump(mode="json")
    elif isinstance(item, dict):
        payload = item
    else:
        payload = {}
    return ApprovalEventView.model_validate(payload)


def user_interaction_to_view(payload: object | None) -> UserInteractionRequestView | None:
    if payload is None:
        return None
    if isinstance(payload, UserInteractionRequestView):
        return payload
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    if not isinstance(payload, dict):
        return None
    try:
        return UserInteractionRequestView.model_validate(payload)
    except Exception:
        return None


def _config_origin_source(config_result, prefix: str) -> str | None:
    for key_path, origin in config_result.origins.items():
        if key_path == prefix or key_path.startswith(f"{prefix}."):
            return origin.source
    return None


def approval_policy_summary_for_state(state: ThreadState) -> str:
    mode = state.execution.execution_mode
    if mode is ThreadExecutionMode.CHAT:
        return "Chat mode disables tool execution and keeps the session conversational."
    if mode is ThreadExecutionMode.FULL_ACCESS:
        return "Full access mode runs tools without approval prompts while audit logs and hard guardrail blocks still apply."
    return "Agent mode allows runtime tool execution while guarded actions continue to require explicit approval."


def allowed_local_actions_for_state(state: ThreadState) -> list[str]:
    mode = state.execution.execution_mode
    if mode is ThreadExecutionMode.CHAT:
        return ["conversation"]
    if mode is ThreadExecutionMode.FULL_ACCESS:
        return ["conversation", "filesystem_tools", "guarded_tool_calls", "promoted_deferred_capabilities"]
    return ["conversation", "filesystem_tools"]


def requires_approval_actions_for_state(state: ThreadState) -> list[str]:
    mode = state.execution.execution_mode
    if mode is ThreadExecutionMode.CHAT:
        return []
    if mode is ThreadExecutionMode.FULL_ACCESS:
        return []
    return ["guarded_tool_calls", "network_or_external_capabilities"]


def restricted_actions_for_state(state: ThreadState) -> list[str]:
    mode = state.execution.execution_mode
    if mode is ThreadExecutionMode.CHAT:
        return ["tool_execution", "filesystem_mutation", "delegated_runtime_actions"]
    if mode is ThreadExecutionMode.AGENT:
        return ["unguarded_full_access_shortcuts"]
    return []


def memory_store_to_view(store) -> MemoryStoreView:
    effective_max_tokens = getattr(store, "effective_max_tokens", None)
    if effective_max_tokens is None:
        effective_max_tokens = getattr(store, "max_tokens", None) or max(int(store.max_chars) // 4, 1)
    effective_injection_tokens = getattr(store, "effective_injection_tokens", None)
    if effective_injection_tokens is None:
        effective_injection_tokens = getattr(store, "injection_tokens", None) or max(int(store.injection_chars) // 4, 1)
    return MemoryStoreView(
        store_id=store.store_id,
        display_name=store.display_name,
        max_chars=store.max_chars,
        injection_chars=store.injection_chars,
        max_tokens=getattr(store, "max_tokens", None) or effective_max_tokens,
        injection_tokens=getattr(store, "injection_tokens", None) or effective_injection_tokens,
        effective_max_tokens=effective_max_tokens,
        effective_injection_tokens=effective_injection_tokens,
        budget_source=str(getattr(store, "budget_source", "fallback")),
        actual_injection_tokens=int(getattr(store, "actual_injection_tokens", 0) or 0),
        actual_injection_chars=int(getattr(store, "actual_injection_chars", 0) or 0),
        usage_chars=store.usage_chars,
        usage_tokens=getattr(store, "usage_tokens", max(store.usage_chars // 4, 1)),
        entry_count=store.entry_count,
        summary=store.summary,
        summary_sections=dict(getattr(store, "summary_sections", {}) or {}),
        snapshot_status=str(getattr(store, "snapshot_status", "live")),
        updated_at=store.updated_at,
    )


def memory_entry_to_view(entry) -> MemoryEntryView:
    return MemoryEntryView(
        entry_id=entry.entry_id,
        memory_id=entry.memory_id,
        store_id=entry.store_id,
        layer_id=entry.layer_id,
        content=entry.content,
        category=entry.category,
        source_kind=entry.source_kind,
        priority=entry.priority,
        confidence=entry.confidence,
        salience=entry.salience,
        last_accessed_at=entry.last_accessed_at,
        evidence_refs=entry.evidence_refs,
        supersedes=entry.supersedes,
        conflicts_with=entry.conflicts_with,
        expires_at=entry.expires_at,
        effective_score=deps_score_entry(entry) if "deps_score_entry" in globals() else entry.priority,
        status=entry.status,
        metadata=entry.metadata,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def recall_evidence_to_view(item) -> RecallEvidenceView:
    if isinstance(item, dict):
        return RecallEvidenceView.model_validate(item)
    return RecallEvidenceView(
        evidence_id=item.evidence_id,
        source_kind=item.source_kind,
        source_id=item.source_id,
        layer_id=item.layer_id,
        memory_id=item.memory_id,
        archive_id=item.archive_id,
        thread_id=item.thread_id,
        score=item.score,
        match_score=item.match_score,
        rerank_score=item.rerank_score,
        recency_score=item.recency_score,
        final_score=item.final_score,
        dropped_reason=item.dropped_reason,
        reason=item.reason,
        excerpt=item.excerpt,
    )


def memory_provider_to_view(provider) -> MemoryProviderView:
    return MemoryProviderView(
        provider_id=provider.provider_id,
        display_name=provider.display_name,
        kind=getattr(provider, "kind", "local_curated"),
        origin=getattr(provider, "origin", "builtin"),
        family=provider.family,
        description=provider.description,
        active=provider.active,
        configured=provider.configured,
        available=provider.available,
        supports_prefetch=provider.supports_prefetch,
        supports_sync=provider.supports_sync,
        supports_index=getattr(provider, "supports_index", True),
        supports_reflection=provider.supports_reflection,
        supports_explain=getattr(provider, "supports_explain", True),
        supports_archive_search=provider.supports_archive_search,
        roles=list(getattr(provider, "roles", ()) or ()),
        health=str(getattr(provider, "health", "unknown")),
        diagnostics=list(getattr(provider, "diagnostics", ()) or ()),
        last_sync_at=getattr(provider, "last_sync_at", None),
    )


def memory_archive_hit_to_view(hit) -> MemoryArchiveSearchHitView:
    if isinstance(hit, dict):
        return MemoryArchiveSearchHitView.model_validate(hit)
    return MemoryArchiveSearchHitView(
        archive_id=hit.archive_id,
        thread_id=hit.thread_id,
        score=hit.score,
        excerpt=hit.excerpt,
        created_at=hit.created_at,
    )


def reflection_job_to_view(job) -> ReflectionJobView:
    return ReflectionJobView(
        job_id=job.job_id,
        name=job.name,
        schedule_kind=job.schedule_kind.value if hasattr(job.schedule_kind, "value") else str(job.schedule_kind),
        target_store_id=job.target_store_id,
        enabled=job.enabled,
        system_managed=job.system_managed,
        template=job.template,
        instructions=job.instructions,
        source_query=job.source_query,
        interval_seconds=job.interval_seconds,
        cron=job.cron,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        last_status=job.last_status,
    )


def scheduled_task_schedule_to_view(schedule) -> ScheduledTaskScheduleView:
    return ScheduledTaskScheduleView(
        kind=schedule.kind.value if hasattr(schedule.kind, "value") else str(schedule.kind),
        display=schedule.display,
        interval_seconds=schedule.interval_seconds,
        cron=schedule.cron,
        run_at=schedule.run_at,
    )


def scheduled_task_to_view(task) -> ScheduledTaskView:
    status_value = task.status.value if hasattr(task.status, "value") else str(task.status)
    return ScheduledTaskView(
        task_id=task.task_id,
        name=task.name,
        prompt=task.prompt,
        schedule=scheduled_task_schedule_to_view(task.schedule),
        enabled=task.enabled,
        status=status_value,
        system_managed=task.system_managed,
        thread_id=task.thread_id,
        execution_mode=task.execution_mode,
        selected_model=task.selected_model,
        selected_profile=task.selected_profile,
        selected_reasoning_effort=task.selected_reasoning_effort,
        promoted_capabilities=list(task.promoted_capabilities),
        max_runs=task.max_runs,
        run_count=task.run_count,
        missed_run_policy=task.missed_run_policy,
        delivery=dict(task.delivery),
        metadata=dict(task.metadata),
        created_at=task.created_at,
        updated_at=task.updated_at,
        next_run_at=task.next_run_at,
        last_run_at=task.last_run_at,
        last_status=task.last_status,
        last_error=task.last_error,
        last_execution_id=task.last_execution_id,
    )


def scheduled_task_execution_to_view(execution) -> ScheduledTaskExecutionView:
    return ScheduledTaskExecutionView(
        execution_id=execution.execution_id,
        task_id=execution.task_id,
        thread_id=execution.thread_id,
        run_id=execution.run_id,
        status=execution.status,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        summary=execution.summary,
        error=execution.error,
        output_path=execution.output_path,
        metadata=dict(execution.metadata),
    )


def scheduled_task_run_to_view(result) -> ScheduledTaskRunView:
    return ScheduledTaskRunView(
        task=scheduled_task_to_view(result.task),
        execution=scheduled_task_execution_to_view(result.execution) if result.execution is not None else None,
        ran=result.ran,
        reason=result.reason,
    )


def scheduled_task_automation_status_to_view(status) -> ScheduledTaskAutomationStatusResponse:
    return ScheduledTaskAutomationStatusResponse(
        enabled=status.enabled,
        tick_seconds=status.tick_seconds,
        max_due_per_tick=status.max_due_per_tick,
        task_count=status.task_count,
        enabled_task_count=status.enabled_task_count,
        due_count=status.due_count,
        running_count=status.running_count,
        failed_count=status.failed_count,
        next_run_at=status.next_run_at,
        last_run_at=status.last_run_at,
        last_execution_id=status.last_execution_id,
        last_status=status.last_status,
        last_error=status.last_error,
        recent_executions=[scheduled_task_execution_to_view(item) for item in status.recent_executions],
        reason=status.reason,
    )


def scheduled_task_automation_run_to_view(result) -> ScheduledTaskAutomationRunResponse:
    return ScheduledTaskAutomationRunResponse(
        status=scheduled_task_automation_status_to_view(result.status),
        ran_count=result.ran_count,
        skipped_count=result.skipped_count,
        results=[scheduled_task_run_to_view(item) for item in result.results],
        reason=result.reason,
    )


def memory_trace_to_view(trace) -> MemoryTraceView:
    return MemoryTraceView(
        trace_id=trace.trace_id,
        thread_id=trace.thread_id,
        query=trace.query,
        trace_kind=trace.trace_kind,
        target_id=trace.target_id,
        provider_notes=list(trace.provider_notes),
        evidence=[recall_evidence_to_view(item) for item in trace.evidence],
        created_at=trace.created_at,
    )


def memory_conflict_to_view(conflict) -> MemoryConflictView:
    return MemoryConflictView(
        conflict_id=conflict.conflict_id,
        memory_id=conflict.memory_id,
        conflicting_memory_id=conflict.conflicting_memory_id,
        reason=conflict.reason,
        created_at=conflict.created_at,
        resolved=conflict.resolved,
        recommended_action=conflict.recommended_action,
        memory_content=conflict.memory_content,
        conflicting_content=conflict.conflicting_content,
    )


def memory_staleness_to_view(item) -> MemoryStalenessEntryView:
    return MemoryStalenessEntryView(
        memory_id=item.memory_id,
        layer_id=item.layer_id,
        stale_score=item.stale_score,
        reason=item.reason,
        last_accessed_at=item.last_accessed_at,
        expires_at=item.expires_at,
        retention_score=getattr(item, "retention_score", 0.0),
        tier=getattr(item, "tier", "cold"),
        access_count=getattr(item, "access_count", 0),
        reinforcement_boost=getattr(item, "reinforcement_boost", 0.0),
        temporal_decay=getattr(item, "temporal_decay", 0.0),
        salience=getattr(item, "salience", 0.0),
    )


def memory_quality_issue_to_view(item) -> MemoryQualityIssueView:
    return MemoryQualityIssueView.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)


def memory_store_health_to_view(item) -> MemoryStoreHealthView:
    payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    return MemoryStoreHealthView(
        **{
            **payload,
            "issues": [memory_quality_issue_to_view(issue) for issue in getattr(item, "issues", ())],
        }
    )


def memory_retention_to_view(item) -> MemoryRetentionEntryView:
    return MemoryRetentionEntryView.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)


def memory_health_to_view(report) -> MemoryHealthResponse:
    return MemoryHealthResponse(
        status=report.status,
        quality_score=report.quality_score,
        archive_turn_count=report.archive_turn_count,
        pending_review_count=report.pending_review_count,
        conflict_count=report.conflict_count,
        stale_count=report.stale_count,
        provider_count=report.provider_count,
        provider_health=dict(report.provider_health),
        stores=[memory_store_health_to_view(item) for item in report.stores],
        issues=[memory_quality_issue_to_view(item) for item in report.issues],
        recommendations=list(report.recommendations),
        generated_at=report.generated_at,
    )


def self_upgrade_backlog_item_to_view(item) -> SelfUpgradeBacklogItemView:
    return SelfUpgradeBacklogItemView.model_validate(
        item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    )


def self_upgrade_domain_health_to_view(item) -> SelfUpgradeDomainHealthView:
    return SelfUpgradeDomainHealthView.model_validate(
        item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    )


def self_upgrade_health_to_view(report) -> SelfUpgradeHealthResponse:
    return SelfUpgradeHealthResponse(
        mode=report.mode,
        status=report.status,
        score=report.score,
        fingerprint=report.fingerprint,
        domains=tuple(self_upgrade_domain_health_to_view(item) for item in report.domains),
        backlog=tuple(self_upgrade_backlog_item_to_view(item) for item in report.backlog),
        recommendations=tuple(report.recommendations),
        generated_at=report.generated_at,
    )


def memory_recall_benchmark_case_to_view(item) -> MemoryRecallBenchmarkCaseResultView:
    return MemoryRecallBenchmarkCaseResultView(
        case_id=item.case_id,
        query=item.query,
        passed=item.passed,
        score=item.score,
        recall_hits=item.recall_hits,
        expected_count=item.expected_count,
        false_positive_count=item.false_positive_count,
        evidence_count=item.evidence_count,
        top_evidence=[recall_evidence_to_view(evidence) for evidence in item.top_evidence],
        missing_expectations=list(item.missing_expectations),
        false_positives=list(item.false_positives),
        summary=item.summary,
    )


def memory_recall_benchmark_to_view(report) -> MemoryRecallBenchmarkResponse:
    return MemoryRecallBenchmarkResponse(
        suite_id=report.suite_id,
        passed=report.passed,
        score=report.score,
        case_count=report.case_count,
        passed_count=report.passed_count,
        failed_count=report.failed_count,
        recall_hit_rate=report.recall_hit_rate,
        false_positive_rate=report.false_positive_rate,
        average_evidence_count=report.average_evidence_count,
        cases=[memory_recall_benchmark_case_to_view(item) for item in report.cases],
        recommendations=list(report.recommendations),
        generated_at=report.generated_at,
    )


def memory_recall_benchmark_case_input_to_view(item) -> dict[str, object]:
    return {
        "case_id": item.case_id,
        "query": item.query,
        "thread_id": item.thread_id,
        "expected_terms": list(item.expected_terms),
        "expected_memory_ids": list(item.expected_memory_ids),
        "expected_archive_thread_ids": list(item.expected_archive_thread_ids),
        "forbidden_terms": list(item.forbidden_terms),
        "forbidden_memory_ids": list(item.forbidden_memory_ids),
        "min_score": item.min_score,
    }


def memory_recall_benchmark_suite_to_view(item) -> MemoryRecallBenchmarkSuiteView:
    return MemoryRecallBenchmarkSuiteView(
        suite_id=item.suite_id,
        name=item.name,
        description=item.description,
        cases=[memory_recall_benchmark_case_input_to_view(case) for case in item.cases],
        tags=list(item.tags),
        enabled=item.enabled,
        source=item.source,
        created_at=item.created_at,
        updated_at=item.updated_at,
        latest_run_id=item.latest_run_id,
        latest_score=item.latest_score,
        latest_passed=item.latest_passed,
        latest_run_at=item.latest_run_at,
    )


def memory_recall_benchmark_run_to_view(item) -> MemoryRecallBenchmarkRunView:
    return MemoryRecallBenchmarkRunView(
        run_id=item.run_id,
        suite_id=item.suite_id,
        suite_name=item.suite_name,
        source=item.source,
        report=memory_recall_benchmark_to_view(item.report),
        created_at=item.created_at,
    )


def memory_review_item_to_view(item) -> MemoryReviewItemView:
    return MemoryReviewItemView.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)


def memory_governance_to_view(result) -> MemoryGovernanceActionResponse:
    return MemoryGovernanceActionResponse(
        action=result.action,
        memory_id=result.memory_id,
        store_id=result.store_id,
        entry_id=result.entry_id,
        status=result.status,
        message=result.message,
        entry=memory_entry_to_view(result.entry) if result.entry is not None else None,
        review_item=memory_review_item_to_view(result.review_item) if result.review_item is not None else None,
        before_retention=memory_retention_to_view(result.before_retention) if result.before_retention is not None else None,
        after_retention=memory_retention_to_view(result.after_retention) if result.after_retention is not None else None,
    )


def memory_governance_plan_item_to_view(item) -> MemoryGovernancePlanItemView:
    return MemoryGovernancePlanItemView.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)


def memory_governance_batch_to_view(result) -> MemoryGovernanceBatchResponse:
    return MemoryGovernanceBatchResponse(
        policy=result.policy,
        layer_id=result.layer_id,
        dry_run=result.dry_run,
        candidate_count=result.candidate_count,
        executed_count=result.executed_count,
        skipped_count=result.skipped_count,
        items=[memory_governance_plan_item_to_view(item) for item in result.items],
        results=[memory_governance_to_view(item) for item in result.results],
        errors=list(result.errors),
    )


def profile_facet_to_view(item) -> ProfileFacetView:
    return ProfileFacetView.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)


def profile_facet_policy_to_view(item) -> ProfileFacetPolicyView:
    return ProfileFacetPolicyView.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)


def profile_facet_audit_to_view(item) -> ProfileFacetAuditEntryView:
    return ProfileFacetAuditEntryView.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)


def profile_facet_governance_to_view(result) -> ProfileFacetGovernanceResponse:
    return ProfileFacetGovernanceResponse(
        action=result.action,
        facet=profile_facet_to_view(result.facet),
        status=result.status,
        message=result.message,
        audit_entry=profile_facet_audit_to_view(result.audit_entry) if result.audit_entry is not None else None,
    )


def profile_facet_rebuild_to_view(result) -> ProfileFacetRebuildResponse:
    return ProfileFacetRebuildResponse(
        status=result.status,
        source=result.source,
        facet_count=result.facet_count,
        updated_count=result.updated_count,
        facets=[profile_facet_to_view(item) for item in result.facets],
        audit_entry=profile_facet_audit_to_view(result.audit_entry) if result.audit_entry is not None else None,
    )


def memory_maintenance_to_view(result) -> MemoryMaintenanceResponse:
    return MemoryMaintenanceResponse(
        run_id=result.run_id,
        status=result.status,
        dry_run=result.dry_run,
        policy=result.policy,
        layer_id=result.layer_id,
        source=result.source,
        update_queue_pending=result.update_queue_pending,
        update_queue_drained=result.update_queue_drained,
        reflection_jobs_due=result.reflection_jobs_due,
        reflection_jobs_run=result.reflection_jobs_run,
        reflection_entries_written=result.reflection_entries_written,
        governance=memory_governance_batch_to_view(result.governance),
        health_before=memory_health_to_view(result.health_before) if result.health_before is not None else None,
        health_after=memory_health_to_view(result.health_after) if result.health_after is not None else None,
        actions_executed=dict(result.actions_executed),
        skipped_actions=dict(result.skipped_actions),
        errors=list(result.errors),
        started_at=result.started_at,
        finished_at=result.finished_at,
    )


def deps_score_entry(entry) -> float:
    from anvil.memory_platform.resolution import MemoryResolutionService

    return MemoryResolutionService().effective_score(entry)


def prompt_snapshot_to_view(snapshot: dict | None) -> PromptSnapshotMetadataView | None:
    if not isinstance(snapshot, dict):
        return None
    return PromptSnapshotMetadataView(
        snapshot_id=str(snapshot.get("snapshot_id") or ""),
        prompt_hash=str(snapshot.get("prompt_hash") or ""),
        skills_fingerprint=str(snapshot.get("skills_fingerprint")) if snapshot.get("skills_fingerprint") is not None else None,
        memory_fingerprint=str(snapshot.get("memory_fingerprint")) if snapshot.get("memory_fingerprint") is not None else None,
        config_fingerprint=str(snapshot.get("config_fingerprint") or ""),
        created_at=str(snapshot.get("created_at") or ""),
    )


def subagent_task_to_view(deps: AppRuntimeDeps, task_id: str) -> SubagentTaskView:
    task = deps.subagent_service.get_task(task_id)
    if task is None:
        raise GatewayAdapterError(status.HTTP_404_NOT_FOUND, "subagent_not_found", f"subagent task '{task_id}' was not found")
    parent_state = deps.checkpointer.get_thread_state(task.parent_thread_id)
    durable_history = parent_state.durable_subagent_job_history if parent_state is not None else []
    return subagent_task_to_view_from_runtime(
        deps.subagent_service,
        path_service=deps.path_service,
        parent_thread_id=task.parent_thread_id,
        task_id=task_id,
        durable_history=durable_history,
    )


def subagent_task_to_view_from_runtime(
    subagent_service,
    *,
    path_service,
    parent_thread_id: str,
    task_id: str,
    durable_history: list[dict[str, object]],
    dependency_state: str | None = None,
) -> SubagentTaskView:
    task = subagent_service.get_task(task_id)
    if task is None:
        raise ValueError(f"unknown subagent task: {task_id}")
    result = subagent_service.get_result(task_id)
    recent_events: list[SubagentEventView] = []
    for item in durable_history:
        if not isinstance(item, dict):
            continue
        if str(item.get("job_id") or "") != task.task_id:
            continue
        recent_events.append(subagent_event_to_view(item))
    recent_tool_activity = [
        subagent_tool_evidence_to_view(item)
        for item in (result.recent_tool_activity if result is not None else ())
    ]
    artifacts = [
        artifact_ref_from_payload(item, kind=str(item.get("kind", "output")) if isinstance(item, dict) else "output")
        for item in (result.artifacts if result is not None else ())
        if isinstance(item, dict)
    ]
    return SubagentTaskView(
        task_id=task.task_id,
        batch_id=task.batch_id,
        parent_thread_id=task.parent_thread_id,
        parent_run_id=task.parent_run_id,
        child_thread_id=task.child_thread_id,
        child_run_id=task.child_run_id,
        status=task.status.value,
        assigned_profile=task.assigned_profile,
        delegation_depth=task.delegation_depth,
        workspace_mode=task.workspace_mode,
        cancel_requested=task.cancel_requested,
        depends_on_task_ids=task.depends_on_task_ids,
        dependency_state=dependency_state,
        started_at=task.started_at,
        completed_at=task.completed_at,
        timeout_at=task.timeout_at,
        error=path_service.translate_runtime_text_to_virtual(task.error, thread_id=parent_thread_id),
        summary=path_service.translate_runtime_text_to_virtual(result.summary if result is not None else None, thread_id=parent_thread_id),
        requested_tool_names=task.requested_tool_names,
        allowed_tool_names=task.allowed_tool_names,
        messages=[
            subagent_message_preview_to_view(item)
            for item in (result.messages if result is not None else ())
        ],
        recent_tool_activity=path_service.translate_runtime_data_to_virtual(recent_tool_activity, thread_id=parent_thread_id),
        recent_events=path_service.translate_runtime_data_to_virtual(recent_events[-30:], thread_id=parent_thread_id),
        artifacts=path_service.translate_runtime_data_to_virtual(artifacts, thread_id=parent_thread_id),
        approval_payload=subagent_approval_summary_to_view(result.approval_payload if result is not None else None),
    )


def subagent_message_preview_to_view(item: object) -> SubagentMessagePreviewView:
    payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    payload = payload if isinstance(payload, dict) else {}
    content = payload.get("content")
    if isinstance(content, str):
        content_preview = content.strip()[:240]
    else:
        content_preview = ""
    tool_calls = payload.get("tool_calls")
    tool_call_count = len(tool_calls) if isinstance(tool_calls, list) else 0
    return SubagentMessagePreviewView(
        role=_string_or_default(payload.get("role"), "unknown"),
        content_preview=content_preview,
        tool_call_count=tool_call_count,
        tool_result_count=1 if _string_or_default(payload.get("role"), "") == "tool" else 0,
    )


def subagent_approval_summary_to_view(payload: object) -> SubagentApprovalSummaryView | None:
    payload = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    if not isinstance(payload, dict) or not payload:
        return None
    request = payload.get("approval_request")
    request_payload = request if isinstance(request, dict) else {}
    summary = SubagentApprovalSummaryView(
        pending_approval=_optional_string(payload.get("pending_approval")),
        request_id=_optional_string(request_payload.get("request_id")),
        thread_id=_optional_string(request_payload.get("thread_id")),
        turn_id=_optional_string(request_payload.get("turn_id")),
        reason=_optional_string(request_payload.get("reason")),
        action_kind=_optional_string(request_payload.get("action_kind")),
        requested_permissions=_string_list(request_payload.get("requested_permissions")),
        tool_name=_optional_string(request_payload.get("tool_name")),
        approval_profile=_optional_string(request_payload.get("approval_profile")),
        risk_category=_optional_string(request_payload.get("risk_category")),
        capability_group=_optional_string(request_payload.get("capability_group")),
    )
    if not any(
        (
            summary.pending_approval,
            summary.request_id,
            summary.thread_id,
            summary.turn_id,
            summary.reason,
            summary.action_kind,
            summary.requested_permissions,
            summary.tool_name,
            summary.approval_profile,
            summary.risk_category,
            summary.capability_group,
        )
    ):
        return None
    return summary


def subagent_tool_evidence_to_view(item: object) -> SubagentToolEvidenceView:
    payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    payload = payload if isinstance(payload, dict) else {}
    args = payload.get("args")
    result_text = payload.get("result_text")
    result = result_text if isinstance(result_text, str) else ""
    return SubagentToolEvidenceView(
        tool_call_id=_optional_string(payload.get("tool_call_id")),
        message_id=_optional_string(payload.get("message_id")),
        name=_optional_string(payload.get("name")),
        display_name=_optional_string(payload.get("display_name")),
        source_kind=_optional_string(payload.get("source_kind")),
        source_id=_optional_string(payload.get("source_id")),
        capability_group=_optional_string(payload.get("capability_group")),
        tool_execution_mode=_optional_string(payload.get("tool_execution_mode")),
        status=_optional_string(payload.get("status")),
        args_keys=sorted(str(key) for key in args.keys()) if isinstance(args, dict) else [],
        has_result=bool(result),
        result_char_count=len(result),
        started_at=parse_datetime_or_none(payload.get("started_at")),
        completed_at=parse_datetime_or_none(payload.get("completed_at")),
        duration_ms=_int_or_none(payload.get("duration_ms")),
    )


def subagent_event_to_view(item: object) -> SubagentEventView:
    payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    payload = payload if isinstance(payload, dict) else {}
    event_payload = payload.get("payload")
    event_payload = event_payload if isinstance(event_payload, dict) else {}
    return SubagentEventView(
        job_id=_string_or_default(payload.get("job_id") or event_payload.get("subagent_job_id") or event_payload.get("task_id"), ""),
        parent_thread_id=_string_or_default(payload.get("parent_thread_id") or event_payload.get("parent_thread_id"), ""),
        parent_run_id=_optional_string(payload.get("parent_run_id") or event_payload.get("parent_run_id")),
        event=_string_or_default(payload.get("event_type") or payload.get("event"), "event"),
        timestamp=parse_datetime_or_none(payload.get("timestamp") or event_payload.get("timestamp")),
        status=_optional_string(event_payload.get("status")),
        summary=_optional_string(event_payload.get("summary")),
        error=_optional_string(event_payload.get("error")),
        tool_name=_optional_string(event_payload.get("tool_name")),
        display_name=_optional_string(event_payload.get("display_name")),
        child_thread_id=_optional_string(event_payload.get("child_thread_id")),
        child_run_id=_optional_string(event_payload.get("child_run_id")),
    )


def process_session_to_view(session, *, path_service=None, thread_id: str | None = None) -> ProcessSessionView:
    payload = session.model_dump(mode="json") if hasattr(session, "model_dump") else session
    if path_service is not None:
        payload = path_service.translate_runtime_data_to_virtual(payload, thread_id=thread_id)
    return ProcessSessionView.model_validate(payload)


def process_log_to_view(log_view, *, path_service=None, thread_id: str | None = None) -> ProcessLogView:
    payload = log_view.model_dump(mode="json") if hasattr(log_view, "model_dump") else log_view
    if path_service is not None:
        payload = path_service.translate_runtime_data_to_virtual(payload, thread_id=thread_id)
    return ProcessLogView.model_validate(payload)


def encode_sse(event: RunStreamEvent) -> str:
    metadata = {
        key: value
        for key in ("event_id", "sequence", "message_id", "block_id", "visibility", "source")
        if (value := getattr(event, key)) is not None
    }
    payload = json.dumps({**event.data, **metadata}, ensure_ascii=False)
    lines = [f"event: {event.event}"]
    event_id = event.event_id
    if event_id is None and event.data.get("event_id") is not None:
        event_id = str(event.data["event_id"])
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"
