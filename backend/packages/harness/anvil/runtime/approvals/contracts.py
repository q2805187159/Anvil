from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalDecision(str, Enum):
    SKIP = "skip"
    NEEDS_USER_APPROVAL = "needs_user_approval"
    FORBIDDEN = "forbidden"


class NetworkApprovalDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    PROMPT = "prompt"


class PermissionScope(str, Enum):
    TURN = "turn"
    SESSION = "session"


class PermissionGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: PermissionScope = PermissionScope.TURN
    file_write_roots: list[str] = Field(default_factory=list)
    network_hosts: list[str] = Field(default_factory=list)
    granted_subset: list[str] = Field(default_factory=list)
    granted_at: datetime = Field(default_factory=utc_now)
    granted_by: str | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    thread_id: str
    turn_id: str
    reason: str
    action_kind: str
    requested_permissions: list[str] = Field(default_factory=list)
    scope_options: tuple[str, ...] = ()
    tool_name: str | None = None
    approval_profile: str | None = None
    risk_category: str | None = None
    capability_group: str | None = None


class ExecutionFailureClassification(str, Enum):
    POLICY_DENIED = "policy_denied"
    APPROVAL_DECLINED = "approval_declined"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    PERMISSION_DENIED = "permission_denied"
    EXECUTION_FAILED = "execution_failed"
    TIMED_OUT = "timed_out"


class RiskLevel(str, Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionRiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    risk_level: RiskLevel
    risk_factors: list[str] = Field(default_factory=list)
    target_paths: list[str] = Field(default_factory=list)
    target_hosts: list[str] = Field(default_factory=list)
    execution_mode: str = "read"


class GuardrailDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    approval_request: ApprovalRequest | None = None
    assessment: ActionRiskAssessment | None = None
