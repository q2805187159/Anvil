from __future__ import annotations

import json

from anvil.agents import RecentApprovalEvent, RecentToolActivity, ThreadLifecycleStatus, ThreadState
from anvil.trajectory import (
    EvaluationReportEvaluatorResult,
    EvaluationReportOptions,
    ThreadEvaluationReportBuilder,
    ThreadTrajectoryExporter,
    TrajectoryCompressionConfig,
    TrajectoryExportFormat,
    TrajectoryExportOptions,
)


def make_state() -> ThreadState:
    return ThreadState(
        identity={"thread_id": "thread-traj", "run_id": "run-1"},
        lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
        conversation={
            "title": "Trajectory test",
            "summary": "User wants a durable export.",
            "messages": [
                {"role": "system", "content": "internal prompt SECRET_KEY=abc123456"},
                {"role": "human", "id": "u1", "content": "Create a file with token ghp_abcdefghijklmnopqrstuvwxyz"},
                {
                    "role": "ai",
                    "id": "a1",
                    "content": "I will create the file.",
                    "content_blocks": [
                        {"type": "thinking", "text": "private chain of thought"},
                        {"type": "text", "text": "I will create the file."},
                    ],
                    "tool_calls": [
                        {"id": "tc1", "name": "write_file", "args": {"path": "calc.py", "content": "print(1)"}}
                    ],
                },
                {"role": "tool", "id": "t1", "tool_call_id": "tc1", "name": "write_file", "status": "success", "content": "ok"},
                {"role": "ai", "id": "a2", "content": "Done."},
            ],
        },
        execution={
            "active_model": "minimax/MiniMax-M2.7",
            "token_usage": {
                "total": {"input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.001},
            },
            "recent_tool_activity": [
                RecentToolActivity(
                    tool_call_id="tc1",
                    message_id="a1",
                    name="write_file",
                    status="success",
                    result_text="ok",
                    duration_ms=200,
                )
            ],
        },
        artifacts={"output_artifacts": ["calc.py"], "uploaded_files": [{"filename": "input.txt"}]},
        approvals={
            "recent_approval_events": [
                RecentApprovalEvent(
                    request_id="approval-1",
                    decision="yes",
                    action_kind="filesystem_write",
                    requested_permissions=["filesystem_write"],
                    status="resolved",
                )
            ]
        },
    )


def test_export_defaults_scrub_secrets_and_exclude_reasoning() -> None:
    entry = ThreadTrajectoryExporter().export_thread(make_state())

    assert entry.id == "thread-traj:run-1"
    assert entry.completed is True
    assert [turn.from_ for turn in entry.conversations] == ["human", "gpt", "tool", "gpt"]
    joined = "\n".join(turn.value for turn in entry.conversations)
    assert "private chain of thought" not in joined
    assert "<think>" not in joined
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in joined
    assert "[REDACTED:github_token]" in joined
    assert entry.quality.status == "passed"
    assert entry.quality.summary["info_count"] == 1
    assert any(issue.code == "secret_redacted" for issue in entry.quality.issues)
    assert entry.stats.tool_call_count == 1
    assert entry.stats.tool_success_count == 1
    assert entry.stats.artifact_count == 2
    assert entry.stats.token_usage["total"]["input_tokens"] == 10
    assert entry.metadata["approvals"][0]["request_id"] == "approval-1"


def test_export_can_include_reasoning_and_sharegpt_payload() -> None:
    options = TrajectoryExportOptions(
        format=TrajectoryExportFormat.SHAREGPT,
        include_reasoning=True,
        include_system=True,
    )
    entry = ThreadTrajectoryExporter().export_thread(make_state(), options=options)

    assert entry.conversations[0].from_ == "system"
    assert "<think>" in entry.conversations[2].value
    payload = entry.to_sharegpt_payload()
    assert payload["conversations"][1]["from"] == "human"
    assert payload["metadata"]["thread_id"] == "thread-traj"


def test_export_compresses_middle_turns() -> None:
    state = make_state()
    state.conversation.messages = [
        {"role": "human", "id": f"u{index}", "content": f"request {index}"}
        for index in range(8)
    ]
    options = TrajectoryExportOptions(
        compression=TrajectoryCompressionConfig(
            enabled=True,
            max_turns=5,
            keep_first_turns=2,
            keep_last_turns=2,
        )
    )

    entry = ThreadTrajectoryExporter().export_thread(state, options=options)

    assert [turn.value for turn in entry.conversations] == [
        "request 0",
        "request 1",
        "[Anvil trajectory compression omitted 4 middle turns.]",
        "request 6",
        "request 7",
    ]
    assert entry.stats.omitted_turn_count == 4
    assert entry.quality.status == "failed"
    assert any(issue.code == "compressed_middle_turns" for issue in entry.quality.issues)
    assert any(issue.code == "missing_assistant_turn" for issue in entry.quality.issues)


def test_export_strips_inline_think_tags_and_reports_dangling_tool_calls() -> None:
    state = make_state()
    state.conversation.messages = [
        {"role": "human", "id": "u1", "content": "run a command"},
        {
            "role": "ai",
            "id": "a1",
            "content": "<think>private reasoning</think>\nI will run it.",
            "tool_calls": [
                {"id": "tc-missing", "name": "run_command", "args": {"command": "echo ok"}},
            ],
        },
    ]

    entry = ThreadTrajectoryExporter().export_thread(state)

    assert entry.conversations[1].value == "I will run it."
    assert entry.quality.status == "warning"
    assert any(issue.code == "tool_call_without_result" for issue in entry.quality.issues)


def test_export_threads_writes_jsonl(contract_tmp_path) -> None:
    path = contract_tmp_path / "trajectories.jsonl"
    result = ThreadTrajectoryExporter().export_threads([make_state()], path=path)

    assert result.exported_count == 1
    line = path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["thread_id"] == "thread-traj"
    assert payload["stats"]["tool_stats"]["write_file"]["count"] == 1


def test_export_parses_textual_tool_calls_for_dataset_metadata() -> None:
    state = make_state()
    state.conversation.messages = [
        {"role": "human", "id": "u1", "content": "search"},
        {
            "role": "ai",
            "id": "a1",
            "content": '```json\n{"name": "web_search", "args": {"query": "anvil"}}\n```',
        },
    ]

    entry = ThreadTrajectoryExporter().export_thread(state)

    parsed = entry.conversations[1].metadata["parsed_tool_calls"][0]
    assert parsed["name"] == "web_search"
    assert parsed["args"] == {"query": "anvil"}
    assert parsed["source_format"] == "fenced_json"


def test_export_parses_content_block_tool_calls_and_scrubs_metadata() -> None:
    state = make_state()
    state.conversation.messages = [
        {"role": "human", "id": "u1", "content": "fetch"},
        {
            "role": "ai",
            "id": "a1",
            "content": "",
            "content_blocks": [
                {"type": "thinking", "text": "<tool_call>{\"name\":\"leaked\",\"args\":{}}</tool_call>"},
                {
                    "type": "text",
                    "text": '<tool_call>{"name":"web_fetch","args":{"url":"https://example.com/?api_key=sk-testsecretsecretsecret"}}</tool_call>',
                },
            ],
        },
    ]

    entry = ThreadTrajectoryExporter().export_thread(state)

    parsed = entry.conversations[1].metadata["parsed_tool_calls"][0]
    assert parsed["name"] == "web_fetch"
    assert parsed["args"]["url"] == "https://example.com/?api_key=[REDACTED:api_key]"
    assert "sk-testsecretsecretsecret" not in json.dumps(entry.model_dump(mode="json"), ensure_ascii=False)
    assert all(call["name"] != "leaked" for call in entry.conversations[1].metadata["parsed_tool_calls"])


def test_export_can_disable_textual_tool_call_parsing() -> None:
    state = make_state()
    state.conversation.messages = [
        {"role": "human", "id": "u1", "content": "search"},
        {
            "role": "ai",
            "id": "a1",
            "content": '```json\n{"name": "web_search", "args": {"query": "anvil"}}\n```',
        },
    ]

    entry = ThreadTrajectoryExporter().export_thread(
        state,
        options=TrajectoryExportOptions(include_parsed_tool_calls=False),
    )

    assert "parsed_tool_calls" not in entry.conversations[1].metadata


def test_export_honors_tool_arg_toggle_for_parsed_tool_calls() -> None:
    state = make_state()
    state.conversation.messages = [
        {"role": "human", "id": "u1", "content": "search"},
        {
            "role": "ai",
            "id": "a1",
            "content": '```json\n{"name": "web_search", "args": {"query": "anvil"}}\n```',
        },
    ]

    entry = ThreadTrajectoryExporter().export_thread(
        state,
        options=TrajectoryExportOptions(include_tool_args=False),
    )

    parsed = entry.conversations[1].metadata["parsed_tool_calls"][0]
    assert parsed["name"] == "web_search"
    assert "args" not in parsed


def test_evaluation_report_summarizes_runtime_tools_memory_and_risks() -> None:
    state = make_state()
    state.execution.runtime_phase_timings = {
        "status": "completed",
        "total_elapsed_ms": 42_000,
        "marks": [
            {
                "phase": "runtime_assembled",
                "label": "Runtime assembled",
                "elapsed_ms": 400,
                "duration_since_previous_ms": 400,
            },
            {
                "phase": "first_content_delta",
                "label": "First content delta",
                "elapsed_ms": 41_000,
                "duration_since_previous_ms": 40_600,
            },
        ],
    }
    state.execution.context_window_usage = {
        "percent_used": 90,
        "auto_compact_threshold_percent": 80,
    }
    state.execution.runtime_assembly_snapshot = {
        "prompt": {
            "stable_prompt_tokens": 1200,
            "volatile_prompt_tokens": 80,
            "stable_section_tokens": {
                "role_and_intent": 120,
                "capability_summary": 700,
            },
            "volatile_section_tokens": {
                "request_context": 80,
            },
            "cache_delta": {
                "hits": 1,
                "misses": 0,
                "writes": 0,
                "bypasses": 0,
                "evictions": 0,
                "size_before": 1,
                "size_after": 1,
            },
            "cache": {
                "hits": 3,
                "misses": 2,
                "writes": 2,
                "bypasses": 0,
                "evictions": 0,
                "size": 2,
                "max_entries": 256,
            },
        },
        "capabilities": {
            "assembly_diagnostics": {
                "visible_tool_count": 12,
                "deferred_tool_count": 4,
                "visible_schema_tokens": 800,
                "visible_schema_token_budget": 1200,
                "schema_compacted_tool_count": 2,
                "schema_deferred_tool_count": 1,
                "action_prefilter_deferred_tool_count": 3,
            },
        },
        "memory_injection_diagnostics": {
            "source": "memory_manager",
            "status": "injected",
            "snapshot_id": "memory-snapshot-1",
            "query_tokens": 12,
            "memory_match_count": 2,
            "archive_hit_count": 1,
            "evidence_count": 3,
            "engine_note_count": 1,
            "rendered_tokens_before_truncation": 1200,
            "rendered_tokens": 900,
            "token_budget": 900,
            "truncated": True,
            "store_counts": {"project": 2},
            "source_kind_counts": {"memory": 2, "archive": 1},
        },
        "compaction_diagnostics": {
            "compaction_level": 2,
            "compaction_level_label": "recursive_summary",
            "compaction_reason": "threshold reached",
            "summary_source": "fallback",
            "summary_model": "minimax/MiniMax-M2.7",
            "archived_message_count": 12,
            "tool_call_count": 3,
            "tool_result_count": 2,
            "image_block_count": 1,
            "truncated_message_count": 2,
            "pruned_tool_result_count": 1,
            "serialized_tokens": 700,
            "summary_prompt_tokens": 900,
            "compaction_input_tokens": 3200,
            "compaction_summary_tokens": 280,
            "keep_recent_turns": 2,
        },
        "context_v2": {
            "enabled": True,
            "diagnostic_only": True,
            "fallback_used": False,
            "actual_prompt_mode": "runtime_context_v2",
            "actual_system_prompt_hash": "system-hash-1",
            "trace": {
                "trace_id": "ctx_trace_eval",
                "prompt_hash": "context-hash-1",
                "candidate_block_ids": ["prompt:role", "memory:fact", "capability:read_file", "memory:stale"],
                "selected_block_ids": ["prompt:role", "memory:fact", "capability:read_file"],
                "compressed_block_ids": ["capability:read_file"],
                "deferred_block_ids": [],
                "dropped_block_ids": ["memory:stale"],
                "layer_token_usage": {"prompt": 120, "semantic_fact": 24, "capability": 40},
                "selected_tools": ["read_file"],
                "selected_mcp_tools": [],
                "selected_skills": ["skill://coding"],
                "selected_memory": ["claim-1"],
                "selected_workspace": ["workspace:root"],
                "selected_events": ["evt-1"],
                "selected_tool_results": ["tc1"],
                "selected_tool_result_refs": ["artifact://thread-traj/tool-results/tc1.txt"],
                "block_traces": [
                    {
                        "block_id": "prompt:role",
                        "block_type": "prompt",
                        "source_kind": "prompt",
                        "token_cost": 120,
                        "selected": True,
                    },
                    {
                        "block_id": "memory:fact",
                        "block_type": "semantic_fact",
                        "source_kind": "memory",
                        "token_cost": 24,
                        "selected": True,
                    },
                    {
                        "block_id": "capability:read_file",
                        "block_type": "capability",
                        "source_kind": "capability",
                        "token_cost": 40,
                        "selected": True,
                    },
                    {
                        "block_id": "memory:stale",
                        "block_type": "semantic_fact",
                        "source_kind": "memory",
                        "token_cost": 16,
                        "selected": False,
                    },
                ],
                "drop_decisions": [{"block_id": "memory:stale", "reason": "low_salience"}],
                "total_tokens": 184,
                "budget": {
                    "max_context_tokens": 8192,
                    "reserved_response_tokens": 1024,
                    "soft_limit_ratio": 0.9,
                    "per_layer_token_budget": {},
                },
                "metadata": {"actual_system_prompt_hash": "system-hash-1"},
            },
            "diagnostics": {
                "retrieval_scores": [0.92, 0.73],
                "llm_output_ref": "artifact://thread-traj/llm/output.txt",
                "llm_output_tokens": 64,
                "llm_output_status": "completed",
                "llm_output": "never export raw llm output",
                "user_satisfaction_proxy": "positive",
            },
            "runtime_state": {
                "event_log": {
                    "event_types": [
                        "user_message_received",
                        "context_assembled",
                        "action_dispatch",
                        "tool_result",
                        "observation_handling",
                        "state_update",
                        "maintenance_scheduling",
                    ],
                    "events": [
                        {
                            "event_id": "evt-ctx-1",
                            "thread_id": "thread-traj",
                            "turn_id": "run-1",
                            "sequence": 1,
                            "event_type": "user_message_received",
                            "source_kind": "user",
                            "source_ref": "u1",
                            "trace_id": "ctx_trace_eval",
                            "payload_summary": "raw request should not become evaluation replay content",
                            "tool_result_refs": [],
                            "workspace_refs": [],
                            "memory_refs": [],
                            "metadata": {"phase": "intake", "raw_prompt": "never export this raw prompt"},
                            "created_at": "2026-06-08T00:00:00Z",
                        },
                        {
                            "event_id": "evt-ctx-2",
                            "thread_id": "thread-traj",
                            "turn_id": "run-1",
                            "sequence": 2,
                            "event_type": "context_assembled",
                            "source_kind": "runtime_context_v2",
                            "source_ref": "ctx_trace_eval",
                            "trace_id": "ctx_trace_eval",
                            "payload_summary": "assembled context",
                            "tool_result_refs": ["tc1"],
                            "workspace_refs": ["workspace:root"],
                            "memory_refs": ["claim-1"],
                            "metadata": {"phase": "context_assembly"},
                            "created_at": "2026-06-08T00:00:01Z",
                        },
                        {
                            "event_id": "evt-ctx-3",
                            "thread_id": "thread-traj",
                            "turn_id": "run-1",
                            "sequence": 3,
                            "event_type": "action_dispatch",
                            "source_kind": "tool",
                            "source_ref": "tc1",
                            "trace_id": "ctx_trace_eval",
                            "payload_summary": "dispatch write_file",
                            "tool_result_refs": [],
                            "workspace_refs": [],
                            "memory_refs": [],
                            "metadata": {
                                "phase": "action_dispatch",
                                "tool_name": "write_file",
                                "outcome": "success",
                                "raw_result": "never export raw tool outcome",
                            },
                            "created_at": "2026-06-08T00:00:02Z",
                        },
                        {
                            "event_id": "evt-ctx-4",
                            "thread_id": "thread-traj",
                            "turn_id": "run-1",
                            "sequence": 4,
                            "event_type": "observation_handling",
                            "source_kind": "tool",
                            "source_ref": "tc1",
                            "trace_id": "ctx_trace_eval",
                            "payload_summary": "observed summarized tool result",
                            "tool_result_refs": ["tc1"],
                            "workspace_refs": ["workspace:root"],
                            "memory_refs": [],
                            "metadata": {"phase": "observation_handling", "raw_output": "never export raw tool output"},
                            "created_at": "2026-06-08T00:00:03Z",
                        },
                        {
                            "event_id": "evt-ctx-5",
                            "thread_id": "thread-traj",
                            "turn_id": "run-1",
                            "sequence": 5,
                            "event_type": "state_update",
                            "source_kind": "workspace_state",
                            "source_ref": "workspace:root",
                            "trace_id": "ctx_trace_eval",
                            "payload_summary": "workspace updated",
                            "tool_result_refs": [],
                            "workspace_refs": ["workspace:root"],
                            "memory_refs": [],
                            "metadata": {
                                "phase": "state_update",
                                "user_correction": True,
                                "user_satisfaction_proxy": "positive",
                            },
                            "created_at": "2026-06-08T00:00:04Z",
                        },
                        {
                            "event_id": "evt-ctx-6",
                            "thread_id": "thread-traj",
                            "turn_id": "run-1",
                            "sequence": 6,
                            "event_type": "maintenance_scheduling",
                            "source_kind": "runtime",
                            "source_ref": "run-1",
                            "trace_id": "ctx_trace_eval",
                            "payload_summary": "maintenance scheduled",
                            "tool_result_refs": [],
                            "workspace_refs": [],
                            "memory_refs": [],
                            "metadata": {"phase": "maintenance_scheduling"},
                            "created_at": "2026-06-08T00:00:05Z",
                        },
                    ],
                },
            },
        },
    }
    state.execution.runtime_assembly_diff = {
        "baseline": "previous_run",
        "changed": True,
        "changed_paths": ["middleware_names", "enabled_feature_flags"],
        "changes": {
            "middleware_names": {"before": ["ClarificationMiddleware"], "after": []},
            "enabled_feature_flags": {"before": ["clarification"], "after": []},
        },
        "added": {},
        "removed": {"middleware_names": ["ClarificationMiddleware"]},
    }
    state.capabilities.visible_tool_names = ["read_file", "write_file"]
    state.capabilities.deferred_tool_names = ["browser_open"]
    state.capabilities.enabled_skill_ids = ["skill://coding"]
    state.memory.memory_namespace = "default/thread-traj"
    state.memory.injected_memory_snapshot_id = "memory-snapshot-1"

    report = ThreadEvaluationReportBuilder().build_thread_report(
        state,
        options=EvaluationReportOptions(include_markdown=True),
    )

    assert report.thread_id == "thread-traj"
    assert report.runtime.runtime_phase_timings["total_elapsed_ms"] == 42_000
    assert report.runtime.runtime_phase_diagnostics["slowest_phase"] == "first_content_delta"
    assert report.runtime.runtime_phase_diagnostics["slowest_phase_category"] == "provider_first_content_wait"
    assert report.runtime.runtime_phase_diagnostics["first_content_wait_ms"] == 40_600
    assert report.runtime.runtime_assembly_snapshot["prompt"]["cache_delta"]["hits"] == 1
    assert report.runtime.runtime_assembly_snapshot["capabilities"]["assembly_diagnostics"]["schema_deferred_tool_count"] == 1
    assert report.runtime.runtime_assembly_snapshot["memory_injection_diagnostics"]["memory_match_count"] == 2
    assert report.runtime.runtime_assembly_snapshot["compaction_diagnostics"]["tool_call_count"] == 3
    assert report.runtime.context_v2_evaluation["trace_id"] == "ctx_trace_eval"
    assert report.runtime.context_v2_evaluation["selected_tools"] == ["read_file"]
    assert report.runtime.context_v2_evaluation["selected_memory"] == ["claim-1"]
    assert report.runtime.context_v2_evaluation["selected_tool_result_refs"] == [
        "artifact://thread-traj/tool-results/tc1.txt"
    ]
    assert report.runtime.context_v2_evaluation["evaluation_run"]["trace_replay_ready"] is True
    assert report.runtime.context_v2_evaluation["evaluation_run"]["runtime_event_counts"]["action_dispatch"] == 1
    assert report.runtime.context_v2_evaluation["evaluation_run"]["replay_phase_coverage"] == {
        "action_dispatch": True,
        "maintenance_scheduling": True,
        "observation_handling": True,
        "state_update": True,
    }
    replay_matrix = report.runtime.context_v2_evaluation["evaluation_run"]["trace_replay_matrix"]
    assert replay_matrix == [
        {
            "case_id": "run-1:1",
            "trace_id": "ctx_trace_eval",
            "prompt_hash": "context-hash-1",
            "trace_replay_ready": True,
            "runtime_event_count": 6,
            "runtime_event_counts": {
                "action_dispatch": 1,
                "context_assembled": 1,
                "maintenance_scheduling": 1,
                "observation_handling": 1,
                "state_update": 1,
                "user_message_received": 1,
            },
            "runtime_event_refs": [
                "evt-ctx-1",
                "evt-ctx-2",
                "evt-ctx-3",
                "evt-ctx-4",
                "evt-ctx-5",
                "evt-ctx-6",
            ],
            "runtime_event_trace_ids": ["ctx_trace_eval"],
            "runtime_tool_result_refs": ["tc1"],
            "runtime_workspace_refs": ["workspace:root"],
            "runtime_memory_refs": ["claim-1"],
            "replay_phase_coverage": {
                "action_dispatch": True,
                "maintenance_scheduling": True,
                "observation_handling": True,
                "state_update": True,
            },
        }
    ]
    event_log_replay = report.runtime.context_v2_evaluation["evaluation_run"]["ablation_variant_metrics"][
        "event_log_replay"
    ]
    assert event_log_replay["enabled"] is True
    assert event_log_replay["trace_count"] == 1
    assert event_log_replay["token_overhead_ratio"] == 0.0257
    observability = report.runtime.context_v2_evaluation["runtime_observability"]
    assert observability["trace_id"] == "ctx_trace_eval"
    assert observability["prompt_hash"] == "context-hash-1"
    assert observability["actual_system_prompt_hash"] == "system-hash-1"
    assert observability["block_counts"] == {
        "candidate": 4,
        "selected": 3,
        "compressed": 1,
        "deferred": 0,
        "dropped": 1,
    }
    assert observability["total_tokens"] == 184
    assert observability["hard_context_tokens"] == 7168
    assert observability["selected_tools"] == ["read_file"]
    assert observability["selected_memory"] == ["claim-1"]
    assert observability["selected_tool_result_refs"] == ["artifact://thread-traj/tool-results/tc1.txt"]
    assert observability["tool_outcome_counts"] == {"success": 1}
    assert observability["tool_outcome_refs"] == ["evt-ctx-3"]
    assert observability["user_correction_count"] == 1
    assert observability["user_satisfaction_proxy"] == "positive"
    assert observability["llm_output_ref"] == "artifact://thread-traj/llm/output.txt"
    assert observability["llm_output_tokens"] == 64
    assert observability["llm_output_status"] == "completed"
    assert observability["retrieval_score_summary"] == {
        "count": 2,
        "min": 0.73,
        "max": 0.92,
        "average": 0.825,
    }
    assert report.runtime.runtime_assembly_diff["changed_paths"] == ["middleware_names", "enabled_feature_flags"]
    assert report.tool_calls[0].name == "write_file"
    assert report.memory.injected_memory_snapshot_id == "memory-snapshot-1"
    assert report.capabilities.enabled_skill_ids == ["skill://coding"]
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in str(report.model_dump(mode="json"))
    assert any(issue.code == "slow_runtime_phase" for issue in report.hidden_bug_risks)
    assert any(issue.code == "context_near_compaction" for issue in report.hidden_bug_risks)
    assert any("summarization model routing" in item for item in report.recommendations)
    assert any("compacted tool/message evidence" in item for item in report.recommendations)
    assert any("Compaction diagnostics include summary/source counters only" in item for item in report.notes)
    assert "Context V2 evaluation" in report.markdown
    assert "trace=ctx_trace_eval" in report.markdown
    assert "prompt_hash=context-hash-1" in report.markdown
    assert "replay_ready=True" in report.markdown
    assert "runtime_events=action_dispatch:1" in report.markdown
    assert "tools=read_file" in report.markdown
    assert "memory=claim-1" in report.markdown
    assert "never export this raw prompt" not in str(report.model_dump(mode="json"))
    assert "never export raw tool output" not in str(report.model_dump(mode="json"))
    assert "never export raw llm output" not in str(report.model_dump(mode="json"))
    assert "never export raw tool outcome" not in str(report.model_dump(mode="json"))
    assert report.score < 1.0


def test_evaluation_report_scrubs_raw_memory_context_from_default_v2_diagnostics() -> None:
    state = make_state()
    raw_memory_context = "<memory_context>never export raw v2 memory prompt</memory_context>"
    state.execution.runtime_assembly_snapshot = {
        "memory_injection_diagnostics": {
            "source": "runtime_context_v2",
            "status": "injected",
            "injection_mode": "context_v2",
            "memory_match_count": 1,
            "archive_hit_count": 0,
            "evidence_count": 1,
            "engine_note_count": 0,
            "rendered_tokens": 24,
            "token_budget": 256,
            "truncated": False,
            "context_v2_block_count": 1,
            "context_v2_memory_block_ids": ["memory:block:fact"],
            "memory_context": raw_memory_context,
            "rendered_memory_context": raw_memory_context,
            "raw_memory_context": raw_memory_context,
            "recall_payload": {"memory_context": raw_memory_context},
        },
        "context_v2": {
            "enabled": True,
            "diagnostic_only": False,
            "fallback_used": False,
            "actual_prompt_mode": "runtime_context_v2",
            "trace": {
                "trace_id": "ctx-memory-scrub",
                "selected_block_ids": ["prompt:role", "memory:block:fact"],
                "selected_memory": ["claim-1"],
                "total_tokens": 48,
                "budget": {
                    "max_context_tokens": 8192,
                    "reserved_response_tokens": 1024,
                },
            },
        },
    }

    report = ThreadEvaluationReportBuilder().build_thread_report(
        state,
        options=EvaluationReportOptions(include_markdown=True),
    )

    dumped = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    assert "never export raw v2 memory prompt" not in dumped
    assert "<memory_context>" not in dumped
    assert "never export raw v2 memory prompt" not in report.markdown
    diagnostics = report.runtime.runtime_assembly_snapshot["memory_injection_diagnostics"]
    assert diagnostics["injection_mode"] == "context_v2"
    assert diagnostics["context_v2_block_count"] == 1
    assert diagnostics["context_v2_memory_block_ids"] == ["memory:block:fact"]
    assert "memory_context" not in diagnostics
    assert "recall_payload" not in diagnostics
    assert "Memory injection diagnostics" in report.markdown
    assert "mode=context_v2" in report.markdown
    assert "blocks=1" in report.markdown


def test_evaluation_report_classifies_runtime_phase_diagnostics_markdown() -> None:
    state = make_state()
    state.execution.runtime_phase_timings = {
        "status": "completed",
        "total_elapsed_ms": 48_278,
        "runtime_assembly_elapsed_ms": 1_100,
        "model_start_wait_ms": 36,
        "first_model_event_elapsed_ms": 1_136,
        "first_content_delta_elapsed_ms": 47_693,
        "first_content_wait_ms": 46_557,
        "post_content_elapsed_ms": 585,
        "completed_elapsed_ms": 48_100,
        "marks": [
            {
                "phase": "agent_stream_entered",
                "label": "Agent stream entered",
                "elapsed_ms": 1_100,
                "duration_since_previous_ms": 120,
            },
            {
                "phase": "first_model_event",
                "label": "First graph/model event",
                "elapsed_ms": 1_136,
                "duration_since_previous_ms": 36,
            },
            {
                "phase": "first_content_delta",
                "label": "First content delta",
                "elapsed_ms": 47_693,
                "duration_since_previous_ms": 46_557,
            },
            {
                "phase": "run_completed_emitted",
                "label": "Run completed emitted",
                "elapsed_ms": 48_278,
                "duration_since_previous_ms": 178,
            },
        ],
    }

    report = ThreadEvaluationReportBuilder().build_thread_report(
        state,
        options=EvaluationReportOptions(include_markdown=True),
    )

    diagnostics = report.runtime.runtime_phase_diagnostics
    assert diagnostics["phase_count"] == 4
    assert diagnostics["slowest_phase"] == "first_content_delta"
    assert diagnostics["slowest_phase_category"] == "provider_first_content_wait"
    assert diagnostics["runtime_assembly_elapsed_ms"] == 1100
    assert diagnostics["model_start_wait_ms"] == 36
    assert diagnostics["first_model_event_elapsed_ms"] == 1136
    assert diagnostics["first_content_delta_elapsed_ms"] == 47693
    assert diagnostics["first_content_wait_ms"] == 46557
    assert diagnostics["post_content_elapsed_ms"] == 585
    assert "Runtime phase diagnostics" in report.markdown
    assert "category=provider_first_content_wait" in report.markdown
    assert "assembly_ms=1100" in report.markdown
    assert "model_start_wait_ms=36" in report.markdown
    assert any("first visible content" in item for item in report.recommendations)


def test_evaluation_report_classifies_interruption_root_causes() -> None:
    empty_final_state = make_state()
    empty_final_state.lifecycle.status = ThreadLifecycleStatus.INTERRUPTED
    empty_final_state.lifecycle.last_error = (
        "The model stopped after tool execution without producing a final answer. "
        "The run was marked interrupted so you can continue from the available tool results."
    )
    empty_final_state.execution.last_message_interrupted = True
    empty_final_state.execution.last_message_interrupted_reason = empty_final_state.lifecycle.last_error

    loop_state = make_state()
    loop_state.lifecycle.status = ThreadLifecycleStatus.INTERRUPTED
    loop_state.lifecycle.last_error = "Repeated internal tool loop stopped after 3 identical tool-call rounds."
    loop_state.execution.last_message_interrupted = True
    loop_state.execution.last_message_interrupted_reason = loop_state.lifecycle.last_error

    builder = ThreadEvaluationReportBuilder()
    empty_report = builder.build_thread_report(
        empty_final_state,
        options=EvaluationReportOptions(include_markdown=True),
    )
    loop_report = builder.build_thread_report(
        loop_state,
        options=EvaluationReportOptions(include_markdown=True),
    )

    assert empty_report.outcome == "interrupted"
    assert any(issue.code == "interruption:empty_final_after_tools" for issue in empty_report.hidden_bug_risks)
    assert any("provider stopped after tool execution" in item for item in empty_report.recommendations)
    assert "interruption:empty_final_after_tools" in empty_report.markdown
    assert loop_report.outcome == "interrupted"
    assert any(issue.code == "interruption:tool_loop_hard_stop" for issue in loop_report.hidden_bug_risks)
    assert any("repeated tool-call signatures" in item for item in loop_report.recommendations)
    assert "interruption:tool_loop_hard_stop" in loop_report.markdown


def test_evaluation_report_summarizes_step_chain_without_hidden_payloads() -> None:
    state = make_state()
    state.conversation.steps = [
        {
            "step_id": "a1:thinking",
            "message_id": "a1",
            "type": "thinking",
            "title": "已思考",
            "status": "success",
            "payload": "Need to inspect visible state.",
            "order": 0,
            "visibility": "chat",
            "duration_ms": 1200,
        },
        {
            "step_id": "a1:thinking:hidden",
            "message_id": "a1",
            "type": "thinking",
            "title": "Provider reasoning",
            "status": "success",
            "payload": "provider private reasoning SECRET_KEY=abc123456",
            "order": 1,
            "visibility": "hidden",
            "duration_ms": 300,
        },
        {
            "step_id": "a1:call:tc1",
            "message_id": "a1",
            "type": "call",
            "title": "已运行 read_file",
            "status": "success",
            "action": '{"path":"README.md","api_key":"sk-testsecretsecretsecret"}',
            "payload": "file content includes token ghp_abcdefghijklmnopqrstuvwxyz",
            "language": "json",
            "tool_name": "read_file",
            "tool_call_id": "tc1",
            "order": 2,
            "visibility": "chat",
            "duration_ms": 42,
        },
        {
            "step_id": "a2:content",
            "message_id": "a2",
            "type": "content",
            "title": "最终回答",
            "status": "success",
            "payload": "Done.",
            "order": 3,
            "visibility": "chat",
            "duration_ms": 10,
        },
    ]

    report = ThreadEvaluationReportBuilder().build_thread_report(
        state,
        options=EvaluationReportOptions(include_markdown=True, max_tool_result_chars=80),
    )
    dumped = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)

    assert report.step_chain.total == 4
    assert report.step_chain.type_counts == {"call": 1, "content": 1, "thinking": 2}
    assert report.step_chain.visible_step_count == 3
    assert report.step_chain.hidden_step_count == 1
    assert report.step_chain.items[1].visibility == "hidden"
    assert report.step_chain.items[1].payload_preview is None
    assert report.step_chain.items[2].tool_name == "read_file"
    assert "[REDACTED:github_token]" in report.step_chain.items[2].payload_preview
    assert "[REDACTED:api_key]" in report.step_chain.items[2].action_preview
    assert "provider private reasoning" not in dumped
    assert "SECRET_KEY=abc123456" not in dumped
    assert "sk-testsecretsecretsecret" not in dumped
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in dumped
    assert "### Step Chain" in report.markdown
    assert "read_file" in report.markdown
    assert "provider private reasoning" not in report.markdown
    assert any("Step chain includes durable step metadata" in item for item in report.notes)


