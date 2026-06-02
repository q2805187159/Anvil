from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


@dataclass
class McpServerRuntimeState:
    server_id: str
    status: str = "configured"
    connected: bool = False
    ready: bool = False
    auth_required: bool = False
    refresh_owner: str | None = None
    last_started_at: str | None = None
    last_refreshed_at: str | None = None
    last_error: str | None = None
    backoff_until: str | None = None
    reconnect_count: int = 0
    diagnostics: list[str] = field(default_factory=list)


class McpLifecycleManager:
    def __init__(self) -> None:
        self._states: dict[str, McpServerRuntimeState] = {}
        self._lock = Lock()

    def snapshot(self, server_id: str) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.get(server_id)
            if state is None:
                state = McpServerRuntimeState(server_id=server_id)
                self._states[server_id] = state
            return McpServerRuntimeState(**state.__dict__)

    def claim_refresh(self, server_id: str, owner: str) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.setdefault(server_id, McpServerRuntimeState(server_id=server_id))
            state.refresh_owner = owner
            state.status = "refreshing"
            return McpServerRuntimeState(**state.__dict__)

    def release_refresh(self, server_id: str, owner: str | None = None) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.setdefault(server_id, McpServerRuntimeState(server_id=server_id))
            if owner is None or state.refresh_owner == owner:
                state.refresh_owner = None
            return McpServerRuntimeState(**state.__dict__)

    def mark_starting(self, server_id: str) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.setdefault(server_id, McpServerRuntimeState(server_id=server_id))
            state.status = "starting"
            state.connected = False
            state.ready = False
            state.auth_required = False
            state.last_started_at = utc_now_iso()
            state.last_error = None
            state.backoff_until = None
            return McpServerRuntimeState(**state.__dict__)

    def mark_ready(self, server_id: str) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.setdefault(server_id, McpServerRuntimeState(server_id=server_id))
            if state.status == "refreshing":
                state.last_refreshed_at = utc_now_iso()
            else:
                state.last_started_at = state.last_started_at or utc_now_iso()
            state.status = "ready"
            state.connected = True
            state.ready = True
            state.auth_required = False
            state.last_error = None
            state.backoff_until = None
            state.refresh_owner = None
            return McpServerRuntimeState(**state.__dict__)

    def mark_refreshed(self, server_id: str) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.setdefault(server_id, McpServerRuntimeState(server_id=server_id))
            state.status = "ready"
            state.connected = True
            state.ready = True
            state.auth_required = False
            state.last_refreshed_at = utc_now_iso()
            state.last_error = None
            state.backoff_until = None
            state.refresh_owner = None
            return McpServerRuntimeState(**state.__dict__)

    def mark_auth_required(self, server_id: str, error: str) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.setdefault(server_id, McpServerRuntimeState(server_id=server_id))
            state.status = "auth_required"
            state.connected = False
            state.ready = False
            state.auth_required = True
            state.last_error = error
            state.diagnostics.append(error)
            state.refresh_owner = None
            return McpServerRuntimeState(**state.__dict__)

    def mark_failed(self, server_id: str, error: str, *, backoff_seconds: int | None = None) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.setdefault(server_id, McpServerRuntimeState(server_id=server_id))
            state.status = "failed" if not backoff_seconds else "backoff"
            state.connected = False
            state.ready = False
            state.auth_required = False
            state.last_error = error
            state.diagnostics.append(error)
            state.backoff_until = (
                (utc_now() + timedelta(seconds=backoff_seconds)).isoformat()
                if backoff_seconds
                else None
            )
            state.refresh_owner = None
            return McpServerRuntimeState(**state.__dict__)

    def mark_disconnected(self, server_id: str, reason: str | None = None) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.setdefault(server_id, McpServerRuntimeState(server_id=server_id))
            state.status = "disconnected"
            state.connected = False
            state.ready = False
            if reason:
                state.last_error = reason
                state.diagnostics.append(reason)
            state.refresh_owner = None
            return McpServerRuntimeState(**state.__dict__)

    def mark_reconnected(self, server_id: str) -> McpServerRuntimeState:
        with self._lock:
            state = self._states.setdefault(server_id, McpServerRuntimeState(server_id=server_id))
            state.connected = True
            state.reconnect_count += 1
            state.status = "starting"
            state.last_refreshed_at = utc_now_iso()
            state.last_error = None
            state.backoff_until = None
            return McpServerRuntimeState(**state.__dict__)
