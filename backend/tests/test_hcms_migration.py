from __future__ import annotations

import sqlite3

from anvil.memory import (
    DebouncedMemoryQueue,
    FileMemoryStore,
    HeuristicMemoryUpdater,
    KnowledgeCompiler,
    MemoryCategory,
    MemoryLifecycleState,
    MemoryService,
    MemoryState,
    SourceType,
)
from anvil.memory.migration import (
    DualWriteMemoryService,
    migrate_agentmemory_payloads,
    migrate_deerflow_payload,
    migrate_legacy_anvil_payloads,
    run_memory_migration,
    validate_migration_result,
)


class RecordingLegacyMemoryClient:
    def __init__(self, *, search_results=None, fail_save: bool = False, fail_search: bool = False) -> None:
        self.saved = []
        self.queries = []
        self.search_results = list(search_results or [])
        self.fail_save = fail_save
        self.fail_search = fail_search

    def save_memory(self, memory):
        if self.fail_save:
            raise RuntimeError("legacy save failed")
        self.saved.append(memory)
        return {"legacy_id": getattr(memory, "memory_id", str(memory))}

    def search(self, query: str, *, limit: int = 10):
        if self.fail_search:
            raise RuntimeError("legacy search failed")
        self.queries.append((query, limit))
        return self.search_results[:limit]


class RecordingHCMSClient:
    def __init__(self, *, search_results=None, fail_save: bool = False, fail_search: bool = False) -> None:
        self.saved = []
        self.queries = []
        self.search_results = list(search_results or [])
        self.fail_save = fail_save
        self.fail_search = fail_search

    def save_memory(self, memory):
        if self.fail_save:
            raise RuntimeError("hcms save failed")
        self.saved.append(memory)
        return memory

    def retrieve(self, query: str, *, limit: int = 10):
        if self.fail_search:
            raise RuntimeError("hcms search failed")
        self.queries.append((query, limit))
        return self.search_results[:limit]


def test_hcms_migrates_legacy_anvil_entries_and_profile_facets_to_typed_memories() -> None:
    result = migrate_legacy_anvil_payloads(
        entries=[
            {
                "entry_id": "entry_python_preference",
                "content": "User prefers Python for backend automation.",
                "category": "preference",
                "confidence": 0.95,
                "salience": 0.8,
                "priority": 0.7,
                "evidence_refs": ["thread:alpha", "pytest:alpha"],
                "state": "active",
                "source_thread_id": "thread-alpha",
            },
            {
                "entry_id": "entry_old_veto",
                "content": "Old veto should be forgotten.",
                "state": "dropped",
            },
        ],
        profile_facets=[
            {
                "facet_id": "facet_concise_style",
                "class_id": "style",
                "key": "communication_style",
                "value": "User prefers concise implementation updates.",
                "stability_score": 1.2,
                "state": "active",
                "prompt_visible": True,
            }
        ],
        source_agent="legacy-anvil",
    )

    by_id = {memory.memory_id: memory for memory in result.memories}

    assert result.total_seen == 3
    assert result.migrated_count == 3
    assert result.error_count == 0
    assert by_id["mem_python_preference"].category == MemoryCategory.PREFERENCE
    assert by_id["mem_python_preference"].confidence == 0.95
    assert by_id["mem_python_preference"].source_thread_id == "thread-alpha"
    assert by_id["mem_python_preference"].source_agent == "legacy-anvil"
    assert by_id["mem_python_preference"].source_type == SourceType.IMPORT
    assert [item.source_id for item in by_id["mem_python_preference"].evidence] == ["thread:alpha", "pytest:alpha"]
    assert by_id["mem_old_veto"].state == MemoryLifecycleState.FORGOTTEN
    assert by_id["mem_concise_style"].category == MemoryCategory.BEHAVIOR
    assert by_id["mem_concise_style"].tags == ["style", "profile_facet", "communication_style"]
    assert by_id["mem_concise_style"].metadata["prompt_visible"] is True


