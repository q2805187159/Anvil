from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .compiler import HeuristicMemoryUpdater, KnowledgeCompiler, bayesian_update
from .contracts import (
    Evidence,
    EvidenceType,
    Memory,
    MemoryCaptureEnvelope,
    MemoryCategory,
    MemoryLifecycleState,
    MemoryState,
    MemoryVersionRecord,
    SourceType,
    bounded_float,
    stable_id,
    tokenize,
    utc_now,
)


@dataclass(frozen=True)
class NewFact:
    content: str
    category: MemoryCategory
    confidence: float
    evidence_text: str = ""
    source_error: str | None = None


@dataclass(frozen=True)
class MemoryUpdateItem:
    memory_id: str
    confidence_delta: float = 0.0
    new_evidence: str = ""
    reasoning: str = ""


@dataclass(frozen=True)
class MemoryUpdatePlan:
    new_facts: tuple[NewFact, ...] = ()
    updates: tuple[MemoryUpdateItem, ...] = ()
    removals: tuple[str, ...] = ()


StructuredUpdateProvider = Callable[[MemoryState, MemoryCaptureEnvelope, str], str | None]


def build_structured_update_prompt(
    current_state: MemoryState,
    envelope: MemoryCaptureEnvelope,
    *,
    max_memories: int = 10,
) -> str:
    """Build the provider prompt for HCMS structured JSON update planning."""

    current_summary = "\n".join(
        (
            f"- id={memory.memory_id} category={memory.category.value} "
            f"confidence={memory.confidence:.3f} summary={memory.summary or memory.content[:160]}"
        )
        for memory in current_state.active_memories()[: max(1, int(max_memories))]
    )
    conversation = _format_envelope_conversation(envelope)
    signal_hints: list[str] = []
    if envelope.explicit_corrections:
        signal_hints.append(
            "CORRECTION SIGNAL DETECTED: record corrected facts as high-confidence entries, "
            "identify what previous memory became wrong, and add correction evidence."
        )
    if envelope.positive_reinforcement:
        signal_hints.append(
            "REINFORCEMENT SIGNAL DETECTED: strengthen existing matching memories before adding duplicates."
        )

    return "\n".join(
        [
            "# HCMS Structured Memory Update",
            "",
            f"namespace: {envelope.memory_namespace}",
            f"thread_id: {envelope.thread_id}",
            f"trace_id: {envelope.trace_id or ''}",
            "",
            "## Current Memory State",
            current_summary or "(empty)",
            "",
            "## New Conversation",
            conversation or "(empty)",
            "",
            "## Signals",
            "\n".join(f"- {hint}" for hint in signal_hints) or "- none",
            "",
            "## Task",
            "Return only JSON. Extract durable HCMS updates from the conversation.",
            "Prefer updating existing memories when the same fact is corrected or reinforced.",
            "",
            "## JSON Contract",
            "{",
            '  "newFacts": [',
            "    {",
            '      "content": "Specific durable fact",',
            '      "category": "preference|knowledge|context|behavior|goal|correction|pattern|project_context|procedure|decision|error_pattern|preference_profile|relationship|note",',
            '      "confidence": 0.0,',
            '      "evidence": "Supporting conversation evidence",',
            '      "sourceError": "Optional previous wrong belief"',
            "    }",
            "  ],",
            '  "updates": [',
            "    {",
            '      "memoryId": "existing_memory_id",',
            '      "confidenceDelta": -1.0,',
            '      "newEvidence": "Evidence to append",',
            '      "reasoning": "Why this update is valid"',
            "    }",
            "  ],",
            '  "removals": ["memory_id_to_soft_forget"]',
            "}",
        ]
    )


