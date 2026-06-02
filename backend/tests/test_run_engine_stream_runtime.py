from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from anvil.agents import RecentApprovalEvent, RecentToolActivity, SandboxState, ThreadExecutionMode, ThreadLifecycleStatus, ThreadState
from anvil.config import ConfigLayer, ConfigLayerKind
from anvil.runtime.approvals import ApprovalDecision, ApprovalRequest
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.context_envelope import ContextAssembler, ContextContinuationWindow
from anvil.runtime.runs import InMemoryRunEventLogStore, JsonlRunEventLogStore, RunEngine, RunEvent, RunEventEnvelope, RunRequest, RunSnapshotProjector
from anvil.runtime.runs.events import list_run_event_page
from anvil.runtime.runs.engine import EMPTY_FINAL_ASSISTANT_MESSAGE, _GraphRunEventAdapter, _merge_stream_payload
from anvil.runtime.tool_registry import ToolRegistry, ToolRegistryEntry, ToolSourceKind
from anvil.runtime.store import StoreBackend, create_store
from anvil.sandbox import PathService
from fake_models import BindableFakeMessagesListChatModel


def base_layers() -> list[ConfigLayer]:
    return [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    }
                },
            },
        )
    ]


class StreamingChunkChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "streaming-chunk-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Hello"))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:  # type: ignore[override]
        for part in ("Hel", "lo"):
            chunk = AIMessageChunk(content=part)
            if run_manager is not None:
                run_manager.on_llm_new_token(part, chunk=chunk)
            yield ChatGenerationChunk(message=chunk)


class ManyTinyChunkChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "many-tiny-chunk-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="x" * 24))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:  # type: ignore[override]
        for _ in range(24):
            chunk = AIMessageChunk(content="x")
            if run_manager is not None:
                run_manager.on_llm_new_token("x", chunk=chunk)
            yield ChatGenerationChunk(message=chunk)


class StreamingReasoningChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "streaming-reasoning-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="Final answer",
                        additional_kwargs={
                            "content_blocks": [
                                {"type": "thinking", "text": "Reasoning path"},
                                {"type": "text", "text": "Final answer"},
                            ]
                        },
                    )
                )
            ]
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:  # type: ignore[override]
        parts = [
            AIMessageChunk(
                content="",
                additional_kwargs={"content_blocks": [{"type": "thinking", "text": "Reasoning path"}]},
            ),
            AIMessageChunk(content="Final answer"),
        ]
        for chunk in parts:
            if run_manager is not None:
                run_manager.on_llm_new_token(str(chunk.content), chunk=chunk)
            yield ChatGenerationChunk(message=chunk)


class PartialStreamingChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "partial-streaming-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="partial response"))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:  # type: ignore[override]
        chunk = AIMessageChunk(content="partial ")
        if run_manager is not None:
            run_manager.on_llm_new_token("partial ", chunk=chunk)
        yield ChatGenerationChunk(message=chunk)
        raise RuntimeError("stream interrupted")


class EmptyFinalAfterToolChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "empty-final-after-tool-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        if any(isinstance(message, ToolMessage) for message in messages):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "file_info",
                                "args": {"path": "/mnt/user-data/workspace/missing.txt"},
                                "id": "call-file-info",
                                "type": "tool_call",
                            }
                        ],
                    )
                )
            ]
        )


class FinalAfterToolResultChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "final-after-tool-result-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        if any(isinstance(message, ToolMessage) for message in messages):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Final answer after tool result."))])
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "file_info",
                                "args": {"path": "/mnt/user-data/workspace/missing.txt"},
                                "id": "call-file-info",
                                "type": "tool_call",
                            }
                        ],
                    )
                )
            ]
        )


class RepeatedToolLoopChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "repeated-tool-loop-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "file_info",
                                "args": {"path": "/mnt/user-data/workspace/repeated.txt"},
                                "id": "call-file-info",
                                "type": "tool_call",
                            }
                        ],
                    )
                )
            ]
        )


class OverloadThenSuccessChatModel(BaseChatModel):
    def __init__(self) -> None:
        super().__init__()
        object.__setattr__(self, "attempts", 0)

    @property
    def _llm_type(self) -> str:
        return "overload-then-success-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        object.__setattr__(self, "attempts", self.attempts + 1)
        if self.attempts == 1:
            raise RuntimeError("context length exceeded")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Recovered after compaction"))])


class RuntimeFailingChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "runtime-failing-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        raise RuntimeError("primary provider returned 503 temporarily unavailable")


class TransientThenSuccessChatModel(BaseChatModel):
    def __init__(self) -> None:
        super().__init__()
        object.__setattr__(self, "attempts", 0)

    @property
    def _llm_type(self) -> str:
        return "transient-then-success-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        object.__setattr__(self, "attempts", self.attempts + 1)
        if self.attempts == 1:
            raise RuntimeError("primary provider returned 503 temporarily unavailable")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Recovered on primary"))])


class RuntimeFallbackChatModel(BaseChatModel):
    captured_messages: list[BaseMessage] = []

    @property
    def _llm_type(self) -> str:
        return "runtime-fallback-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:  # type: ignore[override]
        self.__class__.captured_messages = list(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Recovered on fallback"))])


class _ActiveSubagentDrainRecorder:
    def __init__(self) -> None:
        self.drain_calls = 0

    def list_active_tasks(self, *, parent_thread_id: str):
        return [SimpleNamespace(task_id="sub-1", parent_run_id="run-tail")]

    def drain_events(self, *, parent_thread_id: str, parent_run_id: str | None = None):
        self.drain_calls += 1
        return []


class _SubagentDrainCounter:
    def __init__(self) -> None:
        self.drain_calls = 0

    def drain_events(self, *, parent_thread_id: str, parent_run_id: str | None = None):
        self.drain_calls += 1
        return []

    def reconcile_timeouts(self) -> None:
        return None

    def list_active_tasks(self, *, parent_thread_id: str):
        return []

    def list_tasks(self, *, parent_thread_id: str):
        return []


def test_run_engine_stream_emits_incremental_content_steps(contract_tmp_path) -> None:
    engine = RunEngine()
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-stream-runtime",
            user_message="hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=StreamingChunkChatModel(),
        )
    )

    events = list(session)

    assert [event.event for event in events] == [
        "run_started",
        "summary_update",
        "step_started",
        "step_delta",
        "step_delta",
        "step_updated",
        "message_completed",
        "run_completed",
    ]
    deltas = [event.data["payload_delta"] for event in events if event.event == "step_delta"]
    assert "".join(deltas) == "Hello"
    started = next(event for event in events if event.event == "step_started")
    assert started.data["step"]["type"] == "content"
    assert started.data["step"]["status"] == "running"
    updated = next(event for event in events if event.event == "step_updated")
    assert updated.data["step"]["status"] == "success"
    assert session.final_result is not None
    assert session.final_result.thread_state.conversation.messages[-1]["content"] == "Hello"
    assert session.final_result.thread_state.conversation.steps[-1]["payload"] == "Hello"


def test_run_engine_stream_finalizer_does_not_wait_for_active_subagents(contract_tmp_path) -> None:
    service = _ActiveSubagentDrainRecorder()

    events = RunEngine()._finalize_stream_subagent_events(
        request=RunRequest(
            thread_id="thread-tail-budget",
            run_id="run-tail",
            user_message="hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            subagent_service=service,
        )
    )

    assert events == []
    assert service.drain_calls == 1


def test_run_engine_throttles_empty_subagent_drains_during_token_stream(contract_tmp_path) -> None:
    service = _SubagentDrainCounter()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-subagent-drain-throttle",
            user_message="hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=ManyTinyChunkChatModel(),
            subagent_service=service,
        )
    )

    events = list(session)

    assert events[-1].event == "run_completed"
    assert service.drain_calls <= 3


def test_run_engine_persists_runtime_phase_timings(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    engine = RunEngine()
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-runtime-phases",
            user_message="hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=checkpointer,
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=StreamingChunkChatModel(),
        )
    )

    events = list(session)

    assert events[-1].event == "run_completed"
    assert session.final_result is not None
    timings = session.final_result.thread_state.execution.runtime_phase_timings
    phases = [item["phase"] for item in timings["marks"]]
    assert timings["run_id"].startswith("run-")
    assert timings["status"] == "completed"
    assert timings["runtime_assembly_elapsed_ms"] is not None
    assert timings["model_start_wait_ms"] is not None
    assert timings["first_model_event_elapsed_ms"] is not None
    assert timings["first_content_delta_elapsed_ms"] is not None
    assert timings["first_content_wait_ms"] is not None
    assert timings["post_content_elapsed_ms"] is not None
    assert timings["completed_elapsed_ms"] is not None
    assert "config_resolved" in phases
    assert "model_route_resolved" in phases
    assert "sandbox_provider_created" in phases
    assert "factory_feature_set_resolved" in phases
    assert "capability_assembly_started" in phases
    assert "capability_assembly_completed" in phases
    assert phases.index("capability_assembly_started") < phases.index("capability_assembly_completed")
    assert "prompt_snapshot_built" in phases
    assert "middleware_chain_built" in phases
    assert "chat_model_created" in phases
    assert "langgraph_agent_created" in phases
    assert "runtime_assembled" in phases
    assert phases.index("langgraph_agent_created") < phases.index("runtime_assembled")
    assert "run_started_emitted" in phases
    assert "agent_stream_entered" in phases
    assert "first_model_event" in phases
    assert "first_message_event" in phases
    assert "first_content_step_started" in phases
    assert "first_content_delta" in phases
    assert "run_completed_emitted" in phases
    assert phases.index("agent_stream_entered") < phases.index("first_model_event")
    assert phases.index("first_model_event") <= phases.index("first_message_event")
    assert phases.index("first_content_step_started") <= phases.index("first_content_delta")
    assert timings["runtime_assembly_elapsed_ms"] <= timings["first_model_event_elapsed_ms"]
    persisted = checkpointer.get_thread_state("thread-runtime-phases")
    assert persisted is not None
    assert persisted.execution.runtime_phase_timings["marks"][-1]["phase"] == "run_completed_emitted"
    assert persisted.execution.runtime_phase_timings["event_log"]["last_kind"] == "run_completed"


def test_run_engine_stream_writes_append_only_event_log(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-runtime-event-log",
            user_message="hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=StreamingChunkChatModel(),
            run_event_log_store=event_log,
            execution_mode=ThreadExecutionMode.CHAT,
        )
    )

    events = list(session)
    envelopes = event_log.list_events(thread_id="thread-runtime-event-log")

    assert [envelope.sequence for envelope in envelopes] == list(range(1, len(envelopes) + 1))
    assert [envelope.kind for envelope in envelopes] == [event.event for event in events]
    assert [event.data["event_id"] for event in events] == [envelope.event_id for envelope in envelopes]
    assert events[-1].data["event_log_cursor"] == envelopes[-1].sequence
    assert session.final_result is not None
    event_log_summary = session.final_result.thread_state.execution.runtime_phase_timings["event_log"]
    assert event_log_summary["event_count"] == len(envelopes)
    assert event_log_summary["last_event_id"] == envelopes[-1].event_id
    assert getattr(session.final_result.runtime.context, "run_event_cursor")["event_id"] == envelopes[-1].event_id


def test_run_event_log_pages_after_cursor_with_limit() -> None:
    event_log = InMemoryRunEventLogStore()
    for sequence in range(1, 6):
        event_log.append(
            RunEventEnvelope.from_run_event(
                RunEvent(event="step_delta", data={"thread_id": "thread-event-page", "run_id": "run-page"}),
                run_id="run-page",
                thread_id="thread-event-page",
                sequence=sequence,
            )
        )

    page = list_run_event_page(event_log, thread_id="thread-event-page", run_id="run-page", after_sequence=2, limit=2)

    assert [event.sequence for event in page.events] == [3, 4]
    assert page.next_cursor == 4
    assert page.has_more is True
    tail = list_run_event_page(event_log, thread_id="thread-event-page", run_id="run-page", after_sequence=4, limit=2)
    assert [event.sequence for event in tail.events] == [5]
    assert tail.next_cursor == 5
    assert tail.has_more is False


def test_run_event_log_pages_sort_run_scoped_events_by_sequence(contract_tmp_path) -> None:
    envelopes = [
        RunEventEnvelope.from_run_event(
            RunEvent(event="step_delta", data={"thread_id": "thread-event-page-order", "run_id": "run-page-order"}),
            run_id="run-page-order",
            thread_id="thread-event-page-order",
            sequence=sequence,
        )
        for sequence in [3, 1, 2]
    ]

    for event_log in [
        InMemoryRunEventLogStore(),
        JsonlRunEventLogStore(contract_tmp_path / "run-page-order-events.jsonl"),
    ]:
        for envelope in envelopes:
            event_log.append(envelope)

        page = list_run_event_page(
            event_log,
            thread_id="thread-event-page-order",
            run_id="run-page-order",
            after_sequence=0,
            limit=2,
        )
        tail = list_run_event_page(
            event_log,
            thread_id="thread-event-page-order",
            run_id="run-page-order",
            after_sequence=page.next_cursor,
            limit=2,
        )

        assert [event.sequence for event in page.events] == [1, 2]
        assert page.next_cursor == 2
        assert page.has_more is True
        assert [event.sequence for event in tail.events] == [3]
        assert tail.next_cursor == 3
        assert tail.has_more is False


