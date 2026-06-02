from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Protocol

from anvil.agents import ThreadDataState
from .file_ops import create_directory, delete_path, file_info, list_directory, move_path, write_text_file
from .file_patcher import apply_patch_operations
from .file_readers import read_textual_file, read_textual_file_window
from .path_service import PathService, SandboxPathProjection


@dataclass
class SandboxHandle:
    thread_id: str
    provider_mode: str
    sandbox_id: str
    thread_data: ThreadDataState
    projection: SandboxPathProjection
    path_service: PathService

    def read_file(self, path: str) -> str:
        host_path = self.path_service.resolve_virtual_path(self.thread_id, path)
        return read_textual_file(host_path)

    def read_file_window(
        self,
        path: str,
        *,
        start_line: int = 1,
        max_lines: int | None = None,
        max_chars: int | None = None,
    ) -> dict[str, object]:
        host_path = self.path_service.resolve_virtual_path(self.thread_id, path)
        result = read_textual_file_window(host_path, start_line=start_line, max_lines=max_lines, max_chars=max_chars)
        return {
            "path": path,
            "content": result.content,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "total_lines": result.total_lines,
            "total_bytes": result.total_bytes,
            "truncated": result.truncated,
        }

    def file_info(self, path: str) -> dict[str, object]:
        host_path = self.path_service.resolve_virtual_path(self.thread_id, path)
        result = file_info(host_path)
        return {
            "path": path,
            "name": result.name,
            "kind": result.kind,
            "exists": result.exists,
            "size_bytes": result.size_bytes,
            "line_count": result.line_count,
            "modified_at": result.modified_at,
        }

    def write_file(self, path: str, content: str, *, overwrite: bool = True) -> dict[str, object]:
        host_path = self.path_service.resolve_virtual_path(self.thread_id, path)
        result = write_text_file(host_path, content, overwrite=overwrite)
        return {
            "path": path,
            "operation": result.operation,
            "bytes_written": result.bytes_written,
            "line_count": result.line_count,
        }

    def patch_file(self, path: str, operations: list[dict[str, object]], *, dry_run: bool = False) -> dict[str, object]:
        host_path = self.path_service.resolve_virtual_path(self.thread_id, path)
        result = apply_patch_operations(host_path, operations, dry_run=dry_run)
        return {
            "path": path,
            "operations_applied": result.operations_applied,
            "line_count": result.line_count,
            "byte_length": result.byte_length,
            "changed": result.changed,
            "dry_run": dry_run,
            "diff": result.diff,
        }

    def list_dir(self, path: str) -> list[str]:
        return self.path_service.list_virtual_dir(self.thread_id, path)

    def list_dir_structured(self, path: str, *, offset: int = 0, limit: int = 100) -> dict[str, object]:
        normalized_path = path.strip().rstrip("/") or path.strip()
        if normalized_path in {"/mnt/user-data", "/mnt/user-data/workspace/_host"}:
            names = self.path_service.list_virtual_dir(self.thread_id, path)
            bounded_offset = max(int(offset or 0), 0)
            bounded_limit = max(1, min(int(limit or 100), 500))
            page = names[bounded_offset : bounded_offset + bounded_limit]
            next_offset = bounded_offset + bounded_limit if bounded_offset + bounded_limit < len(names) else None
            root_path = normalized_path
            return {
                "path": root_path,
                "total_count": len(names),
                "returned_count": len(page),
                "offset": bounded_offset,
                "limit": bounded_limit,
                "truncated": next_offset is not None,
                "next_offset": next_offset,
                "entries": [
                    {
                        "name": name,
                        "path": f"{root_path}/{name}",
                        "kind": "directory",
                        "size_bytes": None,
                        "modified_at": None,
                    }
                    for name in page
                ],
            }
        host_path = self.path_service.resolve_virtual_path(self.thread_id, path)
        result = list_directory(
            host_path,
            virtual_path=path.rstrip("/") or path,
            offset=offset,
            limit=limit,
            to_virtual_path=lambda child: self.path_service.to_virtual_path(self.thread_id, child),
        )
        return {
            "path": result.path,
            "total_count": result.total_count,
            "returned_count": result.returned_count,
            "offset": result.offset,
            "limit": result.limit,
            "truncated": result.truncated,
            "next_offset": result.next_offset,
            "scanned_count": result.scanned_count,
            "scan_truncated": result.scan_truncated,
            "max_entries": result.max_entries,
            "entries": [
                {
                    "name": entry.name,
                    "path": entry.path,
                    "kind": entry.kind,
                    "size_bytes": entry.size_bytes,
                    "modified_at": entry.modified_at,
                }
                for entry in result.entries
            ],
        }

    def delete_path(self, path: str, *, recursive: bool = False) -> dict[str, object]:
        host_path = self.path_service.resolve_virtual_path(self.thread_id, path)
        self._reject_runtime_root_mutation(host_path, operation="delete")
        result = delete_path(host_path, recursive=recursive)
        return {
            "path": path,
            "kind": result.kind,
            "recursive": result.recursive,
        }

    def move_path(self, source_path: str, destination_path: str, *, overwrite: bool = False, copy: bool = False) -> dict[str, object]:
        source_host_path = self.path_service.resolve_virtual_path(self.thread_id, source_path)
        destination_host_path = self.path_service.resolve_virtual_path(self.thread_id, destination_path)
        self._reject_runtime_root_mutation(source_host_path, operation="move")
        if overwrite:
            self._reject_runtime_root_mutation(destination_host_path, operation="overwrite")
        result = move_path(source_host_path, destination_host_path, overwrite=overwrite, copy=copy)
        return {
            "source_path": source_path,
            "destination_path": destination_path,
            "operation": result.operation,
            "source_kind": result.source_kind,
            "overwritten": result.overwritten,
        }

    def make_dir(self, path: str) -> dict[str, object]:
        host_path = self.path_service.resolve_virtual_path(self.thread_id, path)
        result = create_directory(host_path)
        return {
            "path": path,
            "existed": result.existed,
        }

    def _reject_runtime_root_mutation(self, host_path: Path, *, operation: str) -> None:
        resolved = host_path.resolve()
        protected_roots = {
            self.path_service.thread_workspace_dir(self.thread_id).resolve(),
            self.path_service.thread_uploads_dir(self.thread_id).resolve(),
            self.path_service.thread_outputs_dir(self.thread_id).resolve(),
        }
        for bridge_root in self.path_service.path_bridges:
            protected_roots.add(Path(bridge_root.actual_root).resolve())
        if resolved in protected_roots:
            raise ValueError(f"cannot {operation} a runtime root: {self.path_service.to_virtual_path(self.thread_id, resolved)}")

    def execute_command(
        self,
        *,
        command: str,
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        host_cwd = self.path_service.resolve_virtual_path(self.thread_id, cwd)
        self.path_service.ensure_within_any_allowed_root(self.thread_id, host_cwd, self.projection.policy_roots)
        return subprocess.run(
            command,
            cwd=host_cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=True,
            env=env,
            timeout=timeout_seconds,
        )


class SandboxProvider(Protocol):
    provider_mode: str

    def acquire(self, *, thread_id: str, path_service: PathService) -> SandboxHandle: ...

    def get(self, thread_id: str) -> SandboxHandle | None: ...

    def release(self, thread_id: str) -> None: ...
