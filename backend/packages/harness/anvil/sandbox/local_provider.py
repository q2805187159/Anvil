from __future__ import annotations

from pathlib import Path

from .provider import SandboxHandle


class LocalSandboxProvider:
    provider_mode = "local"

    def __init__(self) -> None:
        self._handles: dict[str, SandboxHandle] = {}

    def acquire(self, *, thread_id: str, path_service) -> SandboxHandle:
        existing = self._handles.get(thread_id)
        if existing is not None:
            return existing

        thread_data = path_service.bootstrap_thread_paths(thread_id)
        projection = path_service.to_sandbox_projection(
            thread_id,
            writable_kinds=("workspace", "outputs"),
        )
        handle = SandboxHandle(
            thread_id=thread_id,
            provider_mode=self.provider_mode,
            sandbox_id=f"local:{thread_id}",
            thread_data=thread_data,
            projection=projection,
            path_service=path_service,
        )
        self._handles[thread_id] = handle
        return handle

    def get(self, thread_id: str) -> SandboxHandle | None:
        return self._handles.get(thread_id)

    def release(self, thread_id: str) -> None:
        self._handles.pop(thread_id, None)


class HostIsolatedSandboxProvider(LocalSandboxProvider):
    provider_mode = "host_isolated"
