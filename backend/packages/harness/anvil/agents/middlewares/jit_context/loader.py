"""Context loader for lazy loading different context types."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
import time
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anvil.agents.lead_agent.context_files import build_project_context_snapshot
from anvil.memory import sanitize_memory_context_text
from anvil.memory.hcms_v2 import memory_injection_view_v2_from_legacy, memory_injection_view_v2_to_blocks
from anvil.runtime.context_v2 import ContextBlock, ContextSource, ContextSourceKind, EvidenceRef, stable_context_id
from anvil.runtime.token_budget import TokenBudgetService
from anvil.skills.service import normalize_skill_id

from .contracts import ContextRequest, ContextResponse, ContextType

if TYPE_CHECKING:
    from anvil.agents.lead_agent.types import LeadAgentContext
    from anvil.config import EffectiveConfig
    from .cache import ContextCache

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONTEXT_CHARS = 8_000
_MAX_CONTEXT_CHARS = 40_000
_FILE_READ_BYTE_MULTIPLIER = 4
_DIRECT_PROJECT_FILE_NAMES = {"readme.md", "readme_zh.md", "agents.md", "project_rules.md"}
_PROJECT_CONTEXT_ALIASES = {"", "*", ".", "project", "workspace", "context", "context_files"}
_CONTEXT_SECTION_TAGS = (
    "jit_context",
    "project_context_files",
    "context_file",
    "memory_context",
    "memory_recall",
)
_XML_CONTEXT_SECTION_PATTERN = re.compile(
    r"</?(?:jit_context|project_context_files|context_file|memory_context|memory_recall)(?:\s+[^>]*)?>",
    re.IGNORECASE,
)
_BRACKETED_CONTEXT_SECTION_PATTERN = re.compile(
    r"\[/?(?:jit_context|project_context_files|context_file|memory_context|memory_recall)(?:\s+[^\]\r\n]*)?\]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _LoadedContext:
    content: str
    metadata: dict[str, Any]


class ContextLoader:
    """Loads context on-demand from various sources.

    Supports:
    - Memory context (from HCMS recall services)
    - File context (from path service)
    - Skill context (from skills service)
    - Tool context (from tool registry)
    - Conversation context (from message history)
    """

    def __init__(
        self,
        effective_config: EffectiveConfig | None = None,
        cache: ContextCache | None = None,
        max_load_time_ms: float = 500.0,
        parallel_loading: bool = True,
        max_parallel_loads: int = 3,
    ):
        """Initialize context loader.

        Args:
            effective_config: Application configuration
            cache: Context cache instance
            max_load_time_ms: Maximum load time per item
            parallel_loading: Enable parallel loading
            max_parallel_loads: Max concurrent loads
        """
        self.effective_config = effective_config
        self.cache = cache
        self.max_load_time_ms = max_load_time_ms
        self.parallel_loading = parallel_loading
        self.max_parallel_loads = max_parallel_loads

    def load(
        self,
        request: ContextRequest,
        context: LeadAgentContext | None = None
    ) -> ContextResponse | None:
        """Load context based on request.

        Args:
            request: Context load request
            context: Runtime context (optional)

        Returns:
            Context response if successful, None on error
        """
        start_time = time.time()
        cache_key = self._cache_key(request, context)
        use_cache = self.cache is not None and request.context_type != ContextType.MEMORY

        # Check cache first
        if use_cache and self.cache:
            cached_content = self.cache.get(cache_key)
            if cached_content:
                load_time_ms = (time.time() - start_time) * 1000
                logger.debug("Cache hit for %s", cache_key)
                return ContextResponse(
                    context_type=request.context_type,
                    identifier=request.identifier,
                    content=cached_content,
                    tokens=self._estimate_tokens(cached_content),
                    cached=True,
                    load_time_ms=load_time_ms
                )

        # Load from source
        try:
            loaded = self._load_from_source(request, context)
            if loaded is None:
                return None
            if isinstance(loaded, _LoadedContext):
                content = loaded.content
                metadata = dict(loaded.metadata)
            else:
                content = loaded
                metadata = {}

            load_time_ms = (time.time() - start_time) * 1000

            # Cache the result
            if use_cache and self.cache:
                tokens = self._estimate_tokens(content)
                self.cache.put(cache_key, content, tokens)

            return ContextResponse(
                context_type=request.context_type,
                identifier=request.identifier,
                content=content,
                tokens=self._estimate_tokens(content),
                cached=False,
                load_time_ms=load_time_ms,
                metadata=metadata,
            )

        except Exception as e:
            logger.error("Failed to load context %s:%s: %s", request.context_type, request.identifier, e)
            if request.required:
                raise
            return None

    def _load_from_source(
        self,
        request: ContextRequest,
        context: LeadAgentContext | None
    ) -> str | _LoadedContext | None:
        """Load content from appropriate source.

        Args:
            request: Context load request
            context: Runtime context

        Returns:
            Loaded content or None
        """
        if request.context_type == ContextType.MEMORY:
            return self._load_memory(request, context)
        elif request.context_type == ContextType.FILE:
            return self._load_file(request, context)
        elif request.context_type == ContextType.SKILL:
            return self._load_skill(request, context)
        elif request.context_type == ContextType.TOOL:
            return self._load_tool(request, context)
        elif request.context_type == ContextType.CONVERSATION:
            return self._load_conversation(request, context)
        elif request.context_type == ContextType.PROJECT:
            return self._load_project(request, context)
        else:
            logger.warning("Unknown context type: %s", request.context_type)
            return None

    def _load_memory(self, request: ContextRequest, context: LeadAgentContext | None) -> str | _LoadedContext | None:
        """Load memory context from HCMS recall services."""
        if context is None or context.memory_manager is None:
            return None

        try:
            manager = context.memory_manager
            query = request.identifier.strip()
            if not query:
                return None
            if hasattr(manager, "prefetch_recall"):
                recall = manager.prefetch_recall(thread_id=context.thread_id, query=query)
                context_v2_loaded = self._load_memory_recall_as_context_v2(
                    recall,
                    query=query,
                    request=request,
                    context=context,
                )
                if context_v2_loaded is not None:
                    return context_v2_loaded
                rendered = recall.render_turn_block() if hasattr(recall, "render_turn_block") else str(recall)
                return self._load_unstructured_memory_recall_as_context_v2(
                    rendered,
                    recall=recall,
                    query=query,
                    request=request,
                    context=context,
                )
            if hasattr(manager, "search_sessions"):
                result = manager.search_sessions(
                    query=query,
                    current_thread_id=context.thread_id,
                    scope="exclude_current",
                    limit=self._metadata_int(request, "limit", default=3, minimum=1, maximum=10),
                    mode=str(request.metadata.get("mode") or "search"),
                )
                return self._format_context_block(
                    context_type=request.context_type,
                    identifier=query,
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    request=request,
                    attrs={"thread_id": context.thread_id},
                )
            return None

        except Exception as e:
            logger.error("Failed to load memory %s: %s", request.identifier, e)
            return None

    def _load_memory_recall_as_context_v2(
        self,
        recall: Any,
        *,
        query: str,
        request: ContextRequest,
        context: LeadAgentContext,
    ) -> _LoadedContext | None:
        injection = getattr(recall, "injection", None)
        if injection is None:
            return None

        token_budget = TokenBudgetService()
        view = memory_injection_view_v2_from_legacy(injection, query=query, token_budget=token_budget)
        blocks = [
            block.model_dump(mode="json")
            for block in memory_injection_view_v2_to_blocks(view, token_budget=token_budget)
        ]
        if not blocks:
            return None

        existing_blocks = list(getattr(context, "context_v2_memory_blocks", ()) or ())
        merged_blocks = _merge_context_v2_blocks(existing_blocks, blocks)
        context.context_v2_memory_blocks = merged_blocks

        block_ids = [str(block.get("block_id") or "") for block in blocks if block.get("block_id")]
        snapshot_id = str(getattr(recall, "snapshot_fingerprint", "") or "")
        diagnostics = dict(getattr(context, "memory_injection_diagnostics", {}) or {})
        diagnostics.update(
            {
                "source": "jit_context_loader",
                "status": "injected",
                "injection_mode": "context_v2",
                "snapshot_id": snapshot_id,
                "context_v2_block_count": len(blocks),
                "memory_match_count": len(blocks),
                "evidence_count": sum(len(block.get("evidence_refs", ()) or ()) for block in blocks),
                "rendered_tokens": sum(int(block.get("token_cost") or 0) for block in blocks),
            }
        )
        context.memory_injection_diagnostics = diagnostics

        content_payload = {
            "injection_mode": "context_v2",
            "query": query,
            "snapshot_id": snapshot_id,
            "blocks": blocks,
            "diagnostics": {
                "block_count": len(blocks),
                "block_ids": block_ids,
                "source": "jit_context_loader",
            },
        }
        content = self._format_context_block(
            context_type=request.context_type,
            identifier=query,
            content=json.dumps(content_payload, ensure_ascii=False, sort_keys=True, default=str),
            request=request,
            attrs={
                "thread_id": context.thread_id,
                "injection_mode": "context_v2",
                "block_count": str(len(blocks)),
            },
        )
        if content is None:
            return None
        return _LoadedContext(
            content=content,
            metadata={
                "source": "jit_context_loader",
                "status": "injected",
                "injection_mode": "context_v2",
                "snapshot_id": snapshot_id,
                "context_v2_block_count": len(blocks),
                "context_v2_memory_block_ids": block_ids,
            },
        )

    def _load_unstructured_memory_recall_as_context_v2(
        self,
        rendered: str,
        *,
        recall: Any,
        query: str,
        request: ContextRequest,
        context: LeadAgentContext,
    ) -> _LoadedContext | None:
        sanitized = _strip_context_section_fence_tags(rendered)
        if not sanitized:
            return None

        token_budget = TokenBudgetService()
        snapshot_id = str(getattr(recall, "snapshot_fingerprint", "") or "")
        block = ContextBlock(
            block_id=stable_context_id("jit-memory", context.thread_id, query, snapshot_id, sanitized),
            block_type="retrieved_memory",
            source=ContextSource(
                kind=ContextSourceKind.MEMORY,
                name="jit_context_loader",
                ref=snapshot_id or None,
                metadata={"query": query, "unstructured": True},
            ),
            title="JIT Memory Recall",
            content=sanitized,
            token_cost=token_budget.count_text(sanitized),
            priority=0.55,
            salience=0.55,
            confidence=0.5,
            position_hint="memory:jit",
            evidence_refs=(
                EvidenceRef(
                    ref_id=stable_context_id("jit-memory-evidence", context.thread_id, query, snapshot_id),
                    source_kind="memory_recall",
                    source_id=snapshot_id or context.thread_id,
                    span=token_budget.truncate_text(sanitized, max_tokens=80, max_chars=320),
                    confidence=0.5,
                ),
            ),
            tags=("memory", "jit_context", "unstructured_recall"),
            metadata={"source": "jit_context_loader", "injection_mode": "context_v2_unstructured"},
        )
        blocks = [block.model_dump(mode="json")]
        context.context_v2_memory_blocks = _merge_context_v2_blocks(
            list(getattr(context, "context_v2_memory_blocks", ()) or ()),
            blocks,
        )
        diagnostics = dict(getattr(context, "memory_injection_diagnostics", {}) or {})
        diagnostics.update(
            {
                "source": "jit_context_loader",
                "status": "injected",
                "injection_mode": "context_v2_unstructured",
                "snapshot_id": snapshot_id,
                "context_v2_block_count": 1,
                "memory_match_count": 1,
                "evidence_count": 1,
                "rendered_tokens": block.token_cost,
            }
        )
        context.memory_injection_diagnostics = diagnostics
        block_id = block.block_id
        content_payload = {
            "injection_mode": "context_v2_unstructured",
            "query": query,
            "snapshot_id": snapshot_id,
            "blocks": blocks,
            "diagnostics": {
                "block_count": 1,
                "block_ids": [block_id],
                "source": "jit_context_loader",
            },
        }
        content = self._format_context_block(
            context_type=request.context_type,
            identifier=query,
            content=json.dumps(content_payload, ensure_ascii=False, sort_keys=True, default=str),
            request=request,
            attrs={
                "thread_id": context.thread_id,
                "injection_mode": "context_v2_unstructured",
                "block_count": "1",
            },
        )
        if content is None:
            return None
        return _LoadedContext(
            content=content,
            metadata={
                "source": "jit_context_loader",
                "status": "injected",
                "injection_mode": "context_v2_unstructured",
                "snapshot_id": snapshot_id,
                "context_v2_block_count": 1,
                "context_v2_memory_block_ids": [block_id],
            },
        )

    def _load_file(self, request: ContextRequest, context: LeadAgentContext | None) -> str | None:
        """Load file context from path service."""
        if context is None or context.path_service is None:
            return None

        try:
            virtual_path = self._normalize_requested_virtual_path(request.identifier, context)
            resolved = context.path_service.resolve_virtual_path(context.thread_id, virtual_path)
            return self._load_virtual_path_context(
                virtual_path=virtual_path,
                resolved=resolved,
                request=request,
                context_type=request.context_type,
            )

        except Exception as e:
            logger.error("Failed to load file %s: %s", request.identifier, e)
            return None

    def _load_skill(self, request: ContextRequest, context: LeadAgentContext | None) -> str | None:
        """Load skill context from skills service."""
        if context is None or context.skills_service is None:
            return None

        try:
            config_result = getattr(context, "config_result", None)
            if config_result is None:
                return None
            skill_id = normalize_skill_id(request.identifier)
            if not skill_id:
                return None
            content = context.skills_service.get_skill_content(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
                skill_id=skill_id,
            )
            lines = [
                f"title: {content.title}",
                f"skill_id: {content.skill_id}",
                f"file_count: {content.file_count}",
                "",
                content.body,
            ]
            return self._format_context_block(
                context_type=request.context_type,
                identifier=content.skill_id,
                content="\n".join(lines),
                request=request,
                attrs={"skill_id": content.skill_id},
            )

        except Exception as e:
            logger.error("Failed to load skill %s: %s", request.identifier, e)
            return None

    def _load_tool(self, request: ContextRequest, context: LeadAgentContext | None) -> str | None:
        """Load tool context from tool registry."""
        if context is None:
            return None

        try:
            entry = self._find_tool_entry(request.identifier, context)
            if entry is None:
                return None
            payload = {
                "name": entry.name,
                "display_name": entry.display_name,
                "capability_id": entry.capability_id,
                "source_kind": self._enum_value(entry.source_kind),
                "source_id": entry.source_id,
                "capability_group": entry.capability_group,
                "summary": entry.summary,
                "risk_category": entry.risk_category,
                "deferred": entry.deferred,
                "stability": self._enum_value(entry.stability),
                "schema": entry.input_schema,
            }
            return self._format_context_block(
                context_type=request.context_type,
                identifier=entry.name,
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                request=request,
                attrs={"tool": entry.name, "source_kind": self._enum_value(entry.source_kind)},
            )

        except Exception as e:
            logger.error("Failed to load tool %s: %s", request.identifier, e)
            return None

    def _load_conversation(self, request: ContextRequest, context: LeadAgentContext | None) -> str | None:
        """Load conversation context from message history."""
        if context is None:
            return None
        try:
            query = request.identifier.strip() or "recent"
            local_payload = self._conversation_payload_from_context(query, context)
            if local_payload:
                return self._format_context_block(
                    context_type=request.context_type,
                    identifier=query,
                    content=json.dumps(local_payload, ensure_ascii=False, default=str),
                    request=request,
                    attrs={"thread_id": context.thread_id, "source": "runtime_context"},
                )

            if context.memory_manager is None:
                return None
            manager = context.memory_manager
            if not hasattr(manager, "search_sessions"):
                return None
            result = manager.search_sessions(
                query=query,
                current_thread_id=context.thread_id,
                scope=str(request.metadata.get("scope") or "current"),
                limit=self._metadata_int(request, "limit", default=3, minimum=1, maximum=10),
                mode=str(request.metadata.get("mode") or "recent"),
            )
            return self._format_context_block(
                context_type=request.context_type,
                identifier=query,
                content=json.dumps(result, ensure_ascii=False, default=str),
                request=request,
                attrs={"thread_id": context.thread_id, "source": "memory_search"},
            )
        except Exception as e:
            logger.error("Failed to load conversation %s: %s", request.identifier, e)
            return None

    def _load_project(self, request: ContextRequest, context: LeadAgentContext | None) -> str | None:
        """Load project context files such as README, AGENTS.md, and project rules."""
        if context is None or context.path_service is None:
            return None

        try:
            identifier = request.identifier.strip()
            if self._should_try_project_file(identifier):
                virtual_path = self._normalize_requested_virtual_path(identifier, context)
                resolved = context.path_service.resolve_virtual_path(context.thread_id, virtual_path)
                file_context = self._load_virtual_path_context(
                    virtual_path=virtual_path,
                    resolved=resolved,
                    request=request,
                    context_type=request.context_type,
                    attrs={"project_file": "true"},
                )
                if file_context:
                    return file_context

            config_result = getattr(context, "config_result", None)
            context_files_config = getattr(getattr(config_result, "effective_config", None), "context_files", None)
            snapshot = build_project_context_snapshot(
                path_service=context.path_service,
                thread_id=context.thread_id,
                config=context_files_config,
            )
            if not snapshot.has_content:
                return None
            content = snapshot.rendered
            if identifier and identifier not in _PROJECT_CONTEXT_ALIASES:
                matching = [
                    item for item in snapshot.files
                    if item.relative_path == identifier or Path(item.relative_path).name == identifier
                ]
                if not matching:
                    return None
                content = "\n\n".join(item.content for item in matching)
            return self._format_context_block(
                context_type=request.context_type,
                identifier=identifier or "project",
                content=content,
                request=request,
                attrs={"fingerprint": snapshot.fingerprint or "", "file_count": str(len(snapshot.files))},
            )

        except Exception as e:
            logger.error("Failed to load project context %s: %s", request.identifier, e)
            return None

    def _cache_key(self, request: ContextRequest, context: LeadAgentContext | None) -> str:
        metadata = json.dumps(request.metadata, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        thread_id = getattr(context, "thread_id", "") or ""
        config_result = getattr(context, "config_result", None)
        config_fingerprint = getattr(config_result, "fingerprint", "") if config_result is not None else ""
        return f"{thread_id}:{config_fingerprint}:{request.context_type.value}:{request.identifier}:{metadata}"

    def _normalize_requested_virtual_path(self, identifier: str, context: LeadAgentContext) -> str:
        requested = str(identifier or "").strip()
        if not requested:
            raise ValueError("context identifier is required")
        translated = context.path_service.translate_user_text_to_runtime(requested, thread_id=context.thread_id) or requested
        if translated.startswith("/mnt/"):
            return translated
        if _looks_like_windows_path(translated):
            raise ValueError(f"unsupported virtual path prefix: {identifier}")
        normalized = translated.replace("\\", "/").lstrip("/")
        if ".." in Path(normalized).parts:
            raise ValueError(f"path escapes allowed root: {identifier}")
        return f"/mnt/user-data/workspace/{normalized}" if normalized else "/mnt/user-data/workspace"

    def _read_text_window(self, path: Path, request: ContextRequest) -> str:
        max_chars = self._metadata_int(
            request,
            "read_max_chars",
            default=self._metadata_int(request, "max_chars", default=_DEFAULT_MAX_CONTEXT_CHARS, minimum=200, maximum=_MAX_CONTEXT_CHARS),
            minimum=200,
            maximum=_MAX_CONTEXT_CHARS,
        )
        max_bytes = max_chars * _FILE_READ_BYTE_MULTIPLIER + 4096
        with path.open("rb") as handle:
            raw_bytes = handle.read(max_bytes + 1)
        raw = raw_bytes[:max_bytes].decode("utf-8", errors="replace")
        if len(raw_bytes) > max_bytes:
            raw += "\n[file read truncated before context budgeting]"
        start_line = self._metadata_int(request, "line_start", default=1, minimum=1, maximum=1_000_000)
        line_count = self._metadata_int(request, "line_count", default=0, minimum=0, maximum=10_000)
        if line_count > 0:
            lines = raw.splitlines()
            raw = "\n".join(lines[start_line - 1 : start_line - 1 + line_count])
        return raw

    def _load_virtual_path_context(
        self,
        *,
        virtual_path: str,
        resolved: Path,
        request: ContextRequest,
        context_type: ContextType,
        attrs: dict[str, str] | None = None,
    ) -> str | None:
        if resolved.is_dir():
            entries = sorted(child.name + ("/" if child.is_dir() else "") for child in resolved.iterdir())[:80]
            return self._format_context_block(
                context_type=context_type,
                identifier=virtual_path,
                content="\n".join(entries),
                request=request,
                attrs={
                    "path": virtual_path,
                    "kind": "directory",
                    "entry_count": str(len(entries)),
                    **(attrs or {}),
                },
            )
        if not resolved.exists() or not resolved.is_file():
            return None
        if self._is_probably_binary(resolved):
            return None
        content = self._read_text_window(resolved, request)
        return self._format_context_block(
            context_type=context_type,
            identifier=virtual_path,
            content=content,
            request=request,
            attrs={"path": virtual_path, "kind": "file", **(attrs or {})},
        )

    def _find_tool_entry(self, identifier: str, context: LeadAgentContext) -> Any | None:
        query = str(identifier or "").strip().lower()
        if not query:
            return None
        bundle = getattr(context, "capability_bundle", None)
        entries = []
        if bundle is not None:
            entries.extend(getattr(bundle, "visible_tools", ()) or ())
            entries.extend(getattr(bundle, "deferred_tools", ()) or ())
            entries.extend(getattr(bundle, "materialized_tools", ()) or ())
            entries.extend(getattr(bundle, "discovered_tools", ()) or ())
        registry = getattr(context, "tool_registry", None)
        if registry is not None and hasattr(registry, "entries"):
            entries.extend(registry.entries())
        seen: set[str] = set()
        for entry in entries:
            name = str(getattr(entry, "name", "") or "")
            key = str(getattr(entry, "capability_id", "") or name)
            dedupe_key = f"{name}:{key}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            candidates = {
                name.lower(),
                str(getattr(entry, "display_name", "") or "").lower(),
                str(getattr(entry, "capability_id", "") or "").lower(),
            }
            if query in candidates:
                return entry
        return None

    def _format_context_block(
        self,
        *,
        context_type: ContextType,
        identifier: str,
        content: str | None,
        request: ContextRequest,
        attrs: dict[str, str] | None = None,
    ) -> str | None:
        sanitized = self._sanitize_context_text(content)
        if not sanitized.strip():
            return None
        bounded, truncated = self._bound_content(sanitized, request)
        attr_payload = {
            "type": context_type.value,
            "identifier": identifier,
            "truncated": "true" if truncated else "false",
            **(attrs or {}),
        }
        rendered_attrs = " ".join(f'{key}="{escape(str(value), quote=True)}"' for key, value in attr_payload.items())
        return f"<jit_context {rendered_attrs}>\n{bounded.rstrip()}\n</jit_context>"

    def _bound_content(self, content: str, request: ContextRequest) -> tuple[str, bool]:
        max_chars = self._metadata_int(request, "max_chars", default=_DEFAULT_MAX_CONTEXT_CHARS, minimum=200, maximum=_MAX_CONTEXT_CHARS)
        max_tokens = self._metadata_int(request, "max_tokens", default=0, minimum=0, maximum=20_000)
        if max_tokens > 0:
            max_chars = min(max_chars, max_tokens * 4)
        if len(content) <= max_chars:
            return content, False
        omitted = len(content) - max_chars
        suffix = f"\n[truncated {omitted} chars by jit context budget]"
        keep = max(max_chars - len(suffix), 0)
        return content[:keep].rstrip() + suffix, True

    def _metadata_int(
        self,
        request: ContextRequest,
        key: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        raw_value = request.metadata.get(key)
        if raw_value is None and key == "line_start":
            raw_value = request.metadata.get("start_line")
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _sanitize_context_text(self, content: str | None) -> str:
        sanitized = sanitize_memory_context_text(content or "").replace("\x00", "")
        for tag in _CONTEXT_SECTION_TAGS:
            sanitized = sanitized.replace(f"<{tag}", f"[{tag}")
            sanitized = sanitized.replace(f"</{tag}>", f"[/{tag}]")
        return sanitized

    def _is_probably_binary(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return b"\x00" in handle.read(2048)
        except OSError:
            return True

    def _conversation_payload_from_context(self, query: str, context: LeadAgentContext) -> dict[str, str]:
        query_lower = query.lower()
        candidates = {
            "request": context.request_context,
            "summary": context.summary_context,
            "todo": context.todo_context,
            "approval": context.approval_context,
            "uploads": context.upload_context,
        }
        if query_lower in {"recent", "*", "current", "context", "conversation"}:
            return {key: value for key, value in candidates.items() if value}
        aliases = {
            "task": "request",
            "current_task": "request",
            "thread_summary": "summary",
            "todos": "todo",
            "upload": "uploads",
        }
        selected = aliases.get(query_lower, query_lower)
        value = candidates.get(selected)
        return {selected: value} if value else {}

    def _should_try_project_file(self, identifier: str) -> bool:
        normalized = str(identifier or "").strip().replace("\\", "/")
        if not normalized or normalized in _PROJECT_CONTEXT_ALIASES:
            return False
        return Path(normalized).name.lower() in _DIRECT_PROJECT_FILE_NAMES or "/" in normalized or "." in Path(normalized).name

    def _enum_value(self, value: Any) -> str:
        return str(getattr(value, "value", value))

    def _estimate_tokens(self, content: str) -> int:
        """Estimate token count for content.

        Args:
            content: Content to estimate

        Returns:
            Estimated token count (rough approximation)
        """
        # Rough approximation: 1 token ≈ 4 characters
        return len(content) // 4


def _looks_like_windows_path(value: str) -> bool:
    return len(value) >= 2 and value[1] == ":" and value[0].isalpha()


def _strip_context_section_fence_tags(value: str | None) -> str:
    text = _XML_CONTEXT_SECTION_PATTERN.sub("", str(value or ""))
    text = sanitize_memory_context_text(text).replace("\x00", "")
    text = _BRACKETED_CONTEXT_SECTION_PATTERN.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _merge_context_v2_blocks(
    existing_blocks: list[dict[str, Any]],
    new_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in [*existing_blocks, *new_blocks]:
        block_id = str(block.get("block_id") or "")
        if block_id:
            if block_id in seen:
                continue
            seen.add(block_id)
        merged.append(block)
    return merged
