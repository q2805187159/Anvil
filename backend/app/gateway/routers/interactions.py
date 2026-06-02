from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import RunCompletedView, UserInteractionResumeRequest


router = APIRouter(prefix="/threads/{thread_id}/interactions", tags=["interactions"])


@router.post("/resume", response_model=RunCompletedView)
def resume_pending_user_interaction(
    thread_id: str,
    body: UserInteractionResumeRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> RunCompletedView:
    return services.resume_thread_user_interaction(deps, thread_id, body)


@router.post("/resume/stream")
def resume_pending_user_interaction_stream(
    thread_id: str,
    body: UserInteractionResumeRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> StreamingResponse:
    generator = services.stream_thread_user_interaction_events(deps, thread_id, body)
    return StreamingResponse(generator, media_type="text/event-stream")
