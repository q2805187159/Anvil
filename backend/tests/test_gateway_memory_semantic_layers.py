from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from fake_models import BindableFakeMessagesListChatModel


def test_gateway_exposes_semantic_memory_layers_and_session_search(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(content="Stored thread-a context."),
                AIMessage(content="Stored thread-b context."),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-a"})
        run_a = client.post(
            "/threads/thread-a/runs",
            json={"message": "Remember that Northstar is the workspace codename and prefer terse status updates."},
        )
        assert run_a.status_code == 200

        client.post("/threads", json={"thread_id": "thread-b"})
        run_b = client.post(
            "/threads/thread-b/runs",
            json={"message": "Remember that Northstar shipped in the archive thread."},
        )
        assert run_b.status_code == 200
        app.state.runtime_deps.run_engine.wait_for_background_tasks(timeout_seconds=5)

        created_user = client.post(
            "/memory/layers/user/entries",
            json={"content": "User prefers terse updates.", "category": "preference"},
        )
        created_workspace = client.post(
            "/memory/layers/workspace/entries",
            json={"content": "Northstar is the workspace codename.", "category": "project_context"},
        )
        layers = client.get("/memory/layers")
        session_view = client.get("/memory/layers/session", params={"thread_id": "thread-a"})
        session_search = client.post(
            "/memory/session-search",
            json={"query": "Northstar", "thread_id": "thread-a", "limit": 5},
        )
        user_entries = client.get("/memory/stores/hcms_user/entries")
        workspace_entries = client.get("/memory/stores/hcms_workspace/entries")

    assert created_user.status_code == 200
    assert created_workspace.status_code == 200
    assert layers.status_code == 200
    assert [item["layer_id"] for item in layers.json()] == ["session", "user", "workspace"]
    assert session_view.status_code == 200
    assert session_view.json()["layer_id"] == "session"
    assert session_view.json()["thread_id"] == "thread-a"
    assert session_view.json()["latest_prompt_snapshot"] is not None
    assert session_view.json()["recent_turns"]
    assert session_search.status_code == 200
    assert session_search.json()["scope"] == "exclude_current"
    assert [item["thread_id"] for item in session_search.json()["groups"]] == ["thread-b"]
    assert user_entries.status_code == 200
    user_manual_entry = next(item for item in user_entries.json() if item["entry_id"] == created_user.json()["entry_id"])
    assert user_manual_entry["content"] == "User prefers terse updates."
    assert "hcms_schema: compiled_memory.v1" not in user_manual_entry["content"]
    assert workspace_entries.status_code == 200
    workspace_manual_entry = next(item for item in workspace_entries.json() if item["entry_id"] == created_workspace.json()["entry_id"])
    assert workspace_manual_entry["content"] == "Northstar is the workspace codename."
    assert "hcms_schema: compiled_memory.v1" not in workspace_manual_entry["content"]
