from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from anvil.config import ContextFilesConfig, HCMSRuntimeConfig
from anvil.agents.lead_agent.context_files import build_project_context_snapshot, reset_project_context_snapshot_cache
from anvil.memory import (
    BM25Retriever,
    CausalEdge,
    CausalType,
    CaptureSignalProfile,
    DebouncedMemoryQueue,
    DeterministicVectorRetriever,
    Evidence,
    EvidenceType,
    FileMemoryStore,
    FourStreamRetriever,
    GraphRetriever,
    HeuristicMemoryUpdater,
    KnowledgeCompiler,
    Memory,
    MemoryCaptureEnvelope,
    MemoryCategory,
    MemoryLifecycleState,
    MemoryManager,
    MemoryState,
    MultiLevelCompressor,
    QueryIntent,
    Relation,
    RelationType,
    RetrievalConfig,
    MemoryService,
    SourceType,
    TemporalCausalRetriever,
    utc_now,
)
from anvil.memory.contracts import ForgettingConfig
from anvil.memory.signals import detect_capture_signals
from anvil.memory.updater import (
    RuleBasedMemoryUpdater,
    StructuredMemoryUpdater,
    build_structured_update_prompt,
    parse_structured_update_response,
)
from anvil.memory.hcms_v2 import (
    CapabilityUsageEvent,
    ClaimRecord,
    ConflictLedger,
    HCMSV2RuntimeBridge,
    memory_search_result_to_context_block,
)
from anvil.runtime.state_v2 import ReviewInbox, ToolResultStore, WorkspaceState, tool_result_record_to_event
from anvil.sandbox import PathService


def make_hcms(contract_tmp_path):
    return MemoryService(
        store=FileMemoryStore(contract_tmp_path / "hcms-store"),
        queue=DebouncedMemoryQueue(min_window_seconds=5, default_window_seconds=30, max_window_seconds=60),
        updater=HeuristicMemoryUpdater(max_facts=20),
        max_facts=20,
        injection_token_budget=400,
    )


class FailingMemoryUpdater:
    def update(self, current_state: MemoryState, envelope: MemoryCaptureEnvelope) -> MemoryState:
        raise RuntimeError("temporary updater failure")


def test_hcms_manager_uses_configured_hybrid_storage_backend(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(enabled=True, storage_backend="hybrid"),
        base_path=contract_tmp_path / "runtime",
    )

    manager.hcms_service.create_memory(
        "global/default",
        content="Hybrid backend selection stores searchable Markdown and SQLite index data.",
        category="project_context",
        confidence=0.9,
        salience=0.8,
    )

    assert "global/default" in manager.hcms_service.store.list_namespaces()
    assert (contract_tmp_path / "runtime" / "hcms" / "memories").exists()
    assert (contract_tmp_path / "runtime" / "hcms" / "index.sqlite3").exists()


def test_hcms_manager_uses_recall_cache_and_mmr_config(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            recall={
                "max_candidates": 7,
                "turn_recall_token_budget": 320,
                "bm25_weight": 0.11,
                "vector_weight": 0.22,
                "graph_weight": 0.33,
                "temporal_weight": 0.44,
                "rrf_k": 37,
                "enable_adaptive_weights": False,
                "enable_cache": True,
                "cache_ttl": 9,
                "cache_max_entries": 2,
                "enable_mmr": False,
                "mmr_lambda": 0.41,
            },
        ),
        base_path=contract_tmp_path / "runtime",
    )

    retriever_config = manager.hcms_service.retriever.config

    assert manager.hcms_service.max_facts == 7
    assert manager.hcms_service.injection_token_budget == 320
    assert retriever_config.default_limit == 7
    assert retriever_config.bm25_weight == 0.11
    assert retriever_config.vector_weight == 0.22
    assert retriever_config.graph_weight == 0.33
    assert retriever_config.temporal_weight == 0.44
    assert retriever_config.rrf_k == 37
    assert retriever_config.enable_adaptive_weights is False
    assert retriever_config.enable_cache is True
    assert retriever_config.cache_ttl == 9
    assert retriever_config.cache_max_entries == 2
    assert retriever_config.enable_mmr is False
    assert retriever_config.mmr_lambda == 0.41


def test_hcms_manager_sync_conflict_alerts_adds_runtime_warning_to_review_inbox(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(enabled=True, storage_backend="filesystem"),
        base_path=contract_tmp_path / "runtime",
    )
    previous = ClaimRecord(
        claim_id="claim-memory-direct-yes",
        namespace="global/default",
        subject="runtime_memory",
        predicate="direct_append",
        object_value="true",
        human_text="Runtime memory is appended directly to the model prompt.",
    )
    correction = ClaimRecord(
        claim_id="claim-memory-direct-no",
        namespace="global/default",
        subject="runtime_memory",
        predicate="direct_append",
        object_value="false",
        human_text="Runtime memory enters ContextBlock budget competition.",
        source_priority=90,
    )
    conflict = ConflictLedger().detect_exact_conflicts([previous, correction])[0]
    inbox = ReviewInbox(inbox_id="review-inbox-conflict-sync", thread_id="thread-conflict-sync")

    synced = manager.sync_conflict_alerts(inbox, conflicts=[conflict], namespace="global/default")
    duplicate = manager.sync_conflict_alerts(inbox, conflicts=[conflict], namespace="global/default")

    assert len(synced) == 1
    assert len(duplicate) == 1
    assert len(inbox.items) == 1
    item = inbox.items[0]
    warning = inbox.to_context_blocks()[0]
    assert item.conflict_id == conflict.conflict_id
    assert item.review_inbox_id == conflict.review_inbox_id
    assert item.severity == "high"
    assert item.injection_policy == "inject_warning"
    assert warning.block_type == "runtime_warning"
    assert warning.injection_policy.requires_warning is True
    assert warning.conflict_state == "unresolved"
    assert warning.source.ref == conflict.review_inbox_id
    assert inbox.diagnostics["hcms_conflict_alert_sync"]["status"] == "synced"
    assert inbox.diagnostics["hcms_conflict_alert_sync"]["synced_count"] == 1
    assert manager.list_traces(limit=1)[0].trace_kind == "hcms_v2_conflict_alert_sync"


def test_hcms_manager_uses_configured_structured_updater_with_zero_llm_fallback(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            updater={
                "mode": "structured",
                "fact_confidence_threshold": 0.7,
                "fail_open": True,
            },
        ),
        base_path=contract_tmp_path / "runtime",
    )

    assert isinstance(manager.hcms_service.updater, StructuredMemoryUpdater)
    assert manager.hcms_service.updater.fallback_to_rules is True
    assert manager.hcms_service.updater.response_provider is None

    manager.record_turn(
        thread_id="thread-structured-fallback",
        user_content="Actually I prefer Python instead of JavaScript for backend work.",
        assistant_content="Noted.",
    )
    state = manager.hcms_service.prefetch("global/default")

    assert any("Python" in memory.content for memory in state.memories)
    assert state.metrics.deterministic_updates >= 1
    assert state.metrics.llm_calls_avoided >= 1


def test_hcms_manager_turn_capture_trace_exposes_hcms_v2_capture_evidence(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(enabled=True, storage_backend="filesystem"),
        base_path=contract_tmp_path / "runtime",
    )

    manager.record_turn(
        thread_id="thread-trace-capture",
        user_content="Remember that Northstar prefers terse release updates.",
        assistant_content="Stored.",
    )

    trace = manager.list_traces(thread_id="thread-trace-capture", limit=1)[0]
    assert trace.trace_kind == "hcms_capture"
    assert trace.evidence
    assert trace.evidence[0]["capture_envelope_id"].startswith("capture_v2_")
    assert trace.evidence[0]["event_type"] == "user_message"
    assert trace.evidence[0]["source_id"]


def test_hcms_manager_routes_detected_corrections_to_user_store(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(enabled=True, storage_backend="filesystem"),
        base_path=contract_tmp_path / "runtime",
    )

    manager.record_turn(
        thread_id="thread-correction-user-store",
        user_content="Actually, prefer concise project updates.",
        assistant_content="Stored for later.",
    )

    user_entries = manager.list_entries("hcms_user")
    workspace_entries = manager.list_entries("hcms_workspace")
    correction = next(
        memory
        for memory in user_entries
        if memory.category == MemoryCategory.CORRECTION
        and "Actually, prefer concise project updates." in memory.content
    )

    assert correction.metadata["layer_id"] == "user"
    assert correction.metadata["store_id"] == "hcms_user"
    assert correction.layer_id == "user"
    assert correction.store_id == "hcms_user"
    assert all(memory.memory_id != correction.memory_id for memory in workspace_entries)


def test_hcms_memory_store_id_fallback_matches_category_derived_layer() -> None:
    correction = Memory(
        content="Actually, the user prefers concise updates.",
        category=MemoryCategory.CORRECTION,
    )
    layer_only = Memory(
        content="User layer metadata without a concrete store id.",
        category=MemoryCategory.NOTE,
        metadata={"layer_id": "user"},
    )
    workspace = Memory(
        content="Workspace convention without metadata.",
        category=MemoryCategory.PROJECT_CONTEXT,
    )

    assert correction.layer_id == "user"
    assert correction.store_id == "hcms_user"
    assert layer_only.layer_id == "user"
    assert layer_only.store_id == "hcms_user"
    assert workspace.layer_id == "workspace"
    assert workspace.store_id == "hcms_workspace"


