from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Any, Callable, Protocol, TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from anvil.sandbox.path_service import PathService

_LSP_SYMBOL_KIND_NAMES = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum_member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type_parameter",
}
_LSP_CODE_EXTENSIONS = {
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
    ".vue",
    ".svelte",
}
_LSP_IGNORED_DIRS = {
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
_LSP_LANGUAGE_IDS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".mjs": "javascript",
    ".cjs": "javascript",
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
    ".vue": "vue",
    ".svelte": "svelte",
}
_LSP_SESSION_POOL: "_LspSessionPool | None" = None
_SECRETISH_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization|bearer)(\s*[:=]\s*)([^\s,;'\"]{4,})"
)
_LONG_TOKEN_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b")
_LSP_POOL_RECENT_FAILURES: list[dict[str, Any]] = []
DEFAULT_LSP_SCAN_PATH_LIMIT = 10_000
MAX_LSP_SCAN_PATH_LIMIT = 100_000


@dataclass(frozen=True)
class _LspCapabilities:
    document_symbols: bool = False
    definitions: bool = False
    references: bool = False


@dataclass(frozen=True)
class _LspWorkspaceSnapshot:
    fingerprint: str
    file_count: int
    scanned_path_count: int = 0
    max_scanned_paths: int = 0
    scan_truncated: bool = False


@dataclass(frozen=True)
class _LspFileScan:
    files: list[Path]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool


@dataclass(frozen=True)
class LspWorkspaceProbe:
    root: Path
    files: list[Path]
    snapshot: _LspWorkspaceSnapshot


def bounded_limit(value: int, *, minimum: int = 1, maximum: int = 500) -> int:
    return max(minimum, min(value, maximum))


@dataclass(frozen=True)
class CodeSemanticIndex:
    root: str
    fingerprint: str
    cache: str
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, str], ...]
    backend: str
    scanned_path_count: int = 0
    max_scanned_paths: int = 0
    scan_truncated: bool = False
    freshness: str = "current"
    current_fingerprint: str | None = None
    diagnostics: tuple[str, ...] = ()
    reference_resolver: "CodeSemanticReferenceResolver | None" = None

    @classmethod
    def from_analysis(cls, analysis: dict[str, Any], *, backend: str) -> "CodeSemanticIndex":
        scan = analysis.get("scan") if isinstance(analysis.get("scan"), dict) else {}
        return cls(
            root=str(analysis["root"]),
            fingerprint=str(analysis["fingerprint"]),
            cache=str(analysis["cache"]),
            nodes=tuple(dict(node) for node in analysis["nodes"]),
            edges=tuple(dict(edge) for edge in analysis["edges"]),
            backend=backend,
            scanned_path_count=int(scan.get("scanned_path_count") or 0),
            max_scanned_paths=int(scan.get("max_scanned_paths") or 0),
            scan_truncated=bool(scan.get("scan_truncated")),
            freshness="current",
            current_fingerprint=str(analysis["fingerprint"]),
        )

    def metadata(self) -> dict[str, Any]:
        metadata = {
            "root": self.root,
            "fingerprint": self.fingerprint,
            "cache": self.cache,
            "semantic_backend": self.backend,
            "semantic_index_freshness": self.freshness,
            "scanned_path_count": self.scanned_path_count,
            "max_scanned_paths": self.max_scanned_paths,
            "scan_truncated": self.scan_truncated,
        }
        if self.current_fingerprint:
            metadata["current_fingerprint"] = self.current_fingerprint
        if self.diagnostics:
            metadata["semantic_index_diagnostics"] = list(self.diagnostics)
        return metadata


class CodeSemanticBackend(Protocol):
    name: str

    def build_index(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        path: str,
        max_files: int,
    ) -> CodeSemanticIndex:
        ...


class CodeSemanticReferenceResolver(Protocol):
    def find_definitions(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        index: CodeSemanticIndex,
        symbol_name: str,
        file_path: str | None,
        limit: int,
        context: int,
    ) -> dict[str, Any] | None:
        ...

    def find_references(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        index: CodeSemanticIndex,
        symbol_name: str,
        file_path: str | None,
        limit: int,
        context: int,
    ) -> dict[str, Any] | None:
        ...


class StaticCodeSemanticBackend:
    name = "static"

    def __init__(self, analyzer) -> None:
        self._analyzer = analyzer

    def build_index(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        path: str,
        max_files: int,
    ) -> CodeSemanticIndex:
        analysis = self._analyzer(
            path_service=path_service,
            thread_id=thread_id,
            path=path,
            max_files=max_files,
            include_markdown=False,
            include_security=False,
            include_patterns=False,
        )
        return CodeSemanticIndex.from_analysis(analysis, backend=self.name)


class ExternalIndexCodeSemanticBackend:
    name = "external_index"

    def __init__(
        self,
        *,
        index_path: str | Path,
        fallback: CodeSemanticBackend | None = None,
        fingerprint_probe: Callable[..., str] | None = None,
        validate_freshness: bool = True,
    ) -> None:
        self.index_path = str(index_path)
        self.fallback = fallback
        self.fingerprint_probe = fingerprint_probe
        self.validate_freshness = validate_freshness

    def build_index(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        path: str,
        max_files: int,
    ) -> CodeSemanticIndex:
        diagnostics: list[str] = []
        try:
            resolved_index_path = self._resolve_index_path(path_service=path_service, thread_id=thread_id)
            payload = json.loads(resolved_index_path.read_text(encoding="utf-8"))
            index = self._index_from_payload(payload, root=path)
            if index is not None:
                current_fingerprint = None
                if self.validate_freshness and self.fingerprint_probe is not None:
                    current_fingerprint = self.fingerprint_probe(
                        path_service=path_service,
                        thread_id=thread_id,
                        path=path,
                        max_files=max_files,
                    )
                    if not index.fingerprint:
                        diagnostics.append("external index has no fingerprint")
                        return self._fallback_index(
                            path_service=path_service,
                            thread_id=thread_id,
                            path=path,
                            max_files=max_files,
                            reason="unknown_freshness",
                            diagnostics=diagnostics,
                        )
                    if current_fingerprint != index.fingerprint:
                        diagnostics.append("external index fingerprint is stale")
                        diagnostics.append(f"index={index.fingerprint}")
                        diagnostics.append(f"current={current_fingerprint}")
                        return self._fallback_index(
                            path_service=path_service,
                            thread_id=thread_id,
                            path=path,
                            max_files=max_files,
                            reason="stale",
                            diagnostics=diagnostics,
                        )
                return CodeSemanticIndex(
                    root=index.root,
                    fingerprint=index.fingerprint,
                    cache=index.cache,
                    nodes=index.nodes,
                    edges=index.edges,
                    backend=index.backend,
                    scanned_path_count=index.scanned_path_count,
                    max_scanned_paths=index.max_scanned_paths,
                    scan_truncated=index.scan_truncated,
                    freshness="fresh" if current_fingerprint else "unchecked",
                    current_fingerprint=current_fingerprint,
                    diagnostics=tuple(diagnostics),
                )
            diagnostics.append("external index payload is invalid")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            diagnostics.append(str(exc))
        return self._fallback_index(
            path_service=path_service,
            thread_id=thread_id,
            path=path,
            max_files=max_files,
            reason="unavailable",
            diagnostics=diagnostics,
        )

    def _resolve_index_path(self, *, path_service: PathService, thread_id: str) -> Path:
        if self.index_path.startswith("/mnt/"):
            return path_service.resolve_virtual_path(thread_id, self.index_path)
        return Path(self.index_path).expanduser()

    def _fallback_index(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        path: str,
        max_files: int,
        reason: str,
        diagnostics: list[str],
    ) -> CodeSemanticIndex:
        if self.fallback is not None:
            fallback_index = self.fallback.build_index(
                path_service=path_service,
                thread_id=thread_id,
                path=path,
                max_files=max_files,
            )
            backend = (
                f"{self.name}->fallback:{fallback_index.backend}"
                if reason == "unavailable"
                else f"{self.name}->{reason}:fallback:{fallback_index.backend}"
            )
            return CodeSemanticIndex(
                root=fallback_index.root,
                fingerprint=fallback_index.fingerprint,
                cache=fallback_index.cache,
                nodes=fallback_index.nodes,
                edges=fallback_index.edges,
                backend=backend,
                scanned_path_count=fallback_index.scanned_path_count,
                max_scanned_paths=fallback_index.max_scanned_paths,
                scan_truncated=fallback_index.scan_truncated,
                freshness=reason,
                current_fingerprint=fallback_index.current_fingerprint or fallback_index.fingerprint,
                diagnostics=tuple(diagnostics[:10]),
            )
        diagnostic = diagnostics[0] if diagnostics else f"code semantic external index {reason}"
        raise ValueError(f"code semantic external index {reason}: {self.index_path}: {diagnostic}")

    def _index_from_payload(self, payload: dict[str, Any], *, root: str) -> CodeSemanticIndex | None:
        nodes = payload.get("nodes")
        if not isinstance(nodes, list):
            return None
        normalized_nodes = [_normalize_external_node(node) for node in nodes if isinstance(node, dict)]
        if not normalized_nodes:
            return None
        edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
        normalized_edges = [
            {"from": str(edge.get("from") or ""), "to": str(edge.get("to") or ""), "import": str(edge.get("import") or "")}
            for edge in edges
            if isinstance(edge, dict)
        ]
        return CodeSemanticIndex(
            root=str(payload.get("root") or root),
            fingerprint=str(payload.get("fingerprint") or ""),
            cache="external",
            nodes=tuple(normalized_nodes),
            edges=tuple(normalized_edges),
            backend=self.name,
            scanned_path_count=int((payload.get("stats") or {}).get("scanned_path_count") or len(normalized_nodes)),
            max_scanned_paths=int((payload.get("stats") or {}).get("max_scanned_paths") or len(normalized_nodes)),
            scan_truncated=bool((payload.get("stats") or {}).get("scan_truncated")),
        )


