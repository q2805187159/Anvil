from __future__ import annotations

from datetime import datetime, timezone
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
import os
from pathlib import Path
from typing import Any

from anvil.agents import RecentToolActivity, ThreadLifecycleStatus, ThreadMetadataView
from app.contracts import ArtifactRefView
from app.gateway import services as gateway_services
from app.gateway.services import build_message_artifact_refs, thread_metadata_to_view
from conftest import build_gateway_config_layers
from conformance_helpers import parse_sse_text
from fake_models import BindableFakeMessagesListChatModel


class CapturingChatModel(BaseChatModel):
    def __init__(self) -> None:
        super().__init__()
        object.__setattr__(self, "captured_messages", [])

    @property
    def _llm_type(self) -> str:
        return "capturing-chat-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        object.__setattr__(self, "captured_messages", messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Captured."))])


class PartialStreamingChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "partial-streaming-detail-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="partial response"))])

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):  # type: ignore[override]
        chunk = AIMessageChunk(content="partial ")
        if run_manager is not None:
            run_manager.on_llm_new_token("partial ", chunk=chunk)
        yield ChatGenerationChunk(message=chunk)
        raise RuntimeError("stream interrupted")


def test_thread_metadata_view_sanitizes_internal_runtime_preview() -> None:
    metadata = ThreadMetadataView(
        thread_id="thread-loop-preview",
        title="Loop preview",
        status=ThreadLifecycleStatus.READY,
        updated_at=datetime.now(timezone.utc),
        last_user_message_preview="[LOOP DETECTED] You are repeating the same tool calls.",
    )

    view = thread_metadata_to_view(metadata)

    assert view.last_user_message_preview is None


def test_thread_detail_exposes_durable_transcript_and_stream_capabilities(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content=[
                        {"type": "thinking", "thinking": "Need to inspect the request carefully."},
                        {"type": "text", "text": "Hello from the assistant."},
                    ]
                )
            ]
        )
    )

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-detail"})
        assert created.status_code == 200

        run = client.post(
            "/threads/thread-detail/runs",
            json={"message": "Say hello with reasoning."},
        )
        assert run.status_code == 200

        detail = client.get("/threads/thread-detail/detail")
        assert detail.status_code == 200
        payload = detail.json()

        assert payload["thread"]["thread_id"] == "thread-detail"
        assert payload["state"]["thread_id"] == "thread-detail"
        assert payload["state"]["execution_mode"] == "agent"
        assert payload["messages"][0]["role"] == "human"
        assert payload["messages"][0]["content"] == "Say hello with reasoning."
        assert payload["messages"][1]["role"] == "ai"
        assert payload["messages"][1]["content"] == "Hello from the assistant."
        assert payload["messages"][1]["reasoning"] is None
        content_blocks = payload["messages"][1]["content_blocks"]
        assert content_blocks == []
        assert [step["type"] for step in payload["messages"][1]["steps"]] == ["thinking", "content"]
        assert payload["messages"][1]["steps"][0]["payload"] == "Need to inspect the request carefully."
        assert payload["messages"][1]["steps"][0]["visibility"] == "hidden"
        assert payload["messages"][1]["steps"][1]["payload"] == "Hello from the assistant."
        assert payload["messages"][1]["steps"][1]["visibility"] == "chat"
        assert payload["state"]["recent_tool_activity"] == []
        assert payload["stream_capabilities"] == {
            "supports_step_chain": True,
            "supports_message_delta": False,
            "supports_reasoning_delta": False,
            "supports_structured_events": True,
        }
        assert payload["state"]["last_message_interrupted"] is False


def test_thread_detail_hydrates_client_message_id_for_user_turn(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="ack")])
    )

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-client-message-id"})
        assert created.status_code == 200

        run = client.post(
            "/threads/thread-client-message-id/runs",
            json={"message": "same text", "client_message_id": "client:turn-1"},
        )
        assert run.status_code == 200

        detail = client.get("/threads/thread-client-message-id/detail")
        assert detail.status_code == 200
        payload = detail.json()

        assert payload["messages"][0]["role"] == "human"
        assert payload["messages"][0]["content"] == "same text"
        assert payload["messages"][0]["client_message_id"] == "client:turn-1"


def test_thread_detail_supports_tail_and_offset_message_windows(gateway_app_factory) -> None:
    app = gateway_app_factory()

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-window"})
        assert created.status_code == 200
        raw_state = client.app.state.runtime_deps.checkpointer.get_thread_state("thread-window")
        raw_state.conversation.messages = [
            {
                "id": f"message-{index}",
                "role": "human" if index % 2 == 0 else "ai",
                "content": f"message {index}",
            }
            for index in range(8)
        ]
        client.app.state.runtime_deps.checkpointer.put_thread_state(raw_state)

        tail = client.get("/threads/thread-window/detail?message_limit=3")
        assert tail.status_code == 200
        tail_payload = tail.json()
        assert [message["message_id"] for message in tail_payload["messages"]] == [
            "message-5",
            "message-6",
            "message-7",
        ]
        assert tail_payload["message_window"] == {
            "total": 8,
            "offset": 5,
            "limit": 3,
            "returned": 3,
            "has_more_before": True,
            "has_more_after": False,
            "truncated": True,
            "start_message_id": "message-5",
            "end_message_id": "message-7",
        }

        middle = client.get("/threads/thread-window/detail?message_offset=2&message_limit=3")
        assert middle.status_code == 200
        middle_payload = middle.json()
        assert [message["message_id"] for message in middle_payload["messages"]] == [
            "message-2",
            "message-3",
            "message-4",
        ]
        assert middle_payload["message_window"]["offset"] == 2
        assert middle_payload["message_window"]["has_more_before"] is True
        assert middle_payload["message_window"]["has_more_after"] is True

        offset_only = client.get("/threads/thread-window/detail?message_offset=2")
        assert offset_only.status_code == 422
        assert "message_offset requires message_limit" in offset_only.text


