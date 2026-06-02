from __future__ import annotations

from anvil.agents import ThreadLifecycleStatus, ThreadState
from anvil.config import EffectiveConfig, MemoryPlatformConfig, SkillsConfig
from anvil.memory_platform import MemoryManager
from anvil.memory_platform.contracts import MemoryCandidateAuditEntry, MemoryRecallBenchmarkCase, MemoryRecallBenchmarkSuite
from anvil.runtime.checkpointers import InMemoryCheckpointer
from anvil.skills import SkillsService
from anvil.self_upgrade import SelfUpgradeHealthService


def test_self_upgrade_health_report_unifies_memory_and_skill_quality_signals(contract_tmp_path, monkeypatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        memory_platform=MemoryPlatformConfig(
            enabled=True,
            archive={"sqlite_path": str(contract_tmp_path / "archive.sqlite3")},
            update_queue={"min_batch_turns": 4, "max_batch_turns": 8},
        ),
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        ),
    )
    memory_manager = MemoryManager.from_config(
        config=config.memory_platform,
        base_path=contract_tmp_path / "memory-platform",
    )
    memory_manager.create_entry(
        "runtime_memory",
        content="Release workflow requires canary verification before deploy.",
        category="project_context",
        confidence=0.40,
        salience=0.20,
    )
    memory_manager.record_turn(
        thread_id="thread-low-signal",
        user_content="ordinary progress update",
        assistant_content="acknowledged",
    )
    memory_manager._record_candidate_audit(  # type: ignore[attr-defined]
        MemoryCandidateAuditEntry(
            audit_id="candidate-skip-1",
            action="skip",
            reason="quality gate skipped candidate",
            layer_id="runtime",
            store_id="runtime_memory",
            candidate_preview="One-off weak memory candidate.",
            quality_score=0.18,
            quality_decision="skip",
            blockers=("weak_evidence",),
            confidence=0.2,
            salience=0.2,
            priority=0.1,
        )
    )

    skills_service = SkillsService()
    skills_service.manage_curator(
        config=config,
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
    skills_service.manage_curator(
        config=config,
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

    report = SelfUpgradeHealthService().report(
        config=config,
        memory_manager=memory_manager,
        skills_service=skills_service,
        checkpointer=InMemoryCheckpointer(),
        fingerprint="health-test",
    )

    assert report.mode == "self_upgrade_health"
    assert report.fingerprint == "health-test"
    assert report.status in {"watch", "needs_attention"}
    assert report.score < 1.0

    domains = {domain.domain_id: domain for domain in report.domains}
    assert set(domains) == {"memory", "skills", "trajectory"}
    assert domains["memory"].metrics["update_queue_pending"] == 1
    assert domains["memory"].metrics["candidate_audit_skip_count"] == 1
    assert domains["memory"].metrics["low_confidence_count"] == 1
    assert domains["skills"].metrics["procedures_total"] == 1
    assert domains["skills"].metrics["procedures_promotable"] == 0
    assert domains["skills"].metrics["procedures_with_blockers"] == 1
    assert domains["trajectory"].metrics["thread_count"] == 0
    assert domains["trajectory"].metrics["exported_count"] == 0

    backlog_ids = {item.item_id for item in report.backlog}
    assert "memory:update_queue_pending" in backlog_ids
    assert "memory:candidate_audit_skipped" in backlog_ids
    assert "skills:procedure_blockers" in backlog_ids
    assert all(item.domain in {"memory", "skills"} for item in report.backlog)


def test_self_upgrade_health_snapshots_track_recall_benchmark_improvement(
    contract_tmp_path,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        memory_platform=MemoryPlatformConfig(
            enabled=True,
            archive={"sqlite_path": str(contract_tmp_path / "archive.sqlite3")},
        ),
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        ),
    )
    memory_manager = MemoryManager.from_config(
        config=config.memory_platform,
        base_path=contract_tmp_path / "memory-platform",
    )
    memory = memory_manager.create_entry(
        "runtime_memory",
        content="Northstar release uses canary deployment with pytest smoke verification.",
        category="project_context",
        confidence=0.95,
        salience=0.95,
        evidence_refs=("doc:northstar",),
    )
    memory_manager.upsert_recall_benchmark_suite(
        MemoryRecallBenchmarkSuite(
            suite_id="northstar-release",
            name="Northstar release",
            cases=(
                MemoryRecallBenchmarkCase(
                    case_id="northstar-canary",
                    query="Northstar canary pytest",
                    expected_terms=("canary deployment", "pytest"),
                    expected_memory_ids=(memory.memory_id or memory.entry_id,),
                ),
            ),
        ),
        source="test",
    )

    service = SelfUpgradeHealthService()
    skills_service = SkillsService()
    before = service.report(
        config=config,
        memory_manager=memory_manager,
        skills_service=skills_service,
        checkpointer=InMemoryCheckpointer(),
        fingerprint="snapshot-test",
    )
    before_memory = next(domain for domain in before.domains if domain.domain_id == "memory")
    assert before_memory.metrics["recall_benchmark_suite_count"] == 1
    assert before_memory.metrics["recall_benchmark_run_count"] == 0
    assert before_memory.metrics["recall_benchmark_unrun_suite_count"] == 1
    assert any(item.item_id == "memory:recall_benchmark_unrun" for item in before.backlog)

    baseline = service.record_snapshot(
        report=before,
        state_root=contract_tmp_path / "self-upgrade",
        label="before-stage-6",
        source="test",
    )

    run = memory_manager.run_recall_benchmark_suite("northstar-release", source="test")
    assert run.report.passed is True

    after = service.report(
        config=config,
        memory_manager=memory_manager,
        skills_service=skills_service,
        checkpointer=InMemoryCheckpointer(),
        fingerprint="snapshot-test",
    )
    after_memory = next(domain for domain in after.domains if domain.domain_id == "memory")
    assert after_memory.metrics["recall_benchmark_run_count"] == 1
    assert after_memory.metrics["recall_benchmark_latest_score"] == 1.0
    assert after_memory.metrics["recall_benchmark_failed_latest_count"] == 0
    assert not any(item.item_id == "memory:recall_benchmark_unrun" for item in after.backlog)

    candidate = service.record_snapshot(
        report=after,
        state_root=contract_tmp_path / "self-upgrade",
        label="after-stage-6",
        source="test",
    )
    snapshots = service.list_snapshots(state_root=contract_tmp_path / "self-upgrade")

    assert candidate.previous_snapshot_id == baseline.snapshot_id
    assert candidate.backlog_delta < 0
    assert candidate.score_delta >= 0
    assert candidate.improved is True
    assert [item.snapshot_id for item in snapshots] == [candidate.snapshot_id, baseline.snapshot_id]


def test_self_upgrade_health_includes_read_only_trajectory_quality_domain(contract_tmp_path, monkeypatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        memory_platform=MemoryPlatformConfig(enabled=False),
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        ),
        trajectory_export={
            "enabled": True,
            "export_root": str(contract_tmp_path / "trajectories"),
            "batch_min_quality_status_default": "warning",
        },
    )
    checkpointer = InMemoryCheckpointer()
    checkpointer.put_thread_state(
        ThreadState(
            identity={"thread_id": "thread-good", "run_id": "run-good"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={
                "messages": [
                    {"role": "human", "content": "Summarize release risk."},
                    {"role": "ai", "content": "Release risk is low."},
                ]
            },
        )
    )
    checkpointer.put_thread_state(
        ThreadState(
            identity={"thread_id": "thread-bad", "run_id": "run-bad"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={"messages": [{"role": "human", "content": "Only user content."}]},
        )
    )

    report = SelfUpgradeHealthService().report(
        config=config,
        skills_service=SkillsService(),
        checkpointer=checkpointer,
        trajectory_export_root=contract_tmp_path / "trajectories",
        fingerprint="trajectory-health-test",
    )

    domains = {domain.domain_id: domain for domain in report.domains}
    assert set(domains) == {"memory", "skills", "trajectory"}
    assert domains["trajectory"].metrics["thread_count"] == 2
    assert domains["trajectory"].metrics["quality_passed_count"] == 1
    assert domains["trajectory"].metrics["quality_failed_count"] == 1
    assert domains["trajectory"].metrics["quality_filtered_count"] == 1
    assert domains["trajectory"].metrics["procedure_learning_enabled"] is False
    assert domains["trajectory"].status in {"watch", "needs_attention"}

    backlog_ids = {item.item_id for item in report.backlog}
    assert "trajectory:quality_failed" in backlog_ids
    assert "trajectory:quality_filtered" in backlog_ids
    assert any(item.domain == "trajectory" for item in report.backlog)
    assert not (contract_tmp_path / "trajectories").exists()
