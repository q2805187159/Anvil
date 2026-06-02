from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import (
    ScheduledTaskAdminResponse,
    ScheduledTaskAutomationRunResponse,
    ScheduledTaskAutomationStatusResponse,
    ScheduledTaskCreateRequest,
    ScheduledTaskExecutionResponse,
    ScheduledTaskRunView,
    ScheduledTaskUpdateRequest,
    ScheduledTaskView,
)
from .. import services


router = APIRouter(prefix="/scheduled-tasks", tags=["scheduled-tasks"])


@router.get("", response_model=ScheduledTaskAdminResponse)
def list_scheduled_tasks(
    include_disabled: bool = Query(default=True),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskAdminResponse:
    return services.list_scheduled_tasks(deps, include_disabled=include_disabled)


@router.post("", response_model=ScheduledTaskView)
def create_scheduled_task(
    body: ScheduledTaskCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskView:
    return services.create_scheduled_task(deps, body)


@router.get("/executions", response_model=ScheduledTaskExecutionResponse)
def list_scheduled_task_executions(
    task_id: str | None = Query(default=None),
    limit: int = Query(default=50),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskExecutionResponse:
    return services.list_scheduled_task_executions(deps, task_id=task_id, limit=limit)


@router.get("/automation", response_model=ScheduledTaskAutomationStatusResponse)
def get_scheduled_task_automation(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskAutomationStatusResponse:
    return services.get_scheduled_task_automation(deps)


@router.post("/automation/run", response_model=ScheduledTaskAutomationRunResponse)
async def run_scheduled_task_automation(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskAutomationRunResponse:
    return await services.run_scheduled_task_automation(deps)


@router.get("/{task_id}", response_model=ScheduledTaskView)
def get_scheduled_task(
    task_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskView:
    return services.get_scheduled_task(deps, task_id)


@router.patch("/{task_id}", response_model=ScheduledTaskView)
def update_scheduled_task(
    task_id: str,
    body: ScheduledTaskUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskView:
    return services.update_scheduled_task(deps, task_id, body)


@router.post("/{task_id}/run", response_model=ScheduledTaskRunView)
async def run_scheduled_task(
    task_id: str,
    force: bool = Query(default=True),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskRunView:
    return await services.run_scheduled_task(deps, task_id, force=force)


@router.post("/{task_id}/pause", response_model=ScheduledTaskView)
def pause_scheduled_task(
    task_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskView:
    return services.pause_scheduled_task(deps, task_id)


@router.post("/{task_id}/resume", response_model=ScheduledTaskView)
def resume_scheduled_task(
    task_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskView:
    return services.resume_scheduled_task(deps, task_id)


@router.delete("/{task_id}", response_model=ScheduledTaskView)
def remove_scheduled_task(
    task_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ScheduledTaskView:
    return services.remove_scheduled_task(deps, task_id)
