from __future__ import annotations

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from anvil.memory.config import HCMSConfig, RetrievalConfig
from anvil.memory.models import (
    CausalEdge,
    CausalType,
    Evidence,
    EvidenceType,
    Memory,
    MemoryCategory,
    MemoryLifecycleState,
    MemoryState,
    MemoryVersionRecord,
)
from anvil.memory.compiler import KnowledgeCompiler
from anvil.memory.store import FileMemoryStore
from anvil.memory.storage import (
    FileSystemMemoryBackend,
    HybridMemoryBackend,
    MemoryNotFoundError,
    MemoryVersionControl,
    StorageError,
    three_way_merge_content,
)
from anvil.memory import utc_now


def test_hcms_core_config_storage_backend_default_aliases_and_rejection(contract_tmp_path) -> None:
    default_config = HCMSConfig(base_dir=contract_tmp_path / "default")
    markdown_alias = HCMSConfig(base_dir=contract_tmp_path / "markdown", storage_backend="markdown")
    json_alias = HCMSConfig(base_dir=contract_tmp_path / "json", storage_backend="json")

    assert default_config.storage_backend == "hybrid"
    assert markdown_alias.storage_backend == "hybrid"
    assert json_alias.storage_backend == "filesystem"
    with pytest.raises(ValidationError, match="storage_backend"):
        HCMSConfig(base_dir=contract_tmp_path / "invalid", storage_backend="remote_magic")


def test_hcms_phase1_models_config_and_markdown_storage_roundtrip(contract_tmp_path) -> None:
    config = HCMSConfig(base_dir=contract_tmp_path / "hcms", retrieval=RetrievalConfig(default_limit=5))
    backend = HybridMemoryBackend(config.base_dir)
    memory = Memory(
        content="Northstar requires canary verification before release.",
        summary="Northstar release canary verification",
        category=MemoryCategory.PROJECT_CONTEXT,
        confidence=0.91,
        salience=0.82,
        tags=["northstar", "release"],
    )

    stored = backend.save_memory("workspace/northstar", memory)
    reloaded = backend.get_memory("workspace/northstar", stored.memory_id)
    serialized = Memory.from_dict(reloaded.to_dict())
    markdown = next((config.base_dir / "memories").glob("*/*.md")).read_text(encoding="utf-8")
    validation = KnowledgeCompiler.validate_markdown_schema(reloaded.content)

    assert reloaded.memory_id == stored.memory_id
    assert serialized.content == reloaded.content
    assert validation.valid, validation.errors
    assert "source_thread_id: manual" in reloaded.content
    assert "## HCMS Payload" in markdown
    assert "Northstar requires canary verification" in markdown
    assert "workspace/northstar" in backend.list_namespaces()


def test_hcms_filesystem_markdown_frontmatter_exposes_documented_memory_fields(contract_tmp_path) -> None:
    backend = FileSystemMemoryBackend(contract_tmp_path / "filesystem-hcms")
    memory = Memory(
        content="Markdown frontmatter must expose documented HCMS metadata fields.",
        summary="Markdown frontmatter metadata",
        category=MemoryCategory.KNOWLEDGE,
        confidence=0.88,
        salience=0.79,
        reasoning="Persisted because the storage schema should be human-auditable.",
        tags=["schema"],
        entities=["HCMS"],
        concepts=["frontmatter", "metadata"],
        access_count=4,
        source_thread_id="thread-frontmatter",
        source_agent="agent-storage",
        evidence=[
            Evidence(
                evidence_id="ev_frontmatter",
                type=EvidenceType.USER_STATED,
                content="User requested human-auditable HCMS storage.",
                weight=0.88,
                source_id="thread-frontmatter",
            )
        ],
    )

    backend.save_memory("workspace/northstar", memory)
    markdown = next((contract_tmp_path / "filesystem-hcms" / "memories").glob("*/*.md")).read_text(encoding="utf-8")

    assert 'concepts: ["frontmatter", "metadata"]' in markdown
    assert "source_thread_id: \"thread-frontmatter\"" in markdown
    assert "source_agent: \"agent-storage\"" in markdown
    assert "source_type: \"observation\"" in markdown
    assert "access_count: 4" in markdown
    assert "reasoning: \"Persisted because the storage schema should be human-auditable.\"" in markdown


