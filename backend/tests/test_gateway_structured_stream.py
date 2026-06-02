from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
import sys

from anvil.config import ConfigLayer, ConfigLayerKind
from conformance_helpers import names_only, parse_sse_text
from fake_models import BindableFakeMessagesListChatModel


class BindableGenericFakeChatModel(GenericFakeChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


class StreamingWordsChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "streaming-words"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="chunked streaming response"))])

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        for part in ("chunked", " ", "streaming", " ", "response"):
            chunk = AIMessageChunk(content=part)
            if run_manager is not None:
                run_manager.on_llm_new_token(part, chunk=chunk)
            yield ChatGenerationChunk(message=chunk)


def test_structured_stream_emits_message_and_reasoning_events(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content=[
                        {"type": "thinking", "thinking": "Inspecting request."},
                        {"type": "text", "text": "Streamed assistant reply."},
                    ]
                )
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-structured"})
        with client.stream(
            "POST",
            "/threads/thread-structured/runs/stream",
            json={"message": "hello"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    assert names_only(events) == [
        "run_preparing",
        "run_started",
        "step_started",
        "step_delta",
        "summary_update",
        "step_started",
        "step_delta",
        "step_updated",
        "step_updated",
        "message_completed",
        "run_completed",
    ]
    assert events[0]["data"]["phase"] == "gateway_received"
    assert events[2]["data"]["step"]["type"] == "thinking"
    assert events[3]["data"]["payload_delta"] == "Inspecting request."
    assert events[5]["data"]["step"]["type"] == "content"
    assert events[6]["data"]["payload_delta"] == "Streamed assistant reply."


def test_structured_stream_handles_chunked_message_delta_from_streaming_model(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=StreamingWordsChatModel()
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-chunked"})
        with client.stream(
            "POST",
            "/threads/thread-chunked/runs/stream",
            json={"message": "hello"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    assert names_only(events) == [
        "run_preparing",
        "run_started",
        "summary_update",
        "step_started",
        "step_delta",
        "step_delta",
        "step_delta",
        "step_delta",
        "step_delta",
        "step_updated",
        "message_completed",
        "run_completed",
    ]
    deltas = [event["data"]["payload_delta"] for event in events if event["event"] == "step_delta"]
    assert "".join(deltas) == "chunked streaming response"
    completed = next(event for event in events if event["event"] == "run_completed")
    usage = completed["data"]["state"]["context_window_usage"]
    assert usage["context_tokens"] >= 1
    assert usage["context_source"] == "estimated"
    assert usage["total_tokens"] is None
    assert usage["input_tokens"] is None
    timings = completed["data"]["state"]["runtime_phase_timings"]
    assert timings["status"] == "completed"
    assert timings["first_model_event_elapsed_ms"] is not None
    assert timings["first_content_delta_elapsed_ms"] is not None
    phase_names = [mark["phase"] for mark in timings["marks"]]
    assert "runtime_assembled" in phase_names
    assert "run_started_emitted" in phase_names
    assert "run_completed_emitted" in phase_names
    runtime_timeline = completed["data"]["state"]["runtime_operator_status"]["timeline"]
    runtime_phase_names = [
        item["source_kind"]
        for item in runtime_timeline
        if item["kind"] == "runtime"
    ]
    assert "first_content_delta" in runtime_phase_names


def test_structured_stream_exposes_execution_mode_and_rich_tool_activity(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "list_dir",
                            "args": {"path": "/mnt/user-data/workspace"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Listed the workspace."),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-tools"})
        with client.stream(
            "POST",
            "/threads/thread-stream-tools/runs/stream",
            json={"message": "list the workspace", "execution_mode": "full_access"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    assert events[0]["event"] == "run_preparing"
    assert events[0]["data"]["execution_mode"] == "full_access"
    assert events[1]["event"] == "run_started"
    assert events[1]["data"]["execution_mode"] == "full_access"
    started = next(event for event in events if event["event"] == "step_started" and event["data"]["step"]["type"] == "call")
    completed = next(event for event in events if event["event"] == "step_updated" and event["data"]["step"]["type"] == "call")
    assert started["data"]["step"]["tool_name"] == "list_dir"
    assert started["data"]["step"]["status"] == "running"
    assert started["data"]["step"]["started_at"] is not None
    assert completed["data"]["step"]["status"] == "success"
    assert completed["data"]["step"]["completed_at"] is not None
    assert completed["data"]["step"]["duration_ms"] >= 0


def test_structured_stream_emits_approval_requested_for_guarded_tool(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "/mnt/user-data/workspace/example.txt", "content": "hello"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-approval"})
        with client.stream(
            "POST",
            "/threads/thread-stream-approval/runs/stream",
            json={"message": "write a file"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    assert names_only(events) == [
        "run_preparing",
        "run_started",
        "step_started",
        "summary_update",
        "step_started",
        "step_delta",
        "approval_requested",
        "step_updated",
        "step_updated",
        "message_completed",
        "run_completed",
    ]
    assert events[6]["data"]["decision"] == "needs_user_approval"
    call_update = next(event for event in events if event["event"] == "step_updated" and event["data"]["step"]["type"] == "call")
    assert call_update["data"]["step"]["status"] == "pending"


def test_structured_stream_full_access_skips_guarded_tool_approval(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "/mnt/user-data/workspace/example.txt", "content": "hello"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-full-access"})
        with client.stream(
            "POST",
            "/threads/thread-stream-full-access/runs/stream",
            json={"message": "write a file", "execution_mode": "full_access"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    assert "approval_requested" not in names_only(events)
    assert any(event["event"] == "step_updated" and event["data"]["step"]["type"] == "call" for event in events)


def test_structured_stream_emits_process_started_for_background_run_command(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "run_command",
                            "args": {
                                "command": f'"{sys.executable}" -c "import time; print(\'bg\'); time.sleep(2)"',
                                "cwd": "/mnt/user-data/workspace",
                                "background": True,
                            },
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="spawned"),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-process"})
        with client.stream(
            "POST",
            "/threads/thread-stream-process/runs/stream",
            json={"message": "start a background process", "approval_context": "approved for this turn"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    assert "process_started" in names_only(events)


def test_structured_stream_emits_subagent_submitted_for_delegated_task(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegated_task",
                            "args": {"prompt": "inspect config"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="delegated"),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-subagent"})
        with client.stream(
            "POST",
            "/threads/thread-stream-subagent/runs/stream",
            json={"message": "delegate that"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    assert "subagent_submitted" in names_only(events)


def test_structured_stream_emits_subagent_terminal_events(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegated_task",
                            "args": {"prompt": "inspect config"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="delegated"),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-subagent-terminal"})
        with client.stream(
            "POST",
            "/threads/thread-stream-subagent-terminal/runs/stream",
            json={"message": "delegate that"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    names = names_only(events)
    assert "subagent_submitted" in names
    assert any(name in names for name in {"subagent_started", "subagent_completed"})
    subagent_step_events = [
        event
        for event in events
        if event["event"] in {"step_started", "step_updated"}
        and event["data"]["step"].get("metadata", {}).get("subagent_task_id")
    ]
    assert subagent_step_events
    assert subagent_step_events[0]["data"]["step"]["tool_name"] == "subagent"
    assert subagent_step_events[0]["data"]["step"]["status"] == "running"
    assert subagent_step_events[-1]["data"]["step"]["status"] in {"success", "error"}


def test_structured_stream_includes_known_system_version(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="versioned")]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-version"})
        client.post("/admin/reload?scope=skills")
        with client.stream(
            "POST",
            "/threads/thread-stream-version/runs/stream",
            json={"message": "hello"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    assert all("known_system_version" in event["data"] for event in events)
    assert max(event["data"]["known_system_version"] for event in events) >= 1


def test_structured_stream_translates_host_path_in_tool_events(monkeypatch, gateway_app_factory, contract_tmp_path) -> None:
    host_root = contract_tmp_path / "host-harness"
    (host_root / "Anvil").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(
        "ANVIL_PATH_BRIDGES",
        f"harness|E:\\python\\python学习\\harness|{host_root}",
    )
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "list_dir",
                            "args": {"path": "/mnt/user-data/workspace/_host/harness/Anvil"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-host"})
        with client.stream(
            "POST",
            "/threads/thread-stream-host/runs/stream",
            json={"message": r"inspect E:\python\python学习\harness\Anvil"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    started = next(event for event in events if event["event"] == "step_started" and event["data"]["step"]["type"] == "call")
    assert "/mnt/user-data/workspace/_host/harness/Anvil" in started["data"]["step"]["action"]


def test_structured_stream_translates_host_path_in_run_failed_event(monkeypatch, gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="unused")]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-fail"})
        deps = client.app.state.runtime_deps
        workspace = deps.path_service.thread_workspace_dir("thread-stream-fail")

        def fail_run_stream(*args, **kwargs):
            raise RuntimeError(f"failed while reading {workspace}")

        monkeypatch.setattr(deps.run_engine, "run_stream", fail_run_stream)
        with client.stream(
            "POST",
            "/threads/thread-stream-fail/runs/stream",
            json={"message": "trigger failure"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    failed = next(event for event in events if event["event"] == "run_failed")
    assert "/mnt/user-data/workspace" in failed["data"]["error"].replace("\\", "/")
    assert str(workspace) not in failed["data"]["error"]
    assert "known_system_version" in failed["data"]


def test_approval_stream_translates_host_path_in_run_failed_event(monkeypatch, gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "/mnt/user-data/workspace/example.txt", "content": "hello"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-approval-fail"})
        with client.stream(
            "POST",
            "/threads/thread-approval-fail/runs/stream",
            json={"message": "write a file"},
        ) as response:
            parse_sse_text("".join(response.iter_text()))
        deps = client.app.state.runtime_deps
        workspace = deps.path_service.thread_workspace_dir("thread-approval-fail")

        def fail_run_stream(*args, **kwargs):
            raise RuntimeError(f"failed while resuming {workspace}")

        monkeypatch.setattr(deps.run_engine, "run_stream", fail_run_stream)
        with client.stream(
            "POST",
            "/threads/thread-approval-fail/approvals/approve/stream",
            json={"approval_context": "approved for this turn"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    failed = next(event for event in events if event["event"] == "run_failed")
    assert "/mnt/user-data/workspace" in failed["data"]["error"].replace("\\", "/")
    assert str(workspace) not in failed["data"]["error"]
    assert "known_system_version" in failed["data"]


def test_structured_stream_emits_document_stage_events(gateway_app_factory, monkeypatch) -> None:
    from anvil.documents import ExportedDocumentResult

    def fake_export_document_file(*, output_path, content, format, mode, scratch_root, cleanup_intermediates):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"docx-bytes")
        return ExportedDocumentResult(
            output_path=output_path,
            mode=mode,
            format=format,
            provider="test-exporter",
            warnings=("layout fallback",),
            scratch_paths=(scratch_root / "draft.md",),
            cleaned_scratch_paths=(scratch_root / "export-123",),
        )

    monkeypatch.setattr("anvil.tools.assembly.export_document_file", fake_export_document_file)

    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "export_document",
                            "args": {
                                "content": "# Resume\n\nBody",
                                "output_path": "/mnt/user-data/outputs/final.docx",
                            },
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-docs"})
        with client.stream(
            "POST",
            "/threads/thread-stream-docs/runs/stream",
            json={
                "message": "export the resume",
                "execution_mode": "full_access",
                "approval_context": "approved for this turn",
            },
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    assert response.status_code == 200
    assert "document_export_started" in names_only(events)
    assert "document_export_completed" in names_only(events)
    assert "cleanup_scratch" in names_only(events)
    assert "run_warning" in names_only(events)
    assert "artifact_registered" in names_only(events)


def test_structured_stream_uses_selected_model_override(gateway_app_factory) -> None:
    custom_layers = [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    },
                    "minimax": {
                        "name": "minimax",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "mini-model",
                    },
                },
            },
        )
    ]
    app = gateway_app_factory(
        config_layers=custom_layers,
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="selected model reply")]),
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-model"})
        with client.stream(
            "POST",
            "/threads/thread-stream-model/runs/stream",
            json={"message": "hello", "selected_model": "minimax"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

    completed = next(event for event in events if event["event"] == "run_completed")
    assert completed["data"]["state"]["selected_model"] == "minimax"
