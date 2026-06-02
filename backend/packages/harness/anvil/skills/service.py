from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import RLock
from pathlib import Path
from time import monotonic

from anvil.config import EffectiveConfig

from .cache import SkillsCache
from .contracts import (
    SkillContentView,
    SkillDiscoveryDiagnostics,
    SkillDiscoveryResult,
    SkillFileEntry,
    SkillFileIndexView,
    SkillFileReadView,
    SkillManifest,
    SkillPackage,
    SkillSummary,
)
from .curator import SkillCuratorService
from .governance import SkillGovernanceService
from .loader import (
    SkillLoader,
    default_installed_skill_root,
    default_repo_skill_root,
)

_SKILL_ALLOWED_SUBDIRS = {
    "SKILL.md": "manifest",
    "assets": "asset",
    "templates": "template",
    "scripts": "script",
    "references": "reference",
}
DEFAULT_SKILL_SUPPORT_FILE_SCAN_LIMIT = 2_000
MAX_SKILL_SUPPORT_FILE_SCAN_LIMIT = 20_000
DEFAULT_SKILL_TREE_SCAN_LIMIT = 5_000
MAX_SKILL_TREE_SCAN_LIMIT = 50_000
DEFAULT_SKILL_TREE_HASH_FILE_BYTE_LIMIT = 1_000_000
MAX_SKILL_TREE_HASH_FILE_BYTE_LIMIT = 16_000_000
_BUNDLED_SYNC_CACHE_TTL_SECONDS = 5.0
_BUNDLED_SYNC_CACHE: dict[tuple[str, str], "_BundledSyncCacheState"] = {}
_BUNDLED_SYNC_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class _SkillWatchCacheState:
    root_stamps: tuple[tuple[str, int, int], ...]
    manifest_file_stamps: tuple[tuple[str, int, int], ...]
    cache_key: str


@dataclass(frozen=True, slots=True)
class _BundledSyncCacheState:
    root_stamp: str
    installed_stamp: str
    checked_at: float
    skill_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _SkillSupportFileScan:
    paths: tuple[str, ...]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False


@dataclass(frozen=True, slots=True)
class _SkillTreeFileScan:
    files: tuple[tuple[str, str], ...]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False


@dataclass
class _SkillDiscoveryTimer:
    started_at: float = field(default_factory=monotonic)
    last_at: float = field(default_factory=monotonic)
    durations_ms: dict[str, int] = field(default_factory=dict)

    def mark(self, stage: str) -> None:
        now = monotonic()
        self.durations_ms[stage] = max(int((now - self.last_at) * 1000), 0)
        self.last_at = now

    def total_ms(self) -> int:
        return max(int((monotonic() - self.started_at) * 1000), 0)


