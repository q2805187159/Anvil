"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  auditMemoryAdmin,
  batchGovernMemory,
  createMemoryLayerEntry,
  createMemoryEntry,
  deleteMemoryLayerEntry,
  deleteMemoryEntry,
  deleteHcmsMemory,
  exportMemoryAdmin,
  getHcmsMemory,
  getHcmsMemoryDiff,
  getHcmsMemoryHistory,
  getHcmsMemoryVersions,
  getHcmsMemoryRelations,
  getMemoryHealth,
  getMemoryMaintenanceAutomation,
  getSessionMemory,
  getMemoryTrace,
  governMemory,
  hcmsRecall,
  hcmsSearch,
  hcmsWhy,
  flushMemory,
  importMemoryAdmin,
  listHcmsMemories,
  type HCMSMemoryListParams,
  listMemoryBenchmarkRuns,
  listMemoryBenchmarkSuites,
  listMemoryConflicts,
  listMemoryLayerEntries,
  listMemoryLayers,
  getMemoryOverview,
  listMemoryStaleness,
  listMemoryStoreEntries,
  listMemoryStores,
  listReflectionJobs,
  pauseReflectionJob,
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
    queryClient.invalidateQueries({ queryKey: ["memory-audit"] }),
    queryClient.invalidateQueries({ queryKey: ["hcms-memories"], exact: false }),
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

export function useGovernMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ memoryId, action, reason }: { memoryId: string; action: string; reason?: string }) =>
      governMemory(memoryId, { action, reason, source: "ops" }),
    onSuccess: async (_, variables) => {
      await Promise.all([
        invalidateMemoryAdmin(queryClient),
        queryClient.invalidateQueries({ queryKey: ["hcms-memory", variables.memoryId] }),
        queryClient.invalidateQueries({ queryKey: ["hcms-memory-history", variables.memoryId] }),
        queryClient.invalidateQueries({ queryKey: ["hcms-memory-relations", variables.memoryId] }),
        queryClient.invalidateQueries({ queryKey: ["hcms-memory-diff", variables.memoryId] }),
      ]);
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

export function useHCMSRecall() {
  const [data, setData] = useState<Awaited<ReturnType<typeof hcmsRecall>> | null>(null);
  const mutation = useMutation({
    mutationFn: ({ query, limit = 10 }: { query: string; limit?: number }) => hcmsRecall(query, limit),
    onSuccess: (result) => setData(result),
  });

  return {
    ...mutation,
    data,
  };
}

export function useHCMSSearch() {
  const [data, setData] = useState<Awaited<ReturnType<typeof hcmsSearch>> | null>(null);
  const mutation = useMutation({
    mutationFn: ({ query, limit = 10 }: { query: string; limit?: number }) => hcmsSearch(query, limit),
    onSuccess: (result) => setData(result),
  });

  return {
    ...mutation,
    data,
  };
}

export function useHCMSWhy() {
  const [data, setData] = useState<Awaited<ReturnType<typeof hcmsWhy>> | null>(null);
  const mutation = useMutation({
    mutationFn: ({ query, limit = 3 }: { query: string; limit?: number }) => hcmsWhy(query, limit),
    onSuccess: (result) => setData(result),
  });

  return {
    ...mutation,
    data,
  };
}

export function useHCMSMemoryHistory(memoryId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["hcms-memory-history", memoryId],
    queryFn: () => getHcmsMemoryHistory(memoryId as string),
    enabled: Boolean(memoryId) && queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useHCMSMemoryVersions(memoryId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["hcms-memory-versions", memoryId],
    queryFn: () => getHcmsMemoryVersions(memoryId as string),
    enabled: Boolean(memoryId) && queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useHCMSMemory(memoryId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["hcms-memory", memoryId],
    queryFn: () => getHcmsMemory(memoryId as string),
    enabled: Boolean(memoryId) && queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useHCMSMemories(params: HCMSMemoryListParams = {}, options: QueryGateOptions = {}) {
  const query = params.query?.trim() || "";
  const state = params.state || "all";
  const category = params.category?.trim() || "";
  const layerId = params.layerId || "all";
  const limit = params.limit ?? 50;
  const offset = params.offset ?? 0;
  return useQuery({
    queryKey: ["hcms-memories", query, state, category, layerId, limit, offset],
    queryFn: () =>
      listHcmsMemories({
        query,
        state,
        category,
        layerId,
        limit,
        offset,
      }),
    enabled: queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useDeleteHCMSMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (memoryId: string) => deleteHcmsMemory(memoryId),
    onSuccess: async (_, memoryId) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["hcms-memory", memoryId] }),
        queryClient.invalidateQueries({ queryKey: ["hcms-memory-history", memoryId] }),
        queryClient.invalidateQueries({ queryKey: ["hcms-memory-relations", memoryId] }),
        queryClient.invalidateQueries({ queryKey: ["hcms-memory-diff", memoryId] }),
        queryClient.invalidateQueries({ queryKey: ["memory-overview"] }),
        queryClient.invalidateQueries({ queryKey: ["memory-health"] }),
        queryClient.invalidateQueries({ queryKey: ["memory-stores"] }),
        queryClient.invalidateQueries({ queryKey: ["hcms-memories"], exact: false }),
      ]);
    },
  });
}

export function useHCMSMemoryRelations(memoryId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["hcms-memory-relations", memoryId],
    queryFn: () => getHcmsMemoryRelations(memoryId as string),
    enabled: Boolean(memoryId) && queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
}

export function useHCMSMemoryDiff(memoryId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["hcms-memory-diff", memoryId],
    queryFn: () => getHcmsMemoryDiff(memoryId as string),
    enabled: Boolean(memoryId) && queryEnabled(options),
    staleTime: MEMORY_ADMIN_STALE_TIME_MS,
  });
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
