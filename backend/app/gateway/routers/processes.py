from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import (
    ProcessLogView,
    ProcessResizeRequest,
    ProcessSessionView,
    ProcessSpawnRequest,
    ProcessStdinRequest,
    TerminalBackendCapabilitiesView,
)


router = APIRouter(prefix="/threads/{thread_id}/processes", tags=["processes"])


@router.get("", response_model=list[ProcessSessionView])
def list_thread_processes(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[ProcessSessionView]:
    return services.list_process_sessions(deps, thread_id)


@router.post("", response_model=ProcessSessionView)
def spawn_thread_process(
    thread_id: str,
    body: ProcessSpawnRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProcessSessionView:
    return services.spawn_process_session(deps, thread_id, body)


@router.get("/capabilities", response_model=TerminalBackendCapabilitiesView)
def get_thread_process_capabilities(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> TerminalBackendCapabilitiesView:
    return services.get_process_capabilities(deps, thread_id)


@router.get("/{session_id}", response_model=ProcessSessionView)
def get_thread_process(
    thread_id: str,
    session_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProcessSessionView:
    return services.get_process_session(deps, thread_id, session_id)


@router.post("/{session_id}/wait", response_model=ProcessSessionView)
def wait_thread_process(
    thread_id: str,
    session_id: str,
    timeout_seconds: int | None = Query(default=None),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProcessSessionView:
    return services.wait_process_session(deps, thread_id, session_id, timeout_seconds=timeout_seconds)


@router.post("/{session_id}/kill", response_model=ProcessSessionView)
def kill_thread_process(
    thread_id: str,
    session_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProcessSessionView:
    return services.kill_process_session(deps, thread_id, session_id)


@router.post("/{session_id}/stdin", response_model=ProcessSessionView)
def write_thread_process_stdin(
    thread_id: str,
    session_id: str,
    body: ProcessStdinRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProcessSessionView:
    return services.write_process_stdin(deps, thread_id, session_id, body)


@router.post("/{session_id}/stdin/close", response_model=ProcessSessionView)
def close_thread_process_stdin(
    thread_id: str,
    session_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProcessSessionView:
    return services.close_process_stdin(deps, thread_id, session_id)


@router.post("/{session_id}/interrupt", response_model=ProcessSessionView)
def interrupt_thread_process(
    thread_id: str,
    session_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProcessSessionView:
    return services.interrupt_process_session(deps, thread_id, session_id)


@router.post("/{session_id}/resize", response_model=ProcessSessionView)
def resize_thread_process(
    thread_id: str,
    session_id: str,
    body: ProcessResizeRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProcessSessionView:
    return services.resize_process_session(deps, thread_id, session_id, body)


@router.get("/{session_id}/log", response_model=ProcessLogView)
def get_thread_process_log(
    thread_id: str,
    session_id: str,
    offset: int = Query(default=0),
    cursor: int | None = Query(default=None),
    limit: int = Query(default=200),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ProcessLogView:
    return services.read_process_log(deps, thread_id, session_id, offset=offset, limit=limit, cursor=cursor)