def parse_structured_update_response(response: str, *, confidence_threshold: float = 0.7) -> MemoryUpdatePlan:
    """Parse the HCMS structured JSON update response into a bounded plan."""

    data = _load_json_object(response)
    threshold = bounded_float(confidence_threshold, default=0.7)
    new_facts: list[NewFact] = []
    for item in _list_value(_pick(data, "newFacts", "new_facts")):
        if not isinstance(item, Mapping):
            continue
        content = _text_value(_pick(item, "content"))
        if not content:
            continue
        confidence = bounded_float(_pick(item, "confidence"), default=0.5)
        if confidence < threshold:
            continue
        new_facts.append(
            NewFact(
                content=content,
                category=_memory_category(_pick(item, "category")),
                confidence=confidence,
                evidence_text=_text_value(_pick(item, "evidence", "evidenceText", "evidence_text")),
                source_error=_text_value(_pick(item, "sourceError", "source_error")),
            )
        )

    updates: list[MemoryUpdateItem] = []
    for item in _list_value(_pick(data, "updates")):
        if not isinstance(item, Mapping):
            continue
        memory_id = _text_value(_pick(item, "memoryId", "memory_id"))
        if not memory_id:
            continue
        updates.append(
            MemoryUpdateItem(
                memory_id=memory_id,
                confidence_delta=_bounded_delta(_pick(item, "confidenceDelta", "confidence_delta")),
                new_evidence=_text_value(_pick(item, "newEvidence", "new_evidence")),
                reasoning=_text_value(_pick(item, "reasoning")),
            )
        )

    removals: list[str] = []
    for item in _list_value(_pick(data, "removals", "removalIds", "removal_ids")):
        if isinstance(item, Mapping):
            item = _pick(item, "memoryId", "memory_id")
        memory_id = _text_value(item)
        if memory_id:
            removals.append(memory_id)

    return MemoryUpdatePlan(
        new_facts=tuple(new_facts),
        updates=tuple(updates),
        removals=tuple(dict.fromkeys(removals)),
    )


