from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from anvil.config import EffectiveConfig

from .contracts import (
    SkillGovernanceRecord,
    SkillManifest,
    SkillPackage,
    SkillValidationIssue,
    SkillValidationSeverity,
)
from .loader import (
    SkillLoader,
    default_installed_skill_root,
    default_repo_skill_root,
)

DEFAULT_GOVERNANCE_BACKUP_SCAN_LIMIT = 5_000
MAX_GOVERNANCE_BACKUP_SCAN_LIMIT = 50_000
DEFAULT_GOVERNANCE_PACKAGE_SCAN_LIMIT = 5_000
MAX_GOVERNANCE_PACKAGE_SCAN_LIMIT = 50_000
MAX_GOVERNANCE_PACKAGE_UNCOMPRESSED_BYTES = 25 * 1024 * 1024


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class _GovernanceArchiveResult:
    path: Path
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False

    def metadata(self, *, prefix: str = "") -> dict[str, object]:
        return {
            f"{prefix}path": str(self.path),
            f"{prefix}scanned_path_count": self.scanned_path_count,
            f"{prefix}max_scanned_paths": self.max_scanned_paths,
            f"{prefix}scan_truncated": self.scan_truncated,
        }


@dataclass(frozen=True, slots=True)
class _GovernanceTreeFileScan:
    files: tuple[tuple[str, str], ...]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False


@dataclass(frozen=True, slots=True)
class _GovernancePackageEntry:
    info: zipfile.ZipInfo
    filename: str


@dataclass(frozen=True, slots=True)
class _GovernancePackageScan:
    entries: tuple[_GovernancePackageEntry, ...]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False
    has_skill_file: bool = False
    skill_file_count: int = 0
    total_uncompressed_bytes: int = 0
    max_uncompressed_bytes: int = MAX_GOVERNANCE_PACKAGE_UNCOMPRESSED_BYTES

    def metadata(self, *, prefix: str = "package_") -> dict[str, object]:
        return {
            f"{prefix}scanned_path_count": self.scanned_path_count,
            f"{prefix}max_scanned_paths": self.max_scanned_paths,
            f"{prefix}scan_truncated": self.scan_truncated,
            f"{prefix}uncompressed_bytes": self.total_uncompressed_bytes,
            f"{prefix}max_uncompressed_bytes": self.max_uncompressed_bytes,
        }


