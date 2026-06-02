from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import shutil
from pathlib import Path
from typing import Literal


DEFAULT_MAX_DIRECTORY_ENTRIES = 10_000
MAX_DIRECTORY_ENTRIES = 100_000


@dataclass(frozen=True)
class FileReadResult:
    content: str
    start_line: int
    end_line: int
    total_lines: int
    total_bytes: int
    truncated: bool


@dataclass(frozen=True)
class FileInfoResult:
    path: str
    name: str
    kind: Literal["file", "directory", "symlink", "other"]
    exists: bool
    size_bytes: int | None
    line_count: int | None
    modified_at: str | None


@dataclass(frozen=True)
class DirectoryEntryResult:
    name: str
    path: str
    kind: Literal["file", "directory", "symlink", "other"]
    size_bytes: int | None
    modified_at: str | None


@dataclass(frozen=True)
class DirectoryListResult:
    path: str
    total_count: int
    returned_count: int
    offset: int
    limit: int
    truncated: bool
    next_offset: int | None
    entries: tuple[DirectoryEntryResult, ...]
    scanned_count: int
    scan_truncated: bool
    max_entries: int


@dataclass(frozen=True)
class FileWriteResult:
    path: str
    operation: Literal["created", "overwritten"]
    bytes_written: int
    line_count: int


@dataclass(frozen=True)
class FileDeleteResult:
    path: str
    kind: Literal["file", "directory"]
    recursive: bool


@dataclass(frozen=True)
class FileMoveResult:
    source_path: str
    destination_path: str
    operation: Literal["moved", "copied"]
    source_kind: Literal["file", "directory"]
    overwritten: bool


@dataclass(frozen=True)
class DirectoryCreateResult:
    path: str
    existed: bool


def slice_text_for_read(text: str, *, start_line: int = 1, max_lines: int | None = None, max_chars: int | None = None) -> FileReadResult:
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if max_lines is not None and max_lines < 1:
        raise ValueError("max_lines must be >= 1 when provided")
    if max_chars is not None and max_chars < 1:
        raise ValueError("max_chars must be >= 1 when provided")

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    total_bytes = len(text.encode("utf-8"))
    if not lines:
        return FileReadResult(content="", start_line=1, end_line=0, total_lines=0, total_bytes=total_bytes, truncated=False)

    if start_line > total_lines:
        raise ValueError(f"start_line out of bounds: {start_line} (total lines: {total_lines})")

    start_index = start_line - 1
    requested_end_index = total_lines if max_lines is None else min(total_lines, start_index + max_lines)
    selected_lines = lines[start_index:requested_end_index]
    content = "".join(selected_lines)
    end_line = start_line + len(selected_lines) - 1
    truncated = requested_end_index < total_lines

    if max_chars is not None and len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
        end_line = start_line + content.count("\n")
        if content and not content.endswith("\n"):
            end_line = min(total_lines, max(end_line, start_line))

    return FileReadResult(
        content=content,
        start_line=start_line,
        end_line=end_line,
        total_lines=total_lines,
        total_bytes=total_bytes,
        truncated=truncated,
    )


def write_text_file(host_path: Path, content: str, *, overwrite: bool = True) -> FileWriteResult:
    existed = host_path.exists()
    if existed and not host_path.is_file():
        raise ValueError(f"write target is not a file: {host_path}")
    if existed and not overwrite:
        raise ValueError(f"write target already exists: {host_path}")
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text(content, encoding="utf-8")
    return FileWriteResult(
        path=str(host_path),
        operation="overwritten" if existed else "created",
        bytes_written=len(content.encode("utf-8")),
        line_count=_line_count(content),
    )


