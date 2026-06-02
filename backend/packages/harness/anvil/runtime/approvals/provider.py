from __future__ import annotations

from typing import Protocol

from .command_safety import CommandSafetyAnalyzer
from .contracts import ActionRiskAssessment, ApprovalDecision, ApprovalRequest, GuardrailDecision, RiskLevel


class GuardrailProvider(Protocol):
    def evaluate(
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
    ) -> GuardrailDecision: ...


class BuiltinGuardrailProvider:
    def __init__(self, *, guardrails_config=None) -> None:
        self.command_safety = CommandSafetyAnalyzer()
        self.guardrails_config = guardrails_config

    def evaluate(
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
        args = args or {}
        assessment = self._assess(
            tool_name=tool_name,
            args=args,
            capability_group=capability_group,
            risk_category=risk_category,
            execution_mode=execution_mode,
        )
        if assessment.risk_level is RiskLevel.CRITICAL:
            return GuardrailDecision(
                decision=ApprovalDecision.FORBIDDEN,
                assessment=assessment,
                approval_request=ApprovalRequest(
                    request_id=f"{thread_id}/{turn_id}/0/{tool_call_id or tool_name}",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    reason=f"Tool '{tool_name}' was blocked due to critical risk: {', '.join(assessment.risk_factors)}",
                    action_kind="tool_call",
                    requested_permissions=[approval_profile] if approval_profile else [],
                    scope_options=(),
                    tool_name=tool_name,
                    approval_profile=approval_profile,
                    risk_category=risk_category,
                    capability_group=capability_group,
                ),
            )
        if approval_context:
            return GuardrailDecision(decision=ApprovalDecision.SKIP, assessment=assessment)
        if execution_mode == "full_access" and assessment.risk_level not in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            return GuardrailDecision(decision=ApprovalDecision.SKIP, assessment=assessment)
        approval_mode = self._approval_mode_for(risk_category=risk_category, capability_group=capability_group)
        if assessment.risk_level is RiskLevel.MEDIUM and approval_mode == "auto":
            return GuardrailDecision(decision=ApprovalDecision.SKIP, assessment=assessment)
        if assessment.risk_level in {RiskLevel.HIGH, RiskLevel.MEDIUM} or approval_profile is not None:
            return GuardrailDecision(
                decision=ApprovalDecision.NEEDS_USER_APPROVAL,
                assessment=assessment,
                approval_request=ApprovalRequest(
                    request_id=f"{thread_id}/{turn_id}/0/{tool_call_id or tool_name}",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    reason=f"Tool '{tool_name}' requires approval: {', '.join(assessment.risk_factors) or approval_profile or 'guarded action'}",
                    action_kind="tool_call",
                    requested_permissions=[approval_profile] if approval_profile else assessment.risk_factors,
                    scope_options=("turn", "session"),
                    tool_name=tool_name,
                    approval_profile=approval_profile,
                    risk_category=risk_category,
                    capability_group=capability_group,
                ),
            )
        return GuardrailDecision(decision=ApprovalDecision.SKIP, assessment=assessment)

    def _approval_mode_for(self, *, risk_category: str | None, capability_group: str | None) -> str:
        config = self.guardrails_config
        if config is None:
            return "suggest"
        policies = getattr(config, "tool_policies", None)
        if policies is None:
            return getattr(config, "default_approval_mode", "suggest")
        category = risk_category or capability_group or "general"
        policy = getattr(policies, category, None)
        if policy is not None and getattr(policy, "approval_mode", None):
            return str(policy.approval_mode)
        return getattr(config, "default_approval_mode", "suggest")

    def _assess(
        self,
        *,
        tool_name: str,
        args: dict,
        capability_group: str | None,
        risk_category: str | None,
        execution_mode: str,
    ) -> ActionRiskAssessment:
        risk_factors: list[str] = []
        target_paths: list[str] = []
        target_hosts: list[str] = []
        base_level = RiskLevel.SAFE
        effective_category = risk_category or capability_group or "general"
        if execution_mode == "full_access":
            base_level = RiskLevel.LOW
            risk_factors.append("full_access_mode")
        if effective_category in {"execution", "shell_execution", "process"}:
            base_level = max(base_level, RiskLevel.MEDIUM, key=_risk_order)
            report = self.command_safety.analyze(str(args.get("command") or args.get("action") or ""))
            risk_factors.extend(report.findings)
            target_paths.extend(report.target_paths)
            target_hosts.extend(report.target_hosts)
        if effective_category in {"filesystem", "filesystem_write"}:
            path_value = args.get("path") or args.get("output_path")
            if isinstance(path_value, str):
                target_paths.append(path_value)
            if any("/etc/" in path or ".ssh" in path for path in target_paths):
                risk_factors.append("sensitive_path")
            if effective_category == "filesystem_write" or tool_name in {"write_file", "patch_file", "export_document"}:
                base_level = max(base_level, RiskLevel.MEDIUM, key=_risk_order)
                risk_factors.append("filesystem_write")
        if effective_category in {"delegation", "subagent"}:
            base_level = max(base_level, RiskLevel.MEDIUM, key=_risk_order)
            risk_factors.append("delegation")
            requested_tool_names = args.get("requested_tool_names")
            if isinstance(requested_tool_names, list):
                requested = {str(name) for name in requested_tool_names}
                if requested & {"write_file", "export_document"}:
                    risk_factors.append("delegated_filesystem_write")
                    base_level = max(base_level, RiskLevel.HIGH, key=_risk_order)
                if requested & {"run_command", "process"}:
                    risk_factors.append("delegated_shell_execution")
                    base_level = max(base_level, RiskLevel.HIGH, key=_risk_order)
                if requested & {"read_file", "list_dir", "extract_document"}:
                    risk_factors.append("delegated_filesystem_access")
        if target_hosts:
            base_level = max(base_level, RiskLevel.MEDIUM, key=_risk_order)
        if any(
            factor in risk_factors
            for factor in ("destructive_system_command", "remote_script_to_shell", "sensitive_shell_write")
        ):
            base_level = RiskLevel.CRITICAL
        elif "high_impact_command" in risk_factors or "sensitive_path" in risk_factors:
            base_level = RiskLevel.HIGH
        if "shell_control_operator" in risk_factors and "sensitive_path" in risk_factors:
            base_level = RiskLevel.CRITICAL
        execution_kind = "execute" if effective_category in {"execution", "process", "shell_execution"} else "write" if "write" in effective_category or effective_category == "filesystem" else "delegate" if effective_category in {"delegation", "subagent"} else "read"
        return ActionRiskAssessment(
            tool_name=tool_name,
            risk_level=base_level,
            risk_factors=list(dict.fromkeys(risk_factors)),
            target_paths=list(dict.fromkeys(target_paths)),
            target_hosts=list(dict.fromkeys(target_hosts)),
            execution_mode=execution_kind,
        )


def _risk_order(level: RiskLevel) -> int:
    ordering = {
        RiskLevel.SAFE: 0,
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
        RiskLevel.CRITICAL: 4,
    }
    return ordering[level]
