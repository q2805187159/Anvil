from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_core.messages import ToolMessage

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class DanglingToolCallMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def _build_patched_messages(self, messages: list[object]) -> list[object] | None:
        valid_tool_call_ids = {
            str(tool_call.get("id") or "")
            for message in messages
            if isinstance(message, AIMessage)
            for tool_call in (getattr(message, "tool_calls", None) or [])
            if isinstance(tool_call, dict) and tool_call.get("id")
        }
        existing_tool_message_ids = {
            str(getattr(message, "tool_call_id", ""))
            for message in messages
            if isinstance(message, ToolMessage) and getattr(message, "tool_call_id", None)
        }
        patched: list[object] = []
        patched_ids: set[str] = set()
        patched_any = False
        for message in messages:
            if isinstance(message, ToolMessage):
                tool_call_id = str(getattr(message, "tool_call_id", "") or "")
                if tool_call_id and tool_call_id not in valid_tool_call_ids:
                    patched_any = True
                    continue
            patched.append(message)
            if not isinstance(message, AIMessage):
                continue
            for tool_call in getattr(message, "tool_calls", None) or []:
                if not isinstance(tool_call, dict):
                    continue
                tool_call_id = str(tool_call.get("id") or "")
                if not tool_call_id:
                    continue
                if tool_call_id in existing_tool_message_ids or tool_call_id in patched_ids:
                    continue
                patched.append(
                    ToolMessage(
                        content="[Tool call was interrupted and did not return a result.]",
                        tool_call_id=tool_call_id,
                        name=str(tool_call.get("name") or "unknown"),
                        status="error",
                    )
                )
                patched_ids.add(tool_call_id)
                patched_any = True
        return patched if patched_any else None

    def wrap_model_call(self, request, handler):
        patched = self._build_patched_messages(list(request.messages))
        if patched is None:
            return handler(request)
        return handler(request.override(messages=patched))
