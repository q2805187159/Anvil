import type { ConfigOverviewView } from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function getConfigOverview() {
  return apiRequest<ConfigOverviewView>("/config/overview");
}