def test_run_event_log_pages_dedupe_run_scoped_duplicate_sequences(contract_tmp_path) -> None:
    def envelope_for(sequence: int, event_id: str) -> RunEventEnvelope:
        return RunEventEnvelope.from_run_event(
            RunEvent(
                event="step_delta",
                data={
                    "thread_id": "thread-event-page-sequence-dedupe",
                    "run_id": "run-page-sequence-dedupe",
                    "event_id": event_id,
                },
            ),
            run_id="run-page-sequence-dedupe",
            thread_id="thread-event-page-sequence-dedupe",
            sequence=sequence,
        )

    envelopes = [
        envelope_for(1, "run-page-sequence-dedupe:000001"),
        envelope_for(2, "run-page-sequence-dedupe:000002"),
        envelope_for(2, "run-page-sequence-dedupe:000002-duplicate"),
        envelope_for(3, "run-page-sequence-dedupe:000003"),
    ]

    jsonl_log_path = contract_tmp_path / "run-page-sequence-dedupe-events.jsonl"
    jsonl_log_path.write_text(
        "\n".join(json.dumps(envelope.to_record(), ensure_ascii=False) for envelope in envelopes) + "\n",
        encoding="utf-8",
    )

    for event_log in [
        InMemoryRunEventLogStore(events=list(envelopes)),
        JsonlRunEventLogStore(jsonl_log_path),
    ]:
        full_page = list_run_event_page(
            event_log,
            thread_id="thread-event-page-sequence-dedupe",
            run_id="run-page-sequence-dedupe",
            after_sequence=0,
            limit=10,
        )
        page = list_run_event_page(
            event_log,
            thread_id="thread-event-page-sequence-dedupe",
            run_id="run-page-sequence-dedupe",
            after_sequence=0,
            limit=2,
        )
        tail = list_run_event_page(
            event_log,
            thread_id="thread-event-page-sequence-dedupe",
            run_id="run-page-sequence-dedupe",
            after_sequence=page.next_cursor,
            limit=2,
        )

        assert [event.sequence for event in full_page.events] == [1, 2, 3]
        assert full_page.next_cursor == 3
        assert full_page.has_more is False
        assert [event.sequence for event in page.events] == [1, 2]
        assert page.next_cursor == 2
        assert page.has_more is True
        assert [event.sequence for event in tail.events] == [3]
        assert tail.next_cursor == 3
        assert tail.has_more is False


def test_run_event_log_page_requires_run_id_for_cursor() -> None:
    event_log = InMemoryRunEventLogStore()
    event_log.append(
        RunEventEnvelope.from_run_event(
            RunEvent(event="step_delta", data={"thread_id": "thread-event-page-cursor", "run_id": "run-page-a"}),
            run_id="run-page-a",
            thread_id="thread-event-page-cursor",
            sequence=1,
        )
    )
    event_log.append(
        RunEventEnvelope.from_run_event(
            RunEvent(event="step_delta", data={"thread_id": "thread-event-page-cursor", "run_id": "run-page-b"}),
            run_id="run-page-b",
            thread_id="thread-event-page-cursor",
            sequence=1,
        )
    )

    try:
        list_run_event_page(event_log, thread_id="thread-event-page-cursor", after_sequence=0)
    except ValueError as exc:
        assert str(exc) == "run_id_required_for_cursor"
    else:
        raise AssertionError("thread-wide run event cursor should require run_id")


def test_run_event_log_store_append_is_idempotent_by_event_id(contract_tmp_path) -> None:
    first = RunEventEnvelope.from_run_event(
        RunEvent(event="step_delta", data={"thread_id": "thread-event-idempotent", "run_id": "run-idem"}),
        run_id="run-idem",
        thread_id="thread-event-idempotent",
        sequence=1,
    )
    duplicate = RunEventEnvelope.from_record(first.to_record())
    changed_duplicate = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={
                "thread_id": "thread-event-idempotent",
                "run_id": "run-idem",
                "event_id": first.event_id,
                "delta": "ignored duplicate",
            },
        ),
        run_id="run-idem",
        thread_id="thread-event-idempotent",
        sequence=1,
    )

    memory_store = InMemoryRunEventLogStore()
    assert memory_store.append(first) is first
    assert memory_store.append(duplicate).payload == first.payload
    assert memory_store.append(changed_duplicate).payload == first.payload
    assert memory_store.list_events(thread_id="thread-event-idempotent", run_id="run-idem") == [first]

    jsonl_store = JsonlRunEventLogStore(contract_tmp_path / "run-events.jsonl")
    assert jsonl_store.append(first).event_id == first.event_id
    assert jsonl_store.append(duplicate).payload == first.payload
    assert jsonl_store.append(changed_duplicate).payload == first.payload
    assert jsonl_store.list_events(thread_id="thread-event-idempotent", run_id="run-idem") == [first]
    persisted_lines = (contract_tmp_path / "run-events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(persisted_lines) == 1


def test_run_event_log_store_append_is_idempotent_by_run_sequence(contract_tmp_path) -> None:
    first = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={
                "thread_id": "thread-sequence-idempotent",
                "run_id": "run-sequence-idem",
                "event_id": "run-sequence-idem:000001",
                "delta": "first",
            },
        ),
        run_id="run-sequence-idem",
        thread_id="thread-sequence-idempotent",
        sequence=1,
    )
    duplicate_sequence = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={
                "thread_id": "thread-sequence-idempotent",
                "run_id": "run-sequence-idem",
                "event_id": "run-sequence-idem:000001-retry",
                "delta": "duplicate should not persist",
            },
        ),
        run_id="run-sequence-idem",
        thread_id="thread-sequence-idempotent",
        sequence=1,
    )

    memory_store = InMemoryRunEventLogStore()
    assert memory_store.append(first) is first
    assert memory_store.append(duplicate_sequence).event_id == first.event_id
    assert memory_store.list_events(thread_id="thread-sequence-idempotent", run_id="run-sequence-idem") == [first]

    jsonl_store = JsonlRunEventLogStore(contract_tmp_path / "run-sequence-idempotent-events.jsonl")
    assert jsonl_store.append(first).event_id == first.event_id
    assert jsonl_store.append(duplicate_sequence).event_id == first.event_id
    assert jsonl_store.list_events(thread_id="thread-sequence-idempotent", run_id="run-sequence-idem") == [first]
    persisted_lines = (contract_tmp_path / "run-sequence-idempotent-events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(persisted_lines) == 1


def test_run_event_log_store_scopes_event_id_idempotency_to_run_id(contract_tmp_path) -> None:
    first_run_event = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={"thread_id": "thread-run-local-id", "run_id": "run-one", "event_id": "000001"},
        ),
        run_id="run-one",
        thread_id="thread-run-local-id",
        sequence=1,
    )
    second_run_event = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={"thread_id": "thread-run-local-id", "run_id": "run-two", "event_id": "000001"},
        ),
        run_id="run-two",
        thread_id="thread-run-local-id",
        sequence=1,
    )

    memory_store = InMemoryRunEventLogStore()
    assert memory_store.append(first_run_event) is first_run_event
    assert memory_store.append(second_run_event) is second_run_event
    assert memory_store.list_events(thread_id="thread-run-local-id", run_id="run-one") == [first_run_event]
    assert memory_store.list_events(thread_id="thread-run-local-id", run_id="run-two") == [second_run_event]
    assert memory_store.list_events(thread_id="thread-run-local-id") == [first_run_event, second_run_event]

    jsonl_store = JsonlRunEventLogStore(contract_tmp_path / "run-local-events.jsonl")
    assert jsonl_store.append(first_run_event).run_id == "run-one"
    assert jsonl_store.append(second_run_event).run_id == "run-two"
    assert jsonl_store.list_events(thread_id="thread-run-local-id", run_id="run-one") == [first_run_event]
    assert jsonl_store.list_events(thread_id="thread-run-local-id", run_id="run-two") == [second_run_event]
    assert jsonl_store.list_events(thread_id="thread-run-local-id") == [first_run_event, second_run_event]
    assert len((contract_tmp_path / "run-local-events.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_jsonl_run_event_log_store_dedupes_legacy_duplicate_records_on_read(contract_tmp_path) -> None:
    log_path = contract_tmp_path / "legacy-run-events.jsonl"
    first = RunEventEnvelope.from_run_event(
        RunEvent(event="step_delta", data={"thread_id": "thread-legacy-dedupe", "run_id": "run-legacy"}),
        run_id="run-legacy",
        thread_id="thread-legacy-dedupe",
        sequence=1,
    )
    changed_duplicate = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={
                "thread_id": "thread-legacy-dedupe",
                "run_id": "run-legacy",
                "event_id": first.event_id,
                "delta": "duplicate should not replay",
            },
        ),
        run_id="run-legacy",
        thread_id="thread-legacy-dedupe",
        sequence=1,
    )
    second = RunEventEnvelope.from_run_event(
        RunEvent(event="run_completed", data={"thread_id": "thread-legacy-dedupe", "run_id": "run-legacy"}),
        run_id="run-legacy",
        thread_id="thread-legacy-dedupe",
        sequence=2,
    )
    log_path.write_text(
        "\n".join(
            [
                json.dumps(first.to_record(), ensure_ascii=False),
                json.dumps(changed_duplicate.to_record(), ensure_ascii=False),
                json.dumps(second.to_record(), ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    store = JsonlRunEventLogStore(log_path)
    events = store.list_events(thread_id="thread-legacy-dedupe", run_id="run-legacy")

    assert [event.event_id for event in events] == [first.event_id, second.event_id]
    assert events[0].payload == first.payload
    assert list_run_event_page(
        store,
        thread_id="thread-legacy-dedupe",
        run_id="run-legacy",
        after_sequence=0,
        limit=10,
    ).events == events


def test_jsonl_run_event_log_store_dedupes_legacy_duplicate_records_before_cursor(contract_tmp_path) -> None:
    log_path = contract_tmp_path / "legacy-run-events-cursor.jsonl"
    first = RunEventEnvelope.from_run_event(
        RunEvent(event="step_delta", data={"thread_id": "thread-legacy-cursor", "run_id": "run-legacy-cursor"}),
        run_id="run-legacy-cursor",
        thread_id="thread-legacy-cursor",
        sequence=1,
    )
    second = RunEventEnvelope.from_run_event(
        RunEvent(event="step_delta", data={"thread_id": "thread-legacy-cursor", "run_id": "run-legacy-cursor"}),
        run_id="run-legacy-cursor",
        thread_id="thread-legacy-cursor",
        sequence=2,
    )
    late_duplicate = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={
                "thread_id": "thread-legacy-cursor",
                "run_id": "run-legacy-cursor",
                "event_id": first.event_id,
                "delta": "late duplicate should not replay",
            },
        ),
        run_id="run-legacy-cursor",
        thread_id="thread-legacy-cursor",
        sequence=3,
    )
    terminal = RunEventEnvelope.from_run_event(
        RunEvent(event="run_completed", data={"thread_id": "thread-legacy-cursor", "run_id": "run-legacy-cursor"}),
        run_id="run-legacy-cursor",
        thread_id="thread-legacy-cursor",
        sequence=4,
    )
    log_path.write_text(
        "\n".join(
            [
                json.dumps(first.to_record(), ensure_ascii=False),
                json.dumps(second.to_record(), ensure_ascii=False),
                json.dumps(late_duplicate.to_record(), ensure_ascii=False),
                json.dumps(terminal.to_record(), ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    page = list_run_event_page(
        JsonlRunEventLogStore(log_path),
        thread_id="thread-legacy-cursor",
        run_id="run-legacy-cursor",
        after_sequence=2,
        limit=10,
    )

    assert page.events == [terminal]
    assert page.next_cursor == terminal.sequence
    assert page.has_more is False


def test_run_snapshot_projector_rebuilds_assistant_message_and_steps_from_event_log(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector",
            user_message="hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=StreamingChunkChatModel(),
            run_event_log_store=event_log,
            execution_mode=ThreadExecutionMode.CHAT,
        )
    )

    list(session)

    assert session.final_result is not None
    final_state = session.final_result.thread_state
    run_id = final_state.identity.run_id
    assert run_id is not None
    projected = RunSnapshotProjector().project(
        ThreadState(identity={"thread_id": "thread-event-projector", "run_id": run_id}),
        event_log.list_events(thread_id="thread-event-projector", run_id=run_id),
    )

    assert projected.identity.run_id == run_id
    assert projected.execution.execution_mode is ThreadExecutionMode.CHAT
    assert projected.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert projected.conversation.messages == [
        {"role": "ai", "content": "Hello", "id": final_state.conversation.messages[-1]["id"]},
    ]
    assert [
        {key: step.get(key) for key in ("step_id", "message_id", "type", "status", "payload", "visibility")}
        for step in projected.conversation.steps
    ] == [
        {
            "step_id": final_state.conversation.steps[-1]["step_id"],
            "message_id": final_state.conversation.messages[-1]["id"],
            "type": "content",
            "status": "success",
            "payload": "Hello",
            "visibility": "chat",
        }
    ]
    event_log_summary = projected.execution.runtime_phase_timings["event_log"]
    assert event_log_summary["event_count"] == len(event_log.list_events(thread_id="thread-event-projector", run_id=run_id))
    assert event_log_summary["last_kind"] == "run_completed"


def test_run_snapshot_projector_rebuilds_multi_run_thread_from_event_log(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")

    first = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector-multi",
            user_message="first question",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=StreamingChunkChatModel(),
            run_event_log_store=event_log,
            client_message_id="client-first",
            execution_mode=ThreadExecutionMode.CHAT,
        )
    )
    list(first)
    second = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector-multi",
            user_message="second question",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=checkpointer,
            store=store,
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="Second answer")]),
            run_event_log_store=event_log,
            client_message_id="client-second",
            execution_mode=ThreadExecutionMode.CHAT,
        )
    )
    list(second)

    envelopes = event_log.list_events(thread_id="thread-event-projector-multi")
    stale_base = ThreadState(identity={"thread_id": "thread-event-projector-multi"})
    stale_base.execution.recent_tool_activity = [
        {
            "tool_call_id": "stale-call",
            "name": "stale_tool",
            "status": "completed",
            "result_text": "stale result",
        }
    ]
    stale_base.execution.tool_calls = [
        {
            "tool_call_id": "stale-call",
            "name": "stale_tool",
            "status": "completed",
            "output": "stale result",
        }
    ]
    stale_base.artifacts.output_artifacts = ["stale/output.txt"]
    stale_base.durable_subagent_job_history = [
        {"job_id": "stale-task", "event_type": "job_completed", "status": "completed"}
    ]
    projected = RunSnapshotProjector().project_thread(
        stale_base,
        envelopes,
    )

    assert [
        (message.get("role"), message.get("content"))
        for message in projected.conversation.messages
    ] == [
        ("human", "first question"),
        ("ai", "Hello"),
        ("human", "second question"),
        ("ai", "Second answer"),
    ]
    assert projected.conversation.messages[0]["additional_kwargs"]["client_message_id"] == "client-first"
    assert projected.conversation.messages[2]["additional_kwargs"]["client_message_id"] == "client-second"
    assert [step["payload"] for step in projected.conversation.steps if step["type"] == "content"] == [
        "Hello",
        "Second answer",
    ]
    assert projected.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert projected.identity.run_id == second.final_result.thread_state.identity.run_id
    assert projected.execution.recent_tool_activity == []
    assert projected.execution.tool_calls == []
    assert projected.artifacts.output_artifacts == []
    assert projected.durable_subagent_job_history == []
    summary = projected.execution.runtime_phase_timings["event_log"]
    assert summary["run_count"] == 2
    assert summary["event_count"] == len(envelopes)


