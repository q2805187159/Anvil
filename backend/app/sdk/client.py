from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from anvil import ConfigLayer, RuntimeFeatureSet
from anvil.agents import ThreadExecutionMode

from app.contracts import (
    ThreadSettingsUpdateRequest,
    ThreadSettingsView,
    ApprovalResumeRequest,
    CapabilityPromptView,
    CapabilityResourceView,
    EvaluationBatchReportView,
    EvaluationReportRequestView,
    EvaluationThreadReportView,
    ExtensionStatusView,
    MessageView,
    MemoryArchiveSearchResultView,
    MemoryOnboardingRequest,
    MemoryOnboardingResponse,
    MemoryOverviewView,
    MemoryProviderView,
    MemoryStoreView,
    PluginView,
    ReflectionJobRunView,
    ReflectionJobView,
    ScheduledTaskCreateRequest,
    ScheduledTaskExecutionView,
    ScheduledTaskRunView,
    ScheduledTaskUpdateRequest,
    ScheduledTaskView,
    ModelView,
    McpConfigOverviewView,
    McpPromptRenderView,
    McpServerProvenanceView,
    McpServerToolsView,
    McpServerView,
    McpResourceContentView,
    ProcessLogView,
    ProcessSessionView,
    ProcessSpawnRequest,
    RunCompletedView,
    RunRequestBody,
    RunStreamEvent,
    SelfUpgradeHealthResponse,
    SkillContentView,
    SkillCuratorAutomationRequest,
    SkillCuratorAutomationRunResponse,
    SkillCuratorAutomationStatusResponse,
    SkillCuratorRequest,
    SkillFileReadView,
    SkillFileIndexView,
    SkillListItemView,
    SkillView,
    SkillManageRequest,
    TerminalBackendCapabilitiesView,
    ThreadDetailView,
    SubagentDependencyGraphView,
    SubagentTaskView,
    ThreadStateView,
    ThreadView,
    ToolCatalogEntryView,
    UploadResult,
    UserInteractionResumeRequest,
)
from app.gateway import services
from app.gateway.deps import AppRuntimeDeps, build_app_runtime_deps


@dataclass
class EmbeddedClientConfig:
    config_layers: list[ConfigLayer] | None = None
    feature_set: RuntimeFeatureSet | None = None
    thread_root: Path | None = None
    state_db_path: Path | None = None
    chat_model_override: Any | None = None


@dataclass
class EmbeddedRunRequest:
    thread_id: str
    message: str
    execution_mode: ThreadExecutionMode = ThreadExecutionMode.AGENT
    profile: str | None = None
    request_context: str | None = None
    approval_context: str | None = None
    upload_context: str | None = None
    promoted_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmbeddedStreamEvent:
    event: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_run_stream_event(self) -> RunStreamEvent:
        return RunStreamEvent(event=self.event, data=self.data)


