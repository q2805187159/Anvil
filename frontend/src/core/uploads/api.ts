import type { UploadResult } from "@/src/core/contracts";
import { apiFormRequest, apiRequest } from "@/src/core/api/client";

export function listUploads(threadId: string) {
  return apiRequest<UploadResult>(`/threads/${threadId}/uploads`);
}

export function uploadFiles(threadId: string, files: File[]) {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  return apiFormRequest<UploadResult>(`/threads/${threadId}/uploads`, form);
}
