from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import threading
import time
from pathlib import Path

import pytest

from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService
from anvil.runtime.tool_registry.delegation_factory import DelegationToolFactory
from anvil.subagents import SqliteSubagentRegistry, SubagentService, SubagentTaskStatus
from anvil.subagents.contracts import SubagentEvent, SubagentTaskRecord
from anvil.subagents.event_broker import SubagentEventBroker


def make_config(max_concurrency: int = 3, max_depth: int = 1):
    return ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={"subagents": {"enabled": True, "max_concurrency": max_concurrency, "max_depth": max_depth}},
            )
        ]
    )


def test_subagent_service_enforces_concurrency_limit() -> None:
    service = SubagentService()
    blocker = threading.Event()

    def blocking_runner():
        blocker.wait(timeout=2)
        return "done"

    service.submit(
        parent_thread_id="thread-1",
        prompt="first",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=1),
        runner=blocking_runner,
    )

    with pytest.raises(ValueError, match="concurrency limit"):
        service.submit(
            parent_thread_id="thread-1",
            prompt="second",
            parent_visible_tool_names=("read_file",),
            config_result=make_config(max_concurrency=1),
            runner=lambda: "done",
        )

    blocker.set()


def test_subagent_service_enforces_depth_and_intersection() -> None:
    service = SubagentService()
    with pytest.raises(ValueError, match="depth limit"):
        service.submit(
            parent_thread_id="thread-1",
            prompt="too deep",
            parent_visible_tool_names=("read_file", "write_file"),
            config_result=make_config(max_depth=1),
            parent_delegation_depth=1,
            runner=lambda: "done",
        )

    allowed = service.intersect_tool_names(
        parent_visible_tool_names=("read_file", "write_file"),
        requested_tool_names=("write_file", "delegated_task"),
    )
    assert allowed == ("write_file",)


def test_subagent_worker_transitions_to_terminal_result() -> None:
    service = SubagentService()
    task = service.submit(
        parent_thread_id="thread-1",
        prompt="quick task",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(),
        runner=lambda: "subagent done",
    )

    deadline = time.time() + 2
    while time.time() < deadline:
        result = service.registry.get_result(task.task_id)
        if result is not None:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("subagent result was not recorded in time")

    result = service.registry.get_result(task.task_id)
    assert result is not None
    assert result.status is SubagentTaskStatus.COMPLETED
    assert result.summary == "subagent done"


def test_subagent_service_can_cancel_task_without_late_overwrite() -> None:
    service = SubagentService()
    blocker = threading.Event()

    def blocking_runner():
        blocker.wait(timeout=2)
        return "too late"

    task = service.submit(
        parent_thread_id="thread-1",
        prompt="cancel me",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(),
        runner=blocking_runner,
    )

    cancelled = service.cancel(task.task_id)
    blocker.set()

    assert cancelled.status is SubagentTaskStatus.CANCELLED
    result = service.get_result(task.task_id)
    assert result is not None
    assert result.status is SubagentTaskStatus.CANCELLED


def test_subagent_service_reconciles_timeouts() -> None:
    service = SubagentService()
    task = service.submit(
        parent_thread_id="thread-1",
        prompt="timeout me",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(),
        runner=lambda: "done",
    )
    service.registry.terminalize_task(task.task_id, status=SubagentTaskStatus.QUEUED, completed_at=None)
    current = service.registry.get_task(task.task_id)
    assert current is not None
    current.timeout_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    service.registry.update_task(current)

    timed_out = service.reconcile_timeouts()

    assert task.task_id in timed_out
    result = service.get_result(task.task_id)
    assert result is not None
    assert result.status is SubagentTaskStatus.TIMED_OUT


def test_subagent_service_wait_returns_terminal_result() -> None:
    service = SubagentService()
    task = service.submit(
        parent_thread_id="thread-1",
        prompt="wait for me",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(),
        runner=lambda: "waited",
    )

    result = service.wait(task.task_id, timeout_seconds=2)

    assert result.summary == "waited"
    assert result.status is SubagentTaskStatus.COMPLETED