class SkillsService:
    def __init__(
        self,
        *,
        loader: SkillLoader | None = None,
        cache: SkillsCache | None = None,
        governance: SkillGovernanceService | None = None,
        curator: SkillCuratorService | None = None,
    ) -> None:
        self.loader = loader or SkillLoader()
        self.cache = cache or SkillsCache()
        self.governance = governance or SkillGovernanceService(loader=self.loader)
        self.curator = curator or SkillCuratorService(loader=self.loader, governance=self.governance)
        self._watch_signature_cache: dict[tuple[str, tuple[str, ...]], _SkillWatchCacheState] = {}

    def discover(self, *, config: EffectiveConfig, fingerprint: str) -> SkillDiscoveryResult:
        timer = _SkillDiscoveryTimer()
        roots = self.resolve_roots(config)
        timer.mark("resolve_roots")
        cache_key = self._cache_key(config=config, fingerprint=fingerprint, roots=roots)
        timer.mark("watch_cache_lookup")
        if cache_key is not None:
            cached = self.cache.get_shared(cache_key)
            timer.mark("cache_read")
            if cached is not None:
                return cached.model_copy(
                    update={
                        "discovery_diagnostics": _skill_discovery_diagnostics(
                            cache_hit=True,
                            watch_enabled=config.skills_config.watch_enabled,
                            root_count=len(roots),
                            manifest_count=len(cached.all_manifests),
                            enabled_count=len(cached.enabled_ids),
                            package_count=len(cached.packages),
                            stage_durations_ms=timer.durations_ms,
                            total_ms=timer.total_ms(),
                        )
                    }
                )
        else:
            timer.mark("cache_read")

        load_result = self.loader.discover(
            roots,
            include_file_index=False,
            include_body_preview=False,
        )
        timer.mark("loader_discover")
        if cache_key is None:
            cache_key = self._cache_key_from_load_result(
                config=config,
                fingerprint=fingerprint,
                roots=roots,
                manifest_file_stamps=load_result.manifest_file_stamps,
            )
        timer.mark("cache_key_from_load_result")
        manifests = load_result.manifests
        usage = self.curator.usage_snapshot(config=config)
        timer.mark("curator_usage")
        ops_enabled_manifests = self._filter_enabled(config, manifests)
        timer.mark("filter_enabled")
        ops_enabled_ids = {manifest.skill_id for manifest in ops_enabled_manifests}
        enabled_manifests = self._rank_manifests_by_usage(ops_enabled_manifests, usage)
        timer.mark("rank_manifests")
        installed_root = str(default_installed_skill_root().resolve())
        packages = []
        for manifest in manifests:
            if manifest.source_root != installed_root:
                continue
            packages.append(_package_from_discovered_manifest(manifest))
        timer.mark("packages")
        diagnostics = _skill_discovery_diagnostics(
            cache_hit=False,
            watch_enabled=config.skills_config.watch_enabled,
            root_count=len(roots),
            manifest_count=len(manifests),
            enabled_count=len(enabled_manifests),
            package_count=len(packages),
            stage_durations_ms=timer.durations_ms,
            total_ms=timer.total_ms(),
        )
        result = SkillDiscoveryResult(
            all_manifests=manifests,
            all_summaries=tuple(
                manifest.model_copy(update={"enabled": manifest.skill_id in ops_enabled_ids}).to_summary()
                for manifest in manifests
            ),
            enabled_manifests=enabled_manifests,
            enabled_summaries=tuple(
                self._summary_with_usage(manifest, usage, rank=index)
                for index, manifest in enumerate(enabled_manifests)
            ),
            enabled_ids=tuple(manifest.skill_id for manifest in enabled_manifests),
            packages=tuple(packages),
            issues=load_result.issues,
            collisions=load_result.collisions,
            discovery_diagnostics=diagnostics,
        )
        self.cache.put(cache_key, result)
        return result

    def _cache_key(self, *, config: EffectiveConfig, fingerprint: str, roots: list[Path]) -> str | None:
        if not config.skills_config.watch_enabled:
            return fingerprint
        root_paths = tuple(str(root.resolve()) for root in roots)
        root_stamps = tuple(self._root_stamp(root) for root in roots)
        cache_identity = (fingerprint, root_paths)
        cached_signature = self._watch_signature_cache.get(cache_identity)
        if cached_signature is None or cached_signature.root_stamps != root_stamps:
            return None
        if self._manifest_file_stamps_unchanged(cached_signature.manifest_file_stamps):
            return cached_signature.cache_key
        return None

    def _cache_key_from_load_result(
        self,
        *,
        config: EffectiveConfig,
        fingerprint: str,
        roots: list[Path],
        manifest_file_stamps: tuple[tuple[str, int, int, str], ...],
    ) -> str:
        if not config.skills_config.watch_enabled:
            return fingerprint
        root_paths = tuple(str(root.resolve()) for root in roots)
        root_stamps = tuple(self._root_stamp(root) for root in roots)
        digest = hashlib.sha256()
        digest.update(fingerprint.encode("utf-8", errors="replace"))
        for root in roots:
            resolved_root = root.resolve()
            digest.update(str(resolved_root).encode("utf-8", errors="replace"))
            if not resolved_root.exists():
                digest.update(b"\0missing")
        for path, mtime_ns, size, content_hash in manifest_file_stamps:
            digest.update(path.encode("utf-8", errors="replace"))
            digest.update(str(mtime_ns).encode("ascii"))
            digest.update(str(size).encode("ascii"))
            digest.update((content_hash or "").encode("ascii", errors="ignore"))
        cache_key = f"{fingerprint}:skills:{digest.hexdigest()}"
        self._watch_signature_cache[(fingerprint, root_paths)] = _SkillWatchCacheState(
            root_stamps=root_stamps,
            manifest_file_stamps=tuple(
                (path, mtime_ns, size) for path, mtime_ns, size, _content_hash in manifest_file_stamps
            ),
            cache_key=cache_key,
        )
        return cache_key

    def _root_stamp(self, root: Path) -> tuple[str, int, int]:
        resolved_root = root.resolve()
        try:
            stat = resolved_root.stat()
        except OSError:
            return (str(resolved_root), -1, -1)
        return (str(resolved_root), stat.st_mtime_ns, stat.st_size)

    def _manifest_file_stamps_unchanged(self, stamps: tuple[tuple[str, int, int], ...]) -> bool:
        for path, expected_mtime_ns, expected_size in stamps:
            try:
                stat = Path(path).stat()
            except OSError:
                return False
            if stat.st_mtime_ns != expected_mtime_ns or stat.st_size != expected_size:
                return False
        return True

    def _invalidate_caches(self) -> None:
        self.cache.invalidate()
        self._watch_signature_cache.clear()

    def _rank_manifests_by_usage(
        self,
        manifests: tuple[SkillManifest, ...],
        usage: dict[str, dict[str, object]],
    ) -> tuple[SkillManifest, ...]:
        return tuple(
            sorted(
                manifests,
                key=lambda manifest: (
                    self._tier_rank(usage.get(manifest.skill_id, {})),
                    -int(usage.get(manifest.skill_id, {}).get("utility_score") or 0),
                    manifest.skill_id,
                ),
            )
        )

    def _summary_with_usage(
        self,
        manifest: SkillManifest,
        usage: dict[str, dict[str, object]],
        rank: int | None = None,
    ) -> SkillSummary:
        summary = manifest.to_summary()
        item = usage.get(manifest.skill_id, {})
        curator = self._curator_summary(item, rank=rank)
        prefixes: list[str] = []
        tier = str(item.get("tier") or "active")
        if tier == "core":
            prefixes.append("[core]")
        elif tier == "observe":
            prefixes.append("[observe]")
        if item.get("template_path"):
            prefixes.append("[template]")
        if not prefixes:
            return summary.model_copy(update={"curator": curator})
        return summary.model_copy(update={"summary": f"{' '.join(prefixes)} {summary.summary}".strip(), "curator": curator})

    def _curator_summary(self, item: dict[str, object], *, rank: int | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "tier": str(item.get("tier") or "active"),
            "utility_score": int(item.get("utility_score") or 0),
            "context_count": int(item.get("context_count") or 0),
        }
        template_path = str(item.get("template_path") or "").strip()
        if template_path:
            payload["template_path"] = template_path
        if rank is not None:
            payload["rank"] = rank
        return payload

    def _tier_rank(self, item: dict[str, object]) -> int:
        tier = str(item.get("tier") or "active")
        if tier == "core":
            return 0
        if tier == "observe":
            return 2
        return 1

    def resolve_roots(self, config: EffectiveConfig) -> list[Path]:
        installed_root = default_installed_skill_root()
        repo_skill_root = default_repo_skill_root()
        sync_bundled_skills_to_home(installed_root, repo_skill_root)
        ordered_candidates = [
            installed_root,
            *(Path(root).expanduser().resolve() for root in config.skills_config.external_dirs),
            *(
                Path(root).expanduser().resolve()
                for plugin in config.extensions.plugins.values()
                if plugin.enabled
                for root in plugin.skill_roots
            ),
        ]
        roots: list[Path] = []
        seen: set[Path] = set()
        for root in ordered_candidates:
            resolved = root.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            roots.append(resolved)
        return roots

    def _filter_enabled(
        self,
        config: EffectiveConfig,
        manifests: tuple[SkillManifest, ...],
    ) -> tuple[SkillManifest, ...]:
        disabled = set(config.skills_config.disabled_ids)
        governance_state = self.governance.state(config)
        disabled.update(governance_state["disabled_ids"])
        return tuple(
            manifest.model_copy(update={"enabled": True})
            for manifest in manifests
            if manifest.skill_id not in disabled and manifest.valid
        )

    def get_skill(self, *, config: EffectiveConfig, fingerprint: str, skill_id: str) -> SkillManifest | None:
        skill_id = normalize_skill_id(skill_id)
        result = self.discover(config=config, fingerprint=fingerprint)
        return next((manifest for manifest in result.all_manifests if manifest.skill_id == skill_id), None)

    def get_skill_summary(self, *, config: EffectiveConfig, fingerprint: str, skill_id: str) -> SkillSummary | None:
        manifest = self.get_skill(config=config, fingerprint=fingerprint, skill_id=skill_id)
        return manifest.to_summary() if manifest is not None else None

    def get_enabled_skill(self, *, config: EffectiveConfig, fingerprint: str, skill_id: str) -> SkillManifest | None:
        skill_id = normalize_skill_id(skill_id)
        result = self.discover(config=config, fingerprint=fingerprint)
        return next((manifest for manifest in result.enabled_manifests if manifest.skill_id == skill_id), None)

    def get_skill_content(self, *, config: EffectiveConfig, fingerprint: str, skill_id: str) -> SkillContentView:
        skill_id = normalize_skill_id(skill_id)
        manifest = self.get_enabled_skill(config=config, fingerprint=fingerprint, skill_id=skill_id)
        if manifest is None:
            raise ValueError(f"unknown skill '{skill_id}'")
        self.curator.record_view(config=config, manifest=manifest)
        skill_path = Path(manifest.path)
        body = skill_path.read_text(encoding="utf-8")
        files = self.list_skill_files(config=config, fingerprint=fingerprint, skill_id=skill_id)
        return SkillContentView(
            skill_id=manifest.skill_id,
            title=manifest.title,
            path=manifest.path,
            source_root=manifest.source_root,
            body=body,
            body_preview=manifest.body_preview or _body_preview_from_text(body),
            file_count=len(files.files),
        )

    def list_skill_files(self, *, config: EffectiveConfig, fingerprint: str, skill_id: str) -> SkillFileIndexView:
        skill_id = normalize_skill_id(skill_id)
        manifest = self.get_enabled_skill(config=config, fingerprint=fingerprint, skill_id=skill_id)
        if manifest is None:
            raise ValueError(f"unknown skill '{skill_id}'")
        skill_root = Path(manifest.path).parent
        files: list[SkillFileEntry] = [
            SkillFileEntry(
                path="SKILL.md",
                kind="manifest",
                size_bytes=skill_root.joinpath("SKILL.md").stat().st_size,
                is_binary=False,
            )
        ]
        support_file_scan = _list_supporting_skill_files(skill_root)
        for relative_path in support_file_scan.paths:
            file_path = skill_root / relative_path
            if not file_path.exists() or not file_path.is_file():
                continue
            files.append(
                SkillFileEntry(
                    path=relative_path,
                    kind=_file_kind(relative_path),
                    size_bytes=file_path.stat().st_size,
                    is_binary=_is_binary_path(file_path),
                )
            )
        return SkillFileIndexView(
            skill_id=manifest.skill_id,
            path=manifest.path,
            source_root=manifest.source_root,
            files=tuple(files),
            scanned_path_count=support_file_scan.scanned_path_count,
            max_scanned_paths=support_file_scan.max_scanned_paths,
            scan_truncated=support_file_scan.scan_truncated,
        )

    def read_skill_file(
        self,
        *,
        config: EffectiveConfig,
        fingerprint: str,
        skill_id: str,
        relative_path: str,
        max_bytes: int = 64_000,
    ) -> SkillFileReadView:
        skill_id = normalize_skill_id(skill_id)
        manifest = self.get_enabled_skill(config=config, fingerprint=fingerprint, skill_id=skill_id)
        if manifest is None:
            raise ValueError(f"unknown skill '{skill_id}'")
        if not relative_path or relative_path.strip() in {".", "/"}:
            raise ValueError("relative_path is required")
        normalized = relative_path.replace("\\", "/").lstrip("/")
        skill_root = Path(manifest.path).parent.resolve()
        target = (skill_root / normalized).resolve()
        if skill_root not in target.parents and target != skill_root:
            raise ValueError("relative_path escapes the skill root")
        kind = _validated_file_kind(normalized)
        if kind is None:
            raise ValueError(f"unsupported skill file path '{relative_path}'")
        if not target.exists() or not target.is_file():
            raise ValueError(f"skill file '{relative_path}' was not found")
        content_bytes = target.read_bytes()
        truncated = len(content_bytes) > max_bytes
        effective_bytes = content_bytes[:max_bytes]
        if _is_binary_payload(effective_bytes, target):
            return SkillFileReadView(
                skill_id=skill_id,
                relative_path=normalized,
                path=str(target),
                source_root=manifest.source_root,
                kind=kind,
                is_binary=True,
                encoding="base64",
                content=base64.b64encode(effective_bytes).decode("ascii"),
                truncated=truncated,
                size_bytes=len(content_bytes),
            )
        return SkillFileReadView(
            skill_id=skill_id,
            relative_path=normalized,
            path=str(target),
            source_root=manifest.source_root,
            kind=kind,
            is_binary=False,
            encoding="utf-8",
            content=effective_bytes.decode("utf-8", errors="replace"),
            truncated=truncated,
            size_bytes=len(content_bytes),
        )

    def allowed_tool_names(
        self,
        *,
        config: EffectiveConfig,
        fingerprint: str,
        include_core: set[str] | None = None,
        discovery_result: SkillDiscoveryResult | None = None,
    ) -> set[str] | None:
        result = discovery_result or self.discover(config=config, fingerprint=fingerprint)
        allowlists = [set(manifest.allowed_tools) for manifest in result.enabled_manifests if manifest.allowed_tools]
        if not allowlists:
            return None
        allowed = set().union(*allowlists)
        if include_core:
            allowed.update(include_core)
        return allowed

    def mentioned_skill_content_summaries(
        self,
        *,
        config: EffectiveConfig,
        fingerprint: str,
        skill_ids: tuple[str, ...],
        discovery_result: SkillDiscoveryResult | None = None,
    ) -> tuple[str, ...]:
        summaries: list[str] = []
        result = discovery_result or self.discover(config=config, fingerprint=fingerprint)
        enabled_by_id = {manifest.skill_id: manifest for manifest in result.enabled_manifests}
        for skill_id in skill_ids:
            skill_id = normalize_skill_id(skill_id)
            manifest = enabled_by_id.get(skill_id)
            if manifest is None:
                continue
            self.curator.record_use(config=config, manifest=manifest, fingerprint=fingerprint)
            supporting_files = ", ".join(_manifest_supporting_paths(manifest)[:6])
            preview = manifest.body_preview or _body_preview_from_text(Path(manifest.path).read_text(encoding="utf-8"))
            payload = f"${skill_id} content: {preview or manifest.summary}"
            if supporting_files:
                payload += f" | files: {supporting_files}"
            summaries.append(payload)
        return tuple(summaries)

    def manage(
        self,
        *,
        config: EffectiveConfig,
        action: str,
        skill_id: str | None = None,
        source: str | None = None,
        enable: bool | None = None,
        revision: str | None = None,
        destination: str | None = None,
    ) -> dict[str, object]:
        skill_id = normalize_skill_id(skill_id) if skill_id is not None else None
        self._invalidate_caches()
        return self.governance.manage(
            config=config,
            action=action,
            skill_id=skill_id,
            source=source,
            enable=enable,
            revision=revision,
            destination=destination,
        )

    def manage_curator(
        self,
        *,
        config: EffectiveConfig,
        action: str,
        skill_id: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        body: str | None = None,
        rationale: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        allowed_tools: list[str] | tuple[str, ...] | None = None,
        file_path: str | None = None,
        content: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        absorbed_into: str | None = None,
        revision: str | None = None,
        outcome: str | None = None,
        feedback_source: str | None = None,
        confidence: float | int | None = None,
        trigger: str | None = None,
        steps: list[str] | tuple[str, ...] | None = None,
        expected_outcome: str | None = None,
        evidence_refs: list[str] | tuple[str, ...] | None = None,
        source_ref: str | None = None,
        procedure_id: str | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        skill_id = normalize_skill_id(skill_id) if skill_id is not None else None
        self._invalidate_caches()
        return self.curator.manage(
            config=config,
            action=action,
            skill_id=skill_id,
            title=title,
            summary=summary,
            body=body,
            rationale=rationale,
            tags=tags,
            allowed_tools=allowed_tools,
            file_path=file_path,
            content=content,
            old_text=old_text,
            new_text=new_text,
            absorbed_into=absorbed_into,
            revision=revision,
            outcome=outcome,
            feedback_source=feedback_source,
            confidence=confidence,
            trigger=trigger,
            steps=steps,
            expected_outcome=expected_outcome,
            evidence_refs=evidence_refs,
            source_ref=source_ref,
            procedure_id=procedure_id,
            dry_run=dry_run,
            force=force,
        )

    def curator_automation_status(self, *, config: EffectiveConfig) -> dict[str, object]:
        return self.curator.automation_status(config=config)

    def run_curator_automation_if_due(
        self,
        *,
        config: EffectiveConfig,
        force_run: bool = False,
    ):
        self._invalidate_caches()
        return self.curator.run_automation_if_due(
            config=config,
            force_run=force_run,
        )

    def run_curator_maintenance(
        self,
        *,
        config: EffectiveConfig,
        dry_run: bool = True,
        force: bool = False,
        source: str = "ops",
    ) -> dict[str, object]:
        self._invalidate_caches()
        return self.curator.run_maintenance(
            config=config,
            dry_run=dry_run,
            force=force,
            source=source,
        )


def _file_kind(relative_path: str) -> str:
    if relative_path == "SKILL.md":
        return "manifest"
    return relative_path.split("/", 1)[0].rstrip("s") or "file"


def _validated_file_kind(relative_path: str) -> str | None:
    if relative_path == "SKILL.md":
        return "manifest"
    prefix = relative_path.split("/", 1)[0]
    if prefix not in _SKILL_ALLOWED_SUBDIRS:
        return None
    return _SKILL_ALLOWED_SUBDIRS[prefix]


def _body_preview_from_text(text: str, max_chars: int = 240) -> str:
    body = text
    stripped = text.lstrip()
    if stripped.startswith("---\n"):
        _, _, remainder = stripped.partition("---\n")
        _frontmatter, separator, candidate_body = remainder.partition("\n---\n")
        if separator:
            body = candidate_body
    normalized = " ".join(line.strip() for line in body.splitlines() if line.strip())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _bounded_skill_support_file_scan_limit() -> int:
    configured = DEFAULT_SKILL_SUPPORT_FILE_SCAN_LIMIT
    if configured < 1:
        return 1
    return min(configured, MAX_SKILL_SUPPORT_FILE_SCAN_LIMIT)


def _bounded_skill_tree_scan_limit() -> int:
    configured = DEFAULT_SKILL_TREE_SCAN_LIMIT
    if configured < 1:
        return 1
    return min(configured, MAX_SKILL_TREE_SCAN_LIMIT)


def _bounded_skill_tree_hash_file_byte_limit() -> int:
    configured = DEFAULT_SKILL_TREE_HASH_FILE_BYTE_LIMIT
    if configured < 1:
        return 1
    return min(configured, MAX_SKILL_TREE_HASH_FILE_BYTE_LIMIT)


def _list_supporting_skill_files(skill_root: Path) -> _SkillSupportFileScan:
    max_scanned_paths = _bounded_skill_support_file_scan_limit()
    files: list[str] = []
    scanned_path_count = 0
    scan_truncated = False
    for subdir in ("assets", "templates", "scripts", "references"):
        root = skill_root / subdir
        if not root.exists():
            continue
        pending = [root]
        while pending:
            current = pending.pop()
            try:
                iterator = os.scandir(current)
            except OSError:
                continue
            with iterator as entries:
                for entry in entries:
                    if scanned_path_count >= max_scanned_paths:
                        scan_truncated = True
                        pending.clear()
                        break
                    scanned_path_count += 1
                    try:
                        relative_path = Path(entry.path).relative_to(skill_root).as_posix()
                    except ValueError:
                        continue
                    if _validated_file_kind(relative_path) is None:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    files.append(relative_path)
            if scan_truncated:
                break
        if scan_truncated:
            break
    return _SkillSupportFileScan(
        paths=tuple(sorted(dict.fromkeys(files))),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _manifest_supporting_paths(manifest: SkillManifest) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *manifest.asset_paths,
                *manifest.template_paths,
                *manifest.script_paths,
                *manifest.reference_paths,
            )
        )
    )


