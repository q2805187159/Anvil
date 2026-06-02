"""Behavior enhancement layer.

Reads: todos, messages, is_plan_mode
Writes: todos
Side effects: none
Failure behavior: fail-open, ignores malformed updates
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState, TodoItem


JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
PLAN_UPDATE_RE = re.compile(r"<plan_update>\s*(\{.*?\})\s*</plan_update>", re.DOTALL)


class TodoMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def before_model(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        runtime.context.todo_context = self._render_todos(state_obj.todos)
        return None

    def wrap_model_call(self, request, handler):
        if not request.runtime.context.is_plan_mode:
            return handler(request)
        todo_context = request.runtime.context.todo_context
        if not todo_context:
            return handler(request)
        system_prompt = request.system_prompt or ""
        todo_block = f"<todo_state>\n{todo_context}\n</todo_state>"
        if todo_block in system_prompt:
            return handler(request)
        return handler(
            request.override(
                system_message=SystemMessage(
                    content=f"{system_prompt}\n\n{todo_block}" if system_prompt else todo_block
                )
            )
        )

    def after_model(self, state: LeadAgentState, runtime):
        messages = state.get("messages") if isinstance(state, dict) else state.messages
        if not messages:
            return None
        last_message = messages[-1]
        if not isinstance(last_message, AIMessage):
            return None
        payload = self._parse_fallback_payload(str(last_message.content or ""))
        if payload is None:
            return None
        return {"todos": self._coerce_todos(payload, existing=getattr(last_message, "todos", None) or [])}

    def after_agent(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        tool_payload = None
        for message in reversed(state_obj.messages):
            if isinstance(message, ToolMessage) and message.name == "write_todos":
                tool_payload = self._load_payload(str(message.content or ""))
                break
        if tool_payload is None:
            return None
        return {"todos": self._coerce_todos(tool_payload, existing=state_obj.todos)}

    def _render_todos(self, todos: list[TodoItem]) -> str | None:
        if not todos:
            return None
        lines = []
        for item in todos:
            status = item.status.upper()
            lines.append(f"- [{status}] {item.content} ({item.id})")
        return "\n".join(lines)

    def _parse_fallback_payload(self, content: str) -> dict | None:
        for pattern in (PLAN_UPDATE_RE, JSON_BLOCK_RE):
            match = pattern.search(content)
            if not match:
                continue
            return self._load_payload(match.group(1))
        return None

    def _load_payload(self, raw: str) -> dict | None:
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _coerce_todos(self, payload: dict, *, existing: list[TodoItem] | list[dict]) -> list[TodoItem]:
        items = payload.get("todos")
        mode = str(payload.get("mode", "patch")).lower()
        if not isinstance(items, list):
            return [item if isinstance(item, TodoItem) else TodoItem.model_validate(item) for item in existing]

        current = {
            item.id: item if isinstance(item, TodoItem) else TodoItem.model_validate(item)
            for item in existing
        }
        next_items: dict[str, TodoItem] = {} if mode == "replace" else dict(current)
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            todo_id = str(raw_item.get("id") or f"todo-{uuid4().hex[:10]}")
            todo = TodoItem(
                id=todo_id,
                content=str(raw_item.get("content") or "").strip(),
                status=str(raw_item.get("status") or "pending"),
                created_at=str(raw_item.get("created_at") or datetime.now(timezone.utc).isoformat()),
                depends_on=[str(item) for item in raw_item.get("depends_on", [])] if isinstance(raw_item.get("depends_on"), list) else [],
            )
            if todo.content:
                next_items[todo_id] = todo
        return list(next_items.values())
