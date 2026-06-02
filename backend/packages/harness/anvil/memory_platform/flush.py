from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .candidates import MemoryCandidate, MemoryCandidateExtractor
from .contracts import CuratedEntry, MemoryCandidateAuditEntry, MemoryFlushResult, MemoryReviewItem


class MemoryFlushService:
    def __init__(
        self,
        *,
        archive,
        candidate_extractor: MemoryCandidateExtractor,
        apply_candidate: Callable[[MemoryCandidate], CuratedEntry | MemoryReviewItem | tuple[CuratedEntry | MemoryReviewItem | None, MemoryCandidateAuditEntry] | None],
    ) -> None:
        self.archive = archive
        self.candidate_extractor = candidate_extractor
        self.apply_candidate = apply_candidate

    def flush(self, *, thread_id: str | None = None, messages: list[dict[str, Any]] | None = None) -> MemoryFlushResult:
        if messages is None and thread_id is not None:
            messages = [
                {
                    "content": turn.user_content,
                    "assistant_content": turn.assistant_content,
                    "evidence_ref": turn.archive_id,
                }
                for turn in self.archive.list_thread_turns(thread_id, limit=8)
            ]
        messages = messages or []
        written_ids: list[str] = []
        review_ids: list[str] = []
        candidate_audit: list[MemoryCandidateAuditEntry] = []
        entries_skipped = 0
        candidates_seen = 0
        for message in messages:
            user_content = str(message.get("content") or "")
            assistant_content = str(message.get("assistant_content") or "")
            evidence_ref = str(message.get("evidence_ref") or "") or None
            for candidate in self.candidate_extractor.extract_turn(
                user_content=user_content,
                assistant_content=assistant_content,
                evidence_ref=evidence_ref,
            ):
                candidates_seen += 1
                result = self.apply_candidate(candidate)
                if isinstance(result, tuple):
                    result, audit = result
                    candidate_audit.append(audit)
                if isinstance(result, CuratedEntry):
                    written_ids.append(result.memory_id or result.entry_id)
                elif isinstance(result, MemoryReviewItem):
                    review_ids.append(result.review_id)
                else:
                    entries_skipped += 1
        return MemoryFlushResult(
            thread_id=thread_id,
            candidates_seen=candidates_seen,
            entries_written=len(written_ids),
            review_items_created=len(review_ids),
            entries_skipped=entries_skipped,
            written_memory_ids=tuple(written_ids),
            review_ids=tuple(review_ids),
            candidate_audit=tuple(candidate_audit),
        )
