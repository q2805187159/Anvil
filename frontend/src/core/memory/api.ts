import type {
  MemoryArchiveSearchResultView,
  MemoryConflictResponse,
  MemoryConflictResolveRequest,
  MemoryAdminAuditView,
  MemoryAdminExportView,
  MemoryAdminImportRequest,
  MemoryAdminImportResponse,
  MemoryGovernanceActionRequest,
  MemoryGovernanceActionResponse,
  MemoryGovernanceBatchRequest,
  MemoryGovernanceBatchResponse,
  MemoryEntryCreateRequest,
  MemoryEntryUpdateRequest,
  MemoryEntryView,
  MemoryLayerId,
  MemoryLayerView,
  MemoryFlushRequest,
  MemoryFlushResponse,
  MemoryMaintenanceAutomationRequest,
  MemoryMaintenanceAutomationRunResponse,
  MemoryMaintenanceAutomationStatusResponse,
  MemoryMaintenanceRequest,
  MemoryMaintenanceResponse,
  MemoryHealthResponse,
  MemoryRecallBenchmarkRequest,
  MemoryRecallBenchmarkResponse,
  MemoryRecallBenchmarkRunListResponse,
  MemoryRecallBenchmarkRunRequest,
  MemoryRecallBenchmarkRunView,
  MemoryRecallBenchmarkSuiteListResponse,
  MemoryRecallBenchmarkSuiteUpsertRequest,
  MemoryRecallBenchmarkSuiteView,
  MemoryTraceResponse,
  MemoryOverviewView,
  HCMSRecallResponse,
  HCMSWhyResponse,
  HCMSMemoryResponse,
  HCMSMemoryListResponse,
  HCMSMemoryDeleteResponse,
  HCMSMemoryRelationsResponse,
  HCMSMemoryHistoryResponse,
  HCMSMemoryDiffResponse,
  MemoryStoreView,
  MemoryStalenessResponse,
  SessionSearchMode,
  SessionMemoryView,
  SessionSearchResultView,
  ReflectionJobRunView,
  ReflectionJobView,
} from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function listMemoryStores() {
  return apiRequest<MemoryStoreView[]>("/memory/stores");
}

export function getMemoryOverview() {
  return apiRequest<MemoryOverviewView>("/memory/overview");
}

export function listMemoryLayers() {
  return apiRequest<MemoryLayerView[]>("/memory/layers");
}

export function getSessionMemory(threadId: string) {
  const params = new URLSearchParams({ thread_id: threadId });
  return apiRequest<SessionMemoryView>(`/memory/session?${params.toString()}`);
}

export function listMemoryStoreEntries(storeId: string) {
  return apiRequest<MemoryEntryView[]>(`/memory/stores/${storeId}/entries`);
}

export function listMemoryLayerEntries(layerId: Exclude<MemoryLayerId, "session">) {
  return apiRequest<MemoryEntryView[]>(`/memory/${layerId}`);
}

