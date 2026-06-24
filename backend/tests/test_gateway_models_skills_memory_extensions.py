from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from anvil.config import ConfigLayer, ConfigLayerKind
from fake_models import BindableFakeMessagesListChatModel
from conftest import write_test_skill


def test_models_skills_memory_and_extensions_endpoints(gateway_client) -> None:
    models = gateway_client.get("/models")
    assert models.status_code == 200
    assert models.json()[0]["name"] == "openai"

    skills = gateway_client.get("/skills")
    assert skills.status_code == 200
    assert any(item["skill_id"] == "demo-skill" for item in skills.json())

    memory = gateway_client.get("/memory")
    assert memory.status_code == 200
    assert memory.json()["store_count"] == 2

    stores = gateway_client.get("/memory/stores")
    assert stores.status_code == 200
    assert {item["store_id"] for item in stores.json()} == {"hcms_workspace", "hcms_user"}

    extensions = gateway_client.get("/extensions")
    assert extensions.status_code == 200
    assert extensions.json()[0]["server_id"] == "github"

    overview = gateway_client.get("/config/overview")
    assert overview.status_code == 200
    payload = overview.json()
    assert payload["models"]["total"] >= 1
    assert payload["skills"]["source_counts"]["external"] == 1
    assert payload["skills"]["enabled_source_counts"]["external"] == 1
    assert payload["mcp"]["total"] == 1


def test_skills_endpoint_hot_loads_new_skill_directories(gateway_app_factory, contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "gateway-hot-skills"
    write_test_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {"openai": {"name": "openai", "provider": "openai", "model_name": "gpt-5.4"}},
                    "skills_config": {
                        "enabled": True,
                        "watch_enabled": True,
                        "external_dirs": [str(skills_root)],
                    },
                },
            )
        ]
    )

    with TestClient(app) as client:
        first = client.get("/skills")
        assert first.status_code == 200
        first_ids = {item["skill_id"] for item in first.json()}
        assert "alpha" in first_ids
        assert "beta" not in first_ids

        write_test_skill(skills_root, "beta", "Beta Skill", "Beta summary")
        second = client.get("/skills")

    assert second.status_code == 200
    payload = second.json()
    by_id = {item["skill_id"]: item for item in payload}
    assert "alpha" in by_id
    assert "beta" in by_id
    assert by_id["alpha"]["enabled"] is True
    assert by_id["beta"]["enabled"] is True


def test_skill_curator_endpoint_learns_and_promotes_procedures(gateway_app_factory, contract_tmp_path, monkeypatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {"openai": {"name": "openai", "provider": "openai", "model_name": "gpt-5.4"}},
                    "skills_config": {
                        "enabled": True,
                        "governance_root": str(contract_tmp_path / "governance"),
                    },
                },
            )
        ]
    )

    with TestClient(app) as client:
        learned = client.post(
            "/skills/curator",
            json={
                "action": "learn_procedure",
                "title": "Verify Focused Code Change",
                "trigger": "A code change needs direct regression evidence before handoff.",
                "steps": [
                    "Inspect the relevant module boundary.",
                    "Apply the scoped edit.",
                    "Run the narrow regression command.",
                ],
                "expected_outcome": "The change is ready with concrete verification evidence.",
                "evidence_refs": ["thread:gateway-procedure"],
                "source_ref": "thread:gateway-procedure",
                "allowed_tools": ["read_file", "apply_patch", "shell_command"],
                "confidence": 0.88,
            },
        )
        assert learned.status_code == 200
        procedure_id = learned.json()["procedure_id"]

        report = client.post("/skills/curator", json={"action": "procedures"})
        assert report.status_code == 200
        assert report.json()["items"][0]["procedure_id"] == procedure_id

        promoted = client.post(
            "/skills/curator",
            json={
                "action": "promote_procedure",
                "procedure_id": procedure_id,
                "skill_id": "agent-gateway-focused-verification",
            },
        )

    assert promoted.status_code == 200
    assert promoted.json()["accepted"] is True
    skill_path = workspace_skills / "agent-gateway-focused-verification" / "SKILL.md"
    assert skill_path.exists()
    assert "Verify Focused Code Change" in skill_path.read_text(encoding="utf-8")


