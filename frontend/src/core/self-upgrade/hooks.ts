"use client";

import { useQuery } from "@tanstack/react-query";

import type { SelfUpgradeHealthResponse } from "@/src/core/contracts";

import { getSelfUpgradeHealth } from "./api";

export function useSelfUpgradeHealth(candidateAuditLimit = 50) {
  return useQuery<SelfUpgradeHealthResponse>({
    queryKey: ["self-upgrade-health", candidateAuditLimit],
    queryFn: () => getSelfUpgradeHealth(candidateAuditLimit),
    staleTime: 30000,
  });
}
