from __future__ import annotations

import importlib.util
import os
import threading
import time
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage

from anvil.agents.features import RuntimeFeatureSet
from anvil.config import ConfigLayer, ConfigLayerKind
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.runs import RunEngine, RunRequest
from anvil.runtime.store import StoreBackend, create_store
from anvil.sandbox import PathService
from anvil.subagents import SubagentService
from anvil.tracing import NoOpTracingSink, TracingService, TracingSettings
import anvil.tracing.service as tracing_service_module
from fake_models import BindableFakeMessagesListChatModel


@dataclass
class FakeTracingSink:
    run_events: list[tuple[str, str, dict]] = field(default_factory=list)
    tool_events: list[tuple[str, str, str | None, str]] = field(default_factory=list)
    subagent_events: list[tuple[str, str, str]] = field(default_factory=list)

    def run_started(self, *, trace_id: str, metadata: dict) -> None:
        self.run_events.append(("started", trace_id, metadata))

    def run_finished(self, *, trace_id: str, metadata: dict, error: str | None = None) -> None:
        payload = dict(metadata)
        payload["error"] = error
        self.run_events.append(("finished", trace_id, payload))

    def tool_started(self, *, trace_id: str, tool_name: str, tool_call_id: str | None) -> None:
        self.tool_events.append(("started", tool_name, tool_call_id, trace_id))

    def tool_finished(
        self,
        *,
        trace_id: str,
        tool_name: str,
        tool_call_id: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        self.tool_events.append((status, tool_name, tool_call_id, trace_id))

    def subagent_submitted(self, *, parent_trace_id: str | None, task_id: str, metadata: dict) -> None:
        self.subagent_events.append(("submitted", task_id, parent_trace_id or ""))

    def subagent_finished(
        self,
        *,
        parent_trace_id: str | None,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        self.subagent_events.append((status, task_id, parent_trace_id or ""))


@dataclass
class FailingTracingSink:
    calls: int = 0

    def _fail(self, *args, **kwargs) -> None:
        self.calls += 1
        raise RuntimeError("sink unavailable")

    run_started = _fail
    run_finished = _fail
    tool_started = _fail
    tool_finished = _fail
    subagent_submitted = _fail
    subagent_finished = _fail


class BlockingLangSmithSink(FailingTracingSink):
    def __init__(self, settings: TracingSettings) -> None:
        self.settings = settings
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_timeout_seconds = 2.0

    def run_started(self, *args, **kwargs) -> None:
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=self.block_timeout_seconds)
        raise RuntimeError("langsmith unavailable")


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


def test_tracing_service_can_be_disabled_without_side_effects() -> None:
    service = TracingService.from_env_and_config({"enabled": False})
    assert service.enabled() is False
    service.run_started(trace_id="trace-1", metadata={"thread_id": "thread-1"})
    service.run_finished(trace_id="trace-1", metadata={"status": "completed"})


def test_tracing_service_fail_open_disables_sink_after_runtime_error() -> None:
    service = TracingService(
        settings=TracingSettings(enabled=True, fail_open=True),
        sink=FailingTracingSink(),
    )

    service.run_started(trace_id="trace-1", metadata={"thread_id": "thread-1"})
    assert isinstance(service.sink, NoOpTracingSink)
    service.run_finished(trace_id="trace-1", metadata={"status": "completed"})


def test_fail_open_langsmith_sink_calls_are_non_blocking(monkeypatch) -> None:
    created: list[BlockingLangSmithSink] = []

    class BlockingFactorySink(BlockingLangSmithSink):
        def __init__(self, settings: TracingSettings) -> None:
            super().__init__(settings)
            created.append(self)

    monkeypatch.setattr(tracing_service_module, "LangSmithTracingSink", BlockingFactorySink)
    service = TracingService.from_env_and_config({"enabled": True, "provider": "langsmith", "fail_open": True})

    started = time.perf_counter()
    service.run_started(trace_id="trace-1", metadata={"thread_id": "thread-1"})
    elapsed = time.perf_counter() - started

    assert elapsed < 0.2
    assert created and created[0].entered.wait(timeout=1)

    created[0].release.set()
    deadline = time.perf_counter() + 1
    while service.enabled() and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert service.enabled() is False


def test_run_engine_first_event_is_not_blocked_by_fail_open_langsmith(monkeypatch, contract_tmp_path) -> None:
    created: list[BlockingLangSmithSink] = []

    class BlockingFactorySink(BlockingLangSmithSink):
        def __init__(self, settings: TracingSettings) -> None:
            super().__init__(settings)
            self.block_timeout_seconds = 30.0
            created.append(self)

    monkeypatch.setattr(tracing_service_module, "LangSmithTracingSink", BlockingFactorySink)
    tracing = TracingService.from_env_and_config({"enabled": True, "provider": "langsmith", "fail_open": True})
    engine = RunEngine()
    first_events: list[object] = []
    iterators: list[object] = []
    errors: list[BaseException] = []

    def read_first_event() -> None:
        try:
            session = engine.run_stream(
                RunRequest(
                    thread_id="trace-nonblocking-thread",
                    user_message="hello",
                    config_layers=base_layers(),
                    path_service=PathService(contract_tmp_path / "threads"),
                    checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
                    store=create_store(StoreBackend.IN_MEMORY),
                    feature_set=RuntimeFeatureSet(),
                    tracing_service=tracing,
                    chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello from model")]),
                )
            )
            iterator = iter(session)
            iterators.append(iterator)
            first_events.append(next(iterator))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=read_first_event, daemon=True)
    worker.start()
    worker.join(timeout=15)
    if worker.is_alive():
        if created:
            created[0].release.set()
        worker.join(timeout=3)
        raise AssertionError("RunEngine first event was blocked by fail-open tracing")
    if errors:
        raise errors[0]

    assert first_events and first_events[0].event == "run_started"
    assert created and created[0].entered.wait(timeout=1)
    assert not created[0].release.is_set()

    created[0].release.set()
    if iterators:
        remaining_events = list(iterators[0])
        assert remaining_events[-1].event == "run_completed"


