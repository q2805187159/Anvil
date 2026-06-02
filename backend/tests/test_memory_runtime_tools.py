from __future__ import annotations

import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from anvil.config import ConfigLayer, ConfigLayerKind
from fake_models import BindableFakeMessagesListChatModel


def _memory_platform_layers(base_path):
    return [
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
                    }
                },
                "memory_platform": {
                    "enabled": True,
                    "archive": {"sqlite_path": str(base_path / "archive.sqlite3")},
                },
                "guardrails": {"enabled": False},
            },
        )
    ]


def test_memory_tool_writes_user_and_workspace_layers(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "add",
                                "layer": "user",
                                "content": "User prefers terse updates.",
                                "category": "preference",
                            },
                            "id": "call_memory_add",
                            "type": "tool_call",
                        },
                        {
                            "name": "memory",
                            "args": {
                                "action": "add",
                                "layer": "workspace",
                                "content": "Northstar is the active codename.",
                                "category": "project_context",
                            },
                            "id": "call_workspace_add",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Saved both memory layers."),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-memory-tool"})
        run = client.post(
            "/threads/thread-memory-tool/runs",
            json={"message": "Store the durable user and workspace memory.", "execution_mode": "full_access"},
        )
        assert run.status_code == 200

        user_entries = client.get("/memory/layers/user/entries")
        workspace_entries = client.get("/memory/layers/workspace/entries")
        detail = client.get("/threads/thread-memory-tool/detail")

    assert user_entries.status_code == 200
    assert any(item["content"] == "User prefers terse updates." for item in user_entries.json())
    assert workspace_entries.status_code == 200
    assert any(item["content"] == "Northstar is the active codename." for item in workspace_entries.json())
    assert detail.status_code == 200
    ai_tool_message = next(message for message in detail.json()["messages"] if message["role"] == "ai" and message["tool_calls"])
    assert {item["name"] for item in ai_tool_message["tool_calls"]} >= {"memory"}


def test_memory_tool_observe_profile_and_health_surface_quality_state(gateway_app_factory, contract_tmp_path) -> None:
    app = gateway_app_factory(
        config_layers=_memory_platform_layers(contract_tmp_path),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "observe",
                                "layer": "user",
                                "content": "User prefers compact implementation summaries.",
                                "event_type": "style",
                                "profile_class": "style",
                                "confidence": 0.91,
                                "salience": 0.82,
                                "evidence_refs": ["thread-memory-health/run-1"],
                            },
                            "id": "call_observe",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "profile",
                                "layer": "user",
                                "profile_class": "style",
                            },
                            "id": "call_profile",
                            "type": "tool_call",
                        },
                        {
                            "name": "memory",
                            "args": {
                                "action": "health",
                                "layer": "workspace",
                            },
                            "id": "call_health",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Updated profile memory and checked memory health."),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-memory-health"})
        run = client.post(
            "/threads/thread-memory-health/runs",
            json={"message": "Observe a stable preference and inspect memory health.", "execution_mode": "full_access"},
        )
        assert run.status_code == 200
        detail = client.get("/threads/thread-memory-health/detail")
        health = client.get("/memory/admin/health")

    tool_calls = [
        item
        for message in detail.json()["messages"]
        if message["role"] == "ai"
        for item in message["tool_calls"]
    ]
    observe_payload = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_observe")["result_text"])
    profile_payload = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_profile")["result_text"])
    health_payload = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_health")["result_text"])

    assert observe_payload["status"] == "observed"
    assert profile_payload["entries"]
    assert health_payload["status"] in {"healthy", "watch", "needs_attention"}
    assert health.status_code == 200
    assert health.json()["stores"]


