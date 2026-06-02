from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from anvil.agents import ThreadExecutionMode

from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import ApprovalCancelRequest, RunCompletedView, RunEventReplayView, RunRequestBody, ThreadStateView
from .. import services


router = APIRouter(prefix="/threads/{thread_id}/runs", tags=["thread-runs"])


@router.post("", response_model=RunCompletedView)
def run_thread(
    thread_id: str,
    body: RunRequestBody,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> RunCompletedView:
    return services.run_thread_sync(deps, thread_id, body)


@router.get("/stream")
def stream_thread_run(
    thread_id: str,
    message: str = Query(...),
    execution_mode: ThreadExecutionMode = Query(default=ThreadExecutionMode.AGENT),
    selected_model: str | None = Query(default=None),
    selected_reasoning_effort: str | None = Query(default=None),
    profile: str | None = Query(default=None),
    request_context: str | None = Query(default=None),
    approval_context: str | None = Query(default=None),
    upload_context: str | None = Query(default=None),
    client_message_id: str | None = Query(default=None),
    is_plan_mode: bool | None = Query(default=None),
    uploaded_filenames: list[str] = Query(default_factory=list),
    promoted_capabilities: list[str] = Query(default_factory=list),
    followup_dispatch_id: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> StreamingResponse:
    generator = services.stream_thread_run_events(
        deps,
        thread_id,
        message=message,
        execution_mode=execution_mode,
        selected_model=selected_model,
        selected_reasoning_effort=selected_reasoning_effort,
        profile=profile,
        request_context=request_context,
        approval_context=approval_context,
        upload_context=upload_context,
        client_message_id=client_message_id,
        is_plan_mode=is_plan_mode,
        promoted_capabilities=tuple(promoted_capabilities),
        uploaded_filenames=tuple(uploaded_filenames),
        followup_dispatch_id=followup_dispatch_id,
        last_event_id=last_event_id,
    )
    return StreamingResponse(generator, media_type="text/event-stream")


@router.post("/stream")
def stream_thread_run_post(
    thread_id: str,
    body: RunRequestBody,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> StreamingResponse:
    generator = services.stream_thread_run_events(
        deps,
        thread_id,
        message=body.message,
        execution_mode=body.execution_mode,
        selected_model=body.selected_model,
        selected_reasoning_effort=body.selected_reasoning_effort,
        profile=body.profile,
        request_context=body.request_context,
        approval_context=body.approval_context,
        upload_context=body.upload_context,
        client_message_id=body.client_message_id,
        is_plan_mode=body.is_plan_mode,
        promoted_capabilities=tuple(body.promoted_capabilities),
        uploaded_filenames=tuple(body.uploaded_filenames),
        followup_dispatch_id=body.followup_dispatch_id,
        last_event_id=last_event_id,
    )
    return StreamingResponse(generator, media_type="text/event-stream")


@router.get("/events", response_model=RunEventReplayView)
def list_thread_run_events(
    thread_id: str,
    run_id: str | None = Query(default=None),
    after_sequence: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> RunEventReplayView:
    return services.list_thread_run_events(
        deps,
        thread_id,
        run_id=run_id,
        after_sequence=after_sequence,
        limit=limit,
    )


@router.post("/interrupt", response_model=ThreadStateView)
def interrupt_thread_run(
    thread_id: str,
    body: ApprovalCancelRequest | None = None,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ThreadStateView:
    return services.interrupt_thread_run(
        deps,
        thread_id,
        reason=(body.reason if body is not None and body.reason else "Interrupted by user"),
    )
