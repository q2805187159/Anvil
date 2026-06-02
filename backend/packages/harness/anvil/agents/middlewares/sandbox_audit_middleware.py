"""Safety and stability layer.

Reads: tool call, sandbox handle, config audit settings
Writes: structured sandbox audit log records
Side effects: JSONL audit logging
Failure behavior: fail-open; logging failures never block the pipeline
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Empty, Queue
import threading
import time

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


_AUDIT_QUEUE: Queue[str] = Queue()
_AUDIT_THREAD: threading.Thread | None = None
_AUDIT_THREAD_LOCK = threading.Lock()


@dataclass
class SandboxAuditEvent:
    thread_id: str
    turn_id: str
    event_type: str
    tool_name: str
    sandbox_mode: str
    timestamp: str
    args_fingerprint: str
    status: str | None = None
    output_size: int | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    escape_detected: bool = False


class SandboxAuditMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def wrap_tool_call(self, request, handler):
        config = request.runtime.context.config_result.effective_config.sandbox.audit
        if not config.enabled:
            return handler(request)
        handle = request.runtime.context.sandbox_handle or request.runtime.context.sandbox_provider.get(request.runtime.context.thread_id)
        sandbox_mode = handle.provider_mode if handle is not None else "unknown"
        started_at = time.time()
        begin = SandboxAuditEvent(
            thread_id=request.runtime.context.thread_id,
            turn_id=request.runtime.context.run_trace_id or "turn",
            event_type="sandbox_tool_started",
            tool_name=request.tool_call["name"],
            sandbox_mode=sandbox_mode,
            timestamp=datetime.now(timezone.utc).isoformat(),
            args_fingerprint=self._fingerprint(request.tool_call.get("args")),
        )
        self._emit(begin, config.log_path, async_write=config.async_write)
        response = handler(request)
        completed_at = time.time()
        event = SandboxAuditEvent(
            thread_id=request.runtime.context.thread_id,
            turn_id=request.runtime.context.run_trace_id or "turn",
            event_type="sandbox_tool_completed",
            tool_name=request.tool_call["name"],
            sandbox_mode=sandbox_mode,
            timestamp=datetime.now(timezone.utc).isoformat(),
            args_fingerprint=self._fingerprint(request.tool_call.get("args")),
            status=getattr(response, "status", None) or "completed",
            output_size=len(str(getattr(response, "content", "") or "")),
            duration_ms=max(int((completed_at - started_at) * 1000), 0),
            escape_detected=self._detect_escape(str(getattr(response, "content", "") or ""), handle.projection.policy_roots if handle is not None else []),
        )
        self._emit(event, config.log_path, async_write=config.async_write)
        return response

    def _emit(self, event: SandboxAuditEvent, log_path: str | None, *, async_write: bool) -> None:
        if not log_path:
            return
        payload = json.dumps(asdict(event), ensure_ascii=False)
        if async_write:
            self._ensure_writer(log_path)
            _AUDIT_QUEUE.put(payload)
            return
        _write_audit_line(log_path, payload)

    def _ensure_writer(self, log_path: str) -> None:
        global _AUDIT_THREAD
        with _AUDIT_THREAD_LOCK:
            if _AUDIT_THREAD is not None and _AUDIT_THREAD.is_alive():
                return
            _AUDIT_THREAD = threading.Thread(target=_audit_writer_loop, args=(log_path,), daemon=True)
            _AUDIT_THREAD.start()

    def _fingerprint(self, args) -> str:
        raw = json.dumps(args or {}, sort_keys=True, default=str)
        return raw[:256]

    def _detect_escape(self, content: str, policy_roots: list[str]) -> bool:
        if not content or not policy_roots:
            return False
        roots = [str(Path(root).resolve()) for root in policy_roots]
        for token in content.split():
            if not token.startswith("/") or token.startswith("/mnt/"):
                continue
            resolved = str(Path(token.strip(",;")).resolve())
            if not any(resolved.startswith(root) for root in roots):
                return True
        return False


def _audit_writer_loop(log_path: str) -> None:
    while True:
        try:
            payload = _AUDIT_QUEUE.get(timeout=0.5)
        except Empty:
            continue
        _write_audit_line(log_path, payload)
        _AUDIT_QUEUE.task_done()


def _write_audit_line(log_path: str, payload: str) -> None:
    path = Path(log_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")
