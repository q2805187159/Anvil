from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from anvil.agents import ThreadLifecycleStatus, ThreadState

from conftest import build_gateway_config_layers


def test_gateway_self_upgrade_health_exposes_memory_and_skill_backlog(gateway_app_factory, contract_tmp_path) -> None:
    config_layers = build_gateway_config_layers(contract_tmp_path)
    config_layers[0].data["hcms"] = {
        "enabled": True,
        "archive": {"sqlite_path": str(contract_tmp_path / "archive.sqlite3")},
        "update_queue": {"min_batch_turns": 4, "max_batch_turns": 8},
    }
    app = gateway_app_factory(config_layers=config_layers)
    with TestClient(app) as client:
        deps = client.app.state.runtime_deps
        deps.memory_manager.create_entry(
            "hcms_workspace",
            content="Release workflow requires canary verification before deploy.",
            category="project_context",
            confidence=0.40,
            salience=0.20,
        )
        deps.memory_manager.hcms_service.enqueue_capture(
            deps.memory_manager.hcms_service.build_capture_envelope(
                thread_id="thread-low-signal",
                namespace="global/default",
                messages=[HumanMessage(content="ordinary progress update")],
                trace_id="thread-low-signal",
            )
        )
        deps.skills_service.manage_curator(
            config=deps.effective_config,
            action="learn_procedure",
            title="Weak One Off Procedure",
            trigger="A vague task might repeat.",
            steps=["Do the thing.", "Summarize it."],
            expected_outcome="",
            evidence_refs=["thread:weak"],
            source_ref="thread:weak",
            outcome="success",
            feedback_source="runtime_success",
            confidence=0.95,
        )
        deps.skills_service.manage_curator(
            config=deps.effective_config,
            action="learn_procedure",
            title="Weak One Off Procedure",
            trigger="A vague task might repeat.",
            steps=["Do the thing.", "Summarize it."],
            expected_outcome="",
            evidence_refs=["thread:weak-2"],
            source_ref="thread:weak-2",
            outcome="success",
            feedback_source="runtime_success",
            confidence=0.95,
        )

        response = client.get("/self-upgrade/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "self_upgrade_health"
    assert payload["fingerprint"]
    assert payload["status"] in {"watch", "needs_attention"}

    domains = {domain["domain_id"]: domain for domain in payload["domains"]}
    assert set(domains) == {"memory", "skills", "trajectory"}
    assert domains["memory"]["metrics"]["update_queue_pending"] == 1
    assert domains["memory"]["metrics"]["low_confidence_count"] == 1
    assert domains["skills"]["metrics"]["procedures_total"] == 1
    assert domains["skills"]["metrics"]["procedures_with_blockers"] == 1
    assert domains["trajectory"]["metrics"]["thread_count"] >= 0

    backlog_ids = {item["item_id"] for item in payload["backlog"]}
    assert "memory:update_queue_pending" in backlog_ids
    assert "memory:quality_issues" in backlog_ids
    assert "skills:procedure_blockers" in backlog_ids


def test_gateway_self_upgrade_health_exposes_trajectory_quality_domain(gateway_app_factory, contract_tmp_path) -> None:
    config_layers = build_gateway_config_layers(contract_tmp_path)
    config_layers[0].data["hcms"] = {"enabled": False}
    config_layers[0].data["trajectory_export"] = {
        "enabled": True,
        "export_root": str(contract_tmp_path / "trajectories"),
        "batch_min_quality_status_default": "warning",
    }
    app = gateway_app_factory(config_layers=config_layers)
    with TestClient(app) as client:
        deps = client.app.state.runtime_deps
        deps.checkpointer.put_thread_state(
            ThreadState(
                identity={"thread_id": "thread-trajectory-good", "run_id": "run-good"},
                lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
                conversation={
                    "messages": [
                        {"role": "human", "content": "Summarize deployment."},
                        {"role": "ai", "content": "Deployment is green."},
                    ]
                },
            )
        )
        deps.checkpointer.put_thread_state(
            ThreadState(
                identity={"thread_id": "thread-trajectory-bad", "run_id": "run-bad"},
                lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
                conversation={"messages": [{"role": "human", "content": "Only user content."}]},
            )
        )

        response = client.get("/self-upgrade/health")

    assert response.status_code == 200
    payload = response.json()
    domains = {domain["domain_id"]: domain for domain in payload["domains"]}
    assert "trajectory" in domains
    assert domains["trajectory"]["metrics"]["thread_count"] == 2
    assert domains["trajectory"]["metrics"]["quality_failed_count"] == 1
    assert domains["trajectory"]["metrics"]["quality_filtered_count"] == 1
    assert "trajectory:quality_failed" in {item["item_id"] for item in payload["backlog"]}