class LspJsonRpcCodeSemanticBackend:
    name = "lsp_jsonrpc"

    def __init__(
        self,
        *,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: float = 8.0,
        session_idle_ttl_seconds: float = 300.0,
        stderr_max_chars: int = 2000,
        initialization_options: dict[str, Any] | None = None,
        fallback: CodeSemanticBackend | None = None,
    ) -> None:
        self.command = [str(part) for part in command if str(part)]
        self.cwd = cwd
        self.env = dict(env or {})
        self.timeout_seconds = max(0.5, float(timeout_seconds or 8.0))
        self.session_idle_ttl_seconds = max(0.0, min(float(session_idle_ttl_seconds), 3600.0))
        self.stderr_max_chars = max(0, min(int(stderr_max_chars), 10000))
        self.initialization_options = dict(initialization_options or {})
        self.fallback = fallback
        self._last_session_key: tuple[Any, ...] | None = None

    def build_index(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        path: str,
        max_files: int,
    ) -> CodeSemanticIndex:
        diagnostics: list[str] = []
        try:
            if not self.command:
                raise ValueError("code semantic lsp_jsonrpc backend requires lsp_command")
            probe = lsp_workspace_probe(
                path_service=path_service,
                thread_id=thread_id,
                path=path,
                max_files=max_files,
            )
            search_root = probe.root
            nodes = self._document_nodes(
                path_service=path_service,
                thread_id=thread_id,
                workspace_root=search_root,
                files=probe.files,
                workspace_snapshot=probe.snapshot,
            )
            if not nodes:
                raise ValueError("lsp_jsonrpc backend returned no document symbols")
            return CodeSemanticIndex(
                root=path,
                fingerprint=probe.snapshot.fingerprint,
                cache="lsp",
                nodes=tuple(nodes),
                edges=(),
                backend=self.name,
                scanned_path_count=probe.snapshot.scanned_path_count,
                max_scanned_paths=probe.snapshot.max_scanned_paths,
                scan_truncated=probe.snapshot.scan_truncated,
                freshness="current",
                current_fingerprint=probe.snapshot.fingerprint,
                diagnostics=tuple(
                    [
                        *diagnostics,
                        *_lsp_scan_diagnostics(probe.snapshot),
                    ][:10]
                ),
                reference_resolver=self,
            )
        except (OSError, TimeoutError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            diagnostics.extend(self._diagnostics_for_exception(exc))
            return self._fallback_index(
                path_service=path_service,
                thread_id=thread_id,
                path=path,
                max_files=max_files,
                diagnostics=diagnostics,
            )

    def _document_nodes(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        workspace_root: Path,
        files: list[Path],
        workspace_snapshot: _LspWorkspaceSnapshot,
    ) -> list[dict[str, Any]]:
        pool = _lsp_session_pool()
        session = pool.acquire(
            command=self.command,
            cwd=self.cwd,
            env=self.env,
            timeout_seconds=self.timeout_seconds,
            idle_ttl_seconds=self.session_idle_ttl_seconds,
            stderr_max_chars=self.stderr_max_chars,
            workspace_root=workspace_root,
            workspace_snapshot=workspace_snapshot,
            initialization_options=self.initialization_options,
            capabilities=_semantic_lsp_client_capabilities(),
        )
        self._last_session_key = session.key
        try:
            if not session.capabilities.document_symbols:
                raise ValueError("lsp_jsonrpc server does not support textDocument/documentSymbol")
            nodes: list[dict[str, Any]] = []
            opened: list[str] = []
            with session.operation_lock:
                try:
                    for file_path in files:
                        text = _read_lsp_file(file_path)
                        uri = file_path.as_uri()
                        opened.append(uri)
                        language_id = _language_id_for_lsp(file_path)
                        session.notify(
                            "textDocument/didOpen",
                            {
                                "textDocument": {
                                    "uri": uri,
                                    "languageId": language_id,
                                    "version": 1,
                                    "text": text,
                                }
                            },
                        )
                        result = session.request(
                            "textDocument/documentSymbol",
                            {"textDocument": {"uri": uri}},
                        )
                        symbols = _normalize_lsp_document_symbols(result, default_uri=uri)
                        virtual_path = path_service.to_virtual_path(thread_id, file_path)
                        nodes.append(
                            {
                                "path": virtual_path,
                                "relative_path": file_path.relative_to(workspace_root).as_posix(),
                                "language": language_id,
                                "kind": "code",
                                "imports": [],
                                "symbols": [
                                    {
                                        "name": item["name"],
                                        "kind": item["kind"],
                                        "line": item["line"],
                                        "character": item.get("character", 0),
                                    }
                                    for item in symbols
                                    if not item.get("uri") or _same_file_uri(str(item["uri"]), file_path)
                                ],
                                "owners": [],
                                "security_findings": [],
                                "patterns": [],
                                "line_count": len(text.splitlines()),
                                "size_bytes": file_path.stat().st_size if file_path.exists() else len(text.encode("utf-8")),
                            }
                        )
                finally:
                    for uri in opened:
                        try:
                            session.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
                        except (OSError, subprocess.SubprocessError):
                            pool.discard(session.key)
                            break
            return nodes
        except Exception:
            pool.discard(session.key)
            raise

    def _last_capabilities(self) -> _LspCapabilities | None:
        if self._last_session_key is None:
            return None
        return _lsp_session_pool().capabilities(self._last_session_key)

    def _session_for_index(self, *, path_service: PathService, thread_id: str, index: CodeSemanticIndex) -> "_PooledLspSession":
        workspace_root = path_service.resolve_virtual_path(thread_id, index.root)
        if workspace_root.is_file():
            workspace_root = workspace_root.parent
        workspace_snapshot = _LspWorkspaceSnapshot(
            fingerprint=index.current_fingerprint or index.fingerprint,
            file_count=len(index.nodes),
        )
        return _lsp_session_pool().acquire(
            command=self.command,
            cwd=self.cwd,
            env=self.env,
            timeout_seconds=self.timeout_seconds,
            idle_ttl_seconds=self.session_idle_ttl_seconds,
            stderr_max_chars=self.stderr_max_chars,
            workspace_root=workspace_root,
            workspace_snapshot=workspace_snapshot,
            initialization_options=self.initialization_options,
            capabilities=_definition_reference_lsp_client_capabilities(),
        )

    def _capabilities_for_index(self, *, path_service: PathService, thread_id: str, index: CodeSemanticIndex) -> _LspCapabilities:
        if self._last_session_key is not None:
            capabilities = _lsp_session_pool().capabilities(self._last_session_key)
            if capabilities is not None:
                return capabilities
        return self._session_for_index(path_service=path_service, thread_id=thread_id, index=index).capabilities

    def _discard_index_session(self, *, path_service: PathService, thread_id: str, index: CodeSemanticIndex) -> None:
        try:
            session = self._session_for_index(path_service=path_service, thread_id=thread_id, index=index)
        except Exception:
            return
        _lsp_session_pool().discard(session.key)

    def _fallback_index(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        path: str,
        max_files: int,
        diagnostics: list[str],
    ) -> CodeSemanticIndex:
        if self.fallback is not None:
            fallback_index = self.fallback.build_index(
                path_service=path_service,
                thread_id=thread_id,
                path=path,
                max_files=max_files,
            )
            return CodeSemanticIndex(
                root=fallback_index.root,
                fingerprint=fallback_index.fingerprint,
                cache=fallback_index.cache,
                nodes=fallback_index.nodes,
                edges=fallback_index.edges,
                backend=f"{self.name}->fallback:{fallback_index.backend}",
                scanned_path_count=fallback_index.scanned_path_count,
                max_scanned_paths=fallback_index.max_scanned_paths,
                scan_truncated=fallback_index.scan_truncated,
                freshness="unavailable",
                current_fingerprint=fallback_index.current_fingerprint or fallback_index.fingerprint,
                diagnostics=tuple(diagnostics[:10]),
            )
        diagnostic = diagnostics[0] if diagnostics else "code semantic lsp_jsonrpc backend unavailable"
        raise ValueError(f"code semantic lsp_jsonrpc backend unavailable: {diagnostic}")

    def _diagnostics_for_exception(self, exc: BaseException) -> list[str]:
        diagnostics = [_sanitize_lsp_diagnostic(f"{type(exc).__name__}: {exc}")]
        if self._last_session_key is not None:
            session = _lsp_session_pool().get(self._last_session_key)
            if session is not None:
                diagnostics.extend(session.diagnostics())
        return _dedupe_lsp_diagnostics(diagnostics)

    def find_references(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        index: CodeSemanticIndex,
        symbol_name: str,
        file_path: str | None,
        limit: int,
        context: int,
    ) -> dict[str, Any] | None:
        normalized_symbol = symbol_name.strip()
        if not normalized_symbol:
            return None
        focus_node = _reference_focus_node(index.nodes, normalized_symbol, file_path=file_path)
        if focus_node is None:
            return None
        focus_symbol = _reference_focus_symbol(focus_node, normalized_symbol)
        if focus_symbol is None:
            return None
        try:
            references = self._lsp_references_for_symbol(
                path_service=path_service,
                thread_id=thread_id,
                index=index,
                focus_node=focus_node,
                focus_symbol=focus_symbol,
                include_declaration=True,
            )
        except (OSError, TimeoutError, ValueError, TypeError, subprocess.SubprocessError):
            return None
        if not references:
            return None
        bounded = bounded_limit(limit)
        bounded_context = max(0, min(context, 5))
        references.sort(key=lambda item: (str(item["relative_path"]), int(item["line"])))
        returned = references[:bounded]
        if bounded_context:
            returned = [
                {
                    **item,
                    "context": _reference_context(
                        path_service=path_service,
                        thread_id=thread_id,
                        virtual_path=str(item["path"]),
                        line=int(item["line"]),
                        context=bounded_context,
                    ),
                }
                for item in returned
            ]
        else:
            returned = [{**item, "context": []} for item in returned]
        return {
            **index.metadata(),
            "semantic_reference_backend": self.name,
            "symbol_name": normalized_symbol,
            "file_path": file_path,
            "references": returned,
            "returned": len(returned),
            "total_estimate": len(references),
            "truncated": len(references) > len(returned),
        }

    def find_definitions(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        index: CodeSemanticIndex,
        symbol_name: str,
        file_path: str | None,
        limit: int,
        context: int,
    ) -> dict[str, Any] | None:
        normalized_symbol = symbol_name.strip()
        if not normalized_symbol:
            return None
        focus_node = _reference_focus_node(index.nodes, normalized_symbol, file_path=file_path)
        if focus_node is None:
            return None
        focus_symbol = _reference_focus_symbol(focus_node, normalized_symbol)
        if focus_symbol is None:
            return None
        try:
            definitions = self._lsp_locations_for_symbol(
                path_service=path_service,
                thread_id=thread_id,
                index=index,
                focus_node=focus_node,
                focus_symbol=focus_symbol,
                method="textDocument/definition",
            )
        except (OSError, TimeoutError, ValueError, TypeError, subprocess.SubprocessError):
            return None
        if not definitions:
            return None
        bounded = bounded_limit(limit)
        bounded_context = max(0, min(context, 5))
        definitions.sort(key=lambda item: (str(item["relative_path"]), int(item["line"])))
        returned = _attach_location_context(
            definitions[:bounded],
            path_service=path_service,
            thread_id=thread_id,
            context=bounded_context,
        )
        return {
            **index.metadata(),
            "semantic_definition_backend": self.name,
            "symbol_name": normalized_symbol,
            "file_path": file_path,
            "definitions": returned,
            "returned": len(returned),
            "total_estimate": len(definitions),
            "truncated": len(definitions) > len(returned),
        }

    def _lsp_references_for_symbol(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        index: CodeSemanticIndex,
        focus_node: dict[str, Any],
        focus_symbol: dict[str, Any],
        include_declaration: bool,
    ) -> list[dict[str, Any]]:
        return self._lsp_locations_for_symbol(
            path_service=path_service,
            thread_id=thread_id,
            index=index,
            focus_node=focus_node,
            focus_symbol=focus_symbol,
            method="textDocument/references",
            extra_params={"context": {"includeDeclaration": include_declaration}},
        )

    def _lsp_locations_for_symbol(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        index: CodeSemanticIndex,
        focus_node: dict[str, Any],
        focus_symbol: dict[str, Any],
        method: str,
        extra_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        workspace_root = path_service.resolve_virtual_path(thread_id, index.root)
        if workspace_root.is_file():
            workspace_root = workspace_root.parent
        file_path = path_service.resolve_virtual_path(thread_id, str(focus_node["path"]))
        text = _read_lsp_file(file_path)
        uri = file_path.as_uri()
        pool = _lsp_session_pool()
        session = self._session_for_index(path_service=path_service, thread_id=thread_id, index=index)
        if method == "textDocument/definition" and not session.capabilities.definitions:
            return []
        if method == "textDocument/references" and not session.capabilities.references:
            return []
        with session.operation_lock:
            try:
                session.notify(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": uri,
                            "languageId": _language_id_for_lsp(file_path),
                            "version": 1,
                            "text": text,
                        }
                    },
                )
                params = {
                    "textDocument": {"uri": uri},
                    "position": {
                        "line": max(0, int(focus_symbol.get("line") or 1) - 1),
                        "character": max(0, int(focus_symbol.get("character") or 0)),
                    },
                }
                params.update(extra_params or {})
                result = session.request(method, params)
            except Exception:
                pool.discard(session.key)
                raise
            finally:
                try:
                    session.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
                except (OSError, subprocess.SubprocessError):
                    pool.discard(session.key)
        return _normalize_lsp_locations(
            result,
            path_service=path_service,
            thread_id=thread_id,
            workspace_root=workspace_root,
            symbol_name=str(focus_symbol.get("name") or ""),
        )


