from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Lock
from typing import Any

from ..compiler import normalize_memory_for_compiled_storage
from ..contracts import Memory, MemoryVersionRecord, utc_now
from .backend import MemoryNotFoundError, StorageError, VersionConflictError

_PAYLOAD_PATTERN = re.compile(r"## HCMS Payload\s*```json\s*(.*?)\s*```", re.DOTALL)


class FileSystemMemoryBackend:
    """Human-readable Markdown storage for HCMS memory entries."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path).expanduser().resolve()
        self.memories_path = self.base_path / "memories"
        self.versions_path = self.base_path / "versions"
        self.memories_path.mkdir(parents=True, exist_ok=True)
        self.versions_path.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def save_memory(self, namespace: str, memory: Memory, *, expected_version: int | None = None) -> Memory:
        namespace = _normalize_namespace(namespace)
        normalized = normalize_memory_for_compiled_storage(namespace, memory)
        existing = self._find_memory_path(namespace, normalized.memory_id)
        if existing is not None and expected_version is not None:
            current = self._read_memory(existing)
            if current.version != expected_version:
                raise VersionConflictError(
                    f"Memory {normalized.memory_id} expected version {expected_version}, found {current.version}."
                )
        stored = normalized.model_copy(
            deep=True,
            update={"metadata": {**normalized.metadata, "namespace": namespace}},
        )
        path = existing or self._path_for_memory(namespace, stored)
        self._write_text_atomic(path, self.render_memory(stored))
        return stored

    def get_memory(self, namespace: str, memory_id: str) -> Memory:
        path = self._find_memory_path(_normalize_namespace(namespace), memory_id)
        if path is None:
            raise MemoryNotFoundError(memory_id)
        return self._read_memory(path)

    def list_memories(self, namespace: str) -> tuple[Memory, ...]:
        namespace = _normalize_namespace(namespace)
        memories: list[Memory] = []
        for path in sorted(self.memories_path.glob("*/*.md")):
            try:
                memory = self._read_memory(path)
            except StorageError:
                continue
            if _normalize_namespace(memory.metadata.get("namespace")) == namespace:
                memories.append(memory)
        return tuple(memories)

    def delete_memory(self, namespace: str, memory_id: str) -> None:
        path = self._find_memory_path(_normalize_namespace(namespace), memory_id)
        if path is None:
            raise MemoryNotFoundError(memory_id)
        with self._lock:
            path.unlink(missing_ok=True)

    def search_memories(self, namespace: str, query: str, *, limit: int = 20) -> tuple[Memory, ...]:
        terms = tuple(term.lower() for term in re.findall(r"[\w.-]{2,}", str(query or "")))
        scored: list[tuple[int, Memory]] = []
        for memory in self.list_memories(namespace):
            haystack = f"{memory.summary}\n{memory.content}\n{' '.join(memory.tags)}".lower()
            score = sum(haystack.count(term) for term in terms) if terms else 1
            if score:
                scored.append((score, memory))
        scored.sort(key=lambda item: (item[0], item[1].confidence, item[1].updated_at), reverse=True)
        return tuple(memory for _, memory in scored[: max(1, limit)])

    def append_version(self, namespace: str, record: MemoryVersionRecord) -> None:
        path = self._version_path(_normalize_namespace(namespace), record.memory_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(record.model_dump_json() + "\n")

    def history(self, namespace: str, memory_id: str) -> tuple[MemoryVersionRecord, ...]:
        path = self._version_path(_normalize_namespace(namespace), memory_id)
        if not path.exists():
            return ()
        records: list[MemoryVersionRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(MemoryVersionRecord.model_validate_json(line))
            except Exception:
                continue
        records.sort(key=lambda item: (item.version, item.created_at))
        return tuple(records)

    def render_memory(self, memory: Memory) -> str:
        payload = json.dumps(memory.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        frontmatter = {
            "hcms_schema": "memory.v1",
            "memory_id": memory.memory_id,
            "version": memory.version,
            "parent_id": memory.parent_id,
            "category": memory.category.value,
            "state": memory.state.value,
            "confidence": memory.confidence,
            "salience": memory.salience,
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
            "tags": memory.tags,
            "entities": memory.entities,
            "concepts": memory.concepts,
            "source_thread_id": memory.source_thread_id,
            "source_agent": memory.source_agent,
            "source_type": memory.source_type.value,
            "access_count": memory.access_count,
            "reasoning": memory.reasoning,
        }
        lines = ["---"]
        for key, value in frontmatter.items():
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        lines.extend(
            [
                "---",
                "",
                f"# {memory.summary or memory.memory_id}",
                "",
                memory.content.strip(),
                "",
                "## Evidence",
            ]
        )
        if memory.evidence:
            for evidence in memory.evidence:
                lines.append(f"- {evidence.type.value} ({evidence.weight:.3f}): {evidence.content}")
        else:
            lines.append("- none")
        lines.extend(["", "## Relations"])
        if memory.relations:
            for relation in memory.relations:
                lines.append(
                    f"- {relation.relation_type.value}: {relation.source_memory_id} -> {relation.target_memory_id}"
                )
        else:
            lines.append("- none")
        lines.extend(["", "## HCMS Payload", "```json", payload, "```", ""])
        return "\n".join(lines)

    def _read_memory(self, path: Path) -> Memory:
        try:
            text = path.read_text(encoding="utf-8")
            match = _PAYLOAD_PATTERN.search(text)
            if not match:
                raise StorageError(f"Missing HCMS payload in {path}.")
            return Memory.model_validate_json(match.group(1))
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Could not read memory markdown {path}: {exc}") from exc

    def _path_for_memory(self, namespace: str, memory: Memory) -> Path:
        month = memory.created_at.strftime("%Y-%m")
        safe_id = _safe_name(memory.memory_id)
        return self.memories_path / month / f"{safe_id}.md"

    def _find_memory_path(self, namespace: str, memory_id: str) -> Path | None:
        safe_id = _safe_name(memory_id)
        for path in self.memories_path.glob(f"*/{safe_id}.md"):
            try:
                memory = self._read_memory(path)
            except StorageError:
                continue
            if _normalize_namespace(memory.metadata.get("namespace")) == namespace:
                return path
        return None

    def _version_path(self, namespace: str, memory_id: str) -> Path:
        return self.versions_path / _safe_name(namespace) / f"{_safe_name(memory_id)}.jsonl"

    def _write_text_atomic(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with self._lock:
            tmp_path.write_text(text, encoding="utf-8")
            try:
                tmp_path.replace(path)
            except PermissionError:
                path.write_text(tmp_path.read_text(encoding="utf-8"), encoding="utf-8")
                try:
                    tmp_path.unlink(missing_ok=True)
                except PermissionError:
                    pass


def _normalize_namespace(value: Any) -> str:
    normalized = str(value or "global/default").strip().strip("/\\")
    return normalized or "global/default"


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", str(value or "default")).strip("._") or "default"
