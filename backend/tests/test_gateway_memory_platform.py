from __future__ import annotations

from langchain_core.messages import AIMessage

from anvil.agents.features import RuntimeFeatureSet
from anvil.config import ConfigLayer, ConfigLayerKind
from fake_models import BindableFakeMessagesListChatModel


def build_memory_platform_layers(base_path):
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
                    "providers": {"active_provider_id": "anvil_factgraph"},
                    "reflection": {"enabled": True},
                    "maintenance": {
                        "automation_enabled": True,
                        "execute": False,
                        "interval_seconds": 3600,
                        "tick_seconds": 10,
                    },
                },
                "guardrails": {"enabled": False},
            },
        )
    ]


def test_gateway_memory_onboarding_queues_review_without_direct_workspace_write(gateway_app_factory, contract_tmp_path) -> None:
    workspace = contract_tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Northstar\n\nRun `pytest backend/tests` before release.\n", encoding="utf-8")
    (workspace / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = '-q'\n", encoding="utf-8")
    (workspace / ".env").write_text("SECRET_TOKEN=should-not-be-read\n", encoding="utf-8")
    app = gateway_app_factory(
        config_layers=build_memory_platform_layers(contract_tmp_path),
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="ok")]),
    )

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        onboarded = client.post(
            "/memory/admin/onboarding",
            json={"workspace_path": str(workspace), "thread_id": "thread-onboard", "source": "gateway-test"},
        )
        reviews = client.get("/memory/admin/review")
        workspace_entries = client.get("/memory/workspace")

    assert onboarded.status_code == 200
    payload = onboarded.json()
    assert payload["status"] == "review_queued"
    assert {item["relative_path"] for item in payload["files"]} == {"README.md", "pyproject.toml"}
    assert payload["review_ids"]
    assert payload["stable_snapshot_refresh_recommended"] is True
    assert reviews.status_code == 200
    assert len(reviews.json()["items"]) == 1
    assert "Northstar" in reviews.json()["items"][0]["content"]
    assert "SECRET_TOKEN" not in reviews.json()["items"][0]["content"]
    assert workspace_entries.status_code == 200
    assert workspace_entries.json() == []


