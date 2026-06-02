from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import SubagentDependencyGraphView, SubagentTaskView


router = APIRouter(prefix="/threads/{thread_id}/subagents", tags=["subagents"])


@router.get("", response_model=list[SubagentTaskView])
def list_thread_subagents(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[SubagentTaskView]:
    return services.list_subagent_tasks(deps, thread_id)


@router.get("/graph", response_model=SubagentDependencyGraphView)
def get_thread_subagent_graph(
    thread_id: str,
    parent_run_id: str | None = Query(default=None),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SubagentDependencyGraphView:
    return services.get_subagent_dependency_graph(deps, thread_id, parent_run_id=parent_run_id)


@router.get("/{task_id}", response_model=SubagentTaskView)
def get_thread_subagent(
    thread_id: str,
    task_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SubagentTaskView:
    return services.get_subagent_task(deps, thread_id, task_id)


@router.post("/{task_id}/wait", response_model=SubagentTaskView)
def wait_thread_subagent(
    thread_id: str,
    task_id: str,
    timeout_seconds: int | None = Query(default=None),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SubagentTaskView:
    return services.wait_subagent_task(deps, thread_id, task_id, timeout_seconds=timeout_seconds)


@router.post("/{task_id}/cancel", response_model=SubagentTaskView)
def cancel_thread_subagent(
    thread_id: str,
    task_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SubagentTaskView:
    return services.cancel_subagent_task(deps, thread_id, task_id)