class _PooledLspSession:
    def __init__(
        self,
        *,
        key: tuple[Any, ...],
        session: "_LspJsonRpcSession",
        capabilities: _LspCapabilities,
        workspace_root: Path,
        workspace_snapshot: _LspWorkspaceSnapshot,
    ) -> None:
        self.key = key
        self.session = session
        self.capabilities = capabilities
        self.workspace_root = workspace_root
        self.workspace_snapshot = workspace_snapshot
        self.operation_lock = threading.Lock()
        self.created_at = time.monotonic()
        self.last_used_at = self.created_at

    def request(self, method: str, params: Any, *, timeout_seconds: float | None = None) -> Any:
        self.last_used_at = time.monotonic()
        return self.session.request(method, params, timeout_seconds=timeout_seconds)

    def notify(self, method: str, params: Any) -> None:
        self.last_used_at = time.monotonic()
        self.session.notify(method, params)

    def idle_for(self) -> float:
        return max(0.0, time.monotonic() - self.last_used_at)

    def diagnostics(self) -> list[str]:
        return self.session.diagnostics()


class _LspSessionPool:
    def __init__(self) -> None:
        self._sessions: dict[tuple[Any, ...], _PooledLspSession] = {}
        self._lock = threading.Lock()

    def acquire(
        self,
        *,
        command: list[str],
        cwd: str | None,
        env: dict[str, str],
        timeout_seconds: float,
        idle_ttl_seconds: float,
        stderr_max_chars: int,
        workspace_root: Path,
        workspace_snapshot: _LspWorkspaceSnapshot,
        initialization_options: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> _PooledLspSession:
        key = self._key(
            command=command,
            cwd=cwd,
            env=env,
            workspace_root=workspace_root,
            initialization_options=initialization_options,
        )
        with self._lock:
            self._reap_expired_locked(idle_ttl_seconds=idle_ttl_seconds)
            existing = self._sessions.get(key)
            if existing is not None and existing.session.is_running():
                if existing.workspace_snapshot.fingerprint != workspace_snapshot.fingerprint:
                    existing.session.close()
                    self._sessions.pop(key, None)
                    existing = None
                else:
                    existing.last_used_at = time.monotonic()
                    return existing
            if existing is not None:
                existing.session.close()
                self._sessions.pop(key, None)
            session = _LspJsonRpcSession(
                command=command,
                cwd=cwd,
                env=env,
                timeout_seconds=timeout_seconds,
                stderr_max_chars=stderr_max_chars,
            )
            try:
                session.start()
                workspace_uri = workspace_root.as_uri()
                initialize_result = session.request(
                    "initialize",
                    {
                        "processId": None,
                        "rootUri": workspace_uri,
                        "workspaceFolders": [{"uri": workspace_uri, "name": workspace_root.name or "workspace"}],
                        "capabilities": capabilities,
                        "initializationOptions": initialization_options,
                    },
                )
                session.notify("initialized", {})
                pooled = _PooledLspSession(
                    key=key,
                    session=session,
                    capabilities=_lsp_capabilities(initialize_result),
                    workspace_root=workspace_root,
                    workspace_snapshot=workspace_snapshot,
                )
                self._sessions[key] = pooled
                return pooled
            except Exception as exc:
                diagnostics = session.diagnostics()
                session.close()
                _record_lsp_pool_failure(key=key, diagnostics=[f"{type(exc).__name__}: {exc}", *diagnostics])
                if diagnostics:
                    raise subprocess.SubprocessError(
                        "; ".join(_dedupe_lsp_diagnostics([f"{type(exc).__name__}: {exc}", *diagnostics]))
                    ) from exc
                raise

    def capabilities(self, key: tuple[Any, ...]) -> _LspCapabilities | None:
        with self._lock:
            existing = self._sessions.get(key)
            if existing is None or not existing.session.is_running():
                return None
            return existing.capabilities

    def get(self, key: tuple[Any, ...]) -> _PooledLspSession | None:
        with self._lock:
            return self._sessions.get(key)

    def discard(self, key: tuple[Any, ...]) -> None:
        with self._lock:
            existing = self._sessions.pop(key, None)
        if existing is not None:
            existing.session.close()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.session.close()

    def health(
        self,
        *,
        idle_ttl_seconds: float,
        current_workspace_root: Path | None = None,
        current_workspace_snapshot: _LspWorkspaceSnapshot | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._reap_expired_locked(idle_ttl_seconds=idle_ttl_seconds)
            sessions = list(self._sessions.values())
        active = [session for session in sessions if session.session.is_running()]
        return {
            "configured": True,
            "session_count": len(sessions),
            "running_session_count": len(active),
            "sessions": [
                _lsp_session_health_payload(
                    session,
                    current_workspace_root=current_workspace_root,
                    current_workspace_snapshot=current_workspace_snapshot,
                )
                for session in sorted(sessions, key=lambda item: str(item.key))[:20]
            ],
            "sessions_truncated": len(sessions) > 20,
            "recent_failures": list(_LSP_POOL_RECENT_FAILURES[-10:]),
        }

    def recover_stale(
        self,
        *,
        idle_ttl_seconds: float,
        current_workspace_root: Path,
        current_workspace_snapshot: _LspWorkspaceSnapshot,
    ) -> dict[str, Any]:
        with self._lock:
            self._reap_expired_locked(idle_ttl_seconds=idle_ttl_seconds)
            sessions = list(self._sessions.values())
            active = [session for session in sessions if session.session.is_running()]
            stale = [
                session
                for session in active
                if _same_resolved_path(session.workspace_root, current_workspace_root)
                and session.workspace_snapshot.fingerprint != current_workspace_snapshot.fingerprint
            ]
            stale_payloads = [
                _lsp_session_health_payload(
                    session,
                    current_workspace_root=current_workspace_root,
                    current_workspace_snapshot=current_workspace_snapshot,
                )
                for session in sorted(stale, key=lambda item: str(item.key))[:20]
            ]
            for session in stale:
                self._sessions.pop(session.key, None)
        for session in stale:
            session.session.close()
        return {
            "configured": True,
            "action": "discarded_stale_sessions" if stale else "noop",
            "session_count_before": len(sessions),
            "running_session_count_before": len(active),
            "recovered_session_count": len(stale),
            "recovered_sessions": stale_payloads,
            "recovered_sessions_truncated": len(stale) > 20,
        }

    def _reap_expired_locked(self, *, idle_ttl_seconds: float) -> None:
        expired: list[tuple[Any, ...]] = []
        for key, session in self._sessions.items():
            if not session.session.is_running():
                expired.append(key)
            elif idle_ttl_seconds > 0 and session.idle_for() > idle_ttl_seconds:
                expired.append(key)
        for key in expired:
            session = self._sessions.pop(key, None)
            if session is not None:
                session.session.close()

    def _key(
        self,
        *,
        command: list[str],
        cwd: str | None,
        env: dict[str, str],
        workspace_root: Path,
        initialization_options: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            tuple(command),
            str(Path(cwd).resolve()) if cwd else None,
            tuple(sorted((str(key), str(value)) for key, value in env.items())),
            str(workspace_root.resolve()),
            json.dumps(initialization_options, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )


class CodeSemanticService:
    def __init__(self, backend: CodeSemanticBackend) -> None:
        self._backend = backend

    def build_index(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        path: str,
        max_files: int,
    ) -> CodeSemanticIndex:
        return self._backend.build_index(
            path_service=path_service,
            thread_id=thread_id,
            path=path,
            max_files=max_files,
        )

    def symbols_for_file(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        focus: str,
        path: str,
        max_files: int,
        limit: int,
    ) -> dict[str, Any]:
        index = self.build_index(path_service=path_service, thread_id=thread_id, path=path, max_files=max_files)
        node = find_focus_node(index.nodes, focus)
        if node is None:
            return {
                **index.metadata(),
                "query": focus,
                "matched": False,
                "symbols": [],
                "total": 0,
                "truncated": False,
            }
        bounded = bounded_limit(limit)
        symbols = list(node.get("symbols") or [])
        return {
            **index.metadata(),
            "matched": True,
            "path": node["path"],
            "relative_path": node["relative_path"],
            "language": node["language"],
            "symbols": symbols[:bounded],
            "total": len(symbols),
            "truncated": len(symbols) > bounded,
        }

    def search_symbols(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        query: str,
        path: str,
        max_files: int,
        limit: int,
        kind: str | None,
    ) -> dict[str, Any]:
        normalized_query = query.strip().lower()
        if not normalized_query:
            raise ValueError("query is required")
        normalized_kind = (kind or "").strip().lower()
        index = self.build_index(path_service=path_service, thread_id=thread_id, path=path, max_files=max_files)
        matches: list[dict[str, Any]] = []
        for node in index.nodes:
            for symbol in node.get("symbols") or []:
                name = str(symbol.get("name") or "")
                symbol_kind = str(symbol.get("kind") or "")
                if normalized_kind and normalized_kind != symbol_kind.lower():
                    continue
                if normalized_query not in name.lower():
                    continue
                matches.append(
                    {
                        "name": name,
                        "kind": symbol_kind,
                        "line": int(symbol.get("line") or 0),
                        "path": node["path"],
                        "relative_path": node["relative_path"],
                        "language": node["language"],
                    }
                )
        bounded = bounded_limit(limit)
        matches.sort(key=lambda item: (str(item["relative_path"]), int(item["line"]), str(item["name"])))
        return {
            **index.metadata(),
            "query": query,
            "kind": normalized_kind or None,
            "matches": matches[:bounded],
            "total": len(matches),
            "truncated": len(matches) > bounded,
        }

    def find_references(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        symbol_name: str,
        path: str,
        file_path: str | None,
        max_files: int,
        limit: int,
        context: int,
    ) -> dict[str, Any]:
        normalized_symbol = symbol_name.strip()
        if not normalized_symbol:
            raise ValueError("symbol_name is required")
        index = self.build_index(path_service=path_service, thread_id=thread_id, path=path, max_files=max_files)
        if index.reference_resolver is not None:
            resolved = index.reference_resolver.find_references(
                path_service=path_service,
                thread_id=thread_id,
                index=index,
                symbol_name=normalized_symbol,
                file_path=file_path,
                limit=limit,
                context=context,
            )
            if resolved is not None:
                return resolved
        candidate_nodes: tuple[dict[str, Any] | None, ...] = index.nodes
        if file_path:
            candidate_nodes = (find_focus_node(index.nodes, file_path),)
        bounded = bounded_limit(limit)
        bounded_context = max(0, min(context, 5))
        references: list[dict[str, Any]] = []
        pattern = symbol_reference_pattern(normalized_symbol)
        total_estimate = 0
        for node in candidate_nodes:
            if node is None:
                continue
            try:
                host_path = path_service.resolve_virtual_path(thread_id, str(node["path"]))
                lines = host_path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            for index_line, line in enumerate(lines, start=1):
                if not pattern.search(line):
                    continue
                total_estimate += 1
                if len(references) >= bounded:
                    continue
                start = max(index_line - bounded_context, 1)
                end = min(index_line + bounded_context, len(lines))
                references.append(
                    {
                        "path": node["path"],
                        "relative_path": node["relative_path"],
                        "line": index_line,
                        "snippet": line.strip()[:240],
                        "context": [
                            {
                                "line": line_number,
                                "text": lines[line_number - 1][:240],
                            }
                            for line_number in range(start, end + 1)
                        ]
                        if bounded_context
                        else [],
                    }
                )
        return {
            **index.metadata(),
            "symbol_name": normalized_symbol,
            "file_path": file_path,
            "references": references,
            "returned": len(references),
            "total_estimate": total_estimate,
            "truncated": total_estimate > len(references),
        }

    def find_definitions(
        self,
        *,
        path_service: PathService,
        thread_id: str,
        symbol_name: str,
        path: str,
        file_path: str | None,
        max_files: int,
        limit: int,
        context: int,
    ) -> dict[str, Any]:
        normalized_symbol = symbol_name.strip()
        if not normalized_symbol:
            raise ValueError("symbol_name is required")
        index = self.build_index(path_service=path_service, thread_id=thread_id, path=path, max_files=max_files)
        if index.reference_resolver is not None:
            resolved = index.reference_resolver.find_definitions(
                path_service=path_service,
                thread_id=thread_id,
                index=index,
                symbol_name=normalized_symbol,
                file_path=file_path,
                limit=limit,
                context=context,
            )
            if resolved is not None:
                return resolved
        definitions = _definition_matches_from_index(
            index=index,
            symbol_name=normalized_symbol,
            file_path=file_path,
        )
        bounded = bounded_limit(limit)
        bounded_context = max(0, min(context, 5))
        returned = _attach_location_context(
            definitions[:bounded],
            path_service=path_service,
            thread_id=thread_id,
            context=bounded_context,
        )
        return {
            **index.metadata(),
            "symbol_name": normalized_symbol,
            "file_path": file_path,
            "definitions": returned,
            "returned": len(returned),
            "total_estimate": len(definitions),
            "truncated": len(definitions) > len(returned),
        }


def find_focus_node(nodes: tuple[dict[str, Any], ...] | list[dict[str, Any]], focus: str) -> dict[str, Any] | None:
    normalized = focus.replace("\\", "/").strip().lower()
    if not normalized:
        return None
    exact = [
        node
        for node in nodes
        if normalized in {str(node["path"]).lower(), str(node["relative_path"]).lower()}
    ]
    if exact:
        return exact[0]
    suffix = [
        node
        for node in nodes
        if str(node["path"]).replace("\\", "/").lower().endswith(normalized)
        or str(node["relative_path"]).replace("\\", "/").lower().endswith(normalized)
    ]
    if suffix:
        return sorted(suffix, key=lambda node: len(str(node["relative_path"])))[0]
    contains = [
        node
        for node in nodes
        if normalized in str(node["path"]).replace("\\", "/").lower()
        or normalized in str(node["relative_path"]).replace("\\", "/").lower()
    ]
    if contains:
        return sorted(contains, key=lambda node: len(str(node["relative_path"])))[0]
    return None


def symbol_reference_pattern(symbol_name: str) -> re.Pattern[str]:
    if re.match(r"^[A-Za-z_$][\w$]*$", symbol_name):
        return re.compile(rf"(?<![\w$]){re.escape(symbol_name)}(?![\w$])")
    return re.compile(re.escape(symbol_name))


def summarize_external_index_payload(
    payload: dict[str, Any],
    *,
    root: str | None = None,
    current_fingerprint: str | None = None,
    current_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        errors.append("nodes must be a list")
        nodes = []
    normalized_nodes: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"nodes[{index}] must be an object")
            continue
        normalized = _normalize_external_node(node)
        if not normalized["path"]:
            errors.append(f"nodes[{index}].path is required")
            continue
        normalized_nodes.append(normalized)

    edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    invalid_edges = sum(1 for edge in edges if not isinstance(edge, dict) or not edge.get("from") or not edge.get("to"))
    if invalid_edges:
        errors.append(f"{invalid_edges} edges are missing from/to")
    symbol_count = sum(len(node.get("symbols") or []) for node in normalized_nodes)
    fingerprint = str(payload.get("fingerprint") or "")
    freshness = "unchecked"
    fresh: bool | None = None
    if current_fingerprint is not None:
        if fingerprint and fingerprint == current_fingerprint:
            freshness = "fresh"
            fresh = True
        elif fingerprint:
            freshness = "stale"
            fresh = False
        else:
            freshness = "unknown_freshness"
            fresh = False
    drift = _external_index_drift(
        index_nodes=normalized_nodes,
        current_payload=current_payload,
    ) if current_payload is not None else None
    return {
        "valid": not errors and bool(normalized_nodes),
        "root": str(payload.get("root") or root or ""),
        "fingerprint": fingerprint,
        "current_fingerprint": current_fingerprint,
        "fresh": fresh,
        "freshness": freshness,
        "node_count": len(normalized_nodes),
        "symbol_count": symbol_count,
        "edge_count": len(edges),
        "invalid_edge_count": invalid_edges,
        "errors": errors[:20],
        "errors_truncated": len(errors) > 20,
        **({"drift": drift} if drift is not None else {}),
    }


def _external_index_drift(*, index_nodes: list[dict[str, Any]], current_payload: dict[str, Any]) -> dict[str, Any]:
    current_nodes_raw = current_payload.get("nodes") if isinstance(current_payload.get("nodes"), list) else []
    current_nodes = [
        _normalize_external_node(node)
        for node in current_nodes_raw
        if isinstance(node, dict)
    ]
    index_by_path = {str(node.get("relative_path") or node.get("path") or ""): node for node in index_nodes}
    current_by_path = {str(node.get("relative_path") or node.get("path") or ""): node for node in current_nodes}
    added_paths = sorted(path for path in current_by_path if path and path not in index_by_path)
    removed_paths = sorted(path for path in index_by_path if path and path not in current_by_path)
    changed_paths: list[str] = []
    for path in sorted(set(index_by_path) & set(current_by_path)):
        old_symbols = sorted(str(symbol.get("name") or "") for symbol in index_by_path[path].get("symbols") or [])
        new_symbols = sorted(str(symbol.get("name") or "") for symbol in current_by_path[path].get("symbols") or [])
        old_imports = sorted(str(item) for item in index_by_path[path].get("imports") or [])
        new_imports = sorted(str(item) for item in current_by_path[path].get("imports") or [])
        if old_symbols != new_symbols or old_imports != new_imports:
            changed_paths.append(path)
    return {
        "added_paths": added_paths[:20],
        "removed_paths": removed_paths[:20],
        "changed_paths": changed_paths[:20],
        "added_count": len(added_paths),
        "removed_count": len(removed_paths),
        "changed_count": len(changed_paths),
        "truncated": len(added_paths) > 20 or len(removed_paths) > 20 or len(changed_paths) > 20,
    }


def _normalize_external_node(node: dict[str, Any]) -> dict[str, Any]:
    path = str(node.get("path") or node.get("virtual_path") or "")
    relative_path = str(node.get("relative_path") or node.get("name") or path.rsplit("/", 1)[-1])
    symbols = node.get("symbols") if isinstance(node.get("symbols"), list) else []
    return {
        "path": path,
        "relative_path": relative_path,
        "language": str(node.get("language") or "unknown"),
        "kind": str(node.get("kind") or "code"),
        "imports": list(node.get("imports") or []),
        "symbols": [
            {
                "name": str(symbol.get("name") or ""),
                "kind": str(symbol.get("kind") or "symbol"),
                "line": int(symbol.get("line") or 0),
            }
            for symbol in symbols
            if isinstance(symbol, dict) and symbol.get("name")
        ],
        "owners": list(node.get("owners") or []),
        "security_findings": list(node.get("security_findings") or []),
        "patterns": list(node.get("patterns") or []),
        "line_count": int(node.get("line_count") or 0),
        "size_bytes": int(node.get("size_bytes") or 0),
    }


class _LspJsonRpcSession:
    def __init__(
        self,
        *,
        command: list[str],
        cwd: str | None,
        env: dict[str, str],
        timeout_seconds: float,
        stderr_max_chars: int = 2000,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.timeout_seconds = timeout_seconds
        self.stderr_max_chars = max(0, min(int(stderr_max_chars), 10000))
        self._next_id = 1
        self._responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stderr_tail = ""
        self._stderr_lock = threading.Lock()

    def __enter__(self) -> "_LspJsonRpcSession":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> "_LspJsonRpcSession":
        if self.is_running():
            return self
        self._process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env={**os.environ, **self.env} if self.env else None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._reader = threading.Thread(target=self._read_loop, name="anvil-lsp-jsonrpc-reader", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr_loop, name="anvil-lsp-jsonrpc-stderr", daemon=True)
        self._stderr_reader.start()
        return self

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def returncode(self) -> int | None:
        return self._process.returncode if self._process is not None else None

    def stderr_tail(self) -> str:
        with self._stderr_lock:
            return _sanitize_lsp_diagnostic(self._stderr_tail)

    def diagnostics(self) -> list[str]:
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=0.05)
        diagnostics: list[str] = []
        if self._process is not None and self._process.poll() is not None:
            diagnostics.append(f"lsp_jsonrpc process exited with code {self._process.returncode}")
        stderr_tail = self.stderr_tail()
        if stderr_tail:
            diagnostics.append(f"lsp_jsonrpc stderr: {stderr_tail}")
        return _dedupe_lsp_diagnostics(diagnostics)

    def close(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            try:
                self.request("shutdown", None, timeout_seconds=min(self.timeout_seconds, 1.0))
                self.notify("exit", {})
            except (OSError, TimeoutError, subprocess.SubprocessError):
                pass
        try:
            self._process.terminate()
            self._process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self._process.kill()
            except OSError:
                pass
        self._process = None

    def request(self, method: str, params: Any, *, timeout_seconds: float | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + (timeout_seconds or self.timeout_seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"lsp_jsonrpc request timed out: {method}")
            try:
                message = self._responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"lsp_jsonrpc request timed out: {method}") from exc
            if message is None:
                raise subprocess.SubprocessError(f"lsp_jsonrpc process exited before response: {method}")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise ValueError(f"lsp_jsonrpc error from {method}: {message['error']}")
            return message.get("result")

    def notify(self, method: str, params: Any) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise subprocess.SubprocessError("lsp_jsonrpc process is not running")
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        self._process.stdin.flush()

    def _read_loop(self) -> None:
        try:
            while self._process is not None and self._process.stdout is not None:
                message = self._read_message(self._process.stdout)
                if message is None:
                    break
                self._responses.put(message)
        finally:
            self._responses.put(None)

    def _read_stderr_loop(self) -> None:
        if self._process is None or self._process.stderr is None or self.stderr_max_chars <= 0:
            return
        while True:
            chunk = self._process.stderr.read(256)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            with self._stderr_lock:
                self._stderr_tail = (self._stderr_tail + text)[-self.stderr_max_chars :]

    def _read_message(self, stream) -> dict[str, Any] | None:
        content_length: int | None = None
        while True:
            line = stream.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                break
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())
        if content_length is None:
            return None
        body = stream.read(content_length)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))


def _iter_lsp_files(root: Path, *, max_files: int) -> list[Path]:
    return _scan_lsp_files(root, max_files=max_files).files


def _scan_lsp_files(root: Path, *, max_files: int) -> _LspFileScan:
    files: list[Path] = []
    max_scanned_paths = _bounded_lsp_scan_path_limit()
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
                    if entry.name in _LSP_IGNORED_DIRS:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending_dirs.append(Path(entry.path))
                            continue
                        is_file = entry.is_file(follow_symlinks=False)
                    except OSError:
                        continue
                    candidate = Path(entry.path)
                    if is_file and candidate.suffix.lower() in _LSP_CODE_EXTENSIONS:
                        files.append(candidate)
        except OSError:
            continue
    return _LspFileScan(
        files=sorted(files, key=lambda item: item.as_posix()),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _lsp_workspace_snapshot(scan: _LspFileScan, *, root: Path) -> _LspWorkspaceSnapshot:
    return _LspWorkspaceSnapshot(
        fingerprint=_fingerprint_lsp_files(scan.files, root=root),
        file_count=len(scan.files),
        scanned_path_count=scan.scanned_path_count,
        max_scanned_paths=scan.max_scanned_paths,
        scan_truncated=scan.scan_truncated,
    )


def lsp_workspace_probe(
    *,
    path_service: PathService,
    thread_id: str,
    path: str,
    max_files: int,
) -> LspWorkspaceProbe:
    root = path_service.resolve_virtual_path(thread_id, path)
    if not root.exists():
        raise ValueError(f"path does not exist: {path}")
    search_root = root.parent if root.is_file() else root
    if root.is_file():
        scan = _LspFileScan(
            files=[root],
            scanned_path_count=1,
            max_scanned_paths=1,
            scan_truncated=False,
        )
    else:
        scan = _scan_lsp_files(search_root, max_files=max_files)
    return LspWorkspaceProbe(root=search_root, files=scan.files, snapshot=_lsp_workspace_snapshot(scan, root=search_root))


def _bounded_lsp_scan_path_limit() -> int:
    try:
        configured = int(DEFAULT_LSP_SCAN_PATH_LIMIT)
    except (TypeError, ValueError):
        configured = 10_000
    return max(1, min(configured, MAX_LSP_SCAN_PATH_LIMIT))


def _lsp_scan_diagnostics(snapshot: _LspWorkspaceSnapshot) -> list[str]:
    if not snapshot.scan_truncated:
        return []
    return [
        (
            "lsp workspace scan truncated "
            f"after {snapshot.scanned_path_count}/{snapshot.max_scanned_paths} paths"
        )
    ]


def _fingerprint_lsp_files(files: list[Path], *, root: Path) -> str:
    metadata: list[dict[str, Any]] = []
    for file_path in files:
        try:
            stat = file_path.stat()
            relative = file_path.relative_to(root).as_posix()
        except OSError:
            continue
        metadata.append({"path": relative, "mtime_ns": stat.st_mtime_ns, "size": stat.st_size})
    return hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _read_lsp_file(file_path: Path) -> str:
    try:
        return file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _language_id_for_lsp(file_path: Path) -> str:
    return _LSP_LANGUAGE_IDS.get(file_path.suffix.lower(), file_path.suffix.lower().lstrip(".") or "plaintext")


def _dedupe_lsp_diagnostics(diagnostics: list[str]) -> list[str]:
    seen: set[str] = set()
    clean: list[str] = []
    for diagnostic in diagnostics:
        sanitized = _sanitize_lsp_diagnostic(diagnostic)
        if not sanitized or sanitized in seen:
            continue
        seen.add(sanitized)
        clean.append(sanitized)
        if len(clean) >= 10:
            break
    return clean


def _sanitize_lsp_diagnostic(value: Any) -> str:
    text = str(value or "").replace("\r", "\n")
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    text = _SECRETISH_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = _LONG_TOKEN_RE.sub("[REDACTED]", text)
    text = re.sub(r"file:///[^\s)'\"]+", "file:///[REDACTED]", text)
    text = re.sub(r"(?i)([A-Z]:\\)[^\s)'\"]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?<![\w.-])/(?:Users|home|mnt|tmp|var|private|workspace|opt)/[^\s)'\"]+", "/[REDACTED]", text)
    return text[:1000]


def _semantic_lsp_client_capabilities() -> dict[str, Any]:
    return {
        "textDocument": {
            "documentSymbol": {
                "dynamicRegistration": False,
                "hierarchicalDocumentSymbolSupport": True,
            },
            "definition": {"dynamicRegistration": False},
            "references": {"dynamicRegistration": False},
        }
    }


def _definition_reference_lsp_client_capabilities() -> dict[str, Any]:
    return _semantic_lsp_client_capabilities()


def _lsp_session_pool() -> _LspSessionPool:
    global _LSP_SESSION_POOL
    if _LSP_SESSION_POOL is None:
        _LSP_SESSION_POOL = _LspSessionPool()
    return _LSP_SESSION_POOL


def close_lsp_session_pool() -> None:
    global _LSP_SESSION_POOL
    if _LSP_SESSION_POOL is not None:
        _LSP_SESSION_POOL.close_all()
        _LSP_SESSION_POOL = None


def lsp_session_pool_health(
    *,
    idle_ttl_seconds: float = 300.0,
    current_workspace_root: Path | None = None,
    current_workspace_snapshot: _LspWorkspaceSnapshot | None = None,
) -> dict[str, Any]:
    if _LSP_SESSION_POOL is None:
        return {
            "configured": True,
            "session_count": 0,
            "running_session_count": 0,
            "sessions": [],
            "sessions_truncated": False,
            "recent_failures": list(_LSP_POOL_RECENT_FAILURES[-10:]),
        }
    return _LSP_SESSION_POOL.health(
        idle_ttl_seconds=max(0.0, float(idle_ttl_seconds)),
        current_workspace_root=current_workspace_root,
        current_workspace_snapshot=current_workspace_snapshot,
    )


def lsp_session_pool_recover(
    *,
    idle_ttl_seconds: float = 300.0,
    current_workspace_root: Path,
    current_workspace_snapshot: _LspWorkspaceSnapshot,
) -> dict[str, Any]:
    if _LSP_SESSION_POOL is None:
        return {
            "configured": True,
            "action": "noop",
            "session_count_before": 0,
            "running_session_count_before": 0,
            "recovered_session_count": 0,
            "recovered_sessions": [],
            "recovered_sessions_truncated": False,
        }
    return _LSP_SESSION_POOL.recover_stale(
        idle_ttl_seconds=max(0.0, float(idle_ttl_seconds)),
        current_workspace_root=current_workspace_root,
        current_workspace_snapshot=current_workspace_snapshot,
    )


def _lsp_session_health_payload(
    session: _PooledLspSession,
    *,
    current_workspace_root: Path | None,
    current_workspace_snapshot: _LspWorkspaceSnapshot | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key_hash": _lsp_pool_key_hash(session.key),
        "running": session.session.is_running(),
        "capabilities": _lsp_capabilities_payload(session.capabilities),
        "workspace_fingerprint_hash": _lsp_pool_key_hash((session.workspace_snapshot.fingerprint,)),
        "workspace_file_count": session.workspace_snapshot.file_count,
        "workspace_scanned_path_count": session.workspace_snapshot.scanned_path_count,
        "workspace_max_scanned_paths": session.workspace_snapshot.max_scanned_paths,
        "workspace_scan_truncated": session.workspace_snapshot.scan_truncated,
        "idle_seconds": round(session.idle_for(), 3),
        "age_seconds": round(max(0.0, time.monotonic() - session.created_at), 3),
        "diagnostics": session.diagnostics(),
    }
    if current_workspace_root is None or current_workspace_snapshot is None:
        payload.update(
            {
                "workspace_freshness": "unknown",
                "workspace_fresh": None,
                "needs_restart": False,
            }
        )
        return payload
    if not _same_resolved_path(session.workspace_root, current_workspace_root):
        payload.update(
            {
                "workspace_freshness": "not_applicable",
                "workspace_fresh": None,
                "needs_restart": False,
            }
        )
        return payload
    fresh = session.workspace_snapshot.fingerprint == current_workspace_snapshot.fingerprint
    payload.update(
        {
            "workspace_freshness": "fresh" if fresh else "stale",
            "workspace_fresh": fresh,
            "needs_restart": not fresh,
            "current_workspace_fingerprint_hash": _lsp_pool_key_hash((current_workspace_snapshot.fingerprint,)),
            "current_workspace_file_count": current_workspace_snapshot.file_count,
            "current_workspace_scanned_path_count": current_workspace_snapshot.scanned_path_count,
            "current_workspace_max_scanned_paths": current_workspace_snapshot.max_scanned_paths,
            "current_workspace_scan_truncated": current_workspace_snapshot.scan_truncated,
        }
    )
    if not fresh:
        payload["recommendation"] = "Next semantic tool call will restart this LSP session before use."
    return payload


def _same_resolved_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


def _record_lsp_pool_failure(*, key: tuple[Any, ...], diagnostics: list[str]) -> None:
    _LSP_POOL_RECENT_FAILURES.append(
        {
            "key_hash": _lsp_pool_key_hash(key),
            "time": round(time.time(), 3),
            "diagnostics": _dedupe_lsp_diagnostics(diagnostics),
        }
    )
    del _LSP_POOL_RECENT_FAILURES[:-10]


def _lsp_pool_key_hash(key: tuple[Any, ...]) -> str:
    encoded = json.dumps(key, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _lsp_capabilities_payload(capabilities: _LspCapabilities) -> dict[str, bool]:
    return {
        "document_symbols": capabilities.document_symbols,
        "definitions": capabilities.definitions,
        "references": capabilities.references,
    }


def _lsp_capabilities(initialize_result: Any) -> _LspCapabilities:
    if not isinstance(initialize_result, dict):
        return _LspCapabilities()
    capabilities = initialize_result.get("capabilities")
    if not isinstance(capabilities, dict):
        return _LspCapabilities()
    return _LspCapabilities(
        document_symbols=_provider_enabled(capabilities.get("documentSymbolProvider")),
        definitions=_provider_enabled(capabilities.get("definitionProvider")),
        references=_provider_enabled(capabilities.get("referencesProvider")),
    )


def _provider_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return True
    return False


def _normalize_lsp_document_symbols(result: Any, *, default_uri: str) -> list[dict[str, Any]]:
    if not isinstance(result, list):
        return []
    symbols: list[dict[str, Any]] = []
    for item in result:
        if isinstance(item, dict):
            _collect_lsp_symbol(item, symbols, default_uri=default_uri)
    return sorted(symbols, key=lambda item: (str(item.get("uri") or ""), int(item["line"]), str(item["name"])))


def _collect_lsp_symbol(item: dict[str, Any], symbols: list[dict[str, Any]], *, default_uri: str) -> None:
    name = str(item.get("name") or "")
    if not name:
        return
    kind = _LSP_SYMBOL_KIND_NAMES.get(int(item.get("kind") or 0), "symbol")
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    item_range = item.get("range") if isinstance(item.get("range"), dict) else location.get("range")
    start = item_range.get("start") if isinstance(item_range, dict) and isinstance(item_range.get("start"), dict) else {}
    line = int(start.get("line") or 0) + 1
    character = int(start.get("character") or 0)
    uri = str(location.get("uri") or default_uri)
    symbols.append({"name": name, "kind": kind, "line": line, "character": character, "uri": uri})
    children = item.get("children") if isinstance(item.get("children"), list) else []
    for child in children:
        if isinstance(child, dict):
            _collect_lsp_symbol(child, symbols, default_uri=uri)


def _same_file_uri(uri: str, file_path: Path) -> bool:
    uri_path = _path_from_file_uri(uri)
    if uri_path is None:
        return False
    try:
        return uri_path.resolve() == file_path.resolve()
    except OSError:
        return False


def _path_from_file_uri(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    uri_path_text = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", uri_path_text):
        uri_path_text = uri_path_text[1:]
    return Path(uri_path_text)


def _reference_focus_node(
    nodes: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    symbol_name: str,
    *,
    file_path: str | None,
) -> dict[str, Any] | None:
    if file_path:
        node = find_focus_node(nodes, file_path)
        if node is not None and _reference_focus_symbol(node, symbol_name) is not None:
            return node
    normalized = symbol_name.lower()
    for node in nodes:
        for symbol in node.get("symbols") or []:
            if str(symbol.get("name") or "").lower() == normalized:
                return node
    return None


def _reference_focus_symbol(node: dict[str, Any], symbol_name: str) -> dict[str, Any] | None:
    normalized = symbol_name.lower()
    exact = [
        symbol
        for symbol in node.get("symbols") or []
        if str(symbol.get("name") or "").lower() == normalized
    ]
    if exact:
        return dict(exact[0])
    contains = [
        symbol
        for symbol in node.get("symbols") or []
        if normalized in str(symbol.get("name") or "").lower()
    ]
    return dict(contains[0]) if contains else None


def _normalize_lsp_locations(
    result: Any,
    *,
    path_service: PathService,
    thread_id: str,
    workspace_root: Path,
    symbol_name: str,
) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        items = [result]
    elif isinstance(result, list):
        items = result
    else:
        return []
    locations: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        location = item.get("targetUri") or item.get("uri")
        item_range = item.get("targetRange") or item.get("range")
        if not isinstance(location, str) or not isinstance(item_range, dict):
            continue
        start = item_range.get("start") if isinstance(item_range.get("start"), dict) else {}
        line = int(start.get("line") or 0) + 1
        file_path = _path_from_file_uri(location)
        if file_path is None:
            continue
        try:
            virtual_path = path_service.to_virtual_path(thread_id, file_path)
            relative_path = file_path.resolve().relative_to(workspace_root.resolve()).as_posix()
        except (OSError, ValueError):
            continue
        key = (virtual_path, line)
        if key in seen:
            continue
        seen.add(key)
        snippet = _reference_line_snippet(file_path=file_path, line=line)
        locations.append(
            {
                "path": virtual_path,
                "relative_path": relative_path,
                "line": line,
                "snippet": snippet or symbol_name,
                "context": [],
            }
        )
    return locations


def _definition_matches_from_index(
    *,
    index: CodeSemanticIndex,
    symbol_name: str,
    file_path: str | None,
) -> list[dict[str, Any]]:
    normalized = symbol_name.lower()
    nodes: tuple[dict[str, Any] | None, ...]
    if file_path:
        nodes = (find_focus_node(index.nodes, file_path),)
    else:
        nodes = index.nodes
    definitions: list[dict[str, Any]] = []
    for node in nodes:
        if node is None:
            continue
        for symbol in node.get("symbols") or []:
            if str(symbol.get("name") or "").lower() != normalized:
                continue
            definitions.append(
                {
                    "name": str(symbol.get("name") or symbol_name),
                    "kind": str(symbol.get("kind") or "symbol"),
                    "path": node["path"],
                    "relative_path": node["relative_path"],
                    "line": int(symbol.get("line") or 0),
                    "snippet": str(symbol.get("name") or symbol_name),
                    "context": [],
                }
            )
    return sorted(definitions, key=lambda item: (str(item["relative_path"]), int(item["line"]), str(item["name"])))


def _attach_location_context(
    locations: list[dict[str, Any]],
    *,
    path_service: PathService,
    thread_id: str,
    context: int,
) -> list[dict[str, Any]]:
    if context <= 0:
        return [{**item, "context": []} for item in locations]
    return [
        {
            **item,
            "context": _reference_context(
                path_service=path_service,
                thread_id=thread_id,
                virtual_path=str(item["path"]),
                line=int(item["line"]),
                context=context,
            ),
        }
        for item in locations
    ]


def _reference_line_snippet(*, file_path: Path, line: int) -> str:
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    if line < 1 or line > len(lines):
        return ""
    return lines[line - 1].strip()[:240]


def _reference_context(
    *,
    path_service: PathService,
    thread_id: str,
    virtual_path: str,
    line: int,
    context: int,
) -> list[dict[str, Any]]:
    try:
        host_path = path_service.resolve_virtual_path(thread_id, virtual_path)
        lines = host_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    if line < 1 or line > len(lines):
        return []
    start = max(line - context, 1)
    end = min(line + context, len(lines))
    return [
        {
            "line": line_number,
            "text": lines[line_number - 1][:240],
        }
        for line_number in range(start, end + 1)
    ]
