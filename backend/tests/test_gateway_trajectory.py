from __future__ import annotations

from fastapi.testclient import TestClient

from anvil.agents import ThreadLifecycleStatus, ThreadMetadataView, ThreadState


def test_gateway_exports_thread_trajectory(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-trajectory"})
        runtime_deps = client.app.state.runtime_deps
        state = runtime_deps.checkpointer.get_thread_state("thread-trajectory")
        assert state is not None
        updated = state.model_copy(deep=True)
        updated.lifecycle.status = ThreadLifecycleStatus.COMPLETED
        updated.conversation.messages = [
            {"role": "human", "id": "u1", "content": "hello"},
            {
                "role": "ai",
                "id": "a1",
                "content": "answer",
                "content_blocks": [
                    {"type": "thinking", "text": "hidden reasoning"},
                    {"type": "text", "text": "answer"},
                ],
            },
        ]
        runtime_deps.checkpointer.put_thread_state(updated)

        response = client.get("/threads/thread-trajectory/trajectory")

    assert response.status_code == 200
    payload = response.json()
    assert payload["thread_id"] == "thread-trajectory"
    assert payload["format"] == "anvil"
    assert payload["conversations"] == [
        {"from": "human", "value": "hello", "message_id": "u1", "role": "human", "metadata": {}},
        {"from": "gpt", "value": "answer", "message_id": "a1", "role": "ai", "metadata": {}},
    ]
    assert payload["quality"]["status"] == "passed"


def test_gateway_trajectory_post_overrides_reasoning_and_format(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app) as client:
        runtime_deps = client.app.state.runtime_deps
        state = ThreadState(
            identity={"thread_id": "thread-trajectory-reasoning"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={
                "messages": [
                    {"role": "human", "content": "hello"},
                    {
                        "role": "ai",
                        "content": "answer",
                        "content_blocks": [
                            {"type": "thinking", "text": "hidden reasoning"},
                            {"type": "text", "text": "answer"},
                        ],
                    },
                ]
            },
        )
        runtime_deps.checkpointer.put_thread_state(state)
        runtime_deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))
        response = client.post(
            "/threads/thread-trajectory-reasoning/trajectory",
            json={"format": "sharegpt", "include_reasoning": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "sharegpt"
    assert "<think>" in payload["conversations"][1]["value"]


def test_gateway_trajectory_post_can_disable_parsed_tool_call_metadata(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app) as client:
        runtime_deps = client.app.state.runtime_deps
        state = ThreadState(
            identity={"thread_id": "thread-trajectory-parser"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={
                "messages": [
                    {"role": "human", "content": "search"},
                    {
                        "role": "ai",
                        "content": '```json\n{"name": "web_search", "args": {"query": "anvil"}}\n```',
                    },
                ]
            },
        )
        runtime_deps.checkpointer.put_thread_state(state)
        runtime_deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))
        response = client.post(
            "/threads/thread-trajectory-parser/trajectory",
            json={"include_parsed_tool_calls": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversations"][1]["metadata"] == {}


def test_gateway_exports_trajectory_batch(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app) as client:
        runtime_deps = client.app.state.runtime_deps
        for thread_id, content in (("thread-batch-a", "a"), ("thread-batch-b", "b")):
            state = ThreadState(
                identity={"thread_id": thread_id},
                lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
                conversation={
                    "messages": [
                        {"role": "human", "content": content},
                        {"role": "ai", "content": f"answer {content}"},
                    ]
                },
            )
            runtime_deps.checkpointer.put_thread_state(state)
            runtime_deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))

        response = client.post(
            "/threads/trajectory/export",
            json={
                "thread_ids": ["thread-batch-a", "thread-batch-b"],
                "include_entries": True,
                "options": {"format": "sharegpt"},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["exported_count"] == 2
    assert payload["skipped_count"] == 0
    assert payload["format"] == "sharegpt"
    assert len(payload["entries"]) == 2
    assert payload["manifest"]["exported_count"] == 2
    assert payload["manifest"]["jsonl_path"].endswith(".jsonl")


def test_gateway_trajectory_batch_can_learn_procedures(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app) as client:
        runtime_deps = client.app.state.runtime_deps
        state = ThreadState(
            identity={"thread_id": "thread-gateway-learn", "run_id": "run-gateway-learn"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={
                "messages": [
                    {"role": "human", "content": "Inspect and verify a file."},
                    {"role": "ai", "content": "The file was inspected and verified."},
                ],
                "steps": [
                    {"type": "call", "step_id": "step-read", "tool_name": "read_file", "status": "success", "visibility": "chat"},
                    {"type": "call", "step_id": "step-search", "tool_name": "search_files", "status": "success", "visibility": "chat"},
                    {"type": "content", "payload": "The file was inspected and verified."},
                ],
            },
        )
        runtime_deps.checkpointer.put_thread_state(state)
        runtime_deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))
        response = client.post(
            "/threads/trajectory/export",
            json={
                "thread_ids": ["thread-gateway-learn"],
                "include_entries": True,
                "learn_procedures": True,
            },
        )

        procedures = runtime_deps.skills_service.manage_curator(
            config=runtime_deps.effective_config,
            action="procedures",
        )

    assert response.status_code == 200
    payload = response.json()
    learning = payload["manifest"]["stats"]["procedure_learning"]
    assert learning["enabled"] is True
    assert learning["accepted_count"] == 1
    assert procedures["counts"]["total"] == 1


def test_gateway_trajectory_batch_quality_gate_filters_bad_threads(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app) as client:
        runtime_deps = client.app.state.runtime_deps
        good = ThreadState(
            identity={"thread_id": "thread-quality-good"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={
                "messages": [
                    {"role": "human", "content": "hello"},
                    {"role": "ai", "content": "answer"},
                ]
            },
        )
        bad = ThreadState(
            identity={"thread_id": "thread-quality-bad"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={"messages": [{"role": "human", "content": "only user"}]},
        )
        for state in (good, bad):
            runtime_deps.checkpointer.put_thread_state(state)
            runtime_deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))
        response = client.post(
            "/threads/trajectory/export",
            json={
                "thread_ids": ["thread-quality-good", "thread-quality-bad"],
                "include_entries": True,
                "min_quality_status": "warning",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["exported_count"] == 1
    assert payload["skipped_count"] == 1
    assert [entry["thread_id"] for entry in payload["entries"]] == ["thread-quality-good"]
    assert "thread-quality-bad: filtered by quality gate failed < warning" in payload["diagnostics"]


def test_gateway_trajectory_batch_rejects_invalid_quality_gate(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app) as client:
        response = client.post(
            "/threads/trajectory/export",
            json={"min_quality_status": "excellent"},
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "invalid_trajectory_quality_status"
    assert "unsupported trajectory quality status" in payload["detail"]


def test_gateway_exports_thread_evaluation_report(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app) as client:
        runtime_deps = client.app.state.runtime_deps
        state = ThreadState(
            identity={"thread_id": "thread-eval", "run_id": "run-eval"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={
                "title": "Evaluation thread",
                "messages": [
                    {"role": "human", "id": "u1", "content": "Use token ghp_abcdefghijklmnopqrstuvwxyz carefully"},
                    {"role": "ai", "id": "a1", "content": "Done."},
                ],
                "steps": [
                    {
                        "step_id": "a1:thinking",
                        "message_id": "a1",
                        "type": "thinking",
                        "title": "已思考",
                        "status": "success",
                        "payload": "Need to answer visibly.",
                        "order": 0,
                        "visibility": "chat",
                    },
                ],
            },
            execution={
                "active_model": "minimax/MiniMax-M2.7",
                "runtime_phase_timings": {
                    "status": "completed",
                    "total_elapsed_ms": 50_000,
                    "marks": [
                        {
                            "phase": "first_content_delta",
                            "label": "First content delta",
                            "elapsed_ms": 45_000,
                            "duration_since_previous_ms": 45_000,
                        }
                    ],
                },
                "runtime_assembly_snapshot": {
                    "prompt": {
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
                            "misses": 1,
                            "writes": 1,
                            "bypasses": 0,
                            "evictions": 0,
                            "size": 1,
                            "max_entries": 256,
                        },
                    }
                },
            },
            capabilities={"enabled_skill_ids": ["skill://coding"]},
            memory={"injected_memory_snapshot_id": "memory-snapshot-1"},
        )
        runtime_deps.checkpointer.put_thread_state(state)
        runtime_deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))
        response = client.get("/threads/thread-eval/evaluation-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["thread_id"] == "thread-eval"
    assert payload["runtime"]["runtime_phase_timings"]["total_elapsed_ms"] == 50_000
    assert payload["runtime"]["runtime_assembly_snapshot"]["prompt"]["cache_delta"]["hits"] == 1
    assert payload["memory"]["injected_memory_snapshot_id"] == "memory-snapshot-1"
    assert payload["capabilities"]["enabled_skill_ids"] == ["skill://coding"]
    assert payload["step_chain"]["total"] == 1
    assert payload["step_chain"]["items"][0]["payload_preview"] == "Need to answer visibly."
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in response.text
    assert any(issue["code"] == "slow_runtime_phase" for issue in payload["hidden_bug_risks"])


def test_gateway_exports_evaluation_batch_report_with_missing_threads(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app) as client:
        runtime_deps = client.app.state.runtime_deps
        state = ThreadState(
            identity={"thread_id": "thread-eval-batch"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={
                "messages": [
                    {"role": "human", "content": "hello"},
                    {"role": "ai", "content": "answer"},
                ]
            },
        )
        runtime_deps.checkpointer.put_thread_state(state)
        runtime_deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))
        response = client.post(
            "/threads/evaluation-report",
            json={"thread_ids": ["thread-eval-batch", "missing-thread"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["thread_count"] == 1
    assert payload["missing_thread_ids"] == ["missing-thread"]
    assert payload["thread_reports"][0]["thread_id"] == "thread-eval-batch"


def test_gateway_evaluation_batch_report_accepts_external_scores_and_writes_markdown(gateway_app_factory, contract_tmp_path) -> None:
    app = gateway_app_factory()
    output_path = contract_tmp_path / "evaluation" / "report.md"
    with TestClient(app) as client:
        runtime_deps = client.app.state.runtime_deps
        state = ThreadState(
            identity={"thread_id": "thread-eval-markdown"},
            lifecycle={"status": ThreadLifecycleStatus.COMPLETED},
            conversation={
                "messages": [
                    {"role": "human", "content": "hello"},
                    {"role": "ai", "content": "answer"},
                ]
            },
        )
        runtime_deps.checkpointer.put_thread_state(state)
        runtime_deps.store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))
        response = client.post(
            "/threads/evaluation-report",
            json={
                "thread_ids": ["thread-eval-markdown"],
                "options": {"include_markdown": True},
                "evaluator_results": {
                    "thread-eval-markdown": {
                        "evaluator": "harbor",
                        "score": 0.5,
                        "max_score": 1.0,
                        "passed": False,
                        "summary": "hidden assertion failed",
                    }
                },
                "write_markdown": True,
                "output_path": str(output_path),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["external_evaluator_count"] == 1
    assert payload["summary"]["external_evaluator_failed_count"] == 1
    assert payload["thread_reports"][0]["evaluator"]["evaluator"] == "harbor"
    assert payload["markdown_path"] == str(output_path.resolve())
    assert "Anvil Evaluation Report" in payload["markdown"]
    assert output_path.read_text(encoding="utf-8").startswith("# Anvil Evaluation Report")
