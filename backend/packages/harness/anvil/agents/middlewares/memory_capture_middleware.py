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
        except Exception as exc:
            diagnostics = _capture_diagnostics("build_capture_envelope", exc)
            runtime.context.memory_capture_diagnostics = diagnostics
            return {"memory_capture_diagnostics": diagnostics}

        if not memory_service.has_capture_signal(envelope):
            return None
        memory_service.enqueue_capture(envelope)
        try:
            process_immediately = memory_service.should_process_capture_immediately(envelope)
            processed = memory_service.process_pending(namespace, force=process_immediately)
        except Exception as exc:
            processed = 0
            diagnostics = _capture_diagnostics("process_pending", exc)
            runtime.context.memory_capture_diagnostics = diagnostics
            return {
                "memory_snapshot_id": namespace,
                "memory_capture_diagnostics": diagnostics,
            }
        runtime.context.memory_capture_processed = processed > 0
        runtime.context.memory_capture_processed_count += processed
        diagnostics = {
            "source": "memory_service",
            "status": "processed" if processed else "queued",
            "phase": "after_agent",
            "processed_count": processed,
        }
        runtime.context.memory_capture_diagnostics = diagnostics
        return {"memory_snapshot_id": namespace, "memory_capture_diagnostics": diagnostics}


def _capture_diagnostics(phase: str, exc: Exception) -> dict[str, object]:
    return {
        "source": "memory_service",
        "status": "error",
        "phase": phase,
        "error_type": exc.__class__.__name__,
    }