def test_run_snapshot_projector_preserves_thread_replay_input_order() -> None:
    first_run_started = RunEventEnvelope.from_run_event(
        RunEvent(
            event="run_started",
            data={
                "thread_id": "thread-projector-input-order",
                "run_id": "run-first",
                "message": "first question",
            },
        ),
        run_id="run-first",
        thread_id="thread-projector-input-order",
        sequence=1,
    )
    first_answer = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={
                "thread_id": "thread-projector-input-order",
                "run_id": "run-first",
                "message_id": "assistant-first",
                "step_id": "assistant-first:content",
                "payload_delta": "First answer",
            },
        ),
        run_id="run-first",
        thread_id="thread-projector-input-order",
        sequence=2,
    )
    first_completed = RunEventEnvelope.from_run_event(
        RunEvent(
            event="run_completed",
            data={"thread_id": "thread-projector-input-order", "run_id": "run-first"},
        ),
        run_id="run-first",
        thread_id="thread-projector-input-order",
        sequence=3,
    )
    second_run_started = RunEventEnvelope.from_run_event(
        RunEvent(
            event="run_started",
            data={
                "thread_id": "thread-projector-input-order",
                "run_id": "run-second",
                "message": "second question",
            },
        ),
        run_id="run-second",
        thread_id="thread-projector-input-order",
        sequence=1,
    )
    second_answer = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={
                "thread_id": "thread-projector-input-order",
                "run_id": "run-second",
                "message_id": "assistant-second",
                "step_id": "assistant-second:content",
                "payload_delta": "Second answer",
            },
        ),
        run_id="run-second",
        thread_id="thread-projector-input-order",
        sequence=2,
    )
    second_completed = RunEventEnvelope.from_run_event(
        RunEvent(
            event="run_completed",
            data={"thread_id": "thread-projector-input-order", "run_id": "run-second"},
        ),
        run_id="run-second",
        thread_id="thread-projector-input-order",
        sequence=3,
    )
    envelopes = [
        first_run_started,
        first_answer,
        first_completed,
        second_run_started,
        second_answer,
        second_completed,
    ]
    for envelope in envelopes[:3]:
        object.__setattr__(envelope, "created_at", "2026-05-30T10:00:00+00:00")
    for envelope in envelopes[3:]:
        object.__setattr__(envelope, "created_at", "2026-05-30T09:00:00+00:00")

    projected = RunSnapshotProjector().project_thread(
        ThreadState(identity={"thread_id": "thread-projector-input-order"}),
        envelopes,
    )

    assert [
        (message.get("role"), message.get("content"))
        for message in projected.conversation.messages
    ] == [
        ("human", "first question"),
        ("ai", "First answer"),
        ("human", "second question"),
        ("ai", "Second answer"),
    ]
    assert projected.execution.runtime_phase_timings["event_log"]["last_run_id"] == "run-second"


def test_run_snapshot_projector_keeps_same_step_id_across_runs() -> None:
    first_run_started = RunEventEnvelope.from_run_event(
        RunEvent(
            event="run_started",
            data={
                "thread_id": "thread-projector-step-collision",
                "run_id": "run-first",
                "message": "first question",
            },
        ),
        run_id="run-first",
        thread_id="thread-projector-step-collision",
        sequence=1,
    )
    first_answer = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={
                "thread_id": "thread-projector-step-collision",
                "run_id": "run-first",
                "message_id": "assistant-first",
                "step_id": "content",
                "payload_delta": "First answer",
            },
        ),
        run_id="run-first",
        thread_id="thread-projector-step-collision",
        sequence=2,
    )
    first_completed = RunEventEnvelope.from_run_event(
        RunEvent(
            event="run_completed",
            data={"thread_id": "thread-projector-step-collision", "run_id": "run-first"},
        ),
        run_id="run-first",
        thread_id="thread-projector-step-collision",
        sequence=3,
    )
    second_run_started = RunEventEnvelope.from_run_event(
        RunEvent(
            event="run_started",
            data={
                "thread_id": "thread-projector-step-collision",
                "run_id": "run-second",
                "message": "second question",
            },
        ),
        run_id="run-second",
        thread_id="thread-projector-step-collision",
        sequence=1,
    )
    second_answer = RunEventEnvelope.from_run_event(
        RunEvent(
            event="step_delta",
            data={
                "thread_id": "thread-projector-step-collision",
                "run_id": "run-second",
                "message_id": "assistant-second",
                "step_id": "content",
                "payload_delta": "Second answer",
            },
        ),
        run_id="run-second",
        thread_id="thread-projector-step-collision",
        sequence=2,
    )
    second_completed = RunEventEnvelope.from_run_event(
        RunEvent(
            event="run_completed",
            data={"thread_id": "thread-projector-step-collision", "run_id": "run-second"},
        ),
        run_id="run-second",
        thread_id="thread-projector-step-collision",
        sequence=3,
    )

    projected = RunSnapshotProjector().project_thread(
        ThreadState(identity={"thread_id": "thread-projector-step-collision"}),
        [
            first_run_started,
            first_answer,
            first_completed,
            second_run_started,
            second_answer,
            second_completed,
        ],
    )

    assert [
        (step.get("message_id"), step.get("step_id"), step.get("payload"))
        for step in projected.conversation.steps
        if step.get("type") == "content"
    ] == [
        ("assistant-first", "content", "First answer"),
        ("assistant-second", "content", "Second answer"),
    ]
    assert [
        (message.get("role"), message.get("content"))
        for message in projected.conversation.messages
    ] == [
        ("human", "first question"),
        ("ai", "First answer"),
        ("human", "second question"),
        ("ai", "Second answer"),
    ]


def test_run_snapshot_projector_keeps_same_step_id_across_messages_in_run() -> None:
    envelopes = [
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="run_started",
                data={
                    "thread_id": "thread-projector-run-step-collision",
                    "run_id": "run-one",
                    "message": "answer twice",
                },
            ),
            run_id="run-one",
            thread_id="thread-projector-run-step-collision",
            sequence=1,
        ),
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="step_delta",
                data={
                    "thread_id": "thread-projector-run-step-collision",
                    "run_id": "run-one",
                    "message_id": "assistant-first",
                    "step_id": "content",
                    "payload_delta": "First answer",
                },
            ),
            run_id="run-one",
            thread_id="thread-projector-run-step-collision",
            sequence=2,
        ),
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="message_completed",
                data={
                    "thread_id": "thread-projector-run-step-collision",
                    "run_id": "run-one",
                    "message_id": "assistant-first",
                },
            ),
            run_id="run-one",
            thread_id="thread-projector-run-step-collision",
            sequence=3,
        ),
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="step_delta",
                data={
                    "thread_id": "thread-projector-run-step-collision",
                    "run_id": "run-one",
                    "message_id": "assistant-second",
                    "step_id": "content",
                    "payload_delta": "Second answer",
                },
            ),
            run_id="run-one",
            thread_id="thread-projector-run-step-collision",
            sequence=4,
        ),
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="message_completed",
                data={
                    "thread_id": "thread-projector-run-step-collision",
                    "run_id": "run-one",
                    "message_id": "assistant-second",
                },
            ),
            run_id="run-one",
            thread_id="thread-projector-run-step-collision",
            sequence=5,
        ),
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="run_completed",
                data={"thread_id": "thread-projector-run-step-collision", "run_id": "run-one"},
            ),
            run_id="run-one",
            thread_id="thread-projector-run-step-collision",
            sequence=6,
        ),
    ]

    projected = RunSnapshotProjector().project(
        ThreadState(identity={"thread_id": "thread-projector-run-step-collision", "run_id": "run-one"}),
        envelopes,
    )

    assert [
        (step.get("message_id"), step.get("step_id"), step.get("payload"))
        for step in projected.conversation.steps
        if step.get("type") == "content"
    ] == [
        ("assistant-first", "content", "First answer"),
        ("assistant-second", "content", "Second answer"),
    ]
    assert [
        (message.get("id"), message.get("content"))
        for message in projected.conversation.messages
        if message.get("role") == "ai"
    ] == [
        ("assistant-first", "First answer"),
        ("assistant-second", "Second answer"),
    ]


def test_run_snapshot_projector_preserves_interrupted_terminal_from_event_log(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector-interrupted",
            user_message="stream until interrupted",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=PartialStreamingChatModel(),
            run_event_log_store=event_log,
        )
    )

    list(session)

    assert session.final_result is not None
    final_state = session.final_result.thread_state
    run_id = final_state.identity.run_id
    assert run_id is not None
    projected = RunSnapshotProjector().project(
        ThreadState(identity={"thread_id": "thread-event-projector-interrupted", "run_id": run_id}),
        event_log.list_events(thread_id="thread-event-projector-interrupted", run_id=run_id),
    )

    assert projected.lifecycle.status is ThreadLifecycleStatus.INTERRUPTED
    assert projected.execution.last_message_interrupted is True
    assert projected.execution.last_message_interrupted_reason
    assert projected.conversation.messages[-1]["content"] == "partial "
    assert projected.conversation.messages[-1]["status"] == "interrupted"
    assert projected.conversation.steps[-1]["status"] == "error"
    assert projected.conversation.steps[-1]["error"] == "interrupted"
    assert projected.execution.runtime_phase_timings["event_log"]["last_kind"] == "run_completed"