def test_hcms_filesystem_backend_direct_save_normalizes_bare_memory_schema(contract_tmp_path) -> None:
    backend = FileSystemMemoryBackend(contract_tmp_path / "filesystem-hcms")
    memory = Memory(
        content="Filesystem backend direct save must preserve compiled HCMS Markdown.",
        summary="Filesystem backend direct save",
        category=MemoryCategory.PROCEDURE,
        confidence=0.86,
        salience=0.76,
    )

    stored = backend.save_memory("workspace/northstar", memory)
    reloaded = backend.get_memory("workspace/northstar", stored.memory_id)
    validation = KnowledgeCompiler.validate_markdown_schema(reloaded.content)

    assert validation.valid, validation.errors
    assert "source_thread_id: manual" in reloaded.content
    assert "Filesystem backend direct save must preserve compiled HCMS Markdown." in reloaded.content


def test_hcms_file_memory_store_direct_state_save_normalizes_bare_memory_schema(contract_tmp_path) -> None:
    store = FileMemoryStore(contract_tmp_path / "json-hcms-store")
    memory = Memory(
        content="JSON state store direct save must preserve compiled HCMS Markdown.",
        summary="JSON state store direct save",
        category=MemoryCategory.NOTE,
        confidence=0.82,
        salience=0.7,
    )
    state = MemoryState(namespace="global/default", memories=[memory])

    store.save("global/default", state)
    reloaded = store.load("global/default")
    validation = KnowledgeCompiler.validate_markdown_schema(reloaded.memories[0].content)

    assert validation.valid, validation.errors
    assert "source_thread_id: manual" in reloaded.memories[0].content
    assert "JSON state store direct save must preserve compiled HCMS Markdown." in reloaded.memories[0].content


def test_hcms_hybrid_state_sidecar_save_normalizes_bare_memory_schema(contract_tmp_path) -> None:
    backend = HybridMemoryBackend(contract_tmp_path / "hybrid-hcms")
    memory = Memory(
        content="Hybrid state sidecar direct save must preserve compiled HCMS Markdown.",
        summary="Hybrid sidecar direct save",
        category=MemoryCategory.DECISION,
        confidence=0.84,
        salience=0.72,
    )
    state = MemoryState(namespace="workspace/northstar", memories=[memory])

    backend.save("workspace/northstar", state)
    sidecar = backend.base_path / "states" / "workspace-northstar.json"
    persisted = MemoryState.model_validate_json(sidecar.read_text(encoding="utf-8"))
    validation = KnowledgeCompiler.validate_markdown_schema(persisted.memories[0].content)

    assert validation.valid, validation.errors
    assert "source_thread_id: manual" in persisted.memories[0].content
    assert "Hybrid state sidecar direct save must preserve compiled HCMS Markdown." in persisted.memories[0].content


def test_hcms_phase1_hybrid_search_version_history_and_diff(contract_tmp_path) -> None:
    backend = HybridMemoryBackend(contract_tmp_path / "hcms")
    memory = backend.create_memory(
        "workspace/northstar",
        content="Use pytest for backend verification.",
        category="procedure",
        confidence=0.8,
        salience=0.7,
    )
    version_control = MemoryVersionControl(backend, namespace="workspace/northstar")
    validation = KnowledgeCompiler.validate_markdown_schema(memory.content)
    assert validation.valid, validation.errors
    assert "source_thread_id: manual" in memory.content
    assert "Use pytest for backend verification." in memory.content

    updated = version_control.create_version(
        memory.memory_id,
        content="Use pytest -q for backend verification.",
        reason="tighten_test_command",
    )
    updated_validation = KnowledgeCompiler.validate_markdown_schema(updated.content)
    assert updated_validation.valid, updated_validation.errors
    results = backend.search_memories("workspace/northstar", "pytest backend verification", limit=5)
    history = version_control.history(memory.memory_id)
    diff = version_control.diff_versions(memory.memory_id, 1, 2)

    assert updated.version == 2
    assert updated.parent_id == f"{memory.memory_id}@v1"
    assert updated.supersedes == [f"{memory.memory_id}@v1"]
    assert results and results[0].memory_id == memory.memory_id
    assert [record.version for record in history] == [1, 2]
    assert [record.parent_id for record in history] == [None, f"{memory.memory_id}@v1"]
    assert "+Use pytest -q for backend verification." in diff.content_diff
    assert version_control.latest_diff(memory.memory_id) == history[-1].diff