class RuleBasedMemoryUpdater:
    """Zero-LLM structured update planner for HCMS memory state."""

    def __init__(self, *, confidence_threshold: float = 0.7) -> None:
        self.confidence_threshold = bounded_float(confidence_threshold, default=0.7)

    def update(self, current_state: MemoryState, envelope: MemoryCaptureEnvelope) -> MemoryState:
        return self.apply_update(current_state, self.plan_update(current_state, envelope), envelope=envelope)

    def plan_update(self, current_state: MemoryState, envelope: MemoryCaptureEnvelope) -> MemoryUpdatePlan:
        messages = _message_texts(envelope)
        new_facts = tuple(
            fact
            for fact in (self._fact_from_text(text, envelope) for text in messages)
            if fact is not None and fact.confidence >= self.confidence_threshold
        )
        removals = tuple(self._removals(current_state, messages))
        updates = tuple(self._updates(current_state, new_facts, envelope))
        return MemoryUpdatePlan(new_facts=new_facts, updates=updates, removals=removals)

    def apply_update(
        self,
        current_state: MemoryState,
        plan: MemoryUpdatePlan,
        *,
        envelope: MemoryCaptureEnvelope | None = None,
        llm_used: bool = False,
    ) -> MemoryState:
        next_state = current_state.model_copy(deep=True)
        now = utc_now()
        source_id = envelope.thread_id if envelope is not None else current_state.namespace
        memory_by_id = {memory.memory_id: memory for memory in next_state.memories}

        for memory_id in plan.removals:
            memory = memory_by_id.get(memory_id)
            if memory is None:
                continue
            parent_id = f"{memory.memory_id}@v{memory.version}"
            memory.version += 1
            memory.parent_id = parent_id
            memory.supersedes = [*memory.supersedes, parent_id]
            memory.state = MemoryLifecycleState.FORGOTTEN
            memory.updated_at = now
            next_state.versions.append(
                MemoryVersionRecord(
                    memory_id=memory.memory_id,
                    version=memory.version,
                    parent_id=memory.parent_id,
                    content=memory.content,
                    summary=memory.summary,
                    reason="rule_based_removal",
                    metadata=_version_metadata(memory, envelope),
                )
            )

        for item in plan.updates:
            memory = memory_by_id.get(item.memory_id)
            if memory is None:
                continue
            parent_id = f"{memory.memory_id}@v{memory.version}"
            memory.version += 1
            memory.parent_id = parent_id
            memory.supersedes = [*memory.supersedes, parent_id]
            memory.confidence = bayesian_update(
                memory.confidence,
                min(max(0.5 + item.confidence_delta, 0.0), 1.0),
                evidence_weight=abs(item.confidence_delta),
            )
            memory.updated_at = now
            if item.new_evidence:
                evidence_id = stable_id("ev", memory.memory_id, source_id, item.new_evidence, size=12)
                existing_evidence = {evidence.evidence_id for evidence in memory.evidence}
                if evidence_id not in existing_evidence:
                    memory.evidence.append(
                        Evidence(
                            type=EvidenceType.REINFORCEMENT if item.confidence_delta >= 0 else EvidenceType.CORRECTION,
                            evidence_id=evidence_id,
                            content=item.new_evidence,
                            weight=bounded_float(abs(item.confidence_delta), default=0.3),
                            source_id=source_id,
                            metadata={"reasoning": item.reasoning} if item.reasoning else {},
                        )
                    )
            next_state.versions.append(
                MemoryVersionRecord(
                    memory_id=memory.memory_id,
                    version=memory.version,
                    parent_id=memory.parent_id,
                    content=memory.content,
                    summary=memory.summary,
                    reason=item.reasoning or "rule_based_update",
                    metadata=_version_metadata(memory, envelope),
                )
            )

        for fact in plan.new_facts:
            memory = _memory_from_new_fact(
                fact,
                namespace=envelope.memory_namespace if envelope is not None else current_state.namespace,
                source_id=source_id,
                envelope=envelope,
                created_at=now,
            )
            if memory.memory_id in memory_by_id:
                continue
            next_state.memories.append(memory)
            memory_by_id[memory.memory_id] = memory
            next_state.versions.append(
                MemoryVersionRecord(
                    memory_id=memory.memory_id,
                    version=memory.version,
                    content=memory.content,
                    summary=memory.summary,
                    diff=memory.content,
                    reason="rule_based_new_fact",
                    metadata=_version_metadata(memory, envelope),
                )
            )

        next_state.metrics.deterministic_updates += 1
        if not llm_used:
            next_state.metrics.llm_calls_avoided += max(len(plan.new_facts) + len(plan.updates) + len(plan.removals), 1)
        next_state.updated_at = now
        return next_state

    def _fact_from_text(self, text: str, envelope: MemoryCaptureEnvelope) -> NewFact | None:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return None
        explicit = any(text == item for item in envelope.explicit_corrections)
        reinforced = any(text == item for item in envelope.positive_reinforcement)
        confidence = 0.95 if explicit else (0.85 if reinforced else 0.75)

        preference = _extract_preference(normalized)
        if preference:
            preferred, rejected = preference
            return NewFact(
                content=f"User prefers {preferred} instead of {rejected}.",
                category=MemoryCategory.PREFERENCE,
                confidence=confidence,
                evidence_text=normalized,
                source_error=rejected if explicit or "actually" in normalized.lower() else None,
            )
        if explicit:
            return NewFact(
                content=normalized,
                category=MemoryCategory.CORRECTION,
                confidence=confidence,
                evidence_text=normalized,
                source_error="explicit correction",
            )
        if reinforced:
            return NewFact(
                content=normalized,
                category=MemoryCategory.PROCEDURE,
                confidence=confidence,
                evidence_text=normalized,
            )
        return None

    def _removals(self, current_state: MemoryState, messages: tuple[str, ...]) -> list[str]:
        removals: list[str] = []
        lowered_messages = tuple(text.lower() for text in messages)
        for memory in current_state.active_memories():
            memory_text = f"{memory.content} {memory.summary}".lower()
            if any(("forget" in text or "discard" in text or "删除" in text) and _overlaps(memory_text, text) for text in lowered_messages):
                removals.append(memory.memory_id)
        return removals

    def _updates(
        self,
        current_state: MemoryState,
        new_facts: tuple[NewFact, ...],
        envelope: MemoryCaptureEnvelope,
    ) -> list[MemoryUpdateItem]:
        updates: list[MemoryUpdateItem] = []
        fact_text = " ".join(fact.content for fact in new_facts).lower()
        for memory in current_state.active_memories():
            if fact_text and _overlaps(f"{memory.content} {memory.summary}".lower(), fact_text):
                updates.append(
                    MemoryUpdateItem(
                        memory_id=memory.memory_id,
                        confidence_delta=0.25 if envelope.positive_reinforcement else 0.15,
                        new_evidence=fact_text,
                        reasoning="rule_based_reinforcement",
                    )
                )
        return updates


