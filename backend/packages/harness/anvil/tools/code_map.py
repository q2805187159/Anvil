from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import threading
import time
from typing import TYPE_CHECKING, Any

from .code_semantics import (
    CodeSemanticService,
    ExternalIndexCodeSemanticBackend,
    LspJsonRpcCodeSemanticBackend,
    StaticCodeSemanticBackend,
    find_focus_node,
    lsp_session_pool_health,
    lsp_session_pool_recover,
    lsp_workspace_probe,
    summarize_external_index_payload,
    symbol_reference_pattern,
)

if TYPE_CHECKING:
    from anvil.sandbox.path_service import PathService


_CODE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".cs",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".html",
    ".htm",
    ".vue",
    ".svelte",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
}
_MARKDOWN_EXTENSIONS = {".md", ".mdx", ".markdown"}
_ANALYZED_EXTENSIONS = _CODE_EXTENSIONS | _MARKDOWN_EXTENSIONS
_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".anvil-scratch",
}
DEFAULT_CODE_ANALYSIS_SCAN_PATH_LIMIT = 10_000
MAX_CODE_ANALYSIS_SCAN_PATH_LIMIT = 100_000
_TS_IMPORT_RE = re.compile(
    r"""(?mx)
    (?:import\s+(?:type\s+)?(?:[^'"]+\s+from\s+)?|export\s+(?:type\s+)?[^'"]*\s+from\s+|require\s*\()\s*
    ['"](?P<target>[^'"]+)['"]
    """
)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?P<target>[^)\s][^)]*)\)")
_WIKI_LINK_RE = re.compile(r"\[\[(?P<target>[^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$", re.MULTILINE)
_KNOWN_SECRET_RES = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(?P<name>api[_-]?key|secret|token|password|private[_-]?key|access[_-]?key)\b\s*[:=]\s*['\"]?(?P<value>[A-Za-z0-9_./+=:-]{16,})"
)
_SECURITY_RULES: tuple[dict[str, Any], ...] = (
    {
        "kind": "hardcoded_secret",
        "severity": "critical",
        "message": "Possible hardcoded credential or token.",
        "patterns": (*_KNOWN_SECRET_RES, _SECRET_ASSIGN_RE),
    },
    {
        "kind": "dangerous_eval",
        "severity": "high",
        "message": "Dynamic code execution detected.",
        "patterns": (
            re.compile(r"\beval\s*\("),
            re.compile(r"\bexec\s*\("),
            re.compile(r"\bFunction\s*\("),
        ),
    },
    {
        "kind": "sql_injection_risk",
        "severity": "high",
        "message": "String-built SQL execution pattern detected.",
        "patterns": (
            re.compile(r"\.execute\s*\(\s*f['\"]", re.IGNORECASE),
            re.compile(r"\.execute\s*\([^)]*\+", re.IGNORECASE),
            re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.+(%\s*\(|\.format\s*\(|\+)", re.IGNORECASE),
        ),
    },
    {
        "kind": "debug_statement",
        "severity": "low",
        "message": "Debug output or breakpoint statement detected.",
        "patterns": (
            re.compile(r"\bconsole\.log\s*\("),
            re.compile(r"\bdebugger\s*;"),
            re.compile(r"\bpdb\.set_trace\s*\("),
            re.compile(r"\bbreakpoint\s*\("),
            re.compile(r"\bprint\s*\("),
        ),
    },
)
_PATTERN_RULES: tuple[dict[str, Any], ...] = (
    {
        "kind": "factory_pattern",
        "pattern": re.compile(r"\b(class\s+\w*Factory|def\s+create_\w+|function\s+create[A-Z]\w+|const\s+create[A-Z]\w+)", re.MULTILINE),
    },
    {
        "kind": "singleton_pattern",
        "pattern": re.compile(r"\b(Singleton|getInstance|_instance|instance\s*=\s*None)\b"),
    },
    {
        "kind": "observer_event_pattern",
        "pattern": re.compile(r"\b(EventEmitter|addEventListener|dispatchEvent|emit\s*\(|on\s*\(\s*['\"])", re.MULTILINE),
    },
    {
        "kind": "react_custom_hook",
        "pattern": re.compile(r"\b(function\s+use[A-Z]\w+|const\s+use[A-Z]\w+\s*=)", re.MULTILINE),
        "suffixes": {".ts", ".tsx", ".js", ".jsx"},
    },
)
_MANIFEST_NAMES = {
    "package.json",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
}


@dataclass(frozen=True)
class _CodeAnalysisFileScan:
    files: list[Path]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool


@dataclass(frozen=True)
class CodeMapCacheEntry:
    fingerprint: str
    payload: dict[str, Any]


class CodeMapCache:
    def __init__(self) -> None:
        self._entries: dict[tuple[Any, ...], CodeMapCacheEntry] = {}

    def get(self, key: tuple[Any, ...], fingerprint: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None or entry.fingerprint != fingerprint:
            return None
        payload = dict(entry.payload)
        payload["cache"] = "hit"
        return payload

    def set(self, key: tuple[Any, ...], fingerprint: str, payload: dict[str, Any]) -> dict[str, Any]:
        stored = dict(payload)
        stored["cache"] = "miss"
        self._entries[key] = CodeMapCacheEntry(fingerprint=fingerprint, payload=stored)
        return stored


_CACHE = CodeMapCache()


@dataclass(frozen=True)
class CodeSemanticWatchEntry:
    key: tuple[Any, ...]
    snapshot: dict[str, Any]
    created_at: float
    last_polled_at: float
    poll_count: int = 0
    last_change_at: float | None = None


class CodeSemanticWatchStore:
    def __init__(self) -> None:
        self._entries: dict[tuple[Any, ...], CodeSemanticWatchEntry] = {}
        self._lock = threading.Lock()

    def start(self, *, key: tuple[Any, ...], snapshot: dict[str, Any], max_entries: int, ttl_seconds: float) -> CodeSemanticWatchEntry:
        now = time.time()
        with self._lock:
            self._prune_locked(now=now, max_entries=max_entries, ttl_seconds=ttl_seconds)
            entry = CodeSemanticWatchEntry(key=key, snapshot=dict(snapshot), created_at=now, last_polled_at=now)
            self._entries[key] = entry
            self._prune_locked(now=now, max_entries=max_entries, ttl_seconds=ttl_seconds)
            return entry

    def get(self, *, key: tuple[Any, ...], max_entries: int, ttl_seconds: float) -> CodeSemanticWatchEntry | None:
        with self._lock:
            self._prune_locked(now=time.time(), max_entries=max_entries, ttl_seconds=ttl_seconds)
            return self._entries.get(key)

    def update(
        self,
        *,
        key: tuple[Any, ...],
        snapshot: dict[str, Any],
        changed: bool,
        max_entries: int,
        ttl_seconds: float,
    ) -> CodeSemanticWatchEntry:
        now = time.time()
        with self._lock:
            self._prune_locked(now=now, max_entries=max_entries, ttl_seconds=ttl_seconds)
            current = self._entries.get(key)
            if current is None:
                entry = CodeSemanticWatchEntry(key=key, snapshot=dict(snapshot), created_at=now, last_polled_at=now)
                self._entries[key] = entry
                self._prune_locked(now=now, max_entries=max_entries, ttl_seconds=ttl_seconds)
                return entry
            entry = CodeSemanticWatchEntry(
                key=key,
                snapshot=dict(snapshot),
                created_at=current.created_at,
                last_polled_at=now,
                poll_count=current.poll_count + 1,
                last_change_at=now if changed else current.last_change_at,
            )
            self._entries[key] = entry
            return entry

    def clear(self, *, key: tuple[Any, ...]) -> bool:
        with self._lock:
            return self._entries.pop(key, None) is not None

    def _prune_locked(self, *, now: float, max_entries: int, ttl_seconds: float) -> None:
        if ttl_seconds > 0:
            expired = [
                key
                for key, entry in self._entries.items()
                if now - entry.last_polled_at > ttl_seconds
            ]
            for key in expired:
                self._entries.pop(key, None)
        overflow = len(self._entries) - max_entries
        if overflow <= 0:
            return
        for key, _entry in sorted(self._entries.items(), key=lambda item: item[1].last_polled_at)[:overflow]:
            self._entries.pop(key, None)


_SEMANTIC_WATCHES = CodeSemanticWatchStore()


def _semantic_service(config: Any | None = None) -> CodeSemanticService:
    static_backend = StaticCodeSemanticBackend(_analyze_codebase)
    backend = getattr(config, "backend", "static") if config is not None else "static"
    if backend == "external_index" and getattr(config, "external_index_path", None):
        return CodeSemanticService(
            ExternalIndexCodeSemanticBackend(
                index_path=getattr(config, "external_index_path"),
                fallback=static_backend if getattr(config, "fallback_to_static", True) else None,
                fingerprint_probe=_semantic_fingerprint,
                validate_freshness=getattr(config, "validate_freshness", True),
            )
        )
    if backend == "lsp_jsonrpc":
        return CodeSemanticService(
            LspJsonRpcCodeSemanticBackend(
                command=list(getattr(config, "lsp_command", []) or []),
                cwd=getattr(config, "lsp_cwd", None),
                env=dict(getattr(config, "lsp_env", {}) or {}),
                timeout_seconds=float(getattr(config, "lsp_timeout_seconds", 8.0) or 8.0),
                session_idle_ttl_seconds=float(getattr(config, "lsp_session_idle_ttl_seconds", 300.0) or 0.0),
                stderr_max_chars=int(getattr(config, "lsp_stderr_max_chars", 2000) or 0),
                initialization_options=dict(getattr(config, "lsp_initialization_options", {}) or {}),
                fallback=static_backend if getattr(config, "fallback_to_static", True) else None,
            )
        )
    return CodeSemanticService(static_backend)


def _semantic_fingerprint(
    *,
    path_service: PathService,
    thread_id: str,
    path: str,
    max_files: int,
) -> str:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=False,
        include_security=False,
        include_patterns=False,
    )
    return str(analysis["fingerprint"])


def build_code_map(
    *,
    path_service: PathService,
    thread_id: str,
    path: str = "/mnt/user-data/workspace",
    focus: str | None = None,
    max_files: int = 300,
    include_symbols: bool = False,
    max_edges: int = 1000,
) -> dict[str, Any]:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=False,
        include_security=False,
        include_patterns=False,
    )
    edges = analysis["edges"][:max_edges]
    payload = {
        "root": path,
        "fingerprint": analysis["fingerprint"],
        "cache": analysis["cache"],
        "file_count": analysis["file_count"],
        "edge_count": len(analysis["edges"]),
        "edges_truncated": len(analysis["edges"]) > len(edges),
        "nodes": [_compact_node(node, include_symbols=include_symbols) for node in analysis["nodes"]],
        "edges": edges,
        "stats": _compact_stats(analysis["stats"]),
        "focus": _focus_payload(analysis["nodes"], analysis["edges"], focus=focus),
    }
    return payload


def build_code_focus(
    *,
    path_service: PathService,
    thread_id: str,
    focus: str,
    path: str = "/mnt/user-data/workspace",
    max_files: int = 300,
    depth: int = 1,
) -> dict[str, Any]:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=False,
        include_security=True,
        include_patterns=True,
    )
    focus_payload = _focus_payload(analysis["nodes"], analysis["edges"], focus=focus)
    if not focus_payload or not focus_payload.get("matched"):
        return {
            "root": path,
            "fingerprint": analysis["fingerprint"],
            "cache": analysis["cache"],
            "focus": focus_payload,
        }
    related_paths = _related_paths(
        path=str(focus_payload["path"]),
        edges=analysis["edges"],
        depth=max(1, min(depth, 3)),
    )
    nodes_by_path = {str(node["path"]): node for node in analysis["nodes"]}
    related_nodes = [
        _focus_node(nodes_by_path[item])
        for item in sorted(related_paths)
        if item in nodes_by_path and item != focus_payload["path"]
    ]
    return {
        "root": path,
        "fingerprint": analysis["fingerprint"],
        "cache": analysis["cache"],
        "focus": focus_payload,
        "related_files": related_nodes[:100],
        "related_files_truncated": len(related_nodes) > 100,
    }


