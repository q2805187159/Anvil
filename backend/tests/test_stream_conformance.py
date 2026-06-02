from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from conformance_helpers import names_only, normalize_payload, parse_sse_text
from fake_models import BindableFakeMessagesListChatModel


def make_embedded_client(gateway_app, contract_tmp_path: Path):
    from app.sdk import EmbeddedClient, EmbeddedClientConfig

    return EmbeddedClient(
        EmbeddedClientConfig(
            config_layers=gateway_app.state.deps_factory().config_layers,
            feature_set=gateway_app.state.deps_factory().feature_set,
            thread_root=contract_tmp_path / "threads",
            state_db_path=contract_tmp_path / "gateway.sqlite3",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="stream parity")]
            ),
        )
    )


def test_get_post_and_embedded_streams_share_lifecycle_event_contract(
    gateway_app_factory,
    contract_tmp_path: Path,
) -> None:
    gateway_app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(content="stream parity"),
                AIMessage(content="stream parity"),
            ]
        )
    )
    with TestClient(gateway_app) as http_client:
        client = make_embedded_client(gateway_app, contract_tmp_path)
        try:
            http_client.post("/threads", json={"thread_id": "thread-get"})
            http_client.post("/threads", json={"thread_id": "thread-post"})
            client.create_thread(thread_id="thread-embedded")

            with http_client.stream("GET", "/threads/thread-get/runs/stream", params={"message": "hello"}) as response:
                get_events = parse_sse_text("".join(response.iter_text()))
            with http_client.stream(
                "POST",
                "/threads/thread-post/runs/stream",
                json={"message": "hello"},
            ) as response:
                post_events = parse_sse_text("".join(response.iter_text()))

            from app.sdk import EmbeddedRunRequest

            embedded_events = [
                event.as_run_stream_event().model_dump(mode="json")
                for event in client.stream(EmbeddedRunRequest(thread_id="thread-embedded", message="hello"))
            ]

            gateway_expected = [
                "run_preparing",
                "run_started",
                "summary_update",
                "step_started",
                "step_delta",
                "step_updated",
                "message_completed",
                "run_completed",
            ]
            assert names_only(get_events) == gateway_expected
            assert names_only(post_events) == gateway_expected
            assert [event["event"] for event in embedded_events] == [
                "run_preparing",
                "run_started",
                "summary_update",
                "step_started",
                "step_delta",
                "step_updated",
                "message_completed",
                "run_completed",
            ]

            assert [event["data"].get("payload_delta") for event in get_events if event["event"] == "step_delta"] == ["stream parity"]
            assert [event["data"].get("payload_delta") for event in post_events if event["event"] == "step_delta"] == ["stream parity"]
            assert [event["data"].get("payload_delta") for event in embedded_events if event["event"] == "step_delta"] == ["stream parity"]
        finally:
            client.close()


def test_embedded_client_streams_structured_interaction_resume(
    gateway_app_factory,
    contract_tmp_path: Path,
) -> None:
    from app.contracts import UserInteractionResumeRequest
    from app.sdk import EmbeddedClient, EmbeddedClientConfig

    gateway_app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="resume via embedded")])
    )
    client = EmbeddedClient(
        EmbeddedClientConfig(
            config_layers=gateway_app.state.deps_factory().config_layers,
            feature_set=gateway_app.state.deps_factory().feature_set,
            thread_root=contract_tmp_path / "threads",
            state_db_path=contract_tmp_path / "gateway.sqlite3",
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="resume via embedded")]),
        )
    )
    try:
        client.create_thread(thread_id="thread-embedded-interaction")
        state = client.deps.checkpointer.get_thread_state("thread-embedded-interaction")
        assert state is not None
        state.conversation.pending_user_interaction = {
            "request_id": "choice-embedded",
            "kind": "choice",
            "question": "Choose one",
            "selection_mode": "single",
            "options": [
                {"id": "a", "label": "A", "description": None, "recommended": False, "disabled": False},
            ],
            "min_selections": 1,
            "max_selections": 1,
            "allow_custom": False,
            "required": True,
        }
        client.deps.checkpointer.put_thread_state(state)

        events = [
            event.as_run_stream_event().model_dump(mode="json")
            for event in client.stream_user_interaction(
                "thread-embedded-interaction",
                UserInteractionResumeRequest(request_id="choice-embedded", selected_option_ids=["a"]),
            )
        ]

        assert events[0]["event"] == "run_preparing"
        assert events[-1]["event"] == "run_completed"
        assert events[-1]["data"]["assistant_message"] == "resume via embedded"
        persisted = client.get_thread_state("thread-embedded-interaction")
        assert persisted.pending_user_interaction is None
    finally:
        client.close()