def test_hcms_version_control_fallback_preserves_git_like_parent_chain(contract_tmp_path) -> None:
    class MinimalBackend:
        def __init__(self) -> None:
            self.memories: dict[str, Memory] = {}
            self.versions: dict[str, list[MemoryVersionRecord]] = {}

        def save_memory(self, namespace: str, memory: Memory, *, expected_version: int | None = None) -> Memory:
            current = self.memories.get(memory.memory_id)
            if current is not None and expected_version is not None:
                assert current.version == expected_version
            self.memories[memory.memory_id] = memory
            return memory

        def get_memory(self, namespace: str, memory_id: str) -> Memory:
            return self.memories[memory_id]

        def append_version(self, namespace: str, record: MemoryVersionRecord) -> None:
            self.versions.setdefault(record.memory_id, []).append(record)

        def history(self, namespace: str, memory_id: str) -> tuple[MemoryVersionRecord, ...]:
            return tuple(self.versions.get(memory_id, ()))

    backend = MinimalBackend()
    initial = backend.save_memory(
        "workspace/northstar",
        Memory(
            content="Fallback version control starts with a base memory.",
            summary="Fallback version base",
            category=MemoryCategory.KNOWLEDGE,
            confidence=0.72,
        ),
    )
    version_control = MemoryVersionControl(backend, namespace="workspace/northstar")

    updated = version_control.create_version(
        initial.memory_id,
        content="Fallback version control creates a linked v2 memory.",
        reason="fallback_update",
    )

    assert updated.version == 2
    assert updated.parent_id == f"{initial.memory_id}@v1"
    assert updated.supersedes == [f"{initial.memory_id}@v1"]
    assert version_control.history(initial.memory_id)[-1].parent_id == f"{initial.memory_id}@v1"


def test_hcms_version_diff_reports_confidence_and_evidence_changes(contract_tmp_path) -> None:
    backend = HybridMemoryBackend(contract_tmp_path / "hcms")
    memory = backend.save_memory(
        "workspace/northstar",
        Memory(
            content="Northstar release requires canary verification.",
            summary="Northstar canary verification",
            category=MemoryCategory.PROJECT_CONTEXT,
            confidence=0.6,
            salience=0.7,
            evidence=[
                Evidence(
                    evidence_id="ev_original",
                    type=EvidenceType.USER_STATED,
                    content="User stated the canary rule.",
                    weight=0.6,
                    source_id="thread-a",
                )
            ],
        ),
    )
    version_control = MemoryVersionControl(backend, namespace="workspace/northstar")

    version_control.create_version(
        memory.memory_id,
        content="Northstar release requires canary verification and smoke validation.",
        confidence=0.85,
        evidence=[
            *memory.evidence,
            Evidence(
                evidence_id="ev_smoke",
                type=EvidenceType.REINFORCEMENT,
                content="Smoke validation reinforced the canary rule.",
                weight=0.8,
                source_id="thread-b",
            ),
        ],
        reason="reinforce_canary_rule",
    )
    diff = version_control.diff_versions(memory.memory_id, 1, 2)

    assert diff.confidence_delta == 0.25
    assert diff.evidence_added == ("ev_smoke",)
    assert diff.evidence_removed == ()


