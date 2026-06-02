from __future__ import annotations

from anvil.agents import ThreadState

from .base import CheckpointerBackend


class InMemoryCheckpointer:
    backend = CheckpointerBackend.IN_MEMORY
    is_durable = False

    def __init__(self) -> None:
        self._state_by_thread: dict[str, ThreadState] = {}

    def put_thread_state(self, state: ThreadState) -> ThreadState:
        self._state_by_thread[state.identity.thread_id] = state.model_copy(deep=True)
        return state

    def get_thread_state(self, thread_id: str) -> ThreadState | None:
        state = self._state_by_thread.get(thread_id)
        return state.model_copy(deep=True) if state is not None else None

    def delete_thread(self, thread_id: str) -> None:
        self._state_by_thread.pop(thread_id, None)

    def list_thread_ids(self) -> list[str]:
        return sorted(self._state_by_thread)

    def reset(self) -> None:
        self._state_by_thread.clear()

    def close(self) -> None:
        self.reset()
