from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class SubagentLimitMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def __init__(self, max_concurrent: int = 3) -> None:
        super().__init__()
        self.max_concurrent = max(1, max_concurrent)

    def after_model(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        if not state_obj.messages:
            return None
        last_message = state_obj.messages[-1]
        tool_calls = getattr(last_message, "tool_calls", None) or []
        delegated_indices = [
            index for index, tool_call in enumerate(tool_calls)
            if tool_call.get("name") == "delegated_task"
        ]
        if len(delegated_indices) <= self.max_concurrent:
            return None

        to_drop = set(delegated_indices[self.max_concurrent :])
        truncated_tool_calls = [
            tool_call
            for index, tool_call in enumerate(tool_calls)
            if index not in to_drop
        ]
        updated_message = last_message.model_copy(update={"tool_calls": truncated_tool_calls})
        return {"messages": [updated_message]}

