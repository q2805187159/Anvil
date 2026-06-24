from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from anvil.memory import record_memory_diagnostic
from app.gateway.routers import memory as memory_router
from fake_models import BindableFakeMessagesListChatModel


def test_gateway_hcms_route_snapshot_covers_frontend_memory_api() -> None:
    routes = {
        (next(iter(route.methods - {"HEAD", "OPTIONS"})), route.path)
        for route in memory_router.router.routes
        if getattr(route, "methods", None)
    }

    expected = {
        ("POST", "/memory/hcms/recall"),
        ("POST", "/memory/hcms/search"),
        ("POST", "/memory/search"),
        ("POST", "/memory/hcms/why"),
        ("GET", "/memory/hcms/memories"),
        ("GET", "/memory/list"),
        ("GET", "/memory/hcms/memories/{memory_id}"),
        ("GET", "/memory/{memory_id}"),
        ("DELETE", "/memory/hcms/memories/{memory_id}"),
        ("DELETE", "/memory/{memory_id}"),
        ("GET", "/memory/hcms/memories/{memory_id}/history"),
        ("GET", "/memory/hcms/memories/{memory_id}/versions"),
        ("GET", "/memory/{memory_id}/versions"),
        ("GET", "/memory/hcms/memories/{memory_id}/relations"),
        ("GET", "/memory/{memory_id}/relations"),
        ("GET", "/memory/hcms/memories/{memory_id}/diff"),
        ("GET", "/memory/admin/health"),
        ("POST", "/memory/admin/benchmark"),
    }
    assert expected <= routes


