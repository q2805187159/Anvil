from __future__ import annotations

import sys
import os
from pathlib import Path

from anvil.processes import (
    ProcessService,
    ProcessSessionStatus,
    TerminalBackendCapabilities,
    TerminalBackendKind,
    TerminalBackendSpec,
    build_process_env,
    python_virtual_path_shim_dir,
)
from anvil.processes import service as process_service_module
from anvil.sandbox import PathService


def python_command(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def test_process_service_spawns_waits_and_reads_logs(contract_tmp_path: Path) -> None:
    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
    )
    session = service.spawn(
        thread_id="thread-1",
        command=python_command("print('hello process')"),
        cwd=str(contract_tmp_path),
    )

    finished = service.wait(session.session_id, timeout_seconds=5)
    log_view = service.read_log(session.session_id)

    assert finished.status is ProcessSessionStatus.COMPLETED
    assert finished.backend == "local"
    assert finished.backend_id == "local"
    assert finished.interactive is True
    assert "hello process" in log_view.output
    assert log_view.backend == "local"
    assert log_view.next_offset == log_view.total_lines
    service.close()


def test_process_service_reads_incremental_logs_by_cursor(contract_tmp_path: Path) -> None:
    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
    )
    session = service.spawn(
        thread_id="thread-cursor",
        command=python_command("print('line one'); print('line two'); print('line three')"),
        cwd=str(contract_tmp_path),
    )

    service.wait(session.session_id, timeout_seconds=5)
    first = service.read_log(session.session_id, cursor=0, limit=2)
    second = service.read_log(session.session_id, cursor=first.next_offset, limit=2)
    empty = service.read_log(session.session_id, cursor=second.next_offset, limit=2)

    assert first.output.splitlines() == ["line one", "line two"]
    assert first.next_offset == 2
    assert second.output.splitlines() == ["line three"]
    assert second.next_offset == 3
    assert empty.output == ""
    assert empty.next_offset == 3
    service.close()


def test_process_service_reports_terminal_capabilities(contract_tmp_path: Path) -> None:
    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
    )

    capabilities = service.capabilities()

    assert capabilities.kind == "local"
    assert capabilities.backend_id == "local"
    assert capabilities.label == "Local shell"
    assert capabilities.persistent_sessions is True
    assert capabilities.incremental_log is True
    assert capabilities.stdin is True
    service.close()


def test_build_process_env_adds_thread_paths_virtual_map_and_python_shim(contract_tmp_path: Path) -> None:
    path_service = PathService(base_root=contract_tmp_path / "threads")
    base_env = {"PYTHONPATH": "existing-path", "PATH": "bin"}

    env = build_process_env(
        path_service=path_service,
        thread_id="thread-env",
        base_env=base_env,
        extra_env={"CUSTOM": 123},
    )

    assert env["ANVIL_WORKSPACE"] == str(path_service.thread_workspace_dir("thread-env"))
    assert env["ANVIL_UPLOADS"] == str(path_service.thread_uploads_dir("thread-env"))
    assert env["ANVIL_OUTPUTS"] == str(path_service.thread_outputs_dir("thread-env"))
    assert env["ANVIL_SCRATCH"] == str(path_service.thread_scratch_dir("thread-env"))
    assert "/mnt/user-data/workspace" in env["ANVIL_VIRTUAL_PATH_MAP"]
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(python_virtual_path_shim_dir())
    assert env["PYTHONPATH"].split(os.pathsep)[1] == "existing-path"
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["CUSTOM"] == "123"


def test_build_process_env_preserves_reserved_runtime_keys_from_extra_env(contract_tmp_path: Path) -> None:
    path_service = PathService(base_root=contract_tmp_path / "threads")

    env = build_process_env(
        path_service=path_service,
        thread_id="thread-env",
        base_env={"PATH": "bin", "PYTHONPATH": "existing-path"},
        extra_env={
            "ANVIL_WORKSPACE": "/tmp/escape",
            "ANVIL_UPLOADS": "/tmp/uploads",
            "ANVIL_OUTPUTS": "/tmp/outputs",
            "ANVIL_SCRATCH": "/tmp/scratch",
            "ANVIL_VIRTUAL_PATH_MAP": "{}",
            "PYTHONPATH": "attacker-path",
            "PYTHONUTF8": "0",
            "PYTHONIOENCODING": "latin-1",
            "CUSTOM": "allowed",
        },
    )

    assert env["ANVIL_WORKSPACE"] == str(path_service.thread_workspace_dir("thread-env"))
    assert env["ANVIL_UPLOADS"] == str(path_service.thread_uploads_dir("thread-env"))
    assert env["ANVIL_OUTPUTS"] == str(path_service.thread_outputs_dir("thread-env"))
    assert env["ANVIL_SCRATCH"] == str(path_service.thread_scratch_dir("thread-env"))
    assert "/mnt/user-data/workspace" in env["ANVIL_VIRTUAL_PATH_MAP"]
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(python_virtual_path_shim_dir())
    assert env["PYTHONPATH"].split(os.pathsep)[1] == "existing-path"
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["CUSTOM"] == "allowed"


def test_process_service_reports_configured_reserved_backend(contract_tmp_path: Path) -> None:
    import anvil.processes.backends as backends

    original_which = backends.shutil.which
    backends.shutil.which = lambda name: None
    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
        backend="docker",
        backend_id="docker_lab",
        backend_label="Docker Lab",
        backend_notes=["adapter pending"],
    )
    try:
        capabilities = service.capabilities()

        assert capabilities.kind == "docker"
        assert capabilities.backend_id == "docker_lab"
        assert capabilities.label == "Docker Lab"
        assert capabilities.isolated is True
        assert capabilities.executable is False
        assert capabilities.launch_mode == "docker_run"
        assert capabilities.workspace_sync == "bind_mount"
        assert capabilities.required_executables == ["docker"]
        assert capabilities.missing_executables == ["docker"]
        assert "adapter pending" in capabilities.notes
        assert any("docker executable is not available" in note for note in capabilities.notes)
    finally:
        backends.shutil.which = original_which
        service.close()