def test_thread_detail_chat_scope_defaults_to_tail_window(gateway_app_factory) -> None:
    app = gateway_app_factory()

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-default-tail-window"})
        assert created.status_code == 200
        raw_state = client.app.state.runtime_deps.checkpointer.get_thread_state("thread-default-tail-window")
        raw_state.conversation.messages = [
            {
                "id": f"message-{index}",
                "role": "human" if index % 2 == 0 else "ai",
                "content": ("old-heavy-" + "x" * 200_000) if index == 0 else f"message {index}",
            }
            for index in range(130)
        ]
        client.app.state.runtime_deps.checkpointer.put_thread_state(raw_state)

        detail = client.get("/threads/thread-default-tail-window/detail")
        assert detail.status_code == 200
        payload = detail.json()

        assert len(detail.content) < 80_000
        assert len(payload["messages"]) == 120
        assert payload["messages"][0]["message_id"] == "message-10"
        assert payload["messages"][-1]["message_id"] == "message-129"
        assert payload["message_window"] == {
            "total": 130,
            "offset": 10,
            "limit": 120,
            "returned": 120,
            "has_more_before": True,
            "has_more_after": False,
            "truncated": True,
            "start_message_id": "message-10",
            "end_message_id": "message-129",
        }
        assert "old-heavy-" not in detail.text

        full_detail = client.get("/threads/thread-default-tail-window/detail?state_scope=full")
        assert full_detail.status_code == 200
        full_payload = full_detail.json()
        assert len(full_payload["messages"]) == 130
        assert full_payload["message_window"]["limit"] is None
        assert full_payload["message_window"]["truncated"] is False
        assert "old-heavy-" in full_detail.text


def test_thread_detail_defaults_to_light_chat_state(gateway_app_factory) -> None:
    app = gateway_app_factory()

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-light-detail"})
        assert created.status_code == 200
        raw_state = client.app.state.runtime_deps.checkpointer.get_thread_state("thread-light-detail")
        raw_state.conversation.messages = [
            {"id": "message-0", "role": "human", "content": "first"},
            {"id": "message-1", "role": "ai", "content": "reply"},
            {"id": "message-2", "role": "human", "content": "second"},
        ]
        raw_state.capabilities.visible_tool_names = [f"tool_{index}" for index in range(500)]
        raw_state.capabilities.deferred_tool_names = [f"deferred_{index}" for index in range(500)]
        raw_state.capabilities.enabled_skill_ids = [f"skill_{index}" for index in range(500)]
        raw_state.durable_subagent_job_history = [{"payload": "x" * 300_000}]
        client.app.state.runtime_deps.checkpointer.put_thread_state(raw_state)

        detail = client.get("/threads/thread-light-detail/detail?message_limit=120")
        assert detail.status_code == 200
        payload = detail.json()

        assert len(detail.content) < 50_000
        assert [message["message_id"] for message in payload["messages"]] == ["message-0", "message-1", "message-2"]
        assert payload["state"]["visible_tool_names"] == []
        assert payload["state"]["deferred_tool_names"] == []
        assert payload["state"]["enabled_skill_ids"] == []
        assert payload["state"]["durable_subagent_job_history"] == []
        assert payload["state"]["recent_tool_activity"] == []
        assert payload["state"]["runtime_path_roots"] == []
        assert "plan_mode_enabled" in payload["state"]["runtime_capabilities"]
        assert payload["state"]["runtime_capabilities"]["skills_count"] == 0

        full_detail = client.get("/threads/thread-light-detail/detail?message_limit=120&state_scope=full")
        assert full_detail.status_code == 200
        full_payload = full_detail.json()
        assert len(full_detail.content) > len(detail.content)
        assert len(full_payload["state"]["visible_tool_names"]) == 500
        assert len(full_payload["state"]["durable_subagent_job_history"]) == 1


def test_thread_detail_full_scope_does_not_live_discover_runtime_capabilities(monkeypatch, gateway_app_factory) -> None:
    app = gateway_app_factory()
    heavy_calls = 0
    summary_calls = 0
    original_summary_view = gateway_services.build_runtime_capabilities_summary_view

    def fail_heavy_capability_view(_deps):
        nonlocal heavy_calls
        heavy_calls += 1
        raise AssertionError("thread detail full scope must not live-discover runtime capabilities")

    def capture_summary_capability_view(_deps):
        nonlocal summary_calls
        summary_calls += 1
        return original_summary_view(_deps)

    monkeypatch.setattr(gateway_services, "build_runtime_capabilities_view", fail_heavy_capability_view)
    monkeypatch.setattr(gateway_services, "build_runtime_capabilities_summary_view", capture_summary_capability_view)

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-full-detail-summary-capabilities"})
        assert created.status_code == 200

        detail = client.get("/threads/thread-full-detail-summary-capabilities/detail?state_scope=full&message_limit=120")
        assert detail.status_code == 200
        payload = detail.json()

    assert payload["state"]["thread_id"] == "thread-full-detail-summary-capabilities"
    assert "plan_mode_enabled" in payload["state"]["runtime_capabilities"]
    assert heavy_calls == 0
    assert summary_calls == 1


