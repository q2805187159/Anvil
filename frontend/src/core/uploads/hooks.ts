"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listUploads, uploadFiles } from "./api";

export function useUploads(threadId: string | null, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["uploads", threadId],
    queryFn: () => listUploads(threadId!),
    enabled: Boolean(threadId) && (options.enabled ?? true),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
}

export function useUploadFiles(threadId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      filesOrOptions:
        | File[]
        | {
            files: File[];
            threadIdOverride?: string;
          },
    ) => {
      const files = Array.isArray(filesOrOptions) ? filesOrOptions : filesOrOptions.files;
      const effectiveThreadId = Array.isArray(filesOrOptions) ? threadId : filesOrOptions.threadIdOverride ?? threadId;
      return uploadFiles(effectiveThreadId!, files);
    },
    onSuccess: async (_result, filesOrOptions) => {
      const effectiveThreadId = Array.isArray(filesOrOptions) ? threadId : filesOrOptions.threadIdOverride ?? threadId;
      if (!effectiveThreadId) {
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["uploads", effectiveThreadId] });
      await queryClient.invalidateQueries({ queryKey: ["thread-state", effectiveThreadId] });
    },
  });
}
