from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from anvil.memory.hcms_v2 import HCMSV2RuntimeBridge, workspace_state_to_working_memory
from anvil.runtime.context_v2 import (
    AttentionBudget,
    ContextAssemblerV2,
    ContextBlock,
    ContextSource,
    ContextSourceKind,
)
from anvil.runtime.state_v2 import (
    ConflictAlert,
    ConflictAlertSubscriber,
    EventLog,
    GoalFrame,
    GoalStack,
    ReviewInbox,
    RuntimeEvent,
    RuntimeEventBus,
    SalienceRouter,
    Scratchpad,
    TurnPipeline,
    TurnPipelineInput,
    ToolResultStore,
    WorkspaceState,
    tool_result_record_to_event,
)


def test_tool_result_store_updates_workspace_and_exports_reference_context_blocks() -> None:
    raw_ref = "artifact://thread-a/outputs/tool-results/pytest-raw.txt"
    raw_output = "SECRET RAW OUTPUT " * 500
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "status": "completed",
                "command": "pytest backend/tests/test_runtime_state_v2.py -q",
                "output": "1 passed in 0.42s",
                "raw_output_artifact_url": raw_ref,
                "_tool_output_budget": {
                    "truncated": True,
                    "original_chars": len(raw_output),
                    "artifact_url": raw_ref,
                    "compaction": {
                        "profile": "test",
                        "raw_artifact_url": raw_ref,
                    },
                },
            }
        ),
        name="shell_command",
        tool_call_id="call-pytest",
    )
    workspace = WorkspaceState(workspace_id="workspace-thread-a", thread_id="thread-a")
    store = ToolResultStore(thread_id="thread-a")

    record = store.ingest_tool_message(
        tool_message,
        tool_name="shell_command",
        run_id="run-a",
        turn_id="turn-1",
        workspace_state=workspace,
    )

    assert record.result_id.startswith("tool-result:")
    assert record.raw_ref == raw_ref
    assert record.raw_size_chars == len(str(tool_message.content))
    assert record.summary == "1 passed in 0.42s"
    assert record.compacted is True
    assert record.workspace_ref is not None

    assert len(workspace.intermediate_results) == 1
    workspace_result = workspace.intermediate_results[0]
    assert workspace_result.tool_result_id == record.result_id
    assert workspace_result.raw_ref == raw_ref
    assert "1 passed in 0.42s" in workspace_result.summary
    assert "SECRET RAW OUTPUT" not in workspace_result.summary

    blocks = [*store.to_context_blocks(), *workspace.to_context_blocks()]
    tool_block = next(block for block in blocks if block.source.kind == ContextSourceKind.TOOL_RESULT)
    workspace_block = next(block for block in blocks if block.source.kind == ContextSourceKind.WORKSPACE)

    assert tool_block.source.ref == "call-pytest"
    assert tool_block.compression_policy.ref == raw_ref
    assert raw_ref in tool_block.content
    assert "SECRET RAW OUTPUT" not in tool_block.content
    assert record.result_id in workspace_block.content
    assert "SECRET RAW OUTPUT" not in workspace_block.content

    assembled = ContextAssemblerV2().assemble(
        blocks,
        budget=AttentionBudget(max_context_tokens=1000, reserved_response_tokens=0),
    )

    assert assembled.trace.selected_tool_results == ("call-pytest",)
    assert assembled.trace.selected_tool_result_refs == (raw_ref,)
    assert assembled.trace.selected_workspace == (workspace_block.block_id,)