def test_thread_detail_message_window_ignores_out_of_window_tool_payloads(gateway_app_factory) -> None:
    app = gateway_app_factory()

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-window-heavy-tools"})
        assert created.status_code == 200
        raw_state = client.app.state.runtime_deps.checkpointer.get_thread_state("thread-window-heavy-tools")
        raw_state.conversation.messages = [
            {"id": "old-user", "role": "human", "content": "old request"},
            {
                "id": "old-ai",
                "role": "ai",
                "content": "",
                "tool_calls": [
                    {
                        "name": "read_file",
                        "args": {"path": "/mnt/user-data/workspace/huge.txt"},
                        "id": "old-call",
                        "type": "tool_call",
                    }
                ],
            },
            {
                "id": "old-tool",
                "role": "tool",
                "tool_call_id": "old-call",
                "name": "read_file",
                "content": "x" * 400_000,
            },
            {"id": "latest-user", "role": "human", "content": "latest request"},
            {"id": "latest-ai", "role": "ai", "content": "latest reply"},
        ]
        raw_state.conversation.steps = [
            {
                "step_id": "old-ai:call:old-call",
                "message_id": "old-ai",
                "type": "call",
                "title": "已读取文件",
                "status": "success",
                "payload": "y" * 300_000,
                "language": "text",
                "order": 0,
            }
        ]
        client.app.state.runtime_deps.checkpointer.put_thread_state(raw_state)

        detail = client.get("/threads/thread-window-heavy-tools/detail?message_limit=2")
        assert detail.status_code == 200
        payload = detail.json()

        assert len(detail.content) < 30_000
        assert [message["message_id"] for message in payload["messages"]] == ["latest-user", "latest-ai"]
        assert "old-call" not in detail.text
        assert "x" * 100 not in detail.text
        assert "y" * 100 not in detail.text


def test_thread_detail_builds_tool_and_step_lookups_from_message_window_only(monkeypatch, gateway_app_factory) -> None:
    app = gateway_app_factory()
    lookup_sizes: dict[str, int] = {}
    original_tool_lookup = gateway_services.build_tool_result_lookup
    original_step_lookup = gateway_services.build_message_steps_lookup
    original_activity_lookup = gateway_services.build_tool_activity_lookup

    def capture_tool_result_lookup(messages):
        lookup_sizes["messages"] = len(messages)
        return original_tool_lookup(messages)

    def capture_message_steps_lookup(steps):
        lookup_sizes["steps"] = len(steps)
        return original_step_lookup(steps)

    def capture_tool_activity_lookup(items):
        lookup_sizes["activities"] = len(items)
        return original_activity_lookup(items)

    monkeypatch.setattr(gateway_services, "build_tool_result_lookup", capture_tool_result_lookup)
    monkeypatch.setattr(gateway_services, "build_message_steps_lookup", capture_message_steps_lookup)
    monkeypatch.setattr(gateway_services, "build_tool_activity_lookup", capture_tool_activity_lookup)

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-window-lookup-scope"})
        assert created.status_code == 200
        raw_state = client.app.state.runtime_deps.checkpointer.get_thread_state("thread-window-lookup-scope")
        raw_state.conversation.messages = [
            {"id": f"old-{index}", "role": "tool", "tool_call_id": f"old-call-{index}", "content": "x" * 200_000}
            for index in range(5)
        ] + [
            {"id": "latest-user", "role": "human", "content": "latest request"},
            {"id": "latest-ai", "role": "ai", "content": "latest reply"},
        ]
        raw_state.conversation.steps = [
            {
                "step_id": f"old-{index}:step",
                "message_id": f"old-{index}",
                "type": "call",
                "title": "旧工具",
                "status": "success",
                "payload": "y" * 200_000,
                "language": "text",
                "order": index,
            }
            for index in range(5)
        ]
        raw_state.execution.recent_tool_activity = [
            RecentToolActivity(
                tool_call_id=f"old-call-{index}",
                message_id=f"old-{index}",
                name="read_file",
                status="completed",
                result_text="z" * 200_000,
            )
            for index in range(5)
        ]
        client.app.state.runtime_deps.checkpointer.put_thread_state(raw_state)

        detail = client.get("/threads/thread-window-lookup-scope/detail?message_limit=2")
        assert detail.status_code == 200

    assert lookup_sizes == {"messages": 2, "steps": 0, "activities": 0}