def file_info(host_path: Path) -> FileInfoResult:
    exists = host_path.exists()
    kind = _path_kind(host_path) if exists or host_path.is_symlink() else "other"
    size_bytes: int | None = None
    line_count: int | None = None
    modified_at: str | None = None
    if exists or host_path.is_symlink():
        try:
            stat = host_path.stat()
            size_bytes = stat.st_size if host_path.is_file() else None
            modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        except OSError:
            pass
        if host_path.is_file():
            try:
                line_count = _line_count(host_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                line_count = None
    return FileInfoResult(
        path=str(host_path),
        name=host_path.name,
        kind=kind,
        exists=exists,
        size_bytes=size_bytes,
        line_count=line_count,
        modified_at=modified_at,
    )


def list_directory(host_path: Path, *, virtual_path: str, to_virtual_path, offset: int = 0, limit: int = 100) -> DirectoryListResult:
    if not host_path.exists():
        raise ValueError(f"path does not exist: {host_path}")
    if not host_path.is_dir():
        raise ValueError(f"path is not a directory: {host_path}")
    bounded_offset = max(int(offset or 0), 0)
    bounded_limit = max(1, min(int(limit or 100), 500))
    max_entries = max(1, min(int(DEFAULT_MAX_DIRECTORY_ENTRIES), MAX_DIRECTORY_ENTRIES))
    children: list[Path] = []
    scanned_count = 0
    scan_truncated = False
    for child in host_path.iterdir():
        if scanned_count >= max_entries:
            scan_truncated = True
            break
        children.append(child)
        scanned_count += 1
    children = sorted(children, key=lambda item: (0 if item.is_dir() else 1, item.name.lower()))
    page = children[bounded_offset : bounded_offset + bounded_limit]
    entries = tuple(_directory_entry(child, to_virtual_path=to_virtual_path) for child in page)
    total_count = len(children)
    has_more = bounded_offset + bounded_limit < total_count or scan_truncated
    next_offset = bounded_offset + len(entries) if has_more else None
    return DirectoryListResult(
        path=virtual_path,
        total_count=total_count,
        returned_count=len(entries),
        offset=bounded_offset,
        limit=bounded_limit,
        truncated=has_more,
        next_offset=next_offset,
        entries=entries,
        scanned_count=scanned_count,
        scan_truncated=scan_truncated,
        max_entries=max_entries,
    )


def delete_path(host_path: Path, *, recursive: bool = False) -> FileDeleteResult:
    if not host_path.exists():
        raise ValueError(f"delete target does not exist: {host_path}")
    if host_path.is_file() or host_path.is_symlink():
        host_path.unlink()
        return FileDeleteResult(path=str(host_path), kind="file", recursive=False)
    if not host_path.is_dir():
        raise ValueError(f"delete target is not a regular file or directory: {host_path}")
    if recursive:
        shutil.rmtree(host_path)
        return FileDeleteResult(path=str(host_path), kind="directory", recursive=True)
    host_path.rmdir()
    return FileDeleteResult(path=str(host_path), kind="directory", recursive=False)


def move_path(source_path: Path, destination_path: Path, *, overwrite: bool = False, copy: bool = False) -> FileMoveResult:
    if not source_path.exists():
        raise ValueError(f"source path does not exist: {source_path}")
    resolved_source = source_path.resolve()
    resolved_destination = destination_path.resolve()
    if resolved_source == resolved_destination:
        raise ValueError("source and destination paths must be different")
    if source_path.is_dir():
        try:
            resolved_destination.relative_to(resolved_source)
        except ValueError:
            pass
        else:
            raise ValueError("destination path must not be inside the source directory")
    if destination_path.exists() and not overwrite:
        raise ValueError(f"destination path already exists: {destination_path}")

    source_kind: Literal["file", "directory"] = "directory" if source_path.is_dir() else "file"
    existed = destination_path.exists()
    if existed:
        delete_path(destination_path, recursive=destination_path.is_dir())

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        if source_path.is_dir():
            shutil.copytree(source_path, destination_path)
        else:
            shutil.copy2(source_path, destination_path)
        operation: Literal["moved", "copied"] = "copied"
    else:
        shutil.move(str(source_path), str(destination_path))
        operation = "moved"

    return FileMoveResult(
        source_path=str(source_path),
        destination_path=str(destination_path),
        operation=operation,
        source_kind=source_kind,
        overwritten=existed,
    )


def create_directory(host_path: Path) -> DirectoryCreateResult:
    if host_path.exists() and not host_path.is_dir():
        raise ValueError(f"directory target is not a directory: {host_path}")
    existed = host_path.exists()
    host_path.mkdir(parents=True, exist_ok=True)
    return DirectoryCreateResult(path=str(host_path), existed=existed)


def _line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _directory_entry(host_path: Path, *, to_virtual_path) -> DirectoryEntryResult:
    info = file_info(host_path)
    return DirectoryEntryResult(
        name=host_path.name,
        path=to_virtual_path(host_path),
        kind=info.kind,
        size_bytes=info.size_bytes,
        modified_at=info.modified_at,
    )


def _path_kind(host_path: Path) -> Literal["file", "directory", "symlink", "other"]:
    if host_path.is_symlink():
        return "symlink"
    if host_path.is_file():
        return "file"
    if host_path.is_dir():
        return "directory"
    return "other"
