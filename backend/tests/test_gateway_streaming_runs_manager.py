from __future__ import annotations

import threading
import time

from app.contracts import RunStreamEvent
from app.gateway.streaming_runs import BackgroundRunStreamManager, _event_after_cursor


def test_background_run_interrupt_closes_subscribers_and_suppresses_late_replay() -> None:
    manager = BackgroundRunStreamManager(max_buffer=10)
    release_late_event = threading.Event()

    def factory():
        yield RunStreamEvent(event="run_started", data={"thread_id": "thread-a"})
        release_late_event.wait(timeout=1)
        yield RunStreamEvent(event="step_delta", data={"thread_id": "thread-a", "payload_delta": "late"})
        yield RunStreamEvent(event="run_completed", data={"thread_id": "thread-a", "status": "completed"})

    stream = manager.stream("run:thread-a", factory)
    assert next(stream).event == "run_started"

    assert manager.request_interrupt("run:thread-a", reason="Stopped") is True
    assert list(stream) == []

    release_late_event.set()
    for _ in range(20):
        run = manager._runs["run:thread-a"]  # noqa: SLF001 - targeted lifecycle regression.
        if run.done:
            break
        time.sleep(0.02)

    run = manager._runs["run:thread-a"]  # noqa: SLF001 - targeted lifecycle regression.
    assert run.done is True
    assert [event.event for event in run.buffer] == ["run_started"]


def test_background_run_reconnect_after_interrupt_returns_without_waiting_for_factory() -> None:
    manager = BackgroundRunStreamManager(max_buffer=10)
    release_late_event = threading.Event()

    def factory():
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )
        release_late_event.wait(timeout=1)
        yield RunStreamEvent(
            event="step_delta",
            event_id="run-1:000002",
            sequence=2,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 2},
        )

    stream = manager.stream("run:thread-a", factory)
    assert next(stream).event == "run_started"
    assert manager.request_interrupt("run:thread-a", reason="Stopped") is True

    reconnect_result: list[str] | None = None

    def reconnect() -> None:
        nonlocal reconnect_result
        reconnect_result = [event.event for event in manager.stream("run:thread-a", factory, last_event_id="run-1:000001")]

    reconnect_thread = threading.Thread(target=reconnect)
    reconnect_thread.start()
    reconnect_thread.join(timeout=0.2)
    completed_before_factory_release = not reconnect_thread.is_alive()

    release_late_event.set()
    stream.close()
    reconnect_thread.join(timeout=1)

    assert completed_before_factory_release is True
    assert reconnect_result == []


def test_background_run_manager_close_suppresses_late_factory_events() -> None:
    manager = BackgroundRunStreamManager(max_buffer=10)
    release_late_event = threading.Event()
    factory_reached_late_event = threading.Event()

    def factory():
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )
        release_late_event.wait(timeout=1)
        factory_reached_late_event.set()
        yield RunStreamEvent(
            event="step_delta",
            event_id="run-1:000002",
            sequence=2,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 2, "payload_delta": "after close"},
        )
        yield RunStreamEvent(
            event="run_completed",
            event_id="run-1:000003",
            sequence=3,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 3, "status": "completed"},
        )

    stream = manager.stream("run:thread-a", factory)
    assert next(stream).event == "run_started"
    run = manager._runs["run:thread-a"]  # noqa: SLF001 - targeted shutdown lifecycle regression.

    manager.close()
    assert list(stream) == []

    release_late_event.set()
    assert factory_reached_late_event.wait(timeout=1) is True
    time.sleep(0.05)

    assert [event.event for event in run.buffer] == ["run_started"]


def test_background_run_replay_respects_last_event_id_cursor() -> None:
    manager = BackgroundRunStreamManager(max_buffer=10)

    def factory():
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )
        yield RunStreamEvent(
            event="step_started",
            event_id="run-1:000002",
            sequence=2,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 2},
        )
        yield RunStreamEvent(
            event="run_completed",
            event_id="run-1:000003",
            sequence=3,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 3},
        )

    assert [event.event for event in manager.stream("run:thread-a", factory)] == [
        "run_started",
        "step_started",
        "run_completed",
    ]
    assert [event.event for event in manager.stream("run:thread-a", factory, last_event_id="run-1:000001")] == [
        "step_started",
        "run_completed",
    ]


