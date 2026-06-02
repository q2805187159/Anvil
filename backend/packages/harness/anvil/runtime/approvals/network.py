from __future__ import annotations

from collections import defaultdict

from .contracts import NetworkApprovalDecision, PermissionGrant, PermissionScope


class NetworkApprovalService:
    def __init__(self) -> None:
        self._grants: dict[str, set[str]] = defaultdict(set)

    def grant(self, *, session_id: str, host: str) -> PermissionGrant:
        self._grants[session_id].add(host)
        return PermissionGrant(
            scope=PermissionScope.SESSION,
            network_hosts=[host],
            granted_subset=[host],
            granted_by="network-approval-service",
        )

    def classify(self, *, session_id: str, host: str) -> NetworkApprovalDecision:
        if host in self._grants.get(session_id, set()):
            return NetworkApprovalDecision.ALLOW
        return NetworkApprovalDecision.PROMPT
