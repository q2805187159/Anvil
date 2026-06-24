from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .compression import MultiLevelCompressor
from .contracts import (
    CausalEdge,
    CausalType,
    Entity,
    EntityType,
    Evidence,
    EvidenceType,
    Memory,
    MemoryCategory,
    MemoryCaptureEnvelope,
    MemoryLifecycleState,
    MemoryState,
    MemorySummary,
    MemoryVersionRecord,
    Observation,
    ObservationType,
    Relation,
    RelationType,
    SourceType,
    bounded_float,
    stable_id,
    tokenize,
    utc_now,
)
from .signals import detect_capture_signals


@dataclass(frozen=True)
class MarkdownSchemaValidation:
    valid: bool
    errors: tuple[str, ...] = ()
    metadata: dict[str, str] | None = None


class KnowledgeCompiler:
    """Zero-LLM compiler for observations -> structured HCMS memory."""

    def __init__(self, compressor: MultiLevelCompressor | None = None) -> None:
        self.compressor = compressor or MultiLevelCompressor()

    @staticmethod
    def validate_markdown_schema(markdown: str) -> MarkdownSchemaValidation:
        errors: list[str] = []
        metadata = _parse_frontmatter(markdown)
        if metadata is None:
            errors.append("Missing frontmatter")
            metadata = {}
        for field in (
            "memory_id",
            "category",
            "confidence",
            "created_at",
            "source_thread_id",
            "observation_id",
        ):
            if not str(metadata.get(field) or "").strip():
                errors.append(f"Missing required field: {field}")
        try:
            confidence = float(str(metadata.get("confidence", "")).strip())
            if confidence < 0.0 or confidence > 1.0:
                errors.append("Field confidence must be between 0 and 1")
        except ValueError:
            errors.append("Field confidence must be numeric")
        for section in ("## Evidence", "## Relations", "## Metadata"):
            if section not in markdown:
                errors.append(f"Missing section: {section}")
        if not re.search(r"^#\s+\S+", _strip_frontmatter(markdown), flags=re.MULTILINE):
            errors.append("Missing H1 title")
        return MarkdownSchemaValidation(valid=not errors, errors=tuple(errors), metadata=metadata)

    @staticmethod
    def self_correct_markdown_schema(
        markdown: str,
        *,
        memory_id: str,
        category: MemoryCategory | str,
        confidence: float,
        created_at: datetime,
        source_thread_id: str | None,
        observation_id: str | None,
        evidence: Iterable[str] = (),
        entities: Iterable[str] = (),
        concepts: Iterable[str] = (),
    ) -> str:
        category_value = category.value if isinstance(category, MemoryCategory) else str(category or MemoryCategory.NOTE.value)
        evidence_lines = tuple(_bullet_lines(evidence, fallback="- none"))
        entity_values = tuple(str(item) for item in entities if str(item).strip())
        concept_values = tuple(str(item) for item in concepts if str(item).strip())
        body = _strip_frontmatter(markdown).strip()
        if not re.search(r"^#\s+\S+", body, flags=re.MULTILINE):
            body = f"# {category_value.replace('_', ' ').title()}\n\n{body}".strip()

        corrected = "\n".join(
            [
                "---",
                "hcms_schema: compiled_memory.v1",
                f"memory_id: {memory_id}",
                f"category: {category_value}",
                f"confidence: {bounded_float(confidence):.4f}",
                f"created_at: {created_at.isoformat()}",
                f"source_thread_id: {source_thread_id or ''}",
                f"observation_id: {observation_id or ''}",
                f"evidence_count: {len(evidence_lines)}",
                "---",
                "",
                body,
            ]
        ).strip()

        if "## Evidence" not in corrected:
            corrected = "\n\n".join([corrected, "## Evidence", "\n".join(evidence_lines)])
        if "## Relations" not in corrected:
            corrected = "\n\n".join(
                [
                    corrected,
                    "## Relations",
                    "- none recorded yet; relation weaving updates graph edges after compilation.",
                ]
            )
        if "## Metadata" not in corrected:
            metadata = [
                f"- observation: {observation_id or ''}",
                f"- thread: {source_thread_id or ''}",
            ]
            if entity_values:
                metadata.append(f"- entities: {', '.join(entity_values[:10])}")
            if concept_values:
                metadata.append(f"- concepts: {', '.join(concept_values[:10])}")
            corrected = "\n\n".join([corrected, "## Metadata", "\n".join(metadata)])
        return corrected.strip()

    def compile_envelope(self, state: MemoryState, envelope: MemoryCaptureEnvelope, *, max_facts: int = 12) -> MemoryState:
        next_state = state.model_copy(deep=True)
        next_state.namespace = envelope.memory_namespace
        observations = self._observations(envelope)
        next_state.observations.extend(observations)
        for observation in observations:
            memory = self._memory_from_observation(observation)
            next_state = self.upsert_memory(next_state, memory, reason="observation_compile")
            observation.processed = True
        self.weave_relations(next_state)
        self._rebuild_entities(next_state)
        self._rebuild_causal_edges(next_state)
        next_state.summary = MemorySummary(summary=self._summary(next_state), updated_at=utc_now())
        next_state.metrics.deterministic_updates += 1
        next_state.metrics.llm_calls_avoided += max(len(observations), 1)
        next_state.updated_at = utc_now()
        return next_state

    def upsert_memory(self, state: MemoryState, memory: Memory, *, reason: str = "upsert") -> MemoryState:
        existing_index = self._matching_memory_index(state, memory)
        now = utc_now()
        if existing_index is None:
            memory.updated_at = now
            if not memory.summary:
                memory.summary = _summarize(memory.content)
            state.memories.append(memory)
            state.versions.append(
                MemoryVersionRecord(
                    memory_id=memory.memory_id,
                    version=memory.version,
                    parent_id=memory.parent_id,
                    content=memory.content,
                    summary=memory.summary,
                    diff=memory.content,
                    reason=reason,
                    metadata=memory.version_metadata(),
                )
            )
            return state

        previous = state.memories[existing_index]
        merged = self._merge_memory(previous, memory)
        diff = "\n".join(
            difflib.unified_diff(
                previous.content.splitlines(),
                merged.content.splitlines(),
                fromfile=f"{previous.memory_id}@v{previous.version}",
                tofile=f"{previous.memory_id}@v{merged.version}",
                lineterm="",
            )
        )
        state.memories[existing_index] = merged
        state.versions.append(
            MemoryVersionRecord(
                memory_id=merged.memory_id,
                version=merged.version,
                parent_id=merged.parent_id,
                content=merged.content,
                summary=merged.summary,
                diff=diff,
                reason=reason,
                metadata=merged.version_metadata(),
            )
        )
        return state

    def archive_memory(self, state: MemoryState, memory_id: str, *, reason: str = "archive") -> Memory:
        memory = self._require_memory(state, memory_id)
        parent_version_id = f"{memory.memory_id}@v{memory.version}"
        memory.version += 1
        memory.parent_id = parent_version_id
        memory.supersedes = [*memory.supersedes, parent_version_id]
        memory.state = MemoryLifecycleState.ARCHIVED
        memory.updated_at = utc_now()
        state.versions.append(
            MemoryVersionRecord(
                memory_id=memory.memory_id,
                version=memory.version,
                parent_id=memory.parent_id,
                content=memory.content,
                summary=memory.summary,
                reason=reason,
                metadata=memory.version_metadata(),
            )
        )
        return memory

    def restore_memory(self, state: MemoryState, memory_id: str, *, reason: str = "restore") -> Memory:
        memory = self._require_memory(state, memory_id)
        parent_version_id = f"{memory.memory_id}@v{memory.version}"
        memory.version += 1
        memory.parent_id = parent_version_id
        memory.supersedes = [*memory.supersedes, parent_version_id]
        memory.state = MemoryLifecycleState.ACTIVE
        memory.updated_at = utc_now()
        state.versions.append(
            MemoryVersionRecord(
                memory_id=memory.memory_id,
                version=memory.version,
                parent_id=memory.parent_id,
                content=memory.content,
                summary=memory.summary,
                reason=reason,
                metadata=memory.version_metadata(),
            )
        )
        return memory

    def forget_memory(self, state: MemoryState, memory_id: str, *, reason: str = "forget") -> Memory:
        memory = self._require_memory(state, memory_id)
        parent_version_id = f"{memory.memory_id}@v{memory.version}"
        memory.version += 1
        memory.parent_id = parent_version_id
        memory.supersedes = [*memory.supersedes, parent_version_id]
        memory.state = MemoryLifecycleState.FORGOTTEN
        memory.updated_at = utc_now()
        state.versions.append(
            MemoryVersionRecord(
                memory_id=memory.memory_id,
                version=memory.version,
                parent_id=memory.parent_id,
                content=memory.content,
                summary=memory.summary,
                reason=reason,
                metadata=memory.version_metadata(),
            )
        )
        return memory

    def weave_relations(self, state: MemoryState) -> None:
        relation_keys: set[tuple[str, str, RelationType]] = {
            (relation.source_memory_id, relation.target_memory_id, relation.relation_type)
            for relation in state.relations
        }
        active = state.active_memories()
        for index, source in enumerate(active):
            source_terms = set(tokenize(source.content))
            for target in active[index + 1 :]:
                target_terms = set(tokenize(target.content))
                shared = source_terms & target_terms
                if not shared:
                    continue
                score = len(shared) / max(len(source_terms | target_terms), 1)
                relation_type = self.classify_relation(source, target)
                if score < 0.08 and relation_type == RelationType.RELATED_TO:
                    continue
                key = (source.memory_id, target.memory_id, relation_type)
                if key in relation_keys:
                    continue
                relation = Relation(
                    source_memory_id=source.memory_id,
                    target_memory_id=target.memory_id,
                    relation_type=relation_type,
                    weight=max(0.25, min(score * 3, 1.0)),
                    confidence=max(source.confidence, target.confidence) * 0.8,
                    bidirectional=relation_type in {RelationType.RELATED_TO, RelationType.SIMILAR_TO},
                    metadata={"shared_terms": sorted(shared)[:12]},
                )
                state.relations.append(relation)
                source.relations.append(relation)
                if relation.bidirectional:
                    target.relations.append(relation)
                relation_keys.add(key)

    def _observations(self, envelope: MemoryCaptureEnvelope) -> list[Observation]:
        observations: list[Observation] = []
        for text in envelope.user_messages:
            observations.append(self._observation(text, envelope, ObservationType.USER_MESSAGE))
        for text in envelope.final_assistant_messages:
            if _is_assistant_acknowledgement(text):
                continue
            observations.append(self._observation(text, envelope, ObservationType.AGENT_RESPONSE, importance=4))
        for text in envelope.explicit_corrections:
            observations.append(self._observation(text, envelope, ObservationType.DECISION, importance=9, correction=True))
        for text in envelope.positive_reinforcement:
            observations.append(self._observation(text, envelope, ObservationType.DECISION, importance=7, reinforcement=True))
        return _dedupe_observations(observations)

    def _observation(
        self,
        text: str,
        envelope: MemoryCaptureEnvelope,
        obs_type: ObservationType,
        *,
        importance: int = 5,
        correction: bool = False,
        reinforcement: bool = False,
    ) -> Observation:
        entities = _extract_entities(text)
        compression = self.compressor.compress(text, level=1, preserve_terms=tuple(entities))
        signal = detect_capture_signals(text, correction=correction, reinforcement=reinforcement)
        return Observation(
            obs_id=stable_id("obs", envelope.thread_id, envelope.memory_namespace, text, size=16),
            timestamp=envelope.timestamp,
            raw_content=text,
            compressed_content=compression.compressed,
            obs_type=obs_type,
            importance=importance,
            correction_detected=signal.correction,
            reinforcement_detected=signal.reinforcement,
            thread_id=envelope.thread_id,
            metadata={
                "trace_id": envelope.trace_id,
                "compression_method": compression.method,
                "compression_level": compression.level,
                "compression_ratio": compression.compression_ratio,
                "information_retention_score": compression.information_retention_score,
                "preserved_terms": list(compression.preserved_terms),
            },
        )

    def _memory_from_observation(self, observation: Observation) -> Memory:
        content = observation.compressed_content or observation.raw_content
        category = _category_for_text(content, observation)
        evidence_type = EvidenceType.CORRECTION if observation.correction_detected else EvidenceType.USER_STATED
        if observation.reinforcement_detected:
            evidence_type = EvidenceType.REINFORCEMENT
        confidence = 0.95 if observation.correction_detected else (0.82 if observation.reinforcement_detected else 0.68)
        salience = max(observation.importance / 10, confidence * 0.75)
        entities = _extract_entities(content)
        concepts = sorted(tokenize(content))[:12]
        memory_id = stable_id("mem", category.value, _canonical_content(content), size=12)
        evidence = Evidence(
            evidence_id=stable_id("ev", observation.obs_id, content, size=8),
            type=evidence_type,
            content=_summarize(observation.raw_content, limit=180),
            weight=confidence,
            timestamp=observation.timestamp,
            source_id=observation.obs_id,
            metadata={"thread_id": observation.thread_id},
        )
        markdown = _compile_markdown(
            content,
            category=category,
            entities=entities,
            concepts=concepts,
            observation=observation,
            memory_id=memory_id,
            confidence=confidence,
            evidence=(evidence,),
        )
        layer_id = (
            "user"
            if category in {MemoryCategory.PREFERENCE, MemoryCategory.PREFERENCE_PROFILE, MemoryCategory.CORRECTION}
            else "workspace"
        )
        store_id = "hcms_user" if layer_id == "user" else "hcms_workspace"
        return Memory(
            memory_id=memory_id,
            content=markdown,
            summary=_summarize(content),
            category=category,
            confidence=confidence,
            salience=salience,
            evidence=[evidence],
            reasoning="Compiled deterministically from observation signals; zero LLM path.",
            tags=[category.value, observation.obs_type.value],
            entities=entities,
            concepts=concepts,
            created_at=observation.timestamp,
            updated_at=utc_now(),
            accessed_at=utc_now(),
            source_thread_id=observation.thread_id,
            source_type=SourceType.OBSERVATION,
            metadata={"layer_id": layer_id, "store_id": store_id},
        )

    def _matching_memory_index(self, state: MemoryState, memory: Memory) -> int | None:
        incoming_layer = str(memory.metadata.get("layer_id") or "")
        for index, existing in enumerate(state.memories):
            if existing.memory_id == memory.memory_id:
                return index
            if existing.state != MemoryLifecycleState.ACTIVE:
                continue
            existing_layer = str(existing.metadata.get("layer_id") or "")
            if existing_layer != incoming_layer:
                continue
            if existing.category == memory.category and _canonical_content(existing.summary) == _canonical_content(memory.summary):
                return index
        return None

    def _merge_memory(self, previous: Memory, incoming: Memory) -> Memory:
        merged = previous.model_copy(deep=True)
        parent_version_id = f"{previous.memory_id}@v{previous.version}"
        merged.version += 1
        merged.parent_id = parent_version_id
        merged.supersedes = [*previous.supersedes, parent_version_id]
        merged.confidence = bayesian_update(previous.confidence, incoming.confidence, evidence_weight=max((e.weight for e in incoming.evidence), default=0.5))
        merged.salience = max(previous.salience, incoming.salience)
        merged.accessed_at = utc_now()
        merged.updated_at = utc_now()
        merged.evidence = _dedupe_evidence([*previous.evidence, *incoming.evidence])
        merged.entities = sorted(dict.fromkeys([*previous.entities, *incoming.entities]))
        merged.concepts = sorted(dict.fromkeys([*previous.concepts, *incoming.concepts]))[:24]
        merged.tags = sorted(dict.fromkeys([*previous.tags, *incoming.tags]))
        if incoming.content not in previous.content:
            merged.content = f"{previous.content}\n\n## Reinforcement\n\n{incoming.content}".strip()
        merged.summary = _summarize(merged.content)
        return merged

    def _rebuild_entities(self, state: MemoryState) -> None:
        by_name: dict[str, Entity] = {}
        for memory in state.active_memories():
            for name in memory.entities:
                key = name.lower()
                entity = by_name.get(key)
                if entity is None:
                    entity = Entity(
                        entity_id=stable_id("ent", key, size=16),
                        name=name,
                        type=_entity_type(name),
                        mentioned_in=[],
                        first_seen=memory.created_at,
                        last_seen=memory.updated_at,
                    )
                    by_name[key] = entity
                if memory.memory_id not in entity.mentioned_in:
                    entity.mentioned_in.append(memory.memory_id)
                entity.mention_count = len(entity.mentioned_in)
                entity.last_seen = max(entity.last_seen, memory.updated_at)
        state.entities = sorted(by_name.values(), key=lambda item: item.name.lower())

    def _rebuild_causal_edges(self, state: MemoryState) -> None:
        existing = {(edge.source_event, edge.target_event, edge.causal_type) for edge in state.causal_edges}
        active = sorted(state.active_memories(), key=lambda item: item.created_at)
        for source in active:
            for target in active:
                if source.memory_id == target.memory_id or source.created_at > target.created_at:
                    continue
                relation_type = self.classify_relation(source, target)
                if relation_type not in {RelationType.CAUSES, RelationType.ENABLES, RelationType.PREVENTS}:
                    continue
                causal_type = CausalType.DIRECT_CAUSE if relation_type == RelationType.CAUSES else CausalType.CONTRIBUTORY
                key = (source.memory_id, target.memory_id, causal_type)
                if key in existing:
                    continue
                shared_evidence = [evidence.evidence_id for evidence in [*source.evidence, *target.evidence]][:8]
                state.causal_edges.append(
                    CausalEdge(
                        source_event=source.memory_id,
                        target_event=target.memory_id,
                        causal_type=causal_type,
                        strength=min(source.confidence, target.confidence),
                        evidence=shared_evidence,
                        metadata={"relation_type": relation_type.value},
                    )
                )
                existing.add(key)

    def classify_relation(self, source: Memory, target: Memory) -> RelationType:
        joined = f"{source.content}\n{target.content}".lower()
        source_text = source.content.lower()
        target_text = target.content.lower()
        if _has_any(joined, "contradict", "conflict", "incompatible", "must not", "that's wrong", "错误", "矛盾"):
            return RelationType.CONTRADICTS
        if _has_any(joined, "refines", "more specific", "stricter", "clarifies", "精化", "细化"):
            return RelationType.REFINES
        if _has_any(source_text, "part of", "belongs to", "component of", "属于"):
            return RelationType.PART_OF
        if _has_any(source_text, "has part", "contains", "includes", "covers", "包含"):
            return RelationType.HAS_PART
        if _has_any(joined, "instance of", "example of", "kind of", "实例"):
            return RelationType.INSTANCE_OF
        if _has_any(joined, "generalizes", "broader", "all ", "applies to all", "泛化", "通用"):
            return RelationType.GENERALIZES
        if _has_any(joined, "caused by", "is caused by", "driven by", "drove", "被") and _has_any(joined, "cause", "caused", "failure", "导致"):
            return RelationType.CAUSED_BY
        if _has_any(joined, "because", "causes", "caused", "leads to", "results in", "导致", "原因", "why"):
            return RelationType.CAUSES
        if _has_any(joined, "enable", "allows", "unlocks", "depends on", "使得", "解锁"):
            return RelationType.ENABLES
        if _has_any(joined, "prevent", "prevents", "blocked by", "block", "avoid", "阻止", "避免"):
            return RelationType.PREVENTS
        if _has_any(source_text, "happens before", "before ") or _has_any(target_text, " after ", "runs after"):
            return RelationType.HAPPENS_BEFORE
        if _has_any(source_text, "happens after", " after ", "runs after") or _has_any(target_text, "precedes", "before "):
            return RelationType.HAPPENS_AFTER
        if _has_any(joined, "concurrent", "same time", "simultaneous", "parallel", "同时"):
            return RelationType.CONCURRENT_WITH
        if source.category == target.category:
            return RelationType.SIMILAR_TO
        return RelationType.RELATED_TO

    def _require_memory(self, state: MemoryState, memory_id: str) -> Memory:
        for memory in state.memories:
            if memory.memory_id == memory_id:
                return memory
        raise KeyError(memory_id)

    def _summary(self, state: MemoryState) -> str:
        active = state.active_memories()
        if not active:
            return ""
        categories = Counter(memory.category.value for memory in active)
        top = ", ".join(f"{name}={count}" for name, count in categories.most_common(4))
        recent = sorted(active, key=lambda item: item.updated_at, reverse=True)[:3]
        return f"HCMS active memories: {len(active)} ({top}). Recent: " + " | ".join(item.summary for item in recent)


