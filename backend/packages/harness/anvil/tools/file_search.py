from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".turbo",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "venv",
    }
)
DEFAULT_MAX_RESULTS = 50
MAX_RESULTS = 500
DEFAULT_MAX_FILE_BYTES = 1_000_000
DEFAULT_MAX_SCANNED_FILES = 5_000
DEFAULT_MAX_SCANNED_DIRS = 2_000
MAX_SCANNED_FILES = 50_000
MAX_SCANNED_DIRS = 20_000
MAX_CONTEXT_LINES = 5
MAX_LINE_CHARS = 320


@dataclass(frozen=True)
class SearchRoot:
    virtual_path: str
    host_path: Path


def search_runtime_files(
    *,
    path_service: Any,
    thread_id: str,
    pattern: str,
    target: str = "content",
    path: str = "/mnt/user-data/workspace",
    file_glob: str | None = None,
    limit: int = DEFAULT_MAX_RESULTS,
    offset: int = 0,
    output_mode: str = "content",
    context: int = 0,
    literal: bool = False,
    case_sensitive: bool = False,
    include_hidden: bool = False,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> dict[str, object]:
    """Search files inside Anvil virtual roots without shelling out."""

    normalized_target = _normalize_target(target)
    normalized_output = _normalize_output_mode(output_mode)
    bounded_limit = _bounded_int(limit, default=DEFAULT_MAX_RESULTS, minimum=1, maximum=MAX_RESULTS)
    bounded_offset = _bounded_int(offset, default=0, minimum=0, maximum=1_000_000)
    bounded_context = _bounded_int(context, default=0, minimum=0, maximum=MAX_CONTEXT_LINES)
    bounded_file_bytes = _bounded_int(max_file_bytes, default=DEFAULT_MAX_FILE_BYTES, minimum=1, maximum=10_000_000)
    max_scanned_files = _bounded_int(
        DEFAULT_MAX_SCANNED_FILES,
        default=5_000,
        minimum=1,
        maximum=MAX_SCANNED_FILES,
    )
    max_scanned_dirs = _bounded_int(
        DEFAULT_MAX_SCANNED_DIRS,
        default=2_000,
        minimum=1,
        maximum=MAX_SCANNED_DIRS,
    )
    normalized_pattern = (pattern or "").strip()
    if not normalized_pattern:
        return _error_payload("pattern is required", target=normalized_target, path=path)

    try:
        roots = _resolve_roots(path_service=path_service, thread_id=thread_id, path=path)
    except Exception as exc:  # noqa: BLE001
        return _error_payload(str(exc), target=normalized_target, path=path)
    if not roots:
        return _error_payload(f"path does not exist: {path}", target=normalized_target, path=path)

    if normalized_target == "files":
        return _search_file_names(
            path_service=path_service,
            thread_id=thread_id,
            roots=roots,
            pattern=normalized_pattern,
            limit=bounded_limit,
            offset=bounded_offset,
            include_hidden=include_hidden,
            max_scanned_files=max_scanned_files,
            max_scanned_dirs=max_scanned_dirs,
        )

    return _search_file_content(
        path_service=path_service,
        thread_id=thread_id,
        roots=roots,
        pattern=normalized_pattern,
        file_glob=file_glob,
        limit=bounded_limit,
        offset=bounded_offset,
        output_mode=normalized_output,
        context=bounded_context,
        literal=literal,
        case_sensitive=case_sensitive,
        include_hidden=include_hidden,
        max_file_bytes=bounded_file_bytes,
        max_scanned_files=max_scanned_files,
        max_scanned_dirs=max_scanned_dirs,
    )


def _search_file_names(
    *,
    path_service: Any,
    thread_id: str,
    roots: tuple[SearchRoot, ...],
    pattern: str,
    limit: int,
    offset: int,
    include_hidden: bool,
    max_scanned_files: int,
    max_scanned_dirs: int,
) -> dict[str, object]:
    files: list[str] = []
    stats = _search_stats(max_scanned_files=max_scanned_files, max_scanned_dirs=max_scanned_dirs)
    for root in roots:
        if root.host_path.is_file():
            _record_scanned_file(stats)
            if _path_matches(pattern, root.host_path.name, root.host_path.name):
                files.append(root.virtual_path)
            continue
        for file_path in _iter_files(
            root.host_path,
            include_hidden=include_hidden,
            stats=stats,
            max_scanned_files=max_scanned_files,
            max_scanned_dirs=max_scanned_dirs,
        ):
            relative = file_path.relative_to(root.host_path).as_posix()
            if not _path_matches(pattern, file_path.name, relative):
                continue
            try:
                files.append(path_service.to_virtual_path(thread_id, file_path))
            except ValueError:
                continue

    files = sorted(dict.fromkeys(files))
    page = files[offset : offset + limit]
    return {
        "target": "files",
        "pattern": pattern,
        "total_count": len(files),
        "returned_count": len(page),
        "offset": offset,
        "limit": limit,
        "truncated": offset + limit < len(files) or bool(stats["scan_truncated"]),
        "next_offset": offset + len(page) if offset + limit < len(files) or bool(stats["scan_truncated"]) else None,
        "files": page,
        "stats": stats,
    }


def _search_file_content(
    *,
    path_service: Any,
    thread_id: str,
    roots: tuple[SearchRoot, ...],
    pattern: str,
    file_glob: str | None,
    limit: int,
    offset: int,
    output_mode: str,
    context: int,
    literal: bool,
    case_sensitive: bool,
    include_hidden: bool,
    max_file_bytes: int,
    max_scanned_files: int,
    max_scanned_dirs: int,
) -> dict[str, object]:
    try:
        matcher = _compile_matcher(pattern, literal=literal, case_sensitive=case_sensitive)
    except re.error as exc:
        return _error_payload(f"invalid regex pattern: {exc}", target="content", path=", ".join(root.virtual_path for root in roots))

    matches: list[dict[str, object]] = []
    files_only: list[str] = []
    counts: dict[str, int] = {}
    stats = _search_stats(max_scanned_files=max_scanned_files, max_scanned_dirs=max_scanned_dirs)
    stats.update(
        {
            "binary_files_skipped": 0,
            "large_files_skipped": 0,
            "unreadable_files": 0,
        }
    )
    total_count = 0
    truncated = False

    for root in roots:
        candidates: Iterable[Path]
        if root.host_path.is_file():
            _record_scanned_file(stats)
            candidates = (root.host_path,)
        else:
            candidates = _iter_files(
                root.host_path,
                include_hidden=include_hidden,
                stats=stats,
                max_scanned_files=max_scanned_files,
                max_scanned_dirs=max_scanned_dirs,
            )
        for file_path in candidates:
            if file_glob and not _path_matches(file_glob, file_path.name, _relative_for_root(file_path, root)):
                continue
            if not _is_probably_text_file(file_path, max_file_bytes=max_file_bytes, stats=stats):
                continue
            try:
                virtual_path = path_service.to_virtual_path(thread_id, file_path)
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:  # noqa: BLE001
                stats["unreadable_files"] += 1
                continue

            file_match_count = 0
            file_added = False
            for index, line in enumerate(lines, start=1):
                if not matcher(line):
                    continue
                total_count += 1
                file_match_count += 1
                if output_mode == "files_only":
                    if not file_added:
                        files_only.append(virtual_path)
                        file_added = True
                    continue
                if output_mode == "count":
                    continue
                if total_count <= offset:
                    continue
                if len(matches) >= limit:
                    truncated = True
                    break
                item: dict[str, object] = {
                    "path": virtual_path,
                    "line": index,
                    "text": _truncate_line(line),
                }
                if context:
                    before_start = max(index - context - 1, 0)
                    after_end = min(index + context, len(lines))
                    item["context_before"] = [
                        {"line": before_start + pos + 1, "text": _truncate_line(text)}
                        for pos, text in enumerate(lines[before_start : index - 1])
                    ]
                    item["context_after"] = [
                        {"line": index + pos + 1, "text": _truncate_line(text)}
                        for pos, text in enumerate(lines[index:after_end])
                    ]
                matches.append(item)
            if file_match_count:
                counts[virtual_path] = file_match_count
            if truncated:
                break
        if truncated:
            break

    if output_mode == "files_only":
        unique_files = sorted(dict.fromkeys(files_only))
        page = unique_files[offset : offset + limit]
        return {
            "target": "content",
            "pattern": pattern,
            "file_glob": file_glob,
            "output_mode": output_mode,
            "total_count": len(unique_files),
            "returned_count": len(page),
            "offset": offset,
            "limit": limit,
            "truncated": offset + limit < len(unique_files) or truncated or bool(stats["scan_truncated"]),
            "next_offset": offset + len(page) if offset + limit < len(unique_files) or truncated or bool(stats["scan_truncated"]) else None,
            "files": page,
            "stats": stats,
        }

    if output_mode == "count":
        ordered_counts = dict(sorted(counts.items()))
        page_items = list(ordered_counts.items())[offset : offset + limit]
        return {
            "target": "content",
            "pattern": pattern,
            "file_glob": file_glob,
            "output_mode": output_mode,
            "total_count": sum(ordered_counts.values()),
            "returned_count": len(page_items),
            "offset": offset,
            "limit": limit,
            "truncated": offset + limit < len(ordered_counts) or truncated or bool(stats["scan_truncated"]),
            "next_offset": offset + len(page_items) if offset + limit < len(ordered_counts) or truncated or bool(stats["scan_truncated"]) else None,
            "counts": dict(page_items),
            "stats": stats,
        }

    return {
        "target": "content",
        "pattern": pattern,
        "file_glob": file_glob,
        "output_mode": output_mode,
        "total_count": total_count,
        "returned_count": len(matches),
        "offset": offset,
        "limit": limit,
        "truncated": truncated or total_count > offset + len(matches) or bool(stats["scan_truncated"]),
        "next_offset": offset + len(matches) if truncated or total_count > offset + len(matches) or bool(stats["scan_truncated"]) else None,
        "matches": matches,
        "stats": stats,
    }


def _resolve_roots(*, path_service: Any, thread_id: str, path: str) -> tuple[SearchRoot, ...]:
    normalized = (path or "/mnt/user-data/workspace").strip()
    if normalized == "/mnt/user-data":
        roots: list[SearchRoot] = []
        for suffix in ("workspace", "uploads", "outputs"):
            virtual = f"/mnt/user-data/{suffix}"
            host = path_service.resolve_virtual_path(thread_id, virtual)
            if host.exists():
                roots.append(SearchRoot(virtual_path=virtual, host_path=host))
        return tuple(roots)
    host_path = path_service.resolve_virtual_path(thread_id, normalized)
    return (SearchRoot(virtual_path=normalized.rstrip("/") or normalized, host_path=host_path),) if host_path.exists() else ()


def _iter_files(
    root: Path,
    *,
    include_hidden: bool,
    stats: dict[str, object],
    max_scanned_files: int,
    max_scanned_dirs: int,
) -> Iterable[Path]:
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        if int(stats["dirs_scanned"]) >= max_scanned_dirs:
            stats["scan_truncated"] = True
            return
        stats["dirs_scanned"] = int(stats["dirs_scanned"]) + 1
        current_path = Path(current)
        kept_dirs: list[str] = []
        for dir_name in dir_names:
            if _skip_name(dir_name, include_hidden=include_hidden):
                stats["ignored_dirs"] = int(stats.get("ignored_dirs", 0)) + 1
                continue
            kept_dirs.append(dir_name)
        dir_names[:] = sorted(kept_dirs, key=str.lower)
        for file_name in sorted(file_names):
            if _skip_name(file_name, include_hidden=include_hidden):
                continue
            if not _record_scanned_file(stats):
                return
            yield current_path / file_name


def _search_stats(*, max_scanned_files: int, max_scanned_dirs: int) -> dict[str, object]:
    return {
        "ignored_dirs": 0,
        "unreadable_dirs": 0,
        "files_scanned": 0,
        "dirs_scanned": 0,
        "scan_truncated": False,
        "max_scanned_files": max_scanned_files,
        "max_scanned_dirs": max_scanned_dirs,
    }


def _record_scanned_file(stats: dict[str, object]) -> bool:
    if int(stats["files_scanned"]) >= int(stats["max_scanned_files"]):
        stats["scan_truncated"] = True
        return False
    stats["files_scanned"] = int(stats["files_scanned"]) + 1
    return True


def _skip_name(name: str, *, include_hidden: bool) -> bool:
    if name in DEFAULT_IGNORED_DIRS:
        return True
    return not include_hidden and name.startswith(".")


def _relative_for_root(file_path: Path, root: SearchRoot) -> str:
    try:
        return file_path.relative_to(root.host_path).as_posix()
    except ValueError:
        return file_path.name


def _path_matches(pattern: str, name: str, relative_path: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/")
    normalized_relative = relative_path.replace("\\", "/")
    has_glob = any(char in normalized_pattern for char in "*?[]")
    if "/" in normalized_pattern or normalized_pattern.startswith("**"):
        return fnmatch.fnmatch(normalized_relative, normalized_pattern) or PurePosixPath(normalized_relative).match(normalized_pattern)
    if has_glob:
        return fnmatch.fnmatch(name, normalized_pattern) or fnmatch.fnmatch(normalized_relative, normalized_pattern)
    return normalized_pattern.lower() in name.lower() or normalized_pattern.lower() in normalized_relative.lower()


def _compile_matcher(pattern: str, *, literal: bool, case_sensitive: bool):
    flags = 0 if case_sensitive else re.IGNORECASE
    if literal:
        needle = pattern if case_sensitive else pattern.lower()

        def _literal_match(line: str) -> bool:
            haystack = line if case_sensitive else line.lower()
            return needle in haystack

        return _literal_match
    compiled = re.compile(pattern, flags)
    return lambda line: compiled.search(line) is not None


def _is_probably_text_file(file_path: Path, *, max_file_bytes: int, stats: dict[str, int]) -> bool:
    try:
        if file_path.stat().st_size > max_file_bytes:
            stats["large_files_skipped"] += 1
            return False
        sample = file_path.read_bytes()[:4096]
    except Exception:  # noqa: BLE001
        stats["unreadable_files"] += 1
        return False
    if b"\x00" in sample:
        stats["binary_files_skipped"] += 1
        return False
    return True


def _normalize_target(target: str) -> str:
    aliases = {
        "grep": "content",
        "content": "content",
        "file": "files",
        "files": "files",
        "find": "files",
        "glob": "files",
        "path": "files",
        "paths": "files",
    }
    return aliases.get(str(target or "content").strip().lower(), "content")


def _normalize_output_mode(output_mode: str) -> str:
    normalized = str(output_mode or "content").strip().lower()
    return normalized if normalized in {"content", "files_only", "count"} else "content"


def _bounded_int(value: int | str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _truncate_line(value: str) -> str:
    collapsed = value.rstrip("\n\r")
    if len(collapsed) <= MAX_LINE_CHARS:
        return collapsed
    return f"{collapsed[:MAX_LINE_CHARS]}...[truncated]"


def _error_payload(message: str, *, target: str, path: str) -> dict[str, object]:
    return {
        "target": target,
        "path": path,
        "total_count": 0,
        "returned_count": 0,
        "error": message,
    }