class SkillGovernanceService:
    def __init__(self, *, loader: SkillLoader | None = None) -> None:
        self.loader = loader or SkillLoader()

    def state(self, config: EffectiveConfig) -> dict[str, list[str]]:
        path = self._state_path(config)
        if not path.exists():
            return {"enabled_ids": [], "disabled_ids": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"enabled_ids": [], "disabled_ids": []}
        return {
            "enabled_ids": [str(item) for item in payload.get("enabled_ids", []) if str(item).strip()],
            "disabled_ids": [str(item) for item in payload.get("disabled_ids", []) if str(item).strip()],
        }

    def record_history(self, config: EffectiveConfig, record: SkillGovernanceRecord) -> None:
        log_path = self._history_log_path(config)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def history(self, config: EffectiveConfig, skill_id: str | None = None) -> list[SkillGovernanceRecord]:
        path = self._history_log_path(config)
        if not path.exists():
            return []
        items: list[SkillGovernanceRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = SkillGovernanceRecord.model_validate(json.loads(line))
            except Exception:
                continue
            if skill_id and record.skill_id != skill_id:
                continue
            items.append(record)
        return items

    def set_enabled(self, config: EffectiveConfig, skill_id: str, enabled: bool) -> dict[str, object]:
        state = self.state(config)
        enabled_ids = {item for item in state["enabled_ids"] if item}
        disabled_ids = {item for item in state["disabled_ids"] if item}
        if enabled:
            enabled_ids.add(skill_id)
            disabled_ids.discard(skill_id)
        else:
            disabled_ids.add(skill_id)
            enabled_ids.discard(skill_id)
        payload = {
            "enabled_ids": sorted(enabled_ids),
            "disabled_ids": sorted(disabled_ids),
        }
        path = self._state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=skill_id,
                action="enable" if enabled else "disable",
                created_at=utc_now_iso(),
                detail=payload,
            ),
        )
        return payload

    def inspect(self, *, config: EffectiveConfig, source: str | None = None, skill_id: str | None = None) -> dict[str, object]:
        package = self.resolve_package(config=config, source=source, skill_id=skill_id)
        return package.model_dump(mode="json")

    def audit(self, *, config: EffectiveConfig, source: str | None = None, skill_id: str | None = None) -> dict[str, object]:
        package = self.resolve_package(config=config, source=source, skill_id=skill_id)
        return {
            "skill_id": package.manifest.skill_id,
            "status": "passed" if not package.audit_findings and package.manifest.valid else "failed",
            "audit_findings": list(package.audit_findings),
            "audit_warnings": list(package.audit_warnings),
            "checksum": package.checksum,
            "source": package.source,
            "valid": package.manifest.valid,
            "security_scan": package.security_scan,
            "package_scanned_path_count": package.package_scanned_path_count,
            "package_max_scanned_paths": package.package_max_scanned_paths,
            "package_scan_truncated": package.package_scan_truncated,
            "package_uncompressed_bytes": package.package_uncompressed_bytes,
            "package_max_uncompressed_bytes": package.package_max_uncompressed_bytes,
        }

    def install(
        self,
        *,
        config: EffectiveConfig,
        source: str,
        enable: bool | None = None,
    ) -> dict[str, object]:
        package = self.resolve_package(config=config, source=source)
        if not package.manifest.valid:
            raise ValueError(f"skill package '{package.manifest.skill_id}' failed manifest validation")
        if package.audit_findings:
            raise ValueError(f"skill package '{package.manifest.skill_id}' failed audit")
        installed_root = default_installed_skill_root()
        installed_root.mkdir(parents=True, exist_ok=True)
        target_dir = installed_root / package.manifest.skill_id
        backup: _GovernanceArchiveResult | None = None
        if target_dir.exists():
            backup = self._backup_installed_skill(config, package.manifest.skill_id, target_dir, "update-backup")
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        extracted_root = Path(package.installed_path or "")
        if not extracted_root.exists():
            raise ValueError("resolved package did not materialize an installable directory")
        shutil.copytree(extracted_root, target_dir, dirs_exist_ok=True)
        if enable is not False:
            self.set_enabled(config, package.manifest.skill_id, True)
        self.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=package.manifest.skill_id,
                action="install",
                created_at=utc_now_iso(),
                detail={
                    "source": package.source,
                    "installed_path": str(target_dir),
                    "checksum": package.checksum,
                    "security_scan": package.security_scan,
                    "package_scanned_path_count": package.package_scanned_path_count,
                    "package_max_scanned_paths": package.package_max_scanned_paths,
                    "package_scan_truncated": package.package_scan_truncated,
                    "package_uncompressed_bytes": package.package_uncompressed_bytes,
                    "package_max_uncompressed_bytes": package.package_max_uncompressed_bytes,
                    **_governance_archive_metadata(backup, prefix="backup_"),
                },
            ),
        )
        return {
            "installed": True,
            "skill_id": package.manifest.skill_id,
            "path": str(target_dir),
            "checksum": package.checksum,
            "audit_findings": list(package.audit_findings),
            "audit_warnings": list(package.audit_warnings),
            "security_scan": package.security_scan,
            "package_scanned_path_count": package.package_scanned_path_count,
            "package_max_scanned_paths": package.package_max_scanned_paths,
            "package_scan_truncated": package.package_scan_truncated,
            "package_uncompressed_bytes": package.package_uncompressed_bytes,
            "package_max_uncompressed_bytes": package.package_max_uncompressed_bytes,
            **_governance_archive_metadata(backup, prefix="backup_"),
        }

    def uninstall(self, *, config: EffectiveConfig, skill_id: str) -> dict[str, object]:
        target_dir = default_installed_skill_root() / skill_id
        if not target_dir.exists():
            raise ValueError(f"skill '{skill_id}' is not installed in workspace governance root")
        backup = self._backup_installed_skill(config, skill_id, target_dir, "uninstall")
        shutil.rmtree(target_dir)
        self.set_enabled(config, skill_id, False)
        self.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=skill_id,
                action="uninstall",
                created_at=utc_now_iso(),
                detail=_governance_archive_metadata(backup, prefix="backup_"),
            ),
        )
        return {"uninstalled": True, "skill_id": skill_id, **_governance_archive_metadata(backup, prefix="backup_")}

    def rollback(self, *, config: EffectiveConfig, skill_id: str, revision: str | None = None) -> dict[str, object]:
        backup_dir = self._history_root(config) / skill_id
        if not backup_dir.exists():
            raise ValueError(f"no history found for skill '{skill_id}'")
        candidates = sorted(backup_dir.glob("*.skill"))
        if not candidates:
            raise ValueError(f"no rollback package found for skill '{skill_id}'")
        if revision:
            selected = next((item for item in candidates if item.stem == revision or item.name == revision), None)
            if selected is None:
                raise ValueError(f"unknown revision '{revision}' for skill '{skill_id}'")
        else:
            selected = candidates[-1]
        result = self.install(config=config, source=str(selected), enable=True)
        self.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=skill_id,
                action="rollback",
                created_at=utc_now_iso(),
                detail={"revision": selected.name},
            ),
        )
        return result | {"rolled_back_from": selected.name}

    def publish(self, *, config: EffectiveConfig, skill_id: str, destination: str | None = None) -> dict[str, object]:
        skill_dir = self._find_installed_skill(config=config, skill_id=skill_id)
        if skill_dir is None:
            raise ValueError(f"unknown skill '{skill_id}'")
        destination_path = Path(destination).expanduser().resolve() if destination else (
            self._published_root(config) / f"{skill_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.skill"
        )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        archive_result = _write_governance_skill_archive(skill_dir, destination_path)
        checksum = hashlib.sha256(destination_path.read_bytes()).hexdigest()
        self.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=skill_id,
                action="publish",
                created_at=utc_now_iso(),
                detail={"destination": str(destination_path), "checksum": checksum}
                | _governance_archive_metadata(archive_result, prefix="package_"),
            ),
        )
        return {
            "published": True,
            "skill_id": skill_id,
            "destination": str(destination_path),
            "checksum": checksum,
            **_governance_archive_metadata(archive_result, prefix="package_"),
        }

    def manage(self, *, config: EffectiveConfig, action: str, skill_id: str | None = None, source: str | None = None, enable: bool | None = None, revision: str | None = None, destination: str | None = None) -> dict[str, object]:
        normalized = action.strip().lower()
        if normalized == "enable":
            if not skill_id:
                raise ValueError("enable requires skill_id")
            return self.set_enabled(config, skill_id, True)
        if normalized == "disable":
            if not skill_id:
                raise ValueError("disable requires skill_id")
            return self.set_enabled(config, skill_id, False)
        if normalized == "uninstall":
            if not skill_id:
                raise ValueError("uninstall requires skill_id")
            return self.uninstall(config=config, skill_id=skill_id)
        raise ValueError(f"unsupported skill management action: {action}")

    def resolve_package(self, *, config: EffectiveConfig, source: str | None = None, skill_id: str | None = None) -> SkillPackage:
        if source:
            local_path = self._materialize_source(config=config, source=source)
            return self._build_package_from_source(config=config, source_path=local_path, source=source)
        if skill_id:
            installed = self._find_installed_skill(config=config, skill_id=skill_id)
            if installed is None:
                raise ValueError(f"unknown skill '{skill_id}'")
            load_result = self.loader.discover([installed.parent])
            manifest = next((item for item in load_result.manifests if item.skill_id == skill_id), None)
            if manifest is None:
                raise ValueError(f"failed to resolve skill '{skill_id}'")
            return SkillPackage(
                manifest=manifest,
                installed_path=str(installed),
                source=str(installed),
                status="installed",
                audit_findings=tuple(issue.message for issue in manifest.issues if issue.severity.value == "error"),
                audit_warnings=tuple(issue.message for issue in manifest.issues if issue.severity.value == "warning"),
                security_scan={
                    "issues": len(manifest.issues),
                    "error_count": sum(1 for issue in manifest.issues if issue.severity.value == "error"),
                    "warning_count": sum(1 for issue in manifest.issues if issue.severity.value == "warning"),
                },
            )
        raise ValueError("either source or skill_id is required")

    def _build_package_from_source(self, *, config: EffectiveConfig, source_path: Path, source: str) -> SkillPackage:
        findings: list[str] = []
        checksum = hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path.is_file() else None
        extract_root: Path
        quarantine_path: Path | None = None
        package_scan: _GovernancePackageScan | None = None
        if source_path.is_file():
            package_scan = self._scan_archive(source_path)
            findings.extend(self._audit_archive(source_path, package_scan=package_scan))
            if package_scan.scan_truncated:
                manifest = _invalid_package_manifest(source_path)
                return SkillPackage(
                    manifest=manifest,
                    package_path=str(source_path),
                    checksum=checksum,
                    source=source,
                    status="failed",
                    audit_findings=tuple(findings),
                    security_scan={
                        "issues": len(findings),
                        "error_count": len(findings),
                        "warning_count": 0,
                        **package_scan.metadata(),
                    },
                    **package_scan.metadata(prefix="package_"),
                )
            if config.skills_config.quarantine_on_install:
                quarantine_path = self._quarantine_root(config) / source_path.name
                quarantine_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, quarantine_path)
            extract_root = self._extract_archive(source_path, package_scan=package_scan)
        else:
            extract_root = source_path
        load_result = self.loader.discover([extract_root.parent if (extract_root / "SKILL.md").exists() else extract_root])
        manifest: SkillManifest | None = None
        if (extract_root / "SKILL.md").exists():
            manifest = next((item for item in load_result.manifests if item.skill_id == extract_root.name), None)
        if manifest is None:
            manifest = next(iter(load_result.manifests), None)
        if manifest is None:
            raise ValueError("package does not contain a discoverable SKILL.md")
        error_messages = [issue.message for issue in manifest.issues if issue.severity.value == "error"]
        warning_messages = [issue.message for issue in manifest.issues if issue.severity.value == "warning"]
        findings.extend(error_messages)
        return SkillPackage(
            manifest=manifest,
            package_path=str(source_path) if source_path.is_file() else None,
            installed_path=str(extract_root),
            quarantine_path=str(quarantine_path) if quarantine_path is not None else None,
            checksum=checksum,
            source=source,
            status="quarantined" if quarantine_path is not None else "resolved",
            audit_findings=tuple(findings),
            audit_warnings=tuple(warning_messages),
            security_scan={
                "issues": len(load_result.issues),
                "collisions": len(load_result.collisions),
                "error_count": len(error_messages),
                "warning_count": len(warning_messages),
                **(package_scan.metadata() if package_scan is not None else {}),
            },
            **(package_scan.metadata(prefix="package_") if package_scan is not None else {}),
        )

    def _materialize_source(self, *, config: EffectiveConfig, source: str) -> Path:
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme in {"http", "https"}:
            if not config.skills_config.allow_remote_install:
                raise ValueError("remote skill installation is disabled")
            suffix = Path(parsed.path).suffix or ".skill"
            fd, temp_path = tempfile.mkstemp(suffix=suffix)
            Path(temp_path).unlink(missing_ok=True)
            urllib.request.urlretrieve(source, temp_path)
            return Path(temp_path)
        return Path(source).expanduser().resolve()

    def _extract_archive(self, archive_path: Path, package_scan: _GovernancePackageScan | None = None) -> Path:
        extract_root = Path(tempfile.mkdtemp(prefix="anvil-skill-"))
        with zipfile.ZipFile(archive_path) as archive:
            scan = package_scan or self._scan_archive(archive_path, archive=archive)
            if scan.scan_truncated:
                raise ValueError("skill package scan truncated before extraction")
            if scan.total_uncompressed_bytes > scan.max_uncompressed_bytes:
                raise ValueError("skill package exceeds maximum uncompressed size")
            skill_files: list[Path] = []
            for item in scan.entries:
                if self._is_symlink(item.info):
                    raise ValueError("skill package contains symlink entries")
                target = (extract_root / item.filename).resolve()
                if extract_root.resolve() not in target.parents and target != extract_root.resolve():
                    raise ValueError("skill package contains path traversal")
                archive.extract(item.info, extract_root)
                if item.filename.endswith("SKILL.md"):
                    skill_files.append(target)
        if len(skill_files) != 1:
            raise ValueError("skill package must contain exactly one SKILL.md")
        return skill_files[0].parent

    def _audit_archive(
        self,
        archive_path: Path,
        *,
        package_scan: _GovernancePackageScan | None = None,
    ) -> list[str]:
        findings: list[str] = []
        with zipfile.ZipFile(archive_path) as archive:
            scan = package_scan or self._scan_archive(archive_path, archive=archive)
            if scan.scan_truncated:
                findings.append(
                    "skill package scan truncated before full audit; narrow the package or raise governance budget"
                )
            if scan.total_uncompressed_bytes > scan.max_uncompressed_bytes:
                findings.append("skill package exceeds maximum uncompressed size")
            if not scan.has_skill_file:
                findings.append("missing SKILL.md")
            for item in scan.entries:
                if self._is_symlink(item.info):
                    findings.append(f"symlink entry blocked: {item.filename}")
                if ".." in Path(item.filename).parts:
                    findings.append(f"path traversal blocked: {item.filename}")
        return findings

    def _scan_archive(
        self,
        archive_path: Path,
        *,
        archive: zipfile.ZipFile | None = None,
    ) -> _GovernancePackageScan:
        close_archive = archive is None
        handle = archive or zipfile.ZipFile(archive_path)
        try:
            return _scan_governance_package_entries(handle)
        finally:
            if close_archive:
                handle.close()

    def _is_symlink(self, info: zipfile.ZipInfo) -> bool:
        mode = (info.external_attr >> 16) & 0o170000
        return mode == 0o120000

    def _backup_installed_skill(
        self,
        config: EffectiveConfig,
        skill_id: str,
        target_dir: Path,
        action: str,
    ) -> _GovernanceArchiveResult:
        history_dir = self._history_root(config) / skill_id
        history_dir.mkdir(parents=True, exist_ok=True)
        backup_path = history_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{action}.skill"
        return _write_governance_skill_archive(target_dir, backup_path)

    def _find_installed_skill(self, *, config: EffectiveConfig, skill_id: str) -> Path | None:
        roots = [
            default_installed_skill_root(),
            default_repo_skill_root(),
        ]
        for root in roots:
            candidate = root / skill_id
            if (candidate / "SKILL.md").exists():
                return candidate
        return None

    def _state_path(self, config: EffectiveConfig) -> Path:
        return self._governance_root(config) / "state.json"

    def _history_log_path(self, config: EffectiveConfig) -> Path:
        return self._history_root(config) / "governance-log.jsonl"

    def _governance_root(self, config: EffectiveConfig) -> Path:
        root = config.skills_config.governance_root
        if root:
            return Path(root).expanduser().resolve()
        return default_installed_skill_root() / ".governance"

    def _quarantine_root(self, config: EffectiveConfig) -> Path:
        root = config.skills_config.quarantine_root
        if root:
            return Path(root).expanduser().resolve()
        return self._governance_root(config) / "quarantine"

    def _history_root(self, config: EffectiveConfig) -> Path:
        root = config.skills_config.history_root
        if root:
            return Path(root).expanduser().resolve()
        return self._governance_root(config) / "history"

    def _published_root(self, config: EffectiveConfig) -> Path:
        return self._governance_root(config) / "published"


