from __future__ import annotations

from datetime import datetime, timezone

from anvil import ThreadMetadataView, ThreadState
from anvil.runtime.store import StoreBackend, create_store, get_cached_store, reset_store_cache


def make_metadata(
    thread_id: str,
    *,
    updated_at: datetime | None = None,
    last_message_at: datetime | None = None,
) -> ThreadMetadataView:
    lifecycle = {"updated_at": updated_at} if updated_at is not None else {}
    conversation = {"title": f"title-{thread_id}"}
    if last_message_at is not None:
        conversation["last_message_at"] = last_message_at
    state = ThreadState(identity={"thread_id": thread_id}, lifecycle=lifecycle, conversation=conversation)
    return ThreadMetadataView.from_thread_state(state)


def test_in_memory_and_sqlite_store_share_basic_contract(contract_tmp_path) -> None:
    for backend, sqlite_path in (
        (StoreBackend.IN_MEMORY, None),
        (StoreBackend.SQLITE, contract_tmp_path / "store.sqlite3"),
    ):
        store = create_store(backend, sqlite_path=sqlite_path)
        metadata = make_metadata(f"{backend.value}-thread")

        store.put_thread_metadata(metadata)
        loaded = store.get_thread_metadata(metadata.thread_id)

        assert loaded is not None
        assert loaded.thread_id == metadata.thread_id
        assert [item.thread_id for item in store.list_threads()] == [metadata.thread_id]

        store.delete_thread(metadata.thread_id)
        assert store.get_thread_metadata(metadata.thread_id) is None
        store.close()


def test_store_reset_and_recreate_behavior(contract_tmp_path) -> None:
    first = get_cached_store(StoreBackend.IN_MEMORY)
    second = get_cached_store(StoreBackend.IN_MEMORY)
    assert first is second

    sqlite_path = contract_tmp_path / "store.sqlite3"
    sqlite_first = get_cached_store(StoreBackend.SQLITE, sqlite_path=sqlite_path)
    sqlite_first.put_thread_metadata(make_metadata("thread-1"))

    reset_store_cache()

    sqlite_second = get_cached_store(StoreBackend.SQLITE, sqlite_path=sqlite_path)
    assert sqlite_second is not sqlite_first
    assert sqlite_second.get_thread_metadata("thread-1") is not None

    reset_store_cache()


def test_store_is_metadata_only_contract() -> None:
    store = create_store(StoreBackend.IN_MEMORY)
    metadata = make_metadata("thread-1")
    store.put_thread_metadata(metadata)

    loaded = store.get_thread_metadata("thread-1")
    assert loaded is not None
    assert not hasattr(loaded, "conversation")
    store.close()


def test_store_lists_threads_by_latest_update_descending(contract_tmp_path) -> None:
    for backend, sqlite_path in (
        (StoreBackend.IN_MEMORY, None),
        (StoreBackend.SQLITE, contract_tmp_path / "store-recency.sqlite3"),
    ):
        store = create_store(backend, sqlite_path=sqlite_path)
        store.put_thread_metadata(
            make_metadata(
                "thread-middle",
                updated_at=datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc),
            )
        )
        store.put_thread_metadata(
            make_metadata(
                "thread-new",
                updated_at=datetime(2026, 5, 23, 11, 0, tzinfo=timezone.utc),
            )
        )
        store.put_thread_metadata(
            make_metadata(
                "thread-old",
                updated_at=datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc),
            )
        )

        assert [item.thread_id for item in store.list_threads()] == ["thread-new", "thread-middle", "thread-old"]

        store.put_thread_metadata(
            make_metadata(
                "thread-old",
                updated_at=datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc),
            )
        )

        assert [item.thread_id for item in store.list_threads()] == ["thread-old", "thread-new", "thread-middle"]
        store.close()


def test_store_tie_breaks_thread_recency_by_id(contract_tmp_path) -> None:
    timestamp = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)
    for backend, sqlite_path in (
        (StoreBackend.IN_MEMORY, None),
        (StoreBackend.SQLITE, contract_tmp_path / "store-recency-tie.sqlite3"),
    ):
        store = create_store(backend, sqlite_path=sqlite_path)
        store.put_thread_metadata(make_metadata("thread-b", updated_at=timestamp))
        store.put_thread_metadata(make_metadata("thread-a", updated_at=timestamp))

        assert [item.thread_id for item in store.list_threads()] == ["thread-a", "thread-b"]
        store.close()


def test_store_recency_prefers_latest_message_over_settings_update(contract_tmp_path) -> None:
    for backend, sqlite_path in (
        (StoreBackend.IN_MEMORY, None),
        (StoreBackend.SQLITE, contract_tmp_path / "store-message-recency.sqlite3"),
    ):
        store = create_store(backend, sqlite_path=sqlite_path)
        store.put_thread_metadata(
            make_metadata(
                "thread-settings-edited",
                updated_at=datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc),
                last_message_at=datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc),
            )
        )
        store.put_thread_metadata(
            make_metadata(
                "thread-latest-message",
                updated_at=datetime(2026, 5, 23, 11, 0, tzinfo=timezone.utc),
                last_message_at=datetime(2026, 5, 23, 11, 0, tzinfo=timezone.utc),
            )
        )

        assert [item.thread_id for item in store.list_threads()] == ["thread-latest-message", "thread-settings-edited"]
        store.close()
