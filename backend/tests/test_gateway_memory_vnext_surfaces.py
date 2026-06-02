from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from fake_models import BindableFakeMessagesListChatModel


def test_gateway_exposes_memory_vnext_public_surfaces(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(content="Stored prior thread."),
                AIMessage(content="Stored current thread."),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-prior"})
        prior_run = client.post(
            "/threads/thread-prior/runs",
            json={"message": "Remember that Northstar is the codename and the user prefers terse updates."},
        )
        assert prior_run.status_code == 200

        client.post("/threads", json={"thread_id": "thread-current"})
        current_run = client.post(
            "/threads/thread-current/runs",
            json={"message": "Continue the Northstar rollout with concise summaries."},
        )
        assert current_run.status_code == 200
        app.state.runtime_deps.run_engine.wait_for_background_tasks(timeout_seconds=5)

        created_user = client.post("/memory/user", json={"content": "User prefers terse updates.", "category": "preference"})
        created_workspace = client.post("/memory/workspace", json={"content": "Northstar is the active codename.", "category": "project_context"})

        overview = client.get("/memory/overview")
        session = client.get("/memory/session", params={"thread_id": "thread-current"})
        session_search = client.post(
            "/memory/session/search",
            json={"query": "Northstar", "thread_id": "thread-current", "limit": 5, "scope": "exclude_current", "mode": "summarize"},
        )
        recent_sessions = client.post(
            "/memory/session/search",
            json={"query": "", "thread_id": "thread-current", "limit": 5, "scope": "all", "mode": "recent"},
        )
        search_sessions = client.post(
            "/memory/session/search",
            json={"query": "Northstar", "thread_id": "thread-current", "limit": 5, "scope": "exclude_current", "mode": "search"},
        )
        traces = client.post("/memory/trace", json={"thread_id": "thread-current"})
        providers = client.get("/memory/admin/providers")
        reflections = client.get("/memory/admin/reflections")
        conflicts = client.get("/memory/admin/conflicts")
        staleness = client.get("/memory/admin/staleness")

    assert created_user.status_code == 200
    assert created_workspace.status_code == 200
    assert overview.status_code == 200
    assert overview.json()["layers"][0]["layer_id"] == "session"
    assert session.status_code == 200
    assert session.json()["thread_id"] == "thread-current"
    assert session.json()["session_summary"]
    assert session_search.status_code == 200
    assert session_search.json()["groups"][0]["thread_id"] == "thread-prior"
    assert session_search.json()["groups"][0]["summary"]
    assert session_search.json()["groups"][0]["evidence"]
    assert recent_sessions.status_code == 200
    assert recent_sessions.json()["groups"]
    assert search_sessions.status_code == 200
    assert search_sessions.json()["groups"][0]["summary"]
    assert traces.status_code == 200
    assert traces.json()["items"]
    assert providers.status_code == 200
    assert any(item["provider_id"] == "anvil_factgraph" for item in providers.json()["items"])
    assert reflections.status_code == 200
    assert reflections.json()["items"]
    assert conflicts.status_code == 200
    assert isinstance(conflicts.json()["items"], list)
    assert staleness.status_code == 200
    assert isinstance(staleness.json()["items"], list)
