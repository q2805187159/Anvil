from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import (
    MemoryArchiveSearchRequest,
    MemoryArchiveSearchResultView,
    MemoryConflictResolveRequest,
    MemoryConflictResponse,
    MemoryConflictView,
    MemoryEntryCreateRequest,
    MemoryEntryUpdateRequest,
    MemoryEntryView,
    MemoryFlushRequest,
    MemoryFlushResponse,
    MemoryGovernanceActionRequest,
    MemoryGovernanceActionResponse,
    MemoryGovernanceBatchRequest,
    MemoryGovernanceBatchResponse,
    MemoryMaintenanceAutomationRequest,
    MemoryMaintenanceAutomationRunResponse,
    MemoryMaintenanceAutomationStatusResponse,
    MemoryMaintenanceRequest,
    MemoryMaintenanceResponse,
    MemoryOnboardingRequest,
    MemoryOnboardingResponse,
    MemoryHealthResponse,
    MemoryAdminAuditView,
    MemoryAdminExportView,
    MemoryAdminImportRequest,
    MemoryAdminImportResponse,
    MemoryLayerView,
    MemoryEngineAdminResponse,
    MemoryEngineTestResponse,
    MemoryStalenessResponse,
    MemoryTraceRequest,
    MemoryTraceResponse,
    HCMSQueryRequest,
    HCMSRecallResponse,
    HCMSWhyResponse,
    HCMSMemoryListResponse,
    HCMSMemoryResponse,
    HCMSMemoryDeleteResponse,
    HCMSMemoryRelationsResponse,
    HCMSMemoryHistoryResponse,
    HCMSMemoryDiffResponse,
    MemoryRecallBenchmarkRequest,
    MemoryRecallBenchmarkResponse,
    MemoryRecallBenchmarkRunListResponse,
    MemoryRecallBenchmarkRunRequest,
    MemoryRecallBenchmarkRunView,
    MemoryRecallBenchmarkSuiteListResponse,
    MemoryRecallBenchmarkSuiteUpsertRequest,
    MemoryRecallBenchmarkSuiteView,
    MemoryOverviewView,
    MemoryEngineView,
    MemoryStoreView,
    ReflectionJobAdminResponse,
    ReflectionJobCreateRequest,
    ReflectionJobRunView,
    ReflectionJobView,
    SessionMemoryView,
    SessionSearchRequest,
    SessionSearchResultView,
)
from .. import services


