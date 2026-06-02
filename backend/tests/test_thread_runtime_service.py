from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from anvil.agents import ThreadExecutionMode, ThreadLifecycleStatus, ThreadState
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.store import StoreBackend, create_store
from anvil.runtime.thread_service import ThreadRuntimeService
from anvil.sandbox import ArtifactKind, PathService


def test_thread_runtime_service_creates_thread_and_persists_metadata(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    service = ThreadRuntimeService(
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
    )

    metadata = service.create_thread(thread_id="thread-service")

    assert metadata.thread_id == "thread-service"
    assert checkpointer.get_thread_state("thread-service") is not None
    assert store.get_thread_metadata("thread-service") is not None


def test_thread_runtime_service_updates_settings_inside_harness(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    service = ThreadRuntimeService(
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
    )
    service.create_thread(thread_id="thread-settings")

    updated = service.update_thread_settings(
        "thread-settings",
        execution_mode=ThreadExecutionMode.FULL_ACCESS,
        selected_model="openai_compatible",
        selected_profile="coder",
        selected_reasoning_effort="high",
    )

    assert updated.execution.execution_mode is ThreadExecutionMode.FULL_ACCESS
    assert updated.execution.selected_model == "openai_compatible"
    assert updated.execution.selected_profile == "coder"
    assert updated.execution.selected_reasoning_effort == "high"


def test_thread_runtime_service_settings_do_not_change_last_message_recency(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    service = ThreadRuntimeService(
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
    )
    service.create_thread(thread_id="thread-settings-recency")
    state = checkpointer.get_thread_state("thread-settings-recency")
    assert state is not None
    last_message_at = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
    state.conversation.last_message_at = last_message_at
    checkpointer.put_thread_state(state)

    updated = service.update_thread_settings(
        "thread-settings-recency",
        selected_model="minimax_cn",
    )

    assert updated.conversation.last_message_at == last_message_at


def test_thread_runtime_service_rewrite_updates_last_message_recency(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    service = ThreadRuntimeService(
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
    )
    service.create_thread(thread_id="thread-rewrite-recency")
    state = checkpointer.get_thread_state("thread-rewrite-recency")
    assert state is not None
    state.conversation.messages = [{"id": "user-1", "role": "human", "content": "old"}]
    state.conversation.last_message_at = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
    checkpointer.put_thread_state(state)

    updated = service.rewrite_latest_user_message(
        "thread-rewrite-recency",
        message_id="user-1",
        content="new",
    )

    assert updated.conversation.last_message_at is not None
    assert updated.conversation.last_message_at > datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)


def test_thread_runtime_service_interrupts_lifecycle_and_running_steps(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    service = ThreadRuntimeService(
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
    )
    service.create_thread(thread_id="thread-interrupt")
    state = checkpointer.get_thread_state("thread-interrupt")
    assert state is not None
    state.lifecycle.status = ThreadLifecycleStatus.RUNNING
    state.conversation.steps = [
        {"step_id": "think", "status": "running", "payload": "still thinking"},
        {"step_id": "done", "status": "success", "payload": "finished"},
    ]
    state.execution.recent_tool_activity = [
        {"tool_call_id": "call-1", "name": "read_file", "status": "running", "started_at": datetime.now(timezone.utc)},
        {"tool_call_id": "call-2", "name": "list_dir", "status": "completed"},
    ]
    checkpointer.put_thread_state(state)

    updated = service.request_thread_interrupt("thread-interrupt", reason="User stopped the run")

    assert updated.lifecycle.status is ThreadLifecycleStatus.INTERRUPTED
    assert updated.lifecycle.last_error == "User stopped the run"
    assert updated.execution.cancellation_requested is True
    assert updated.execution.last_message_interrupted is True
    assert updated.conversation.steps[0]["status"] == "error"
    assert updated.conversation.steps[0]["error"] == "User stopped the run"
    assert updated.conversation.steps[1]["status"] == "success"
    assert updated.execution.recent_tool_activity[0]["status"] == "interrupted"
    assert updated.execution.recent_tool_activity[1]["status"] == "completed"
    assert store.get_thread_metadata("thread-interrupt") is not None


def test_thread_runtime_service_builds_canonical_artifact_refs(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    service = ThreadRuntimeService(
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
    )
    service.create_thread(thread_id="thread-artifacts")
    state = checkpointer.get_thread_state("thread-artifacts")
    assert state is not None
    state.artifacts.uploaded_files = [
        path_service.to_artifact_descriptor("thread-artifacts", ArtifactKind.UPLOADS, "notes.txt").model_dump(mode="json")
    ]
    state.artifacts.output_artifacts = ["report.md"]
    checkpointer.put_thread_state(state)

    refs = service.build_artifact_refs("thread-artifacts")

    assert refs["uploads"][0]["artifact_url"].endswith("/threads/thread-artifacts/artifacts/uploads/notes.txt")
    assert refs["outputs"][0].artifact_url.endswith("/threads/thread-artifacts/artifacts/outputs/report.md")
    assert refs["outputs"][0].virtual_path == "/mnt/user-data/outputs/report.md"


def test_thread_runtime_service_updates_and_clears_external_workspace_override(contract_tmp_path) -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    path_service = PathService(contract_tmp_path / "threads")
    service = ThreadRuntimeService(
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
    )
    service.create_thread(thread_id="thread-workspace")
    external_workspace = contract_tmp_path / "external-workspace"
    external_workspace.mkdir(parents=True, exist_ok=True)

    updated = service.update_thread_settings(
        "thread-workspace",
        workspace_root=str(external_workspace),
    )
    assert updated.thread_data.workspace_mode == "external"
    assert updated.thread_data.workspace_root == str(external_workspace.resolve())
    assert Path(updated.thread_data.workspace_path) == external_workspace.resolve()

    reset = service.update_thread_settings(
        "thread-workspace",
        workspace_root="",
    )
    assert reset.thread_data.workspace_mode == "thread"
    assert reset.thread_data.workspace_root is None
    assert Path(reset.thread_data.workspace_path) == (contract_tmp_path / "threads" / "thread-workspace" / "workspace").resolve()
