from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .contracts import MemoryReviewItem, utc_now
from .scrubber import MemorySecretScrubber


class MemoryReviewQueue:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.scrubber = MemorySecretScrubber()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        if not self.path.exists():
            self._save(())

    def list_items(self, *, status: str | None = None) -> tuple[MemoryReviewItem, ...]:
        items = self._load()
        if status is not None:
            normalized = status.strip().lower()
            items = tuple(item for item in items if item.status == normalized)
        return items

    def add_item(
        self,
        *,
        layer_id: str,
        store_id: str,
        action: str,
        content: str,
        category: str,
        priority: float,
        confidence: float,
        salience: float,
        evidence_refs: tuple[str, ...],
        supersedes: tuple[str, ...] = (),
        conflicts_with: tuple[str, ...] = (),
        rationale: str | None = None,
    ) -> MemoryReviewItem:
        items = list(self._load())
        scrubbed = self.scrubber.scrub(content)
        normalized = scrubbed.text.strip()
        next_rationale = rationale
        if scrubbed.redacted:
            note = "redacted memory secrets: " + ", ".join(scrubbed.rule_ids)
            next_rationale = "; ".join(part for part in (rationale, note) if part)
        existing = next(
            (
                item
                for item in items
                if item.status == "pending"
                and item.layer_id == layer_id
                and item.action == action
                and item.content == normalized
            ),
            None,
        )
        if existing is not None:
            return existing
        item = MemoryReviewItem(
            review_id=f"review-{uuid4().hex[:16]}",
            layer_id=layer_id,
            store_id=store_id,
            action=action,
            content=normalized,
            category=category,
            priority=priority,
            confidence=confidence,
            salience=salience,
            evidence_refs=evidence_refs,
            supersedes=supersedes,
            conflicts_with=conflicts_with,
            rationale=next_rationale,
        )
        items.append(item)
        self._save(tuple(items))
        return item

    def mark(self, review_id: str, status: str) -> MemoryReviewItem:
        items = list(self._load())
        normalized = status.strip().lower()
        for item in items:
            if item.review_id != review_id:
                continue
            item.status = normalized
            item.updated_at = utc_now()
            self._save(tuple(items))
            return item
        raise KeyError(review_id)

    def get(self, review_id: str) -> MemoryReviewItem:
        for item in self._load():
            if item.review_id == review_id:
                return item
        raise KeyError(review_id)

    def _load(self) -> tuple[MemoryReviewItem, ...]:
        with self._lock:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        return tuple(MemoryReviewItem.model_validate(item) for item in data.get("items", []))

    def _save(self, items: tuple[MemoryReviewItem, ...]) -> None:
        payload = {"items": [item.model_dump(mode="json") for item in items]}
        tmp_path = self.path.with_suffix(".json.tmp")
        with self._lock:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            tmp_path.replace(self.path)
