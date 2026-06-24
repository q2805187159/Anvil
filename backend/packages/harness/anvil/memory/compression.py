from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CompressionProfile:
    level: int
    name: str
    max_chars: int
    fallback_lines: int


@dataclass(frozen=True)
class CompressionResult:
    original: str
    compressed: str
    method: str
    level: int
    original_length: int
    compressed_length: int
    compression_ratio: float
    preserved_terms: tuple[str, ...] = ()

    @property
    def information_retention_score(self) -> float:
        if not self.preserved_terms:
            return 1.0
        lower = self.compressed.lower()
        retained = sum(1 for term in self.preserved_terms if term.lower() in lower)
        return round(retained / len(self.preserved_terms), 4)


class MultiLevelCompressor:
    """Deterministic HCMS compression with bounded, inspectable outputs."""

    def __init__(self, profiles: tuple[CompressionProfile, ...] | None = None) -> None:
        self.profiles = {
            profile.level: profile
            for profile in (
                profiles
                or (
                    CompressionProfile(level=1, name="summary", max_chars=500, fallback_lines=8),
                    CompressionProfile(level=2, name="recursive_summary", max_chars=250, fallback_lines=6),
                    CompressionProfile(level=3, name="emergency", max_chars=120, fallback_lines=4),
                )
            )
        }

    def compress(
        self,
        content: str,
        *,
        level: int = 1,
        preserve_terms: tuple[str, ...] = (),
    ) -> CompressionResult:
        profile = self.profiles.get(level) or self.profiles[1]
        original = " ".join(str(content or "").split())
        if len(original) <= profile.max_chars:
            return CompressionResult(
                original=original,
                compressed=original,
                method="passthrough",
                level=profile.level,
                original_length=len(original),
                compressed_length=len(original),
                compression_ratio=1.0,
                preserved_terms=tuple(dict.fromkeys(preserve_terms)),
            )

        selected = self._select_sentences(original, profile=profile, preserve_terms=preserve_terms)
        compressed = " ".join(selected)
        if len(compressed) > profile.max_chars:
            compressed = compressed[: profile.max_chars - 1].rstrip() + "..."
        return CompressionResult(
            original=original,
            compressed=compressed,
            method="deterministic",
            level=profile.level,
            original_length=len(original),
            compressed_length=max(len(compressed), 1),
            compression_ratio=round(len(original) / max(len(compressed), 1), 4),
            preserved_terms=tuple(dict.fromkeys(preserve_terms)),
        )

    def _select_sentences(
        self,
        content: str,
        *,
        profile: CompressionProfile,
        preserve_terms: tuple[str, ...],
    ) -> list[str]:
        sentences = _split_sentences(content)
        if not sentences:
            return [content[: profile.max_chars]]

        scored = [
            (index, sentence, self._score_sentence(sentence, preserve_terms=preserve_terms))
            for index, sentence in enumerate(sentences)
        ]
        scored.sort(key=lambda item: (-item[2], item[0]))

        selected: list[tuple[int, str]] = []
        used = 0
        for index, sentence, _score in scored:
            if len(selected) >= profile.fallback_lines:
                break
            next_used = used + len(sentence) + (1 if selected else 0)
            if selected and next_used > profile.max_chars:
                continue
            selected.append((index, sentence))
            used = next_used

        if not selected:
            selected.append((0, sentences[0][: profile.max_chars]))
        selected.sort(key=lambda item: item[0])
        return [sentence for _index, sentence in selected]

    def _score_sentence(self, sentence: str, *, preserve_terms: tuple[str, ...]) -> float:
        lower = sentence.lower()
        score = 0.0
        for term in preserve_terms:
            if term and term.lower() in lower:
                score += 2.0
        for marker in ("because", "caused", "failed", "error", "prefer", "must", "critical", "why", "导致", "原因"):
            if marker in lower:
                score += 0.5
        if re.search(r"\b[A-Z][A-Za-z0-9_./-]{2,}\b", sentence):
            score += 0.25
        if re.search(r"\d", sentence):
            score += 0.15
        if 40 <= len(sentence) <= 180:
            score += 0.2
        return score


def _split_sentences(content: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+", content)
    return [part.strip() for part in parts if part.strip()]
