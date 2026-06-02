from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from dataclasses import dataclass, field
import json
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from anvil.agents import RecentApprovalEvent, RecentToolActivity, RunToolCallRecord, ThreadExecutionMode, ThreadLifecycleStatus
from anvil.agents.user_interaction import build_user_interaction_request
from anvil.runtime.approvals import ApprovalDecision, ApprovalRequest


_RAW_SUBAGENT_EVENT_KINDS = {
    "subagent_submitted",
    "subagent_started",
    "subagent_tool_call",
    "subagent_tool_result",
    "subagent_model_response",
    "subagent_completed",
    "subagent_failed",
    "subagent_cancelled",
    "subagent_timed_out",
    "subagent_interrupted",
    "subagent_event",
}
_SUBAGENT_KIND_TO_EVENT_TYPE = {
    "subagent_submitted": "job_submitted",
    "subagent_started": "job_started",
    "subagent_tool_call": "tool_call",
    "subagent_tool_result": "tool_result",
    "subagent_model_response": "model_response",
    "subagent_completed": "job_completed",
    "subagent_failed": "job_failed",
    "subagent_cancelled": "job_cancelled",
    "subagent_timed_out": "job_timed_out",
    "subagent_interrupted": "job_interrupted",
}
_TERMINAL_SUBAGENT_EVENT_TYPES = {
    "job_completed",
    "job_failed",
    "job_cancelled",
    "job_timed_out",
    "job_interrupted",
}
_TERMINAL_SUBAGENT_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "interrupted",
    "failed_recovery",
}
_SUBAGENT_STATUS_BY_EVENT_TYPE = {
    "job_submitted": "queued",
    "job_started": "running",
    "tool_call": "running",
    "tool_result": "running",
    "model_response": "running",
    "job_completed": "completed",
    "job_failed": "failed",
    "job_cancelled": "cancelled",
    "job_timed_out": "timed_out",
    "job_interrupted": "interrupted",
}


