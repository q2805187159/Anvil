from __future__ import annotations

import importlib.util
import os
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TracingSettings:
    enabled: bool = False
    provider: str = "langsmith"
    project: str | None = None
    endpoint: str | None = None
    api_key: str | None = None
    fail_open: bool = True
    enabled_source: str | None = None
    api_key_source: str | None = None
    project_source: str | None = None
    endpoint_source: str | None = None

    @classmethod
    def from_env_and_config(cls, config: dict[str, Any] | None = None) -> "TracingSettings":
        config = config or {}
        enabled, enabled_source = _resolve_flag_value(
            config=config,
            config_key="enabled",
            env_names=("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"),
        )
        project, project_source = _resolve_text_value(
            config=config,
            config_key="project",
            env_names=("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT"),
        )
        endpoint, endpoint_source = _resolve_text_value(
            config=config,
            config_key="endpoint",
            env_names=("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT"),
        )
        api_key, api_key_source = _resolve_text_value(
            config=config,
            config_key="api_key",
            env_names=("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"),
        )
        return cls(
            enabled=enabled,
            provider=str(config.get("provider", "langsmith")),
            project=project,
            endpoint=endpoint,
            api_key=api_key,
            fail_open=bool(config.get("fail_open", True)),
            enabled_source=enabled_source,
            api_key_source=api_key_source,
            project_source=project_source,
            endpoint_source=endpoint_source,
        )


