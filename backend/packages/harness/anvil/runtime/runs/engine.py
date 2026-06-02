from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import threading
import time
from copy import deepcopy
from queue import Queue
from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage

from anvil.agents import ArchivedSummary, RecentApprovalEvent, RecentToolActivity, SandboxState, ThreadExecutionMode, ThreadLifecycleStatus, ThreadMetadataView, ThreadState
from anvil.agents.features import RuntimeFeatureSet
from anvil.agents.lead_agent.agent import make_lead_agent
from anvil.agents.runtime_snapshot import RuntimeAssemblySnapshot
from anvil.agents.middlewares.llm_error_handling_middleware import LLMExecutionError
from anvil.agents.middlewares.title_middleware import TitleMiddleware
from anvil.agents.lead_agent.types import LeadAgentState
from anvil.config import ConfigResolutionResult, ConfigService, ConfigLayer, ModelRouteRequest, RequiredModelCapabilities
from anvil.extensions import ExtensionsService
from anvil.runtime.checkpointers import Checkpointer
from anvil.runtime.context_envelope import ContextAssembler, is_image_upload
from anvil.runtime.serialization import deserialize_messages, normalize_message_content, serialize_messages, strip_inline_thinking_tags, translate_message_for_runtime
from anvil.runtime.store import Store
from anvil.runtime.token_usage import aggregate_token_usage_from_messages, enrich_token_usage_summary
from anvil.runtime.tool_registry import CapabilityAssemblyService, ToolRegistry
from anvil.runtime.approvals import ApprovalDecision, ApprovalRequest
from anvil.sandbox import PathService
from anvil.skills import ProcedureLearningService, SkillsService
from anvil.memory_platform.pollution import tool_activity_pollution_reason

from .events import (
    InMemoryRunEventLogStore,
    RunEvent,
    RunEventEnvelope,
    RunEventLogStore,
    RunSnapshotProjector,
    RunStreamSession,
)

EMPTY_FINAL_ASSISTANT_MESSAGE = (
    "The model stopped after tool execution without producing a final answer. "
    "The run was marked interrupted so you can continue from the available tool results."
)


_RUN_PHASE_LABELS: dict[str, str] = {
    "config_resolved": "Config resolved",
    "config_reused": "Config reused",
    "thread_state_loaded": "Thread state loaded",
    "model_route_resolved": "Model route resolved",
    "sandbox_provider_created": "Sandbox provider created",
    "factory_started": "Runtime factory started",
    "factory_feature_set_resolved": "Feature set resolved",
    "factory_memory_services_ready": "Memory services ready",
    "factory_approval_service_ready": "Approval service ready",
    "capability_assembly_started": "Capability assembly started",
    "capability_assembly_completed": "Capability assembly completed",
    "memory_snapshot_loaded": "Memory snapshot loaded",
    "project_context_loaded": "Project context loaded",
    "runtime_path_context_built": "Runtime path context built",
    "prompt_snapshot_built": "Prompt snapshot built",
    "turn_injection_built": "Turn injection built",
    "system_prompt_composed": "System prompt composed",
    "lead_context_built": "Lead context built",
    "middleware_chain_built": "Middleware chain built",
    "chat_model_created": "Chat model created",
    "langgraph_agent_created": "LangGraph agent created",
    "assembly_snapshot_built": "Assembly snapshot built",
    "runtime_assembled": "Runtime assembled",
    "tracing_started": "Tracing started",
    "input_payload_built": "Input payload built",
    "running_state_persisted": "Running state persisted",
    "run_started_emitted": "Run started emitted",
    "agent_stream_entered": "Agent stream entered",
    "first_model_event": "First graph/model event",
    "first_message_event": "First message stream event",
    "first_update_event": "First update stream event",
    "first_values_event": "First values stream event",
    "first_reasoning_delta": "First reasoning delta",
    "first_content_step_started": "First content step started",
    "first_content_delta": "First content delta",
    "agent_stream_completed": "Agent stream completed",
    "agent_state_merged": "Agent state merged",
    "terminal_events_finalized": "Terminal events finalized",
    "subagent_finalizer_started": "Subagent finalizer started",
    "subagent_finalizer_completed": "Subagent finalizer completed",
    "final_state_persisted": "Final state persisted",
    "run_completed_emitted": "Run completed emitted",
    "run_failed": "Run failed",
}

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _merge_stream_payload(previous: str, incoming: str, *, allow_overlap_replay: bool = False) -> tuple[str, str]:
    incoming = str(incoming or "")
    if not incoming:
        return previous, ""
    if not previous:
        return incoming, incoming
    if incoming == previous:
        return previous, ""
    if _json_superset_snapshot(previous, incoming):
        return incoming, incoming
    if incoming.startswith(previous):
        return incoming, incoming[len(previous) :]
    if not allow_overlap_replay:
        return f"{previous}{incoming}", incoming
    if previous.endswith(incoming):
        return previous, ""
    overlap = _longest_suffix_prefix_overlap(previous, incoming)
    merged = f"{previous}{incoming[overlap:]}"
    return merged, incoming[overlap:]


def _json_superset_snapshot(previous: str, incoming: str) -> bool:
    try:
        previous_value = json.loads(previous)
        incoming_value = json.loads(incoming)
    except (TypeError, json.JSONDecodeError):
        return False
    if isinstance(previous_value, dict) and isinstance(incoming_value, dict):
        return all(key in incoming_value and incoming_value[key] == value for key, value in previous_value.items())
    if isinstance(previous_value, list) and isinstance(incoming_value, list):
        return len(incoming_value) >= len(previous_value)
    return False


def _longest_suffix_prefix_overlap(previous: str, incoming: str) -> int:
    max_length = min(len(previous), len(incoming))
    for length in range(max_length, 0, -1):
        if previous.endswith(incoming[:length]):
            return length
    return 0


class _RunPhaseRecorder:
    def __init__(self, *, run_id: str, thread_id: str) -> None:
        self._run_id = run_id
        self._thread_id = thread_id
        self._start_monotonic = time.perf_counter()
        self._start_wall = utc_now()
        self._marks: dict[str, float] = {}

    def mark(self, phase: str) -> None:
        self._marks.setdefault(phase, time.perf_counter())

    def snapshot(self, *, status: str = "running") -> dict[str, Any]:
        now = time.perf_counter()
        marks = [
            {
                "phase": phase,
                "label": _RUN_PHASE_LABELS.get(phase, phase.replace("_", " ").title()),
                "elapsed_ms": self._elapsed_ms(mark_time),
                "duration_since_previous_ms": self._duration_since_previous_ms(index, mark_time),
            }
            for index, (phase, mark_time) in enumerate(self._marks.items())
        ]
        runtime_assembly_ms = (
            self._phase_elapsed_ms("agent_stream_entered")
            or self._phase_elapsed_ms("run_started_emitted")
            or self._phase_elapsed_ms("input_payload_built")
            or self._phase_elapsed_ms("runtime_assembled")
        )
        first_model_ms = self._phase_elapsed_ms("first_model_event")
        first_content_ms = self._phase_elapsed_ms("first_content_delta")
        completed_ms = self._phase_elapsed_ms("run_completed_emitted") or self._phase_elapsed_ms("final_state_persisted")
        model_start_wait_ms = self._duration_between(runtime_assembly_ms, first_model_ms)
        first_content_wait_ms = self._duration_between(first_model_ms, first_content_ms)
        post_content_elapsed_ms = self._duration_between(first_content_ms, completed_ms)
        return {
            "run_id": self._run_id,
            "thread_id": self._thread_id,
            "status": status,
            "started_at": self._start_wall.isoformat(),
            "total_elapsed_ms": self._elapsed_ms(now),
            "runtime_assembly_elapsed_ms": runtime_assembly_ms,
            "model_start_wait_ms": model_start_wait_ms,
            "first_model_event_elapsed_ms": first_model_ms,
            "first_content_delta_elapsed_ms": first_content_ms,
            "first_content_wait_ms": first_content_wait_ms,
            "post_content_elapsed_ms": post_content_elapsed_ms,
            "completed_elapsed_ms": completed_ms,
            "marks": marks,
        }

    def _elapsed_ms(self, value: float) -> int:
        return max(int((value - self._start_monotonic) * 1000), 0)

    def _phase_elapsed_ms(self, phase: str) -> int | None:
        value = self._marks.get(phase)
        return self._elapsed_ms(value) if value is not None else None

    @staticmethod
    def _duration_between(start_ms: int | None, end_ms: int | None) -> int | None:
        if start_ms is None or end_ms is None or end_ms < start_ms:
            return None
        return end_ms - start_ms

    def _duration_since_previous_ms(self, index: int, value: float) -> int:
        if index <= 0:
            return self._elapsed_ms(value)
        previous_value = list(self._marks.values())[index - 1]
        return max(int((value - previous_value) * 1000), 0)


@dataclass
class RunRequest:
    thread_id: str
    user_message: str
    config_layers: list[ConfigLayer]
    path_service: PathService
    checkpointer: Checkpointer
    store: Store
    config_result: ConfigResolutionResult | None = None
    run_id: str | None = None
    feature_set: RuntimeFeatureSet | None = None
    execution_mode: ThreadExecutionMode = ThreadExecutionMode.AGENT
    selected_model: str | None = None
    selected_reasoning_effort: str | None = None
    profile: str | None = None
    request_context: str | None = None
    approval_context: str | None = None
    upload_context: str | None = None
    client_message_id: str | None = None
    is_plan_mode: bool | None = None
    promoted_capabilities: tuple[str, ...] = ()
    parent_visible_tool_names: tuple[str, ...] | None = None
    subagent_service: object | None = None
    process_service: object | None = None
    scheduled_task_service: object | None = None
    memory_manager: object | None = None
    skills_service: SkillsService | None = None
    extensions_service: ExtensionsService | None = None
    capability_assembly_service: CapabilityAssemblyService | None = None
    tracing_service: object | None = None
    include_user_message: bool = True
    drop_last_assistant_message: bool = False
    chat_model_override: BaseChatModel | None = None
    recent_upload_filenames: tuple[str, ...] = ()
    approval_session_grants: tuple[str, ...] = ()
    transcript_rewrite_boundary: bool = False
    cancellation_checker: Callable[[], bool] | None = None
    cancellation_reason: Callable[[], str | None] | None = None
    run_event_log_store: RunEventLogStore | None = None


@dataclass
class RunResult:
    thread_state: ThreadState
    metadata_view: ThreadMetadataView
    runtime: object


class RunInterruptedError(RuntimeError):
    def __init__(self, reason: str | None = None) -> None:
        super().__init__(reason or "Interrupted by user")


@dataclass(frozen=True)
class _BackgroundTask:
    name: str
    fn: Callable[[], None]


class _BackgroundTaskRunner:
    def __init__(self, *, max_workers: int = 2) -> None:
        self._queue: Queue[_BackgroundTask] = Queue()
        self._max_workers = max(1, max_workers)
        self._workers: list[threading.Thread] = []
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._pending = 0

    def submit(self, name: str, fn: Callable[[], None]) -> None:
        with self._lock:
            self._ensure_workers_locked()
            self._pending += 1
        self._queue.put(_BackgroundTask(name=name, fn=fn))

    def wait(self, timeout_seconds: float = 5.0) -> None:
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        with self._lock:
            while self._pending > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._idle.wait(timeout=remaining)

    def _ensure_workers_locked(self) -> None:
        while len(self._workers) < self._max_workers:
            worker_index = len(self._workers) + 1
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"anvil-run-background-{worker_index}",
                daemon=True,
            )
            self._workers.append(thread)
            thread.start()

    def _worker_loop(self) -> None:
        while True:
            task = self._queue.get()
            try:
                task.fn()
            except Exception:
                pass
            finally:
                with self._lock:
                    self._pending = max(0, self._pending - 1)
                    if self._pending == 0:
                        self._idle.notify_all()
                self._queue.task_done()


