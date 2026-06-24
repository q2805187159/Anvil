from __future__ import annotations

from typing import Any


POLLUTING_TOOL_SOURCE_KINDS = frozenset({"mcp", "extension", "plugin", "future_app"})
POLLUTING_TOOL_CAPABILITY_GROUPS = frozenset({"browser", "google", "google_workspace", "media", "research", "web"})
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