def test_thread_detail_chat_scope_does_not_build_full_artifact_refs(monkeypatch, gateway_app_factory) -> None:
    app = gateway_app_factory()
    canonical_calls = 0
    original_build_canonical = gateway_services.build_canonical_artifact_refs

    def capture_build_canonical(deps, thread_id):
        nonlocal canonical_calls
        canonical_calls += 1
        return original_build_canonical(deps, thread_id)

    monkeypatch.setattr(gateway_services, "build_canonical_artifact_refs", capture_build_canonical)

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-chat-artifact-window"})
        assert created.status_code == 200
        raw_state = client.app.state.runtime_deps.checkpointer.get_thread_state("thread-chat-artifact-window")
        raw_state.conversation.messages = [
            {
                "id": "old-user",
                "role": "human",
                "content": "old upload",
                "additional_kwargs": {"uploaded_filenames": ["old.txt"]},
            },
            {"id": "old-ai", "role": "ai", "content": "old reply"},
            {
                "id": "latest-user",
                "role": "human",
                "content": "current upload",
                "additional_kwargs": {
                    "files": [
                        {
                            "filename": "current.txt",
                            "virtual_path": "/mnt/user-data/uploads/current.txt",
                        }
                    ]
                },
            },
            {"id": "latest-ai", "role": "ai", "content": "current reply"},
        ]
        raw_state.artifacts.uploaded_files = [
            {
                "filename": "old.txt",
                "virtual_path": "/mnt/user-data/uploads/old.txt",
                "artifact_url": "/threads/thread-chat-artifact-window/artifacts/uploads/old.txt",
            },
            {
                "filename": "current.txt",
                "virtual_path": "/mnt/user-data/uploads/current.txt",
                "artifact_url": "/threads/thread-chat-artifact-window/artifacts/uploads/current.txt",
            },
        ]
        raw_state.artifacts.output_artifacts = ["large/output.txt"]
        client.app.state.runtime_deps.checkpointer.put_thread_state(raw_state)

        detail = client.get("/threads/thread-chat-artifact-window/detail?message_limit=2")
        assert detail.status_code == 200
        payload = detail.json()

        assert canonical_calls == 0
        assert [item["label"] for item in payload["state"]["uploaded_files"]] == ["current.txt"]
        assert payload["state"]["output_artifacts"] == []
        latest_user = next(message for message in payload["messages"] if message["message_id"] == "latest-user")
        assert [item["label"] for item in latest_user["artifact_refs"]] == ["current.txt"]

        full_detail = client.get("/threads/thread-chat-artifact-window/detail?message_limit=2&state_scope=full")
        assert full_detail.status_code == 200
        full_payload = full_detail.json()

        assert canonical_calls == 1
        assert [item["label"] for item in full_payload["state"]["uploaded_files"]] == ["old.txt", "current.txt"]
        assert [item["label"] for item in full_payload["state"]["output_artifacts"]] == ["large/output.txt"]


def test_thread_detail_hydrates_streamed_terminal_steps_without_reopening(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content=[
                        {"type": "thinking", "thinking": "Check durable step state."},
                        {"type": "text", "text": "Durable answer."},
                    ]
                )
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-detail-stream-steps"})
        with client.stream(
            "POST",
            "/threads/thread-detail-stream-steps/runs/stream",
            json={"message": "stream then refresh"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

        assert response.status_code == 200
        stream_terminal_steps = {
            event["data"]["step"]["step_id"]: event["data"]["step"]
            for event in events
            if event["event"] == "step_updated"
            and event["data"]["step"]["type"] in {"thinking", "content"}
        }
        assert {step["type"] for step in stream_terminal_steps.values()} == {"thinking", "content"}
        assert all(step["status"] == "success" for step in stream_terminal_steps.values())
        assert all(step["completed_at"] is not None for step in stream_terminal_steps.values())

        detail = client.get("/threads/thread-detail-stream-steps/detail")
        assert detail.status_code == 200
        assistant_message = next(message for message in detail.json()["messages"] if message["role"] == "ai")
        detail_steps = assistant_message["steps"]

        assert assistant_message["stream_status"] == "complete"
        assert [step["type"] for step in detail_steps] == ["thinking", "content"]
        assert len({step["step_id"] for step in detail_steps}) == len(detail_steps)
        assert {step["step_id"] for step in detail_steps} == set(stream_terminal_steps)
        assert all(step["status"] == "success" for step in detail_steps)
        assert all(step["completed_at"] is not None for step in detail_steps)
        assert all(step["status"] != "running" for step in detail_steps)
        for step in detail_steps:
            streamed = stream_terminal_steps[step["step_id"]]
            assert step["payload"] == streamed["payload"]
            assert step["status"] == streamed["status"]


def test_thread_detail_projects_active_run_from_event_log(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="event-log answer")])
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-detail-active-event-log"})
        with client.stream(
            "POST",
            "/threads/thread-detail-active-event-log/runs/stream",
            json={"message": "hydrate the active run"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

        assert response.status_code == 200
        run_id = next(event["data"]["run_id"] for event in events if event["data"].get("run_id"))
        assistant_message_id = next(
            event["data"]["message_id"]
            for event in events
            if event["event"] == "message_completed" and event["data"].get("message_id")
        )
        deps = client.app.state.runtime_deps
        raw_state = deps.checkpointer.get_thread_state("thread-detail-active-event-log")
        raw_state.lifecycle.status = ThreadLifecycleStatus.RUNNING
        raw_state.identity.run_id = run_id
        raw_state.conversation.messages = [
            {"id": "stale-user", "role": "human", "content": "hydrate the active run"},
            {"id": assistant_message_id, "role": "ai", "content": "stale snapshot answer"},
        ]
        raw_state.conversation.steps = []
        deps.checkpointer.put_thread_state(raw_state)

        detail = client.get("/threads/thread-detail-active-event-log/detail")

        assert detail.status_code == 200
        payload = detail.json()
        assistant_message = next(message for message in payload["messages"] if message["role"] == "ai")

        assert payload["state"]["status"] == "completed"
        assert payload["state"]["runtime_phase_timings"]["event_log"]["last_kind"] == "run_completed"
        assert assistant_message["message_id"] == assistant_message_id
        assert assistant_message["content"] == "event-log answer"
        assert "stale snapshot answer" not in repr(payload["messages"])


def test_thread_detail_auto_projects_completed_thread_from_full_event_log(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="first detail answer"), AIMessage(content="second detail answer")]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-detail-event-log-auto"})
        for message in ("first detail question", "second detail question"):
            with client.stream(
                "POST",
                "/threads/thread-detail-event-log-auto/runs/stream",
                json={"message": message, "execution_mode": "chat"},
            ) as response:
                assert response.status_code == 200
                parse_sse_text("".join(response.iter_text()))

        deps = client.app.state.runtime_deps
        raw_state = deps.checkpointer.get_thread_state("thread-detail-event-log-auto")
        raw_state.conversation.messages = [
            {"id": "stale-user", "role": "human", "content": "stale snapshot question"},
            {"id": "stale-ai", "role": "ai", "content": "stale snapshot answer"},
        ]
        raw_state.conversation.steps = []
        deps.checkpointer.put_thread_state(raw_state)

        detail = client.get("/threads/thread-detail-event-log-auto/detail")

        assert detail.status_code == 200
        payload = detail.json()

        assert [message["role"] for message in payload["messages"]] == ["human", "ai", "human", "ai"]
        assert [message["content"] for message in payload["messages"]] == [
            "first detail question",
            "first detail answer",
            "second detail question",
            "second detail answer",
        ]
        assert payload["state"]["runtime_phase_timings"]["event_log"]["run_count"] == 2
        assert "stale snapshot answer" not in repr(payload["messages"])


def test_thread_detail_snapshot_source_keeps_snapshot_for_legacy_debug(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="event detail answer")])
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-detail-snapshot-source"})
        with client.stream(
            "POST",
            "/threads/thread-detail-snapshot-source/runs/stream",
            json={"message": "event detail question"},
        ) as response:
            assert response.status_code == 200
            parse_sse_text("".join(response.iter_text()))

        deps = client.app.state.runtime_deps
        raw_state = deps.checkpointer.get_thread_state("thread-detail-snapshot-source")
        raw_state.conversation.messages = [
            {"id": "snapshot-user", "role": "human", "content": "snapshot question"},
            {"id": "snapshot-ai", "role": "ai", "content": "snapshot answer"},
        ]
        raw_state.conversation.steps = []
        deps.checkpointer.put_thread_state(raw_state)

        detail = client.get("/threads/thread-detail-snapshot-source/detail?state_source=snapshot")

        assert detail.status_code == 200
        payload = detail.json()
        assert [message["content"] for message in payload["messages"]] == ["snapshot question", "snapshot answer"]


