from __future__ import annotations

from datetime import datetime, timezone
from math import exp

from .contracts import CuratedEntry, utc_now


class MemoryResolutionService:
    def __init__(
        self,
        *,
        auto_accept_confidence: float = 0.82,
        auto_supersede_confidence: float = 0.90,
    ) -> None:
        self.auto_accept_confidence = auto_accept_confidence
        self.auto_supersede_confidence = auto_supersede_confidence

    def effective_score(self, entry: CuratedEntry, *, now: datetime | None = None) -> float:
        if entry.status in {"superseded", "rejected", "archived"}:
            return -1.0
        now = now or utc_now()
        age_days = max((now - entry.updated_at).days, 0)
        accessed_age = max((now - (entry.last_accessed_at or entry.updated_at)).days, 0)
        recency = exp(-age_days / 90)
        accessed = exp(-accessed_age / 45)
        evidence = min(len(entry.evidence_refs), 3) / 3
        conflict_penalty = 0.2 if entry.conflicts_with else 0.0
        expiry_penalty = 0.4 if entry.expires_at is not None and entry.expires_at <= now else 0.0
        score = (
            entry.priority * 0.28
            + entry.confidence * 0.30
            + entry.salience * 0.20
            + recency * 0.10
            + accessed * 0.07
            + evidence * 0.05
            - conflict_penalty
            - expiry_penalty
        )
        return max(score, -1.0)

    def should_auto_accept(self, *, confidence: float, evidence_refs: tuple[str, ...]) -> bool:
        return confidence >= self.auto_accept_confidence and bool(evidence_refs)

    def should_auto_supersede(
        self,
        *,
        confidence: float,
        supersedes: tuple[str, ...],
        conflicts_with: tuple[str, ...],
    ) -> bool:
        return confidence >= self.auto_supersede_confidence and bool(supersedes or conflicts_with)
