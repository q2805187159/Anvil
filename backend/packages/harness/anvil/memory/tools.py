from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

from .manager import MemoryRecallBenchmarkCase
from anvil.runtime.tool_registry.contracts import SchemaSanitizerDiagnostics, sanitize_tool_input_schema


MEMORY_TOOL_DESCRIPTION = (
    "Inspect, recall, explain, and edit HCMS durable memory. "
    "Use recall for four-stream retrieval, why/counterfactual for causal reasoning, history/diff for version control, "
    "and add/observe/replace/remove for lifecycle operations."
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
                "recall",
                "why",
                "counterfactual",
                "history",
                "diff",
                "replace",
                "remove",
                "flush",
                "consolidate",
                "health",
                "retention",
                "reinforce",
                "govern",
                "govern_batch",
                "maintenance",
                "benchmark",
                "review",
            ],
        },
        "layer": {"type": "string", "enum": ["session", "user", "workspace", "all"]},
        "content": {"type": ["string", "null"]},
        "entry_id": {"type": ["string", "null"]},
        "old_text": {"type": ["string", "null"]},
        "category": {"type": "string"},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "salience": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "event_type": {"type": ["string", "null"]},
        "evidence_refs": {"type": ["array", "null"], "items": {"type": "string"}},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "suite_id": {"type": ["string", "null"]},
        "cases": {"type": ["array", "null"], "items": {"type": "object"}},
        "dry_run": {"type": ["boolean", "null"]},
        "resolution": {"type": ["string", "null"]},
    },
    "required": ["action", "layer"],
}

SESSION_SEARCH_TOOL_DESCRIPTION = "Search HCMS memory as prior-session context. Read-only."
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

