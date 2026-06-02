from __future__ import annotations

import pytest

from anvil.config import EffectiveConfig, SkillCuratorConfig, SkillsConfig
from anvil.skills import SkillsService


def _config(contract_tmp_path, *, max_promotions: int = 2) -> EffectiveConfig:
    return EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(max_procedure_promotions_per_run=max_promotions),
        )
    )


def _patch_curator_roots(monkeypatch: pytest.MonkeyPatch, contract_tmp_path) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)


def test_procedure_promotion_regression_suite_requires_repetition_quality_and_verification(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_curator_roots(monkeypatch, contract_tmp_path)
    config = _config(contract_tmp_path, max_promotions=1)
    service = SkillsService()
    common = {
        "title": "Focused Memory Regression Repair",
        "trigger": "A memory regression needs the inspect patch test workflow.",
        "steps": [
            "Inspect the relevant memory manager behavior.",
            "Patch the smallest affected block.",
            "Run the focused pytest regression.",
        ],
        "expected_outcome": "The memory regression is fixed and pytest passes.",
        "allowed_tools": ["read_file", "patch_file", "run_command"],
        "outcome": "success",
        "feedback_source": "runtime_success",
        "confidence": 0.95,
    }

    first = service.manage_curator(
        config=config,
        action="learn_procedure",
        evidence_refs=["thread:alpha/run:1", "pytest:alpha"],
        source_ref="thread:alpha/run:1",
        **common,
    )
    second = service.manage_curator(
        config=config,
        action="learn_procedure",
        evidence_refs=["thread:beta/run:2", "pytest:beta"],
        source_ref="thread:beta/run:2",
        **common,
    )
    report = service.manage_curator(config=config, action="procedures")
    maintenance = service.run_curator_maintenance(config=config, dry_run=False, source="regression-suite")

    assert first["accepted"] is True
    assert second["reinforced"] is True
    assert second["procedure_id"] == first["procedure_id"]
    assert report["counts"]["total"] == 1
    assert report["items"][0]["promotion_readiness"]["promotable"] is True
    assert maintenance["actions_executed"]["promote_procedure"] == 1
    assert (contract_tmp_path / "workspace-skills" / "learned-focused-memory-regression-repair" / "SKILL.md").exists()


def test_procedure_promotion_regression_suite_blocks_weak_and_failed_candidates(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_curator_roots(monkeypatch, contract_tmp_path)
    config = _config(contract_tmp_path)
    service = SkillsService()
    weak_common = {
        "title": "Vague One Off Workflow",
        "trigger": "A vague task might repeat.",
        "steps": ["Do the thing.", "Summarize it."],
        "expected_outcome": "",
        "allowed_tools": ["read_file"],
        "feedback_source": "runtime_success",
        "confidence": 0.95,
    }
    for index in range(2):
        service.manage_curator(
            config=config,
            action="learn_procedure",
            evidence_refs=[f"thread:weak/{index}"],
            source_ref=f"thread:weak/{index}",
            outcome="success",
            **weak_common,
        )
    failed_common = {
        "title": "Flaky Build Repair",
        "trigger": "A flaky build needs a repeated repair flow.",
        "steps": ["Inspect build output.", "Patch the issue.", "Run pytest regression."],
        "expected_outcome": "The build passes with regression evidence.",
        "allowed_tools": ["read_file", "patch_file", "run_command"],
        "feedback_source": "runtime_failure",
        "confidence": 0.95,
    }
    for index in range(2):
        service.manage_curator(
            config=config,
            action="learn_procedure",
            evidence_refs=[f"thread:failed/{index}", f"pytest:failed/{index}"],
            source_ref=f"thread:failed/{index}",
            outcome="failure",
            **failed_common,
        )

    report = service.manage_curator(config=config, action="procedures", outcome="all")
    items = {item["title"]: item for item in report["items"]}

    assert items["Vague One Off Workflow"]["promotion_readiness"]["promotable"] is False
    assert "weak_quality" in items["Vague One Off Workflow"]["promotion_readiness"]["blockers"]
    assert "needs_expected_outcome" in items["Vague One Off Workflow"]["promotion_readiness"]["blockers"]
    assert items["Flaky Build Repair"]["promotion_readiness"]["promotable"] is False
    assert "failure_signal" in items["Flaky Build Repair"]["promotion_readiness"]["blockers"]
