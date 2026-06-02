from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class ThreadDataMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def before_agent(self, state: LeadAgentState, runtime):
        current_thread_data = state.get("thread_data") if isinstance(state, dict) else state.thread_data
        thread_data = current_thread_data or runtime.context.thread_data
        if thread_data is None:
            thread_data = runtime.context.path_service.bootstrap_thread_paths(runtime.context.thread_id)
            runtime.context.thread_data = thread_data
        return {"thread_data": thread_data}
