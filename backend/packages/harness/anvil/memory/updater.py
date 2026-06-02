from __future__ import annotations

import hashlib

from .contracts import MemoryCaptureEnvelope, MemoryFact, MemoryState, MemorySummary


class HeuristicMemoryUpdater:
    def __init__(self, *, max_facts: int = 12) -> None:
        self.max_facts = max_facts

    def update(self, current_state: MemoryState, envelope: MemoryCaptureEnvelope) -> MemoryState:
        state = current_state.model_copy(deep=True)
        state.namespace = envelope.memory_namespace

        facts_by_id = {fact.id: fact for fact in state.facts}
        ordered_ids = [fact.id for fact in state.facts]

        for correction in envelope.explicit_corrections:
            fact = self._make_fact("correction", correction, 1.0)
            facts_by_id[fact.id] = fact
            if fact.id not in ordered_ids:
                ordered_ids.insert(0, fact.id)

        for item in envelope.positive_reinforcement:
            fact = self._make_fact("preference", item, 0.9)
            facts_by_id[fact.id] = fact
            if fact.id not in ordered_ids:
                ordered_ids.append(fact.id)

        for item in envelope.user_messages:
            fact = self._make_fact("project_context", item, 0.7)
            facts_by_id.setdefault(fact.id, fact)
            if fact.id not in ordered_ids:
                ordered_ids.append(fact.id)

        state.facts = [facts_by_id[fact_id] for fact_id in ordered_ids][: self.max_facts]
        state.summary = MemorySummary(summary=self._build_summary(envelope))
        return state

    def _make_fact(self, category: str, content: str, confidence: float) -> MemoryFact:
        digest = hashlib.sha256(f"{category}:{content}".encode("utf-8")).hexdigest()[:16]
        return MemoryFact(
            id=f"{category}:{digest}",
            category=category,
            content=content,
            confidence=confidence,
        )

    def _build_summary(self, envelope: MemoryCaptureEnvelope) -> str:
        sources = [*envelope.user_messages, *envelope.final_assistant_messages]
        joined = " ".join(item.strip() for item in sources if item.strip())
        return joined[:240]
