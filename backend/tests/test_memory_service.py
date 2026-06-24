from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from anvil.memory.hcms_v2 import capture_envelope_v2_from_legacy
from anvil.memory import DebouncedMemoryQueue, FileMemoryStore, HeuristicMemoryUpdater, MemoryManager, MemoryService
from anvil.runtime.state_v2 import RuntimeEvent


def make_service(contract_tmp_path):
    return MemoryService(
        store=FileMemoryStore(contract_tmp_path / "memory-store"),
        queue=DebouncedMemoryQueue(),
        updater=HeuristicMemoryUpdater(max_facts=5),
        max_facts=5,
        injection_token_budget=200,
    )


def test_memory_capture_filters_tool_chatter_and_detects_signals(contract_tmp_path) -> None:
    service = make_service(contract_tmp_path)
    envelope = service.build_capture_envelope(
        thread_id="thread-1",
        namespace="global/default",
        messages=[
            HumanMessage(content="Actually, that's wrong. Prefer concise output."),
            AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1", "type": "tool_call"}]),
            ToolMessage(content="tool chatter", tool_call_id="1"),
            AIMessage(content="Understood. I will keep it concise."),
        ],
    )

    assert envelope.user_messages == ["Actually, that's wrong. Prefer concise output."]
    assert envelope.explicit_corrections == ["Actually, that's wrong. Prefer concise output."]
    assert envelope.final_assistant_messages == ["Understood. I will keep it concise."]
    runtime_event_refs = envelope.metadata["runtime_event_refs"]
    assert [item["event_type"] for item in runtime_event_refs] == ["user_message", "assistant_message"]
    assert all(item["payload_summary"] != "tool chatter" for item in runtime_event_refs)

    envelope_v2 = capture_envelope_v2_from_legacy(envelope)
    assert [item.event_type for item in envelope_v2.runtime_events] == ["user_message", "assistant_message"]
    assert envelope_v2.user_message_refs == [runtime_event_refs[0]["source_ref"]]


def test_memory_queue_coalesces_pending_snapshots_without_dropping_messages(contract_tmp_path) -> None:
    service = make_service(contract_tmp_path)
    first = service.build_capture_envelope(
        thread_id="thread-1",
        namespace="global/default",
        messages=[HumanMessage(content="first")],
    )
    second = service.build_capture_envelope(
        thread_id="thread-1",
        namespace="global/default",
        messages=[HumanMessage(content="second")],
    )
    service.enqueue_capture(first)
    service.enqueue_capture(second)

    pending = service.queue.get_pending()
    assert len(pending) == 1
    assert pending[0].user_messages == ["first", "second"]
    assert pending[0].metadata["coalesced_capture_count"] == 2


def test_memory_injection_is_fenced_and_budgeted(contract_tmp_path) -> None:
    service = make_service(contract_tmp_path)
    envelope = service.build_capture_envelope(
        thread_id="thread-1",
        namespace="global/default",
        messages=[HumanMessage(content="Actually, use concise bullet summaries for project updates.")],
    )
    service.enqueue_capture(envelope)
    assert service.process_pending() == 1

    injection = service.build_injection_view("global/default")
    rendered = injection.render_fenced()
    assert rendered.startswith("<memory_context>")
    assert "correction:" in rendered


def test_memory_manager_stable_snapshot_renders_plain_text_not_memory_context_fence(contract_tmp_path) -> None:
    service = make_service(contract_tmp_path)
    service.create_memory(
        "global/default",
        content="Northstar deploys with canary verification.",
        category="project_context",
        confidence=0.93,
        salience=0.88,
    )
    service.create_memory(
        "global/default",
        content="Canary verification prevents repeated release failures.",
        category="knowledge",
        confidence=0.91,
        salience=0.86,
    )
    manager = MemoryManager(service=service, state_root=contract_tmp_path / "state")

    rendered = manager.render_stable_snapshot()

    assert rendered
    assert "<memory_context>" not in rendered
    assert "</memory_context>" not in rendered
    assert "namespace=global/default" in rendered
    assert "Northstar deploys with canary verification." in rendered
    assert "Canary verification prevents repeated release failures." in rendered


def test_memory_service_fail_open_on_missing_store_data(contract_tmp_path) -> None:
    service = make_service(contract_tmp_path)
    injection = service.build_injection_view("missing/namespace")
    assert injection.namespace == "missing/namespace"
    assert injection.summary == ""


