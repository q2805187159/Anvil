from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from langchain_core.messages import AIMessage
from pydantic import ValidationError
import pytest

from app.contracts import (
    ArtifactRefView,
    QueuedFollowUpCreateRequest,
    QueuedFollowUpUpdateRequest,
    RunRequestBody,
    RuntimePhaseTimingsView,
    ThreadSettingsUpdateRequest,
    UserInteractionResumeRequest,
    UserInteractionSubmitRequest,
)
from app.gateway import services
from app.gateway.services import GatewayAdapterError
from anvil.agents import ThreadExecutionMode, ThreadLifecycleStatus, ThreadMetadataView, ThreadState
from anvil.runtime.runs import EMPTY_FINAL_ASSISTANT_MESSAGE
from fake_models import BindableFakeMessagesListChatModel


def test_create_list_read_and_state_thread_views(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()

    created = services.create_thread(deps, "thread-alpha")
    assert created.thread_id == "thread-alpha"

    listed = services.list_threads(deps)
    assert [item.thread_id for item in listed] == ["thread-alpha"]

    single = services.get_thread_view(deps, "thread-alpha")
    assert single.status == "ready"

    state = services.get_thread_state_view(deps, "thread-alpha")
    assert state.thread_id == "thread-alpha"
    assert state.run_id is None
    assert state.status == "ready"
    assert state.execution_mode == "agent"
    assert state.recent_tool_activity == []
    assert state.token_usage == {}
    assert state.selected_model is None
    assert state.recent_approval_events == []
    assert state.runtime_operator_status.status == "ready"
    assert state.runtime_operator_status.timeline == []
    assert state.workspace_mode == "thread"
    assert state.workspace_root is None
    assert state.resolved_workspace_path.endswith("thread-alpha\\workspace")
    assert state.uploads_path.endswith("thread-alpha\\uploads")
    assert state.outputs_path.endswith("thread-alpha\\outputs")


def test_thread_state_view_exposes_current_run_id_for_event_reconnect(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-run-id")
    state = deps.checkpointer.get_thread_state("thread-run-id")
    assert state is not None
    state.identity.run_id = "run-current"
    deps.checkpointer.put_thread_state(state)

    projected = services.get_thread_state_view(deps, "thread-run-id")

    assert projected.run_id == "run-current"


def test_thread_state_endpoint_defaults_to_light_chat_scope(gateway_app_factory, monkeypatch) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-light-state")
    heavy_calls = 0

    def fail_heavy_capability_view(_deps):
        nonlocal heavy_calls
        heavy_calls += 1
        raise AssertionError("default state endpoint must not build full runtime capabilities")

    monkeypatch.setattr(services, "build_runtime_capabilities_view", fail_heavy_capability_view)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.get("/threads/thread-light-state/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["thread_id"] == "thread-light-state"
    assert payload["visible_tool_names"] == []
    assert payload["deferred_tool_names"] == []
    assert payload["enabled_skill_ids"] == []
    assert payload["runtime_path_roots"] == []
    assert payload["subagent_tasks"] == []
    assert payload["process_sessions"] == []
    assert "plan_mode_enabled" in payload["runtime_capabilities"]
    assert payload["runtime_capabilities"]["skills_count"] == 0
    assert heavy_calls == 0


def test_thread_state_endpoint_supports_explicit_full_scope(gateway_app_factory, monkeypatch) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-full-state")
    calls = 0
    original_full_view = services.build_runtime_capabilities_view

    def capture_full_capability_view(_deps):
        nonlocal calls
        calls += 1
        return original_full_view(_deps)

    monkeypatch.setattr(services, "build_runtime_capabilities_view", capture_full_capability_view)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.get("/threads/thread-full-state/state?state_scope=full")

    assert response.status_code == 200
    assert response.json()["thread_id"] == "thread-full-state"
    assert calls == 1


def test_runtime_timeline_adds_missing_phase_timing_marks() -> None:
    timeline = services.build_runtime_timeline_items(
        runtime_phase_timings=RuntimePhaseTimingsView(
            run_id="run-1",
            status="completed",
            started_at=datetime(2026, 5, 27, 1, 0, tzinfo=timezone.utc),
            marks=[],
            first_model_event_elapsed_ms=120,
            first_content_delta_elapsed_ms=240,
            completed_elapsed_ms=360,
        ),
        visible_tools=[],
        approval_events=[],
        subagent_tasks=[],
        process_sessions=[],
        limit=10,
    )

    runtime_phase_names = [item.source_kind for item in timeline if item.kind == "runtime"]

    assert runtime_phase_names == ["run_completed_emitted", "first_content_delta", "first_model_event"]


def test_list_threads_uses_latest_updated_thread_first(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-old")
    services.create_thread(deps, "thread-new")

    old_state = deps.checkpointer.get_thread_state("thread-old")
    assert old_state is not None
    old_state.lifecycle.updated_at = datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc)
    old_state.conversation.last_message_at = datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc)
    deps.checkpointer.put_thread_state(old_state)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(old_state))

    new_state = deps.checkpointer.get_thread_state("thread-new")
    assert new_state is not None
    new_state.lifecycle.updated_at = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
    new_state.conversation.last_message_at = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
    deps.checkpointer.put_thread_state(new_state)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(new_state))

    assert [item.thread_id for item in services.list_threads(deps)] == ["thread-new", "thread-old"]

    old_state.lifecycle.updated_at = datetime(2026, 5, 23, 11, 0, tzinfo=timezone.utc)
    old_state.conversation.last_message_at = datetime(2026, 5, 23, 11, 0, tzinfo=timezone.utc)
    deps.checkpointer.put_thread_state(old_state)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(old_state))

    assert [item.thread_id for item in services.list_threads(deps)] == ["thread-old", "thread-new"]


def test_thread_list_recency_ignores_settings_only_updates(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-settings-edited")
    services.create_thread(deps, "thread-latest-message")

    edited_state = deps.checkpointer.get_thread_state("thread-settings-edited")
    assert edited_state is not None
    edited_state.lifecycle.updated_at = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    edited_state.conversation.last_message_at = datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc)
    deps.checkpointer.put_thread_state(edited_state)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(edited_state))

    latest_state = deps.checkpointer.get_thread_state("thread-latest-message")
    assert latest_state is not None
    latest_state.lifecycle.updated_at = datetime(2026, 5, 23, 11, 0, tzinfo=timezone.utc)
    latest_state.conversation.last_message_at = datetime(2026, 5, 23, 11, 0, tzinfo=timezone.utc)
    deps.checkpointer.put_thread_state(latest_state)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(latest_state))

    listed = services.list_threads(deps)

    assert [item.thread_id for item in listed] == ["thread-latest-message", "thread-settings-edited"]
    assert listed[0].last_message_at == datetime(2026, 5, 23, 11, 0, tzinfo=timezone.utc)


def test_thread_list_reconciles_stale_metadata_recency_from_thread_state(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-stale-metadata")
    services.create_thread(deps, "thread-current")

    stale_state = deps.checkpointer.get_thread_state("thread-stale-metadata")
    assert stale_state is not None
    stale_state.lifecycle.updated_at = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    stale_state.conversation.last_message_at = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    deps.checkpointer.put_thread_state(stale_state)
    stale_metadata = ThreadMetadataView.from_thread_state(stale_state).model_copy(
        update={
            "updated_at": datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc),
            "last_message_at": datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc),
        }
    )
    deps.store.put_thread_metadata(stale_metadata)

    current_state = deps.checkpointer.get_thread_state("thread-current")
    assert current_state is not None
    current_state.lifecycle.updated_at = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
    current_state.conversation.last_message_at = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
    deps.checkpointer.put_thread_state(current_state)
    deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(current_state))

    listed = services.list_threads(deps)

    assert [item.thread_id for item in listed] == ["thread-stale-metadata", "thread-current"]
    assert listed[0].last_message_at == datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    assert deps.store.get_thread_metadata("thread-stale-metadata").last_message_at == datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def test_run_thread_sync_uses_runtime_deps_capability_service(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="shared service hello")])
    )
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-shared-runtime-services")
    assemble_calls: list[dict[str, object]] = []
    original_assemble = deps.capability_assembly_service.assemble

    def assemble_spy(*args, **kwargs):
        assemble_calls.append(dict(kwargs))
        return original_assemble(*args, **kwargs)

    deps.capability_assembly_service.assemble = assemble_spy

    result = services.run_thread_sync(
        deps,
        "thread-shared-runtime-services",
        RunRequestBody(
            message="say hello",
            execution_mode=ThreadExecutionMode.AGENT,
        ),
    )

    assert result.assistant_message == "shared service hello"
    assert len(assemble_calls) == 1
    assert assemble_calls[0]["thread_id"] == "thread-shared-runtime-services"
    assert deps.capability_assembly_service.process_service is deps.process_service
    assert deps.capability_assembly_service.scheduled_task_service is deps.scheduled_task_service


