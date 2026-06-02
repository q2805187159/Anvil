"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  activateMemoryProvider,
  auditMemoryAdmin,
  approveMemoryReview,
  batchGovernMemory,
  batchMemoryReview,
  createMemoryLayerEntry,
  createMemoryEntry,
  deleteMemoryLayerEntry,
  deleteMemoryEntry,
  exportMemoryAdmin,
  getMemoryHealth,
  getMemoryMaintenanceAutomation,
  getSessionMemory,
  getMemoryTrace,
  governProfileFacet,
  governMemory,
  flushMemory,
  importMemoryAdmin,
  listMemoryBenchmarkRuns,
  listMemoryBenchmarkSuites,
  listMemoryConflicts,
  listMemoryLayerEntries,
  listMemoryLayers,
  getMemoryOverview,
  listMemoryProviders,
  listProfileFacetAudit,
  listProfileFacets,
  listMemoryReview,
  listMemoryStaleness,
  listMemoryStoreEntries,
  listMemoryStores,
  listReflectionJobs,
  pauseReflectionJob,
  rejectMemoryReview,
  reloadMemoryProviders,
  rebuildProfileFacets,
  removeReflectionJob,
  resolveMemoryConflict,
  resumeReflectionJob,
  runMemoryMaintenance,
  runMemoryMaintenanceAutomation,
  runReflectionJob,
  runMemoryBenchmark,
  runMemoryBenchmarkSuite,
  searchMemorySessions,
  searchMemoryArchive,
  testMemoryProvider,
  updateMemoryLayerEntry,
  updateMemoryEntry,
} from "./api";

const MEMORY_ADMIN_STALE_TIME_MS = 30_000;

type QueryGateOptions = {
  enabled?: boolean;
};

function queryEnabled(options?: QueryGateOptions) {
  return options?.enabled ?? true;
}

export function useMemoryOverview(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-overview"],
    queryFn: getMemoryOverview,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useMemoryLayers(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-layers"],
    queryFn: listMemoryLayers,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useSessionMemory(threadId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-session", threadId],
    queryFn: () => getSessionMemory(threadId as string),
    enabled: Boolean(threadId) && queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useMemoryStores(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-stores"],
    queryFn: listMemoryStores,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useMemoryLayerEntries(layerId: "user" | "workspace" | "session", options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-layer-entries", layerId],
    queryFn: () => listMemoryLayerEntries(layerId as "user" | "workspace"),
    enabled: (layerId === "user" || layerId === "workspace") && queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useMemoryStoreEntries(storeId: string, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-entries", storeId],
    queryFn: () => listMemoryStoreEntries(storeId),
    enabled: Boolean(storeId) && queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useCreateMemoryLayerEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ layerId, body }: { layerId: "user" | "workspace"; body: Parameters<typeof createMemoryLayerEntry>[1] }) =>
      createMemoryLayerEntry(layerId, body),
    onSuccess: async (_, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["memory-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-layers"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-layer-entries", variables.layerId] });
      await queryClient.invalidateQueries({ queryKey: ["memory-stores"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-conflicts"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-staleness"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-health"] });
    },
  });
}

export function useCreateMemoryEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ storeId, body }: { storeId: string; body: Parameters<typeof createMemoryEntry>[1] }) =>
      createMemoryEntry(storeId, body),
    onSuccess: async (_, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["memory-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-stores"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-entries", variables.storeId] });
      await queryClient.invalidateQueries({ queryKey: ["memory-conflicts"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-staleness"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-health"] });
    },
  });
}

export function useUpdateMemoryLayerEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      layerId,
      entryId,
      body,
    }: {
      layerId: "user" | "workspace";
      entryId: string;
      body: Parameters<typeof updateMemoryLayerEntry>[2];
    }) => updateMemoryLayerEntry(layerId, entryId, body),
    onSuccess: async (_, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["memory-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-layers"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-layer-entries", variables.layerId] });
      await queryClient.invalidateQueries({ queryKey: ["memory-stores"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-conflicts"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-staleness"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-health"] });
    },
  });
}

export function useUpdateMemoryEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      storeId,
      entryId,
      body,
    }: {
      storeId: string;
      entryId: string;
      body: Parameters<typeof updateMemoryEntry>[2];
    }) => updateMemoryEntry(storeId, entryId, body),
    onSuccess: async (_, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["memory-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-stores"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-entries", variables.storeId] });
      await queryClient.invalidateQueries({ queryKey: ["memory-conflicts"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-staleness"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-health"] });
    },
  });
}

export function useDeleteMemoryLayerEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ layerId, entryId }: { layerId: "user" | "workspace"; entryId: string }) =>
      deleteMemoryLayerEntry(layerId, entryId),
    onSuccess: async (_, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["memory-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-layers"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-layer-entries", variables.layerId] });
      await queryClient.invalidateQueries({ queryKey: ["memory-stores"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-conflicts"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-staleness"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-health"] });
    },
  });
}

export function useDeleteMemoryEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ storeId, entryId }: { storeId: string; entryId: string }) =>
      deleteMemoryEntry(storeId, entryId),
    onSuccess: async (_, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["memory-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-stores"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-entries", variables.storeId] });
      await queryClient.invalidateQueries({ queryKey: ["memory-conflicts"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-staleness"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-health"] });
    },
  });
}

export function useMemoryProviders(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-providers"],
    queryFn: listMemoryProviders,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useMemoryConflicts(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-conflicts"],
    queryFn: listMemoryConflicts,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useMemoryStaleness(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-staleness"],
    queryFn: listMemoryStaleness,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useMemoryHealth(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-health"],
    queryFn: getMemoryHealth,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useRunMemoryBenchmark() {
  return useMutation({
    mutationFn: runMemoryBenchmark,
  });
}

export function useMemoryBenchmarkSuites(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-benchmark-suites"],
    queryFn: listMemoryBenchmarkSuites,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useMemoryBenchmarkRuns(suiteId?: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-benchmark-runs", suiteId ?? "all"],
    queryFn: () => listMemoryBenchmarkRuns(suiteId, 20),
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useRunMemoryBenchmarkSuite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ suiteId, evidenceLimit = 5 }: { suiteId: string; evidenceLimit?: number }) =>
      runMemoryBenchmarkSuite(suiteId, {
        evidence_limit: evidenceLimit,
        source: "ops",
        record: true,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["memory-benchmark-suites"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-benchmark-runs"], exact: false });
      await queryClient.invalidateQueries({ queryKey: ["memory-audit"] });
    },
  });
}

export function useMemoryReview(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-review"],
    queryFn: listMemoryReview,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useMemoryAdminAudit(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-audit"],
    queryFn: auditMemoryAdmin,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

function invalidateMemoryAdmin(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ["memory-overview"] }),
    queryClient.invalidateQueries({ queryKey: ["memory-stores"] }),
    queryClient.invalidateQueries({ queryKey: ["memory-layers"] }),
    queryClient.invalidateQueries({ queryKey: ["memory-conflicts"] }),
    queryClient.invalidateQueries({ queryKey: ["memory-staleness"] }),
    queryClient.invalidateQueries({ queryKey: ["memory-health"] }),
    queryClient.invalidateQueries({ queryKey: ["memory-review"] }),
    queryClient.invalidateQueries({ queryKey: ["memory-audit"] }),
    queryClient.invalidateQueries({ queryKey: ["memory-providers"] }),
    queryClient.invalidateQueries({ queryKey: ["profile-facets"] }),
    queryClient.invalidateQueries({ queryKey: ["profile-facet-audit"] }),
    queryClient.invalidateQueries({ queryKey: ["config-overview"] }),
  ]);
}

export function useFlushMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId = null }: { threadId?: string | null } = {}) =>
      flushMemory({ thread_id: threadId }),
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useApproveMemoryReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reviewId: string) => approveMemoryReview(reviewId),
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useRejectMemoryReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reviewId: string) => rejectMemoryReview(reviewId),
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useBatchMemoryReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: batchMemoryReview,
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useGovernMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ memoryId, action, reason }: { memoryId: string; action: string; reason?: string }) =>
      governMemory(memoryId, { action, reason, source: "ops" }),
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useProfileFacets(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["profile-facets"],
    queryFn: listProfileFacets,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useProfileFacetAudit(limit = 20, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["profile-facet-audit", limit],
    queryFn: () => listProfileFacetAudit(limit),
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useGovernProfileFacet() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ facetId, action, reason }: { facetId: string; action: string; reason?: string }) =>
      governProfileFacet(facetId, { action, reason, source: "ops" }),
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useRebuildProfileFacets() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => rebuildProfileFacets({ source: "ops" }),
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useBatchGovernMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: batchGovernMemory,
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useRunMemoryMaintenance() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: runMemoryMaintenance,
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
      await queryClient.invalidateQueries({ queryKey: ["memory-maintenance-automation"] });
    },
  });
}