def test_tool_result_event_log_feeds_hcms_capture_without_raw_output() -> None:
    raw_ref = "artifact://thread-a/outputs/tool-results/pytest-raw.txt"
    raw_output = "SECRET RAW OUTPUT " * 500
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "status": "completed",
                "command": "pytest -q",
                "output": "1 passed in 0.42s",
                "raw_output_artifact_url": raw_ref,
                "_tool_output_budget": {
                    "truncated": True,
                    "original_chars": len(raw_output),
                    "compaction": {"profile": "test", "raw_artifact_url": raw_ref},
                },
            }
        ),
        name="shell_command",
        tool_call_id="call-pytest",
    )
    workspace = WorkspaceState(workspace_id="workspace-thread-a", thread_id="thread-a")
    store = ToolResultStore(thread_id="thread-a")
    record = store.ingest_tool_message(
        tool_message,
        tool_name="shell_command",
        run_id="run-a",
        turn_id="turn-1",
        workspace_state=workspace,
    )
    event = tool_result_record_to_event(record, thread_id="thread-a", workspace_refs=[record.workspace_ref])
    event_log = EventLog(thread_id="thread-a")
    seen = []
    bus = RuntimeEventBus(event_log=event_log)
    bus.subscribe(lambda runtime_event: seen.append(runtime_event.event_id))

    published = bus.publish(event)

    assert published.sequence == 1
    assert event_log.latest_sequence == 1
    assert seen == [published.event_id]
    assert published.event_type == "tool_result"
    assert published.tool_result_refs == [record.result_id]
    assert published.payload_ref == raw_ref
    assert published.workspace_refs == [record.workspace_ref]
    assert "1 passed in 0.42s" in published.payload_summary
    assert "SECRET RAW OUTPUT" not in published.payload_summary

    capture = HCMSV2RuntimeBridge().capture_runtime_event(published, namespace="global/default")

    assert capture.envelope.tool_result_refs == [record.result_id]
    assert capture.envelope.runtime_events[0].event_id == published.event_id
    assert capture.observation.observation_type == "tool_result"
    assert capture.observation.content_ref == raw_ref
    assert "1 passed in 0.42s" in capture.observation.content
    assert "SECRET RAW OUTPUT" not in capture.observation.content


def test_runtime_event_bus_is_idempotent_by_event_id() -> None:
    event_log = EventLog(thread_id="thread-dedupe")
    bus = RuntimeEventBus(event_log=event_log)
    seen_sequences: list[int] = []
    bus.subscribe(lambda runtime_event: seen_sequences.append(runtime_event.sequence))
    event = RuntimeEvent(
        event_id="event-dedupe-1",
        event_type="user_message_received",
        actor="runtime",
        thread_id="thread-dedupe",
        run_id="run-dedupe",
        turn_id="turn-dedupe",
        source_kind="user_message",
        source_ref="user-message:turn-dedupe",
        payload_summary="Remember: Northstar deploys with canary verification.",
    )

    first = bus.publish(event)
    replay = bus.publish(event.model_copy(update={"payload_summary": "Replay should not replace the first event."}))

    assert replay is first
    assert event_log.latest_sequence == 1
    assert len(event_log.events) == 1
    assert seen_sequences == [1]
    assert event_log.events[0].payload_summary == "Remember: Northstar deploys with canary verification."


def test_workspace_state_exports_bounded_working_memory_without_raw_output() -> None:
    raw_ref = "artifact://thread-a/outputs/tool-results/pytest-raw.txt"
    raw_output = "SECRET RAW OUTPUT " * 500
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "status": "completed",
                "command": "pytest backend/tests/test_runtime_state_v2.py -q",
                "output": "1 passed in 0.42s",
                "raw_output_artifact_url": raw_ref,
                "_tool_output_budget": {
                    "truncated": True,
                    "original_chars": len(raw_output),
                    "artifact_url": raw_ref,
                    "compaction": {
                        "profile": "test",
                        "raw_artifact_url": raw_ref,
                    },
                },
            }
        ),
        name="shell_command",
        tool_call_id="call-pytest",
    )
    workspace = WorkspaceState(
        workspace_id="workspace-thread-a",
        thread_id="thread-a",
        project_root="E:/repo",
        active_files=["backend/app.py"],
        variables={"current_batch": "Batch C"},
    )
    store = ToolResultStore(thread_id="thread-a")
    record = store.ingest_tool_message(
        tool_message,
        tool_name="shell_command",
        run_id="run-a",
        turn_id="turn-1",
        workspace_state=workspace,
    )

    memory = workspace_state_to_working_memory(workspace, namespace="global/default")

    assert memory.memory_id.startswith("mem_v2_")
    assert memory.namespace == "global/default"
    assert memory.layer == "working"
    assert memory.category == "workspace_state"
    assert "Workspace state" in memory.title
    assert "workspace-thread-a" in memory.canonical_content
    assert "thread-a" in memory.canonical_content
    assert "backend/app.py" in memory.canonical_content
    assert "current_batch=Batch C" in memory.canonical_content
    assert record.result_id in memory.canonical_content
    assert raw_ref in memory.canonical_content
    assert "1 passed in 0.42s" in memory.summary
    assert "SECRET RAW OUTPUT" not in memory.summary
    assert "SECRET RAW OUTPUT" not in memory.canonical_content
    assert memory.metadata["workspace_state_ref"] == "workspace-thread-a"
    assert memory.metadata["thread_id"] == "thread-a"
    assert memory.metadata["project_root"] == "E:/repo"
    assert memory.metadata["active_file_count"] == 1
    assert memory.metadata["intermediate_result_count"] == 1
    assert memory.evidence
    assert memory.evidence[0].source_label == "workspace_state"
    assert memory.evidence[0].source_uri == "workspace://workspace-thread-a"