def test_gateway_exposes_memory_platform_v2_surfaces(gateway_app_factory, contract_tmp_path) -> None:
    app = gateway_app_factory(
        config_layers=build_memory_platform_layers(contract_tmp_path),
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="Stored for later.")]
        ),
    )

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        overview = client.get("/memory")
        stores = client.get("/memory/stores")
        providers = client.get("/memory/providers")
        jobs = client.get("/memory/reflections/jobs")

        created = client.post(
            "/memory/stores/user_profile/entries",
            json={"content": "User prefers terse updates.", "category": "preference"},
        )
        created_payload = created.json()
        updated = client.patch(
            f"/memory/stores/user_profile/entries/{created_payload['entry_id']}",
            json={"content": "User prefers ultra-terse updates."},
        )
        governed = client.post(
            f"/memory/admin/memories/{created_payload['memory_id']}/govern",
            json={"action": "reinforce", "reason": "operator confirmed preference"},
        )
        governed_review = client.post(
            f"/memory/admin/memories/{created_payload['memory_id']}/govern",
            json={"action": "review", "reason": "verify wording"},
        )
        facets = client.get("/memory/admin/profile/facets")
        facet_payload = facets.json()["items"][0] if facets.status_code == 200 and facets.json()["items"] else {}
        pinned_facet = client.post(
            f"/memory/admin/profile/facets/{facet_payload.get('facet_id', 'missing')}/govern",
            json={"action": "pin", "reason": "operator confirmed profile facet"},
        )
        rebuilt_facets = client.post("/memory/admin/profile/facets/rebuild", json={"source": "gateway-test"})
        facet_audit = client.get("/memory/admin/profile/facets/audit")
        governance_plan = client.post("/memory/admin/governance", json={"policy": "review", "layer_id": "user", "limit": 5})
        maintenance = client.post(
            "/memory/admin/maintenance",
            json={"dry_run": True, "policy": "review", "layer_id": "user", "limit": 5},
        )
        maintenance_status = client.get("/memory/admin/maintenance/automation")
        maintenance_automation = client.post("/memory/admin/maintenance/automation/run", json={"force_run": True})

        client.post("/threads", json={"thread_id": "thread-archive"})
        run = client.post(
            "/threads/thread-archive/runs",
            json={"message": "Remember that the codename is Northstar."},
        )
        app.state.runtime_deps.run_engine.wait_for_background_tasks(timeout_seconds=5)
        archive = client.post(
            "/memory/archive/search",
            json={"query": "Northstar", "limit": 5},
        )
        benchmark = client.post(
            "/memory/admin/benchmark",
            json={
                "suite_id": "gateway-memory-recall",
                "cases": [
                    {
                        "case_id": "northstar-archive",
                        "query": "Northstar codename",
                        "thread_id": "thread-benchmark",
                        "expected_terms": ["Northstar"],
                        "expected_archive_thread_ids": ["thread-archive"],
                    }
                ],
            },
        )
        benchmark_suite = client.post(
            "/memory/admin/benchmark/suites",
            json={
                "suite_id": "gateway-recall-suite",
                "name": "Gateway recall suite",
                "description": "Persistent suite for recall regression.",
                "tags": ["gateway", "memory"],
                "cases": [
                    {
                        "case_id": "northstar-archive",
                        "query": "Northstar codename",
                        "thread_id": "thread-benchmark",
                        "expected_terms": ["Northstar"],
                        "expected_archive_thread_ids": ["thread-archive"],
                    }
                ],
            },
        )
        benchmark_suites = client.get("/memory/admin/benchmark/suites")
        benchmark_suite_run = client.post(
            "/memory/admin/benchmark/suites/gateway-recall-suite/run",
            json={"evidence_limit": 3, "source": "test"},
        )
        benchmark_runs = client.get("/memory/admin/benchmark/runs?suite_id=gateway-recall-suite")
        flush = client.post("/memory/admin/flush", json={"thread_id": "thread-archive"})
        export = client.get("/memory/admin/export")
        imported_review = client.post(
            "/memory/admin/import",
            json={
                "review_queue": [
                    {
                        "review_id": "review-imported",
                        "layer_id": "workspace",
                        "store_id": "runtime_memory",
                        "action": "add",
                        "content": "Imported review candidate.",
                        "category": "project_context",
                        "priority": 0.5,
                        "confidence": 0.5,
                        "salience": 0.5,
                        "evidence_refs": ["manual-import"],
                    }
                ]
            },
        )
        audit = client.get("/memory/admin/audit")
        review = client.get("/memory/admin/review")
        batch_review = client.post("/memory/admin/review/batch", json={"approve": [], "reject": []})
        conflicts = client.get("/memory/admin/conflicts")
        activate = client.post("/memory/providers/anvil_factgraph/activate")
        provider_test = client.post("/memory/providers/local_curated/test")
        provider_reload = client.post("/memory/providers/reload")
        reflection = client.post("/memory/reflections/jobs/system-project-recap/run")
        deleted = client.request(
            "DELETE",
            f"/memory/stores/user_profile/entries/{created_payload['entry_id']}",
        )

    assert overview.status_code == 200
    assert overview.json()["store_count"] == 2
    assert overview.json()["runtime_mode"] == "memory_platform"
    assert overview.json()["legacy_capture_enabled"] is False
    assert all(item["effective_max_tokens"] > 0 for item in overview.json()["stores"])
    assert all(item["effective_injection_tokens"] > 0 for item in overview.json()["stores"])
    assert {item["store_id"] for item in stores.json()} == {"runtime_memory", "user_profile"}
    assert all(item["max_tokens"] for item in stores.json())
    assert all(item["injection_tokens"] for item in stores.json())
    assert any(item["provider_id"] == "anvil_factgraph" for item in providers.json())
    assert any(item["job_id"] == "system-project-recap" for item in jobs.json())
    assert created.status_code == 200
    assert updated.status_code == 200
    assert governed.status_code == 200
    assert governed.json()["action"] == "reinforce"
    assert governed.json()["after_retention"]["access_count"] >= governed.json()["before_retention"]["access_count"] + 1
    assert governed_review.status_code == 200
    assert governed_review.json()["review_item"]["action"] == "review_existing"
    assert facets.status_code == 200
    assert facets.json()["policy"]["active_threshold"] == 1.5
    assert "style" in facets.json()["policy"]["class_budgets"]
    assert facet_payload["source_memory_id"] == created_payload["memory_id"]
    assert pinned_facet.status_code == 200
    assert pinned_facet.json()["facet"]["user_state"] == "pinned"
    assert rebuilt_facets.status_code == 200
    assert rebuilt_facets.json()["facet_count"] >= 1
    assert facet_audit.status_code == 200
    assert any(item["action"] in {"pin", "rebuild"} for item in facet_audit.json()["items"])
    assert governance_plan.status_code == 200
    assert governance_plan.json()["dry_run"] is True
    assert "items" in governance_plan.json()
    assert maintenance.status_code == 200
    assert maintenance.json()["dry_run"] is True
    assert "governance" in maintenance.json()
    assert maintenance_status.status_code == 200
    assert maintenance_status.json()["enabled"] is True
    assert maintenance_status.json()["policy"] == "balanced"
    assert maintenance_automation.status_code == 200
    assert maintenance_automation.json()["ran"] is True
    assert maintenance_automation.json()["reason"] == "forced"
    assert maintenance_automation.json()["report"]["source"] == "automation"
    assert run.status_code == 200
    assert archive.status_code == 200
    assert archive.json()["hits"]
    assert benchmark.status_code == 200
    assert benchmark.json()["passed"] is True
    assert benchmark.json()["case_count"] == 1
    assert benchmark_suite.status_code == 200
    assert benchmark_suite.json()["suite_id"] == "gateway-recall-suite"
    assert benchmark_suites.status_code == 200
    assert benchmark_suites.json()["items"][0]["latest_run_id"] is None
    assert benchmark_suite_run.status_code == 200
    assert benchmark_suite_run.json()["report"]["passed"] is True
    assert benchmark_runs.status_code == 200
    assert benchmark_runs.json()["items"][0]["suite_id"] == "gateway-recall-suite"
    assert flush.status_code == 200
    assert "candidates_seen" in flush.json()
    assert "candidate_audit" in flush.json()
    assert export.status_code == 200
    assert "stores" in export.json()
    assert imported_review.status_code == 200
    assert imported_review.json()["review_items_created"] == 1
    assert audit.status_code == 200
    assert "pending_review_count" in audit.json()
    assert "candidate_audit" in audit.json()
    assert "recall_benchmark_suites" in audit.json()
    assert "recall_benchmark_runs" in audit.json()
    assert review.status_code == 200
    assert "items" in review.json()
    assert batch_review.status_code == 200
    assert batch_review.json()["approved"] == []
    assert conflicts.status_code == 200
    assert all("recommended_action" in item for item in conflicts.json()["items"])
    assert activate.status_code == 200
    assert provider_test.status_code == 200
    assert provider_test.json()["provider_id"] == "local_curated"
    assert provider_reload.status_code == 200
    assert any(item["provider_id"] == "local_curated" for item in provider_reload.json())
    assert reflection.status_code == 200
    assert deleted.status_code == 200
