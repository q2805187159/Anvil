from __future__ import annotations

import hashlib
import json
from typing import Any

from langchain_core.tools import StructuredTool
from anvil.runtime.tool_registry.contracts import SchemaSanitizerDiagnostics, sanitize_tool_input_schema

from .contracts import MemoryRecallBenchmarkCase


MEMORY_TOOL_DESCRIPTION = (
    "Inspect or edit durable memory layers. "
    "Use layer='user' for user profile memory, layer='workspace' for durable global work memory, "
    "and layer='session' only with action='inspect'. "
    "Actions: inspect, list, add, observe, profile, health, retention, govern, govern_batch, maintenance, reinforce, refresh, archive, benchmark, replace, remove, consolidate, review, resolve, flush."
)
MEMORY_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "inspect",
                "list",
                "add",
                "observe",
                "profile",
                "health",
                "diagnose",
                "diagnostics",
                "retention",
                "govern",
                "govern_batch",
                "maintenance",
                "maintain",
                "reinforce",
                "refresh",
                "archive",
                "review_memory",
                "benchmark",
                "replace",
                "remove",
                "consolidate",
                "review",
                "resolve",
                "flush",
            ],
        },
        "layer": {"type": "string", "enum": ["session", "user", "workspace", "all"]},
        "content": {"type": ["string", "null"]},
        "entry_id": {"type": ["string", "null"]},
        "old_text": {"type": ["string", "null"]},
        "conflict_id": {"type": ["string", "null"]},
        "review_id": {"type": ["string", "null"]},
        "resolution": {"type": "string"},
        "governance_action": {"type": ["string", "null"]},
        "category": {"type": "string"},
        "priority": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "salience": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "event_type": {"type": ["string", "null"]},
        "evidence_refs": {"type": ["array", "null"], "items": {"type": "string"}},
        "profile_class": {"type": ["string", "null"]},
        "cases": {"type": ["array", "null"], "items": {"type": "object"}},
        "suite_id": {"type": ["string", "null"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "dry_run": {"type": "boolean"},
    },
    "required": ["action", "layer"],
}

SESSION_SEARCH_TOOL_DESCRIPTION = (
    "Search archived sessions and grouped recall from prior threads. "
    "Modes: recent lists recent sessions without an LLM call, search returns matched sessions with rule summaries, "
    "summarize returns focused small-model summaries with evidence. Read-only."
)
SESSION_SEARCH_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "mode": {"type": "string", "enum": ["recent", "search", "summarize"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "scope": {"type": "string", "enum": ["exclude_current", "include_current", "current", "all"]},
        "thread_id_override": {"type": ["string", "null"]},
    },
}

