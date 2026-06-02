"""Safety and stability layer.

Reads: visible tool metadata, tool-call arguments, approval context
Writes: pending approval state via synthetic assistant message metadata
Side effects: none
Failure behavior: blocks or requests approval without executing the tool
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState
from anvil.runtime.approvals import ApprovalDecision


class GuardrailMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def wrap_model_call(self, request, handler):
        response = handler(request)
        approval_service = request.runtime.context.approval_service
        if approval_service is None:
            return response

        ai_message = response.result[0]
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
            entry = next(
                (tool for tool in request.runtime.context.capability_bundle.visible_tools if tool.name == tool_name),
                None,
            )
            if entry is None:
                continue
            approval_profile = entry.approval_profile
            risk_category = entry.risk_category
            if tool_name == "memory":
                action = str(tool_args.get("action") or "").strip().lower()
                layer = str(tool_args.get("layer") or "").strip().lower()
                if action in {"add", "replace", "remove"} and layer in {"user", "workspace"}:
                    approval_profile = "memory_write"
                    risk_category = "memory_write"
            decision = approval_service.evaluate_tool_call(
                tool_name=tool_name,
                args=tool_args,
                capability_group=entry.capability_group,
                risk_category=risk_category,
                thread_id=request.runtime.context.thread_id,
                turn_id=request.runtime.context.run_id or request.runtime.context.run_trace_id or "turn",
                tool_call_id=tool_call.get("id"),
                execution_mode=getattr(request.runtime.context, "execution_mode", "agent"),
                approval_profile=approval_profile,
                approval_context=request.runtime.context.approval_context,
            )
            if decision.decision is ApprovalDecision.SKIP:
                continue
            return AIMessage(
                content="guardrail decision",
                additional_kwargs={
                    "guardrail_decision": decision.decision.value,
                    "risk_assessment": decision.assessment.model_dump(mode="json") if decision.assessment is not None else None,
                    "approval_request": decision.approval_request.model_dump(mode="json")
                    if decision.approval_request
                    else None
                },
            )

        return response

    def after_model(self, state: LeadAgentState, runtime):
        messages = state.get("messages") if isinstance(state, dict) else state.messages
        if not messages:
            return None
        last_message = messages[-1]
        if not isinstance(last_message, AIMessage):
            return None

        approval_request = last_message.additional_kwargs.get("approval_request")
        if approval_request is not None:
            return {
                "pending_approval": ApprovalDecision.NEEDS_USER_APPROVAL,
                "approval_request": approval_request,
                "approval_request_reason": approval_request["reason"],
            }
        guardrail_decision = last_message.additional_kwargs.get("guardrail_decision")
        if guardrail_decision == ApprovalDecision.FORBIDDEN.value:
            return {
                "approval_request_reason": last_message.additional_kwargs.get("approval_request", {}).get("reason")
                if isinstance(last_message.additional_kwargs.get("approval_request"), dict)
                else "guardrail blocked tool execution",
            }
        return None


ApprovalMiddleware = GuardrailMiddleware