class StructuredMemoryUpdater(RuleBasedMemoryUpdater):
    """Structured JSON updater with a rule-based fallback when no response exists."""

    def __init__(
        self,
        *,
        confidence_threshold: float = 0.7,
        response_provider: StructuredUpdateProvider | None = None,
        fallback_to_rules: bool = True,
    ) -> None:
        super().__init__(confidence_threshold=confidence_threshold)
        self.response_provider = response_provider
        self.fallback_to_rules = fallback_to_rules

    def update(self, current_state: MemoryState, envelope: MemoryCaptureEnvelope) -> MemoryState:
        if self.response_provider is None:
            return super().update(current_state, envelope)
        try:
            prompt = build_structured_update_prompt(current_state, envelope)
            response = self.response_provider(current_state, envelope, prompt)
            if not response:
                raise ValueError("empty structured update response")
            plan = parse_structured_update_response(response, confidence_threshold=self.confidence_threshold)
        except Exception:
            if not self.fallback_to_rules:
                raise
            return super().update(current_state, envelope)
        return self.apply_update(current_state, plan, envelope=envelope, llm_used=True)


def _message_texts(envelope: MemoryCaptureEnvelope) -> tuple[str, ...]:
    return tuple(
        text
        for text in (
            *envelope.user_messages,
            *envelope.explicit_corrections,
            *envelope.positive_reinforcement,
        )
        if str(text or "").strip()
    )


def _format_envelope_conversation(envelope: MemoryCaptureEnvelope) -> str:
    lines: list[str] = []
    for text in envelope.user_messages:
        lines.append(f"USER: {text}")
    for text in envelope.final_assistant_messages:
        lines.append(f"ASSISTANT: {text}")
    for text in envelope.explicit_corrections:
        lines.append(f"CORRECTION: {text}")
    for text in envelope.positive_reinforcement:
        lines.append(f"REINFORCEMENT: {text}")
    return "\n".join(lines)


