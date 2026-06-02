from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from anvil.memory import DebouncedMemoryQueue, FileMemoryStore, HeuristicMemoryUpdater, MemoryService


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


def test_memory_queue_replaces_older_pending_snapshots(contract_tmp_path) -> None:
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
    assert pending[0].user_messages == ["second"]


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


def test_memory_service_fail_open_on_missing_store_data(contract_tmp_path) -> None:
    service = make_service(contract_tmp_path)
    injection = service.build_injection_view("missing/namespace")
    assert injection.namespace == "missing/namespace"
    assert injection.summary == ""
