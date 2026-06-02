from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState
from anvil.runtime.token_usage import aggregate_token_usage_from_messages


class TokenUsageMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def after_model(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        if not state_obj.messages:
            return None
        usage = aggregate_token_usage_from_messages(state_obj.messages)
        if not usage:
            return None
        return {"token_usage": usage}

