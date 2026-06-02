import type { SelfUpgradeHealthResponse } from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function getSelfUpgradeHealth(candidateAuditLimit = 50) {
  const params = new URLSearchParams({ candidate_audit_limit: String(candidateAuditLimit) });
  return apiRequest<SelfUpgradeHealthResponse>(`/self-upgrade/health?${params.toString()}`);
}