def test_hcms_manager_record_turn_preserves_runtime_event_refs_with_source_metadata(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(enabled=True, storage_backend="filesystem"),
        base_path=contract_tmp_path / "runtime",
    )

    manager.record_turn(
        thread_id="thread-v2-capture-metadata",
        user_content="Low signal turn should stay pending.",
        assistant_content="Recorded.",
        source_metadata={"workspace_state_ref": "workspace-state-1"},
    )

    pending = manager.hcms_service.queue.get_pending("global/default")
    assert len(pending) == 1
    metadata = pending[0].metadata
    assert metadata["workspace_state_ref"] == "workspace-state-1"
    assert [item["event_type"] for item in metadata["runtime_event_refs"]] == ["user_message", "assistant_message"]


def test_hcms_manager_capture_runtime_event_persists_tool_result_as_episodic_memory(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(enabled=True, storage_backend="filesystem"),
        base_path=contract_tmp_path / "runtime",
    )
    raw_ref = "artifact://thread-tool/outputs/tool-results/list-dir.json"
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "status": "completed",
                "output": "evidence.txt found",
                "raw_output_artifact_url": raw_ref,
                "_tool_output_budget": {
                    "truncated": True,
                    "original_chars": len("SECRET RAW OUTPUT " * 100),
                    "artifact_url": raw_ref,
                    "compaction": {"raw_artifact_url": raw_ref},
                },
            }
        ),
        name="list_dir",
        tool_call_id="call-list",
    )
    workspace = WorkspaceState(workspace_id="workspace-thread-tool", thread_id="thread-tool")
    store = ToolResultStore(thread_id="thread-tool")
    record = store.ingest_tool_message(
        tool_message,
        tool_name="list_dir",
        run_id="run-tool",
        turn_id="turn-tool",
        workspace_state=workspace,
    )
    event = tool_result_record_to_event(
        record,
        thread_id="thread-tool",
        workspace_refs=[record.workspace_ref],
        trace_id="trace-tool",
    )

    capture = manager.capture_runtime_event(event, namespace="global/default")

    state = manager.hcms_service.prefetch("global/default")
    memories = [
        memory
        for memory in state.memories
        if memory.metadata.get("hcms_v2_observation_id") == capture.observation.observation_id
    ]
    assert len(memories) == 1
    memory = memories[0]
    assert memory.category == MemoryCategory.CONTEXT
    assert memory.source_type == SourceType.TOOL
    assert memory.source_thread_id == "thread-tool"
    assert memory.metadata["layer"] == "episodic"
    assert memory.metadata["hcms_layer"] == "episodic"
    assert memory.metadata["layer_id"] == "episodic"
    assert memory.metadata["capture_envelope_id"] == capture.envelope.envelope_id
    assert memory.metadata["hcms_v2_observation_id"] == capture.observation.observation_id
    assert memory.metadata["event_id"] == event.event_id
    assert memory.metadata["event_type"] == "tool_result"
    assert memory.metadata["tool_result_refs"] == [record.result_id]
    assert memory.metadata["workspace_refs"] == [record.workspace_ref]
    assert memory.metadata["content_ref"] == raw_ref
    assert memory.metadata["tool_name"] == "list_dir"
    assert memory.metadata["tool_call_id"] == "call-list"
    assert "evidence.txt found" in memory.summary
    assert "SECRET RAW OUTPUT" not in memory.content
    assert memory.evidence
    assert memory.evidence[0].source_id == capture.observation.observation_id
    assert memory.evidence[0].metadata["tool_result_refs"] == [record.result_id]
    assert memory.evidence[0].metadata["workspace_refs"] == [record.workspace_ref]

    results = manager.hcms_service.search("global/default", "evidence.txt list_dir", limit=5)
    retrieval = next(result for result in results if result.memory_id == memory.memory_id)
    v2_result = HCMSV2RuntimeBridge().search_result_from_retrieval_result(retrieval, namespace="global/default")
    assert v2_result.layer == "episodic"


def test_hcms_service_mines_capability_usage_events_as_procedural_and_wisdom_memory(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    raw_success_output = "RAW_CAPABILITY_OUTPUT " * 80
    raw_failure_output = "RAW_BROWSER_OUTPUT " * 80
    success_event = CapabilityUsageEvent(
        usage_id="usage-success-1",
        capability_id="builtin:core:shell_command",
        capability_kind="tool",
        tool_name="shell_command",
        skill_ids=["test-driven-development"],
        turn_id="turn-capability-1",
        goal_id="goal-capability-mining",
        input_summary="Run focused pytest for HCMS V2 capability mining.",
        output_summary=f"pytest passed. {raw_success_output}",
        status="success",
        latency_ms=250,
        verification_signal="tests_passed",
        context_block_refs=["ctx:test", "ctx:memory"],
    )
    failure_event = CapabilityUsageEvent(
        usage_id="usage-failure-1",
        capability_id="mcp:browser:navigate",
        capability_kind="mcp_tool",
        tool_name="browser_navigate",
        mcp_server_id="browser",
        skill_ids=["browser:control-in-app-browser"],
        turn_id="turn-capability-2",
        goal_id="goal-capability-mining",
        input_summary="Open local preview for visual validation.",
        output_summary=f"navigation timed out. {raw_failure_output}",
        status="failed",
        error_type="timeout",
        verification_signal="failed",
        context_block_refs=["ctx:browser"],
    )

    batch = service.mine_capability_usage_events_v2(
        [success_event, failure_event],
        namespace="global/default",
    )

    assert batch.event_count == 2
    assert len(batch.results) == 2
    assert batch.diagnostics["procedure_count"] >= 1
    assert batch.diagnostics["wisdom_count"] >= 2
    assert len(batch.persisted_memory_ids) >= 3

    state = service.prefetch("global/default")
    persisted = [memory for memory in state.memories if memory.memory_id in batch.persisted_memory_ids]
    assert {memory.metadata["hcms_layer"] for memory in persisted} >= {"procedural", "wisdom"}
    assert any(memory.category == MemoryCategory.PROCEDURE for memory in persisted)
    assert any(memory.category == MemoryCategory.ERROR_PATTERN for memory in persisted)
    assert any(memory.metadata["hcms_v2_capability_usage_id"] == "usage-success-1" for memory in persisted)
    assert any(memory.metadata["hcms_v2_capability_usage_id"] == "usage-failure-1" for memory in persisted)
    assert any(memory.metadata["context_block_refs"] == ["ctx:test", "ctx:memory"] for memory in persisted)
    assert any(memory.metadata["skill_ids"] == ["test-driven-development"] for memory in persisted)
    dumped_state = "\n".join(memory.model_dump_json() for memory in persisted)
    assert "RAW_CAPABILITY_OUTPUT" not in dumped_state
    assert "RAW_BROWSER_OUTPUT" not in dumped_state

    retrieval = service.search("global/default", "shell_command tests_passed browser timeout", limit=10)
    bridge = HCMSV2RuntimeBridge()
    v2_results = [
        bridge.search_result_from_retrieval_result(result, namespace="global/default")
        for result in retrieval
        if result.memory is not None and result.memory.memory_id in batch.persisted_memory_ids
    ]
    blocks = [memory_search_result_to_context_block(result) for result in v2_results]
    assert any(block.block_type == "procedural_hint" for block in blocks)
    assert any(block.block_type == "wisdom_warning" for block in blocks)
    assert all("RAW_" not in block.content for block in blocks)


def test_hcms_manager_sync_workspace_state_persists_working_memory(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(enabled=True, storage_backend="filesystem"),
        base_path=contract_tmp_path / "runtime",
    )
    workspace_state = WorkspaceState(
        workspace_id="workspace:thread-working-memory",
        thread_id="thread-working-memory",
        project_root=str(contract_tmp_path / "workspace"),
        active_files=["src/main.py"],
        variables={"current_batch": "Batch C"},
    )
    tool_store = ToolResultStore(thread_id="thread-working-memory")
    record = tool_store.ingest_tool_message(
        ToolMessage(
            content="1 passed in 0.10s SECRET_TOKEN=workspace-raw-secret",
            tool_call_id="call-working-memory-1",
            name="pytest",
        ),
        tool_name="pytest",
        run_id="run-working-memory",
        turn_id="turn-working-memory",
        workspace_state=workspace_state,
    )

    memory = manager.sync_workspace_state(workspace_state, namespace="global/default")

    assert memory is not None
    assert memory.category == MemoryCategory.CONTEXT
    assert memory.source_type == SourceType.OBSERVATION
    assert memory.source_thread_id == "thread-working-memory"
    assert memory.metadata["layer"] == "working"
    assert memory.metadata["hcms_layer"] == "working"
    assert memory.metadata["layer_id"] == "working"
    assert memory.metadata["store_id"] == "hcms_working"
    assert memory.metadata["workspace_state_ref"] == "workspace:thread-working-memory"
    assert memory.metadata["thread_id"] == "thread-working-memory"
    assert memory.metadata["active_file_count"] == 1
    assert memory.metadata["variable_count"] == 1
    assert memory.metadata["intermediate_result_count"] == 1
    assert memory.metadata["source"] == "workspace_state"
    assert "src/main.py" in memory.content
    assert "current_batch" in memory.content
    assert workspace_state.intermediate_results[0].result_ref in memory.content
    if record.raw_ref is not None:
        assert record.raw_ref in memory.content
    assert "1 passed" in memory.summary or "1 passed" in memory.content
    assert "workspace-raw-secret" not in memory.content
    assert "workspace-raw-secret" not in memory.summary

    first_memory_id = memory.memory_id
    workspace_state.variables["current_batch"] = "Batch C follow-up"
    updated = manager.sync_workspace_state(workspace_state, namespace="global/default")
    state = manager.hcms_service.prefetch("global/default")
    working_memories = [
        item
        for item in state.memories
        if item.metadata.get("workspace_state_ref") == "workspace:thread-working-memory"
        and item.metadata.get("layer") == "working"
    ]

    assert updated is not None
    assert updated.memory_id == first_memory_id
    assert len(working_memories) == 1
    assert "Batch C follow-up" in working_memories[0].content


def test_hcms_manager_structured_updater_consumes_injected_provider_response(contract_tmp_path) -> None:
    calls: list[str] = []

    def provider(_state, _envelope, prompt: str) -> str:
        calls.append(prompt)
        return """
{
  "newFacts": [
    {
      "content": "Provider planned memory updates must be applied.",
      "category": "decision",
      "confidence": 0.91,
      "evidence": "Injected provider response"
    }
  ],
  "updates": [],
  "removals": []
}
"""

    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            updater={
                "mode": "structured",
                "fact_confidence_threshold": 0.7,
                "fail_open": False,
            },
        ),
        base_path=contract_tmp_path / "runtime",
        structured_update_provider=provider,
    )

    manager.record_turn(
        thread_id="thread-structured-provider",
        user_content="Remember that provider planned updates are required.",
        assistant_content="Noted.",
    )
    state = manager.hcms_service.prefetch("global/default")

    assert calls
    assert "thread-structured-provider" in calls[0]
    memory = state.memories[0]
    validation = KnowledgeCompiler.validate_markdown_schema(memory.content)
    assert validation.valid, validation.errors
    assert memory.content.startswith("---\n")
    assert "Provider planned memory updates must be applied." in memory.content
    assert "Injected provider response" in memory.content
    assert "source_thread_id: thread-structured-provider" in memory.content
    assert memory.source_thread_id == "thread-structured-provider"
    assert state.metrics.deterministic_updates == 1
    assert state.metrics.llm_calls_avoided == 0


