from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from anvil.config.loader import (
    default_anvil_config_dir,
    get_repo_root,
)

from .contracts import (
    SkillCollisionRecord,
    SkillDependency,
    SkillManifest,
    SkillReadiness,
    SkillValidationIssue,
    SkillValidationSeverity,
)

MAX_SKILL_SCAN_DEPTH = 6
MAX_SKILL_DIRS_PER_ROOT = 2000
DEFAULT_SKILL_MANIFEST_FILE_INDEX_SCAN_LIMIT = 5_000
MAX_SKILL_MANIFEST_FILE_INDEX_SCAN_LIMIT = 50_000
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SKILL_DESCRIPTION_MAX_CHARS = 1024
SKILL_XML_TAG_PATTERN = re.compile(r"<[A-Za-z][^>]*>")


def default_repo_skill_root() -> Path:
    return get_repo_root() / "skills"


def default_installed_skill_root() -> Path:
    return default_anvil_config_dir() / "skills"


def default_curator_state_root() -> Path:
    return default_installed_skill_root()


@dataclass(slots=True)
class SkillLoadResult:
    manifests: tuple[SkillManifest, ...]
    issues: tuple[SkillValidationIssue, ...]
    collisions: tuple[SkillCollisionRecord, ...]
    manifest_file_stamps: tuple[tuple[str, int, int, str], ...] = ()


@dataclass(slots=True)
class _SkillManifestFileIndexScan:
    asset_paths: tuple[str, ...] = ()
    template_paths: tuple[str, ...] = ()
    script_paths: tuple[str, ...] = ()
    reference_paths: tuple[str, ...] = ()
    scanned_path_count: int = 0
    max_scanned_paths: int = 0
    scan_truncated: bool = False


