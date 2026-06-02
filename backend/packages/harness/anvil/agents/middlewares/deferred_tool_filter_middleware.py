"""Behavior enhancement layer.

Reads: request tools, runtime capability bundle visible tools
Writes: prompt-visible tool schema list only
Side effects: none
Failure behavior: fail-open by leaving request.tools unchanged if the bundle is unavailable
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class DeferredToolFilterMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def wrap_model_call(self, request, handler):
        bundle = request.runtime.context.capability_bundle
        if bundle is None:
            return handler(request)
        visible_tool_names = {entry.name for entry in bundle.visible_tools}
        filtered_tools = [
            tool
            for tool in request.tools
            if getattr(tool, "name", None) in visible_tool_names
        ]
        return handler(request.override(tools=filtered_tools))