def test_background_run_initial_subscriber_receives_events_before_buffer_trims() -> None:
    manager = BackgroundRunStreamManager(max_buffer=2)

    def factory():
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )
        yield RunStreamEvent(
            event="step_started",
            event_id="run-1:000002",
            sequence=2,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 2},
        )
        yield RunStreamEvent(
            event="step_delta",
            event_id="run-1:000003",
            sequence=3,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 3},
        )
        yield RunStreamEvent(
            event="run_completed",
            event_id="run-1:000004",
            sequence=4,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 4},
        )

    assert [event.event for event in manager.stream("run:thread-a", factory)] == [
        "run_started",
        "step_started",
        "step_delta",
        "run_completed",
    ]


def test_background_run_reconnect_with_cursor_before_buffer_returns_empty_for_durable_replay() -> None:
    manager = BackgroundRunStreamManager(max_buffer=2)

    def factory():
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )
        yield RunStreamEvent(
            event="step_started",
            event_id="run-1:000002",
            sequence=2,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 2},
        )
        yield RunStreamEvent(
            event="step_delta",
            event_id="run-1:000003",
            sequence=3,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 3},
        )
        yield RunStreamEvent(
            event="run_completed",
            event_id="run-1:000004",
            sequence=4,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 4},
        )

    assert [event.event for event in manager.stream("run:thread-a", factory)] == [
        "run_started",
        "step_started",
        "step_delta",
        "run_completed",
    ]

    assert [event.event for event in manager.stream("run:thread-a", factory, last_event_id="run-1:000001")] == []


def test_background_run_live_reconnect_with_cursor_before_buffer_returns_empty_without_future_events() -> None:
    manager = BackgroundRunStreamManager(max_buffer=2)
    release_completion = threading.Event()

    def factory():
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )
        yield RunStreamEvent(
            event="step_started",
            event_id="run-1:000002",
            sequence=2,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 2},
        )
        yield RunStreamEvent(
            event="step_delta",
            event_id="run-1:000003",
            sequence=3,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 3},
        )
        release_completion.wait(timeout=1)
        yield RunStreamEvent(
            event="run_completed",
            event_id="run-1:000004",
            sequence=4,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 4},
        )

    stream = manager.stream("run:thread-a", factory)
    assert next(stream).event == "run_started"
    assert next(stream).event == "step_started"
    assert next(stream).event == "step_delta"

    reconnect_result: list[str] | None = None

    def reconnect() -> None:
        nonlocal reconnect_result
        reconnect_result = [
            event.event for event in manager.stream("run:thread-a", factory, last_event_id="run-1:000001")
        ]

    reconnect_thread = threading.Thread(target=reconnect)
    reconnect_thread.start()
    reconnect_thread.join(timeout=0.2)
    completed_before_completion_release = not reconnect_thread.is_alive()

    release_completion.set()
    stream.close()
    reconnect_thread.join(timeout=1)

    assert completed_before_completion_release is True
    assert reconnect_result == []


def test_background_run_live_reconnect_filters_future_events_before_cursor() -> None:
    manager = BackgroundRunStreamManager(max_buffer=10)
    release_followup = threading.Event()

    def factory():
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )
        release_followup.wait(timeout=1)
        yield RunStreamEvent(
            event="step_started",
            event_id="run-1:000002",
            sequence=2,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 2},
        )
        yield RunStreamEvent(
            event="step_delta",
            event_id="run-1:000003",
            sequence=3,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 3},
        )
        yield RunStreamEvent(
            event="run_completed",
            event_id="run-1:000004",
            sequence=4,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 4},
        )

    stream = manager.stream("run:thread-a", factory)
    assert next(stream).event == "run_started"
    reconnect_result: list[str] | None = None

    def reconnect() -> None:
        nonlocal reconnect_result
        reconnect_result = [
            event.event for event in manager.stream("run:thread-a", factory, last_event_id="run-1:000003")
        ]

    reconnect_thread = threading.Thread(target=reconnect)
    reconnect_thread.start()
    time.sleep(0.05)
    release_followup.set()
    reconnect_thread.join(timeout=1)
    stream.close()

    assert reconnect_result == ["run_completed"]


