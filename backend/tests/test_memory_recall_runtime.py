from __future__ import annotations

import sqlite3
from pathlib import Path

from anvil.config import MemoryPlatformProviderConfig
from anvil.config import EffectiveConfig, MemoryPlatformRecallConfig, MemoryPlatformSessionSearchConfig, ModelConfig
from anvil.memory_platform.archive import SqliteSessionArchive
from anvil.memory_platform.contracts import RecallEvidence, ReflectionJob, ReflectionScheduleKind
from anvil.memory_platform.curated import CuratedStoreManager, JsonCuratedStoreRepository
from anvil.memory_platform.guard import MemoryGuard
from anvil.memory_platform.prompt_snapshots import PromptSnapshotStore
from anvil.memory_platform.provider_runtime import ProviderRuntime
from anvil.memory_platform.providers import PROVIDER_TEMPLATES, ProviderRegistry
from anvil.memory_platform.recall import RecallPlanner, SessionSearchService
from anvil.memory_platform.reflection_service import ReflectionService
from anvil.memory_platform.retrieval_index import RetrievalIndexStore
from anvil.memory_platform.trace import MemoryTraceStore
from anvil.memory_platform.write_service import MemoryWriteService
from anvil.config import MemoryPlatformStoreConfig


def build_runtime(tmp_path: Path):
    curated = CuratedStoreManager(
        store_configs={
            "runtime_memory": MemoryPlatformStoreConfig(display_name="Runtime Memory"),
            "user_profile": MemoryPlatformStoreConfig(display_name="User Profile"),
        },
        repository=JsonCuratedStoreRepository(tmp_path / "curated"),
    )
    archive = SqliteSessionArchive(tmp_path / "archive.sqlite3")
    prompt_snapshots = PromptSnapshotStore(tmp_path / "prompt-snapshots")
    provider_runtime = ProviderRuntime(registry=ProviderRegistry(active_provider_id="anvil_factgraph"))
    retrieval_index = RetrievalIndexStore(tmp_path / "retrieval-index.sqlite3")
    trace_store = MemoryTraceStore(tmp_path / "memory-trace.sqlite3")
    write_service = MemoryWriteService(
        curated_store_manager=curated,
        guard=MemoryGuard(),
        retrieval_index=retrieval_index,
        provider_runtime=provider_runtime,
        trace_store=trace_store,
    )
    session_search = SessionSearchService(
        archive=archive,
        retrieval_index=retrieval_index,
        prompt_snapshot_store=prompt_snapshots,
        trace_store=trace_store,
    )
    planner = RecallPlanner(
        curated_store_manager=curated,
        archive=archive,
        retrieval_index=retrieval_index,
        provider_runtime=provider_runtime,
        trace_store=trace_store,
    )
    reflection = ReflectionService(
        archive=archive,
        curated_store_manager=curated,
        session_search_service=session_search,
        write_service=write_service,
    )
    return curated, archive, prompt_snapshots, provider_runtime, retrieval_index, trace_store, write_service, session_search, planner, reflection


def test_recall_planner_builds_summary_and_trace(contract_tmp_path) -> None:
    _, archive, _, _, _, trace_store, write_service, _, planner, _ = build_runtime(contract_tmp_path / "recall-runtime")

    entry = write_service.create_entry(
        "runtime_memory",
        content="Northstar is the active codename.",
        category="project_context",
        source_kind="manual",
    )
    record = archive.record_turn(
        thread_id="thread-a",
        user_content="Remember that Northstar shipped in the prior thread.",
        assistant_content="Stored the Northstar thread context.",
        status="completed",
    )
    write_service.index_archive_turn(record)

    plan = planner.build(
        query="Northstar",
        thread_id="thread-b",
        stable_snapshot="snapshot text",
    )
    traces = trace_store.list_traces(thread_id="thread-b")

    assert entry.memory_id is not None
    assert plan.summary
    assert plan.evidence
    assert all(item.final_score is not None for item in plan.evidence)
    assert all(item.match_score is not None for item in plan.evidence)
    assert traces
    assert traces[0].trace_kind == "recall"
    assert all("semantic" not in item.source_kind for item in plan.evidence)


