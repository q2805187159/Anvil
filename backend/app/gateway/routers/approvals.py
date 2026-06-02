from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import ApprovalCancelRequest, ApprovalResumeRequest, RunCompletedView, ThreadStateView


router = APIRouter(prefix="/threads/{thread_id}/approvals", tags=["approvals"])


@router.post("/approve", response_model=RunCompletedView)
def approve_pending_thread_run(
    thread_id: str,
    body: ApprovalResumeRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> RunCompletedView:
    return services.resume_thread_approval(deps, thread_id, body)


@router.post("/approve/stream")
def approve_pending_thread_run_stream(
    thread_id: str,
    body: ApprovalResumeRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> StreamingResponse:
    generator = services.stream_thread_approval_events(
        deps,
        thread_id,
        approval_context=body.approval_context,
        profile=body.profile,
        request_context=body.request_context,
        upload_context=body.upload_context,
        promoted_capabilities=tuple(body.promoted_capabilities),
    )
    return StreamingResponse(generator, media_type="text/event-stream")


@router.post("/cancel", response_model=ThreadStateView)
def cancel_pending_thread_run(
    thread_id: str,
    body: ApprovalCancelRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ThreadStateView:
    return services.cancel_thread_approval(deps, thread_id, body)
