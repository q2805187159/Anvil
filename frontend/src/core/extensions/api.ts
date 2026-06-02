import type { ExtensionStatusView } from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function listExtensions() {
  return apiRequest<ExtensionStatusView[]>("/extensions");
}