class EmbeddedClient:
    def __init__(self, config: EmbeddedClientConfig | None = None) -> None:
        self._config = config or EmbeddedClientConfig()
        self._bundle: AppRuntimeDeps | None = None

    def __enter__(self) -> EmbeddedClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def deps(self) -> AppRuntimeDeps:
        if self._bundle is None:
            self._bundle = build_app_runtime_deps(
                config_layers=self._config.config_layers,
                feature_set=self._config.feature_set,
                thread_root=self._config.thread_root,
                state_db_path=self._config.state_db_path,
                chat_model_override=self._config.chat_model_override,
            )
        return self._bundle

    def close(self) -> None:
        if self._bundle is not None:
            self._bundle.close()
            self._bundle = None

    def _run_async(self, awaitable):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            raise RuntimeError("EmbeddedClient async helper cannot run inside an active event loop")
        return asyncio.run(awaitable)

    def list_threads(self) -> list[ThreadView]:
        return services.list_threads(self.deps)

    def create_thread(self, *, thread_id: str | None = None) -> ThreadView:
        return services.create_thread(self.deps, thread_id=thread_id)

    def get_thread(self, thread_id: str) -> ThreadView:
        return services.get_thread_view(self.deps, thread_id)

    def get_thread_state(self, thread_id: str) -> ThreadStateView:
        return services.get_thread_state_view(self.deps, thread_id)

    def get_thread_detail(self, thread_id: str) -> ThreadDetailView:
        return services.get_thread_detail_view(self.deps, thread_id)

    def get_thread_evaluation_report(self, thread_id: str) -> EvaluationThreadReportView:
        return services.get_thread_evaluation_report_view(self.deps, thread_id)

    def build_evaluation_report(self, body: EvaluationReportRequestView | None = None) -> EvaluationBatchReportView:
        return services.build_evaluation_batch_report_view(self.deps, body or EvaluationReportRequestView())

    def get_thread_settings(self, thread_id: str) -> ThreadSettingsView:
        return services.get_thread_settings_view(self.deps, thread_id)

    def update_thread_settings(self, thread_id: str, body: ThreadSettingsUpdateRequest) -> ThreadSettingsView:
        return services.update_thread_settings(self.deps, thread_id, body)

    def run(self, request: EmbeddedRunRequest) -> RunCompletedView:
        body = RunRequestBody(
            message=request.message,
            execution_mode=request.execution_mode,
            profile=request.profile,
            request_context=request.request_context,
            approval_context=request.approval_context,
            upload_context=request.upload_context,
            promoted_capabilities=list(request.promoted_capabilities),
        )
        return services.run_thread_sync(self.deps, request.thread_id, body)

    def approve(self, thread_id: str, *, approval_context: str = "approved for this turn") -> RunCompletedView:
        return services.resume_thread_approval(
            self.deps,
            thread_id,
            ApprovalResumeRequest(approval_context=approval_context),
        )

    def cancel_approval(self, thread_id: str, *, reason: str = "Cancelled from shell") -> ThreadStateView:
        from app.contracts import ApprovalCancelRequest

        return services.cancel_thread_approval(
            self.deps,
            thread_id,
            ApprovalCancelRequest(reason=reason),
        )

    def resume_user_interaction(
        self,
        thread_id: str,
        body: UserInteractionResumeRequest,
    ) -> RunCompletedView:
        return services.resume_thread_user_interaction(self.deps, thread_id, body)

    def stream_user_interaction(
        self,
        thread_id: str,
        body: UserInteractionResumeRequest,
    ) -> Iterable[EmbeddedStreamEvent]:
        for event in services.stream_thread_user_interaction_events(self.deps, thread_id, body):
            yield EmbeddedStreamEvent(event=event.event, data=event.data)

    def stream(self, request: EmbeddedRunRequest) -> Iterable[EmbeddedStreamEvent]:
        for event in services.iter_thread_run_events(
            self.deps,
            request.thread_id,
            message=request.message,
            execution_mode=request.execution_mode,
            profile=request.profile,
            request_context=request.request_context,
            approval_context=request.approval_context,
            upload_context=request.upload_context,
            promoted_capabilities=request.promoted_capabilities,
        ):
            yield EmbeddedStreamEvent(event=event.event, data=event.data)

    def upload_files(self, thread_id: str, files: list[tuple[str, bytes]]) -> UploadResult:
        return services.upload_files(self.deps, thread_id, files)

    def get_artifact_bytes(self, thread_id: str, kind: str, relative_path: str) -> tuple[bytes, str]:
        return services.get_artifact_content(self.deps, thread_id, kind, relative_path)

    def list_models(self) -> list[ModelView]:
        return services.list_models(self.deps)

    def list_skills(self) -> list[SkillListItemView]:
        return services.list_skills(self.deps)

    def get_skill(self, skill_id: str) -> SkillView:
        return services.get_skill_view(self.deps, skill_id)

    def get_skill_content(self, skill_id: str) -> SkillContentView:
        return services.get_skill_content_view(self.deps, skill_id)

    def list_skill_files(self, skill_id: str) -> SkillFileIndexView:
        return services.list_skill_files_view(self.deps, skill_id)

    def read_skill_file(self, skill_id: str, *, relative_path: str, max_bytes: int = 64_000) -> SkillFileReadView:
        return services.read_skill_file_view(
            self.deps,
            skill_id,
            relative_path=relative_path,
            max_bytes=max_bytes,
        )

    def manage_skill(self, body: SkillManageRequest) -> dict[str, object]:
        return self._run_async(services.manage_skill(self.deps, body))

    def manage_skill_curator(self, body: SkillCuratorRequest) -> dict[str, object]:
        return self._run_async(services.manage_skill_curator(self.deps, body))

    def get_skill_curator_automation(self) -> SkillCuratorAutomationStatusResponse:
        return services.get_skill_curator_automation(self.deps)

    def run_skill_curator_automation(self, body: SkillCuratorAutomationRequest) -> SkillCuratorAutomationRunResponse:
        return self._run_async(services.run_skill_curator_automation(self.deps, body))

    def get_self_upgrade_health(self, *, candidate_audit_limit: int = 50) -> SelfUpgradeHealthResponse:
        return services.get_self_upgrade_health(
            self.deps,
            candidate_audit_limit=candidate_audit_limit,
        )

    def onboard_memory_workspace(
        self,
        *,
        workspace_path: str | None = None,
        thread_id: str | None = None,
        force: bool = False,
        source: str = "embedded",
    ) -> MemoryOnboardingResponse:
        return services.onboard_memory_workspace(
            self.deps,
            MemoryOnboardingRequest(
                workspace_path=workspace_path,
                thread_id=thread_id,
                force=force,
                source=source,
            ),
        )

    def list_tool_catalog(
        self,
        *,
        query: str | None = None,
        source_kind: str | None = None,
        capability_group: str | None = None,
    ) -> list[ToolCatalogEntryView]:
        return services.list_tools_catalog(
            self.deps,
            query=query,
            source_kind=source_kind,
            capability_group=capability_group,
        )

    def get_tool_catalog_entry(self, name_or_capability_id: str) -> ToolCatalogEntryView:
        return services.get_tool_catalog_entry(self.deps, name_or_capability_id)

    def get_memory_overview(self) -> MemoryOverviewView:
        return services.get_memory_overview(self.deps)

    def list_memory_stores(self) -> list[MemoryStoreView]:
        return services.list_memory_stores(self.deps)

    def list_memory_providers(self) -> list[MemoryProviderView]:
        return services.list_memory_providers(self.deps)

    def search_memory_archive(self, query: str, limit: int = 5) -> MemoryArchiveSearchResultView:
        from app.contracts import MemoryArchiveSearchRequest

        return services.search_memory_archive(
            self.deps,
            MemoryArchiveSearchRequest(query=query, limit=limit),
        )

    def list_reflection_jobs(self) -> list[ReflectionJobView]:
        return services.list_reflection_jobs(self.deps)

    def run_reflection_job(self, job_id: str) -> ReflectionJobRunView:
        return services.run_reflection_job(self.deps, job_id)

    def list_scheduled_tasks(self, *, include_disabled: bool = True) -> list[ScheduledTaskView]:
        return services.list_scheduled_tasks(self.deps, include_disabled=include_disabled).items

    def create_scheduled_task(self, body: ScheduledTaskCreateRequest) -> ScheduledTaskView:
        return services.create_scheduled_task(self.deps, body)

    def update_scheduled_task(self, task_id: str, body: ScheduledTaskUpdateRequest) -> ScheduledTaskView:
        return services.update_scheduled_task(self.deps, task_id, body)

    def run_scheduled_task(self, task_id: str, *, force: bool = True) -> ScheduledTaskRunView:
        return self._run_async(services.run_scheduled_task(self.deps, task_id, force=force))

    def pause_scheduled_task(self, task_id: str) -> ScheduledTaskView:
        return services.pause_scheduled_task(self.deps, task_id)

    def resume_scheduled_task(self, task_id: str) -> ScheduledTaskView:
        return services.resume_scheduled_task(self.deps, task_id)

    def remove_scheduled_task(self, task_id: str) -> ScheduledTaskView:
        return services.remove_scheduled_task(self.deps, task_id)

    def list_scheduled_task_executions(self, *, task_id: str | None = None, limit: int = 50) -> list[ScheduledTaskExecutionView]:
        return services.list_scheduled_task_executions(self.deps, task_id=task_id, limit=limit).items

    def list_memory(self) -> list[MemoryStoreView]:
        return self.list_memory_stores()

    def list_extensions(self) -> list[ExtensionStatusView]:
        return services.list_extensions(self.deps)

    def refresh_extension(self, server_id: str) -> ExtensionStatusView:
        return services.refresh_extension(self.deps, server_id)

    def list_plugins(self) -> list[PluginView]:
        return services.list_plugins(self.deps)

    def list_mcp_servers(self) -> list[McpServerView]:
        return self._run_async(services.list_mcp_servers(self.deps))

    def get_mcp_config_overview(self) -> McpConfigOverviewView:
        return self._run_async(services.get_mcp_config_overview(self.deps))

    def get_mcp_server_tools(self, server_id: str) -> McpServerToolsView:
        return self._run_async(services.get_mcp_server_tools(self.deps, server_id))

    def reconnect_mcp_server(self, server_id: str) -> ExtensionStatusView:
        return self._run_async(services.reconnect_mcp_server(self.deps, server_id))

    def get_mcp_server_provenance(self, server_id: str) -> McpServerProvenanceView:
        return self._run_async(services.get_mcp_server_provenance(self.deps, server_id))

    def list_mcp_resources(self, *, server_id: str | None = None) -> list[CapabilityResourceView]:
        return services.list_mcp_resources(self.deps, server_id=server_id)

    def read_mcp_resource(self, server_id: str, resource_id: str) -> McpResourceContentView:
        return services.read_mcp_resource(self.deps, server_id=server_id, resource_id=resource_id)

    def list_mcp_prompts(self, *, server_id: str | None = None) -> list[CapabilityPromptView]:
        return services.list_mcp_prompts(self.deps, server_id=server_id)

    def get_mcp_prompt(
        self,
        server_id: str,
        prompt_id: str,
        *,
        arguments: dict[str, object] | None = None,
    ) -> McpPromptRenderView:
        return services.get_mcp_prompt(self.deps, server_id=server_id, prompt_id=prompt_id, arguments=arguments)

    def list_subagent_tasks(self, thread_id: str) -> list[SubagentTaskView]:
        return services.list_subagent_tasks(self.deps, thread_id)

    def get_subagent_dependency_graph(
        self,
        thread_id: str,
        *,
        parent_run_id: str | None = None,
    ) -> SubagentDependencyGraphView:
        return services.get_subagent_dependency_graph(self.deps, thread_id, parent_run_id=parent_run_id)

    def get_subagent_task(self, thread_id: str, task_id: str) -> SubagentTaskView:
        return services.get_subagent_task(self.deps, thread_id, task_id)

    def wait_subagent_task(self, thread_id: str, task_id: str, *, timeout_seconds: int | None = None) -> SubagentTaskView:
        return services.wait_subagent_task(self.deps, thread_id, task_id, timeout_seconds=timeout_seconds)

    def cancel_subagent_task(self, thread_id: str, task_id: str) -> SubagentTaskView:
        return services.cancel_subagent_task(self.deps, thread_id, task_id)

    def list_process_sessions(self, thread_id: str) -> list[ProcessSessionView]:
        return services.list_process_sessions(self.deps, thread_id)

    def get_process_capabilities(self, thread_id: str) -> TerminalBackendCapabilitiesView:
        return services.get_process_capabilities(self.deps, thread_id)

    def spawn_process_session(
        self,
        thread_id: str,
        *,
        command: str,
        cwd: str = "/mnt/user-data/workspace",
        env: dict[str, str] | None = None,
    ) -> ProcessSessionView:
        return services.spawn_process_session(
            self.deps,
            thread_id,
            ProcessSpawnRequest(command=command, cwd=cwd, env=env or {}),
        )

    def get_process_session(self, thread_id: str, session_id: str) -> ProcessSessionView:
        return services.get_process_session(self.deps, thread_id, session_id)

    def wait_process_session(self, thread_id: str, session_id: str, *, timeout_seconds: int | None = None) -> ProcessSessionView:
        return services.wait_process_session(self.deps, thread_id, session_id, timeout_seconds=timeout_seconds)

    def kill_process_session(self, thread_id: str, session_id: str) -> ProcessSessionView:
        return services.kill_process_session(self.deps, thread_id, session_id)

    def write_process_stdin(self, thread_id: str, session_id: str, data: str, *, submit: bool = False) -> ProcessSessionView:
        return services.write_process_stdin(self.deps, thread_id, session_id, ProcessStdinRequest(data=data, submit=submit))

    def close_process_stdin(self, thread_id: str, session_id: str) -> ProcessSessionView:
        return services.close_process_stdin(self.deps, thread_id, session_id)

    def interrupt_process_session(self, thread_id: str, session_id: str) -> ProcessSessionView:
        return services.interrupt_process_session(self.deps, thread_id, session_id)

    def resize_process_session(self, thread_id: str, session_id: str, *, columns: int, rows: int) -> ProcessSessionView:
        return services.resize_process_session(
            self.deps,
            thread_id,
            session_id,
            ProcessResizeRequest(columns=columns, rows=rows),
        )

    def read_process_log(
        self,
        thread_id: str,
        session_id: str,
        *,
        offset: int = 0,
        limit: int = 200,
        cursor: int | None = None,
    ) -> ProcessLogView:
        return services.read_process_log(self.deps, thread_id, session_id, offset=offset, limit=limit, cursor=cursor)
