from __future__ import annotations

from datetime import datetime, timezone
import shutil
from pathlib import Path

from anvil.agents.thread_state import (
    RecentToolActivity,
    RecentApprovalEvent,
    ThreadExecutionMode,
    ThreadLifecycleStatus,
    ThreadMetadataView,
    ThreadState,
)
from anvil.sandbox import ArtifactKind, PathService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _thread_metadata_recency_sort_key(metadata: ThreadMetadataView) -> tuple[float, str]:
    activity_at = metadata.last_message_at or metadata.updated_at
    return (-activity_at.timestamp(), metadata.thread_id)


_INTERRUPTIBLE_STEP_STATUSES = {"pending", "running"}
_INTERRUPTIBLE_TOOL_STATUSES = {"pending", "running", "started", "in_progress"}


def _mark_steps_interrupted(steps: list[dict], *, reason: str, completed_at: datetime) -> list[dict]:
    updated: list[dict] = []
    for step in steps:
        if not isinstance(step, dict):
            updated.append(step)
            continue
        next_step = dict(step)
        if str(next_step.get("status") or "").lower() in _INTERRUPTIBLE_STEP_STATUSES:
            next_step["status"] = "error"
            next_step["error"] = next_step.get("error") or reason
            next_step["completed_at"] = next_step.get("completed_at") or completed_at.isoformat()
        updated.append(next_step)
    return updated


def _mark_tool_activity_interrupted(items: list[RecentToolActivity], *, reason: str, completed_at: datetime) -> list[RecentToolActivity | dict]:
    updated: list[RecentToolActivity | dict] = []
    for item in items:
        if isinstance(item, dict):
            status = str(item.get("status") or "").lower()
            if status not in _INTERRUPTIBLE_TOOL_STATUSES:
                updated.append(item)
                continue
            next_item = dict(item)
            next_item["status"] = "interrupted"
            next_item["result_text"] = next_item.get("result_text") or reason
            next_item["completed_at"] = next_item.get("completed_at") or completed_at.isoformat()
            updated.append(next_item)
            continue
        status = str(item.status or "").lower()
        if status not in _INTERRUPTIBLE_TOOL_STATUSES:
            updated.append(item)
            continue
        updated.append(
            item.model_copy(
                update={
                    "status": "interrupted",
                    "result_text": item.result_text or reason,
                    "completed_at": item.completed_at or completed_at,
                }
            )
        )
    return updated


