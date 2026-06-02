from __future__ import annotations

from anvil import ThreadState
from anvil.runtime.checkpointers import (
    CheckpointerBackend,
    create_checkpointer,
    get_cached_checkpointer,
    reset_checkpointer_cache,
)


def make_state(thread_id: str) -> ThreadState:
    return ThreadState(identity={"thread_id": thread_id}, conversation={"title": f"title-{thread_id}"})


def test_in_memory_and_sqlite_checkpointers_share_basic_contract(contract_tmp_path) -> None:
    for backend, sqlite_path in (
        (CheckpointerBackend.IN_MEMORY, None),
        (CheckpointerBackend.SQLITE, contract_tmp_path / "checkpoints.sqlite3"),
    ):
        checkpointer = create_checkpointer(backend, sqlite_path=sqlite_path)
        state = make_state(f"{backend.value}-thread")

        checkpointer.put_thread_state(state)
        loaded = checkpointer.get_thread_state(state.identity.thread_id)

        assert loaded is not None
        assert loaded.identity.thread_id == state.identity.thread_id
        assert checkpointer.list_thread_ids() == [state.identity.thread_id]

        checkpointer.delete_thread(state.identity.thread_id)
        assert checkpointer.get_thread_state(state.identity.thread_id) is None
        checkpointer.close()


def test_cached_checkpointer_can_reset_and_recreate(contract_tmp_path) -> None:
    first = get_cached_checkpointer(CheckpointerBackend.IN_MEMORY)
    second = get_cached_checkpointer(CheckpointerBackend.IN_MEMORY)
    assert first is second

    sqlite_path = contract_tmp_path / "checkpoints.sqlite3"
    sqlite_first = get_cached_checkpointer(CheckpointerBackend.SQLITE, sqlite_path=sqlite_path)
    sqlite_first.put_thread_state(make_state("thread-1"))

    reset_checkpointer_cache()

    sqlite_second = get_cached_checkpointer(CheckpointerBackend.SQLITE, sqlite_path=sqlite_path)
    assert sqlite_second is not sqlite_first
    assert sqlite_second.get_thread_state("thread-1") is not None

    reset_checkpointer_cache()


def test_in_memory_checkpointer_is_explicitly_non_durable() -> None:
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    assert checkpointer.is_durable is False
    checkpointer.close()