def test_background_run_cursor_does_not_filter_events_from_a_new_run_id() -> None:
    assert _event_after_cursor(
        RunStreamEvent(
            event="run_started",
            event_id="run-new:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-new", "sequence": 1},
        ),
        "run-old:000002",
    ) is True
    assert _event_after_cursor(
        RunStreamEvent(
            event="run_started",
            event_id="run-old:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-old", "sequence": 1},
        ),
        "run-old:000002",
    ) is False


def test_background_run_disconnect_keeps_run_alive_and_reconnect_replays_without_restart() -> None:
    manager = BackgroundRunStreamManager(max_buffer=10)
    release_second_event = threading.Event()
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )
        release_second_event.wait(timeout=1)
        yield RunStreamEvent(
            event="step_delta",
            event_id="run-1:000002",
            sequence=2,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 2, "payload_delta": "still running"},
        )
        yield RunStreamEvent(
            event="run_completed",
            event_id="run-1:000003",
            sequence=3,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 3, "status": "completed"},
        )

    stream = manager.stream("run:thread-a", factory)
    first = next(stream)
    assert first.event == "run_started"
    stream.close()

    release_second_event.set()
    for _ in range(20):
        run = manager._runs["run:thread-a"]  # noqa: SLF001 - targeted reconnect lifecycle regression.
        if run.done:
            break
        time.sleep(0.02)

    replayed = list(manager.stream("run:thread-a", factory, last_event_id="run-1:000001"))

    assert factory_calls == 1
    assert [event.event for event in replayed] == ["step_delta", "run_completed"]


def test_background_run_reconnect_with_missing_memory_run_does_not_start_factory() -> None:
    manager = BackgroundRunStreamManager(max_buffer=10)
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )

    assert list(manager.stream("run:thread-a", factory, last_event_id="run-1:000001")) == []
    assert factory_calls == 0


def test_background_run_manager_evicts_old_completed_runs_without_restarting_factory() -> None:
    manager = BackgroundRunStreamManager(max_buffer=10, max_completed_runs=2)
    factory_calls: list[str] = []

    def factory_for(run_number: int):
        def factory():
            factory_calls.append(f"run-{run_number}")
            yield RunStreamEvent(
                event="run_started",
                event_id=f"run-{run_number}:000001",
                sequence=1,
                data={
                    "thread_id": f"thread-{run_number}",
                    "run_id": f"run-{run_number}",
                    "sequence": 1,
                },
            )
            yield RunStreamEvent(
                event="run_completed",
                event_id=f"run-{run_number}:000002",
                sequence=2,
                data={
                    "thread_id": f"thread-{run_number}",
                    "run_id": f"run-{run_number}",
                    "sequence": 2,
                    "status": "completed",
                },
            )

        return factory

    for run_number in range(1, 4):
        assert [event.event for event in manager.stream(f"run:thread-{run_number}", factory_for(run_number))] == [
            "run_started",
            "run_completed",
        ]

    assert "run:thread-1" not in manager._runs  # noqa: SLF001 - targeted completed replay cache regression.
    assert set(manager._runs) == {"run:thread-2", "run:thread-3"}  # noqa: SLF001

    assert list(
        manager.stream(
            "run:thread-1",
            factory_for(1),
            last_event_id="run-1:000001",
        )
    ) == []
    assert factory_calls == ["run-1", "run-2", "run-3"]


def test_background_run_manager_can_disable_completed_memory_replay() -> None:
    manager = BackgroundRunStreamManager(max_buffer=10, max_completed_runs=0)
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        yield RunStreamEvent(
            event="run_started",
            event_id="run-1:000001",
            sequence=1,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 1},
        )
        yield RunStreamEvent(
            event="run_completed",
            event_id="run-1:000002",
            sequence=2,
            data={"thread_id": "thread-a", "run_id": "run-1", "sequence": 2, "status": "completed"},
        )

    assert [event.event for event in manager.stream("run:thread-a", factory)] == ["run_started", "run_completed"]
    assert "run:thread-a" not in manager._runs  # noqa: SLF001 - targeted completed replay cache regression.

    assert list(manager.stream("run:thread-a", factory, last_event_id="run-1:000001")) == []
    assert factory_calls == 1