def test_thread_followup_queue_persists_and_dispatches_guidance_first(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-followups")

    first = services.enqueue_thread_followup(
        deps,
        "thread-followups",
        QueuedFollowUpCreateRequest(
            message="normal follow-up",
            uploaded_filenames=["note.txt"],
            uploaded_file_refs=[
                ArtifactRefView(
                    kind="upload",
                    label="note.txt",
                    artifact_url="/threads/thread-followups/artifacts/uploads/note.txt",
                    virtual_path="/mnt/user-data/workspace/uploads/note.txt",
                )
            ],
        ),
    )
    guide = services.enqueue_thread_followup(
        deps,
        "thread-followups",
        QueuedFollowUpCreateRequest(message="steer the next safe turn", mode="guidance"),
    )

    state = services.get_thread_state_view(deps, "thread-followups")
    assert [item.message for item in state.queued_followups] == ["normal follow-up", "steer the next safe turn"]
    assert state.queued_followups[1].mode == "guidance"
    assert state.queued_followups[1].status == "queued"
    assert state.queued_followups[0].uploaded_filenames == ["note.txt"]
    assert state.queued_followups[0].uploaded_file_refs[0].label == "note.txt"

    edited = services.update_thread_followup(
        deps,
        "thread-followups",
        first.queue_id,
        QueuedFollowUpUpdateRequest(message="edited normal follow-up"),
    )
    assert edited.message == "edited normal follow-up"

    next_item = services.pop_next_thread_followup(deps, "thread-followups")
    assert next_item is not None
    assert next_item.queue_id == guide.queue_id
    assert next_item.message == "steer the next safe turn"
    assert next_item.dispatch_id is not None

    services.delete_thread_followup(deps, "thread-followups", first.queue_id)
    remaining_state = deps.checkpointer.get_thread_state("thread-followups")
    assert remaining_state is not None
    assert remaining_state.conversation.queued_followups == []


def test_thread_followup_queue_drains_one_item_per_dispatch(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-followup-drain")

    services.enqueue_thread_followup(
        deps,
        "thread-followup-drain",
        QueuedFollowUpCreateRequest(message="first"),
    )
    services.enqueue_thread_followup(
        deps,
        "thread-followup-drain",
        QueuedFollowUpCreateRequest(message="second"),
    )

    first = services.pop_next_thread_followup(deps, "thread-followup-drain")
    state_after_first = services.get_thread_state_view(deps, "thread-followup-drain")
    assert first is not None
    assert first.dispatch_id is not None
    services.clear_thread_followup_dispatch(deps, "thread-followup-drain", first.dispatch_id)
    second = services.pop_next_thread_followup(deps, "thread-followup-drain")
    state_after_second = services.get_thread_state_view(deps, "thread-followup-drain")

    assert first.message == "first"
    assert [item.message for item in state_after_first.queued_followups] == ["second"]
    assert second is not None
    assert second.message == "second"
    assert state_after_second.queued_followups == []


def test_thread_followup_queue_blocks_second_pop_while_dispatch_in_flight(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-followup-lease")

    services.enqueue_thread_followup(
        deps,
        "thread-followup-lease",
        QueuedFollowUpCreateRequest(message="first"),
    )
    services.enqueue_thread_followup(
        deps,
        "thread-followup-lease",
        QueuedFollowUpCreateRequest(message="second"),
    )

    popped = services.pop_next_thread_followup(deps, "thread-followup-lease")
    assert popped is not None
    assert popped.message == "first"
    assert popped.dispatch_id is not None

    try:
        services.pop_next_thread_followup(deps, "thread-followup-lease")
    except services.GatewayAdapterError as error:
        assert error.status_code == 409
        assert error.error == "thread_followup_dispatch_in_flight"
    else:
        raise AssertionError("expected second pop to be blocked while first queued follow-up is in flight")

    remaining = services.get_thread_state_view(deps, "thread-followup-lease")
    assert [item.message for item in remaining.queued_followups] == ["second"]
    assert remaining.active_followup_dispatch is not None
    assert remaining.active_followup_dispatch.dispatch_id == popped.dispatch_id
    assert remaining.active_followup_dispatch.queue_id == popped.queue_id


def test_thread_followup_queue_recovers_stale_idle_dispatch_lease(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-followup-stale-lease")

    services.enqueue_thread_followup(
        deps,
        "thread-followup-stale-lease",
        QueuedFollowUpCreateRequest(message="first"),
    )
    services.enqueue_thread_followup(
        deps,
        "thread-followup-stale-lease",
        QueuedFollowUpCreateRequest(message="second"),
    )

    first = services.pop_next_thread_followup(deps, "thread-followup-stale-lease")
    assert first is not None

    state = deps.checkpointer.get_thread_state("thread-followup-stale-lease")
    assert state is not None
    assert state.conversation.active_followup_dispatch is not None
    state.conversation.active_followup_dispatch["started_at"] = (
        datetime.now(timezone.utc) - services.FOLLOWUP_DISPATCH_LEASE_TTL - timedelta(seconds=1)
    )
    deps.checkpointer.put_thread_state(state)

    second = services.pop_next_thread_followup(deps, "thread-followup-stale-lease")

    assert second is not None
    assert second.message == "second"
    recovered = services.get_thread_state_view(deps, "thread-followup-stale-lease")
    assert recovered.active_followup_dispatch is not None
    assert recovered.active_followup_dispatch.queue_id == second.queue_id


def test_thread_followup_queue_keeps_stale_dispatch_lease_while_thread_running(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-followup-running-lease")

    services.enqueue_thread_followup(
        deps,
        "thread-followup-running-lease",
        QueuedFollowUpCreateRequest(message="first"),
    )
    services.enqueue_thread_followup(
        deps,
        "thread-followup-running-lease",
        QueuedFollowUpCreateRequest(message="second"),
    )

    first = services.pop_next_thread_followup(deps, "thread-followup-running-lease")
    assert first is not None

    state = deps.checkpointer.get_thread_state("thread-followup-running-lease")
    assert state is not None
    assert state.conversation.active_followup_dispatch is not None
    state.conversation.active_followup_dispatch["started_at"] = (
        datetime.now(timezone.utc) - services.FOLLOWUP_DISPATCH_LEASE_TTL - timedelta(seconds=1)
    )
    state.lifecycle.status = ThreadLifecycleStatus.RUNNING
    deps.checkpointer.put_thread_state(state)

    try:
        services.pop_next_thread_followup(deps, "thread-followup-running-lease")
    except services.GatewayAdapterError as error:
        assert error.status_code == 409
        assert error.error == "thread_followup_dispatch_in_flight"
    else:
        raise AssertionError("expected running thread lease to remain protected")


def test_thread_followup_queue_can_restore_failed_dispatch_to_front(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-followup-restore")

    first = services.enqueue_thread_followup(
        deps,
        "thread-followup-restore",
        QueuedFollowUpCreateRequest(message="first"),
    )
    second = services.enqueue_thread_followup(
        deps,
        "thread-followup-restore",
        QueuedFollowUpCreateRequest(message="second"),
    )

    popped = services.pop_next_thread_followup(deps, "thread-followup-restore")
    assert popped is not None
    assert popped.queue_id == first.queue_id
    assert popped.dispatch_id is not None

    restored = services.enqueue_thread_followup(
        deps,
        "thread-followup-restore",
        QueuedFollowUpCreateRequest(message=popped.message, insert_position="front"),
    )

    state_after_restore = services.get_thread_state_view(deps, "thread-followup-restore")
    assert [item.queue_id for item in state_after_restore.queued_followups] == [restored.queue_id, second.queue_id]
    assert [item.message for item in state_after_restore.queued_followups] == ["first", "second"]
    assert state_after_restore.active_followup_dispatch is None


def test_thread_followup_queue_rejects_invalid_insert_position_contract() -> None:
    try:
        QueuedFollowUpCreateRequest(message="bad insert position", insert_position="middle")
    except ValidationError as error:
        assert error.errors()[0]["loc"] == ("insert_position",)
    else:
        raise AssertionError("expected invalid insert_position to be rejected")


def test_thread_followup_queue_rejects_invalid_mode_contract() -> None:
    try:
        QueuedFollowUpCreateRequest(message="bad mode", mode="steer")
    except ValidationError as error:
        assert error.errors()[0]["loc"] == ("mode",)
    else:
        raise AssertionError("expected invalid mode to be rejected")


def test_thread_followup_queue_does_not_pop_while_thread_needs_user_action(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-followup-blocked")
    queued = services.enqueue_thread_followup(
        deps,
        "thread-followup-blocked",
        QueuedFollowUpCreateRequest(message="wait until safe"),
    )

    state = deps.checkpointer.get_thread_state("thread-followup-blocked")
    assert state is not None
    state.lifecycle.status = ThreadLifecycleStatus.AWAITING_CLARIFICATION
    deps.checkpointer.put_thread_state(state)

    try:
        services.pop_next_thread_followup(deps, "thread-followup-blocked")
    except services.GatewayAdapterError as error:
        assert error.status_code == 409
        assert error.error == "thread_not_ready_for_followup"
    else:
        raise AssertionError("expected queued follow-up pop to be blocked")

    remaining = services.get_thread_state_view(deps, "thread-followup-blocked")
    assert [item.queue_id for item in remaining.queued_followups] == [queued.queue_id]


def test_thread_state_projects_pending_user_interaction_and_submit_message(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-interaction")
    state = deps.checkpointer.get_thread_state("thread-interaction")
    assert state is not None
    state.lifecycle.status = ThreadLifecycleStatus.AWAITING_CLARIFICATION
    state.lifecycle.last_error = "Choose a style"
    state.conversation.pending_user_interaction = {
        "request_id": "choice-1",
        "kind": "choice",
        "title": "Presentation style",
        "question": "Choose a style",
        "selection_mode": "multiple",
        "options": [
            {"id": "modern", "label": "Modern", "description": "Clean layout", "recommended": True, "disabled": False},
            {"id": "classic", "label": "Classic", "description": None, "recommended": False, "disabled": False},
        ],
        "min_selections": 1,
        "max_selections": 2,
        "allow_custom": True,
        "custom_label": "Other",
        "placeholder": "Describe another style",
        "required": True,
    }
    deps.checkpointer.put_thread_state(state)

    projected = services.get_thread_state_view(deps, "thread-interaction")
    assert projected.pending_user_interaction is not None
    assert projected.pending_user_interaction.selection_mode == "multiple"
    assert projected.pending_user_interaction.options[0].recommended is True

    submit = UserInteractionSubmitRequest(
        request_id="choice-1",
        selected_option_ids=["modern"],
        custom_response="Use a crisp white canvas.",
    )
    message = services.build_user_interaction_response_message(state, submit)
    assert "Choose a style" in message
    assert "- Modern (modern)" in message
    assert "Use a crisp white canvas." in message
    persisted = deps.checkpointer.get_thread_state("thread-interaction")
    assert persisted is not None
    assert persisted.conversation.pending_user_interaction == state.conversation.pending_user_interaction

    persisted_message = services.build_user_interaction_response_message(state, submit, deps=deps)
    assert persisted_message == message
    cleared = deps.checkpointer.get_thread_state("thread-interaction")
    assert cleared is not None
    assert cleared.conversation.pending_user_interaction is None


def test_user_interaction_submit_rejects_multiple_single_select_options(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-interaction-single")
    state = deps.checkpointer.get_thread_state("thread-interaction-single")
    assert state is not None
    state.lifecycle.status = ThreadLifecycleStatus.AWAITING_CLARIFICATION
    state.conversation.pending_user_interaction = {
        "request_id": "single-1",
        "kind": "choice",
        "question": "Choose one stack",
        "selection_mode": "single",
        "options": [
            {"id": "vite", "label": "Vite", "description": None, "recommended": False, "disabled": False},
            {"id": "next", "label": "Next.js", "description": None, "recommended": False, "disabled": False},
        ],
        "min_selections": 1,
        "max_selections": 1,
        "allow_custom": False,
        "required": True,
    }

    with pytest.raises(GatewayAdapterError) as exc_info:
        services.build_user_interaction_response_message(
            state,
            UserInteractionSubmitRequest(
                request_id="single-1",
                selected_option_ids=["vite", "next"],
            ),
        )

    assert exc_info.value.error == "too_many_interaction_choices"


def test_user_interaction_submit_validates_and_renders_multi_field_responses(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-interaction-form")
    state = deps.checkpointer.get_thread_state("thread-interaction-form")
    assert state is not None
    state.lifecycle.status = ThreadLifecycleStatus.AWAITING_CLARIFICATION
    state.conversation.pending_user_interaction = {
        "request_id": "form-1",
        "kind": "form",
        "question": "Choose project decisions",
        "selection_mode": "single",
        "options": [
            {"id": "vite", "label": "Vite", "description": None, "recommended": True, "disabled": False},
            {"id": "next", "label": "Next.js", "description": None, "recommended": False, "disabled": False},
        ],
        "min_selections": 1,
        "max_selections": 1,
        "allow_custom": False,
        "required": True,
        "fields": [
            {
                "field_id": "stack",
                "label": "Framework",
                "selection_mode": "single",
                "options": [
                    {"id": "vite", "label": "Vite", "description": None, "recommended": True, "disabled": False},
                    {"id": "next", "label": "Next.js", "description": None, "recommended": False, "disabled": False},
                ],
                "min_selections": 1,
                "max_selections": 1,
                "allow_custom": False,
                "required": True,
                "metadata": {},
            },
            {
                "field_id": "scope",
                "label": "Completeness",
                "selection_mode": "multiple",
                "options": [
                    {"id": "routing", "label": "Routing", "description": None, "recommended": False, "disabled": False},
                    {"id": "tests", "label": "Tests", "description": None, "recommended": False, "disabled": False},
                ],
                "min_selections": 1,
                "max_selections": 2,
                "allow_custom": False,
                "required": True,
                "metadata": {},
            },
            {
                "field_id": "notes",
                "label": "Extra constraints",
                "selection_mode": "text",
                "options": [],
                "min_selections": 0,
                "max_selections": None,
                "allow_custom": False,
                "required": False,
                "metadata": {},
            },
        ],
    }

    message = services.build_user_interaction_response_message(
        state,
        UserInteractionSubmitRequest(
            request_id="form-1",
            field_responses=[
                {"field_id": "stack", "selected_option_ids": ["vite"]},
                {"field_id": "scope", "selected_option_ids": ["routing", "tests"]},
                {"field_id": "notes", "free_text": "Keep the UI quiet and dense."},
            ],
        ),
    )

    assert "Field: Framework (stack)" in message
    assert "- Vite (vite)" in message
    assert "Field: Completeness (scope)" in message
    assert "- Routing (routing)" in message
    assert "- Tests (tests)" in message
    assert "Free text: Keep the UI quiet and dense." in message

    with pytest.raises(GatewayAdapterError) as exc_info:
        services.build_user_interaction_response_message(
            state,
            UserInteractionSubmitRequest(
                request_id="form-1",
                field_responses=[
                    {"field_id": "stack", "selected_option_ids": ["vite"]},
                    {"field_id": "notes", "free_text": "missing scope"},
                ],
            ),
        )
    assert exc_info.value.error == "too_few_interaction_choices"


def test_stream_user_interaction_resume_uses_thread_defaults_and_clears_pending(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="interaction resumed")])
    )
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-interaction-stream")
    state = deps.checkpointer.get_thread_state("thread-interaction-stream")
    assert state is not None
    state.lifecycle.status = ThreadLifecycleStatus.AWAITING_CLARIFICATION
    state.execution.execution_mode = ThreadExecutionMode.AGENT
    state.execution.selected_model = "openai"
    state.execution.selected_profile = "default"
    state.execution.selected_reasoning_effort = "medium"
    state.execution.is_plan_mode = True
    state.conversation.pending_user_interaction = {
        "request_id": "choice-stream",
        "kind": "choice",
        "question": "Choose one stack",
        "selection_mode": "single",
        "options": [
            {"id": "vite", "label": "Vite", "description": None, "recommended": True, "disabled": False},
        ],
        "min_selections": 1,
        "max_selections": 1,
        "allow_custom": False,
        "required": True,
    }
    deps.checkpointer.put_thread_state(state)
    captured_requests: list[object] = []
    original_run_stream = deps.run_engine.run_stream

    def capture_run_stream(request):
        captured_requests.append(request)
        return original_run_stream(request)

    deps.run_engine.run_stream = capture_run_stream  # type: ignore[method-assign]

    events = list(
        services.stream_thread_user_interaction_events(
            deps,
            "thread-interaction-stream",
            UserInteractionResumeRequest(request_id="choice-stream", selected_option_ids=["vite"]),
        )
    )

    assert events[0].event == "run_preparing"
    assert events[-1].event == "run_completed", events[-1].data
    assert captured_requests
    request = captured_requests[0]
    assert getattr(request, "selected_model") == "openai"
    assert getattr(request, "profile") == "default"
    assert getattr(request, "selected_reasoning_effort") == "medium"
    assert getattr(request, "is_plan_mode") is True
    persisted = deps.checkpointer.get_thread_state("thread-interaction-stream")
    assert persisted is not None
    assert persisted.conversation.pending_user_interaction is None


def test_stream_thread_run_events_passes_uploaded_filenames_and_plan_mode(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="queued upload reply")])
    )
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-stream-uploads")
    services.upload_files(deps, "thread-stream-uploads", [("queued.txt", b"queued file")])
    captured_requests: list[object] = []
    original_run_stream = deps.run_engine.run_stream

    def capture_run_stream(request):
        captured_requests.append(request)
        return original_run_stream(request)

    deps.run_engine.run_stream = capture_run_stream  # type: ignore[method-assign]

    events = list(
        services.iter_thread_run_events(
            deps,
            "thread-stream-uploads",
            message="process queued files",
            execution_mode=ThreadExecutionMode.AGENT,
            is_plan_mode=True,
            uploaded_filenames=("queued.txt",),
        )
    )

    assert events[-1].event == "run_completed", events[-1].data
    assert captured_requests
    assert getattr(captured_requests[0], "recent_upload_filenames") == ("queued.txt",)
    assert getattr(captured_requests[0], "is_plan_mode") is True


def test_stream_thread_run_events_emits_gateway_preparing_before_runtime(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="prepared reply")])
    )
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-stream-preparing")
    runtime_started = False
    captured_requests: list[object] = []
    original_run_stream = deps.run_engine.run_stream

    def capture_run_stream(request):
        nonlocal runtime_started
        runtime_started = True
        captured_requests.append(request)
        return original_run_stream(request)

    deps.run_engine.run_stream = capture_run_stream  # type: ignore[method-assign]
    events = services.iter_thread_run_events(
        deps,
        "thread-stream-preparing",
        message="hello",
        execution_mode=ThreadExecutionMode.CHAT,
        selected_model="openai",
    )

    first = next(events)
    assert first.event == "run_preparing"
    assert runtime_started is False
    assert first.data["thread_id"] == "thread-stream-preparing"
    assert first.data["status"] == "preparing"
    assert first.data["phase"] == "gateway_received"
    assert first.data["source"] == "gateway"
    assert first.data["execution_mode"] == "chat"
    assert first.data["selected_model"] == "openai"
    assert "known_system_version" in first.data

    rest = list(events)
    assert runtime_started is True
    assert captured_requests
    assert getattr(captured_requests[0], "config_result") is deps.config_result
    assert rest[0].event == "run_started"
    assert rest[-1].event == "run_completed", rest[-1].data


def test_stream_thread_run_events_passes_last_event_id_to_stream_manager(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="prepared reply")])
    )
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-stream-cursor")
    captured_last_event_ids: list[str | None] = []
    original_stream = deps.stream_run_manager.stream

    def capture_stream(key, factory, *, last_event_id=None):
        captured_last_event_ids.append(last_event_id)
        return original_stream(key, factory, last_event_id=last_event_id)

    deps.stream_run_manager.stream = capture_stream  # type: ignore[method-assign]

    events = list(
        services.stream_thread_run_events(
            deps,
            "thread-stream-cursor",
            message="hello",
            execution_mode=ThreadExecutionMode.CHAT,
            last_event_id="run-1:000002",
        )
    )

    assert events == []
    assert captured_last_event_ids == ["run-1:000002"]