def _bounded_governance_backup_scan_limit() -> int:
    configured = DEFAULT_GOVERNANCE_BACKUP_SCAN_LIMIT
    if configured < 1:
        return 1
    return min(configured, MAX_GOVERNANCE_BACKUP_SCAN_LIMIT)


def _bounded_governance_package_scan_limit() -> int:
    configured = DEFAULT_GOVERNANCE_PACKAGE_SCAN_LIMIT
    if configured < 1:
        return 1
    return min(configured, MAX_GOVERNANCE_PACKAGE_SCAN_LIMIT)


def _scan_governance_tree_files(root: Path) -> _GovernanceTreeFileScan:
    max_scanned_paths = _bounded_governance_backup_scan_limit()
    files: list[tuple[str, str]] = []
    stack: list[tuple[str, str]] = [("", os.fspath(root))]
    scanned_path_count = 0
    scan_truncated = False
    while stack:
        relative_dir, absolute_dir = stack.pop()
        try:
            iterator = os.scandir(absolute_dir)
        except OSError:
            continue
        with iterator as entries:
            for entry in entries:
                if scanned_path_count >= max_scanned_paths:
                    scan_truncated = True
                    stack.clear()
                    break
                scanned_path_count += 1
                relative_path = f"{relative_dir}/{entry.name}" if relative_dir else entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((relative_path, entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        files.append((relative_path.replace("\\", "/"), entry.path))
                except OSError:
                    files.append((relative_path.replace("\\", "/"), entry.path))
        if scan_truncated:
            break
    return _GovernanceTreeFileScan(
        files=tuple(sorted(files, key=lambda item: item[0])),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _write_governance_skill_archive(skill_dir: Path, destination_path: Path) -> _GovernanceArchiveResult:
    tree_scan = _scan_governance_tree_files(skill_dir)
    with zipfile.ZipFile(destination_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for _relative, path_text in tree_scan.files:
            path = Path(path_text)
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(skill_dir.parent)).replace("\\", "/"))
    return _GovernanceArchiveResult(
        path=destination_path,
        scanned_path_count=tree_scan.scanned_path_count,
        max_scanned_paths=tree_scan.max_scanned_paths,
        scan_truncated=tree_scan.scan_truncated,
    )


def _scan_governance_package_entries(archive: zipfile.ZipFile) -> _GovernancePackageScan:
    max_scanned_paths = _bounded_governance_package_scan_limit()
    entries: list[_GovernancePackageEntry] = []
    scanned_path_count = 0
    scan_truncated = False
    has_skill_file = False
    skill_file_count = 0
    total_uncompressed_bytes = 0
    for info in archive.filelist:
        if scanned_path_count >= max_scanned_paths:
            scan_truncated = True
            break
        scanned_path_count += 1
        filename = str(info.filename).replace("\\", "/")
        if filename.endswith("SKILL.md"):
            has_skill_file = True
            skill_file_count += 1
        total_uncompressed_bytes += int(info.file_size)
        entries.append(_GovernancePackageEntry(info=info, filename=filename))
        if total_uncompressed_bytes > MAX_GOVERNANCE_PACKAGE_UNCOMPRESSED_BYTES:
            break
    return _GovernancePackageScan(
        entries=tuple(entries),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
        has_skill_file=has_skill_file,
        skill_file_count=skill_file_count,
        total_uncompressed_bytes=total_uncompressed_bytes,
        max_uncompressed_bytes=MAX_GOVERNANCE_PACKAGE_UNCOMPRESSED_BYTES,
    )


def _invalid_package_manifest(source_path: Path) -> SkillManifest:
    skill_id = source_path.stem or "invalid-package"
    return SkillManifest(
        skill_id=skill_id,
        path=str(source_path),
        source_root=str(source_path.parent),
        title=skill_id,
        summary="Skill package did not pass governance audit.",
        valid=False,
        issues=(
            SkillValidationIssue(
                severity=SkillValidationSeverity.ERROR,
                code="skill_package_audit_failed",
                message="Skill package did not pass governance audit.",
                skill_id=skill_id,
                source_root=str(source_path.parent),
                path=str(source_path),
            ),
        ),
    )


def _governance_archive_metadata(
    archive: _GovernanceArchiveResult | None,
    *,
    prefix: str,
) -> dict[str, object]:
    if archive is None:
        return {
            f"{prefix}path": None,
            f"{prefix}scanned_path_count": 0,
            f"{prefix}max_scanned_paths": 0,
            f"{prefix}scan_truncated": False,
        }
    return archive.metadata(prefix=prefix)