def test_thread_detail_hydrates_interrupted_stream_status(gateway_app_factory) -> None:
    app = gateway_app_factory(chat_model_override=PartialStreamingChatModel())

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-detail-interrupted"})
        with client.stream(
            "POST",
            "/threads/thread-detail-interrupted/runs/stream",
            json={"message": "stream until interrupted"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

        assert response.status_code == 200
        completed = next(event for event in events if event["event"] == "message_completed")
        assert completed["data"]["stream_status"] == "interrupted"
        run_completed = next(event for event in events if event["event"] == "run_completed")
        assert run_completed["data"]["stream_status"] == "interrupted"

        detail = client.get("/threads/thread-detail-interrupted/detail")
        assert detail.status_code == 200
        payload = detail.json()
        assistant_message = next(message for message in payload["messages"] if message["role"] == "ai")
        content_step = next(step for step in assistant_message["steps"] if step["type"] == "content")

        assert payload["state"]["last_message_interrupted"] is True
        assert assistant_message["stream_status"] == "interrupted"
        assert assistant_message["status"] == "interrupted"
        assert assistant_message["content"] == "partial "
        assert content_step["status"] == "error"
        assert content_step["payload"] == "partial "
        assert content_step["completed_at"] is not None


def test_thread_detail_filters_model_only_view_image_bridge(gateway_app_factory) -> None:
    app = gateway_app_factory()

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-model-only-bridge"})
        assert created.status_code == 200
        deps = client.app.state.runtime_deps
        state = deps.checkpointer.get_thread_state("thread-model-only-bridge")
        assert state is not None
        state.conversation.messages = [
            {
                "id": "user-1",
                "role": "human",
                "content": "visible user message",
            },
            {
                "id": "bridge-1",
                "role": "human",
                "content": [
                    {
                        "type": "text",
                        "text": "Images returned by view_image are attached below for visual analysis.",
                    },
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
                ],
                "additional_kwargs": {
                    "anvil_view_image_injection": True,
                    "anvil_model_only": True,
                    "visibility": "model_only",
                },
            },
            {
                "id": "assistant-1",
                "role": "ai",
                "content": "visible answer",
            },
        ]
        deps.checkpointer.put_thread_state(state)
        detail = client.get("/threads/thread-model-only-bridge/detail")
        assert detail.status_code == 200
        payload = detail.json()

    assert [message["message_id"] for message in payload["messages"]] == ["user-1", "assistant-1"]
    assert "Images returned by view_image" not in repr(payload["messages"])


def test_thread_detail_filters_legacy_empty_final_diagnostic_message(gateway_app_factory) -> None:
    app = gateway_app_factory()

    with TestClient(app) as client:
        created = client.post("/threads", json={"thread_id": "thread-legacy-empty-final"})
        assert created.status_code == 200
        deps = client.app.state.runtime_deps
        state = deps.checkpointer.get_thread_state("thread-legacy-empty-final")
        assert state is not None
        legacy_text = (
            "The model stopped after tool execution without producing a final answer. "
            "The run was marked interrupted so you can continue from the available tool results."
        )
        state.lifecycle.status = ThreadLifecycleStatus.INTERRUPTED
        state.conversation.messages = [
            {"id": "user-1", "role": "human", "content": "visible user message"},
            {"id": "assistant-empty-final", "role": "ai", "content": legacy_text, "status": "interrupted"},
        ]
        state.conversation.steps = [
            {
                "step_id": "assistant-empty-final:content",
                "message_id": "assistant-empty-final",
                "type": "content",
                "title": "最终回答",
                "status": "error",
                "payload": legacy_text,
                "language": "markdown",
                "visibility": "chat",
            }
        ]
        deps.checkpointer.put_thread_state(state)

        detail = client.get("/threads/thread-legacy-empty-final/detail")
        assert detail.status_code == 200
        payload = detail.json()

    assistant_message = next(message for message in payload["messages"] if message["message_id"] == "assistant-empty-final")
    assert assistant_message["status"] == "interrupted"
    assert assistant_message["stream_status"] == "interrupted"
    assert assistant_message["content"] == ""
    assert all(step["visibility"] != "chat" or legacy_text not in step["payload"] for step in assistant_message["steps"])
    assert legacy_text not in repr(payload)


def test_thread_detail_hydrates_streamed_subagent_terminal_step(gateway_app_factory) -> None:
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
        client.post("/threads", json={"thread_id": "thread-detail-subagent-steps"})
        with client.stream(
            "POST",
            "/threads/thread-detail-subagent-steps/runs/stream",
            json={"message": "delegate that"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

        assert response.status_code == 200
        streamed_subagent_steps = [
            event["data"]["step"]
            for event in events
            if event["event"] in {"step_started", "step_updated"}
            and event["data"]["step"].get("metadata", {}).get("subagent_task_id")
        ]
        assert streamed_subagent_steps
        terminal_stream_step = streamed_subagent_steps[-1]
        assert terminal_stream_step["status"] in {"success", "error"}
        assert terminal_stream_step["tool_name"] == "subagent"
        assert terminal_stream_step["metadata"]["subagent_task_id"]

        detail = client.get("/threads/thread-detail-subagent-steps/detail")
        assert detail.status_code == 200
        assistant_messages = [message for message in detail.json()["messages"] if message["role"] == "ai"]
        detail_subagent_steps = [
            step
            for message in assistant_messages
            for step in message["steps"]
            if step.get("metadata", {}).get("subagent_task_id")
        ]

        assert len(detail_subagent_steps) == 1
        detail_step = detail_subagent_steps[0]
        assert detail_step["step_id"] == terminal_stream_step["step_id"]
        assert detail_step["tool_name"] == "subagent"
        assert detail_step["status"] == terminal_stream_step["status"]
        assert detail_step["payload"] == terminal_stream_step["payload"]
        assert detail_step["metadata"]["subagent_task_id"] == terminal_stream_step["metadata"]["subagent_task_id"]
        assert detail_step["completed_at"] is not None


def test_thread_detail_includes_pending_approval_projection(gateway_app_factory) -> None:
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
        client.post("/threads", json={"thread_id": "thread-approval-detail"})
        run = client.post(
            "/threads/thread-approval-detail/runs",
            json={"message": "Write a file."},
        )
        assert run.status_code == 200
        assert run.json()["status"] == "awaiting_approval"

        detail = client.get("/threads/thread-approval-detail/detail")
        assert detail.status_code == 200
        payload = detail.json()

        assert payload["state"]["has_pending_approval"] is True
        assert payload["pending_approval"]["decision"] == "needs_user_approval"
        assert payload["pending_approval"]["reason"]
        assert payload["messages"][-1]["approval"]["decision"] == "needs_user_approval"


def test_thread_detail_uses_stable_message_ids_and_artifact_refs(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="Stored artifact references.")]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-artifacts"})
        upload = client.post(
            "/threads/thread-artifacts/uploads",
            files=[("files", ("notes.txt", b"artifact body", "text/plain"))],
        )
        assert upload.status_code == 200

        run = client.post(
            "/threads/thread-artifacts/runs",
            json={"message": "Describe the upload.", "uploaded_filenames": ["notes.txt"]},
        )
        assert run.status_code == 200

        detail = client.get("/threads/thread-artifacts/detail")
        assert detail.status_code == 200
        payload = detail.json()

        assert payload["messages"][0]["message_id"]
        assert payload["messages"][1]["message_id"]
        assert payload["messages"][0]["message_id"] != payload["messages"][1]["message_id"]

        artifact_refs = payload["messages"][0]["artifact_refs"]
        assert artifact_refs
        assert artifact_refs[0]["kind"] == "upload"
        assert artifact_refs[0]["artifact_url"].endswith("/threads/thread-artifacts/artifacts/uploads/notes.txt")
        assert payload["state"]["uploaded_files"][0]["kind"] == "upload"
        assert payload["state"]["uploaded_files"][0]["artifact_url"].endswith("/threads/thread-artifacts/artifacts/uploads/notes.txt")


def test_thread_detail_scopes_upload_artifact_refs_to_their_user_turn(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content=f"Reply {index}.") for index in range(12)]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-turn-artifacts"})
        client.post(
            "/threads/thread-turn-artifacts/uploads",
            files=[("files", ("first.txt", b"first", "text/plain"))],
        )
        client.post(
            "/threads/thread-turn-artifacts/runs",
            json={"message": "Use the first file.", "uploaded_filenames": ["first.txt"]},
        )
        client.post(
            "/threads/thread-turn-artifacts/runs",
            json={"message": "No file this turn."},
        )
        client.post(
            "/threads/thread-turn-artifacts/uploads",
            files=[("files", ("third.txt", b"third", "text/plain"))],
        )
        client.post(
            "/threads/thread-turn-artifacts/runs",
            json={"message": "Use the third file.", "uploaded_filenames": ["third.txt"]},
        )

        detail = client.get("/threads/thread-turn-artifacts/detail")
        assert detail.status_code == 200
        messages = detail.json()["messages"]
        human_messages = [message for message in messages if message["role"] == "human"]
        assistant_messages = [message for message in messages if message["role"] in {"ai", "assistant"}]

        assert [[ref["label"] for ref in message["artifact_refs"]] for message in human_messages] == [
            ["first.txt"],
            [],
            ["third.txt"],
        ]
        assert all(ref["kind"] != "upload" for message in assistant_messages for ref in message["artifact_refs"])


def test_current_upload_paths_are_injected_to_model_but_hidden_from_transcript(gateway_app_factory) -> None:
    chat_model = CapturingChatModel()
    app = gateway_app_factory(chat_model_override=chat_model)

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-upload-context"})
        upload = client.post(
            "/threads/thread-upload-context/uploads",
            files=[("files", ("brief.txt", b"file body", "text/plain"))],
        )
        assert upload.status_code == 200

        run = client.post(
            "/threads/thread-upload-context/runs",
            json={"message": "Summarize this file.", "uploaded_filenames": ["brief.txt"]},
        )
        assert run.status_code == 200

        human_contents = [message.content for message in chat_model.captured_messages if message.type == "human"]
        assert any("<attached_files>" in content for content in human_contents)
        assert any("/mnt/user-data/uploads/brief.txt" in content for content in human_contents)

        detail = client.get("/threads/thread-upload-context/detail")
        assert detail.status_code == 200
        first_message = detail.json()["messages"][0]
        assert first_message["content"] == "Summarize this file."
        assert "<attached_files>" not in first_message["content"]
        assert [ref["label"] for ref in first_message["artifact_refs"]] == ["brief.txt"]


def test_current_image_upload_is_sent_as_multimodal_content_for_vision_model(
    gateway_app_factory,
    contract_tmp_path,
) -> None:
    config_layers = build_gateway_config_layers(contract_tmp_path)
    config_layers[0].data["models"]["openai"]["supports_vision"] = True
    chat_model = CapturingChatModel()
    app = gateway_app_factory(config_layers=config_layers, chat_model_override=chat_model)

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-image-upload"})
        upload = client.post(
            "/threads/thread-image-upload/uploads",
            files=[("files", ("diagram.png", b"\x89PNG\r\n\x1a\nsample-image", "image/png"))],
        )
        assert upload.status_code == 200

        run = client.post(
            "/threads/thread-image-upload/runs",
            json={"message": "Analyze the image.", "uploaded_filenames": ["diagram.png"]},
        )
        assert run.status_code == 200

        human_messages = [message for message in chat_model.captured_messages if message.type == "human"]
        content = human_messages[-1].content
        assert isinstance(content, list)
        assert any(
            isinstance(block, dict)
            and block.get("type") == "text"
            and "Analyze the image." in str(block.get("text", ""))
            for block in content
        )
        image_blocks = [
            block
            for block in content
            if isinstance(block, dict) and block.get("type") == "image_url"
        ]
        assert len(image_blocks) == 1
        image_url = image_blocks[0]["image_url"]
        if isinstance(image_url, dict):
            image_url = image_url.get("url")
        assert isinstance(image_url, str)
        assert image_url.startswith("data:image/png;base64,")

        detail = client.get("/threads/thread-image-upload/detail")
        assert detail.status_code == 200
        first_message = detail.json()["messages"][0]
        assert first_message["content"] == "Analyze the image."
        assert [ref["label"] for ref in first_message["artifact_refs"]] == ["diagram.png"]


def test_image_upload_requires_vision_capable_model(gateway_app_factory) -> None:
    chat_model = CapturingChatModel()
    app = gateway_app_factory(chat_model_override=chat_model)

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-image-needs-vision"})
        upload = client.post(
            "/threads/thread-image-needs-vision/uploads",
            files=[("files", ("diagram.png", b"\x89PNG\r\n\x1a\nsample-image", "image/png"))],
        )
        assert upload.status_code == 200

        run = client.post(
            "/threads/thread-image-needs-vision/runs",
            json={"message": "Analyze the image.", "uploaded_filenames": ["diagram.png"]},
        )
        assert run.status_code == 503
        payload = run.json()
        assert payload["error"] == "runtime_unavailable"
        assert "lacks required capabilities: vision" in payload["detail"]
        assert chat_model.captured_messages == []


def test_output_artifacts_are_not_attached_to_every_assistant_message() -> None:
    output_ref = ArtifactRefView(
        kind="output",
        label="tool-results/tool_catalog-a60f0bd4ee7b.txt",
        artifact_url="/threads/thread-a/artifacts/outputs/tool-results/tool_catalog-a60f0bd4ee7b.txt",
        virtual_path="/mnt/user-data/outputs/tool-results/tool_catalog-a60f0bd4ee7b.txt",
    )

    assert build_message_artifact_refs({"role": "assistant"}, upload_refs=[], output_refs=[output_ref]) == []


def test_thread_detail_exposes_markdown_companion_metadata_for_uploads(gateway_app_factory, monkeypatch) -> None:
    from anvil.uploads import service as upload_service_module
    from anvil.uploads.conversion import DocumentConversionResult

    def fake_convert_document_to_markdown(file_path: Path, *, config):
        markdown_path = file_path.with_suffix(".md")
        markdown_path.write_text("# Resume outline\n\nBody", encoding="utf-8")
        return DocumentConversionResult(
            extension=".pdf",
            markdown_path=markdown_path,
            outline=[{"title": "Resume outline", "line": 1}],
            outline_preview=[],
            converter_used="test-converter",
            ocr_used=False,
            conversion_error=None,
        )

    monkeypatch.setattr(upload_service_module, "convert_document_to_markdown", fake_convert_document_to_markdown)

    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="Stored artifact references.")]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-artifact-companion"})
        upload = client.post(
            "/threads/thread-artifact-companion/uploads",
            files=[("files", ("resume.pdf", b"%PDF-1.4 fake", "application/pdf"))],
        )
        assert upload.status_code == 200

        detail = client.get("/threads/thread-artifact-companion/detail?state_scope=full")
        assert detail.status_code == 200
        payload = detail.json()

        upload_ref = payload["state"]["uploaded_files"][0]
        assert upload_ref["markdown_file"] == "resume.md"
        assert upload_ref["markdown_artifact_url"].endswith("/threads/thread-artifact-companion/artifacts/uploads/resume.md")
        assert upload_ref["outline"][0]["title"] == "Resume outline"