def test_run_snapshot_projector_preserves_existing_messages_outside_projected_run(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector-history",
            user_message="new question",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=StreamingChunkChatModel(),
            run_event_log_store=event_log,
        )
    )

    list(session)

    assert session.final_result is not None
    run_id = session.final_result.thread_state.identity.run_id
    assert run_id is not None
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-history", "run_id": run_id})
    base_state.conversation.messages = [
        {"role": "human", "content": "old question", "id": "old-user"},
        {"role": "ai", "content": "old answer", "id": "old-assistant"},
        {"role": "human", "content": "new question", "id": "new-user"},
    ]

    projected = RunSnapshotProjector().project(
        base_state,
        event_log.list_events(thread_id="thread-event-projector-history", run_id=run_id),
    )

    assert projected.conversation.messages[0] == {"role": "human", "content": "old question", "id": "old-user"}
    assert projected.conversation.messages[1] == {"role": "ai", "content": "old answer", "id": "old-assistant"}
    assert projected.conversation.messages[2] == {"role": "human", "content": "new question", "id": "new-user"}
    assert projected.conversation.messages[-1]["role"] == "ai"
    assert projected.conversation.messages[-1]["content"] == "Hello"


def test_run_snapshot_projector_replaces_existing_projected_message_in_place(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector-in-place",
            user_message="old run",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=StreamingChunkChatModel(),
            run_event_log_store=event_log,
        )
    )

    list(session)

    assert session.final_result is not None
    run_id = session.final_result.thread_state.identity.run_id
    assistant_id = session.final_result.thread_state.conversation.messages[-1]["id"]
    assert run_id is not None
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-in-place", "run_id": "newer-run"})
    base_state.conversation.messages = [
        {"role": "human", "content": "old run", "id": "old-user"},
        {"role": "ai", "content": "stale old answer", "id": assistant_id},
        {"role": "human", "content": "newer run", "id": "new-user"},
        {"role": "ai", "content": "newer answer", "id": "new-assistant"},
    ]

    projected = RunSnapshotProjector().project(
        base_state,
        event_log.list_events(thread_id="thread-event-projector-in-place", run_id=run_id),
    )

    assert [message["id"] for message in projected.conversation.messages] == [
        "old-user",
        assistant_id,
        "new-user",
        "new-assistant",
    ]
    assert projected.conversation.messages[1]["content"] == "Hello"
    assert projected.conversation.messages[-1]["content"] == "newer answer"


def test_run_snapshot_projector_rebuilds_recent_tool_activity_from_call_steps(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector-tools",
            user_message="check a file and answer",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=FinalAfterToolResultChatModel(),
            run_event_log_store=event_log,
        )
    )

    list(session)

    assert session.final_result is not None
    run_id = session.final_result.thread_state.identity.run_id
    assert run_id is not None
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-tools", "run_id": run_id})
    projected = RunSnapshotProjector().project(
        base_state,
        event_log.list_events(thread_id="thread-event-projector-tools", run_id=run_id),
    )

    assert len(projected.execution.recent_tool_activity) == 1
    activity = projected.execution.recent_tool_activity[0]
    assert activity.tool_call_id == "call-file-info"
    assert activity.message_id
    assert activity.name == "file_info"
    assert activity.display_name == "File Info"
    assert activity.source_kind == "builtin"
    assert activity.source_id == "core"
    assert activity.capability_group == "filesystem"
    assert activity.tool_execution_mode == "sync"
    assert activity.status == "completed"
    assert activity.args == {"path": "/mnt/user-data/workspace/missing.txt"}
    assert activity.result_text
    assert '"exists": false' in activity.result_text
    assert activity.started_at is not None
    assert activity.completed_at is not None
    assert isinstance(activity.duration_ms, int)
    assert len(projected.execution.tool_calls) == 1
    tool_call = projected.execution.tool_calls[0]
    assert tool_call.run_id == run_id
    assert tool_call.thread_id == "thread-event-projector-tools"
    assert tool_call.tool_call_id == "call-file-info"
    assert tool_call.name == "file_info"
    assert tool_call.display_name == "File Info"
    assert tool_call.source_kind == "builtin"
    assert tool_call.source_id == "core"
    assert tool_call.capability_group == "filesystem"
    assert tool_call.tool_execution_mode == "sync"
    assert tool_call.input == {"path": "/mnt/user-data/workspace/missing.txt"}
    assert tool_call.output
    assert '"exists": false' in str(tool_call.output)
    assert tool_call.status == "completed"
    assert tool_call.is_error is False
    assert tool_call.started_at is not None
    assert tool_call.completed_at is not None
    assert isinstance(tool_call.duration_ms, int)
    assert tool_call.visibility == "chat"
    tool_message = next(
        message
        for message in projected.conversation.messages
        if message.get("role") == "ai" and message.get("tool_calls")
    )
    assert tool_message["tool_calls"] == [
        {
            "name": "file_info",
            "args": {"path": "/mnt/user-data/workspace/missing.txt"},
            "id": "call-file-info",
            "type": "tool_call",
        }
    ]


def test_run_snapshot_projector_preserves_existing_tool_activity_outside_projected_run(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector-tool-history",
            user_message="check a file and answer",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=FinalAfterToolResultChatModel(),
            run_event_log_store=event_log,
        )
    )

    list(session)

    assert session.final_result is not None
    run_id = session.final_result.thread_state.identity.run_id
    assert run_id is not None
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-tool-history", "run_id": run_id})
    base_state.execution.recent_tool_activity = [
        RecentToolActivity(
            tool_call_id="old-call",
            message_id="old-message",
            name="old_tool",
            args={"old": True},
            status="completed",
            result_text="old result",
        )
    ]

    projected = RunSnapshotProjector().project(
        base_state,
        event_log.list_events(thread_id="thread-event-projector-tool-history", run_id=run_id),
    )

    assert [activity.tool_call_id for activity in projected.execution.recent_tool_activity] == [
        "call-file-info",
        "old-call",
    ]
    assert projected.execution.recent_tool_activity[1].result_text == "old result"


def test_run_snapshot_projector_rebuilds_artifacts_from_event_log() -> None:
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-artifacts", "run_id": "run-artifacts"})
    envelopes = [
        RunEventEnvelope.from_run_event(
            thread_id="thread-event-projector-artifacts",
            run_id="run-artifacts",
            sequence=1,
            event=RunEvent(
                event="run_started",
                data={"thread_id": "thread-event-projector-artifacts"},
            ),
        ),
        RunEventEnvelope.from_run_event(
            thread_id="thread-event-projector-artifacts",
            run_id="run-artifacts",
            sequence=2,
            event=RunEvent(
                event="artifact_registered",
                data={
                    "thread_id": "thread-event-projector-artifacts",
                    "kind": "output",
                    "label": "reports/summary.md",
                    "artifact_url": "/threads/thread-event-projector-artifacts/artifacts/outputs/reports/summary.md",
                    "virtual_path": "/mnt/user-data/outputs/reports/summary.md",
                },
            ),
        ),
        RunEventEnvelope.from_run_event(
            thread_id="thread-event-projector-artifacts",
            run_id="run-artifacts",
            sequence=3,
            event=RunEvent(
                event="artifact_emitted",
                data={
                    "thread_id": "thread-event-projector-artifacts",
                    "kind": "upload",
                    "label": "brief.txt",
                    "artifact_url": "/threads/thread-event-projector-artifacts/artifacts/uploads/brief.txt",
                    "virtual_path": "/mnt/user-data/uploads/brief.txt",
                },
            ),
        ),
        RunEventEnvelope.from_run_event(
            thread_id="thread-event-projector-artifacts",
            run_id="run-artifacts",
            sequence=4,
            event=RunEvent(
                event="artifact_emitted",
                data={
                    "thread_id": "thread-event-projector-artifacts",
                    "kind": "presented",
                    "label": "/mnt/user-data/outputs/reports/summary.md",
                    "virtual_path": "/mnt/user-data/outputs/reports/summary.md",
                },
            ),
        ),
        RunEventEnvelope.from_run_event(
            thread_id="thread-event-projector-artifacts",
            run_id="run-artifacts",
            sequence=5,
            event=RunEvent(
                event="artifact_emitted",
                data={
                    "thread_id": "thread-event-projector-artifacts",
                    "kind": "output",
                    "label": "reports/summary.md",
                    "artifact_url": "/threads/thread-event-projector-artifacts/artifacts/outputs/reports/summary.md",
                    "virtual_path": "/mnt/user-data/outputs/reports/summary.md",
                },
            ),
        ),
    ]

    projected = RunSnapshotProjector().project(base_state, envelopes)

    assert projected.artifacts.output_artifacts == ["reports/summary.md"]
    assert projected.artifacts.uploaded_files == [
        {
            "filename": "brief.txt",
            "artifact_url": "/threads/thread-event-projector-artifacts/artifacts/uploads/brief.txt",
            "virtual_path": "/mnt/user-data/uploads/brief.txt",
        }
    ]
    assert projected.artifacts.presented_artifacts == ["/mnt/user-data/outputs/reports/summary.md"]


def test_run_snapshot_projector_preserves_existing_artifacts_outside_projected_run() -> None:
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-existing-artifacts", "run_id": "run-artifacts"})
    base_state.artifacts.output_artifacts = ["old/report.txt"]
    base_state.artifacts.uploaded_files = [
        {
            "filename": "old-upload.txt",
            "artifact_url": "/threads/thread-event-projector-existing-artifacts/artifacts/uploads/old-upload.txt",
            "virtual_path": "/mnt/user-data/uploads/old-upload.txt",
        }
    ]
    base_state.artifacts.presented_artifacts = ["old/presented.md"]
    envelopes = [
        RunEventEnvelope.from_run_event(
            thread_id="thread-event-projector-existing-artifacts",
            run_id="run-artifacts",
            sequence=1,
            event=RunEvent(
                event="artifact_emitted",
                data={
                    "thread_id": "thread-event-projector-existing-artifacts",
                    "kind": "output",
                    "label": "new/report.txt",
                    "artifact_url": "/threads/thread-event-projector-existing-artifacts/artifacts/outputs/new/report.txt",
                    "virtual_path": "/mnt/user-data/outputs/new/report.txt",
                },
            ),
        ),
        RunEventEnvelope.from_run_event(
            thread_id="thread-event-projector-existing-artifacts",
            run_id="run-artifacts",
            sequence=2,
            event=RunEvent(
                event="artifact_emitted",
                data={
                    "thread_id": "thread-event-projector-existing-artifacts",
                    "kind": "upload",
                    "label": "old-upload.txt",
                    "artifact_url": "/threads/thread-event-projector-existing-artifacts/artifacts/uploads/old-upload.txt",
                    "virtual_path": "/mnt/user-data/uploads/old-upload.txt",
                },
            ),
        ),
    ]

    projected = RunSnapshotProjector().project(base_state, envelopes)

    assert projected.artifacts.output_artifacts == ["new/report.txt", "old/report.txt"]
    assert projected.artifacts.uploaded_files == [
        {
            "filename": "old-upload.txt",
            "artifact_url": "/threads/thread-event-projector-existing-artifacts/artifacts/uploads/old-upload.txt",
            "virtual_path": "/mnt/user-data/uploads/old-upload.txt",
        }
    ]
    assert projected.artifacts.presented_artifacts == ["old/presented.md"]


def test_run_snapshot_projector_rebuilds_pending_approval_from_event_log() -> None:
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-approval", "run_id": "run-approval"})
    envelopes = [
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="run_started",
                data={
                    "thread_id": "thread-event-projector-approval",
                    "run_id": "run-approval",
                    "execution_mode": "agent",
                },
            ),
            run_id="run-approval",
            thread_id="thread-event-projector-approval",
            sequence=1,
        ),
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="approval_requested",
                data={
                    "thread_id": "thread-event-projector-approval",
                    "execution_mode": "agent",
                    "decision": "needs_user_approval",
                    "request_id": "approval-1",
                    "reason": "Tool 'write_file' requires approval: filesystem_write",
                    "action_kind": "tool_call",
                    "requested_permissions": ["filesystem_write"],
                    "scope_options": ["turn", "session"],
                },
            ),
            run_id="run-approval",
            thread_id="thread-event-projector-approval",
            sequence=2,
        ),
    ]

    projected = RunSnapshotProjector().project(base_state, envelopes)

    assert projected.lifecycle.status is ThreadLifecycleStatus.AWAITING_APPROVAL
    assert projected.lifecycle.completed_at is None
    assert projected.lifecycle.last_error == "Tool 'write_file' requires approval: filesystem_write"
    assert projected.approvals.pending_approval is ApprovalDecision.NEEDS_USER_APPROVAL
    assert projected.approvals.approval_request == ApprovalRequest(
        request_id="approval-1",
        thread_id="thread-event-projector-approval",
        turn_id="run-approval",
        reason="Tool 'write_file' requires approval: filesystem_write",
        action_kind="tool_call",
        requested_permissions=["filesystem_write"],
        scope_options=("turn", "session"),
    )
    assert len(projected.approvals.recent_approval_events) == 1
    event = projected.approvals.recent_approval_events[0]
    assert event.request_id == "approval-1"
    assert event.decision == "needs_user_approval"
    assert event.reason == "Tool 'write_file' requires approval: filesystem_write"
    assert event.action_kind == "tool_call"
    assert event.requested_permissions == ["filesystem_write"]
    assert event.scope_options == ["turn", "session"]
    assert event.status == "requested"
    assert event.execution_mode is ThreadExecutionMode.AGENT


