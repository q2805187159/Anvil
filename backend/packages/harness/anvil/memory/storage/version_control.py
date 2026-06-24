from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

from ..compiler import compile_manual_memory_content
from ..contracts import Memory, MemoryVersionRecord, stable_id, utc_now
from .backend import StorageBackend


@dataclass(frozen=True)
class MemoryDiff:
    memory_id: str
    from_version: int
    to_version: int
    content_diff: str
    confidence_delta: float
    evidence_added: tuple[str, ...] = ()
    evidence_removed: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryMergeResult:
    """Result of a deterministic three-way memory merge."""

    memory_id: str
    base_version: int
    left_version: int
    right_version: int
    merged_content: str
    conflicts: tuple[str, ...] = ()
    merged_memory: Memory | None = None

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)

    @property
    def success(self) -> bool:
        return not self.conflicts


class MemoryVersionControl:
    """Git-like version history helper for HCMS memories."""

    def __init__(self, backend: StorageBackend, *, namespace: str = "global/default") -> None:
        self.backend = backend
        self.namespace = namespace

    def create_version(self, memory_id: str, *, reason: str = "update", **changes: Any) -> Memory:
        updater = getattr(self.backend, "update_memory", None)
        if updater is None:
            current = self.backend.get_memory(self.namespace, memory_id)
            parent_id = f"{current.memory_id}@v{current.version}"
            updated = current.model_copy(
                deep=True,
                update={
                    **changes,
                    "version": current.version + 1,
                    "parent_id": parent_id,
                    "supersedes": [*current.supersedes, parent_id],
                    "updated_at": utc_now(),
                },
            )
            stored = self.backend.save_memory(self.namespace, updated, expected_version=current.version)
            self.backend.append_version(
                self.namespace,
                MemoryVersionRecord(
                    memory_id=stored.memory_id,
                    version=stored.version,
                    parent_id=stored.parent_id,
                    content=stored.content,
                    summary=stored.summary,
                    diff=_diff(current.content, stored.content, current.version, stored.version, stored.memory_id),
                    reason=reason,
                    metadata=stored.version_metadata(),
                ),
            )
            return stored
        return updater(self.namespace, memory_id, reason=reason, **changes)

    def history(self, memory_id: str) -> tuple[MemoryVersionRecord, ...]:
        return self.backend.history(self.namespace, memory_id)

    def diff_versions(self, memory_id: str, from_version: int, to_version: int) -> MemoryDiff:
        by_version = {record.version: record for record in self.history(memory_id)}
        source = by_version[from_version]
        target = by_version[to_version]
        source_evidence = _evidence_ids(source)
        target_evidence = _evidence_ids(target)
        return MemoryDiff(
            memory_id=memory_id,
            from_version=from_version,
            to_version=to_version,
            content_diff=_diff(source.content, target.content, from_version, to_version, memory_id),
            confidence_delta=round(_confidence(target) - _confidence(source), 4),
            evidence_added=tuple(sorted(target_evidence - source_evidence)),
            evidence_removed=tuple(sorted(source_evidence - target_evidence)),
        )

    def latest_diff(self, memory_id: str) -> str:
        records = self.history(memory_id)
        return records[-1].diff if records else ""

    def merge_versions(
        self,
        memory_id: str,
        *,
        base_version: int,
        left_version: int,
        right_version: int,
        reason: str = "three_way_merge",
    ) -> MemoryMergeResult:
        by_version = {record.version: record for record in self.history(memory_id)}
        base = by_version[base_version]
        left = by_version[left_version]
        right = by_version[right_version]
        result = three_way_merge_content(
            memory_id=memory_id,
            base_version=base_version,
            left_version=left_version,
            right_version=right_version,
            base_content=_merge_body(base.content),
            left_content=_merge_body(left.content),
            right_content=_merge_body(right.content),
        )
        if not result.success:
            return result

        current = self.backend.get_memory(self.namespace, memory_id)
        next_version = max(by_version) + 1
        parent_id = f"{current.memory_id}@v{current.version}"
        supersedes = tuple(
            dict.fromkeys(
                [
                    *current.supersedes,
                    parent_id,
                    f"{memory_id}@v{left_version}",
                    f"{memory_id}@v{right_version}",
                ]
            )
        )
        source_thread_id = current.source_thread_id or "manual"
        observation_id = str(
            current.metadata.get("observation_id")
            or stable_id("obs", source_thread_id, self.namespace, result.merged_content, size=16)
        )
        merged_content = compile_manual_memory_content(
            result.merged_content,
            memory_id=current.memory_id,
            category=current.category,
            confidence=current.confidence,
            created_at=current.created_at,
            source_thread_id=source_thread_id,
            observation_id=observation_id,
            evidence=current.evidence,
        )
        updated = current.model_copy(
            deep=True,
            update={
                "content": merged_content,
                "summary": result.merged_content[:120],
                "version": next_version,
                "parent_id": parent_id,
                "supersedes": list(supersedes),
                "updated_at": utc_now(),
                "metadata": {**current.metadata, "observation_id": observation_id},
            },
        )
        merged = self.backend.save_memory(self.namespace, updated, expected_version=current.version)
        self.backend.append_version(
            self.namespace,
            MemoryVersionRecord(
                memory_id=merged.memory_id,
                version=merged.version,
                parent_id=merged.parent_id,
                content=merged.content,
                summary=merged.summary,
                diff=_diff(current.content, merged.content, current.version, merged.version, memory_id),
                reason=reason,
                metadata=merged.version_metadata(),
            ),
        )
        return MemoryMergeResult(
            memory_id=memory_id,
            base_version=base_version,
            left_version=left_version,
            right_version=right_version,
            merged_content=result.merged_content,
            merged_memory=merged,
            conflicts=(),
        )