MEMORY_TRACE_TOOL_DESCRIPTION = (
    "Explain why memory or session recall was surfaced. "
    "Use thread_id_override or target_id to inspect recent memory traces."
)
MEMORY_TRACE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thread_id_override": {"type": ["string", "null"]},
        "target_id": {"type": ["string", "null"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
}


def build_memory_platform_tools(*, memory_manager: Any, thread_id: str):
    def require_memory_manager():
        if memory_manager is None:
            raise ValueError("memory platform is unavailable for this runtime")
        return memory_manager

    def memory(
        action: str,
        layer: str,
        content: str | None = None,
        entry_id: str | None = None,
        old_text: str | None = None,
        conflict_id: str | None = None,
        review_id: str | None = None,
        resolution: str = "keep_both",
        governance_action: str | None = None,
        category: str = "note",
        priority: float | None = None,
        confidence: float | None = None,
        salience: float | None = None,
        event_type: str | None = None,
        evidence_refs: list[str] | None = None,
        profile_class: str | None = None,
        cases: list[dict[str, Any]] | None = None,
        suite_id: str | None = None,
        limit: int = 5,
        dry_run: bool = True,
    ) -> str:
        manager = require_memory_manager()
        normalized_action = action.strip().lower()
        normalized_layer = layer.strip().lower()

        if normalized_action == "review":
            return json.dumps(
                {"items": [item.model_dump(mode="json") for item in manager.list_review_items(status="pending")]},
                ensure_ascii=False,
                default=str,
            )
        if normalized_action == "flush":
            result = manager.flush_memory(thread_id=thread_id)
            return result.model_dump_json()
        if normalized_action in {"health", "diagnose", "diagnostics"}:
            return manager.health_report().model_dump_json()
        if normalized_action == "retention":
            items = manager.list_retention()
            if normalized_layer in {"user", "workspace"}:
                items = tuple(item for item in items if item.layer_id == normalized_layer)
            elif normalized_layer != "all":
                return json.dumps({"error": "retention layer must be user, workspace, or all"}, ensure_ascii=False)
            return json.dumps(
                {
                    "layer_id": normalized_layer,
                    "items": [item.model_dump(mode="json") for item in items[: max(1, limit)]],
                },
                ensure_ascii=False,
                default=str,
            )
        if normalized_action in {"govern", "reinforce", "refresh", "archive", "review_memory"}:
            target_id = entry_id
            if not target_id and old_text:
                try:
                    target_id = _single_match_memory_id(manager.list_layer_entries(normalized_layer), old_text)
                except (KeyError, ValueError) as exc:
                    return json.dumps({"error": str(exc)}, ensure_ascii=False)
            if not target_id:
                return json.dumps({"error": "entry_id or old_text is required for memory governance"}, ensure_ascii=False)
            action_name = (governance_action or resolution) if normalized_action == "govern" else normalized_action
            if action_name == "review_memory":
                action_name = "review"
            try:
                result = manager.govern_memory(
                    target_id,
                    action=action_name,
                    reason=content,
                    source="tool",
                )
            except (KeyError, ValueError) as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
            return result.model_dump_json()
        if normalized_action == "govern_batch":
            policy = governance_action or resolution or "balanced"
            try:
                result = manager.plan_memory_governance(
                    policy=policy,
                    layer_id=None if normalized_layer == "all" else normalized_layer,
                    limit=limit,
                ) if dry_run else manager.execute_memory_governance(
                    policy=policy,
                    layer_id=None if normalized_layer == "all" else normalized_layer,
                    limit=limit,
                    source="tool",
                )
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
            return result.model_dump_json()
        if normalized_action in {"maintenance", "maintain"}:
            policy = governance_action or resolution or "balanced"
            try:
                result = manager.run_maintenance(
                    dry_run=dry_run,
                    policy=policy,
                    layer_id=None if normalized_layer == "all" else normalized_layer,
                    limit=limit,
                    source="tool",
                )
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
            return result.model_dump_json()
        if normalized_action == "benchmark":
            try:
                benchmark_cases = tuple(MemoryRecallBenchmarkCase.model_validate(item) for item in (cases or []))
            except Exception as exc:
                return json.dumps({"error": f"invalid benchmark cases: {exc}"}, ensure_ascii=False)
            if benchmark_cases:
                return manager.recall_benchmark(
                    suite_id=suite_id or "tool",
                    cases=benchmark_cases,
                    evidence_limit=limit,
                ).model_dump_json()
            if suite_id:
                try:
                    return manager.run_recall_benchmark_suite(
                        suite_id,
                        evidence_limit=limit,
                        source="tool",
                    ).model_dump_json()
                except (KeyError, ValueError) as exc:
                    return json.dumps({"error": str(exc)}, ensure_ascii=False)
            return json.dumps({"error": "benchmark requires cases or suite_id"}, ensure_ascii=False)
        if normalized_action == "resolve":
            if review_id:
                if resolution in {"approve", "approved"}:
                    entry = manager.approve_review_item(review_id)
                    return json.dumps({"status": "approved", "entry": entry.model_dump(mode="json")}, ensure_ascii=False, default=str)
                if resolution in {"reject", "rejected"}:
                    item = manager.reject_review_item(review_id)
                    return json.dumps({"status": "rejected", "review": item.model_dump(mode="json")}, ensure_ascii=False, default=str)
            if conflict_id:
                resolved = manager.resolve_conflict(conflict_id, action=resolution)
                return json.dumps({"status": "resolved", "conflict": resolved.model_dump(mode="json")}, ensure_ascii=False, default=str)
            return json.dumps({"error": "resolve requires review_id or conflict_id"}, ensure_ascii=False)

        if normalized_action in {"inspect", "list", "profile"}:
            if normalized_layer == "session":
                return json.dumps(
                    manager.get_session_memory(thread_id=thread_id, limit=limit),
                    ensure_ascii=False,
                )
            try:
                entries = manager.list_layer_entries(normalized_layer)
            except (KeyError, ValueError) as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
            if normalized_action == "profile":
                entries = _filter_profile_entries(
                    entries,
                    profile_class=profile_class,
                    category=category,
                    limit=limit,
                )
                return json.dumps(
                    {
                        "layer_id": normalized_layer,
                        "profile_class": profile_class,
                        "entries": [entry.model_dump(mode="json") for entry in entries],
                    },
                    ensure_ascii=False,
                    default=str,
                )
            return json.dumps(
                {
                    "layer_id": normalized_layer,
                    "entries": [entry.model_dump(mode="json") for entry in entries],
                },
                ensure_ascii=False,
            )

        if normalized_layer == "session":
            return json.dumps(
                {"error": "session layer is read-only; use action='inspect' or the session_search tool."},
                ensure_ascii=False,
            )

        try:
            if normalized_action == "add":
                if not content:
                    return json.dumps({"error": "content is required for memory add"}, ensure_ascii=False)
                entry = manager.create_layer_entry(
                    normalized_layer,
                    content=content,
                    category=category,
                    source_kind="tool_write",
                    priority=priority if priority is not None else 0.5,
                    confidence=confidence if confidence is not None else 0.5,
                    salience=salience if salience is not None else 0.5,
                    evidence_refs=tuple(_safe_string_list(evidence_refs)),
                )
                return json.dumps({"status": "added", "entry": entry.model_dump(mode="json")}, ensure_ascii=False)
            if normalized_action == "observe":
                if not content:
                    return json.dumps({"error": "content is required for memory observe"}, ensure_ascii=False)
                normalized_event = _normalize_label(event_type or category or "observation")
                normalized_confidence = _bounded_float(confidence, default=0.6)
                normalized_salience = _bounded_float(salience, default=0.5)
                if normalized_salience < 0.25 and normalized_confidence < 0.7:
                    return json.dumps(
                        {
                            "status": "ignored",
                            "reason": "low salience observation was not persisted",
                            "confidence": normalized_confidence,
                            "salience": normalized_salience,
                        },
                        ensure_ascii=False,
                    )
                normalized_evidence = tuple(_safe_string_list(evidence_refs)[:20])
                fingerprint = _memory_fingerprint(
                    layer=normalized_layer,
                    event_type=normalized_event,
                    content=content,
                )
                duplicate = _find_duplicate_observation(manager.list_layer_entries(normalized_layer), fingerprint)
                if duplicate is not None:
                    return json.dumps(
                        {
                            "status": "duplicate",
                            "entry": duplicate.model_dump(mode="json"),
                            "fingerprint": fingerprint,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                entry = manager.create_layer_entry(
                    normalized_layer,
                    content=content,
                    category=f"observation:{normalized_event}",
                    source_kind="tool_observation",
                    priority=priority if priority is not None else max(0.4, normalized_salience),
                    metadata={
                        "event_type": normalized_event,
                        "fingerprint": fingerprint,
                        "profile_class": _normalize_label(profile_class or "") or None,
                    },
                    thread_id=thread_id,
                    source_ref=f"thread:{thread_id}",
                    confidence=normalized_confidence,
                    salience=normalized_salience,
                    evidence_refs=normalized_evidence,
                )
                return json.dumps(
                    {
                        "status": "observed",
                        "entry": entry.model_dump(mode="json"),
                        "fingerprint": fingerprint,
                        "evidence_count": len(normalized_evidence),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            if normalized_action == "replace":
                if not entry_id and old_text:
                    entry_id = _single_match_entry_id(manager.list_layer_entries(normalized_layer), old_text)
                if not entry_id:
                    return json.dumps({"error": "entry_id is required for memory replace"}, ensure_ascii=False)
                entry = manager.update_layer_entry(
                    normalized_layer,
                    entry_id,
                    content=content,
                    category=category if category else None,
                    priority=priority,
                )
                return json.dumps({"status": "replaced", "entry": entry.model_dump(mode="json")}, ensure_ascii=False)
            if normalized_action == "remove":
                if not entry_id and old_text:
                    entry_id = _single_match_entry_id(manager.list_layer_entries(normalized_layer), old_text)
                if not entry_id:
                    return json.dumps({"error": "entry_id is required for memory remove"}, ensure_ascii=False)
                manager.delete_layer_entry(normalized_layer, entry_id)
                return json.dumps({"status": "removed", "entry_id": entry_id}, ensure_ascii=False)
            if normalized_action == "consolidate":
                entry = manager.consolidate_layer(normalized_layer)
                return json.dumps({"status": "consolidated", "entry": entry.model_dump(mode="json")}, ensure_ascii=False, default=str)
        except (KeyError, ValueError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

        return json.dumps({"error": f"unsupported memory action '{action}'"}, ensure_ascii=False)

    def session_search(
        query: str = "",
        mode: str = "summarize",
        limit: int = 5,
        scope: str = "exclude_current",
        thread_id_override: str | None = None,
    ) -> str:
        manager = require_memory_manager()
        try:
            result = manager.search_sessions(
                query=query,
                current_thread_id=thread_id_override or thread_id,
                scope=scope,
                limit=limit,
                mode=mode,
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False, default=str)

    def memory_trace(
        thread_id_override: str | None = None,
        target_id: str | None = None,
        limit: int = 10,
    ) -> str:
        manager = require_memory_manager()
        traces = manager.list_traces(
            thread_id=thread_id_override or thread_id,
            target_id=target_id,
            limit=limit,
        )
        return json.dumps(
            {"items": [trace.model_dump(mode="json") for trace in traces]},
            ensure_ascii=False,
            default=str,
        )

    return (
        (_structured_tool(name="memory", description=MEMORY_TOOL_DESCRIPTION, func=memory, schema=MEMORY_TOOL_SCHEMA), "memory", "Memory"),
        (
            _structured_tool(
                name="session_search",
                description=SESSION_SEARCH_TOOL_DESCRIPTION,
                func=session_search,
                schema=SESSION_SEARCH_TOOL_SCHEMA,
            ),
            "session_search",
            "Session Search",
        ),
        (
            _structured_tool(
                name="memory_trace",
                description=MEMORY_TRACE_TOOL_DESCRIPTION,
                func=memory_trace,
                schema=MEMORY_TRACE_TOOL_SCHEMA,
            ),
            "memory_trace",
            "Memory Trace",
        ),
    )


def _structured_tool(*, name: str, description: str, func, schema: dict[str, Any]) -> StructuredTool:
    clean_schema = sanitize_tool_input_schema(schema, diagnostics=SchemaSanitizerDiagnostics())
    return StructuredTool(name=name, description=description, func=func, args_schema=clean_schema)


def _single_match_entry_id(entries, old_text: str) -> str:
    matches = [entry for entry in entries if old_text in entry.content]
    if len(matches) != 1:
        raise ValueError(f"substring matched {len(matches)} memory entries; use entry_id")
    return matches[0].entry_id


def _single_match_memory_id(entries, old_text: str) -> str:
    matches = [entry for entry in entries if old_text in entry.content]
    if len(matches) != 1:
        raise ValueError(f"substring matched {len(matches)} memory entries; use entry_id")
    return matches[0].memory_id or matches[0].entry_id


def _safe_string_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in result:
            result.append(value[:240])
    return result


def _normalize_label(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in str(value or "").strip().lower()).strip("_.-")[:64]


def _bounded_float(value: float | int | None, *, default: float) -> float:
    try:
        numeric = float(value) if value is not None else default
    except (TypeError, ValueError):
        numeric = default
    return round(min(max(numeric, 0.0), 1.0), 4)


def _memory_fingerprint(*, layer: str, event_type: str, content: str) -> str:
    normalized = " ".join(str(content or "").lower().split())[:1200]
    return hashlib.sha256(f"{layer}\0{event_type}\0{normalized}".encode("utf-8", errors="replace")).hexdigest()[:24]


def _find_duplicate_observation(entries, fingerprint: str):
    for entry in entries:
        metadata = getattr(entry, "metadata", {}) or {}
        if metadata.get("fingerprint") == fingerprint and getattr(entry, "status", "active") not in {"archived", "rejected", "superseded"}:
            return entry
    return None


def _filter_profile_entries(entries, *, profile_class: str | None, category: str, limit: int):
    normalized_class = _normalize_label(profile_class or "")
    normalized_category = _normalize_label(category or "")
    filtered = []
    for entry in entries:
        metadata = getattr(entry, "metadata", {}) or {}
        entry_class = _normalize_label(str(metadata.get("profile_class") or ""))
        entry_category = _normalize_label(str(getattr(entry, "category", "") or ""))
        if normalized_class and entry_class != normalized_class:
            continue
        if normalized_category and normalized_category != "note" and normalized_category not in entry_category:
            continue
        if entry_category.startswith("observation:") or entry_class:
            filtered.append(entry)
    filtered.sort(key=lambda entry: (-float(getattr(entry, "salience", 0.0) or 0.0), str(getattr(entry, "updated_at", ""))), reverse=False)
    return filtered[: max(1, min(limit, 100))]