def test_retrieval_index_uses_fts_schema_without_vector_json(contract_tmp_path) -> None:
    _, archive, _, _, retrieval_index, _, write_service, _, _, _ = build_runtime(contract_tmp_path / "fts-runtime")

    entry = write_service.create_entry(
        "runtime_memory",
        content="Northstar rollout decisions live in the workspace memory.",
        category="project_context",
        source_kind="manual",
    )
    record = archive.record_turn(
        thread_id="thread-fts",
        user_content="Northstar rollout used the FTS memory index.",
        assistant_content="Recorded the indexed recall evidence.",
        status="completed",
    )
    write_service.index_archive_turn(record)

    memory_hits = retrieval_index.search_memory("Northstar rollout", limit=2)
    archive_hits = retrieval_index.search_archive("Northstar rollout", limit=2)

    with sqlite3.connect(retrieval_index.sqlite_path) as conn:
        memory_columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_index)")}
        archive_columns = {row[1] for row in conn.execute("PRAGMA table_info(archive_index)")}

    assert "vector_json" not in memory_columns
    assert "vector_json" not in archive_columns
    assert memory_hits[0]["memory_id"] == entry.memory_id
    assert archive_hits[0]["archive_id"] == record.archive_id


class _FocusedSummaryStub:
    def __init__(self) -> None:
        self.calls = 0

    def summarize(self, request) -> str:
        self.calls += 1
        return f"LLM focused summary for {request.query}"


def test_session_search_modes_control_focused_summary_calls(contract_tmp_path) -> None:
    _, archive, prompt_snapshots, _, retrieval_index, trace_store, write_service, _, _, _ = build_runtime(contract_tmp_path / "session-modes")
    record = archive.record_turn(
        thread_id="thread-summary",
        user_content="Northstar session search needs focused summaries.",
        assistant_content="Implemented summarize mode with evidence.",
        status="completed",
    )
    write_service.index_archive_turn(record)
    summary_service = _FocusedSummaryStub()
    session_search = SessionSearchService(
        archive=archive,
        retrieval_index=retrieval_index,
        prompt_snapshot_store=prompt_snapshots,
        trace_store=trace_store,
        summary_service=summary_service,
    )

    search_only = session_search.search(query="Northstar", mode="search", scope="all", limit=1)
    summarized = session_search.search(query="Northstar", mode="summarize", scope="all", limit=1)
    recent = session_search.search(query="", mode="summarize", scope="all", limit=1)

    assert "LLM focused summary" not in search_only[0].summary
    assert summarized[0].summary == "LLM focused summary for Northstar"
    assert recent[0].summary
    assert summary_service.calls == 1


def test_model_rerank_disables_thinking_and_marks_internal_invocation(contract_tmp_path, monkeypatch) -> None:
    _, archive, _, provider_runtime, retrieval_index, trace_store, _, _, _, _ = build_runtime(contract_tmp_path / "model-rerank")
    calls = []

    class CapturingModel:
        def invoke(self, prompt, config=None):
            calls.append({"prompt": prompt, "config": config})

            class Response:
                content = '{"ev-hello": 0.9}'

            return Response()

    def fake_create_chat_model(model_config, **kwargs):
        calls.append({"model_config": model_config, "kwargs": kwargs})
        return CapturingModel()

    monkeypatch.setattr("anvil.memory_platform.recall.create_chat_model", fake_create_chat_model)
    planner = RecallPlanner(
        curated_store_manager=CuratedStoreManager(
            store_configs={},
            repository=JsonCuratedStoreRepository(contract_tmp_path / "model-rerank" / "curated-empty"),
        ),
        archive=archive,
        retrieval_index=retrieval_index,
        provider_runtime=provider_runtime,
        trace_store=trace_store,
        config=MemoryPlatformRecallConfig(enable_model_rerank=True, rerank_model_name="memory-reranker"),
        effective_config=EffectiveConfig(
            default_model="memory-reranker",
            models={
                "memory-reranker": ModelConfig(
                    name="memory-reranker",
                    provider_kind="openai_compatible",
                    model="gpt-test",
                    supports_thinking=True,
                )
            },
            memory_platform={"session_search": MemoryPlatformSessionSearchConfig(model_name="memory-reranker")},
        ),
    )
    evidence = [
        RecallEvidence(
            evidence_id="ev-hello",
            source_kind="archive",
            source_id="archive",
            score=1.0,
            final_score=1.0,
            excerpt="你好",
        )
    ]

    ranked = planner._model_rerank(query="你好", evidence=evidence)

    assert ranked[0].rerank_score == 0.9
    assert calls[0]["kwargs"]["thinking_enabled"] is False
    assert calls[1]["config"]["metadata"] == {
        "anvil_internal": True,
        "anvil_internal_kind": "memory_rerank",
    }
    assert "anvil_internal_memory_rerank" in calls[1]["config"]["tags"]