class HeuristicMemoryUpdater:
    """Compatibility updater backed by the HCMS compiler."""

    def __init__(self, *, max_facts: int = 12) -> None:
        self.max_facts = max_facts
        self.compiler = KnowledgeCompiler()

    def update(self, current_state: MemoryState, envelope: MemoryCaptureEnvelope) -> MemoryState:
        return self.compiler.compile_envelope(current_state, envelope, max_facts=self.max_facts)


def bayesian_update(prior: float, likelihood: float, *, evidence_weight: float = 0.5) -> float:
    prior = bounded_float(prior)
    likelihood = bounded_float(likelihood)
    weight = bounded_float(evidence_weight)
    evidence_signal = (likelihood - 0.5) * 2.0
    effective_weight = weight * abs(evidence_signal)
    if evidence_signal >= 0.0:
        updated = prior + (1.0 - prior) * evidence_signal * effective_weight
    else:
        updated = prior + prior * evidence_signal * effective_weight
    return bounded_float(updated)


def compile_manual_memory_content(
    content: str,
    *,
    memory_id: str,
    category: MemoryCategory,
    confidence: float,
    created_at: datetime,
    source_thread_id: str | None,
    observation_id: str,
    evidence: Iterable[Evidence] = (),
) -> str:
    """Compile manual/API memory writes into the same HCMS Markdown schema."""

    raw_content = str(content or "").strip()
    concepts = sorted(tokenize(raw_content))[:12]
    evidence_lines = [
        f"{item.type.value} ({item.weight:.2f}): {item.content}"
        for item in evidence
    ]
    draft = "\n".join(
        [
            f"# {category.value.replace('_', ' ').title()}",
            "",
            raw_content,
        ]
    ).strip()
    return KnowledgeCompiler.self_correct_markdown_schema(
        draft,
        memory_id=memory_id,
        category=category,
        confidence=confidence,
        created_at=created_at,
        source_thread_id=source_thread_id or "manual",
        observation_id=observation_id,
        evidence=evidence_lines,
        concepts=concepts,
    )