def build_code_symbols(
    *,
    path_service: PathService,
    thread_id: str,
    focus: str,
    path: str = "/mnt/user-data/workspace",
    max_files: int = 300,
    limit: int = 120,
    code_semantics_config: Any | None = None,
) -> dict[str, Any]:
    return _semantic_service(code_semantics_config).symbols_for_file(
        path_service=path_service,
        thread_id=thread_id,
        focus=focus,
        path=path,
        max_files=max_files,
        limit=limit,
    )


def build_code_symbol_search(
    *,
    path_service: PathService,
    thread_id: str,
    query: str,
    path: str = "/mnt/user-data/workspace",
    max_files: int = 300,
    limit: int = 80,
    kind: str | None = None,
    code_semantics_config: Any | None = None,
) -> dict[str, Any]:
    return _semantic_service(code_semantics_config).search_symbols(
        path_service=path_service,
        thread_id=thread_id,
        query=query,
        path=path,
        max_files=max_files,
        limit=limit,
        kind=kind,
    )


def build_code_references(
    *,
    path_service: PathService,
    thread_id: str,
    symbol_name: str,
    path: str = "/mnt/user-data/workspace",
    file_path: str | None = None,
    max_files: int = 300,
    limit: int = 100,
    context: int = 1,
    code_semantics_config: Any | None = None,
) -> dict[str, Any]:
    return _semantic_service(code_semantics_config).find_references(
        path_service=path_service,
        thread_id=thread_id,
        symbol_name=symbol_name,
        path=path,
        file_path=file_path,
        max_files=max_files,
        limit=limit,
        context=context,
    )


def build_code_definition(
    *,
    path_service: PathService,
    thread_id: str,
    symbol_name: str,
    path: str = "/mnt/user-data/workspace",
    file_path: str | None = None,
    max_files: int = 300,
    limit: int = 20,
    context: int = 1,
    code_semantics_config: Any | None = None,
) -> dict[str, Any]:
    return _semantic_service(code_semantics_config).find_definitions(
        path_service=path_service,
        thread_id=thread_id,
        symbol_name=symbol_name,
        path=path,
        file_path=file_path,
        max_files=max_files,
        limit=limit,
        context=context,
    )


def build_code_semantic_index(
    *,
    path_service: PathService,
    thread_id: str,
    path: str = "/mnt/user-data/workspace",
    output_path: str = "/mnt/user-data/outputs/code-semantic-index.json",
    max_files: int = 300,
    mode: str = "write",
    watch_action: str = "poll",
    auto_recover: bool | None = None,
    code_semantics_config: Any | None = None,
) -> dict[str, Any]:
    normalized_mode = str(mode or "write").strip().lower()
    if normalized_mode not in {"write", "validate", "refresh", "recover", "health", "watch"}:
        raise ValueError("mode must be 'write', 'validate', 'refresh', 'recover', 'health', or 'watch'")
    if normalized_mode == "health":
        return _semantic_backend_health(
            config=code_semantics_config,
            path=path,
            max_files=max_files,
            output_path=output_path,
            path_service=path_service,
            thread_id=thread_id,
        )
    if normalized_mode == "recover":
        return _semantic_backend_recover(
            config=code_semantics_config,
            path=path,
            output_path=output_path,
            max_files=max_files,
            path_service=path_service,
            thread_id=thread_id,
        )
    if normalized_mode == "watch":
        return _semantic_backend_watch(
            config=code_semantics_config,
            path=path,
            output_path=output_path,
            max_files=max_files,
            watch_action=watch_action,
            auto_recover=auto_recover,
            path_service=path_service,
            thread_id=thread_id,
        )
    if normalized_mode == "validate":
        try:
            resolved_output = path_service.resolve_virtual_path(thread_id, output_path)
            payload = json.loads(resolved_output.read_text(encoding="utf-8"))
            current_analysis = _analyze_codebase(
                path_service=path_service,
                thread_id=thread_id,
                path=path,
                max_files=max_files,
                include_markdown=False,
                include_security=False,
                include_patterns=False,
            )
            current_payload = _semantic_index_payload(current_analysis)
            summary = summarize_external_index_payload(
                payload,
                root=path,
                current_fingerprint=str(current_analysis["fingerprint"]),
                current_payload=current_payload,
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return {
                "mode": "validate",
                "path": output_path,
                "valid": False,
                "errors": [_code_index_error(exc, raw_path=output_path, resolved_path=locals().get("resolved_output"))],
                "errors_truncated": False,
            }
        return {
            "mode": "validate",
            "path": output_path,
            **summary,
        }

    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=False,
        include_security=False,
        include_patterns=False,
    )
    payload = _semantic_index_payload(analysis)
    summary = summarize_external_index_payload(
        payload,
        root=path,
        current_fingerprint=str(analysis["fingerprint"]),
        current_payload=payload,
    )
    resolved_output = path_service.resolve_virtual_path(thread_id, output_path)
    if normalized_mode == "refresh":
        previous_summary: dict[str, Any] | None = None
        try:
            existing_payload = json.loads(resolved_output.read_text(encoding="utf-8"))
            previous_summary = summarize_external_index_payload(
                existing_payload,
                root=path,
                current_fingerprint=str(analysis["fingerprint"]),
                current_payload=payload,
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            previous_summary = {
                "valid": False,
                "fresh": False,
                "freshness": "unavailable",
                "errors": [_code_index_error(exc, raw_path=output_path, resolved_path=resolved_output)],
                "errors_truncated": False,
            }
        if previous_summary.get("valid") is True and previous_summary.get("fresh") is True:
            return {
                "mode": "refresh",
                "action": "kept",
                "path": output_path,
                "cache": analysis["cache"],
                **previous_summary,
            }
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        resolved_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return {
            "mode": "refresh",
            "action": "written" if previous_summary.get("freshness") == "unavailable" else "rewritten",
            "path": output_path,
            "cache": analysis["cache"],
            "previous_valid": previous_summary.get("valid"),
            "previous_fresh": previous_summary.get("fresh"),
            "previous_freshness": previous_summary.get("freshness"),
            "previous_errors": list(previous_summary.get("errors") or [])[:5],
            "previous_drift": previous_summary.get("drift"),
            **summary,
        }
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "mode": "write",
        "path": output_path,
        "cache": analysis["cache"],
        **summary,
    }


def _semantic_backend_health(
    *,
    config: Any | None,
    path: str,
    max_files: int,
    output_path: str,
    path_service: PathService,
    thread_id: str,
) -> dict[str, Any]:
    backend = str(getattr(config, "backend", "static") if config is not None else "static")
    payload: dict[str, Any] = {
        "mode": "health",
        "path": path,
        "max_files": max_files,
        "backend": backend,
        "fallback_to_static": bool(getattr(config, "fallback_to_static", True) if config is not None else True),
        "validate_freshness": bool(getattr(config, "validate_freshness", True) if config is not None else True),
    }
    if backend == "external_index":
        configured_path = str(getattr(config, "external_index_path", "") or "")
        path_kind = "virtual" if configured_path.startswith("/mnt/") else "host" if configured_path else "unset"
        payload["external_index"] = {
            "configured": bool(configured_path),
            "path_configured": bool(configured_path),
            "path_kind": path_kind,
        }
        if path_kind == "virtual":
            payload["external_index"]["path"] = configured_path
        if configured_path:
            payload["external_index"].update(
                _external_index_health_summary(
                    index_path=configured_path,
                    path_service=path_service,
                    thread_id=thread_id,
                    path=path,
                    max_files=max_files,
                )
            )
        else:
            payload["external_index"]["recommendation"] = "Configure code_semantics.external_index_path or use mode=refresh to write a governed index."
    elif backend == "lsp_jsonrpc":
        idle_ttl = float(getattr(config, "lsp_session_idle_ttl_seconds", 300.0) or 0.0)
        command = list(getattr(config, "lsp_command", []) or []) if config is not None else []
        pool_health_kwargs: dict[str, Any] = {"idle_ttl_seconds": idle_ttl}
        try:
            probe = lsp_workspace_probe(
                path_service=path_service,
                thread_id=thread_id,
                path=path,
                max_files=max_files,
            )
            pool_health_kwargs.update(
                {
                    "current_workspace_root": probe.root,
                    "current_workspace_snapshot": probe.snapshot,
                }
            )
            workspace_probe: dict[str, Any] = {
                "available": True,
                "file_count": probe.snapshot.file_count,
                "scanned_path_count": probe.snapshot.scanned_path_count,
                "max_scanned_paths": probe.snapshot.max_scanned_paths,
                "scan_truncated": probe.snapshot.scan_truncated,
                "fingerprint_hash": hashlib.sha256(probe.snapshot.fingerprint.encode("utf-8")).hexdigest()[:16],
            }
        except (OSError, ValueError) as exc:
            workspace_probe = {
                "available": False,
                "error": _code_index_error(exc, raw_path=path),
            }
        payload["lsp_jsonrpc"] = {
            "configured": bool(command),
            "command_configured": bool(command),
            "command_size": len(command),
            "cwd_configured": bool(getattr(config, "lsp_cwd", None) if config is not None else None),
            "env_keys_count": len(getattr(config, "lsp_env", {}) or {}) if config is not None else 0,
            "timeout_seconds": float(getattr(config, "lsp_timeout_seconds", 8.0) or 8.0) if config is not None else 8.0,
            "session_idle_ttl_seconds": idle_ttl,
            "stderr_max_chars": int(getattr(config, "lsp_stderr_max_chars", 2000) or 0) if config is not None else 2000,
            "initialization_options_keys": sorted(str(key) for key in (getattr(config, "lsp_initialization_options", {}) or {}).keys()) if config is not None else [],
            "workspace_probe": workspace_probe,
            "pool": lsp_session_pool_health(**pool_health_kwargs),
        }
    return payload


