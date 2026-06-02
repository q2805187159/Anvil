from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .contracts import sanitize_memory_context_text, utc_now


class MemorySessionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    snapshot_id: str
    fingerprint: str
    content: str = ""
    provider_block: str = ""
    status: str = "frozen"
    audit: tuple[dict[str, str], ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    refreshed_at: datetime = Field(default_factory=utc_now)


class MemorySessionSnapshotStore:
    def __init__(self, base_path: str | Path, *, enabled: bool = True) -> None:
        self.base_path = Path(base_path).expanduser().resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self._lock = Lock()

    def get(self, thread_id: str) -> MemorySessionSnapshot | None:
        path = self._path(thread_id)
        if not self.enabled or not path.exists():
            return None
        try:
            return MemorySessionSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            self._quarantine_corrupt(path)
            return None

    def get_or_create(
        self,
        *,
        thread_id: str,
        content: str,
        provider_block: str = "",
        refresh: bool = False,
        reason: str = "first_run",
    ) -> MemorySessionSnapshot:
        if not self.enabled:
            return self._build(thread_id=thread_id, content=content, provider_block=provider_block, reason="disabled")
        current = self.get(thread_id)
        if current is not None and not refresh:
            return current
        if current is None:
            snapshot = self._build(thread_id=thread_id, content=content, provider_block=provider_block, reason=reason)
        else:
            snapshot = self._build(
                thread_id=thread_id,
                content=content,
                provider_block=provider_block,
                reason=reason,
                existing=current,
            )
        self.save(snapshot)
        return snapshot

    def save(self, snapshot: MemorySessionSnapshot) -> None:
        path = self._path(snapshot.thread_id)
        tmp_path = path.with_suffix(".json.tmp")
        payload = snapshot.model_dump(mode="json")
        with self._lock:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            tmp_path.replace(path)

    def delete(self, thread_id: str) -> bool:
        path = self._path(thread_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _path(self, thread_id: str) -> Path:
        safe = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:24]
        return self.base_path / f"{safe}.json"

    def _quarantine_corrupt(self, path: Path) -> None:
        stamp = utc_now().strftime("%Y%m%d%H%M%S%f")
        target = path.with_name(f"{path.name}.corrupt-{stamp}-{uuid4().hex[:8]}")
        with self._lock:
            if path.exists():
                try:
                    path.replace(target)
                except OSError:
                    pass

    def _build(
        self,
        *,
        thread_id: str,
        content: str,
        provider_block: str,
        reason: str,
        existing: MemorySessionSnapshot | None = None,
    ) -> MemorySessionSnapshot:
        now = utc_now()
        safe_content = sanitize_memory_context_text(content.strip())
        safe_provider_block = sanitize_memory_context_text(provider_block.strip())
        rendered = "\n\n".join(part for part in (safe_content, safe_provider_block) if part)
        fingerprint = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        previous_audit = tuple(existing.audit) if existing is not None else ()
        audit = (
            *previous_audit,
            {
                "reason": reason,
                "fingerprint": fingerprint,
                "created_at": now.isoformat(),
            },
        )
        return MemorySessionSnapshot(
            thread_id=thread_id,
            snapshot_id=f"memory-session-{uuid4().hex[:12]}",
            fingerprint=fingerprint,
            content=rendered,
            provider_block=safe_provider_block,
            status="frozen",
            audit=audit[-20:],
            created_at=existing.created_at if existing is not None else now,
            refreshed_at=now,
        )