def test_run_snapshot_projector_preserves_existing_approval_events_outside_projected_run() -> None:
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-approval-history", "run_id": "run-approval"})
    base_state.approvals.recent_approval_events = [
        RecentApprovalEvent(
            request_id="old-approval",
            decision="approved",
            reason="old approval",
            action_kind="tool_call",
            requested_permissions=["filesystem_write"],
            scope_options=["turn"],
            status="resolved",
        )
    ]
    envelopes = [
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="approval_requested",
                data={
                    "thread_id": "thread-event-projector-approval-history",
                    "execution_mode": "agent",
                    "decision": "needs_user_approval",
                    "request_id": "new-approval",
                    "reason": "new approval",
                    "action_kind": "tool_call",
                    "requested_permissions": ["network_request"],
                    "scope_options": ["turn"],
                },
            ),
            run_id="run-approval",
            thread_id="thread-event-projector-approval-history",
            sequence=1,
        ),
    ]

    projected = RunSnapshotProjector().project(base_state, envelopes)

    assert [event.request_id for event in projected.approvals.recent_approval_events] == [
        "new-approval",
        "old-approval",
    ]
    assert projected.approvals.recent_approval_events[1].decision == "approved"
    assert projected.approvals.recent_approval_events[1].status == "resolved"


def test_run_snapshot_projector_rebuilds_streamed_guardrail_approval(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector-approval-stream",
            user_message="write a file",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "write_file",
                                "args": {"path": "/mnt/user-data/workspace/example.txt", "content": "hello"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                ]
            ),
            run_event_log_store=event_log,
        )
    )

    list(session)

    assert session.final_result is not None
    final_state = session.final_result.thread_state
    run_id = final_state.identity.run_id
    assert run_id is not None
    projected = RunSnapshotProjector().project(
        ThreadState(identity={"thread_id": "thread-event-projector-approval-stream", "run_id": run_id}),
        event_log.list_events(thread_id="thread-event-projector-approval-stream", run_id=run_id),
    )

    assert projected.lifecycle.status is ThreadLifecycleStatus.AWAITING_APPROVAL
    assert projected.approvals.pending_approval is ApprovalDecision.NEEDS_USER_APPROVAL
    assert projected.approvals.approval_request is not None
    assert projected.approvals.approval_request.action_kind == "tool_call"
    assert projected.approvals.approval_request.requested_permissions == ["filesystem_write"]
    assert projected.approvals.recent_approval_events[0].request_id == projected.approvals.approval_request.request_id
    assert projected.approvals.recent_approval_events[0].status == "requested"


def test_run_snapshot_projector_rebuilds_streamed_clarification_from_event_log(contract_tmp_path) -> None:
    event_log = InMemoryRunEventLogStore()
    session = RunEngine().run_stream(
        RunRequest(
            thread_id="thread-event-projector-clarification-stream",
            user_message="build a frontend app",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "ask_clarification",
                                "args": {
                                    "title": "Choose a frontend stack",
                                    "question": "Which stack should I scaffold?",
                                    "response_type": "single_select",
                                    "options": [
                                        {
                                            "id": "vite-react",
                                            "label": "Vite + React",
                                            "recommended": True,
                                        },
                                        {
                                            "id": "nextjs",
                                            "label": "Next.js",
                                        },
                                    ],
                                    "allow_custom": True,
                                },
                                "id": "call_stack",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            ),
            run_event_log_store=event_log,
            execution_mode=ThreadExecutionMode.AGENT,
        )
    )

    list(session)

    assert session.final_result is not None
    final_state = session.final_result.thread_state
    run_id = final_state.identity.run_id
    assert run_id is not None
    projected = RunSnapshotProjector().project(
        ThreadState(identity={"thread_id": "thread-event-projector-clarification-stream", "run_id": run_id}),
        event_log.list_events(thread_id="thread-event-projector-clarification-stream", run_id=run_id),
    )

    assert projected.lifecycle.status is ThreadLifecycleStatus.AWAITING_CLARIFICATION
    assert projected.lifecycle.completed_at is None
    assert projected.lifecycle.last_error == "Which stack should I scaffold?"
    interaction = projected.conversation.pending_user_interaction
    assert interaction is not None
    assert interaction["request_id"] == "call_stack"
    assert interaction["title"] == "Choose a frontend stack"
    assert interaction["question"] == "Which stack should I scaffold?"
    assert interaction["selection_mode"] == "single"
    assert interaction["allow_custom"] is True
    assert interaction["options"][0]["id"] == "vite-react"
    assert interaction["options"][0]["recommended"] is True
    assert projected.execution.runtime_phase_timings["event_log"]["last_kind"] == "run_completed"


def test_run_snapshot_projector_clears_stale_user_interaction_after_completed_event_log() -> None:
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-clarification-clear", "run_id": "run-done"})
    base_state.lifecycle.status = ThreadLifecycleStatus.AWAITING_CLARIFICATION
    base_state.conversation.pending_user_interaction = {
        "request_id": "old-clarification",
        "question": "Old question?",
        "selection_mode": "single",
        "options": [{"id": "a", "label": "A"}],
    }
    envelopes = [
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="run_started",
                data={
                    "thread_id": "thread-event-projector-clarification-clear",
                    "run_id": "run-done",
                },
            ),
            run_id="run-done",
            thread_id="thread-event-projector-clarification-clear",
            sequence=1,
        ),
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="run_completed",
                data={
                    "thread_id": "thread-event-projector-clarification-clear",
                    "run_id": "run-done",
                    "status": "completed",
                },
            ),
            run_id="run-done",
            thread_id="thread-event-projector-clarification-clear",
            sequence=2,
        ),
    ]

    projected = RunSnapshotProjector().project(base_state, envelopes)

    assert projected.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert projected.conversation.pending_user_interaction is None


def test_run_snapshot_projector_resolves_existing_approval_from_event_log() -> None:
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-approval-resolved", "run_id": "run-approval"})
    base_state.lifecycle.status = ThreadLifecycleStatus.AWAITING_APPROVAL
    base_state.approvals.pending_approval = ApprovalDecision.NEEDS_USER_APPROVAL
    base_state.approvals.approval_request = ApprovalRequest(
        request_id="approval-1",
        thread_id="thread-event-projector-approval-resolved",
        turn_id="previous-run",
        reason="old approval",
        action_kind="tool_call",
        requested_permissions=["filesystem_write"],
        scope_options=("turn",),
    )
    base_state.approvals.recent_approval_events = [
        RecentApprovalEvent(
            request_id="approval-1",
            decision="needs_user_approval",
            reason="old approval",
            action_kind="tool_call",
            requested_permissions=["filesystem_write"],
            scope_options=["turn"],
            status="requested",
        )
    ]
    envelopes = [
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="approval_resolved",
                data={
                    "thread_id": "thread-event-projector-approval-resolved",
                    "request_id": "approval-1",
                    "decision": "approved",
                    "execution_mode": "agent",
                },
            ),
            run_id="run-approval",
            thread_id="thread-event-projector-approval-resolved",
            sequence=1,
        ),
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="run_completed",
                data={
                    "thread_id": "thread-event-projector-approval-resolved",
                    "run_id": "run-approval",
                    "status": "completed",
                    "stream_status": "complete",
                },
            ),
            run_id="run-approval",
            thread_id="thread-event-projector-approval-resolved",
            sequence=2,
        ),
    ]

    projected = RunSnapshotProjector().project(base_state, envelopes)

    assert projected.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert projected.approvals.pending_approval is None
    assert projected.approvals.approval_request is None
    assert [(event.request_id, event.status, event.decision) for event in projected.approvals.recent_approval_events] == [
        ("approval-1", "resolved", "approved")
    ]


def test_run_snapshot_projector_rebuilds_subagent_state_from_raw_event_log() -> None:
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-subagent", "run_id": "run-subagent"})
    base_state.delegation.active_subagent_tasks = [
        {
            "task_id": "old-active",
            "parent_thread_id": "thread-event-projector-subagent",
            "parent_run_id": "old-run",
            "status": "running",
        }
    ]
    base_state.durable_subagent_job_history = [
        {
            "job_id": "old-job",
            "parent_thread_id": "thread-event-projector-subagent",
            "parent_run_id": "old-run",
            "event_type": "job_completed",
            "timestamp": "2026-05-28T03:00:00+00:00",
            "payload": {"status": "completed", "summary": "old result"},
        }
    ]
    envelopes = [
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="subagent_submitted",
                data={
                    "thread_id": "thread-event-projector-subagent",
                    "run_id": "run-subagent",
                    "subagent_job_id": "task-1",
                    "event_type": "job_submitted",
                    "timestamp": "2026-05-28T03:30:00+00:00",
                    "task_id": "task-1",
                    "batch_id": "batch-1",
                    "prompt_preview": "生成 tasks.json",
                    "child_thread_id": "child-thread-1",
                    "child_run_id": "child-run-1",
                    "status": "queued",
                },
            ),
            run_id="run-subagent",
            thread_id="thread-event-projector-subagent",
            sequence=1,
        ),
        RunEventEnvelope.from_run_event(
            RunEvent(
                event="subagent_started",
                data={
                    "thread_id": "thread-event-projector-subagent",
                    "run_id": "run-subagent",
                    "subagent_job_id": "task-1",
                    "event_type": "job_started",
                    "timestamp": "2026-05-28T03:31:00+00:00",
                    "task_id": "task-1",
                    "batch_id": "batch-1",
                    "prompt_preview": "生成 tasks.json",
                    "child_thread_id": "child-thread-1",
                    "child_run_id": "child-run-1",
                    "status": "running",
                    "started_at": "2026-05-28T03:31:00+00:00",
                },
            ),
            run_id="run-subagent",
            thread_id="thread-event-projector-subagent",
            sequence=2,
        ),
    ]

    projected = RunSnapshotProjector().project(base_state, envelopes)

    assert [task["task_id"] for task in projected.delegation.active_subagent_tasks] == ["task-1", "old-active"]
    active_task = projected.delegation.active_subagent_tasks[0]
    assert active_task["status"] == "running"
    assert active_task["parent_thread_id"] == "thread-event-projector-subagent"
    assert active_task["parent_run_id"] == "run-subagent"
    assert active_task["prompt_preview"] == "生成 tasks.json"
    assert active_task["child_thread_id"] == "child-thread-1"

    task_history = [item for item in projected.durable_subagent_job_history if item["job_id"] == "task-1"]
    assert [(item["event_type"], item["payload"]["status"]) for item in task_history] == [
        ("job_submitted", "queued"),
        ("job_started", "running"),
    ]
    assert task_history[0]["parent_thread_id"] == "thread-event-projector-subagent"
    assert task_history[0]["parent_run_id"] == "run-subagent"
    assert task_history[0]["payload"]["prompt_preview"] == "生成 tasks.json"
    assert task_history[1]["payload"]["started_at"] == "2026-05-28T03:31:00+00:00"
    assert any(item["job_id"] == "old-job" for item in projected.durable_subagent_job_history)


def test_run_snapshot_projector_rebuilds_terminal_subagent_from_step_events() -> None:
    base_state = ThreadState(identity={"thread_id": "thread-event-projector-subagent-step", "run_id": "run-subagent"})
    base_state.delegation.active_subagent_tasks = [
        {
            "task_id": "task-2",
            "parent_thread_id": "thread-event-projector-subagent-step",
            "parent_run_id": "run-subagent",
            "status": "running",
        },
        {
            "task_id": "old-active",
            "parent_thread_id": "thread-event-projector-subagent-step",
            "parent_run_id": "old-run",
            "status": "running",
        },
    ]
    adapter = _GraphRunEventAdapter(
        thread_id="thread-event-projector-subagent-step",
        run_id="run-subagent",
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )
    adapter.handle_message_stream((AIMessage(content="", id="assistant-subagent-step"), {}))
    events = adapter.handle_subagent_event(
        SimpleNamespace(
            event_type=SimpleNamespace(value="job_completed"),
            job_id="task-2",
            payload={
                "prompt": "完整 prompt 不应进入 durable history",
                "prompt_preview": "生成报告",
                "child_thread_id": "child-thread-2",
                "child_run_id": "child-run-2",
                "batch_id": "batch-2",
                "status": "completed",
                "summary": "报告已生成",
            },
            timestamp=datetime(2026, 5, 28, 4, 0, tzinfo=timezone.utc),
        )
    )
    envelopes = [
        RunEventEnvelope.from_run_event(
            event,
            run_id="run-subagent",
            thread_id="thread-event-projector-subagent-step",
            sequence=index,
        )
        for index, event in enumerate(events, start=1)
    ]

    projected = RunSnapshotProjector().project(base_state, envelopes)

    assert [task["task_id"] for task in projected.delegation.active_subagent_tasks] == ["old-active"]
    task_history = [item for item in projected.durable_subagent_job_history if item["job_id"] == "task-2"]
    assert len(task_history) == 1
    terminal = task_history[0]
    assert terminal["event_type"] == "job_completed"
    assert terminal["parent_thread_id"] == "thread-event-projector-subagent-step"
    assert terminal["parent_run_id"] == "run-subagent"
    assert terminal["payload"]["status"] == "completed"
    assert terminal["payload"]["summary"] == "报告已生成"
    assert terminal["payload"]["child_thread_id"] == "child-thread-2"
    assert terminal["payload"]["child_run_id"] == "child-run-2"
    assert terminal["payload"]["prompt_preview"] == "生成报告"
    assert "prompt" not in terminal["payload"]