def _is_binary_path(path: Path) -> bool:
    return _is_binary_payload(path.read_bytes()[:2048], path)


def _is_binary_payload(payload: bytes, path: Path) -> bool:
    if b"\x00" in payload:
        return True
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".zip", ".skill"}:
        return True
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _package_from_discovered_manifest(manifest: SkillManifest) -> SkillPackage:
    error_count = sum(1 for issue in manifest.issues if issue.severity.value == "error")
    warning_count = sum(1 for issue in manifest.issues if issue.severity.value == "warning")
    return SkillPackage(
        manifest=manifest,
        installed_path=str(Path(manifest.path).parent),
        source=str(Path(manifest.path).parent),
        status="installed",
        audit_findings=tuple(issue.message for issue in manifest.issues if issue.severity.value == "error"),
        audit_warnings=tuple(issue.message for issue in manifest.issues if issue.severity.value == "warning"),
        security_scan={
            "issues": len(manifest.issues),
            "error_count": error_count,
            "warning_count": warning_count,
        },
    )


def _skill_discovery_diagnostics(
    *,
    cache_hit: bool,
    watch_enabled: bool,
    root_count: int,
    manifest_count: int,
    enabled_count: int,
    package_count: int,
    stage_durations_ms: dict[str, int],
    total_ms: int,
) -> SkillDiscoveryDiagnostics:
    durations = dict(sorted(stage_durations_ms.items()))
    durations["total"] = total_ms
    slowest_stage, slowest_stage_duration_ms = _slowest_skill_discovery_stage(durations)
    return SkillDiscoveryDiagnostics(
        cache_hit=cache_hit,
        watch_enabled=watch_enabled,
        root_count=root_count,
        manifest_count=manifest_count,
        enabled_count=enabled_count,
        package_count=package_count,
        stage_durations_ms=durations,
        slowest_stage=slowest_stage,
        slowest_stage_duration_ms=slowest_stage_duration_ms,
    )