def test_goal_stack_exports_salience_route_and_context_block_without_completed_noise() -> None:
    completed_noise = "COMPLETED VERBOSE LOG " * 200
    goal_stack = GoalStack(
        stack_id="goals-thread-a",
        thread_id="thread-a",
        active_goal_id="goal-active",
        goals=[
            GoalFrame(
                goal_id="goal-active",
                title="Implement Runtime Context V2 state routing",
                status="active",
                priority=0.9,
                blockers=["memory retriever not goal-conditioned"],
                next_actions=["wire salience route into HCMS search"],
                keywords=["runtime-context", "hcms-v2"],
            ),
            GoalFrame(
                goal_id="goal-done",
                title="Completed old exploration",
                status="completed",
                summary=completed_noise,
                keywords=["obsolete-noise"],
            ),
        ],
    )

    route = goal_stack.to_salience_route()

    assert route.route_id.startswith("salience-route:")
    assert route.goal_stack_ref == "goals-thread-a"
    assert route.active_goal_id == "goal-active"
    assert route.memory_query
    assert "Runtime Context V2 state routing" in route.memory_query
    assert "memory retriever not goal-conditioned" in route.memory_query
    assert "wire salience route into HCMS search" in route.memory_query
    assert route.boost_terms["runtime-context"] == 0.9
    assert route.boost_terms["hcms-v2"] == 0.9
    assert "obsolete-noise" not in route.boost_terms
    assert route.diagnostics["suppressed_completed_goals"] == 1
    assert "COMPLETED VERBOSE LOG" not in route.memory_query

    blocks = goal_stack.to_context_blocks()

    assert len(blocks) == 1
    block = blocks[0]
    assert block.source.kind == ContextSourceKind.WORKSPACE
    assert block.source.name == "goal_stack"
    assert block.metadata["goal_stack_ref"] == "goals-thread-a"
    assert block.metadata["active_goal_id"] == "goal-active"
    assert "Implement Runtime Context V2 state routing" in block.content
    assert "wire salience route into HCMS search" in block.content
    assert "COMPLETED VERBOSE LOG" not in block.content

    assembled = ContextAssemblerV2().assemble(
        blocks,
        budget=AttentionBudget(max_context_tokens=400, reserved_response_tokens=0),
    )

    assert assembled.trace.selected_workspace == (block.block_id,)