export function useMemoryMaintenanceAutomation(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["memory-maintenance-automation"],
    queryFn: getMemoryMaintenanceAutomation,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useRunMemoryMaintenanceAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: runMemoryMaintenanceAutomation,
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
      await queryClient.invalidateQueries({ queryKey: ["memory-maintenance-automation"] });
    },
  });
}

export function useResolveMemoryConflict() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ conflictId, action }: { conflictId: string; action: string }) =>
      resolveMemoryConflict(conflictId, { action }),
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useActivateMemoryProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerId: string) => activateMemoryProvider(providerId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["memory-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-providers"] });
    },
  });
}

export function useReloadMemoryProviders() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: reloadMemoryProviders,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["memory-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-providers"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-audit"] });
    },
  });
}

export function useTestMemoryProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: testMemoryProvider,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["memory-providers"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-audit"] });
    },
  });
}

export function useExportMemoryAdmin() {
  return useMutation({
    mutationFn: exportMemoryAdmin,
  });
}

export function useImportMemoryAdmin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: importMemoryAdmin,
    onSuccess: async () => {
      await invalidateMemoryAdmin(queryClient);
    },
  });
}

export function useMemoryArchiveSearch() {
  const [data, setData] = useState<Awaited<ReturnType<typeof searchMemoryArchive>> | null>(null);
  const mutation = useMutation({
    mutationFn: ({ query, limit = 5 }: { query: string; limit?: number }) =>
      searchMemoryArchive(query, limit),
    onSuccess: (result) => setData(result),
  });

  return {
    ...mutation,
    data,
  };
}

export function useSessionSearch() {
  const [data, setData] = useState<Awaited<ReturnType<typeof searchMemorySessions>> | null>(null);
  const mutation = useMutation({
    mutationFn: ({
      query,
      threadId,
      limit = 5,
    }: {
      query: string;
      threadId: string | null;
      limit?: number;
    }) => searchMemorySessions(query, threadId, limit),
    onSuccess: (result) => setData(result),
  });

  return {
    ...mutation,
    data,
  };
}

export function useMemoryTrace(threadId: string | null) {
  const [data, setData] = useState<Awaited<ReturnType<typeof getMemoryTrace>> | null>(null);
  const mutation = useMutation({
    mutationFn: ({ targetId = null, limit = 10 }: { targetId?: string | null; limit?: number }) =>
      getMemoryTrace(threadId, targetId, limit),
    onSuccess: (result) => setData(result),
  });

  return {
    ...mutation,
    data,
  };
}

export function useReflectionJobs(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["reflection-jobs"],
    queryFn: listReflectionJobs,
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useRunReflectionJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => runReflectionJob(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["memory-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-stores"] });
      await queryClient.invalidateQueries({ queryKey: ["memory-entries"] });
      await queryClient.invalidateQueries({ queryKey: ["reflection-jobs"] });
    },
  });
}

export function usePauseReflectionJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => pauseReflectionJob(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["reflection-jobs"] });
    },
  });
}

export function useResumeReflectionJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => resumeReflectionJob(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["reflection-jobs"] });
    },
  });
}

export function useRemoveReflectionJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => removeReflectionJob(jobId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["reflection-jobs"] });
    },
  });
}