def test_gateway_exposes_hcms_public_memory_surfaces(gateway_app_factory) -> None:
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
        hcms_recall = client.post("/memory/hcms/recall", json={"query": "Northstar", "limit": 5})
        hcms_search = client.post("/memory/hcms/search", json={"query": "Northstar", "limit": 5})
        memory_search = client.post("/memory/search", json={"query": "Northstar", "limit": 5})
        hcms_memory_id = hcms_recall.json()["items"][0]["memory_id"]
        hcms_list = client.get("/memory/hcms/memories", params={"query": "Northstar", "state": "active", "limit": 2, "offset": 0})
        memory_list = client.get("/memory/list", params={"query": "Northstar", "state": "active", "limit": 2, "offset": 0})
        hcms_list_page = client.get("/memory/hcms/memories", params={"limit": 1, "offset": 1})
        hcms_list_bad_layer = client.get("/memory/hcms/memories", params={"layer_id": "unknown-layer"})
        memory_list_bad_layer = client.get("/memory/list", params={"layer_id": "unknown-layer"})
        hcms_memory = client.get(f"/memory/hcms/memories/{hcms_memory_id}")
        memory_detail = client.get(f"/memory/{hcms_memory_id}")
        hcms_why = client.post("/memory/hcms/why", json={"query": "Northstar", "limit": 3})
        hcms_history = client.get(f"/memory/hcms/memories/{hcms_memory_id}/history")
        hcms_versions = client.get(f"/memory/hcms/memories/{hcms_memory_id}/versions")
        memory_versions = client.get(f"/memory/{hcms_memory_id}/versions")
        hcms_relations = client.get(f"/memory/hcms/memories/{hcms_memory_id}/relations")
        memory_relations = client.get(f"/memory/{hcms_memory_id}/relations")
        hcms_diff = client.get(f"/memory/hcms/memories/{hcms_memory_id}/diff")
        hcms_missing = client.get("/memory/hcms/memories/missing-memory")
        memory_missing = client.get("/memory/missing-memory")
        hcms_missing_history = client.get("/memory/hcms/memories/missing-memory/history")
        hcms_missing_versions = client.get("/memory/hcms/memories/missing-memory/versions")
        memory_missing_versions = client.get("/memory/missing-memory/versions")
        hcms_missing_diff = client.get("/memory/hcms/memories/missing-memory/diff")
        client.post("/memory/workspace", json={"content": "AliasDelete Northstar temporary memory.", "category": "project_context"})
        alias_search = client.post("/memory/search", json={"query": "AliasDelete Northstar", "limit": 5})
        alias_memory_id = alias_search.json()["items"][0]["memory_id"]
        memory_deleted = client.delete(f"/memory/{alias_memory_id}")
        memory_deleted_read = client.get(f"/memory/{alias_memory_id}")
        hcms_deleted = client.delete(f"/memory/hcms/memories/{hcms_memory_id}")
        hcms_deleted_read = client.get(f"/memory/hcms/memories/{hcms_memory_id}")
        engines = client.get("/memory/admin/engines")
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
    trace_evidence = [evidence for trace in traces.json()["items"] for evidence in trace["evidence"]]
    assert any(evidence["source_kind"] == "hcms_v2_capture" for evidence in trace_evidence)
    for evidence in trace_evidence:
        assert "capture_envelope_id" not in evidence
        assert "observation_id" not in evidence
        assert "hcms_v2_slow_consolidation_claim_ids" not in evidence
        assert "tool_result_refs" not in evidence
    assert hcms_recall.status_code == 200
    assert hcms_recall.json()["items"]
    assert hcms_recall.json()["metrics"]["recall_count"] >= 0
    assert hcms_search.status_code == 200
    assert hcms_search.json()["items"][0]["memory_id"]
    assert memory_search.status_code == 200
    assert memory_search.json()["items"][0]["memory_id"]
    assert hcms_list.status_code == 200
    assert hcms_list.json()["items"]
    assert hcms_list.json()["items"][0]["memory_id"]
    assert hcms_list.json()["total"] >= len(hcms_list.json()["items"])
    assert hcms_list.json()["limit"] == 2
    assert hcms_list.json()["offset"] == 0
    assert hcms_list.json()["state"] == "active"
    assert memory_list.status_code == 200
    assert memory_list.json()["items"]
    assert memory_list.json()["items"][0]["memory_id"]
    assert memory_list.json()["limit"] == 2
    assert memory_list.json()["offset"] == 0
    assert memory_list.json()["state"] == "active"
    assert hcms_list_page.status_code == 200
    assert hcms_list_page.json()["limit"] == 1
    assert hcms_list_page.json()["offset"] == 1
    assert hcms_list_bad_layer.status_code == 400
    assert hcms_list_bad_layer.json()["error"] == "invalid_hcms_memory_filter"
    assert memory_list_bad_layer.status_code == 400
    assert memory_list_bad_layer.json()["error"] == "invalid_hcms_memory_filter"
    assert hcms_memory.status_code == 200
    assert hcms_memory.json()["memory"]["memory_id"] == hcms_memory_id
    assert memory_detail.status_code == 200
    assert memory_detail.json()["memory"]["memory_id"] == hcms_memory_id
    assert hcms_why.status_code == 200
    assert "paths" in hcms_why.json()
    assert hcms_history.status_code == 200
    assert hcms_history.json()["versions"]
    assert hcms_versions.status_code == 200
    assert hcms_versions.json()["versions"]
    assert memory_versions.status_code == 200
    assert memory_versions.json()["versions"]
    assert hcms_relations.status_code == 200
    assert hcms_relations.json()["memory_id"] == hcms_memory_id
    assert hcms_relations.json()["relations"]
    assert hcms_relations.json()["relations"][0]["source_memory"] or hcms_relations.json()["relations"][0]["target_memory"]
    assert memory_relations.status_code == 200
    assert memory_relations.json()["memory_id"] == hcms_memory_id
    assert memory_relations.json()["relations"]
    assert hcms_diff.status_code == 200
    assert "diff" in hcms_diff.json()
    assert hcms_missing.status_code == 404
    assert hcms_missing.json()["error"] == "memory_not_found"
    assert memory_missing.status_code == 404
    assert memory_missing.json()["error"] == "memory_not_found"
    assert hcms_missing_history.status_code == 404
    assert hcms_missing_versions.status_code == 404
    assert memory_missing_versions.status_code == 404
    assert hcms_missing_diff.status_code == 404
    assert alias_search.status_code == 200
    assert alias_search.json()["items"][0]["memory_id"] == alias_memory_id
    assert memory_deleted.status_code == 200
    assert memory_deleted.json()["memory_id"] == alias_memory_id
    assert memory_deleted.json()["deleted"] is True
    assert memory_deleted_read.status_code == 404
    assert hcms_deleted.status_code == 200
    assert hcms_deleted.json()["memory_id"] == hcms_memory_id
    assert hcms_deleted.json()["deleted"] is True
    assert hcms_deleted_read.status_code == 404
    assert engines.status_code == 200
    assert any(item["engine_id"] == "hcms" for item in engines.json()["items"])
    assert reflections.status_code == 200
    assert reflections.json()["items"]
    assert conflicts.status_code == 200
    assert isinstance(conflicts.json()["items"], list)
    assert staleness.status_code == 200
    assert isinstance(staleness.json()["items"], list)


