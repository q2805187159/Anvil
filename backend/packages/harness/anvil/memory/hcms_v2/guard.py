from __future__ import annotations

from anvil.memory.scrubber import MemorySecretScrubber

from .contracts import MemoryGuardDecision, ObservationRecord, stable_hcms_id


_PROMPT_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "reveal secrets",
    "exfiltrate",
    "forget all",
    "<memory_context",
    "</memory_context",
    "disregard instructions",
)

_UNTRUSTED_LEVELS = {"external", "untrusted"}


class MemoryGuard:
    """Deterministic HCMS V2 boundary guard for capture and injection decisions."""

    def __init__(self, *, scrubber: MemorySecretScrubber | None = None) -> None:
        self.scrubber = scrubber or MemorySecretScrubber()

    def inspect_observation(self, observation: ObservationRecord) -> MemoryGuardDecision:
        scrubbed = self.scrubber.scrub(observation.content)
        lowered = scrubbed.text.lower()
        detected_secret_ids = [
            str(item)
            for item in observation.metadata.get("detected_secret_rule_ids", [])
            if str(item or "").strip()
        ]
        reasons: list[str] = []
        if scrubbed.redacted or detected_secret_ids:
            reasons.append("secret_detected")
        if any(marker in lowered for marker in _PROMPT_INJECTION_MARKERS):
            reasons.append("prompt_injection_marker")
        if observation.trust_level in _UNTRUSTED_LEVELS:
            reasons.append("untrusted_source")
        if observation.privacy_level in {"secret", "quarantine"}:
            reasons.append(f"privacy_{observation.privacy_level}")

        if "prompt_injection_marker" in reasons and observation.trust_level in _UNTRUSTED_LEVELS:
            action = "quarantine"
            trust_score = 0.1
        elif "secret_detected" in reasons and observation.trust_level in _UNTRUSTED_LEVELS:
            action = "quarantine"
            trust_score = 0.15
        elif "secret_detected" in reasons:
            action = "redact"
            trust_score = 0.55
        elif observation.trust_level in _UNTRUSTED_LEVELS:
            action = "allow_no_inject"
            trust_score = 0.35
        elif observation.privacy_level == "quarantine":
            action = "quarantine"
            trust_score = 0.1
        else:
            action = "allow"
            trust_score = 0.85

        return MemoryGuardDecision(
            decision_id=stable_hcms_id("guard_v2", observation.observation_id, action, ",".join(reasons), size=16),
            source_ref=observation.observation_id,
            action=action,
            reasons=reasons,
            detected_secrets=list(dict.fromkeys([*scrubbed.rule_ids, *detected_secret_ids])),
            trust_score=trust_score,
            sanitized_content=scrubbed.text,
            metadata={
                "source_kind": observation.source_kind,
                "source_id": observation.source_id,
                "privacy_level": observation.privacy_level,
                "trust_level": observation.trust_level,
            },
        )


__all__ = ["MemoryGuard"]