def _extract_preference(text: str) -> tuple[str, str] | None:
    patterns = (
        r"\bprefer\s+(.+?)\s+instead\s+of\s+(.+?)(?:\.|$)",
        r"\bprefer\s+(.+?)\s+over\s+(.+?)(?:\.|$)",
        r"\bprefer\s+(.+?)\s+to\s+(.+?)(?:\.|$)",
        r"\blike\s+using\s+(.+?)(?:\.|$)",
        r"\bdon'?t\s+like\s+(.+?)(?:\.|$)",
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            if index == 3:
                return match.group(1).strip(), "unspecified alternatives"
            if index == 4:
                disliked = match.group(1).strip()
                return f"alternatives to {disliked}", disliked
            return match.group(1).strip(), match.group(2).strip()
    return None


def _overlaps(left: str, right: str) -> bool:
    left_terms = set(re.findall(r"[a-zA-Z0-9_]{3,}", left.lower()))
    right_terms = set(re.findall(r"[a-zA-Z0-9_]{3,}", right.lower()))
    return bool(left_terms & right_terms)


def _memory_from_new_fact(
    fact: NewFact,
    *,
    namespace: str,
    source_id: str,
    envelope: MemoryCaptureEnvelope | None,
    created_at: datetime,
) -> Memory:
    memory_id = stable_id("mem", namespace, fact.category.value, fact.content, size=12)
    observation_id = stable_id("obs", source_id, namespace, fact.content, fact.evidence_text, size=16)
    source_thread_id = envelope.thread_id if envelope is not None else source_id
    evidence_type = EvidenceType.CORRECTION if fact.source_error else EvidenceType.USER_STATED
    evidence = Evidence(
        evidence_id=stable_id("ev", source_id, fact.content, fact.evidence_text, size=12),
        type=evidence_type,
        content=fact.evidence_text or fact.content,
        weight=fact.confidence,
        timestamp=created_at,
        source_id=source_id,
        metadata={"source_error": fact.source_error} if fact.source_error else {},
    )
    concepts = list(tokenize(fact.content))[:12]
    markdown = KnowledgeCompiler.self_correct_markdown_schema(
        "\n".join(
            [
                f"# {fact.category.value.replace('_', ' ').title()}",
                "",
                fact.content.strip(),
            ]
        ).strip(),
        memory_id=memory_id,
        category=fact.category,
        confidence=fact.confidence,
        created_at=created_at,
        source_thread_id=source_thread_id,
        observation_id=observation_id,
        evidence=(f"{evidence.type.value} ({evidence.weight:.2f}): {evidence.content}",),
        concepts=concepts,
    )
    layer_id = (
        "user"
        if fact.category in {MemoryCategory.PREFERENCE, MemoryCategory.PREFERENCE_PROFILE, MemoryCategory.CORRECTION}
        else "workspace"
    )
    lifecycle_state = MemoryLifecycleState.ACTIVE if fact.confidence >= 0.8 else MemoryLifecycleState.PROVISIONAL
    return Memory(
        memory_id=memory_id,
        content=markdown,
        summary=fact.content[:120],
        category=fact.category,
        confidence=fact.confidence,
        salience=max(0.5, fact.confidence),
        evidence=[evidence],
        tags=[fact.category.value, "structured_update"],
        concepts=concepts,
        created_at=created_at,
        updated_at=created_at,
        accessed_at=created_at,
        state=lifecycle_state,
        source_type=SourceType.OBSERVATION,
        source_thread_id=source_thread_id,
        metadata={
            "layer_id": layer_id,
            "store_id": f"hcms_{layer_id}",
            "observation_id": observation_id,
        },
    )


def _load_json_object(response: str) -> Mapping[str, Any]:
    text = _strip_json_fence(response)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("HCMS structured update response must be valid JSON") from exc
    if not isinstance(data, Mapping):
        raise ValueError("HCMS structured update response must be a JSON object")
    return data


def _strip_json_fence(response: str) -> str:
    text = str(response or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return text


def _pick(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _list_value(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return (value,)


def _text_value(value: Any) -> str:
    return str(value or "").strip()


def _memory_category(value: Any) -> MemoryCategory:
    normalized = _text_value(value).lower().replace("-", "_")
    aliases = {
        "project": MemoryCategory.PROJECT_CONTEXT.value,
    }
    normalized = aliases.get(normalized, normalized)
    try:
        return MemoryCategory(normalized)
    except ValueError:
        return MemoryCategory.NOTE


def _bounded_delta(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return round(min(max(numeric, -1.0), 1.0), 4)


def _version_metadata(memory: Memory, envelope: MemoryCaptureEnvelope | None) -> dict[str, Any]:
    metadata = memory.version_metadata()
    if envelope is not None:
        metadata["source_thread_id"] = envelope.thread_id
        metadata["trace_id"] = envelope.trace_id
        metadata["memory_namespace"] = envelope.memory_namespace
    return metadata


__all__ = [
    "HeuristicMemoryUpdater",
    "KnowledgeCompiler",
    "MemoryUpdateItem",
    "MemoryUpdatePlan",
    "NewFact",
    "StructuredMemoryUpdater",
    "RuleBasedMemoryUpdater",
    "bayesian_update",
    "build_structured_update_prompt",
    "parse_structured_update_response",
]
