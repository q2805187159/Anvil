from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from .contracts import MemoryPollutionMarker, utc_now


POLLUTING_TOOL_SOURCE_KINDS = frozenset({"mcp", "extension", "plugin", "future_app"})
POLLUTING_TOOL_CAPABILITY_GROUPS = frozenset(
    {
        "browser",
        "google",
        "google_workspace",
        "media",
        "research",
        "web",
    }
)
POLLUTING_TOOL_NAMES = frozenset(
    {
        "browser_back",
        "browser_cdp",
        "browser_click",
        "browser_close",
        "browser_console",
        "browser_dialog",
        "browser_get_images",
        "browser_navigate",
        "browser_press",
        "browser_screenshot",
        "browser_scroll",
        "browser_snapshot",
        "browser_type",
        "browser_vision",
        "image_search",
        "web_crawl",
        "web_extract",
        "web_fetch",
        "web_search",
        "gmail_create_draft",
        "gmail_labels",
        "gmail_read",
        "gmail_search",
        "gmail_send",
        "calendar_create_event",
        "calendar_delete_event",
        "calendar_free_busy",
        "calendar_list_events",
        "calendar_update_event",
        "speech_to_text",
        "text_to_speech",
    }
)


class MemoryPollutionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        if not self.path.exists():
            self._save(())

    def list_markers(self, *, thread_id: str | None = None, limit: int = 100) -> tuple[MemoryPollutionMarker, ...]:
        markers = list(self._load())
        if thread_id is not None:
            markers = [marker for marker in markers if marker.thread_id == thread_id]
        markers.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(markers[: max(1, min(limit, 500))])

    def mark(
        self,
        *,
        thread_id: str,
        source_kind: str,
        source_id: str | None = None,
        tool_name: str | None = None,
        reason: str,
        evidence_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryPollutionMarker:
        normalized_thread_id = str(thread_id or "").strip()
        if not normalized_thread_id:
            raise ValueError("thread_id is required")
        normalized_source_kind = str(source_kind or "unknown").strip().lower() or "unknown"
        normalized_source_id = str(source_id or "").strip() or None
        normalized_tool_name = str(tool_name or "").strip() or None
        normalized_evidence_ref = str(evidence_ref or "").strip() or None
        existing = list(self._load())
        for marker in existing:
            if (
                marker.thread_id == normalized_thread_id
                and marker.source_kind == normalized_source_kind
                and marker.source_id == normalized_source_id
                and marker.tool_name == normalized_tool_name
                and marker.evidence_ref == normalized_evidence_ref
            ):
                return marker
        marker = MemoryPollutionMarker(
            marker_id=f"pollution-{uuid4().hex[:16]}",
            thread_id=normalized_thread_id,
            source_kind=normalized_source_kind,
            source_id=normalized_source_id,
            tool_name=normalized_tool_name,
            reason=str(reason or "external source used").strip() or "external source used",
            evidence_ref=normalized_evidence_ref,
            metadata=dict(metadata or {}),
        )
        existing.append(marker)
        self._save(tuple(existing[-500:]))
        return marker

    def has_pollution(self, *, thread_id: str | None = None, evidence_refs: tuple[str, ...] = ()) -> bool:
        return self.first_match(thread_id=thread_id, evidence_refs=evidence_refs) is not None

    def first_match(
        self,
        *,
        thread_id: str | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> MemoryPollutionMarker | None:
        evidence = {str(item).strip() for item in evidence_refs if str(item).strip()}
        for marker in self.list_markers(limit=500):
            if thread_id and marker.thread_id == thread_id:
                return marker
            if marker.evidence_ref and marker.evidence_ref in evidence:
                return marker
        return None

    def _load(self) -> tuple[MemoryPollutionMarker, ...]:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return ()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return ()
        markers: list[MemoryPollutionMarker] = []
        for raw_item in raw_items:
            try:
                markers.append(MemoryPollutionMarker.model_validate(raw_item))
            except Exception:
                continue
        return tuple(markers)

    def _save(self, markers: tuple[MemoryPollutionMarker, ...]) -> None:
        payload = {"items": [marker.model_dump(mode="json") for marker in markers]}
        tmp_path = self.path.with_suffix(".json.tmp")
        with self._lock:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            tmp_path.replace(self.path)


def tool_activity_pollution_reason(activity: Any) -> str | None:
    name = _text(getattr(activity, "name", None)).lower()
    source_kind = _text(getattr(activity, "source_kind", None)).lower()
    capability_group = _text(getattr(activity, "capability_group", None)).lower()
    risk_category = _text(getattr(activity, "risk_category", None)).lower()
    if source_kind in POLLUTING_TOOL_SOURCE_KINDS:
        return f"external tool source kind '{source_kind}' used"
    if name in POLLUTING_TOOL_NAMES:
        return f"external information tool '{name}' used"
    if name.startswith("mcp_"):
        return f"MCP governance surface '{name}' used"
    if capability_group in POLLUTING_TOOL_CAPABILITY_GROUPS:
        return f"external capability group '{capability_group}' used"
    if risk_category in {"network_request", "web", "image_search"}:
        return f"network-risk tool '{name or risk_category}' used"
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()
