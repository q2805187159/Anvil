from __future__ import annotations

from langchain_core.messages import AIMessage
from fastapi.testclient import TestClient

from conformance_helpers import parse_sse_text
from fake_models import BindableFakeMessagesListChatModel


def test_sse_emits_started_then_completed(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="streamed hello")])
    )
    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream"})
        with client.stream(
            "GET",
            "/threads/thread-stream/runs/stream",
            params={"message": "hello", "execution_mode": "chat"},
        ) as response:
            body = "".join(response.iter_text())
            events = parse_sse_text(body)

        assert response.status_code == 200
        assert "event: run_preparing" in body
        assert "event: run_started" in body
        assert "event: run_completed" in body
        assert "streamed hello" in body
        assert events[0]["event"] == "run_preparing"
        assert events[0]["data"]["phase"] == "gateway_received"
        assert events[1]["data"]["execution_mode"] == "chat"


def test_sse_emits_standard_id_lines_for_reconnect_cursor(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="streamed id")])
    )
    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-sse-id"})
        with client.stream(
            "GET",
            "/threads/thread-stream-sse-id/runs/stream",
            params={"message": "hello", "execution_mode": "chat"},
        ) as response:
            body = "".join(response.iter_text())
            events = parse_sse_text(body)

        event_ids = [
            event["data"]["event_id"]
            for event in events
            if event["data"].get("event_id")
        ]

        assert event_ids
        assert all(f"id: {event_id}" in body for event_id in event_ids)


def test_sse_emits_failed_on_exception(gateway_app_factory) -> None:
    class BrokenModel:
        def bind_tools(self, *args, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            raise RuntimeError("boom")

    app = gateway_app_factory(chat_model_override=BrokenModel())
    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-fail"})
        with client.stream("GET", "/threads/thread-fail/runs/stream", params={"message": "hello"}) as response:
            body = "".join(response.iter_text())

        assert response.status_code == 200
        assert "event: run_preparing" in body
        assert "event: run_started" in body
        assert "event: run_completed" in body or "event: run_failed" in body


def test_post_sse_emits_started_then_completed(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="posted stream hello")])
    )
    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-stream-post"})
        with client.stream(
            "POST",
            "/threads/thread-stream-post/runs/stream",
            json={"message": "hello from post"},
        ) as response:
            body = "".join(response.iter_text())

        assert response.status_code == 200
        assert "event: run_preparing" in body
        assert "event: run_started" in body
        assert "event: run_completed" in body
        assert "posted stream hello" in body


def test_gateway_replays_run_events_after_cursor(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="replay hello")])
    )
    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-replay-events"})
        with client.stream(
            "POST",
            "/threads/thread-replay-events/runs/stream",
            json={"message": "hello replay"},
        ) as response:
            stream_events = parse_sse_text("".join(response.iter_text()))

        cursor = stream_events[1]["data"]["sequence"]
        run_id = stream_events[1]["data"]["run_id"]
        ambiguous_response = client.get(
            "/threads/thread-replay-events/runs/events",
            params={"after_sequence": cursor},
        )
        replay_response = client.get(
            "/threads/thread-replay-events/runs/events",
            params={"run_id": run_id, "after_sequence": cursor, "limit": 3},
        )

        assert ambiguous_response.status_code == 400
        assert ambiguous_response.json()["error"] == "run_id_required_for_cursor"
        assert replay_response.status_code == 200
        replay = replay_response.json()
        assert replay["thread_id"] == "thread-replay-events"
        assert replay["run_id"] == run_id
        assert replay["after_sequence"] == cursor
        assert replay["events"][0]["sequence"] == cursor + 1
        assert len(replay["events"]) == 3
        assert replay["next_cursor"] == replay["events"][-1]["sequence"]
        assert replay["has_more"] is True
        assert all(event["data"]["known_system_version"] >= 0 for event in replay["events"])


def test_gateway_can_project_thread_state_from_run_event_log(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="event state hello")])
    )
    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-event-state"})
        with client.stream(
            "POST",
            "/threads/thread-event-state/runs/stream",
            json={"message": "hello state"},
        ) as response:
            stream_events = parse_sse_text("".join(response.iter_text()))

        run_id = stream_events[1]["data"]["run_id"]
        projected_response = client.get(
            "/threads/thread-event-state/state",
            params={"state_source": "event_log", "run_id": run_id},
        )

        assert projected_response.status_code == 200
        payload = projected_response.json()
        assert payload["thread_id"] == "thread-event-state"
        assert payload["status"] == "completed"
        assert payload["runtime_phase_timings"]["event_log"]["last_kind"] == "run_completed"


def test_gateway_can_project_thread_state_from_full_thread_event_log(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="first event answer"), AIMessage(content="second event answer")]
        )
    )
    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-event-state-all"})
        for message in ("first event question", "second event question"):
            with client.stream(
                "POST",
                "/threads/thread-event-state-all/runs/stream",
                json={"message": message, "execution_mode": "chat"},
            ) as response:
                assert response.status_code == 200
                parse_sse_text("".join(response.iter_text()))

        response = client.get(
            "/threads/thread-event-state-all/state",
            params={"state_source": "event_log"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["thread_id"] == "thread-event-state-all"
        assert payload["status"] == "completed"
        assert payload["runtime_phase_timings"]["event_log"]["run_count"] == 2
