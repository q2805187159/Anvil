from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from anvil.config import EffectiveConfig, SkillsConfig
from anvil.skills import SkillGovernanceService


def write_skill(root: Path, slug: str) -> Path:
    skill_dir = root / slug
    (skill_dir / "assets").mkdir(parents=True, exist_ok=True)
    (skill_dir / "templates").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "title: Alpha Skill\n"
        "summary: Alpha summary\n"
        "version: 1.0.0\n"
        "trust: trusted\n"
        "dependencies:\n"
        "  - kind: tool\n"
        "    name: read_file\n"
        "readiness:\n"
        "  status: ready\n"
        "  checks:\n"
        "    - workspace\n"
        "---\n\n"
        "# Alpha Skill\n\n"
        "Alpha body\n",
        encoding="utf-8",
    )
    (skill_dir / "assets" / "guide.txt").write_text("guide", encoding="utf-8")
    (skill_dir / "templates" / "template.md").write_text("# template", encoding="utf-8")
    (skill_dir / "scripts" / "run.ps1").write_text("Write-Output ok", encoding="utf-8")
    (skill_dir / "references" / "ref.md").write_text("reference", encoding="utf-8")
    return skill_dir


def build_skill_archive(skill_dir: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(skill_dir.parent)).replace("\\", "/"))
    return destination


def test_skill_governance_enable_disable_and_uninstall(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root = contract_tmp_path / "workspace-skills"
    write_skill(workspace_root, "alpha")

    monkeypatch.setattr("anvil.skills.governance.default_installed_skill_root", lambda: workspace_root)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            quarantine_root=str(contract_tmp_path / "governance" / "quarantine"),
            history_root=str(contract_tmp_path / "governance" / "history"),
        )
    )
    service = SkillGovernanceService()

    enable_payload = service.manage(config=config, action="enable", skill_id="alpha")
    disable_payload = service.manage(config=config, action="disable", skill_id="alpha")
    uninstall_payload = service.manage(config=config, action="uninstall", skill_id="alpha")

    assert enable_payload["enabled_ids"] == ["alpha"]
    assert disable_payload["disabled_ids"] == ["alpha"]
    assert uninstall_payload["uninstalled"] is True
    assert not (workspace_root / "alpha").exists()


def test_skill_governance_uninstall_backup_stops_at_scan_budget_without_rglob(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = contract_tmp_path / "workspace-skills"
    skill_dir = write_skill(workspace_root, "alpha")
    references_dir = skill_dir / "references"
    for index in range(16):
        (references_dir / f"guide-{index:02}.md").write_text("reference guide", encoding="utf-8")

    monkeypatch.setattr("anvil.skills.governance.default_installed_skill_root", lambda: workspace_root)
    monkeypatch.setattr("anvil.skills.governance.DEFAULT_GOVERNANCE_BACKUP_SCAN_LIMIT", 5, raising=False)

    def fail_rglob(_self: Path, pattern: str):
        raise AssertionError(f"governance backup should not use rglob({pattern!r})")

    monkeypatch.setattr(Path, "rglob", fail_rglob)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            history_root=str(contract_tmp_path / "governance" / "history"),
        )
    )
    service = SkillGovernanceService()

    uninstall_payload = service.manage(config=config, action="uninstall", skill_id="alpha")

    assert uninstall_payload["uninstalled"] is True
    assert uninstall_payload["backup_scan_truncated"] is True
    assert uninstall_payload["backup_scanned_path_count"] == 5
    assert uninstall_payload["backup_max_scanned_paths"] == 5


def test_skill_governance_audit_package_stops_at_scan_budget_without_extracting(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = contract_tmp_path / "packages" / "large.skill"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "alpha/SKILL.md",
            "---\n"
            "title: Alpha Skill\n"
            "summary: Alpha summary\n"
            "version: 1.0.0\n"
            "trust: trusted\n"
            "---\n\n"
            "# Alpha Skill\n\n"
            "Alpha body\n",
        )
        for index in range(16):
            archive.writestr(f"alpha/references/ref-{index:02}.md", "reference guide")

    monkeypatch.setattr("anvil.skills.governance.DEFAULT_GOVERNANCE_PACKAGE_SCAN_LIMIT", 5, raising=False)

    def fail_extract(*_args, **_kwargs):
        raise AssertionError("truncated package audits must not extract the archive")

    monkeypatch.setattr(SkillGovernanceService, "_extract_archive", fail_extract)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillGovernanceService()

    audit_payload = service.audit(config=config, source=str(archive_path))

    assert audit_payload["status"] == "failed"
    assert audit_payload["package_scan_truncated"] is True
    assert audit_payload["package_scanned_path_count"] == 5
    assert audit_payload["package_max_scanned_paths"] == 5
    assert any("scan truncated" in finding for finding in audit_payload["audit_findings"])


def test_skill_governance_blocks_path_traversal_archives(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.governance.default_installed_skill_root", lambda: workspace_root)

    archive_path = contract_tmp_path / "packages" / "bad.skill"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt", "escape")
        archive.writestr("alpha/SKILL.md", "# Bad Skill\n\nOops\n")

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillGovernanceService()

    with pytest.raises(ValueError, match="path traversal"):
        service.install(config=config, source=str(archive_path), enable=True)