def test_run_engine_input_payload_exposes_context_envelope_and_strips_storage_bridge(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")
    state = engine._create_initial_thread_state(
        RunRequest(
            thread_id="thread-context-envelope",
            user_message="hello",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
        )
    )
    state.conversation.messages = [
        {
            "id": "model-only",
            "role": "human",
            "content": "internal image bridge",
            "additional_kwargs": {
                "anvil_model_only": True,
                "anvil_view_image_injection": True,
            },
        }
    ]

    payload = engine._build_input_payload(
        thread_state=state,
        request=RunRequest(
            thread_id="thread-context-envelope",
            user_message="new turn",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
        ),
    )

    envelope = payload["context_envelope"]
    assert isinstance(envelope, dict)
    assert envelope["frontend_visible"]["current_turn_upload_count"] == 0
    assert envelope["debug_trace"]["model_message_count"] == 2
    assert envelope["debug_trace"]["persistent_message_count"] == 1
    assert "internal image bridge" in repr(payload["messages"])


def test_run_engine_context_envelope_counts_current_turn_uploads(contract_tmp_path) -> None:
    engine = RunEngine()
    path_service = PathService(contract_tmp_path / "threads")
    request = RunRequest(
        thread_id="thread-context-envelope-uploads",
        user_message="inspect the current files",
        config_layers=base_layers(),
        path_service=path_service,
        checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
        store=create_store(StoreBackend.IN_MEMORY),
        recent_upload_filenames=("diagram.png", "notes.txt"),
    )
    state = engine._create_initial_thread_state(request)
    uploads_dir = path_service.thread_uploads_dir("thread-context-envelope-uploads")
    (uploads_dir / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\nsample-image")
    (uploads_dir / "notes.txt").write_text("notes", encoding="utf-8")
    state.artifacts.uploaded_files = [
        {
            "filename": "diagram.png",
            "virtual_path": "/mnt/user-data/uploads/diagram.png",
            "mime_type": "image/png",
            "extension": ".png",
        },
        {
            "filename": "notes.txt",
            "virtual_path": "/mnt/user-data/uploads/notes.txt",
            "mime_type": "text/plain",
            "extension": ".txt",
        },
        {
            "filename": "old.txt",
            "virtual_path": "/mnt/user-data/uploads/old.txt",
            "mime_type": "text/plain",
            "extension": ".txt",
        },
    ]

    payload = engine._build_input_payload(thread_state=state, request=request)

    envelope = payload["context_envelope"]
    assert envelope["frontend_visible"] == {
        "current_turn_upload_count": 2,
        "current_turn_image_count": 1,
        "vision_supported": False,
    }
    assert envelope["debug_trace"]["current_turn_upload_count"] == 2
    assert envelope["debug_trace"]["current_turn_image_count"] == 1
    assert envelope["debug_trace"]["model_image_block_count"] == 1
    assert envelope["debug_trace"]["unsupported_image_placeholder"] is True
    assert "old.txt" not in repr(payload["messages"])


def test_context_assembler_resume_window_drops_pending_assistant_tail(contract_tmp_path) -> None:
    assembler = ContextAssembler(
        path_service=PathService(contract_tmp_path / "threads"),
        thread_id="thread-context-resume-window",
    )
    pending_assistant = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "shell",
                "args": {"command": "echo ready"},
                "id": "call-approval",
                "type": "tool_call",
            }
        ],
    )
    history_messages = [
        HumanMessage(content="please run this"),
        pending_assistant,
    ]

    envelope = assembler.assemble_input_envelope(
        history_messages=history_messages,
        translate_message=lambda message, **_: message,
        user_message="",
        include_user_message=False,
        drop_last_assistant_message=True,
        uploaded_files=[],
        recent_upload_filenames=(),
        client_message_id=None,
        vision_supported=True,
    )

    assert envelope.model_visible == history_messages[:1]
    assert len(envelope.persistent_transcript) == 1
    assert envelope.persistent_transcript[0].content == "please run this"
    assert envelope.persistent_transcript[0].additional_kwargs["display_content"] == "please run this"
    assert envelope.debug_trace["model_message_count"] == 1


def test_context_assembler_continuation_window_keeps_non_assistant_tail(contract_tmp_path) -> None:
    assembler = ContextAssembler(
        path_service=PathService(contract_tmp_path / "threads"),
        thread_id="thread-context-resume-window-tool-tail",
    )
    messages = [
        HumanMessage(content="read a file"),
        ToolMessage(content="result", tool_call_id="call-read"),
    ]

    selected = assembler.apply_continuation_window(
        messages=messages,
        continuation_window=ContextContinuationWindow(
            include_current_user_message=False,
            drop_pending_assistant_tail=True,
        ),
    )

    assert selected == messages


def test_context_assembler_builds_compacted_summary_model_call(contract_tmp_path) -> None:
    assembler = ContextAssembler(
        path_service=PathService(contract_tmp_path / "threads"),
        thread_id="thread-context-summary",
    )
    messages = [
        HumanMessage(content="old user"),
        AIMessage(content="old assistant"),
        HumanMessage(content="new user"),
    ]

    compacted, system_message = assembler.compacted_summary_model_call(
        messages=messages,
        system_prompt="Base system.",
        summary_context="Older turns established the runtime contract.",
        keep_recent_turns=2,
    )

    assert compacted == messages[-2:]
    assert system_message is not None
    assert system_message.content == (
        "Base system.\n\n"
        "<conversation_summary>\n"
        "Older turns established the runtime contract.\n"
        "</conversation_summary>"
    )

    compacted_again, system_message_again = assembler.compacted_summary_model_call(
        messages=messages,
        system_prompt=system_message.content,
        summary_context="Older turns established the runtime contract.",
        keep_recent_turns=2,
    )

    assert compacted_again == messages[-2:]
    assert system_message_again is not None
    assert system_message_again.content.count("<conversation_summary>") == 1


def test_context_assembler_compacted_window_preserves_tool_pairs(contract_tmp_path) -> None:
    assembler = ContextAssembler(
        path_service=PathService(contract_tmp_path / "threads"),
        thread_id="thread-context-summary-tools",
    )
    tool_call_assistant = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"path": "/mnt/user-data/workspace/a.txt"},
                "id": "call-read",
                "type": "tool_call",
            }
        ],
    )
    tool_result = ToolMessage(content="file contents", tool_call_id="call-read")
    messages = [
        HumanMessage(content="old user"),
        tool_call_assistant,
        tool_result,
        HumanMessage(content="new user"),
    ]

    compacted, system_message = assembler.compacted_summary_model_call(
        messages=messages,
        system_prompt=None,
        summary_context="Older turns.",
        keep_recent_turns=2,
    )

    assert compacted == [tool_call_assistant, tool_result, messages[-1]]
    assert system_message is not None


def test_run_engine_marks_empty_final_assistant_after_tools_interrupted(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    engine = RunEngine()
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-empty-final-after-tool",
            user_message="check a file and answer",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=checkpointer,
            store=store,
            chat_model_override=EmptyFinalAfterToolChatModel(),
        )
    )

    events = list(session)

    assert events[-1].event == "run_completed"
    assert events[-1].data["status"] == "interrupted"
    assert events[-1].data["stream_status"] == "interrupted"
    assert events[-1].data["assistant_message"] is None
    message_completed = [event for event in events if event.event == "message_completed"][-1]
    assert message_completed.data["stream_status"] == "interrupted"
    assert session.final_result is not None
    state = session.final_result.thread_state
    assert state.lifecycle.status is ThreadLifecycleStatus.INTERRUPTED
    assert state.lifecycle.last_error == EMPTY_FINAL_ASSISTANT_MESSAGE
    assert state.execution.last_message_interrupted is True
    assert state.execution.last_message_interrupted_reason == EMPTY_FINAL_ASSISTANT_MESSAGE
    assert state.conversation.messages[-1]["content"] == ""
    assert state.conversation.messages[-1]["status"] == "interrupted"
    assert state.conversation.messages[-1]["metadata"]["empty_final_reason"] == EMPTY_FINAL_ASSISTANT_MESSAGE
    empty_final_steps = [
        step
        for step in state.conversation.steps
        if step.get("payload") == EMPTY_FINAL_ASSISTANT_MESSAGE
    ]
    assert empty_final_steps == []
    error_step_updates = [
        event
        for event in events
        if event.event == "step_updated"
        and event.data["step"].get("payload") == EMPTY_FINAL_ASSISTANT_MESSAGE
        and event.data["step"]["status"] == "error"
    ]
    assert error_step_updates == []
    persisted = checkpointer.get_thread_state("thread-empty-final-after-tool")
    assert persisted is not None
    assert persisted.lifecycle.status is ThreadLifecycleStatus.INTERRUPTED


def test_run_engine_lets_model_finish_after_tool_results_in_same_graph_loop(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    engine = RunEngine()
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-final-after-tool-result",
            user_message="check a file and answer",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=checkpointer,
            store=store,
            chat_model_override=FinalAfterToolResultChatModel(),
        )
    )

    events = list(session)

    assert events[-1].event == "run_completed"
    assert events[-1].data["status"] == "completed"
    assert events[-1].data["stream_status"] == "complete"
    assert events[-1].data["assistant_message"] == "Final answer after tool result."
    assert session.final_result is not None
    state = session.final_result.thread_state
    assert state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert state.lifecycle.last_error is None
    assert state.execution.last_message_interrupted is False
    assert state.conversation.messages[-1]["content"] == "Final answer after tool result."
    assert len([message for message in state.conversation.messages if message.get("role") in {"human", "user"}]) == 1
    phases = [item["phase"] for item in state.execution.runtime_phase_timings["marks"]]
    assert "agent_stream_entered" in phases
    assert "agent_stream_completed" in phases
    persisted = checkpointer.get_thread_state("thread-final-after-tool-result")
    assert persisted is not None
    assert persisted.lifecycle.status is ThreadLifecycleStatus.COMPLETED


def test_run_engine_marks_loop_detection_hard_stop_interrupted(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    engine = RunEngine()
    layers = base_layers()
    layers.append(
        ConfigLayer(
            name="loop-detection",
            kind=ConfigLayerKind.USER,
            data={
                "loop_detection": {
                    "warn_threshold": 2,
                    "hard_limit": 3,
                    "window_size": 3,
                }
            },
        )
    )
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-loop-hard-stop",
            user_message="repeat the same tool until the loop guard stops it",
            config_layers=layers,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=checkpointer,
            store=store,
            chat_model_override=RepeatedToolLoopChatModel(),
        )
    )

    events = list(session)

    assert events[-1].event == "run_completed"
    assert events[-1].data["status"] == "interrupted"
    assert events[-1].data["stream_status"] == "interrupted"
    assert session.final_result is not None
    state = session.final_result.thread_state
    assert state.lifecycle.status is ThreadLifecycleStatus.INTERRUPTED
    assert state.execution.last_message_interrupted is True
    assert state.execution.last_message_interrupted_reason == (
        "Repeated internal tool loop stopped after 3 identical tool-call rounds."
    )
    assert state.lifecycle.last_error == state.execution.last_message_interrupted_reason
    assert state.conversation.messages[-1]["status"] == "interrupted"
    assert "I stopped a repeated internal tool loop" in state.conversation.messages[-1]["content"]
    assert any(
        step.get("type") == "content"
        and step.get("status") == "error"
        and "I stopped a repeated internal tool loop" in str(step.get("payload") or "")
        for step in state.conversation.steps
    )
    persisted = checkpointer.get_thread_state("thread-loop-hard-stop")
    assert persisted is not None
    assert persisted.lifecycle.status is ThreadLifecycleStatus.INTERRUPTED