def _slowest_skill_discovery_stage(stage_durations_ms: dict[str, int]) -> tuple[str | None, int | None]:
    candidates = {
        stage: duration
        for stage, duration in stage_durations_ms.items()
        if stage != "total"
    }
    if not candidates:
        return None, None
    slowest_stage = max(candidates, key=lambda stage: candidates[stage])
    return slowest_stage, candidates[slowest_stage]


def sync_bundled_skills_to_home(installed_root: Path, bundled_root: Path) -> None:
    if not bundled_root.exists():
        return
    cache_key = (str(installed_root.expanduser().resolve()), str(bundled_root.expanduser().resolve()))
    cached = _BUNDLED_SYNC_CACHE.get(cache_key)
    now = monotonic()
    if cached is not None and now - cached.checked_at <= _BUNDLED_SYNC_CACHE_TTL_SECONDS:
        if _bundled_sync_targets_exist(installed_root, cached.skill_ids):
            return
    source_stamps = _bundled_source_skill_stamps(bundled_root)
    root_stamp = _bundled_source_root_stamp(source_stamps)
    with _BUNDLED_SYNC_LOCK:
        now = monotonic()
        cached = _BUNDLED_SYNC_CACHE.get(cache_key)
        installed_stamp = _installed_skills_root_stamp(installed_root)
        if cached is not None and cached.root_stamp == root_stamp and cached.installed_stamp == installed_stamp:
            _BUNDLED_SYNC_CACHE[cache_key] = _BundledSyncCacheState(
                root_stamp=root_stamp,
                installed_stamp=installed_stamp,
                checked_at=now,
                skill_ids=cached.skill_ids,
            )
            return
        try:
            installed_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            _BUNDLED_SYNC_CACHE[cache_key] = _BundledSyncCacheState(
                root_stamp=root_stamp,
                installed_stamp=installed_stamp,
                checked_at=now,
                skill_ids=tuple(skill_id for skill_id, _source_stamp in source_stamps),
            )
            return
        installed_stamp = _installed_skills_root_stamp(installed_root)
        manifest_path = installed_root / ".bundled_manifest"
        manifest = _read_bundled_manifest(manifest_path)
        if _bundled_manifest_matches_root_stamp(
            manifest=manifest,
            installed_root=installed_root,
            source_stamps=source_stamps,
            root_stamp=root_stamp,
        ):
            _BUNDLED_SYNC_CACHE[cache_key] = _BundledSyncCacheState(
                root_stamp=root_stamp,
                installed_stamp=installed_stamp,
                checked_at=now,
                skill_ids=tuple(skill_id for skill_id, _source_stamp in source_stamps),
            )
            return
        changed = False
        for skill_id, source_stamp in source_stamps:
            source_skill = bundled_root / skill_id
            skill_id = source_skill.name
            target_skill = installed_root / skill_id
            previous = manifest.get(skill_id, {}) if isinstance(manifest.get(skill_id), dict) else {}
            if target_skill.exists() and previous.get("source_stamp") == source_stamp and previous.get("source_hash"):
                continue
            try:
                source_hash = _skill_tree_hash(source_skill)
            except OSError:
                continue
            if not target_skill.exists():
                try:
                    _copy_skill_tree(source_skill, target_skill)
                    installed_hash = _skill_tree_hash(target_skill)
                except OSError:
                    continue
                manifest[skill_id] = {
                    "source_hash": source_hash,
                    "source_stamp": source_stamp,
                    "installed_hash": installed_hash,
                }
                changed = True
                continue
            try:
                target_hash = _skill_tree_hash(target_skill)
            except OSError:
                continue
            if previous.get("source_hash") == source_hash:
                if previous.get("source_stamp") != source_stamp:
                    manifest[skill_id] = {
                        **previous,
                        "source_hash": source_hash,
                        "source_stamp": source_stamp,
                        "installed_hash": target_hash,
                    }
                    changed = True
                continue
            if previous.get("installed_hash") and target_hash != previous.get("installed_hash"):
                manifest[skill_id] = {
                    "source_hash": previous.get("source_hash"),
                    "source_stamp": previous.get("source_stamp"),
                    "installed_hash": target_hash,
                    "source_update_available": source_hash,
                    "source_update_stamp": source_stamp,
                }
                changed = True
                continue
            try:
                _copy_skill_tree(source_skill, target_skill)
                installed_hash = _skill_tree_hash(target_skill)
            except OSError:
                continue
            manifest[skill_id] = {
                "source_hash": source_hash,
                "source_stamp": source_stamp,
                "installed_hash": installed_hash,
            }
            changed = True
        if changed or manifest.get("_bundled_root_stamp") != root_stamp:
            manifest["_bundled_root_stamp"] = root_stamp
            _write_bundled_manifest(manifest_path, manifest)
        _BUNDLED_SYNC_CACHE[cache_key] = _BundledSyncCacheState(
            root_stamp=root_stamp,
            installed_stamp=_installed_skills_root_stamp(installed_root),
            checked_at=monotonic(),
            skill_ids=tuple(skill_id for skill_id, _source_stamp in source_stamps),
        )