def test_evaluation_report_flags_terminal_step_chain_anomalies() -> None:
    state = make_state()
    state.conversation.steps = [
        {
            "step_id": "a1:call:tc-running",
            "message_id": "a1",
            "type": "call",
            "title": "已运行 run_command",
            "status": "running",
            "tool_name": "run_command",
            "tool_call_id": "tc-running",
            "order": 0,
            "visibility": "chat",
        },
        {
            "step_id": "a1:call:tc-error",
            "message_id": "a1",
            "type": "call",
            "title": "已运行 failing_tool",
            "status": "error",
            "error": "tool failed",
            "tool_name": "failing_tool",
            "tool_call_id": "tc-error",
            "order": 1,
            "visibility": "chat",
        },
    ]

    report = ThreadEvaluationReportBuilder().build_thread_report(
        state,
        options=EvaluationReportOptions(include_markdown=True),
    )

    assert report.step_chain.open_step_count == 1
    assert report.step_chain.error_step_count == 1
    assert any(issue.code == "step_chain:open_steps_after_terminal" for issue in report.hidden_bug_risks)
    assert any(issue.code == "step_chain:error_steps_with_completed_run" for issue in report.hidden_bug_risks)
    assert any("pending/running steps remained" in item for item in report.recommendations)
    assert "step_chain:open_steps_after_terminal" in report.markdown


