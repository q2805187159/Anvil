from __future__ import annotations

from datetime import datetime, timezone
from math import exp
from typing import Any

from .contracts import CuratedEntry, utc_now


RECENT_ACCESS_LIMIT = 20


def record_access_metadata(
    metadata: dict[str, Any] | None,
    *,
    source: str = "recall",
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = now or utc_now()
    payload = dict(metadata or {})
    access_count = _safe_int(payload.get("access_count"), default=0) + 1
    recent = _recent_accesses(payload.get("access_recent"))
    recent.append(timestamp.isoformat())
    payload["access_count"] = access_count
    payload["access_recent"] = recent[-RECENT_ACCESS_LIMIT:]
    payload["access_last_source"] = _safe_label(source)
    return payload


def retention_metrics(entry: CuratedEntry, *, now: datetime | None = None) -> dict[str, Any]:
    timestamp = now or utc_now()
    access_count = _safe_int(entry.metadata.get("access_count"), default=0)
    recent = _recent_accesses(entry.metadata.get("access_recent"))
    last_accessed_at = entry.last_accessed_at or _last_recent_access(recent)
    age_days = max(0.0, (timestamp - _as_aware(entry.created_at)).total_seconds() / 86400)
    salience = _bounded_float(entry.salience, default=0.0)
    confidence = _bounded_float(entry.confidence, default=0.0)
    temporal_decay = exp(-0.018 * age_days)
    reinforcement_boost = _reinforcement_boost(recent, access_count=access_count, now=timestamp)
    base_score = (salience * 0.72 + confidence * 0.18 + float(entry.priority or 0.0) * 0.10) * temporal_decay
    retention_score = min(1.0, max(0.0, base_score + reinforcement_boost))
    tier = "hot" if retention_score >= 0.70 else "warm" if retention_score >= 0.40 else "cold"
    return {
        "tier": tier,
        "retention_score": round(retention_score, 4),
        "salience": round(salience, 4),
        "temporal_decay": round(temporal_decay, 4),
        "reinforcement_boost": round(reinforcement_boost, 4),
        "access_count": access_count,
        "last_accessed_at": last_accessed_at,
    }


def _recent_accesses(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    recent: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            recent.append(text)
    return recent[-RECENT_ACCESS_LIMIT:]


def _last_recent_access(recent: list[str]) -> datetime | None:
    for item in reversed(recent):
        parsed = _parse_datetime(item)
        if parsed is not None:
            return parsed
    return None


def _reinforcement_boost(recent: list[str], *, access_count: int, now: datetime) -> float:
    if access_count <= 0 and not recent:
        return 0.0
    boost = min(0.18, max(access_count, len(recent)) * 0.018)
    for item in recent[-RECENT_ACCESS_LIMIT:]:
        parsed = _parse_datetime(item)
        if parsed is None:
            continue
        age_days = max(1.0, (now - parsed).total_seconds() / 86400)
        boost += min(0.045, 0.045 / age_days)
    return min(0.35, boost)


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_aware(parsed)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def _safe_label(value: str) -> str:
    normalized = "".join(char for char in value.strip().lower() if char.isalnum() or char in {"_", "-", ":"})
    return normalized[:48] or "recall"
