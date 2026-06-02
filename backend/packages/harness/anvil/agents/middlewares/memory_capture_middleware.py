from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class MemoryCaptureMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def after_agent(self, state: LeadAgentState, runtime):
        memory_service = runtime.context.memory_service
        if memory_service is None:
            return None

        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        blocked = state_obj.pending_approval is not None or state_obj.clarification_requested

        namespace = runtime.context.memory_namespace or "global/default"
        try:
            envelope = memory_service.build_capture_envelope(
                thread_id=runtime.context.thread_id,
                namespace=namespace,
                messages=state_obj.messages,
                trace_id=runtime.context.thread_id,
                blocked=blocked,
            )
        except Exception:
            return None

        if not memory_service.has_capture_signal(envelope):
            return None
        memory_service.enqueue_capture(envelope)
        return {"memory_snapshot_id": namespace}