export function createMemoryEntry(storeId: string, body: MemoryEntryCreateRequest) {
  return apiRequest<MemoryEntryView>(`/memory/stores/${storeId}/entries`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function createMemoryLayerEntry(layerId: Exclude<MemoryLayerId, "session">, body: MemoryEntryCreateRequest) {
  return apiRequest<MemoryEntryView>(`/memory/${layerId}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateMemoryEntry(
  storeId: string,
  entryId: string,
  body: MemoryEntryUpdateRequest,
) {
  return apiRequest<MemoryEntryView>(`/memory/stores/${storeId}/entries/${entryId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function updateMemoryLayerEntry(
  layerId: Exclude<MemoryLayerId, "session">,
  entryId: string,
  body: MemoryEntryUpdateRequest,
) {
  return apiRequest<MemoryEntryView>(`/memory/${layerId}/${entryId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteMemoryEntry(storeId: string, entryId: string) {
  return apiRequest<{ status: string }>(`/memory/stores/${storeId}/entries/${entryId}`, {
    method: "DELETE",
  });
}

export function deleteMemoryLayerEntry(layerId: Exclude<MemoryLayerId, "session">, entryId: string) {
  return apiRequest<{ status: string }>(`/memory/${layerId}/${entryId}`, {
    method: "DELETE",
  });
}

export function searchMemoryArchive(query: string, limit = 5) {
  return apiRequest<MemoryArchiveSearchResultView>("/memory/archive/search", {
    method: "POST",
    body: JSON.stringify({ query, limit }),
  });
}

export function searchMemorySessions(query: string, threadId: string | null, limit = 5, mode: SessionSearchMode = "summarize") {
  return apiRequest<SessionSearchResultView>("/memory/session/search", {
    method: "POST",
    body: JSON.stringify({ query, thread_id: threadId, limit, scope: "exclude_current", mode }),
  });
}

export function listMemoryConflicts() {
  return apiRequest<MemoryConflictResponse>("/memory/admin/conflicts").then((payload) => payload.items);
}

export function listMemoryStaleness() {
  return apiRequest<MemoryStalenessResponse>("/memory/admin/staleness").then((payload) => payload.items);
}

export function getMemoryHealth() {
  return apiRequest<MemoryHealthResponse>("/memory/admin/health");
}

export function runMemoryBenchmark(body: MemoryRecallBenchmarkRequest) {
  return apiRequest<MemoryRecallBenchmarkResponse>("/memory/admin/benchmark", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listMemoryBenchmarkSuites() {
  return apiRequest<MemoryRecallBenchmarkSuiteListResponse>("/memory/admin/benchmark/suites").then((payload) => payload.items);
}

export function upsertMemoryBenchmarkSuite(body: MemoryRecallBenchmarkSuiteUpsertRequest) {
  return apiRequest<MemoryRecallBenchmarkSuiteView>("/memory/admin/benchmark/suites", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteMemoryBenchmarkSuite(suiteId: string) {
  return apiRequest<MemoryRecallBenchmarkSuiteView>(`/memory/admin/benchmark/suites/${encodeURIComponent(suiteId)}`, {
    method: "DELETE",
  });
}

export function runMemoryBenchmarkSuite(suiteId: string, body: MemoryRecallBenchmarkRunRequest = {}) {
  return apiRequest<MemoryRecallBenchmarkRunView>(`/memory/admin/benchmark/suites/${encodeURIComponent(suiteId)}/run`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listMemoryBenchmarkRuns(suiteId?: string | null, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (suiteId) {
    params.set("suite_id", suiteId);
  }
  return apiRequest<MemoryRecallBenchmarkRunListResponse>(`/memory/admin/benchmark/runs?${params.toString()}`).then((payload) => payload.items);
}

export function flushMemory(body: MemoryFlushRequest = {}) {
  return apiRequest<MemoryFlushResponse>("/memory/admin/flush", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function exportMemoryAdmin() {
  return apiRequest<MemoryAdminExportView>("/memory/admin/export");
}

export function importMemoryAdmin(body: MemoryAdminImportRequest) {
  return apiRequest<MemoryAdminImportResponse>("/memory/admin/import", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function auditMemoryAdmin() {
  return apiRequest<MemoryAdminAuditView>("/memory/admin/audit");
}

export function governMemory(memoryId: string, body: MemoryGovernanceActionRequest) {
  return apiRequest<MemoryGovernanceActionResponse>(`/memory/admin/memories/${memoryId}/govern`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function batchGovernMemory(body: MemoryGovernanceBatchRequest) {
  return apiRequest<MemoryGovernanceBatchResponse>("/memory/admin/governance", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function runMemoryMaintenance(body: MemoryMaintenanceRequest) {
  return apiRequest<MemoryMaintenanceResponse>("/memory/admin/maintenance", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getMemoryMaintenanceAutomation() {
  return apiRequest<MemoryMaintenanceAutomationStatusResponse>("/memory/admin/maintenance/automation");
}

export function runMemoryMaintenanceAutomation(body: MemoryMaintenanceAutomationRequest = {}) {
  return apiRequest<MemoryMaintenanceAutomationRunResponse>("/memory/admin/maintenance/automation/run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function resolveMemoryConflict(conflictId: string, body: MemoryConflictResolveRequest) {
  return apiRequest<MemoryConflictResponse["items"][number]>(`/memory/admin/conflicts/${conflictId}/resolve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listReflectionJobs() {
  return apiRequest<{ items: ReflectionJobView[] }>("/memory/admin/reflections").then((payload) => payload.items);
}

export function runReflectionJob(jobId: string) {
  return apiRequest<ReflectionJobRunView>(`/memory/reflections/jobs/${jobId}/run`, {
    method: "POST",
  });
}

export function pauseReflectionJob(jobId: string) {
  return apiRequest<ReflectionJobView>(`/memory/reflections/jobs/${jobId}/pause`, {
    method: "POST",
  });
}

export function resumeReflectionJob(jobId: string) {
  return apiRequest<ReflectionJobView>(`/memory/reflections/jobs/${jobId}/resume`, {
    method: "POST",
  });
}

export function removeReflectionJob(jobId: string) {
  return apiRequest<ReflectionJobView>(`/memory/reflections/jobs/${jobId}/remove`, {
    method: "POST",
  });
}

export function getMemoryTrace(threadId: string | null, targetId: string | null = null, limit = 10) {
  return apiRequest<MemoryTraceResponse>("/memory/trace", {
    method: "POST",
    body: JSON.stringify({ thread_id: threadId, target_id: targetId, limit }),
  });
}

export function hcmsRecall(query: string, limit = 10) {
  return apiRequest<HCMSRecallResponse>("/memory/hcms/recall", {
    method: "POST",
    body: JSON.stringify({ query, limit }),
  });
}

export function hcmsSearch(query: string, limit = 10) {
  return apiRequest<HCMSRecallResponse>("/memory/hcms/search", {
    method: "POST",
    body: JSON.stringify({ query, limit }),
  });
}

export function hcmsWhy(query: string, limit = 3) {
  return apiRequest<HCMSWhyResponse>("/memory/hcms/why", {
    method: "POST",
    body: JSON.stringify({ query, limit }),
  });
}

export type HCMSMemoryListParams = {
  query?: string | null;
  state?: string | null;
  category?: string | null;
  layerId?: string | null;
  limit?: number;
  offset?: number;
};

export function listHcmsMemories(params: HCMSMemoryListParams = {}) {
  const search = new URLSearchParams();
  if (params.query?.trim()) {
    search.set("query", params.query.trim());
  }
  if (params.state && params.state !== "all") {
    search.set("state", params.state);
  }
  if (params.category?.trim()) {
    search.set("category", params.category.trim());
  }
  if (params.layerId && params.layerId !== "all") {
    search.set("layer_id", params.layerId);
  }
  if (params.limit !== undefined) {
    search.set("limit", String(params.limit));
  }
  if (params.offset !== undefined) {
    search.set("offset", String(params.offset));
  }
  const suffix = search.toString();
  return apiRequest<HCMSMemoryListResponse>(`/memory/hcms/memories${suffix ? `?${suffix}` : ""}`);
}

export function listPublicHcmsMemories(params: HCMSMemoryListParams = {}) {
  const search = new URLSearchParams();
  if (params.query?.trim()) {
    search.set("query", params.query.trim());
  }
  if (params.state && params.state !== "all") {
    search.set("state", params.state);
  }
  if (params.category?.trim()) {
    search.set("category", params.category.trim());
  }
  if (params.layerId && params.layerId !== "all") {
    search.set("layer_id", params.layerId);
  }
  if (params.limit !== undefined) {
    search.set("limit", String(params.limit));
  }
  if (params.offset !== undefined) {
    search.set("offset", String(params.offset));
  }
  const suffix = search.toString();
  return apiRequest<HCMSMemoryListResponse>(`/memory/list${suffix ? `?${suffix}` : ""}`);
}

export function getHcmsMemory(memoryId: string) {
  return apiRequest<HCMSMemoryResponse>(`/memory/hcms/memories/${encodeURIComponent(memoryId)}`);
}

export function getPublicHcmsMemory(memoryId: string) {
  return apiRequest<HCMSMemoryResponse>(`/memory/${encodeURIComponent(memoryId)}`);
}

export function deleteHcmsMemory(memoryId: string) {
  return apiRequest<HCMSMemoryDeleteResponse>(`/memory/hcms/memories/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
  });
}

export function deletePublicHcmsMemory(memoryId: string) {
  return apiRequest<HCMSMemoryDeleteResponse>(`/memory/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
  });
}

export function getHcmsMemoryHistory(memoryId: string) {
  return apiRequest<HCMSMemoryHistoryResponse>(`/memory/hcms/memories/${encodeURIComponent(memoryId)}/history`);
}

export function getHcmsMemoryVersions(memoryId: string) {
  return apiRequest<HCMSMemoryHistoryResponse>(`/memory/hcms/memories/${encodeURIComponent(memoryId)}/versions`);
}

export function getPublicHcmsMemoryVersions(memoryId: string) {
  return apiRequest<HCMSMemoryHistoryResponse>(`/memory/${encodeURIComponent(memoryId)}/versions`);
}

export function getHcmsMemoryRelations(memoryId: string) {
  return apiRequest<HCMSMemoryRelationsResponse>(`/memory/hcms/memories/${encodeURIComponent(memoryId)}/relations`);
}

export function getPublicHcmsMemoryRelations(memoryId: string) {
  return apiRequest<HCMSMemoryRelationsResponse>(`/memory/${encodeURIComponent(memoryId)}/relations`);
}

export function getHcmsMemoryDiff(memoryId: string) {
  return apiRequest<HCMSMemoryDiffResponse>(`/memory/hcms/memories/${encodeURIComponent(memoryId)}/diff`);
}