class SkillLoader:
    ALLOWED_FRONTMATTER_KEYS = {
        "name",
        "title",
        "summary",
        "description",
        "version",
        "trust",
        "allowed_tools",
        "tags",
        "domain",
        "task_type",
        "input_requirements",
        "risk_level",
        "dependencies",
        "dependency",
        "readiness",
        "config",
        "platforms",
        "related_skills",
        "author",
        "license",
        "metadata",
        "triggers",
        "argument-hint",
        "allowed-tools",
        "prerequisites",
        "required_credential_files",
        "required_credentials",
        "required_env",
        "required_env_vars",
    }

    def discover(
        self,
        roots: list[Path],
        *,
        include_file_index: bool = True,
        include_body_preview: bool = True,
    ) -> SkillLoadResult:
        manifests_by_id: dict[str, SkillManifest] = {}
        issues: list[SkillValidationIssue] = []
        collisions: list[SkillCollisionRecord] = []
        manifest_file_stamps: list[tuple[str, int, int, str]] = []

        for root in roots:
            resolved_root = _absolute_path(root)
            for skill_file in _iter_skill_manifest_files(root):
                resolved_skill_path = _absolute_path(skill_file)
                manifest = self._load_manifest(
                    root=root,
                    skill_file=skill_file,
                    resolved_root=resolved_root,
                    resolved_skill_path=resolved_skill_path,
                    include_file_index=include_file_index,
                    include_body_preview=include_body_preview,
                )
                manifest_file_stamps.append(
                    _manifest_file_stamp(
                        skill_file,
                        manifest.content_hash,
                        resolved_path=resolved_skill_path,
                    )
                )
                issues.extend(manifest.issues)
                previous = manifests_by_id.get(manifest.skill_id)
                if previous is not None:
                    winner, loser = _prefer_skill_manifest(previous, manifest, root=root)
                    collision = SkillCollisionRecord(
                        skill_id=manifest.skill_id,
                        winner_source_root=winner.source_root,
                        loser_source_root=loser.source_root,
                    )
                    collisions.append(collision)
                    previous_issue = SkillValidationIssue(
                        severity=SkillValidationSeverity.WARNING,
                        code="skill_collision_shadowed",
                        message=f"Skill '{manifest.skill_id}' was shadowed by a later root.",
                        skill_id=manifest.skill_id,
                        source_root=previous.source_root,
                        path=previous.path,
                    )
                    current_issue = SkillValidationIssue(
                        severity=SkillValidationSeverity.WARNING,
                        code="skill_collision_override",
                        message=f"Skill '{manifest.skill_id}' overrides an earlier root.",
                        skill_id=manifest.skill_id,
                        source_root=manifest.source_root,
                        path=manifest.path,
                    )
                    winning_issue = current_issue if winner is manifest else previous_issue
                    manifests_by_id[manifest.skill_id] = winner.model_copy(
                        update={"issues": (*winner.issues, winning_issue)}
                    )
                    manifests_by_id[previous.skill_id] = manifests_by_id[manifest.skill_id]
                    issues.extend([previous_issue, current_issue])
                    continue
                manifests_by_id[manifest.skill_id] = manifest

        manifests = tuple(manifests_by_id[skill_id] for skill_id in sorted(manifests_by_id))
        return SkillLoadResult(
            manifests=manifests,
            issues=tuple(issues),
            collisions=tuple(collisions),
            manifest_file_stamps=tuple(sorted(manifest_file_stamps)),
        )

    def _load_manifest(
        self,
        *,
        root: Path,
        skill_file: Path,
        resolved_root: str | None = None,
        resolved_skill_path: str | None = None,
        include_file_index: bool = True,
        include_body_preview: bool = True,
    ) -> SkillManifest:
        skill_id = skill_file.parent.name
        text = skill_file.read_text(encoding="utf-8")
        metadata, body, frontmatter_issues = _extract_frontmatter(text, skill_id=skill_id, source_root=root, path=skill_file)
        title, summary = _extract_title_and_summary(body, skill_id)
        if isinstance(metadata.get("title"), str):
            title = metadata["title"].strip() or title
        if isinstance(metadata.get("summary"), str):
            summary = metadata["summary"].strip() or summary
        elif isinstance(metadata.get("description"), str):
            summary = metadata["description"].strip() or summary

        issues = list(frontmatter_issues)
        issues.extend(
            _validate_skill_identity_metadata(
                metadata=metadata,
                skill_id=skill_id,
                source_root=root,
                path=skill_file,
            )
        )
        if "allowed_tools" not in metadata and "allowed-tools" in metadata:
            metadata["allowed_tools"] = metadata["allowed-tools"]
        if "dependencies" not in metadata and "dependency" in metadata:
            metadata["dependencies"] = metadata["dependency"]
        allowed_tools, allowed_issues = _normalize_string_list(
            metadata.get("allowed_tools"),
            field="allowed_tools",
            skill_id=skill_id,
            source_root=root,
            path=skill_file,
        )
        tags, tag_issues = _normalize_string_list(
            metadata.get("tags"),
            field="tags",
            skill_id=skill_id,
            source_root=root,
            path=skill_file,
        )
        metadata_tags, metadata_related = _metadata_skill_hints(metadata.get("metadata"))
        tags.extend(item for item in metadata_tags if item not in tags)
        routing_metadata, routing_issues = _normalize_routing_metadata(
            metadata,
            skill_id=skill_id,
            source_root=root,
            path=skill_file,
        )
        platforms, platform_issues = _normalize_string_list(
            metadata.get("platforms"),
            field="platforms",
            skill_id=skill_id,
            source_root=root,
            path=skill_file,
        )
        related_skills, related_issues = _normalize_string_list(
            metadata.get("related_skills"),
            field="related_skills",
            skill_id=skill_id,
            source_root=root,
            path=skill_file,
        )
        related_skills.extend(item for item in metadata_related if item not in related_skills)
        dependencies, dependency_issues = _normalize_dependencies(
            metadata.get("dependencies"),
            skill_id=skill_id,
            source_root=root,
            path=skill_file,
        )
        readiness, readiness_issues = _normalize_readiness(
            metadata.get("readiness"),
            skill_id=skill_id,
            source_root=root,
            path=skill_file,
        )
        config_value, config_issues = _normalize_mapping(
            metadata.get("config"),
            field="config",
            skill_id=skill_id,
            source_root=root,
            path=skill_file,
        )
        config_value.update(_external_metadata_config(metadata))
        issues.extend(
            [
                *allowed_issues,
                *tag_issues,
                *routing_issues,
                *platform_issues,
                *related_issues,
                *dependency_issues,
                *readiness_issues,
                *config_issues,
            ]
        )
        body_preview = _body_preview(body) if include_body_preview else ""
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        valid = not any(issue.severity is SkillValidationSeverity.ERROR for issue in issues)
        file_index = (
            _collect_manifest_file_index(skill_file.parent)
            if include_file_index
            else _SkillManifestFileIndexScan()
        )

        return SkillManifest(
            skill_id=skill_id,
            path=resolved_skill_path or str(skill_file.resolve()),
            source_root=resolved_root or str(root.resolve()),
            title=title,
            summary=summary,
            name=_string_or_none(metadata.get("name")) or skill_id,
            description=_string_or_none(metadata.get("description")) or summary,
            version=_string_or_none(metadata.get("version")) or "0.1.0",
            trust=_string_or_none(metadata.get("trust")) or "local",
            allowed_tools=tuple(allowed_tools),
            tags=tuple(tags),
            domain=routing_metadata["domain"],
            task_type=routing_metadata["task_type"],
            input_requirements=tuple(routing_metadata["input_requirements"]),
            risk_level=routing_metadata["risk_level"],
            dependencies=tuple(dependencies),
            readiness=readiness,
            config=config_value,
            platforms=tuple(platforms),
            related_skills=tuple(related_skills),
            asset_paths=file_index.asset_paths,
            template_paths=file_index.template_paths,
            script_paths=file_index.script_paths,
            reference_paths=file_index.reference_paths,
            file_index_scanned_path_count=file_index.scanned_path_count,
            file_index_max_scanned_paths=file_index.max_scanned_paths,
            file_index_scan_truncated=file_index.scan_truncated,
            body_preview=body_preview,
            valid=valid,
            issues=tuple(issues),
            content_hash=content_hash,
        )