def _semantic_backend_recover(
    *,
    config: Any | None,
    path: str,
    output_path: str,
    max_files: int,
    path_service: PathService,
    thread_id: str,
) -> dict[str, Any]:
    backend = str(getattr(config, "backend", "static") if config is not None else "static")
    if backend == "external_index":
        configured_path = str(getattr(config, "external_index_path", "") or "") or output_path
        return build_code_semantic_index(
            path_service=path_service,
            thread_id=thread_id,
            path=path,
            output_path=configured_path,
            max_files=max_files,
            mode="refresh",
            code_semantics_config=config,
        ) | {
            "mode": "recover",
            "backend": backend,
            "recovery": "external_index_refresh",
        }
    if backend == "lsp_jsonrpc":
        idle_ttl = float(getattr(config, "lsp_session_idle_ttl_seconds", 300.0) or 0.0)
        command = list(getattr(config, "lsp_command", []) or []) if config is not None else []
        try:
            probe = lsp_workspace_probe(
                path_service=path_service,
                thread_id=thread_id,
                path=path,
                max_files=max_files,
            )
        except (OSError, ValueError) as exc:
            return {
                "mode": "recover",
                "backend": backend,
                "configured": bool(command),
                "recovery": "lsp_session_recover",
                "recovered": False,
                "errors": [_code_index_error(exc, raw_path=path)],
            }
        recovery = lsp_session_pool_recover(
            idle_ttl_seconds=idle_ttl,
            current_workspace_root=probe.root,
            current_workspace_snapshot=probe.snapshot,
        )
        return {
            "mode": "recover",
            "backend": backend,
            "configured": bool(command),
            "recovery": "lsp_session_recover",
            "workspace_probe": {
                "available": True,
                "file_count": probe.snapshot.file_count,
                "scanned_path_count": probe.snapshot.scanned_path_count,
                "max_scanned_paths": probe.snapshot.max_scanned_paths,
                "scan_truncated": probe.snapshot.scan_truncated,
                "fingerprint_hash": hashlib.sha256(probe.snapshot.fingerprint.encode("utf-8")).hexdigest()[:16],
            },
            "recovered": int(recovery.get("recovered_session_count") or 0) > 0,
            **recovery,
            "post_recovery": lsp_session_pool_health(
                idle_ttl_seconds=idle_ttl,
                current_workspace_root=probe.root,
                current_workspace_snapshot=probe.snapshot,
            ),
        }
    return {
        "mode": "recover",
        "backend": backend,
        "recovery": "noop",
        "recovered": False,
        "recommendation": "No semantic cache recovery is needed for the static backend.",
    }


def _semantic_backend_watch(
    *,
    config: Any | None,
    path: str,
    output_path: str,
    max_files: int,
    watch_action: str,
    auto_recover: bool | None,
    path_service: PathService,
    thread_id: str,
) -> dict[str, Any]:
    normalized_action = str(watch_action or "poll").strip().lower()
    if normalized_action not in {"start", "poll", "stop", "status"}:
        raise ValueError("watch_action must be 'start', 'poll', 'stop', or 'status'")
    backend = str(getattr(config, "backend", "static") if config is not None else "static")
    max_entries = int(getattr(config, "watch_max_entries", 128) if config is not None else 128)
    ttl_seconds = float(getattr(config, "watch_state_ttl_seconds", 3600.0) if config is not None else 3600.0)
    drift_path_limit = int(getattr(config, "watch_drift_path_limit", 20) if config is not None else 20)
    effective_auto_recover = (
        bool(getattr(config, "watch_default_auto_recover", True) if config is not None else True)
        if auto_recover is None
        else bool(auto_recover)
    )
    key = _semantic_watch_key(
        thread_id=thread_id,
        path=path,
        output_path=output_path,
        max_files=max_files,
        backend=backend,
    )
    if normalized_action == "stop":
        cleared = _SEMANTIC_WATCHES.clear(key=key)
        return {
            "mode": "watch",
            "watch_action": "stop",
            "backend": backend,
            "path": path,
            "watching": False,
            "cleared": cleared,
        }

    current = _semantic_watch_snapshot(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
    )
    previous = _SEMANTIC_WATCHES.get(key=key, max_entries=max_entries, ttl_seconds=ttl_seconds)
    if normalized_action == "status":
        return {
            "mode": "watch",
            "watch_action": "status",
            "backend": backend,
            "path": path,
            "watching": previous is not None,
            "current": _semantic_watch_snapshot_view(current),
            "baseline": _semantic_watch_snapshot_view(previous.snapshot) if previous is not None else None,
            "poll_count": previous.poll_count if previous is not None else 0,
            "last_polled_at": _iso_time(previous.last_polled_at) if previous is not None else None,
            "last_change_at": _iso_time(previous.last_change_at) if previous is not None and previous.last_change_at else None,
        }

    if previous is None or normalized_action == "start":
        entry = _SEMANTIC_WATCHES.start(
            key=key,
            snapshot=current,
            max_entries=max_entries,
            ttl_seconds=ttl_seconds,
        )
        return {
            "mode": "watch",
            "watch_action": "start",
            "backend": backend,
            "path": path,
            "watching": True,
            "changed": False,
            "baseline": _semantic_watch_snapshot_view(entry.snapshot),
            "current": _semantic_watch_snapshot_view(current),
            "poll_count": entry.poll_count,
            "recommendation": "Poll this watch after edits; Anvil will report bounded path drift and can recover semantic caches.",
        }

    drift = _semantic_watch_drift(previous.snapshot, current, limit=drift_path_limit)
    changed = bool(drift["changed"])
    recovery: dict[str, Any] | None = None
    if changed and effective_auto_recover and backend in {"external_index", "lsp_jsonrpc"}:
        recovery = _semantic_backend_recover(
            config=config,
            path=path,
            output_path=output_path,
            max_files=max_files,
            path_service=path_service,
            thread_id=thread_id,
        )
    entry = _SEMANTIC_WATCHES.update(
        key=key,
        snapshot=current,
        changed=changed,
        max_entries=max_entries,
        ttl_seconds=ttl_seconds,
    )
    recommendation = "No semantic workspace changes detected."
    if changed and recovery is None:
        recommendation = "Workspace changed; run code_semantic_index mode=recover before relying on stale external or LSP semantic state."
    elif recovery is not None:
        recommendation = "Workspace changed; semantic backend recovery was triggered."
    return {
        "mode": "watch",
        "watch_action": "poll",
        "backend": backend,
        "path": path,
        "watching": True,
        "changed": changed,
        "drift": drift,
        "baseline": _semantic_watch_snapshot_view(previous.snapshot),
        "current": _semantic_watch_snapshot_view(current),
        "poll_count": entry.poll_count,
        "last_polled_at": _iso_time(entry.last_polled_at),
        "last_change_at": _iso_time(entry.last_change_at) if entry.last_change_at else None,
        "auto_recover": effective_auto_recover,
        "recovery": recovery,
        "recommendation": recommendation,
    }


def _semantic_watch_snapshot(
    *,
    path_service: PathService,
    thread_id: str,
    path: str,
    max_files: int,
) -> dict[str, Any]:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=False,
        include_security=False,
        include_patterns=False,
    )
    nodes = analysis.get("nodes") if isinstance(analysis.get("nodes"), list) else []
    files = [
        {
            "relative_path": str(node.get("relative_path") or ""),
            "symbol_names": sorted(str(symbol.get("name") or "") for symbol in (node.get("symbols") or []) if symbol.get("name")),
            "imports": sorted(str(item) for item in (node.get("imports") or [])),
            "line_count": int(node.get("line_count") or 0),
            "size_bytes": int(node.get("size_bytes") or 0),
            "content_hash": _semantic_watch_file_hash(
                path_service=path_service,
                thread_id=thread_id,
                virtual_path=str(node.get("path") or ""),
            ),
        }
        for node in nodes
        if isinstance(node, dict)
    ]
    return {
        "root": path,
        "fingerprint": str(analysis["fingerprint"]),
        "file_count": int(analysis.get("file_count") or len(files)),
        "symbol_count": sum(len(item["symbol_names"]) for item in files),
        "edge_count": len(analysis.get("edges") or []),
        "scanned_path_count": analysis.get("scan", {}).get("scanned_path_count", 0),
        "max_scanned_paths": analysis.get("scan", {}).get("max_scanned_paths", 0),
        "scan_truncated": bool(analysis.get("scan", {}).get("scan_truncated")),
        "cache": str(analysis.get("cache") or "unknown"),
        "files": files,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _semantic_watch_snapshot_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "root": snapshot.get("root"),
        "fingerprint": snapshot.get("fingerprint"),
        "file_count": snapshot.get("file_count"),
        "symbol_count": snapshot.get("symbol_count"),
        "edge_count": snapshot.get("edge_count"),
        "scanned_path_count": snapshot.get("scanned_path_count"),
        "max_scanned_paths": snapshot.get("max_scanned_paths"),
        "scan_truncated": snapshot.get("scan_truncated"),
        "cache": snapshot.get("cache"),
        "generated_at": snapshot.get("generated_at"),
    }