class TracingSink(Protocol):
    def run_started(self, *, trace_id: str, metadata: dict[str, Any]) -> None: ...

    def run_finished(self, *, trace_id: str, metadata: dict[str, Any], error: str | None = None) -> None: ...

    def tool_started(self, *, trace_id: str, tool_name: str, tool_call_id: str | None) -> None: ...

    def tool_finished(
        self,
        *,
        trace_id: str,
        tool_name: str,
        tool_call_id: str | None,
        status: str,
        error: str | None = None,
    ) -> None: ...

    def subagent_submitted(self, *, parent_trace_id: str | None, task_id: str, metadata: dict[str, Any]) -> None: ...

    def subagent_finished(
        self,
        *,
        parent_trace_id: str | None,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> None: ...


class NoOpTracingSink:
    def run_started(self, *, trace_id: str, metadata: dict[str, Any]) -> None:
        return None

    def run_finished(self, *, trace_id: str, metadata: dict[str, Any], error: str | None = None) -> None:
        return None

    def tool_started(self, *, trace_id: str, tool_name: str, tool_call_id: str | None) -> None:
        return None

    def tool_finished(
        self,
        *,
        trace_id: str,
        tool_name: str,
        tool_call_id: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        return None

    def subagent_submitted(self, *, parent_trace_id: str | None, task_id: str, metadata: dict[str, Any]) -> None:
        return None

    def subagent_finished(
        self,
        *,
        parent_trace_id: str | None,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        return None


class LangSmithTracingSink:
    def __init__(self, settings: TracingSettings) -> None:
        from langsmith import Client

        kwargs: dict[str, Any] = {}
        if settings.api_key:
            kwargs["api_key"] = settings.api_key
        if settings.endpoint:
            kwargs["api_url"] = settings.endpoint
        self.client = Client(**kwargs)
        self.project = settings.project or "anvil"
        self._tool_runs: dict[tuple[str, str | None], str] = {}
        self._subagent_runs: dict[str, str] = {}

    def run_started(self, *, trace_id: str, metadata: dict[str, Any]) -> None:
        self.client.create_run(
            id=trace_id,
            name="anvil.run",
            run_type="chain",
            inputs={"thread_id": metadata.get("thread_id"), "message": metadata.get("user_message")},
            extra={"metadata": metadata},
            project_name=self.project,
            start_time=utc_now(),
        )

    def run_finished(self, *, trace_id: str, metadata: dict[str, Any], error: str | None = None) -> None:
        self.client.update_run(
            trace_id,
            outputs={"status": metadata.get("status"), "thread_id": metadata.get("thread_id")},
            error=error,
            end_time=utc_now(),
        )

    def tool_started(self, *, trace_id: str, tool_name: str, tool_call_id: str | None) -> None:
        child_id = str(uuid4())
        self._tool_runs[(trace_id, tool_call_id)] = child_id
        self.client.create_run(
            id=child_id,
            name=f"tool:{tool_name}",
            run_type="tool",
            inputs={"tool_name": tool_name, "tool_call_id": tool_call_id},
            parent_run_id=trace_id,
            project_name=self.project,
            start_time=utc_now(),
        )

    def tool_finished(
        self,
        *,
        trace_id: str,
        tool_name: str,
        tool_call_id: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        child_id = self._tool_runs.pop((trace_id, tool_call_id), None)
        if child_id is None:
            return
        self.client.update_run(
            child_id,
            outputs={"tool_name": tool_name, "status": status},
            error=error,
            end_time=utc_now(),
        )

    def subagent_submitted(self, *, parent_trace_id: str | None, task_id: str, metadata: dict[str, Any]) -> None:
        child_id = str(uuid4())
        self._subagent_runs[task_id] = child_id
        self.client.create_run(
            id=child_id,
            name="anvil.subagent",
            run_type="chain",
            inputs={"task_id": task_id, "prompt": metadata.get("prompt")},
            extra={"metadata": metadata},
            parent_run_id=parent_trace_id,
            project_name=self.project,
            start_time=utc_now(),
        )

    def subagent_finished(
        self,
        *,
        parent_trace_id: str | None,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        child_id = self._subagent_runs.pop(task_id, None)
        if child_id is None:
            return
        self.client.update_run(
            child_id,
            outputs={"task_id": task_id, "status": status},
            error=error,
            end_time=utc_now(),
        )


class AsyncFailOpenTracingSink:
    def __init__(self, sink: TracingSink, *, max_queue: int = 256) -> None:
        self._sink = sink
        self._queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=max_queue)
        self._active = True
        self._lock = threading.Lock()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="anvil-tracing-fail-open",
            daemon=True,
        )
        self._worker.start()

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def run_started(self, *, trace_id: str, metadata: dict[str, Any]) -> None:
        self._enqueue("run_started", {"trace_id": trace_id, "metadata": metadata})

    def run_finished(self, *, trace_id: str, metadata: dict[str, Any], error: str | None = None) -> None:
        self._enqueue("run_finished", {"trace_id": trace_id, "metadata": metadata, "error": error})

    def tool_started(self, *, trace_id: str, tool_name: str, tool_call_id: str | None) -> None:
        self._enqueue("tool_started", {"trace_id": trace_id, "tool_name": tool_name, "tool_call_id": tool_call_id})

    def tool_finished(
        self,
        *,
        trace_id: str,
        tool_name: str,
        tool_call_id: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        self._enqueue(
            "tool_finished",
            {
                "trace_id": trace_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "status": status,
                "error": error,
            },
        )

    def subagent_submitted(self, *, parent_trace_id: str | None, task_id: str, metadata: dict[str, Any]) -> None:
        self._enqueue(
            "subagent_submitted",
            {"parent_trace_id": parent_trace_id, "task_id": task_id, "metadata": metadata},
        )

    def subagent_finished(
        self,
        *,
        parent_trace_id: str | None,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        self._enqueue(
            "subagent_finished",
            {"parent_trace_id": parent_trace_id, "task_id": task_id, "status": status, "error": error},
        )

    def _enqueue(self, method_name: str, kwargs: dict[str, Any]) -> None:
        if not self.is_active():
            return
        try:
            self._queue.put_nowait((method_name, kwargs))
        except queue.Full:
            self._disable()

    def _worker_loop(self) -> None:
        while self.is_active():
            method_name, kwargs = self._queue.get()
            try:
                getattr(self._sink, method_name)(**kwargs)
            except Exception:
                self._disable()
            finally:
                self._queue.task_done()

    def _disable(self) -> None:
        with self._lock:
            self._active = False


@dataclass
class TracingService:
    settings: TracingSettings
    sink: TracingSink = field(default_factory=NoOpTracingSink)

    @classmethod
    def from_env_and_config(cls, config: dict[str, Any] | None = None) -> "TracingService":
        settings = TracingSettings.from_env_and_config(config)
        if not settings.enabled:
            return cls(settings=settings, sink=NoOpTracingSink())
        try:
            if settings.provider == "langsmith":
                sink: TracingSink = LangSmithTracingSink(settings)
                if settings.fail_open:
                    sink = AsyncFailOpenTracingSink(sink)
                return cls(settings=settings, sink=sink)
        except Exception:
            if not settings.fail_open:
                raise
        return cls(settings=settings, sink=NoOpTracingSink())

    def enabled(self) -> bool:
        if not self.settings.enabled or isinstance(self.sink, NoOpTracingSink):
            return False
        is_active = getattr(self.sink, "is_active", None)
        if callable(is_active):
            return bool(is_active())
        return True

    def build_model_callbacks(self) -> list[Any]:
        if not self.settings.enabled or self.settings.provider != "langsmith":
            return []
        if self.settings.fail_open:
            return []
        if importlib.util.find_spec("langsmith") is None:
            return []
        try:
            from langchain_core.tracers.langchain import LangChainTracer

            return [LangChainTracer(project_name=self.settings.project or "anvil")]
        except Exception:
            if not self.settings.fail_open:
                raise
            return []

    def new_trace_id(self) -> str:
        return str(uuid4())

    def suppress_env_auto_tracing(self) -> None:
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGCHAIN_TRACING"] = "false"

    def run_started(self, *, trace_id: str, metadata: dict[str, Any]) -> None:
        self._call_sink("run_started", trace_id=trace_id, metadata=metadata)

    def run_finished(self, *, trace_id: str, metadata: dict[str, Any], error: str | None = None) -> None:
        self._call_sink("run_finished", trace_id=trace_id, metadata=metadata, error=error)

    def tool_started(self, *, trace_id: str, tool_name: str, tool_call_id: str | None) -> None:
        self._call_sink("tool_started", trace_id=trace_id, tool_name=tool_name, tool_call_id=tool_call_id)

    def tool_finished(
        self,
        *,
        trace_id: str,
        tool_name: str,
        tool_call_id: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        self._call_sink(
            "tool_finished",
            trace_id=trace_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status=status,
            error=error,
        )

    def subagent_submitted(self, *, parent_trace_id: str | None, task_id: str, metadata: dict[str, Any]) -> None:
        self._call_sink("subagent_submitted", parent_trace_id=parent_trace_id, task_id=task_id, metadata=metadata)

    def subagent_finished(
        self,
        *,
        parent_trace_id: str | None,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        self._call_sink(
            "subagent_finished",
            parent_trace_id=parent_trace_id,
            task_id=task_id,
            status=status,
            error=error,
        )

    def _call_sink(self, method_name: str, **kwargs: Any) -> None:
        method = getattr(self.sink, method_name)
        try:
            method(**kwargs)
        except Exception:
            if not self.settings.fail_open:
                raise
            self.sink = NoOpTracingSink()


_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def _resolve_flag_value(
    *,
    config: dict[str, Any],
    config_key: str,
    env_names: tuple[str, ...],
) -> tuple[bool, str | None]:
    if config_key in config:
        return bool(config.get(config_key)), f"config.{config_key}"
    for name in env_names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip().lower() in _TRUTHY_VALUES, name
    return False, None


def _resolve_text_value(
    *,
    config: dict[str, Any],
    config_key: str,
    env_names: tuple[str, ...],
) -> tuple[str | None, str | None]:
    if config.get(config_key):
        return str(config.get(config_key)), f"config.{config_key}"
    for name in env_names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip(), name
    return None, None