def test_salience_router_routes_goal_stack_with_current_query_without_completed_noise() -> None:
    completed_noise = "COMPLETED VERBOSE LOG " * 200
    raw_query = "How should HCMS route memory for GoalStack? " + "RAW_GOAL_QUERY " * 80
    goal_stack = GoalStack(
        stack_id="goals-thread-a",
        thread_id="thread-a",
        active_goal_id="goal-active",
        goals=[
            GoalFrame(
                goal_id="goal-active",
                title="Ship HCMS Runtime Context V2",
                status="active",
                summary="Make GoalStack influence memory retrieval and context assembly.",
                priority=0.91,
                blockers=["memory retrieval ignores active batch"],
                next_actions=["route goal stack into salience router"],
                keywords=["hcms-v2", "runtime-context"],
            ),
            GoalFrame(
                goal_id="goal-done",
                title="Completed old exploration",
                status="completed",
                summary=completed_noise,
                keywords=["obsolete-noise"],
            ),
        ],
    )

    route = SalienceRouter(
        router_id="salience-thread-a",
        thread_id="thread-a",
    ).route_goal_stack(goal_stack, query=raw_query)

    assert route.route_id.startswith("salience-route:")
    assert route.goal_stack_ref == "goals-thread-a"
    assert route.active_goal_id == "goal-active"
    assert "current_query=" in route.memory_query
    assert "How should HCMS route memory" in route.memory_query
    assert "memory retrieval ignores active batch" in route.memory_query
    assert "route goal stack into salience router" in route.memory_query
    assert len(route.memory_query) <= 2000
    assert route.boost_terms["hcms-v2"] == 0.91
    assert route.boost_terms["runtime-context"] == 0.91
    assert route.suppressed_goal_refs == ["goal-done"]
    assert route.diagnostics["router_id"] == "salience-thread-a"
    assert route.diagnostics["thread_id"] == "thread-a"
    assert route.diagnostics["query_tokens"] > 0
    assert route.diagnostics["query_bounded"] is True
    assert "COMPLETED VERBOSE LOG" not in route.memory_query
    assert "obsolete-noise" not in route.boost_terms


def test_scratchpad_exports_bounded_context_block_with_raw_refs() -> None:
    raw_ref = "artifact://thread-a/scratchpad/entry-raw.txt"
    sensitive_detail = "PRIVATE_VERBOSE_DETAIL " * 300
    scratchpad = Scratchpad(scratchpad_id="scratch-thread-a", thread_id="thread-a")

    entry = scratchpad.add_entry(
        kind="decision",
        summary="Use ReviewInbox runtime warning blocks for unresolved conflicts.",
        status="active",
        priority=0.88,
        source_refs=["review-1", "conflict-1"],
        raw_ref=raw_ref,
        raw_detail=sensitive_detail,
    )
    blocks = scratchpad.to_context_blocks()

    assert entry.entry_id.startswith("scratchpad-entry:")
    assert entry.raw_ref == raw_ref
    assert entry.raw_size_chars == len(sensitive_detail)
    assert entry.summary == "Use ReviewInbox runtime warning blocks for unresolved conflicts."
    assert scratchpad.diagnostics["entry_count"] == 1
    assert scratchpad.diagnostics["raw_ref_count"] == 1

    assert len(blocks) == 1
    block = blocks[0]
    assert block.block_type == "scratchpad"
    assert block.source.kind == ContextSourceKind.WORKSPACE
    assert block.source.name == "scratchpad"
    assert block.source.ref == "scratch-thread-a"
    assert block.compression_policy.ref == raw_ref
    assert block.metadata["scratchpad_id"] == "scratch-thread-a"
    assert block.metadata["entry_refs"] == [entry.entry_id]
    assert raw_ref in block.content
    assert "Use ReviewInbox runtime warning blocks" in block.content
    assert "PRIVATE_VERBOSE_DETAIL" not in block.content

    assembled = ContextAssemblerV2().assemble(
        blocks,
        budget=AttentionBudget(max_context_tokens=260, reserved_response_tokens=0),
    )

    assert "Scratchpad" in assembled.rendered_context
    assert "PRIVATE_VERBOSE_DETAIL" not in assembled.rendered_context
    assert assembled.trace.selected_workspace == ("scratch-thread-a",)