def _absolute_path(path: Path) -> str:
    return os.path.abspath(os.fspath(path))


def _prefer_skill_manifest(previous: SkillManifest, current: SkillManifest, *, root: Path) -> tuple[SkillManifest, SkillManifest]:
    previous_depth = _skill_manifest_depth(previous.path, root=root)
    current_depth = _skill_manifest_depth(current.path, root=root)
    if previous.source_root == current.source_root and previous_depth != current_depth:
        if previous_depth < current_depth:
            return previous, current
        return current, previous
    return current, previous


def _skill_manifest_depth(path: str, *, root: Path) -> int:
    try:
        relative = Path(path).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return 10_000
    return len(relative.parts)


def _iter_skill_manifest_files(root: Path) -> tuple[Path, ...]:
    manifests: list[Path] = []
    queued: list[tuple[str, int]] = [(os.fspath(root), 0)]
    visited: set[str] = set()

    while queued:
        directory_path, depth = queued.pop(0)
        if depth > MAX_SKILL_SCAN_DEPTH:
            continue
        resolved_directory = os.path.normcase(os.path.abspath(directory_path))
        if resolved_directory in visited:
            continue
        if len(visited) >= MAX_SKILL_DIRS_PER_ROOT:
            break
        visited.add(resolved_directory)

        skill_file_path = os.path.join(directory_path, "SKILL.md")
        if os.path.isfile(skill_file_path):
            manifests.append(Path(skill_file_path))

        if depth >= MAX_SKILL_SCAN_DEPTH:
            continue
        try:
            with os.scandir(directory_path) as entries:
                children = sorted(entries, key=lambda item: item.name.casefold())
        except OSError:
            continue
        for child in children:
            if child.name.startswith("."):
                continue
            try:
                if not child.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            queued.append((child.path, depth + 1))
    return tuple(manifests)


def _extract_title_and_summary(text: str, fallback: str) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines()]
    title = fallback
    summary = ""
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    for line in lines:
        if not line or line.startswith("---") or line.startswith("#"):
            continue
        summary = line
        break
    return title, summary