def test_hcms_manager_applies_recall_relevance_and_evidence_limits(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            recall={
                "max_candidates": 5,
                "max_evidence": 2,
                "min_relevance_score": 0.95,
                "turn_recall_token_budget": 500,
            },
        ),
        base_path=contract_tmp_path / "runtime",
    )
    strong = manager.hcms_service.create_memory(
        "global/default",
        content="Northstar canary verification protects production release safety.",
        category="project_context",
        confidence=0.95,
        salience=0.9,
        evidence_text="Northstar canary verification is the release safety rule.",
    )
    manager.hcms_service.create_memory(
        "global/default",
        content="Unrelated frontend palette note for a different surface.",
        category="note",
        confidence=0.8,
        salience=0.6,
        evidence_text="Palette note is unrelated.",
    )
    updated = manager.hcms_service.update_memory(
        "global/default",
        strong.memory_id,
        content="Northstar canary verification protects production release safety and rollback confidence.",
    )
    manager.hcms_service.update_memory(
        "global/default",
        updated.memory_id,
        content="Northstar canary verification protects production release safety, rollback confidence, and smoke validation.",
    )
    state = manager.hcms_service.store.load("global/default")
    stored = next(memory for memory in state.memories if memory.memory_id == strong.memory_id)
    stored.evidence.extend(
        [
            Evidence(type=EvidenceType.REINFORCEMENT, content="Rollback confidence reinforces the rule.", source_id="test"),
            Evidence(type=EvidenceType.PATTERN, content="Smoke validation confirms the canary rule.", source_id="test"),
        ]
    )
    manager.hcms_service.store.save("global/default", state)

    recall = manager.prefetch_recall(thread_id="thread-relevance", query="Northstar")

    assert recall.results
    assert all(result.score >= 0.95 for result in recall.results)
    assert all("Palette note" not in fact for fact in recall.injection.facts)
    assert len(recall.injection.evidence) == 2


def test_hcms_hybrid_storage_preserves_full_state_for_causal_reasoning(contract_tmp_path) -> None:
    config = HCMSRuntimeConfig(enabled=True, storage_backend="hybrid")
    manager = MemoryManager.from_config(config=config, base_path=contract_tmp_path / "runtime")
    cause = manager.hcms_service.create_memory(
        "global/default",
        content="Direct full rollout caused repeated release failures.",
        category="project_context",
        confidence=0.92,
        salience=0.9,
    )
    manager.hcms_service.create_memory(
        "global/default",
        content="Northstar canary verification prevents repeated release failures.",
        category="project_context",
        confidence=0.93,
        salience=0.88,
    )

    first_result = manager.hcms_service.counterfactual(
        "global/default",
        "What if direct full rollout had not happened?",
        avoid="direct full rollout",
    )
    reloaded = MemoryManager.from_config(config=config, base_path=contract_tmp_path / "runtime")
    reloaded_result = reloaded.hcms_service.counterfactual(
        "global/default",
        "What if direct full rollout had not happened?",
        avoid="direct full rollout",
    )

    assert first_result.removed_memory_id == cause.memory_id
    assert first_result.impacts
    assert reloaded_result.impacts