def test_conflict_alert_flows_through_review_inbox_to_runtime_warning_block() -> None:
    alert = ConflictAlert(
        alert_id="alert-1",
        conflict_id="conflict-1",
        severity="high",
        affected_claims=["claim-old", "claim-new"],
        affected_memories=["mem-legacy"],
        preferred_claim_id="claim-new",
        unresolved_reason="Exact contradiction requires review.",
        injection_policy="inject_warning",
        review_inbox_id="review-1",
    )
    inbox = ReviewInbox(inbox_id="review-thread-a", thread_id="thread-a")

    item = inbox.add_alert(alert)
    blocks = inbox.to_context_blocks()

    assert item.review_inbox_id == "review-1"
    assert item.status == "needs_review"
    assert item.alert_id == "alert-1"
    assert item.conflict_id == "conflict-1"
    assert item.affected_claims == ["claim-old", "claim-new"]
    assert inbox.diagnostics["open_item_count"] == 1

    assert len(blocks) == 1
    block = blocks[0]
    assert block.block_type == "runtime_warning"
    assert block.title == "Runtime Conflict Warning"
    assert block.source.kind == ContextSourceKind.EVENT
    assert block.source.name == "review_inbox"
    assert block.source.ref == "review-1"
    assert block.injection_policy.requires_warning is True
    assert block.injection_policy.protected is True
    assert block.injection_policy.reason == "inject_warning"
    assert block.compression_policy.allow_compression is False
    assert block.compression_policy.ref == "review-1"
    assert block.conflict_state == "unresolved"
    assert block.metadata["alert_id"] == "alert-1"
    assert block.metadata["conflict_id"] == "conflict-1"
    assert block.metadata["review_inbox_id"] == "review-1"
    assert "claim-old" in block.content
    assert "preferred_claim_id=claim-new" in block.content
    assert "Exact contradiction requires review." in block.content

    assembled = ContextAssemblerV2().assemble(
        blocks,
        budget=AttentionBudget(max_context_tokens=220, reserved_response_tokens=0),
    )

    assert "Runtime Conflict Warning" in assembled.rendered_context
    assert "claim-old" in assembled.rendered_context
    assert assembled.trace.selected_events == ("review-1",)


def test_runtime_event_bus_conflict_alert_updates_review_inbox_warning_block() -> None:
    inbox = ReviewInbox(inbox_id="review-thread-a", thread_id="thread-a")
    event_log = EventLog(thread_id="thread-a")
    bus = RuntimeEventBus(event_log=event_log)
    subscriber = ConflictAlertSubscriber(review_inbox=inbox)
    bus.subscribe(subscriber)

    conflict_event = RuntimeEvent(
        event_id="event-conflict-1",
        event_type="conflict_alert",
        actor="memory",
        thread_id="thread-a",
        run_id="run-a",
        turn_id="turn-1",
        source_kind="hcms_v2_conflict_ledger",
        source_ref="conflict-1",
        payload_summary="Exact contradiction requires review before injection.",
        metadata={
            "alert_id": "alert-1",
            "conflict_id": "conflict-1",
            "severity": "high",
            "affected_claims": ["claim-old", "claim-new"],
            "affected_memories": ["mem-legacy"],
            "preferred_claim_id": "claim-new",
            "unresolved_reason": "Exact contradiction requires review.",
            "injection_policy": "inject_warning",
            "review_inbox_id": "review-1",
            "conflict_type": "contradiction",
        },
    )

    published = bus.publish(conflict_event)

    assert len(inbox.items) == 1
    item = inbox.items[0]
    assert item.review_inbox_id == "review-1"
    assert item.alert_id == "alert-1"
    assert item.conflict_id == "conflict-1"
    assert item.severity == "high"
    assert item.affected_claims == ["claim-old", "claim-new"]
    assert item.affected_memories == ["mem-legacy"]
    assert inbox.diagnostics["open_item_count"] == 1

    blocks = inbox.to_context_blocks()
    assert len(blocks) == 1
    warning = blocks[0]
    assert warning.block_type == "runtime_warning"
    assert warning.source.kind == ContextSourceKind.EVENT
    assert warning.source.ref == "review-1"
    assert warning.injection_policy.protected is True
    assert warning.injection_policy.requires_warning is True
    assert warning.compression_policy.allow_compression is False
    assert warning.metadata["runtime_event_id"] == "event-conflict-1"

    assert published.metadata["review_inbox_id"] == "review-1"
    assert published.metadata["runtime_warning_block_id"] == warning.block_id
    assert published.metadata["runtime_warning_injected"] is True
    assert published.metadata["hcms_v2_conflict_alert_routed"]["conflict_id"] == "conflict-1"
    assert subscriber.diagnostics["routed_alert_count"] == 1

    assembled = ContextAssemblerV2().assemble(
        blocks,
        budget=AttentionBudget(max_context_tokens=220, reserved_response_tokens=0),
    )

    assert "Runtime Conflict Warning" in assembled.rendered_context
    assert "claim-old" in assembled.rendered_context
    assert assembled.trace.selected_events == ("review-1",)

    duplicate = bus.publish(conflict_event)

    assert len(inbox.items) == 1
    assert duplicate.metadata["runtime_warning_duplicate"] is True
    assert subscriber.diagnostics["duplicate_event_count"] == 1


