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
    ProfileFacetAuditResponse,
    ProfileFacetGovernanceRequest,
    ProfileFacetGovernanceResponse,
    ProfileFacetListResponse,
    ProfileFacetRebuildRequest,
    ProfileFacetRebuildResponse,
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
    MemoryProviderAdminResponse,
    MemoryProviderTestResponse,
    MemoryStalenessResponse,
    MemoryTraceRequest,
    MemoryTraceResponse,
    MemoryRecallBenchmarkRequest,
    MemoryRecallBenchmarkResponse,
    MemoryRecallBenchmarkRunListResponse,
    MemoryRecallBenchmarkRunRequest,
    MemoryRecallBenchmarkRunView,
    MemoryRecallBenchmarkSuiteListResponse,
    MemoryRecallBenchmarkSuiteUpsertRequest,
    MemoryRecallBenchmarkSuiteView,
    MemoryOverviewView,
    MemoryProviderView,
    MemoryReviewDecisionRequest,
    MemoryReviewBatchRequest,
    MemoryReviewBatchResponse,
    MemoryReviewItemView,
    MemoryReviewResponse,
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
def get_memory_overview_vnext(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryOverviewView:
    return services.get_memory_overview_vnext(deps)


@router.get("/session", response_model=SessionMemoryView)
def get_memory_session_vnext(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SessionMemoryView:
    return services.get_session_memory(deps, thread_id)


@router.post("/session/search", response_model=SessionSearchResultView)
def search_memory_session_vnext(
    body: SessionSearchRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SessionSearchResultView:
    return services.search_memory_sessions(deps, body)


@router.get("/user", response_model=list[MemoryEntryView])
def list_memory_user_vnext(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryEntryView]:
    return services.list_memory_user_entries(deps)


@router.post("/user", response_model=MemoryEntryView)
def create_memory_user_vnext(
    body: MemoryEntryCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.create_memory_user_entry(deps, body)


@router.patch("/user/{entry_id}", response_model=MemoryEntryView)
def update_memory_user_vnext(
    entry_id: str,
    body: MemoryEntryUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.update_memory_user_entry(deps, entry_id, body)


@router.delete("/user/{entry_id}")
def delete_memory_user_vnext(
    entry_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, str]:
    return services.delete_memory_user_entry(deps, entry_id)


@router.get("/workspace", response_model=list[MemoryEntryView])
def list_memory_workspace_vnext(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryEntryView]:
    return services.list_memory_workspace_entries(deps)


@router.post("/workspace", response_model=MemoryEntryView)
def create_memory_workspace_vnext(
    body: MemoryEntryCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.create_memory_workspace_entry(deps, body)


@router.patch("/workspace/{entry_id}", response_model=MemoryEntryView)
def update_memory_workspace_vnext(
    entry_id: str,
    body: MemoryEntryUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.update_memory_workspace_entry(deps, entry_id, body)


@router.delete("/workspace/{entry_id}")
def delete_memory_workspace_vnext(
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


@router.get("/admin/providers", response_model=MemoryProviderAdminResponse)
def list_memory_admin_providers(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryProviderAdminResponse:
    return services.list_memory_admin_providers(deps)


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


@router.get("/admin/review", response_model=MemoryReviewResponse)
def list_memory_admin_review(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryReviewResponse:
    return services.list_memory_admin_review(deps)


@router.post("/admin/review/{review_id}/approve", response_model=MemoryEntryView)
def approve_memory_admin_review(
    review_id: str,
    body: MemoryReviewDecisionRequest | None = None,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryEntryView:
    return services.approve_memory_review_item(deps, review_id, body or MemoryReviewDecisionRequest())


@router.post("/admin/review/{review_id}/reject", response_model=MemoryReviewItemView)
def reject_memory_admin_review(
    review_id: str,
    body: MemoryReviewDecisionRequest | None = None,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryReviewItemView:
    return services.reject_memory_review_item(deps, review_id, body or MemoryReviewDecisionRequest())


@router.post("/admin/review/batch", response_model=MemoryReviewBatchResponse)
def batch_memory_admin_review(
    body: MemoryReviewBatchRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryReviewBatchResponse:
    return services.batch_memory_review(deps, body)


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


@router.get("/admin/profile/facets", response_model=ProfileFacetListResponse)
def list_memory_admin_profile_facets(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProfileFacetListResponse:
    return services.list_profile_facets(deps)


@router.post("/admin/profile/facets/rebuild", response_model=ProfileFacetRebuildResponse)
def rebuild_memory_admin_profile_facets(
    body: ProfileFacetRebuildRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProfileFacetRebuildResponse:
    return services.rebuild_profile_facets(deps, body)


@router.get("/admin/profile/facets/audit", response_model=ProfileFacetAuditResponse)
def list_memory_admin_profile_facet_audit(
    limit: int = 50,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProfileFacetAuditResponse:
    return services.list_profile_facet_audit(deps, limit=limit)


@router.post("/admin/profile/facets/{facet_id}/govern", response_model=ProfileFacetGovernanceResponse)
def govern_memory_admin_profile_facet(
    facet_id: str,
    body: ProfileFacetGovernanceRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProfileFacetGovernanceResponse:
    return services.govern_profile_facet(deps, facet_id, body)


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


@router.get("/providers", response_model=list[MemoryProviderView])
def list_memory_providers(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryProviderView]:
    return services.list_memory_providers(deps)


@router.post("/providers/reload", response_model=list[MemoryProviderView])
def reload_memory_providers(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[MemoryProviderView]:
    return services.reload_memory_providers(deps)


@router.post("/providers/{provider_id}/activate", response_model=MemoryProviderView)
def activate_memory_provider(
    provider_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryProviderView:
    return services.activate_memory_provider(deps, provider_id)


@router.post("/providers/{provider_id}/test", response_model=MemoryProviderTestResponse)
def test_memory_provider(
    provider_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> MemoryProviderTestResponse:
    return services.test_memory_provider(deps, provider_id)


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
