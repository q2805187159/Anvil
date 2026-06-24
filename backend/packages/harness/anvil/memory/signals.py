from __future__ import annotations

import re
from dataclasses import dataclass


_CORRECTION_PATTERNS = (
    re.compile(r"\bactually\b", re.IGNORECASE),
    re.compile(r"\bthat(?:'s| is) wrong\b", re.IGNORECASE),
    re.compile(r"\byou are wrong\b", re.IGNORECASE),
    re.compile(r"\bnot correct\b", re.IGNORECASE),
    re.compile(r"\bincorrect\b", re.IGNORECASE),
    re.compile(r"纠正|不对|不是|错误"),
)
_REINFORCEMENT_PATTERNS = (
    re.compile(r"\bcorrect\b", re.IGNORECASE),
    re.compile(r"\bexactly\b", re.IGNORECASE),
    re.compile(r"\bgood\b", re.IGNORECASE),
    re.compile(r"没错|正确|很好"),
)
_NEGATED_REINFORCEMENT_PATTERNS = (
    re.compile(r"\bnot\s+correct\b", re.IGNORECASE),
    re.compile(r"\bnot\s+good\b", re.IGNORECASE),
    re.compile(r"\bincorrect\b", re.IGNORECASE),
    re.compile(r"不正确|不好|不对"),
)
_REMEMBER_PATTERNS = (
    re.compile(r"\bremember\b", re.IGNORECASE),
    re.compile(r"\bpreference\b", re.IGNORECASE),
    re.compile(r"\bprefer\b", re.IGNORECASE),
    re.compile(r"记住|偏好|喜欢"),
)


@dataclass(frozen=True)
class CaptureSignalDetection:
    correction: bool = False
    reinforcement: bool = False
    remember: bool = False
    strength: float = 0.0

    @property
    def has_signal(self) -> bool:
        return self.correction or self.reinforcement or self.remember


def detect_capture_signals(
    text: str,
    *,
    correction: bool = False,
    reinforcement: bool = False,
    remember: bool = False,
    detect_remember: bool = True,
) -> CaptureSignalDetection:
    value = str(text or "")
    detected_correction = correction or _matches_any(value, _CORRECTION_PATTERNS)
    detected_remember = remember or (detect_remember and _matches_any(value, _REMEMBER_PATTERNS))
    negated_reinforcement = _matches_any(value, _NEGATED_REINFORCEMENT_PATTERNS)
    detected_reinforcement = reinforcement or (_matches_any(value, _REINFORCEMENT_PATTERNS) and not negated_reinforcement)
    strength = 0.0
    if detected_correction:
        strength += 0.5
    if detected_reinforcement:
        strength += 0.3
    if detected_remember:
        strength += 0.2
    return CaptureSignalDetection(
        correction=detected_correction,
        reinforcement=detected_reinforcement,
        remember=detected_remember,
        strength=round(min(strength, 1.0), 4),
    )


def merge_capture_signals(*signals: CaptureSignalDetection) -> CaptureSignalDetection:
    correction = any(signal.correction for signal in signals)
    reinforcement = any(signal.reinforcement for signal in signals)
    remember = any(signal.remember for signal in signals)
    return detect_capture_signals("", correction=correction, reinforcement=reinforcement, remember=remember)


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)
