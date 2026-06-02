from __future__ import annotations


def test_gateway_subagent_get_wait_and_cancel(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-sub"})
    deps = gateway_client.app.state.runtime_deps
    task = deps.subagent_service.submit(
        parent_thread_id="thread-sub",
        parent_run_id="run-sub",
        prompt="hello",
        parent_visible_tool_names=("read_file",),
        config_result=deps.config_result,
        runner=lambda: "done",
    )
    deps.subagent_service.wait(task.task_id, timeout_seconds=2)
    dependent = deps.subagent_service.submit(
        parent_thread_id="thread-sub",
        parent_run_id="run-sub",
        prompt="after hello",
        parent_visible_tool_names=("read_file",),
        config_result=deps.config_result,
        depends_on_task_ids=(task.task_id,),
        runner=lambda: "after done",
    )

    listed = gateway_client.get("/threads/thread-sub/subagents")
    assert listed.status_code == 200
    listed_ids = {item["task_id"] for item in listed.json()}
    assert {task.task_id, dependent.task_id}.issubset(listed_ids)

    graph = gateway_client.get("/threads/thread-sub/subagents/graph", params={"parent_run_id": "run-sub"})
    assert graph.status_code == 200
    graph_payload = graph.json()
    assert graph_payload["parent_thread_id"] == "thread-sub"
    assert graph_payload["parent_run_id"] == "run-sub"
    assert graph_payload["edges"] == [
        {
            "source_task_id": task.task_id,
            "target_task_id": dependent.task_id,
            "status": "satisfied",
            "source_status": "completed",
        }
    ]
    assert graph_payload["tasks"][0]["depends_on_task_ids"] == []
    assert graph_payload["tasks"][1]["depends_on_task_ids"] == [task.task_id]

    detail = gateway_client.get(f"/threads/thread-sub/subagents/{task.task_id}")
    assert detail.status_code == 200
    assert detail.json()["task_id"] == task.task_id

    waited = gateway_client.post(f"/threads/thread-sub/subagents/{task.task_id}/wait")
    assert waited.status_code == 200
    assert waited.json()["status"] in {"completed", "running", "queued"}

    cancelled = gateway_client.post(f"/threads/thread-sub/subagents/{task.task_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["task_id"] == task.task_id
