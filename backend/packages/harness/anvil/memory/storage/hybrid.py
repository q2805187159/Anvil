from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..compiler import compile_manual_memory_content
from ..contracts import (
    Evidence,
    EvidenceType,
    Memory,
    MemoryCategory,
    MemoryLifecycleState,
    MemoryState,
    MemoryVersionRecord,
    stable_id,
    utc_now,
)
from .backend import MemoryNotFoundError, StorageError
from .filesystem import FileSystemMemoryBackend
from .sqlite import SQLiteMemoryIndex


@dataclass(frozen=True)
class MemoryBackupManifest:
    """Metadata returned after exporting or restoring an HCMS namespace backup."""

    namespace: str
    backup_id: str
    memory_count: int
    version_count: int
    path: Path
    created_at: str


class HybridMemoryBackend:
    """Markdown filesystem store plus SQLite index."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path).expanduser().resolve()
        self.filesystem = FileSystemMemoryBackend(self.base_path)
        self.index = SQLiteMemoryIndex(self.base_path / "index.sqlite3")
        self.states_path = self.base_path / "states"
        self.states_path.mkdir(parents=True, exist_ok=True)

    def save_memory(self, namespace: str, memory: Memory, *, expected_version: int | None = None) -> Memory:
        stored = self.filesystem.save_memory(namespace, memory, expected_version=expected_version)
        path = self.filesystem._find_memory_path(namespace, stored.memory_id)  # noqa: SLF001 - index stores durable path.
        self.index.upsert(namespace, stored, markdown_path=str(path or ""))
        if not self.filesystem.history(namespace, stored.memory_id):
            self.append_version(
                namespace,
                MemoryVersionRecord(
                    memory_id=stored.memory_id,
                    version=stored.version,
                    parent_id=stored.parent_id,
                    content=stored.content,
                    summary=stored.summary,
                    diff=stored.content,
                    reason="create",
                    metadata=stored.version_metadata(),
                ),
            )
        return stored

    def get_memory(self, namespace: str, memory_id: str) -> Memory:
        return self.filesystem.get_memory(namespace, memory_id)

    def list_memories(self, namespace: str) -> tuple[Memory, ...]:
        return self.filesystem.list_memories(namespace)

    def search_memories(self, namespace: str, query: str, *, limit: int = 20) -> tuple[Memory, ...]:
        ids = self.index.search_ids(namespace, query, limit=limit)
        memories: list[Memory] = []
        for memory_id in ids:
            try:
                memories.append(self.get_memory(namespace, memory_id))
            except MemoryNotFoundError:
                continue
        if not memories:
            return self.filesystem.search_memories(namespace, query, limit=limit)
        return tuple(memories)

    def update_memory(self, namespace: str, memory_id: str, **changes: Any) -> Memory:
        previous = self.get_memory(namespace, memory_id)
        allowed = {
            "content",
            "summary",
            "category",
            "confidence",
            "salience",
            "tags",
            "entities",
            "concepts",
            "evidence",
            "reasoning",
            "forget_after",
            "metadata",
        }
        payload = {key: value for key, value in changes.items() if key in allowed}
        if "category" in payload and not isinstance(payload["category"], MemoryCategory):
            payload["category"] = _category(payload["category"])
        if "content" in payload and "summary" not in payload:
            payload["summary"] = str(payload["content"])[:120]
        if "content" in payload:
            content = str(payload["content"])
            category = payload.get("category", previous.category)
            confidence = float(payload.get("confidence", previous.confidence))
            evidence = tuple(payload.get("evidence", previous.evidence))
            metadata = dict(payload.get("metadata", previous.metadata))
            source_thread_id = previous.source_thread_id or "manual"
            observation_id = str(metadata.get("observation_id") or stable_id("obs", source_thread_id, namespace, content, size=16))
            payload["content"] = compile_manual_memory_content(
                content,
                memory_id=previous.memory_id,
                category=category,
                confidence=confidence,
                created_at=previous.created_at,
                source_thread_id=source_thread_id,
                observation_id=observation_id,
                evidence=evidence,
            )
            payload["concepts"] = list(_tokenize(content))[:12]
            payload["metadata"] = {**metadata, "observation_id": observation_id}
        payload.update(
            {
                "version": previous.version + 1,
                "parent_id": f"{previous.memory_id}@v{previous.version}",
                "supersedes": [*previous.supersedes, f"{previous.memory_id}@v{previous.version}"],
                "updated_at": utc_now(),
            }
        )
        updated = previous.model_copy(deep=True, update=payload)
        diff = "\n".join(
            difflib.unified_diff(
                previous.content.splitlines(),
                updated.content.splitlines(),
                fromfile=f"{previous.memory_id}@v{previous.version}",
                tofile=f"{updated.memory_id}@v{updated.version}",
                lineterm="",
            )
        )
        stored = self.save_memory(namespace, updated, expected_version=previous.version)
        self.append_version(
            namespace,
            MemoryVersionRecord(
                memory_id=stored.memory_id,
                version=stored.version,
                parent_id=stored.parent_id,
                content=stored.content,
                summary=stored.summary,
                diff=diff,
                reason=str(changes.get("reason") or "update"),
                metadata=stored.version_metadata(),
            ),
        )
        return stored

    def archive_memory(self, namespace: str, memory_id: str) -> Memory:
        return self._set_state(namespace, memory_id, MemoryLifecycleState.ARCHIVED, "archive")

    def restore_memory(self, namespace: str, memory_id: str) -> Memory:
        return self._set_state(namespace, memory_id, MemoryLifecycleState.ACTIVE, "restore")

    def forget_memory(self, namespace: str, memory_id: str) -> Memory:
        return self._set_state(namespace, memory_id, MemoryLifecycleState.FORGOTTEN, "forget")

    def delete_memory(self, namespace: str, memory_id: str) -> None:
        self.filesystem.delete_memory(namespace, memory_id)
        self.index.delete(namespace, memory_id)

    def append_version(self, namespace: str, record: MemoryVersionRecord) -> None:
        self.filesystem.append_version(namespace, record)

    def history(self, namespace: str, memory_id: str) -> tuple[MemoryVersionRecord, ...]:
        return self.filesystem.history(namespace, memory_id)

    def export_namespace_backup(self, namespace: str, destination: str | Path, *, since: object | None = None) -> Path:
        namespace = str(namespace or "global/default")
        destination_path = Path(destination).expanduser().resolve()
        destination_path.mkdir(parents=True, exist_ok=True)
        state = self.load(namespace)
        since_dt = _coerce_backup_since(since)
        memories = [
            memory
            for memory in state.memories
            if since_dt is None or memory.updated_at > since_dt or memory.created_at > since_dt
        ]
        memory_ids = {memory.memory_id for memory in memories}
        version_records = [
            record
            for record in state.versions
            if since_dt is None or record.memory_id in memory_ids or record.created_at > since_dt
        ]
        exported_state = state.model_copy(
            deep=True,
            update={
                "memories": memories,
                "versions": version_records,
                "relations": [
                    relation
                    for relation in state.relations
                    if relation.source_memory_id in memory_ids and relation.target_memory_id in memory_ids
                ],
                "causal_edges": [
                    edge
                    for edge in state.causal_edges
                    if edge.source_event in memory_ids and edge.target_event in memory_ids
                ],
            },
        )
        timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        backup_id = f"hcms-backup-{_safe_backup_name(namespace)}-{timestamp}"
        payload = {
            "manifest": {
                "hcms_schema": "namespace-backup.v1",
                "namespace": namespace,
                "backup_id": backup_id,
                "created_at": utc_now().isoformat(),
                "incremental": since_dt is not None,
                "since": since_dt.isoformat() if since_dt is not None else None,
                "memory_count": len(memories),
                "version_count": len(version_records),
            },
            "memories": [memory.model_dump(mode="json") for memory in memories],
            "versions": [record.model_dump(mode="json") for record in version_records],
            "state": exported_state.model_dump(mode="json"),
        }
        backup_path = destination_path / f"{backup_id}.json"
        tmp_path = backup_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        try:
            tmp_path.replace(backup_path)
        except PermissionError:
            backup_path.write_text(tmp_path.read_text(encoding="utf-8"), encoding="utf-8")
            tmp_path.unlink(missing_ok=True)
        return backup_path

    def restore_namespace_backup(self, backup_path: str | Path, *, namespace: str | None = None) -> MemoryBackupManifest:
        path = Path(backup_path).expanduser().resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise StorageError(f"Missing HCMS backup object in {path}.")
            manifest = payload.get("manifest")
            if not isinstance(manifest, dict):
                raise StorageError(f"Missing HCMS backup manifest in {path}.")
            memories_payload = payload.get("memories")
            versions_payload = payload.get("versions")
            if not isinstance(memories_payload, list) or not isinstance(versions_payload, list):
                raise StorageError(f"Invalid HCMS backup payload lists in {path}.")
            backup_namespace = str(manifest.get("namespace") or namespace or "global/default")
            target_namespace = str(namespace or backup_namespace)
            memories = [Memory.model_validate(item) for item in memories_payload]
            versions = [MemoryVersionRecord.model_validate(item) for item in versions_payload]
            state_payload = payload.get("state")
            restored_state = None
            if state_payload is not None:
                if not isinstance(state_payload, dict):
                    raise StorageError(f"Invalid HCMS backup state in {path}.")
                restored_state = MemoryState.model_validate(state_payload)
        except StorageError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise StorageError(f"Could not restore HCMS backup {path}: {exc}") from exc

        restored_memories: list[Memory] = []
        for memory in memories:
            restored = memory.model_copy(deep=True, update={"metadata": {**memory.metadata, "namespace": target_namespace}})
            self.save_memory(target_namespace, restored)
            restored_memories.append(restored)
        existing_versions: set[tuple[str, int]] = set()
        for memory in memories:
            for record in self.history(target_namespace, memory.memory_id):
                existing_versions.add((record.memory_id, record.version))
        for record in versions:
            key = (record.memory_id, record.version)
            if key in existing_versions:
                continue
            self.append_version(target_namespace, record)
            existing_versions.add(key)
        if restored_state is not None:
            self._save_state_sidecar(
                target_namespace,
                restored_state.model_copy(
                    deep=True,
                    update={
                        "namespace": target_namespace,
                        "memories": restored_memories,
                        "versions": versions,
                    },
                ),
            )

        return MemoryBackupManifest(
            namespace=target_namespace,
            backup_id=str(manifest.get("backup_id") or path.stem),
            memory_count=len(memories),
            version_count=len(versions),
            path=path,
            created_at=str(manifest.get("created_at") or ""),
        )

    def load(self, namespace: str) -> MemoryState:
        namespace = str(namespace or "global/default")
        state = self._load_state_sidecar(namespace)
        memories = list(self.list_memories(namespace))
        versions = [record for memory in memories for record in self.history(namespace, memory.memory_id)]
        if state is None:
            return MemoryState(namespace=namespace, memories=memories, versions=versions)
        return state.model_copy(
            deep=True,
            update={"namespace": namespace, "memories": memories, "versions": versions},
        )

    def save(self, namespace: str, memory_state: MemoryState) -> None:
        namespace = str(namespace or "global/default")
        current_ids = {memory.memory_id for memory in self.list_memories(namespace)}
        next_ids = {memory.memory_id for memory in memory_state.memories}
        stored_memories: list[Memory] = []
        for memory in memory_state.memories:
            stored_memories.append(self.save_memory(namespace, memory))
        for stale_id in current_ids - next_ids:
            self.delete_memory(namespace, stale_id)
        existing_versions = {
            (record.memory_id, record.version)
            for memory in stored_memories
            for record in self.history(namespace, memory.memory_id)
        }
        for record in memory_state.versions:
            key = (record.memory_id, record.version)
            if key in existing_versions:
                continue
            self.append_version(namespace, record)
            existing_versions.add(key)
        state = memory_state.model_copy(deep=True, update={"namespace": namespace, "memories": stored_memories})
        self._save_state_sidecar(namespace, state)

    def invalidate(self, namespace: str) -> None:
        return None

    def list_namespaces(self) -> list[str]:
        discovered: set[str] = set()
        for path in self.states_path.glob("*.json"):
            try:
                state = MemoryState.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            discovered.add(str(state.namespace or "global/default"))
        for path in self.filesystem.memories_path.glob("*/*.md"):
            try:
                memory = self.filesystem._read_memory(path)  # noqa: SLF001 - namespace discovery.
            except Exception:
                continue
            discovered.add(str(memory.metadata.get("namespace") or "global/default"))
        return sorted(discovered)

    def create_memory(
        self,
        namespace: str,
        *,
        content: str,
        category: str = "note",
        confidence: float = 0.5,
        salience: float = 0.5,
        summary: str = "",
    ) -> Memory:
        now = utc_now()
        category_value = _category(category)
        memory_id = stable_id("mem", namespace, category_value.value, content, size=12)
        source_thread_id = "manual"
        observation_id = stable_id("obs", source_thread_id, namespace, content, size=16)
        evidence = Evidence(
            evidence_id=stable_id("ev", memory_id, source_thread_id, content[:180], size=12),
            type=EvidenceType.USER_STATED,
            content=content[:180],
            weight=confidence,
            timestamp=now,
            source_id=source_thread_id,
        )
        compiled_content = compile_manual_memory_content(
            content,
            memory_id=memory_id,
            category=category_value,
            confidence=confidence,
            created_at=now,
            source_thread_id=source_thread_id,
            observation_id=observation_id,
            evidence=(evidence,),
        )
        memory = Memory(
            memory_id=memory_id,
            content=compiled_content,
            summary=summary or content[:120],
            category=category_value,
            confidence=confidence,
            salience=salience,
            evidence=[evidence],
            concepts=list(_tokenize(content))[:12],
            created_at=now,
            updated_at=now,
            accessed_at=now,
            source_thread_id=source_thread_id,
            metadata={"observation_id": observation_id},
        )
        return self.save_memory(namespace, memory)

    def _set_state(self, namespace: str, memory_id: str, state: MemoryLifecycleState, reason: str) -> Memory:
        previous = self.get_memory(namespace, memory_id)
        updated = previous.model_copy(
            deep=True,
            update={"state": state, "version": previous.version + 1, "updated_at": utc_now()},
        )
        stored = self.save_memory(namespace, updated, expected_version=previous.version)
        self.append_version(
            namespace,
            MemoryVersionRecord(
                memory_id=stored.memory_id,
                version=stored.version,
                parent_id=stored.parent_id,
                content=stored.content,
                summary=stored.summary,
                reason=reason,
                metadata=stored.version_metadata(),
            ),
        )
        return stored

    def _state_path(self, namespace: str) -> Path:
        return self.states_path / f"{_safe_backup_name(namespace)}.json"

    def _load_state_sidecar(self, namespace: str) -> MemoryState | None:
        path = self._state_path(namespace)
        if not path.exists():
            return None
        try:
            return MemoryState.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_state_sidecar(self, namespace: str, memory_state: MemoryState) -> None:
        state = memory_state.model_copy(deep=True, update={"namespace": namespace, "updated_at": utc_now()})
        path = self._state_path(namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        try:
            tmp_path.replace(path)
        except PermissionError:
            path.write_text(tmp_path.read_text(encoding="utf-8"), encoding="utf-8")
            tmp_path.unlink(missing_ok=True)


def _category(value: Any) -> MemoryCategory:
    try:
        return MemoryCategory(str(value or "note").strip().lower())
    except ValueError:
        return MemoryCategory.NOTE


def _safe_backup_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value).strip("-") or "default"


def _coerce_backup_since(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise StorageError(f"Invalid HCMS backup since timestamp: {text}") from exc


def _tokenize(text: str) -> tuple[str, ...]:
    import re

    return tuple(dict.fromkeys(term.lower() for term in re.findall(r"[\w\-/\.]{2,}", str(text or ""))))
