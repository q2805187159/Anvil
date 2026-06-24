from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from pydantic import BaseModel, ConfigDict, Field

from anvil.config import ContextFilesConfig
from anvil.memory.scrubber import MemorySecretScrubber

if TYPE_CHECKING:
    from anvil.sandbox.path_service import PathService


class ProjectContextFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    virtual_path: str
    relative_path: str
    applies_to: str = "/mnt/user-data/workspace"
    scope: str = "."
    content: str
    truncated: bool = False


class ProjectContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str | None = None
    files: tuple[ProjectContextFile, ...] = ()
    rendered: str = ""
    total_chars: int = 0
    cache_status: str | None = None
    discovery_scanned_path_count: int = 0
    discovery_max_scanned_paths: int = 0
    discovery_scan_truncated: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.files and self.rendered.strip())


@dataclass(frozen=True)
class ProjectContextSnapshotCacheStats:
    max_entries: int
    size: int
    hits: int
    misses: int
    writes: int
    evictions: int
    bypasses: int


@dataclass(frozen=True)
class _ContextDiscovery:
    candidates: list[Path]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool


@dataclass(frozen=True)
class _WorkspacePathEntry:
    path: Path
    ignored: bool = False


@dataclass(frozen=True)
class _WorkspaceScan:
    entries: list[_WorkspacePathEntry]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool


class ProjectContextSnapshotCache:
    def __init__(self, *, max_entries: int = 128) -> None:
        self.max_entries = max(max_entries, 1)
        self._items: OrderedDict[str, ProjectContextSnapshot] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.evictions = 0
        self.bypasses = 0

    def get(self, key: str) -> ProjectContextSnapshot | None:
        snapshot = self._items.get(key)
        if snapshot is None:
            self.misses += 1
            return None
        self.hits += 1
        self._items.move_to_end(key)
        return snapshot.model_copy(update={"cache_status": "hit"}, deep=True)

    def put(self, key: str, snapshot: ProjectContextSnapshot) -> ProjectContextSnapshot:
        self.writes += 1
        stored = snapshot.model_copy(update={"cache_status": "miss"}, deep=True)
        self._items[key] = stored
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
            self.evictions += 1
        return stored.model_copy(deep=True)

    def bypass(self) -> None:
        self.bypasses += 1

    def reset(self, *, max_entries: int | None = None) -> None:
        if max_entries is not None:
            self.max_entries = max(max_entries, 1)
        self._items.clear()
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.evictions = 0
        self.bypasses = 0

    def stats(self) -> ProjectContextSnapshotCacheStats:
        return ProjectContextSnapshotCacheStats(
            max_entries=self.max_entries,
            size=len(self._items),
            hits=self.hits,
            misses=self.misses,
            writes=self.writes,
            evictions=self.evictions,
            bypasses=self.bypasses,
        )


_DEFAULT_CONTEXT_FILES = (
    "AGENTS.md",
    "PROJECT_RULES.md",
    ".cursorrules",
    ".windsurfrules",
)
_DEFAULT_CONTEXT_GLOBS = (
    ".cursor/rules/*.md",
    ".github/copilot-instructions.md",
)
_PROMPT_SECTION_TAGS = (
    "project_context_files",
    "context_file",
)
_PROJECT_CONTEXT_CACHE = ProjectContextSnapshotCache()


def project_context_snapshot_cache_stats() -> ProjectContextSnapshotCacheStats:
    return _PROJECT_CONTEXT_CACHE.stats()


def reset_project_context_snapshot_cache(*, max_entries: int | None = None) -> None:
    _PROJECT_CONTEXT_CACHE.reset(max_entries=max_entries)