def test_hcms_migration_reports_invalid_legacy_payloads_without_stopping_batch() -> None:
    result = migrate_legacy_anvil_payloads(
        entries=[
            {"entry_id": "entry_valid", "content": "Valid migrated fact."},
            {"entry_id": "entry_invalid"},
        ]
    )

    assert result.total_seen == 2
    assert result.migrated_count == 1
    assert result.error_count == 1
    assert result.errors[0]["source_id"] == "entry_invalid"
    assert "content" in result.errors[0]["error"]


def test_hcms_migrated_legacy_anvil_memories_roundtrip_through_store_and_search(contract_tmp_path) -> None:
    result = migrate_legacy_anvil_payloads(
        entries=[
            {
                "entry_id": "entry_release_canary",
                "content": "Northstar release requires canary verification before rollout.",
                "category": "project_context",
                "confidence": 0.9,
                "salience": 0.85,
                "state": "active",
            }
        ]
    )
    store = FileMemoryStore(contract_tmp_path / "hcms-migration")
    service = MemoryService(store=store, queue=DebouncedMemoryQueue(), updater=HeuristicMemoryUpdater())

    store.save("global/default", MemoryState(namespace="global/default", memories=result.memories))
    state = store.load("global/default")
    results = service.search("global/default", "canary rollout", limit=5)

    assert len(state.memories) == 1
    assert KnowledgeCompiler.validate_markdown_schema(state.memories[0].content).valid
    assert results[0].memory_id == "mem_release_canary"


def test_hcms_migrates_agentmemory_payloads_with_validation_report() -> None:
    result = migrate_agentmemory_payloads(
        [
            {
                "id": "mem_agent_python",
                "version": 2,
                "parentId": "mem_agent_python@v1",
                "content": "User prefers Python over JavaScript.",
                "title": "Python preference",
                "category": "preference",
                "confidence": 0.94,
                "strength": 0.81,
                "isLatest": True,
                "supersedes": ["mem_agent_python@v1"],
                "concepts": ["Python", "JavaScript"],
                "sessionId": "thread-agentmemory",
                "accessCount": 4,
            }
        ],
        source_agent="agentmemory",
    )
    report = validate_migration_result(result)

    assert result.source_system == "agentmemory"
    assert result.migrated_count == 1
    assert result.memories[0].memory_id == "mem_agent_python"
    assert result.memories[0].version == 2
    assert result.memories[0].parent_id == "mem_agent_python@v1"
    assert result.memories[0].salience == 0.81
    assert result.memories[0].concepts == ["Python", "JavaScript"]
    assert result.memories[0].source_thread_id == "thread-agentmemory"
    assert report.valid is True
    assert report.schema_valid_count == 1


def test_hcms_migrates_deerflow_flat_context_and_facts() -> None:
    result = migrate_deerflow_payload(
        {
            "user": {
                "workContext": {"summary": "Working on the Northstar FastAPI backend."},
                "personalContext": {"summary": "Prefers concise implementation notes."},
            },
            "facts": [
                {
                    "id": "fact_release_rule",
                    "content": "Canary release is required before full rollout.",
                    "category": "procedure",
                    "confidence": 0.91,
                    "source": "thread-deerflow",
                    "sourceError": "Direct rollout failed previously.",
                }
            ],
        },
        source_agent="deerflow",
    )

    by_id = {memory.memory_id: memory for memory in result.memories}

    assert result.source_system == "deerflow"
    assert result.migrated_count == 3
    assert any(memory.category == MemoryCategory.CONTEXT for memory in result.memories)
    assert by_id["mem_release_rule"].category == MemoryCategory.CORRECTION
    assert by_id["mem_release_rule"].source_thread_id == "thread-deerflow"
    assert by_id["mem_release_rule"].evidence[0].type.value == "correction"