def test_skill_curator_maintenance_endpoint_runs_bounded_plan(gateway_app_factory, contract_tmp_path, monkeypatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {"openai": {"name": "openai", "provider": "openai", "model_name": "gpt-5.4"}},
                    "skills_config": {
                        "enabled": True,
                        "governance_root": str(contract_tmp_path / "governance"),
                        "curator": {
                            "max_quality_plan_per_run": 1,
                            "max_actions_per_run": 1,
                        },
                    },
                },
            )
        ]
    )

    with TestClient(app) as client:
        for skill_id in ("agent-review-a", "agent-review-b"):
            created = client.post(
                "/skills/curator",
                json={
                    "action": "create",
                    "skill_id": skill_id,
                    "title": skill_id,
                    "summary": f"{skill_id} summary",
                    "body": f"Use when {skill_id} should be reviewed.",
                },
            )
            assert created.status_code == 200
            feedback = client.post(
                "/skills/curator",
                json={
                    "action": "feedback",
                    "skill_id": skill_id,
                    "outcome": "failure",
                    "rationale": "Needs review.",
                    "feedback_source": "user",
                    "confidence": 1.0,
                },
            )
            assert feedback.status_code == 200

        planned = client.post("/skills/curator/maintenance", json={"dry_run": True, "source": "test"})
        executed = client.post("/skills/curator/maintenance", json={"dry_run": False, "source": "test"})

    assert planned.status_code == 200
    planned_payload = planned.json()
    assert planned_payload["status"] == "planned"
    assert planned_payload["selected_count"] == 1
    assert planned_payload["skipped_actions"]["quality_plan"] == 1
    assert executed.status_code == 200
    executed_payload = executed.json()
    assert executed_payload["status"] == "completed"
    assert executed_payload["actions_executed"]["quality_plan"] == 1


def test_skill_curator_automation_endpoint_exposes_status_and_force_run(gateway_app_factory, contract_tmp_path, monkeypatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {"openai": {"name": "openai", "provider": "openai", "model_name": "gpt-5.4"}},
                    "skills_config": {
                        "enabled": True,
                        "governance_root": str(contract_tmp_path / "governance"),
                        "curator": {
                            "automation_enabled": True,
                            "dry_run": False,
                            "interval_seconds": 3600,
                            "tick_seconds": 10,
                            "max_actions_per_run": 1,
                            "max_quality_plan_per_run": 1,
                        },
                    },
                },
            )
        ]
    )

    with TestClient(app) as client:
        created = client.post(
            "/skills/curator",
            json={
                "action": "create",
                "skill_id": "agent-auto-status",
                "title": "Agent Auto Status",
                "summary": "Exercise curator automation status.",
                "body": "Use when curator automation status should be verified.",
            },
        )
        assert created.status_code == 200
        feedback = client.post(
            "/skills/curator",
            json={
                "action": "feedback",
                "skill_id": "agent-auto-status",
                "outcome": "failure",
                "rationale": "Needs automatic review proposal.",
                "feedback_source": "user",
                "confidence": 1.0,
            },
        )
        assert feedback.status_code == 200

        before = client.get("/skills/curator/automation")
        forced = client.post("/skills/curator/automation/run", json={"force_run": True})
        after = client.get("/skills/curator/automation")

    assert before.status_code == 200
    before_payload = before.json()
    assert before_payload["enabled"] is True
    assert before_payload["dry_run"] is False
    assert before_payload["auto_merge"] is True
    assert before_payload["pin_protection"] is True
    assert forced.status_code == 200
    forced_payload = forced.json()
    assert forced_payload["ran"] is True
    assert forced_payload["reason"] == "forced"
    assert forced_payload["report"]["accepted"] is True
    assert after.status_code == 200
    after_payload = after.json()
    assert after_payload["last_run_id"] == forced_payload["report"]["run_id"]
    assert after_payload["last_status"] == "completed"
    assert after_payload["last_recommendation_count"] >= 1
    assert after_payload["last_recommendations"][0]["next_tool_call"]["action"] == "quality_plan"


def test_models_endpoint_reports_braced_secret_env_diagnostics(gateway_app_factory) -> None:
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "braced",
                    "models": {
                        "braced": {
                            "name": "braced",
                            "use": "langchain_openai:ChatOpenAI",
                            "model": "gpt-5.4",
                            "api_key": "${MISSING_GATEWAY_BRACED_KEY}",
                        }
                    },
                },
            )
        ]
    )

    with TestClient(app) as client:
        response = client.get("/models")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["available"] is False
    assert payload["diagnostics"] == ["missing environment variable MISSING_GATEWAY_BRACED_KEY"]


