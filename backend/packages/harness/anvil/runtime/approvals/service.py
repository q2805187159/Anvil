from __future__ import annotations

from .contracts import ApprovalDecision, ExecutionFailureClassification, GuardrailDecision
from .network import NetworkApprovalService
from .provider import BuiltinGuardrailProvider, GuardrailProvider


class ApprovalService:
    def __init__(
        self,
        *,
        provider: GuardrailProvider | None = None,
        network_service: NetworkApprovalService | None = None,
        skip_tool_approvals: bool = False,
        guardrails_config=None,
        session_grants: tuple[str, ...] = (),
    ) -> None:
        self.provider = provider or BuiltinGuardrailProvider(guardrails_config=guardrails_config)
        self.network_service = network_service or NetworkApprovalService()
        self.skip_tool_approvals = skip_tool_approvals
        self._session_grants: set[str] = set(session_grants)

    def evaluate_tool_call(
        self,
        *,
        tool_name: str,
        args: dict | None,
        capability_group: str | None,
        risk_category: str | None,
        thread_id: str,
        turn_id: str,
        tool_call_id: str | None,
        execution_mode: str,
        approval_profile: str | None,
        approval_context: str | None,
    ) -> GuardrailDecision:
        grant_key = _session_grant_key(
            thread_id=thread_id,
            tool_name=tool_name,
            approval_profile=approval_profile,
            risk_category=risk_category,
            capability_group=capability_group,
        )
        if grant_key in self._session_grants:
            decision = self.provider.evaluate(
                tool_name=tool_name,
                args=args,
                capability_group=capability_group,
                risk_category=risk_category,
                thread_id=thread_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                execution_mode=execution_mode,
                approval_profile=approval_profile,
                approval_context=approval_context,
            )
            if decision.decision is ApprovalDecision.FORBIDDEN:
                return decision
            return GuardrailDecision(decision=ApprovalDecision.SKIP, assessment=decision.assessment)

        session_grant_requested = _approval_context_requests_session_grant(approval_context)
        if self.skip_tool_approvals:
            decision = self.provider.evaluate(
                tool_name=tool_name,
                args=args,
                capability_group=capability_group,
                risk_category=risk_category,
                thread_id=thread_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                execution_mode=execution_mode,
                approval_profile=approval_profile,
                approval_context=approval_context,
            )
            if decision.decision is ApprovalDecision.FORBIDDEN:
                return decision
            if session_grant_requested:
                self._session_grants.add(grant_key)
            return GuardrailDecision(decision=ApprovalDecision.SKIP, assessment=decision.assessment)
        decision = self.provider.evaluate(
            tool_name=tool_name,
            args=args,
            capability_group=capability_group,
            risk_category=risk_category,
            thread_id=thread_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            execution_mode=execution_mode,
            approval_profile=approval_profile,
            approval_context=approval_context,
        )
        if decision.decision is ApprovalDecision.SKIP and session_grant_requested:
            self._session_grants.add(grant_key)
        return decision

    def classify_failure(self, *, approved: bool, had_policy: bool) -> ExecutionFailureClassification:
        if had_policy and not approved:
            return ExecutionFailureClassification.APPROVAL_DECLINED
        if had_policy:
            return ExecutionFailureClassification.POLICY_DENIED
        return ExecutionFailureClassification.EXECUTION_FAILED


def _session_grant_key(
    *,
    thread_id: str,
    tool_name: str,
    approval_profile: str | None,
    risk_category: str | None,
    capability_group: str | None,
) -> str:
    scope = approval_profile or risk_category or capability_group or tool_name
    return str(scope)


def _approval_context_requests_session_grant(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.lower()
    return (
        "do not ask again" in normalized
        or "don't ask" in normalized
        or "this session" in normalized
        or "本会话" in value
        or "不再询问" in value
    )
