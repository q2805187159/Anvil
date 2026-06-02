from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import (
    SkillContentView,
    SkillCuratorAutomationRequest,
    SkillCuratorAutomationRunResponse,
    SkillCuratorAutomationStatusResponse,
    SkillCuratorMaintenanceRequest,
    SkillCuratorRequest,
    SkillFileIndexView,
    SkillFileReadRequest,
    SkillFileReadView,
    SkillManageRequest,
    SkillListItemView,
    SkillView,
)
from .. import services


router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=list[SkillListItemView])
def list_skills(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[SkillListItemView]:
    return services.list_skills(deps)


@router.get("/{skill_id}", response_model=SkillView)
def get_skill(
    skill_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SkillView:
    return services.get_skill_view(deps, skill_id)


@router.get("/{skill_id}/content", response_model=SkillContentView)
def get_skill_content(
    skill_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SkillContentView:
    return services.get_skill_content_view(deps, skill_id)


@router.get("/{skill_id}/files", response_model=SkillFileIndexView)
def list_skill_files(
    skill_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SkillFileIndexView:
    return services.list_skill_files_view(deps, skill_id)


@router.post("/{skill_id}/files/read", response_model=SkillFileReadView)
def read_skill_file(
    skill_id: str,
    body: SkillFileReadRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SkillFileReadView:
    return services.read_skill_file_view(
        deps,
        skill_id,
        relative_path=body.relative_path,
        max_bytes=body.max_bytes,
    )


@router.post("/reload")
async def reload_skills(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, object]:
    return await services.reload_skills(deps)


@router.post("/manage")
async def manage_skill(
    body: SkillManageRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, object]:
    return await services.manage_skill(deps, body)


@router.post("/curator")
async def manage_skill_curator(
    body: SkillCuratorRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, object]:
    return await services.manage_skill_curator(deps, body)


@router.get("/curator/automation", response_model=SkillCuratorAutomationStatusResponse)
def get_skill_curator_automation(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SkillCuratorAutomationStatusResponse:
    return services.get_skill_curator_automation(deps)


@router.post("/curator/automation/run", response_model=SkillCuratorAutomationRunResponse)
async def run_skill_curator_automation(
    body: SkillCuratorAutomationRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SkillCuratorAutomationRunResponse:
    return await services.run_skill_curator_automation(deps, body)


@router.post("/curator/maintenance")
async def run_skill_curator_maintenance(
    body: SkillCuratorMaintenanceRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, object]:
    return await services.run_skill_curator_maintenance(deps, body)