def normalize_memory_for_compiled_storage(namespace: str, memory: Memory) -> Memory:
    """Ensure direct storage writes persist the compiled HCMS Markdown schema."""

    validation = KnowledgeCompiler.validate_markdown_schema(memory.content)
    if validation.valid:
        return memory

    source_thread_id = memory.source_thread_id or "manual"
    observation_id = str(
        memory.metadata.get("observation_id")
        or stable_id("obs", source_thread_id, namespace, memory.memory_id, memory.summary or memory.content, size=16)
    )
    content = compile_manual_memory_content(
        memory.content,
        memory_id=memory.memory_id,
        category=memory.category,
        confidence=memory.confidence,
        created_at=memory.created_at,
        source_thread_id=source_thread_id,
        observation_id=observation_id,
        evidence=memory.evidence,
    )
    return memory.model_copy(
        deep=True,
        update={
            "content": content,
            "source_thread_id": source_thread_id,
            "concepts": memory.concepts or list(tokenize(memory.summary or memory.content))[:12],
            "metadata": {**memory.metadata, "observation_id": observation_id},
        },
    )


def _dedupe_observations(observations: list[Observation]) -> list[Observation]:
    seen: set[str] = set()
    result: list[Observation] = []
    for observation in observations:
        key = observation.obs_id
        if key in seen:
            continue
        seen.add(key)
        result.append(observation)
    return result