def test_tracing_service_can_disable_env_auto_tracing(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING", "true")

    service = TracingService(settings=TracingSettings(enabled=True, fail_open=True))
    service.suppress_env_auto_tracing()

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert os.environ["LANGCHAIN_TRACING"] == "false"


def test_tracing_settings_accept_langsmith_aliases(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "alias-project")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://smith.example.com")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)
    monkeypatch.delenv("LANGCHAIN_ENDPOINT", raising=False)

    settings = TracingSettings.from_env_and_config()
    assert settings.enabled is True
    assert settings.api_key == "lsv2_key"
    assert settings.project == "alias-project"
    assert settings.endpoint == "https://smith.example.com"
    assert settings.enabled_source == "LANGSMITH_TRACING"
    assert settings.api_key_source == "LANGSMITH_API_KEY"


def test_tracing_settings_prefer_langsmith_over_langchain_aliases(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_key")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "langchain-key")

    settings = TracingSettings.from_env_and_config()
    assert settings.enabled is False
    assert settings.enabled_source == "LANGSMITH_TRACING"
    assert settings.api_key == "lsv2_key"
    assert settings.api_key_source == "LANGSMITH_API_KEY"


def test_tracing_service_builds_model_callbacks_when_langsmith_is_available() -> None:
    service = TracingService(settings=TracingSettings(enabled=True, provider="langsmith", project="anvil"))
    callbacks = service.build_model_callbacks()
    if importlib.util.find_spec("langsmith") is None or service.settings.fail_open:
        assert callbacks == []
    else:
        assert callbacks


def test_run_engine_emits_run_and_tool_trace_events(contract_tmp_path) -> None:
    sink = FakeTracingSink()
    tracing = TracingService(settings=TracingSettings(enabled=True), sink=sink)
    engine = RunEngine()

    result = engine.run(
        RunRequest(
            thread_id="trace-thread",
            user_message="list files",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(),
            tracing_service=tracing,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_dir",
                                "args": {"path": "/mnt/user-data/workspace"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status.value == "completed"
    assert sink.run_events[0][0] == "started"
    assert sink.run_events[-1][0] == "finished"
    assert sink.run_events[-1][2]["status"] == "completed"
    assert ("started", "list_dir", "call_1", result.runtime.context.run_trace_id) in sink.tool_events
    assert ("completed", "list_dir", "call_1", result.runtime.context.run_trace_id) in sink.tool_events


def test_run_engine_survives_runtime_tracing_sink_failures(contract_tmp_path) -> None:
    tracing = TracingService(
        settings=TracingSettings(enabled=True, fail_open=True),
        sink=FailingTracingSink(),
    )
    engine = RunEngine()

    result = engine.run(
        RunRequest(
            thread_id="trace-fail-open-thread",
            user_message="hello",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(),
            tracing_service=tracing,
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[AIMessage(content="hello from model")]
            ),
        )
    )

    assert result.thread_state.lifecycle.status.value == "completed"


def test_subagent_service_emits_trace_events() -> None:
    sink = FakeTracingSink()
    tracing = TracingService(settings=TracingSettings(enabled=True), sink=sink)
    service = SubagentService(tracing_service=tracing)
    blocker = threading.Event()

    def runner():
        blocker.wait(timeout=1)
        return "done"

    task = service.submit(
        parent_thread_id="thread-1",
        prompt="trace me",
        parent_visible_tool_names=("read_file",),
        config_result=__import__("anvil").ConfigService().resolve(
            [ConfigLayer(name="default", kind=ConfigLayerKind.DEFAULT, data={"subagents": {"enabled": True}})]
        ),
        trace_id="parent-trace",
        runner=runner,
    )
    blocker.set()

    deadline = time.time() + 2
    while time.time() < deadline:
        result = service.get_result(task.task_id)
        if result is not None:
            break
        time.sleep(0.01)

    assert ("submitted", task.task_id, "parent-trace") in sink.subagent_events
    assert ("completed", task.task_id, "parent-trace") in sink.subagent_events
