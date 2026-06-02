from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contracts import (
    ProcessInputEvent,
    ProcessLogView,
    ProcessSessionStatus,
    ProcessSessionView,
    TerminalBackendCapabilities,
    TerminalBackendKind,
    TerminalBackendSpec,
)
from .backends import TerminalBackendAdapter, create_terminal_backend_adapter


DEFAULT_PROCESS_WAIT_TIMEOUT_SECONDS = 5
MAX_PROCESS_WAIT_TIMEOUT_SECONDS = 120


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_timeout_seconds(timeout_seconds: int | None, *, default_seconds: int, max_seconds: int) -> int:
    if timeout_seconds is None:
        return default_seconds
    return min(max(int(timeout_seconds), 0), max_seconds)


class ProcessService:
    def __init__(
        self,
        *,
        sqlite_path: str | Path,
        logs_dir: str | Path,
        backend: TerminalBackendKind | str = TerminalBackendKind.LOCAL,
        backend_id: str | None = None,
        backend_label: str | None = None,
        backend_notes: list[str] | None = None,
        backend_adapter: TerminalBackendAdapter | None = None,
    ) -> None:
        self._storage_path = Path(sqlite_path)
        self.logs_dir = Path(logs_dir)
        self.backend = TerminalBackendKind(backend)
        self.backend_id = backend_id or self.backend.value
        self.backend_label = backend_label or _backend_label(self.backend)
        self.backend_notes = list(backend_notes or [])
        self.backend_adapter = backend_adapter or create_terminal_backend_adapter(
            TerminalBackendSpec(
                kind=self.backend,
                backend_id=self.backend_id,
                label=self.backend_label,
                notes=self.backend_notes,
            )
        )
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._live: dict[str, subprocess.Popen] = {}
        self._closed = False
        if not self._storage_path.exists():
            self._storage_path.write_text("[]", encoding="utf-8")
        self._recover_detached()

    def capabilities(self) -> TerminalBackendCapabilities:
        return self.backend_adapter.capabilities()

    def close(self) -> None:
        if self._closed:
            return
        with self._lock:
            for process in self._live.values():
                if process.poll() is None:
                    _terminate_process_tree(process)
            self._live.clear()
            self._closed = True

    def spawn(self, *, thread_id: str, command: str, cwd: str, env: dict[str, str] | None = None) -> ProcessSessionView:
        capabilities = self.capabilities()
        if not capabilities.configured or not capabilities.executable:
            details = [*capabilities.missing_config, *capabilities.missing_executables]
            suffix = f": {', '.join(details)}" if details else ""
            raise RuntimeError(f"terminal backend '{capabilities.backend_id}' is not ready{suffix}")
        session_id = f"proc_{uuid.uuid4().hex[:12]}"
        log_path = self.logs_dir / f"{session_id}.log"
        launch = self.backend_adapter.prepare_launch(thread_id=thread_id, command=command, cwd=cwd, env=env or {})
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                launch.popen_args,
                cwd=launch.cwd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                shell=launch.shell,
                env=launch.env,
                executable=launch.executable,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                preexec_fn=None if os.name == "nt" else os.setsid,
            )
        view = ProcessSessionView(
            session_id=session_id,
            thread_id=thread_id,
            command=launch.display_command,
            cwd=cwd,
            backend=self.backend,
            backend_id=self.backend_id,
            backend_label=self.backend_label,
            interactive=True,
            pty=False,
            pid=process.pid,
            status=ProcessSessionStatus.RUNNING,
            log_path=str(log_path),
        )
        self._live[session_id] = process
        self._persist(view)
        return view


    def list_sessions(self, *, thread_id: str | None = None) -> tuple[ProcessSessionView, ...]:
        sessions = list(self._load_all())
        refreshed = [self._refresh(session) for session in sessions]
        if thread_id is not None:
            refreshed = [session for session in refreshed if session.thread_id == thread_id]
        return tuple(sorted(refreshed, key=lambda item: item.started_at, reverse=True))

    def get_session(self, session_id: str) -> ProcessSessionView | None:
        row = self._load(session_id)
        if row is None:
            return None
        return self._refresh(row)

    def wait(self, session_id: str, *, timeout_seconds: int | None = None) -> ProcessSessionView:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown process session: {session_id}")
        effective_timeout = _bounded_timeout_seconds(
            timeout_seconds if timeout_seconds is not None else self.backend_adapter.spec.timeout_seconds,
            default_seconds=DEFAULT_PROCESS_WAIT_TIMEOUT_SECONDS,
            max_seconds=MAX_PROCESS_WAIT_TIMEOUT_SECONDS,
        )
        deadline = time.time() + effective_timeout if effective_timeout is not None else None
        while session.status is ProcessSessionStatus.RUNNING:
            if deadline is not None and time.time() >= deadline:
                break
            time.sleep(0.2)
            session = self.get_session(session_id)
            if session is None:
                raise ValueError(f"unknown process session: {session_id}")
        return session

    def kill(self, session_id: str) -> ProcessSessionView:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown process session: {session_id}")
        process = self._live.get(session_id)
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
            self._live.pop(session_id, None)
        elif session.pid:
            _terminate_pid_tree(session.pid)
        session.status = ProcessSessionStatus.KILLED
        session.completed_at = utc_now()
        session.exit_code = -15
        session.last_output = self._tail_output(Path(session.log_path))
        self._persist(session)
        return session

    def timeout(self, session_id: str, *, timeout_seconds: int | None = None) -> ProcessSessionView:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown process session: {session_id}")
        if session.status is not ProcessSessionStatus.RUNNING:
            return session
        process = self._live.get(session_id)
        if process is not None and process.poll() is None:
            _terminate_process_tree(process, wait_timeout_seconds=timeout_seconds)
            self._live.pop(session_id, None)
        elif session.pid:
            _terminate_pid_tree(session.pid)
        session.status = ProcessSessionStatus.TIMED_OUT
        session.completed_at = utc_now()
        session.exit_code = -15
        session.last_signal = "SIGTERM"
        session.last_signal_at = session.completed_at
        session.last_output = self._tail_output(Path(session.log_path))
        session.log_cursor = self._line_count(Path(session.log_path))
        self._persist(session)
        return session

    def read_log(self, session_id: str, *, offset: int = 0, limit: int = 200, cursor: int | None = None) -> ProcessLogView:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown process session: {session_id}")
        output = _read_log_text(Path(session.log_path))
        lines = output.splitlines()
        total_lines = len(lines)
        start_offset = max(int(cursor if cursor is not None else offset), 0)
        selected = lines[-limit:] if cursor is None and offset == 0 else lines[start_offset:start_offset + limit]
        next_offset = total_lines if cursor is None and offset == 0 else min(start_offset + len(selected), total_lines)
        session.log_cursor = total_lines
        self._persist(session)
        return ProcessLogView(
            session_id=session_id,
            status=session.status,
            output="\n".join(selected),
            total_lines=total_lines,
            showing=f"{len(selected)} lines",
            next_offset=next_offset,
            start_offset=max(total_lines - len(selected), 0) if cursor is None and offset == 0 else start_offset,
            backend=session.backend,
            incremental=True,
        )

    def write_stdin(self, session_id: str, data: str, *, submit: bool = False) -> ProcessSessionView:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown process session: {session_id}")
        if session.stdin_closed:
            raise ValueError(f"process session '{session_id}' stdin is closed")
        process = self._live.get(session_id)
        if process is None or process.stdin is None or process.poll() is not None:
            raise ValueError(f"process session '{session_id}' is not accepting input")
        payload = f"{data}\n" if submit else data
        process.stdin.write(payload)
        process.stdin.flush()
        session.last_stdin_at = utc_now()
        session.input_history = _append_input_history(session.input_history, data, submitted=submit)
        self._persist(session)
        return session

    def close_stdin(self, session_id: str) -> ProcessSessionView:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown process session: {session_id}")
        process = self._live.get(session_id)
        if process is not None and process.stdin is not None:
            process.stdin.close()
        session.stdin_closed = True
        self._persist(session)
        return session

    def interrupt(self, session_id: str) -> ProcessSessionView:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown process session: {session_id}")
        process = self._live.get(session_id)
        if process is not None and process.poll() is None:
            if os.name == "nt":
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    process.terminate()
            else:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGINT)
                except Exception:
                    process.send_signal(signal.SIGINT)
        elif session.pid:
            try:
                os.kill(session.pid, signal.SIGINT)
            except Exception:
                pass
        session.last_signal = "SIGINT"
        session.last_signal_at = utc_now()
        self._persist(session)
        return session

    def resize(self, session_id: str, *, columns: int, rows: int) -> ProcessSessionView:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown process session: {session_id}")
        session.columns = max(int(columns), 1)
        session.rows = max(int(rows), 1)
        self._persist(session)
        return session

    def delete_for_thread(self, thread_id: str) -> int:
        sessions = [session for session in self._load_all() if session.thread_id == thread_id]
        for session in sessions:
            if session.status is ProcessSessionStatus.RUNNING:
                try:
                    self.kill(session.session_id)
                except Exception:
                    pass
        with self._lock:
            remaining = [session for session in self._load_all_unlocked() if session.thread_id != thread_id]
            self._save_all(tuple(remaining))
        for session in sessions:
            self._live.pop(session.session_id, None)
            try:
                Path(session.log_path).unlink(missing_ok=True)
            except Exception:
                pass
        return len(sessions)

    def _persist(self, session: ProcessSessionView) -> None:
        with self._lock:
            sessions = {item.session_id: item for item in self._load_all_unlocked()}
            sessions[session.session_id] = session
            self._save_all(tuple(sessions.values()))

    def _load(self, session_id: str) -> ProcessSessionView | None:
        for session in self._load_all():
            if session.session_id == session_id:
                return session
        return None

    def _load_all(self) -> list[ProcessSessionView]:
        with self._lock:
            return self._load_all_unlocked()

    def _load_all_unlocked(self) -> list[ProcessSessionView]:
        if not self._storage_path.exists():
            return []
        payload = json.loads(self._storage_path.read_text(encoding="utf-8") or "[]")
        return [ProcessSessionView.model_validate(item) for item in payload]

    def _save_all(self, sessions: tuple[ProcessSessionView, ...]) -> None:
        payload = [session.model_dump(mode="json") for session in sessions]
        self._storage_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _refresh(self, session: ProcessSessionView) -> ProcessSessionView:
        process = self._live.get(session.session_id)
        if process is not None:
            exit_code = process.poll()
            if exit_code is not None and session.status is ProcessSessionStatus.RUNNING:
                session.status = ProcessSessionStatus.COMPLETED if exit_code == 0 else ProcessSessionStatus.FAILED
                session.exit_code = exit_code
                session.completed_at = utc_now()
                session.last_output = self._tail_output(Path(session.log_path))
                session.log_cursor = self._line_count(Path(session.log_path))
                self._persist(session)
                self._live.pop(session.session_id, None)
            elif session.status is ProcessSessionStatus.RUNNING:
                session.last_output = self._tail_output(Path(session.log_path))
                session.log_cursor = self._line_count(Path(session.log_path))
                self._persist(session)
            return session

        if session.status is ProcessSessionStatus.RUNNING and session.pid is not None:
            if self._pid_alive(session.pid):
                session.detached = True
                session.last_output = self._tail_output(Path(session.log_path))
                session.log_cursor = self._line_count(Path(session.log_path))
                self._persist(session)
                return session
            session.status = ProcessSessionStatus.INTERRUPTED
            session.completed_at = utc_now()
            session.last_output = self._tail_output(Path(session.log_path))
            session.log_cursor = self._line_count(Path(session.log_path))
            self._persist(session)
        return session

    def _recover_detached(self) -> None:
        for session in self._load_all():
            if session.status is ProcessSessionStatus.RUNNING:
                self._refresh(session)

    def _tail_output(self, path: Path, limit: int = 4000) -> str:
        return _read_log_text(path)[-limit:]

    def _line_count(self, path: Path) -> int:
        return len(_read_log_text(path).splitlines())

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False
def _append_input_history(
    existing: list[ProcessInputEvent],
    data: str,
    *,
    submitted: bool,
    limit: int = 50,
) -> list[ProcessInputEvent]:
    preview = data.replace("\r", "\\r").replace("\n", "\\n")
    if len(preview) > 200:
        preview = f"{preview[:200]}...[truncated {len(preview) - 200} chars]"
    event = ProcessInputEvent(
        text_preview=preview,
        submitted=submitted,
        byte_count=len(data.encode("utf-8", errors="replace")),
    )
    return [*existing, event][-limit:]


def _read_log_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _terminate_process_tree(process: subprocess.Popen, *, wait_timeout_seconds: int | float | None = None) -> None:
    if os.name == "nt":
        _terminate_pid_tree(process.pid)
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
    if wait_timeout_seconds is None or wait_timeout_seconds <= 0:
        return
    try:
        process.wait(timeout=max(float(wait_timeout_seconds), 0.1))
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            _terminate_pid_tree(process.pid)
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


def _terminate_pid_tree(pid: int) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            return
        except Exception:
            pass
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _backend_label(kind: TerminalBackendKind) -> str:
    labels = {
        TerminalBackendKind.LOCAL: "Local shell",
        TerminalBackendKind.DOCKER: "Docker shell",
        TerminalBackendKind.SSH: "SSH shell",
        TerminalBackendKind.SINGULARITY: "Singularity shell",
        TerminalBackendKind.MODAL: "Modal shell",
        TerminalBackendKind.DAYTONA: "Daytona shell",
        TerminalBackendKind.VERCEL: "Vercel sandbox shell",
    }
    return labels[kind]