def test_memory_tool_runs_recall_benchmark(gateway_app_factory, contract_tmp_path) -> None:
    app = gateway_app_factory(
        config_layers=_memory_platform_layers(contract_tmp_path),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "add",
                                "layer": "workspace",
                                "content": "Northstar release uses canary deployment with pytest verification.",
                                "category": "project_context",
                                "confidence": 0.92,
                                "salience": 0.9,
                                "evidence_refs": ["thread-memory-benchmark/setup"],
                            },
                            "id": "call_add",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "benchmark",
                                "layer": "workspace",
                                "suite_id": "tool-recall",
                                "cases": [
                                    {
                                        "case_id": "northstar-canary",
                                        "query": "Northstar canary pytest",
                                        "thread_id": "thread-memory-benchmark",
                                        "expected_terms": ["canary deployment", "pytest"],
                                    }
                                ],
                            },
                            "id": "call_benchmark",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Benchmark completed."),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-memory-benchmark"})
        run = client.post(
            "/threads/thread-memory-benchmark/runs",
            json={"message": "Store and benchmark recall.", "execution_mode": "full_access"},
        )
        assert run.status_code == 200
        detail = client.get("/threads/thread-memory-benchmark/detail")

    tool_calls = [
        item
        for message in detail.json()["messages"]
        if message["role"] == "ai"
        for item in message["tool_calls"]
    ]
    benchmark_payload = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_benchmark")["result_text"])
    assert benchmark_payload["suite_id"] == "tool-recall"
    assert benchmark_payload["passed"] is True
    assert benchmark_payload["cases"][0]["top_evidence"]


def test_memory_tool_runs_persisted_recall_benchmark_suite(gateway_app_factory, contract_tmp_path) -> None:
    app = gateway_app_factory(
        config_layers=_memory_platform_layers(contract_tmp_path),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "benchmark",
                                "layer": "workspace",
                                "suite_id": "tool-persisted-recall",
                            },
                            "id": "call_benchmark",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Persisted benchmark completed."),
            ]
        ),
    )

    with TestClient(app) as client:
        created_memory = client.post(
            "/memory/workspace",
            json={
                "content": "Northstar release uses canary deployment with pytest verification.",
                "category": "project_context",
                "confidence": 0.92,
                "salience": 0.9,
                "evidence_refs": ["thread-memory-suite/setup"],
            },
        )
        created_memory_id = created_memory.json().get("memory_id") or created_memory.json()["entry_id"]
        created_suite = client.post(
            "/memory/admin/benchmark/suites",
            json={
                "suite_id": "tool-persisted-recall",
                "name": "Tool persisted recall",
                "cases": [
                    {
                        "case_id": "northstar-canary",
                        "query": "Northstar canary pytest",
                        "thread_id": "thread-memory-suite",
                        "expected_terms": ["canary deployment", "pytest"],
                        "expected_memory_ids": [created_memory_id],
                    }
                ],
            },
        )
        assert created_suite.status_code == 200
        client.post("/threads", json={"thread_id": "thread-memory-suite"})
        run = client.post(
            "/threads/thread-memory-suite/runs",
            json={"message": "Run the persisted recall benchmark.", "execution_mode": "full_access"},
        )
        assert run.status_code == 200
        detail = client.get("/threads/thread-memory-suite/detail")

    tool_calls = [
        item
        for message in detail.json()["messages"]
        if message["role"] == "ai"
        for item in message["tool_calls"]
    ]
    benchmark_payload = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_benchmark")["result_text"])
    assert benchmark_payload["suite_id"] == "tool-persisted-recall"
    assert benchmark_payload["report"]["passed"] is True
    assert benchmark_payload["report"]["cases"][0]["top_evidence"]


def test_memory_tool_surfaces_retention_state(gateway_app_factory, contract_tmp_path) -> None:
    app = gateway_app_factory(
        config_layers=_memory_platform_layers(contract_tmp_path),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "add",
                                "layer": "workspace",
                                "content": "Northstar release uses canary deployment with pytest verification.",
                                "category": "project_context",
                                "confidence": 0.92,
                                "salience": 0.9,
                                "evidence_refs": ["thread-memory-retention/setup"],
                            },
                            "id": "call_add",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "retention",
                                "layer": "workspace",
                                "limit": 10,
                            },
                            "id": "call_retention",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Retention state checked."),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-memory-retention"})
        run = client.post(
            "/threads/thread-memory-retention/runs",
            json={"message": "Store memory and inspect retention.", "execution_mode": "full_access"},
        )
        assert run.status_code == 200
        detail = client.get("/threads/thread-memory-retention/detail")

    tool_calls = [
        item
        for message in detail.json()["messages"]
        if message["role"] == "ai"
        for item in message["tool_calls"]
    ]
    retention_payload = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_retention")["result_text"])
    assert retention_payload["layer_id"] == "workspace"
    assert retention_payload["items"]
    assert retention_payload["items"][0]["retention_score"] > 0