def test_subagent_service_join_waits_current_active_tasks_without_task_id() -> None:
    service = SubagentService()
    first = service.submit(
        parent_thread_id="thread-join",
        parent_run_id="run-join",
        prompt="first",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(),
        runner=lambda: "first done",
    )
    second = service.submit(
        parent_thread_id="thread-join",
        parent_run_id="run-join",
        prompt="second",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(),
        runner=lambda: "second done",
    )

    joined = service.wait_many(parent_thread_id="thread-join", parent_run_id="run-join", timeout_seconds=2)

    assert joined["all_terminal"] is True
    payload_by_id = {item["task_id"]: item for item in joined["items"]}
    assert payload_by_id[first.task_id]["summary"] == "first done"
    assert payload_by_id[second.task_id]["summary"] == "second done"
    assert joined["active_remaining"] == []


def test_subagent_service_builds_dependency_graph_for_parent_thread() -> None:
    service = SubagentService()
    blocker = threading.Event()
    second_started = threading.Event()
    first = service.submit(
        parent_thread_id="thread-graph",
        parent_run_id="run-graph",
        prompt="inspect shared API",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=2),
        runner=lambda: "first done" if blocker.wait(timeout=2) else "first done",
    )
    second = service.submit(
        parent_thread_id="thread-graph",
        parent_run_id="run-graph",
        prompt="summarize after inspection",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=2),
        depends_on_task_ids=(first.task_id,),
        runner=lambda: (second_started.set() or "second done"),
    )

    graph = service.build_dependency_graph(parent_thread_id="thread-graph", parent_run_id="run-graph")

    assert second_started.wait(timeout=0.05) is False
    assert [edge["source_task_id"] for edge in graph["edges"]] == [first.task_id]
    assert graph["edges"][0]["target_task_id"] == second.task_id
    assert graph["edges"][0]["status"] == "waiting"
    nodes = {node["task_id"]: node for node in graph["nodes"]}
    assert nodes[first.task_id]["dependency_state"] == "ready"
    assert nodes[second.task_id]["dependency_state"] == "waiting"
    assert graph["waiting_task_ids"] == [second.task_id]

    blocker.set()
    service.wait(first.task_id, timeout_seconds=2)
    ready_graph = service.build_dependency_graph(parent_thread_id="thread-graph", parent_run_id="run-graph")

    assert ready_graph["edges"][0]["status"] == "satisfied"
    ready_nodes = {node["task_id"]: node for node in ready_graph["nodes"]}
    assert ready_nodes[second.task_id]["dependency_state"] == "ready"
    assert second.task_id in ready_graph["ready_task_ids"]


def test_subagent_dependencies_wait_until_prerequisite_completes() -> None:
    service = SubagentService()
    blocker = threading.Event()
    first_started = threading.Event()
    second_started = threading.Event()
    execution_order: list[str] = []

    def first_runner():
        first_started.set()
        blocker.wait(timeout=2)
        execution_order.append("first")
        return "first done"

    def second_runner():
        second_started.set()
        execution_order.append("second")
        return "second done"

    first = service.submit(
        parent_thread_id="thread-dependent-run",
        parent_run_id="run-dependent-run",
        prompt="inspect first",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=1),
        runner=first_runner,
    )
    assert first_started.wait(timeout=1) is True

    second = service.submit(
        parent_thread_id="thread-dependent-run",
        parent_run_id="run-dependent-run",
        prompt="summarize second",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=1),
        depends_on_task_ids=(first.task_id,),
        runner=second_runner,
    )

    assert second_started.wait(timeout=0.05) is False
    waiting = service.get_task(second.task_id)
    assert waiting is not None
    assert waiting.status is SubagentTaskStatus.QUEUED

    blocker.set()
    assert service.wait(first.task_id, timeout_seconds=2).summary == "first done"
    assert service.wait(second.task_id, timeout_seconds=2).summary == "second done"
    assert execution_order == ["first", "second"]