def test_run_engine_reuses_pre_resolved_config_result(contract_tmp_path) -> None:
    engine = RunEngine()
    config_layers = base_layers()
    config_result = engine.config_service.resolve(config_layers)

    session = engine.run_stream(
        RunRequest(
            thread_id="thread-runtime-config-reuse",
            user_message="hello",
            config_layers=config_layers,
            config_result=config_result,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=StreamingChunkChatModel(),
        )
    )

    events = list(session)

    assert events[-1].event == "run_completed"
    assert session.final_result is not None
    timings = session.final_result.thread_state.execution.runtime_phase_timings
    phases = [item["phase"] for item in timings["marks"]]
    assert "config_reused" in phases
    assert "config_resolved" not in phases
    assert timings["runtime_assembly_elapsed_ms"] is not None
    assert timings["model_start_wait_ms"] is not None


def test_run_engine_persists_running_stream_steps(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    engine = RunEngine()
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-running-stream",
            user_message="hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=checkpointer,
            store=store,
            chat_model_override=StreamingChunkChatModel(),
        )
    )

    first_step = None
    for event in session:
        if event.event == "step_delta":
            first_step = checkpointer.get_thread_state("thread-running-stream")
            break

    assert first_step is not None
    assert first_step.lifecycle.status is ThreadLifecycleStatus.RUNNING
    assert first_step.conversation.steps
    assert first_step.conversation.steps[-1]["payload"] == "Hel"
    list(session)


def test_step_event_adapter_suppresses_internal_title_model_streams() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-internal-title",
        execution_mode="chat",
        tool_registry=ToolRegistry(),
    )

    events = adapter.handle_message_stream(
        (
            AIMessage(content="The user is asking me to generate a concise conversation title.", id="title-model-message"),
            {"tags": ["anvil_internal_title"], "metadata": {"anvil_internal": True}},
        )
    )

    assert events == []


def test_step_event_adapter_suppresses_internal_memory_rerank_streams() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-internal-memory",
        execution_mode="chat",
        tool_registry=ToolRegistry(),
    )

    events = adapter.handle_message_stream(
        (
            AIMessage(
                content='{"lexical-archive-archive-41940175bd554f38": 0.95}',
                id="memory-rerank-message",
            ),
            {
                "tags": ["anvil_internal_memory", "anvil_internal_memory_rerank"],
                "metadata": {"anvil_internal_kind": "memory_rerank"},
            },
        )
    )

    assert events == []


def test_step_event_adapter_hides_memory_tool_calls_from_chat_steps() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="memory",
            display_name="Memory",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="memory",
            summary="Inspect and maintain agent memory.",
            input_schema={"type": "object", "properties": {"action": {"type": "string"}}},
        )
    )
    adapter = _GraphRunEventAdapter(
        thread_id="thread-memory-tool",
        execution_mode="agent",
        tool_registry=registry,
    )

    started_events = adapter.handle_message_stream(
        (
            AIMessage(
                content="",
                id="assistant-memory-tool",
                tool_calls=[
                    {
                        "name": "memory",
                        "args": {"action": "inspect", "layer": "user"},
                        "id": "call-memory",
                        "type": "tool_call",
                    }
                ],
            ),
            {},
        )
    )
    completed_events = adapter.handle_message_stream(
        (
            ToolMessage(
                content='{"entries":[]}',
                name="memory",
                tool_call_id="call-memory",
            ),
            {},
        )
    )

    started = next(event.data["step"] for event in started_events if event.event == "step_started")
    updated = next(event.data["step"] for event in completed_events if event.event == "step_updated")
    assert started["type"] == "call"
    assert started["tool_name"] == "memory"
    assert started["visibility"] == "hidden"
    assert updated["visibility"] == "hidden"
    assert adapter.snapshot_recent_tool_activity()[0].name == "memory"


def test_step_event_adapter_hides_memory_tool_planning_text_from_chat_steps() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="memory",
            display_name="Memory",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="memory",
            summary="Inspect and maintain agent memory.",
            input_schema={"type": "object", "properties": {"action": {"type": "string"}}},
        )
    )
    adapter = _GraphRunEventAdapter(
        thread_id="thread-memory-tool-planning",
        execution_mode="agent",
        tool_registry=registry,
    )

    events = adapter.handle_message_stream(
        (
            AIMessage(
                content="我先检查记忆。",
                id="assistant-memory-plan",
                tool_calls=[
                    {
                        "name": "memory",
                        "args": {"action": "inspect", "layer": "workspace"},
                        "id": "call-memory-plan",
                        "type": "tool_call",
                    }
                ],
            ),
            {},
        )
    )

    started_steps = [event.data["step"] for event in events if event.event == "step_started"]
    assert [step["type"] for step in started_steps] == ["thinking", "call"]
    assert all(step["visibility"] == "hidden" for step in started_steps)
    assert started_steps[0]["title"] == "已处理内部能力"


def test_step_event_adapter_hides_delegation_orchestration_text_from_content_stream() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-delegation-live",
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )

    events = adapter.handle_message_stream(
        (
            AIMessage(content="batch 格式有点问题，让我改用单独委托的方式：", id="assistant-live-1"),
            {},
        )
    )

    assert [event.event for event in events] == ["step_started", "step_delta"]
    started = events[0].data["step"]
    assert started["type"] == "thinking"
    assert started["visibility"] == "hidden"


def test_hidden_delegation_reasoning_does_not_overwrite_visible_thinking_step() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-delegation-mixed",
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )

    hidden_events = adapter.handle_message_stream(
        (
            AIMessage(content="现在开始并行委托任务。", id="assistant-mixed-1"),
            {},
        )
    )
    visible_events = adapter.handle_message_stream(
        (
            AIMessage(
                content="",
                id="assistant-mixed-1",
                additional_kwargs={"content_blocks": [{"type": "thinking", "text": "真实思考过程"}]},
            ),
            {},
        )
    )

    hidden_started = next(event.data["step"] for event in hidden_events if event.event == "step_started")
    hidden_delta = next(event.data for event in visible_events if event.event == "step_delta")
    assert hidden_started["step_id"] == "assistant-mixed-1:thinking:hidden"
    assert hidden_started["visibility"] == "hidden"
    assert hidden_delta["step_id"] == "assistant-mixed-1:thinking:hidden"
    assert hidden_delta["payload_delta"] == "真实思考过程"


def test_tool_planning_content_is_reclassified_from_final_answer_to_thinking() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-tool-planning",
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )
    content_events = adapter.handle_message_stream((AIMessage(content="我先检查文件。", id="assistant-plan-1"), {}))
    tool_events = adapter.handle_message_stream(
        (
            AIMessage(
                content="",
                id="assistant-plan-2",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "/mnt/user-data/workspace/a.py"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            {},
        )
    )

    content_started = next(event.data["step"] for event in content_events if event.event == "step_started")
    reclassified = next(
        event.data["step"]
        for event in tool_events
        if event.event == "step_updated" and event.data["step"]["step_id"] == content_started["step_id"]
    )
    assert reclassified["type"] == "thinking"
    assert reclassified["metadata"]["reclassified_from"] == "content"


def test_tool_result_replay_does_not_duplicate_persisted_step_payload() -> None:
    registry = ToolRegistry()
    adapter = _GraphRunEventAdapter(
        thread_id="thread-tool-replay",
        run_id=None,
        execution_mode="agent",
        tool_registry=registry,
    )
    adapter.handle_message_stream(
        (
            AIMessage(
                content="",
                id="assistant-tool-replay",
                tool_calls=[
                    {
                        "name": "file_info",
                        "args": {"path": "/mnt/user-data/workspace/a.pptx"},
                        "id": "call-file-info",
                        "type": "tool_call",
                    }
                ],
            ),
            {},
        )
    )
    tool_message = ToolMessage(
        content='{"path":"/mnt/user-data/workspace/a.pptx","exists":true}',
        name="file_info",
        tool_call_id="call-file-info",
    )

    adapter.handle_update_stream({"tools": {"messages": [tool_message]}})
    completed_events = adapter.handle_message_stream((tool_message, {}))
    replay_events = adapter.handle_update_stream({"tools": {"messages": [tool_message]}})
    state = SimpleNamespace(conversation=SimpleNamespace(messages=[{"id": "assistant-tool-replay"}], steps=[]))
    adapter.apply_step_metadata(state)  # type: ignore[arg-type]

    updated = next(event.data["step"] for event in completed_events if event.event == "step_updated")
    assert updated["payload"] == '{"path":"/mnt/user-data/workspace/a.pptx","exists":true}'
    assert not [event for event in replay_events if event.event == "step_delta"]
    assert state.conversation.steps[-1]["payload"] == '{"path":"/mnt/user-data/workspace/a.pptx","exists":true}'


def test_tool_result_snapshot_update_does_not_append_duplicate_payload() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-tool-snapshot",
        run_id=None,
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )
    adapter.handle_message_stream(
        (
            AIMessage(
                content="",
                id="assistant-tool-snapshot",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "/mnt/user-data/outputs/plan.json", "content": "{}"},
                        "id": "call-write-file",
                        "type": "tool_call",
                    }
                ],
            ),
            {},
        )
    )
    content = '{"path":"/mnt/user-data/outputs/plan.json","operation":"created"}'
    tool_message = ToolMessage(
        content=content,
        name="write_file",
        tool_call_id="call-write-file",
    )

    adapter.handle_update_stream({"tools": {"messages": [tool_message]}})
    adapter.handle_update_stream({"tools": {"messages": [tool_message]}})
    state = SimpleNamespace(conversation=SimpleNamespace(messages=[{"id": "assistant-tool-snapshot"}], steps=[]))
    adapter.apply_step_metadata(state)  # type: ignore[arg-type]

    assert state.conversation.steps[-1]["payload"] == content


def test_stream_payload_merge_appends_plain_token_deltas_without_overlap_trimming() -> None:
    assert _merge_stream_payload("Hel", "lo") == ("Hello", "lo")
    assert _merge_stream_payload("abcdef", "defghi") == ("abcdefdefghi", "defghi")


def test_stream_payload_merge_can_handle_structured_suffix_and_overlap_replays() -> None:
    assert _merge_stream_payload("abcdef", "def", allow_overlap_replay=True) == ("abcdef", "")
    assert _merge_stream_payload("abcdef", "defghi", allow_overlap_replay=True) == ("abcdefghi", "ghi")


def test_visible_reasoning_resumes_in_new_step_after_tool_without_completed_event() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-visible-reasoning-phases",
        run_id=None,
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )

    first_events = adapter.handle_message_stream(
        (
            AIMessage(
                content="first thought",
                id="assistant-reasoning-phases",
                tool_calls=[
                    {
                        "name": "file_info",
                        "args": {"path": "/mnt/user-data/workspace/a.py"},
                        "id": "call-file-info",
                        "type": "tool_call",
                    }
                ],
            ),
            {},
        )
    )
    second_events = adapter.handle_message_stream(
        (
            AIMessage(
                content="second thought",
                id="assistant-reasoning-phases",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "/mnt/user-data/workspace/a.py"},
                        "id": "call-read-file",
                        "type": "tool_call",
                    }
                ],
            ),
            {},
        )
    )
    state = SimpleNamespace(conversation=SimpleNamespace(messages=[{"id": "assistant-reasoning-phases"}], steps=[]))
    adapter.apply_step_metadata(state)  # type: ignore[arg-type]

    assert [event.data["step"]["type"] for event in first_events if event.event == "step_started"] == ["thinking", "call"]
    assert [event.data["step"]["type"] for event in second_events if event.event == "step_started"] == ["thinking", "call"]
    assert [event.data["step"]["payload"] for event in first_events if event.event == "step_started"] == ["first thought", ""]
    assert [event.data["step"]["payload"] for event in second_events if event.event == "step_started"] == ["second thought", ""]
    assert [(step["type"], step["payload"]) for step in state.conversation.steps] == [
        ("thinking", "first thought"),
        ("call", ""),
        ("thinking", "second thought"),
        ("call", ""),
    ]


def test_prior_content_is_hidden_when_reclassified_for_internal_memory_tool_planning() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="memory",
            display_name="Memory",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="memory",
            summary="Inspect and maintain agent memory.",
            input_schema={"type": "object", "properties": {"action": {"type": "string"}}},
        )
    )
    adapter = _GraphRunEventAdapter(
        thread_id="thread-memory-tool-reclassify",
        execution_mode="agent",
        tool_registry=registry,
    )
    content_events = adapter.handle_message_stream((AIMessage(content="我先检查记忆。", id="assistant-memory-plan-1"), {}))
    tool_events = adapter.handle_message_stream(
        (
            AIMessage(
                content="",
                id="assistant-memory-plan-2",
                tool_calls=[
                    {
                        "name": "memory",
                        "args": {"action": "inspect", "layer": "workspace"},
                        "id": "call-memory-reclassify",
                        "type": "tool_call",
                    }
                ],
            ),
            {},
        )
    )

    content_started = next(event.data["step"] for event in content_events if event.event == "step_started")
    reclassified = next(
        event.data["step"]
        for event in tool_events
        if event.event == "step_updated" and event.data["step"]["step_id"] == content_started["step_id"]
    )
    assert reclassified["type"] == "thinking"
    assert reclassified["visibility"] == "hidden"
    assert reclassified["metadata"]["reason"] == "tool_planning"


