import type { HealthView, SystemEventView } from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";
import { getEventStream } from "@/src/core/api/sse";

export function getGatewayHealth() {
  return apiRequest<HealthView>("/health");
}

export function streamSystemEvents(signal?: AbortSignal): AsyncGenerator<SystemEventView> {
  return getEventStream("/events/system", { signal }) as AsyncGenerator<SystemEventView>;
}

export function reloadSystem(scope: "config" | "skills" | "mcp" | "all" = "all") {
  return apiRequest<Record<string, unknown>>(`/admin/reload?scope=${scope}`, {
    method: "POST",
  });
}
