from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState
from anvil.agents.user_interaction import build_user_interaction_request, render_user_interaction_message


class ClarificationMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def wrap_tool_call(self, request, handler):
        if request.tool_call.get("name") != "ask_clarification":
            return handler(request)

        args = request.tool_call.get("args", {})
        interaction = build_user_interaction_request(args, request_id=request.tool_call.get("id") or None)
        message = render_user_interaction_message(interaction)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=message,
                        tool_call_id=request.tool_call.get("id", ""),
                        name="ask_clarification",
                    )
                ],
                "clarification_requested": True,
                "clarification_prompt": interaction.question or message,
                "pending_user_interaction": interaction,
            },
            goto=END,
        )

    def after_model(self, state: LeadAgentState, runtime):
        messages = state.get("messages") if isinstance(state, dict) else state.messages
        if not messages:
            return None
        last_message = messages[-1]
        if not isinstance(last_message, AIMessage):
            return None

        tool_calls = getattr(last_message, "tool_calls", None) or []
        clarification_call = next(
            (tool_call for tool_call in tool_calls if tool_call.get("name") == "ask_clarification"),
            None,
        )
        if clarification_call is not None:
            args = clarification_call.get("args", {})
            interaction = build_user_interaction_request(args, request_id=clarification_call.get("id") or None)
            message = render_user_interaction_message(interaction)
            updated_message = last_message.model_copy(update={"tool_calls": []})
            return {
                "messages": [
                    updated_message,
                    ToolMessage(
                        content=message,
                        tool_call_id=clarification_call.get("id", ""),
                        name="ask_clarification",
                    ),
                ],
                "clarification_requested": True,
                "clarification_prompt": interaction.question or message,
                "pending_user_interaction": interaction,
            }

        content = last_message.content
        if isinstance(content, str) and content.startswith("CLARIFY:"):
            interaction = build_user_interaction_request({"question": content[len("CLARIFY:") :].strip()})
            return {
                "clarification_requested": True,
                "clarification_prompt": interaction.question,
                "pending_user_interaction": interaction,
            }
        return None