def three_way_merge_content(
    *,
    memory_id: str,
    base_version: int,
    left_version: int,
    right_version: int,
    base_content: str,
    left_content: str,
    right_content: str,
) -> MemoryMergeResult:
    """Deterministically merge two derived memory versions against a base.

    This is intentionally conservative: same-line divergent edits return
    conflicts instead of inventing content.
    """
    base_lines = base_content.splitlines()
    left_lines = left_content.splitlines()
    right_lines = right_content.splitlines()
    max_len = max(len(base_lines), len(left_lines), len(right_lines))
    merged: list[str] = []
    conflicts: list[str] = []

    for index in range(max_len):
        base_line = base_lines[index] if index < len(base_lines) else ""
        left_line = left_lines[index] if index < len(left_lines) else ""
        right_line = right_lines[index] if index < len(right_lines) else ""

        if left_line == right_line:
            merged.append(left_line)
            continue
        if left_line == base_line:
            merged.append(right_line)
            continue
        if right_line == base_line:
            merged.append(left_line)
            continue

        conflicts.append(
            f"line {index + 1}: base={base_line!r} left={left_line!r} right={right_line!r}"
        )

    return MemoryMergeResult(
        memory_id=memory_id,
        base_version=base_version,
        left_version=left_version,
        right_version=right_version,
        merged_content="\n".join(merged).rstrip("\n"),
        conflicts=tuple(conflicts),
    )


def _diff(before: str, after: str, before_version: int, after_version: int, memory_id: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{memory_id}@v{before_version}",
            tofile=f"{memory_id}@v{after_version}",
            lineterm="",
        )
    )


def _confidence(record: MemoryVersionRecord) -> float:
    value = record.metadata.get("confidence")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _evidence_ids(record: MemoryVersionRecord) -> set[str]:
    value = record.metadata.get("evidence_ids")
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if str(item or "").strip()}


def _merge_body(content: str) -> str:
    text = str(content or "")
    if not text.startswith("---"):
        return text
    lines = text.splitlines()
    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return text
    body = "\n".join(lines[end_index + 1 :]).strip()
    sections = ("## Evidence", "## Relations", "## Metadata")
    cut = len(body)
    for section in sections:
        position = body.find(section)
        if position >= 0:
            cut = min(cut, position)
    body = body[:cut].strip()
    if body.startswith("#"):
        body_lines = body.splitlines()
        title = body_lines[0].lstrip("#").strip()
        rest = "\n".join(body_lines[1:]).strip()
        return rest or title
    return body