def test_process_service_rejects_unready_backend_before_popen(contract_tmp_path: Path) -> None:
    class UnreadyAdapter:
        spec = TerminalBackendSpec(kind=TerminalBackendKind.DOCKER, backend_id="docker_missing")

        def capabilities(self) -> TerminalBackendCapabilities:
            return TerminalBackendCapabilities(
                kind=TerminalBackendKind.DOCKER,
                backend_id="docker_missing",
                configured=False,
                executable=False,
                missing_config=["image"],
                missing_executables=["docker"],
            )

        def prepare_launch(self, **kwargs):  # pragma: no cover - should not be reached
            raise AssertionError("prepare_launch should not run for an unready backend")

    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
        backend=TerminalBackendKind.DOCKER,
        backend_id="docker_missing",
        backend_adapter=UnreadyAdapter(),
    )
    try:
        try:
            service.spawn(thread_id="thread-1", command="pwd", cwd="/mnt/user-data/workspace")
        except RuntimeError as exc:
            assert "terminal backend 'docker_missing' is not ready: image, docker" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
    finally:
        service.close()


def test_process_service_can_kill_running_session(contract_tmp_path: Path) -> None:
    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
    )
    session = service.spawn(
        thread_id="thread-2",
        command=python_command("import time; print('starting'); time.sleep(30)"),
        cwd=str(contract_tmp_path),
    )

    killed = service.kill(session.session_id)

    assert killed.status is ProcessSessionStatus.KILLED
    service.close()


def test_process_service_timeout_terminates_running_session(contract_tmp_path: Path) -> None:
    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
    )
    session = service.spawn(
        thread_id="thread-timeout",
        command=python_command("import time; print('starting timeout'); time.sleep(30)"),
        cwd=str(contract_tmp_path),
    )
    try:
        timed_out = service.timeout(session.session_id, timeout_seconds=1)
        refreshed = service.get_session(session.session_id)

        assert timed_out.status is ProcessSessionStatus.TIMED_OUT
        assert timed_out.exit_code == -15
        assert timed_out.completed_at is not None
        assert refreshed is not None
        assert refreshed.status is ProcessSessionStatus.TIMED_OUT
    finally:
        service.close()


def test_process_service_wait_without_timeout_returns_running_after_bounded_default(contract_tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(process_service_module, "DEFAULT_PROCESS_WAIT_TIMEOUT_SECONDS", 0)
    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
    )
    session = service.spawn(
        thread_id="thread-default-wait",
        command=python_command("import time; print('still running'); time.sleep(30)"),
        cwd=str(contract_tmp_path),
    )
    try:
        waited = service.wait(session.session_id)

        assert waited.status is ProcessSessionStatus.RUNNING
    finally:
        service.kill(session.session_id)
        service.close()


def test_process_service_accepts_stdin_and_closes_input(contract_tmp_path: Path) -> None:
    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
    )
    session = service.spawn(
        thread_id="thread-stdin",
        command=python_command("import sys; value=sys.stdin.readline().strip(); print(f'got:{value}')"),
        cwd=str(contract_tmp_path),
    )

    updated = service.write_stdin(session.session_id, "hello", submit=True)
    finished = service.wait(session.session_id, timeout_seconds=5)
    closed = service.close_stdin(session.session_id)
    log_view = service.read_log(session.session_id)

    assert updated.input_history[-1].text_preview == "hello"
    assert updated.input_history[-1].submitted is True
    assert finished.status is ProcessSessionStatus.COMPLETED
    assert closed.stdin_closed is True
    assert "got:hello" in log_view.output
    service.close()


def test_process_service_tracks_interrupt_and_resize(contract_tmp_path: Path) -> None:
    service = ProcessService(
        sqlite_path=contract_tmp_path / "processes.sqlite3",
        logs_dir=contract_tmp_path / "process-logs",
    )
    session = service.spawn(
        thread_id="thread-interrupt",
        command=python_command("import time; print('waiting'); time.sleep(30)"),
        cwd=str(contract_tmp_path),
    )

    resized = service.resize(session.session_id, columns=120, rows=40)
    interrupted = service.interrupt(session.session_id)
    service.kill(session.session_id)

    assert resized.columns == 120
    assert resized.rows == 40
    assert interrupted.last_signal == "SIGINT"
    assert interrupted.last_signal_at is not None
    service.close()


def test_process_service_recovers_running_session_as_detached_or_interrupted(contract_tmp_path: Path) -> None:
    sqlite_path = contract_tmp_path / "processes.sqlite3"
    logs_dir = contract_tmp_path / "process-logs"
    service = ProcessService(sqlite_path=sqlite_path, logs_dir=logs_dir)
    session = service.spawn(
        thread_id="thread-3",
        command=python_command("import time; print('recover me'); time.sleep(2)"),
        cwd=str(contract_tmp_path),
    )
    session_id = session.session_id
    service.close()

    restored = ProcessService(sqlite_path=sqlite_path, logs_dir=logs_dir)
    recovered = restored.get_session(session_id)

    assert recovered is not None
    assert recovered.status in {ProcessSessionStatus.RUNNING, ProcessSessionStatus.INTERRUPTED}
    restored.close()
