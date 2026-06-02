from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
from typing import Any


SUPPORTED_PATCH_ACTIONS = {
    "insert_before_anchor",
    "insert_after_anchor",
    "replace_text",
    "delete_text",
    "insert_before_line",
    "insert_after_line",
    "replace_lines",
    "delete_lines",
}


@dataclass(frozen=True)
class PatchApplicationResult:
    operations_applied: int
    line_count: int
    byte_length: int
    changed: bool
    diff: str | None = None


def apply_patch_operations(host_path: Path, operations: list[dict[str, Any]], *, dry_run: bool = False) -> PatchApplicationResult:
    if not host_path.exists():
        raise ValueError(f"patch target does not exist: {host_path}")
    if not host_path.is_file():
        raise ValueError(f"patch target is not a file: {host_path}")
    if not operations:
        raise ValueError("patch operations must not be empty")

    original_text = host_path.read_text(encoding="utf-8")
    text = original_text
    for index, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict):
            raise ValueError(f"invalid patch operation at index {index}: expected an object")
        text = _apply_single_operation(text, operation=operation, index=index)

    if not dry_run:
        host_path.write_text(text, encoding="utf-8")
    return PatchApplicationResult(
        operations_applied=len(operations),
        line_count=_line_count(text),
        byte_length=len(text.encode("utf-8")),
        changed=text != original_text,
        diff=_unified_diff(original_text, text, path=host_path),
    )


def _apply_single_operation(text: str, *, operation: dict[str, Any], index: int) -> str:
    action = str(operation.get("action", "")).strip()
    if not action:
        raise ValueError(f"invalid patch operation at index {index}: missing action")
    if action not in SUPPORTED_PATCH_ACTIONS:
        raise ValueError(f"unsupported patch action: {action}")

    if action in {"insert_before_anchor", "insert_after_anchor"}:
        anchor = _require_non_empty_string(operation, "anchor", index=index)
        content = _require_string(operation, "content", index=index)
        start, end = _find_unique_substring(text, anchor, label="anchor")
        insert_at = start if action == "insert_before_anchor" else end
        return text[:insert_at] + content + text[insert_at:]

    if action in {"replace_text", "delete_text"}:
        target_text = _require_non_empty_string(operation, "text", index=index)
        content = _require_string(operation, "content", index=index) if action == "replace_text" else ""
        start, end = _find_unique_substring(text, target_text, label="text")
        _verify_expected_old_text(target_text, operation.get("expected_old_text"))
        return text[:start] + content + text[end:]

    if action in {"insert_before_line", "insert_after_line"}:
        line = _require_positive_int(operation, "line", index=index)
        content = _require_string(operation, "content", index=index)
        insert_at = _line_insertion_offset(text, line=line, after=action == "insert_after_line")
        return text[:insert_at] + content + text[insert_at:]

    start_line = _require_positive_int(operation, "start_line", index=index)
    end_line = _require_positive_int(operation, "end_line", index=index)
    start, end, selected_text = _line_range_span(text, start_line=start_line, end_line=end_line)
    _verify_expected_old_text(selected_text, operation.get("expected_old_text"))
    if action == "replace_lines":
        content = _require_string(operation, "content", index=index)
        return text[:start] + content + text[end:]
    return text[:start] + text[end:]


def _verify_expected_old_text(actual_text: str, expected_old_text: Any) -> None:
    if expected_old_text is None:
        return
    if not isinstance(expected_old_text, str):
        raise ValueError("invalid patch operation: expected_old_text must be a string when provided")
    if actual_text != expected_old_text:
        raise ValueError("expected old text mismatch")


def _find_unique_substring(text: str, target: str, *, label: str) -> tuple[int, int]:
    start = text.find(target)
    if start == -1:
        raise ValueError(f"{label} not found")
    second = text.find(target, start + 1)
    if second != -1:
        raise ValueError(f"{label} matched multiple locations")
    return start, start + len(target)


def _line_insertion_offset(text: str, *, line: int, after: bool) -> int:
    lines = text.splitlines(keepends=True)
    if not lines:
        if line != 1:
            raise ValueError(f"line number out of bounds: {line} (total lines: 0)")
        return 0

    total_lines = len(lines)
    if line < 1 or line > total_lines + (0 if after else 1):
        raise ValueError(f"line number out of bounds: {line} (total lines: {total_lines})")

    offsets = _line_offsets(lines)
    if after:
        if line == total_lines:
            return len(text)
        return offsets[line]
    if line == total_lines + 1:
        return len(text)
    return offsets[line - 1]


def _line_range_span(text: str, *, start_line: int, end_line: int) -> tuple[int, int, str]:
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        raise ValueError("line range out of bounds: file is empty")
    if start_line > end_line:
        raise ValueError(f"line range out of bounds: start_line {start_line} is greater than end_line {end_line}")
    if start_line < 1 or end_line > total_lines:
        raise ValueError(f"line range out of bounds: {start_line}-{end_line} (total lines: {total_lines})")

    offsets = _line_offsets(lines)
    start = offsets[start_line - 1]
    end = offsets[end_line] if end_line < total_lines else len(text)
    return start, end, text[start:end]


def _line_offsets(lines: list[str]) -> list[int]:
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    return offsets


def _line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _unified_diff(original_text: str, new_text: str, *, path: Path) -> str | None:
    if original_text == new_text:
        return None
    return "\n".join(
        difflib.unified_diff(
            original_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"{path.as_posix()} (before)",
            tofile=f"{path.as_posix()} (after)",
            lineterm="",
        )
    ) + "\n"


def _require_non_empty_string(operation: dict[str, Any], key: str, *, index: int) -> str:
    value = operation.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid patch operation at index {index}: {key} must be a non-empty string")
    return value


def _require_string(operation: dict[str, Any], key: str, *, index: int) -> str:
    value = operation.get(key)
    if not isinstance(value, str):
        raise ValueError(f"invalid patch operation at index {index}: {key} must be a string")
    return value


def _require_positive_int(operation: dict[str, Any], key: str, *, index: int) -> int:
    value = operation.get(key)
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"invalid patch operation at index {index}: {key} must be a positive integer")
    return value