def _semantic_watch_drift(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    limit: int,
) -> dict[str, Any]:
    previous_files = {
        str(item.get("relative_path") or ""): item
        for item in (previous.get("files") or [])
        if isinstance(item, dict) and item.get("relative_path")
    }
    current_files = {
        str(item.get("relative_path") or ""): item
        for item in (current.get("files") or [])
        if isinstance(item, dict) and item.get("relative_path")
    }
    added_paths = sorted(path for path in current_files if path not in previous_files)
    removed_paths = sorted(path for path in previous_files if path not in current_files)
    changed_paths: list[str] = []
    changed_details: list[dict[str, Any]] = []
    for path_name in sorted(set(previous_files) & set(current_files)):
        before = previous_files[path_name]
        after = current_files[path_name]
        reasons: list[str] = []
        if before.get("symbol_names") != after.get("symbol_names"):
            reasons.append("symbols")
        if before.get("imports") != after.get("imports"):
            reasons.append("imports")
        if before.get("line_count") != after.get("line_count"):
            reasons.append("line_count")
        if before.get("size_bytes") != after.get("size_bytes"):
            reasons.append("size_bytes")
        if before.get("content_hash") != after.get("content_hash"):
            reasons.append("content")
        if not reasons:
            continue
        changed_paths.append(path_name)
        changed_details.append(
            {
                "path": path_name,
                "reasons": reasons,
                "symbol_delta": {
                    "added": sorted(set(after.get("symbol_names") or []) - set(before.get("symbol_names") or []))[:limit],
                    "removed": sorted(set(before.get("symbol_names") or []) - set(after.get("symbol_names") or []))[:limit],
                },
            }
        )
    bounded_limit = max(1, limit)
    changed = (
        previous.get("fingerprint") != current.get("fingerprint")
        or bool(added_paths)
        or bool(removed_paths)
        or bool(changed_paths)
    )
    return {
        "changed": changed,
        "previous_fingerprint": previous.get("fingerprint"),
        "current_fingerprint": current.get("fingerprint"),
        "added_paths": added_paths[:bounded_limit],
        "removed_paths": removed_paths[:bounded_limit],
        "changed_paths": changed_paths[:bounded_limit],
        "changed_details": changed_details[:bounded_limit],
        "added_count": len(added_paths),
        "removed_count": len(removed_paths),
        "changed_count": len(changed_paths),
        "truncated": len(added_paths) > bounded_limit or len(removed_paths) > bounded_limit or len(changed_paths) > bounded_limit,
    }


def _semantic_watch_key(
    *,
    thread_id: str,
    path: str,
    output_path: str,
    max_files: int,
    backend: str,
) -> tuple[Any, ...]:
    return ("semantic-watch", thread_id, backend, path, output_path, int(max_files))


def _semantic_watch_file_hash(
    *,
    path_service: PathService,
    thread_id: str,
    virtual_path: str,
) -> str:
    try:
        file_path = path_service.resolve_virtual_path(thread_id, virtual_path)
        return hashlib.sha256(file_path.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return ""


def _iso_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _external_index_health_summary(
    *,
    index_path: str,
    path_service: PathService,
    thread_id: str,
    path: str,
    max_files: int,
) -> dict[str, Any]:
    try:
        resolved_index = path_service.resolve_virtual_path(thread_id, index_path) if index_path.startswith("/mnt/") else Path(index_path).expanduser()
        payload = json.loads(resolved_index.read_text(encoding="utf-8"))
        current_analysis = _analyze_codebase(
            path_service=path_service,
            thread_id=thread_id,
            path=path,
            max_files=max_files,
            include_markdown=False,
            include_security=False,
            include_patterns=False,
        )
        current_payload = _semantic_index_payload(current_analysis)
        summary = summarize_external_index_payload(
            payload,
            root=path,
            current_fingerprint=str(current_analysis["fingerprint"]),
            current_payload=current_payload,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "available": False,
            "valid": False,
            "fresh": False,
            "freshness": "unavailable",
            "errors": [_code_index_error(exc, raw_path=index_path, resolved_path=locals().get("resolved_index"))],
            "recommendation": "Run code_semantic_index with mode=refresh to create a fresh governed semantic index.",
        }
    recommendation = "External semantic index is fresh."
    if summary.get("valid") is not True:
        recommendation = "Fix the external semantic index shape or regenerate it with mode=refresh."
    elif summary.get("fresh") is not True:
        recommendation = "Run code_semantic_index with mode=refresh before relying on external_index results."
    return {
        "available": True,
        "valid": summary.get("valid"),
        "fresh": summary.get("fresh"),
        "freshness": summary.get("freshness"),
        "node_count": summary.get("node_count"),
        "symbol_count": summary.get("symbol_count"),
        "edge_count": summary.get("edge_count"),
        "invalid_edge_count": summary.get("invalid_edge_count"),
        "errors": list(summary.get("errors") or [])[:5],
        "drift": summary.get("drift"),
        "fingerprint": summary.get("fingerprint"),
        "current_fingerprint": summary.get("current_fingerprint"),
        "recommendation": recommendation,
    }


def _code_index_error(exc: BaseException, *, raw_path: str, resolved_path: object | None = None) -> str:
    text = str(exc)
    for candidate in (raw_path, str(resolved_path or "")):
        if candidate:
            text = text.replace(candidate, "[PATH]")
    text = re.sub(r"(?i)([A-Z]:\\)[^\s)'\"]+", r"\1[PATH]", text)
    text = re.sub(r"(?<![\w.-])/(?:Users|home|mnt|tmp|var|private|workspace|opt)/[^\s)'\"]+", "/[PATH]", text)
    return text[:500] or type(exc).__name__


def build_code_file_summary(
    *,
    path_service: PathService,
    thread_id: str,
    file_path: str,
    path: str = "/mnt/user-data/workspace",
    max_files: int = 300,
    include_risk_notes: bool = True,
    symbol_limit: int = 60,
) -> dict[str, Any]:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=True,
        include_security=include_risk_notes,
        include_patterns=include_risk_notes,
    )
    node = _find_focus_node(analysis["nodes"], file_path)
    if node is None:
        return {
            "root": path,
            "fingerprint": analysis["fingerprint"],
            "cache": analysis["cache"],
            "query": file_path,
            "matched": False,
        }
    symbols = list(node.get("symbols") or [])
    bounded_symbol_limit = max(1, min(symbol_limit, 300))
    summary = {
        "root": path,
        "fingerprint": analysis["fingerprint"],
        "cache": analysis["cache"],
        "matched": True,
        "path": node["path"],
        "relative_path": node["relative_path"],
        "language": node["language"],
        "kind": node["kind"],
        "line_count": node["line_count"],
        "size_bytes": node["size_bytes"],
        "owners": node.get("owners") or [],
        "imports": list(node.get("imports") or [])[:80],
        "imports_total": len(node.get("imports") or []),
        "symbols": symbols[:bounded_symbol_limit],
        "symbols_total": len(symbols),
        "symbols_truncated": len(symbols) > bounded_symbol_limit,
    }
    if node.get("markdown"):
        markdown = node.get("markdown") or {}
        summary["markdown"] = {
            "headings": list(markdown.get("headings") or [])[:40],
            "links_total": len(markdown.get("links") or []),
        }
    if include_risk_notes:
        summary["security_findings"] = list(node.get("security_findings") or [])[:40]
        summary["patterns"] = list(node.get("patterns") or [])[:40]
    return summary


def build_code_impact(
    *,
    path_service: PathService,
    thread_id: str,
    target_path: str,
    symbol_name: str | None = None,
    path: str = "/mnt/user-data/workspace",
    max_files: int = 300,
    depth: int = 1,
    limit: int = 80,
) -> dict[str, Any]:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=True,
        include_security=True,
        include_patterns=True,
    )
    target = _find_focus_node(analysis["nodes"], target_path)
    if target is None:
        return {
            "root": path,
            "fingerprint": analysis["fingerprint"],
            "cache": analysis["cache"],
            "query": target_path,
            "matched": False,
        }

    bounded_depth = max(1, min(depth, 3))
    bounded_limit = max(1, min(limit, 500))
    target_virtual_path = str(target["path"])
    dependencies = [edge for edge in analysis["edges"] if edge["from"] == target_virtual_path]
    dependents = [edge for edge in analysis["edges"] if edge["to"] == target_virtual_path]
    related_paths = _related_paths(path=target_virtual_path, edges=analysis["edges"], depth=bounded_depth)
    nodes_by_path = {str(node["path"]): node for node in analysis["nodes"]}
    related_nodes = [
        _impact_node(nodes_by_path[item])
        for item in sorted(related_paths)
        if item in nodes_by_path and item != target_virtual_path
    ]

    normalized_symbol = symbol_name.strip() if symbol_name else ""
    symbol_matches = _symbol_matches(
        nodes=analysis["nodes"],
        symbol_name=normalized_symbol,
        target_path=target_virtual_path,
    )
    symbol_references = _symbol_reference_summaries(
        path_service=path_service,
        thread_id=thread_id,
        nodes=analysis["nodes"],
        symbol_name=normalized_symbol,
        limit=bounded_limit,
    )
    candidate_tests = _candidate_tests(
        nodes=analysis["nodes"],
        edges=analysis["edges"],
        target=target,
        symbol_references=symbol_references,
        symbol_name=normalized_symbol,
        limit=bounded_limit,
    )
    candidate_docs = _candidate_docs(
        nodes=analysis["nodes"],
        markdown_graph=analysis["markdown_graph"],
        target=target,
        symbol_name=normalized_symbol,
        limit=bounded_limit,
    )

    risk_notes = _impact_risk_notes(
        target=target,
        dependents=dependents,
        related_count=len(related_nodes),
        candidate_tests=candidate_tests,
        symbol_references=symbol_references,
        anti_patterns=analysis["anti_patterns"],
    )
    reference_files_total_estimate = _reference_total_estimate(symbol_references, "files_total_estimate")
    references_total_estimate = _reference_total_estimate(symbol_references, "references_total_estimate")
    return {
        "root": path,
        "fingerprint": analysis["fingerprint"],
        "cache": analysis["cache"],
        "matched": True,
        "target": _impact_node(target),
        "symbol_name": normalized_symbol or None,
        "impact": {
            "dependencies": [_compact_edge(edge, direction="dependency") for edge in dependencies[:bounded_limit]],
            "dependencies_total": len(dependencies),
            "dependencies_truncated": len(dependencies) > bounded_limit,
            "dependents": [_compact_edge(edge, direction="dependent") for edge in dependents[:bounded_limit]],
            "dependents_total": len(dependents),
            "dependents_truncated": len(dependents) > bounded_limit,
            "related_files": related_nodes[:bounded_limit],
            "related_files_total": len(related_nodes),
            "related_files_truncated": len(related_nodes) > bounded_limit,
        },
        "symbols": {
            "target_symbols": list(target.get("symbols") or [])[:bounded_limit],
            "target_symbols_total": len(target.get("symbols") or []),
            "matches": symbol_matches[:bounded_limit],
            "matches_total": len(symbol_matches),
            "matches_truncated": len(symbol_matches) > bounded_limit,
        },
        "references": {
            "files": symbol_references[:bounded_limit],
            "files_total": len(symbol_references),
            "files_total_estimate": reference_files_total_estimate,
            "references_total_estimate": references_total_estimate,
            "files_truncated": reference_files_total_estimate > min(len(symbol_references), bounded_limit),
        },
        "candidate_tests": candidate_tests[:bounded_limit],
        "candidate_tests_total": len(candidate_tests),
        "candidate_docs": candidate_docs[:bounded_limit],
        "candidate_docs_total": len(candidate_docs),
        "risk_notes": risk_notes,
        "suggested_next_tools": _impact_next_tools(
            target_path=target_path,
            symbol_name=normalized_symbol,
            has_candidate_tests=bool(candidate_tests),
            has_security_findings=bool(target.get("security_findings")),
        ),
    }