class RunEngine:
    def __init__(self, config_service: ConfigService | None = None) -> None:
        self.config_service = config_service or ConfigService()
        self._background_tasks = _BackgroundTaskRunner(max_workers=2)

    def run(self, request: RunRequest) -> RunResult:
        return self.run_sync(request)

    def run_sync(self, request: RunRequest) -> RunResult:
        session = self.run_stream(request)
        for _ in session:
            pass
        if session.final_result is None:
            raise RuntimeError("run_stream completed without final_result")
        return session.final_result

    def run_stream(self, request: RunRequest) -> RunStreamSession:
        request.execution_mode = self._normalize_execution_mode(request.execution_mode)
        request.run_id = request.run_id or f"run-{uuid4().hex[:12]}"
        phase_recorder = _RunPhaseRecorder(run_id=request.run_id, thread_id=request.thread_id)
        if request.config_result is not None:
            config_result = request.config_result
            phase_recorder.mark("config_reused")
        else:
            config_result = self.config_service.resolve(request.config_layers)
            phase_recorder.mark("config_resolved")
        if request.subagent_service is not None:
            request.subagent_service.reconcile_timeouts()
        thread_state = request.checkpointer.get_thread_state(request.thread_id)
        if thread_state is None:
            thread_state = self._create_initial_thread_state(request)
            request.checkpointer.put_thread_state(thread_state)
            request.store.put_thread_metadata(ThreadMetadataView.from_thread_state(thread_state))
        phase_recorder.mark("thread_state_loaded")
        context_assembler = ContextAssembler(path_service=request.path_service, thread_id=request.thread_id)
        baseline_outputs = set(request.path_service.list_artifact_relative_paths(request.thread_id, "outputs"))
        turn_message_at = utc_now() if request.include_user_message else None
        current_turn_uploads = (
            context_assembler.current_turn_uploads(
                uploaded_files=list(thread_state.artifacts.uploaded_files),
                recent_upload_filenames=request.recent_upload_filenames,
            )
            if request.include_user_message
            else []
        )
        requires_vision = any(is_image_upload(item) for item in current_turn_uploads)

        trace_id = request.tracing_service.new_trace_id() if request.tracing_service is not None else request.thread_id
        runtime = make_lead_agent(
            config_result=config_result,
            path_service=request.path_service,
            checkpointer=request.checkpointer,
            store=request.store,
            thread_id=request.thread_id,
            feature_set=request.feature_set or RuntimeFeatureSet(),
            route_request=ModelRouteRequest(
                subsystem="lead_agent",
                request_override_model=request.selected_model,
                profile=request.profile,
                required_capabilities=RequiredModelCapabilities(
                    tool_calling=request.execution_mode is not ThreadExecutionMode.CHAT,
                    vision=requires_vision,
                ),
            ),
            request_context=request.request_context,
            approval_context=request.approval_context,
            upload_context=request.upload_context,
            is_plan_mode=bool(
                request.is_plan_mode
                if request.is_plan_mode is not None
                else config_result.effective_config.plan_mode.default
            ),
            execution_mode=request.execution_mode,
            reasoning_effort_override=request.selected_reasoning_effort,
            promoted_capabilities=request.promoted_capabilities,
            parent_visible_tool_names=request.parent_visible_tool_names,
            run_id=request.run_id,
            subagent_service=request.subagent_service,
            process_service=request.process_service,
            scheduled_task_service=request.scheduled_task_service,
            memory_manager=request.memory_manager,
            skills_service=request.skills_service,
            extensions_service=request.extensions_service,
            capability_assembly_service=request.capability_assembly_service,
            tracing_service=request.tracing_service,
            run_trace_id=trace_id,
            runtime_phase_marker=phase_recorder.mark,
            recent_upload_filenames=request.recent_upload_filenames,
            chat_model_override=request.chat_model_override,
            approval_session_grants=request.approval_session_grants,
        )
        phase_recorder.mark("runtime_assembled")
        request.runtime = runtime
        runtime_assembly_diff = self._runtime_assembly_diff_payload(
            previous_snapshot=thread_state.execution.runtime_assembly_snapshot,
            runtime=runtime,
        )
        runtime.context.runtime_assembly_diff = runtime_assembly_diff
        runtime.context.initial_uploaded_files = tuple(thread_state.artifacts.uploaded_files)
        runtime.context.existing_thread_title = thread_state.conversation.title
        runtime.context.current_title = thread_state.conversation.title
        runtime.context.summary_context = thread_state.conversation.summary
        runtime.context.run_phase_timings = phase_recorder.snapshot(status="assembling")

        if request.tracing_service is not None:
            request.tracing_service.run_started(
                trace_id=trace_id,
                metadata={
                    "thread_id": request.thread_id,
                    "run_id": request.run_id,
                    "user_message": request.user_message if request.include_user_message else "<resume>",
                    "profile": request.profile,
                    "selected_model": request.selected_model,
                    "selected_reasoning_effort": request.selected_reasoning_effort,
                    "model_name": runtime.resolved_route.model_name,
                    "reasoning_effort": runtime.resolved_route.reasoning_effort,
                    "capability_bundle_fingerprint": runtime.context.capability_bundle.fingerprint,
                    "sandbox_mode": runtime.context.sandbox_handle.provider_mode if runtime.context.sandbox_handle else None,
                    "approval_context_present": bool(request.approval_context),
                    "execution_mode": request.execution_mode.value,
                    "is_plan_mode": bool(request.is_plan_mode),
                },
            )
        phase_recorder.mark("tracing_started")
        runtime.context.run_phase_timings = phase_recorder.snapshot(status="tracing_started")

        input_payload = self._build_input_payload(
            thread_state=thread_state,
            request=request,
            context_assembler=context_assembler,
        )
        phase_recorder.mark("input_payload_built")
        runtime.context.run_phase_timings = phase_recorder.snapshot(status="input_ready")
        session = RunStreamSession()
        event_log_store = request.run_event_log_store or InMemoryRunEventLogStore()
        session.event_log_store = event_log_store

        def iterator():
            trace_error: str | None = None
            trace_exception: Exception | None = None
            final_values: dict | None = None
            updated_state = self._build_running_state(
                thread_state=thread_state,
                request=request,
                runtime=runtime,
                input_messages=input_payload["messages"],
                context_assembler=context_assembler,
                turn_message_at=turn_message_at,
            )
            event_adapter = _GraphRunEventAdapter(
                thread_id=request.thread_id,
                run_id=request.run_id,
                execution_mode=request.execution_mode,
                tool_registry=runtime.tool_registry,
                existing_steps=thread_state.conversation.steps,
            )
            partial_persist_pending = False
            last_partial_persist_at = 0.0
            stream_sequence = 0
            run_event_envelopes: list[RunEventEnvelope] = []
            last_subagent_drain_at = 0.0
            user_message_ref = self._current_user_message_ref(
                updated_state,
                request=request,
            )

            def append_event(event: RunEvent) -> RunEvent:
                nonlocal stream_sequence
                stream_sequence += 1
                envelope = RunEventEnvelope.from_run_event(
                    event,
                    run_id=request.run_id or request.thread_id,
                    thread_id=request.thread_id,
                    sequence=stream_sequence,
                )
                event_log_store.append(envelope)
                run_event_envelopes.append(envelope)
                runtime.context.run_event_cursor = envelope.header()
                return envelope.to_run_event()

            def persist_partial_state(*, force: bool = False) -> None:
                nonlocal last_partial_persist_at, partial_persist_pending
                now = time.monotonic()
                if not force and last_partial_persist_at and (now - last_partial_persist_at) < 0.25:
                    partial_persist_pending = True
                    return
                partial_state = self._build_partial_stream_state(
                    base_state=updated_state,
                    request=request,
                    runtime=runtime,
                    event_adapter=event_adapter,
                )
                request.checkpointer.put_thread_state(partial_state)
                request.store.put_thread_metadata(ThreadMetadataView.from_thread_state(partial_state))
                last_partial_persist_at = now
                partial_persist_pending = False

            def emit_events(events: list[RunEvent]):
                for event in events:
                    if event.event in {
                        "step_started",
                        "summary_update",
                        "approval_requested",
                        "message_completed",
                        "run_failed",
                    }:
                        persist_partial_state(force=True)
                    elif event.event in {"step_delta", "step_updated"}:
                        persist_partial_state()
                    yield append_event(event)

            def drain_subagent_events_throttled() -> list[RunEvent]:
                nonlocal last_subagent_drain_at
                if request.subagent_service is None:
                    return []
                now = time.monotonic()
                if last_subagent_drain_at and (now - last_subagent_drain_at) < 0.25:
                    return []
                last_subagent_drain_at = now
                return self._drain_subagent_events(request=request, event_adapter=event_adapter)

            request.checkpointer.put_thread_state(updated_state)
            request.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated_state))
            user_message_ref = self._current_user_message_ref(
                updated_state,
                request=request,
            )
            phase_recorder.mark("running_state_persisted")
            phase_recorder.mark("run_started_emitted")
            runtime.context.run_phase_timings = phase_recorder.snapshot(status="streaming")
            run_started_data = {
                "thread_id": request.thread_id,
                "run_id": request.run_id,
                "message": request.user_message,
                "user_message": user_message_ref,
                "execution_mode": request.execution_mode.value,
            }
            if request.transcript_rewrite_boundary:
                run_started_data["transcript_rewrite_boundary"] = True
            yield append_event(
                RunEvent(
                    event="run_started",
                    data=run_started_data,
                )
            )
            if not request.include_user_message and request.approval_context:
                yield append_event(
                    RunEvent(
                        event="approval_resolved",
                        data={
                            "thread_id": request.thread_id,
                            "request_id": thread_state.approvals.approval_request.request_id
                            if thread_state.approvals.approval_request is not None
                            else None,
                            "approval_context": request.approval_context,
                        },
                    )
                )
            try:
                phase_recorder.mark("agent_stream_entered")
                for mode, payload in runtime.agent.stream(
                    input_payload,
                    context=runtime.context,
                    stream_mode=["messages", "updates", "values"],
                ):
                    phase_recorder.mark("first_model_event")
                    self._raise_if_cancelled(request)
                    if mode == "messages":
                        phase_recorder.mark("first_message_event")
                        events = event_adapter.handle_message_stream(payload)
                        self._mark_first_visible_output(phase_recorder, events)
                        yield from emit_events(events)
                        yield from emit_events(drain_subagent_events_throttled())
                    elif mode == "updates":
                        phase_recorder.mark("first_update_event")
                        events = event_adapter.handle_update_stream(payload)
                        self._mark_first_visible_output(phase_recorder, events)
                        yield from emit_events(events)
                        yield from emit_events(drain_subagent_events_throttled())
                    elif mode == "values" and isinstance(payload, dict):
                        phase_recorder.mark("first_values_event")
                        final_values = payload

                if final_values is None:
                    raise RuntimeError("stream execution completed without final graph values")
                phase_recorder.mark("agent_stream_completed")

                agent_state = LeadAgentState.model_validate(final_values)
                self._raise_if_cancelled(request)
                updated_state = self._merge_agent_state(
                    thread_state=thread_state,
                    agent_state=agent_state,
                    runtime=runtime,
                    request=request,
                    context_assembler=context_assembler,
                )
                updated_state = self._interrupt_if_missing_final_answer(updated_state, request=request, runtime=runtime)
                phase_recorder.mark("agent_state_merged")
                yield from emit_events(self._drain_subagent_events(request=request, event_adapter=event_adapter))
                event_adapter.apply_reasoning_metadata(updated_state)
                event_adapter.apply_step_metadata(updated_state)
                newly_registered_outputs = self._sync_output_artifacts(
                    updated_state,
                    path_service=request.path_service,
                )
                yield from emit_events(event_adapter.finalize(updated_state))
                event_adapter.apply_step_metadata(updated_state)
                for relative_path in newly_registered_outputs:
                    yield append_event(
                        RunEvent(
                            event="artifact_registered",
                            data={
                                "thread_id": request.thread_id,
                                "kind": "output",
                                "label": relative_path,
                                "artifact_url": f"/threads/{request.thread_id}/artifacts/outputs/{relative_path}",
                                "virtual_path": f"/mnt/user-data/outputs/{relative_path}",
                            },
                        )
                    )
                for artifact in _emit_artifact_events(updated_state):
                    yield append_event(artifact)
                phase_recorder.mark("terminal_events_finalized")
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, RunInterruptedError):
                    updated_state = self._build_interrupted_state(
                        thread_state=thread_state,
                        request=request,
                        runtime=runtime,
                        event_adapter=event_adapter,
                        input_messages=input_payload["messages"],
                        context_assembler=context_assembler,
                        error=str(exc),
                    )
                    event_adapter.apply_reasoning_metadata(updated_state)
                    yield from emit_events(event_adapter.finalize(updated_state))
                    event_adapter.apply_step_metadata(updated_state)
                elif isinstance(exc, LLMExecutionError) and exc.category == "partial_stream":
                    updated_state = self._build_interrupted_state(
                        thread_state=thread_state,
                        request=request,
                        runtime=runtime,
                        event_adapter=event_adapter,
                        input_messages=input_payload["messages"],
                        context_assembler=context_assembler,
                        error=str(exc),
                    )
                    event_adapter.apply_reasoning_metadata(updated_state)
                    yield from emit_events(event_adapter.finalize(updated_state))
                    event_adapter.apply_step_metadata(updated_state)
                else:
                    updated_state = thread_state.model_copy(deep=True)
                    updated_state.lifecycle.status = ThreadLifecycleStatus.FAILED
                    updated_state.lifecycle.last_error = str(exc)
                    updated_state.lifecycle.updated_at = utc_now()
                    updated_state.lifecycle.completed_at = utc_now()
                    updated_state.identity.run_id = request.run_id
                    updated_state.conversation.messages = serialize_messages(
                        context_assembler.persistent_transcript(list(input_payload["messages"]))
                    )
                    updated_state.execution.execution_mode = request.execution_mode
                    updated_state.execution.active_model = self._effective_active_model_name(runtime)
                    updated_state.execution.reasoning_effort = self._effective_active_reasoning_effort(runtime)
                    updated_state.execution.runtime_assembly_snapshot = self._runtime_assembly_snapshot_payload(runtime)
                    updated_state.execution.runtime_assembly_diff = self._runtime_assembly_diff_payload(runtime=runtime)
                    updated_state.execution.token_usage = self._extract_token_usage(
                        input_payload["messages"],
                        previous=updated_state.execution.token_usage,
                        runtime=runtime,
                    )
                    updated_state.execution.context_window_usage = self._build_context_window_usage(
                        token_usage=updated_state.execution.token_usage,
                        runtime=runtime,
                        messages=input_payload["messages"],
                    )
                    phase_recorder.mark("run_failed")
                    updated_state.execution.runtime_phase_timings = phase_recorder.snapshot(status="failed")
                    runtime.context.run_phase_timings = updated_state.execution.runtime_phase_timings
                    updated_state.execution.model_fallback_history = list(runtime.context.model_fallback_history)
                    event_adapter.mark_run_failed(str(exc))
                    trace_error = str(exc)
                    trace_exception = exc
                    self._enqueue_failed_turn_capture(runtime=runtime, messages=input_payload["messages"])
                    updated_state = self._sync_subagent_state(updated_state, request=request)
                    updated_state = self._maybe_generate_thread_title(
                        updated_state,
                        request=request,
                        runtime=runtime,
                        require_assistant=False,
                    )
                    yield from emit_events([RunEvent(
                        event="run_failed",
                        data={
                            "thread_id": request.thread_id,
                            "run_id": request.run_id,
                            "error": str(exc),
                            "kind": exc.__class__.__name__,
                            "execution_mode": request.execution_mode.value,
                            "token_usage": updated_state.execution.token_usage,
                        },
                    )])
            finally:
                if request.subagent_service is not None:
                    request.subagent_service.reconcile_timeouts()

            updated_state.execution.recent_tool_activity = self._merge_recent_tool_activity(
                updated_state.execution.recent_tool_activity,
                event_adapter.snapshot_recent_tool_activity(),
            )
            if request.approval_context and _approval_context_requests_session_grant(request.approval_context):
                approval_req = updated_state.approvals.approval_request or thread_state.approvals.approval_request
                if approval_req is not None:
                    grant_key = _compute_session_grant_key(
                        tool_name=approval_req.tool_name,
                        approval_profile=approval_req.approval_profile,
                        risk_category=approval_req.risk_category,
                        capability_group=approval_req.capability_group,
                    )
                    if grant_key and grant_key not in updated_state.approvals.session_approval_grants:
                        updated_state.approvals.session_approval_grants.append(grant_key)
            event_adapter.apply_step_metadata(updated_state)
            updated_state = self._sync_subagent_state(updated_state, request=request)
            phase_recorder.mark("subagent_finalizer_started")
            yield from emit_events(self._finalize_stream_subagent_events(request=request, event_adapter=event_adapter))
            phase_recorder.mark("subagent_finalizer_completed")
            updated_state = self._reconcile_terminal_subagent_tool_messages(updated_state, request=request)
            event_adapter.apply_step_metadata(updated_state)
            updated_state = self._sync_subagent_state(updated_state, request=request)
            updated_state = self._maybe_generate_thread_title(
                updated_state,
                request=request,
                runtime=runtime,
                require_assistant=False,
            )
            persisted_state = request.checkpointer.get_thread_state(request.thread_id)
            if persisted_state is not None:
                updated_state.durable_subagent_job_history = self._merge_subagent_history(
                    updated_state.durable_subagent_job_history,
                    persisted_state.durable_subagent_job_history,
                )
            phase_recorder.mark("final_state_persisted")
            updated_state.execution.runtime_phase_timings = phase_recorder.snapshot(status=updated_state.lifecycle.status.value)
            runtime.context.run_phase_timings = updated_state.execution.runtime_phase_timings
            if partial_persist_pending:
                persist_partial_state(force=True)
            request.checkpointer.put_thread_state(updated_state)
            metadata = ThreadMetadataView.from_thread_state(updated_state)
            request.store.put_thread_metadata(metadata)
            if request.tracing_service is not None:
                request.tracing_service.run_finished(
                    trace_id=trace_id,
                    metadata={
                        "thread_id": request.thread_id,
                        "status": updated_state.lifecycle.status.value,
                        "active_subagent_task_ids": [
                            task.get("task_id", "")
                            for task in updated_state.delegation.active_subagent_tasks
                        ],
                    },
                    error=trace_error,
                )
            if trace_error is None:
                phase_recorder.mark("run_completed_emitted")
                updated_state.execution.runtime_phase_timings = phase_recorder.snapshot(status=updated_state.lifecycle.status.value)
                runtime.context.run_phase_timings = updated_state.execution.runtime_phase_timings
                completed_event = append_event(
                    RunEvent(
                        event="run_completed",
                        data={
                            "thread_id": request.thread_id,
                            "run_id": request.run_id,
                            "status": updated_state.lifecycle.status.value,
                            "assistant_message": None
                            if updated_state.execution.last_message_interrupted
                            else self._latest_assistant_content(updated_state),
                            "execution_mode": request.execution_mode.value,
                            "stream_status": "interrupted" if updated_state.execution.last_message_interrupted else "complete",
                            "reason": updated_state.execution.last_message_interrupted_reason
                            if updated_state.execution.last_message_interrupted
                            else None,
                        },
                    )
                )
                completed_envelopes = event_log_store.list_events(thread_id=request.thread_id, run_id=request.run_id)
                updated_state = RunSnapshotProjector().project(updated_state, completed_envelopes)
                runtime.context.run_phase_timings = updated_state.execution.runtime_phase_timings
                request.checkpointer.put_thread_state(updated_state)
                metadata = ThreadMetadataView.from_thread_state(updated_state)
                request.store.put_thread_metadata(metadata)
                session.final_result = RunResult(thread_state=updated_state, metadata_view=metadata, runtime=runtime)
                self._schedule_post_run_maintenance(
                    updated_state,
                    request=request,
                    runtime=runtime,
                    trace_error=trace_error,
                    trace_exception=trace_exception,
                )
                yield completed_event
            else:
                session.final_result = RunResult(thread_state=updated_state, metadata_view=metadata, runtime=runtime)
                self._schedule_post_run_maintenance(
                    updated_state,
                    request=request,
                    runtime=runtime,
                    trace_error=trace_error,
                    trace_exception=trace_exception,
                )

        session.set_iterator(iterator())
        return session

    def _mark_first_visible_output(self, phase_recorder: _RunPhaseRecorder, events: list[RunEvent]) -> None:
        for event in events:
            if event.event == "step_started":
                step = event.data.get("step")
                if isinstance(step, dict) and step.get("type") == "content":
                    phase_recorder.mark("first_content_step_started")
                continue
            if event.event != "step_delta":
                continue
            step_id = str(event.data.get("step_id") or "")
            if ":thinking" in step_id:
                phase_recorder.mark("first_reasoning_delta")
            elif step_id.endswith(":content"):
                phase_recorder.mark("first_content_delta")

    def _raise_if_cancelled(self, request: RunRequest) -> None:
        if request.cancellation_checker is None or not request.cancellation_checker():
            return
        reason = request.cancellation_reason() if request.cancellation_reason is not None else None
        raise RunInterruptedError(reason)

    def resume_approval(
        self,
        *,
        thread_id: str,
        config_layers: list[ConfigLayer],
        config_result: ConfigResolutionResult | None = None,
        path_service: PathService,
        checkpointer: Checkpointer,
        store: Store,
        approval_context: str,
        feature_set: RuntimeFeatureSet | None = None,
        selected_model: str | None = None,
        selected_reasoning_effort: str | None = None,
        profile: str | None = None,
        request_context: str | None = None,
        upload_context: str | None = None,
        is_plan_mode: bool | None = None,
        promoted_capabilities: tuple[str, ...] = (),
        parent_visible_tool_names: tuple[str, ...] | None = None,
        subagent_service: object | None = None,
        process_service: object | None = None,
        scheduled_task_service: object | None = None,
        memory_manager: object | None = None,
        skills_service: SkillsService | None = None,
        extensions_service: ExtensionsService | None = None,
        capability_assembly_service: CapabilityAssemblyService | None = None,
        tracing_service: object | None = None,
        chat_model_override: BaseChatModel | None = None,
        run_event_log_store: RunEventLogStore | None = None,
    ) -> RunResult:
        state = checkpointer.get_thread_state(thread_id)
        if state is None:
            raise ValueError(f"thread '{thread_id}' was not found")
        if state.approvals.pending_approval is None:
            raise ValueError(f"thread '{thread_id}' has no pending approval to resume")

        return self.run(
            RunRequest(
                thread_id=thread_id,
                user_message="",
                config_layers=config_layers,
                config_result=config_result,
                path_service=path_service,
                checkpointer=checkpointer,
                store=store,
                feature_set=feature_set,
                execution_mode=state.execution.execution_mode,
                selected_model=selected_model,
                selected_reasoning_effort=selected_reasoning_effort,
                profile=profile,
                request_context=request_context,
                approval_context=approval_context,
                upload_context=upload_context,
                is_plan_mode=is_plan_mode if is_plan_mode is not None else state.execution.is_plan_mode,
                promoted_capabilities=promoted_capabilities,
                parent_visible_tool_names=parent_visible_tool_names,
                subagent_service=subagent_service,
                process_service=process_service,
                scheduled_task_service=scheduled_task_service,
                memory_manager=memory_manager,
                skills_service=skills_service,
                extensions_service=extensions_service,
                capability_assembly_service=capability_assembly_service,
                tracing_service=tracing_service,
                include_user_message=False,
                drop_last_assistant_message=True,
                recent_upload_filenames=(),
                chat_model_override=chat_model_override,
                run_event_log_store=run_event_log_store,
                approval_session_grants=tuple(state.approvals.session_approval_grants),
            )
        )

    def _create_initial_thread_state(self, request: RunRequest) -> ThreadState:
        thread_data = request.path_service.bootstrap_thread_paths(request.thread_id)
        now = utc_now()
        return ThreadState(
            identity={"thread_id": request.thread_id, "run_id": request.run_id},
            lifecycle={
                "status": ThreadLifecycleStatus.READY,
                "created_at": now,
                "updated_at": now,
            },
            execution={
                "execution_mode": request.execution_mode,
                "is_plan_mode": bool(request.is_plan_mode),
            },
            thread_data=thread_data.model_dump(),
        )

    def _build_running_state(
        self,
        *,
        thread_state: ThreadState,
        request: RunRequest,
        runtime,
        input_messages,
        context_assembler: ContextAssembler,
        turn_message_at: datetime | None = None,
    ) -> ThreadState:
        updated = thread_state.model_copy(deep=True)
        updated.identity.run_id = request.run_id
        updated.lifecycle.status = ThreadLifecycleStatus.RUNNING
        updated.lifecycle.updated_at = utc_now()
        updated.lifecycle.completed_at = None
        updated.lifecycle.last_error = None
        updated.conversation.messages = serialize_messages(context_assembler.persistent_transcript(list(input_messages)))
        if turn_message_at is not None:
            updated.conversation.last_message_at = turn_message_at
        updated.execution.execution_mode = request.execution_mode
        updated.execution.is_plan_mode = bool(
            request.is_plan_mode if request.is_plan_mode is not None else updated.execution.is_plan_mode
        )
        updated.execution.selected_model = request.selected_model
        updated.execution.selected_profile = request.profile
        updated.execution.selected_reasoning_effort = request.selected_reasoning_effort
        updated.execution.active_model = runtime.resolved_route.model_name
        updated.execution.reasoning_effort = runtime.resolved_route.reasoning_effort
        updated.execution.runtime_assembly_snapshot = self._runtime_assembly_snapshot_payload(runtime)
        updated.execution.runtime_assembly_diff = self._runtime_assembly_diff_payload(runtime=runtime)
        updated.execution.cancellation_requested = False
        updated.execution.last_message_interrupted = False
        updated.execution.last_message_interrupted_reason = None
        if runtime.context.sandbox_handle is not None:
            updated.execution.sandbox_state = SandboxState(
                sandbox_id=runtime.context.sandbox_handle.sandbox_id,
                sandbox_mode=runtime.context.sandbox_handle.provider_mode,
            )
        bundle = runtime.context.capability_bundle
        updated.capabilities.visible_tool_names = [entry.name for entry in bundle.visible_tools]
        updated.capabilities.deferred_tool_names = [entry.name for entry in bundle.deferred_tools]
        updated.capabilities.enabled_skill_ids = list(bundle.enabled_skill_ids)
        updated.capabilities.capability_bundle_fingerprint = bundle.fingerprint
        updated.memory.memory_namespace = runtime.context.memory_namespace or updated.memory.memory_namespace
        updated.thread_data = runtime.context.thread_data or updated.thread_data
        return self._sync_subagent_state(updated, request=request)

    def _current_user_message_ref(self, state: ThreadState, *, request: RunRequest) -> dict[str, object] | None:
        if not request.include_user_message and not request.transcript_rewrite_boundary:
            return None
        for message in reversed(state.conversation.messages):
            if message.get("role") not in {"human", "user"}:
                continue
            return dict(message)
        return None

    def _build_partial_stream_state(
        self,
        *,
        base_state: ThreadState,
        request: RunRequest,
        runtime,
        event_adapter,
    ) -> ThreadState:
        partial = base_state.model_copy(deep=True)
        partial.lifecycle.status = ThreadLifecycleStatus.RUNNING
        partial.lifecycle.updated_at = utc_now()
        partial.lifecycle.completed_at = None
        partial.lifecycle.last_error = None
        partial.execution.recent_tool_activity = self._merge_recent_tool_activity(
            partial.execution.recent_tool_activity,
            event_adapter.snapshot_recent_tool_activity(),
        )
        partial.execution.context_window_usage = self._build_context_window_usage(
            token_usage=partial.execution.token_usage,
            runtime=runtime,
            messages=deserialize_messages(partial.conversation.messages),
        )
        partial.execution.runtime_phase_timings = dict(getattr(runtime.context, "run_phase_timings", {}) or {})
        partial.execution.runtime_assembly_snapshot = self._runtime_assembly_snapshot_payload(runtime)
        partial.execution.runtime_assembly_diff = self._runtime_assembly_diff_payload(runtime=runtime)
        partial.execution.active_model = self._effective_active_model_name(runtime)
        partial.execution.reasoning_effort = self._effective_active_reasoning_effort(runtime)
        partial.execution.model_fallback_history = list(runtime.context.model_fallback_history)
        event_adapter.apply_reasoning_metadata(partial)
        event_adapter.apply_step_metadata(partial)
        self._ensure_step_placeholder_messages(partial)
        return self._sync_subagent_state(partial, request=request)

    def _ensure_step_placeholder_messages(self, state: ThreadState) -> None:
        known_message_ids = {
            str(payload.get("id"))
            for payload in state.conversation.messages
            if payload.get("id") is not None
        }
        for step in state.conversation.steps:
            message_id = str(step.get("message_id") or "")
            if not message_id or message_id in known_message_ids:
                continue
            state.conversation.messages.append(
                {
                    "id": message_id,
                    "role": "ai",
                    "content": "",
                    "status": "streaming",
                }
            )
            known_message_ids.add(message_id)

    def _build_input_payload(
        self,
        *,
        thread_state: ThreadState,
        request: RunRequest,
        context_assembler: ContextAssembler | None = None,
    ) -> dict[str, object]:
        assembler = context_assembler or ContextAssembler(path_service=request.path_service, thread_id=request.thread_id)
        envelope = assembler.assemble_input_envelope(
            history_messages=deserialize_messages(thread_state.conversation.messages),
            translate_message=translate_message_for_runtime,
            user_message=request.user_message,
            include_user_message=request.include_user_message,
            drop_last_assistant_message=request.drop_last_assistant_message,
            uploaded_files=list(thread_state.artifacts.uploaded_files),
            recent_upload_filenames=request.recent_upload_filenames,
            client_message_id=request.client_message_id,
            vision_supported=_request_runtime_supports_vision(request),
        )
        return envelope.to_input_payload(
            uploaded_files=list(thread_state.artifacts.uploaded_files),
            title=thread_state.conversation.title,
            summary=thread_state.conversation.summary,
            todos=list(thread_state.planning.todo_snapshot),
            token_usage=dict(thread_state.execution.token_usage),
        )

    def _merge_agent_state(
        self,
        *,
        thread_state: ThreadState,
        agent_state: LeadAgentState,
        runtime,
        request: RunRequest,
        context_assembler: ContextAssembler,
    ) -> ThreadState:
        updated = thread_state.model_copy(deep=True)
        updated.lifecycle.updated_at = utc_now()
        updated.identity.run_id = request.run_id
        updated.conversation.messages = serialize_messages(
            context_assembler.persistent_transcript(list(agent_state.messages))
        )
        updated.thread_data = agent_state.thread_data or updated.thread_data
        updated.execution.execution_mode = request.execution_mode
        updated.execution.is_plan_mode = bool(
            request.is_plan_mode
            if request.is_plan_mode is not None
            else updated.execution.is_plan_mode
        )
        updated.execution.selected_model = request.selected_model
        updated.execution.selected_profile = request.profile
        updated.execution.selected_reasoning_effort = request.selected_reasoning_effort
        updated.execution.active_model = self._effective_active_model_name(runtime)
        updated.execution.reasoning_effort = self._effective_active_reasoning_effort(runtime)
        updated.execution.token_usage = self._extract_token_usage(
            agent_state.messages,
            previous=updated.execution.token_usage,
            runtime=runtime,
        )
        if agent_state.token_usage:
            updated.execution.token_usage = self._enrich_token_usage_summary(agent_state.token_usage, runtime=runtime)
        updated.execution.context_window_usage = self._build_context_window_usage(
            token_usage=updated.execution.token_usage,
            runtime=runtime,
            messages=agent_state.messages,
        )
        updated.execution.model_fallback_history = list(runtime.context.model_fallback_history)
        updated.execution.runtime_phase_timings = dict(getattr(runtime.context, "run_phase_timings", {}) or {})
        updated.execution.runtime_assembly_snapshot = self._runtime_assembly_snapshot_payload(runtime)
        updated.execution.runtime_assembly_diff = self._runtime_assembly_diff_payload(runtime=runtime)
        updated.execution.last_message_interrupted = bool(
            agent_state.stream_interrupted or runtime.context.interrupted_stream
        )
        updated.execution.last_message_interrupted_reason = (
            agent_state.interrupted_stream_reason
            or runtime.context.interrupted_stream_reason
        )
        updated.execution.sandbox_state = agent_state.sandbox_state
        updated.capabilities.visible_tool_names = agent_state.visible_tool_names
        updated.capabilities.deferred_tool_names = agent_state.deferred_tool_names
        updated.capabilities.capability_bundle_fingerprint = agent_state.capability_bundle_fingerprint
        updated.capabilities.enabled_skill_ids = agent_state.enabled_skill_ids
        updated.memory.memory_namespace = runtime.context.memory_namespace or updated.memory.memory_namespace
        updated.memory.injected_memory_snapshot_id = agent_state.memory_snapshot_id
        updated.delegation.active_subagent_tasks = agent_state.active_subagent_tasks
        if agent_state.title:
            updated.conversation.title = agent_state.title
        effective_summary = agent_state.summary or (
            runtime.context.summary_context if runtime.context.summarization_triggered else None
        )
        if effective_summary:
            updated.conversation.summary = effective_summary
        if agent_state.todos:
            updated.planning.todo_snapshot = [item.model_dump(mode="json") for item in agent_state.todos]
        updated.prompt_snapshot.snapshot_id = runtime.prompt_snapshot.snapshot_id
        updated.prompt_snapshot.snapshot_hash = runtime.prompt_snapshot.snapshot_key.digest()
        updated.prompt_snapshot.created_at = utc_now()
        updated.prompt_snapshot.project_context_fingerprint = runtime.context.project_context_fingerprint
        updated.prompt_snapshot.project_context_files = [dict(item) for item in runtime.context.project_context_files]
        if (agent_state.summarization_triggered or runtime.context.summarization_triggered) and effective_summary:
            updated.archived_summaries.append(
                ArchivedSummary(
                    summary_id=f"summary-{len(updated.archived_summaries) + 1}",
                    summary_text=effective_summary,
                    covers_turn_range=(0, max(len(updated.conversation.messages) - 1, 0)),
                    token_count=max(len(effective_summary) // 4, 1),
                    prompt_snapshot_id=runtime.prompt_snapshot.snapshot_id,
                    compaction_level=int(getattr(runtime.context, "compaction_level", 0) or 0),
                    compaction_level_label=getattr(runtime.context, "compaction_level_label", None),
                    compaction_reason=getattr(runtime.context, "compaction_reason", None),
                    diagnostics=dict(getattr(runtime.context, "compaction_diagnostics", {}) or {}),
                )
            )

        if agent_state.pending_approval is not None:
            updated.lifecycle.status = ThreadLifecycleStatus.AWAITING_APPROVAL
            updated.lifecycle.completed_at = None
            updated.lifecycle.last_error = agent_state.approval_request_reason
            updated.approvals.pending_approval = agent_state.pending_approval
            updated.approvals.approval_request = agent_state.approval_request
            if agent_state.approval_request is not None:
                updated.approvals.recent_approval_events = self._record_approval_requested(
                    updated.approvals.recent_approval_events,
                    approval_request=agent_state.approval_request,
                    execution_mode=request.execution_mode,
                )
            return self._sync_subagent_state(updated, request=request)

        if self._should_pause_for_plan_confirmation(request=request, agent_state=agent_state):
            approval_request = self._build_plan_approval_request(
                thread_id=request.thread_id,
                run_id=request.run_id or updated.identity.run_id or "run-plan",
            )
            updated.lifecycle.status = ThreadLifecycleStatus.AWAITING_APPROVAL
            updated.lifecycle.completed_at = None
            updated.lifecycle.last_error = approval_request.reason
            updated.approvals.pending_approval = ApprovalDecision.NEEDS_USER_APPROVAL
            updated.approvals.approval_request = approval_request
            updated.approvals.recent_approval_events = self._record_approval_requested(
                updated.approvals.recent_approval_events,
                approval_request=approval_request,
                execution_mode=request.execution_mode,
            )
            return self._sync_subagent_state(updated, request=request)

        if agent_state.clarification_requested:
            updated.lifecycle.status = ThreadLifecycleStatus.AWAITING_CLARIFICATION
            updated.lifecycle.completed_at = None
            updated.lifecycle.last_error = agent_state.clarification_prompt
            updated.conversation.pending_user_interaction = (
                agent_state.pending_user_interaction.model_dump(mode="json")
                if agent_state.pending_user_interaction is not None
                else None
            )
        elif updated.execution.last_message_interrupted:
            updated.lifecycle.status = ThreadLifecycleStatus.INTERRUPTED
            updated.lifecycle.completed_at = utc_now()
            updated.lifecycle.last_error = (
                updated.execution.last_message_interrupted_reason
                or "Run interrupted before a normal completion."
            )
            updated.conversation.pending_user_interaction = None
            updated.conversation.messages = self._mark_latest_assistant_message_interrupted(
                updated.conversation.messages
            )
            if updated.approvals.pending_approval is not None:
                updated.approvals.recent_approval_events = self._record_approval_resolved(
                    updated.approvals.recent_approval_events,
                    decision="approved",
                    execution_mode=request.execution_mode,
                )
            updated.approvals.pending_approval = None
            updated.approvals.approval_request = None
        else:
            updated.lifecycle.status = ThreadLifecycleStatus.COMPLETED
            updated.lifecycle.completed_at = utc_now()
            updated.lifecycle.last_error = None
            updated.conversation.pending_user_interaction = None
            if updated.approvals.pending_approval is not None:
                updated.approvals.recent_approval_events = self._record_approval_resolved(
                    updated.approvals.recent_approval_events,
                    decision="approved",
                    execution_mode=request.execution_mode,
                )
            updated.approvals.pending_approval = None
            updated.approvals.approval_request = None

        if updated.lifecycle.status is ThreadLifecycleStatus.FAILED and request.subagent_service is not None:
            request.subagent_service.cancel_for_parent_thread(
                updated.identity.thread_id,
                reason="parent thread failed",
            )
        return self._sync_subagent_state(updated, request=request)

    def _interrupt_if_missing_final_answer(self, state: ThreadState, *, request: RunRequest, runtime) -> ThreadState:
        if state.lifecycle.status is not ThreadLifecycleStatus.COMPLETED:
            return state
        messages = list(state.conversation.messages)
        if not messages:
            return state

        last_message_snapshot = messages[-1]
        last_role = str(last_message_snapshot.get("role") or "")
        if last_role not in {"assistant", "ai"}:
            return state
        if self._assistant_message_has_visible_content(last_message_snapshot):
            return state

        updated = state.model_copy(deep=True)
        reason = EMPTY_FINAL_ASSISTANT_MESSAGE
        now = utc_now()
        updated.lifecycle.status = ThreadLifecycleStatus.INTERRUPTED
        updated.lifecycle.completed_at = now
        updated.lifecycle.updated_at = now
        updated.lifecycle.last_error = reason
        updated.execution.last_message_interrupted = True
        updated.execution.last_message_interrupted_reason = reason
        updated.execution.model_fallback_history = list(runtime.context.model_fallback_history)
        if runtime.context.sandbox_handle is not None:
            updated.execution.sandbox_state = SandboxState(
                sandbox_id=runtime.context.sandbox_handle.sandbox_id,
                sandbox_mode=runtime.context.sandbox_handle.provider_mode,
            )

        last_message = dict(updated.conversation.messages[-1])
        last_message["content"] = ""
        last_message["status"] = "interrupted"
        metadata = dict(last_message.get("metadata") or {}) if isinstance(last_message.get("metadata"), dict) else {}
        metadata["empty_final_reason"] = reason
        last_message["metadata"] = metadata
        updated.conversation.messages[-1] = last_message
        runtime.context.interrupted_stream = True
        runtime.context.interrupted_stream_reason = reason

        return updated

    def _mark_latest_assistant_message_interrupted(
        self,
        messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        updated_messages = list(messages)
        for index in range(len(updated_messages) - 1, -1, -1):
            message = updated_messages[index]
            if message.get("role") not in {"assistant", "ai"}:
                continue
            updated_message = dict(message)
            updated_message["status"] = "interrupted"
            updated_messages[index] = updated_message
            break
        return updated_messages

    def _assistant_message_has_visible_content(self, message: dict[str, object]) -> bool:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return True

        blocks_source = message.get("content_blocks")
        if isinstance(content, list):
            blocks_source = content
        if not isinstance(blocks_source, list):
            return False

        for item in blocks_source:
            if isinstance(item, str) and item.strip():
                return True
            if not isinstance(item, dict):
                continue
            block_type = str(item.get("type") or "text")
            if block_type == "thinking":
                continue
            if block_type == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    if str(image_url.get("url") or "").strip():
                        return True
                elif str(image_url or "").strip():
                    return True
            if str(item.get("text") or item.get("content") or "").strip():
                return True
        return False

    def _maybe_generate_thread_title(
        self,
        state: ThreadState,
        *,
        request: RunRequest,
        runtime,
        require_assistant: bool,
    ) -> ThreadState:
        if state.conversation.title:
            return state
        try:
            messages = deserialize_messages(state.conversation.messages)
            agent_state = LeadAgentState(
                messages=messages,
                title=state.conversation.title,
            )
            title = TitleMiddleware().generate_title_for_state(
                agent_state,
                runtime,
                require_assistant=require_assistant,
                allow_llm=False,
            )
        except Exception:
            title = None
        if not title:
            return state
        updated = state.model_copy(deep=True)
        updated.conversation.title = title
        updated.lifecycle.updated_at = utc_now()
        runtime.context.current_title = title
        return updated

    def _schedule_post_run_maintenance(
        self,
        state: ThreadState,
        *,
        request: RunRequest,
        runtime,
        trace_error: str | None,
        trace_exception: Exception | None,
    ) -> None:
        state_snapshot = state.model_copy(deep=True)

        self._schedule_async_title_refinement(
            state_snapshot,
            request=request,
            runtime=runtime,
            require_assistant=trace_error is None,
        )
        self._record_memory_platform_turn(state_snapshot, request=request, runtime=runtime)

        def run_maintenance() -> None:
            if trace_error is not None:
                self._record_failed_skill_feedback(
                    runtime=runtime,
                    error=trace_exception or RuntimeError(trace_error),
                )
                return

            self._record_completed_skill_feedback(updated_state=state_snapshot, runtime=runtime)
            procedure_state = self._record_successful_procedure_candidate(
                updated_state=state_snapshot,
                request=request,
                runtime=runtime,
            )
            self._merge_async_procedure_learning_state(
                before=state_snapshot,
                after=procedure_state,
                request=request,
            )

        self._submit_background_task("post-run-maintenance", run_maintenance)

    def _schedule_async_title_refinement(
        self,
        state: ThreadState,
        *,
        request: RunRequest,
        runtime,
        require_assistant: bool,
    ) -> None:
        middleware = TitleMiddleware()
        if not require_assistant or not middleware.wants_llm_title(runtime):
            return

        try:
            messages = deserialize_messages(state.conversation.messages)
            title_state = LeadAgentState(messages=messages, title=None)
            fallback_title = middleware.generate_title_for_state(
                title_state,
                runtime,
                require_assistant=require_assistant,
                allow_llm=False,
                ignore_existing=True,
                update_runtime=False,
            )
        except Exception:
            return

        current_title = state.conversation.title
        if not fallback_title and not current_title:
            return

        def refine_title() -> None:
            try:
                generated = middleware.generate_title_for_state(
                    title_state,
                    runtime,
                    require_assistant=require_assistant,
                    allow_llm=True,
                    ignore_existing=True,
                    update_runtime=False,
                )
            except Exception:
                return
            if not generated or generated == current_title:
                return

            def merge_title(latest: ThreadState) -> ThreadState | None:
                latest_title = latest.conversation.title
                allowed_titles = {None, "", current_title, fallback_title}
                if latest_title not in allowed_titles:
                    return None
                updated = latest.model_copy(deep=True)
                updated.conversation.title = generated
                updated.lifecycle.updated_at = utc_now()
                return updated

            self._patch_thread_state(request=request, mutator=merge_title)

        self._submit_background_task("title-refine", refine_title)

    def _merge_async_procedure_learning_state(
        self,
        *,
        before: ThreadState,
        after: ThreadState,
        request: RunRequest,
    ) -> None:
        new_runs = [
            run_id
            for run_id in after.memory.procedure_learning_runs
            if run_id not in before.memory.procedure_learning_runs
        ]
        new_signatures = [
            signature
            for signature in after.memory.procedure_learning_signatures
            if signature not in before.memory.procedure_learning_signatures
        ]
        if not new_runs and not new_signatures:
            return

        def merge_procedure_state(latest: ThreadState) -> ThreadState | None:
            updated = latest.model_copy(deep=True)
            changed = False
            if new_runs:
                merged_runs = [*updated.memory.procedure_learning_runs]
                for run_id in new_runs:
                    if run_id not in merged_runs:
                        merged_runs.append(run_id)
                        changed = True
                updated.memory.procedure_learning_runs = merged_runs[-50:]
            if new_signatures:
                merged_signatures = [*updated.memory.procedure_learning_signatures]
                for signature in new_signatures:
                    if signature not in merged_signatures:
                        merged_signatures.append(signature)
                        changed = True
                updated.memory.procedure_learning_signatures = merged_signatures[-50:]
            if not changed:
                return None
            updated.lifecycle.updated_at = utc_now()
            return updated

        self._patch_thread_state(request=request, mutator=merge_procedure_state)

    def _patch_thread_state(
        self,
        *,
        request: RunRequest,
        mutator: Callable[[ThreadState], ThreadState | None],
    ) -> None:
        try:
            latest = request.checkpointer.get_thread_state(request.thread_id)
            if latest is None:
                return
            updated = mutator(latest)
            if updated is None:
                return
            request.checkpointer.put_thread_state(updated)
            request.store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))
        except Exception:
            return

    def _submit_background_task(self, name: str, fn: Callable[[], None]) -> None:
        self.submit_background_task(name, fn)

    def submit_background_task(self, name: str, fn: Callable[[], None]) -> None:
        def run_task() -> None:
            try:
                fn()
            except Exception:
                return

        self._background_tasks.submit(name, run_task)

    def wait_for_background_tasks(self, timeout_seconds: float = 5.0) -> None:
        self._background_tasks.wait(timeout_seconds)

    def _should_pause_for_plan_confirmation(self, *, request: RunRequest, agent_state: LeadAgentState) -> bool:
        if not request.is_plan_mode:
            return False
        if request.approval_context:
            return False
        if request.execution_mode is ThreadExecutionMode.CHAT:
            return False
        if agent_state.clarification_requested or agent_state.pending_approval is not None:
            return False
        return bool(agent_state.todos)

    def _build_plan_approval_request(self, *, thread_id: str, run_id: str) -> ApprovalRequest:
        return ApprovalRequest(
            request_id=f"{thread_id}/{run_id}/plan_confirmation",
            thread_id=thread_id,
            turn_id=run_id,
            reason="Review the proposed plan before implementation continues.",
            action_kind="plan_confirmation",
            requested_permissions=["plan_confirmation"],
            scope_options=("turn",),
        )

    def _sync_subagent_state(self, state: ThreadState, *, request: RunRequest) -> ThreadState:
        if request.subagent_service is None:
            return state
        active = [
            task.model_dump(mode="json")
            for task in request.subagent_service.list_active_tasks(parent_thread_id=state.identity.thread_id)
        ]
        updated = state.model_copy(deep=True)
        updated.delegation.active_subagent_tasks = active
        return updated

    def _drain_subagent_events(self, *, request: RunRequest, event_adapter=None, settle_seconds: float = 0.0) -> list[RunEvent]:
        if request.subagent_service is None or not hasattr(request.subagent_service, "drain_events"):
            return []
        drained = request.subagent_service.drain_events(
            parent_thread_id=request.thread_id,
            parent_run_id=request.run_id,
        )
        events: list[RunEvent] = []
        name_map = {
            "job_submitted": "subagent_submitted",
            "job_started": "subagent_started",
            "tool_call": "subagent_tool_call",
            "tool_result": "subagent_tool_result",
            "model_response": "subagent_model_response",
            "job_completed": "subagent_completed",
            "job_failed": "subagent_failed",
            "job_cancelled": "subagent_cancelled",
            "job_timed_out": "subagent_timed_out",
            "job_interrupted": "subagent_interrupted",
        }
        for item in drained:
            if event_adapter is not None:
                events.extend(event_adapter.handle_subagent_event(item))
            events.append(
                RunEvent(
                    event=name_map.get(item.event_type.value, "subagent_event"),
                    data={
                        "thread_id": request.thread_id,
                        "run_id": request.run_id,
                        "subagent_job_id": item.job_id,
                        "event_type": item.event_type.value,
                        "timestamp": item.timestamp.isoformat(),
                        **item.payload,
                    },
                )
            )
        return events

    def _sync_subagent_step_snapshots(self, *, request: RunRequest, event_adapter=None) -> list[RunEvent]:
        if request.subagent_service is None or event_adapter is None or not hasattr(request.subagent_service, "list_tasks"):
            return []
        events: list[RunEvent] = []
        for task in request.subagent_service.list_tasks(parent_thread_id=request.thread_id):
            if request.run_id is not None and getattr(task, "parent_run_id", None) != request.run_id:
                continue
            result = None
            if hasattr(request.subagent_service, "get_result"):
                result = request.subagent_service.get_result(task.task_id)
            events.extend(event_adapter.handle_subagent_task_snapshot(task=task, result=result))
        return events

    def _finalize_stream_subagent_events(self, *, request: RunRequest, event_adapter=None) -> list[RunEvent]:
        events = self._drain_subagent_events(request=request, event_adapter=event_adapter)
        if not self._has_active_subagents_for_run(request=request) and event_adapter is not None:
            events.extend(self._sync_subagent_step_snapshots(request=request, event_adapter=event_adapter))
        return events

    def _has_active_subagents_for_run(self, *, request: RunRequest) -> bool:
        if request.subagent_service is None or not hasattr(request.subagent_service, "list_active_tasks"):
            return False
        return any(
            getattr(task, "parent_run_id", None) == request.run_id
            for task in request.subagent_service.list_active_tasks(parent_thread_id=request.thread_id)
        )

    def _reconcile_terminal_subagent_tool_messages(self, state: ThreadState, *, request: RunRequest) -> ThreadState:
        service = request.subagent_service
        if service is None or not hasattr(service, "serialize_result_payload"):
            return state
        messages = list(state.conversation.messages)
        if not messages:
            return state

        terminal_payloads: dict[str, dict[str, object]] = {}
        changed = False
        for index, message in enumerate(messages):
            if message.get("role") != "tool" or message.get("name") != "subagent":
                continue
            payload = self._json_object_from_text(message.get("content"))
            if str(payload.get("status") or "") != "waiting":
                continue
            task_id = payload.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                continue
            terminal_payload = service.serialize_result_payload(task_id, compact=True)
            if not self._is_terminal_subagent_payload(terminal_payload):
                continue
            terminal_payloads[task_id] = terminal_payload
            updated_message = dict(message)
            updated_message["content"] = json.dumps(terminal_payload, ensure_ascii=False)
            messages[index] = updated_message
            changed = True

        if not changed:
            return state

        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.get("role") not in {"ai", "assistant"}:
                continue
            content = message.get("content")
            if not isinstance(content, str) or "Subagent completed: waiting" not in content:
                break
            replacement = self._assistant_subagent_reconciliation_text(terminal_payloads)
            if not replacement:
                break
            updated_message = dict(message)
            updated_message["content"] = content.replace("Subagent completed: waiting", replacement, 1)
            messages[index] = updated_message
            break

        updated = state.model_copy(deep=True)
        updated.conversation.messages = messages
        updated.lifecycle.updated_at = utc_now()
        updated.conversation.steps = self._reconciled_content_steps(
            state.conversation.steps,
            terminal_payloads=terminal_payloads,
        )
        return updated

    def _json_object_from_text(self, value: object) -> dict[str, object]:
        if not isinstance(value, str):
            return {}
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(key): item for key, item in parsed.items()}

    def _is_terminal_subagent_payload(self, payload: dict[str, object] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        return str(payload.get("status") or "") in {
            "completed",
            "failed",
            "cancelled",
            "timed_out",
            "interrupted",
            "failed_recovery",
        }

    def _assistant_subagent_reconciliation_text(self, payloads: dict[str, dict[str, object]]) -> str | None:
        for payload in reversed(list(payloads.values())):
            status = str(payload.get("status") or "")
            summary = str(payload.get("summary") or "").strip()
            error = str(payload.get("error") or "").strip()
            if status == "completed" and summary:
                return f"Subagent completed: {summary}"
            if status in {"failed", "timed_out", "cancelled", "interrupted", "failed_recovery"}:
                reason = error or summary or status
                return f"Subagent failed: {reason}"
        return None

    def _reconciled_content_steps(
        self,
        steps: list[dict[str, object]],
        *,
        terminal_payloads: dict[str, dict[str, object]],
    ) -> list[dict[str, object]]:
        replacement = self._assistant_subagent_reconciliation_text(terminal_payloads)
        if not replacement:
            return steps
        updated_steps = deepcopy(steps)
        for step in reversed(updated_steps):
            if step.get("type") != "content":
                continue
            payload = step.get("payload")
            if not isinstance(payload, str) or "Subagent completed: waiting" not in payload:
                continue
            step["payload"] = payload.replace("Subagent completed: waiting", replacement, 1)
            break
        return updated_steps

    def _build_interrupted_state(
        self,
        *,
        thread_state: ThreadState,
        request: RunRequest,
        runtime,
        event_adapter,
        input_messages,
        context_assembler: ContextAssembler,
        error: str,
    ) -> ThreadState:
        updated = thread_state.model_copy(deep=True)
        updated.identity.run_id = request.run_id
        updated.lifecycle.status = ThreadLifecycleStatus.INTERRUPTED
        updated.lifecycle.completed_at = utc_now()
        updated.lifecycle.updated_at = utc_now()
        updated.lifecycle.last_error = error
        updated.execution.execution_mode = request.execution_mode
        updated.execution.is_plan_mode = bool(
            request.is_plan_mode if request.is_plan_mode is not None else updated.execution.is_plan_mode
        )
        updated.execution.selected_model = request.selected_model
        updated.execution.selected_profile = request.profile
        updated.execution.selected_reasoning_effort = request.selected_reasoning_effort
        updated.execution.active_model = self._effective_active_model_name(runtime)
        updated.execution.reasoning_effort = self._effective_active_reasoning_effort(runtime)
        updated.execution.runtime_assembly_snapshot = self._runtime_assembly_snapshot_payload(runtime)
        updated.execution.runtime_assembly_diff = self._runtime_assembly_diff_payload(runtime=runtime)
        updated.execution.cancellation_requested = False
        updated.execution.last_message_interrupted = True
        updated.execution.last_message_interrupted_reason = error
        updated.execution.model_fallback_history = list(runtime.context.model_fallback_history)
        if runtime.context.sandbox_handle is not None:
            updated.execution.sandbox_state = SandboxState(
                sandbox_id=runtime.context.sandbox_handle.sandbox_id,
                sandbox_mode=runtime.context.sandbox_handle.provider_mode,
            )
        partial_message = event_adapter.build_interrupted_message()
        runtime_messages = list(input_messages)
        if partial_message is not None:
            runtime_messages.append(partial_message)
        serialized = serialize_messages(context_assembler.persistent_transcript(runtime_messages))
        if serialized and partial_message is not None:
            serialized[-1]["status"] = "interrupted"
        updated.conversation.messages = serialized
        updated.conversation.summary = runtime.context.summary_context or updated.conversation.summary
        updated.prompt_snapshot.snapshot_id = runtime.prompt_snapshot.snapshot_id
        updated.prompt_snapshot.snapshot_hash = runtime.prompt_snapshot.snapshot_key.digest()
        updated.prompt_snapshot.created_at = utc_now()
        updated.prompt_snapshot.project_context_fingerprint = runtime.context.project_context_fingerprint
        updated.prompt_snapshot.project_context_files = [dict(item) for item in runtime.context.project_context_files]
        return self._sync_subagent_state(updated, request=request)

    def _enqueue_failed_turn_capture(self, *, runtime, messages) -> None:
        memory_service = runtime.context.memory_service
        if memory_service is None:
            return

        namespace = runtime.context.memory_namespace or "global/default"
        try:
            envelope = memory_service.build_capture_envelope(
                thread_id=runtime.context.thread_id,
                namespace=namespace,
                messages=messages,
                trace_id=runtime.context.thread_id,
                failed=True,
            )
        except Exception:
            return

        if not memory_service.has_capture_signal(envelope):
            return
        memory_service.enqueue_capture(envelope)

    def _record_failed_skill_feedback(self, *, runtime, error: Exception) -> None:
        skills_service = runtime.context.skills_service
        config_result = runtime.context.config_result
        if skills_service is None or config_result is None:
            return
        skill_ids = self._loaded_skill_ids(runtime)
        if not skill_ids:
            return
        error_text = str(error).strip()
        if len(error_text) > 500:
            error_text = error_text[:497].rstrip() + "..."
        rationale = f"Runtime run failed after this skill was loaded: {error.__class__.__name__}: {error_text}"
        for skill_id in skill_ids:
            try:
                skills_service.manage_curator(
                    config=config_result.effective_config,
                    action="feedback",
                    skill_id=skill_id,
                    outcome="failure",
                    rationale=rationale,
                    feedback_source="runtime_failure",
                    confidence=0.7,
                )
            except Exception:
                continue

    def _record_completed_skill_feedback(self, *, updated_state: ThreadState, runtime) -> None:
        if updated_state.lifecycle.status is not ThreadLifecycleStatus.COMPLETED:
            return
        skills_service = runtime.context.skills_service
        config_result = runtime.context.config_result
        if skills_service is None or config_result is None:
            return
        skill_ids = self._loaded_skill_ids(runtime)
        if not skill_ids:
            return
        procedure_learning = ProcedureLearningService()
        evidence_tool_steps = procedure_learning.procedure_tool_steps(updated_state)
        evidence_tool_activities = procedure_learning.procedure_tool_activities(updated_state)
        if not evidence_tool_steps and not evidence_tool_activities:
            return
        tool_names = ", ".join(
            dict.fromkeys(
                [
                    *(str(step.get("tool_name") or "").strip() for step in evidence_tool_steps),
                    *(activity.name for activity in evidence_tool_activities if activity.name),
                ]
            )
        )
        rationale = f"Runtime run completed successfully after this skill was loaded with visible tool evidence: {tool_names or 'visible tools'}."
        for skill_id in skill_ids:
            try:
                skills_service.manage_curator(
                    config=config_result.effective_config,
                    action="feedback",
                    skill_id=skill_id,
                    outcome="success",
                    rationale=rationale,
                    feedback_source="runtime_success",
                    confidence=0.4,
                )
            except Exception:
                continue

    def _record_successful_procedure_candidate(self, *, updated_state: ThreadState, request: RunRequest, runtime) -> ThreadState:
        skills_service = runtime.context.skills_service
        config_result = runtime.context.config_result
        if skills_service is None or config_result is None:
            return updated_state
        procedure_learning = ProcedureLearningService()
        skill_ids = self._loaded_skill_ids(runtime)
        evidence = procedure_learning.evaluate_thread(
            state=updated_state,
            run_id=request.run_id or updated_state.identity.run_id,
            skill_ids=skill_ids,
        )
        if not evidence.accepted:
            return updated_state
        run_id = request.run_id or updated_state.identity.run_id
        if run_id and run_id in updated_state.memory.procedure_learning_runs:
            return updated_state
        if evidence.signature and evidence.signature in updated_state.memory.procedure_learning_signatures:
            return updated_state
        result = procedure_learning.learn_from_thread(
            state=updated_state,
            config=config_result.effective_config,
            skills_service=skills_service,
            source="runtime_success",
            run_id=run_id,
            skill_ids=skill_ids,
        )
        if result.accepted:
            updated = updated_state.model_copy(deep=True)
            if run_id:
                updated.memory.procedure_learning_runs = [*updated.memory.procedure_learning_runs, run_id][-50:]
            if evidence.signature:
                updated.memory.procedure_learning_signatures = [*updated.memory.procedure_learning_signatures, evidence.signature][-50:]
            return updated
        return updated_state

    def _procedure_step_for_tool(self, tool_name: str) -> str:
        return ProcedureLearningService().procedure_step_for_tool(tool_name)

    def _loaded_skill_ids(self, runtime) -> tuple[str, ...]:
        bundle = runtime.context.capability_bundle
        mentioned = getattr(bundle, "mentioned_skill_ids", ())
        return tuple(dict.fromkeys(str(item) for item in mentioned if str(item).strip()))

    def _record_memory_platform_turn(self, updated_state: ThreadState, *, request: RunRequest, runtime) -> None:
        memory_manager = request.memory_manager
        if memory_manager is None:
            return

        if request.include_user_message:
            user_content = request.user_message
        elif request.approval_context:
            user_content = f"[approval-resume] {request.approval_context}"
        else:
            user_content = "[system-turn]"

        assistant_content = self._latest_assistant_content(updated_state)
        if assistant_content is None:
            assistant_content = updated_state.lifecycle.last_error or updated_state.lifecycle.status.value

        try:
            memory_manager.record_turn(
                thread_id=request.thread_id,
                user_content=user_content,
                assistant_content=assistant_content,
                status=updated_state.lifecycle.status.value,
                source_metadata=self._memory_source_metadata(updated_state),
            )
            memory_manager.record_prompt_snapshot(
                thread_id=request.thread_id,
                snapshot_id=runtime.prompt_snapshot.snapshot_id,
                prompt_hash=runtime.prompt_snapshot.snapshot_key.digest(),
                prompt_text=runtime.system_prompt,
                skills_fingerprint=runtime.prompt_snapshot.snapshot_key.enabled_skill_summary_fingerprint,
                memory_fingerprint=runtime.prompt_snapshot.snapshot_key.memory_snapshot_fingerprint,
                config_fingerprint=runtime.prompt_snapshot.snapshot_key.config_fingerprint,
            )
        except Exception:
            return

    def _latest_assistant_content(self, state: ThreadState) -> str | None:
        for step in reversed(state.conversation.steps):
            if step.get("type") == "content":
                payload = step.get("payload")
                if isinstance(payload, str) and payload.strip():
                    return payload
        for message in reversed(state.conversation.messages):
            if message.get("role") in {"assistant", "ai"}:
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
        return None

    def _memory_source_metadata(self, state: ThreadState) -> dict[str, object]:
        markers: list[dict[str, object]] = []
        seen: set[tuple[str, str, str]] = set()
        for activity in state.execution.recent_tool_activity:
            reason = tool_activity_pollution_reason(activity)
            if reason is None:
                continue
            key = (
                activity.source_kind or "",
                activity.source_id or "",
                activity.name or "",
            )
            if key in seen:
                continue
            seen.add(key)
            markers.append(
                {
                    "source_kind": activity.source_kind or "external",
                    "source_id": activity.source_id,
                    "tool_name": activity.name,
                    "reason": reason,
                    "capability_group": activity.capability_group,
                    "tool_call_id": activity.tool_call_id,
                    "status": activity.status,
                }
            )
            if len(markers) >= 20:
                break
        return {"pollution_markers": markers} if markers else {}

    def _extract_token_usage(self, messages, *, previous: dict[str, object], runtime) -> dict[str, object]:
        route_model_name = self._effective_active_model_name(runtime)
        model_config = runtime.context.config_result.effective_config.models.get(route_model_name)
        return aggregate_token_usage_from_messages(
            messages,
            previous=previous,
            model_config=model_config,
            route_model_name=route_model_name,
            token_usage_config=runtime.context.config_result.effective_config.token_usage,
        )

    def _enrich_token_usage_summary(self, token_usage: dict[str, object], *, runtime) -> dict[str, object]:
        route_model_name = self._effective_active_model_name(runtime)
        model_config = runtime.context.config_result.effective_config.models.get(route_model_name)
        return enrich_token_usage_summary(
            token_usage,
            model_config=model_config,
            route_model_name=route_model_name,
            token_usage_config=runtime.context.config_result.effective_config.token_usage,
        )

    def _build_context_window_usage(self, *, token_usage: dict[str, object], runtime, messages=None) -> dict[str, object]:
        route_model_name = self._effective_active_model_name(runtime)
        model_config = runtime.context.config_result.effective_config.models.get(route_model_name)
        context_window_tokens = model_config.effective_context_window_tokens() if model_config is not None else None
        auto_compact_threshold_tokens = (
            model_config.effective_auto_compact_threshold_tokens() if model_config is not None else None
        )

        context_breakdown = self._estimate_runtime_context_breakdown(runtime, messages=messages)
        estimated_context = context_breakdown["context_tokens"]
        category_breakdown = context_breakdown.get("context_breakdown")
        if not isinstance(category_breakdown, dict):
            category_breakdown = {}
        input_tokens = _first_int(
            token_usage,
            "input_tokens",
            "total.input_tokens",
            "prompt_tokens",
            "input_token_count",
            "prompt_token_count",
        )
        output_tokens = _first_int(
            token_usage,
            "output_tokens",
            "total.output_tokens",
            "completion_tokens",
            "output_token_count",
            "completion_token_count",
            "generated_tokens",
        )
        total_tokens = _first_int(token_usage, "total_tokens", "total.total_tokens", "total_token_count")
        if total_tokens is None:
            if input_tokens is not None and output_tokens is not None:
                total_tokens = input_tokens + output_tokens
            elif input_tokens is not None:
                total_tokens = input_tokens
        last_usage = token_usage.get("last") if isinstance(token_usage.get("last"), dict) else {}
        last_input_tokens = _first_int(last_usage, "input_tokens")
        context_tokens = estimated_context if estimated_context is not None else last_input_tokens
        if estimated_context is not None:
            context_source = "estimated"
        elif last_input_tokens is not None:
            context_source = "provider_last_input"
        else:
            context_source = None

        usage_ratio = _ratio(context_tokens, context_window_tokens)
        compact_ratio = _ratio(context_tokens, auto_compact_threshold_tokens)
        compact_status = "unknown"
        if context_tokens is not None and auto_compact_threshold_tokens is not None:
            compact_status = "over_threshold" if context_tokens >= auto_compact_threshold_tokens else "below_threshold"
        if (
            getattr(runtime.context, "summarization_triggered", False)
            or self._uses_compacted_message_window(runtime, messages)
        ):
            compact_status = "compacted"
        compaction_level = int(getattr(runtime.context, "compaction_level", 0) or 0)
        uses_compacted_window = self._uses_compacted_message_window(runtime, messages)
        if compaction_level <= 0 and uses_compacted_window:
            compaction_level = 1
        compaction_level_label = getattr(runtime.context, "compaction_level_label", None)
        if not compaction_level_label:
            compaction_level_label = {
                0: "none",
                1: "summary",
                2: "recursive_summary",
                3: "emergency",
            }.get(compaction_level, "summary")
        compaction_input_tokens = _first_int(
            {
                "value": getattr(runtime.context, "compaction_input_tokens", None),
            },
            "value",
        )
        compaction_summary_tokens = _first_int(
            {
                "value": getattr(runtime.context, "compaction_summary_tokens", None),
            },
            "value",
        )
        if compaction_summary_tokens is None:
            compaction_summary_tokens = category_breakdown.get("conversation_summary")
        compaction_keep_recent_turns = _first_int(
            {
                "value": getattr(runtime.context, "compaction_keep_recent_turns", None),
            },
            "value",
        )
        if compaction_keep_recent_turns is None:
            config_result = getattr(runtime.context, "config_result", None)
            effective_config = getattr(config_result, "effective_config", None)
            summarization_config = getattr(effective_config, "summarization", None)
            keep_recent_turns = getattr(summarization_config, "keep_recent_turns", None)
            compaction_keep_recent_turns = keep_recent_turns if isinstance(keep_recent_turns, int) else None
        compaction_savings_tokens = (
            _non_negative_difference(compaction_input_tokens, context_tokens)
            if compaction_input_tokens is not None and context_tokens is not None
            else None
        )
        compaction_diagnostics = dict(getattr(runtime.context, "compaction_diagnostics", {}) or {})

        cost = token_usage.get("cost") if isinstance(token_usage.get("cost"), dict) else {}
        cache_read_tokens = _first_int(token_usage, "cache_read_tokens", "total.cache_read_tokens")
        cache_write_tokens = _first_int(token_usage, "cache_write_tokens", "total.cache_write_tokens")
        cache_hit_ratio = _ratio(cache_read_tokens, (cache_read_tokens or 0) + (cache_write_tokens or 0))
        return {
            "model": route_model_name,
            "concrete_model": model_config.effective_model_name() if model_config is not None else route_model_name,
            "provider": token_usage.get("provider") or (model_config.provider if model_config is not None else None),
            **context_breakdown,
            "context_tokens": context_tokens,
            "estimated_context_tokens": estimated_context,
            "context_source": context_source,
            "context_breakdown_percentages": _category_percentages(category_breakdown, context_tokens),
            "dominant_context_category": _dominant_context_category(category_breakdown),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "request_count": _first_int(token_usage, "request_count"),
            "context_window_tokens": context_window_tokens,
            "auto_compact_threshold_tokens": auto_compact_threshold_tokens,
            "usage_ratio": usage_ratio,
            "compact_ratio": compact_ratio,
            "compact_status": compact_status,
            "summarization_triggered": bool(getattr(runtime.context, "summarization_triggered", False)),
            "compaction_level": compaction_level,
            "compaction_level_label": compaction_level_label,
            "compaction_reason": getattr(runtime.context, "compaction_reason", None),
            "compaction_input_tokens": compaction_input_tokens,
            "compaction_summary_tokens": compaction_summary_tokens,
            "compaction_savings_tokens": compaction_savings_tokens,
            "compaction_keep_recent_turns": compaction_keep_recent_turns,
            "compaction_diagnostics": compaction_diagnostics,
            "estimated_cost_usd": _first_float(token_usage, "estimated_cost_usd") or _first_float(cost, "estimated_cost_usd"),
            "cost_status": token_usage.get("cost_status") or cost.get("status"),
            "currency": token_usage.get("currency") or cost.get("currency"),
            "autocompact_buffer_tokens": _non_negative_difference(auto_compact_threshold_tokens, context_tokens),
            "free_space_tokens": _non_negative_difference(context_window_tokens, context_tokens),
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cache_hit_ratio": cache_hit_ratio,
            "cache_savings_tokens": cache_read_tokens,
        }

    def _estimate_runtime_context_usage(self, runtime, messages=None) -> int | None:
        return self._estimate_runtime_context_breakdown(runtime, messages=messages)["context_tokens"]

    def _estimate_runtime_context_breakdown(self, runtime, messages=None) -> dict[str, object]:
        if messages is None:
            return _empty_context_breakdown()
        total_chars = 0
        visible_messages = self._messages_for_context_accounting(runtime, messages)
        for message in visible_messages:
            text, blocks = normalize_message_content(getattr(message, "content", ""))
            visible_blocks = [block for block in blocks if block.get("type") != "thinking"]
            if visible_blocks:
                total_chars += sum(len(str(block.get("text") or "")) for block in visible_blocks)
            else:
                total_chars += len(text)
            for key in ("tool_calls", "invalid_tool_calls"):
                value = getattr(message, key, None)
                if value:
                    total_chars += len(json.dumps(value, ensure_ascii=False, default=str))
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id:
                total_chars += len(str(tool_call_id))
        message_tokens = _estimate_tokens_from_chars(total_chars)
        stable_sections = list(getattr(runtime.prompt_snapshot, "stable_sections", []) or [])
        section_tokens = {
            str(getattr(section, "name", "")): _estimate_tokens_from_text(str(getattr(section, "content", "") or ""))
            for section in stable_sections
        }
        capability_tokens = section_tokens.get("capability_summary")
        deferred_tokens = section_tokens.get("deferred_capabilities")
        memory_tokens = section_tokens.get("memory_snapshot")
        project_context_tokens = section_tokens.get("project_context_files")
        runtime_path_tokens = section_tokens.get("runtime_path_roots")
        volatile_context_tokens = self._estimate_volatile_context_tokens(runtime)
        system_tokens = sum(
            value or 0
            for key, value in section_tokens.items()
            if key
            not in {
                "capability_summary",
                "deferred_capabilities",
                "memory_snapshot",
                "project_context_files",
                "runtime_path_roots",
            }
        )
        tool_schema_tokens = self._estimate_tool_schema_tokens(runtime)
        skill_tokens = self._estimate_skill_tokens(runtime)
        context_tokens = sum(
            value or 0
            for value in (
                message_tokens,
                system_tokens,
                tool_schema_tokens,
                skill_tokens,
                memory_tokens,
                project_context_tokens,
                runtime_path_tokens,
                capability_tokens,
                deferred_tokens,
                *volatile_context_tokens.values(),
            )
        )
        category_breakdown = {
            "messages": message_tokens,
            "system": system_tokens,
            "tool_schemas": tool_schema_tokens,
            "skills": skill_tokens,
            "memory": memory_tokens,
            "project_context": project_context_tokens,
            "runtime_paths": runtime_path_tokens,
            "visible_capabilities": capability_tokens,
            "deferred_capabilities": deferred_tokens,
        }
        category_breakdown.update(volatile_context_tokens)
        category_breakdown = {key: value for key, value in category_breakdown.items() if isinstance(value, int) and value > 0}
        return {
            "context_tokens": context_tokens if context_tokens > 0 else None,
            "context_breakdown": category_breakdown,
            "message_tokens": message_tokens,
            "system_tokens": system_tokens or None,
            "tool_schema_tokens": tool_schema_tokens,
            "skill_tokens": skill_tokens,
            "memory_tokens": memory_tokens,
            "project_context_tokens": project_context_tokens,
            "runtime_path_tokens": runtime_path_tokens,
        }

    def _messages_for_context_accounting(self, runtime, messages) -> list:
        message_list = list(messages)
        if not self._uses_compacted_message_window(runtime, message_list):
            return message_list
        context = getattr(runtime, "context", None)
        config_result = getattr(context, "config_result", None)
        effective_config = getattr(config_result, "effective_config", None)
        keep_recent_turns = getattr(getattr(effective_config, "summarization", None), "keep_recent_turns", None)
        return message_list[-keep_recent_turns:]

    def _uses_compacted_message_window(self, runtime, messages) -> bool:
        if messages is None:
            return False
        message_list = list(messages)
        context = getattr(runtime, "context", None)
        if context is None or not getattr(context, "summary_context", None):
            return False
        config_result = getattr(context, "config_result", None)
        effective_config = getattr(config_result, "effective_config", None)
        summarization_config = getattr(effective_config, "summarization", None)
        if not bool(getattr(summarization_config, "enabled", False)):
            return False
        keep_recent_turns = getattr(summarization_config, "keep_recent_turns", None)
        if not isinstance(keep_recent_turns, int) or keep_recent_turns <= 0:
            return False
        return len(message_list) > keep_recent_turns

    def _estimate_volatile_context_tokens(self, runtime) -> dict[str, int]:
        text_by_category: dict[str, list[str]] = {}
        seen: set[tuple[str, str]] = set()

        def add_text(category: str, value: object) -> None:
            text = self._context_accounting_text(value)
            if text is None:
                return
            marker = (category, text)
            if marker in seen:
                return
            seen.add(marker)
            text_by_category.setdefault(category, []).append(text)

        injection_view = getattr(runtime, "prompt_injection_view", None)
        if injection_view is not None:
            add_text("request_context", getattr(injection_view, "request_context", None))
            add_text("upload_context", getattr(injection_view, "upload_context", None))
            add_text("approval_context", getattr(injection_view, "approval_context", None))
            add_text("plan_context", getattr(injection_view, "plan_context", None))
            add_text("memory_context", getattr(injection_view, "memory_context", None))
            add_text("promoted_capabilities", getattr(injection_view, "promoted_capabilities", ()))

        context = getattr(runtime, "context", None)
        if context is not None:
            add_text("request_context", getattr(context, "request_context", None))
            add_text("upload_context", getattr(context, "upload_context", None))
            add_text("approval_context", getattr(context, "approval_context", None))
            add_text("memory_context", getattr(context, "memory_context", None))
            add_text("conversation_summary", getattr(context, "summary_context", None))
            add_text("todo_state", getattr(context, "todo_context", None))
            add_text("view_image_context", getattr(context, "view_image_context", None))
            add_text("promoted_capabilities", getattr(context, "promoted_capabilities", ()))

        tokens: dict[str, int] = {}
        for category, parts in text_by_category.items():
            token_count = _estimate_tokens_from_text("\n\n".join(parts))
            if token_count is not None and token_count > 0:
                tokens[category] = token_count
        return tokens

    def _context_accounting_text(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, dict):
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            return text if text and text != "{}" else None
        if isinstance(value, (tuple, list, set, frozenset)):
            parts = [str(item).strip() for item in value if str(item).strip()]
            if not parts:
                return None
            return "\n".join(f"- {part}" for part in parts)
        text = str(value).strip()
        return text or None

    def _estimate_tool_schema_tokens(self, runtime) -> int | None:
        total_chars = 0
        for entry in getattr(runtime.context.capability_bundle, "visible_tools", ()) or ():
            total_chars += len(str(getattr(entry, "name", "") or ""))
            total_chars += len(str(getattr(entry, "description", "") or ""))
            schema = getattr(entry, "input_schema", None) or getattr(entry, "schema", None) or getattr(entry, "args_schema", None)
            if schema:
                total_chars += len(json.dumps(schema, ensure_ascii=False, default=str))
        return _estimate_tokens_from_chars(total_chars)

    def _estimate_skill_tokens(self, runtime) -> int | None:
        total_chars = 0
        for item in getattr(runtime.context.capability_bundle, "prompt_safe_summaries", ()) or ():
            total_chars += len(str(item))
        return _estimate_tokens_from_chars(total_chars)

    def _effective_active_model_name(self, runtime) -> str:
        return str(getattr(runtime.context, "active_model_name", None) or runtime.resolved_route.model_name)

    def _effective_active_reasoning_effort(self, runtime) -> str | None:
        return getattr(runtime.context, "active_reasoning_effort", None) or runtime.resolved_route.reasoning_effort

    def _runtime_assembly_snapshot_payload(self, runtime) -> dict[str, object]:
        snapshot = getattr(runtime, "assembly_snapshot", None)
        if snapshot is None:
            return {}
        if hasattr(snapshot, "model_dump"):
            payload = snapshot.model_dump(mode="json")
            if isinstance(payload, dict):
                diagnostics = getattr(getattr(runtime, "context", None), "memory_injection_diagnostics", None)
                if isinstance(diagnostics, dict) and diagnostics:
                    payload["memory_injection_diagnostics"] = dict(diagnostics)
                compaction_diagnostics = getattr(getattr(runtime, "context", None), "compaction_diagnostics", None)
                if isinstance(compaction_diagnostics, dict) and compaction_diagnostics:
                    payload["compaction_diagnostics"] = dict(compaction_diagnostics)
                return payload
            return payload if isinstance(payload, dict) else {}
        if isinstance(snapshot, dict):
            payload = dict(snapshot)
            diagnostics = getattr(getattr(runtime, "context", None), "memory_injection_diagnostics", None)
            if isinstance(diagnostics, dict) and diagnostics:
                payload["memory_injection_diagnostics"] = dict(diagnostics)
            compaction_diagnostics = getattr(getattr(runtime, "context", None), "compaction_diagnostics", None)
            if isinstance(compaction_diagnostics, dict) and compaction_diagnostics:
                payload["compaction_diagnostics"] = dict(compaction_diagnostics)
            return payload
        return {}

    def _runtime_assembly_diff_payload(
        self,
        *,
        runtime,
        previous_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        existing = getattr(runtime.context, "runtime_assembly_diff", None)
        if isinstance(existing, dict) and previous_snapshot is None:
            return dict(existing)
        current = getattr(runtime, "assembly_snapshot", None)
        if current is None or not isinstance(previous_snapshot, dict) or not previous_snapshot:
            return {
                "baseline": "none",
                "changed": False,
                "changed_paths": [],
                "changes": {},
                "added": {},
                "removed": {},
            }
        try:
            previous = RuntimeAssemblySnapshot.model_validate(previous_snapshot)
            diff = previous.diff(current)
        except Exception as exc:
            return {
                "baseline": "invalid",
                "changed": False,
                "changed_paths": [],
                "changes": {},
                "added": {},
                "removed": {},
                "error": exc.__class__.__name__,
            }
        payload = diff.model_dump(mode="json")
        payload["baseline"] = "previous_run"
        payload["changed"] = diff.changed
        return payload

    def _record_approval_requested(self, existing, *, approval_request, execution_mode: ThreadExecutionMode):
        request_id = approval_request.request_id
        if any(item.request_id == request_id and item.status == "requested" for item in existing):
            return existing
        event = RecentApprovalEvent(
            request_id=request_id,
            decision="needs_user_approval",
            reason=approval_request.reason,
            action_kind=approval_request.action_kind,
            requested_permissions=list(approval_request.requested_permissions),
            scope_options=list(approval_request.scope_options),
            status="requested",
            execution_mode=execution_mode,
        )
        return [event, *existing][:20]

    def _record_approval_resolved(self, existing, *, decision: str, execution_mode: ThreadExecutionMode):
        updated: list[RecentApprovalEvent] = []
        resolved_one = False
        for item in existing:
            if not resolved_one and item.status == "requested":
                resolved_one = True
                updated.append(
                    item.model_copy(
                        update={
                            "decision": decision,
                            "status": "resolved",
                            "execution_mode": execution_mode,
                            "resolved_at": utc_now(),
                        }
                    )
                )
            else:
                updated.append(item)
        return updated[:20]

    def _merge_recent_tool_activity(
        self,
        existing: list[RecentToolActivity],
        latest: list[RecentToolActivity],
        *,
        limit: int = 20,
    ) -> list[RecentToolActivity]:
        merged: dict[str, RecentToolActivity] = {}
        order: list[str] = []
        for item in [*existing, *latest]:
            key = self._recent_tool_activity_key(item)
            merged[key] = item.model_copy(deep=True)
            if key not in order:
                order.append(key)
        ordered = sorted(
            (merged[key] for key in order),
            key=self._recent_tool_activity_sort_key,
            reverse=True,
        )
        return ordered[:limit]

    def _recent_tool_activity_key(self, item: RecentToolActivity) -> str:
        if item.tool_call_id:
            return item.tool_call_id
        if item.message_id and item.name:
            return f"{item.message_id}:{item.name}"
        if item.name and item.started_at is not None:
            return f"{item.name}:{item.started_at.isoformat()}"
        return f"{item.name or 'tool'}:{item.status or 'unknown'}"

    def _recent_tool_activity_sort_key(self, item: RecentToolActivity) -> tuple[datetime, datetime]:
        return (
            item.completed_at or item.started_at or datetime.min.replace(tzinfo=timezone.utc),
            item.started_at or datetime.min.replace(tzinfo=timezone.utc),
        )

    def _merge_subagent_history(
        self,
        existing: list[dict[str, object]],
        latest: list[dict[str, object]],
        *,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        merged: dict[str, dict[str, object]] = {}
        order: list[str] = []
        for item in [*existing, *latest]:
            event_type = str(item.get("event_type", "event"))
            job_id = str(item.get("job_id", item.get("subagent_job_id", "job")))
            timestamp = str(item.get("timestamp", ""))
            key = f"{job_id}:{event_type}:{timestamp}"
            merged[key] = dict(item)
            if key not in order:
                order.append(key)
        ordered = sorted(
            (merged[key] for key in order),
            key=lambda item: str(item.get("timestamp", "")),
        )
        return ordered[-limit:]

    def _normalize_execution_mode(self, value: ThreadExecutionMode | str) -> ThreadExecutionMode:
        if isinstance(value, ThreadExecutionMode):
            return value
        try:
            return ThreadExecutionMode(str(value))
        except ValueError:
            return ThreadExecutionMode.AGENT

    def _sync_output_artifacts(
        self,
        state: ThreadState,
        *,
        path_service: PathService,
    ) -> list[str]:
        current_outputs = path_service.list_artifact_relative_paths(state.identity.thread_id, "outputs")
        existing = list(state.artifacts.output_artifacts)
        merged = list(dict.fromkeys([*existing, *current_outputs]))
        state.artifacts.output_artifacts = merged
        return [relative_path for relative_path in current_outputs if relative_path not in existing]


class _GraphRunEventAdapter:
    _HIDDEN_TOOL_NAMES = frozenset(
        {
            "delegate_batch",
            "delegate_cancel",
            "delegate_status",
            "delegated_task",
            "memory",
            "memory_trace",
            "session_search",
            "subagent",
        }
    )
    _HIDDEN_TOOL_CAPABILITY_GROUPS = frozenset({"memory"})

    def __init__(
        self,
        *,
        thread_id: str,
        run_id: str | None = None,
        execution_mode: ThreadExecutionMode,
        tool_registry: ToolRegistry,
        existing_steps: list[dict[str, object]] | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.execution_mode = execution_mode
        self._tool_metadata_by_name = {
            entry.name: entry for entry in tool_registry.entries()
        }
        self._opened_message_ids: set[str] = set()
        self._opened_message_order: list[str] = []
        self._opened_reasoning_ids: set[str] = set()
        self._opened_hidden_reasoning_ids: set[str] = set()
        self._started_tool_call_keys: set[str] = set()
        self._tool_activity_by_key: dict[str, RecentToolActivity] = {}
        self._tool_activity_order: list[str] = []
        self._message_roles: dict[str, str] = {}
        self._message_raw_text_by_id: dict[str, str] = {}
        self._message_text_by_id: dict[str, str] = {}
        self._message_reasoning_by_id: dict[str, list[str]] = {}
        self._pending_reasoning_text_by_id: dict[str, str] = {}
        self._reasoning_started_at_by_id: dict[str, datetime] = {}
        self._reasoning_completed_at_by_id: dict[str, datetime] = {}
        self._steps_by_id: dict[str, dict[str, object]] = {}
        self._step_order: list[str] = []
        self._next_step_order = 0
        self._reasoning_step_by_message_id: dict[str, str] = {}
        self._hidden_reasoning_step_by_message_id: dict[str, str] = {}
        self._content_step_by_message_id: dict[str, str] = {}
        self._tool_step_by_activity_key: dict[str, str] = {}
        self._subagent_step_by_task_id: dict[str, str] = {}
        self._subagent_message_by_task_id: dict[str, str] = {}
        self._summary_sent_by_message_id: set[str] = set()
        self._load_existing_steps(existing_steps or [])

    def _load_existing_steps(self, steps: list[dict[str, object]]) -> None:
        for raw_step in steps:
            if not isinstance(raw_step, dict):
                continue
            step_id = str(raw_step.get("step_id") or "")
            message_id = str(raw_step.get("message_id") or "")
            if not step_id or not message_id:
                continue
            step = self._normalize_step_record(raw_step)
            self._steps_by_id[step_id] = step
            self._step_order.append(step_id)
            order = step.get("order")
            if isinstance(order, int):
                self._next_step_order = max(self._next_step_order, order + 1)

    def handle_message_stream(self, payload) -> list[RunEvent]:
        events: list[RunEvent] = []
        if not isinstance(payload, tuple) or len(payload) != 2:
            return events

        message, metadata = payload
        if self._is_internal_stream_metadata(metadata):
            return events
        message_id = getattr(message, "id", None) or f"stream-{len(self._opened_message_ids)}"
        role = self._normalize_role(getattr(message, "type", None) or message.__class__.__name__)

        if isinstance(message, ToolMessage):
            tool_call_id = message.tool_call_id
            marked = self._mark_tool_completed(
                tool_call_id=tool_call_id,
                name=message.name,
                result_text=str(message.content),
                status=getattr(message, "status", None) or "completed",
            )
            if marked is None:
                return events
            activity_key, activity = marked
            events.extend(self._update_tool_step_from_activity(activity_key, activity))
            events.extend(self._handle_special_tool_completion(message))
            return events

        if message_id not in self._opened_message_ids:
            self._opened_message_ids.add(message_id)
            self._opened_message_order.append(message_id)
            self._message_roles[message_id] = role

        content_source = getattr(message, "content", "")
        if isinstance(message, AIMessage) and "content_blocks" in message.additional_kwargs:
            content_source = message.additional_kwargs["content_blocks"]
        text_content, content_blocks = normalize_message_content(content_source)
        if content_blocks:
            text_content = "".join(
                block["text"]
                for block in content_blocks
                if block.get("type") == "text" and block.get("text")
            )
        ai_tool_calls = getattr(message, "tool_calls", None) or [] if isinstance(message, AIMessage) else []

        reasoning_chunks = [block["text"] for block in content_blocks if block.get("type") == "thinking" and block.get("text")]
        if reasoning_chunks:
            now = utc_now()
            self._reasoning_started_at_by_id.setdefault(message_id, now)
            self._reasoning_completed_at_by_id[message_id] = now
            for chunk in reasoning_chunks:
                events.extend(
                    self._append_hidden_reasoning_step_delta(
                        message_id,
                        chunk,
                        title="Provider reasoning",
                    )
                )

        internal_orchestration_text = bool(text_content) and self._is_delegation_orchestration_text(text_content)
        internal_tool_planning = bool(ai_tool_calls) and self._tool_calls_are_chat_hidden(ai_tool_calls)
        if text_content and isinstance(message, AIMessage) and ai_tool_calls:
            if internal_orchestration_text or internal_tool_planning:
                events.extend(
                    self._append_hidden_reasoning_step_delta(
                        message_id,
                        text_content,
                        title="已处理内部能力",
                    )
                )
            else:
                self._message_reasoning_by_id.setdefault(message_id, []).append(text_content)
                events.extend(self._record_reasoning_step_text(message_id, text_content))
        elif isinstance(content_source, str) or text_content:
            if internal_orchestration_text:
                events.extend(self._append_hidden_reasoning_step_delta(message_id, text_content))
            else:
                visible_delta = self._visible_text_delta(message_id, content_source if isinstance(content_source, str) else text_content)
                if visible_delta:
                    events.extend(self._emit_summary_before_content(message_id))
                    events.extend(self._append_content_step_delta(message_id, visible_delta))

        if isinstance(message, AIMessage):
            tool_calls = ai_tool_calls
            if tool_calls:
                events.extend(
                    self._reclassify_content_steps_as_thinking(
                        reason="tool_planning",
                        visibility="hidden" if internal_tool_planning else "chat",
                    )
                )
            for tool_index, tool_call in enumerate(tool_calls):
                normalized_name = self._normalize_tool_name(tool_call.get("name"))
                if normalized_name is None:
                    continue
                tool_call_id = tool_call.get("id")
                activity_key = self._tool_activity_key(
                    tool_call_id=tool_call_id,
                    message_id=message_id,
                    name=normalized_name,
                    ordinal=tool_index,
                )
                if activity_key in self._started_tool_call_keys:
                    continue
                self._started_tool_call_keys.add(activity_key)
                activity_key, activity = self._mark_tool_started(
                    tool_call_id=tool_call_id,
                    message_id=message_id,
                    name=normalized_name,
                    args=tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {},
                    ordinal=tool_index,
                )
                events.extend(self._start_tool_step(activity_key, activity))
                if normalized_name == "extract_document":
                    events.append(
                        RunEvent(
                            event="document_ingestion_started",
                            data={
                                "thread_id": self.thread_id,
                                "tool_call_id": tool_call_id,
                                "path": tool_call.get("args", {}).get("path") if isinstance(tool_call.get("args"), dict) else None,
                            },
                        )
                    )
                if normalized_name == "export_document":
                    events.append(
                        RunEvent(
                            event="document_export_started",
                            data={
                                "thread_id": self.thread_id,
                                "tool_call_id": tool_call_id,
                                "output_path": tool_call.get("args", {}).get("output_path") if isinstance(tool_call.get("args"), dict) else None,
                            },
                        )
                    )

        return events

    def _handle_special_tool_completion(self, message: ToolMessage) -> list[RunEvent]:
        events: list[RunEvent] = []
        tool_name = message.name or ""
        content = str(message.content)

        if tool_name == "delegated_task":
            if content.startswith("SUBAGENT_TASK:"):
                task_id = content.split(":", 1)[1]
                events.append(
                    RunEvent(
                        event="subagent_submitted",
                        data={
                            "thread_id": self.thread_id,
                            "task_id": task_id,
                            "status": "queued",
                        },
                    )
                )
            return events

        if content.startswith("SUBAGENT_TASK:"):
            task_id = content.split(":", 1)[1]
            events.append(
                RunEvent(
                    event="subagent_submitted",
                    data={
                        "thread_id": self.thread_id,
                        "task_id": task_id,
                        "status": "queued",
                    },
                )
            )
            return events

        if tool_name not in {"run_command", "process"}:
            if tool_name == "extract_document":
                try:
                    payload = json.loads(content)
                except Exception:
                    payload = {}
                if isinstance(payload, dict):
                    events.append(
                        RunEvent(
                            event="document_ingestion_completed",
                            data={
                                "thread_id": self.thread_id,
                                "path": payload.get("path"),
                                "content_path": payload.get("content_path"),
                                "provider": payload.get("provider"),
                                "ocr_provider": payload.get("ocr_provider"),
                                "diagnostics": payload.get("diagnostics", []),
                            },
                        )
                    )
                return events
            if tool_name == "export_document":
                try:
                    payload = json.loads(content)
                except Exception:
                    payload = {}
                if isinstance(payload, dict):
                    warnings = payload.get("warnings", [])
                    cleaned = payload.get("cleaned_scratch_paths", [])
                    events.append(
                        RunEvent(
                            event="document_export_completed",
                            data={
                                "thread_id": self.thread_id,
                                "output_path": payload.get("output_path"),
                                "mode": payload.get("mode"),
                                "format": payload.get("format"),
                                "provider": payload.get("provider"),
                                "warnings": warnings if isinstance(warnings, list) else [],
                            },
                        )
                    )
                    if isinstance(cleaned, list) and cleaned:
                        events.append(
                            RunEvent(
                                event="cleanup_scratch",
                                data={
                                    "thread_id": self.thread_id,
                                    "paths": cleaned,
                                },
                            )
                        )
                    if isinstance(warnings, list):
                        for warning in warnings:
                            events.append(
                                RunEvent(
                                    event="run_warning",
                                    data={
                                        "thread_id": self.thread_id,
                                        "message": str(warning),
                                        "source": "export_document",
                                    },
                                )
                            )
                return events
            return events

        try:
            payload = json.loads(content)
        except Exception:
            return events

        if isinstance(payload, dict) and payload.get("session_id"):
            event_name = "process_started"
            status = str(payload.get("status", "running"))
            if status not in {"running", "queued"}:
                event_name = "process_completed"
            events.append(
                RunEvent(
                    event=event_name,
                    data={
                        "thread_id": self.thread_id,
                        "session_id": str(payload.get("session_id")),
                        "status": status,
                        "command": payload.get("command"),
                        "backend": payload.get("backend"),
                        "backend_id": payload.get("backend_id"),
                        "backend_label": payload.get("backend_label"),
                        "exit_code": payload.get("exit_code"),
                        "output": payload.get("output"),
                    },
                )
            )
        elif isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict) or "session_id" not in item:
                    continue
                events.append(
                    RunEvent(
                        event="process_snapshot",
                        data={
                            "thread_id": self.thread_id,
                            "session_id": str(item.get("session_id")),
                            "status": str(item.get("status", "running")),
                            "command": item.get("command"),
                            "backend": item.get("backend"),
                            "backend_id": item.get("backend_id"),
                            "backend_label": item.get("backend_label"),
                        },
                    )
                )
        return events

    def handle_update_stream(self, payload: dict) -> list[RunEvent]:
        events: list[RunEvent] = []
        if not isinstance(payload, dict):
            return events

        approval_update = payload.get("GuardrailMiddleware.after_model") or payload.get("ApprovalMiddleware.after_model")
        if isinstance(approval_update, dict) and approval_update.get("approval_request"):
            approval_request = approval_update["approval_request"]
            if isinstance(approval_request, dict):
                decision = approval_update.get("pending_approval", "needs_user_approval")
                if hasattr(decision, "value"):
                    decision = decision.value
                events.append(
                    RunEvent(
                        event="approval_requested",
                        data={
                            "thread_id": self.thread_id,
                            "execution_mode": self.execution_mode.value,
                            "decision": str(decision),
                            "request_id": approval_request.get("request_id"),
                            "reason": approval_request.get("reason"),
                            "action_kind": approval_request.get("action_kind"),
                            "requested_permissions": approval_request.get("requested_permissions", []),
                            "scope_options": list(approval_request.get("scope_options", ())),
                        },
                    )
                )
                marked = self._mark_latest_inflight_tool(status="pending_approval")
                if marked is not None:
                    activity_key, activity = marked
                    events.extend(self._update_tool_step_from_activity(activity_key, activity))

        tools_update = payload.get("tools")
        if isinstance(tools_update, dict):
            tool_messages = tools_update.get("messages")
            if isinstance(tool_messages, list):
                for message in tool_messages:
                    if not isinstance(message, ToolMessage):
                        continue
                    marked = self._mark_tool_progress(
                        tool_call_id=message.tool_call_id,
                        name=message.name,
                    )
                    if marked is None:
                        continue
                    activity_key, activity = marked
                    events.extend(self._append_tool_step_delta(activity_key, activity, str(message.content)))

        return events

    def handle_subagent_event(self, item) -> list[RunEvent]:
        event_type = str(getattr(getattr(item, "event_type", None), "value", getattr(item, "event_type", "")))
        task_id = str(getattr(item, "job_id", "") or "")
        if not task_id:
            return []
        payload = dict(getattr(item, "payload", {}) or {})
        message_id = self._subagent_message_by_task_id.get(task_id)
        if message_id is None:
            message_id = self._latest_assistant_message_id() or f"{self.thread_id}:subagents"
            self._subagent_message_by_task_id[task_id] = message_id
        step_id = self._subagent_step_by_task_id.get(task_id) or f"{message_id}:subagent:{task_id}"
        self._subagent_step_by_task_id[task_id] = step_id
        prompt_preview = str(payload.get("prompt_preview") or payload.get("prompt") or task_id)
        title = self._subagent_title(event_type=event_type, prompt_preview=prompt_preview)
        status = self._subagent_step_status(event_type=event_type, raw_status=str(payload.get("status") or ""))
        metadata = {
            "subagent_task_id": task_id,
            "batch_id": payload.get("batch_id"),
            "child_thread_id": payload.get("child_thread_id"),
            "child_run_id": payload.get("child_run_id"),
            "event_type": event_type,
            "prompt_preview": prompt_preview,
            "prompt": payload.get("prompt") or prompt_preview,
        }
        step, created = self._create_step(
            step_id=step_id,
            message_id=message_id,
            step_type="call",
            title=title,
            action=str(payload.get("prompt") or prompt_preview),
            status=status,
            language="text",
            tool_name="subagent",
            tool_call_id=task_id,
            metadata=metadata,
        )
        started_at = self._parse_datetime(payload.get("started_at"))
        if started_at is not None:
            step["started_at"] = started_at
        if created:
            events = [self._step_started_event(step)]
        else:
            events = []
        delta = self._subagent_event_delta(event_type=event_type, payload=payload)
        if delta:
            step["payload"] = f"{step.get('payload') or ''}{delta}"
            events.append(self._step_delta_event(step, delta))
        if status in {"success", "error"}:
            completed_at = self._parse_datetime(payload.get("completed_at")) or getattr(item, "timestamp", None) or utc_now()
            started = step.get("started_at")
            duration_ms = None
            if isinstance(started, datetime) and isinstance(completed_at, datetime):
                duration_ms = max(int((completed_at - started).total_seconds() * 1000), 0)
            summary = str(payload.get("summary") or "").strip()
            error = str(payload.get("error") or "").strip()
            terminal_payload = summary or error
            if terminal_payload:
                step["payload"] = terminal_payload
            step.update(
                {
                    "title": title,
                    "status": status,
                    "completed_at": completed_at,
                    "duration_ms": duration_ms,
                    "duration": self._format_duration(duration_ms),
                    "error": error or None if status == "error" else None,
                    "metadata": {**dict(step.get("metadata") or {}), **metadata},
                }
            )
            events.append(self._step_updated_event(step))
        elif not created:
            step.update({"title": title, "status": status, "metadata": {**dict(step.get("metadata") or {}), **metadata}})
            events.append(self._step_updated_event(step))
        return events

    def handle_subagent_task_snapshot(self, *, task, result=None) -> list[RunEvent]:
        task_id = str(getattr(task, "task_id", "") or "")
        if not task_id:
            return []
        status_value = str(getattr(getattr(task, "status", None), "value", getattr(task, "status", "")) or "")
        terminal_success = status_value == "completed"
        terminal_error = status_value in {"failed", "cancelled", "timed_out", "interrupted", "failed_recovery"}
        if not terminal_success and not terminal_error:
            return []

        message_id = self._subagent_message_by_task_id.get(task_id)
        if message_id is None:
            message_id = self._latest_assistant_message_id() or f"{self.thread_id}:subagents"
            self._subagent_message_by_task_id[task_id] = message_id
        step_id = self._subagent_step_by_task_id.get(task_id) or f"{message_id}:subagent:{task_id}"
        self._subagent_step_by_task_id[task_id] = step_id
        prompt_preview = str(getattr(task, "prompt_preview", "") or task_id)
        event_type = "job_completed" if terminal_success else "job_failed"
        title = self._subagent_title(event_type=event_type, prompt_preview=prompt_preview)
        summary = str(getattr(result, "summary", "") or "").strip() if result is not None else ""
        error = str(getattr(task, "error", "") or getattr(result, "error", "") or "").strip() if result is not None else str(getattr(task, "error", "") or "").strip()
        status = "success" if terminal_success else "error"
        metadata = {
            "subagent_task_id": task_id,
            "batch_id": getattr(task, "batch_id", None),
            "child_thread_id": getattr(result, "child_thread_id", None) if result is not None else getattr(task, "child_thread_id", None),
            "child_run_id": getattr(result, "child_run_id", None) if result is not None else getattr(task, "child_run_id", None),
            "event_type": event_type,
            "prompt_preview": prompt_preview,
            "prompt": prompt_preview,
        }
        step, created = self._create_step(
            step_id=step_id,
            message_id=message_id,
            step_type="call",
            title=title,
            action=prompt_preview,
            status=status,
            language="text",
            tool_name="subagent",
            tool_call_id=task_id,
            metadata=metadata,
        )
        completed_at = getattr(task, "completed_at", None) or utc_now()
        started = getattr(task, "started_at", None) or step.get("started_at")
        duration_ms = None
        if isinstance(started, datetime) and isinstance(completed_at, datetime):
            duration_ms = max(int((completed_at - started).total_seconds() * 1000), 0)
        step.update(
            {
                "title": title,
                "status": status,
                "payload": summary or error or str(step.get("payload") or ""),
                "completed_at": completed_at,
                "duration_ms": duration_ms,
                "duration": self._format_duration(duration_ms),
                "error": error or None if status == "error" else None,
                "metadata": {**dict(step.get("metadata") or {}), **metadata},
            }
        )
        return [self._step_started_event(step)] if created else [self._step_updated_event(step)]

    def _latest_assistant_message_id(self) -> str | None:
        for message_id in reversed(self._opened_message_order):
            if self._message_roles.get(message_id) in {"ai", "assistant"}:
                return message_id
        return None

    def _subagent_step_status(self, *, event_type: str, raw_status: str) -> str:
        terminal_success = {"job_completed"}
        terminal_error = {"job_failed", "job_cancelled", "job_timed_out", "job_interrupted"}
        if event_type in terminal_success or raw_status == "completed":
            return "success"
        if event_type in terminal_error or raw_status in {"failed", "cancelled", "timed_out", "interrupted", "failed_recovery"}:
            return "error"
        return "running"

    def _subagent_title(self, *, event_type: str, prompt_preview: str) -> str:
        preview = self._truncate_inline(prompt_preview, limit=72)
        if event_type == "job_completed":
            return f"已完成子代理 {preview}"
        if event_type == "job_failed":
            return f"子代理失败 {preview}"
        if event_type == "job_cancelled":
            return f"已取消子代理 {preview}"
        if event_type == "job_timed_out":
            return f"子代理超时 {preview}"
        if event_type == "job_interrupted":
            return f"子代理中断 {preview}"
        return f"正在运行子代理 {preview}"

    def _subagent_event_delta(self, *, event_type: str, payload: dict[str, object]) -> str:
        if event_type == "tool_call":
            tool_name = str(payload.get("tool_name") or payload.get("name") or "tool")
            return f"调用子工具 {tool_name}\n"
        if event_type == "tool_result":
            tool_name = str(payload.get("tool_name") or payload.get("name") or "tool")
            status = str(payload.get("status") or "completed")
            return f"子工具 {tool_name} {status}\n"
        return ""

    def _truncate_inline(self, value: str, *, limit: int) -> str:
        normalized = " ".join(str(value or "").split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 1]}…"

    def _parse_datetime(self, value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def mark_run_failed(self, error: str) -> None:
        for key, activity in self._tool_activity_by_key.items():
            if activity.completed_at is not None:
                continue
            activity.status = "error"
            activity.result_text = activity.result_text or error
            activity.completed_at = utc_now()
            if activity.started_at is not None:
                activity.duration_ms = max(
                    int((activity.completed_at - activity.started_at).total_seconds() * 1000),
                    0,
                )
            step_id = self._tool_step_by_activity_key.get(key)
            if step_id and step_id in self._steps_by_id:
                step = self._steps_by_id[step_id]
                step.update(
                    {
                        "status": "error",
                        "payload": step.get("payload") or error,
                        "duration_ms": activity.duration_ms,
                        "duration": self._format_duration(activity.duration_ms),
                        "completed_at": activity.completed_at,
                        "error": error,
                    }
                )

    def snapshot_recent_tool_activity(self, *, limit: int = 20) -> list[RecentToolActivity]:
        ordered = sorted(
            (self._tool_activity_by_key[key].model_copy(deep=True) for key in self._tool_activity_order),
            key=lambda item: (
                item.completed_at or item.started_at or datetime.min.replace(tzinfo=timezone.utc),
                item.started_at or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        return ordered[:limit]

    def _normalize_step_record(self, raw_step: dict[str, object]) -> dict[str, object]:
        step_id = str(raw_step.get("step_id") or "")
        message_id = str(raw_step.get("message_id") or "")
        step_type = str(raw_step.get("type") or "content")
        if step_type not in {"thinking", "call", "content"}:
            step_type = "content"
        status = str(raw_step.get("status") or "success")
        if status not in {"pending", "running", "success", "error"}:
            status = "success" if status in {"completed", "complete"} else "running"
        language = str(raw_step.get("language") or "text")
        if language not in {"shell", "json", "markdown", "text"}:
            language = "text"
        return {
            "step_id": step_id,
            "message_id": message_id,
            "type": step_type,
            "title": str(raw_step.get("title") or ""),
            "action": str(raw_step["action"]) if raw_step.get("action") is not None else None,
            "status": status,
            "duration": str(raw_step["duration"]) if raw_step.get("duration") is not None else None,
            "duration_ms": int(raw_step["duration_ms"]) if isinstance(raw_step.get("duration_ms"), int) else None,
            "payload": str(raw_step.get("payload") or ""),
            "language": language,
            "tool_name": str(raw_step["tool_name"]) if raw_step.get("tool_name") is not None else None,
            "tool_call_id": str(raw_step["tool_call_id"]) if raw_step.get("tool_call_id") is not None else None,
            "order": int(raw_step["order"]) if isinstance(raw_step.get("order"), int) else self._next_step_order,
            "started_at": raw_step.get("started_at"),
            "completed_at": raw_step.get("completed_at"),
            "error": str(raw_step["error"]) if raw_step.get("error") is not None else None,
            "metadata": dict(raw_step.get("metadata") or {}) if isinstance(raw_step.get("metadata"), dict) else {},
            "visibility": str(raw_step.get("visibility") or "chat"),
        }

    def _is_internal_stream_metadata(self, metadata: object) -> bool:
        if not isinstance(metadata, dict):
            return False
        direct_metadata = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
        tags = metadata.get("tags") or metadata.get("ls_tags") or direct_metadata.get("tags")
        if isinstance(tags, (list, tuple, set)):
            tag_values = {str(tag) for tag in tags}
            if any(tag == "anvil_internal_title" or tag.startswith("anvil_internal_") for tag in tag_values):
                return True
        if metadata.get("anvil_internal") is True or direct_metadata.get("anvil_internal") is True:
            return True
        internal_kind = direct_metadata.get("anvil_internal_kind") or metadata.get("anvil_internal_kind")
        if isinstance(internal_kind, str) and internal_kind.startswith(("memory_", "title_", "summary_")):
            return True
        text = " ".join(
            str(value)
            for value in (
                metadata.get("langgraph_node"),
                metadata.get("langgraph_checkpoint_ns"),
                metadata.get("checkpoint_ns"),
                metadata.get("langgraph_path"),
                internal_kind,
            )
            if value is not None
        )
        return (
            "TitleMiddleware" in text
            or "anvil_internal_title" in text
            or "memory_rerank" in text
            or "memory_platform" in text
        )

    def _is_delegation_orchestration_text(self, content: object) -> bool:
        if not isinstance(content, str):
            return False
        text = content.strip()
        if not text:
            return False
        markers = (
            "batch 格式",
            "Agent 已成功启动",
            "已成功启动",
            "让我等待",
            "让我先列出当前活跃",
            "需要指定 task_id",
            "现在开始并行委托",
            "改用单独委托",
            "[LOOP DETECTED]",
        )
        return any(marker in text for marker in markers)

    def _tool_calls_are_chat_hidden(self, tool_calls: object) -> bool:
        if not isinstance(tool_calls, list) or not tool_calls:
            return False
        saw_tool = False
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                return False
            name = self._normalize_tool_name(tool_call.get("name"))
            if name is None:
                return False
            saw_tool = True
            if not self._tool_name_is_chat_hidden(name):
                return False
        return saw_tool

    def _tool_name_is_chat_hidden(self, name: str) -> bool:
        if name in self._HIDDEN_TOOL_NAMES:
            return True
        entry = self._tool_metadata_by_name.get(name)
        if entry is None:
            return False
        return (entry.capability_group or "").strip() in self._HIDDEN_TOOL_CAPABILITY_GROUPS

    def _create_step(
        self,
        *,
        step_id: str,
        message_id: str,
        step_type: str,
        title: str,
        status: str,
        language: str,
        action: str | None = None,
        payload: str = "",
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        metadata: dict[str, object] | None = None,
        visibility: str = "chat",
    ) -> tuple[dict[str, object], bool]:
        existing = self._steps_by_id.get(step_id)
        if existing is not None:
            existing.update(
                {
                    "message_id": message_id,
                    "type": step_type,
                    "title": title or str(existing.get("title") or ""),
                    "action": action if action is not None else existing.get("action"),
                    "status": status,
                    "language": language,
                    "tool_name": tool_name if tool_name is not None else existing.get("tool_name"),
                    "tool_call_id": tool_call_id if tool_call_id is not None else existing.get("tool_call_id"),
                    "metadata": self._step_metadata(existing.get("metadata"), metadata),
                    "visibility": visibility or str(existing.get("visibility") or "chat"),
                }
            )
            return existing, False
        step = {
            "step_id": step_id,
            "message_id": message_id,
            "type": step_type,
            "title": title,
            "action": action,
            "status": status,
            "duration": None,
            "duration_ms": None,
            "payload": payload,
            "language": language,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "order": self._next_step_order,
            "started_at": utc_now(),
            "completed_at": None,
            "error": None,
            "metadata": self._step_metadata(None, metadata),
            "visibility": visibility,
        }
        self._next_step_order += 1
        self._steps_by_id[step_id] = step
        self._step_order.append(step_id)
        return step, True

    def _step_metadata(self, existing: object, metadata: dict[str, object] | None) -> dict[str, object]:
        merged = dict(existing or {}) if isinstance(existing, dict) else {}
        if self.run_id:
            merged["run_id"] = self.run_id
        merged.update(dict(metadata or {}))
        return merged

    def _step_to_public(self, step: dict[str, object]) -> dict[str, object]:
        payload = self._normalize_step_record(step)
        payload["started_at"] = self._datetime_to_iso(payload.get("started_at"))
        payload["completed_at"] = self._datetime_to_iso(payload.get("completed_at"))
        return payload

    def _datetime_to_iso(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _step_started_event(self, step: dict[str, object]) -> RunEvent:
        return RunEvent(
            event="step_started",
            data={
                "thread_id": self.thread_id,
                "message_id": step["message_id"],
                "step": self._step_to_public(step),
            },
        )

    def _step_delta_event(self, step: dict[str, object], delta: str) -> RunEvent:
        return RunEvent(
            event="step_delta",
            data={
                "thread_id": self.thread_id,
                "message_id": step["message_id"],
                "step_id": step["step_id"],
                "payload_delta": delta,
            },
        )

    def _step_updated_event(self, step: dict[str, object]) -> RunEvent:
        return RunEvent(
            event="step_updated",
            data={
                "thread_id": self.thread_id,
                "message_id": step["message_id"],
                "step": self._step_to_public(step),
            },
        )

    def _record_reasoning_step_text(self, message_id: str, text: str) -> list[RunEvent]:
        step_id = self._reasoning_step_by_message_id.get(message_id)
        if step_id is None or not self._can_append_to_reasoning_step(message_id=message_id, step_id=step_id):
            step_id = self._next_reasoning_step_id(message_id)
        self._reasoning_step_by_message_id[message_id] = step_id
        self._opened_reasoning_ids.add(message_id)
        now = utc_now()
        self._reasoning_started_at_by_id.setdefault(message_id, now)
        self._reasoning_completed_at_by_id[message_id] = now
        step, created = self._create_step(
            step_id=step_id,
            message_id=message_id,
            step_type="thinking",
            title="Analyzing...",
            status="running",
            language="text",
        )
        merged_payload, _event_delta = _merge_stream_payload(str(step.get("payload") or ""), text)
        step["payload"] = merged_payload
        self._pending_reasoning_text_by_id[step_id] = merged_payload
        return [self._step_started_event(step)] if created else []

    def _next_reasoning_step_id(self, message_id: str) -> str:
        base_id = f"{message_id}:thinking"
        if base_id not in self._steps_by_id:
            return base_id
        index = 2
        while f"{base_id}:{index}" in self._steps_by_id:
            index += 1
        return f"{base_id}:{index}"

    def _can_append_to_reasoning_step(self, *, message_id: str, step_id: str) -> bool:
        step = self._steps_by_id.get(step_id)
        if step is None or step.get("type") != "thinking" or step.get("visibility") == "hidden":
            return False
        seen_current = False
        for ordered_step_id in self._step_order:
            if ordered_step_id == step_id:
                seen_current = True
                continue
            if not seen_current:
                continue
            later = self._steps_by_id.get(ordered_step_id)
            if later is None or later.get("message_id") != message_id:
                continue
            if later.get("visibility") == "hidden":
                continue
            return False
        return True

    def _append_hidden_reasoning_step_delta(self, message_id: str, delta: str, *, title: str = "已处理委托编排") -> list[RunEvent]:
        step_id = self._hidden_reasoning_step_by_message_id.get(message_id) or f"{message_id}:thinking:hidden"
        self._hidden_reasoning_step_by_message_id[message_id] = step_id
        self._opened_hidden_reasoning_ids.add(message_id)
        step, created = self._create_step(
            step_id=step_id,
            message_id=message_id,
            step_type="thinking",
            title=title,
            status="running",
            language="text",
            visibility="hidden",
        )
        events = [self._step_started_event(step)] if created else []
        merged_payload, event_delta = _merge_stream_payload(str(step.get("payload") or ""), delta)
        if not event_delta:
            return events
        step["payload"] = merged_payload
        events.append(self._step_delta_event(step, event_delta))
        return events

    def _reclassify_content_steps_as_thinking(self, *, reason: str, visibility: str = "chat") -> list[RunEvent]:
        events: list[RunEvent] = []
        for message_id, step_id in list(self._content_step_by_message_id.items()):
            step = self._steps_by_id.get(step_id)
            if step is None or step.get("type") != "content":
                continue
            payload = str(step.get("payload") or "")
            if not payload.strip():
                continue
            step.update(
                {
                    "type": "thinking",
                    "title": "Analyzing...",
                    "language": "text",
                    "metadata": {**dict(step.get("metadata") or {}), "reclassified_from": "content", "reason": reason},
                    "visibility": visibility,
                }
            )
            if visibility == "hidden":
                self._opened_hidden_reasoning_ids.add(message_id)
                self._hidden_reasoning_step_by_message_id[message_id] = step_id
            else:
                self._opened_reasoning_ids.add(message_id)
                self._reasoning_step_by_message_id[message_id] = step_id
                self._message_reasoning_by_id.setdefault(message_id, []).append(payload)
            self._message_text_by_id.pop(message_id, None)
            self._reasoning_started_at_by_id.setdefault(message_id, step.get("started_at") if isinstance(step.get("started_at"), datetime) else utc_now())
            self._reasoning_completed_at_by_id[message_id] = utc_now()
            del self._content_step_by_message_id[message_id]
            events.append(self._step_updated_event(step))
        return events

    def _emit_summary_before_content(self, message_id: str) -> list[RunEvent]:
        if message_id in self._summary_sent_by_message_id:
            return []
        self._summary_sent_by_message_id.add(message_id)
        folded_count = sum(
            1
            for step_id in self._step_order
            if self._steps_by_id[step_id].get("message_id") == message_id
            and self._steps_by_id[step_id].get("type") != "content"
        )
        return [
            RunEvent(
                event="summary_update",
                data={
                    "thread_id": self.thread_id,
                    "message_id": message_id,
                    "title": f"已运行 {folded_count} 条消息" if folded_count else "准备最终回答",
                    "folded_step_count": folded_count,
                },
            )
        ]

    def _append_content_step_delta(
        self,
        message_id: str,
        delta: str,
        *,
        visibility: str = "chat",
    ) -> list[RunEvent]:
        step_id = self._content_step_by_message_id.get(message_id) or f"{message_id}:content"
        self._content_step_by_message_id[message_id] = step_id
        step, created = self._create_step(
            step_id=step_id,
            message_id=message_id,
            step_type="content",
            title="最终回答",
            status="running",
            language="markdown",
            visibility=visibility,
        )
        events = [self._step_started_event(step)] if created else []
        merged_payload, event_delta = _merge_stream_payload(str(step.get("payload") or ""), delta)
        if not event_delta:
            return events
        step["payload"] = merged_payload
        events.append(self._step_delta_event(step, event_delta))
        return events

    def _visible_text_delta(self, message_id: str, raw_delta: str) -> str:
        raw_text = f"{self._message_raw_text_by_id.get(message_id, '')}{raw_delta}"
        self._message_raw_text_by_id[message_id] = raw_text
        visible_text = strip_inline_thinking_tags(raw_text)
        previous = self._message_text_by_id.get(message_id, "")
        self._message_text_by_id[message_id] = visible_text
        if not visible_text:
            return ""
        if visible_text.startswith(previous):
            return visible_text[len(previous) :]
        return visible_text

    def _start_tool_step(self, activity_key: str, activity: RecentToolActivity) -> list[RunEvent]:
        message_id = activity.message_id or activity_key
        step_id = self._tool_step_by_activity_key.get(activity_key) or f"{message_id}:call:{activity_key}"
        self._tool_step_by_activity_key[activity_key] = step_id
        action = self._tool_step_action(activity)
        step, created = self._create_step(
            step_id=step_id,
            message_id=message_id,
            step_type="call",
            title=self._tool_step_title(activity),
            action=action,
            status="running",
            language=self._tool_step_language(activity, action),
            tool_name=activity.name,
            tool_call_id=activity.tool_call_id,
            metadata=self._tool_step_metadata(activity),
            visibility=self._tool_step_visibility(activity),
        )
        if activity.started_at is not None:
            step["started_at"] = activity.started_at
        return [self._step_started_event(step)] if created else [self._step_updated_event(step)]

    def _append_tool_step_delta(self, activity_key: str, activity: RecentToolActivity, delta: str) -> list[RunEvent]:
        events = []
        if activity_key not in self._tool_step_by_activity_key:
            events.extend(self._start_tool_step(activity_key, activity))
        step_id = self._tool_step_by_activity_key[activity_key]
        step = self._steps_by_id[step_id]
        merged_payload, event_delta = _merge_stream_payload(str(step.get("payload") or ""), delta, allow_overlap_replay=True)
        if not event_delta:
            return events
        step["status"] = "running"
        step["payload"] = merged_payload
        events.append(self._step_delta_event(step, event_delta))
        return events

    def _update_tool_step_from_activity(self, activity_key: str, activity: RecentToolActivity) -> list[RunEvent]:
        events = []
        if activity_key not in self._tool_step_by_activity_key:
            events.extend(self._start_tool_step(activity_key, activity))
        step_id = self._tool_step_by_activity_key[activity_key]
        step = self._steps_by_id[step_id]
        status = self._step_status_for_tool(activity.status)
        step.update(
            {
                "title": self._tool_step_title(activity),
                "action": self._tool_step_action(activity),
                "status": status,
                "payload": activity.result_text if activity.result_text is not None else step.get("payload") or "",
                "duration_ms": activity.duration_ms,
                "duration": self._format_duration(activity.duration_ms),
                "completed_at": activity.completed_at if status in {"success", "error"} else step.get("completed_at"),
                "error": activity.result_text if status == "error" else None,
                "tool_name": activity.name,
                "tool_call_id": activity.tool_call_id,
                "metadata": self._step_metadata(step.get("metadata"), self._tool_step_metadata(activity)),
                "visibility": self._tool_step_visibility(activity),
            }
        )
        events.append(self._step_updated_event(step))
        return events

    def _tool_step_visibility(self, activity: RecentToolActivity) -> str:
        name = (activity.name or "").strip()
        capability_group = (activity.capability_group or "").strip()
        if name in self._HIDDEN_TOOL_NAMES:
            return "hidden"
        if capability_group in self._HIDDEN_TOOL_CAPABILITY_GROUPS:
            return "hidden"
        return "chat"

    def _tool_step_metadata(self, activity: RecentToolActivity) -> dict[str, object]:
        metadata: dict[str, object] = {"tool_activity": True}
        for key, value in (
            ("display_name", activity.display_name),
            ("source_kind", activity.source_kind),
            ("source_id", activity.source_id),
            ("capability_group", activity.capability_group),
            ("tool_execution_mode", activity.tool_execution_mode),
        ):
            if value is not None:
                metadata[key] = value
        return metadata

    def _complete_reasoning_step(self, message_id: str) -> list[RunEvent]:
        duration_ms = self._reasoning_duration_ms(message_id)
        events: list[RunEvent] = []
        completed_at = self._reasoning_completed_at_by_id.get(message_id) or utc_now()
        for step_id in self._step_order:
            step = self._steps_by_id.get(step_id)
            if (
                step is None
                or step.get("message_id") != message_id
                or step.get("type") != "thinking"
                or step.get("visibility") == "hidden"
            ):
                continue
            step.update(
                {
                    "title": f"已思考 {self._format_duration_zh(duration_ms)}" if duration_ms is not None else "已思考",
                    "status": "success",
                    "payload": self._pending_reasoning_text_by_id.pop(step_id, str(step.get("payload") or "")),
                    "duration_ms": duration_ms,
                    "duration": self._format_duration(duration_ms),
                    "completed_at": completed_at,
                }
            )
            events.append(self._step_updated_event(step))
        return events

    def _complete_hidden_reasoning_step(self, message_id: str) -> list[RunEvent]:
        step_id = self._hidden_reasoning_step_by_message_id.get(message_id)
        if step_id is None or step_id not in self._steps_by_id:
            return []
        step = self._steps_by_id[step_id]
        completed_at = utc_now()
        started_at = step.get("started_at")
        duration_ms = None
        if isinstance(started_at, datetime):
            duration_ms = max(int((completed_at - started_at).total_seconds() * 1000), 0)
        step.update(
            {
                "status": "success",
                "duration_ms": duration_ms,
                "duration": self._format_duration(duration_ms),
                "completed_at": completed_at,
            }
        )
        return [self._step_updated_event(step)]

    def _complete_content_step(self, message_id: str, *, status: str = "success") -> list[RunEvent]:
        step_id = self._content_step_by_message_id.get(message_id)
        if step_id is None or step_id not in self._steps_by_id:
            return []
        step = self._steps_by_id[step_id]
        completed_at = utc_now()
        started_at = step.get("started_at")
        duration_ms = None
        if isinstance(started_at, datetime):
            duration_ms = max(int((completed_at - started_at).total_seconds() * 1000), 0)
        step.update(
            {
                "status": "error" if status == "error" else "success",
                "duration_ms": duration_ms,
                "duration": self._format_duration(duration_ms),
                "completed_at": completed_at,
                "error": "interrupted" if status == "error" else None,
            }
        )
        return [self._step_updated_event(step)]

    def _materialize_interruption_content_step(
        self,
        *,
        message_id: str,
        reason: str,
    ) -> list[RunEvent]:
        if self._content_step_by_message_id.get(message_id):
            return []
        return self._append_content_step_delta(message_id, reason, visibility="developer")

    def apply_step_metadata(self, state: ThreadState) -> None:
        active_message_ids = {
            str(payload.get("id"))
            for payload in state.conversation.messages
            if payload.get("id") is not None
        }
        steps = [
            self._step_to_public(self._steps_by_id[step_id])
            for step_id in self._step_order
            if step_id in self._steps_by_id
            and (
                not active_message_ids
                or str(self._steps_by_id[step_id].get("message_id")) in active_message_ids
            )
        ]
        state.conversation.steps = steps

    def _tool_step_title(self, activity: RecentToolActivity) -> str:
        name = activity.name or "tool"
        args = activity.args or {}
        if name in {"run_command", "process", "bash"}:
            command = str(args.get("command") or args.get("cmd") or "").strip()
            return f"已运行 {command}" if command else "已运行命令"
        if name == "read_file":
            path = str(args.get("path") or args.get("file_path") or "").strip()
            return f"已读取 {path}" if path else "已读取文件"
        if name in {"file_info", "list_dir", "search_files", "glob_files", "grep_files"}:
            path = str(args.get("path") or "").strip()
            return f"已检查 {path}" if path else "已检查文件"
        if name in {"write_file", "patch_file"}:
            path = str(args.get("path") or args.get("file_path") or "").strip()
            return f"已编辑 {path}" if path else "已编辑文件"
        if name in {"tool_catalog", "capability_search"}:
            return "已检索工具能力"
        return f"已运行 {activity.display_name or name}"

    def _tool_step_action(self, activity: RecentToolActivity) -> str | None:
        args = activity.args or {}
        if activity.name in {"run_command", "process", "bash"}:
            command = str(args.get("command") or args.get("cmd") or "").strip()
            return command or None
        if not args:
            return None
        return json.dumps(args, ensure_ascii=False, indent=2, sort_keys=True)

    def _tool_step_language(self, activity: RecentToolActivity, action: str | None) -> str:
        if activity.name in {"run_command", "process", "bash"}:
            return "shell"
        if action and action.lstrip().startswith(("{", "[")):
            return "json"
        return "text"

    def _step_status_for_tool(self, status: str | None) -> str:
        normalized = self._normalize_tool_status(status)
        if normalized == "completed":
            return "success"
        if normalized == "error":
            return "error"
        if normalized in {"pending", "pending_approval", "needs_user_approval"}:
            return "pending"
        return "running"

    def _format_duration(self, duration_ms: int | None) -> str | None:
        if duration_ms is None:
            return None
        total_seconds = max(int(round(duration_ms / 1000)), 0)
        if total_seconds <= 0:
            return "<1s"
        minutes, seconds = divmod(total_seconds, 60)
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _format_duration_zh(self, duration_ms: int | None) -> str:
        if duration_ms is None:
            return ""
        total_seconds = max(int(round(duration_ms / 1000)), 0)
        if total_seconds <= 0:
            return "<1 秒"
        minutes, seconds = divmod(total_seconds, 60)
        if minutes:
            return f"{minutes} 分 {seconds} 秒"
        return f"{seconds} 秒"

    def finalize(self, state: ThreadState) -> list[RunEvent]:
        events: list[RunEvent] = []
        seen_completed: set[str] = set()
        for payload in state.conversation.messages:
            message_id = str(payload.get("id")) if payload.get("id") is not None else None
            if message_id is None:
                continue
            content = payload.get("content")
            if (
                str(payload.get("role") or "") in {"assistant", "ai"}
                and message_id not in self._content_step_by_message_id
                and message_id in self._opened_message_ids
                and isinstance(content, str)
                and content.strip()
            ):
                self._message_text_by_id[message_id] = content
                events.extend(self._emit_summary_before_content(message_id))
                events.extend(self._append_content_step_delta(message_id, content))
            if message_id in self._opened_hidden_reasoning_ids:
                events.extend(self._complete_hidden_reasoning_step(message_id))
            if message_id in self._opened_reasoning_ids:
                events.extend(self._complete_reasoning_step(message_id))
            if (
                str(payload.get("status")) == "interrupted"
                and message_id not in self._content_step_by_message_id
            ):
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                reason = str(
                    metadata.get("empty_final_reason")
                    or state.execution.last_message_interrupted_reason
                    or state.lifecycle.last_error
                    or "The run was interrupted before a final answer was produced."
                )
                if not metadata.get("empty_final_reason"):
                    events.extend(self._materialize_interruption_content_step(message_id=message_id, reason=reason))
            if message_id in self._content_step_by_message_id:
                stream_status = "error" if str(payload.get("status")) == "interrupted" else "success"
                events.extend(self._complete_content_step(message_id, status=stream_status))
            if message_id in self._opened_message_ids and message_id not in seen_completed:
                seen_completed.add(message_id)
                stream_status = "interrupted" if str(payload.get("status")) == "interrupted" else "complete"
                events.append(
                    RunEvent(
                        event="message_completed",
                        data={"thread_id": self.thread_id, "message_id": message_id, "stream_status": stream_status},
                    )
                )
        return events

    def build_interrupted_message(self) -> AIMessage | None:
        if not self._message_text_by_id and not self._message_reasoning_by_id:
            return None
        message_id = self._opened_message_order[-1] if self._opened_message_order else None
        if message_id is None:
            return None
        text = self._message_text_by_id.get(message_id, "")
        reasoning = self._message_reasoning_by_id.get(message_id, [])
        additional_kwargs = {}
        if reasoning:
            additional_kwargs["content_blocks"] = [
                *({"type": "thinking", "text": chunk} for chunk in reasoning),
                *([{"type": "text", "text": text}] if text else []),
            ]
        return AIMessage(content=text, id=message_id, additional_kwargs=additional_kwargs)

    def apply_reasoning_metadata(self, state: ThreadState) -> None:
        for index, payload in enumerate(state.conversation.messages):
            message_id = str(payload.get("id")) if payload.get("id") is not None else f"message-{index}"
            duration_ms = self._reasoning_duration_ms(message_id)
            if duration_ms is not None:
                payload["reasoning_duration_ms"] = duration_ms

    def _reasoning_duration_ms(self, message_id: str) -> int | None:
        started_at = self._reasoning_started_at_by_id.get(message_id)
        if started_at is None:
            return None
        completed_at = self._reasoning_completed_at_by_id.get(message_id) or utc_now()
        return max(int((completed_at - started_at).total_seconds() * 1000), 0)

    def _normalize_role(self, raw_role: str) -> str:
        normalized = raw_role.lower()
        if "ai" in normalized:
            return "ai"
        if "tool" in normalized:
            return "tool"
        if "human" in normalized or "user" in normalized:
            return "human"
        if "system" in normalized:
            return "system"
        return normalized

    def _tool_activity_key(
        self,
        *,
        tool_call_id: str | None,
        message_id: str | None,
        name: object,
        ordinal: int = 0,
    ) -> str:
        if tool_call_id is not None:
            return str(tool_call_id)
        return f"{message_id or 'message'}:{name or 'tool'}:{ordinal}"

    def _mark_tool_started(
        self,
        *,
        tool_call_id: str | None,
        message_id: str | None,
        name: object,
        args: dict[str, object],
        ordinal: int,
    ) -> tuple[str, RecentToolActivity]:
        key = self._tool_activity_key(
            tool_call_id=tool_call_id,
            message_id=message_id,
            name=name,
            ordinal=ordinal,
        )
        activity = self._tool_activity_by_key.get(key)
        if activity is None:
            entry = self._tool_metadata_by_name.get(str(name))
            activity = RecentToolActivity(
                tool_call_id=str(tool_call_id) if tool_call_id is not None else None,
                message_id=message_id,
                name=str(name) if name is not None else None,
                display_name=entry.display_name if entry is not None else None,
                source_kind=entry.source_kind.value if entry is not None else None,
                source_id=entry.source_id if entry is not None else None,
                capability_group=entry.capability_group if entry is not None else None,
                tool_execution_mode=entry.execution_mode.value if entry is not None else None,
                args={str(k): v for k, v in args.items()},
                status="started",
                started_at=utc_now(),
            )
            self._tool_activity_by_key[key] = activity
            self._tool_activity_order.append(key)
            return key, activity
        activity.status = "started"
        activity.args = {str(k): v for k, v in args.items()}
        activity.message_id = message_id or activity.message_id
        return key, activity

    def _mark_tool_progress(
        self,
        *,
        tool_call_id: str | None,
        name: object,
    ) -> tuple[str, RecentToolActivity] | None:
        normalized_name = self._normalize_tool_name(name)
        key = self._find_existing_tool_key(
            tool_call_id=tool_call_id,
            name=normalized_name,
            prefer_inflight=True,
        )
        if key is None and normalized_name is None:
            return None
        key = key or self._tool_activity_key(
            tool_call_id=tool_call_id,
            message_id=None,
            name=normalized_name,
        )
        activity = self._tool_activity_by_key.get(key)
        if activity is None:
            key, activity = self._mark_tool_started(
                tool_call_id=tool_call_id,
                message_id=None,
                name=normalized_name,
                args={},
                ordinal=0,
            )
        if activity.completed_at is not None:
            return None
        activity.status = "running"
        return key, activity

    def _mark_tool_completed(
        self,
        *,
        tool_call_id: str | None,
        name: object,
        result_text: str,
        status: str,
    ) -> tuple[str, RecentToolActivity] | None:
        normalized_name = self._normalize_tool_name(name)
        key = self._find_existing_tool_key(
            tool_call_id=tool_call_id,
            name=normalized_name,
            prefer_inflight=True,
        )
        if key is None and normalized_name is None:
            return None
        key = key or self._tool_activity_key(
            tool_call_id=tool_call_id,
            message_id=None,
            name=normalized_name,
        )
        activity = self._tool_activity_by_key.get(key)
        if activity is None:
            key, activity = self._mark_tool_started(
                tool_call_id=tool_call_id,
                message_id=None,
                name=normalized_name,
                args={},
                ordinal=0,
            )
        activity.status = self._normalize_tool_status(status)
        activity.result_text = result_text
        activity.completed_at = utc_now()
        if activity.started_at is not None:
            activity.duration_ms = max(
                int((activity.completed_at - activity.started_at).total_seconds() * 1000),
                0,
            )
        return key, activity

    def _find_existing_tool_key(
        self,
        *,
        tool_call_id: str | None,
        name: object,
        prefer_inflight: bool = False,
    ) -> str | None:
        if tool_call_id is not None:
            direct_key = str(tool_call_id)
            if direct_key in self._tool_activity_by_key:
                return direct_key
        normalized_name = self._normalize_tool_name(name)
        if normalized_name is None:
            return None
        for key in reversed(self._tool_activity_order):
            activity = self._tool_activity_by_key[key]
            if activity.name != normalized_name:
                continue
            if prefer_inflight and activity.completed_at is not None:
                continue
            return key
        return None

    def _mark_latest_inflight_tool(self, *, status: str) -> tuple[str, RecentToolActivity] | None:
        for key in reversed(self._tool_activity_order):
            activity = self._tool_activity_by_key[key]
            if activity.completed_at is not None:
                continue
            activity.status = self._normalize_tool_status(status)
            return key, activity
        return None

    def _activity_event_data(self, activity_key: str, activity: RecentToolActivity) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "execution_mode": self.execution_mode.value,
            "activity_key": activity_key,
            "message_id": activity.message_id,
            "tool_call_id": activity.tool_call_id,
            "name": activity.name,
            "display_name": activity.display_name,
            "source_kind": activity.source_kind,
            "source_id": activity.source_id,
            "capability_group": activity.capability_group,
            "tool_execution_mode": activity.tool_execution_mode,
            "args": activity.args,
            "status": activity.status,
            "result_text": activity.result_text,
            "started_at": activity.started_at.isoformat() if activity.started_at is not None else None,
            "completed_at": activity.completed_at.isoformat() if activity.completed_at is not None else None,
            "duration_ms": activity.duration_ms,
        }

    def _normalize_tool_status(self, status: str | None) -> str:
        normalized = (status or "completed").lower()
        if normalized in {"success", "completed"}:
            return "completed"
        if normalized in {"error", "failed", "failure"}:
            return "error"
        return normalized

    def _normalize_tool_name(self, name: object) -> str | None:
        if name is None:
            return None
        normalized = str(name).strip()
        return normalized or None


def _first_int(values: dict[str, object], *keys: str) -> int | None:
    for key in keys:
        value = _nested_mapping_get(values, key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return None


def _request_runtime_supports_vision(request: RunRequest) -> bool:
    runtime = getattr(request, "runtime", None)
    route = getattr(runtime, "resolved_route", None)
    capabilities = getattr(route, "capabilities", None)
    return bool(getattr(capabilities, "vision", False))


def _first_float(values: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = _nested_mapping_get(values, key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
    return None


def _nested_mapping_get(values: dict[str, object], key: str) -> object:
    current: object = values
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return min(max(numerator / denominator, 0.0), 1.0)


def _category_percentages(breakdown: dict[str, object], total: int | None) -> dict[str, float]:
    if total is None or total <= 0:
        return {}
    percentages: dict[str, float] = {}
    for key, value in sorted(breakdown.items()):
        if isinstance(value, int) and value > 0:
            percentages[str(key)] = round(min(max(value / total, 0.0), 1.0), 4)
    return percentages


def _dominant_context_category(breakdown: dict[str, object]) -> str | None:
    dominant_key: str | None = None
    dominant_value = 0
    for key, value in breakdown.items():
        if isinstance(value, int) and value > dominant_value:
            dominant_key = str(key)
            dominant_value = value
    return dominant_key


def _estimate_tokens_from_text(value: str | None) -> int | None:
    return _estimate_tokens_from_chars(len(value or ""))


def _estimate_tokens_from_chars(char_count: int) -> int | None:
    if char_count <= 0:
        return None
    return max(1, int((char_count + 3) // 4))


def _empty_context_breakdown() -> dict[str, object]:
    return {
        "context_tokens": None,
        "context_breakdown": {},
        "message_tokens": None,
        "system_tokens": None,
        "tool_schema_tokens": None,
        "skill_tokens": None,
        "memory_tokens": None,
        "project_context_tokens": None,
        "runtime_path_tokens": None,
    }


def _non_negative_difference(total: int | None, used: int | None) -> int | None:
    if total is None or used is None:
        return None
    return max(total - used, 0)


def _approval_context_requests_session_grant(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.lower()
    return (
        "do not ask again" in normalized
        or "don't ask" in normalized
        or "this session" in normalized
        or "本会话" in value
        or "不再询问" in value
    )


def _compute_session_grant_key(
    *,
    tool_name: str | None,
    approval_profile: str | None,
    risk_category: str | None,
    capability_group: str | None,
) -> str | None:
    scope = approval_profile or risk_category or capability_group or tool_name
    return str(scope) if scope else None


def _emit_artifact_events(state: ThreadState) -> list[RunEvent]:
    events: list[RunEvent] = []
    for item in state.artifacts.uploaded_files:
        if not isinstance(item, dict):
            continue
        events.append(
            RunEvent(
                event="artifact_emitted",
                data={
                    "thread_id": state.identity.thread_id,
                    "kind": "upload",
                    "label": item.get("filename"),
                    "artifact_url": item.get("artifact_url"),
                    "virtual_path": item.get("virtual_path"),
                },
            )
        )
    for relative_path in state.artifacts.output_artifacts:
        events.append(
            RunEvent(
                event="artifact_emitted",
                data={
                    "thread_id": state.identity.thread_id,
                    "kind": "output",
                    "label": relative_path,
                    "artifact_url": f"/threads/{state.identity.thread_id}/artifacts/outputs/{relative_path}",
                    "virtual_path": f"/mnt/user-data/outputs/{relative_path}",
                },
            )
        )
    for relative_path in state.artifacts.presented_artifacts:
        events.append(
            RunEvent(
                event="artifact_emitted",
                data={
                    "thread_id": state.identity.thread_id,
                    "kind": "presented",
                    "label": relative_path,
                    "artifact_url": None,
                    "virtual_path": relative_path,
                },
            )
        )
    return events