def build_project_context_snapshot(
    *,
    path_service: PathService,
    thread_id: str,
    config: ContextFilesConfig | None = None,
) -> ProjectContextSnapshot:
    """Discover bounded project context files from the active thread workspace.

    Context files are stable prompt-prefix material. They are intentionally
    read-only and fail-open: unreadable, binary, or over-budget files are skipped
    rather than preventing runtime assembly.
    """

    effective = config or ContextFilesConfig()
    if not effective.enabled:
        _PROJECT_CONTEXT_CACHE.bypass()
        return ProjectContextSnapshot(cache_status="disabled")

    root = path_service.thread_workspace_dir(thread_id)
    if not root.exists() or not root.is_dir():
        return ProjectContextSnapshot(cache_status="empty")

    discovery = _discover_candidate_paths(root=root, config=effective)
    candidates = discovery.candidates
    cache_key = _project_context_cache_key(root=root, candidates=candidates, config=effective)
    cached = _PROJECT_CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    files = _load_context_files(
        root=root,
        candidates=candidates,
        path_service=path_service,
        thread_id=thread_id,
        config=effective,
    )
    if not files:
        return _PROJECT_CONTEXT_CACHE.put(cache_key, _empty_project_context_snapshot(discovery))

    rendered = _render_context_files(files)
    fingerprint = _fingerprint_context_files(files)
    return _PROJECT_CONTEXT_CACHE.put(
        cache_key,
        ProjectContextSnapshot(
        fingerprint=fingerprint,
        files=tuple(files),
        rendered=rendered,
        total_chars=sum(len(item.content) for item in files),
        discovery_scanned_path_count=discovery.scanned_path_count,
        discovery_max_scanned_paths=discovery.max_scanned_paths,
        discovery_scan_truncated=discovery.scan_truncated,
        ),
    )


def _empty_project_context_snapshot(discovery: _ContextDiscovery) -> ProjectContextSnapshot:
    return ProjectContextSnapshot(
        discovery_scanned_path_count=discovery.scanned_path_count,
        discovery_max_scanned_paths=discovery.max_scanned_paths,
        discovery_scan_truncated=discovery.scan_truncated,
    )


def _load_context_files(
    *,
    root: Path,
    candidates: list[Path],
    path_service: PathService,
    thread_id: str,
    config: ContextFilesConfig,
) -> list[ProjectContextFile]:
    scrubber = MemorySecretScrubber()
    loaded: list[ProjectContextFile] = []
    remaining = max(config.max_chars, 0)

    for candidate in candidates:
        if len(loaded) >= config.max_files or remaining <= 0:
            break
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        if not resolved.is_file() or _is_probably_binary(resolved):
            continue
        try:
            raw = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        per_file_budget = max(min(config.max_chars_per_file, remaining), 0)
        if per_file_budget <= 0:
            break
        truncated = len(raw) > per_file_budget
        content = raw[:per_file_budget]
        content = _sanitize_prompt_text(scrubber.scrub(content).text)
        if not content.strip():
            continue
        remaining -= len(content)

        loaded.append(
            ProjectContextFile(
                virtual_path=_safe_virtual_path(path_service, thread_id, resolved),
                relative_path=resolved.relative_to(root.resolve()).as_posix(),
                applies_to=_context_applies_to(path_service, thread_id, root, resolved),
                scope=_context_scope(root, resolved),
                content=content,
                truncated=truncated,
            )
        )
    return loaded


