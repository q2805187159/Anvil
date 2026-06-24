from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import RLock
from pathlib import Path
from time import monotonic
from typing import Any

from anvil.config import EffectiveConfig

from .cache import SkillsCache
from .contracts import (
    SkillCandidate,
    SkillContentView,
    SkillDiscoveryDiagnostics,
    SkillDiscoveryResult,
    SkillFileEntry,
    SkillFileIndexView,
    SkillFileReadView,
    SkillManifest,
    SkillPackage,
    SkillRetrievalPlan,
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

    def retrieve(
        self,
        *,
        config: EffectiveConfig,
        fingerprint: str,
        query: str | None = None,
        top_k: int = 4,
        feedback_by_skill_id: dict[str, dict[str, object]] | None = None,
        graph_neighbors_by_skill_id: dict[str, tuple[str, ...] | list[str]] | None = None,
        salience_boost_terms: dict[str, float] | None = None,
        prefetch_terms: tuple[str, ...] | list[str] | None = None,
        discovery_result: SkillDiscoveryResult | None = None,
    ) -> SkillRetrievalPlan:
        """Return L0-L6 skill retrieval candidates without loading full skill bodies."""

        result = discovery_result or self.discover(config=config, fingerprint=fingerprint)
        query_text = str(query or "").strip()
        query_terms = _retrieval_terms(query_text)
        normalized_salience_terms = _normalize_salience_boost_terms(salience_boost_terms)
        prefetch_query_terms = _retrieval_terms(" ".join(str(term) for term in (prefetch_terms or ())))
        hyde_triggered = _skill_hyde_should_expand(
            query_terms=query_terms,
            enabled_count=len(result.enabled_ids),
            salience_boost_terms=normalized_salience_terms,
        )
        expanded_query_terms = (
            _skill_hyde_expanded_terms(
                query_terms=query_terms,
                salience_boost_terms=normalized_salience_terms,
            )
            if hyde_triggered
            else query_terms
        )
        normalized_feedback = {
            normalize_skill_id(skill_id): dict(value)
            for skill_id, value in (feedback_by_skill_id or {}).items()
        }
        normalized_neighbors = {
            normalize_skill_id(skill_id): tuple(str(item) for item in value if str(item).strip())
            for skill_id, value in (graph_neighbors_by_skill_id or {}).items()
        }
        l0_summary = _skill_l0_summary(result)
        scored: list[dict[str, Any]] = []
        for manifest in result.enabled_manifests:
            fields = _skill_retrieval_fields(manifest)
            bm25_score, matched_terms, matched_fields = _skill_bm25_score(fields, expanded_query_terms)
            vector_score = _skill_lexical_vector_score(fields, expanded_query_terms)
            feedback = normalized_feedback.get(manifest.skill_id, {})
            history_score = _skill_history_score(feedback)
            graph_neighbors = _skill_graph_neighbors(manifest, normalized_neighbors.get(manifest.skill_id, ()))
            graph_score = _skill_graph_score(graph_neighbors, expanded_query_terms, matched_terms)
            salience_score = _skill_salience_score(fields, normalized_salience_terms)
            hyde_score = _skill_hyde_score(fields, expanded_query_terms, query_terms)
            prefetch_score = _skill_prefetch_score(fields, prefetch_query_terms)
            scored.append(
                {
                    "manifest": manifest,
                    "fields": fields,
                    "bm25": bm25_score,
                    "vector": vector_score,
                    "history": history_score,
                    "graph": graph_score,
                    "salience": salience_score,
                    "hyde": hyde_score,
                    "prefetch": prefetch_score,
                    "matched_terms": matched_terms,
                    "matched_fields": matched_fields,
                    "graph_neighbors": graph_neighbors,
                    "feedback": feedback,
                }
            )

        fusion_scores = _skill_rrf_fusion(scored)
        for item in scored:
            item["fusion"] = fusion_scores.get(item["manifest"].skill_id, 0.0)
            item["rerank"] = 0.0
        rerank_triggered, rerank_reasons = _skill_l4_should_rerank(scored)
        if rerank_triggered:
            for item in scored:
                item["rerank"] = _skill_l4_rerank_score(item)
                item["fusion"] = round(float(item["fusion"]) + float(item["rerank"]) * 0.12, 6)
        scored.sort(key=lambda item: (-float(item["fusion"]), item["manifest"].skill_id))
        limit = max(int(top_k or 0), 0)
        selected_ids = tuple(item["manifest"].skill_id for item in scored[:limit] if float(item["fusion"]) > 0.0)
        selected_set = set(selected_ids)
        rank_by_id = {skill_id: index for index, skill_id in enumerate(selected_ids, start=1)}
        prefetch_ids = _skill_l6_prefetch_ids(scored, exclude_skill_ids=selected_set)
        candidates = tuple(
            _skill_candidate_from_score(
                item,
                selected=item["manifest"].skill_id in selected_set,
                selection_rank=rank_by_id.get(item["manifest"].skill_id),
                prefetch_candidate=item["manifest"].skill_id in prefetch_ids,
            )
            for item in scored
        )
        tiers_used = ("L0", "L1", "L2", "L3")
        if rerank_triggered:
            tiers_used = (*tiers_used, "L4")
        if hyde_triggered:
            tiers_used = (*tiers_used, "L5")
        if prefetch_ids:
            tiers_used = (*tiers_used, "L6")
        return SkillRetrievalPlan(
            query=query_text,
            top_k=limit,
            selected_skill_ids=selected_ids,
            l0_summary=l0_summary,
            tiers_used=tiers_used,
            candidates=candidates,
            diagnostics={
                "loaded_full_skill_content": False,
                "embedding_mode": "lexical_fallback",
                "candidate_count": len(candidates),
                "query_terms": query_terms,
                "expanded_query_terms": expanded_query_terms,
                "selected_count": len(selected_ids),
                "cache_hit": result.discovery_diagnostics.cache_hit,
                "l4_rerank_triggered": rerank_triggered,
                "l4_trigger_reasons": rerank_reasons,
                "l5_hyde_triggered": hyde_triggered,
                "l6_prefetch_triggered": bool(prefetch_ids),
                "prefetch_skill_ids": prefetch_ids,
                "candidates_per_tier": {
                    "L0": len(result.enabled_ids),
                    "L1": len(tuple(item for item in scored if item["bm25"] > 0)),
                    "L2": len(tuple(item for item in scored if item["vector"] > 0)),
                    "L3": len(tuple(item for item in scored if item["fusion"] > 0)),
                    "L4": len(tuple(item for item in scored if item["rerank"] > 0)),
                    "L5": len(tuple(item for item in scored if item["hyde"] > 0)),
                    "L6": len(prefetch_ids),
                },
            },
        )

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


_RETRIEVAL_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_RETRIEVAL_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "for",
    "id",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
_SKILL_FIELD_WEIGHTS = {
    "skill_id": 3.5,
    "title": 3.2,
    "summary": 2.8,
    "tags": 2.4,
    "task_type": 2.0,
    "domain": 1.8,
    "allowed_tools": 1.2,
    "input_requirements": 1.2,
    "related_skills": 1.4,
    "description": 1.0,
    "body_preview": 0.8,
}


def _retrieval_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for match in _RETRIEVAL_TOKEN_RE.finditer(_normalize_retrieval_text(text)):
        term = match.group(0)
        if len(term) <= 1 or term in _RETRIEVAL_STOPWORDS:
            continue
        if term not in terms:
            terms.append(term)
    return tuple(terms[:32])


def _normalize_salience_boost_terms(terms: dict[str, float] | None) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for raw_term, raw_weight in (terms or {}).items():
        weight = _float_from_object(raw_weight)
        if weight is None or weight <= 0:
            continue
        for term in _retrieval_terms(str(raw_term)):
            normalized[term] = max(normalized.get(term, 0.0), min(weight, 1.0))
    return dict(sorted(normalized.items()))


def _normalize_retrieval_text(text: str) -> str:
    return str(text or "").casefold().replace("-", " ").replace("_", " ").replace("/", " ")


def _skill_l0_summary(result: SkillDiscoveryResult) -> dict[str, object]:
    domains = Counter(str(manifest.domain or "unspecified") for manifest in result.enabled_manifests)
    task_types = Counter(str(manifest.task_type or "unspecified") for manifest in result.enabled_manifests)
    tags = Counter(tag for manifest in result.enabled_manifests for tag in manifest.tags)
    source_roots = Counter(manifest.source_root for manifest in result.enabled_manifests)
    return {
        "root_count": result.discovery_diagnostics.root_count,
        "manifest_count": len(result.all_manifests),
        "enabled_count": len(result.enabled_ids),
        "domain_counts": dict(sorted(domains.items())),
        "task_type_counts": dict(sorted(task_types.items())),
        "tag_counts": dict(sorted(tags.items())),
        "source_root_count": len(source_roots),
    }


def _skill_retrieval_fields(manifest: SkillManifest) -> dict[str, str]:
    return {
        "skill_id": manifest.skill_id,
        "title": manifest.title,
        "summary": manifest.summary,
        "description": manifest.description or "",
        "tags": " ".join(manifest.tags),
        "domain": manifest.domain or "",
        "task_type": manifest.task_type or "",
        "allowed_tools": " ".join(manifest.allowed_tools),
        "input_requirements": " ".join(manifest.input_requirements),
        "related_skills": " ".join(manifest.related_skills),
        "body_preview": manifest.body_preview,
    }


def _skill_bm25_score(fields: dict[str, str], query_terms: tuple[str, ...]) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    if not query_terms:
        return 0.0, (), ()
    score = 0.0
    matched_terms: list[str] = []
    matched_fields: list[str] = []
    for field_name, raw_text in fields.items():
        haystack = _normalize_retrieval_text(raw_text)
        if not haystack:
            continue
        weight = _SKILL_FIELD_WEIGHTS.get(field_name, 1.0)
        field_matched = False
        for term in query_terms:
            if _term_matches_text(term, haystack):
                score += weight
                field_matched = True
                if term not in matched_terms:
                    matched_terms.append(term)
        if field_matched and field_name not in matched_fields:
            matched_fields.append(field_name)
    return round(score, 4), tuple(matched_terms), tuple(matched_fields)


def _skill_lexical_vector_score(fields: dict[str, str], query_terms: tuple[str, ...]) -> float:
    if not query_terms:
        return 0.0
    haystack = _normalize_retrieval_text(" ".join(fields.values()))
    field_terms = set(_retrieval_terms(haystack))
    if not field_terms:
        return 0.0
    overlap = sum(1 for term in query_terms if term in field_terms or _term_matches_text(term, haystack))
    denominator = (len(query_terms) * len(field_terms)) ** 0.5
    return round(overlap / denominator, 4) if denominator else 0.0


def _skill_history_score(feedback: dict[str, object]) -> float:
    if not feedback:
        return 0.0
    utility = _float_from_object(feedback.get("utility_score"))
    usage_count = _int_from_object(feedback.get("usage_count") or feedback.get("feedback_count"))
    success_count = _int_from_object(feedback.get("success_count"))
    failure_count = _int_from_object(feedback.get("failure_count"))
    correction_count = _int_from_object(feedback.get("correction_count") or feedback.get("user_correction_count"))
    success_rate = success_count / usage_count if usage_count else 0.0
    score = (utility or 0.0) * 0.55 + success_rate * 0.35 + min(usage_count, 10) / 10 * 0.1
    score -= min(failure_count + correction_count, 5) * 0.04
    return round(min(max(score, 0.0), 1.0), 4)


def _skill_graph_neighbors(manifest: SkillManifest, configured_neighbors: tuple[str, ...]) -> tuple[str, ...]:
    neighbors: list[str] = []
    for item in (*manifest.related_skills, *configured_neighbors):
        normalized = normalize_skill_id(str(item))
        if normalized and normalized != manifest.skill_id and normalized not in neighbors:
            neighbors.append(normalized)
    return tuple(neighbors[:16])


def _skill_graph_score(
    graph_neighbors: tuple[str, ...],
    query_terms: tuple[str, ...],
    matched_terms: tuple[str, ...],
) -> float:
    if not graph_neighbors:
        return 0.0
    neighbor_text = _normalize_retrieval_text(" ".join(graph_neighbors))
    query_hits = sum(1 for term in query_terms if _term_matches_text(term, neighbor_text))
    matched_boost = min(len(matched_terms), 3) * 0.08
    score = query_hits * 0.18 + matched_boost + min(len(graph_neighbors), 6) * 0.02
    return round(min(score, 1.0), 4)


def _skill_salience_score(fields: dict[str, str], salience_boost_terms: dict[str, float]) -> float:
    if not salience_boost_terms:
        return 0.0
    haystack = _normalize_retrieval_text(" ".join(fields.values()))
    score = 0.0
    for term, weight in salience_boost_terms.items():
        if _term_matches_text(term, haystack):
            score += min(max(weight, 0.0), 1.0)
    return round(min(score / max(len(salience_boost_terms), 1), 1.0), 4)


def _skill_hyde_should_expand(
    *,
    query_terms: tuple[str, ...],
    enabled_count: int,
    salience_boost_terms: dict[str, float],
) -> bool:
    return enabled_count > 0 and (len(query_terms) <= 2 or bool(salience_boost_terms))


def _skill_hyde_expanded_terms(
    *,
    query_terms: tuple[str, ...],
    salience_boost_terms: dict[str, float],
) -> tuple[str, ...]:
    terms: list[str] = list(query_terms)
    for term in salience_boost_terms:
        if term not in terms:
            terms.append(term)
    return tuple(terms[:32])


def _skill_hyde_score(
    fields: dict[str, str],
    expanded_query_terms: tuple[str, ...],
    original_query_terms: tuple[str, ...],
) -> float:
    expanded_only = tuple(term for term in expanded_query_terms if term not in set(original_query_terms))
    if not expanded_only:
        return 0.0
    haystack = _normalize_retrieval_text(" ".join(fields.values()))
    hits = sum(1 for term in expanded_only if _term_matches_text(term, haystack))
    return round(min(hits / max(len(expanded_only), 1), 1.0), 4)


def _skill_l4_should_rerank(scored: list[dict[str, Any]]) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if len(scored) >= 4:
        reasons.append("high_candidate_count")
    if any(_skill_risk_rank(str(item["manifest"].risk_level or "")) >= 2 for item in scored):
        reasons.append("high_risk_candidate")
    return bool(reasons), tuple(reasons)


def _skill_l4_rerank_score(item: dict[str, Any]) -> float:
    manifest: SkillManifest = item["manifest"]
    base = (
        min(float(item["bm25"]) / 20.0, 1.0) * 0.3
        + float(item["vector"]) * 0.2
        + float(item["history"]) * 0.18
        + float(item["graph"]) * 0.08
        + float(item.get("salience") or 0.0) * 0.18
        + float(item.get("hyde") or 0.0) * 0.12
    )
    risk_penalty = _skill_risk_rank(str(manifest.risk_level or "")) * 0.08
    return round(min(max(base - risk_penalty, 0.0), 1.0), 4)


def _skill_risk_rank(risk_level: str) -> int:
    normalized = str(risk_level or "").casefold().strip()
    if normalized in {"critical", "danger", "dangerous", "high"}:
        return 2
    if normalized in {"medium", "moderate", "normal"}:
        return 1
    return 0


def _skill_prefetch_score(fields: dict[str, str], prefetch_terms: tuple[str, ...]) -> float:
    if not prefetch_terms:
        return 0.0
    haystack = _normalize_retrieval_text(" ".join(fields.values()))
    hits = sum(1 for term in prefetch_terms if _term_matches_text(term, haystack))
    return round(min(hits / max(len(prefetch_terms), 1), 1.0), 4)


def _skill_l6_prefetch_ids(
    scored: list[dict[str, Any]],
    *,
    exclude_skill_ids: set[str],
    limit: int = 3,
    min_score: float = 0.25,
) -> tuple[str, ...]:
    ranked = [
        item
        for item in sorted(
            scored,
            key=lambda candidate: (-float(candidate.get("prefetch") or 0.0), candidate["manifest"].skill_id),
        )
        if float(item.get("prefetch") or 0.0) >= min_score
        and item["manifest"].skill_id not in exclude_skill_ids
        and _skill_risk_rank(str(item["manifest"].risk_level or "")) < 2
    ]
    return tuple(item["manifest"].skill_id for item in ranked[:limit])


def _skill_rrf_fusion(scored: list[dict[str, Any]], *, rank_constant: int = 60) -> dict[str, float]:
    fusion: dict[str, float] = {item["manifest"].skill_id: 0.0 for item in scored}
    for score_key in ("bm25", "vector", "history", "graph", "salience", "hyde"):
        ranked = [
            item
            for item in sorted(
                scored,
                key=lambda candidate: (-float(candidate[score_key]), candidate["manifest"].skill_id),
            )
            if float(item[score_key]) > 0.0
        ]
        for rank, item in enumerate(ranked, start=1):
            fusion[item["manifest"].skill_id] += 1.0 / (rank_constant + rank)
    for item in scored:
        direct_score = (
            min(float(item["bm25"]) / 20.0, 1.0) * 0.3
            + float(item["vector"]) * 0.2
            + float(item["history"]) * 0.22
            + float(item["graph"]) * 0.07
            + float(item.get("salience") or 0.0) * 0.16
            + float(item.get("hyde") or 0.0) * 0.05
        )
        fusion[item["manifest"].skill_id] += direct_score
    return {skill_id: round(score, 6) for skill_id, score in fusion.items()}


def _skill_candidate_from_score(
    item: dict[str, Any],
    *,
    selected: bool,
    selection_rank: int | None,
    prefetch_candidate: bool = False,
) -> SkillCandidate:
    manifest: SkillManifest = item["manifest"]
    feedback = dict(item["feedback"])
    tier_scores = {
        "bm25": round(float(item["bm25"]), 4),
        "vector": round(float(item["vector"]), 4),
        "history": round(float(item["history"]), 4),
        "graph": round(float(item["graph"]), 4),
        "salience": round(float(item.get("salience") or 0.0), 4),
        "hyde": round(float(item.get("hyde") or 0.0), 4),
        "rerank": round(float(item.get("rerank") or 0.0), 4),
        "prefetch": round(float(item.get("prefetch") or 0.0), 4),
        "fusion": round(float(item["fusion"]), 6),
    }
    metadata = {
        "source_kind": "skill",
        "source_id": manifest.skill_id,
        "path": manifest.path,
        "source_root": manifest.source_root,
        "trust": manifest.trust,
        "version": manifest.version,
        "domain": manifest.domain,
        "task_type": manifest.task_type,
        "tags": manifest.tags,
        "allowed_tools": manifest.allowed_tools,
        "input_requirements": manifest.input_requirements,
        "risk_level": manifest.risk_level or "normal",
        "readiness": manifest.readiness.model_dump(mode="json"),
        "feedback": feedback,
        "loaded_full_skill_content": False,
    }
    if prefetch_candidate:
        metadata.update(
            {
                "prefetch_candidate": True,
                "prefetch_reason": "L6_goal_prefetch",
            }
        )
    return SkillCandidate(
        skill_id=manifest.skill_id,
        title=manifest.title,
        summary=manifest.summary,
        selection_rank=selection_rank,
        selected=selected,
        tier_scores=tier_scores,
        fusion_score=tier_scores["fusion"],
        matched_terms=tuple(item["matched_terms"]),
        matched_fields=tuple(item["matched_fields"]),
        graph_neighbors=tuple(item["graph_neighbors"]),
        source_ref=f"skill://{manifest.skill_id}",
        token_cost=_skill_candidate_token_cost(manifest),
        metadata=metadata,
    )


def _skill_candidate_token_cost(manifest: SkillManifest) -> int:
    payload = " ".join(
        (
            manifest.skill_id,
            manifest.title,
            manifest.summary,
            " ".join(manifest.tags),
            manifest.domain or "",
            manifest.task_type or "",
        )
    )
    return max(len(_retrieval_terms(payload)), 1)


def _term_matches_text(term: str, haystack: str) -> bool:
    if term in haystack:
        return True
    if term.endswith("s") and term[:-1] in haystack:
        return True
    return f"{term}s" in haystack


def _int_from_object(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _float_from_object(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