def test_evaluation_batch_report_aggregates_scores_and_missing_threads() -> None:
    state = make_state()
    report = ThreadEvaluationReportBuilder().build_batch_report(
        [state],
        requested_thread_ids=["thread-traj", "missing-thread"],
    )

    assert report.summary["thread_count"] == 1
    assert report.missing_thread_ids == ["missing-thread"]
    assert report.summary["tool_call_count"] == 1
    assert report.score == report.thread_reports[0].score


def test_evaluation_report_accepts_external_evaluator_and_renders_markdown(contract_tmp_path) -> None:
    state = make_state()
    state.execution.runtime_assembly_snapshot = {
        "prompt": {
            "project_context_cache_status": "hit",
            "runtime_path_cache_status": "hit",
            "project_context_file_count": 2,
            "project_context_truncated_file_count": 1,
            "project_context_total_chars": 4096,
            "runtime_path_root_count": 4,
            "runtime_path_host_bridge_count": 2,
            "stable_prompt_tokens": 1100,
            "volatile_prompt_tokens": 64,
            "stable_section_tokens": {"capability_summary": 650, "role_and_intent": 120},
            "volatile_section_tokens": {"request_context": 64},
            "cache_delta": {"hits": 0, "misses": 1, "writes": 1, "bypasses": 0, "evictions": 0, "size_before": 0, "size_after": 1},
            "cache": {"hits": 0, "misses": 1, "writes": 1, "bypasses": 0, "evictions": 0, "size": 1, "max_entries": 256},
        },
        "capabilities": {
            "assembly_diagnostics": {
                "visible_tool_count": 10,
                "deferred_tool_count": 2,
                "visible_schema_tokens": 900,
                "visible_schema_token_budget": 1000,
                "schema_compacted_tool_count": 1,
                "schema_deferred_tool_count": 1,
                "action_prefilter_deferred_tool_count": 0,
                "assembly_stage_durations_ms": {
                    "runtime_tools": 25,
                    "skills_discovery": 10,
                    "final_bundle": 42,
                    "total": 100,
                },
                "slowest_assembly_stage": "final_bundle",
                "slowest_assembly_stage_duration_ms": 42,
                "skills_discovery_cache_hit": False,
                "skills_discovery_manifest_count": 40,
                "skills_discovery_enabled_count": 35,
                "skills_discovery_stage_durations_ms": {
                    "resolve_roots": 5,
                    "loader_discover": 30,
                    "total": 42,
                },
                "slowest_skills_discovery_stage": "loader_discover",
                "slowest_skills_discovery_stage_duration_ms": 30,
            },
        },
        "memory_injection_diagnostics": {
            "source": "memory_manager",
            "status": "injected",
            "memory_match_count": 2,
            "archive_hit_count": 1,
            "evidence_count": 3,
            "engine_note_count": 1,
            "rendered_tokens": 900,
            "token_budget": 900,
            "truncated": True,
            "store_counts": {"project": 2},
            "source_kind_counts": {"memory": 2, "archive": 1},
        },
        "compaction_diagnostics": {
            "compaction_level": 1,
            "compaction_level_label": "summary",
            "compaction_reason": "threshold reached",
            "summary_source": "model",
            "summary_model": "minimax/MiniMax-M2.7",
            "archived_message_count": 8,
            "tool_call_count": 2,
            "tool_result_count": 2,
            "image_block_count": 1,
            "truncated_message_count": 1,
            "pruned_tool_result_count": 1,
            "serialized_tokens": 512,
            "summary_prompt_tokens": 640,
            "compaction_input_tokens": 2048,
            "compaction_summary_tokens": 220,
            "keep_recent_turns": 2,
            "secret_probe": "ghp_abcdefghijklmnopqrstuvwxyz",
        },
    }
    state.execution.runtime_assembly_diff = {
        "baseline": "previous_run",
        "changed": True,
        "changed_paths": ["model.model_name"],
        "changes": {"model.model_name": {"before": "openai", "after": "minimax_cn"}},
        "added": {},
        "removed": {},
    }
    builder = ThreadEvaluationReportBuilder()
    output_path = contract_tmp_path / "reports" / "eval.md"
    report = builder.build_batch_report(
        [state],
        requested_thread_ids=["thread-traj"],
        options=EvaluationReportOptions(include_markdown=True),
        evaluator_results={
            "thread-traj": EvaluationReportEvaluatorResult(
                evaluator="terminal-bench",
                score=0.75,
                max_score=1.0,
                passed=True,
                task_id="tb-task",
                summary="pytest passed",
            )
        },
        markdown_path=output_path,
    )

    assert report.thread_reports[0].evaluator is not None
    assert report.thread_reports[0].evaluator.evaluator == "terminal-bench"
    assert report.summary["external_evaluator_count"] == 1
    assert report.summary["external_evaluator_average_score"] == 0.75
    assert report.markdown_path == str(output_path.resolve())
    assert report.markdown is not None
    assert "Anvil Evaluation Report" in report.markdown
    assert "terminal-bench" in report.markdown
    assert "Prompt cache delta" in report.markdown
    assert "Project context cache" in report.markdown
    assert "Runtime path cache" in report.markdown
    assert "Context cache diagnostics" in report.markdown
    assert "files=2 truncated=1" in report.markdown
    assert "runtime_paths=hit roots=4" in report.markdown
    assert "Prompt section tokens" in report.markdown
    assert "stable_top=capability_summary:650" in report.markdown
    assert "Capability diagnostics" in report.markdown
    assert "slowest_stage=final_bundle" in report.markdown
    assert "slowest_stage_ms=42" in report.markdown
    assert "skills_cache_hit=False" in report.markdown
    assert "skills_slowest=loader_discover" in report.markdown
    assert "skills_stages=loader_discover:30" in report.markdown
    assert "Memory injection diagnostics" in report.markdown
    assert "memory=2 archive=1 evidence=3" in report.markdown
    assert "Compaction diagnostics" in report.markdown
    assert "level=1 label=summary" in report.markdown
    assert "source=model" in report.markdown
    assert "tool_calls=2 tool_results=2 images=1" in report.markdown
    assert "pruned_tools=1" in report.markdown
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in report.markdown
    assert "Runtime assembly diff" in report.markdown
    written = output_path.read_text(encoding="utf-8")
    assert "Thread Report: thread-traj" in written
    assert "terminal-bench" in written