def test_thread_detail_associates_tool_result_with_tool_call(gateway_app_factory) -> None:
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
        client.post("/threads", json={"thread_id": "thread-tool-link"})
        run = client.post(
            "/threads/thread-tool-link/runs",
            json={"message": "List the workspace."},
        )
        assert run.status_code == 200

        detail = client.get("/threads/thread-tool-link/detail?state_scope=full")
        assert detail.status_code == 200
        payload = detail.json()

        ai_tool_message = next(message for message in payload["messages"] if message["role"] == "ai" and message["tool_calls"])
        tool_call = ai_tool_message["tool_calls"][0]
        assert tool_call["tool_call_id"] == "call_1"
        assert tool_call["name"] == "list_dir"
        assert tool_call["display_name"] == "List Directory"
        assert tool_call["source_kind"] == "builtin"
        assert tool_call["source_id"] == "core"
        assert tool_call["capability_group"] == "filesystem"
        assert tool_call["tool_execution_mode"] == "sync"
        assert tool_call["status"] == "completed"
        assert tool_call["result_text"] is not None
        assert tool_call["started_at"] is not None
        assert tool_call["completed_at"] is not None
        assert tool_call["duration_ms"] >= 0

        recent_activity = payload["state"]["recent_tool_activity"]
        assert recent_activity
        assert recent_activity[0]["tool_call_id"] == "call_1"
        assert recent_activity[0]["name"] == "list_dir"
        assert recent_activity[0]["display_name"] == "List Directory"
        assert recent_activity[0]["status"] == "completed"
        assert recent_activity[0]["duration_ms"] >= 0
        operator_status = payload["state"]["runtime_operator_status"]
        assert operator_status["status"] in {"completed", "ready"}
        assert operator_status["completed_tool_count"] >= 1
        assert operator_status["timeline"][0]["kind"] == "tool"
        assert operator_status["timeline"][0]["title"] == "List Directory"
        assert operator_status["timeline"][0]["status"] == "completed"


