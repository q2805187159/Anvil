from __future__ import annotations

import re
from dataclasses import dataclass

from .extraction_policy import has_durable_outcome_signal, is_durable_user_preference, is_stable_workspace_memory


SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+|\n+")
PREFERENCE_RE = re.compile(
    r"\b(prefer|dislike|avoid|reply with|respond with|like|hate)\b|喜欢|不喜欢|避免|不要|回复|偏好",
    re.IGNORECASE,
)
WORKSPACE_RE = re.compile(
    r"\b(remember|project|repo|repository|workspace|project constraint|codename|workflow|environment variable|repo rule|workspace rule|deployment|deployment rule)\b|记住|项目|仓库|工作区|项目约束|代号|工作流|环境变量|部署|仓库规则|工作区规则|部署规则",
    re.IGNORECASE,
)
CORRECTION_RE = re.compile(r"\b(correction|actually|not that|wrong)\b|纠正|其实|不是|错了", re.IGNORECASE)
RESOLVED_OUTCOME_RE = re.compile(
    r"\b(fixed|resolved|implemented|completed|successfully|now works|verified|returns 200|passed)\b|已修复|解决了|已完成|测试通过|验证通过|成功",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MemoryCandidate:
    layer_id: str
    content: str
    category: str
    priority: float
    confidence: float
    salience: float
    rationale: str
    evidence_refs: tuple[str, ...]
    review_required: bool = False
    supersedes: tuple[str, ...] = ()
    redacted_rules: tuple[str, ...] = ()


class MemoryCandidateExtractor:
    def __init__(self, *, max_direct_content_chars: int = 360) -> None:
        self.max_direct_content_chars = max_direct_content_chars

    def extract_turn(
        self,
        *,
        user_content: str,
        assistant_content: str = "",
        evidence_ref: str | None = None,
    ) -> tuple[MemoryCandidate, ...]:
        text = self._normalize(user_content)
        assistant_text = self._normalize(assistant_content)
        if not text and not assistant_text:
            return ()

        pieces = [piece.strip() for piece in SENTENCE_SPLIT_RE.split(text) if piece.strip()]
        if not pieces and text:
            pieces = [text]

        candidates: list[MemoryCandidate] = []
        for piece in pieces[:6]:
            candidates.extend(self._extract_piece(piece, evidence_ref=evidence_ref, whole_text=text))
        candidates.extend(
            self._extract_assistant_outcomes(
                assistant_text,
                evidence_ref=evidence_ref,
                whole_text=f"{text}\n{assistant_text}".strip(),
            )
        )
        return tuple(candidates)

    def _extract_piece(self, piece: str, *, evidence_ref: str | None, whole_text: str) -> list[MemoryCandidate]:
        review_required = len(whole_text) > self.max_direct_content_chars or len(piece) > self.max_direct_content_chars
        clipped = self._clip(piece)
        evidence_refs = (evidence_ref,) if evidence_ref else ()
        candidates: list[MemoryCandidate] = []
        if PREFERENCE_RE.search(piece) and is_durable_user_preference(piece):
            candidates.append(
                MemoryCandidate(
                    layer_id="user",
                    content=self._prefix_if_needed(clipped, "User preference"),
                    category="preference" if not CORRECTION_RE.search(piece) else "correction",
                    priority=0.86,
                    confidence=0.86 if evidence_refs else 0.72,
                    salience=0.78,
                    rationale="preference/correction signal",
                    evidence_refs=evidence_refs,
                    review_required=review_required,
                )
            )
        if WORKSPACE_RE.search(piece) and is_stable_workspace_memory(piece):
            candidates.append(
                MemoryCandidate(
                    layer_id="workspace",
                    content=self._prefix_if_needed(clipped, "Workspace fact"),
                    category="project_context",
                    priority=0.72,
                    confidence=0.84 if evidence_refs else 0.70,
                    salience=0.72,
                    rationale="project/environment/workflow signal",
                    evidence_refs=evidence_refs,
                    review_required=review_required,
                )
            )
        return candidates

    def _extract_assistant_outcomes(self, assistant_text: str, *, evidence_ref: str | None, whole_text: str) -> list[MemoryCandidate]:
        if not assistant_text or not RESOLVED_OUTCOME_RE.search(assistant_text):
            return []
        if not has_durable_outcome_signal(assistant_text):
            return []
        evidence_refs = (evidence_ref,) if evidence_ref else ()
        review_required = len(assistant_text) > self.max_direct_content_chars or len(whole_text) > self.max_direct_content_chars * 2
        content = self._prefix_if_needed(self._clip(assistant_text), "Resolved outcome")
        return [
            MemoryCandidate(
                layer_id="workspace",
                content=content,
                category="resolved_outcome",
                priority=0.84,
                confidence=0.88 if evidence_refs else 0.72,
                salience=0.82,
                rationale="assistant final response indicates a resolved outcome",
                evidence_refs=evidence_refs,
                review_required=review_required,
            )
        ]

    def _normalize(self, value: str) -> str:
        return " ".join(str(value or "").strip().split())

    def _clip(self, value: str) -> str:
        if len(value) <= self.max_direct_content_chars:
            return value
        return value[: self.max_direct_content_chars - 3].rstrip() + "..."

    def _prefix_if_needed(self, value: str, prefix: str) -> str:
        if value.lower().startswith(prefix.lower()):
            return value
        return f"{prefix}: {value}"


def _is_durable_outcome_text(value: str) -> bool:
    return has_durable_outcome_signal(value)