class ThreadRuntimeService:
    def __init__(self, *, path_service: PathService, checkpointer, store) -> None:
        self.path_service = path_service
        self.checkpointer = checkpointer
        self.store = store
        self._metadata_index_reconciled = False

    def list_threads(self) -> tuple[ThreadMetadataView, ...]:
        metadata = self.store.list_threads()
        metadata_ids = {item.thread_id for item in metadata}
        checkpoint_ids = set(self.checkpointer.list_thread_ids())
        if not self._metadata_index_reconciled or metadata_ids != checkpoint_ids:
            metadata = self._reconcile_thread_metadata_index(checkpoint_ids=checkpoint_ids)
            self._metadata_index_reconciled = True
        return tuple(sorted(metadata, key=_thread_metadata_recency_sort_key))

    def _reconcile_thread_metadata_index(self, *, checkpoint_ids: set[str] | None = None) -> list[ThreadMetadataView]:
        checkpoint_ids = checkpoint_ids if checkpoint_ids is not None else set(self.checkpointer.list_thread_ids())
        metadata_by_thread = {metadata.thread_id: metadata for metadata in self.store.list_threads()}
        refreshed: list[ThreadMetadataView] = []
        for thread_id in sorted(checkpoint_ids):
            state = self.checkpointer.get_thread_state(thread_id)
            if state is None:
                continue
            next_metadata = ThreadMetadataView.from_thread_state(state)
            current_metadata = metadata_by_thread.pop(thread_id, None)
            if current_metadata != next_metadata:
                self.store.put_thread_metadata(next_metadata)
            refreshed.append(next_metadata)
        for metadata in metadata_by_thread.values():
            if self.checkpointer.get_thread_state(metadata.thread_id) is None:
                self.store.delete_thread(metadata.thread_id)
        return refreshed

    def create_thread(self, *, thread_id: str) -> ThreadMetadataView:
        if self.checkpointer.get_thread_state(thread_id) is not None:
            raise ValueError(f"thread '{thread_id}' already exists")

        thread_data = self.path_service.bootstrap_thread_paths(thread_id)
        state = ThreadState(
            identity={"thread_id": thread_id},
            lifecycle={"status": ThreadLifecycleStatus.READY},
            execution={"is_plan_mode": False},
            thread_data=thread_data.model_dump(),
        )
        self.checkpointer.put_thread_state(state)
        metadata = ThreadMetadataView.from_thread_state(state)
        self.store.put_thread_metadata(metadata)
        return metadata

    def delete_thread(self, thread_id: str) -> ThreadMetadataView:
        metadata = self.get_thread_metadata(thread_id)
        self.checkpointer.delete_thread(thread_id)
        self.store.delete_thread(thread_id)
        self._delete_thread_storage(thread_id)
        return metadata

    def get_thread_state(self, thread_id: str) -> ThreadState:
        state = self.checkpointer.get_thread_state(thread_id)
        if state is None:
            raise ValueError(f"thread '{thread_id}' was not found")
        return state

    def get_thread_metadata(self, thread_id: str) -> ThreadMetadataView:
        metadata = self.store.get_thread_metadata(thread_id)
        if metadata is not None:
            return metadata

        state = self.get_thread_state(thread_id)
        metadata = ThreadMetadataView.from_thread_state(state)
        self.store.put_thread_metadata(metadata)
        return metadata

    def update_thread_settings(
        self,
        thread_id: str,
        *,
        execution_mode: ThreadExecutionMode | None = None,
        selected_model: str | None = None,
        selected_profile: str | None = None,
        selected_reasoning_effort: str | None = None,
        is_plan_mode: bool | None = None,
        workspace_root: str | None = None,
    ) -> ThreadState:
        state = self.get_thread_state(thread_id)
        updated = state.model_copy(deep=True)
        if execution_mode is not None:
            updated.execution.execution_mode = execution_mode
        if selected_model is not None:
            updated.execution.selected_model = selected_model or None
        if selected_profile is not None:
            updated.execution.selected_profile = selected_profile or None
        if selected_reasoning_effort is not None:
            updated.execution.selected_reasoning_effort = selected_reasoning_effort or None
        if is_plan_mode is not None:
            updated.execution.is_plan_mode = bool(is_plan_mode)
        if workspace_root is not None:
            normalized_workspace_root = workspace_root.strip()
            thread_data = self.path_service.bootstrap_thread_paths(
                thread_id,
                workspace_root=normalized_workspace_root or None,
                clear_workspace_override=not bool(normalized_workspace_root),
            )
            updated.thread_data = thread_data
        updated.lifecycle.updated_at = utc_now()
        self.checkpointer.put_thread_state(updated)
        self.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
        return updated

    def request_thread_interrupt(
        self,
        thread_id: str,
        *,
        reason: str = "Interrupted by user",
    ) -> ThreadState:
        state = self.get_thread_state(thread_id)
        updated = state.model_copy(deep=True)
        now = utc_now()
        updated.lifecycle.status = ThreadLifecycleStatus.INTERRUPTED
        updated.lifecycle.updated_at = now
        updated.lifecycle.completed_at = updated.lifecycle.completed_at or now
        updated.lifecycle.last_error = reason
        updated.execution.cancellation_requested = True
        updated.execution.last_message_interrupted = True
        updated.execution.last_message_interrupted_reason = reason
        updated.conversation.steps = _mark_steps_interrupted(updated.conversation.steps, reason=reason, completed_at=now)
        updated.execution.recent_tool_activity = _mark_tool_activity_interrupted(
            updated.execution.recent_tool_activity,
            reason=reason,
            completed_at=now,
        )
        self.checkpointer.put_thread_state(updated)
        self.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
        return updated

    def rewrite_latest_user_message(
        self,
        thread_id: str,
        *,
        message_id: str,
        content: str,
    ) -> ThreadState:
        state = self.get_thread_state(thread_id)
        updated = state.model_copy(deep=True)

        latest_user_index = None
        latest_user_resolved_id = None
        first_user_index = None
        for index, payload in enumerate(updated.conversation.messages):
            role = str(payload.get("role", ""))
            if role not in {"human", "user"}:
                continue
            resolved_id = str(payload.get("id")) if payload.get("id") is not None else f"message-{index}"
            if first_user_index is None:
                first_user_index = index
            latest_user_index = index
            latest_user_resolved_id = resolved_id

        if latest_user_index is None or latest_user_resolved_id != message_id:
            raise ValueError("latest_user_message_only")

        updated.conversation.messages = list(updated.conversation.messages[: latest_user_index + 1])
        updated.conversation.messages[latest_user_index]["content"] = content
        updated.lifecycle.status = ThreadLifecycleStatus.READY
        updated.lifecycle.updated_at = utc_now()
        updated.conversation.last_message_at = updated.lifecycle.updated_at
        updated.lifecycle.completed_at = None
        updated.lifecycle.last_error = None
        updated.execution.token_usage = {}
        updated.execution.context_window_usage = {}
        updated.execution.recent_tool_activity = []
        updated.execution.last_message_interrupted = False
        updated.execution.last_message_interrupted_reason = None
        updated.approvals.pending_approval = None
        updated.approvals.approval_request = None
        updated.approvals.recent_approval_events = []
        updated.delegation.active_subagent_tasks = []
        updated.planning.todo_snapshot = []
        updated.archived_summaries = []
        updated.prompt_snapshot.snapshot_id = None
        updated.prompt_snapshot.snapshot_hash = None
        updated.prompt_snapshot.created_at = None
        updated.prompt_snapshot.project_context_fingerprint = None
        updated.prompt_snapshot.project_context_files = []
        updated.durable_subagent_job_history = []
        updated.execution.sandbox_state = None
        if first_user_index == latest_user_index:
            updated.conversation.title = None
        updated.conversation.summary = None
        self.checkpointer.put_thread_state(updated)
        self.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
        return updated

    def cancel_pending_approval(
        self,
        thread_id: str,
        *,
        reason: str = "Approval cancelled by user",
    ) -> ThreadState:
        state = self.get_thread_state(thread_id)
        if state.approvals.pending_approval is None:
            raise ValueError(f"thread '{thread_id}' has no pending approval")

        updated = state.model_copy(deep=True)
        updated.lifecycle.status = ThreadLifecycleStatus.CANCELLED
        updated.lifecycle.updated_at = utc_now()
        updated.lifecycle.completed_at = utc_now()
        updated.lifecycle.last_error = reason
        updated.approvals.recent_approval_events = self._record_cancelled_approval(
            updated.approvals.recent_approval_events,
            reason=reason,
            execution_mode=updated.execution.execution_mode,
        )
        updated.approvals.pending_approval = None
        updated.approvals.approval_request = None
        self.checkpointer.put_thread_state(updated)
        self.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
        return updated

    def build_artifact_refs(self, thread_id: str) -> dict[str, list]:
        state = self.get_thread_state(thread_id)
        uploads = []
        for payload in state.artifacts.uploaded_files:
            if isinstance(payload, dict) and payload.get("artifact_url") and payload.get("virtual_path"):
                uploads.append(dict(payload))
        outputs = [
            self.path_service.to_artifact_descriptor(thread_id, ArtifactKind.OUTPUTS, relative_path)
            for relative_path in state.artifacts.output_artifacts
        ]
        presented = list(state.artifacts.presented_artifacts)
        return {
            "uploads": uploads,
            "outputs": outputs,
            "presented": presented,
        }

    def build_execution_policy_projection(self, state: ThreadState) -> dict[str, object]:
        mode = state.execution.execution_mode
        if mode is ThreadExecutionMode.CHAT:
            summary = "Chat mode disables tool execution and keeps the session conversational."
            allowed = ["conversation"]
            requires_approval = []
            restricted = ["tool_execution", "filesystem_mutation", "delegated_runtime_actions"]
        elif mode is ThreadExecutionMode.FULL_ACCESS:
            summary = "Full access mode runs tools directly without approval prompts while audit logs and hard guardrail blocks still apply."
            allowed = ["conversation", "filesystem_tools", "guarded_tool_calls", "promoted_deferred_capabilities"]
            requires_approval = []
            restricted = []
        else:
            summary = (
                "Agent mode allows runtime tool execution. Read-only filesystem actions like list_dir, "
                "read_file, and extract_document run without approval; writes, shell execution, and "
                "external or otherwise guarded actions still require explicit approval."
            )
            allowed = ["conversation", "filesystem_tools"]
            requires_approval = ["guarded_tool_calls", "network_or_external_capabilities"]
            restricted = ["unguarded_full_access_shortcuts"]

        return {
            "approval_policy_summary": summary,
            "allowed_local_actions": allowed,
            "requires_approval_actions": requires_approval,
            "restricted_actions": restricted,
            "pending_approval_reason": state.lifecycle.last_error if state.approvals.pending_approval is not None else None,
        }

    def _delete_thread_storage(self, thread_id: str) -> None:
        base_root = self.path_service.base_root.resolve()
        thread_root = self.path_service.thread_storage_dir(thread_id).resolve()
        try:
            thread_root.relative_to(base_root)
        except ValueError as exc:
            raise ValueError(f"thread '{thread_id}' resolves outside the thread storage root") from exc
        if thread_root.exists():
            shutil.rmtree(thread_root)

    def _record_cancelled_approval(
        self,
        existing: list[RecentApprovalEvent],
        *,
        reason: str,
        execution_mode: ThreadExecutionMode,
    ) -> list[RecentApprovalEvent]:
        updated: list[RecentApprovalEvent] = []
        resolved_one = False
        for item in existing:
            if not resolved_one and item.status == "requested":
                resolved_one = True
                updated.append(
                    item.model_copy(
                        update={
                            "decision": "cancelled",
                            "reason": reason,
                            "status": "resolved",
                            "execution_mode": execution_mode,
                            "resolved_at": utc_now(),
                        }
                    )
                )
            else:
                updated.append(item)
        if resolved_one:
            return updated[:20]
        return [
            RecentApprovalEvent(
                decision="cancelled",
                reason=reason,
                status="resolved",
                execution_mode=execution_mode,
                resolved_at=utc_now(),
            ),
            *updated,
        ][:20]