def test_hcms_dual_write_service_writes_legacy_and_hcms_and_reports_partial_failures() -> None:
    legacy = RecordingLegacyMemoryClient()
    hcms = RecordingHCMSClient(fail_save=True)
    bridge = DualWriteMemoryService(legacy_client=legacy, hcms_client=hcms)
    memory = migrate_legacy_anvil_payloads(entries=[{"entry_id": "entry_dual", "content": "Dual write canary."}]).memories[0]

    result = bridge.save_memory(memory)

    assert result.legacy_ok is True
    assert result.hcms_ok is False
    assert result.ok is False
    assert len(legacy.saved) == 1
    assert hcms.saved == []
    assert result.errors[0]["target"] == "hcms"
    assert "hcms save failed" in result.errors[0]["error"]


def test_hcms_dual_write_service_prefers_hcms_read_and_falls_back_to_legacy() -> None:
    legacy = RecordingLegacyMemoryClient(search_results=[{"id": "legacy-hit"}])
    hcms = RecordingHCMSClient(search_results=[{"id": "hcms-hit"}])
    bridge = DualWriteMemoryService(legacy_client=legacy, hcms_client=hcms)

    hcms_result = bridge.retrieve("canary", limit=3)

    assert hcms_result.source == "hcms"
    assert hcms_result.results == [{"id": "hcms-hit"}]
    assert legacy.queries == []

    fallback = DualWriteMemoryService(
        legacy_client=legacy,
        hcms_client=RecordingHCMSClient(fail_search=True),
    ).retrieve("canary", limit=2)

    assert fallback.source == "legacy"
    assert fallback.results == [{"id": "legacy-hit"}]
    assert fallback.fallback_used is True
    assert fallback.errors[0]["target"] == "hcms"
    assert legacy.queries[-1] == ("canary", 2)


def test_hcms_migration_runner_reads_agentmemory_file_and_writes_target_store(contract_tmp_path) -> None:
    source_file = contract_tmp_path / "agentmemory.json"
    source_file.write_text(
        """
{
  "memories": [
    {
      "id": "mem_agent_cli",
      "content": "AgentMemory migrated CLI fact uses Python.",
      "category": "preference",
      "confidence": 0.93,
      "strength": 0.84,
      "sessionId": "thread-agent-cli"
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )
    target_dir = contract_tmp_path / "hcms-target"

    result = run_memory_migration(
        source_system="agentmemory",
        source_path=source_file,
        target_dir=target_dir,
        namespace="global/default",
    )
    state = FileMemoryStore(target_dir).load("global/default")

    assert result.source_system == "agentmemory"
    assert result.total_seen == 1
    assert result.migrated_count == 1
    assert result.written_count == 1
    assert result.validation_valid is True
    assert state.memories[0].memory_id == "mem_agent_cli"
    assert state.memories[0].source_thread_id == "thread-agent-cli"
    assert KnowledgeCompiler.validate_markdown_schema(state.memories[0].content).valid


def test_hcms_migration_runner_reads_legacy_anvil_sqlite_db(contract_tmp_path) -> None:
    source_db = contract_tmp_path / "curated.db"
    with sqlite3.connect(source_db) as conn:
        conn.execute(
            "CREATE TABLE curated_entries (entry_id TEXT, content TEXT, category TEXT, confidence REAL, salience REAL, state TEXT)"
        )
        conn.execute(
            "CREATE TABLE profile_facets (facet_id TEXT, class_id TEXT, key TEXT, value TEXT, stability_score REAL, state TEXT, prompt_visible INTEGER)"
        )
        conn.execute(
            "INSERT INTO curated_entries VALUES (?, ?, ?, ?, ?, ?)",
            ("entry_release", "Legacy Anvil release fact requires canary.", "knowledge", 0.9, 0.8, "active"),
        )
        conn.execute(
            "INSERT INTO profile_facets VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("facet_style", "style", "updates", "User prefers concise progress updates.", 1.2, "active", 1),
        )

    result = run_memory_migration(
        source_system="anvil",
        source_path=source_db,
        target_dir=contract_tmp_path / "hcms-target-db",
        namespace="global/default",
    )

    assert result.total_seen == 2
    assert result.migrated_count == 2
    assert result.written_count == 2
    assert set(result.memory_ids) == {"mem_release", "mem_style"}