def test_models_endpoint_reports_provider_model_catalog(gateway_app_factory) -> None:
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "MiMo",
                    "models": {
                        "MiMo": {
                            "name": "MiMo",
                            "use": "langchain_openai:ChatOpenAI",
                            "provider": "openai",
                            "model": ["mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-flash"],
                            "default_model": "mimo-v2-flash",
                            "api_key": "${MIMO_API_KEY}",
                            "context_window_tokens": 1048576,
                            "auto_compact_threshold_tokens": 786432,
                            "model_context_windows": {
                                "mimo-v2.5-pro": 1048576,
                                "mimo-v2.5": 1048576,
                                "mimo-v2-flash": 32768,
                            },
                            "model_auto_compact_thresholds": {
                                "mimo-v2.5-pro": 786432,
                                "mimo-v2.5": 786432,
                                "mimo-v2-flash": 24576,
                            },
                        }
                    },
                },
            )
        ]
    )

    with TestClient(app) as client:
        response = client.get("/models")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["model_catalog"] == ["mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-flash"]
    assert payload["default_model"] == "mimo-v2-flash"
    assert payload["model_name"] == "mimo-v2-flash"
    assert payload["context_window_tokens"] == 32768
    assert payload["auto_compact_threshold_tokens"] == 24576
    assert payload["model_context_windows"]["mimo-v2-flash"] == 32768


def test_thread_state_reports_context_window_usage(gateway_app_factory) -> None:
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "base_url": "https://example.test/v1",
                            "model": "gpt-5.4",
                            "context_window_tokens": 1000,
                            "auto_compact_threshold_tokens": 800,
                        }
                    },
                    "token_usage": {"enabled": True},
                },
            )
        ],
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="usage tracked",
                    usage_metadata={"input_tokens": 600, "output_tokens": 50, "total_tokens": 650},
                )
            ]
        ),
    )

    with TestClient(app) as client:
        create = client.post("/threads", json={"thread_id": "thread-context-usage"})
        assert create.status_code == 200
        run = client.post("/threads/thread-context-usage/runs", json={"message": "track context"})
        assert run.status_code == 200

        state = run.json()["state"]
        usage = state["context_window_usage"]

    assert usage["model"] == "openai"
    assert usage["concrete_model"] == "gpt-5.4"
    assert usage["provider"] == "openai"
    assert usage["request_count"] == 1
    assert usage["context_tokens"] == usage["estimated_context_tokens"]
    assert usage["estimated_context_tokens"] >= 1
    assert usage["context_source"] == "estimated"
    assert usage["context_breakdown"]["messages"] >= 1
    assert usage["context_breakdown_percentages"]["messages"] > 0
    assert usage["dominant_context_category"] in usage["context_breakdown"]
    assert usage["input_tokens"] == 600
    assert usage["output_tokens"] == 50
    assert usage["total_tokens"] == 650
    assert usage["context_window_tokens"] == 1000
    assert usage["auto_compact_threshold_tokens"] == 800
    assert usage["usage_ratio"] == min(usage["context_tokens"] / 1000, 1.0)
    assert usage["compact_ratio"] == min(usage["context_tokens"] / 800, 1.0)
    assert usage["compact_status"] in {"below_threshold", "over_threshold"}


def test_extension_refresh_respects_refresh_policy(gateway_app_factory) -> None:
    from fastapi.testclient import TestClient

    app = gateway_app_factory(refresh_policy="fingerprint")
    with TestClient(app) as client:
        response = client.post("/extensions/github/refresh")
        assert response.status_code == 409
        assert response.json()["error"] == "refresh_not_enabled"

    app_dynamic = gateway_app_factory(refresh_policy="dynamic")
    with TestClient(app_dynamic) as client:
        response = client.post("/extensions/github/refresh")
        assert response.status_code == 200
        assert response.json()["server_id"] == "github"


def test_memory_endpoint_reflects_captured_turns_after_run(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="Stored for later.")]
        )
    )

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-memory"})
        run = client.post(
            "/threads/thread-memory/runs",
            json={"message": "Actually, prefer concise project updates."},
        )

        assert run.status_code == 200
        app.state.runtime_deps.run_engine.wait_for_background_tasks(timeout_seconds=5)

        stores = client.get("/memory/stores")
        assert stores.status_code == 200
        user_store = next(item for item in stores.json() if item["store_id"] == "hcms_user")
        assert user_store["entry_count"] >= 1
        entries = client.get("/memory/stores/hcms_user/entries")
        assert entries.status_code == 200
        assert any("Actually, prefer concise project updates." in item["content"] for item in entries.json())
