from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..sandbox.path_service import PathService


class InheritedThreadPathService:
    """PathService proxy that gives a child thread its own identity while reusing a parent thread's user-data roots."""

    def __init__(self, *, base: "PathService", child_thread_id: str, parent_thread_id: str) -> None:
        self.base = base
        self.child_thread_id = child_thread_id
        self.parent_thread_id = parent_thread_id
        self.base_root = base.base_root
        self.artifact_base_url = base.artifact_base_url
        self.path_bridges = base.path_bridges

    def bootstrap_thread_paths(self, thread_id: str):
        if thread_id == self.child_thread_id:
            return self.base.bootstrap_thread_paths(self.parent_thread_id)
        return self.base.bootstrap_thread_paths(thread_id)

    def thread_workspace_dir(self, thread_id: str) -> Path:
        return self.base.thread_workspace_dir(self._effective_thread_id(thread_id))

    def thread_uploads_dir(self, thread_id: str) -> Path:
        return self.base.thread_uploads_dir(self._effective_thread_id(thread_id))

    def thread_outputs_dir(self, thread_id: str) -> Path:
        return self.base.thread_outputs_dir(self._effective_thread_id(thread_id))

    def thread_scratch_dir(self, thread_id: str) -> Path:
        return self.base.thread_scratch_dir(self._effective_thread_id(thread_id))

    def resolve_virtual_path(self, thread_id: str, virtual_path: str) -> Path:
        return self.base.resolve_virtual_path(self._effective_thread_id(thread_id), virtual_path)

    def list_virtual_dir(self, thread_id: str, virtual_path: str) -> list[str]:
        return self.base.list_virtual_dir(self._effective_thread_id(thread_id), virtual_path)

    def to_virtual_path(self, thread_id: str, host_path: str | Path) -> str:
        return self.base.to_virtual_path(self._effective_thread_id(thread_id), host_path)

    def to_artifact_descriptor(self, thread_id: str, kind: object, relative_path: str):
        return self.base.to_artifact_descriptor(thread_id, kind, relative_path)

    def ensure_within_allowed_root(self, thread_id: str, host_path: str | Path, allowed_root: str | Path) -> None:
        return self.base.ensure_within_allowed_root(self._effective_thread_id(thread_id), host_path, allowed_root)

    def to_sandbox_projection(self, thread_id: str, logical_cwd: str = "/mnt/user-data/workspace", writable_kinds: tuple[object, ...] = ("workspace",)):
        return self.base.to_sandbox_projection(self._effective_thread_id(thread_id), logical_cwd=logical_cwd, writable_kinds=writable_kinds)

    def visible_runtime_roots(self, thread_id: str):
        return self.base.visible_runtime_roots(self._effective_thread_id(thread_id))

    def virtual_path_map(self, thread_id: str) -> dict[str, str]:
        return self.base.virtual_path_map(self._effective_thread_id(thread_id))

    def translate_user_text_to_runtime(self, text: str | None, thread_id: str | None = None) -> str | None:
        return self.base.translate_user_text_to_runtime(text, thread_id=self._effective_thread_id(thread_id) if thread_id is not None else None)

    def translate_runtime_text_to_display(self, text: str | None, thread_id: str | None = None) -> str | None:
        return self.base.translate_runtime_text_to_display(text, thread_id=self._effective_thread_id(thread_id) if thread_id is not None else None)

    def translate_runtime_text_to_host(self, text: str | None, thread_id: str | None = None) -> str | None:
        return self.base.translate_runtime_text_to_host(text, thread_id=self._effective_thread_id(thread_id) if thread_id is not None else None)

    def translate_runtime_text_to_virtual(self, text: str | None, thread_id: str | None = None) -> str | None:
        return self.base.translate_runtime_text_to_virtual(text, thread_id=self._effective_thread_id(thread_id) if thread_id is not None else None)

    def translate_runtime_data_to_display(self, value: Any, thread_id: str | None = None) -> Any:
        return self.base.translate_runtime_data_to_display(value, thread_id=self._effective_thread_id(thread_id) if thread_id is not None else None)

    def translate_runtime_data_to_host(self, value: Any, thread_id: str | None = None) -> Any:
        return self.base.translate_runtime_data_to_host(value, thread_id=self._effective_thread_id(thread_id) if thread_id is not None else None)

    def translate_runtime_data_to_virtual(self, value: Any, thread_id: str | None = None) -> Any:
        return self.base.translate_runtime_data_to_virtual(value, thread_id=self._effective_thread_id(thread_id) if thread_id is not None else None)

    def translate_user_data_to_runtime(self, value: Any, thread_id: str | None = None) -> Any:
        return self.base.translate_user_data_to_runtime(value, thread_id=self._effective_thread_id(thread_id) if thread_id is not None else None)

    def list_artifact_relative_paths(self, thread_id: str, kind: object) -> list[str]:
        return self.base.list_artifact_relative_paths(self._effective_thread_id(thread_id), kind)

    def _effective_thread_id(self, thread_id: str | None) -> str:
        if thread_id is None:
            return self.parent_thread_id
        return self.parent_thread_id if thread_id == self.child_thread_id else thread_id
