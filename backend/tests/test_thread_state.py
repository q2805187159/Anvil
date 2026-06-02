from __future__ import annotations

from anvil.agents import ThreadMetadataView, ThreadState, merge_mapping, merge_unique_strings
from anvil.runtime import ApprovalDecision, PermissionGrant


def make_state() -> ThreadState:
    return ThreadState(
        identity={"thread_id": "thread-1"},
        conversation={
            "messages": [
                {"role": "system", "content": "hello"},
                {"role": "user", "content": "build a thing"},
            ],
            "title": "Example",
        },
        approvals={
            "pending_approval": ApprovalDecision.NEEDS_USER_APPROVAL,
            "granted_permissions": [
                PermissionGrant(file_write_roots=["/tmp/project"], granted_by="user")
            ],
            "session_approval_grants": ["filesystem_write"],
        },
        delegation={"active_subagent_tasks": [{"task_id": "task-1"}]},
    )


def test_merge_unique_strings_deduplicates_and_preserves_order() -> None:
    assert merge_unique_strings(["a", "b"], ["b", "c"]) == ["a", "b", "c"]


def test_merge_mapping_overrides_newer_values() -> None:
    assert merge_mapping({"a": 1, "b": 2}, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}


def test_thread_metadata_view_derives_runtime_flags() -> None:
    state = make_state()

    metadata = ThreadMetadataView.from_thread_state(state)

    assert metadata.thread_id == "thread-1"
    assert metadata.title == "Example"
    assert metadata.last_user_message_preview == "build a thing"
    assert metadata.has_pending_approval is True
    assert metadata.has_active_subagent_tasks is True


def test_thread_metadata_view_uses_human_role_for_preview() -> None:
    state = ThreadState(
        identity={"thread_id": "thread-human"},
        conversation={
            "messages": [
                {"role": "system", "content": "hello"},
                {"role": "human", "content": "summarize this first request"},
            ],
        },
    )

    metadata = ThreadMetadataView.from_thread_state(state)

    assert metadata.last_user_message_preview == "summarize this first request"


def test_thread_metadata_view_exposes_last_message_at() -> None:
    from datetime import datetime, timezone

    last_message_at = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
    state = ThreadState(
        identity={"thread_id": "thread-last-message"},
        conversation={"last_message_at": last_message_at},
    )

    metadata = ThreadMetadataView.from_thread_state(state)

    assert metadata.last_message_at == last_message_at


def test_thread_metadata_view_leaves_last_message_at_empty_without_messages() -> None:
    state = ThreadState(identity={"thread_id": "thread-empty"})

    metadata = ThreadMetadataView.from_thread_state(state)

    assert metadata.last_message_at is None


def test_thread_state_accepts_typed_approval_data() -> None:
    state = make_state()

    assert state.approvals.pending_approval == ApprovalDecision.NEEDS_USER_APPROVAL
    assert state.approvals.granted_permissions[0].file_write_roots == ["/tmp/project"]
    assert state.approvals.session_approval_grants == ["filesystem_write"]