@dataclass(frozen=True)
class RunEvent:
    event: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunEventEnvelope:
    event_id: str
    run_id: str
    thread_id: str
    message_id: str | None
    block_id: str | None
    sequence: int
    kind: str
    visibility: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_run_event(
        cls,
        event: RunEvent,
        *,
        run_id: str,
        thread_id: str,
        sequence: int,
    ) -> "RunEventEnvelope":
        payload = dict(event.data)
        step = payload.get("step")
        message_id = payload.get("message_id")
        block_id = payload.get("block_id") or payload.get("step_id")
        visibility = str(payload.get("visibility") or "chat")
        if isinstance(step, dict):
            message_id = message_id or step.get("message_id")
            block_id = block_id or step.get("step_id")
            visibility = str(payload.get("visibility") or step.get("visibility") or "chat")

        normalized_run_id = str(payload.get("run_id") or run_id)
        normalized_thread_id = str(payload.get("thread_id") or thread_id)
        normalized_sequence = int(sequence)
        event_id = str(payload.get("event_id") or f"{normalized_run_id}:{normalized_sequence:06d}")
        return cls(
            event_id=event_id,
            run_id=normalized_run_id,
            thread_id=normalized_thread_id,
            message_id=str(message_id) if message_id is not None else None,
            block_id=str(block_id) if block_id is not None else None,
            sequence=normalized_sequence,
            kind=event.event,
            visibility=visibility,
            payload=payload,
        )

    def to_run_event(self) -> RunEvent:
        data = dict(self.payload)
        data.update(
            {
                "event_id": self.event_id,
                "run_id": self.run_id,
                "thread_id": self.thread_id,
                "sequence": self.sequence,
                "visibility": self.visibility,
                "source": data.get("source") or "runtime",
            }
        )
        if self.message_id is not None:
            data.setdefault("message_id", self.message_id)
        if self.block_id is not None:
            data.setdefault("block_id", self.block_id)
        if self.kind in {"run_completed", "run_interrupted", "run_failed"}:
            data.setdefault("event_log_cursor", self.sequence)
        return RunEvent(event=self.kind, data=data)

    def header(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "message_id": self.message_id,
            "block_id": self.block_id,
            "sequence": self.sequence,
            "visibility": self.visibility,
            "created_at": self.created_at,
        }

    def to_record(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "message_id": self.message_id,
            "block_id": self.block_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "visibility": self.visibility,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "RunEventEnvelope":
        return cls(
            event_id=str(record["event_id"]),
            run_id=str(record["run_id"]),
            thread_id=str(record["thread_id"]),
            message_id=str(record["message_id"]) if record.get("message_id") is not None else None,
            block_id=str(record["block_id"]) if record.get("block_id") is not None else None,
            sequence=int(record["sequence"]),
            kind=str(record["kind"]),
            visibility=str(record.get("visibility") or "chat"),
            payload=dict(record.get("payload") or {}),
            created_at=str(record.get("created_at") or datetime.now(timezone.utc).isoformat()),
        )


class RunEventSink(Protocol):
    def emit(self, event: RunEvent) -> None: ...


class RunEventLogStore(Protocol):
    def append(self, envelope: RunEventEnvelope) -> RunEventEnvelope: ...

    def list_events(
        self,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        after_sequence: int | None = None,
    ) -> list[RunEventEnvelope]: ...

    def last_sequence(self, *, thread_id: str | None = None, run_id: str | None = None) -> int: ...


@dataclass(frozen=True)
class RunEventPage:
    events: list[RunEventEnvelope]
    next_cursor: int
    has_more: bool


def list_run_event_page(
    store: RunEventLogStore,
    *,
    thread_id: str,
    run_id: str | None = None,
    after_sequence: int | None = None,
    limit: int = 100,
) -> RunEventPage:
    if after_sequence is not None and run_id is None:
        raise ValueError("run_id_required_for_cursor")
    normalized_limit = min(max(int(limit), 1), 500)
    events = store.list_events(thread_id=thread_id, run_id=run_id, after_sequence=after_sequence)
    page_events = events[:normalized_limit]
    next_cursor = page_events[-1].sequence if page_events else int(after_sequence or 0)
    return RunEventPage(
        events=page_events,
        next_cursor=next_cursor,
        has_more=len(events) > len(page_events),
    )


@dataclass
class ListRunEventSink:
    events: list[RunEvent] = field(default_factory=list)

    def emit(self, event: RunEvent) -> None:
        self.events.append(event)


@dataclass
class InMemoryRunEventLogStore:
    events: list[RunEventEnvelope] = field(default_factory=list)

    def append(self, envelope: RunEventEnvelope) -> RunEventEnvelope:
        for existing in self.events:
            if _same_run_event_identity(existing, envelope) or _same_run_event_sequence(existing, envelope):
                return existing
        self.events.append(envelope)
        return envelope

    def list_events(
        self,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        after_sequence: int | None = None,
    ) -> list[RunEventEnvelope]:
        events: list[RunEventEnvelope] = []
        seen_event_keys: set[tuple[str, str, str]] = set()
        seen_run_sequences: set[tuple[str, str, int]] = set()
        for event in self.events:
            if thread_id is not None and event.thread_id != thread_id:
                continue
            if run_id is not None and event.run_id != run_id:
                continue
            event_key = _run_event_identity_key(event)
            if event_key in seen_event_keys:
                continue
            seen_event_keys.add(event_key)
            if run_id is not None:
                sequence_key = _run_event_sequence_key(event)
                if sequence_key in seen_run_sequences:
                    continue
                seen_run_sequences.add(sequence_key)
            if after_sequence is not None and event.sequence <= after_sequence:
                continue
            events.append(event)
        if run_id is not None:
            events.sort(key=lambda event: event.sequence)
        return events

    def last_sequence(self, *, thread_id: str | None = None, run_id: str | None = None) -> int:
        events = self.list_events(thread_id=thread_id, run_id=run_id)
        return max((event.sequence for event in events), default=0)


class JsonlRunEventLogStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def append(self, envelope: RunEventEnvelope) -> RunEventEnvelope:
        with self._lock:
            existing = self._find_event_locked(envelope)
            if existing is not None:
                return existing
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(envelope.to_record(), ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        return envelope

    def list_events(
        self,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        after_sequence: int | None = None,
    ) -> list[RunEventEnvelope]:
        if not self.path.exists():
            return []
        events: list[RunEventEnvelope] = []
        seen_event_keys: set[tuple[str, str, str]] = set()
        seen_run_sequences: set[tuple[str, str, int]] = set()
        with self._lock:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        envelope = RunEventEnvelope.from_record(json.loads(raw))
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if thread_id is not None and envelope.thread_id != thread_id:
                        continue
                    if run_id is not None and envelope.run_id != run_id:
                        continue
                    event_key = _run_event_identity_key(envelope)
                    if event_key in seen_event_keys:
                        continue
                    seen_event_keys.add(event_key)
                    if run_id is not None:
                        sequence_key = _run_event_sequence_key(envelope)
                        if sequence_key in seen_run_sequences:
                            continue
                        seen_run_sequences.add(sequence_key)
                    if after_sequence is not None and envelope.sequence <= after_sequence:
                        continue
                    events.append(envelope)
        if run_id is not None:
            events.sort(key=lambda event: event.sequence)
        return events

    def last_sequence(self, *, thread_id: str | None = None, run_id: str | None = None) -> int:
        events = self.list_events(thread_id=thread_id, run_id=run_id)
        return max((event.sequence for event in events), default=0)

    def _find_event_locked(self, target: RunEventEnvelope) -> RunEventEnvelope | None:
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    envelope = RunEventEnvelope.from_record(json.loads(raw))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if _same_run_event_identity(envelope, target) or _same_run_event_sequence(envelope, target):
                    return envelope
        return None


class RunSnapshotProjector:
    def project_thread(self, thread_state: Any, envelopes: list[RunEventEnvelope]) -> Any:
        projected = thread_state.model_copy(deep=True) if hasattr(thread_state, "model_copy") else thread_state
        if not envelopes:
            return projected
        runs: dict[str, list[RunEventEnvelope]] = {}
        run_order: list[str] = []
        for envelope in envelopes:
            if envelope.run_id not in runs:
                run_order.append(envelope.run_id)
                runs[envelope.run_id] = []
            runs[envelope.run_id].append(envelope)

        projected.conversation.messages = []
        projected.conversation.steps = []
        self._reset_thread_projection_fields(projected)
        for run_id in run_order:
            run_envelopes = sorted(runs[run_id], key=lambda envelope: envelope.sequence)
            if self._run_resets_thread_projection(run_envelopes):
                projected.conversation.messages = []
                projected.conversation.steps = []
                self._reset_thread_projection_fields(projected)
            user_message = self._user_message_from_run_events(run_envelopes)
            if user_message is not None:
                projected.conversation.messages.append(user_message)
            previous_steps = list(projected.conversation.steps)
            projected = self.project(projected, run_envelopes)
            projected.conversation.steps = self._merge_projected_steps(
                previous_steps,
                list(projected.conversation.steps),
            )

        last = envelopes[-1]
        runtime_phase_timings = dict(getattr(projected.execution, "runtime_phase_timings", {}) or {})
        runtime_phase_timings["event_log"] = {
            "event_count": len(envelopes),
            "run_count": len(run_order),
            "last_event_id": last.event_id,
            "last_sequence": last.sequence,
            "last_kind": last.kind,
            "last_run_id": last.run_id,
        }
        projected.execution.runtime_phase_timings = runtime_phase_timings
        return projected

    def _reset_thread_projection_fields(self, projected: Any) -> None:
        projected.execution.tool_calls = []
        projected.execution.recent_tool_activity = []
        projected.execution.last_message_interrupted = False
        projected.execution.last_message_interrupted_reason = None
        projected.artifacts.output_artifacts = []
        projected.artifacts.uploaded_files = []
        projected.artifacts.presented_artifacts = []
        projected.approvals.pending_approval = None
        projected.approvals.approval_request = None
        projected.approvals.recent_approval_events = []
        projected.delegation.active_subagent_tasks = []
        projected.durable_subagent_job_history = []

    def project(self, thread_state: Any, envelopes: list[RunEventEnvelope]) -> Any:
        projected = thread_state.model_copy(deep=True) if hasattr(thread_state, "model_copy") else thread_state
        if not envelopes:
            return projected
        ordered = sorted(envelopes, key=lambda envelope: envelope.sequence)
        last = ordered[-1]
        projected.identity.run_id = last.run_id
        projected.lifecycle.updated_at = _parse_event_datetime(last.created_at) or datetime.now(timezone.utc)
        self._project_run_metadata(projected, ordered)
        self._project_steps(projected, ordered)
        self._project_tool_calls(projected, ordered)
        self._project_recent_tool_activity(projected)
        self._project_artifacts(projected, ordered)
        self._project_approvals(projected, ordered)
        self._project_user_interaction(projected, ordered)
        self._project_subagents(projected, ordered)
        self._project_terminal_state(projected, ordered)
        runtime_phase_timings = dict(getattr(projected.execution, "runtime_phase_timings", {}) or {})
        runtime_phase_timings["event_log"] = {
            "event_count": len(ordered),
            "last_event_id": last.event_id,
            "last_sequence": last.sequence,
            "last_kind": last.kind,
        }
        projected.execution.runtime_phase_timings = runtime_phase_timings
        return projected

    def _merge_projected_steps(
        self,
        existing_steps: list[dict[str, Any]],
        new_steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        order: list[tuple[str, str]] = []
        for step in [*existing_steps, *new_steps]:
            step_key = self._projected_step_identity_key(step)
            if step_key is None:
                continue
            if step_key not in merged:
                order.append(step_key)
            merged[step_key] = dict(step)
        return [merged[step_key] for step_key in order]

    def _projected_step_identity_key(self, step: dict[str, Any]) -> tuple[str, str] | None:
        step_id = _optional_string(step.get("step_id"))
        if not step_id:
            return None
        message_id = _optional_string(step.get("message_id"))
        return message_id or "", step_id

    def _user_message_from_run_events(self, envelopes: list[RunEventEnvelope]) -> dict[str, Any] | None:
        for envelope in envelopes:
            if envelope.kind != "run_started":
                continue
            user_message = envelope.payload.get("user_message")
            if isinstance(user_message, dict):
                role = str(user_message.get("role") or "")
                if role in {"human", "user"}:
                    return dict(user_message)
            message = _optional_string(envelope.payload.get("message"))
            if message:
                return {"role": "human", "content": message}
        return None

    def _run_resets_thread_projection(self, envelopes: list[RunEventEnvelope]) -> bool:
        return any(
            envelope.kind == "run_started"
            and bool(envelope.payload.get("transcript_rewrite_boundary"))
            for envelope in envelopes
        )

    def _project_run_metadata(self, projected: Any, envelopes: list[RunEventEnvelope]) -> None:
        execution_mode = None
        for envelope in envelopes:
            raw_mode = envelope.payload.get("execution_mode")
            if raw_mode is not None:
                execution_mode = str(raw_mode)
        if execution_mode is None:
            return
        try:
            projected.execution.execution_mode = ThreadExecutionMode(execution_mode)
        except ValueError:
            return

    def _project_steps(self, projected: Any, envelopes: list[RunEventEnvelope]) -> None:
        steps_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        step_order: list[tuple[str, str]] = []
        message_ids: list[str] = []
        message_status: dict[str, str] = {}

        for envelope in envelopes:
            payload = envelope.payload
            message_id = _optional_string(payload.get("message_id") or envelope.message_id)
            if message_id and message_id not in message_ids:
                message_ids.append(message_id)

            if envelope.kind in {"step_started", "step_updated"}:
                raw_step = payload.get("step")
                if not isinstance(raw_step, dict):
                    continue
                step = dict(raw_step)
                step_id = str(step.get("step_id") or envelope.block_id or "")
                if not step_id:
                    continue
                step_message_id = _optional_string(step.get("message_id") or message_id)
                if step_message_id:
                    step["message_id"] = step_message_id
                    if step_message_id not in message_ids:
                        message_ids.append(step_message_id)
                step_key = self._project_step_identity_key(step_id=step_id, message_id=step_message_id)
                if step_key not in steps_by_key:
                    step_order.append(step_key)
                previous_payload = steps_by_key.get(step_key, {}).get("payload")
                if envelope.kind == "step_updated" and previous_payload and not step.get("payload"):
                    step["payload"] = previous_payload
                steps_by_key[step_key] = step
                continue

            if envelope.kind == "step_delta":
                step_id = str(payload.get("step_id") or envelope.block_id or "")
                if not step_id:
                    continue
                step_key = self._project_step_identity_key(step_id=step_id, message_id=message_id)
                if step_key not in steps_by_key:
                    step_order.append(step_key)
                    steps_by_key[step_key] = {
                        "step_id": step_id,
                        "message_id": message_id,
                        "type": "content",
                        "title": "Streaming",
                        "status": "running",
                        "payload": "",
                        "visibility": envelope.visibility,
                    }
                step = dict(steps_by_key[step_key])
                step["payload"] = f"{step.get('payload') or ''}{payload.get('payload_delta') or ''}"
                steps_by_key[step_key] = step
                continue

            if envelope.kind == "message_completed" and message_id:
                message_status[message_id] = str(payload.get("stream_status") or "complete")

        projected.conversation.steps = [steps_by_key[step_key] for step_key in step_order if step_key in steps_by_key]
        projected.conversation.messages = self._project_assistant_messages(
            projected.conversation.messages,
            message_ids=message_ids,
            steps=projected.conversation.steps,
            message_status=message_status,
        )

    def _project_step_identity_key(self, *, step_id: str, message_id: str | None) -> tuple[str, str]:
        return message_id or "", step_id

    def _project_tool_calls(self, projected: Any, envelopes: list[RunEventEnvelope]) -> None:
        projected_records = [
            record
            for envelope in envelopes
            if (record := self._tool_call_record_from_envelope(envelope)) is not None
        ]
        if not projected_records:
            return

        merged: dict[str, RunToolCallRecord] = {}
        order: list[str] = []
        for record in projected_records:
            key = self._tool_call_record_key(record)
            if key not in merged:
                order.append(key)
            merged[key] = self._merge_tool_call_records(merged.get(key), record)
        for existing in getattr(projected.execution, "tool_calls", []) or []:
            if not isinstance(existing, RunToolCallRecord):
                try:
                    existing = RunToolCallRecord.model_validate(existing)
                except Exception:  # noqa: BLE001
                    continue
            key = self._tool_call_record_key(existing)
            if key in merged:
                continue
            order.append(key)
            merged[key] = existing
        projected.execution.tool_calls = [merged[key] for key in order][:200]

    def _tool_call_record_from_envelope(self, envelope: RunEventEnvelope) -> RunToolCallRecord | None:
        if envelope.kind not in {"step_started", "step_updated"}:
            return None
        raw_step = envelope.payload.get("step")
        if not isinstance(raw_step, dict):
            return None
        step = dict(raw_step)
        if str(step.get("type") or "") != "call":
            return None
        name = _optional_string(step.get("tool_name"))
        tool_call_id = _optional_string(step.get("tool_call_id"))
        if name is None and tool_call_id is None:
            return None
        metadata = step.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        status = str(step.get("status") or "")
        error_message = _optional_string(step.get("error"))
        if error_message is None and self._tool_activity_status(status) == "error":
            error_message = _optional_string(step.get("payload"))
        return RunToolCallRecord(
            run_id=envelope.run_id,
            thread_id=envelope.thread_id,
            message_id=_optional_string(step.get("message_id") or envelope.message_id),
            block_id=_optional_string(step.get("block_id") or envelope.block_id or step.get("step_id")),
            sequence=envelope.sequence,
            tool_call_id=tool_call_id,
            name=name,
            display_name=_optional_string(metadata.get("display_name")),
            source_kind=_optional_string(metadata.get("source_kind")),
            source_id=_optional_string(metadata.get("source_id")),
            capability_group=_optional_string(metadata.get("capability_group")),
            tool_execution_mode=_optional_string(metadata.get("tool_execution_mode")),
            input=self._tool_args_from_step_action(step.get("action")),
            output=_optional_string(step.get("payload")) if step.get("payload") not in (None, "") else None,
            status=self._tool_activity_status(status),
            is_error=self._tool_activity_status(status) == "error",
            error_message=error_message,
            started_at=_parse_event_datetime(step.get("started_at")),
            completed_at=_parse_event_datetime(step.get("completed_at")),
            duration_ms=int(step["duration_ms"]) if isinstance(step.get("duration_ms"), int) else None,
            visibility=str(step.get("visibility") or envelope.visibility or "chat"),
        )

    def _merge_tool_call_records(
        self,
        previous: RunToolCallRecord | None,
        current: RunToolCallRecord,
    ) -> RunToolCallRecord:
        if previous is None:
            return current
        return previous.model_copy(
            update={
                "run_id": current.run_id or previous.run_id,
                "thread_id": current.thread_id or previous.thread_id,
                "message_id": current.message_id or previous.message_id,
                "block_id": current.block_id or previous.block_id,
                "sequence": current.sequence if current.sequence is not None else previous.sequence,
                "tool_call_id": current.tool_call_id or previous.tool_call_id,
                "name": current.name or previous.name,
                "display_name": current.display_name or previous.display_name,
                "source_kind": current.source_kind or previous.source_kind,
                "source_id": current.source_id or previous.source_id,
                "capability_group": current.capability_group or previous.capability_group,
                "tool_execution_mode": current.tool_execution_mode or previous.tool_execution_mode,
                "input": current.input or previous.input,
                "output": current.output if current.output is not None else previous.output,
                "status": current.status or previous.status,
                "is_error": bool(current.is_error or previous.is_error),
                "error_message": current.error_message or previous.error_message,
                "started_at": current.started_at or previous.started_at,
                "completed_at": current.completed_at or previous.completed_at,
                "duration_ms": current.duration_ms if current.duration_ms is not None else previous.duration_ms,
                "visibility": current.visibility or previous.visibility,
            }
        )

    def _tool_call_record_key(self, record: RunToolCallRecord) -> str:
        if record.tool_call_id:
            return record.tool_call_id
        if record.block_id:
            return record.block_id
        if record.message_id or record.name:
            return f"{record.message_id or 'message'}:{record.name or 'tool'}"
        return "unknown-tool"

    def _project_recent_tool_activity(self, projected: Any) -> None:
        projected_activities = [
            activity
            for step in projected.conversation.steps
            if (activity := self._tool_activity_from_step(step)) is not None
        ]
        if not projected_activities:
            return
        merged: dict[str, RecentToolActivity] = {}
        order: list[str] = []
        for activity in projected_activities:
            key = self._tool_activity_key(activity)
            if key not in merged:
                order.append(key)
            merged[key] = activity
        for existing in getattr(projected.execution, "recent_tool_activity", []) or []:
            key = self._tool_activity_key(existing)
            if key in merged:
                continue
            order.append(key)
            merged[key] = existing
        projected.execution.recent_tool_activity = [merged[key] for key in order][:20]

    def _tool_activity_from_step(self, step: dict[str, Any]) -> RecentToolActivity | None:
        if str(step.get("type") or "") != "call":
            return None
        name = _optional_string(step.get("tool_name"))
        tool_call_id = _optional_string(step.get("tool_call_id"))
        if name is None and tool_call_id is None:
            return None
        status = str(step.get("status") or "")
        metadata = step.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        return RecentToolActivity(
            tool_call_id=tool_call_id,
            message_id=_optional_string(step.get("message_id")),
            name=name,
            display_name=_optional_string(metadata.get("display_name")),
            source_kind=_optional_string(metadata.get("source_kind")),
            source_id=_optional_string(metadata.get("source_id")),
            capability_group=_optional_string(metadata.get("capability_group")),
            tool_execution_mode=_optional_string(metadata.get("tool_execution_mode")),
            args=self._tool_args_from_step_action(step.get("action")),
            status=self._tool_activity_status(status),
            result_text=_optional_string(step.get("payload")) if step.get("payload") not in (None, "") else None,
            started_at=_parse_event_datetime(step.get("started_at")),
            completed_at=_parse_event_datetime(step.get("completed_at")),
            duration_ms=int(step["duration_ms"]) if isinstance(step.get("duration_ms"), int) else None,
        )

    def _tool_args_from_step_action(self, action: object) -> dict[str, Any]:
        if not isinstance(action, str) or not action.strip():
            return {}
        try:
            parsed = json.loads(action)
        except json.JSONDecodeError:
            return {"command": action} if "\n" not in action else {"input": action}
        return parsed if isinstance(parsed, dict) else {}

    def _tool_activity_status(self, status: str) -> str:
        normalized = status.lower()
        if normalized == "success":
            return "completed"
        if normalized == "error":
            return "error"
        if normalized == "pending":
            return "pending"
        return "running"

    def _tool_activity_key(self, activity: RecentToolActivity) -> str:
        if activity.tool_call_id:
            return activity.tool_call_id
        if activity.message_id or activity.name:
            return f"{activity.message_id or 'message'}:{activity.name or 'tool'}"
        return "unknown-tool"

    def _project_artifacts(self, projected: Any, envelopes: list[RunEventEnvelope]) -> None:
        output_paths: list[str] = []
        uploaded_files: list[dict[str, Any]] = []
        presented_paths: list[str] = []

        for envelope in envelopes:
            if envelope.kind not in {"artifact_registered", "artifact_emitted"}:
                continue
            payload = envelope.payload
            kind = str(payload.get("kind") or "")
            if kind == "output":
                relative_path = self._output_artifact_path(payload)
                if relative_path is not None:
                    output_paths.append(relative_path)
                continue
            if kind == "upload":
                uploaded_file = self._upload_artifact_file(payload)
                if uploaded_file is not None:
                    uploaded_files.append(uploaded_file)
                continue
            if kind == "presented":
                presented_path = _optional_string(payload.get("virtual_path") or payload.get("label"))
                if presented_path is not None:
                    presented_paths.append(presented_path)

        if output_paths:
            projected.artifacts.output_artifacts = _merge_unique_strings(
                output_paths,
                getattr(projected.artifacts, "output_artifacts", []),
            )
        if uploaded_files:
            projected.artifacts.uploaded_files = _merge_unique_uploads(
                uploaded_files,
                getattr(projected.artifacts, "uploaded_files", []),
            )
        if presented_paths:
            projected.artifacts.presented_artifacts = _merge_unique_strings(
                presented_paths,
                getattr(projected.artifacts, "presented_artifacts", []),
            )

    def _output_artifact_path(self, payload: dict[str, Any]) -> str | None:
        label = _optional_string(payload.get("label"))
        if label:
            return label
        virtual_path = _optional_string(payload.get("virtual_path"))
        prefix = "/mnt/user-data/outputs/"
        if virtual_path and virtual_path.startswith(prefix):
            return virtual_path[len(prefix) :]
        artifact_url = _optional_string(payload.get("artifact_url"))
        marker = "/artifacts/outputs/"
        if artifact_url and marker in artifact_url:
            return artifact_url.split(marker, 1)[1]
        return None

    def _upload_artifact_file(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        filename = _optional_string(payload.get("label"))
        virtual_path = _optional_string(payload.get("virtual_path"))
        artifact_url = _optional_string(payload.get("artifact_url"))
        if not filename and virtual_path:
            filename = virtual_path.rsplit("/", 1)[-1]
        if not filename and artifact_url:
            filename = artifact_url.rsplit("/", 1)[-1]
        if not filename:
            return None

        uploaded: dict[str, Any] = {"filename": filename}
        if artifact_url:
            uploaded["artifact_url"] = artifact_url
        if virtual_path:
            uploaded["virtual_path"] = virtual_path
        return uploaded

    def _project_approvals(self, projected: Any, envelopes: list[RunEventEnvelope]) -> None:
        approval_events: list[RecentApprovalEvent] = []
        latest_request: ApprovalRequest | None = None
        latest_decision: ApprovalDecision | None = None

        for envelope in envelopes:
            payload = envelope.payload
            if envelope.kind == "approval_requested":
                request = self._approval_request_from_event(envelope, payload)
                if request is None:
                    continue
                decision = self._approval_decision(payload.get("decision"))
                approval_events.append(
                    RecentApprovalEvent(
                        request_id=request.request_id,
                        decision=decision.value,
                        reason=request.reason,
                        action_kind=request.action_kind,
                        requested_permissions=list(request.requested_permissions),
                        scope_options=list(request.scope_options),
                        status="requested",
                        execution_mode=self._execution_mode_from_payload(payload),
                        created_at=_parse_event_datetime(envelope.created_at) or datetime.now(timezone.utc),
                    )
                )
                latest_request = request
                latest_decision = decision
                continue
            if envelope.kind == "approval_resolved":
                resolved = self._approval_resolved_event(envelope, payload)
                if resolved is not None:
                    approval_events.append(resolved)
                    latest_request = None
                    latest_decision = None

        if not approval_events:
            return

        projected.approvals.recent_approval_events = _merge_unique_approval_events(
            approval_events,
            getattr(projected.approvals, "recent_approval_events", []),
        )[:20]
        projected.approvals.pending_approval = latest_decision
        projected.approvals.approval_request = latest_request
        if latest_request is not None:
            projected.lifecycle.status = ThreadLifecycleStatus.AWAITING_APPROVAL
            projected.lifecycle.completed_at = None
            projected.lifecycle.last_error = latest_request.reason
            return

        projected.approvals.pending_approval = None
        projected.approvals.approval_request = None

    def _project_user_interaction(self, projected: Any, envelopes: list[RunEventEnvelope]) -> None:
        latest = self._pending_user_interaction_from_events(envelopes)
        if latest is None:
            return
        projected.conversation.pending_user_interaction = latest["interaction"]
        projected.lifecycle.status = ThreadLifecycleStatus.AWAITING_CLARIFICATION
        projected.lifecycle.completed_at = None
        projected.lifecycle.last_error = latest["prompt"]

    def _pending_user_interaction_from_events(self, envelopes: list[RunEventEnvelope]) -> dict[str, Any] | None:
        for envelope in reversed(envelopes):
            if envelope.kind not in {"step_started", "step_updated"}:
                continue
            raw_step = envelope.payload.get("step")
            if not isinstance(raw_step, dict):
                continue
            step = dict(raw_step)
            if str(step.get("type") or "") != "call":
                continue
            if _optional_string(step.get("tool_name")) != "ask_clarification":
                continue
            args = self._tool_args_from_step_action(step.get("action"))
            interaction = build_user_interaction_request(
                args,
                request_id=_optional_string(step.get("tool_call_id")),
            )
            prompt = interaction.question or _optional_string(step.get("payload")) or "More information is required before the runtime can continue."
            return {"interaction": interaction.model_dump(mode="json"), "prompt": prompt}
        return None

    def _approval_request_from_event(self, envelope: RunEventEnvelope, payload: dict[str, Any]) -> ApprovalRequest | None:
        request_id = _optional_string(payload.get("request_id"))
        reason = _optional_string(payload.get("reason"))
        action_kind = _optional_string(payload.get("action_kind"))
        if request_id is None or reason is None or action_kind is None:
            return None
        requested_permissions = payload.get("requested_permissions")
        scope_options = payload.get("scope_options")
        return ApprovalRequest(
            request_id=request_id,
            thread_id=envelope.thread_id,
            turn_id=envelope.run_id,
            reason=reason,
            action_kind=action_kind,
            requested_permissions=_string_list(requested_permissions),
            scope_options=tuple(_string_list(scope_options)),
            tool_name=_optional_string(payload.get("tool_name")),
            approval_profile=_optional_string(payload.get("approval_profile")),
            risk_category=_optional_string(payload.get("risk_category")),
            capability_group=_optional_string(payload.get("capability_group")),
        )

    def _approval_resolved_event(self, envelope: RunEventEnvelope, payload: dict[str, Any]) -> RecentApprovalEvent | None:
        request_id = _optional_string(payload.get("request_id"))
        if request_id is None:
            return None
        decision = _optional_string(payload.get("decision")) or "approved"
        return RecentApprovalEvent(
            request_id=request_id,
            decision=decision,
            reason=_optional_string(payload.get("reason")),
            action_kind=_optional_string(payload.get("action_kind")),
            requested_permissions=_string_list(payload.get("requested_permissions")),
            scope_options=_string_list(payload.get("scope_options")),
            status="resolved",
            execution_mode=self._execution_mode_from_payload(payload),
            created_at=_parse_event_datetime(envelope.created_at) or datetime.now(timezone.utc),
            resolved_at=_parse_event_datetime(envelope.created_at) or datetime.now(timezone.utc),
        )

    def _approval_decision(self, value: object) -> ApprovalDecision:
        raw = _optional_string(value) or ApprovalDecision.NEEDS_USER_APPROVAL.value
        try:
            return ApprovalDecision(raw)
        except ValueError:
            return ApprovalDecision.NEEDS_USER_APPROVAL

    def _execution_mode_from_payload(self, payload: dict[str, Any]) -> ThreadExecutionMode | None:
        raw_mode = _optional_string(payload.get("execution_mode"))
        if raw_mode is None:
            return None
        try:
            return ThreadExecutionMode(raw_mode)
        except ValueError:
            return None

    def _project_subagents(self, projected: Any, envelopes: list[RunEventEnvelope]) -> None:
        raw_entries: list[dict[str, Any]] = []
        raw_task_event_types: set[tuple[str, str]] = set()
        for envelope in envelopes:
            entry = self._subagent_history_from_raw_event(envelope)
            if entry is None:
                continue
            raw_entries.append(entry)
            raw_task_event_types.add((str(entry["job_id"]), str(entry["event_type"])))

        step_entries_by_task_event: dict[tuple[str, str], dict[str, Any]] = {}
        for envelope in envelopes:
            entry = self._subagent_history_from_step_event(envelope)
            if entry is None:
                continue
            task_event = (str(entry["job_id"]), str(entry["event_type"]))
            if task_event in raw_task_event_types:
                continue
            previous = step_entries_by_task_event.get(task_event)
            if previous is None or self._subagent_entry_sort_key(entry) >= self._subagent_entry_sort_key(previous):
                step_entries_by_task_event[task_event] = entry

        projected_entries = _merge_unique_subagent_history(
            [*raw_entries, *step_entries_by_task_event.values()],
            [],
        )
        if not projected_entries:
            return

        projected.durable_subagent_job_history = _merge_unique_subagent_history(
            projected_entries,
            getattr(projected, "durable_subagent_job_history", []),
        )

        latest_by_task: dict[str, dict[str, Any]] = {}
        task_order: list[str] = []
        for entry in projected_entries:
            task_id = str(entry.get("job_id") or "")
            if not task_id:
                continue
            if task_id not in latest_by_task:
                task_order.append(task_id)
            previous = latest_by_task.get(task_id)
            if previous is None or self._subagent_entry_sort_key(entry) >= self._subagent_entry_sort_key(previous):
                latest_by_task[task_id] = entry

        active_tasks: list[dict[str, Any]] = []
        for task_id in task_order:
            entry = latest_by_task.get(task_id)
            if entry is None or self._subagent_entry_is_terminal(entry):
                continue
            active_tasks.append(self._active_subagent_task_from_entry(entry))

        touched_task_ids = set(latest_by_task)
        for existing in getattr(projected.delegation, "active_subagent_tasks", []) or []:
            if not isinstance(existing, dict):
                continue
            task_id = self._subagent_task_id_from_mapping(existing)
            if task_id is not None and task_id in touched_task_ids:
                continue
            active_tasks.append(dict(existing))
        projected.delegation.active_subagent_tasks = active_tasks

    def _subagent_history_from_raw_event(self, envelope: RunEventEnvelope) -> dict[str, Any] | None:
        if envelope.kind not in _RAW_SUBAGENT_EVENT_KINDS:
            return None
        payload = envelope.payload
        job_id = _optional_string(payload.get("subagent_job_id") or payload.get("task_id") or payload.get("job_id"))
        if job_id is None:
            return None
        event_type = _optional_string(payload.get("event_type")) or _SUBAGENT_KIND_TO_EVENT_TYPE.get(envelope.kind)
        if event_type is None:
            return None
        timestamp = _optional_string(payload.get("timestamp")) or envelope.created_at
        event_payload = self._sanitize_subagent_payload(payload, job_id=job_id, event_type=event_type)
        return {
            "job_id": job_id,
            "parent_thread_id": _optional_string(payload.get("parent_thread_id")) or envelope.thread_id,
            "parent_run_id": _optional_string(payload.get("parent_run_id")) or envelope.run_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "payload": event_payload,
        }

    def _subagent_history_from_step_event(self, envelope: RunEventEnvelope) -> dict[str, Any] | None:
        if envelope.kind not in {"step_started", "step_updated"}:
            return None
        raw_step = envelope.payload.get("step")
        if not isinstance(raw_step, dict):
            return None
        step = dict(raw_step)
        metadata = step.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if str(step.get("tool_name") or "") != "subagent" and "subagent_task_id" not in metadata:
            return None
        job_id = _optional_string(metadata.get("subagent_task_id") or step.get("tool_call_id"))
        if job_id is None:
            return None
        event_type = _optional_string(metadata.get("event_type")) or self._subagent_event_type_from_step_status(step)
        if (
            envelope.kind == "step_started"
            and event_type in _TERMINAL_SUBAGENT_EVENT_TYPES
            and not _optional_string(step.get("payload"))
            and not _optional_string(step.get("error"))
        ):
            return None
        timestamp = (
            _optional_string(step.get("completed_at"))
            or _optional_string(step.get("started_at"))
            or envelope.created_at
        )
        status = self._subagent_status_from_step(step, event_type=event_type)
        event_payload: dict[str, Any] = {
            "task_id": job_id,
            "subagent_job_id": job_id,
            "status": status,
        }
        self._copy_string(event_payload, metadata, "batch_id")
        self._copy_string(event_payload, metadata, "child_thread_id")
        self._copy_string(event_payload, metadata, "child_run_id")
        self._copy_string(event_payload, metadata, "prompt_preview")
        self._copy_string(event_payload, step, "started_at")
        self._copy_string(event_payload, step, "completed_at")
        duration_ms = step.get("duration_ms")
        if isinstance(duration_ms, int):
            event_payload["duration_ms"] = duration_ms
        payload_text = _optional_string(step.get("payload"))
        error_text = _optional_string(step.get("error"))
        if event_type == "job_completed" and payload_text:
            event_payload["summary"] = payload_text
        elif event_type in _TERMINAL_SUBAGENT_EVENT_TYPES and event_type != "job_completed":
            event_payload["error"] = error_text or payload_text
        return {
            "job_id": job_id,
            "parent_thread_id": envelope.thread_id,
            "parent_run_id": envelope.run_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "payload": event_payload,
        }

    def _sanitize_subagent_payload(
        self,
        payload: dict[str, Any],
        *,
        job_id: str,
        event_type: str,
    ) -> dict[str, Any]:
        sanitized: dict[str, Any] = {
            "task_id": _optional_string(payload.get("task_id")) or job_id,
            "subagent_job_id": job_id,
            "status": self._subagent_payload_status(payload, event_type=event_type),
        }
        for key in (
            "batch_id",
            "prompt_preview",
            "child_thread_id",
            "child_run_id",
            "summary",
            "error",
            "tool_name",
            "display_name",
            "started_at",
            "completed_at",
            "assigned_profile",
            "workspace_mode",
        ):
            self._copy_string(sanitized, payload, key)
        if "tool_name" not in sanitized:
            self._copy_string(sanitized, payload, "name", target_key="tool_name")
        for key in ("requested_tool_names", "allowed_tool_names", "depends_on_task_ids"):
            values = _string_list(payload.get(key))
            if values:
                sanitized[key] = values
        for key in ("delegation_depth", "duration_ms"):
            value = payload.get(key)
            if isinstance(value, int):
                sanitized[key] = value
        if isinstance(payload.get("cancel_requested"), bool):
            sanitized["cancel_requested"] = payload["cancel_requested"]
        return sanitized

    def _copy_string(
        self,
        target: dict[str, Any],
        source: dict[str, Any],
        key: str,
        *,
        target_key: str | None = None,
    ) -> None:
        value = _optional_string(source.get(key))
        if value is not None:
            target[target_key or key] = value

    def _subagent_payload_status(self, payload: dict[str, Any], *, event_type: str) -> str:
        raw_status = _optional_string(payload.get("status"))
        if raw_status:
            return raw_status
        return _SUBAGENT_STATUS_BY_EVENT_TYPE.get(event_type, "running")

    def _subagent_event_type_from_step_status(self, step: dict[str, Any]) -> str:
        status = str(step.get("status") or "")
        if status == "success":
            return "job_completed"
        if status == "error":
            return "job_failed"
        return "job_started"

    def _subagent_status_from_step(self, step: dict[str, Any], *, event_type: str) -> str:
        if event_type in _SUBAGENT_STATUS_BY_EVENT_TYPE:
            return _SUBAGENT_STATUS_BY_EVENT_TYPE[event_type]
        status = str(step.get("status") or "")
        if status == "success":
            return "completed"
        if status == "error":
            return "failed"
        return "running"

    def _active_subagent_task_from_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        payload = entry.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        event_type = str(entry.get("event_type") or "")
        task_id = str(entry.get("job_id") or "")
        task = {
            "task_id": task_id,
            "batch_id": payload.get("batch_id"),
            "parent_thread_id": entry.get("parent_thread_id"),
            "parent_run_id": entry.get("parent_run_id"),
            "child_thread_id": payload.get("child_thread_id"),
            "child_run_id": payload.get("child_run_id"),
            "status": self._active_subagent_status(event_type, payload),
            "prompt_preview": payload.get("prompt_preview"),
            "assigned_profile": payload.get("assigned_profile"),
            "delegation_depth": payload.get("delegation_depth", 0),
            "workspace_mode": payload.get("workspace_mode", "inherited_parent_workspace"),
            "cancel_requested": bool(payload.get("cancel_requested", False)),
            "requested_tool_names": _string_list(payload.get("requested_tool_names")),
            "allowed_tool_names": _string_list(payload.get("allowed_tool_names")),
            "depends_on_task_ids": _string_list(payload.get("depends_on_task_ids")),
            "started_at": payload.get("started_at"),
            "completed_at": None,
            "timeout_at": payload.get("timeout_at"),
            "error": payload.get("error"),
        }
        return {key: value for key, value in task.items() if value is not None}

    def _active_subagent_status(self, event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "job_submitted":
            status = str(payload.get("status") or "queued")
            return status if status in {"queued", "running"} else "queued"
        if event_type in _TERMINAL_SUBAGENT_EVENT_TYPES:
            return _SUBAGENT_STATUS_BY_EVENT_TYPE.get(event_type, str(payload.get("status") or "failed"))
        return "running"

    def _subagent_entry_is_terminal(self, entry: dict[str, Any]) -> bool:
        event_type = str(entry.get("event_type") or "")
        if event_type in _TERMINAL_SUBAGENT_EVENT_TYPES:
            return True
        payload = entry.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        return event_type not in _SUBAGENT_STATUS_BY_EVENT_TYPE and str(payload.get("status") or "") in _TERMINAL_SUBAGENT_STATUSES

    def _subagent_entry_sort_key(self, entry: dict[str, Any]) -> tuple[datetime, str]:
        timestamp = _parse_event_datetime(entry.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)
        return timestamp, str(entry.get("event_type") or "")

    def _subagent_task_id_from_mapping(self, value: dict[str, Any]) -> str | None:
        return _optional_string(value.get("task_id") or value.get("job_id") or value.get("subagent_job_id"))

    def _project_assistant_messages(
        self,
        existing_messages: list[dict[str, Any]],
        *,
        message_ids: list[str],
        steps: list[dict[str, Any]],
        message_status: dict[str, str],
    ) -> list[dict[str, Any]]:
        message_by_id = {
            message_id: self._assistant_message_from_steps(message_id, steps, message_status)
            for message_id in message_ids
        }
        projected: list[dict[str, Any]] = []
        replaced_ids: set[str] = set()
        for existing in existing_messages:
            existing_id = str(existing.get("id") or "")
            if existing_id in message_by_id:
                message = message_by_id[existing_id]
                metadata = existing.get("metadata")
                if isinstance(metadata, dict):
                    message["metadata"] = dict(metadata)
                projected.append(message)
                replaced_ids.add(existing_id)
                continue
            projected.append(dict(existing))
        known_ids = {
            str(message.get("id"))
            for message in projected
            if message.get("id") is not None
        }
        for message_id in message_ids:
            if message_id in replaced_ids or message_id in known_ids:
                continue
            projected.append(message_by_id[message_id])
            known_ids.add(message_id)
        return projected

    def _assistant_message_from_steps(
        self,
        message_id: str,
        steps: list[dict[str, Any]],
        message_status: dict[str, str],
    ) -> dict[str, Any]:
        content = self._message_content_from_steps(message_id, steps)
        message: dict[str, Any] = {"role": "ai", "content": content, "id": message_id}
        tool_calls = self._message_tool_calls_from_steps(message_id, steps)
        if tool_calls:
            message["tool_calls"] = tool_calls
        reasoning_duration_ms = self._reasoning_duration_from_steps(message_id, steps)
        if reasoning_duration_ms is not None:
            message["reasoning_duration_ms"] = reasoning_duration_ms
        if message_status.get(message_id) == "interrupted":
            message["status"] = "interrupted"
        return message

    def _message_content_from_steps(self, message_id: str, steps: list[dict[str, Any]]) -> str:
        content_steps = [
            step
            for step in steps
            if str(step.get("message_id") or "") == message_id
            and str(step.get("type") or "") == "content"
            and str(step.get("visibility") or "chat") == "chat"
        ]
        if not content_steps:
            return ""
        return "".join(str(step.get("payload") or "") for step in content_steps)

    def _message_tool_calls_from_steps(self, message_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, step in enumerate(steps):
            if str(step.get("message_id") or "") != message_id:
                continue
            if str(step.get("type") or "") != "call":
                continue
            name = _optional_string(step.get("tool_name"))
            tool_call_id = _optional_string(step.get("tool_call_id"))
            if name is None and tool_call_id is None:
                continue
            key = tool_call_id or f"{name or 'tool'}:{index}"
            if key in seen:
                continue
            seen.add(key)
            tool_call = {
                "name": name,
                "args": self._tool_args_from_step_action(step.get("action")),
                "id": tool_call_id,
                "type": "tool_call",
            }
            tool_calls.append({key: value for key, value in tool_call.items() if value is not None})
        return tool_calls

    def _reasoning_duration_from_steps(self, message_id: str, steps: list[dict[str, Any]]) -> int | None:
        durations = [
            duration_ms
            for step in steps
            if str(step.get("message_id") or "") == message_id
            and str(step.get("type") or "") == "thinking"
            and isinstance((duration_ms := step.get("duration_ms")), int)
        ]
        if not durations:
            return None
        return max(durations)

    def _project_terminal_state(self, projected: Any, envelopes: list[RunEventEnvelope]) -> None:
        terminal = next(
            (envelope for envelope in reversed(envelopes) if envelope.kind in {"run_completed", "run_interrupted", "run_failed"}),
            None,
        )
        if terminal is None:
            if projected.approvals.pending_approval is not None:
                projected.lifecycle.status = ThreadLifecycleStatus.AWAITING_APPROVAL
                projected.lifecycle.completed_at = None
                return
            projected.lifecycle.status = ThreadLifecycleStatus.RUNNING
            projected.lifecycle.completed_at = None
            projected.lifecycle.last_error = None
            return

        payload = terminal.payload
        terminal_status = str(payload.get("status") or "")
        if terminal_status == ThreadLifecycleStatus.AWAITING_APPROVAL.value:
            projected.lifecycle.status = ThreadLifecycleStatus.AWAITING_APPROVAL
            projected.lifecycle.completed_at = None
            projected.lifecycle.last_error = (
                getattr(getattr(projected, "approvals", None), "approval_request", None).reason
                if getattr(getattr(projected, "approvals", None), "approval_request", None) is not None
                else _optional_string(payload.get("reason"))
            )
            return
        if terminal_status == ThreadLifecycleStatus.AWAITING_CLARIFICATION.value:
            projected.lifecycle.status = ThreadLifecycleStatus.AWAITING_CLARIFICATION
            projected.lifecycle.completed_at = None
            projected.lifecycle.last_error = (
                _optional_string(getattr(getattr(projected, "conversation", None), "pending_user_interaction", {}).get("question"))
                if isinstance(getattr(getattr(projected, "conversation", None), "pending_user_interaction", None), dict)
                else _optional_string(payload.get("reason"))
            )
            return

        completed_at = _parse_event_datetime(terminal.created_at) or datetime.now(timezone.utc)
        projected.lifecycle.completed_at = completed_at
        if terminal.kind == "run_failed":
            projected.lifecycle.status = ThreadLifecycleStatus.FAILED
            projected.lifecycle.last_error = _optional_string(payload.get("error")) or "Run failed."
            projected.conversation.pending_user_interaction = None
            return

        stream_status = str(payload.get("stream_status") or "")
        status = str(payload.get("status") or "")
        if terminal.kind == "run_interrupted" or stream_status == "interrupted" or status == "interrupted":
            projected.lifecycle.status = ThreadLifecycleStatus.INTERRUPTED
            projected.conversation.pending_user_interaction = None
            error = self._interrupted_reason(projected, payload)
            projected.lifecycle.last_error = error
            projected.execution.last_message_interrupted = True
            projected.execution.last_message_interrupted_reason = error
            for message in reversed(projected.conversation.messages):
                if str(message.get("role") or "") in {"assistant", "ai"}:
                    message["status"] = "interrupted"
                    break
            return

        projected.lifecycle.status = ThreadLifecycleStatus.COMPLETED
        projected.lifecycle.last_error = None
        projected.conversation.pending_user_interaction = None
        projected.execution.last_message_interrupted = False
        projected.execution.last_message_interrupted_reason = None

    def _interrupted_reason(self, projected: Any, payload: dict[str, Any]) -> str:
        for message in reversed(projected.conversation.messages):
            if str(message.get("role") or "") not in {"assistant", "ai"}:
                continue
            metadata = message.get("metadata")
            if isinstance(metadata, dict):
                reason = metadata.get("empty_final_reason")
                if reason:
                    return str(reason)
        return (
            _optional_string(payload.get("error"))
            or _optional_string(payload.get("reason"))
            or "Run interrupted before a normal completion."
        )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _merge_unique_strings(primary: list[str], existing: list[str]) -> list[str]:
    return list(dict.fromkeys([*primary, *existing]))


def _merge_unique_uploads(primary: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in [*primary, *existing]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("virtual_path") or item.get("artifact_url") or item.get("filename") or "")
        if not key:
            continue
        if key not in merged:
            order.append(key)
        merged[key] = dict(item)
    return [merged[key] for key in order]


def _merge_unique_approval_events(
    primary: list[RecentApprovalEvent],
    existing: list[RecentApprovalEvent],
) -> list[RecentApprovalEvent]:
    merged: dict[str, RecentApprovalEvent] = {}
    order: list[str] = []
    for item in primary:
        if not isinstance(item, RecentApprovalEvent):
            continue
        key = _approval_event_key(item)
        if key not in merged:
            order.append(key)
        merged[key] = item
    for item in existing:
        if not isinstance(item, RecentApprovalEvent):
            continue
        key = _approval_event_key(item)
        if key in merged:
            continue
        order.append(key)
        merged[key] = item
    return [merged[key] for key in order]


def _approval_event_key(item: RecentApprovalEvent) -> str:
    if item.request_id:
        return item.request_id
    created_at = item.created_at.isoformat() if item.created_at is not None else ""
    return f"{item.decision}:{item.status}:{created_at}"


def _merge_unique_subagent_history(
    primary: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in primary:
        if not isinstance(item, dict):
            continue
        key = _subagent_history_key(item)
        if key is None:
            continue
        if key not in merged:
            order.append(key)
        merged[key] = dict(item)
    for item in existing:
        if not isinstance(item, dict):
            continue
        key = _subagent_history_key(item)
        if key is None or key in merged:
            continue
        order.append(key)
        merged[key] = dict(item)
    ordered = sorted(
        (merged[key] for key in order),
        key=lambda item: str(item.get("timestamp") or ""),
    )
    return ordered[-limit:]


def _subagent_history_key(item: dict[str, Any]) -> str | None:
    job_id = _optional_string(item.get("job_id") or item.get("subagent_job_id"))
    if job_id is None:
        payload = item.get("payload")
        if isinstance(payload, dict):
            job_id = _optional_string(payload.get("task_id") or payload.get("subagent_job_id"))
    event_type = _optional_string(item.get("event_type") or item.get("event"))
    timestamp = _optional_string(item.get("timestamp"))
    if job_id is None or event_type is None:
        return None
    return f"{job_id}:{event_type}:{timestamp or ''}"


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        return [text] if text else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _parse_event_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _run_event_identity_key(envelope: RunEventEnvelope) -> tuple[str, str, str]:
    return envelope.thread_id, envelope.run_id, envelope.event_id


def _same_run_event_identity(first: RunEventEnvelope, second: RunEventEnvelope) -> bool:
    return _run_event_identity_key(first) == _run_event_identity_key(second)


def _run_event_sequence_key(envelope: RunEventEnvelope) -> tuple[str, str, int]:
    return envelope.thread_id, envelope.run_id, envelope.sequence


def _same_run_event_sequence(first: RunEventEnvelope, second: RunEventEnvelope) -> bool:
    return _run_event_sequence_key(first) == _run_event_sequence_key(second)


class RunStreamSession(Iterator[RunEvent]):
    def __init__(self) -> None:
        self._iterator: Iterator[RunEvent] | None = None
        self.final_result: Any | None = None
        self.event_log_store: RunEventLogStore | None = None

    def set_iterator(self, iterator: Iterator[RunEvent]) -> None:
        self._iterator = iterator

    def __iter__(self) -> "RunStreamSession":
        return self

    def __next__(self) -> RunEvent:
        if self._iterator is None:
            raise StopIteration
        return next(self._iterator)
