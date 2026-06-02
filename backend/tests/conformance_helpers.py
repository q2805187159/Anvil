from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


TIMESTAMP_KEYS = {
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "granted_at",
    "generated_at",
    "last_started_at",
    "last_refreshed_at",
}


def to_json_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def normalize_payload(value: Any, *, replacements: dict[str, str] | None = None) -> Any:
    replacements = replacements or {}
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key in TIMESTAMP_KEYS and isinstance(item, str):
                normalized[key] = "<timestamp>"
            else:
                normalized[key] = normalize_payload(item, replacements=replacements)
        return normalized
    if isinstance(value, list):
        return [normalize_payload(item, replacements=replacements) for item in value]
    if isinstance(value, tuple):
        return [normalize_payload(item, replacements=replacements) for item in value]
    if isinstance(value, str):
        result = value
        for original, target in replacements.items():
            result = result.replace(original, target)
        return result
    return value


def parse_sse_text(raw_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        payload = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
        events.append({"event": name, "data": json.loads(payload) if payload else {}})
    return events


def names_only(events: Iterable[dict[str, Any]]) -> list[str]:
    return [event["event"] for event in events]
