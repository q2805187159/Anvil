from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class PromptSnapshotStore:
    def __init__(
        self,
        base_path: str | Path,
        *,
        enabled: bool = True,
        ttl_days: int = 7,
        max_snapshots_per_thread: int = 10,
    ) -> None:
        self.base_path = Path(base_path).expanduser().resolve()
        self.enabled = enabled
        self.ttl_days = ttl_days
        self.max_snapshots_per_thread = max_snapshots_per_thread
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def record(
        self,
        *,
        thread_id: str,
        snapshot_id: str,
        prompt_hash: str,
        prompt_text: str,
        skills_fingerprint: str | None,
        memory_fingerprint: str | None,
        config_fingerprint: str,
    ) -> dict[str, Any]:
        record = {
            "snapshot_id": snapshot_id,
            "prompt_hash": prompt_hash,
            "prompt_text": prompt_text,
            "skills_fingerprint": skills_fingerprint,
            "memory_fingerprint": memory_fingerprint,
            "config_fingerprint": config_fingerprint,
            "created_at": _utcnow_iso(),
        }
        if not self.enabled:
            return record
        with self._lock:
            existing = self.list_for_thread(thread_id)
            if existing and existing[-1]["prompt_hash"] == prompt_hash:
                return existing[-1]
            existing.append(record)
            self._write(thread_id, existing)
            self.maybe_prune(thread_id)
        return record

    def latest_for_thread(self, thread_id: str) -> dict[str, Any] | None:
        snapshots = self.list_for_thread(thread_id)
        return snapshots[-1] if snapshots else None

    def list_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        path = self._path(thread_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return payload if isinstance(payload, list) else []

    def _write(self, thread_id: str, snapshots: list[dict[str, Any]]) -> None:
        path = self._path(thread_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshots, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def prune_expired(self) -> int:
        removed = 0
        cutoff = _cutoff_iso(self.ttl_days)
        for path in self.base_path.glob("*.json"):
            snapshots = self._read_path(path)
            fresh = [item for item in snapshots if item.get("created_at", "") >= cutoff]
            removed += max(len(snapshots) - len(fresh), 0)
            if len(fresh) != len(snapshots):
                self._write(path.stem, fresh)
        return removed

    def prune_excess(self, thread_id: str, keep: int | None = None) -> int:
        snapshots = self.list_for_thread(thread_id)
        limit = max(keep if keep is not None else self.max_snapshots_per_thread, 0)
        if len(snapshots) <= limit:
            return 0
        trimmed = snapshots[-limit:]
        removed = len(snapshots) - len(trimmed)
        self._write(thread_id, trimmed)
        return removed

    def maybe_prune(self, thread_id: str) -> None:
        if not self.enabled:
            return
        self.prune_expired()
        self.prune_excess(thread_id)

    def delete_for_thread(self, thread_id: str) -> bool:
        path = self._path(thread_id)
        if not path.exists():
            return False
        path.unlink(missing_ok=True)
        return True

    def _path(self, thread_id: str) -> Path:
        return self.base_path / f"{thread_id}.json"

    def _read_path(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return payload if isinstance(payload, list) else []


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _cutoff_iso(ttl_days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=max(ttl_days, 0))).isoformat()