router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=MemoryOverviewView)
def get_memory_overview(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryOverviewView:
    return services.get_memory_overview(deps)


@router.get("/overview", response_model=MemoryOverviewView)
def get_memory_overview_hcms(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryOverviewView:
    return services.get_memory_overview_hcms(deps)


@router.get("/session", response_model=SessionMemoryView)
def get_memory_session(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SessionMemoryView:
    return services.get_session_memory(deps, thread_id)


@router.post("/session/search", response_model=SessionSearchResultView)
def search_memory_session(
    body: SessionSearchRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SessionSearchResultView:
    return services.search_memory_sessions(deps, body)


@router.get("/user", response_model=list[MemoryEntryView])
def list_memory_user(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryEntryView]:
    return services.list_memory_user_entries(deps)


@router.post("/user", response_model=MemoryEntryView)
def create_memory_user(
    body: MemoryEntryCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.create_memory_user_entry(deps, body)


@router.patch("/user/{entry_id}", response_model=MemoryEntryView)
def update_memory_user(
    entry_id: str,
    body: MemoryEntryUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.update_memory_user_entry(deps, entry_id, body)


@router.delete("/user/{entry_id}")
def delete_memory_user(
    entry_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, str]:
    return services.delete_memory_user_entry(deps, entry_id)


@router.get("/workspace", response_model=list[MemoryEntryView])
def list_memory_workspace(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryEntryView]:
    return services.list_memory_workspace_entries(deps)


@router.post("/workspace", response_model=MemoryEntryView)
def create_memory_workspace(
    body: MemoryEntryCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.create_memory_workspace_entry(deps, body)


@router.patch("/workspace/{entry_id}", response_model=MemoryEntryView)
def update_memory_workspace(
    entry_id: str,
    body: MemoryEntryUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.update_memory_workspace_entry(deps, entry_id, body)


@router.delete("/workspace/{entry_id}")
def delete_memory_workspace(
    entry_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, str]:
    return services.delete_memory_workspace_entry(deps, entry_id)


@router.post("/trace", response_model=MemoryTraceResponse)
def get_memory_trace(
    body: MemoryTraceRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryTraceResponse:
    return services.get_memory_trace(deps, body)


@router.post("/hcms/recall", response_model=HCMSRecallResponse)
def hcms_recall(
    body: HCMSQueryRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSRecallResponse:
    return services.hcms_recall(deps, body)


@router.post("/hcms/search", response_model=HCMSRecallResponse)
def hcms_search(
    body: HCMSQueryRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSRecallResponse:
    return services.hcms_recall(deps, body)


@router.post("/search", response_model=HCMSRecallResponse)
def search_memory_hcms(
    body: HCMSQueryRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSRecallResponse:
    return services.hcms_recall(deps, body)


@router.post("/hcms/why", response_model=HCMSWhyResponse)
def hcms_why(
    body: HCMSQueryRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSWhyResponse:
    return services.hcms_why(deps, body)


@router.get("/hcms/memories", response_model=HCMSMemoryListResponse)
def list_hcms_memories(
    query: str | None = None,
    state: str | None = None,
    category: str | None = None,
    layer_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryListResponse:
    return services.list_hcms_memories(
        deps,
        query=query,
        state=state,
        category=category,
        layer_id=layer_id,
        limit=limit,
        offset=offset,
    )


@router.get("/hcms/memories/{memory_id}", response_model=HCMSMemoryResponse)
def hcms_memory(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryResponse:
    return services.hcms_memory(deps, memory_id)


@router.delete("/hcms/memories/{memory_id}", response_model=HCMSMemoryDeleteResponse)
def delete_hcms_memory(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryDeleteResponse:
    return services.delete_hcms_memory(deps, memory_id)


@router.get("/hcms/memories/{memory_id}/history", response_model=HCMSMemoryHistoryResponse)
def hcms_history(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryHistoryResponse:
    return services.hcms_history(deps, memory_id)


@router.get("/hcms/memories/{memory_id}/versions", response_model=HCMSMemoryHistoryResponse)
def hcms_versions(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryHistoryResponse:
    return services.hcms_history(deps, memory_id)


@router.get("/hcms/memories/{memory_id}/relations", response_model=HCMSMemoryRelationsResponse)
def hcms_relations(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryRelationsResponse:
    return services.hcms_relations(deps, memory_id)


@router.get("/hcms/memories/{memory_id}/diff", response_model=HCMSMemoryDiffResponse)
def hcms_diff(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryDiffResponse:
    return services.hcms_diff(deps, memory_id)


@router.get("/admin/engines", response_model=MemoryEngineAdminResponse)
def list_memory_admin_engines(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEngineAdminResponse:
    return services.list_memory_admin_engines(deps)


@router.get("/admin/reflections", response_model=ReflectionJobAdminResponse)
def list_memory_admin_reflections(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ReflectionJobAdminResponse:
    return services.list_memory_admin_reflections(deps)


@router.get("/admin/conflicts", response_model=MemoryConflictResponse)
def list_memory_admin_conflicts(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryConflictResponse:
    return services.list_memory_admin_conflicts(deps)


@router.get("/admin/staleness", response_model=MemoryStalenessResponse)
def list_memory_admin_staleness(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryStalenessResponse:
    return services.list_memory_admin_staleness(deps)


@router.get("/admin/health", response_model=MemoryHealthResponse)
def get_memory_admin_health(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryHealthResponse:
    return services.get_memory_admin_health(deps)


@router.post("/admin/benchmark", response_model=MemoryRecallBenchmarkResponse)
def run_memory_admin_benchmark(
    body: MemoryRecallBenchmarkRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryRecallBenchmarkResponse:
    return services.run_memory_admin_benchmark(deps, body)


@router.get("/admin/benchmark/suites", response_model=MemoryRecallBenchmarkSuiteListResponse)
def list_memory_admin_benchmark_suites(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryRecallBenchmarkSuiteListResponse:
    return services.list_memory_admin_benchmark_suites(deps)


@router.post("/admin/benchmark/suites", response_model=MemoryRecallBenchmarkSuiteView)
def upsert_memory_admin_benchmark_suite(
    body: MemoryRecallBenchmarkSuiteUpsertRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryRecallBenchmarkSuiteView:
    return services.upsert_memory_admin_benchmark_suite(deps, body)


@router.delete("/admin/benchmark/suites/{suite_id}", response_model=MemoryRecallBenchmarkSuiteView)
def delete_memory_admin_benchmark_suite(
    suite_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryRecallBenchmarkSuiteView:
    return services.delete_memory_admin_benchmark_suite(deps, suite_id)


@router.post("/admin/benchmark/suites/{suite_id}/run", response_model=MemoryRecallBenchmarkRunView)
def run_memory_admin_benchmark_suite(
    suite_id: str,
    body: MemoryRecallBenchmarkRunRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryRecallBenchmarkRunView:
    return services.run_memory_admin_benchmark_suite(deps, suite_id, body)


@router.get("/admin/benchmark/runs", response_model=MemoryRecallBenchmarkRunListResponse)
def list_memory_admin_benchmark_runs(
    suite_id: str | None = None,
    limit: int = 20,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryRecallBenchmarkRunListResponse:
    return services.list_memory_admin_benchmark_runs(deps, suite_id=suite_id, limit=limit)


@router.get("/admin/export", response_model=MemoryAdminExportView)
def export_memory_admin(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryAdminExportView:
    return services.export_memory_admin(deps)


@router.post("/admin/import", response_model=MemoryAdminImportResponse)
def import_memory_admin(
    body: MemoryAdminImportRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryAdminImportResponse:
    return services.import_memory_admin(deps, body)


@router.get("/admin/audit", response_model=MemoryAdminAuditView)
def audit_memory_admin(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryAdminAuditView:
    return services.audit_memory_admin(deps)


@router.post("/admin/flush", response_model=MemoryFlushResponse)
def flush_memory_admin(
    body: MemoryFlushRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryFlushResponse:
    return services.flush_memory_admin(deps, body)


@router.post("/admin/onboarding", response_model=MemoryOnboardingResponse)
def onboard_memory_admin_workspace(
    body: MemoryOnboardingRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryOnboardingResponse:
    return services.onboard_memory_workspace(deps, body)


@router.post("/admin/memories/{memory_id}/govern", response_model=MemoryGovernanceActionResponse)
def govern_memory_admin_entry(
    memory_id: str,
    body: MemoryGovernanceActionRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryGovernanceActionResponse:
    return services.govern_memory_entry(deps, memory_id, body)


@router.post("/admin/governance", response_model=MemoryGovernanceBatchResponse)
def batch_govern_memory_admin(
    body: MemoryGovernanceBatchRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryGovernanceBatchResponse:
    return services.batch_govern_memory(deps, body)


@router.post("/admin/maintenance", response_model=MemoryMaintenanceResponse)
def run_memory_admin_maintenance(
    body: MemoryMaintenanceRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryMaintenanceResponse:
    return services.run_memory_maintenance(deps, body)


@router.get("/admin/maintenance/automation", response_model=MemoryMaintenanceAutomationStatusResponse)
def get_memory_admin_maintenance_automation(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryMaintenanceAutomationStatusResponse:
    return services.get_memory_maintenance_automation(deps)


@router.post("/admin/maintenance/automation/run", response_model=MemoryMaintenanceAutomationRunResponse)
async def run_memory_admin_maintenance_automation(
    body: MemoryMaintenanceAutomationRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryMaintenanceAutomationRunResponse:
    return await services.run_memory_maintenance_automation(deps, body)


@router.post("/admin/conflicts/{conflict_id}/resolve", response_model=MemoryConflictView)
def resolve_memory_admin_conflict(
    conflict_id: str,
    body: MemoryConflictResolveRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryConflictView:
    return services.resolve_memory_conflict(deps, conflict_id, body)


@router.get("/stores", response_model=list[MemoryStoreView])
def list_memory_stores(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryStoreView]:
    return services.list_memory_stores(deps)


@router.get("/layers", response_model=list[MemoryLayerView])
def list_memory_layers(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryLayerView]:
    return services.list_memory_layers(deps)


@router.get("/layers/session", response_model=SessionMemoryView)
def get_session_memory(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SessionMemoryView:
    return services.get_session_memory(deps, thread_id)


@router.get("/layers/user/entries", response_model=list[MemoryEntryView])
def list_user_memory_entries(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryEntryView]:
    return services.list_memory_layer_entries(deps, "user")


@router.post("/layers/user/entries", response_model=MemoryEntryView)
def create_user_memory_entry(
    body: MemoryEntryCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.create_memory_layer_entry(deps, "user", body)


@router.patch("/layers/user/entries/{entry_id}", response_model=MemoryEntryView)
def update_user_memory_entry(
    entry_id: str,
    body: MemoryEntryUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.update_memory_layer_entry(deps, "user", entry_id, body)


@router.delete("/layers/user/entries/{entry_id}")
def delete_user_memory_entry(
    entry_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, str]:
    return services.delete_memory_layer_entry(deps, "user", entry_id)


@router.get("/layers/workspace/entries", response_model=list[MemoryEntryView])
def list_workspace_memory_entries(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryEntryView]:
    return services.list_memory_layer_entries(deps, "workspace")


@router.post("/layers/workspace/entries", response_model=MemoryEntryView)
def create_workspace_memory_entry(
    body: MemoryEntryCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.create_memory_layer_entry(deps, "workspace", body)


@router.patch("/layers/workspace/entries/{entry_id}", response_model=MemoryEntryView)
def update_workspace_memory_entry(
    entry_id: str,
    body: MemoryEntryUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.update_memory_layer_entry(deps, "workspace", entry_id, body)


@router.delete("/layers/workspace/entries/{entry_id}")
def delete_workspace_memory_entry(
    entry_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, str]:
    return services.delete_memory_layer_entry(deps, "workspace", entry_id)


@router.get("/stores/{store_id}/entries", response_model=list[MemoryEntryView])
def list_memory_entries(
    store_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryEntryView]:
    return services.list_memory_store_entries(deps, store_id)


@router.post("/stores/{store_id}/entries", response_model=MemoryEntryView)
def create_memory_entry(
    store_id: str,
    body: MemoryEntryCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.create_memory_entry(deps, store_id, body)


@router.patch("/stores/{store_id}/entries/{entry_id}", response_model=MemoryEntryView)
def update_memory_entry(
    store_id: str,
    entry_id: str,
    body: MemoryEntryUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.update_memory_entry(deps, store_id, entry_id, body)


@router.delete("/stores/{store_id}/entries/{entry_id}")
def delete_memory_entry(
    store_id: str,
    entry_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, str]:
    return services.delete_memory_entry(deps, store_id, entry_id)


@router.get("/engines", response_model=list[MemoryEngineView])
def list_memory_engines(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryEngineView]:
    return services.list_memory_engines(deps)


@router.post("/engines/reload", response_model=list[MemoryEngineView])
def reload_memory_engines(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryEngineView]:
    return services.reload_memory_engines(deps)


@router.post("/engines/{engine_id}/activate", response_model=MemoryEngineView)
def activate_memory_engine(
    engine_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEngineView:
    return services.activate_memory_engine(deps, engine_id)


@router.post("/engines/{engine_id}/test", response_model=MemoryEngineTestResponse)
def test_memory_engine(
    engine_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEngineTestResponse:
    return services.test_memory_engine(deps, engine_id)


@router.post("/archive/search", response_model=MemoryArchiveSearchResultView)
def search_memory_archive(
    body: MemoryArchiveSearchRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryArchiveSearchResultView:
    return services.search_memory_archive(deps, body)


@router.post("/session-search", response_model=SessionSearchResultView)
def search_memory_sessions(
    body: SessionSearchRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SessionSearchResultView:
    return services.search_memory_sessions(deps, body)


@router.get("/reflections/jobs", response_model=list[ReflectionJobView])
def list_reflection_jobs(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[ReflectionJobView]:
    return services.list_reflection_jobs(deps)


@router.post("/reflections/jobs", response_model=ReflectionJobView)
def create_reflection_job(
    body: ReflectionJobCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ReflectionJobView:
    return services.create_reflection_job(deps, body)


@router.post("/reflections/jobs/{job_id}/run", response_model=ReflectionJobRunView)
def run_reflection_job(
    job_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ReflectionJobRunView:
    return services.run_reflection_job(deps, job_id)


@router.post("/reflections/jobs/{job_id}/pause", response_model=ReflectionJobView)
def pause_reflection_job(
    job_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ReflectionJobView:
    return services.pause_reflection_job(deps, job_id)


@router.post("/reflections/jobs/{job_id}/resume", response_model=ReflectionJobView)
def resume_reflection_job(
    job_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ReflectionJobView:
    return services.resume_reflection_job(deps, job_id)


@router.post("/reflections/jobs/{job_id}/remove", response_model=ReflectionJobView)
def remove_reflection_job(
    job_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ReflectionJobView:
    return services.remove_reflection_job(deps, job_id)


@router.get("/list", response_model=HCMSMemoryListResponse)
def list_memory_hcms_alias(
    query: str | None = None,
    state: str | None = None,
    category: str | None = None,
    layer_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryListResponse:
    return services.list_hcms_memories(
        deps,
        query=query,
        state=state,
        category=category,
        layer_id=layer_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{memory_id}/versions", response_model=HCMSMemoryHistoryResponse)
def memory_hcms_versions_alias(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryHistoryResponse:
    return services.hcms_history(deps, memory_id)


@router.get("/{memory_id}/relations", response_model=HCMSMemoryRelationsResponse)
def memory_hcms_relations_alias(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryRelationsResponse:
    return services.hcms_relations(deps, memory_id)


@router.get("/{memory_id}", response_model=HCMSMemoryResponse)
def memory_hcms_alias(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryResponse:
    return services.hcms_memory(deps, memory_id)


@router.delete("/{memory_id}", response_model=HCMSMemoryDeleteResponse)
def delete_memory_hcms_alias(
    memory_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> HCMSMemoryDeleteResponse:
    return services.delete_hcms_memory(deps, memory_id)
