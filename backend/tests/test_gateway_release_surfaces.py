from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient
from conformance_helpers import names_only, parse_sse_text
from langchain_core.messages import AIMessage

from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService
from anvil.subagents import SubagentService
from fake_models import BindableFakeMessagesListChatModel


def test_gateway_can_resume_pending_approval(gateway_app_factory) -> None:
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
        client.post("/threads", json={"thread_id": "thread-approval"})
        first = client.post("/threads/thread-approval/runs", json={"message": "write a file"})
        assert first.status_code == 200
        assert first.json()["status"] == "awaiting_approval"

        resumed = client.post(
            "/threads/thread-approval/approvals/approve",
            json={"approval_context": "approved for this turn"},
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "completed"

        artifact = client.get("/threads/thread-approval/artifacts/outputs/example.txt")
        # write_file stores in workspace, not outputs, so inspect state instead of artifact endpoint
        state = client.get("/threads/thread-approval/state").json()
        assert state["status"] == "completed"


def test_gateway_can_stream_resume_pending_approval(gateway_app_factory) -> None:
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
        client.post("/threads", json={"thread_id": "thread-approval-stream"})
        first = client.post("/threads/thread-approval-stream/runs", json={"message": "write a file"})
        assert first.status_code == 200
        assert first.json()["status"] == "awaiting_approval"

        with client.stream(
            "POST",
            "/threads/thread-approval-stream/approvals/approve/stream",
            json={"approval_context": "approved for this turn"},
        ) as response:
            events = parse_sse_text("".join(response.iter_text()))

        assert response.status_code == 200
        assert "approval_resolved" in names_only(events)
        resolved = next(event for event in events if event["event"] == "approval_resolved")
        assert resolved["data"]["request_id"].endswith("/call_1")
        completed = next(event for event in events if event["event"] == "run_completed")
        assert completed["data"]["status"] == "completed"


def test_gateway_can_cancel_pending_approval(gateway_app_factory) -> None:
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
        client.post("/threads", json={"thread_id": "thread-approval-cancel"})
        first = client.post("/threads/thread-approval-cancel/runs", json={"message": "write a file"})
        assert first.status_code == 200
        assert first.json()["status"] == "awaiting_approval"

        cancelled = client.post(
            "/threads/thread-approval-cancel/approvals/cancel",
            json={"reason": "cancelled from ui"},
        )
        assert cancelled.status_code == 200
        payload = cancelled.json()
        assert payload["status"] == "cancelled"
        assert payload["has_pending_approval"] is False

        detail = client.get("/threads/thread-approval-cancel/detail")
        assert detail.status_code == 200
        assert detail.json()["pending_approval"] is None
        assert detail.json()["state"]["recent_approval_events"][0]["decision"] == "cancelled"


def test_gateway_lists_and_cancels_subagent_tasks(gateway_app_factory) -> None:
    blocker = threading.Event()
    started = threading.Event()

    def blocking_runner_factory(*, task, prompt, config_result, allowed_tool_names):
        def _runner() -> str:
            started.set()
            blocker.wait()
            return f"done:{prompt}"

        return _runner

    subagent_service = SubagentService(default_runner_factory=blocking_runner_factory)
    app = gateway_app_factory(
        subagent_service=subagent_service,
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegated_task",
                            "args": {"prompt": "background task"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="submitted"),
            ]
        ),
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-subagents"})
        response = client.post("/threads/thread-subagents/runs", json={"message": "delegate work"})
        assert response.status_code == 200

        deadline = time.time() + 2
        tasks = []
        while time.time() < deadline:
            tasks = client.get("/threads/thread-subagents/subagents").json()
            if tasks:
                break
            time.sleep(0.05)
        assert tasks
        assert started.wait(timeout=2) is True
        task_id = tasks[0]["task_id"]

        cancelled = client.post(f"/threads/thread-subagents/subagents/{task_id}/cancel")
        blocker.set()
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
