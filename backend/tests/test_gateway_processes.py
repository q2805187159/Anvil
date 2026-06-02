from __future__ import annotations

import sys


def test_gateway_process_endpoints_list_wait_log_and_kill(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-proc"})
    deps = gateway_client.app.state.runtime_deps
    workspace = deps.path_service.base_root / "thread-proc" / "workspace"
    command = f'"{sys.executable}" -c "import os; print(os.getcwd())"'
    session = deps.process_service.spawn(
        thread_id="thread-proc",
        command=command,
        cwd=str(workspace),
    )

    capabilities = gateway_client.get("/threads/thread-proc/processes/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["kind"] == "local"
    assert capabilities.json()["backend_id"] == "local"
    assert capabilities.json()["executable"] is True

    listed = gateway_client.get("/threads/thread-proc/processes")
    assert listed.status_code == 200
    assert isinstance(listed.json(), list)
    assert listed.json()[0]["session_id"] == session.session_id
    assert listed.json()[0]["cwd"] == "/mnt/user-data/workspace"
    assert listed.json()[0]["backend"] == "local"
    assert listed.json()[0]["backend_id"] == "local"
    assert listed.json()[0]["interactive"] is True
    assert str(workspace) not in listed.text

    waited = gateway_client.post(f"/threads/thread-proc/processes/{session.session_id}/wait")
    assert waited.status_code == 200
    assert waited.json()["status"] in {"completed", "failed", "running"}
    assert waited.json()["cwd"] == "/mnt/user-data/workspace"
    assert str(workspace) not in waited.text

    log_view = gateway_client.get(f"/threads/thread-proc/processes/{session.session_id}/log")
    assert log_view.status_code == 200
    assert "/mnt/user-data/workspace" in log_view.json()["output"].replace("\\", "/")
    assert log_view.json()["next_offset"] >= 1
    assert log_view.json()["backend"] == "local"
    assert str(workspace) not in log_view.text

    cursor_view = gateway_client.get(f"/threads/thread-proc/processes/{session.session_id}/log?cursor={log_view.json()['next_offset']}")
    assert cursor_view.status_code == 200
    assert cursor_view.json()["output"] == ""

    chat_state = gateway_client.get("/threads/thread-proc/state")
    assert chat_state.status_code == 200
    assert chat_state.json()["process_sessions"] == []

    state = gateway_client.get("/threads/thread-proc/state?state_scope=full")
    assert state.status_code == 200
    assert state.json()["process_sessions"][0]["cwd"] == "/mnt/user-data/workspace"
    assert str(workspace) not in state.text

    detail = gateway_client.get("/threads/thread-proc/detail?state_scope=full")
    assert detail.status_code == 200
    assert detail.json()["state"]["process_sessions"][0]["cwd"] == "/mnt/user-data/workspace"
    assert str(workspace) not in detail.text


def test_gateway_process_spawn_endpoint_uses_virtual_cwd_and_translates_output(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-proc-spawn"})
    deps = gateway_client.app.state.runtime_deps
    workspace = deps.path_service.base_root / "thread-proc-spawn" / "workspace"
    command = (
        f'"{sys.executable}" -c "import os; '
        "print(os.environ['ANVIL_VIRTUAL_PATH_MAP']); "
        "print(os.getcwd())\""
    )

    spawned = gateway_client.post(
        "/threads/thread-proc-spawn/processes",
        json={"command": command, "cwd": "/mnt/user-data/workspace"},
    )
    assert spawned.status_code == 200
    assert spawned.json()["cwd"] == "/mnt/user-data/workspace"
    assert str(workspace) not in spawned.text

    session_id = spawned.json()["session_id"]
    waited = gateway_client.post(f"/threads/thread-proc-spawn/processes/{session_id}/wait?timeout_seconds=5")
    log_view = gateway_client.get(f"/threads/thread-proc-spawn/processes/{session_id}/log")
    assert waited.status_code == 200
    assert waited.json()["status"] == "completed", log_view.text
    assert log_view.status_code == 200
    assert "/mnt/user-data/workspace" in log_view.json()["output"]
    assert str(workspace) not in log_view.text


def test_gateway_process_kill_endpoint_kills_running_process(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-proc-kill"})
    deps = gateway_client.app.state.runtime_deps
    command = f'"{sys.executable}" -c "import time; print(\'bg\'); time.sleep(30)"'
    session = deps.process_service.spawn(
        thread_id="thread-proc-kill",
        command=command,
        cwd=str(deps.path_service.base_root / "thread-proc-kill" / "workspace"),
    )

    killed = gateway_client.post(f"/threads/thread-proc-kill/processes/{session.session_id}/kill")
    assert killed.status_code == 200
    assert killed.json()["status"] == "killed"


def test_gateway_process_terminal_control_endpoints(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-proc-terminal"})
    deps = gateway_client.app.state.runtime_deps
    workspace = deps.path_service.base_root / "thread-proc-terminal" / "workspace"
    command = f'"{sys.executable}" -c "import sys; value=sys.stdin.readline().strip(); print(f\'echo:{{value}}\')"'
    session = deps.process_service.spawn(
        thread_id="thread-proc-terminal",
        command=command,
        cwd=str(workspace),
    )

    resized = gateway_client.post(
        f"/threads/thread-proc-terminal/processes/{session.session_id}/resize",
        json={"columns": 100, "rows": 32},
    )
    assert resized.status_code == 200
    assert resized.json()["columns"] == 100
    assert resized.json()["rows"] == 32

    written = gateway_client.post(
        f"/threads/thread-proc-terminal/processes/{session.session_id}/stdin",
        json={"data": "typed line", "submit": True},
    )
    assert written.status_code == 200
    assert written.json()["input_history"][-1]["text_preview"] == "typed line"
    assert written.json()["input_history"][-1]["submitted"] is True

    waited = gateway_client.post(f"/threads/thread-proc-terminal/processes/{session.session_id}/wait?timeout_seconds=5")
    assert waited.status_code == 200
    assert waited.json()["status"] == "completed"

    closed = gateway_client.post(f"/threads/thread-proc-terminal/processes/{session.session_id}/stdin/close")
    assert closed.status_code == 200
    assert closed.json()["stdin_closed"] is True

    log_view = gateway_client.get(f"/threads/thread-proc-terminal/processes/{session.session_id}/log")
    assert "echo:typed line" in log_view.json()["output"]


def test_gateway_process_interrupt_endpoint_records_signal(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-proc-interrupt"})
    deps = gateway_client.app.state.runtime_deps
    command = f'"{sys.executable}" -c "import time; print(\'waiting\'); time.sleep(30)"'
    session = deps.process_service.spawn(
        thread_id="thread-proc-interrupt",
        command=command,
        cwd=str(deps.path_service.base_root / "thread-proc-interrupt" / "workspace"),
    )

    interrupted = gateway_client.post(f"/threads/thread-proc-interrupt/processes/{session.session_id}/interrupt")
    gateway_client.post(f"/threads/thread-proc-interrupt/processes/{session.session_id}/kill")

    assert interrupted.status_code == 200
    assert interrupted.json()["last_signal"] == "SIGINT"
    assert interrupted.json()["last_signal_at"] is not None