def test_hidden_delegation_reasoning_is_not_persisted_as_interrupted_reasoning() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-delegation-interrupted",
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )

    adapter.handle_message_stream(
        (
            AIMessage(content="让我等待它们完成任务。", id="assistant-hidden-only"),
            {},
        )
    )

    interrupted = adapter.build_interrupted_message()
    assert interrupted is None


def test_provider_reasoning_is_hidden_and_not_persisted_as_interrupted_reasoning() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-provider-reasoning",
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )

    events = adapter.handle_message_stream(
        (
            AIMessage(
                content="",
                id="assistant-provider-reasoning",
                additional_kwargs={"content_blocks": [{"type": "thinking", "text": "private provider thought"}]},
            ),
            {},
        )
    )

    started = next(event.data["step"] for event in events if event.event == "step_started")
    assert started["type"] == "thinking"
    assert started["visibility"] == "hidden"
    assert adapter.build_interrupted_message() is None


def test_inline_think_stream_chunks_are_hidden_from_content_steps() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-inline-think",
        run_id=None,
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )

    first_events = adapter.handle_message_stream((AIMessage(content="<think>private", id="assistant-inline-think"), {}))
    second_events = adapter.handle_message_stream((AIMessage(content=" reasoning</think>\n\nVisible answer.", id="assistant-inline-think"), {}))

    content_deltas = [
        event.data["payload_delta"]
        for event in [*first_events, *second_events]
        if event.event == "step_delta" and event.data["step_id"] == "assistant-inline-think:content"
    ]
    joined_content = "".join(content_deltas)
    assert joined_content == "Visible answer."
    assert "<think" not in joined_content.lower()
    assert "private reasoning" not in joined_content


def test_subagent_lifecycle_updates_one_stable_expandable_step() -> None:
    adapter = _GraphRunEventAdapter(
        thread_id="thread-subagent-live",
        execution_mode="agent",
        tool_registry=ToolRegistry(),
    )
    adapter.handle_message_stream((AIMessage(content="", id="assistant-subagent-1"), {}))

    submitted = SimpleNamespace(
        event_type=SimpleNamespace(value="job_submitted"),
        job_id="task-1",
        payload={
            "prompt": "请生成 tasks.json 示例数据",
            "prompt_preview": "生成 tasks.json",
            "child_thread_id": "child-thread-1",
            "batch_id": "batch-1",
            "status": "running",
        },
        timestamp=None,
    )
    completed = SimpleNamespace(
        event_type=SimpleNamespace(value="job_completed"),
        job_id="task-1",
        payload={
            "prompt": "请生成 tasks.json 示例数据",
            "prompt_preview": "生成 tasks.json",
            "child_thread_id": "child-thread-1",
            "batch_id": "batch-1",
            "status": "completed",
            "summary": "tasks.json 已生成",
        },
        timestamp=None,
    )

    start_events = adapter.handle_subagent_event(submitted)
    done_events = adapter.handle_subagent_event(completed)

    started = next(event.data["step"] for event in start_events if event.event == "step_started")
    updated = next(event.data["step"] for event in done_events if event.event == "step_updated")
    assert started["step_id"] == updated["step_id"]
    assert started["tool_name"] == "subagent"
    assert started["metadata"]["prompt"] == "请生成 tasks.json 示例数据"
    assert started["metadata"]["prompt_preview"] == "生成 tasks.json"
    assert updated["status"] == "success"
    assert updated["payload"] == "tasks.json 已生成"


def test_run_engine_stream_emits_reasoning_and_final_answer_steps(contract_tmp_path) -> None:
    engine = RunEngine()
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-stream-reasoning",
            user_message="explain",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=StreamingReasoningChatModel(),
        )
    )

    events = list(session)

    names = [event.event for event in events]
    assert "reasoning_delta" not in names
    assert "message_delta" not in names
    assert "summary_update" in names
    started_steps = [event.data["step"] for event in events if event.event == "step_started"]
    assert [step["type"] for step in started_steps] == ["thinking", "content"]
    assert started_steps[0]["visibility"] == "hidden"
    updated_steps = [event.data["step"] for event in events if event.event == "step_updated"]
    content = next(step for step in updated_steps if step["type"] == "content")
    assert content["payload"] == "Final answer"
    assert session.final_result is not None
    assert isinstance(
        session.final_result.thread_state.conversation.messages[-1]["reasoning_duration_ms"],
        int,
    )
    assert [step["type"] for step in session.final_result.thread_state.conversation.steps] == ["thinking", "content"]
    assert session.final_result.thread_state.conversation.steps[0]["visibility"] == "hidden"
    phase_names = [item["phase"] for item in session.final_result.thread_state.execution.runtime_phase_timings["marks"]]
    assert "first_reasoning_delta" in phase_names
    assert "first_content_delta" in phase_names
    assert phase_names.index("first_reasoning_delta") <= phase_names.index("first_content_delta")


def test_run_engine_completes_visible_tool_planning_thinking_step(contract_tmp_path) -> None:
    engine = RunEngine()
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-visible-tool-thinking",
            user_message="inspect",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="我先读取文件。",
                        tool_calls=[
                            {
                                "name": "read_file",
                                "args": {"path": "/mnt/user-data/workspace/a.py"},
                                "id": "call_visible_planning",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="完成。"),
                ]
            ),
        )
    )

    events = list(session)

    thinking_updates = [
        event.data["step"]
        for event in events
        if event.event == "step_updated"
        and event.data["step"]["type"] == "thinking"
        and event.data["step"]["visibility"] == "chat"
    ]
    assert thinking_updates
    assert thinking_updates[-1]["status"] == "success"
    assert thinking_updates[-1]["completed_at"] is not None
    assert session.final_result is not None
    thinking_steps = [
        step
        for step in session.final_result.thread_state.conversation.steps
        if step["type"] == "thinking" and step["visibility"] == "chat"
    ]
    assert thinking_steps
    assert all(step["status"] == "success" for step in thinking_steps)
    assert all(step["completed_at"] is not None for step in thinking_steps)


def test_run_engine_stream_keeps_non_streaming_provider_on_same_contract(contract_tmp_path) -> None:
    engine = RunEngine()
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-stream-fallback",
            user_message="hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="fallback hello")]
            ),
        )
    )

    events = list(session)

    assert [event.event for event in events] == [
        "run_started",
        "summary_update",
        "step_started",
        "step_delta",
        "step_updated",
        "message_completed",
        "run_completed",
    ]
    assert session.final_result is not None
    assert session.final_result.thread_state.conversation.messages[-1]["content"] == "fallback hello"


def test_run_engine_persists_partial_stream_as_interrupted_message(contract_tmp_path) -> None:
    engine = RunEngine()
    session = engine.run_stream(
        RunRequest(
            thread_id="thread-stream-interrupted",
            user_message="stream until interrupted",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=PartialStreamingChatModel(),
        )
    )

    events = list(session)

    assert [event.event for event in events] == [
        "run_started",
        "summary_update",
        "step_started",
        "step_delta",
        "step_updated",
        "message_completed",
        "run_completed",
    ]
    completed = next(event for event in events if event.event == "message_completed")
    assert completed.data["stream_status"] == "interrupted"
    run_completed = next(event for event in events if event.event == "run_completed")
    assert run_completed.data["status"] == "interrupted"
    assert run_completed.data["stream_status"] == "interrupted"
    assert session.final_result is not None
    assert session.final_result.thread_state.lifecycle.status is ThreadLifecycleStatus.INTERRUPTED
    assert isinstance(session.final_result.thread_state.execution.sandbox_state, SandboxState)
    assert session.final_result.thread_state.execution.last_message_interrupted is True
    assert session.final_result.thread_state.conversation.messages[-1]["content"] == "partial "
    assert session.final_result.thread_state.conversation.messages[-1]["status"] == "interrupted"


def test_run_engine_overload_retries_with_emergency_summarization(contract_tmp_path) -> None:
    engine = RunEngine()
    model = OverloadThenSuccessChatModel()
    layers = base_layers()
    layers.append(
        ConfigLayer(
            name="summarization",
            kind=ConfigLayerKind.USER,
            data={
                "summarization": {
                    "enabled": True,
                    "token_threshold": 999999,
                    "keep_recent_turns": 1,
                }
            },
        )
    )

    session = engine.run_stream(
        RunRequest(
            thread_id="thread-stream-overload",
            user_message="need a retry after overload",
            config_layers=layers,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=model,
        )
    )

    events = list(session)

    assert events[-1].event == "run_completed"
    assert model.attempts == 2
    assert session.final_result is not None
    assert session.final_result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert session.final_result.thread_state.conversation.summary is not None
    context_usage = session.final_result.thread_state.execution.context_window_usage
    assert context_usage["compact_status"] == "compacted"
    assert context_usage["compaction_level"] == 3
    assert context_usage["compaction_level_label"] == "emergency"
    assert context_usage["compaction_reason"] == "context length exceeded"
    assert context_usage["compaction_keep_recent_turns"] == 1
    assert context_usage["compaction_diagnostics"]["compaction_level"] == 3
    assert context_usage["compaction_diagnostics"]["summary_source"] in {"empty_fallback", "fallback", "model"}


def test_run_engine_retries_same_model_and_ignores_configured_fallback(
    contract_tmp_path,
) -> None:
    engine = RunEngine()
    model = TransientThenSuccessChatModel()
    layers = base_layers()
    layers[0].data["llm"] = {
        "retry": {"max_attempts": 2, "initial_delay": 0.0, "backoff_multiplier": 1.0, "max_delay": 0.0},
        "fallback_models": ["backup"],
    }
    layers[0].data["models"]["backup"] = {
        "name": "backup",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "model_name": "gpt-5.4-mini",
        "use": "test_run_engine_stream_runtime:RuntimeFallbackChatModel",
        "context_window_tokens": 100000,
        "auto_compact_threshold_tokens": 80000,
    }

    session = engine.run_stream(
        RunRequest(
            thread_id="thread-stream-same-model-retry",
            user_message="recover on the selected model",
            config_layers=layers,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=model,
        )
    )

    events = list(session)

    assert events[-1].event == "run_completed"
    assert model.attempts == 2
    assert session.final_result is not None
    state = session.final_result.thread_state
    assert state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert state.execution.active_model == "openai"
    assert state.execution.model_fallback_history == []
    assert state.conversation.messages[-1]["content"] == "Recovered on primary"
    assert session.final_result.runtime.context.model_fallback_history == []


def test_run_engine_reports_error_after_same_model_retries_exhausted(
    contract_tmp_path,
    monkeypatch,
) -> None:
    engine = RunEngine()
    layers = base_layers()
    layers[0].data["llm"] = {
        "retry": {"max_attempts": 1, "initial_delay": 0.0, "backoff_multiplier": 1.0, "max_delay": 0.0},
        "fallback_models": ["backup"],
    }
    layers[0].data["models"]["openai"]["use"] = "test_run_engine_stream_runtime:RuntimeFailingChatModel"
    layers[0].data["models"]["backup"] = {
        "name": "backup",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "model_name": "gpt-5.4-mini",
        "use": "test_run_engine_stream_runtime:RuntimeFallbackChatModel",
        "context_window_tokens": 100000,
        "auto_compact_threshold_tokens": 80000,
    }

    monkeypatch.syspath_prepend(str(Path(__file__).parent))

    session = engine.run_stream(
        RunRequest(
            thread_id="thread-stream-provider-error",
            user_message="do not recover with backup model",
            config_layers=layers,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
        )
    )

    events = list(session)

    assert events[-1].event == "run_failed"
    assert session.final_result is not None
    state = session.final_result.thread_state
    assert state.lifecycle.status is ThreadLifecycleStatus.FAILED
    assert state.execution.active_model == "openai"
    assert state.execution.model_fallback_history == []
    assert session.final_result.runtime.context.model_fallback_history == []
    assert "fallback" not in (state.lifecycle.last_error or "").lower()


def test_run_engine_does_not_fallback_after_partial_stream(contract_tmp_path) -> None:
    engine = RunEngine()
    layers = base_layers()
    layers[0].data["llm"] = {"fallback_models": ["backup"]}
    layers[0].data["models"]["backup"] = {
        "name": "backup",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "model_name": "gpt-5.4-mini",
    }

    session = engine.run_stream(
        RunRequest(
            thread_id="thread-stream-no-fallback-after-partial",
            user_message="stream until interrupted",
            config_layers=layers,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=PartialStreamingChatModel(),
        )
    )

    list(session)

    assert session.final_result is not None
    assert session.final_result.thread_state.lifecycle.status is ThreadLifecycleStatus.INTERRUPTED
    assert session.final_result.thread_state.execution.active_model == "openai"
    assert session.final_result.thread_state.execution.model_fallback_history == []
    assert session.final_result.runtime.context.model_fallback_history == []