def test_provider_runtime_dispatches_hooks_by_role(contract_tmp_path) -> None:
    archive = SqliteSessionArchive(contract_tmp_path / "providers" / "archive.sqlite3")
    catalog = {
        template.provider_id: MemoryPlatformProviderConfig(configured=True, roles=())
        for template in PROVIDER_TEMPLATES
    }
    catalog["anvil_factgraph"] = MemoryPlatformProviderConfig(configured=True, roles=("sync",))
    catalog["anvil_hybrid"] = MemoryPlatformProviderConfig(configured=True, roles=("index",))
    catalog["anvil_tree"] = MemoryPlatformProviderConfig(enabled=False, configured=True, roles=("index",))
    provider_runtime = ProviderRuntime(
        registry=ProviderRegistry(
            active_provider_id="anvil_factgraph",
            catalog=catalog,
        )
    )
    record = archive.record_turn(
        thread_id="thread-provider",
        user_content="Northstar provider role boundaries.",
        assistant_content="Provider hooks were separated.",
        status="completed",
    )

    notes = provider_runtime.index_write(record=record)
    sync_providers = provider_runtime.registry.providers_for_role("sync")
    index_providers = provider_runtime.registry.providers_for_role("index")

    assert provider_runtime.active_provider_id == "anvil_factgraph"
    assert [provider.manifest().provider_id for provider in sync_providers] == ["anvil_factgraph"]
    assert notes == (f"Anvil Hybrid indexed archive turn {record.archive_id}.",)
    assert all(provider.manifest().provider_id != "anvil_tree" for provider in index_providers)


def test_reflection_service_generates_artifacts_with_evidence(contract_tmp_path) -> None:
    _, archive, _, _, _, _, write_service, session_search, _, reflection = build_runtime(contract_tmp_path / "reflection-runtime")

    record = archive.record_turn(
        thread_id="thread-a",
        user_content="I prefer terse updates about Northstar.",
        assistant_content="Understood.",
        status="completed",
    )
    write_service.index_archive_turn(record)

    preference_job = ReflectionJob(
        job_id="job-pref",
        name="Preference Extraction",
        schedule_kind=ReflectionScheduleKind.ONCE,
        target_store_id="user_profile",
        template="preference_extraction",
    )
    recap_job = ReflectionJob(
        job_id="job-recap",
        name="Project Recap",
        schedule_kind=ReflectionScheduleKind.ONCE,
        target_store_id="runtime_memory",
        template="project_recap",
        source_query="Northstar",
    )

    preference_result = reflection.run_job(preference_job)
    recap_result = reflection.run_job(recap_job)
    summaries = session_search.search(query="Northstar", current_thread_id=None, scope="all", limit=3)

    assert preference_result.artifacts
    assert preference_result.artifacts[0].evidence_refs
    assert recap_result.artifacts
    assert summaries
    assert summaries[0].summary


def test_reflection_service_rejects_one_off_instruction_as_preference(contract_tmp_path) -> None:
    _, archive, _, _, _, _, write_service, _session_search, _, reflection = build_runtime(contract_tmp_path / "reflection-noise")

    record = archive.record_turn(
        thread_id="thread-exact",
        user_content="Reply with exactly OK. Do not use tools.",
        assistant_content="OK",
        status="completed",
    )
    write_service.index_archive_turn(record)

    preference_job = ReflectionJob(
        job_id="job-pref-noise",
        name="Preference Extraction",
        schedule_kind=ReflectionScheduleKind.ONCE,
        target_store_id="user_profile",
        template="preference_extraction",
    )

    result = reflection.run_job(preference_job)

    assert result.status == "noop"
    assert result.entries_written == 0
    assert result.artifacts == ()