def test_memory_tool_governs_retention_actions(gateway_app_factory, contract_tmp_path) -> None:
    app = gateway_app_factory(
        config_layers=_memory_platform_layers(contract_tmp_path),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "add",
                                "layer": "workspace",
                                "content": "Northstar stale release note should be governed.",
                                "category": "project_context",
                                "confidence": 0.45,
                                "salience": 0.2,
                            },
                            "id": "call_add",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "reinforce",
                                "layer": "workspace",
                                "old_text": "Northstar stale release note",
                                "content": "Confirmed useful during governance smoke.",
                            },
                            "id": "call_reinforce",
                            "type": "tool_call",
                        },
                        {
                            "name": "memory",
                            "args": {
                                "action": "review_memory",
                                "layer": "workspace",
                                "old_text": "Northstar stale release note",
                                "content": "Needs explicit review.",
                            },
                            "id": "call_review",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Governance actions completed."),
            ]
        ),
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-memory-governance"})
        run = client.post(
            "/threads/thread-memory-governance/runs",
            json={"message": "Store and govern memory.", "execution_mode": "full_access"},
        )
        assert run.status_code == 200
        detail = client.get("/threads/thread-memory-governance/detail")

    tool_calls = [
        item
        for message in detail.json()["messages"]
        if message["role"] == "ai"
        for item in message["tool_calls"]
    ]
    reinforced = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_reinforce")["result_text"])
    reviewed = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_review")["result_text"])

    assert reinforced["action"] == "reinforce"
    assert reinforced["after_retention"]["access_count"] >= reinforced["before_retention"]["access_count"] + 1
    assert reviewed["action"] == "review"
    assert reviewed["review_item"]["action"] == "review_existing"


def test_memory_tool_plans_batch_governance(gateway_app_factory, contract_tmp_path) -> None:
    app = gateway_app_factory(
        config_layers=_memory_platform_layers(contract_tmp_path),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "add",
                                "layer": "workspace",
                                "content": "Northstar batch governance candidate.",
                                "category": "project_context",
                                "confidence": 0.2,
                                "salience": 0.05,
                            },
                            "id": "call_add",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "govern_batch",
                                "layer": "workspace",
                                "resolution": "review",
                                "limit": 5,
                                "dry_run": True,
                            },
                            "id": "call_batch",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Batch governance plan generated."),
            ]
        ),
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-memory-governance-batch"})
        run = client.post(
            "/threads/thread-memory-governance-batch/runs",
            json={"message": "Store and plan memory governance.", "execution_mode": "full_access"},
        )
        assert run.status_code == 200
        detail = client.get("/threads/thread-memory-governance-batch/detail")

    tool_calls = [
        item
        for message in detail.json()["messages"]
        if message["role"] == "ai"
        for item in message["tool_calls"]
    ]
    batch_payload = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_batch")["result_text"])
    assert batch_payload["dry_run"] is True
    assert batch_payload["policy"] == "review"
    assert "items" in batch_payload


def test_memory_tool_runs_maintenance_plan(gateway_app_factory, contract_tmp_path) -> None:
    app = gateway_app_factory(
        config_layers=_memory_platform_layers(contract_tmp_path),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "add",
                                "layer": "workspace",
                                "content": "Northstar maintenance candidate.",
                                "category": "project_context",
                                "confidence": 0.2,
                                "salience": 0.05,
                            },
                            "id": "call_add",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "maintenance",
                                "layer": "workspace",
                                "resolution": "review",
                                "limit": 5,
                                "dry_run": True,
                            },
                            "id": "call_maintenance",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Maintenance plan generated."),
            ]
        ),
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-memory-maintenance"})
        run = client.post(
            "/threads/thread-memory-maintenance/runs",
            json={"message": "Store and run memory maintenance.", "execution_mode": "full_access"},
        )
        assert run.status_code == 200
        detail = client.get("/threads/thread-memory-maintenance/detail")

    tool_calls = [
        item
        for message in detail.json()["messages"]
        if message["role"] == "ai"
        for item in message["tool_calls"]
    ]
    payload = json.loads(next(item for item in tool_calls if item["tool_call_id"] == "call_maintenance")["result_text"])
    assert payload["dry_run"] is True
    assert payload["policy"] == "review"
    assert "governance" in payload


