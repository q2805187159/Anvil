from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from concurrent.futures import TimeoutError as FutureTimeoutError
from uuid import uuid4

from anvil.agents.features import RuntimeFeatureSet
from anvil.config import ConfigResolutionResult
from anvil.runtime.tool_registry.contracts import ToolRegistryEntry, ToolSourceKind

from .contracts import SubagentEvent, SubagentEventType, SubagentTaskRecord, SubagentTaskStatus
from .executor import SubagentExecutor
from .event_broker import SubagentEventBroker
from .registry import InMemorySubagentRegistry
from .sqlite_registry import SqliteSubagentRegistry


class SubagentService:
    def __init__(
        self,
        *,
        registry: InMemorySubagentRegistry | SqliteSubagentRegistry | None = None,
        executor: SubagentExecutor | None = None,
        tracing_service=None,
        default_runner_factory=None,
        event_broker: SubagentEventBroker | None = None,
        event_persister=None,
    ) -> None:
        self.registry = registry or InMemorySubagentRegistry()
        self.tracing_service = tracing_service
        self.event_broker = event_broker or SubagentEventBroker()
        self.event_persister = event_persister
        self.executor = executor or SubagentExecutor(
            registry=self.registry,
            tracing_service=tracing_service,
            event_broker=self.event_broker,
            event_persister=event_persister,
        )
        self.default_runner_factory = default_runner_factory
        self._live_futures: dict[str, object] = {}
        self._pending_runners: dict[str, Callable[[], object]] = {}
        self._pending_max_concurrency: dict[str, int] = {}
        self._scheduler_lock = threading.RLock()
        self.recover_orphaned_tasks()

    def submit(
        self,
        *,
        parent_thread_id: str,
        parent_run_id: str | None = None,
        prompt: str,
        parent_visible_tool_names: tuple[str, ...],
        config_result: ConfigResolutionResult,
        requested_tool_names: tuple[str, ...] = (),
        parent_delegation_depth: int = 0,
        trace_id: str | None = None,
        execution_mode: object | None = None,
        batch_id: str | None = None,
        prompt_preview: str | None = None,
        depends_on_task_ids: tuple[str, ...] = (),
        runner=None,
    ) -> SubagentTaskRecord:
        config = config_result.effective_config.subagents
        next_depth = parent_delegation_depth + 1
        if next_depth > config.max_depth:
            raise ValueError("subagent delegation depth limit reached")
        dependency_task_ids = self._normalize_dependency_task_ids(depends_on_task_ids)
        self._validate_dependency_task_ids(
            parent_thread_id=parent_thread_id,
            dependency_task_ids=dependency_task_ids,
        )
        dependency_state = self._dependency_state_for_dependency_ids(
            parent_thread_id=parent_thread_id,
            dependency_task_ids=dependency_task_ids,
        )
        if dependency_state == "ready":
            self._enforce_execution_capacity(max_concurrency=config.max_concurrency)

        task_id = f"subagent-{uuid4().hex[:12]}"
        child_thread_id = self.build_child_thread_id(parent_thread_id=parent_thread_id, task_id=task_id)
        child_run_id = self.build_child_run_id(task_id=task_id)
        allowed_tool_names = self.intersect_tool_names(
            parent_visible_tool_names=parent_visible_tool_names,
            requested_tool_names=requested_tool_names,
        )

        task = SubagentTaskRecord(
            task_id=task_id,
            batch_id=batch_id,
            parent_thread_id=parent_thread_id,
            parent_run_id=parent_run_id,
            child_thread_id=child_thread_id,
            child_run_id=child_run_id,
            trace_id=trace_id,
            prompt_preview=prompt_preview or self._preview_text(prompt),
            assigned_profile="general",
            delegation_depth=next_depth,
            timeout_at=self.executor.default_timeout_at(config.timeout_seconds),
            requested_tool_names=tuple(sorted(requested_tool_names)),
            allowed_tool_names=allowed_tool_names,
            depends_on_task_ids=dependency_task_ids,
        )

        if runner is None:
            if self.default_runner_factory is not None:
                try:
                    runner = self.default_runner_factory(
                        task=task,
                        prompt=prompt,
                        config_result=config_result,
                        allowed_tool_names=allowed_tool_names,
                        execution_mode=execution_mode,
                    )
                except TypeError:
                    runner = self.default_runner_factory(
                        task=task,
                        prompt=prompt,
                        config_result=config_result,
                        allowed_tool_names=allowed_tool_names,
                    )
            else:
                def runner():
                    raise RuntimeError("subagent runner is not configured")

        self.registry.add_task(task)
        self._publish_event(
            task,
            event_type=SubagentEventType.JOB_SUBMITTED,
            payload={
                "status": task.status.value,
                "prompt": prompt,
                "prompt_preview": task.prompt_preview,
                "batch_id": task.batch_id,
                "allowed_tool_names": list(allowed_tool_names),
                "depends_on_task_ids": list(dependency_task_ids),
                "delegation_depth": task.delegation_depth,
            },
        )
        if self.tracing_service is not None and trace_id is not None:
            self.tracing_service.subagent_submitted(
                parent_trace_id=trace_id,
                task_id=task.task_id,
                metadata={
                    "prompt": prompt,
                    "allowed_tool_names": list(allowed_tool_names),
                    "depends_on_task_ids": list(dependency_task_ids),
                    "delegation_depth": task.delegation_depth,
                },
            )

        if dependency_state == "ready":
            self._start_task(task, runner)
            return task
        if dependency_state == "blocked":
            return self._terminalize_dependency_blocked_task(task)

        with self._scheduler_lock:
            self._pending_runners[task.task_id] = runner
            self._pending_max_concurrency[task.task_id] = int(config.max_concurrency)
        return task

    def cancel(self, task_id: str, *, reason: str = "cancelled by runtime") -> SubagentTaskRecord:
        task = self.registry.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task id: {task_id}")
        if task.status in {
            SubagentTaskStatus.COMPLETED,
            SubagentTaskStatus.FAILED,
            SubagentTaskStatus.CANCELLED,
            SubagentTaskStatus.TIMED_OUT,
            SubagentTaskStatus.INTERRUPTED,
            SubagentTaskStatus.FAILED_RECOVERY,
        }:
            return task

        with self._scheduler_lock:
            self._pending_runners.pop(task_id, None)
            self._pending_max_concurrency.pop(task_id, None)
        task.cancel_requested = True
        self.registry.update_task(task)
        cancelled = self.registry.terminalize_task(
            task_id,
            status=SubagentTaskStatus.CANCELLED,
            error=reason,
            completed_at=datetime.now(task.created_at.tzinfo or timezone.utc),
        )
        self._publish_event(
            cancelled,
            event_type=SubagentEventType.JOB_CANCELLED,
            payload={
                "status": cancelled.status.value,
                "error": reason,
            },
        )
        if self.tracing_service is not None:
            self.tracing_service.subagent_finished(
                parent_trace_id=cancelled.trace_id,
                task_id=task_id,
                status=cancelled.status.value,
                error=reason,
            )
        self.schedule_ready_tasks(parent_thread_id=cancelled.parent_thread_id)
        return cancelled

    def reconcile_timeouts(self, *, now: datetime | None = None) -> tuple[str, ...]:
        current = now or datetime.now(timezone.utc)
        timed_out: list[str] = []
        parent_thread_ids: set[str] = set()
        for task in self.registry.list_tasks():
            if task.status not in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}:
                continue
            if task.timeout_at is None or task.timeout_at > current:
                continue
            with self._scheduler_lock:
                self._pending_runners.pop(task.task_id, None)
                self._pending_max_concurrency.pop(task.task_id, None)
            self.registry.terminalize_task(
                task.task_id,
                status=SubagentTaskStatus.TIMED_OUT,
                error="subagent timed out",
                completed_at=current,
            )
            refreshed = self.registry.get_task(task.task_id)
            if refreshed is not None:
                self._publish_event(
                    refreshed,
                    event_type=SubagentEventType.JOB_TIMED_OUT,
                    payload={
                        "status": refreshed.status.value,
                        "error": "subagent timed out",
                    },
                )
            if self.tracing_service is not None:
                self.tracing_service.subagent_finished(
                    parent_trace_id=task.trace_id,
                    task_id=task.task_id,
                    status=SubagentTaskStatus.TIMED_OUT.value,
                    error="subagent timed out",
                )
            timed_out.append(task.task_id)
            parent_thread_ids.add(task.parent_thread_id)
        if timed_out:
            for parent_thread_id in parent_thread_ids:
                self.schedule_ready_tasks(parent_thread_id=parent_thread_id)
        return tuple(sorted(timed_out))

    def get_result(self, task_id: str):
        return self.registry.get_result(task_id)

    def get_task(self, task_id: str):
        return self.registry.get_task(task_id)

    def serialize_result_payload(self, task_id: str, *, compact: bool = False) -> dict[str, object] | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        result = self.get_result(task_id)
        return self._serialize_result(task, result, compact=compact)

    def wait(self, task_id: str, *, timeout_seconds: int | None = None):
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task id: {task_id}")

        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        while True:
            result = self.get_result(task_id)
            if result is not None:
                return result

            latest = self.get_task(task_id)
            if latest is None:
                raise ValueError(f"unknown task id: {task_id}")
            self.schedule_ready_tasks(parent_thread_id=latest.parent_thread_id)

            future = self._live_futures.get(task_id)
            if future is not None:
                try:
                    future.result(timeout=self._remaining_timeout(deadline))
                except FutureTimeoutError as exc:
                    raise TimeoutError(f"timeout waiting for subagent task '{task_id}'") from exc
                except Exception:
                    # Child failures are materialized into the registry as terminal results.
                    # The control plane should return that typed result instead of re-raising
                    # a raw worker exception into the parent tool layer.
                    pass
                finally:
                    self._close_live_future_if_terminal(task_id)
                continue

            if latest.status not in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}:
                result = self.get_result(task_id)
                if result is not None:
                    return result
                raise RuntimeError(f"subagent task '{task_id}' has no terminal result yet")

            sleep_seconds = self._pending_wait_sleep_seconds(deadline)
            if sleep_seconds is None:
                raise TimeoutError(f"timeout waiting for subagent task '{task_id}'")
            time.sleep(sleep_seconds)

    def wait_many(
        self,
        *,
        parent_thread_id: str,
        parent_run_id: str | None = None,
        task_ids: tuple[str, ...] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        selected = self._select_tasks_for_wait(
            parent_thread_id=parent_thread_id,
            parent_run_id=parent_run_id,
            task_ids=task_ids,
        )
        selected = tuple(sorted(selected, key=lambda task: (task.created_at, task.task_id)))
        items: list[dict[str, object]] = []
        for task in selected:
            try:
                result = self.wait(task.task_id, timeout_seconds=timeout_seconds)
            except TimeoutError as exc:
                latest = self.get_task(task.task_id) or task
                items.append(
                    {
                        **self._serialize_task(latest),
                        "status": "waiting",
                        "summary": "",
                        "error": str(exc),
                    }
                )
                continue
            except Exception as exc:  # noqa: BLE001
                latest = self.get_task(task.task_id) or task
                items.append(
                    {
                        **self._serialize_task(latest),
                        "summary": "",
                        "error": str(exc),
                    }
                )
                continue
            latest = self.get_task(task.task_id) or task
            items.append(self._serialize_result(latest, result, compact=True))

        terminal_statuses = {"completed", "failed", "cancelled", "timed_out", "interrupted", "failed_recovery"}
        return {
            "items": items,
            "all_terminal": all(str(item.get("status")) in terminal_statuses for item in items),
            "active_remaining": [
                self._serialize_task(task)
                for task in self.list_active_tasks(parent_thread_id=parent_thread_id)
                if parent_run_id is None or task.parent_run_id == parent_run_id
            ],
        }

    def close_task(self, task_id: str) -> None:
        self._live_futures.pop(task_id, None)
        with self._scheduler_lock:
            self._pending_runners.pop(task_id, None)
            self._pending_max_concurrency.pop(task_id, None)

    def close(self) -> None:
        self._live_futures.clear()
        with self._scheduler_lock:
            self._pending_runners.clear()
            self._pending_max_concurrency.clear()
        if hasattr(self.registry, "close"):
            self.registry.close()

    def list_tasks(
        self,
        *,
        parent_thread_id: str | None = None,
        statuses: set[SubagentTaskStatus] | None = None,
    ) -> tuple[SubagentTaskRecord, ...]:
        tasks = self.registry.list_tasks()
        filtered: list[SubagentTaskRecord] = []
        for task in tasks:
            if parent_thread_id is not None and task.parent_thread_id != parent_thread_id:
                continue
            if statuses is not None and task.status not in statuses:
                continue
            filtered.append(task)
        return tuple(filtered)

    def list_active_tasks(self, *, parent_thread_id: str | None = None) -> tuple[SubagentTaskRecord, ...]:
        return self.list_tasks(
            parent_thread_id=parent_thread_id,
            statuses={SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING},
        )

    def build_dependency_graph(
        self,
        *,
        parent_thread_id: str,
        parent_run_id: str | None = None,
    ) -> dict[str, object]:
        parent_tasks = self.list_tasks(parent_thread_id=parent_thread_id)
        parent_task_by_id = {task.task_id: task for task in parent_tasks}
        selected_tasks = tuple(
            task
            for task in parent_tasks
            if parent_run_id is None or task.parent_run_id == parent_run_id
        )
        selected_tasks = tuple(
            sorted(
                selected_tasks,
                key=lambda task: (task.created_at, task.task_id),
            )
        )

        edges: list[dict[str, object]] = []
        dependency_states_by_task_id: dict[str, str] = {}
        missing_dependency_task_ids: set[str] = set()
        for task in selected_tasks:
            edge_statuses: list[str] = []
            for dependency_task_id in task.depends_on_task_ids:
                dependency_task = parent_task_by_id.get(dependency_task_id)
                edge_status = self._dependency_edge_status(dependency_task)
                edge_statuses.append(edge_status)
                if edge_status == "missing":
                    missing_dependency_task_ids.add(dependency_task_id)
                edges.append(
                    {
                        "source_task_id": dependency_task_id,
                        "target_task_id": task.task_id,
                        "status": edge_status,
                        "source_status": dependency_task.status.value if dependency_task is not None else None,
                    }
                )
            dependency_states_by_task_id[task.task_id] = self._dependency_state(edge_statuses)

        nodes: list[dict[str, object]] = []
        ready_task_ids: list[str] = []
        waiting_task_ids: list[str] = []
        blocked_task_ids: list[str] = []
        for task in selected_tasks:
            dependency_state = dependency_states_by_task_id.get(task.task_id, "ready")
            node = self._serialize_task(task)
            node["dependency_state"] = dependency_state
            nodes.append(node)
            if dependency_state == "ready":
                ready_task_ids.append(task.task_id)
            elif dependency_state == "waiting":
                waiting_task_ids.append(task.task_id)
            else:
                blocked_task_ids.append(task.task_id)

        return {
            "parent_thread_id": parent_thread_id,
            "parent_run_id": parent_run_id,
            "nodes": nodes,
            "edges": edges,
            "ready_task_ids": ready_task_ids,
            "waiting_task_ids": waiting_task_ids,
            "blocked_task_ids": blocked_task_ids,
            "missing_dependency_task_ids": sorted(missing_dependency_task_ids),
        }

    def cancel_for_parent_thread(
        self,
        parent_thread_id: str,
        *,
        reason: str = "parent thread completed",
    ) -> tuple[str, ...]:
        cancelled: list[str] = []
        for task in self.list_active_tasks(parent_thread_id=parent_thread_id):
            self.cancel(task.task_id, reason=reason)
            cancelled.append(task.task_id)
        return tuple(sorted(cancelled))

    def recover_orphaned_tasks(self) -> tuple[str, ...]:
        interrupted: list[str] = []
        for task in self.registry.list_tasks():
            if task.status not in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}:
                continue
            self.registry.terminalize_task(
                task.task_id,
                status=SubagentTaskStatus.INTERRUPTED,
                summary="",
                error="subagent runtime interrupted before recovery could reattach live worker",
                completed_at=datetime.now(timezone.utc),
            )
            refreshed = self.registry.get_task(task.task_id)
            if refreshed is not None:
                self._publish_event(
                    refreshed,
                    event_type=SubagentEventType.JOB_INTERRUPTED,
                    payload={
                        "status": refreshed.status.value,
                        "error": refreshed.error or "subagent runtime interrupted before recovery could reattach live worker",
                    },
                )
            interrupted.append(task.task_id)
        return tuple(sorted(interrupted))

    def delete_for_parent_thread(self, parent_thread_id: str) -> int:
        for task in self.list_active_tasks(parent_thread_id=parent_thread_id):
            self.cancel(task.task_id, reason="cleared by thread rewrite")
        deleted = 0
        if hasattr(self.registry, "delete_for_parent_thread"):
            deleted = int(self.registry.delete_for_parent_thread(parent_thread_id))
        for task_id in list(self._live_futures):
            task = self.get_task(task_id)
            if task is None or task.parent_thread_id == parent_thread_id:
                self._live_futures.pop(task_id, None)
        return deleted

    def intersect_tool_names(
        self,
        *,
        parent_visible_tool_names: tuple[str, ...],
        requested_tool_names: tuple[str, ...],
    ) -> tuple[str, ...]:
        hard_blocked = {"delegated_task", "subagent", "delegate_batch", "delegate_status", "delegate_cancel", "ask_clarification"}
        if not requested_tool_names:
            return tuple(sorted(name for name in parent_visible_tool_names if name not in hard_blocked and name != "capability_search"))
        parent = set(parent_visible_tool_names)
        return tuple(sorted(name for name in requested_tool_names if name in parent and name not in hard_blocked))

    def build_child_thread_id(self, *, parent_thread_id: str, task_id: str) -> str:
        suffix = task_id.replace("subagent-", "", 1)
        return f"{parent_thread_id}--subagent--{suffix}"

    def build_child_run_id(self, *, task_id: str) -> str:
        suffix = task_id.replace("subagent-", "", 1)
        return f"run-subagent-{suffix}"

    def build_tool(
        self,
        *,
        thread_id: str,
        config_result: ConfigResolutionResult,
        feature_set: RuntimeFeatureSet,
        parent_visible_tool_names: tuple[str, ...],
        execution_mode: object,
        parent_run_id: str | None = None,
        trace_id: str | None = None,
    ) -> ToolRegistryEntry:
        from langchain_core.tools import tool

        @tool(description="Delegate a bounded task to a subagent with the parent's visible tool intersection. Optionally request a narrower child tool subset or declare dependencies on earlier task_ids.")
        def delegated_task(
            prompt: str,
            requested_tool_names: list[str] | None = None,
            depends_on_task_ids: list[str] | None = None,
        ) -> str:
            task = self.submit(
                parent_thread_id=thread_id,
                parent_run_id=parent_run_id,
                prompt=prompt,
                parent_visible_tool_names=parent_visible_tool_names,
                config_result=config_result,
                requested_tool_names=tuple(requested_tool_names or ()),
                depends_on_task_ids=tuple(depends_on_task_ids or ()),
                parent_delegation_depth=0,
                trace_id=trace_id,
                execution_mode=execution_mode,
            )
            return json.dumps(
                {
                    "task_id": task.task_id,
                    "status": task.status.value,
                    "parent_thread_id": task.parent_thread_id,
                    "parent_run_id": task.parent_run_id,
                    "batch_id": task.batch_id,
                    "child_thread_id": task.child_thread_id,
                    "child_run_id": task.child_run_id,
                    "prompt_preview": task.prompt_preview,
                    "workspace_mode": task.workspace_mode,
                    "allowed_tool_names": list(task.allowed_tool_names),
                    "depends_on_task_ids": list(task.depends_on_task_ids),
                    "hint": "Use subagent with action join for active jobs, or wait/get/result/cancel with task_id for one job.",
                },
                ensure_ascii=False,
            )

        return ToolRegistryEntry(
            name="delegated_task",
            display_name="Delegated Task",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="subagents",
            capability_group="delegation",
            handler=delegated_task,
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "requested_tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "depends_on_task_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["prompt"],
            },
        )

    def build_control_tool(
        self,
        *,
        thread_id: str,
        parent_run_id: str | None = None,
    ) -> ToolRegistryEntry:
        from langchain_core.tools import tool

        @tool(description="Inspect, join, or control delegated subagent jobs for the current thread. Actions: list, active, graph, get, result, wait, join, wait_many, cancel.")
        def subagent(
            action: str,
            task_id: str | None = None,
            task_ids: object | None = None,
            timeout_seconds: int | None = None,
        ) -> str:
            normalized = action.strip().lower()
            if normalized in {"list", "active"}:
                tasks = (
                    self.list_active_tasks(parent_thread_id=thread_id)
                    if normalized == "active"
                    else self.list_tasks(parent_thread_id=thread_id)
                )
                return json.dumps(
                    [self._serialize_task(task) for task in tasks],
                    ensure_ascii=False,
                )

            if normalized == "graph":
                return json.dumps(
                    self.build_dependency_graph(parent_thread_id=thread_id, parent_run_id=parent_run_id),
                    ensure_ascii=False,
                )

            if normalized in {"join", "wait_many"} or (normalized == "wait" and not task_id):
                try:
                    selected_task_ids = self._normalize_task_ids(task_id=task_id, task_ids=task_ids)
                    return json.dumps(
                        self.wait_many(
                            parent_thread_id=thread_id,
                            parent_run_id=parent_run_id,
                            task_ids=selected_task_ids,
                            timeout_seconds=timeout_seconds,
                        ),
                        ensure_ascii=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    return json.dumps({"error": str(exc)}, ensure_ascii=False)

            if not task_id:
                return json.dumps({"error": "task_id is required for this action"}, ensure_ascii=False)

            task = self.get_task(task_id)
            if task is None or task.parent_thread_id != thread_id:
                return json.dumps({"error": f"unknown subagent task: {task_id}"}, ensure_ascii=False)

            if normalized == "get":
                return json.dumps(self._serialize_task(task), ensure_ascii=False)

            if normalized == "result":
                result = self.get_result(task_id)
                return json.dumps(
                    self._serialize_result(task, result),
                    ensure_ascii=False,
                )

            if normalized == "wait":
                try:
                    result = self.wait(task_id, timeout_seconds=timeout_seconds)
                except TimeoutError as exc:
                    return json.dumps(
                        {
                            **self._serialize_task(task),
                            "status": "waiting",
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    )
                return json.dumps(self._serialize_result(task, result), ensure_ascii=False)

            if normalized == "cancel":
                cancelled = self.cancel(task_id)
                result = self.get_result(task_id)
                return json.dumps(self._serialize_result(cancelled, result), ensure_ascii=False)

            return json.dumps({"error": f"unsupported action: {action}"}, ensure_ascii=False)

        return ToolRegistryEntry(
            name="subagent",
            display_name="Subagent Control",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="subagents",
            capability_group="delegation",
            handler=subagent,
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "active", "graph", "get", "result", "wait", "join", "wait_many", "cancel"],
                    },
                    "task_id": {"type": ["string", "null"]},
                    "task_ids": {
                        "oneOf": [
                            {"type": "array", "items": {"type": "string"}},
                            {"type": "string"},
                            {"type": "null"},
                        ]
                    },
                    "timeout_seconds": {"type": ["integer", "null"]},
                },
                "required": ["action"],
            },
        )

    def drain_events(self, *, parent_thread_id: str, parent_run_id: str | None) -> list[SubagentEvent]:
        return self.event_broker.drain(parent_thread_id=parent_thread_id, parent_run_id=parent_run_id)

    def schedule_ready_tasks(self, *, parent_thread_id: str | None = None) -> tuple[str, ...]:
        scheduled: list[str] = []
        blocked: list[str] = []
        while True:
            progressed = False
            with self._scheduler_lock:
                pending_task_ids = tuple(self._pending_runners)

            for task_id in pending_task_ids:
                task = self.get_task(task_id)
                if task is None:
                    self.close_task(task_id)
                    continue
                if parent_thread_id is not None and task.parent_thread_id != parent_thread_id:
                    continue
                if task.status not in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}:
                    self.close_task(task_id)
                    continue

                dependency_state = self._dependency_state_for_task(task)
                if dependency_state == "waiting":
                    continue
                if dependency_state == "blocked":
                    self._terminalize_dependency_blocked_task(task)
                    blocked.append(task.task_id)
                    progressed = True
                    continue

                max_concurrency = self._pending_max_concurrency.get(task.task_id)
                if max_concurrency is not None and not self._execution_capacity_available(
                    max_concurrency=max_concurrency,
                    excluding_task_id=task.task_id,
                ):
                    continue
                with self._scheduler_lock:
                    runner = self._pending_runners.pop(task.task_id, None)
                    self._pending_max_concurrency.pop(task.task_id, None)
                if runner is None:
                    continue
                self._start_task(task, runner)
                scheduled.append(task.task_id)
                progressed = True

            if not progressed:
                break
        return tuple(sorted(dict.fromkeys([*scheduled, *blocked])))

    def _start_task(self, task: SubagentTaskRecord, runner: Callable[[], object]) -> None:
        latest = self.get_task(task.task_id) or task
        if latest.status is not SubagentTaskStatus.QUEUED:
            return
        future = self.executor.submit(latest, runner)
        with self._scheduler_lock:
            self._live_futures[latest.task_id] = future
        future.add_done_callback(lambda _future, task_id=latest.task_id: self._handle_future_done(task_id))

    def _handle_future_done(self, task_id: str) -> None:
        latest = self.get_task(task_id)
        if latest is None:
            self.close_task(task_id)
            return
        self._close_live_future_if_terminal(task_id)
        if latest.status not in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}:
            self.schedule_ready_tasks(parent_thread_id=latest.parent_thread_id)

    def _close_live_future_if_terminal(self, task_id: str) -> None:
        latest = self.get_task(task_id)
        if latest is not None and latest.status not in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}:
            with self._scheduler_lock:
                self._live_futures.pop(task_id, None)

    def _terminalize_dependency_blocked_task(self, task: SubagentTaskRecord) -> SubagentTaskRecord:
        reason = self._dependency_blocked_reason(task)
        with self._scheduler_lock:
            self._pending_runners.pop(task.task_id, None)
            self._pending_max_concurrency.pop(task.task_id, None)
        failed = self.registry.terminalize_task(
            task.task_id,
            status=SubagentTaskStatus.FAILED,
            summary="",
            error=reason,
            completed_at=datetime.now(timezone.utc),
        )
        self._publish_event(
            failed,
            event_type=SubagentEventType.JOB_FAILED,
            payload={
                "status": failed.status.value,
                "error": reason,
                "depends_on_task_ids": list(failed.depends_on_task_ids),
            },
        )
        if self.tracing_service is not None:
            self.tracing_service.subagent_finished(
                parent_trace_id=failed.trace_id,
                task_id=failed.task_id,
                status=failed.status.value,
                error=reason,
            )
        self.schedule_ready_tasks(parent_thread_id=failed.parent_thread_id)
        return failed

    def _enforce_execution_capacity(self, *, max_concurrency: int) -> None:
        if not self._execution_capacity_available(max_concurrency=max_concurrency):
            raise ValueError("subagent concurrency limit reached")

    def _execution_capacity_available(
        self,
        *,
        max_concurrency: int,
        excluding_task_id: str | None = None,
    ) -> bool:
        active_task_ids: set[str] = set()
        with self._scheduler_lock:
            active_task_ids.update(self._live_futures)
        for task in self.registry.list_tasks():
            if task.task_id == excluding_task_id:
                continue
            if task.status is SubagentTaskStatus.RUNNING:
                active_task_ids.add(task.task_id)
        return len(active_task_ids) < max(int(max_concurrency), 1)

    def _dependency_state_for_task(self, task: SubagentTaskRecord) -> str:
        return self._dependency_state_for_dependency_ids(
            parent_thread_id=task.parent_thread_id,
            dependency_task_ids=task.depends_on_task_ids,
        )

    def _dependency_state_for_dependency_ids(
        self,
        *,
        parent_thread_id: str,
        dependency_task_ids: tuple[str, ...],
    ) -> str:
        if not dependency_task_ids:
            return "ready"
        parent_task_by_id = {task.task_id: task for task in self.list_tasks(parent_thread_id=parent_thread_id)}
        edge_statuses = [
            self._dependency_edge_status(parent_task_by_id.get(dependency_task_id))
            for dependency_task_id in dependency_task_ids
        ]
        return self._dependency_state(edge_statuses)

    def _dependency_blocked_reason(self, task: SubagentTaskRecord) -> str:
        blocked: list[str] = []
        parent_task_by_id = {item.task_id: item for item in self.list_tasks(parent_thread_id=task.parent_thread_id)}
        for dependency_task_id in task.depends_on_task_ids:
            dependency_task = parent_task_by_id.get(dependency_task_id)
            edge_status = self._dependency_edge_status(dependency_task)
            if edge_status in {"missing", "terminal_unsatisfied"}:
                source_status = dependency_task.status.value if dependency_task is not None else "missing"
                blocked.append(f"{dependency_task_id}:{source_status}")
        if not blocked:
            return "subagent dependency is not satisfied"
        return f"subagent dependency failed: {', '.join(blocked)}"

    def _remaining_timeout(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FutureTimeoutError()
        return remaining

    def _pending_wait_sleep_seconds(self, deadline: float | None) -> float | None:
        if deadline is None:
            return 0.01
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        return min(0.01, remaining)

    def _publish_event(
        self,
        task: SubagentTaskRecord,
        *,
        event_type: SubagentEventType,
        payload: dict[str, object],
    ) -> None:
        event = SubagentEvent(
            job_id=task.task_id,
            parent_thread_id=task.parent_thread_id,
            parent_run_id=task.parent_run_id,
            event_type=event_type,
            payload={
                "task_id": task.task_id,
                "batch_id": task.batch_id,
                "prompt_preview": task.prompt_preview,
                "child_thread_id": task.child_thread_id,
                "child_run_id": task.child_run_id,
                "status": task.status.value,
                "started_at": task.started_at.isoformat() if task.started_at is not None else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at is not None else None,
                **dict(payload),
            },
        )
        self.event_broker.publish(event)
        if self.event_persister is not None:
            self.event_persister(event)

    def _serialize_task(self, task: SubagentTaskRecord) -> dict[str, object]:
        return {
            "task_id": task.task_id,
            "batch_id": task.batch_id,
            "parent_thread_id": task.parent_thread_id,
            "parent_run_id": task.parent_run_id,
            "child_thread_id": task.child_thread_id,
            "child_run_id": task.child_run_id,
            "status": task.status.value,
            "prompt_preview": task.prompt_preview,
            "assigned_profile": task.assigned_profile,
            "delegation_depth": task.delegation_depth,
            "workspace_mode": task.workspace_mode,
            "cancel_requested": task.cancel_requested,
            "requested_tool_names": list(task.requested_tool_names),
            "allowed_tool_names": list(task.allowed_tool_names),
            "depends_on_task_ids": list(task.depends_on_task_ids),
            "started_at": task.started_at.isoformat() if task.started_at is not None else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at is not None else None,
            "timeout_at": task.timeout_at.isoformat() if task.timeout_at is not None else None,
            "error": task.error,
        }

    def _serialize_result(self, task: SubagentTaskRecord, result, *, compact: bool = False) -> dict[str, object]:
        payload = self._serialize_task(task)
        if result is not None:
            messages = list(result.messages)
            recent_tool_activity = list(result.recent_tool_activity)
            payload.update(
                {
                    "status": result.status.value,
                    "summary": result.summary,
                    "messages": messages[-3:] if compact else messages,
                    "recent_tool_activity": recent_tool_activity[-6:] if compact else recent_tool_activity,
                    "artifacts": list(result.artifacts),
                    "approval_payload": result.approval_payload,
                    "child_thread_id": result.child_thread_id or task.child_thread_id,
                    "child_run_id": result.child_run_id or task.child_run_id,
                    "error": result.error,
                }
            )
        return payload

    def _select_tasks_for_wait(
        self,
        *,
        parent_thread_id: str,
        parent_run_id: str | None,
        task_ids: tuple[str, ...] | None,
    ) -> tuple[SubagentTaskRecord, ...]:
        if task_ids:
            selected: list[SubagentTaskRecord] = []
            for task_id in dict.fromkeys(task_ids):
                task = self.get_task(task_id)
                if task is None:
                    raise ValueError(f"unknown task id: {task_id}")
                if task.parent_thread_id != parent_thread_id:
                    raise ValueError(f"subagent task '{task_id}' does not belong to this thread")
                selected.append(task)
            return tuple(selected)
        all_thread_tasks = self.list_tasks(parent_thread_id=parent_thread_id)
        if parent_run_id is None:
            active = tuple(
                task
                for task in all_thread_tasks
                if task.status in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}
            )
            return active or all_thread_tasks
        current_run_tasks = tuple(
            task
            for task in all_thread_tasks
            if task.parent_run_id == parent_run_id
        )
        if current_run_tasks:
            return current_run_tasks
        return tuple(
            task
            for task in all_thread_tasks
            if task.status in {SubagentTaskStatus.QUEUED, SubagentTaskStatus.RUNNING}
        )

    def _normalize_task_ids(self, *, task_id: str | None, task_ids: object | None) -> tuple[str, ...] | None:
        values: list[str] = []
        if task_id:
            values.append(task_id)
        parsed = self._maybe_json(task_ids)
        if isinstance(parsed, str):
            values.append(parsed)
        elif isinstance(parsed, list):
            values.extend(str(item) for item in parsed if str(item).strip())
        elif isinstance(parsed, tuple):
            values.extend(str(item) for item in parsed if str(item).strip())
        elif parsed is not None:
            values.append(str(parsed))
        normalized = tuple(item.strip() for item in values if item.strip())
        return normalized or None

    def _maybe_json(self, value: object | None) -> object | None:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return None
        if stripped[0] not in "[{":
            return stripped
        try:
            return json.loads(stripped)
        except Exception:
            return stripped

    def _preview_text(self, text: str, *, limit: int = 96) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: limit - 1]}…"

    def _normalize_dependency_task_ids(self, depends_on_task_ids: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not depends_on_task_ids:
            return ()
        normalized: list[str] = []
        for item in depends_on_task_ids:
            value = str(item).strip()
            if value:
                normalized.append(value)
        return tuple(dict.fromkeys(normalized))

    def _validate_dependency_task_ids(
        self,
        *,
        parent_thread_id: str,
        dependency_task_ids: tuple[str, ...],
    ) -> None:
        for dependency_task_id in dependency_task_ids:
            dependency_task = self.get_task(dependency_task_id)
            if dependency_task is None:
                raise ValueError(f"unknown dependency task id: {dependency_task_id}")
            if dependency_task.parent_thread_id != parent_thread_id:
                raise ValueError(
                    f"dependency task '{dependency_task_id}' does not belong to parent thread '{parent_thread_id}'"
                )

    def _dependency_edge_status(self, dependency_task: SubagentTaskRecord | None) -> str:
        if dependency_task is None:
            return "missing"
        if dependency_task.status is SubagentTaskStatus.COMPLETED:
            return "satisfied"
        if dependency_task.status in {
            SubagentTaskStatus.FAILED,
            SubagentTaskStatus.CANCELLED,
            SubagentTaskStatus.TIMED_OUT,
            SubagentTaskStatus.INTERRUPTED,
            SubagentTaskStatus.FAILED_RECOVERY,
        }:
            return "terminal_unsatisfied"
        return "waiting"

    def _dependency_state(self, edge_statuses: list[str]) -> str:
        if not edge_statuses:
            return "ready"
        if all(status == "satisfied" for status in edge_statuses):
            return "ready"
        if any(status in {"missing", "terminal_unsatisfied"} for status in edge_statuses):
            return "blocked"
        return "waiting"
