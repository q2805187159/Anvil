from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from anvil.agents import SandboxState
from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class SandboxMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def before_agent(self, state: LeadAgentState, runtime):
        handle = runtime.context.sandbox_handle
        if handle is None:
            handle = runtime.context.sandbox_provider.acquire(
                thread_id=runtime.context.thread_id,
                path_service=runtime.context.path_service,
            )
            runtime.context.sandbox_handle = handle
        return {
            "sandbox_state": SandboxState(
                sandbox_id=handle.sandbox_id,
                sandbox_mode=handle.provider_mode,
            )
        }
