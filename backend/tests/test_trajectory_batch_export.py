from __future__ import annotations

import json

from anvil.agents import ThreadLifecycleStatus, ThreadState
from anvil.config import EffectiveConfig, SkillsConfig
from anvil.runtime.checkpointers import InMemoryCheckpointer
from anvil.skills import SkillsService
from anvil.trajectory import (
    TrajectoryBatchExportRequest,
    TrajectoryBatchExporter,
    TrajectoryExportFormat,
    TrajectoryExportOptions,
    TrajectoryQualityStatus,
)


def make_state(thread_id: str, content: str) -> ThreadState:
    return ThreadState(
        identity={"thread_id": thread_id},
        lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
        conversation={
            "messages": [
                {"role": "human", "content": content},
                {"role": "ai", "content": f"answer {content}"},
            ]
        },
        execution={"active_model": "minimax/MiniMax-M2.7"},
    )


def make_tool_state(thread_id: str) -> ThreadState:
    return ThreadState(
        identity={"thread_id": thread_id, "run_id": "run-learn"},
        lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
        conversation={
            "messages": [
                {"role": "human", "content": "Inspect and verify a file."},
                {"role": "ai", "content": "The file was inspected and verified."},
            ],
            "steps": [
                {"type": "call", "step_id": "step-read", "tool_name": "read_file", "status": "success", "visibility": "chat"},
                {"type": "call", "step_id": "step-search", "tool_name": "search_files", "status": "success", "visibility": "chat"},
                {"type": "content", "payload": "The file was inspected and verified."},
            ],
        },
        execution={"active_model": "minimax/MiniMax-M2.7"},
    )


def test_batch_export_writes_jsonl_and_manifest(contract_tmp_path) -> None:
    checkpointer = InMemoryCheckpointer()
    checkpointer.put_thread_state(make_state("thread-a", "a"))
    checkpointer.put_thread_state(make_state("thread-b", "b"))

    request = TrajectoryBatchExportRequest(
        options=TrajectoryExportOptions(format=TrajectoryExportFormat.SHAREGPT),
        include_entries=True,
    )
    result, manifest = TrajectoryBatchExporter(
        checkpointer=checkpointer,
        export_root=contract_tmp_path,
    ).export(request)

    assert result.exported_count == 2
    assert result.skipped_count == 0
    assert len(result.entries) == 2
    assert manifest.exported_count == 2
    assert manifest.stats["completed_count"] == 2
    assert manifest.stats["quality_passed_count"] == 2
    assert manifest.stats["quality_failed_count"] == 0
    assert manifest.stats["models"] == {"minimax/MiniMax-M2.7": 2}
    assert manifest.jsonl_path is not None
    assert manifest.manifest_path is not None
    lines = [json.loads(line) for line in open(manifest.jsonl_path, encoding="utf-8")]
    assert lines[0]["conversations"][0]["from"] == "human"
    manifest_payload = json.loads(open(manifest.manifest_path, encoding="utf-8").read())
    assert manifest_payload["exported_count"] == 2


def test_batch_export_reports_missing_threads(contract_tmp_path) -> None:
    checkpointer = InMemoryCheckpointer()
    checkpointer.put_thread_state(make_state("thread-a", "a"))

    result, manifest = TrajectoryBatchExporter(
        checkpointer=checkpointer,
        export_root=contract_tmp_path,
    ).export(TrajectoryBatchExportRequest(thread_ids=["thread-a", "missing"]))

    assert result.exported_count == 1
    assert result.skipped_count == 1
    assert "missing: thread not found" in result.diagnostics
    assert manifest.skipped_count == 1


def test_batch_export_filters_failed_quality_entries_before_jsonl(contract_tmp_path) -> None:
    checkpointer = InMemoryCheckpointer()
    checkpointer.put_thread_state(make_state("thread-good", "good"))
    checkpointer.put_thread_state(
        ThreadState(
            identity={"thread_id": "thread-bad"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={"messages": [{"role": "human", "content": "only user"}]},
        )
    )

    result, manifest = TrajectoryBatchExporter(
        checkpointer=checkpointer,
        export_root=contract_tmp_path,
    ).export(
        TrajectoryBatchExportRequest(
            include_entries=True,
            min_quality_status=TrajectoryQualityStatus.WARNING,
        )
    )

    assert result.exported_count == 1
    assert result.skipped_count == 1
    assert [entry.thread_id for entry in result.entries] == ["thread-good"]
    assert "thread-bad: filtered by quality gate failed < warning" in result.diagnostics
    assert manifest.exported_count == 1
    assert manifest.skipped_count == 1
    assert manifest.stats["quality_passed_count"] == 1
    assert manifest.stats["quality_failed_count"] == 0
    assert manifest.jsonl_path is not None
    lines = [json.loads(line) for line in open(manifest.jsonl_path, encoding="utf-8")]
    assert [line["thread_id"] for line in lines] == ["thread-good"]


def test_batch_export_can_keep_failed_quality_entries_for_audit(contract_tmp_path) -> None:
    checkpointer = InMemoryCheckpointer()
    checkpointer.put_thread_state(
        ThreadState(
            identity={"thread_id": "thread-bad"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={"messages": [{"role": "human", "content": "only user"}]},
        )
    )

    result, manifest = TrajectoryBatchExporter(
        checkpointer=checkpointer,
        export_root=contract_tmp_path,
    ).export(
        TrajectoryBatchExportRequest(
            include_entries=True,
            min_quality_status=TrajectoryQualityStatus.FAILED,
        )
    )

    assert result.exported_count == 1
    assert result.skipped_count == 0
    assert result.entries[0].quality.status == "failed"
    assert manifest.stats["quality_failed_count"] == 1


def test_batch_export_can_learn_procedures_from_kept_trajectories(contract_tmp_path, monkeypatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    checkpointer = InMemoryCheckpointer()
    checkpointer.put_thread_state(make_tool_state("thread-learn"))
    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    skills_service = SkillsService()

    result, manifest = TrajectoryBatchExporter(
        checkpointer=checkpointer,
        export_root=contract_tmp_path,
        config=config,
        skills_service=skills_service,
    ).export(TrajectoryBatchExportRequest(learn_procedures=True, include_entries=True))

    assert result.exported_count == 1
    learning = manifest.stats["procedure_learning"]
    assert learning["enabled"] is True
    assert learning["accepted_count"] == 1
    assert learning["skipped_count"] == 0
    procedures = skills_service.manage_curator(config=config, action="procedures")
    assert procedures["counts"]["total"] == 1
    assert procedures["items"][0]["source_refs"][0] == "thread:thread-learn/run:run-learn"
    assert procedures["items"][0]["quality"]["evidence_count"] >= 2
    assert procedures["items"][0]["promotion_readiness"]["promotable"] is False