def test_stream_thread_run_events_reconnect_replays_completed_run_without_rerun(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="reconnect reply")])
    )
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-stream-reconnect")
    run_stream_calls = 0
    original_run_stream = deps.run_engine.run_stream

    def capture_run_stream(request):
        nonlocal run_stream_calls
        run_stream_calls += 1
        return original_run_stream(request)

    deps.run_engine.run_stream = capture_run_stream  # type: ignore[method-assign]

    first_events = list(
        services.stream_thread_run_events(
            deps,
            "thread-stream-reconnect",
            message="hello",
            execution_mode=ThreadExecutionMode.CHAT,
        )
    )
    replayed_events = list(
        services.stream_thread_run_events(
            deps,
            "thread-stream-reconnect",
            message="hello",
            execution_mode=ThreadExecutionMode.CHAT,
            last_event_id="unknown:000001",
        )
    )

    assert any(event.startswith("event: run_completed") for event in first_events)
    assert any(event.startswith("event: run_completed") for event in replayed_events)
    assert run_stream_calls == 1


def test_stream_thread_run_events_reports_preparing_before_runtime_creation_failure(
    gateway_app_factory,
) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-stream-preparing-fail")

    def fail_run_stream(_request):
        raise RuntimeError("runtime assembly failed")

    deps.run_engine.run_stream = fail_run_stream  # type: ignore[method-assign]

    events = list(
        services.iter_thread_run_events(
            deps,
            "thread-stream-preparing-fail",
            message="hello",
            execution_mode=ThreadExecutionMode.AGENT,
        )
    )

    assert [event.event for event in events] == ["run_preparing", "run_failed"]
    assert events[0].data["phase"] == "gateway_received"
    assert events[1].data["kind"] == "RuntimeError"
    assert "runtime assembly failed" in events[1].data["error"]