def test_hcms_v2_runtime_event_capture_schedules_fast_and_slow_consolidation(contract_tmp_path) -> None:
    service = make_service(contract_tmp_path)
    capture = service.capture_runtime_event_v2(
        {
            "event_id": "event-hcms-v2-schedule-1",
            "event_type": "tool_result",
            "thread_id": "thread-schedule",
            "run_id": "run-schedule",
            "turn_id": "turn-schedule",
            "source_ref": "tool-call-schedule",
            "payload_summary": (
                "pytest passed for Runtime Context V2. "
                "OPENAI_API_KEY=sk-test123456789 should be redacted."
            ),
            "payload_ref": "artifact://thread-schedule/tool-results/raw.txt",
            "tool_result_refs": ["tool-result-schedule-1"],
            "workspace_refs": ["workspace-schedule"],
            "metadata": {
                "tool_name": "shell_command",
                "status": "success",
                "summary_size_chars": 46,
                "raw_size_chars": 4096,
            },
        },
        namespace="global/default",
    )

    state = service.prefetch("global/default")
    persisted_id = capture.envelope.metadata["persisted_memory_id"]
    memory = next(item for item in state.memories if item.memory_id == persisted_id)

    schedule = memory.metadata.get("hcms_v2_consolidation_schedule")
    assert isinstance(schedule, dict)
    assert schedule["schedule_id"].startswith("consolidation_schedule_v2_")
    assert schedule["namespace"] == "global/default"
    assert schedule["capture_envelope_id"] == capture.envelope.envelope_id
    assert schedule["fast_task"]["mode"] == "fast"
    assert schedule["fast_task"]["status"] == "completed"
    assert schedule["fast_task"]["target_layer"] == "episodic"
    assert schedule["fast_task"]["source_memory_ids"] == [persisted_id]
    assert schedule["slow_task"]["mode"] == "slow"
    assert schedule["slow_task"]["status"] == "scheduled"
    assert schedule["slow_task"]["target_layer"] == "semantic"
    assert schedule["slow_task"]["capture_envelope_id"] == capture.envelope.envelope_id
    assert schedule["slow_task"]["observation_id"] == capture.observation.observation_id
    assert schedule["slow_task"]["source_memory_ids"] == [persisted_id]
    assert schedule["slow_task"]["runtime_event_ids"] == ["event-hcms-v2-schedule-1"]
    assert schedule["slow_task"]["replay_refs"]["payload_ref"] == "artifact://thread-schedule/tool-results/raw.txt"
    assert schedule["slow_task"]["replay_refs"]["tool_result_refs"] == ["tool-result-schedule-1"]
    assert schedule["slow_task"]["replay_refs"]["workspace_refs"] == ["workspace-schedule"]
    assert schedule["slow_task"]["content_hash"].startswith("content_hash_v2_")
    assert "sk-test123456789" not in str(schedule)

    assert memory.metadata["hcms_v2_fast_consolidated"] is True
    assert memory.metadata["hcms_v2_slow_consolidation_task_id"] == schedule["slow_task"]["task_id"]
    assert capture.envelope.metadata["hcms_v2_slow_consolidation_task_id"] == schedule["slow_task"]["task_id"]

    diagnostics = [
        item
        for item in state.diagnostics
        if item.component == "hcms_v2_consolidation" and item.reason == "slow_consolidation_scheduled"
    ]
    assert diagnostics
    assert diagnostics[-1].metadata["task_id"] == schedule["slow_task"]["task_id"]


def test_hcms_v2_runtime_event_batch_capture_commits_slow_replay_once(contract_tmp_path) -> None:
    class CountingStore(FileMemoryStore):
        def __init__(self, base_path):
            super().__init__(base_path)
            self.save_count = 0

        def save(self, namespace, memory_state) -> None:
            self.save_count += 1
            super().save(namespace, memory_state)

    store = CountingStore(contract_tmp_path / "batch-memory-store")
    service = MemoryService(
        store=store,
        queue=DebouncedMemoryQueue(),
        updater=HeuristicMemoryUpdater(max_facts=5),
        max_facts=5,
        injection_token_budget=200,
    )
    events = [
        RuntimeEvent(
            event_id=f"event-hcms-v2-batch-{index}",
            event_type="tool_result",
            actor="runtime",
            thread_id="thread-batch",
            run_id="run-batch",
            turn_id="turn-batch",
            source_kind="tool",
            source_ref=f"tool-call-batch-{index}",
            payload_summary=f"Batch HCMS V2 capture persisted tool result {index}.",
            payload_ref=f"artifact://thread-batch/tool-results/{index}.txt",
            tool_result_refs=[f"tool-result-batch-{index}"],
            workspace_refs=[f"workspace-batch-{index}"],
            metadata={"tool_name": "shell_command", "tool_call_id": f"call-batch-{index}", "status": "success"},
        )
        for index in range(3)
    ]

    captures = service.capture_runtime_events_v2(events, namespace="global/default")

    assert len(captures) == 3
    assert store.save_count == 1
    state = service.prefetch("global/default")
    source_memories = [
        memory
        for memory in state.memories
        if str(memory.metadata.get("event_id") or "").startswith("event-hcms-v2-batch-")
    ]
    assert len(source_memories) == 3
    assert all(memory.metadata["hcms_v2_slow_consolidated"] is True for memory in source_memories)
    assert all(memory.metadata["hcms_v2_slow_consolidated_memory_ids"] for memory in source_memories)
    assert all(memory.metadata["hcms_v2_slow_consolidation_claim_ids"] for memory in source_memories)
    slow_memories = [
        memory
        for memory in state.memories
        if memory.metadata.get("source_kind") == "runtime_event_slow_consolidation"
        and str(memory.metadata.get("observation_id") or "").startswith("obs_v2_")
    ]
    assert len(slow_memories) >= 3