def _copy_skill_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _read_bundled_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_bundled_manifest(path: Path, manifest: dict[str, object]) -> bool:
    try:
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _bundled_manifest_matches_root_stamp(
    *,
    manifest: dict[str, object],
    installed_root: Path,
    source_stamps: tuple[tuple[str, str], ...],
    root_stamp: str,
) -> bool:
    if manifest.get("_bundled_root_stamp") != root_stamp:
        return False
    for skill_id, source_stamp in source_stamps:
        previous = manifest.get(skill_id)
        if not isinstance(previous, dict) or not previous.get("source_hash") or not previous.get("source_stamp"):
            return False
        if previous.get("source_stamp") != source_stamp:
            return False
        if not (installed_root / skill_id / "SKILL.md").exists():
            return False
    return True


def _bundled_sync_targets_exist(installed_root: Path, skill_ids: tuple[str, ...]) -> bool:
    if not skill_ids:
        return False
    for skill_id in skill_ids:
        if not (installed_root / skill_id / "SKILL.md").exists():
            return False
    return True


def _bundled_source_skill_stamps(bundled_root: Path) -> tuple[tuple[str, str], ...]:
    try:
        source_skills = tuple(
            source_skill
            for source_skill in sorted(bundled_root.iterdir(), key=lambda item: item.name.casefold())
            if (source_skill / "SKILL.md").exists()
        )
    except OSError:
        return ()
    if len(source_skills) <= 1:
        return tuple((source_skill.name, _skill_tree_metadata_stamp(source_skill)) for source_skill in source_skills)
    max_workers = min(8, len(source_skills))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="anvil-skill-stamp") as executor:
        stamps = tuple(executor.map(_source_skill_metadata_stamp, source_skills))
    return stamps


