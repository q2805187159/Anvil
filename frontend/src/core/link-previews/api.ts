import type { LinkPreviewView } from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function getLinkPreview(url: string) {
  const params = new URLSearchParams({ url });
  return apiRequest<LinkPreviewView>(`/link-previews/metadata?${params.toString()}`);
}