def _dedupe_evidence(items: list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    result: list[Evidence] = []
    for item in items:
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        result.append(item)
    return result[:40]


def _is_assistant_acknowledgement(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).lower().strip(" .。!！")
    if not normalized:
        return True
    starters = (
        "recorded",
        "noted",
        "understood",
        "got it",
        "i will remember",
        "i'll remember",
        "i will keep",
        "i'll keep",
        "我会记住",
        "已记录",
        "记住了",
        "明白",
    )
    if not normalized.startswith(starters):
        return False
    durable_markers = (
        "because",
        "caused",
        "failed",
        "error",
        "decision",
        "preference",
        "prefers",
        "instead of",
        "原因",
        "导致",
        "失败",
        "决策",
    )
    return not any(marker in normalized for marker in durable_markers)


def _compress_text(text: str, *, limit: int = 500) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "..."


def _summarize(text: str, *, limit: int = 120) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "..."


def _canonical_content(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _category_for_text(text: str, observation: Observation) -> MemoryCategory:
    lowered = text.lower()
    if observation.correction_detected:
        return MemoryCategory.CORRECTION
    if any(marker in lowered for marker in ("prefer", "preference", "偏好", "喜欢")):
        return MemoryCategory.PREFERENCE
    if any(marker in lowered for marker in ("because", "decision", "decided", "原因", "决定")):
        return MemoryCategory.DECISION
    if any(marker in lowered for marker in ("error", "failed", "exception", "bug", "失败", "错误")):
        return MemoryCategory.ERROR_PATTERN
    if any(marker in lowered for marker in ("run ", "pytest", "make ", "command", "步骤", "流程")):
        return MemoryCategory.PROCEDURE
    if any(marker in lowered for marker in ("project", "repo", "workspace", "northstar", "项目")):
        return MemoryCategory.PROJECT_CONTEXT
    return MemoryCategory.NOTE


def _extract_entities(text: str) -> list[str]:
    entities: list[str] = []
    for match in re.findall(r"\b[A-Z][A-Za-z0-9_./-]{2,}\b", text):
        if match not in entities:
            entities.append(match)
    for match in re.findall(r"[\w.-]+/[\w./-]+", text):
        if match not in entities:
            entities.append(match)
    return entities[:20]


def _entity_type(name: str) -> EntityType:
    if "/" in name or "." in name:
        return EntityType.FILE
    if name.lower() in {"python", "typescript", "react", "sqlite", "pytest", "fastapi"}:
        return EntityType.TECHNOLOGY
    return EntityType.CONCEPT


def _has_any(text: str, *markers: str) -> bool:
    return any(marker in text for marker in markers)


def _compile_markdown(
    content: str,
    *,
    category: MemoryCategory,
    entities: list[str],
    concepts: list[str],
    observation: Observation,
    memory_id: str,
    confidence: float,
    evidence: Iterable[Evidence],
) -> str:
    evidence_lines = [
        f"{item.type.value} ({item.weight:.2f}): {item.content}"
        for item in evidence
    ]
    draft = "\n".join(
        [
            f"# {category.value.replace('_', ' ').title()}",
            "",
            content.strip(),
        ]
    ).strip()
    return KnowledgeCompiler.self_correct_markdown_schema(
        draft,
        memory_id=memory_id,
        category=category,
        confidence=confidence,
        created_at=observation.timestamp,
        source_thread_id=observation.thread_id,
        observation_id=observation.obs_id,
        evidence=evidence_lines,
        entities=entities,
        concepts=concepts,
    )


def _parse_frontmatter(markdown: str) -> dict[str, str] | None:
    lines = str(markdown or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return metadata
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return None


def _strip_frontmatter(markdown: str) -> str:
    lines = str(markdown or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return str(markdown or "")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :]).strip()
    return str(markdown or "")


def _bullet_lines(items: Iterable[str], *, fallback: str) -> list[str]:
    lines: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        lines.append(text if text.startswith("- ") else f"- {text}")
    return lines or [fallback]
