from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .compiler import compile_manual_memory_content
from .contracts import Evidence, EvidenceType, Memory, MemoryCategory, MemoryLifecycleState, MemoryState, SourceType, stable_id, utc_now
from .store import FileMemoryStore


class MemoryMigrationResult(BaseModel):
    """Batch migration report for legacy memory payload conversion."""

    model_config = ConfigDict(extra="forbid")

    source_system: str = "anvil"
    total_seen: int = 0
    migrated_count: int = 0
    error_count: int = 0
    memories: list[Memory] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


class MemoryMigrationValidationReport(BaseModel):
    """Validation summary for converted HCMS memories."""

    model_config = ConfigDict(extra="forbid")

    valid: bool = True
    memory_count: int = 0
    schema_valid_count: int = 0
    error_count: int = 0
    errors: list[dict[str, str]] = Field(default_factory=list)


class MemoryMigrationRunResult(BaseModel):
    """End-to-end source-file migration summary."""

    model_config = ConfigDict(extra="forbid")

    source_system: str
    source_path: str
    target_dir: str
    namespace: str = "global/default"
    total_seen: int = 0
    migrated_count: int = 0
    written_count: int = 0
    error_count: int = 0
    validation_valid: bool = True
    memory_ids: list[str] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


class DualWriteSaveResult(BaseModel):
    """Result of a migration-period dual-write save."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = False
    legacy_ok: bool = False
    hcms_ok: bool = False
    errors: list[dict[str, str]] = Field(default_factory=list)


class DualWriteRetrieveResult(BaseModel):
    """Result of a migration-period read with rollback fallback metadata."""

    model_config = ConfigDict(extra="forbid")

    source: str = "none"
    results: list[Any] = Field(default_factory=list)
    fallback_used: bool = False
    errors: list[dict[str, str]] = Field(default_factory=list)


class DualWriteMemoryService:
    """Migration bridge that writes old+HCMS and reads HCMS with legacy fallback.

    The bridge is intentionally protocol-based and synchronous so the reusable
    harness does not import any removed legacy memory runtime. Adapters can wrap
    concrete old clients outside the harness boundary.
    """

    def __init__(self, *, legacy_client: Any, hcms_client: Any) -> None:
        self.legacy_client = legacy_client
        self.hcms_client = hcms_client

    def save_memory(self, memory: Memory) -> DualWriteSaveResult:
        errors: list[dict[str, str]] = []
        legacy_ok = _try_save(self.legacy_client, memory, target="legacy", errors=errors)
        hcms_ok = _try_save(self.hcms_client, memory, target="hcms", errors=errors)
        return DualWriteSaveResult(
            ok=legacy_ok and hcms_ok,
            legacy_ok=legacy_ok,
            hcms_ok=hcms_ok,
            errors=errors,
        )

    def retrieve(self, query: str, *, limit: int = 10) -> DualWriteRetrieveResult:
        errors: list[dict[str, str]] = []
        try:
            results = _retrieve(self.hcms_client, query, limit=limit)
            return DualWriteRetrieveResult(source="hcms", results=list(results), fallback_used=False)
        except Exception as exc:
            errors.append({"target": "hcms", "error": str(exc)})
        try:
            results = _retrieve(self.legacy_client, query, limit=limit)
            return DualWriteRetrieveResult(source="legacy", results=list(results), fallback_used=True, errors=errors)
        except Exception as exc:
            errors.append({"target": "legacy", "error": str(exc)})
            return DualWriteRetrieveResult(source="none", results=[], fallback_used=True, errors=errors)


def migrate_legacy_anvil_payloads(
    *,
    entries: Iterable[dict[str, Any]] = (),
    profile_facets: Iterable[dict[str, Any]] = (),
    source_agent: str | None = None,
) -> MemoryMigrationResult:
    """Convert legacy Anvil memory entries and ProfileFacet-like payloads into HCMS memories.

    This is a pure harness conversion helper. It does not read app state, write
    storage, or keep legacy compatibility behavior alive.
    """

    memories: list[Memory] = []
    errors: list[dict[str, str]] = []
    total_seen = 0

    for payload in entries:
        total_seen += 1
        source_id = str(payload.get("entry_id") or payload.get("id") or f"entry:{total_seen}")
        try:
            memories.append(_convert_legacy_entry(payload, source_agent=source_agent))
        except Exception as exc:
            errors.append({"source_id": source_id, "error": str(exc)})

    for payload in profile_facets:
        total_seen += 1
        source_id = str(payload.get("facet_id") or payload.get("id") or f"facet:{total_seen}")
        try:
            memories.append(_convert_profile_facet(payload, source_agent=source_agent))
        except Exception as exc:
            errors.append({"source_id": source_id, "error": str(exc)})

    return MemoryMigrationResult(
        source_system="anvil",
        total_seen=total_seen,
        migrated_count=len(memories),
        error_count=len(errors),
        memories=memories,
        errors=errors,
    )


def migrate_agentmemory_payloads(
    payloads: Iterable[dict[str, Any]],
    *,
    source_agent: str | None = None,
) -> MemoryMigrationResult:
    """Convert AgentMemory-like memory payloads into HCMS memories."""

    memories: list[Memory] = []
    errors: list[dict[str, str]] = []
    total_seen = 0
    for payload in payloads:
        total_seen += 1
        source_id = str(payload.get("id") or payload.get("memory_id") or f"agentmemory:{total_seen}")
        try:
            memories.append(_convert_agentmemory_payload(payload, source_agent=source_agent))
        except Exception as exc:
            errors.append({"source_id": source_id, "error": str(exc)})
    return MemoryMigrationResult(
        source_system="agentmemory",
        total_seen=total_seen,
        migrated_count=len(memories),
        error_count=len(errors),
        memories=memories,
        errors=errors,
    )


def migrate_deerflow_payload(
    payload: dict[str, Any],
    *,
    source_agent: str | None = None,
) -> MemoryMigrationResult:
    """Convert DeerFlow flat user context and fact payloads into HCMS memories."""

    memories: list[Memory] = []
    errors: list[dict[str, str]] = []
    total_seen = 0
    user = payload.get("user") if isinstance(payload, dict) else None
    if isinstance(user, dict):
        for section, value in user.items():
            total_seen += 1
            source_id = f"user.{section}"
            try:
                memories.append(_convert_deerflow_context(section, value, source_agent=source_agent))
            except Exception as exc:
                errors.append({"source_id": source_id, "error": str(exc)})

    facts = payload.get("facts") if isinstance(payload, dict) else None
    if isinstance(facts, Iterable) and not isinstance(facts, (str, bytes, dict)):
        for fact in facts:
            total_seen += 1
            source_id = str(fact.get("id") if isinstance(fact, dict) else f"fact:{total_seen}")
            try:
                if not isinstance(fact, dict):
                    raise ValueError("DeerFlow fact must be a mapping")
                memories.append(_convert_deerflow_fact(fact, source_agent=source_agent))
            except Exception as exc:
                errors.append({"source_id": source_id, "error": str(exc)})
    return MemoryMigrationResult(
        source_system="deerflow",
        total_seen=total_seen,
        migrated_count=len(memories),
        error_count=len(errors),
        memories=memories,
        errors=errors,
    )


def validate_migration_result(result: MemoryMigrationResult) -> MemoryMigrationValidationReport:
    """Validate converted memories without persisting them."""

    from .compiler import KnowledgeCompiler

    errors = [dict(item) for item in result.errors]
    schema_valid_count = 0
    for memory in result.memories:
        validation = KnowledgeCompiler.validate_markdown_schema(memory.content)
        if validation.valid:
            schema_valid_count += 1
        else:
            errors.append({"source_id": memory.memory_id, "error": "; ".join(validation.errors)})
    return MemoryMigrationValidationReport(
        valid=not errors,
        memory_count=len(result.memories),
        schema_valid_count=schema_valid_count,
        error_count=len(errors),
        errors=errors,
    )


def run_memory_migration(
    *,
    source_system: str,
    source_path: str | Path,
    target_dir: str | Path,
    namespace: str = "global/default",
    source_agent: str | None = None,
    validate: bool = True,
    dry_run: bool = False,
) -> MemoryMigrationRunResult:
    """Read legacy memory source data, convert it to HCMS, and optionally persist it."""

    normalized_source = source_system.strip().lower().replace("-", "_")
    path = Path(source_path).expanduser().resolve()
    target = Path(target_dir).expanduser().resolve()
    result = _migrate_from_source(normalized_source, path, source_agent=source_agent)
    validation = validate_migration_result(result) if validate else MemoryMigrationValidationReport(valid=True)
    errors = [*result.errors, *validation.errors]

    written_ids: list[str] = []
    if not dry_run and result.memories:
        store = FileMemoryStore(target)
        current = store.load(namespace)
        merged = _merge_migration_memories(current, result.memories)
        store.save(namespace, merged)
        written_ids = [memory.memory_id for memory in result.memories]

    return MemoryMigrationRunResult(
        source_system=result.source_system,
        source_path=str(path),
        target_dir=str(target),
        namespace=namespace,
        total_seen=result.total_seen,
        migrated_count=result.migrated_count,
        written_count=len(written_ids),
        error_count=len(errors),
        validation_valid=validation.valid,
        memory_ids=written_ids if written_ids else [memory.memory_id for memory in result.memories],
        errors=errors,
    )


def _convert_legacy_entry(payload: dict[str, Any], *, source_agent: str | None) -> Memory:
    content = _required_text(payload, "content")
    source_id = str(payload.get("entry_id") or payload.get("id") or stable_id("legacy", content, size=12))
    memory_id = _legacy_memory_id(source_id)
    category = _category(payload.get("category"))
    confidence = _score(payload.get("confidence"), default=0.5)
    salience = _score(payload.get("salience", payload.get("priority")), default=0.5)
    state = _legacy_state(payload.get("state"))
    evidence = _evidence_from_refs(payload.get("evidence_refs"), source_id=source_id)
    created_at = utc_now()
    source_thread_id = _optional_text(payload.get("source_thread_id") or payload.get("session_id") or payload.get("thread_id"))
    content = compile_manual_memory_content(
        content,
        memory_id=memory_id,
        category=category,
        confidence=confidence,
        created_at=created_at,
        source_thread_id=source_thread_id or "migration",
        observation_id=stable_id("obs", "legacy-anvil", source_id, size=16),
        evidence=evidence,
    )
    return Memory(
        memory_id=memory_id,
        content=content,
        summary=_summary(payload.get("summary") or payload.get("title") or content),
        category=category,
        confidence=confidence,
        salience=salience,
        evidence=evidence,
        tags=_string_list(payload.get("tags")),
        concepts=_string_list(payload.get("concepts")),
        created_at=created_at,
        updated_at=created_at,
        accessed_at=created_at,
        access_count=max(0, int(payload.get("access_count") or payload.get("accessCount") or 0)),
        state=state,
        source_thread_id=source_thread_id,
        source_agent=source_agent,
        source_type=SourceType.IMPORT,
        metadata={
            "migration_source": "legacy_anvil_entry",
            "legacy_id": source_id,
            "observation_id": stable_id("obs", "legacy-anvil", source_id, size=16),
        },
    )


def _migrate_from_source(source_system: str, source_path: Path, *, source_agent: str | None) -> MemoryMigrationResult:
    if not source_path.exists():
        return MemoryMigrationResult(
            source_system=source_system,
            errors=[{"source_id": str(source_path), "error": "source path does not exist"}],
            error_count=1,
        )
    if source_system == "agentmemory":
        return migrate_agentmemory_payloads(_load_agentmemory_payloads(source_path), source_agent=source_agent)
    if source_system == "deerflow":
        return migrate_deerflow_payload(_load_deerflow_payload(source_path), source_agent=source_agent)
    if source_system == "anvil":
        entries, profile_facets = _load_legacy_anvil_source(source_path)
        return migrate_legacy_anvil_payloads(entries=entries, profile_facets=profile_facets, source_agent=source_agent)
    raise ValueError(f"unsupported memory migration source system: {source_system}")


def _load_agentmemory_payloads(source_path: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for path in _json_source_files(source_path):
        payload = _read_json(path)
        items = _payload_items(payload, "memories")
        for item in items:
            if isinstance(item, dict):
                payloads.append(item)
            else:
                payloads.append({"id": f"{path.stem}:{len(payloads) + 1}", "content": str(item)})
    return payloads


def _load_deerflow_payload(source_path: Path) -> dict[str, Any]:
    if source_path.is_file():
        payload = _read_json(source_path)
        return payload if isinstance(payload, dict) else {"facts": []}
    memory_file = source_path / "memory.json"
    payload = _read_json(memory_file) if memory_file.exists() else {}
    if not isinstance(payload, dict):
        payload = {}
    facts = list(_payload_items(payload, "facts"))
    agents_dir = source_path / "agents"
    if agents_dir.exists():
        for agent_file in sorted(agents_dir.glob("*/memory.json")):
            agent_payload = _read_json(agent_file)
            if isinstance(agent_payload, dict):
                for fact in _payload_items(agent_payload, "facts"):
                    if isinstance(fact, dict):
                        fact = {**fact, "source_agent": agent_file.parent.name}
                    facts.append(fact)
    if facts:
        payload["facts"] = facts
    return payload


def _load_legacy_anvil_source(source_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if source_path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return _load_legacy_anvil_sqlite(source_path)
    payload = _read_json(source_path) if source_path.is_file() else _read_legacy_anvil_directory(source_path)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], []
    if not isinstance(payload, dict):
        return [], []
    entries = _payload_items(payload, "entries")
    if not entries:
        entries = _payload_items(payload, "curated_entries")
    profile_facets = _payload_items(payload, "profile_facets")
    if not profile_facets:
        profile_facets = _payload_items(payload, "facets")
    return [item for item in entries if isinstance(item, dict)], [item for item in profile_facets if isinstance(item, dict)]


def _load_legacy_anvil_sqlite(source_db: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    facets: list[dict[str, Any]] = []
    with sqlite3.connect(source_db) as conn:
        conn.row_factory = sqlite3.Row
        for table in ("curated_entries", "entries", "memory_entries"):
            if _sqlite_table_exists(conn, table):
                entries.extend(dict(row) for row in conn.execute(f"SELECT * FROM {table}"))
                break
        for table in ("profile_facets", "facets"):
            if _sqlite_table_exists(conn, table):
                facets.extend(dict(row) for row in conn.execute(f"SELECT * FROM {table}"))
                break
    return entries, facets


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _read_legacy_anvil_directory(source_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {"entries": [], "profile_facets": []}
    for name in ("entries.json", "curated_entries.json", "memory_entries.json"):
        path = source_dir / name
        if path.exists():
            payload["entries"].extend(_payload_items(_read_json(path), "entries"))
    for name in ("profile_facets.json", "facets.json"):
        path = source_dir / name
        if path.exists():
            payload["profile_facets"].extend(_payload_items(_read_json(path), "profile_facets"))
    return payload


def _json_source_files(source_path: Path) -> list[Path]:
    if source_path.is_file():
        return [source_path]
    return sorted(source_path.glob("*.json"))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _payload_items(payload: Any, preferred_key: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    value = payload.get(preferred_key)
    if isinstance(value, list):
        return value
    if preferred_key != "memories" and isinstance(payload.get("memories"), list):
        return list(payload["memories"])
    if preferred_key != "facts" and isinstance(payload.get("facts"), list):
        return list(payload["facts"])
    return []


def _merge_migration_memories(current: MemoryState, memories: list[Memory]) -> MemoryState:
    merged = current.model_copy(deep=True)
    by_id = {memory.memory_id: index for index, memory in enumerate(merged.memories)}
    for memory in memories:
        index = by_id.get(memory.memory_id)
        if index is None:
            by_id[memory.memory_id] = len(merged.memories)
            merged.memories.append(memory)
        else:
            merged.memories[index] = memory
    return merged


def _convert_profile_facet(payload: dict[str, Any], *, source_agent: str | None) -> Memory:
    content = _required_text(payload, "value")
    source_id = str(payload.get("facet_id") or payload.get("id") or stable_id("facet", content, size=12))
    class_id = str(payload.get("class_id") or payload.get("classId") or "context").strip().lower()
    key = _optional_text(payload.get("key"))
    memory_id = _legacy_memory_id(source_id)
    category = _profile_category(class_id)
    confidence = _score(float(payload.get("stability_score") or payload.get("stabilityScore") or 0.75) / 1.5, default=0.5)
    salience = confidence
    state = _legacy_state(payload.get("state"))
    created_at = utc_now()
    evidence = [
        Evidence(
            type=EvidenceType.PATTERN,
            content=f"Migrated ProfileFacet {class_id}{':' + key if key else ''}.",
            weight=confidence,
            source_id=source_id,
        )
    ]
    content = compile_manual_memory_content(
        content,
        memory_id=memory_id,
        category=category,
        confidence=confidence,
        created_at=created_at,
        source_thread_id="migration",
        observation_id=stable_id("obs", "legacy-anvil-facet", source_id, size=16),
        evidence=evidence,
    )
    tags = [class_id, "profile_facet"]
    if key:
        tags.append(key)
    return Memory(
        memory_id=memory_id,
        content=content,
        summary=_summary(payload.get("summary") or payload.get("value")),
        category=category,
        confidence=confidence,
        salience=salience,
        evidence=evidence,
        tags=tags,
        created_at=created_at,
        updated_at=created_at,
        accessed_at=created_at,
        state=state,
        source_agent=source_agent,
        source_type=SourceType.IMPORT,
        metadata={
            "migration_source": "legacy_anvil_profile_facet",
            "legacy_id": source_id,
            "class_id": class_id,
            "key": key,
            "prompt_visible": bool(payload.get("prompt_visible", payload.get("promptVisible", False))),
            "observation_id": stable_id("obs", "legacy-anvil-facet", source_id, size=16),
        },
    )


def _convert_agentmemory_payload(payload: dict[str, Any], *, source_agent: str | None) -> Memory:
    content = _required_text(payload, "content")
    source_id = str(payload.get("id") or payload.get("memory_id") or stable_id("agentmemory", content, size=12))
    memory_id = _legacy_memory_id(source_id)
    category = _category(payload.get("category"))
    confidence = _score(payload.get("confidence"), default=0.5)
    salience = _score(payload.get("strength", payload.get("salience")), default=0.5)
    version = max(1, int(payload.get("version") or 1))
    parent_id = _optional_text(payload.get("parentId") or payload.get("parent_id"))
    supersedes = _string_list(payload.get("supersedes"))
    state = MemoryLifecycleState.ACTIVE if bool(payload.get("isLatest", True)) else MemoryLifecycleState.ARCHIVED
    source_thread_id = _optional_text(payload.get("sessionId") or payload.get("session_id") or payload.get("thread_id"))
    created_at = utc_now()
    evidence = _evidence_from_refs(payload.get("evidence_refs") or payload.get("evidenceRefs"), source_id=source_id)
    content = compile_manual_memory_content(
        content,
        memory_id=memory_id,
        category=category,
        confidence=confidence,
        created_at=created_at,
        source_thread_id=source_thread_id or "migration",
        observation_id=stable_id("obs", "agentmemory", source_id, size=16),
        evidence=evidence,
    )
    return Memory(
        memory_id=memory_id,
        version=version,
        parent_id=parent_id,
        supersedes=supersedes,
        content=content,
        summary=_summary(payload.get("title") or payload.get("summary") or payload.get("content")),
        category=category,
        confidence=confidence,
        salience=salience,
        evidence=evidence,
        tags=_string_list(payload.get("tags")),
        concepts=_string_list(payload.get("concepts")),
        created_at=created_at,
        updated_at=created_at,
        accessed_at=created_at,
        access_count=max(0, int(payload.get("accessCount") or payload.get("access_count") or 0)),
        state=state,
        source_thread_id=source_thread_id,
        source_agent=source_agent,
        source_type=SourceType.IMPORT,
        metadata={
            "migration_source": "agentmemory",
            "legacy_id": source_id,
            "observation_id": stable_id("obs", "agentmemory", source_id, size=16),
        },
    )


def _convert_deerflow_context(section: str, value: Any, *, source_agent: str | None) -> Memory:
    payload = value if isinstance(value, dict) else {"summary": value}
    content = _required_text(payload, "summary")
    source_id = f"deerflow_{section}"
    memory_id = _legacy_memory_id(source_id)
    created_at = utc_now()
    evidence = [
        Evidence(
            type=EvidenceType.OBSERVATION,
            content=f"Migrated DeerFlow user context section {section}.",
            weight=0.6,
            source_id=source_id,
        )
    ]
    content = compile_manual_memory_content(
        content,
        memory_id=memory_id,
        category=MemoryCategory.CONTEXT,
        confidence=0.7,
        created_at=created_at,
        source_thread_id="migration",
        observation_id=stable_id("obs", "deerflow-context", section, size=16),
        evidence=evidence,
    )
    return Memory(
        memory_id=memory_id,
        content=content,
        summary=_summary(payload.get("summary")),
        category=MemoryCategory.CONTEXT,
        confidence=0.7,
        salience=0.6,
        evidence=evidence,
        tags=[str(section)],
        created_at=created_at,
        updated_at=created_at,
        accessed_at=created_at,
        source_agent=source_agent,
        source_type=SourceType.IMPORT,
        metadata={
            "migration_source": "deerflow_context",
            "legacy_id": source_id,
            "section": str(section),
            "observation_id": stable_id("obs", "deerflow-context", section, size=16),
        },
    )


def _convert_deerflow_fact(payload: dict[str, Any], *, source_agent: str | None) -> Memory:
    content = _required_text(payload, "content")
    source_id = str(payload.get("id") or stable_id("deerflow-fact", content, size=12))
    memory_id = _legacy_memory_id(source_id)
    category = MemoryCategory.CORRECTION if payload.get("sourceError") else _category(payload.get("category"))
    confidence = _score(payload.get("confidence"), default=0.7)
    source_thread_id = _optional_text(payload.get("source") or payload.get("thread_id"))
    created_at = utc_now()
    evidence_type = EvidenceType.CORRECTION if payload.get("sourceError") else EvidenceType.OBSERVATION
    evidence_content = str(payload.get("sourceError") or f"Migrated DeerFlow fact {source_id}.")
    evidence = [
        Evidence(
            type=evidence_type,
            content=evidence_content,
            weight=confidence,
            source_id=source_thread_id or source_id,
            metadata={"source_error": payload.get("sourceError")} if payload.get("sourceError") else {},
        )
    ]
    content = compile_manual_memory_content(
        content,
        memory_id=memory_id,
        category=category,
        confidence=confidence,
        created_at=created_at,
        source_thread_id=source_thread_id or "migration",
        observation_id=stable_id("obs", "deerflow-fact", source_id, size=16),
        evidence=evidence,
    )
    return Memory(
        memory_id=memory_id,
        content=content,
        summary=_summary(payload.get("content")),
        category=category,
        confidence=confidence,
        salience=0.5,
        evidence=evidence,
        created_at=created_at,
        updated_at=created_at,
        accessed_at=created_at,
        source_thread_id=source_thread_id,
        source_agent=source_agent,
        source_type=SourceType.IMPORT,
        metadata={
            "migration_source": "deerflow_fact",
            "legacy_id": source_id,
            "observation_id": stable_id("obs", "deerflow-fact", source_id, size=16),
        },
    )


def _legacy_memory_id(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("mem_"):
        return normalized
    for prefix in ("entry_", "facet_", "memory_", "fact_"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    normalized = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in normalized).strip("_")
    return f"mem_{normalized}" if normalized else stable_id("mem", value, size=12)


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"legacy payload missing required {key!r}")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _summary(value: Any) -> str:
    return str(value or "").strip()[:120]


def _score(value: Any, *, default: float) -> float:
    try:
        numeric = float(value if value is not None else default)
    except (TypeError, ValueError):
        numeric = default
    return round(min(max(numeric, 0.0), 1.0), 4)


def _category(value: Any) -> MemoryCategory:
    try:
        return MemoryCategory(str(value or MemoryCategory.NOTE.value).strip().lower())
    except ValueError:
        return MemoryCategory.NOTE


def _profile_category(class_id: str) -> MemoryCategory:
    return {
        "style": MemoryCategory.BEHAVIOR,
        "identity": MemoryCategory.CONTEXT,
        "tooling": MemoryCategory.PREFERENCE,
        "veto": MemoryCategory.PREFERENCE,
        "goal": MemoryCategory.GOAL,
        "workflow": MemoryCategory.BEHAVIOR,
        "environment": MemoryCategory.CONTEXT,
        "project_fact": MemoryCategory.KNOWLEDGE,
    }.get(class_id, MemoryCategory.KNOWLEDGE)


def _legacy_state(value: Any) -> MemoryLifecycleState:
    normalized = str(value or "active").strip().lower()
    return {
        "active": MemoryLifecycleState.ACTIVE,
        "provisional": MemoryLifecycleState.PROVISIONAL,
        "archived": MemoryLifecycleState.ARCHIVED,
        "forgotten": MemoryLifecycleState.FORGOTTEN,
        "dropped": MemoryLifecycleState.FORGOTTEN,
        "deleted": MemoryLifecycleState.DELETED,
        "review": MemoryLifecycleState.REVIEW,
    }.get(normalized, MemoryLifecycleState.ACTIVE)


def _evidence_from_refs(value: Any, *, source_id: str) -> list[Evidence]:
    refs = _string_list(value)
    return [
        Evidence(
            type=EvidenceType.OBSERVATION,
            content=f"Migrated legacy evidence reference: {ref}",
            weight=0.6,
            source_id=ref or source_id,
        )
        for ref in refs
    ]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _try_save(client: Any, memory: Memory, *, target: str, errors: list[dict[str, str]]) -> bool:
    try:
        if hasattr(client, "save_memory"):
            client.save_memory(memory)
        elif hasattr(client, "save"):
            client.save(memory)
        else:
            raise AttributeError(f"{target} client must expose save_memory() or save()")
        return True
    except Exception as exc:
        errors.append({"target": target, "error": str(exc)})
        return False


def _retrieve(client: Any, query: str, *, limit: int) -> Iterable[Any]:
    if hasattr(client, "retrieve"):
        return client.retrieve(query, limit=limit)
    if hasattr(client, "search"):
        return client.search(query, limit=limit)
    raise AttributeError("memory client must expose retrieve() or search()")