def test_create_thread_bootstraps_checkpointer_and_store(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()

    response = services.create_thread(deps)
    thread_id = response.thread_id

    assert deps.checkpointer.get_thread_state(thread_id) is not None
    assert deps.store.get_thread_metadata(thread_id) is not None


def test_thread_settings_can_be_read_and_updated(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-settings")

    settings = services.get_thread_settings_view(deps, "thread-settings")
    assert settings.execution_mode == "agent"
    assert settings.selected_model is None
    assert settings.workspace_mode == "thread"
    assert settings.workspace_root is None
    assert settings.anvil_home is not None
    assert settings.anvil_profile == "default"
    assert settings.anvil_profile_home is not None

    updated = services.update_thread_settings(
        deps,
        "thread-settings",
        body=ThreadSettingsUpdateRequest(
            execution_mode="full_access",
            selected_model="openai_compatible",
            selected_profile="coder",
            selected_reasoning_effort="xhigh",
            workspace_root="E:/workspace/demo-project",
        ),
    )
    assert updated.execution_mode == "full_access"
    assert updated.selected_model == "openai_compatible"
    assert updated.selected_profile == "coder"
    assert updated.selected_reasoning_effort == "xhigh"
    assert updated.workspace_mode == "external"
    assert updated.workspace_root == str(Path("E:/workspace/demo-project").resolve())
    assert updated.resolved_workspace_path == str(Path("E:/workspace/demo-project").resolve())

    state = services.get_thread_state_view(deps, "thread-settings")
    assert state.execution_mode == "full_access"
    assert state.selected_model == "openai_compatible"
    assert state.selected_profile == "coder"
    assert state.workspace_mode == "external"
    assert state.workspace_root == str(Path("E:/workspace/demo-project").resolve())
    assert state.resolved_workspace_path == str(Path("E:/workspace/demo-project").resolve())

    reset = services.update_thread_settings(
        deps,
        "thread-settings",
        body=ThreadSettingsUpdateRequest(
            workspace_root="",
        ),
    )
    assert reset.workspace_mode == "thread"
    assert reset.workspace_root is None


def test_thread_state_view_virtualizes_project_context_file_paths(contract_tmp_path: Path) -> None:
    from anvil.sandbox.path_service import PathService

    path_service = PathService(contract_tmp_path / "threads")
    thread_data = path_service.bootstrap_thread_paths("thread-context")
    workspace = Path(str(thread_data.workspace_path))
    context_file = workspace / "AGENTS.md"
    context_file.parent.mkdir(parents=True, exist_ok=True)
    context_file.write_text("Context rule.\n", encoding="utf-8")
    state = ThreadState(
        identity={"thread_id": "thread-context"},
        thread_data=thread_data.model_dump(mode="json"),
        prompt_snapshot={
            "project_context_files": [
                {
                    "virtual_path": str(context_file),
                    "relative_path": "AGENTS.md",
                    "applies_to": "/mnt/user-data/workspace",
                    "scope": ".",
                    "truncated": False,
                }
            ]
        },
    )

    view = services.thread_state_to_view(state, path_service=path_service)

    assert view.project_context_files[0].virtual_path == "/mnt/user-data/workspace/AGENTS.md"


def test_thread_state_chat_scope_does_not_build_full_artifact_refs_by_default() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-chat-state-artifacts"},
        artifacts={
            "uploaded_files": [
                {
                    "filename": f"upload-{index}.txt",
                    "virtual_path": f"/mnt/user-data/uploads/upload-{index}.txt",
                    "artifact_url": f"/threads/thread-chat-state-artifacts/artifacts/uploads/upload-{index}.txt",
                }
                for index in range(100)
            ],
            "output_artifacts": [f"reports/output-{index}.txt" for index in range(100)],
            "presented_artifacts": [f"/mnt/user-data/outputs/reports/presented-{index}.txt" for index in range(100)],
        },
    )

    chat_view = services.thread_state_to_view(state, state_scope="chat")

    assert chat_view.uploaded_files == []
    assert chat_view.output_artifacts == []
    assert chat_view.presented_artifacts == []

    full_view = services.thread_state_to_view(state, state_scope="full")

    assert len(full_view.uploaded_files) == 100
    assert len(full_view.output_artifacts) == 100
    assert len(full_view.presented_artifacts) == 100


def test_thread_state_view_respects_explicit_bounded_artifact_refs() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-explicit-artifact-refs"},
        artifacts={
            "uploaded_files": [
                {
                    "filename": "old-upload.txt",
                    "virtual_path": "/mnt/user-data/uploads/old-upload.txt",
                    "artifact_url": "/threads/thread-explicit-artifact-refs/artifacts/uploads/old-upload.txt",
                }
            ],
            "output_artifacts": ["reports/old-output.txt"],
            "presented_artifacts": ["/mnt/user-data/outputs/reports/old-presented.txt"],
        },
    )
    bounded_upload = ArtifactRefView(
        kind="upload",
        label="window-upload.txt",
        artifact_url="/threads/thread-explicit-artifact-refs/artifacts/uploads/window-upload.txt",
        virtual_path="/mnt/user-data/uploads/window-upload.txt",
    )

    full_view = services.thread_state_to_view(
        state,
        state_scope="full",
        artifact_refs={"uploads": [bounded_upload]},
    )

    assert [item.label for item in full_view.uploaded_files] == ["window-upload.txt"]
    assert full_view.output_artifacts == []
    assert full_view.presented_artifacts == []