def test_hcms_version_control_three_way_merge_commit(contract_tmp_path) -> None:
    backend = HybridMemoryBackend(contract_tmp_path / "hcms")
    memory = backend.create_memory(
        "workspace/northstar",
        content="Title\nBase fact\nStable line",
        category="procedure",
        confidence=0.8,
        salience=0.7,
    )
    version_control = MemoryVersionControl(backend, namespace="workspace/northstar")
    backend.append_version(
        "workspace/northstar",
        MemoryVersionRecord(
            memory_id=memory.memory_id,
            version=2,
            parent_id=f"{memory.memory_id}@v1",
            content="Title\nLeft fact\nStable line",
            summary="left branch",
            reason="left_branch",
        ),
    )
    backend.append_version(
        "workspace/northstar",
        MemoryVersionRecord(
            memory_id=memory.memory_id,
            version=3,
            parent_id=f"{memory.memory_id}@v1",
            content="Title\nBase fact\nRight line",
            summary="right branch",
            reason="right_branch",
        ),
    )

    merged = version_control.merge_versions(
        memory.memory_id,
        base_version=1,
        left_version=2,
        right_version=3,
    )

    assert merged.success is True
    assert merged.conflict_count == 0
    assert merged.merged_content == "Title\nLeft fact\nRight line"
    assert merged.merged_memory is not None
    merged_validation = KnowledgeCompiler.validate_markdown_schema(merged.merged_memory.content)
    assert merged_validation.valid, merged_validation.errors
    assert "Title\nLeft fact\nRight line" in merged.merged_memory.content
    assert merged.merged_memory.version == 4
    assert f"{memory.memory_id}@v2" in merged.merged_memory.supersedes
    assert f"{memory.memory_id}@v3" in merged.merged_memory.supersedes
    assert version_control.history(memory.memory_id)[-1].reason == "three_way_merge"


def test_hcms_version_control_three_way_merge_reports_conflicts() -> None:
    result = three_way_merge_content(
        memory_id="mem_conflict",
        base_version=1,
        left_version=2,
        right_version=3,
        base_content="Preference\nUse pytest",
        left_content="Preference\nUse pytest -q",
        right_content="Preference\nUse uv run pytest",
    )

    assert result.success is False
    assert result.conflict_count == 1
    assert "line 2" in result.conflicts[0]
    assert "pytest -q" in result.conflicts[0]
    assert "uv run pytest" in result.conflicts[0]


def test_hcms_hybrid_backend_exports_and_restores_namespace_backup(contract_tmp_path) -> None:
    source = HybridMemoryBackend(contract_tmp_path / "source-hcms")
    memory = source.create_memory(
        "workspace/northstar",
        content="Northstar backup restoration keeps searchable canary release memory.",
        category="project_context",
        confidence=0.91,
        salience=0.86,
    )
    version_control = MemoryVersionControl(source, namespace="workspace/northstar")
    version_control.create_version(
        memory.memory_id,
        content="Northstar backup restoration keeps searchable canary release memory with version history.",
        reason="add_restore_detail",
    )

    backup_path = source.export_namespace_backup("workspace/northstar", contract_tmp_path / "backups")
    restored = HybridMemoryBackend(contract_tmp_path / "restored-hcms")
    manifest = restored.restore_namespace_backup(backup_path)
    recovered = restored.get_memory("workspace/northstar", memory.memory_id)
    history = restored.history("workspace/northstar", memory.memory_id)
    results = restored.search_memories("workspace/northstar", "canary release version history", limit=5)

    assert backup_path.exists()
    assert manifest.namespace == "workspace/northstar"
    assert manifest.memory_count == 1
    assert manifest.version_count >= 2
    recovered_validation = KnowledgeCompiler.validate_markdown_schema(recovered.content)
    assert recovered_validation.valid, recovered_validation.errors
    assert "Northstar backup restoration keeps searchable canary release memory with version history." in recovered.content
    assert [record.version for record in history] == [1, 2]
    assert results and results[0].memory_id == memory.memory_id