def test_subagent_dependency_failure_terminalizes_dependent_without_running_it() -> None:
    service = SubagentService()
    fail_now = threading.Event()
    second_started = threading.Event()

    def failing_runner():
        fail_now.wait(timeout=2)
        raise RuntimeError("source failed")

    first = service.submit(
        parent_thread_id="thread-dependent-failure",
        parent_run_id="run-dependent-failure",
        prompt="fail first",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=2),
        runner=failing_runner,
    )
    second = service.submit(
        parent_thread_id="thread-dependent-failure",
        parent_run_id="run-dependent-failure",
        prompt="must not run",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=2),
        depends_on_task_ids=(first.task_id,),
        runner=lambda: (second_started.set() or "should not run"),
    )

    assert second_started.wait(timeout=0.05) is False
    fail_now.set()
    first_result = service.wait(first.task_id, timeout_seconds=2)
    second_result = service.wait(second.task_id, timeout_seconds=2)

    assert first_result.status is SubagentTaskStatus.FAILED
    assert second_result.status is SubagentTaskStatus.FAILED
    assert second_started.is_set() is False
    assert second_result.error is not None
    assert "subagent dependency failed" in second_result.error
    assert first.task_id in second_result.error


def test_subagent_service_rejects_dependencies_outside_parent_thread() -> None:
    service = SubagentService()
    other = service.submit(
        parent_thread_id="thread-other",
        prompt="other work",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=2),
        runner=lambda: "other done",
    )

    with pytest.raises(ValueError, match="does not belong to parent thread"):
        service.submit(
            parent_thread_id="thread-current",
            prompt="bad dependency",
            parent_visible_tool_names=("read_file",),
            config_result=make_config(max_concurrency=2),
            depends_on_task_ids=(other.task_id,),
            runner=lambda: "done",
        )


def test_subagent_control_tool_returns_dependency_graph() -> None:
    service = SubagentService()
    first = service.submit(
        parent_thread_id="thread-control",
        parent_run_id="run-control",
        prompt="source",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=2),
        runner=lambda: "source done",
    )
    service.wait(first.task_id, timeout_seconds=2)
    second = service.submit(
        parent_thread_id="thread-control",
        parent_run_id="run-control",
        prompt="target",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(max_concurrency=2),
        depends_on_task_ids=(first.task_id,),
        runner=lambda: "target done",
    )
    entry = service.build_control_tool(thread_id="thread-control", parent_run_id="run-control")

    payload = json.loads(entry.handler.invoke({"action": "graph"}))

    assert payload["parent_thread_id"] == "thread-control"
    assert payload["parent_run_id"] == "run-control"
    assert payload["edges"] == [
        {
            "source_task_id": first.task_id,
            "target_task_id": second.task_id,
            "status": "satisfied",
            "source_status": "completed",
        }
    ]


def test_delegate_batch_accepts_json_string_prompts() -> None:
    service = SubagentService(default_runner_factory=lambda **_: (lambda: "done"))
    factory = DelegationToolFactory(subagent_service=service)
    entry = factory.build_tools(
        config_result=make_config(max_concurrency=3),
        thread_id="thread-batch",
        parent_visible_tool_names=("read_file",),
        execution_mode=None,
        feature_set=None,
        parent_run_id="run-batch",
        trace_id=None,
    )[0]

    raw = entry.handler.invoke({"prompts": json.dumps(["inspect config", "summarize docs"])})
    payload = json.loads(raw)

    assert payload["batch_id"].startswith("delegate-batch-")
    assert len(payload["tasks"]) == 2
    assert all(str(item["task_id"]).startswith("subagent-") for item in payload["tasks"])
    assert all(item["prompt_preview"] for item in payload["tasks"])


def test_delegation_tools_use_explicit_structured_schemas(monkeypatch) -> None:
    import langchain_core.tools as langchain_tools

    def fail_tool_decorator(*args, **kwargs):  # pragma: no cover - failure path proves accidental use
        raise AssertionError("delegation tools should use explicit StructuredTool handlers")

    monkeypatch.setattr(langchain_tools, "tool", fail_tool_decorator)
    service = SubagentService(default_runner_factory=lambda **_: (lambda: "done"))
    factory = DelegationToolFactory(subagent_service=service)

    entries = factory.build_tools(
        config_result=make_config(max_concurrency=3),
        thread_id="thread-static-delegation",
        parent_visible_tool_names=("read_file",),
        execution_mode=None,
        feature_set=None,
        parent_run_id="run-static-delegation",
        trace_id=None,
    )

    assert [entry.name for entry in entries] == ["delegate_batch", "delegate_status", "delegate_cancel"]
    for entry in entries:
        assert entry.handler.name == entry.name
        assert entry.handler.args_schema == entry.input_schema