def test_thread_state_view_projects_subagent_history_without_raw_payload() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-subagent-history-view"},
        durable_subagent_job_history=[
            {
                "job_id": "subagent-1",
                "parent_thread_id": "thread-subagent-history-view",
                "parent_run_id": "run-1",
                "event_type": "tool_result",
                "timestamp": "2026-05-28T03:30:00+00:00",
                "payload": {
                    "status": "completed",
                    "summary": "subagent wrote a file",
                    "tool_name": "write_file",
                    "display_name": "Write File",
                    "result_text": "sensitive tool result body should never be projected",
                    "messages": [{"content": "sensitive child transcript should never be projected"}],
                },
            }
        ],
    )

    view = services.thread_state_to_view(state, state_scope="full")
    payload = view.model_dump(mode="json")

    assert len(view.durable_subagent_job_history) == 1
    event = view.durable_subagent_job_history[0]
    assert event.job_id == "subagent-1"
    assert event.parent_thread_id == "thread-subagent-history-view"
    assert event.parent_run_id == "run-1"
    assert event.event == "tool_result"
    assert event.status == "completed"
    assert event.summary == "subagent wrote a file"
    assert event.tool_name == "write_file"
    assert event.display_name == "Write File"
    assert "sensitive tool result body" not in repr(payload)
    assert "sensitive child transcript" not in repr(payload)


def test_subagent_task_view_projects_message_and_approval_summaries_without_raw_payload(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-subagent-task-view")

    from anvil.subagents import SubagentResult, SubagentTaskRecord, SubagentTaskStatus

    task = SubagentTaskRecord(
        task_id="subagent-task-1",
        parent_thread_id="thread-subagent-task-view",
        parent_run_id="run-1",
        child_thread_id="child-thread-1",
        child_run_id="child-run-1",
        status=SubagentTaskStatus.FAILED,
        assigned_profile="general",
        delegation_depth=1,
    )
    deps.subagent_service.registry.add_task(task)
    deps.subagent_service.registry.put_result(
        SubagentResult(
            task_id="subagent-task-1",
            status=SubagentTaskStatus.FAILED,
            summary="subagent requested approval",
            child_thread_id="child-thread-1",
            child_run_id="child-run-1",
            messages=(
                {
                    "role": "human",
                    "content": "sensitive child prompt should never be projected",
                    "tool_calls": [{"args": {"secret": "sensitive child tool args should never be projected"}}],
                },
                {"role": "assistant", "content": "Concise visible child answer."},
            ),
            approval_payload={
                "pending_approval": "needs_user_approval",
                "approval_request": {
                    "request_id": "approval-1",
                    "thread_id": "child-thread-1",
                    "turn_id": "turn-1",
                    "reason": "write outside workspace",
                    "action_kind": "filesystem_write",
                    "requested_permissions": ["write"],
                    "tool_name": "write_file",
                    "risk_category": "filesystem_write",
                    "raw_command": "sensitive approval command should never be projected",
                },
                "child_messages": [{"content": "sensitive approval transcript should never be projected"}],
            },
            recent_tool_activity=(
                {
                    "tool_call_id": "tool-call-1",
                    "message_id": "child-message-1",
                    "name": "write_file",
                    "display_name": "Write File",
                    "source_kind": "builtin",
                    "source_id": "builtin",
                    "capability_group": "filesystem",
                    "tool_execution_mode": "direct",
                    "args": {
                        "path": "/mnt/user-data/workspace/secret.md",
                        "secret": "sensitive subagent tool args should never be projected",
                    },
                    "status": "completed",
                    "result_text": "sensitive subagent tool result body should never be projected",
                    "duration_ms": 42,
                },
            ),
            error="approval required",
        )
    )

    task_view = services.subagent_task_to_view(deps, "subagent-task-1")
    payload = task_view.model_dump(mode="json")

    assert len(task_view.messages) == 2
    assert task_view.messages[0].role == "human"
    assert task_view.messages[0].content_preview == "sensitive child prompt should never be projected"[:240]
    assert task_view.messages[0].tool_call_count == 1
    assert task_view.messages[1].role == "assistant"
    assert task_view.messages[1].content_preview == "Concise visible child answer."
    assert task_view.approval_payload is not None
    assert task_view.approval_payload.pending_approval == "needs_user_approval"
    assert task_view.approval_payload.request_id == "approval-1"
    assert task_view.approval_payload.reason == "write outside workspace"
    assert task_view.approval_payload.action_kind == "filesystem_write"
    assert task_view.approval_payload.tool_name == "write_file"
    assert task_view.approval_payload.risk_category == "filesystem_write"
    assert task_view.approval_payload.requested_permissions == ["write"]
    assert len(task_view.recent_tool_activity) == 1
    assert task_view.recent_tool_activity[0].tool_call_id == "tool-call-1"
    assert task_view.recent_tool_activity[0].name == "write_file"
    assert task_view.recent_tool_activity[0].display_name == "Write File"
    assert task_view.recent_tool_activity[0].status == "completed"
    assert task_view.recent_tool_activity[0].args_keys == ["path", "secret"]
    assert task_view.recent_tool_activity[0].has_result is True
    assert task_view.recent_tool_activity[0].result_char_count == len("sensitive subagent tool result body should never be projected")
    assert not hasattr(task_view.recent_tool_activity[0], "args")
    assert not hasattr(task_view.recent_tool_activity[0], "result_text")
    assert "sensitive child tool args" not in repr(payload)
    assert "sensitive approval command" not in repr(payload)
    assert "sensitive approval transcript" not in repr(payload)
    assert "sensitive subagent tool args" not in repr(payload)
    assert "sensitive subagent tool result body" not in repr(payload)


def test_thread_state_view_projects_prompt_cache_diagnostics_without_full_runtime_snapshot() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-prompt-cache-view"},
        execution={
            "runtime_assembly_snapshot": {
                "prompt": {
                    "cache_delta": {
                        "hits": 1,
                        "misses": 0,
                        "writes": 0,
                        "evictions": 0,
                        "bypasses": 0,
                        "size_before": 1,
                        "size_after": 1,
                        "net_size_change": 0,
                        "max_entries": 256,
                    },
                    "cache": {
                        "hits": 4,
                        "misses": 2,
                        "writes": 2,
                        "evictions": 0,
                        "bypasses": 1,
                        "size": 2,
                        "max_entries": 256,
                    },
                },
                "capabilities": {"visible_tool_names": ["read_file"]},
            }
        },
    )

    view = services.thread_state_to_view(state, state_scope="chat")

    assert view.prompt_cache_diagnostics is not None
    assert view.prompt_cache_diagnostics.hits == 1
    assert view.prompt_cache_diagnostics.misses == 0
    assert view.prompt_cache_diagnostics.cumulative_hits == 4
    assert view.prompt_cache_diagnostics.cumulative_misses == 2
    assert not hasattr(view, "runtime_assembly_snapshot")