def _reference_total_estimate(items: list[dict[str, Any]], key: str) -> int:
    for item in items:
        value = item.get(key)
        if isinstance(value, int):
            return value
    return len(items)


def build_code_security_scan(
    *,
    path_service: PathService,
    thread_id: str,
    path: str = "/mnt/user-data/workspace",
    max_files: int = 300,
    severity: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=False,
        include_security=True,
        include_patterns=False,
    )
    findings = _all_security_findings(analysis["nodes"])
    normalized_severity = severity.lower().strip() if severity else None
    if normalized_severity:
        findings = [item for item in findings if item["severity"] == normalized_severity]
    bounded_limit = max(1, min(limit, 500))
    return {
        "root": path,
        "fingerprint": analysis["fingerprint"],
        "cache": analysis["cache"],
        "summary": _security_summary(analysis["nodes"]),
        "findings": findings[:bounded_limit],
        "total_findings": len(findings),
        "truncated": len(findings) > bounded_limit,
    }


def build_code_pattern_scan(
    *,
    path_service: PathService,
    thread_id: str,
    path: str = "/mnt/user-data/workspace",
    max_files: int = 300,
    include_anti_patterns: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=False,
        include_security=False,
        include_patterns=True,
    )
    patterns = _all_pattern_findings(analysis["nodes"])
    bounded_limit = max(1, min(limit, 500))
    return {
        "root": path,
        "fingerprint": analysis["fingerprint"],
        "cache": analysis["cache"],
        "summary": _pattern_summary(analysis["nodes"]),
        "patterns": patterns[:bounded_limit],
        "patterns_truncated": len(patterns) > bounded_limit,
        "anti_patterns": analysis["anti_patterns"][:bounded_limit] if include_anti_patterns else [],
        "anti_patterns_truncated": include_anti_patterns and len(analysis["anti_patterns"]) > bounded_limit,
    }


def build_code_doc_graph(
    *,
    path_service: PathService,
    thread_id: str,
    path: str = "/mnt/user-data/workspace",
    max_files: int = 300,
    include_headings: bool = True,
    limit: int = 300,
) -> dict[str, Any]:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=True,
        include_security=False,
        include_patterns=False,
    )
    graph = analysis["markdown_graph"]
    bounded_limit = max(1, min(limit, 1000))
    nodes = graph["nodes"]
    if include_headings:
        markdown_by_path = {str(node["path"]): node for node in analysis["nodes"] if node.get("kind") == "markdown"}
        nodes = [
            {
                **node,
                "headings": (markdown_by_path.get(str(node["path"]), {}).get("markdown") or {}).get("headings", [])[:20],
            }
            for node in nodes
        ]
    return {
        "root": path,
        "fingerprint": analysis["fingerprint"],
        "cache": analysis["cache"],
        "nodes": nodes[:bounded_limit],
        "edges": graph["edges"][:bounded_limit],
        "broken_links": graph["broken_links"][:bounded_limit],
        "total_nodes": len(graph["nodes"]),
        "total_edges": len(graph["edges"]),
        "total_broken_links": len(graph["broken_links"]),
        "truncated": any(len(graph[key]) > bounded_limit for key in ("nodes", "edges", "broken_links")),
    }


def build_code_health(
    *,
    path_service: PathService,
    thread_id: str,
    path: str = "/mnt/user-data/workspace",
    max_files: int = 300,
) -> dict[str, Any]:
    analysis = _analyze_codebase(
        path_service=path_service,
        thread_id=thread_id,
        path=path,
        max_files=max_files,
        include_markdown=True,
        include_security=True,
        include_patterns=True,
    )
    return {
        "root": path,
        "fingerprint": analysis["fingerprint"],
        "cache": analysis["cache"],
        "health": analysis["health"],
        "stats": analysis["stats"],
        "hotspots": analysis["hotspots"],
        "ownership": analysis["ownership"],
        "security": analysis["security"],
        "patterns": analysis["patterns"],
        "anti_pattern_count": len(analysis["anti_patterns"]),
        "top_anti_patterns": analysis["anti_patterns"][:20],
        "doc_broken_link_count": len(analysis["markdown_graph"]["broken_links"]),
    }


