import type {
  BasicConfigOverviewView,
  BasicConfigTestRequest,
  BasicConfigTestView,
  BasicConfigUpdateRequest,
  BasicConfigUpdateView,
  ConfigOverviewView,
} from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function getConfigOverview() {
  return apiRequest<ConfigOverviewView>("/config/overview");
}

export function getBasicConfig() {
  return apiRequest<BasicConfigOverviewView>("/config/basics");
}

export function saveBasicConfig(body: BasicConfigUpdateRequest) {
  return apiRequest<BasicConfigUpdateView>("/config/basics", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function testBasicConfig(body: BasicConfigTestRequest) {
  return apiRequest<BasicConfigTestView>("/config/basics/test", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