MEMORY_TRACE_TOOL_DESCRIPTION = "Inspect recent HCMS recall/capture traces."
MEMORY_TRACE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thread_id_override": {"type": ["string", "null"]},
        "target_id": {"type": ["string", "null"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
}


def build_memory_tools(*, memory_manager: Any, thread_id: str):
    def require_memory_manager():
        if memory_manager is None:
            raise ValueError("HCMS memory is unavailable for this runtime")
        return memory_manager

    def memory(
        action: str,
        layer: str,
        content: str | None = None,
        entry_id: str | None = None,
        old_text: str | None = None,
        category: str = "note",
        confidence: float | None = None,
        salience: float | None = None,
        event_type: str | None = None,
        evidence_refs: list[str] | None = None,
        limit: int = 5,
        suite_id: str | None = None,
        cases: list[dict[str, Any]] | None = None,
        dry_run: bool | None = None,
        resolution: str | None = None,
        **_: Any,
    ) -> str:
        manager = require_memory_manager()
        normalized_action = action.strip().lower()
        normalized_layer = layer.strip().lower()
        try:
            if normalized_action == "flush":
                return manager.flush_memory(thread_id=thread_id).model_dump_json()
            if normalized_action == "consolidate":
                result = manager.flush_memory(thread_id=thread_id)
                payload = result.model_dump(mode="json")
                payload.update({"status": "consolidated", "layer_id": normalized_layer})
                return json.dumps(payload, ensure_ascii=False, default=str)
            if normalized_action == "recall":
                return json.dumps(manager.hcms_search(query=content or old_text or "", limit=limit), ensure_ascii=False, default=str)
            if normalized_action == "why":
                return json.dumps(manager.hcms_why(query=content or old_text or "", limit=limit), ensure_ascii=False, default=str)
            if normalized_action == "counterfactual":
                return json.dumps(manager.hcms_counterfactual(query=content or old_text or "", avoid=old_text or "", limit=limit), ensure_ascii=False, default=str)
            if normalized_action == "history":
                target = entry_id or _latest_memory_id(manager, normalized_layer)
                return json.dumps(manager.hcms_history(memory_id=target), ensure_ascii=False, default=str)
            if normalized_action == "diff":
                target = entry_id or _latest_memory_id(manager, normalized_layer)
                return json.dumps(manager.hcms_diff(memory_id=target), ensure_ascii=False, default=str)
            if normalized_action in {"inspect", "list"}:
                if normalized_layer == "session":
                    return json.dumps(manager.get_session_memory(thread_id=thread_id, limit=limit), ensure_ascii=False, default=str)
                entries = manager.list_layer_entries(normalized_layer)
                return json.dumps({"layer_id": normalized_layer, "entries": [_dump(item) for item in entries[:limit]]}, ensure_ascii=False, default=str)
            if normalized_action in {"add", "observe"}:
                if not content:
                    return json.dumps({"error": "content is required"}, ensure_ascii=False)
                memory_obj = manager.create_layer_entry(
                    normalized_layer,
                    content=content,
                    category=event_type or category,
                    source_kind="tool_observation" if normalized_action == "observe" else "tool_write",
                    thread_id=thread_id,
                    confidence=confidence if confidence is not None else 0.6,
                    salience=salience if salience is not None else 0.5,
                    evidence_refs=tuple(_safe_string_list(evidence_refs)),
                )
                return json.dumps({"status": "observed" if normalized_action == "observe" else "added", "entry": _dump(memory_obj), "memory_id": memory_obj.memory_id}, ensure_ascii=False, default=str)
            if normalized_action == "replace":
                target = entry_id or _single_match_memory_id(manager.list_layer_entries(normalized_layer), old_text or "")
                memory_obj = manager.update_layer_entry(
                    normalized_layer,
                    target,
                    content=content,
                    category=category,
                    confidence=confidence,
                    salience=salience,
                    evidence_refs=tuple(_safe_string_list(evidence_refs)),
                )
                return json.dumps({"status": "replaced", "entry": _dump(memory_obj), "memory_id": memory_obj.memory_id}, ensure_ascii=False, default=str)
            if normalized_action == "remove":
                target = entry_id or _single_match_memory_id(manager.list_layer_entries(normalized_layer), old_text or "")
                manager.delete_layer_entry(normalized_layer, target)
                return json.dumps({"status": "removed", "entry_id": target}, ensure_ascii=False)
            if normalized_action == "health":
                return manager.health_report().model_dump_json()
            if normalized_action == "retention":
                items = [
                    item.model_dump(mode="json")
                    for item in manager.list_retention()
                    if normalized_layer in {"all", "*"} or getattr(item, "layer_id", None) == normalized_layer
                ]
                return json.dumps({"layer_id": normalized_layer, "items": items[:limit]}, ensure_ascii=False, default=str)
            if normalized_action in {"reinforce", "review", "govern"}:
                target = entry_id or _single_match_memory_id(manager.list_layer_entries(normalized_layer), old_text or content or "")
                requested_action = resolution or ("review" if normalized_action == "review" else normalized_action)
                result = manager.govern_memory(target, action=requested_action, reason=content, source="runtime_tool")
                return result.model_dump_json() if hasattr(result, "model_dump_json") else json.dumps(result.model_dump(mode="json"), ensure_ascii=False, default=str)
            if normalized_action == "govern_batch":
                dry = True if dry_run is None else dry_run
                runner = manager.plan_memory_governance if dry else manager.execute_memory_governance
                result = runner(policy=resolution or "balanced", layer_id=normalized_layer, limit=limit, source="runtime_tool")
                return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, default=str)
            if normalized_action == "maintenance":
                result = manager.run_maintenance(
                    policy=resolution or "balanced",
                    layer_id=normalized_layer,
                    limit=limit,
                    dry_run=True if dry_run is None else dry_run,
                    source="runtime_tool",
                )
                return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, default=str)
            if normalized_action == "benchmark":
                if suite_id and not cases:
                    run = manager.run_recall_benchmark_suite(suite_id, evidence_limit=limit, source="runtime_tool")
                    return run.model_dump_json()
                benchmark_cases = tuple(MemoryRecallBenchmarkCase.model_validate(item) for item in (cases or ()))
                report = manager.recall_benchmark(suite_id=suite_id or "runtime-tool", cases=benchmark_cases, evidence_limit=limit)
                return report.model_dump_json()
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
        return json.dumps(
            manager.search_sessions(
                query=query,
                current_thread_id=thread_id_override or thread_id,
                scope=scope,
                limit=limit,
                mode=mode,
            ),
            ensure_ascii=False,
            default=str,
        )

    def memory_trace(thread_id_override: str | None = None, target_id: str | None = None, limit: int = 10) -> str:
        manager = require_memory_manager()
        traces = manager.list_traces(thread_id=thread_id_override or thread_id, target_id=target_id, limit=limit)
        return json.dumps({"items": [trace.model_dump(mode="json") for trace in traces]}, ensure_ascii=False, default=str)

    return (
        (_structured_tool(name="memory", description=MEMORY_TOOL_DESCRIPTION, func=memory, schema=MEMORY_TOOL_SCHEMA), "memory", "Memory"),
        (_structured_tool(name="session_search", description=SESSION_SEARCH_TOOL_DESCRIPTION, func=session_search, schema=SESSION_SEARCH_TOOL_SCHEMA), "session_search", "Session Search"),
        (_structured_tool(name="memory_trace", description=MEMORY_TRACE_TOOL_DESCRIPTION, func=memory_trace, schema=MEMORY_TRACE_TOOL_SCHEMA), "memory_trace", "Memory Trace"),
    )


def _structured_tool(*, name: str, description: str, func, schema: dict[str, Any]) -> StructuredTool:
    clean_schema = sanitize_tool_input_schema(schema, diagnostics=SchemaSanitizerDiagnostics())
    return StructuredTool(name=name, description=description, func=func, args_schema=clean_schema)


def _dump(item: Any) -> dict[str, Any]:
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return dict(item)


def _single_match_memory_id(entries, old_text: str) -> str:
    matches = [entry for entry in entries if old_text and old_text in getattr(entry, "content", "")]
    if len(matches) != 1:
        raise ValueError(f"substring matched {len(matches)} memory entries; use entry_id")
    return matches[0].memory_id


def _latest_memory_id(manager: Any, layer: str) -> str:
    entries = manager.list_layer_entries(layer)
    if not entries:
        raise ValueError("no memory entries available")
    latest = max(entries, key=lambda item: getattr(item, "updated_at", None) or getattr(item, "created_at", None))
    return latest.memory_id


def _safe_string_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in result:
            result.append(value[:240])
    return result