def test_thread_state_view_projects_prompt_section_token_ledger_without_prompt_text() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-prompt-token-ledger-view"},
        execution={
            "runtime_assembly_snapshot": {
                "prompt": {
                    "stable_prompt_tokens": 1200,
                    "volatile_prompt_tokens": 88,
                    "stable_section_tokens": {
                        "role_and_intent": 100,
                        "capability_summary": 700,
                    },
                    "volatile_section_tokens": {
                        "request_context": 88,
                    },
                    "stable_sections": ["sensitive stable prompt text"],
                    "volatile_sections": ["sensitive request text"],
                },
            }
        },
    )

    view = services.thread_state_to_view(state, state_scope="chat")
    payload = view.model_dump(mode="json")

    assert view.prompt_section_token_ledger is not None
    assert view.prompt_section_token_ledger.stable_prompt_tokens == 1200
    assert view.prompt_section_token_ledger.volatile_prompt_tokens == 88
    assert view.prompt_section_token_ledger.stable_section_tokens["capability_summary"] == 700
    assert view.prompt_section_token_ledger.volatile_section_tokens == {"request_context": 88}
    assert "sensitive stable prompt text" not in repr(payload)
    assert "sensitive request text" not in repr(payload)
    assert not hasattr(view, "runtime_assembly_snapshot")


def test_thread_state_view_projects_context_cache_diagnostics_without_context_text() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-context-cache-view"},
        execution={
            "runtime_assembly_snapshot": {
                "prompt": {
                    "project_context_fingerprint": "project-context-fingerprint-secret-long",
                    "project_context_cache_status": "hit",
                    "project_context_file_count": 2,
                    "project_context_truncated_file_count": 1,
                    "project_context_total_chars": 4096,
                    "project_context_discovery_scanned_path_count": 77,
                    "project_context_discovery_max_scanned_paths": 100,
                    "project_context_discovery_scan_truncated": True,
                    "runtime_path_fingerprint": "runtime-path-fingerprint-secret-long",
                    "runtime_path_cache_status": "miss",
                    "runtime_path_root_count": 4,
                    "runtime_path_host_bridge_count": 2,
                    "project_context_files": [
                        {
                            "virtual_path": "/mnt/user-data/workspace/AGENTS.md",
                            "relative_path": "AGENTS.md",
                            "applies_to": "/mnt/user-data/workspace",
                            "scope": ".",
                            "truncated": False,
                            "content": "sensitive project context text should never be projected",
                        },
                        {
                            "virtual_path": "/mnt/user-data/workspace/docs/CODEX.md",
                            "relative_path": "docs/CODEX.md",
                            "applies_to": "/mnt/user-data/workspace/docs",
                            "scope": "docs",
                            "truncated": True,
                            "content": "sensitive nested context text should never be projected",
                        },
                    ],
                    "stable_sections": ["raw stable prompt should never be projected"],
                },
            }
        },
    )

    view = services.thread_state_to_view(state, state_scope="chat")
    payload = view.model_dump(mode="json")

    assert view.context_cache_diagnostics is not None
    assert view.context_cache_diagnostics.project_context_cache_status == "hit"
    assert view.context_cache_diagnostics.project_context_file_count == 2
    assert view.context_cache_diagnostics.project_context_truncated_file_count == 1
    assert view.context_cache_diagnostics.project_context_total_chars == 4096
    assert view.context_cache_diagnostics.project_context_discovery_scanned_path_count == 77
    assert view.context_cache_diagnostics.project_context_discovery_max_scanned_paths == 100
    assert view.context_cache_diagnostics.project_context_discovery_scan_truncated is True
    assert view.context_cache_diagnostics.project_context_scope_counts == {".": 1, "docs": 1}
    assert view.context_cache_diagnostics.runtime_path_cache_status == "miss"
    assert view.context_cache_diagnostics.runtime_path_root_count == 4
    assert view.context_cache_diagnostics.runtime_path_host_bridge_count == 2
    assert "sensitive project context text" not in repr(payload)
    assert "raw stable prompt" not in repr(payload)
    assert not hasattr(view, "runtime_assembly_snapshot")


def test_thread_state_view_projects_capability_assembly_diagnostics_without_tool_payload() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-capability-diagnostics-view"},
        execution={
            "runtime_assembly_snapshot": {
                "capabilities": {
                    "visible_tool_names": ["sensitive_tool_name"],
                    "deferred_tool_names": ["hidden_external_tool"],
                    "assembly_diagnostics": {
                        "discovered_tool_count": 20,
                        "enabled_tool_count": 18,
                        "materialized_tool_count": 16,
                        "visible_tool_count": 8,
                        "deferred_tool_count": 8,
                        "active_promotion_count": 1,
                        "visible_schema_token_budget": 1200,
                        "visible_schema_tokens": 900,
                        "deferred_schema_tokens": 700,
                        "total_schema_tokens": 1600,
                        "visible_schema_budget_remaining_tokens": 300,
                        "schema_compacted_tool_count": 2,
                        "schema_deferred_tool_count": 3,
                        "action_prefilter_deferred_tool_count": 4,
                        "sanitizer_truncated_tool_count": 1,
                        "assembly_stage_durations_ms": {
                            "runtime_tools": 25,
                            "skills_discovery": 12,
                            "final_bundle": 41,
                            "total": 100,
                        },
                        "slowest_assembly_stage": "final_bundle",
                        "slowest_assembly_stage_duration_ms": 41,
                        "skills_discovery_cache_hit": False,
                        "skills_discovery_watch_enabled": True,
                        "skills_discovery_root_count": 2,
                        "skills_discovery_manifest_count": 40,
                        "skills_discovery_enabled_count": 35,
                        "skills_discovery_package_count": 12,
                        "skills_discovery_stage_durations_ms": {
                            "resolve_roots": 4,
                            "loader_discover": 30,
                            "total": 42,
                        },
                        "slowest_skills_discovery_stage": "loader_discover",
                        "slowest_skills_discovery_stage_duration_ms": 30,
                        "visible_by_source_kind": {"builtin": 6, "skill": 2},
                        "deferred_by_source_kind": {"mcp": 8},
                        "visible_by_group": {"code": 5},
                        "deferred_by_group": {"browser": 3},
                    },
                },
            }
        },
    )

    view = services.thread_state_to_view(state, state_scope="chat")
    payload = view.model_dump(mode="json")

    assert view.capability_assembly_diagnostics is not None
    assert view.capability_assembly_diagnostics.visible_tool_count == 8
    assert view.capability_assembly_diagnostics.deferred_tool_count == 8
    assert view.capability_assembly_diagnostics.visible_schema_tokens == 900
    assert view.capability_assembly_diagnostics.visible_schema_token_budget == 1200
    assert view.capability_assembly_diagnostics.schema_deferred_tool_count == 3
    assert view.capability_assembly_diagnostics.deferred_by_source_kind == {"mcp": 8}
    assert view.capability_assembly_diagnostics.assembly_stage_durations_ms["final_bundle"] == 41
    assert view.capability_assembly_diagnostics.slowest_assembly_stage == "final_bundle"
    assert view.capability_assembly_diagnostics.slowest_assembly_stage_duration_ms == 41
    assert view.capability_assembly_diagnostics.skills_discovery_cache_hit is False
    assert view.capability_assembly_diagnostics.skills_discovery_watch_enabled is True
    assert view.capability_assembly_diagnostics.skills_discovery_manifest_count == 40
    assert view.capability_assembly_diagnostics.skills_discovery_enabled_count == 35
    assert view.capability_assembly_diagnostics.skills_discovery_stage_durations_ms["loader_discover"] == 30
    assert view.capability_assembly_diagnostics.slowest_skills_discovery_stage == "loader_discover"
    assert "sensitive_tool_name" not in repr(payload)
    assert "hidden_external_tool" not in repr(payload)
    assert not hasattr(view, "runtime_assembly_snapshot")


