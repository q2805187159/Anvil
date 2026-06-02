from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from fake_models import BindableFakeMessagesListChatModel


def test_embedded_client_matches_gateway_contracts(gateway_app_factory, contract_tmp_path: Path) -> None:
    assert importlib.util.find_spec("app.sdk") is not None

    from app.sdk import EmbeddedClient, EmbeddedClientConfig, EmbeddedRunRequest, EmbeddedStreamEvent
    from app.contracts import (
        CapabilityPromptView,
        CapabilityResourceView,
        ExtensionStatusView,
        MessageView,
        MemoryOverviewView,
        MemoryProviderView,
        MemoryStoreView,
        EvaluationBatchReportView,
        EvaluationReportRequestView,
        EvaluationThreadReportView,
        McpPromptRenderView,
        McpResourceContentView,
        McpServerProvenanceView,
        McpServerToolsView,
        McpServerView,
        ModelView,
        ProcessLogView,
        ProcessSessionView,
        RunCompletedView,
        ReflectionJobView,
        ScheduledTaskCreateRequest,
        ScheduledTaskExecutionView,
        ScheduledTaskRunView,
        ScheduledTaskUpdateRequest,
        ScheduledTaskView,
        SelfUpgradeHealthResponse,
        SkillContentView,
        SkillCuratorAutomationRequest,
        SkillCuratorAutomationRunResponse,
        SkillCuratorAutomationStatusResponse,
        SkillCuratorRequest,
        SkillFileIndexView,
        SkillFileReadView,
        SkillListItemView,
        SkillView,
        SubagentTaskView,
        ThreadDetailView,
        ThreadStateView,
        ThreadView,
        ToolCatalogEntryView,
        UploadResult,
    )
    from app.gateway.services import encode_sse

    gateway_app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="gateway parity hello")])
    )
    with TestClient(gateway_app) as http_client:
        config = EmbeddedClientConfig(
            config_layers=gateway_app.state.deps_factory().config_layers,
            thread_root=contract_tmp_path / "embedded-threads",
            state_db_path=contract_tmp_path / "embedded.sqlite3",
            feature_set=gateway_app.state.deps_factory().feature_set,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(content="gateway parity hello"),
                    AIMessage(content="stream parity hello"),
                    AIMessage(content="scheduled parity hello"),
                ]
            ),
        )
        client = EmbeddedClient(config)

        thread = client.create_thread(thread_id="sdk-thread")
        assert isinstance(thread, ThreadView)
        assert thread.thread_id == "sdk-thread"

        sync_result = client.run(EmbeddedRunRequest(thread_id="sdk-thread", message="say hello", execution_mode="chat"))
        assert isinstance(sync_result, RunCompletedView)
        assert sync_result.thread_id == "sdk-thread"
        assert sync_result.assistant_message == "gateway parity hello"
        assert sync_result.state.execution_mode == "chat"

        state = client.get_thread_state("sdk-thread")
        assert isinstance(state, ThreadStateView)
        assert state.thread_id == "sdk-thread"

        detail = client.get_thread_detail("sdk-thread")
        assert isinstance(detail, ThreadDetailView)
        assert detail.thread.thread_id == "sdk-thread"
        assert detail.messages and isinstance(detail.messages[0], MessageView)

        thread_report = client.get_thread_evaluation_report("sdk-thread")
        assert isinstance(thread_report, EvaluationThreadReportView)
        assert thread_report.thread_id == "sdk-thread"
        batch_report = client.build_evaluation_report(
            EvaluationReportRequestView(
                thread_ids=["sdk-thread"],
                options={"include_markdown": True},
                evaluator_results={
                    "sdk-thread": {
                        "evaluator": "sdk-parity",
                        "score": 1.0,
                        "max_score": 1.0,
                        "passed": True,
                    }
                },
            )
        )
        assert isinstance(batch_report, EvaluationBatchReportView)
        assert batch_report.thread_reports[0].evaluator is not None
        assert batch_report.markdown and "sdk-parity" in batch_report.markdown

        upload_result = client.upload_files("sdk-thread", [("notes.txt", b"hello embedded sdk")])
        assert isinstance(upload_result, UploadResult)
        assert upload_result.files[0].filename == "notes.txt"

        body, media_type = client.get_artifact_bytes("sdk-thread", "uploads", "notes.txt")
        assert body == b"hello embedded sdk"
        assert media_type == "text/plain"

        events = list(client.stream(EmbeddedRunRequest(thread_id="sdk-thread", message="stream it", execution_mode="chat")))
        assert all(isinstance(event, EmbeddedStreamEvent) for event in events)
        event_names = [event.event for event in events]
        assert event_names[:7] == [
            "run_preparing",
            "run_started",
            "summary_update",
            "step_started",
            "step_delta",
            "step_updated",
            "message_completed",
        ]
        assert "artifact_emitted" in event_names
        assert event_names[-1] == "run_completed"
        assert events[0].data["phase"] == "gateway_received"
        assert events[1].data["execution_mode"] == "chat"
        assert events[4].data["payload_delta"] == "stream parity hello"
        assert events[-1].data["assistant_message"] == "stream parity hello"
        assert encode_sse(events[0].as_run_stream_event()).startswith("event: run_preparing")

        models = client.list_models()
        assert models and isinstance(models[0], ModelView)

        skills = client.list_skills()
        assert skills and isinstance(skills[0], SkillListItemView)
        assert isinstance(client.get_skill("demo-skill"), SkillView)
        assert isinstance(client.get_skill_content("demo-skill"), SkillContentView)
        skill_files = client.list_skill_files("demo-skill")
        assert isinstance(skill_files, SkillFileIndexView)
        assert skill_files.files and skill_files.files[0].path == "SKILL.md"
        assert skill_files.scanned_path_count >= 0
        assert skill_files.max_scanned_paths > 0
        assert skill_files.scan_truncated is False
        assert isinstance(client.read_skill_file("demo-skill", relative_path="SKILL.md"), SkillFileReadView)
        curator_report = client.manage_skill_curator(SkillCuratorRequest(action="report"))
        assert curator_report["mode"] == "curator"
        curator_automation = client.get_skill_curator_automation()
        assert isinstance(curator_automation, SkillCuratorAutomationStatusResponse)
        assert curator_automation.enabled is True
        curator_automation_run = client.run_skill_curator_automation(SkillCuratorAutomationRequest(force_run=True))
        assert isinstance(curator_automation_run, SkillCuratorAutomationRunResponse)
        assert curator_automation_run.ran is True
        assert isinstance(client.get_self_upgrade_health(), SelfUpgradeHealthResponse)

        tool_catalog = client.list_tool_catalog()
        assert tool_catalog and isinstance(tool_catalog[0], ToolCatalogEntryView)
        assert isinstance(client.get_tool_catalog_entry("read_file"), ToolCatalogEntryView)

        overview = client.get_memory_overview()
        assert isinstance(overview, MemoryOverviewView)

        stores = client.list_memory_stores()
        assert stores and isinstance(stores[0], MemoryStoreView)

        providers = client.list_memory_providers()
        assert providers and isinstance(providers[0], MemoryProviderView)

        jobs = client.list_reflection_jobs()
        assert jobs and isinstance(jobs[0], ReflectionJobView)
        scheduled = client.create_scheduled_task(
            ScheduledTaskCreateRequest(
                task_id="sdk-scheduled",
                name="SDK scheduled",
                prompt="Summarize the SDK thread.",
                schedule="every 1h",
                max_runs=1,
            )
        )
        assert isinstance(scheduled, ScheduledTaskView)
        assert isinstance(client.list_scheduled_tasks()[0], ScheduledTaskView)
        assert isinstance(client.update_scheduled_task("sdk-scheduled", ScheduledTaskUpdateRequest(name="SDK scheduled updated")), ScheduledTaskView)
        assert isinstance(client.pause_scheduled_task("sdk-scheduled"), ScheduledTaskView)
        assert isinstance(client.resume_scheduled_task("sdk-scheduled"), ScheduledTaskView)
        scheduled_run = client.run_scheduled_task("sdk-scheduled")
        assert isinstance(scheduled_run, ScheduledTaskRunView)
        assert scheduled_run.execution is not None and isinstance(scheduled_run.execution, ScheduledTaskExecutionView)
        assert isinstance(client.list_scheduled_task_executions(task_id="sdk-scheduled")[0], ScheduledTaskExecutionView)

        extensions = client.list_extensions()
        assert extensions and isinstance(extensions[0], ExtensionStatusView)
        assert client.list_mcp_servers() and isinstance(client.list_mcp_servers()[0], McpServerView)
        assert isinstance(client.get_mcp_server_tools("github"), McpServerToolsView)
        assert isinstance(client.get_mcp_server_provenance("github"), McpServerProvenanceView)
        resources = client.list_mcp_resources(server_id="github")
        if resources:
            assert isinstance(resources[0], CapabilityResourceView)
            resource = client.read_mcp_resource("github", resources[0].resource_id)
            assert isinstance(resource, McpResourceContentView)
        prompts = client.list_mcp_prompts(server_id="github")
        if prompts:
            assert isinstance(prompts[0], CapabilityPromptView)
            prompt = client.get_mcp_prompt("github", prompts[0].prompt_id, arguments={"repo": "anvil"})
            assert isinstance(prompt, McpPromptRenderView)

        refreshed = client.refresh_extension("github")
        assert isinstance(refreshed, ExtensionStatusView)
        assert refreshed.server_id == "github"
        assert isinstance(client.reconnect_mcp_server("github"), ExtensionStatusView)

        process_session = client.deps.process_service.spawn(
            thread_id="sdk-thread",
            command=f'"{sys.executable}" -c "print(\'sdk process\')"',
            cwd=str(client.deps.path_service.base_root / "sdk-thread" / "workspace"),
        )
        processes = client.list_process_sessions("sdk-thread")
        assert processes and isinstance(processes[0], ProcessSessionView)
        log_view = client.read_process_log("sdk-thread", process_session.session_id)
        assert isinstance(log_view, ProcessLogView)

        subagent_task = client.deps.subagent_service.submit(
            parent_thread_id="sdk-thread",
            prompt="quick task",
            parent_visible_tool_names=("read_file",),
            config_result=client.deps.config_result,
            runner=lambda: "done",
        )
        tasks = client.list_subagent_tasks("sdk-thread")
        assert tasks and isinstance(tasks[0], SubagentTaskView)
        waited = client.wait_subagent_task("sdk-thread", subagent_task.task_id)
        assert isinstance(waited, SubagentTaskView)

        http_client.post("/threads", json={"thread_id": "http-thread"})
        http_run = http_client.post("/threads/http-thread/runs", json={"message": "say hello"})
        assert http_run.status_code == 200
        assert set(sync_result.model_dump(mode="json")) == set(http_run.json())