def _body_preview(text: str, max_chars: int = 240) -> str:
    normalized = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _extract_frontmatter(
    text: str,
    *,
    skill_id: str,
    source_root: Path,
    path: Path,
) -> tuple[dict[str, Any], str, tuple[SkillValidationIssue, ...]]:
    stripped = text.lstrip()
    if not stripped.startswith("---\n"):
        return {}, text, ()

    _, _, remainder = stripped.partition("---\n")
    frontmatter_block, separator, body = remainder.partition("\n---\n")
    if not separator:
        return {}, text, (
            SkillValidationIssue(
                severity=SkillValidationSeverity.ERROR,
                code="frontmatter_unterminated",
                message="Frontmatter block is not terminated by '---'.",
                skill_id=skill_id,
                source_root=str(source_root.resolve()),
                path=str(path.resolve()),
            ),
        )
    parsed = _parse_simple_frontmatter(frontmatter_block)
    if parsed is None:
        try:
            parsed = yaml.safe_load(frontmatter_block) or {}
        except yaml.YAMLError as exc:
            parsed = _salvage_frontmatter(frontmatter_block)
            return parsed, body, (
                SkillValidationIssue(
                    severity=SkillValidationSeverity.WARNING,
                    code="frontmatter_invalid_yaml",
                    message=f"Frontmatter YAML is invalid; using compatible fallback parser: {exc}",
                    skill_id=skill_id,
                    source_root=str(source_root.resolve()),
                    path=str(path.resolve()),
                ),
            )
    if not isinstance(parsed, dict):
        return {}, body, (
            SkillValidationIssue(
                severity=SkillValidationSeverity.ERROR,
                code="frontmatter_not_mapping",
                message="Frontmatter must parse to a mapping object.",
                skill_id=skill_id,
                source_root=str(source_root.resolve()),
                path=str(path.resolve()),
            ),
        )

    issues: list[SkillValidationIssue] = []
    unknown_keys = sorted(key for key in parsed if key not in SkillLoader.ALLOWED_FRONTMATTER_KEYS)
    for key in unknown_keys:
        issues.append(
            SkillValidationIssue(
                severity=SkillValidationSeverity.WARNING,
                code="frontmatter_unknown_key",
                message=f"Unknown frontmatter key preserved as compatibility metadata: {key}",
                skill_id=skill_id,
                source_root=str(source_root.resolve()),
                path=str(path.resolve()),
                field=str(key),
            )
        )
    return {str(key): value for key, value in parsed.items()}, body, tuple(issues)


def _validate_skill_identity_metadata(
    *,
    metadata: dict[str, Any],
    skill_id: str,
    source_root: Path,
    path: Path,
) -> tuple[SkillValidationIssue, ...]:
    issues: list[SkillValidationIssue] = []
    explicit_name = _string_or_none(metadata.get("name"))
    if explicit_name is not None:
        if len(explicit_name) > 64 or SKILL_NAME_PATTERN.fullmatch(explicit_name) is None:
            issues.append(
                SkillValidationIssue(
                    severity=SkillValidationSeverity.WARNING,
                    code="skill_name_invalid",
                    message="Skill frontmatter name is not portable to Claude Agent Skills: use 1-64 chars of lowercase letters, numbers, and hyphens.",
                    skill_id=skill_id,
                    source_root=str(source_root.resolve()),
                    path=str(path.resolve()),
                    field="name",
                )
            )
        if "anthropic" in explicit_name or "claude" in explicit_name:
            issues.append(
                SkillValidationIssue(
                    severity=SkillValidationSeverity.WARNING,
                    code="skill_name_reserved_term",
                    message="Skill frontmatter name is not portable to Claude Agent Skills because it contains 'anthropic' or 'claude'.",
                    skill_id=skill_id,
                    source_root=str(source_root.resolve()),
                    path=str(path.resolve()),
                    field="name",
                )
            )

    explicit_description = _string_or_none(metadata.get("description"))
    if explicit_description is not None:
        if len(explicit_description) > SKILL_DESCRIPTION_MAX_CHARS:
            issues.append(
                SkillValidationIssue(
                    severity=SkillValidationSeverity.WARNING,
                    code="skill_description_too_long",
                    message=f"Skill frontmatter description is not portable to Claude Agent Skills because it exceeds {SKILL_DESCRIPTION_MAX_CHARS} characters.",
                    skill_id=skill_id,
                    source_root=str(source_root.resolve()),
                    path=str(path.resolve()),
                    field="description",
                )
            )
        if SKILL_XML_TAG_PATTERN.search(explicit_description):
            issues.append(
                SkillValidationIssue(
                    severity=SkillValidationSeverity.WARNING,
                    code="skill_description_xml_tag",
                    message="Skill frontmatter description is not portable to Claude Agent Skills because it contains XML tags.",
                    skill_id=skill_id,
                    source_root=str(source_root.resolve()),
                    path=str(path.resolve()),
                    field="description",
                )
            )
    return tuple(issues)