def test_thread_state_view_projects_memory_injection_diagnostics_without_memory_payload() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-memory-injection-diagnostics-view"},
        execution={
            "runtime_assembly_snapshot": {
                "memory_injection_diagnostics": {
                    "source": "memory_manager",
                    "status": "injected",
                    "snapshot_id": "memory-snapshot-123",
                    "query_tokens": 9,
                    "curated_match_count": 3,
                    "archive_hit_count": 2,
                    "evidence_count": 4,
                    "provider_note_count": 1,
                    "summary_present": True,
                    "rendered_tokens_before_truncation": 1200,
                    "rendered_tokens": 900,
                    "token_budget": 900,
                    "truncated": True,
                    "store_counts": {"project": 2, "user": 1},
                    "source_kind_counts": {"curated": 3, "archive": 1},
                    "memory_context": "sensitive memory content should never be projected",
                },
            }
        },
    )

    view = services.thread_state_to_view(state, state_scope="chat")
    payload = view.model_dump(mode="json")

    assert view.memory_injection_diagnostics is not None
    assert view.memory_injection_diagnostics.source == "memory_manager"
    assert view.memory_injection_diagnostics.status == "injected"
    assert view.memory_injection_diagnostics.snapshot_id == "memory-snapshot-123"
    assert view.memory_injection_diagnostics.curated_match_count == 3
    assert view.memory_injection_diagnostics.archive_hit_count == 2
    assert view.memory_injection_diagnostics.evidence_count == 4
    assert view.memory_injection_diagnostics.provider_note_count == 1
    assert view.memory_injection_diagnostics.rendered_tokens == 900
    assert view.memory_injection_diagnostics.token_budget == 900
    assert view.memory_injection_diagnostics.truncated is True
    assert view.memory_injection_diagnostics.store_counts == {"project": 2, "user": 1}
    assert view.memory_injection_diagnostics.source_kind_counts == {"curated": 3, "archive": 1}
    assert "sensitive memory content" not in repr(payload)
    assert not hasattr(view, "runtime_assembly_snapshot")


def test_thread_state_view_projects_compaction_diagnostics_without_archived_context() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-compaction-diagnostics-view"},
        execution={
            "context_window_usage": {
                "context_tokens": 600,
                "context_window_tokens": 1000,
                "compaction_diagnostics": {
                    "summary_source": "fallback_should_not_win",
                    "archived_transcript": "fallback archived transcript should never be projected",
                },
            },
            "runtime_assembly_snapshot": {
                "compaction_diagnostics": {
                    "compaction_level": 2,
                    "compaction_level_label": "recursive_summary",
                    "compaction_reason": "token_threshold_exceeded",
                    "summary_source": "model",
                    "summary_model": "minimax/MiniMax-M2.7",
                    "summary_error_type": "fallback_parse",
                    "has_existing_summary": True,
                    "archived_message_count": 9,
                    "tool_call_count": 3,
                    "tool_result_count": 2,
                    "image_block_count": 1,
                    "truncated_message_count": 2,
                    "pruned_tool_result_count": 1,
                    "serialized_chars": 4096,
                    "serialized_tokens": 720,
                    "summary_prompt_tokens": 940,
                    "compaction_input_tokens": 1800,
                    "compaction_summary_tokens": 120,
                    "compaction_savings_tokens": 1150,
                    "keep_recent_turns": 4,
                    "archived_transcript": "sensitive archived transcript should never be projected",
                    "summary_prompt": "sensitive summary prompt should never be projected",
                    "tool_result_preview": "sensitive tool result should never be projected",
                    "image_data": "base64 image data should never be projected",
                },
            },
        },
    )

    view = services.thread_state_to_view(state, state_scope="chat")
    payload = view.model_dump(mode="json")

    assert view.compaction_diagnostics is not None
    assert view.compaction_diagnostics.compaction_level == 2
    assert view.compaction_diagnostics.compaction_level_label == "recursive_summary"
    assert view.compaction_diagnostics.compaction_reason == "token_threshold_exceeded"
    assert view.compaction_diagnostics.summary_source == "model"
    assert view.compaction_diagnostics.summary_model == "minimax/MiniMax-M2.7"
    assert view.compaction_diagnostics.summary_error_type == "fallback_parse"
    assert view.compaction_diagnostics.has_existing_summary is True
    assert view.compaction_diagnostics.archived_message_count == 9
    assert view.compaction_diagnostics.tool_call_count == 3
    assert view.compaction_diagnostics.tool_result_count == 2
    assert view.compaction_diagnostics.image_block_count == 1
    assert view.compaction_diagnostics.truncated_message_count == 2
    assert view.compaction_diagnostics.pruned_tool_result_count == 1
    assert view.compaction_diagnostics.serialized_chars == 4096
    assert view.compaction_diagnostics.serialized_tokens == 720
    assert view.compaction_diagnostics.summary_prompt_tokens == 940
    assert view.compaction_diagnostics.compaction_input_tokens == 1800
    assert view.compaction_diagnostics.compaction_summary_tokens == 120
    assert view.compaction_diagnostics.compaction_savings_tokens == 1150
    assert view.compaction_diagnostics.keep_recent_turns == 4
    assert "sensitive archived transcript" not in repr(payload)
    assert "sensitive summary prompt" not in repr(payload)
    assert "sensitive tool result" not in repr(payload)
    assert "base64 image data" not in repr(payload)
    assert "fallback_should_not_win" not in repr(payload)
    assert not hasattr(view, "runtime_assembly_snapshot")


def test_thread_state_view_projects_archived_summaries_without_raw_diagnostics() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-archived-summary-view"},
        archived_summaries=[
            {
                "summary_id": "summary-1",
                "summary_text": "Older turns discussed provider image support and context compaction.",
                "covers_turn_range": [1, 8],
                "token_count": 128,
                "prompt_snapshot_id": "prompt-snapshot-1",
                "compaction_level": 2,
                "compaction_level_label": "recursive_summary",
                "compaction_reason": "token_threshold_exceeded",
                "diagnostics": {
                    "summary_source": "model",
                    "summary_model": "minimax/MiniMax-M2.7",
                    "archived_message_count": 8,
                    "tool_call_count": 2,
                    "tool_result_count": 1,
                    "image_block_count": 1,
                    "truncated_message_count": 3,
                    "pruned_tool_result_count": 1,
                    "serialized_tokens": 640,
                    "summary_prompt_tokens": 720,
                    "compaction_input_tokens": 1600,
                    "compaction_summary_tokens": 128,
                    "compaction_savings_tokens": 1100,
                    "keep_recent_turns": 4,
                    "archived_transcript": "sensitive archived transcript should never be projected",
                    "summary_prompt": "sensitive summary prompt should never be projected",
                    "tool_result_preview": "sensitive tool result should never be projected",
                    "image_data": "base64 image data should never be projected",
                },
            }
        ],
    )

    view = services.thread_state_to_view(state, state_scope="full")
    payload = view.model_dump(mode="json")

    assert len(view.archived_summaries) == 1
    archived = view.archived_summaries[0]
    assert archived.summary_id == "summary-1"
    assert archived.summary_text == "Older turns discussed provider image support and context compaction."
    assert archived.covers_turn_range == [1, 8]
    assert archived.token_count == 128
    assert archived.prompt_snapshot_id == "prompt-snapshot-1"
    assert archived.compaction_level == 2
    assert archived.compaction_level_label == "recursive_summary"
    assert archived.compaction_reason == "token_threshold_exceeded"
    assert archived.diagnostics is not None
    assert archived.diagnostics.summary_source == "model"
    assert archived.diagnostics.summary_model == "minimax/MiniMax-M2.7"
    assert archived.diagnostics.archived_message_count == 8
    assert archived.diagnostics.tool_call_count == 2
    assert archived.diagnostics.tool_result_count == 1
    assert archived.diagnostics.image_block_count == 1
    assert archived.diagnostics.truncated_message_count == 3
    assert archived.diagnostics.pruned_tool_result_count == 1
    assert archived.diagnostics.serialized_tokens == 640
    assert archived.diagnostics.summary_prompt_tokens == 720
    assert archived.diagnostics.compaction_input_tokens == 1600
    assert archived.diagnostics.compaction_summary_tokens == 128
    assert archived.diagnostics.compaction_savings_tokens == 1100
    assert archived.diagnostics.keep_recent_turns == 4
    assert "sensitive archived transcript" not in repr(payload)
    assert "sensitive summary prompt" not in repr(payload)
    assert "sensitive tool result" not in repr(payload)
    assert "base64 image data" not in repr(payload)