def test_thread_detail_translates_host_path_history_and_tool_surfaces(monkeypatch, gateway_app_factory, contract_tmp_path) -> None:
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
                AIMessage(content="Inspected E:\\python\\python学习\\harness\\Anvil"),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-host-path"})
        run = client.post(
            "/threads/thread-host-path/runs",
            json={"message": r"inspect E:\python\python学习\harness\Anvil"},
        )
        assert run.status_code == 200

        raw_state = client.app.state.runtime_deps.checkpointer.get_thread_state("thread-host-path")
        assert raw_state is not None
        assert raw_state.conversation.messages[0]["content"] == r"inspect E:\python\python学习\harness\Anvil"

        detail = client.get("/threads/thread-host-path/detail")
        assert detail.status_code == 200
        payload = detail.json()

        assert payload["messages"][0]["content"] == r"inspect E:\python\python学习\harness\Anvil"
        ai_tool_message = next(message for message in payload["messages"] if message["role"] == "ai" and message["tool_calls"])
        assert ai_tool_message["tool_calls"][0]["args"]["path"] == "/mnt/user-data/workspace/_host/harness/Anvil"


def test_thread_detail_displays_legacy_runtime_user_paths_as_host_paths(monkeypatch, gateway_app_factory, contract_tmp_path) -> None:
    host_root = contract_tmp_path / "e-drive"
    host_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ANVIL_PATH_BRIDGES", f"e_drive|E:\\|{host_root}")
    app = gateway_app_factory()

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-legacy-runtime-path"})
        raw_state = client.app.state.runtime_deps.checkpointer.get_thread_state("thread-legacy-runtime-path")
        assert raw_state is not None
        raw_state.conversation.messages = [
            {
                "id": "legacy-user",
                "role": "human",
                "content": "在“/mnt/user-data/workspace/_host/e_drive/临时下载”目录下生成一个PPT",
            }
        ]
        client.app.state.runtime_deps.checkpointer.put_thread_state(raw_state)

        detail = client.get("/threads/thread-legacy-runtime-path/detail")
        assert detail.status_code == 200

        assert detail.json()["messages"][0]["content"] == r"在“E:\临时下载”目录下生成一个PPT"