def _parse_simple_frontmatter(frontmatter_block: str) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    pending_key: str | None = None
    pending_items: list[str] | None = None

    def flush_pending() -> None:
        nonlocal pending_key, pending_items
        if pending_key is not None:
            metadata[pending_key] = list(pending_items or []) if pending_items else None
        pending_key = None
        pending_items = None

    for raw_line in frontmatter_block.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith((" ", "\t")):
            if pending_key is None or pending_items is None:
                return None
            stripped = raw_line.strip()
            if not stripped.startswith("- "):
                return None
            item = stripped[2:].strip()
            if ":" in item and not (item.startswith(("'", '"')) and item.endswith(("'", '"'))):
                return None
            pending_items.append(_parse_simple_scalar(item))
            continue

        flush_pending()
        key, separator, value = raw_line.partition(":")
        if not separator:
            return None
        key = key.strip()
        raw_value = value.strip()
        if not key:
            return None
        if raw_value == "":
            pending_key = key
            pending_items = []
            continue
        if raw_value[0] in "{|>&*!" or raw_value in {"---", "..."}:
            return None
        if raw_value.startswith("["):
            if not raw_value.endswith("]"):
                return None
            metadata[key] = [
                _parse_simple_scalar(item.strip())
                for item in raw_value[1:-1].split(",")
                if item.strip()
            ]
            continue
        metadata[key] = _parse_simple_scalar(raw_value)

    flush_pending()
    return metadata


def _parse_simple_scalar(value: str) -> str:
    return value.strip().strip("\"'")


def _salvage_frontmatter(frontmatter_block: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    current_key: str | None = None
    list_items: list[str] | None = None
    for raw_line in frontmatter_block.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith((" ", "\t")):
            if list_items is not None:
                stripped = raw_line.strip()
                if stripped.startswith("- "):
                    list_items.append(stripped[2:].strip().strip("\"'"))
            continue
        if list_items is not None and current_key is not None:
            metadata[current_key] = list_items
            list_items = None
        key, separator, value = raw_line.partition(":")
        if not separator:
            continue
        current_key = key.strip()
        raw_value = value.strip()
        if raw_value == "":
            list_items = []
            continue
        if raw_value.startswith("[") and raw_value.endswith("]"):
            metadata[current_key] = [item.strip().strip("\"'") for item in raw_value[1:-1].split(",") if item.strip()]
            continue
        metadata[current_key] = raw_value.strip("\"'")
    if list_items is not None and current_key is not None:
        metadata[current_key] = list_items
    return metadata


def _normalize_string_list(
    value: object,
    *,
    field: str,
    skill_id: str,
    source_root: Path,
    path: Path,
) -> tuple[list[str], tuple[SkillValidationIssue, ...]]:
    if value is None:
        return [], ()
    if not isinstance(value, list):
        return [], (
            SkillValidationIssue(
                severity=SkillValidationSeverity.ERROR,
                code="frontmatter_invalid_type",
                message=f"Field '{field}' must be a list of strings.",
                skill_id=skill_id,
                source_root=str(source_root.resolve()),
                path=str(path.resolve()),
                field=field,
            ),
        )
    items: list[str] = []
    issues: list[SkillValidationIssue] = []
    for item in value:
        normalized = str(item).strip()
        if not normalized:
            issues.append(
                SkillValidationIssue(
                    severity=SkillValidationSeverity.WARNING,
                    code="frontmatter_empty_list_item",
                    message=f"Field '{field}' contains an empty item.",
                    skill_id=skill_id,
                    source_root=str(source_root.resolve()),
                    path=str(path.resolve()),
                    field=field,
                )
            )
            continue
        items.append(normalized)
    return items, tuple(issues)


def _normalize_mapping(
    value: object,
    *,
    field: str,
    skill_id: str,
    source_root: Path,
    path: Path,
) -> tuple[dict[str, object], tuple[SkillValidationIssue, ...]]:
    if value is None:
        return {}, ()
    if not isinstance(value, dict):
        return {}, (
            SkillValidationIssue(
                severity=SkillValidationSeverity.ERROR,
                code="frontmatter_invalid_type",
                message=f"Field '{field}' must be a mapping.",
                skill_id=skill_id,
                source_root=str(source_root.resolve()),
                path=str(path.resolve()),
                field=field,
            ),
        )
    return {str(key): value for key, value in value.items()}, ()


def _normalize_dependencies(
    value: object,
    *,
    skill_id: str,
    source_root: Path,
    path: Path,
) -> tuple[list[SkillDependency], tuple[SkillValidationIssue, ...]]:
    if value is None:
        return [], ()
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, list):
        return [], (
            SkillValidationIssue(
                severity=SkillValidationSeverity.WARNING,
                code="frontmatter_invalid_type",
                message="Field 'dependencies' should be a list; ignored incompatible value.",
                skill_id=skill_id,
                source_root=str(source_root.resolve()),
                path=str(path.resolve()),
                field="dependencies",
            ),
        )
    items: list[SkillDependency] = []
    issues: list[SkillValidationIssue] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(SkillDependency(kind="generic", name=item.strip()))
            continue
        if not isinstance(item, dict):
            issues.append(
                SkillValidationIssue(
                    severity=SkillValidationSeverity.ERROR,
                    code="dependency_invalid_item",
                    message="Dependencies items must be strings or objects with name/kind.",
                    skill_id=skill_id,
                    source_root=str(source_root.resolve()),
                    path=str(path.resolve()),
                    field="dependencies",
                )
            )
            continue
        if "name" not in item and len(item) == 1:
            dep_name, dep_detail = next(iter(item.items()))
            details = dep_detail if isinstance(dep_detail, dict) else {"version": dep_detail}
            items.append(
                SkillDependency(
                    kind="generic",
                    name=str(dep_name),
                    details={str(key): val for key, val in details.items()},
                )
            )
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            issues.append(
                SkillValidationIssue(
                    severity=SkillValidationSeverity.ERROR,
                    code="dependency_missing_name",
                    message="Dependency objects require a non-empty 'name'.",
                    skill_id=skill_id,
                    source_root=str(source_root.resolve()),
                    path=str(path.resolve()),
                    field="dependencies",
                )
            )
            continue
        items.append(
            SkillDependency(
                kind=str(item.get("kind") or "generic"),
                name=name,
                required=bool(item.get("required", True)),
                details={str(key): val for key, val in item.items() if key not in {"kind", "name", "required"}},
            )
        )
    return items, tuple(issues)