def test_delegate_batch_maps_dependency_keys_to_task_ids() -> None:
    service = SubagentService(default_runner_factory=lambda **_: (lambda: "done"))
    factory = DelegationToolFactory(subagent_service=service)
    entry = factory.build_tools(
        config_result=make_config(max_concurrency=3),
        thread_id="thread-batch-graph",
        parent_visible_tool_names=("read_file",),
        execution_mode=None,
        feature_set=None,
        parent_run_id="run-batch-graph",
        trace_id=None,
    )[0]

    raw = entry.handler.invoke(
        {
            "tasks": [
                {"key": "inspect", "prompt": "inspect the API"},
                {"prompt": "summarize the API", "depends_on": ["inspect"]},
            ]
        }
    )
    payload = json.loads(raw)

    first, second = payload["tasks"]
    assert second["depends_on_task_ids"] == [first["task_id"]]
    graph = service.build_dependency_graph(parent_thread_id="thread-batch-graph", parent_run_id="run-batch-graph")
    assert graph["edges"][0]["source_task_id"] == first["task_id"]
    assert graph["edges"][0]["target_task_id"] == second["task_id"]


def test_sqlite_subagent_registry_persists_and_recovery_marks_orphans_interrupted(contract_tmp_path: Path) -> None:
    registry_path = contract_tmp_path / "subagents.sqlite3"
    registry = SqliteSubagentRegistry(registry_path)
    prerequisite = SubagentTaskRecord(
        task_id="subagent-prereq",
        parent_thread_id="thread-1",
        child_thread_id="thread-1--subagent--prereq",
        child_run_id="run-subagent-prereq",
        prompt_preview="complete first",
        status=SubagentTaskStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
    )
    task = SubagentTaskRecord(
        task_id="subagent-blocked",
        parent_thread_id="thread-1",
        child_thread_id="thread-1--subagent--blocked",
        child_run_id="run-subagent-blocked",
        prompt_preview="block forever",
        status=SubagentTaskStatus.RUNNING,
        depends_on_task_ids=(prerequisite.task_id,),
    )
    registry.add_task(prerequisite)
    registry.add_task(task)
    registry.close()

    restored = SubagentService(registry=SqliteSubagentRegistry(registry_path))
    recovered_task = restored.get_task(task.task_id)
    recovered_result = restored.get_result(task.task_id)

    assert recovered_task is not None
    assert recovered_task.status is SubagentTaskStatus.INTERRUPTED
    assert recovered_task.depends_on_task_ids == (prerequisite.task_id,)
    assert recovered_result is not None
    assert recovered_result.status is SubagentTaskStatus.INTERRUPTED


def test_subagent_service_publishes_normalized_lifecycle_events() -> None:
    broker = SubagentEventBroker()
    persisted: list[SubagentEvent] = []
    service = SubagentService(
        event_broker=broker,
        event_persister=persisted.append,
    )

    task = service.submit(
        parent_thread_id="thread-events",
        parent_run_id="run-events",
        prompt="quick task",
        parent_visible_tool_names=("read_file",),
        config_result=make_config(),
        runner=lambda: "broker done",
    )
    result = service.wait(task.task_id, timeout_seconds=2)

    assert result.summary == "broker done"
    deadline = time.time() + 2
    drained: list[SubagentEvent] = []
    while time.time() < deadline:
        drained = broker.drain(parent_thread_id="thread-events", parent_run_id="run-events")
        if any(event.event_type == "job_completed" for event in drained):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("subagent broker did not publish completion events in time")

    assert [event.event_type for event in drained][:2] == ["job_submitted", "job_started"]
    assert drained[-1].event_type == "job_completed"
    assert all(event.job_id == task.task_id for event in drained)
    assert all(event.parent_thread_id == "thread-events" for event in drained)
    assert all(event.parent_run_id == "run-events" for event in drained)
    assert persisted[-1].event_type == "job_completed"
