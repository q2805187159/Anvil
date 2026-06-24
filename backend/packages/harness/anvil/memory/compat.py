from __future__ import annotations

from collections import defaultdict
from typing import Any

from .contracts import RetrievalResult, bounded_float


class AgentMemoryCompatLayer:
    """AgentMemory-shaped async facade backed by HCMS service APIs.

    The facade is intentionally thin and harness-owned. It adapts existing
    observe/consolidate/search callers without importing any removed legacy
    runtime or making the old memory model authoritative again.
    """

    def __init__(self, hcms_client: Any, *, namespace: str = "global/default") -> None:
        self.hcms_client = hcms_client
        self.namespace = namespace
        self._pending: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def observe(self, observation_data: dict[str, Any]) -> dict[str, Any]:
        """Record an AgentMemory-style observation for later consolidation."""

        content = _text(observation_data.get("content") or observation_data.get("raw_content") or observation_data.get("text"))
        session_id = _session_id(observation_data)
        if not content:
            return {
                "status": "error",
                "session_id": session_id,
                "pending_count": len(self._pending.get(session_id, [])),
                "error": "observation content is required",
            }
        pending_item = dict(observation_data)
        pending_item["content"] = content
        pending_item["session_id"] = session_id
        self._pending[session_id].append(pending_item)
        return {
            "status": "observed",
            "session_id": session_id,
            "pending_count": len(self._pending[session_id]),
        }

    async def consolidate(self, session_id: str | None = None) -> dict[str, Any]:
        """Persist pending observations through HCMS and report written ids."""

        selected_sessions = [session_id] if session_id else list(self._pending)
        memory_ids: list[str] = []
        errors: list[dict[str, str]] = []
        for current_session in selected_sessions:
            if current_session is None:
                continue
            pending = self._pending.pop(current_session, [])
            for observation in pending:
                try:
                    memory = self._create_memory(observation, current_session)
                    memory_ids.append(str(getattr(memory, "memory_id")))
                except Exception as exc:
                    errors.append({"session_id": current_session, "error": str(exc)})
        if not memory_ids and not errors:
            return {"status": "empty", "memories_written": 0, "memory_ids": [], "errors": []}
        return {
            "status": "partial" if errors else "consolidated",
            "memories_written": len(memory_ids),
            "memory_ids": memory_ids,
            "errors": errors,
        }

    async def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search HCMS and return AgentMemory-style result dictionaries."""

        results = self._search(query, limit=limit)
        return [_project_result(result) for result in results]

    def _create_memory(self, observation: dict[str, Any], session_id: str):
        if not hasattr(self.hcms_client, "create_memory"):
            raise AttributeError("HCMS client must expose create_memory()")
        return self.hcms_client.create_memory(
            self.namespace,
            content=str(observation["content"]),
            category=str(observation.get("category") or "note"),
            confidence=bounded_float(float(observation.get("confidence", 0.5))),
            salience=bounded_float(float(observation.get("strength", observation.get("salience", 0.5)))),
            source_thread_id=session_id,
            evidence_text=_text(observation.get("evidence") or observation.get("evidence_text")) or str(observation["content"])[:180],
            metadata={
                "compat_source": "agentmemory",
                "agentmemory_session_id": session_id,
                "tags": _string_list(observation.get("tags")),
            },
        )

    def _search(self, query: str, *, limit: int) -> list[Any]:
        if hasattr(self.hcms_client, "search"):
            return list(self.hcms_client.search(self.namespace, query, limit=limit))
        if hasattr(self.hcms_client, "retrieve"):
            return list(self.hcms_client.retrieve(query, limit=limit))
        raise AttributeError("HCMS client must expose search() or retrieve()")


def _session_id(payload: dict[str, Any]) -> str:
    return _text(payload.get("sessionId") or payload.get("session_id") or payload.get("thread_id")) or "default"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    try:
        return [str(item).strip() for item in value if str(item).strip()]
    except TypeError:
        return [str(value).strip()] if str(value).strip() else []


def _project_result(result: Any) -> dict[str, Any]:
    if isinstance(result, RetrievalResult):
        memory = result.memory
        content = _memory_content(memory) if memory is not None else result.highlight or ""
        return {
            "id": result.memory_id,
            "memory_id": result.memory_id,
            "content": content,
            "score": result.score,
            "summary": getattr(memory, "summary", "") if memory is not None else "",
            "category": getattr(getattr(memory, "category", None), "value", None) if memory is not None else None,
        }
    if isinstance(result, dict):
        memory_id = str(result.get("memory_id") or result.get("id") or "")
        return {
            "id": memory_id,
            "memory_id": memory_id,
            "content": str(result.get("content") or result.get("summary") or ""),
            "score": float(result.get("score") or 0.0),
            **result,
        }
    memory_id = str(getattr(result, "memory_id", getattr(result, "id", "")))
    return {
        "id": memory_id,
        "memory_id": memory_id,
        "content": str(getattr(result, "content", getattr(result, "summary", ""))),
        "score": float(getattr(result, "score", 0.0)),
    }


def _memory_content(memory: Any) -> str:
    summary = _text(getattr(memory, "summary", ""))
    if summary:
        return summary
    content = _text(getattr(memory, "content", ""))
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            content = parts[2].strip()
    lines = [line.strip("# ").strip() for line in content.splitlines() if line.strip()]
    return lines[0] if lines else ""