def test_turn_pipeline_prepares_traceable_llm_context_from_runtime_state() -> None:
    raw_ref = "artifact://thread-a/outputs/tool-results/pytest-raw.txt"
    sensitive_detail = "PRIVATE_VERBOSE_DETAIL " * 200
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "status": "completed",
                "output": "pytest state tests passed",
                "raw_output_artifact_url": raw_ref,
                "_tool_output_budget": {
                    "truncated": True,
                    "artifact_url": raw_ref,
                    "compaction": {"profile": "test", "raw_artifact_url": raw_ref},
                },
            }
        ),
        name="shell_command",
        tool_call_id="call-pipeline-pytest",
    )
    workspace = WorkspaceState(
        workspace_id="workspace-thread-a",
        thread_id="thread-a",
        active_files=["backend/tests/test_runtime_state_v2.py"],
    )
    tool_store = ToolResultStore(thread_id="thread-a")
    tool_record = tool_store.ingest_tool_message(
        tool_message,
        tool_name="shell_command",
        run_id="run-a",
        turn_id="turn-12",
        workspace_state=workspace,
    )
    goal_stack = GoalStack(
        stack_id="goals-thread-a",
        thread_id="thread-a",
        active_goal_id="goal-c",
        goals=[
            GoalFrame(
                goal_id="goal-c",
                title="Wire Batch C state systems into TurnPipeline",
                status="active",
                priority=0.9,
                next_actions=["assemble state ContextBlocks before LLM call"],
                keywords=["turn-pipeline", "runtime-context"],
            )
        ],
    )
    scratchpad = Scratchpad(scratchpad_id="scratch-thread-a", thread_id="thread-a")
    scratch_entry = scratchpad.add_entry(
        kind="decision",
        summary="Use typed TurnPipeline as the Batch C composition root.",
        priority=0.85,
        raw_ref="artifact://thread-a/scratchpad/turn-plan.txt",
        raw_detail=sensitive_detail,
    )
    inbox = ReviewInbox(inbox_id="review-thread-a", thread_id="thread-a")
    inbox.add_alert(
        ConflictAlert(
            alert_id="alert-1",
            conflict_id="conflict-1",
            severity="high",
            affected_claims=["claim-old", "claim-new"],
            preferred_claim_id="claim-new",
            unresolved_reason="Needs review before injecting as fact.",
            review_inbox_id="review-1",
        )
    )
    event_log = EventLog(thread_id="thread-a")
    event_bus = RuntimeEventBus(event_log=event_log)
    pipeline = TurnPipeline(event_bus=event_bus)

    result = pipeline.prepare_llm_context(
        TurnPipelineInput(
            thread_id="thread-a",
            run_id="run-a",
            turn_id="turn-12",
            user_text="Continue Batch C without re-reading every V2 document.",
            goal_stack=goal_stack,
            workspace_state=workspace,
            scratchpad=scratchpad,
            tool_result_store=tool_store,
            review_inbox=inbox,
            budget=AttentionBudget(max_context_tokens=1400, reserved_response_tokens=0),
        )
    )

    assert result.turn_state.turn_id == "turn-12"
    assert result.turn_state.thread_id == "thread-a"
    assert result.turn_state.user_message_ref.startswith("user-message:")
    assert result.turn_state.context_trace_id == result.assembled_context.trace.trace_id
    assert result.turn_state.phase_statuses["intake"] == "completed"
    assert result.turn_state.phase_statuses["context_assembly"] == "completed"
    assert result.turn_state.diagnostics["candidate_block_count"] == len(result.candidate_blocks)
    assert result.turn_state.diagnostics["selected_block_count"] == len(result.assembled_context.blocks)
    assert result.turn_state.diagnostics["tool_result_count"] == 1

    assert result.assembled_context.trace.metadata["turn_id"] == "turn-12"
    assert result.assembled_context.trace.metadata["pipeline"] == "runtime_context_v2_turn_pipeline"
    assert "scratch-thread-a" in result.assembled_context.trace.selected_workspace
    assert "review-1" in result.assembled_context.trace.selected_events
    assert result.assembled_context.trace.selected_tool_results == ("call-pipeline-pytest",)
    assert result.assembled_context.trace.selected_tool_result_refs == (raw_ref,)
    assert tool_record.result_id in result.turn_state.tool_result_refs
    assert scratch_entry.entry_id in result.turn_state.scratchpad_entry_refs
    assert "PRIVATE_VERBOSE_DETAIL" not in result.assembled_context.rendered_context
    assert raw_ref in result.assembled_context.rendered_context

    event_types = [event.event_type for event in event_log.events]
    assert event_types == ["user_message_received", "context_assembled"]
    assert event_log.events[0].payload_summary == "Continue Batch C without re-reading every V2 document."
    assert event_log.events[1].trace_id == result.assembled_context.trace.trace_id
    assert event_log.events[1].workspace_refs
    assert event_log.events[1].tool_result_refs == [tool_record.result_id]