def test_memory_tool_rejects_session_writes_and_session_search_reads_archive(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(content="Stored archive context."),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "add",
                                "layer": "session",
                                "content": "Do not allow this write.",
                            },
                            "id": "call_session_write",
                            "type": "tool_call",
                        },
                        {
                            "name": "session_search",
                            "args": {
                                "query": "Northstar",
                                "scope": "exclude_current",
                                "limit": 5,
                            },
                            "id": "call_session_search",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Looked up the prior session."),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-archive-source"})
        source_run = client.post(
            "/threads/thread-archive-source/runs",
            json={"message": "Remember that Northstar belongs to the earlier thread."},
        )
        assert source_run.status_code == 200
        app.state.runtime_deps.run_engine.wait_for_background_tasks(timeout_seconds=5)

        client.post("/threads", json={"thread_id": "thread-session-tool"})
        run = client.post(
            "/threads/thread-session-tool/runs",
            json={"message": "Search prior session memory without writing to the session layer."},
        )
        assert run.status_code == 200

        detail = client.get("/threads/thread-session-tool/detail")

    assert detail.status_code == 200
    ai_tool_message = next(message for message in detail.json()["messages"] if message["role"] == "ai" and message["tool_calls"])
    session_write_call = next(item for item in ai_tool_message["tool_calls"] if item["tool_call_id"] == "call_session_write")
    session_search_call = next(item for item in ai_tool_message["tool_calls"] if item["tool_call_id"] == "call_session_search")
    assert "read-only" in (session_write_call["result_text"] or "")
    session_search_payload = json.loads(session_search_call["result_text"] or "{}")
    assert [item["thread_id"] for item in session_search_payload["groups"]] == ["thread-archive-source"]


def test_memory_trace_and_consolidate_tools_surface_recent_memory_activity(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "add",
                                "layer": "workspace",
                                "content": "Northstar is the active codename.",
                                "category": "project_context",
                            },
                            "id": "call_workspace_add",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Added workspace memory."),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "memory",
                            "args": {
                                "action": "consolidate",
                                "layer": "workspace",
                                "content": "",
                            },
                            "id": "call_workspace_consolidate",
                            "type": "tool_call",
                        },
                        {
                            "name": "memory_trace",
                            "args": {
                                "limit": 5,
                            },
                            "id": "call_memory_trace",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Consolidated workspace memory and explained the trace."),
            ]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-memory-trace"})
        first_run = client.post(
            "/threads/thread-memory-trace/runs",
            json={"message": "Add workspace memory first.", "execution_mode": "full_access"},
        )
        assert first_run.status_code == 200

        run = client.post(
            "/threads/thread-memory-trace/runs",
            json={"message": "Add memory, consolidate it, and explain the recent memory activity.", "execution_mode": "full_access"},
        )
        assert run.status_code == 200

        detail = client.get("/threads/thread-memory-trace/detail")

    assert detail.status_code == 200
    ai_tool_message = next(
        message
        for message in detail.json()["messages"]
        if message["role"] == "ai"
        and any(item["tool_call_id"] == "call_workspace_consolidate" for item in message["tool_calls"])
    )
    consolidate_call = next(item for item in ai_tool_message["tool_calls"] if item["tool_call_id"] == "call_workspace_consolidate")
    trace_call = next(item for item in ai_tool_message["tool_calls"] if item["tool_call_id"] == "call_memory_trace")
    assert "consolidated" in (consolidate_call["result_text"] or "")
    trace_payload = json.loads(trace_call["result_text"] or "{}")
    assert trace_payload["items"]