def test_hcms_hybrid_backend_exports_incremental_namespace_backup_since_timestamp(contract_tmp_path) -> None:
    source = HybridMemoryBackend(contract_tmp_path / "source-hcms")
    old_memory = source.create_memory(
        "workspace/northstar",
        content="Old backup memory should stay out of incremental export.",
        category="note",
        confidence=0.9,
        salience=0.8,
    )
    new_memory = source.create_memory(
        "workspace/northstar",
        content="New backup memory should be included in incremental export.",
        category="note",
        confidence=0.9,
        salience=0.8,
    )
    cutoff = utc_now() - timedelta(hours=1)
    source.save_memory(
        "workspace/northstar",
        old_memory.model_copy(
            deep=True,
            update={"updated_at": cutoff - timedelta(days=1), "created_at": cutoff - timedelta(days=2)},
        ),
    )
    source.save_memory(
        "workspace/northstar",
        new_memory.model_copy(deep=True, update={"updated_at": cutoff + timedelta(minutes=5)}),
    )

    backup_path = source.export_namespace_backup("workspace/northstar", contract_tmp_path / "backups", since=cutoff)
    payload = json.loads(backup_path.read_text(encoding="utf-8"))
    memory_ids = {item["memory_id"] for item in payload["memories"]}

    assert payload["manifest"]["incremental"] is True
    assert payload["manifest"]["since"] == cutoff.isoformat()
    assert payload["manifest"]["memory_count"] == 1
    assert memory_ids == {new_memory.memory_id}


def test_hcms_hybrid_backup_restores_full_state_graph(contract_tmp_path) -> None:
    source = HybridMemoryBackend(contract_tmp_path / "source-hcms")
    cause = source.create_memory(
        "workspace/northstar",
        content="Northstar changed the deployment gate and caused canary verification to run before release.",
        category="decision",
        confidence=0.92,
        salience=0.9,
    )
    effect = source.create_memory(
        "workspace/northstar",
        content="Canary verification happened after the Northstar deployment gate changed.",
        category="event",
        confidence=0.89,
        salience=0.84,
    )
    state = source.load("workspace/northstar").model_copy(
        deep=True,
        update={
            "causal_edges": [
                CausalEdge(
                    source_event=cause.memory_id,
                    target_event=effect.memory_id,
                    causal_type=CausalType.DIRECT_CAUSE,
                    strength=0.88,
                    evidence=[cause.memory_id, effect.memory_id],
                )
            ],
        },
    )
    source.save("workspace/northstar", state)

    backup_path = source.export_namespace_backup("workspace/northstar", contract_tmp_path / "backups")
    restored = HybridMemoryBackend(contract_tmp_path / "restored-hcms")
    restored.restore_namespace_backup(backup_path)
    recovered_state = restored.load("workspace/northstar")

    assert [edge.source_event for edge in recovered_state.causal_edges] == [cause.memory_id]
    assert [edge.target_event for edge in recovered_state.causal_edges] == [effect.memory_id]


def test_hcms_hybrid_backend_restore_reports_typed_backup_errors(contract_tmp_path) -> None:
    backend = HybridMemoryBackend(contract_tmp_path / "hcms")
    malformed_backup = contract_tmp_path / "malformed-backup.json"
    malformed_backup.write_text("{not-valid-json", encoding="utf-8")

    with pytest.raises(StorageError, match="Could not restore HCMS backup"):
        backend.restore_namespace_backup(malformed_backup)

    missing_manifest_backup = contract_tmp_path / "missing-manifest-backup.json"
    missing_manifest_backup.write_text(json.dumps({"memories": [], "versions": []}), encoding="utf-8")

    with pytest.raises(StorageError, match="manifest"):
        backend.restore_namespace_backup(missing_manifest_backup)


def test_hcms_phase1_crud_archive_restore_forget_delete(contract_tmp_path) -> None:
    backend = HybridMemoryBackend(contract_tmp_path / "hcms")
    memory = backend.create_memory(
        "workspace/northstar",
        content="Temporary rollout note.",
        category="note",
        confidence=0.5,
        salience=0.4,
    )

    archived = backend.archive_memory("workspace/northstar", memory.memory_id)
    restored = backend.restore_memory("workspace/northstar", memory.memory_id)
    forgotten = backend.forget_memory("workspace/northstar", memory.memory_id)
    state = backend.load("workspace/northstar")

    assert archived.state == MemoryLifecycleState.ARCHIVED
    assert restored.state == MemoryLifecycleState.ACTIVE
    assert forgotten.state == MemoryLifecycleState.FORGOTTEN
    assert state.memories[0].memory_id == memory.memory_id

    backend.delete_memory("workspace/northstar", memory.memory_id)
    with pytest.raises(MemoryNotFoundError):
        backend.get_memory("workspace/northstar", memory.memory_id)
    assert backend.history("workspace/northstar", memory.memory_id)