def _normalize_readiness(
    value: object,
    *,
    skill_id: str,
    source_root: Path,
    path: Path,
) -> tuple[SkillReadiness, tuple[SkillValidationIssue, ...]]:
    if value is None:
        return SkillReadiness(), ()
    if not isinstance(value, dict):
        return SkillReadiness(), (
            SkillValidationIssue(
                severity=SkillValidationSeverity.ERROR,
                code="frontmatter_invalid_type",
                message="Field 'readiness' must be a mapping.",
                skill_id=skill_id,
                source_root=str(source_root.resolve()),
                path=str(path.resolve()),
                field="readiness",
            ),
        )
    requirements, requirement_issues = _normalize_string_list(
        value.get("requirements"),
        field="readiness.requirements",
        skill_id=skill_id,
        source_root=source_root,
        path=path,
    )
    notes, note_issues = _normalize_string_list(
        value.get("notes"),
        field="readiness.notes",
        skill_id=skill_id,
        source_root=source_root,
        path=path,
    )
    return SkillReadiness(
        status=str(value.get("status") or "ready"),
        requirements=tuple(requirements),
        notes=tuple(notes),
    ), tuple([*requirement_issues, *note_issues])


def _metadata_skill_hints(value: object) -> tuple[list[str], list[str]]:
    if not isinstance(value, dict):
        return [], []
    routing = value.get("anvil") or value.get("routing")
    if not isinstance(routing, dict):
        return [], []
    tags = _plain_string_items(routing.get("tags"))
    related = _plain_string_items(routing.get("related_skills"))
    return tags, related


def _normalize_routing_metadata(
    metadata: dict[str, Any],
    *,
    skill_id: str,
    source_root: Path,
    path: Path,
) -> tuple[dict[str, object], tuple[SkillValidationIssue, ...]]:
    nested = metadata.get("metadata")
    nested_routing = nested.get("routing") if isinstance(nested, dict) else None
    if not isinstance(nested_routing, dict):
        nested_routing = {}

    domain = _string_or_none(metadata.get("domain")) or _string_or_none(nested_routing.get("domain"))
    task_type = _string_or_none(metadata.get("task_type")) or _string_or_none(nested_routing.get("task_type"))
    risk_level = _string_or_none(metadata.get("risk_level")) or _string_or_none(nested_routing.get("risk_level"))

    requirements_source = (
        metadata.get("input_requirements")
        if "input_requirements" in metadata
        else nested_routing.get("input_requirements")
    )
    input_requirements, requirement_issues = _normalize_string_items(
        requirements_source,
        field="input_requirements",
        skill_id=skill_id,
        source_root=source_root,
        path=path,
    )
    return {
        "domain": domain,
        "task_type": task_type,
        "input_requirements": input_requirements,
        "risk_level": risk_level,
    }, requirement_issues