def test_hcms_compiles_observation_into_seven_layer_state(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    envelope = service.build_capture_envelope(
        thread_id="thread-hcms",
        namespace="global/default",
        messages=[
            HumanMessage(content="Remember: Northstar uses pytest because unittest caused brittle release checks."),
            HumanMessage(content="I prefer concise implementation updates."),
        ],
    )

    service.enqueue_capture(envelope)
    assert service.process_pending() == 1

    state = service.prefetch("global/default")
    assert state.observations
    assert state.memories
    assert state.entities
    assert state.relations
    assert state.causal_edges
    assert state.versions
    assert state.metrics.llm_calls_avoided >= 1
    assert state.summary.summary.startswith("HCMS active memories")


def test_hcms_process_pending_requeues_capture_when_update_fails(contract_tmp_path) -> None:
    service = MemoryService(
        store=FileMemoryStore(contract_tmp_path / "hcms-store"),
        queue=DebouncedMemoryQueue(min_window_seconds=5, default_window_seconds=30, max_window_seconds=60),
        updater=FailingMemoryUpdater(),
        max_facts=20,
        injection_token_budget=400,
    )
    envelope = service.build_capture_envelope(
        thread_id="thread-update-retry",
        namespace="global/default",
        messages=[HumanMessage(content="Remember: Northstar retries HCMS capture after transient updater failure.")],
    )

    service.enqueue_capture(envelope)
    with pytest.raises(RuntimeError, match="temporary updater failure"):
        service.process_pending("global/default", force=True)

    pending = service.queue.get_pending("global/default")
    assert len(pending) == 1
    assert pending[0].thread_id == "thread-update-retry"
    assert pending[0].metadata["last_processing_error"] == "RuntimeError"
    assert pending[0].metadata["processing_attempts"] == 1

    service.updater = HeuristicMemoryUpdater(max_facts=20)
    assert service.process_pending("global/default", force=True) == 1
    state = service.prefetch("global/default")
    assert service.queue.pending_count() == 0
    assert any("transient updater failure" in memory.content for memory in state.memories)


def test_hcms_compiler_filters_assistant_acknowledgement_without_losing_facts(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    envelope = service.build_capture_envelope(
        thread_id="thread-assistant-ack",
        namespace="global/default",
        messages=[
            HumanMessage(content="Remember: Northstar deploys with canary verification because full rollouts failed."),
            AIMessage(content="Recorded the Northstar deployment memory."),
            AIMessage(content="The deployment failed because schema validation ran after migration."),
        ],
    )

    service.enqueue_capture(envelope)
    assert service.process_pending() == 1

    state = service.prefetch("global/default")
    summaries = [memory.summary for memory in state.memories]

    assert any("Northstar" in summary for summary in summaries)
    assert any("schema validation" in summary for summary in summaries)
    assert not any(summary == "Recorded the Northstar deployment memory." for summary in summaries)


def test_hcms_compiled_memory_content_has_frontmatter_schema_and_self_correction(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    envelope = service.build_capture_envelope(
        thread_id="thread-compiled-schema",
        namespace="global/default",
        messages=[
            HumanMessage(
                content="Remember: Northstar deploys with canary verification because full rollouts failed.",
            ),
        ],
    )

    service.enqueue_capture(envelope)
    service.process_pending()

    memory = service.prefetch("global/default").memories[0]
    validation = KnowledgeCompiler.validate_markdown_schema(memory.content)

    assert memory.content.startswith("---\n")
    assert validation.valid, validation.errors
    assert "memory_id: " in memory.content
    assert "category: " in memory.content
    assert "confidence: " in memory.content
    assert "created_at: " in memory.content
    assert "source_thread_id: thread-compiled-schema" in memory.content
    assert "observation_id: " in memory.content
    assert "## Evidence" in memory.content
    assert "## Relations" in memory.content
    assert "## Metadata" in memory.content

    corrected = KnowledgeCompiler.self_correct_markdown_schema(
        "# Project Context\n\nNorthstar deploys with canary verification.",
        memory_id="mem_schema_demo",
        category=MemoryCategory.PROJECT_CONTEXT,
        confidence=0.91,
        created_at=utc_now(),
        source_thread_id="thread-compiled-schema",
        observation_id="obs_schema_demo",
        evidence=[
            "user_stated (0.91): Northstar deploys with canary verification.",
        ],
        entities=["Northstar"],
        concepts=["canary", "verification"],
    )

    corrected_validation = KnowledgeCompiler.validate_markdown_schema(corrected)

    assert corrected_validation.valid, corrected_validation.errors
    assert corrected.startswith("---\n")
    assert "## Evidence" in corrected
    assert "## Relations" in corrected
    assert "## Metadata" in corrected


def test_hcms_preserves_documented_memory_categories(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    documented_categories = (
        "preference",
        "knowledge",
        "context",
        "behavior",
        "goal",
        "correction",
        "pattern",
    )

    for category in documented_categories:
        memory = service.create_memory(
            "global/default",
            content=f"Northstar durable {category} memory must keep its documented category.",
            category=category,
            confidence=0.86,
            salience=0.72,
        )

        assert memory.category.value == category
        assert f"category: {category}" in memory.content

    response = """{
      "newFacts": [
        {
          "content": "Northstar benchmark targets are reusable knowledge.",
          "category": "knowledge",
          "confidence": 0.91
        },
        {
          "content": "Northstar correction facts stay typed as corrections.",
          "category": "correction",
          "confidence": 0.92
        }
      ],
      "updates": [],
      "removals": []
    }"""

    plan = parse_structured_update_response(response, confidence_threshold=0.7)

    assert [fact.category for fact in plan.new_facts] == [
        MemoryCategory.KNOWLEDGE,
        MemoryCategory.CORRECTION,
    ]


def test_hcms_four_stream_recall_and_why_query_return_evidence(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    envelope = service.build_capture_envelope(
        thread_id="thread-recall",
        namespace="global/default",
        messages=[
            HumanMessage(content="Northstar deploys with canary verification."),
            HumanMessage(content="Canary verification is required because prior full rollouts caused failed releases."),
        ],
    )
    service.enqueue_capture(envelope)
    service.process_pending()

    results = service.search("global/default", "why does Northstar need canary verification", limit=5)
    paths = service.why("global/default", "why does Northstar need canary verification")
    injection = service.build_injection_view("global/default", query="Northstar canary")

    assert results
    assert results[0].raw_scores.keys() & {"bm25", "vector", "graph", "temporal"}
    assert paths
    assert injection.evidence
    assert "Northstar" in injection.render_fenced()


def test_hcms_retrieval_streams_run_independently(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    cause = service.create_memory(
        "global/default",
        content="Direct full rollout caused repeated release failures.",
        category="project_context",
        confidence=0.91,
        salience=0.86,
    )
    effect = service.create_memory(
        "global/default",
        content="Northstar canary verification prevents repeated release failures.",
        category="project_context",
        confidence=0.93,
        salience=0.88,
    )
    state = service.prefetch("global/default")
    memories = state.active_memories()
    analysis = FourStreamRetriever().analyze("why does Northstar use canary verification")

    bm25 = BM25Retriever().search(memories, "Northstar canary")
    vector = DeterministicVectorRetriever().search(memories, "canary verification failures")
    graph = GraphRetriever().search(state, memories, "direct rollout")
    temporal = TemporalCausalRetriever().search(
        state,
        memories,
        "why does Northstar use canary verification",
        analysis=analysis,
    )

    assert effect.memory_id in bm25
    assert effect.memory_id in vector
    assert cause.memory_id in graph
    assert {cause.memory_id, effect.memory_id} <= set(temporal)


def test_hcms_retrieval_fails_open_when_one_stream_errors(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    memory = service.create_memory(
        "global/default",
        content="Northstar canary verification prevents repeated release failures.",
        category="project_context",
        confidence=0.93,
        salience=0.88,
    )
    state = service.prefetch("global/default")
    retriever = FourStreamRetriever()

    def broken_bm25(memories, query):
        raise RuntimeError("bm25 index unavailable")

    retriever.bm25_retriever.search = broken_bm25

    results = retriever.retrieve(state, "Northstar canary verification", limit=3)

    assert results
    assert results[0].memory_id == memory.memory_id
    assert "bm25" not in results[0].raw_scores
    assert results[0].raw_scores.keys() & {"vector", "graph", "temporal"}
    assert state.diagnostics
    diagnostic = state.diagnostics[-1]
    assert diagnostic.component == "retrieval"
    assert diagnostic.reason == "stream_failed"
    assert diagnostic.stream_name == "bm25"
    assert diagnostic.error_type == "RuntimeError"
    assert diagnostic.count == 1
    assert state.metrics.recall_count == 1
    assert state.metrics.last_latency_ms >= 0.0


def test_hcms_temporal_query_analysis_extracts_entities_and_time_range(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    old = service.create_memory(
        "global/default",
        content="Northstar release failed after a direct rollout.",
        category="project_context",
        confidence=0.91,
        salience=0.8,
    )
    recent = service.create_memory(
        "global/default",
        content="Northstar canary verification prevented yesterday's release failure.",
        category="project_context",
        confidence=0.93,
        salience=0.85,
    )
    state = service.prefetch("global/default")
    old_memory = next(item for item in state.memories if item.memory_id == old.memory_id)
    recent_memory = next(item for item in state.memories if item.memory_id == recent.memory_id)
    old_memory.created_at = utc_now() - timedelta(days=14)
    old_memory.updated_at = old_memory.created_at
    recent_memory.created_at = utc_now() - timedelta(hours=8)
    recent_memory.updated_at = recent_memory.created_at
    service.store.save("global/default", state)

    retriever = FourStreamRetriever()
    analysis = retriever.analyze("Why did Northstar fail yesterday?")
    temporal_scores = TemporalCausalRetriever().search(
        state,
        state.active_memories(),
        "Why did Northstar fail yesterday?",
        analysis=analysis,
    )
    results = retriever.retrieve(state, "Why did Northstar fail yesterday?", limit=2)

    assert analysis.intent == QueryIntent.TEMPORAL_CAUSAL
    assert "Northstar" in analysis.entities
    assert analysis.time_range is not None
    assert analysis.filters["temporal_marker"] == "yesterday"
    assert temporal_scores[recent.memory_id] > temporal_scores[old.memory_id]
    assert results[0].memory_id == recent.memory_id


def test_hcms_query_analyzer_recognizes_documented_causal_intent_terms() -> None:
    retriever = FourStreamRetriever()
    queries = [
        "What impact did direct rollout have on release stability?",
        "What consequence followed the failed deployment?",
        "What enabled canary verification?",
        "What prevented repeated release failures?",
        "什么影响了发布稳定性？",
        "什么阻止了重复发布失败？",
    ]

    for query in queries:
        assert retriever.analyze(query).intent == QueryIntent.TEMPORAL_CAUSAL


def test_hcms_counterfactual_projects_downstream_causal_impact(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    cause = service.create_memory(
        "global/default",
        content="Direct full rollout caused repeated release failures.",
        category="project_context",
        confidence=0.92,
        salience=0.9,
    )
    effect = service.create_memory(
        "global/default",
        content="Northstar adopted canary verification because full rollouts caused release failures.",
        category="decision",
        confidence=0.94,
        salience=0.9,
    )

    result = service.counterfactual(
        "global/default",
        "What if direct full rollout had not happened?",
        avoid="direct full rollout",
        limit=5,
    )

    assert result.removed_memory_id == cause.memory_id
    assert "direct full rollout" in result.assumption.lower()
    assert result.impacts
    assert any(impact.memory_id == effect.memory_id for impact in result.impacts)
    assert any("release failures" in impact.projected_change.lower() for impact in result.impacts)
    assert result.evidence
    assert result.confidence > 0.0
    assert result.engine_notes == ["HCMS counterfactual reasoning active"]


def test_hcms_effect_query_returns_downstream_causal_path(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    cause = service.create_memory(
        "global/default",
        content="Direct full rollout caused repeated release failures.",
        category="project_context",
        confidence=0.92,
        salience=0.9,
    )
    effect = service.create_memory(
        "global/default",
        content="Repeated release failures caused adoption of canary verification.",
        category="decision",
        confidence=0.94,
        salience=0.9,
    )

    paths = service.why("global/default", "What impact did direct full rollout have?", limit=1)

    assert paths
    assert [node.memory_id for node in paths[0].nodes] == [cause.memory_id, effect.memory_id]
    assert paths[0].edges
    assert paths[0].edges[0].source_event == cause.memory_id
    assert paths[0].edges[0].target_event == effect.memory_id


def test_hcms_relation_classifier_covers_all_documented_relation_types() -> None:
    compiler = KnowledgeCompiler()

    def memory(memory_id: str, content: str, category: MemoryCategory = MemoryCategory.NOTE) -> Memory:
        return Memory(
            memory_id=memory_id,
            content=content,
            summary=content,
            category=category,
            confidence=0.9,
            salience=0.8,
        )

    cases = {
        RelationType.SIMILAR_TO: (
            memory("mem_sim_a", "Northstar uses pytest release checks.", MemoryCategory.PROCEDURE),
            memory("mem_sim_b", "Northstar uses backend pytest smoke checks.", MemoryCategory.PROCEDURE),
        ),
        RelationType.CONTRADICTS: (
            memory("mem_contra_a", "Northstar must use full rollout."),
            memory("mem_contra_b", "Northstar must not use full rollout; this contradicts prior guidance."),
        ),
        RelationType.REFINES: (
            memory("mem_refines_a", "Northstar release verification policy."),
            memory("mem_refines_b", "Northstar canary verification refines the release policy with stricter smoke checks."),
        ),
        RelationType.GENERALIZES: (
            memory("mem_general_a", "Northstar canary verification policy."),
            memory("mem_general_b", "Northstar verification guidance generalizes this rule to all release tracks."),
        ),
        RelationType.HAPPENS_BEFORE: (
            memory("mem_before_a", "Northstar design review happens before release verification."),
            memory("mem_before_b", "Northstar release verification runs after design review."),
        ),
        RelationType.HAPPENS_AFTER: (
            memory("mem_after_a", "Northstar release verification happens after staging approval."),
            memory("mem_after_b", "Northstar staging approval precedes release verification."),
        ),
        RelationType.CONCURRENT_WITH: (
            memory("mem_concurrent_a", "Northstar smoke checks run concurrently with canary monitoring."),
            memory("mem_concurrent_b", "Northstar canary monitoring happens at the same time as smoke checks."),
        ),
        RelationType.CAUSES: (
            memory("mem_causes_a", "Direct rollout causes Northstar release failures."),
            memory("mem_causes_b", "Northstar release failures appear after direct rollout."),
        ),
        RelationType.CAUSED_BY: (
            memory("mem_caused_by_a", "Northstar canary policy is caused by prior full rollout failures."),
            memory("mem_caused_by_b", "Prior full rollout failures drove the Northstar canary policy."),
        ),
        RelationType.ENABLES: (
            memory("mem_enables_a", "Northstar canary verification enables safer production release."),
            memory("mem_enables_b", "Safer production release depends on canary verification."),
        ),
        RelationType.PREVENTS: (
            memory("mem_prevents_a", "Northstar canary verification prevents repeated release failures."),
            memory("mem_prevents_b", "Repeated release failures are blocked by canary verification."),
        ),
        RelationType.PART_OF: (
            memory("mem_part_a", "Northstar canary verification is part of the release checklist."),
            memory("mem_part_b", "The release checklist covers Northstar deployment."),
        ),
        RelationType.HAS_PART: (
            memory("mem_has_part_a", "Northstar release checklist includes canary verification."),
            memory("mem_has_part_b", "Canary verification is a checklist item."),
        ),
        RelationType.INSTANCE_OF: (
            memory("mem_instance_a", "Northstar canary verification is an instance of progressive delivery."),
            memory("mem_instance_b", "Progressive delivery is the broader deployment pattern."),
        ),
        RelationType.RELATED_TO: (
            memory("mem_related_a", "Northstar release notes mention pytest verification.", MemoryCategory.PROJECT_CONTEXT),
            memory("mem_related_b", "Northstar team tracks deployment details.", MemoryCategory.NOTE),
        ),
    }

    for expected, (source, target) in cases.items():
        assert compiler.classify_relation(source, target) == expected


def test_hcms_hybrid_recall_applies_mmr_diversity(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    first = service.create_memory(
        "global/default",
        content="Northstar canary release pytest verification before production.",
        category="project_context",
        confidence=0.95,
        salience=0.9,
    )
    near_duplicate = service.create_memory(
        "global/default",
        content="Northstar canary release pytest verification before production smoke checks.",
        category="project_context",
        confidence=0.94,
        salience=0.88,
    )
    causal_context = service.create_memory(
        "global/default",
        content="Direct full rollout caused release failures; canary policy prevents repeated failures.",
        category="project_context",
        confidence=0.92,
        salience=0.87,
    )
    state = service.prefetch("global/default")

    diversified = FourStreamRetriever(RetrievalConfig(default_limit=5, enable_mmr=True, mmr_lambda=0.45)).retrieve(
        state,
        "Northstar canary release pytest verification",
        limit=3,
    )
    raw_relevance = FourStreamRetriever(RetrievalConfig(default_limit=5, enable_mmr=False)).retrieve(
        state,
        "Northstar canary release pytest verification",
        limit=3,
    )

    diversified_ids = [item.memory_id for item in diversified]
    raw_ids = [item.memory_id for item in raw_relevance]

    assert diversified_ids[0] == raw_ids[0] == first.memory_id
    assert raw_ids.index(near_duplicate.memory_id) < raw_ids.index(causal_context.memory_id)
    assert diversified_ids.index(causal_context.memory_id) < diversified_ids.index(near_duplicate.memory_id)


def test_hcms_retrieval_cache_is_lru_ttl_bounded_and_tracks_stats(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    first = service.create_memory(
        "global/default",
        content="Northstar canary verification protects release safety.",
        category="project_context",
        confidence=0.95,
        salience=0.9,
    )
    service.create_memory(
        "global/default",
        content="Backend pytest checks guard release confidence.",
        category="procedure",
        confidence=0.92,
        salience=0.85,
    )
    state = service.prefetch("global/default")
    retriever = FourStreamRetriever(RetrievalConfig(default_limit=2, cache_ttl=300, cache_max_entries=1))

    first_query = retriever.retrieve(state, "Northstar canary", limit=1)
    repeated_query = retriever.retrieve(state, "Northstar canary", limit=1)
    retriever.retrieve(state, "pytest checks", limit=1)
    retriever.retrieve(state, "Northstar canary", limit=1)
    stats = retriever.cache_stats()

    assert first_query[0].memory_id == repeated_query[0].memory_id == first.memory_id
    assert state.metrics.recall_count == 4
    current_first = next(memory for memory in state.memories if memory.memory_id == first.memory_id)
    assert current_first.access_count >= 3
    assert stats.max_entries == 1
    assert stats.size == 1
    assert stats.hits == 1
    assert stats.misses == 3
    assert stats.writes == 3
    assert stats.evictions == 2
    assert stats.hit_rate == 0.25

    expiring = FourStreamRetriever(RetrievalConfig(default_limit=2, cache_ttl=0, cache_max_entries=1))
    expiring.retrieve(state, "Northstar canary", limit=1)
    assert expiring.cache_stats().bypasses == 1

    short_ttl = FourStreamRetriever(RetrievalConfig(default_limit=2, cache_ttl=1, cache_max_entries=2))
    short_ttl.retrieve(state, "Northstar canary", limit=1)
    time.sleep(1.05)
    short_ttl.retrieve(state, "Northstar canary", limit=1)
    assert short_ttl.cache_stats().expirations == 1


def test_hcms_exports_cursor_rule_loaded_by_project_context_snapshot(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    manager = MemoryManager(service=service, state_root=contract_tmp_path / "state", config=HCMSRuntimeConfig())
    manager.create_layer_entry(
        "workspace",
        content="Northstar must keep canary verification because full rollout failed.",
        category="decision",
        thread_id="thread-cursor",
        confidence=0.94,
        salience=0.91,
        evidence_refs=("full rollout failed",),
    )
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-cursor")
    workspace.mkdir(parents=True)
    reset_project_context_snapshot_cache(max_entries=8)

    exported = manager.export_cursor_memory_rule(workspace_root=workspace, max_entries=5, max_chars=3000)
    snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-cursor",
        config=ContextFilesConfig(filenames=[], rule_globs=[".cursor/rules/*.md"], max_chars=4000),
    )

    assert exported.relative_path == ".cursor/rules/hcms-memory.md"
    assert exported.memory_count == 1
    assert snapshot.has_content is True
    assert snapshot.files[0].relative_path == ".cursor/rules/hcms-memory.md"
    assert "HCMS Memory" in snapshot.rendered
    assert "Northstar must keep canary verification" in snapshot.rendered
    assert "full rollout failed" in snapshot.rendered
    assert "version: 1" in snapshot.rendered
    assert "state: active" in snapshot.rendered
    assert "source_thread_id: thread-cursor" in snapshot.rendered
    assert "evidence_ids:" in snapshot.rendered


def test_hcms_observation_reinforcement_updates_git_like_version_chain(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    messages = [HumanMessage(content="I prefer concise implementation updates.")]

    first = service.build_capture_envelope(thread_id="thread-chain", namespace="global/default", messages=messages)
    second = service.build_capture_envelope(thread_id="thread-chain", namespace="global/default", messages=messages)
    service.enqueue_capture(first)
    service.process_pending()
    service.enqueue_capture(second)
    service.process_pending()

    state = service.prefetch("global/default")
    memory = next(item for item in state.memories if "concise implementation updates" in item.content)
    history = service.history("global/default", memory.memory_id)

    assert memory.version == 2
    assert memory.parent_id == f"{memory.memory_id}@v1"
    assert memory.supersedes == [f"{memory.memory_id}@v1"]
    assert [record.version for record in history] == [1, 2]
    assert history[-1].parent_id == f"{memory.memory_id}@v1"


def test_hcms_lifecycle_crud_version_diff_archive_restore_and_forget(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    memory = service.create_memory(
        "global/default",
        content="Use pytest for backend tests.",
        category="procedure",
        confidence=0.8,
        salience=0.7,
    )
    memory_id = memory.memory_id
    created_validation = KnowledgeCompiler.validate_markdown_schema(memory.content)
    assert created_validation.valid, created_validation.errors
    assert "source_thread_id: manual" in memory.content
    assert "Use pytest for backend tests." in memory.content

    updated = service.update_memory("global/default", memory_id, content="Use pytest -q for backend tests.")
    updated_validation = KnowledgeCompiler.validate_markdown_schema(updated.content)
    assert updated_validation.valid, updated_validation.errors
    assert "Use pytest -q for backend tests." in updated.content
    assert updated.version == 2
    assert updated.parent_id == f"{memory_id}@v1"
    assert updated.supersedes == [f"{memory_id}@v1"]
    assert "Use pytest for backend tests." in service.diff("global/default", memory_id)
    assert "Use pytest -q for backend tests." in service.diff("global/default", memory_id)
    assert service.history("global/default", memory_id)

    archived = service.archive_memory("global/default", memory_id)
    assert archived.state == MemoryLifecycleState.ARCHIVED
    assert archived.version == 3
    assert archived.parent_id == f"{memory_id}@v2"
    restored = service.restore_memory("global/default", memory_id)
    assert restored.state == MemoryLifecycleState.ACTIVE
    assert restored.version == 4
    assert restored.parent_id == f"{memory_id}@v3"
    forgotten = service.forget_memory("global/default", memory_id)
    assert forgotten.state == MemoryLifecycleState.FORGOTTEN
    assert forgotten.version == 5
    assert forgotten.supersedes[-1] == f"{memory_id}@v4"

    service.delete_memory("global/default", memory_id)
    assert memory_id not in {item.memory_id for item in service.prefetch("global/default").memories}
    assert service.history("global/default", memory_id)


def test_hcms_manual_update_recompiles_frontmatter_with_new_metadata(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    memory = service.create_memory(
        "global/default",
        content="Remember the old project note.",
        category="note",
        confidence=0.4,
    )

    updated = service.update_memory(
        "global/default",
        memory.memory_id,
        content="Use canary verification because release safety depends on it.",
        category="decision",
        confidence=0.92,
    )
    validation = KnowledgeCompiler.validate_markdown_schema(updated.content)
    history = service.history("global/default", memory.memory_id)

    assert validation.valid, validation.errors
    assert updated.category == MemoryCategory.DECISION
    assert updated.confidence == 0.92
    assert "category: decision" in updated.content
    assert "confidence: 0.92" in updated.content
    assert history[-1].metadata["category"] == "decision"
    assert history[-1].metadata["confidence"] == 0.92


def test_hcms_active_forgetting_archives_low_retention_memory(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    memory = service.create_memory(
        "global/default",
        content="Temporary low value note.",
        category="note",
        confidence=0.05,
        salience=0.05,
    )
    state = service.prefetch("global/default")
    target = next(item for item in state.memories if item.memory_id == memory.memory_id)
    target.created_at = utc_now() - timedelta(days=365)
    target.updated_at = target.created_at
    service.store.save("global/default", state)

    assert service.lifecycle.apply_forgetting(state) == (memory.memory_id,)
    archived = next(item for item in state.memories if item.memory_id == memory.memory_id)
    history = [record for record in state.versions if record.memory_id == memory.memory_id]
    assert archived.state == MemoryLifecycleState.ARCHIVED
    assert archived.version == 2
    assert archived.parent_id == f"{memory.memory_id}@v1"
    assert history[-1].reason == "auto_forget"


def test_hcms_active_forgetting_hard_deletes_expired_cold_low_importance_memory(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    service.lifecycle = service.lifecycle.__class__(
        ForgettingConfig(retention_threshold=0.15, low_importance_ttl_days=180),
    )
    stale = service.create_memory(
        "global/default",
        content="Cold low importance note should leave hot HCMS state after TTL.",
        category="note",
        confidence=0.04,
        salience=0.03,
    )
    related = service.create_memory(
        "global/default",
        content="Related durable note should remain after cold cleanup.",
        category="note",
        confidence=0.9,
        salience=0.9,
    )
    state = service.prefetch("global/default")
    stale_memory = next(item for item in state.memories if item.memory_id == stale.memory_id)
    stale_memory.state = MemoryLifecycleState.ARCHIVED
    stale_memory.created_at = utc_now() - timedelta(days=365)
    stale_memory.updated_at = stale_memory.created_at
    state.relations.append(
        Relation(
            source_memory_id=stale.memory_id,
            target_memory_id=related.memory_id,
            relation_type=RelationType.RELATED_TO,
        )
    )
    state.causal_edges.append(
        CausalEdge(
            source_event=stale.memory_id,
            target_event=related.memory_id,
            causal_type=CausalType.CONTRIBUTORY,
            evidence=[stale.memory_id, related.memory_id],
        )
    )

    assert service.lifecycle.apply_forgetting(state) == (stale.memory_id,)
    assert [memory.memory_id for memory in state.memories] == [related.memory_id]
    assert not state.relations
    assert not state.causal_edges
    assert any(record.memory_id == stale.memory_id and record.reason == "auto_delete_expired_cold" for record in state.versions)


def test_hcms_adaptive_debounce_tracks_cost_reduction(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    for index in range(10):
        envelope = service.build_capture_envelope(
            thread_id="thread-debounce",
            namespace="global/default",
            messages=[HumanMessage(content=f"Low signal continuity detail {index}")],
        )
        service.enqueue_capture(envelope)

    assert service.queue.pending_count() == 1
    assert service.queue.cost_reduction_ratio() >= 0.85

    pending = service.queue.get_pending()[0]
    assert pending.metadata["coalesced_capture_count"] == 10
    assert len(pending.user_messages) == 10

    assert service.process_pending() == 1
    state = service.prefetch("global/default")
    assert len(state.observations) == 10
    assert state.metrics.deterministic_updates == 1
    assert state.metrics.llm_calls_avoided == 10
    assert service.queue.cost_reduction_ratio() >= 0.85


def test_hcms_adaptive_debounce_uses_signal_strength_windows() -> None:
    queue = DebouncedMemoryQueue(min_window_seconds=5, default_window_seconds=30, max_window_seconds=60)
    correction = MemoryCaptureEnvelope(
        thread_id="thread-signal",
        memory_namespace="global/default",
        explicit_corrections=["Actually use Python for backend services."],
    )
    reinforcement = MemoryCaptureEnvelope(
        thread_id="thread-signal",
        memory_namespace="global/default",
        positive_reinforcement=["Python backend preference is correct."],
    )
    low_signal = MemoryCaptureEnvelope(
        thread_id="thread-signal",
        memory_namespace="global/default",
        user_messages=["tiny note"],
    )
    remember = MemoryCaptureEnvelope(
        thread_id="thread-signal",
        memory_namespace="global/default",
        user_messages=["Remember: User prefers concise updates."],
    )

    assert isinstance(queue.signal_profile(correction), CaptureSignalProfile)
    assert queue.signal_profile(correction).strength == 0.5
    assert queue.signal_profile(correction).window_seconds == 5
    assert queue.signal_profile(reinforcement).strength == 0.3
    assert queue.signal_profile(reinforcement).window_seconds == 30
    assert queue.signal_profile(remember).strength == 0.2
    assert queue.signal_profile(remember).window_seconds == 5
    assert queue.signal_profile(low_signal).strength == 0.0
    assert queue.signal_profile(low_signal).window_seconds == 60


def test_hcms_detect_capture_signals_multilingual_and_negation_safe() -> None:
    correction = detect_capture_signals("Actually, that's wrong. 应该纠正为 Python 后端。")
    reinforcement = detect_capture_signals("Exactly, that is correct. 没错，这个方案正确。")
    remember = detect_capture_signals("请记住这个偏好: keep concise updates.")
    negated = detect_capture_signals("That is not correct and not good enough.")

    assert correction.correction is True
    assert correction.reinforcement is False
    assert correction.strength == 0.5
    assert reinforcement.reinforcement is True
    assert reinforcement.correction is False
    assert reinforcement.strength == 0.3
    assert remember.remember is True
    assert remember.strength == 0.2
    assert negated.reinforcement is False


def test_hcms_adaptive_debounce_supports_async_thread_safe_enqueue() -> None:
    queue = DebouncedMemoryQueue(min_window_seconds=5, default_window_seconds=30, max_window_seconds=60)

    async def scenario() -> None:
        async def enqueue(index: int) -> None:
            await queue.enqueue_async(
                MemoryCaptureEnvelope(
                    thread_id="thread-async-signal",
                    memory_namespace="global/default",
                    user_messages=[f"Low signal async detail {index}"],
                )
            )

        await asyncio.gather(*(enqueue(index) for index in range(8)))

        pending = await queue.get_pending_async()
        assert len(pending) == 1
        assert pending[0].metadata["coalesced_capture_count"] == 8
        assert len(pending[0].user_messages) == 8

        popped = await queue.pop_next_async(force=True)
        assert popped is not None
        assert popped.metadata["coalesced_capture_count"] == 8
        assert await queue.pending_count_async() == 0

    asyncio.run(scenario())


def test_hcms_manager_record_turn_preserves_low_signal_debounce_window(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(enabled=True, storage_backend="filesystem"),
        base_path=contract_tmp_path / "runtime",
    )

    for index in range(3):
        manager.record_turn(
            thread_id="thread-debounce-runtime",
            user_content=f"Low signal continuity detail {index}",
            assistant_content="Noted.",
            status="completed",
        )

    assert manager.hcms_service.queue.pending_count() == 1
    assert manager.hcms_service.queue.get_pending()[0].metadata["coalesced_capture_count"] == 3
    assert manager.hcms_service.store.load("global/default").memories == []

    flushed = manager.flush_memory(thread_id="thread-debounce-runtime", force=True).model_dump(mode="json")
    assert flushed["observations_processed"] == 1
    state = manager.hcms_service.store.load("global/default")
    assert len(state.observations) == 3
    assert any("Low signal continuity detail" in memory.content for memory in state.memories)


def test_hcms_manager_record_turn_flushes_paused_status_without_debounce(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(enabled=True, storage_backend="filesystem"),
        base_path=contract_tmp_path / "runtime",
    )

    manager.record_turn(
        thread_id="thread-approval-runtime",
        user_content="Low signal approval detail before a tool request.",
        assistant_content="Needs approval before the tool call can run.",
        status="awaiting_approval",
    )

    assert manager.hcms_service.queue.pending_count() == 0
    state = manager.hcms_service.store.load("global/default")
    assert any("Low signal approval detail" in memory.content for memory in state.memories)


def test_hcms_manager_uses_update_queue_window_config(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            update_queue={
                "min_window_seconds": 2.0,
                "default_window_seconds": 11.0,
                "max_window_seconds": 17.0,
                "min_batch_turns": 3,
                "max_batch_turns": 5,
            },
        ),
        base_path=contract_tmp_path / "runtime",
    )

    queue = manager.hcms_service.queue
    assert queue.min_window_seconds == 2.0
    assert queue.default_window_seconds == 11.0
    assert queue.max_window_seconds == 17.0
    assert queue.min_batch_turns == 3
    assert queue.max_batch_turns == 5


def test_hcms_update_queue_flushes_when_max_batch_turns_is_reached(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            update_queue={
                "min_window_seconds": 2.0,
                "default_window_seconds": 30.0,
                "max_window_seconds": 60.0,
                "min_batch_turns": 2,
                "max_batch_turns": 3,
            },
        ),
        base_path=contract_tmp_path / "runtime",
    )

    for index in range(2):
        manager.record_turn(
            thread_id="thread-batch-limit",
            user_content=f"Low signal batch detail {index}",
            assistant_content="Noted.",
            status="completed",
        )

    assert manager.hcms_service.queue.pending_count() == 1
    assert manager.hcms_service.store.load("global/default").memories == []

    manager.record_turn(
        thread_id="thread-batch-limit",
        user_content="Low signal batch detail 2",
        assistant_content="Noted.",
        status="completed",
    )

    assert manager.hcms_service.queue.pending_count() == 0
    state = manager.hcms_service.store.load("global/default")
    assert len(state.observations) == 3
    assert any("Low signal batch detail" in memory.content for memory in state.memories)


def test_hcms_update_queue_disabled_bypasses_pending_capture(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            update_queue={
                "enabled": False,
                "debounce_seconds": 1.25,
            },
        ),
        base_path=contract_tmp_path / "runtime",
    )

    queue = manager.hcms_service.queue
    assert queue.enabled is False
    assert queue.min_window_seconds == 1.25
    assert queue.default_window_seconds == 1.25
    assert queue.max_window_seconds == 1.25

    manager.record_turn(
        thread_id="thread-queue-disabled",
        user_content="Low signal queue disabled detail",
        assistant_content="Noted.",
        status="completed",
    )

    assert manager.hcms_service.queue.pending_count() == 0
    state = manager.hcms_service.store.load("global/default")
    assert len(state.observations) == 1
    assert any("Low signal queue disabled detail" in memory.content for memory in state.memories)


def test_hcms_maintenance_uses_runtime_config_defaults(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            maintenance={
                "enabled": True,
                "automation_enabled": False,
                "policy": "review",
                "layer_id": "workspace",
                "limit": 1,
                "execute": False,
                "tick_seconds": 45,
                "interval_seconds": 1800,
                "min_idle_seconds": 90,
                "run_reflection_due_jobs": False,
                "include_health": False,
            },
        ),
        base_path=contract_tmp_path / "runtime",
    )
    manager.create_layer_entry(
        "workspace",
        content="Workspace maintenance candidate.",
        category="project_context",
        confidence=0.2,
        salience=0.05,
    )
    manager.create_layer_entry(
        "user",
        content="User maintenance candidate should stay out of workspace run.",
        category="preference",
        confidence=0.2,
        salience=0.05,
    )

    result = manager.run_maintenance(source="test").model_dump(mode="json")

    assert result["dry_run"] is True
    assert result["policy"] == "review"
    assert result["layer_id"] == "workspace"
    assert result["governance"]["candidate_count"] == 1
    assert result["governance"]["dry_run"] is True
    assert result["governance"]["items"][0]["layer_id"] == "workspace"
    assert result["governance"]["items"][0]["action"] == "review"
    assert result["health_before"] is None
    assert result["health_after"] is None
    assert result["reflection_jobs_due"] == 0
    assert result["reflection_jobs_run"] == 0

    status = manager.maintenance_automation_status()
    assert status["enabled"] is False
    assert status["tick_seconds"] == 45
    assert status["interval_seconds"] == 1800
    assert status["min_idle_seconds"] == 90
    assert status["dry_run"] is True
    assert status["execute"] is False
    assert status["policy"] == "review"
    assert status["layer_id"] == "workspace"
    assert status["limit"] == 1
    assert status["run_reflection_due_jobs"] is False


def test_hcms_maintenance_bounds_archive_actions_per_run(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            maintenance={
                "policy": "balanced",
                "layer_id": "workspace",
                "limit": 5,
                "execute": True,
                "max_archive_per_run": 1,
            },
        ),
        base_path=contract_tmp_path / "runtime",
    )
    for index in range(3):
        manager.create_layer_entry(
            "workspace",
            content=f"Low retention archive candidate {index}.",
            category="note",
            confidence=0.01,
            salience=0.01,
        )

    result = manager.run_maintenance(source="test").model_dump(mode="json")
    state = manager.hcms_service.store.load("global/default")
    archived = [memory for memory in state.memories if memory.state == MemoryLifecycleState.ARCHIVED]

    assert result["dry_run"] is False
    assert result["governance"]["candidate_count"] == 1
    assert result["governance"]["executed_count"] == 1
    assert result["actions_executed"] == {"archive": 1}
    assert result["skipped_actions"] == {"archive": 2}
    assert len(archived) == 1


def test_hcms_maintenance_disabled_skips_runtime_work(contract_tmp_path) -> None:
    manager = MemoryManager.from_config(
        config=HCMSRuntimeConfig(
            enabled=True,
            storage_backend="filesystem",
            maintenance={
                "enabled": False,
                "automation_enabled": True,
                "policy": "balanced",
                "layer_id": "workspace",
                "limit": 5,
                "execute": True,
            },
        ),
        base_path=contract_tmp_path / "runtime",
    )
    manager.create_layer_entry(
        "workspace",
        content="Disabled maintenance candidate must remain active.",
        category="note",
        confidence=0.01,
        salience=0.01,
    )

    result = manager.run_maintenance(source="test").model_dump(mode="json")
    state = manager.hcms_service.store.load("global/default")

    assert result["status"] == "skipped"
    assert result["skipped_reason"] == "disabled"
    assert result["governance"]["candidate_count"] == 0
    assert result["actions_executed"] == {}
    assert all(memory.state == MemoryLifecycleState.ACTIVE for memory in state.memories)
    assert manager.maintenance_automation_status()["enabled"] is False
    assert manager.run_maintenance_automation_if_due().model_dump(mode="json")["reason"] == "disabled"


def test_hcms_rule_based_updater_extracts_updates_and_removals() -> None:
    existing = Memory(
        content="User prefers JavaScript for backend work.",
        summary="User prefers JavaScript",
        category=MemoryCategory.PREFERENCE,
        confidence=0.7,
        salience=0.6,
    )
    state = MemoryState(namespace="global/default", memories=[existing])
    envelope = MemoryCaptureEnvelope(
        thread_id="thread-updater",
        memory_namespace="global/default",
        user_messages=[
            "Actually I prefer Python instead of JavaScript for backend work.",
            "Forget the old JavaScript backend preference.",
        ],
        explicit_corrections=["Python is the backend preference now."],
        positive_reinforcement=["Python backend preference is correct."],
    )

    updater = RuleBasedMemoryUpdater(confidence_threshold=0.7)
    plan = updater.plan_update(state, envelope)
    updated = updater.apply_update(state, plan)

    assert plan.new_facts
    assert plan.updates
    assert plan.removals == (existing.memory_id,)
    assert any("Python" in fact.content for fact in plan.new_facts)
    assert updated.memories[0].state == MemoryLifecycleState.FORGOTTEN
    assert any("Python" in memory.content for memory in updated.memories)
    assert updated.metrics.deterministic_updates == 1
    assert updated.metrics.llm_calls_avoided >= 1


def test_hcms_rule_based_updater_extracts_documented_zero_llm_preference_patterns() -> None:
    state = MemoryState(namespace="global/default")
    envelope = MemoryCaptureEnvelope(
        thread_id="thread-zero-llm-preferences",
        memory_namespace="global/default",
        user_messages=[
            "I like using Python for backend services.",
            "I don't like JavaScript for backend services.",
        ],
    )

    plan = RuleBasedMemoryUpdater(confidence_threshold=0.7).plan_update(state, envelope)

    assert len(plan.new_facts) == 2
    assert all(fact.category == MemoryCategory.PREFERENCE for fact in plan.new_facts)
    assert any("Python" in fact.content for fact in plan.new_facts)
    assert any("JavaScript" in fact.content for fact in plan.new_facts)


def test_hcms_rule_based_updater_extracts_documented_prefer_to_pattern() -> None:
    state = MemoryState(namespace="global/default")
    envelope = MemoryCaptureEnvelope(
        thread_id="thread-zero-llm-prefer-to",
        memory_namespace="global/default",
        user_messages=["I prefer SQLite to JSON files for local indexes."],
    )

    plan = RuleBasedMemoryUpdater(confidence_threshold=0.7).plan_update(state, envelope)

    assert len(plan.new_facts) == 1
    assert plan.new_facts[0].category == MemoryCategory.PREFERENCE
    assert "SQLite" in plan.new_facts[0].content
    assert "JSON files" in plan.new_facts[0].content


def test_hcms_rule_based_updater_records_explicit_corrections_as_corrections() -> None:
    state = MemoryState(namespace="global/default")
    envelope = MemoryCaptureEnvelope(
        thread_id="thread-zero-llm-correction",
        memory_namespace="global/default",
        explicit_corrections=["Actually, release verification runs before deployment."],
    )

    plan = RuleBasedMemoryUpdater(confidence_threshold=0.7).plan_update(state, envelope)

    assert len(plan.new_facts) == 1
    assert plan.new_facts[0].category == MemoryCategory.CORRECTION
    assert plan.new_facts[0].confidence >= 0.9
    assert plan.new_facts[0].source_error == "explicit correction"


def test_hcms_structured_updater_parses_json_plan_and_accumulates_evidence() -> None:
    existing = Memory(
        memory_id="mem_existing_policy",
        content="Canary verification is optional before release.",
        summary="Canary verification optional",
        category=MemoryCategory.PROCEDURE,
        confidence=0.55,
        salience=0.5,
        evidence=[
            Evidence(
                evidence_id="ev_old",
                type=EvidenceType.USER_STATED,
                content="Initial weak policy note.",
                weight=0.55,
                source_id="thread-old",
            )
        ],
    )
    state = MemoryState(namespace="global/default", memories=[existing])
    envelope = MemoryCaptureEnvelope(
        thread_id="thread-structured-update",
        memory_namespace="global/default",
        user_messages=["Actually canary verification is required before release."],
        explicit_corrections=["Canary verification is required before release."],
    )

    response = """```json
{
  "newFacts": [
    {
      "content": "Canary verification is required before every release.",
      "category": "procedure",
      "confidence": 0.92,
      "evidence": "User corrected the release policy.",
      "sourceError": "optional before release"
    },
    {
      "content": "Vague low confidence note should be filtered.",
      "category": "unknown-category",
      "confidence": 0.4
    }
  ],
  "updates": [
    {
      "memoryId": "mem_existing_policy",
      "confidenceDelta": 1.5,
      "newEvidence": "Correction confirms canary verification is required.",
      "reasoning": "explicit correction"
    }
  ],
  "removals": ["mem_missing", ""]
}
```"""

    plan = parse_structured_update_response(response, confidence_threshold=0.7)
    updated = StructuredMemoryUpdater(confidence_threshold=0.7).apply_update(state, plan, envelope=envelope)
    updated_existing = next(memory for memory in updated.memories if memory.memory_id == "mem_existing_policy")

    assert len(plan.new_facts) == 1
    assert plan.new_facts[0].category == MemoryCategory.PROCEDURE
    assert plan.updates[0].confidence_delta == 1.0
    assert plan.removals == ("mem_missing",)
    assert updated_existing.confidence > existing.confidence
    assert updated_existing.version == 2
    assert updated_existing.parent_id == "mem_existing_policy@v1"
    assert any(item.content == "Correction confirms canary verification is required." for item in updated_existing.evidence)
    new_memory = next(memory for memory in updated.memories if memory.memory_id != "mem_existing_policy")
    validation = KnowledgeCompiler.validate_markdown_schema(new_memory.content)
    assert validation.valid, validation.errors
    assert "source_thread_id: thread-structured-update" in new_memory.content
    assert "User corrected the release policy." in new_memory.content
    assert any(memory.source_thread_id == "thread-structured-update" for memory in updated.memories)
    assert updated.versions[-1].metadata["source_thread_id"] == "thread-structured-update"
    assert updated.metrics.deterministic_updates == 1


def test_hcms_structured_updater_marks_lower_confidence_new_facts_provisional() -> None:
    state = MemoryState(namespace="global/default")
    envelope = MemoryCaptureEnvelope(
        thread_id="thread-provisional-fact",
        memory_namespace="global/default",
        user_messages=["Northstar may need a release checklist reminder."],
    )

    response = """{
      "newFacts": [
        {
          "content": "Northstar may need a release checklist reminder.",
          "category": "goal",
          "confidence": 0.76,
          "evidence": "User described a likely goal."
        }
      ],
      "updates": [],
      "removals": []
    }"""

    plan = parse_structured_update_response(response, confidence_threshold=0.7)
    updated = StructuredMemoryUpdater(confidence_threshold=0.7).apply_update(state, plan, envelope=envelope)
    memory = updated.memories[0]

    assert MemoryLifecycleState("provisional") == MemoryLifecycleState.PROVISIONAL
    assert MemoryLifecycleState("deleted") == MemoryLifecycleState.DELETED
    assert memory.category == MemoryCategory.GOAL
    assert memory.confidence == 0.76
    assert memory.state == MemoryLifecycleState.PROVISIONAL
    assert updated.versions[-1].metadata["state"] == "provisional"


def test_hcms_structured_update_prompt_includes_state_signals_and_json_contract() -> None:
    state = MemoryState(
        namespace="global/default",
        memories=[
            Memory(
                memory_id="mem_release_policy",
                content="Canary verification is optional before release.",
                summary="Canary verification optional",
                category=MemoryCategory.PROCEDURE,
                confidence=0.55,
            )
        ],
    )
    envelope = MemoryCaptureEnvelope(
        thread_id="thread-prompt",
        memory_namespace="global/default",
        user_messages=["Actually canary verification is required."],
        final_assistant_messages=["I will update the release policy."],
        explicit_corrections=["Canary verification is required before release."],
    )

    prompt = build_structured_update_prompt(state, envelope)

    assert "mem_release_policy" in prompt
    assert "Canary verification optional" in prompt
    assert "CORRECTION SIGNAL DETECTED" in prompt
    assert '"newFacts"' in prompt
    assert '"confidenceDelta"' in prompt
    assert "thread-prompt" in prompt


def test_hcms_multi_level_compression_reaches_target_ratio() -> None:
    repeated_context = " ".join(
        f"Noise sentence {index} repeats background detail without changing the decision."
        for index in range(80)
    )
    source = (
        "Northstar critical release policy must keep canary verification because full rollout failed. "
        f"{repeated_context} "
        "Canary verification prevents repeated production failures."
    )

    result = MultiLevelCompressor().compress(
        source,
        level=3,
        preserve_terms=("Northstar", "canary verification", "production failures"),
    )

    assert result.method == "deterministic"
    assert result.compression_ratio > 8.0
    assert result.information_retention_score >= 0.66
    assert "Northstar" in result.compressed
    assert "canary verification" in result.compressed


def test_hcms_observation_records_compression_metrics(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    long_message = (
        "Remember: Northstar must keep canary verification because full rollout failed. "
        + " ".join(f"Filler detail {index} repeats prior context." for index in range(70))
    )
    envelope = service.build_capture_envelope(
        thread_id="thread-compression",
        namespace="global/default",
        messages=[HumanMessage(content=long_message)],
    )

    service.enqueue_capture(envelope)
    service.process_pending()

    observation = service.prefetch("global/default").observations[0]
    assert observation.metadata["compression_method"] == "deterministic"
    assert observation.metadata["compression_ratio"] > 4.0
    assert observation.metadata["information_retention_score"] >= 1.0
    assert len(observation.compressed_content or "") <= 500