def _source_skill_metadata_stamp(source_skill: Path) -> tuple[str, str]:
    return (source_skill.name, _skill_tree_metadata_stamp(source_skill))


def _bundled_source_root_stamp(source_stamps: tuple[tuple[str, str], ...]) -> str:
    digest = hashlib.sha256()
    for skill_id, source_stamp in source_stamps:
        digest.update(skill_id.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(source_stamp.encode("ascii", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _installed_skills_root_stamp(root: Path) -> str:
    digest = hashlib.sha256()
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return "unreadable"
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_path = child / "SKILL.md"
        digest.update(child.name.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        for path in (child, skill_path):
            try:
                stat = path.stat()
            except OSError:
                digest.update(path.name.encode("utf-8", errors="replace"))
                digest.update(b":missing\0")
                continue
            digest.update(str(stat.st_mtime_ns).encode("ascii", errors="ignore"))
            digest.update(b":")
            digest.update(str(stat.st_size).encode("ascii", errors="ignore"))
            digest.update(b"\0")
    return digest.hexdigest()


def _skill_tree_metadata_stamp(root: Path) -> str:
    digest = hashlib.sha256()
    tree_scan = _iter_tree_files(root)
    for relative, path in tree_scan.files:
        if relative.startswith("."):
            continue
        try:
            stat = os.stat(path, follow_symlinks=False)
        except OSError:
            digest.update(relative.encode("utf-8", errors="replace"))
            digest.update(b"\0unreadable\0")
            continue
        digest.update(relative.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii", errors="ignore"))
        digest.update(b":")
        digest.update(str(stat.st_size).encode("ascii", errors="ignore"))
        digest.update(b"\0")
    if tree_scan.scan_truncated:
        digest.update(b"scan_truncated\0")
        digest.update(str(tree_scan.scanned_path_count).encode("ascii", errors="ignore"))
        digest.update(b":")
        digest.update(str(tree_scan.max_scanned_paths).encode("ascii", errors="ignore"))
        return f"metadata-sha256-truncated:{digest.hexdigest()}"
    return digest.hexdigest()


def _iter_tree_files(root: Path) -> _SkillTreeFileScan:
    max_scanned_paths = _bounded_skill_tree_scan_limit()
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
    return _SkillTreeFileScan(
        files=tuple(sorted(files, key=lambda item: item[0])),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def normalize_skill_id(skill_id: str) -> str:
    normalized = str(skill_id or "").strip()
    while normalized and normalized[0] in "`'\"":
        normalized = normalized[1:].strip()
    while normalized and normalized[-1] in "`'\"":
        normalized = normalized[:-1].strip()
    normalized = normalized.lstrip("@$").strip()
    if normalized.lower().startswith("skill://"):
        normalized = normalized[len("skill://") :].strip()
    normalized = normalized.replace("\\", "/").strip()
    for marker in ("/.anvil/skills/", "/skills/"):
        marker_index = normalized.lower().find(marker)
        if marker_index >= 0:
            normalized = normalized[marker_index + len(marker) :]
            break
    if normalized.lower().endswith("/skill.md"):
        normalized = normalized[: -len("/SKILL.md")]
    normalized = normalized.strip("/")
    if "/" in normalized:
        normalized = normalized.split("/", 1)[0]
    return normalized.lstrip("@$").strip()


def _skill_tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    tree_scan = _iter_tree_files(root)
    max_file_bytes = _bounded_skill_tree_hash_file_byte_limit()
    content_truncated = False
    for relative, path_text in tree_scan.files:
        if relative.startswith("."):
            continue
        path = Path(path_text)
        digest.update(relative.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        payload = path.read_bytes()
        if len(payload) > max_file_bytes:
            payload = payload[:max_file_bytes]
            content_truncated = True
        digest.update(payload)
        digest.update(b"\0")
    if tree_scan.scan_truncated or content_truncated:
        digest.update(b"scan_truncated\0" if tree_scan.scan_truncated else b"content_truncated\0")
        digest.update(str(tree_scan.scanned_path_count).encode("ascii", errors="ignore"))
        digest.update(b":")
        digest.update(str(tree_scan.max_scanned_paths).encode("ascii", errors="ignore"))
        digest.update(b":")
        digest.update(str(max_file_bytes).encode("ascii", errors="ignore"))
        return f"sha256-truncated:{digest.hexdigest()}"
    return digest.hexdigest()
