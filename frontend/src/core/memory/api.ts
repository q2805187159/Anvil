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
  MemoryProviderView,
  MemoryProviderTestResponse,
  ProfileFacetAuditResponse,
  ProfileFacetGovernanceRequest,
  ProfileFacetGovernanceResponse,
  ProfileFacetListResponse,
  ProfileFacetRebuildRequest,
  ProfileFacetRebuildResponse,
  MemoryReviewBatchRequest,
  MemoryReviewBatchResponse,
  MemoryReviewDecisionRequest,
  MemoryReviewItemView,
  MemoryReviewResponse,
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

export function listMemoryProviders() {
  return apiRequest<{ items: MemoryProviderView[] }>("/memory/admin/providers").then((payload) => payload.items);
}

export function activateMemoryProvider(providerId: string) {
  return apiRequest<MemoryProviderView>(`/memory/providers/${providerId}/activate`, {
    method: "POST",
  });
}

export function reloadMemoryProviders() {
  return apiRequest<MemoryProviderView[]>("/memory/providers/reload", {
    method: "POST",
  });
}

export function testMemoryProvider(providerId: string) {
  return apiRequest<MemoryProviderTestResponse>(`/memory/providers/${providerId}/test`, {
    method: "POST",
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

export function listMemoryReview() {
  return apiRequest<MemoryReviewResponse>("/memory/admin/review").then((payload) => payload.items);
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

export function listProfileFacets() {
  return apiRequest<ProfileFacetListResponse>("/memory/admin/profile/facets");
}

export function governProfileFacet(facetId: string, body: ProfileFacetGovernanceRequest) {
  return apiRequest<ProfileFacetGovernanceResponse>(`/memory/admin/profile/facets/${encodeURIComponent(facetId)}/govern`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function rebuildProfileFacets(body: ProfileFacetRebuildRequest = {}) {
  return apiRequest<ProfileFacetRebuildResponse>("/memory/admin/profile/facets/rebuild", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listProfileFacetAudit(limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  return apiRequest<ProfileFacetAuditResponse>(`/memory/admin/profile/facets/audit?${params.toString()}`);
}

export function approveMemoryReview(reviewId: string, body: MemoryReviewDecisionRequest = {}) {
  return apiRequest<MemoryEntryView>(`/memory/admin/review/${reviewId}/approve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function rejectMemoryReview(reviewId: string, body: MemoryReviewDecisionRequest = {}) {
  return apiRequest<MemoryReviewItemView>(`/memory/admin/review/${reviewId}/reject`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function batchMemoryReview(body: MemoryReviewBatchRequest) {
  return apiRequest<MemoryReviewBatchResponse>("/memory/admin/review/batch", {
    method: "POST",
    body: JSON.stringify(body),
  });
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