def test_gateway_hcms_diff_exposes_version_confidence_and_evidence_metadata(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="unused")])
    )

    with TestClient(app) as client:
        created = client.post(
            "/memory/workspace",
            json={
                "content": "Northstar requires canary verification before release.",
                "category": "project_context",
                "confidence": 0.6,
                "evidence_refs": ["thread:initial-canary-rule"],
            },
        )
        memory_id = created.json()["memory_id"]
        updated = client.patch(
            f"/memory/workspace/{memory_id}",
            json={
                "content": "Northstar requires canary verification and smoke validation before release.",
                "confidence": 0.85,
                "evidence_refs": ["thread:smoke-validation"],
            },
        )
        diff = client.get(f"/memory/hcms/memories/{memory_id}/diff")

    assert created.status_code == 200
    assert updated.status_code == 200
    assert diff.status_code == 200
    payload = diff.json()
    assert "smoke validation" in payload["diff"]
    assert payload["from_version"] == 1
    assert payload["to_version"] == 2
    assert payload["confidence_delta"] == 0.25
    assert len(payload["evidence_added"]) == 1
    assert payload["evidence_removed"] == []


def test_gateway_hcms_why_exposes_degradation_fields(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="unused")])
    )

    with TestClient(app) as client:
        client.post(
            "/memory/workspace",
            json={
                "content": "Northstar fallback evidence is related to rollout safety but has no causal edge.",
                "category": "project_context",
                "confidence": 0.74,
                "evidence_refs": ["thread:related-rollout-safety"],
            },
        )
        why = client.post("/memory/hcms/why", json={"query": "why is Northstar fallback related to rollout safety", "limit": 1})

    assert why.status_code == 200
    path = why.json()["paths"][0]
    assert path["edges"] == []
    assert path["explanation_kind"] in {"correlation", "degraded"}
    assert path["degradation_reason"]
    assert isinstance(path["evidence_summary"], list)


def test_gateway_hcms_health_exposes_degraded_diagnostics(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="unused")])
    )

    with TestClient(app) as client:
        state = app.state.runtime_deps.memory_manager.hcms_service.prefetch("global/default")
        record_memory_diagnostic(
            state,
            component="retrieval",
            reason="stream_failed",
            stream_name="bm25",
            error_type="RuntimeError",
            message="bm25 stream failed open.",
        )
        app.state.runtime_deps.memory_manager.hcms_service.store.save("global/default", state)
        health = client.get("/memory/admin/health")
        engines = client.get("/memory/admin/engines")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["engine_health"]["hcms"] == "degraded"
    assert health.json()["diagnostics"] == ["retrieval:stream_failed:bm25:RuntimeError:x1"]
    assert engines.status_code == 200
    assert engines.json()["items"][0]["health"] == "degraded"
    assert engines.json()["items"][0]["diagnostics"]