def _external_metadata_config(metadata: dict[str, Any]) -> dict[str, object]:
    external_keys = {
        "author",
        "license",
        "metadata",
        "triggers",
        "argument-hint",
        "allowed-tools",
        "dependency",
        "prerequisites",
        "required_credential_files",
        "required_credentials",
        "required_env",
        "required_env_vars",
    }
    payload = {key: metadata[key] for key in external_keys if key in metadata}
    return {"external_metadata": payload} if payload else {}


def _plain_string_items(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in _split_inline_items(value) if item]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_string_items(
    value: object,
    *,
    field: str,
    skill_id: str,
    source_root: Path,
    path: Path,
) -> tuple[list[str], tuple[SkillValidationIssue, ...]]:
    if value is None:
        return [], ()
    if isinstance(value, str):
        return [value.strip()] if value.strip() else [], ()
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items, ()
    return [], (
        SkillValidationIssue(
            severity=SkillValidationSeverity.ERROR,
            code="frontmatter_invalid_type",
            message=f"Field '{field}' must be a string or list of strings.",
            skill_id=skill_id,
            source_root=str(source_root.resolve()),
            path=str(path.resolve()),
            field=field,
        ),
    )


def _split_inline_items(value: str) -> list[str]:
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip("\"'") for item in re.split(r"[,;]", text) if item.strip()]


def _bounded_manifest_file_index_scan_limit() -> int:
    configured = DEFAULT_SKILL_MANIFEST_FILE_INDEX_SCAN_LIMIT
    if configured < 1:
        return 1
    return min(configured, MAX_SKILL_MANIFEST_FILE_INDEX_SCAN_LIMIT)


def _collect_manifest_file_index(skill_root: Path) -> _SkillManifestFileIndexScan:
    max_scanned_paths = _bounded_manifest_file_index_scan_limit()
    by_kind: dict[str, list[str]] = {
        "assets": [],
        "templates": [],
        "scripts": [],
        "references": [],
    }
    scanned_path_count = 0
    scan_truncated = False

    for kind in ("assets", "templates", "scripts", "references"):
        root = skill_root / kind
        if not root.exists():
            continue
        stack: list[tuple[str, str]] = [(kind, os.fspath(root))]
        while stack:
            relative_dir, absolute_dir = stack.pop()
            try:
                iterator = os.scandir(absolute_dir)
            except OSError:
                continue
            with iterator as entries:
                children = sorted(entries, key=lambda item: item.name.casefold())
            for entry in children:
                if scanned_path_count >= max_scanned_paths:
                    scan_truncated = True
                    stack.clear()
                    break
                scanned_path_count += 1
                relative_path = f"{relative_dir}/{entry.name}"
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((relative_path, entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        by_kind[kind].append(relative_path.replace("\\", "/"))
                except OSError:
                    continue
            if scan_truncated:
                break
        if scan_truncated:
            break

    return _SkillManifestFileIndexScan(
        asset_paths=tuple(sorted(dict.fromkeys(by_kind["assets"]))),
        template_paths=tuple(sorted(dict.fromkeys(by_kind["templates"]))),
        script_paths=tuple(sorted(dict.fromkeys(by_kind["scripts"]))),
        reference_paths=tuple(sorted(dict.fromkeys(by_kind["references"]))),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _manifest_file_stamp(
    skill_file: Path,
    content_hash: str | None,
    *,
    resolved_path: str | None = None,
) -> tuple[str, int, int, str]:
    resolved = resolved_path or str(skill_file.resolve())
    try:
        stat = skill_file.stat()
    except OSError:
        return (resolved, -1, -1, content_hash or "")
    return (resolved, stat.st_mtime_ns, stat.st_size, content_hash or "")


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