def _analyze_codebase(
    *,
    path_service: PathService,
    thread_id: str,
    path: str,
    max_files: int,
    include_markdown: bool,
    include_security: bool,
    include_patterns: bool,
) -> dict[str, Any]:
    root = path_service.resolve_virtual_path(thread_id, path)
    if not root.exists():
        raise ValueError(f"path does not exist: {path}")
    if root.is_file():
        search_root = root.parent
        files = [root]
        scan = _CodeAnalysisFileScan(
            files=files,
            scanned_path_count=1,
            max_scanned_paths=1,
            scan_truncated=False,
        )
    else:
        search_root = root
        scan = _iter_analyzed_files(search_root, max_files=max_files, include_markdown=include_markdown)
        files = scan.files

    fingerprint = _fingerprint_files(files, root=search_root)
    cache_key = (
        "analysis",
        thread_id,
        path,
        max_files,
        scan.max_scanned_paths,
        "md" if include_markdown else "no-md",
        "sec" if include_security else "no-sec",
        "patterns" if include_patterns else "no-patterns",
    )
    cached = _CACHE.get(cache_key, fingerprint)
    if cached is not None:
        return cached

    codeowners = _load_codeowners(search_root)
    nodes = [
        _analyze_file(
            file_path,
            root=search_root,
            path_service=path_service,
            thread_id=thread_id,
            codeowners=codeowners,
            include_security=include_security,
            include_patterns=include_patterns,
        )
        for file_path in files
    ]
    edges = _resolve_edges(nodes)
    markdown_graph = _markdown_graph(nodes)
    stats = _project_stats(nodes=nodes, edges=edges, markdown_graph=markdown_graph)
    hotspots = _hotspots(nodes=nodes, edges=edges)
    health = _health_payload(nodes=nodes, edges=edges, stats=stats)
    payload = {
        "root": path,
        "fingerprint": fingerprint,
        "file_count": len(nodes),
        "nodes": [node for node in nodes],
        "edges": edges,
        "markdown_graph": markdown_graph,
        "ownership": _ownership_summary(nodes),
        "hotspots": hotspots,
        "security": _security_summary(nodes),
        "patterns": _pattern_summary(nodes),
        "anti_patterns": _anti_patterns(nodes=nodes, edges=edges),
        "health": health,
        "stats": stats,
        "scan": {
            "scanned_path_count": scan.scanned_path_count,
            "max_scanned_paths": scan.max_scanned_paths,
            "scan_truncated": scan.scan_truncated,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["stats"].update(payload["scan"])
    return _CACHE.set(cache_key, fingerprint, payload)


def _semantic_index_payload(analysis: dict[str, Any]) -> dict[str, Any]:
    nodes = [
        {
            "path": node["path"],
            "relative_path": node["relative_path"],
            "language": node["language"],
            "kind": node["kind"],
            "imports": list(node.get("imports") or []),
            "symbols": list(node.get("symbols") or []),
            "owners": list(node.get("owners") or []),
            "line_count": node.get("line_count", 0),
            "size_bytes": node.get("size_bytes", 0),
        }
        for node in analysis["nodes"]
    ]
    return {
        "version": 1,
        "root": analysis["root"],
        "fingerprint": analysis["fingerprint"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "anvil.static",
        "nodes": nodes,
        "edges": list(analysis["edges"]),
        "stats": {
            "file_count": analysis["file_count"],
            "node_count": len(nodes),
            "symbol_count": sum(len(node.get("symbols") or []) for node in nodes),
            "edge_count": len(analysis["edges"]),
            "scanned_path_count": analysis.get("scan", {}).get("scanned_path_count", 0),
            "max_scanned_paths": analysis.get("scan", {}).get("max_scanned_paths", 0),
            "scan_truncated": bool(analysis.get("scan", {}).get("scan_truncated")),
        },
    }


def _iter_analyzed_files(root: Path, *, max_files: int, include_markdown: bool) -> _CodeAnalysisFileScan:
    files: list[Path] = []
    allowed_extensions = _ANALYZED_EXTENSIONS if include_markdown else _CODE_EXTENSIONS
    max_scanned_paths = _bounded_code_analysis_scan_path_limit()
    scanned_path_count = 0
    scan_truncated = False
    pending_dirs = [root]
    while pending_dirs:
        if len(files) >= max_files:
            break
        current_dir = pending_dirs.pop()
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if len(files) >= max_files:
                        pending_dirs.clear()
                        break
                    if scanned_path_count >= max_scanned_paths:
                        scan_truncated = True
                        pending_dirs.clear()
                        break
                    scanned_path_count += 1
                    if entry.name in _IGNORED_DIRS:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending_dirs.append(Path(entry.path))
                            continue
                        is_file = entry.is_file(follow_symlinks=False)
                    except OSError:
                        continue
                    candidate = Path(entry.path)
                    if is_file and (candidate.suffix.lower() in allowed_extensions or candidate.name in _MANIFEST_NAMES):
                        files.append(candidate)
        except OSError:
            continue
    return _CodeAnalysisFileScan(
        files=sorted(files, key=lambda item: item.as_posix()),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _bounded_code_analysis_scan_path_limit() -> int:
    try:
        configured = int(DEFAULT_CODE_ANALYSIS_SCAN_PATH_LIMIT)
    except (TypeError, ValueError):
        configured = 10_000
    return max(1, min(configured, MAX_CODE_ANALYSIS_SCAN_PATH_LIMIT))


def _fingerprint_files(files: list[Path], *, root: Path) -> str:
    metadata: list[dict[str, Any]] = []
    for file_path in files:
        try:
            stat = file_path.stat()
            relative = file_path.relative_to(root).as_posix()
        except OSError:
            continue
        metadata.append(
            {
                "path": relative,
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            }
        )
    return hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _analyze_file(
    file_path: Path,
    *,
    root: Path,
    path_service: PathService,
    thread_id: str,
    codeowners: list[dict[str, Any]],
    include_security: bool,
    include_patterns: bool,
) -> dict[str, Any]:
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    relative = file_path.relative_to(root).as_posix()
    virtual_path = path_service.to_virtual_path(thread_id, file_path)
    suffix = file_path.suffix.lower()
    language = _language_for_file(file_path)
    symbols = _python_symbols(text) if suffix == ".py" else _generic_symbols(text)
    imports = _imports_for_file(text, suffix=suffix)
    headings = _markdown_headings(text) if suffix in _MARKDOWN_EXTENSIONS else []
    markdown_links = _markdown_links(text, source_relative=relative) if suffix in _MARKDOWN_EXTENSIONS else []
    security_findings = _security_findings(text, relative_path=relative, suffix=suffix) if include_security else []
    pattern_findings = _pattern_findings(text, relative_path=relative, suffix=suffix) if include_patterns else []
    return {
        "path": virtual_path,
        "relative_path": relative,
        "language": language,
        "kind": "markdown" if suffix in _MARKDOWN_EXTENSIONS else "manifest" if file_path.name in _MANIFEST_NAMES else "code",
        "imports": imports,
        "symbols": symbols,
        "markdown": {
            "headings": headings,
            "links": markdown_links,
        } if suffix in _MARKDOWN_EXTENSIONS else None,
        "owners": _owners_for_relative_path(relative, codeowners),
        "security_findings": security_findings,
        "patterns": pattern_findings,
        "line_count": text.count("\n") + (1 if text else 0),
        "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
    }


def _python_symbols(text: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    symbols: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append({"name": node.name, "kind": type(node).__name__.replace("Def", "").lower(), "line": node.lineno})
    return sorted(symbols, key=lambda item: (int(item["line"]), str(item["name"])))


def _generic_symbols(text: str) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    patterns = (
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:public|private|protected|static|\s)*[\w<>\[\], ?]+\s+([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)),
        ("type", re.compile(r"^\s*(?:export\s+)?(?:interface|type)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)),
    )
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            symbols.append({"name": match.group(1), "kind": kind, "line": line})
    return sorted(symbols, key=lambda item: (int(item["line"]), str(item["name"])))


def _imports_for_file(text: str, *, suffix: str) -> list[str]:
    if suffix == ".py":
        return _python_imports(text)
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return sorted(dict.fromkeys(match.group("target") for match in _TS_IMPORT_RE.finditer(text)))
    if suffix in {".go", ".java", ".kt", ".swift", ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".rb", ".php"}:
        return _generic_imports(text, suffix=suffix)
    return []


def _generic_imports(text: str, *, suffix: str) -> list[str]:
    patterns: list[re.Pattern[str]]
    if suffix == ".go":
        patterns = [
            re.compile(r"import\s+\"(?P<target>[^\"]+)\""),
            re.compile(r"\"(?P<target>[^\"]+)\""),
        ]
    elif suffix in {".java", ".kt"}:
        patterns = [re.compile(r"^\s*import\s+(?:static\s+)?(?P<target>[\w.]+)", re.MULTILINE)]
    elif suffix in {".cpp", ".cc", ".c", ".h", ".hpp"}:
        patterns = [re.compile(r"^\s*#include\s+[<\"](?P<target>[^>\"]+)[>\"]", re.MULTILINE)]
    elif suffix == ".rb":
        patterns = [re.compile(r"^\s*require(?:_relative)?\s+['\"](?P<target>[^'\"]+)['\"]", re.MULTILINE)]
    elif suffix == ".php":
        patterns = [re.compile(r"^\s*(?:use|require|include)(?:_once)?\s+['\"]?(?P<target>[^;'\"\n]+)", re.MULTILINE)]
    else:
        patterns = []
    imports: list[str] = []
    for pattern in patterns:
        imports.extend(match.group("target") for match in pattern.finditer(text))
    return sorted(dict.fromkeys(imports))


def _python_imports(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            imports.append(module)
    return sorted(dict.fromkeys(imports))


def _resolve_edges(nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_relative = {str(node["relative_path"]): node for node in nodes}
    module_to_node = _module_index(nodes)
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for node in nodes:
        source = str(node["path"])
        source_relative = str(node["relative_path"])
        for target in node["imports"]:
            target_node = _resolve_import(target, source_relative=source_relative, module_to_node=module_to_node, by_relative=by_relative)
            if target_node is None:
                continue
            edge = (source, str(target_node["path"]), str(target))
            if edge in seen:
                continue
            seen.add(edge)
            edges.append({"from": source, "to": str(target_node["path"]), "import": str(target)})
    return edges


def _markdown_graph(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    markdown_nodes = [node for node in nodes if node.get("kind") == "markdown"]
    by_relative = {str(node["relative_path"]).lower(): node for node in markdown_nodes}
    by_stem = {Path(str(node["relative_path"])).stem.lower(): node for node in markdown_nodes}
    edges: list[dict[str, str]] = []
    broken_links: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    for node in markdown_nodes:
        markdown = node.get("markdown") or {}
        for link in markdown.get("links") or []:
            target = str(link.get("target") or "")
            resolved = _resolve_markdown_target(target, source_relative=str(node["relative_path"]), by_relative=by_relative, by_stem=by_stem)
            if resolved is None:
                if link.get("local"):
                    broken_links.append(
                        {
                            "from": str(node["path"]),
                            "target": target,
                            "line": int(link.get("line") or 0),
                        }
                    )
                continue
            edge = (str(node["path"]), str(resolved["path"]))
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            edges.append({"from": edge[0], "to": edge[1], "target": target})
    return {
        "nodes": [
            {
                "path": node["path"],
                "relative_path": node["relative_path"],
                "heading_count": len((node.get("markdown") or {}).get("headings") or []),
                "link_count": len((node.get("markdown") or {}).get("links") or []),
            }
            for node in markdown_nodes
        ],
        "edges": sorted(edges, key=lambda item: (item["from"], item["to"], item["target"])),
        "broken_links": sorted(broken_links, key=lambda item: (item["from"], item["target"], item["line"])),
    }


def _module_index(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if node.get("kind") == "markdown":
            continue
        relative = str(node["relative_path"])
        suffix = Path(relative).suffix
        stem = relative[: -len(suffix)] if suffix else relative
        dotted = stem.replace("/", ".")
        index[dotted] = node
        if dotted.endswith(".__init__"):
            index[dotted[: -len(".__init__")]] = node
    return index


def _resolve_import(
    target: str,
    *,
    source_relative: str,
    module_to_node: dict[str, dict[str, Any]],
    by_relative: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if target in module_to_node:
        return module_to_node[target]
    if target.startswith("."):
        source_dir = PurePosixPath(source_relative).parent
        parts = target.split(".")
        leading = len(target) - len(target.lstrip("."))
        base = source_dir
        for _ in range(max(leading - 1, 0)):
            base = base.parent
        remainder = ".".join(part for part in parts[leading:] if part)
        if remainder:
            candidate = (base / remainder.replace(".", "/")).as_posix()
            for suffix in (".py", ".ts", ".tsx", ".js", ".jsx", "/__init__.py"):
                resolved = by_relative.get(candidate + suffix)
                if resolved is not None:
                    return resolved
    if target.startswith(("./", "../")):
        source_dir = PurePosixPath(source_relative).parent
        candidate = (source_dir / target).as_posix()
        normalized = PurePosixPath(candidate)
        compact = "/".join(part for part in normalized.parts if part not in {".", ""})
        for suffix in ("", ".ts", ".tsx", ".js", ".jsx", ".py", "/index.ts", "/index.tsx", "/index.js", "/index.jsx"):
            resolved = by_relative.get(compact + suffix)
            if resolved is not None:
                return resolved
    return None


def _focus_payload(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
    *,
    focus: str | None,
) -> dict[str, Any] | None:
    if not focus:
        return None
    normalized = focus.replace("\\", "/")
    match = _find_focus_node(nodes, normalized)
    if match is None:
        return {"query": focus, "matched": False, "dependents": [], "dependencies": []}
    path = str(match["path"])
    dependencies = [edge["to"] for edge in edges if edge["from"] == path]
    dependents = [edge["from"] for edge in edges if edge["to"] == path]
    symbols = list(match["symbols"])
    return {
        "query": focus,
        "matched": True,
        "path": path,
        "dependencies": dependencies[:100],
        "dependencies_total": len(dependencies),
        "dependencies_truncated": len(dependencies) > 100,
        "dependents": dependents[:100],
        "dependents_total": len(dependents),
        "dependents_truncated": len(dependents) > 100,
        "symbols": symbols[:80],
        "symbols_total": len(symbols),
        "symbols_truncated": len(symbols) > 80,
    }


def _find_focus_node(nodes: list[dict[str, Any]], focus: str) -> dict[str, Any] | None:
    return find_focus_node(nodes, focus)


def _symbol_reference_pattern(symbol_name: str) -> re.Pattern[str]:
    return symbol_reference_pattern(symbol_name)


def _compact_node(node: dict[str, Any], *, include_symbols: bool) -> dict[str, Any]:
    payload = {
        "path": node["path"],
        "relative_path": node["relative_path"],
        "language": node["language"],
        "kind": node["kind"],
        "line_count": node["line_count"],
        "imports": node["imports"],
        "owners": node["owners"],
    }
    if include_symbols:
        payload["symbols"] = node["symbols"][:80]
        payload["symbols_truncated"] = len(node["symbols"]) > 80
    else:
        payload["symbol_count"] = len(node["symbols"])
    return payload


def _focus_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": node["path"],
        "relative_path": node["relative_path"],
        "language": node["language"],
        "kind": node["kind"],
        "line_count": node["line_count"],
        "symbols": node["symbols"][:40],
        "owners": node["owners"],
        "security_findings": node["security_findings"][:20],
        "patterns": node["patterns"][:20],
    }


def _impact_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": node["path"],
        "relative_path": node["relative_path"],
        "language": node["language"],
        "kind": node["kind"],
        "line_count": node["line_count"],
        "symbol_count": len(node.get("symbols") or []),
        "owners": node.get("owners") or [],
        "security_findings": list(node.get("security_findings") or [])[:10],
        "patterns": list(node.get("patterns") or [])[:10],
    }


def _compact_edge(edge: dict[str, str], *, direction: str) -> dict[str, str]:
    if direction == "dependency":
        return {"path": edge["to"], "import": edge["import"]}
    return {"path": edge["from"], "import": edge["import"]}


def _symbol_matches(
    *,
    nodes: list[dict[str, Any]],
    symbol_name: str,
    target_path: str,
) -> list[dict[str, Any]]:
    if not symbol_name:
        return []
    normalized = symbol_name.lower()
    matches: list[dict[str, Any]] = []
    for node in nodes:
        for symbol in node.get("symbols") or []:
            name = str(symbol.get("name") or "")
            if normalized not in name.lower():
                continue
            matches.append(
                {
                    "name": name,
                    "kind": symbol.get("kind"),
                    "line": symbol.get("line"),
                    "path": node["path"],
                    "relative_path": node["relative_path"],
                    "in_target": str(node["path"]) == target_path,
                }
            )
    return sorted(matches, key=lambda item: (not bool(item["in_target"]), str(item["relative_path"]), int(item["line"] or 0)))


def _symbol_reference_summaries(
    *,
    path_service: PathService,
    thread_id: str,
    nodes: list[dict[str, Any]],
    symbol_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not symbol_name:
        return []
    pattern = _symbol_reference_pattern(symbol_name)
    summaries: list[dict[str, Any]] = []
    total_files = 0
    total_references = 0
    for node in nodes:
        try:
            host_path = path_service.resolve_virtual_path(thread_id, str(node["path"]))
            lines = host_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        reference_lines: list[dict[str, Any]] = []
        reference_count = 0
        for index, line in enumerate(lines, start=1):
            if not pattern.search(line):
                continue
            reference_count += 1
            if len(reference_lines) < 5:
                reference_lines.append({"line": index, "snippet": line.strip()[:180]})
        if not reference_lines:
            continue
        total_files += 1
        total_references += reference_count
        if len(summaries) >= limit:
            continue
        summaries.append(
            {
                "path": node["path"],
                "relative_path": node["relative_path"],
                "language": node["language"],
                "reference_count": reference_count,
                "reference_count_sampled": len(reference_lines),
                "references": reference_lines,
                "is_test": _looks_like_test_path(str(node["relative_path"])),
            }
        )
    for item in summaries:
        item["files_total_estimate"] = total_files
        item["references_total_estimate"] = total_references
        item["files_truncated"] = total_files > len(summaries)
    return summaries


def _candidate_tests(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
    target: dict[str, Any],
    symbol_references: list[dict[str, Any]],
    symbol_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    target_path = str(target["path"])
    target_stem = Path(str(target["relative_path"])).stem.lower()
    dependency_tests = {edge["from"] for edge in edges if edge["to"] == target_path and _looks_like_test_path(edge["from"])}
    reference_tests = {str(item["path"]) for item in symbol_references if item.get("is_test")}
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        relative = str(node["relative_path"])
        path = str(node["path"])
        if not _looks_like_test_path(relative):
            continue
        reasons: list[str] = []
        lower_relative = relative.lower()
        if path in dependency_tests:
            reasons.append("imports target file")
        if path in reference_tests:
            reasons.append(f"references {symbol_name}")
        if target_stem and target_stem in Path(relative).stem.lower():
            reasons.append("name matches target file")
        if target_stem and target_stem in lower_relative:
            reasons.append("path contains target name")
        if not reasons:
            continue
        if path in seen:
            continue
        seen.add(path)
        candidates.append(
            {
                "path": path,
                "relative_path": relative,
                "language": node["language"],
                "line_count": node["line_count"],
                "reasons": reasons,
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def _candidate_docs(
    *,
    nodes: list[dict[str, Any]],
    markdown_graph: dict[str, Any],
    target: dict[str, Any],
    symbol_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    target_stem = Path(str(target["relative_path"])).stem.lower()
    link_sources = {
        str(edge.get("from"))
        for edge in markdown_graph.get("edges", [])
        if str(edge.get("to")) == str(target.get("path")) or target_stem in str(edge.get("target", "")).lower()
    }
    docs: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("kind") != "markdown":
            continue
        markdown = node.get("markdown") or {}
        relative = str(node["relative_path"])
        reasons: list[str] = []
        if str(node["path"]) in link_sources:
            reasons.append("links to target")
        lowered = relative.lower()
        if target_stem and target_stem in lowered:
            reasons.append("path contains target name")
        if symbol_name:
            for heading in markdown.get("headings") or []:
                if symbol_name.lower() in str(heading.get("title", "")).lower():
                    reasons.append("heading mentions symbol")
                    break
        if not reasons:
            continue
        docs.append(
            {
                "path": node["path"],
                "relative_path": relative,
                "heading_count": len(markdown.get("headings") or []),
                "link_count": len(markdown.get("links") or []),
                "reasons": reasons,
            }
        )
        if len(docs) >= limit:
            break
    return docs


def _impact_risk_notes(
    *,
    target: dict[str, Any],
    dependents: list[dict[str, str]],
    related_count: int,
    candidate_tests: list[dict[str, Any]],
    symbol_references: list[dict[str, Any]],
    anti_patterns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    if len(dependents) >= 5:
        notes.append(
            {
                "severity": "high",
                "kind": "broad_dependents",
                "message": "Many files import or depend on the target; verify callers before editing public behavior.",
                "count": len(dependents),
            }
        )
    if related_count >= 20:
        notes.append(
            {
                "severity": "medium",
                "kind": "wide_related_graph",
                "message": "The target sits in a wide dependency neighborhood; keep edits narrow and run focused tests.",
                "count": related_count,
            }
        )
    if target.get("security_findings"):
        notes.append(
            {
                "severity": "high",
                "kind": "target_security_findings",
                "message": "The target has local security findings; inspect them before changing behavior.",
                "count": len(target.get("security_findings") or []),
            }
        )
    if not candidate_tests:
        notes.append(
            {
                "severity": "medium",
                "kind": "no_candidate_tests",
                "message": "No obvious impacted tests were found from imports, references, or file names.",
            }
        )
    if symbol_references and all(not item.get("is_test") for item in symbol_references):
        notes.append(
            {
                "severity": "low",
                "kind": "references_without_tests",
                "message": "Symbol references were found, but none look like tests.",
            }
        )
    target_anti_patterns = [
        item for item in anti_patterns if str(item.get("path")) == str(target.get("path"))
    ]
    for item in target_anti_patterns[:3]:
        notes.append(
            {
                "severity": item.get("severity", "medium"),
                "kind": item.get("kind", "anti_pattern"),
                "message": item.get("message", "Structural risk hint on target file."),
            }
        )
    return notes


def _impact_next_tools(
    *,
    target_path: str,
    symbol_name: str,
    has_candidate_tests: bool,
    has_security_findings: bool,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = [
        {"tool": "code_file_summary", "args": {"file_path": target_path}},
        {"tool": "code_symbols", "args": {"focus": target_path}},
    ]
    if symbol_name:
        calls.append({"tool": "code_references", "args": {"symbol_name": symbol_name, "context": 1}})
    if has_security_findings:
        calls.append({"tool": "code_security_scan", "args": {"severity": "high"}})
    if has_candidate_tests:
        calls.append({"tool": "run_command", "args": {"command": "<run impacted tests>", "cwd": "/mnt/user-data/workspace"}})
    return calls


def _looks_like_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    name = Path(normalized).name
    return (
        "/test/" in normalized
        or "/tests/" in normalized
        or normalized.startswith("test/")
        or normalized.startswith("tests/")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
        or name.endswith(".test.js")
        or name.endswith(".spec.js")
    )


def _compact_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_count": stats["file_count"],
        "code_file_count": stats["code_file_count"],
        "line_count": stats["line_count"],
        "language_counts": stats["language_counts"],
        "edge_count": stats["edge_count"],
        "scanned_path_count": stats.get("scanned_path_count", 0),
        "max_scanned_paths": stats.get("max_scanned_paths", 0),
        "scan_truncated": bool(stats.get("scan_truncated")),
    }


def _related_paths(*, path: str, edges: list[dict[str, str]], depth: int) -> set[str]:
    forward: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        forward[edge["from"]].add(edge["to"])
        reverse[edge["to"]].add(edge["from"])
    seen = {path}
    frontier = {path}
    for _ in range(depth):
        next_frontier: set[str] = set()
        for item in frontier:
            next_frontier.update(forward.get(item, set()))
            next_frontier.update(reverse.get(item, set()))
        next_frontier -= seen
        seen.update(next_frontier)
        frontier = next_frontier
        if not frontier:
            break
    return seen


def _load_codeowners(root: Path) -> list[dict[str, Any]]:
    candidates = [
        root / "CODEOWNERS",
        root / ".github" / "CODEOWNERS",
        root / "docs" / "CODEOWNERS",
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        entries: list[dict[str, Any]] = []
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []
        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            entries.append(
                {
                    "pattern": parts[0],
                    "owners": parts[1:],
                    "line": line_no,
                }
            )
        return entries
    return []


def _owners_for_relative_path(relative_path: str, codeowners: list[dict[str, Any]]) -> list[str]:
    owners: list[str] = []
    normalized = relative_path.replace("\\", "/")
    for entry in codeowners:
        pattern = str(entry["pattern"]).lstrip("/")
        if pattern.endswith("/"):
            matched = normalized.startswith(pattern)
        elif "/" not in pattern:
            matched = fnmatch.fnmatch(Path(normalized).name, pattern)
        else:
            matched = fnmatch.fnmatch(normalized, pattern)
        if matched:
            owners = list(entry["owners"])
    return owners


def _markdown_headings(text: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for match in _HEADING_RE.finditer(text):
        headings.append(
            {
                "level": len(match.group("level")),
                "title": match.group("title").strip(),
                "line": text.count("\n", 0, match.start()) + 1,
            }
        )
    return headings


def _markdown_links(text: str, *, source_relative: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for match in _MARKDOWN_LINK_RE.finditer(text):
        target = match.group("target").strip()
        links.append(
            {
                "target": target,
                "line": text.count("\n", 0, match.start()) + 1,
                "local": _is_local_markdown_target(target),
                "source": source_relative,
            }
        )
    for match in _WIKI_LINK_RE.finditer(text):
        target = match.group("target").strip()
        links.append(
            {
                "target": target,
                "line": text.count("\n", 0, match.start()) + 1,
                "local": True,
                "wiki": True,
                "source": source_relative,
            }
        )
    return links


def _is_local_markdown_target(target: str) -> bool:
    lowered = target.lower()
    return not (
        lowered.startswith(("http://", "https://", "mailto:", "#"))
        or "://" in lowered
    )


def _resolve_markdown_target(
    target: str,
    *,
    source_relative: str,
    by_relative: dict[str, dict[str, Any]],
    by_stem: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    clean_target = target.split("#", 1)[0].strip()
    if not clean_target:
        return None
    if clean_target in by_stem:
        return by_stem[clean_target]
    source_dir = PurePosixPath(source_relative).parent
    candidate = (source_dir / clean_target).as_posix().lstrip("./").lower()
    candidate = candidate.replace("%20", " ")
    if candidate in by_relative:
        return by_relative[candidate]
    if not Path(candidate).suffix:
        for suffix in _MARKDOWN_EXTENSIONS:
            resolved = by_relative.get(candidate + suffix)
            if resolved is not None:
                return resolved
    return None


def _security_findings(text: str, *, relative_path: str, suffix: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if suffix in _MARKDOWN_EXTENSIONS:
        return findings
    for rule in _SECURITY_RULES:
        for pattern in rule["patterns"]:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                snippet = _redact_secret_like(match.group(0).strip())
                findings.append(
                    {
                        "kind": rule["kind"],
                        "severity": rule["severity"],
                        "path": relative_path,
                        "line": line,
                        "message": rule["message"],
                        "snippet": snippet[:160],
                    }
                )
    return findings


def _pattern_findings(text: str, *, relative_path: str, suffix: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rule in _PATTERN_RULES:
        allowed_suffixes = rule.get("suffixes")
        if allowed_suffixes is not None and suffix not in allowed_suffixes:
            continue
        for match in rule["pattern"].finditer(text):
            findings.append(
                {
                    "kind": rule["kind"],
                    "path": relative_path,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "snippet": match.group(0).strip()[:160],
                }
            )
    return findings


def _redact_secret_like(value: str) -> str:
    return re.sub(r"([A-Za-z0-9_./+=:-]{8})[A-Za-z0-9_./+=:-]{8,}", r"\1[REDACTED]", value)


def _all_security_findings(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in nodes:
        for finding in node.get("security_findings") or []:
            findings.append({**finding, "path": node["path"], "relative_path": node["relative_path"]})
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(findings, key=lambda item: (severity_rank.get(str(item["severity"]), 9), str(item["path"]), int(item["line"])))


def _security_summary(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    findings = _all_security_findings(nodes)
    severity_counts = Counter(str(finding["severity"]) for finding in findings)
    kind_counts = Counter(str(finding["kind"]) for finding in findings)
    return {
        "total_findings": len(findings),
        "severity_counts": dict(sorted(severity_counts.items())),
        "kind_counts": dict(sorted(kind_counts.items())),
        "highest_severity": findings[0]["severity"] if findings else None,
    }


def _all_pattern_findings(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in nodes:
        for finding in node.get("patterns") or []:
            findings.append({**finding, "path": node["path"], "relative_path": node["relative_path"]})
    return sorted(findings, key=lambda item: (str(item["kind"]), str(item["path"]), int(item["line"])))


def _pattern_summary(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    findings = _all_pattern_findings(nodes)
    return {
        "total_patterns": len(findings),
        "kind_counts": dict(sorted(Counter(str(item["kind"]) for item in findings).items())),
    }


def _project_stats(*, nodes: list[dict[str, Any]], edges: list[dict[str, str]], markdown_graph: dict[str, Any]) -> dict[str, Any]:
    language_counts = Counter(str(node["language"]) for node in nodes)
    kind_counts = Counter(str(node["kind"]) for node in nodes)
    return {
        "file_count": len(nodes),
        "code_file_count": kind_counts.get("code", 0),
        "markdown_file_count": kind_counts.get("markdown", 0),
        "manifest_file_count": kind_counts.get("manifest", 0),
        "line_count": sum(int(node.get("line_count") or 0) for node in nodes),
        "language_counts": dict(sorted(language_counts.items())),
        "kind_counts": dict(sorted(kind_counts.items())),
        "edge_count": len(edges),
        "doc_edge_count": len(markdown_graph["edges"]),
        "broken_doc_link_count": len(markdown_graph["broken_links"]),
    }


def _hotspots(*, nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> list[dict[str, Any]]:
    node_by_path = {str(node["path"]): node for node in nodes}
    inbound = Counter(edge["to"] for edge in edges)
    outbound = Counter(edge["from"] for edge in edges)
    scored: list[dict[str, Any]] = []
    for path, node in node_by_path.items():
        score = inbound[path] * 2 + outbound[path] + min(int(node.get("line_count") or 0) // 200, 10)
        if score <= 0:
            continue
        scored.append(
            {
                "path": path,
                "relative_path": node["relative_path"],
                "score": score,
                "dependents": inbound[path],
                "dependencies": outbound[path],
                "line_count": node["line_count"],
                "owners": node["owners"],
            }
        )
    return sorted(scored, key=lambda item: (-int(item["score"]), str(item["path"])))[:25]


def _anti_patterns(*, nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    inbound = Counter(edge["to"] for edge in edges)
    outbound = Counter(edge["from"] for edge in edges)
    for node in nodes:
        path = str(node["path"])
        line_count = int(node.get("line_count") or 0)
        if line_count >= 800:
            findings.append(
                {
                    "kind": "large_file",
                    "severity": "medium",
                    "path": path,
                    "relative_path": node["relative_path"],
                    "message": "Large file may need splitting or focused review.",
                    "line_count": line_count,
                }
            )
        if len(node.get("symbols") or []) >= 80:
            findings.append(
                {
                    "kind": "symbol_dense_file",
                    "severity": "medium",
                    "path": path,
                    "relative_path": node["relative_path"],
                    "message": "File defines many symbols; inspect cohesion before broad edits.",
                    "symbol_count": len(node["symbols"]),
                }
            )
        if outbound[path] >= 20:
            findings.append(
                {
                    "kind": "high_dependency_fanout",
                    "severity": "low",
                    "path": path,
                    "relative_path": node["relative_path"],
                    "message": "File imports many internal modules.",
                    "dependencies": outbound[path],
                }
            )
        if inbound[path] >= 20:
            findings.append(
                {
                    "kind": "high_blast_radius",
                    "severity": "high",
                    "path": path,
                    "relative_path": node["relative_path"],
                    "message": "Many files depend on this file; changes need broader tests.",
                    "dependents": inbound[path],
                }
            )
    return sorted(findings, key=lambda item: (str(item["severity"]), str(item["path"])))


def _ownership_summary(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    owner_counts: Counter[str] = Counter()
    unowned = 0
    for node in nodes:
        owners = node.get("owners") or []
        if not owners:
            unowned += 1
            continue
        for owner in owners:
            owner_counts[str(owner)] += 1
    return {
        "owners": dict(owner_counts.most_common(25)),
        "unowned_file_count": unowned,
    }


def _health_payload(*, nodes: list[dict[str, Any]], edges: list[dict[str, str]], stats: dict[str, Any]) -> dict[str, Any]:
    security = _security_summary(nodes)
    anti_patterns = _anti_patterns(nodes=nodes, edges=edges)
    critical = int(security["severity_counts"].get("critical", 0))
    high = int(security["severity_counts"].get("high", 0))
    score = 100
    score -= critical * 25
    score -= high * 15
    score -= min(len(anti_patterns) * 3, 30)
    score -= min(int(stats.get("broken_doc_link_count") or 0) * 2, 10)
    score = max(0, min(100, score))
    if score >= 85:
        grade = "good"
    elif score >= 65:
        grade = "watch"
    else:
        grade = "needs_attention"
    return {
        "score": score,
        "grade": grade,
        "signals": {
            "security_findings": security["total_findings"],
            "anti_patterns": len(anti_patterns),
            "broken_doc_links": stats.get("broken_doc_link_count", 0),
            "hotspot_candidates": len(_hotspots(nodes=nodes, edges=edges)),
        },
    }


def _language_for_suffix(suffix: str) -> str:
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".swift": "swift",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".rb": "ruby",
        ".php": "php",
        ".html": "html",
        ".htm": "html",
        ".vue": "vue",
        ".svelte": "svelte",
        ".sh": "shell",
        ".bash": "shell",
        ".zsh": "shell",
        ".ps1": "powershell",
        ".md": "markdown",
        ".mdx": "mdx",
        ".markdown": "markdown",
    }.get(suffix, suffix.lstrip(".") or "text")


def _language_for_file(file_path: Path) -> str:
    if file_path.name == "package.json":
        return "json"
    if file_path.name in {"requirements.txt", "requirements-dev.txt"}:
        return "python-requirements"
    if file_path.name in {"pyproject.toml", "Cargo.toml"}:
        return "toml"
    if file_path.name == "go.mod":
        return "go-module"
    return _language_for_suffix(file_path.suffix.lower())