def test_thread_state_view_projects_todo_snapshot_without_unknown_payload() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-todo-snapshot-view"},
        planning={
            "todo_snapshot": [
                {
                    "id": "todo-1",
                    "content": "Verify the typed gateway contract",
                    "status": "pending",
                    "created_at": "2026-05-28T03:22:00+08:00",
                    "depends_on": ["todo-0"],
                    "internal_prompt": "sensitive todo prompt should never be projected",
                    "raw_tool_payload": {"secret": "raw todo payload should never be projected"},
                }
            ]
        },
    )

    view = services.thread_state_to_view(state, state_scope="full")
    payload = view.model_dump(mode="json")

    assert len(view.todo_snapshot) == 1
    todo = view.todo_snapshot[0]
    assert todo.id == "todo-1"
    assert todo.content == "Verify the typed gateway contract"
    assert todo.status == "pending"
    assert todo.created_at == "2026-05-28T03:22:00+08:00"
    assert todo.depends_on == ["todo-0"]
    assert "sensitive todo prompt" not in repr(payload)
    assert "raw todo payload" not in repr(payload)


def test_thread_state_view_projects_token_usage_summary_without_raw_entries() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-token-usage-summary-view"},
        execution={
            "token_usage": {
                "model": "minimax",
                "concrete_model": "MiniMax-M2.7",
                "provider": "minimax_cn",
                "request_count": 2,
                "input_tokens": 240,
                "output_tokens": 30,
                "total_tokens": 270,
                "cache_read_tokens": 25,
                "cache_write_tokens": 0,
                "reasoning_tokens": 8,
                "total": {
                    "input_tokens": 240,
                    "output_tokens": 30,
                    "total_tokens": 270,
                    "cache_read_tokens": 25,
                    "cache_write_tokens": 10,
                    "reasoning_tokens": 8,
                },
                "last": {
                    "input_tokens": 140,
                    "output_tokens": 20,
                    "total_tokens": 160,
                    "cache_read_tokens": 5,
                    "cache_write_tokens": 0,
                    "reasoning_tokens": 3,
                    "raw_usage": {"secret": "last raw usage should never be projected"},
                },
                "entries": [
                    {
                        "message_index": 1,
                        "raw_usage": {"secret": "entry raw usage should never be projected"},
                    },
                ],
                "cost": {
                    "estimated_cost_usd": 0.0012,
                    "status": "estimated",
                    "currency": "USD",
                    "source": "config",
                },
                "estimated_cost_usd": 0.0,
                "provider_models": ["MiniMax-M2.7", "", None],
            }
        },
    )

    view = services.thread_state_to_view(state, state_scope="chat")
    payload = view.model_dump(mode="json")

    assert view.token_usage_summary is not None
    assert view.token_usage_summary.model == "minimax"
    assert view.token_usage_summary.concrete_model == "MiniMax-M2.7"
    assert view.token_usage_summary.provider == "minimax_cn"
    assert view.token_usage_summary.request_count == 2
    assert view.token_usage_summary.input_tokens == 240
    assert view.token_usage_summary.output_tokens == 30
    assert view.token_usage_summary.total_tokens == 270
    assert view.token_usage_summary.cache_read_tokens == 25
    assert view.token_usage_summary.cache_write_tokens == 0
    assert view.token_usage_summary.reasoning_tokens == 8
    assert view.token_usage_summary.total is not None
    assert view.token_usage_summary.total.input_tokens == 240
    assert view.token_usage_summary.last is not None
    assert view.token_usage_summary.last.input_tokens == 140
    assert view.token_usage_summary.last.total_tokens == 160
    assert view.token_usage_summary.total.cache_write_tokens == 10
    assert view.token_usage_summary.estimated_cost_usd == 0.0
    assert view.token_usage_summary.cost_status == "estimated"
    assert view.token_usage_summary.currency == "USD"
    assert view.token_usage_summary.pricing_source == "config"
    assert view.token_usage_summary.provider_models == ["MiniMax-M2.7"]
    assert "entry raw usage should never be projected" not in repr(payload.get("token_usage_summary"))
    assert "last raw usage should never be projected" not in repr(payload.get("token_usage_summary"))


def test_create_thread_accepts_initial_workspace_override(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()

    created = services.create_thread(
        deps,
        "thread-workspace-create",
        "E:/workspace/initial-project",
    )
    assert created.thread_id == "thread-workspace-create"

    settings = services.get_thread_settings_view(deps, "thread-workspace-create")
    assert settings.workspace_mode == "external"
    assert settings.workspace_root == str(Path("E:/workspace/initial-project").resolve())
    assert settings.resolved_workspace_path == str(Path("E:/workspace/initial-project").resolve())


def test_thread_state_view_projects_tool_call_records(gateway_app_factory) -> None:
    app = gateway_app_factory()
    state = ThreadState(identity={"thread_id": "thread-tool-call-record-view", "run_id": "run-tool-record"})
    state.execution.tool_calls = [
        {
            "run_id": "run-tool-record",
            "thread_id": "thread-tool-call-record-view",
            "message_id": "msg-1",
            "block_id": "msg-1:call:call-1",
            "sequence": 4,
            "tool_call_id": "call-1",
            "name": "read_file",
            "display_name": "Read File",
            "source_kind": "builtin",
            "source_id": "core",
            "capability_group": "filesystem",
            "tool_execution_mode": "sync",
            "input": {"path": "/mnt/user-data/workspace/README.md"},
            "output": "contents",
            "status": "completed",
            "is_error": False,
            "duration_ms": 12,
            "visibility": "chat",
        }
    ]

    view = services.thread_state_to_view(state)
    payload = view.model_dump(mode="json")

    assert payload["tool_calls"] == [
        {
            "run_id": "run-tool-record",
            "thread_id": "thread-tool-call-record-view",
            "message_id": "msg-1",
            "block_id": "msg-1:call:call-1",
            "sequence": 4,
            "tool_call_id": "call-1",
            "name": "read_file",
            "display_name": "Read File",
            "source_kind": "builtin",
            "source_id": "core",
            "capability_group": "filesystem",
            "tool_execution_mode": "sync",
            "input": {"path": "/mnt/user-data/workspace/README.md"},
            "output": "contents",
            "status": "completed",
            "is_error": False,
            "error_message": None,
            "started_at": None,
            "completed_at": None,
            "duration_ms": 12,
            "visibility": "chat",
        }
    ]


def test_thread_state_view_hides_empty_final_internal_reason_from_chat_contract(gateway_app_factory) -> None:
    app = gateway_app_factory()
    state = ThreadState(identity={"thread_id": "thread-empty-final-reason-view"})
    state.lifecycle.status = ThreadLifecycleStatus.INTERRUPTED
    state.lifecycle.last_error = EMPTY_FINAL_ASSISTANT_MESSAGE
    state.execution.last_message_interrupted = True
    state.execution.last_message_interrupted_reason = EMPTY_FINAL_ASSISTANT_MESSAGE

    view = services.thread_state_to_view(state)
    payload = view.model_dump(mode="json")

    assert payload["last_message_interrupted"] is True
    assert payload["last_message_interrupted_reason"] is None
    assert payload["last_error"] is None
    assert EMPTY_FINAL_ASSISTANT_MESSAGE not in repr(payload)


def test_threads_can_be_deleted(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-delete")

    deleted = services.delete_thread(deps, "thread-delete")
    assert deleted.thread_id == "thread-delete"
    assert deleted.deleted is True

    listed = services.list_threads(deps)
    assert listed == []


def test_delete_thread_flushes_memory_without_network(gateway_app_factory, monkeypatch) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()
    services.create_thread(deps, "thread-delete-no-network")
    calls = []

    def capture_session_end(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(deps.memory_manager, "on_session_end", capture_session_end)

    deleted = services.delete_thread(deps, "thread-delete-no-network")

    assert deleted.deleted is True
    assert calls == [
        {
            "thread_id": "thread-delete-no-network",
            "reason": "thread_delete",
            "allow_network": False,
        }
    ]


def test_link_preview_endpoint_returns_safe_placeholder_metadata(gateway_app_factory) -> None:
    app = gateway_app_factory()
    deps = app.state.deps_factory()

    payload = services.get_link_preview(deps, "https://example.com/docs")
    assert payload.url == "https://example.com/docs"
    assert payload.hostname == "example.com"
    assert payload.preview_enabled is False
    assert payload.preview_status == "disabled"
