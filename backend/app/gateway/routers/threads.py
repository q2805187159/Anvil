from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import AppRuntimeDeps, get_runtime_deps
from fastapi.responses import StreamingResponse

from ..models import (
    MessageEditResendRequest,
    EvaluationBatchReportView,
    EvaluationReportRequestView,
    EvaluationThreadReportView,
    QueuedFollowUpCreateRequest,
    QueuedFollowUpUpdateRequest,
    QueuedFollowUpView,
    RunCompletedView,
    ThreadCreateRequest,
    ThreadDeleteView,
    ThreadDetailView,
    ThreadSettingsUpdateRequest,
    ThreadSettingsView,
    ThreadStateView,
    ThreadView,
    TrajectoryBatchExportRequest,
    TrajectoryBatchExportView,
    TrajectoryExportRequest,
    TrajectoryExportView,
)
from .. import services


router = APIRouter(prefix="/threads", tags=["threads"])


@router.get("", response_model=list[ThreadView])
def list_threads(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[ThreadView]:
    return services.list_threads(deps)


@router.post("", response_model=ThreadView)
def create_thread(
    body: ThreadCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ThreadView:
    return services.create_thread(deps, body.thread_id, body.workspace_root)


@router.delete("/{thread_id}", response_model=ThreadDeleteView)
def delete_thread(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ThreadDeleteView:
    return services.delete_thread(deps, thread_id)


@router.get("/{thread_id}", response_model=ThreadView)
def get_thread(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ThreadView:
    return services.get_thread_view(deps, thread_id)


@router.get("/{thread_id}/state", response_model=ThreadStateView)
def get_thread_state(
    thread_id: str,
    state_scope: Literal["chat", "full"] = Query(default="chat"),
    state_source: Literal["snapshot", "event_log"] = Query(default="snapshot"),
    run_id: str | None = Query(default=None),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ThreadStateView:
    return services.get_thread_state_view(
        deps,
        thread_id,
        state_scope=state_scope,
        state_source=state_source,
        run_id=run_id,
    )


@router.post("/{thread_id}/followups", response_model=QueuedFollowUpView)
def enqueue_thread_followup(
    thread_id: str,
    body: QueuedFollowUpCreateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> QueuedFollowUpView:
    return services.enqueue_thread_followup(deps, thread_id, body)


@router.patch("/{thread_id}/followups/{queue_id}", response_model=QueuedFollowUpView)
def update_thread_followup(
    thread_id: str,
    queue_id: str,
    body: QueuedFollowUpUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> QueuedFollowUpView:
    return services.update_thread_followup(deps, thread_id, queue_id, body)


@router.delete("/{thread_id}/followups/{queue_id}", response_model=QueuedFollowUpView)
def delete_thread_followup(
    thread_id: str,
    queue_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> QueuedFollowUpView:
    return services.delete_thread_followup(deps, thread_id, queue_id)


@router.post("/{thread_id}/followups/next", response_model=QueuedFollowUpView | None)
def pop_next_thread_followup(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> QueuedFollowUpView | None:
    return services.pop_next_thread_followup(deps, thread_id)


@router.get("/{thread_id}/detail", response_model=ThreadDetailView)
def get_thread_detail(
    thread_id: str,
    message_offset: int | None = Query(default=None, ge=0),
    message_limit: int | None = Query(default=None, ge=1, le=500),
    state_scope: Literal["chat", "full"] = Query(default="chat"),
    state_source: Literal["snapshot", "event_log", "auto"] = Query(default="auto"),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ThreadDetailView:
    if message_offset is not None and message_limit is None:
        raise HTTPException(
            status_code=422,
            detail="message_offset requires message_limit",
        )
    return services.get_thread_detail_view(
        deps,
        thread_id,
        message_offset=message_offset,
        message_limit=message_limit,
        state_scope=state_scope,
        state_source=state_source,
    )


@router.get("/{thread_id}/trajectory", response_model=TrajectoryExportView)
def get_thread_trajectory(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> TrajectoryExportView:
    return services.get_thread_trajectory_view(deps, thread_id)


@router.post("/{thread_id}/trajectory", response_model=TrajectoryExportView)
def post_thread_trajectory(
    thread_id: str,
    body: TrajectoryExportRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> TrajectoryExportView:
    return services.get_thread_trajectory_view(deps, thread_id, body)


@router.post("/trajectory/export", response_model=TrajectoryBatchExportView)
def post_trajectory_batch_export(
    body: TrajectoryBatchExportRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> TrajectoryBatchExportView:
    return services.export_trajectory_batch_view(deps, body)


@router.get("/{thread_id}/evaluation-report", response_model=EvaluationThreadReportView)
def get_thread_evaluation_report(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> EvaluationThreadReportView:
    return services.get_thread_evaluation_report_view(deps, thread_id)


@router.post("/evaluation-report", response_model=EvaluationBatchReportView)
def post_evaluation_batch_report(
    body: EvaluationReportRequestView,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> EvaluationBatchReportView:
    return services.build_evaluation_batch_report_view(deps, body)


@router.get("/{thread_id}/settings", response_model=ThreadSettingsView)
def get_thread_settings(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ThreadSettingsView:
    return services.get_thread_settings_view(deps, thread_id)


@router.put("/{thread_id}/settings", response_model=ThreadSettingsView)
def put_thread_settings(
    thread_id: str,
    body: ThreadSettingsUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ThreadSettingsView:
    return services.update_thread_settings(deps, thread_id, body)


@router.post("/{thread_id}/messages/{message_id}/edit-latest-and-resend", response_model=RunCompletedView)
def edit_latest_and_resend(
    thread_id: str,
    message_id: str,
    body: MessageEditResendRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> RunCompletedView:
    return services.edit_latest_user_message_and_run_sync(deps, thread_id, message_id, body)


@router.post("/{thread_id}/messages/{message_id}/edit-latest-and-resend/stream")
def edit_latest_and_resend_stream(
    thread_id: str,
    message_id: str,
    body: MessageEditResendRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> StreamingResponse:
    generator = services.iter_edit_latest_user_message_events(deps, thread_id, message_id, body)
    return StreamingResponse(generator, media_type="text/event-stream")
