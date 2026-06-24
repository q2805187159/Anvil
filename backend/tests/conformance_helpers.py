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
    "latest_activity_at",
    "timestamp",
}

VOLATILE_NUMERIC_KEYS = {
    "completed_elapsed_ms",
    "duration_ms",
    "duration_since_previous_ms",
    "elapsed_ms",
    "first_content_delta_elapsed_ms",
    "first_model_event_elapsed_ms",
    "post_content_elapsed_ms",
    "runtime_assembly_elapsed_ms",
    "total_elapsed_ms",
}

VOLATILE_RUNTIME_KEYS = {
    "item_id",
    "last_event_id",
    "run_id",
    "source_id",
}


def to_json_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def normalize_payload(
    value: Any,
    *,
    replacements: dict[str, str] | None = None,
    normalize_runtime_volatiles: bool = False,
) -> Any:
    replacements = replacements or {}
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key in TIMESTAMP_KEYS and isinstance(item, str):
                normalized[key] = "<timestamp>"
            elif (
                normalize_runtime_volatiles
                and (key in VOLATILE_NUMERIC_KEYS or key.endswith("_ms"))
                and isinstance(item, (int, float))
                and not isinstance(item, bool)
            ):
                normalized[key] = "<elapsed>"
            elif normalize_runtime_volatiles and key in VOLATILE_RUNTIME_KEYS and isinstance(item, str):
                normalized[key] = normalize_payload(
                    "<runtime_id>",
                    replacements=replacements,
                    normalize_runtime_volatiles=normalize_runtime_volatiles,
                )
            else:
                normalized[key] = normalize_payload(
                    item,
                    replacements=replacements,
                    normalize_runtime_volatiles=normalize_runtime_volatiles,
                )
        return normalized
    if isinstance(value, list):
        return [
            normalize_payload(
                item,
                replacements=replacements,
                normalize_runtime_volatiles=normalize_runtime_volatiles,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            normalize_payload(
                item,
                replacements=replacements,
                normalize_runtime_volatiles=normalize_runtime_volatiles,
            )
            for item in value
        ]
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
