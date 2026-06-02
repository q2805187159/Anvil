from __future__ import annotations

import re
from collections import Counter

from .contracts import CuratedEntry, MemoryPolicyDecision
from .scrubber import MemorySecretScrubber


_THREAT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"ignore\s+(previous|all|above|prior)\s+instructions", "prompt_injection"),
    (r"you\s+are\s+now\s+", "role_hijack"),
    (r"system\s+prompt\s+override", "system_prompt_override"),
    (r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)", "disregard_rules"),
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfiltration_curl"),
    (r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfiltration_wget"),
    (r"authorized_keys", "ssh_backdoor"),
)

_CATEGORY_PREFIX_RE = re.compile(
    r"^(user preference|workspace fact|project constraint|environment fact|resolved outcome|outcome|workflow|correction)\s*:\s*",
    re.IGNORECASE,
)

_INVISIBLE_CHARS = {
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\ufeff",
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
}


def _normalize_for_memory_similarity(text: str) -> str:
    normalized = _CATEGORY_PREFIX_RE.sub("", str(text or "").strip())
    normalized = re.sub(r"\b(i|me|my|mine)\b", "user", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bthe user\b", "user", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\buser\s+(?:prefers?|likes?|wants?|needs?)\b", "user prefer", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\buser\s+(?:dislikes?|hates?)\b", "user dislike", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(?:prefer|prefers|preferred|like|likes|liked|want|wants|wanted|need|needs|needed)\b", "prefer", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(?:dislike|dislikes|disliked|hate|hates|hated)\b", "dislike", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(?:will|keep|continue|understood|noted|remembered|recorded)\b", " ", normalized, flags=re.IGNORECASE)
    return " ".join(normalized.lower().split())


def _normalize_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_/-]{2,}", _normalize_for_memory_similarity(text))


def _near_duplicate_score(left: str, right: str) -> float:
    left_tokens = set(_normalize_tokens(left))
    right_tokens = set(_normalize_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / max(union, 1)


class MemoryGuard:
    def __init__(
        self,
        *,
        max_chars: int = 8000,
        near_duplicate_threshold: float = 0.75,
        scrubber: MemorySecretScrubber | None = None,
    ) -> None:
        self.max_chars = max_chars
        self.near_duplicate_threshold = near_duplicate_threshold
        self.scrubber = scrubber or MemorySecretScrubber()

    def evaluate_write(
        self,
        *,
        layer_id: str,
        action: str,
        content: str,
        existing_entries: tuple[CuratedEntry, ...],
        current_entry_id: str | None = None,
    ) -> MemoryPolicyDecision:
        normalized_layer = layer_id.strip().lower()
        normalized_action = action.strip().lower()
        scrubbed = self.scrubber.scrub(content)
        normalized_content = scrubbed.text.strip()

        if normalized_layer not in {"user", "workspace"} and normalized_action in {"add", "replace", "remove", "consolidate"}:
            return MemoryPolicyDecision(
                allowed=False,
                reason=f"{normalized_layer} layer is not writable",
                error_code="layer_not_writable",
                sanitized_content=normalized_content,
                matched_rules=scrubbed.rule_ids,
            )

        if normalized_action != "remove" and not normalized_content:
            return MemoryPolicyDecision(
                allowed=False,
                reason="memory content cannot be empty",
                error_code="empty_content",
                sanitized_content=normalized_content,
                matched_rules=scrubbed.rule_ids,
            )

        if len(normalized_content) > self.max_chars:
            return MemoryPolicyDecision(
                allowed=False,
                reason=f"memory content exceeds {self.max_chars} chars",
                error_code="content_too_large",
                sanitized_content=normalized_content,
                matched_rules=scrubbed.rule_ids,
            )

        matched_rules: list[str] = []
        for invisible in _INVISIBLE_CHARS:
            if invisible in normalized_content:
                matched_rules.append(f"invisible_unicode_u+{ord(invisible):04x}")
                return MemoryPolicyDecision(
                    allowed=False,
                    reason="memory content contains invisible unicode",
                    error_code="invisible_unicode",
                    sanitized_content=normalized_content,
                    matched_rules=tuple([*scrubbed.rule_ids, *matched_rules]),
                )

        for pattern, rule_id in _THREAT_PATTERNS:
            if re.search(pattern, normalized_content, re.IGNORECASE):
                matched_rules.append(rule_id)
                return MemoryPolicyDecision(
                    allowed=False,
                    reason=f"memory content matches blocked pattern '{rule_id}'",
                    error_code="threat_pattern",
                    sanitized_content=normalized_content,
                    matched_rules=tuple([*scrubbed.rule_ids, *matched_rules]),
                )

        if normalized_action == "remove":
            return MemoryPolicyDecision(
                allowed=True,
                reason="remove action allowed",
                sanitized_content=normalized_content,
                matched_rules=scrubbed.rule_ids,
            )

        tokens = _normalize_tokens(normalized_content)
        if tokens:
            frequencies = Counter(tokens)
            dominant = max(frequencies.values())
            if dominant / max(len(tokens), 1) > 0.6:
                return MemoryPolicyDecision(
                    allowed=False,
                    reason="memory content is too repetitive to be useful",
                    error_code="low_information_density",
                    sanitized_content=normalized_content,
                    matched_rules=scrubbed.rule_ids,
                )

        duplicate_of: str | None = None
        near_duplicates: list[str] = []
        for entry in existing_entries:
            if current_entry_id is not None and entry.entry_id == current_entry_id:
                continue
            if entry.content.strip() == normalized_content:
                duplicate_of = entry.entry_id
                return MemoryPolicyDecision(
                    allowed=False,
                    reason="memory content duplicates an existing entry",
                    error_code="duplicate_entry",
                    sanitized_content=normalized_content,
                    duplicate_of=duplicate_of,
                    matched_rules=scrubbed.rule_ids,
                )
            similarity = _near_duplicate_score(entry.content, normalized_content)
            if similarity >= self.near_duplicate_threshold:
                near_duplicates.append(entry.entry_id)

        if near_duplicates:
            return MemoryPolicyDecision(
                allowed=False,
                reason="memory content is too similar to existing entries",
                error_code="near_duplicate_entry",
                sanitized_content=normalized_content,
                near_duplicates=tuple(sorted(set(near_duplicates))),
                matched_rules=scrubbed.rule_ids,
            )

        return MemoryPolicyDecision(
            allowed=True,
            reason="memory write accepted",
            sanitized_content=normalized_content,
            matched_rules=scrubbed.rule_ids,
        )

    def detect_conflicts(
        self,
        *,
        candidate_content: str,
        existing_entries: tuple[CuratedEntry, ...],
        current_entry_id: str | None = None,
    ) -> tuple[str, ...]:
        normalized = candidate_content.lower()
        conflicts: list[str] = []
        negation_markers = (" not ", " never ", " no ", " avoid ", " dislike ")
        candidate_is_negative = any(marker in f" {normalized} " for marker in negation_markers)
        for entry in existing_entries:
            if current_entry_id is not None and entry.entry_id == current_entry_id:
                continue
            existing_normalized = entry.content.lower()
            existing_is_negative = any(marker in f" {existing_normalized} " for marker in negation_markers)
            if candidate_is_negative == existing_is_negative:
                continue
            overlap = _near_duplicate_score(existing_normalized, normalized)
            if overlap >= 0.5:
                conflicts.append(entry.entry_id)
        return tuple(sorted(set(conflicts)))