def _discover_candidate_paths(*, root: Path, config: ContextFilesConfig) -> _ContextDiscovery:
    seen: set[Path] = set()
    ordered: list[Path] = []
    max_scanned_paths = int(config.max_discovery_paths)
    scan_truncated = False
    scanned_path_count = 0

    def scan_budget_remaining() -> int:
        return max(max_scanned_paths - scanned_path_count, 0)

    filenames = tuple(config.filenames or _DEFAULT_CONTEXT_FILES)
    for name in filenames:
        normalized = str(name).strip().replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in Path(normalized).parts:
            continue
        _append_unique(ordered, seen, root / normalized)

    if config.recursive_agents:
        recursive_names = tuple(config.recursive_names or ("AGENTS.md",))
        for name in recursive_names:
            normalized_name = Path(str(name).strip().replace("\\", "/")).name
            if not normalized_name:
                continue
            scan = _iter_recursive_named_files(
                root,
                normalized_name,
                max_candidates=config.max_files * 4,
                max_scanned_paths=scan_budget_remaining(),
            )
            scanned_path_count += scan.scanned_path_count
            scan_truncated = scan_truncated or scan.scan_truncated
            for candidate in scan.candidates:
                _append_unique(ordered, seen, candidate)
            if scan_truncated:
                break

    if config.include_readme:
        for name in ("README.md", "README_zh.md"):
            _append_unique(ordered, seen, root / name)

    if not scan_truncated:
        for glob_pattern in tuple(config.rule_globs or _DEFAULT_CONTEXT_GLOBS):
            normalized = str(glob_pattern).strip().replace("\\", "/")
            if not normalized or normalized.startswith("/") or ".." in Path(normalized).parts:
                continue
            scan = _iter_glob_matches(root, normalized, max_scanned_paths=scan_budget_remaining())
            scanned_path_count += scan.scanned_path_count
            scan_truncated = scan_truncated or scan.scan_truncated
            for candidate in scan.candidates:
                _append_unique(ordered, seen, candidate)
            if scan_truncated:
                break

    return _ContextDiscovery(
        candidates=ordered,
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _project_context_cache_key(*, root: Path, candidates: list[Path], config: ContextFilesConfig) -> str:
    try:
        root_key = root.resolve().as_posix()
    except OSError:
        root_key = root.as_posix()
    payload = {
        "root": root_key,
        "config": {
            "filenames": list(config.filenames),
            "rule_globs": list(config.rule_globs),
            "include_readme": bool(config.include_readme),
            "recursive_agents": bool(config.recursive_agents),
            "recursive_names": list(config.recursive_names),
            "max_files": int(config.max_files),
            "max_chars": int(config.max_chars),
            "max_chars_per_file": int(config.max_chars_per_file),
            "max_discovery_paths": int(config.max_discovery_paths),
        },
        "candidates": [_candidate_stat_fingerprint(root, candidate) for candidate in candidates],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _candidate_stat_fingerprint(root: Path, candidate: Path) -> dict[str, object]:
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    try:
        relative_path = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        relative_path = resolved.as_posix()
    try:
        stat = resolved.stat()
    except OSError:
        return {
            "path": relative_path,
            "exists": False,
        }
    return {
        "path": relative_path,
        "exists": True,
        "is_file": resolved.is_file(),
        "mtime_ns": int(stat.st_mtime_ns),
        "size": int(stat.st_size),
    }


def _iter_recursive_named_files(
    root: Path,
    name: str,
    *,
    max_candidates: int,
    max_scanned_paths: int,
) -> _ContextDiscovery:
    candidates: list[Path] = []
    scan = _scan_workspace_paths(root=root, max_scanned_paths=max_scanned_paths)
    for entry in scan.entries:
        if entry.ignored:
            continue
        path = entry.path
        if path.name != name:
            continue
        if path.is_file():
            candidates.append(path)
        if len(candidates) >= max_candidates:
            break
    return _ContextDiscovery(
        candidates=candidates,
        scanned_path_count=scan.scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan.scan_truncated,
    )


def _iter_glob_matches(root: Path, pattern: str, *, max_scanned_paths: int) -> _ContextDiscovery:
    candidates: list[Path] = []
    scan = _scan_workspace_paths(root=root, max_scanned_paths=max_scanned_paths)
    for entry in scan.entries:
        if entry.ignored:
            continue
        path = entry.path
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if Path(relative).match(pattern):
            candidates.append(path)
    return _ContextDiscovery(
        candidates=sorted(candidates, key=lambda item: item.as_posix()),
        scanned_path_count=scan.scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan.scan_truncated,
    )


def _scan_workspace_paths(*, root: Path, max_scanned_paths: int) -> _WorkspaceScan:
    ignored = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".anvil-scratch"}
    entries_out: list[_WorkspacePathEntry] = []
    if max_scanned_paths <= 0:
        return _WorkspaceScan(entries=entries_out, scanned_path_count=0, max_scanned_paths=max_scanned_paths, scan_truncated=False)
    pending_dirs = [root]
    scanned_path_count = 0
    while pending_dirs:
        current_dir = pending_dirs.pop()
        try:
            with os.scandir(current_dir) as raw_entries:
                entries = sorted(raw_entries, key=lambda item: item.name.lower())
                for entry in entries:
                    if scanned_path_count >= max_scanned_paths:
                        return _WorkspaceScan(
                            entries=entries_out,
                            scanned_path_count=scanned_path_count,
                            max_scanned_paths=max_scanned_paths,
                            scan_truncated=True,
                        )
                    scanned_path_count += 1
                    path = Path(entry.path)
                    is_ignored = entry.name in ignored
                    entries_out.append(_WorkspacePathEntry(path=path, ignored=is_ignored))
                    if is_ignored:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending_dirs.append(path)
                    except OSError:
                        continue
        except OSError:
            continue
    return _WorkspaceScan(
        entries=entries_out,
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=False,
    )


def _append_unique(ordered: list[Path], seen: set[Path], candidate: Path) -> None:
    try:
        resolved = candidate.resolve()
    except OSError:
        return
    if resolved in seen:
        return
    seen.add(resolved)
    ordered.append(resolved)


def _render_context_files(files: list[ProjectContextFile]) -> str:
    lines = [
        "Project context files discovered from the active workspace. Treat these as durable project instructions "
        "unless higher-priority system, developer, user, or scoped AGENTS.md instructions contradict them. "
        "Each context_file declares the virtual subtree it applies_to; deeper scopes override broader scopes for files under that subtree. "
        "Inspect the source file directly before editing it.",
    ]
    for item in files:
        truncated = "true" if item.truncated else "false"
        lines.extend(
            [
                "",
                (
                    f'<context_file path="{item.virtual_path}" relative_path="{item.relative_path}" '
                    f'applies_to="{item.applies_to}" scope="{item.scope}" truncated="{truncated}">'
                ),
                item.content.rstrip(),
                "</context_file>",
            ]
        )
    return "\n".join(lines).strip()


def _fingerprint_context_files(files: list[ProjectContextFile]) -> str:
    payload = [
        {
            "virtual_path": item.virtual_path,
            "relative_path": item.relative_path,
            "applies_to": item.applies_to,
            "scope": item.scope,
            "content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
            "truncated": item.truncated,
        }
        for item in files
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_virtual_path(path_service: PathService, thread_id: str, path: Path) -> str:
    try:
        return path_service.to_virtual_path(thread_id, path)
    except Exception:
        return path.as_posix()


def _context_applies_to(path_service: PathService, thread_id: str, root: Path, path: Path) -> str:
    scope = _context_scope(root, path)
    if scope == ".":
        return "/mnt/user-data/workspace"
    scope_path = root.resolve() / scope
    return _safe_virtual_path(path_service, thread_id, scope_path)


def _context_scope(root: Path, path: Path) -> str:
    try:
        relative_parent = path.parent.resolve().relative_to(root.resolve())
    except ValueError:
        return "."
    scope = relative_parent.as_posix()
    return scope if scope else "."


def _sanitize_prompt_text(text: str) -> str:
    sanitized = text.replace("\x00", "")
    for tag in _PROMPT_SECTION_TAGS:
        sanitized = sanitized.replace(f"<{tag}", f"[{tag}")
        sanitized = sanitized.replace(f"</{tag}>", f"[/{tag}]")
    return sanitized


def _is_probably_binary(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:2048]
    except OSError:
        return True
    return b"\x00" in sample