def test_turn_pipeline_uses_salience_route_for_extra_block_budget_competition() -> None:
    goal_stack = GoalStack(
        stack_id="goals-thread-a",
        thread_id="thread-a",
        active_goal_id="goal-hcms",
        goals=[
            GoalFrame(
                goal_id="goal-hcms",
                title="Prioritize HCMS V2 runtime context salience",
                status="active",
                priority=0.96,
                next_actions=["wire salience route into context assembly"],
                keywords=["hcms-v2", "runtime-context", "salience-route"],
            )
        ],
    )
    salience_route = SalienceRouter(
        router_id="salience-router:thread-a",
        thread_id="thread-a",
    ).route_goal_stack(goal_stack, query="Use HCMS V2 runtime-context salience")
    unrelated_memory = ContextBlock(
        block_id="memory:unrelated",
        block_type="memory",
        source=ContextSource(kind=ContextSourceKind.MEMORY, name="hcms"),
        title="Unrelated local preference",
        content="Keep sidebar density compact for review workflows.",
        priority=0.94,
        salience=0.94,
        confidence=0.9,
        token_cost=24,
        position_hint="memory:unrelated",
    )
    goal_memory = ContextBlock(
        block_id="memory:hcms-v2",
        block_type="memory",
        source=ContextSource(kind=ContextSourceKind.MEMORY, name="hcms"),
        title="HCMS V2 salience route",
        content="runtime-context and salience-route evidence for the active HCMS V2 goal.",
        priority=0.34,
        salience=0.34,
        confidence=0.85,
        token_cost=24,
        position_hint="memory:hcms-v2",
        tags=("hcms-v2", "runtime-context"),
        metadata={"memory_id": "mem-hcms-v2"},
    )
    pipeline = TurnPipeline(event_bus=RuntimeEventBus(event_log=EventLog(thread_id="thread-a")))

    result = pipeline.prepare_llm_context(
        TurnPipelineInput(
            thread_id="thread-a",
            run_id="run-a",
            turn_id="turn-salience",
            user_text="Use HCMS V2 runtime-context salience",
            goal_stack=goal_stack,
            extra_blocks=[unrelated_memory, goal_memory],
            budget=AttentionBudget(
                max_context_tokens=128,
                    reserved_response_tokens=0,
                    per_layer_token_budget={"memory": 24},
                ),
        )
    )

    assert "memory:hcms-v2" in result.assembled_context.trace.selected_block_ids
    assert "memory:unrelated" in result.assembled_context.trace.dropped_block_ids
    assert result.assembled_context.trace.metadata["salience_route_id"].startswith("salience-route:")
    assert result.assembled_context.trace.metadata["goal_stack_ref"] == "goals-thread-a"
    assert result.assembled_context.trace.retrieval_scores["memory:hcms-v2"]["goal_alignment"] > 0
    assert result.assembled_context.trace.retrieval_scores["memory:unrelated"]["goal_alignment"] == 0.0
