from __future__ import annotations

from collections import defaultdict

from .adapters import conflict_record_to_warning_block
from .contracts import ClaimRecord, ConflictRecord, normalize_claim_text, stable_hcms_id


class ConflictLedger:
    """Baseline deterministic conflict ledger for exact HCMS V2 contradictions."""

    def detect_exact_conflicts(self, claims: list[ClaimRecord]) -> list[ConflictRecord]:
        by_key: dict[tuple[str, str, str, str, str | None], list[ClaimRecord]] = defaultdict(list)
        for claim in claims:
            key = (
                claim.namespace,
                claim.scope.scope_type,
                claim.scope.scope_key,
                normalize_claim_text(claim.subject),
                normalize_claim_text(claim.predicate),
            )
            by_key[key].append(claim)

        conflicts: list[ConflictRecord] = []
        for grouped in by_key.values():
            object_values = {normalize_claim_text(claim.object_value) for claim in grouped}
            if len(object_values) <= 1:
                continue
            preferred = max(grouped, key=lambda claim: (claim.source_priority, claim.confidence, claim.updated_at))
            conflict = ConflictRecord(
                conflict_id=stable_hcms_id(
                    "conflict_v2",
                    grouped[0].namespace,
                    "contradiction",
                    *sorted(str(claim.claim_id) for claim in grouped),
                    size=16,
                ),
                namespace=grouped[0].namespace,
                claim_ids=[str(claim.claim_id) for claim in grouped],
                conflict_type="contradiction",
                severity="high",
                status="needs_review",
                detection_method="exact_normalized",
                explanation=_contradiction_explanation(grouped),
                preferred_claim_id=str(preferred.claim_id),
                resolution_policy="prefer_highest_source_priority_after_review",
                injection_policy="inject_warning",
                metadata={
                    "normalized_subject": normalize_claim_text(grouped[0].subject),
                    "normalized_predicate": normalize_claim_text(grouped[0].predicate),
                    "object_values": sorted(object_values),
                },
            )
            conflicts.append(conflict)
        return conflicts

    def record_user_correction(self, previous_claim: ClaimRecord, correction_claim: ClaimRecord) -> ConflictRecord:
        return ConflictRecord(
            conflict_id=stable_hcms_id(
                "conflict_v2",
                previous_claim.namespace,
                "user_correction",
                previous_claim.claim_id,
                correction_claim.claim_id,
                size=16,
            ),
            namespace=previous_claim.namespace,
            claim_ids=[str(previous_claim.claim_id), str(correction_claim.claim_id)],
            conflict_type="user_correction",
            severity="high",
            status="needs_review",
            detection_method="user_feedback",
            explanation=f"User correction supersedes prior claim: {previous_claim.human_text} -> {correction_claim.human_text}",
            preferred_claim_id=str(correction_claim.claim_id),
            resolution_policy="prefer_explicit_user_correction",
            injection_policy="inject_warning",
        )

    def conflict_to_warning_block(self, conflict: ConflictRecord):
        return conflict_record_to_warning_block(conflict)


def _contradiction_explanation(claims: list[ClaimRecord]) -> str:
    subject = claims[0].subject
    predicate = claims[0].predicate
    values = ", ".join(sorted({claim.object_value for claim in claims}))
    return f"Claims disagree for {subject} {predicate}: {values}."


__all__ = ["ConflictLedger"]
