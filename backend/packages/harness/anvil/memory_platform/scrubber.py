from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryScrubResult:
    text: str
    rule_ids: tuple[str, ...] = ()

    @property
    def redacted(self) -> bool:
        return bool(self.rule_ids)


class MemorySecretScrubber:
    """Deterministic scrubber for secrets before memory is persisted or injected."""

    _TOKEN_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
        (
            "github_token",
            re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
            "[REDACTED:github_token]",
        ),
        (
            "openai_project_token",
            re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"),
            "[REDACTED:openai_project_token]",
        ),
        (
            "openrouter_token",
            re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{16,}\b"),
            "[REDACTED:openrouter_token]",
        ),
        (
            "api_key",
            re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
            "[REDACTED:api_key]",
        ),
        (
            "bearer_token",
            re.compile(r"(?i)\bBearer\s+(?!\[REDACTED:)[A-Za-z0-9._~+/=-]{24,}\b"),
            "Bearer [REDACTED:bearer_token]",
        ),
    )
    _ASSIGNMENT_RULES: tuple[tuple[str, re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
        (
            "secret_assignment",
            re.compile(
                r"(?i)\b("
                r"(?:[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|CREDENTIAL)[A-Z0-9_]*)"
                r")\s*[:=]\s*(?!\[REDACTED:)([^\s,;\"']{8,})"
            ),
            lambda match: f"{match.group(1)}=[REDACTED:secret_assignment]",
        ),
        (
            "password_assignment",
            re.compile(r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|PWD)[A-Z0-9_]*)\s*[:=]\s*([^\s,;\"']{8,})"),
            lambda match: f"{match.group(1)}=[REDACTED:password_assignment]",
        ),
    )

    def scrub(self, text: str) -> MemoryScrubResult:
        scrubbed = str(text or "")
        matched: list[str] = []
        for rule_id, pattern, replacement in self._TOKEN_RULES:
            scrubbed, count = pattern.subn(replacement, scrubbed)
            if count:
                matched.append(rule_id)
        for rule_id, pattern, replacement in self._ASSIGNMENT_RULES:
            scrubbed, count = pattern.subn(replacement, scrubbed)
            if count:
                matched.append(rule_id)
        return MemoryScrubResult(text=scrubbed, rule_ids=tuple(dict.fromkeys(matched)))
