from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .scrubber import MemorySecretScrubber


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def bounded_float(value: float | int | None, *, default: float = 0.5) -> float:
    try:
        numeric = float(default if value is None else value)
    except (TypeError, ValueError):
        numeric = default
    if not math.isfinite(numeric):
        numeric = default
    return round(min(max(numeric, 0.0), 1.0), 4)


def stable_id(prefix: str, *parts: object, size: int = 12) -> str:
    import hashlib

    seed = "\0".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(seed.encode('utf-8', errors='replace')).hexdigest()[:size]}"


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(term.lower() for term in re.findall(r"[\w\-/\.]{2,}", str(text or ""), re.UNICODE)))


_MEMORY_FENCE_PATTERN = re.compile(r"</?memory(?:_[a-z0-9_-]+)?\s*>", re.IGNORECASE)


def sanitize_memory_context_text(value: Any) -> str:
    text = "" if value is None else str(value)
    scrubbed = MemorySecretScrubber().scrub(text).text
    return _MEMORY_FENCE_PATTERN.sub(lambda match: match.group(0).replace("<", "[").replace(">", "]"), scrubbed)


class MemoryCategory(str, Enum):
    PREFERENCE = "preference"
    KNOWLEDGE = "knowledge"
    CONTEXT = "context"
    BEHAVIOR = "behavior"
    GOAL = "goal"
    CORRECTION = "correction"
    PATTERN = "pattern"
    PROJECT_CONTEXT = "project_context"
    PROCEDURE = "procedure"
    DECISION = "decision"
    ERROR_PATTERN = "error_pattern"
    PREFERENCE_PROFILE = "preference_profile"
    RELATIONSHIP = "relationship"
    NOTE = "note"


class MemoryLifecycleState(str, Enum):
    ACTIVE = "active"
    PROVISIONAL = "provisional"
    ARCHIVED = "archived"
    FORGOTTEN = "forgotten"
    SUPERSEDED = "superseded"
    REVIEW = "review"
    DELETED = "deleted"


class SourceType(str, Enum):
    OBSERVATION = "observation"
    MANUAL = "manual"
    TOOL = "tool"
    IMPORT = "import"
    INFERENCE = "inference"


class EvidenceType(str, Enum):
    OBSERVATION = "observation"
    REINFORCEMENT = "reinforcement"
    CORRECTION = "correction"
    INFERENCE = "inference"
    USER_STATED = "user_stated"
    PATTERN = "pattern"


class ObservationType(str, Enum):
    TOOL_CALL = "tool_call"
    USER_MESSAGE = "user_message"
    AGENT_RESPONSE = "agent_response"
    ERROR = "error"
    DECISION = "decision"
    FILE_OPERATION = "file_operation"


class AttachmentType(str, Enum):
    IMAGE = "image"
    PDF = "pdf"
    CODE = "code"
    AUDIO = "audio"
    VIDEO = "video"
    OTHER = "other"


class EntityType(str, Enum):
    PERSON = "person"
    TECHNOLOGY = "technology"
    PROJECT = "project"
    FILE = "file"
    CONCEPT = "concept"
    ORGANIZATION = "organization"
    LOCATION = "location"


class RelationType(str, Enum):
    SIMILAR_TO = "similar_to"
    CONTRADICTS = "contradicts"
    REFINES = "refines"
    GENERALIZES = "generalizes"
    HAPPENS_BEFORE = "happens_before"
    HAPPENS_AFTER = "happens_after"
    CONCURRENT_WITH = "concurrent_with"
    CAUSES = "causes"
    CAUSED_BY = "caused_by"
    ENABLES = "enables"
    PREVENTS = "prevents"
    PART_OF = "part_of"
    HAS_PART = "has_part"
    INSTANCE_OF = "instance_of"
    RELATED_TO = "related_to"


class CausalType(str, Enum):
    DIRECT_CAUSE = "direct_cause"
    INDIRECT_CAUSE = "indirect_cause"
    NECESSARY = "necessary"
    SUFFICIENT = "sufficient"
    CONTRIBUTORY = "contributory"


class QueryIntent(str, Enum):
    EXACT_MATCH = "exact_match"
    SEMANTIC = "semantic"
    RELATIONAL = "relational"
    TEMPORAL_CAUSAL = "temporal_causal"
    EXPLORATORY = "exploratory"


class HCMSLayer(str, Enum):
    OBSERVATION = "observation"
    COMPILATION = "compilation"
    STRUCTURED = "structured"
    RELATION_WEAVING = "relation_weaving"
    SEMANTIC_INDEX = "semantic_index"
    ACTIVE_RECALL = "active_recall"
    CAUSAL_REASONING = "causal_reasoning"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(default_factory=lambda: f"ev_{uuid4().hex[:8]}")
    type: EvidenceType = EvidenceType.OBSERVATION
    content: str
    weight: float = 0.5
    timestamp: datetime = Field(default_factory=utc_now)
    source_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("weight")
    @classmethod
    def _bound_weight(cls, value: float) -> float:
        return bounded_float(value)


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment_id: str = Field(default_factory=lambda: f"att_{uuid4().hex[:12]}")
    type: AttachmentType = AttachmentType.OTHER
    path: str
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0
    description: str | None = None


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obs_id: str = Field(default_factory=lambda: f"obs_{uuid4().hex}")
    timestamp: datetime = Field(default_factory=utc_now)
    raw_content: str
    compressed_content: str | None = None
    obs_type: ObservationType = ObservationType.USER_MESSAGE
    importance: int = 5
    correction_detected: bool = False
    reinforcement_detected: bool = False
    thread_id: str
    agent_name: str | None = None
    processed: bool = False
    archived_at: datetime | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("importance")
    @classmethod
    def _bound_importance(cls, value: int) -> int:
        return max(1, min(int(value), 10))


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(default_factory=lambda: f"rel_{uuid4().hex[:16]}")
    source_memory_id: str
    target_memory_id: str
    relation_type: RelationType = RelationType.RELATED_TO
    weight: float = 0.5
    confidence: float = 0.5
    bidirectional: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("weight", "confidence")
    @classmethod
    def _bound_scores(cls, value: float) -> float:
        return bounded_float(value)


class Memory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(default_factory=lambda: f"mem_{uuid4().hex[:12]}")
    version: int = 1
    parent_id: str | None = None
    supersedes: list[str] = Field(default_factory=list)
    content: str
    summary: str = ""
    category: MemoryCategory = MemoryCategory.NOTE
    confidence: float = 0.5
    salience: float = 0.5
    evidence: list[Evidence] = Field(default_factory=list)
    reasoning: str | None = None
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    accessed_at: datetime = Field(default_factory=utc_now)
    access_count: int = 0
    state: MemoryLifecycleState = MemoryLifecycleState.ACTIVE
    forget_after: datetime | None = None
    source_thread_id: str | None = None
    source_agent: str | None = None
    source_type: SourceType = SourceType.OBSERVATION
    relations: list[Relation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence", "salience")
    @classmethod
    def _bound_scores(cls, value: float) -> float:
        return bounded_float(value)

    def compute_retention_score(self, *, now: datetime | None = None, decay_lambda: float = 0.01) -> float:
        current = now or utc_now()
        age_days = max((current - self.created_at).days, 0)
        base = self.confidence * 0.4 + self.salience * 0.3
        access_boost = min(0.2, self.access_count * 0.02)
        time_decay = math.exp(-decay_lambda * age_days)
        return bounded_float((base + access_boost) * time_decay)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Memory":
        return cls.model_validate(data)

    def version_metadata(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "confidence": self.confidence,
            "salience": self.salience,
            "state": self.state.value,
            "evidence_ids": [item.evidence_id for item in self.evidence],
            "evidence_count": len(self.evidence),
            "source_thread_id": self.source_thread_id,
            "source_type": self.source_type.value,
        }

    @property
    def entry_id(self) -> str:
        return self.memory_id

    @property
    def store_id(self) -> str:
        explicit_store_id = self.metadata.get("store_id")
        if explicit_store_id:
            return str(explicit_store_id)
        normalized_layer = str(self.layer_id or "workspace").strip().lower().replace("-", "_")
        if normalized_layer.startswith("hcms_"):
            return normalized_layer
        if normalized_layer in {"all", "*", "hcms"}:
            return "hcms"
        return f"hcms_{normalized_layer or 'workspace'}"

    @property
    def layer_id(self) -> str:
        return str(
            self.metadata.get("layer_id")
            or (
                "user"
                if self.category in {MemoryCategory.PREFERENCE, MemoryCategory.PREFERENCE_PROFILE, MemoryCategory.CORRECTION}
                else "workspace"
            )
        )

    @property
    def source_kind(self) -> str:
        return str(self.metadata.get("source_kind") or self.source_type.value)

    @property
    def priority(self) -> float:
        return self.salience

    @property
    def last_accessed_at(self) -> datetime:
        return self.accessed_at

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.evidence)

    @property
    def conflicts_with(self) -> tuple[str, ...]:
        return tuple(relation.target_memory_id for relation in self.relations if relation.relation_type == RelationType.CONTRADICTS)

    @property
    def expires_at(self) -> datetime | None:
        return self.forget_after

    @property
    def status(self) -> str:
        return self.state.value


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(default_factory=lambda: f"ent_{uuid4().hex[:16]}")
    name: str
    type: EntityType = EntityType.CONCEPT
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    mentioned_in: list[str] = Field(default_factory=list)
    mention_count: int = 0
    first_seen: datetime = Field(default_factory=utc_now)
    last_seen: datetime = Field(default_factory=utc_now)
    embedding: list[float] | None = None


class CausalEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str = Field(default_factory=lambda: f"cause_{uuid4().hex[:16]}")
    source_event: str
    target_event: str
    causal_type: CausalType = CausalType.CONTRIBUTORY
    strength: float = 0.5
    evidence: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("strength")
    @classmethod
    def _bound_strength(cls, value: float) -> float:
        return bounded_float(value)


class CausalNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    event_type: str = "memory"
    timestamp: datetime
    confidence: float = 0.5


class CausalPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[CausalNode] = Field(default_factory=list)
    edges: list[CausalEdge] = Field(default_factory=list)
    total_strength: float = 0.0
    confidence: float = 0.0
    explanation_kind: str = "causal"
    degradation_reason: str | None = None
    evidence_summary: list[str] = Field(default_factory=list)

    @field_validator("total_strength", "confidence")
    @classmethod
    def _bound_path_score(cls, value: float) -> float:
        return bounded_float(value, default=0.0)


class CounterfactualImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    summary: str
    projected_change: str
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    causal_depth: int = 1
    relation_type: str = ""

    @field_validator("confidence")
    @classmethod
    def _bound_confidence(cls, value: float) -> float:
        return bounded_float(value, default=0.0)


class CounterfactualResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    assumption: str
    removed_memory_id: str | None = None
    impacts: list[CounterfactualImpact] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    engine_notes: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _bound_confidence(cls, value: float) -> float:
        return bounded_float(value, default=0.0)


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    score: float
    raw_scores: dict[str, float] = Field(default_factory=dict)
    ranks: dict[str, int] = Field(default_factory=dict)
    memory: Memory | None = None
    highlight: str | None = None
    explanation: str | None = None


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: QueryIntent = QueryIntent.SEMANTIC
    entities: list[str] = Field(default_factory=list)
    time_range: tuple[datetime, datetime] | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class MemorySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    updated_at: datetime = Field(default_factory=utc_now)


class MemoryVersionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(default_factory=lambda: f"ver_{uuid4().hex[:12]}")
    memory_id: str
    version: int
    parent_id: str | None = None
    content: str
    summary: str = ""
    diff: str = ""
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class HCMSMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_calls_avoided: int = 0
    deterministic_updates: int = 0
    recall_count: int = 0
    last_latency_ms: float = 0.0
    recall_hit_rate: float = 0.0


class MemoryDiagnosticEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str
    reason: str
    error_type: str | None = None
    stream_name: str | None = None
    count: int = 1
    message: str = ""
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("component", "reason")
    @classmethod
    def _diagnostic_label(cls, value: str) -> str:
        normalized = str(value or "").strip().lower().replace(" ", "_")[:80]
        return normalized or "unknown"

    @field_validator("error_type", "stream_name")
    @classmethod
    def _optional_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value or "").strip()[:120]
        return normalized or None

    @field_validator("message")
    @classmethod
    def _safe_message(cls, value: str) -> str:
        return sanitize_memory_context_text(value)[:240]

    @field_validator("count")
    @classmethod
    def _positive_count(cls, value: int) -> int:
        return max(1, int(value or 1))


class MemoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    summary: MemorySummary = Field(default_factory=MemorySummary)
    memories: list[Memory] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    causal_edges: list[CausalEdge] = Field(default_factory=list)
    versions: list[MemoryVersionRecord] = Field(default_factory=list)
    metrics: HCMSMetrics = Field(default_factory=HCMSMetrics)
    diagnostics: list[MemoryDiagnosticEvent] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("memories")
    @classmethod
    def _unique_memories(cls, value: list[Memory]) -> list[Memory]:
        seen: set[str] = set()
        result: list[Memory] = []
        for item in value:
            if item.memory_id in seen:
                continue
            seen.add(item.memory_id)
            result.append(item)
        return result

    def active_memories(self) -> list[Memory]:
        return [item for item in self.memories if item.state == MemoryLifecycleState.ACTIVE]


def record_memory_diagnostic(
    state: MemoryState,
    *,
    component: str,
    reason: str,
    error_type: str | None = None,
    stream_name: str | None = None,
    message: str = "",
    metadata: dict[str, Any] | None = None,
) -> MemoryDiagnosticEvent:
    safe_metadata: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        safe_key = str(key or "")[:80]
        if not safe_key:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe_metadata[safe_key] = sanitize_memory_context_text(value)[:240]
        else:
            safe_metadata[safe_key] = type(value).__name__

    event = MemoryDiagnosticEvent(
        component=component,
        reason=reason,
        error_type=error_type,
        stream_name=stream_name,
        message=message,
        metadata=safe_metadata,
    )
    for existing in reversed(state.diagnostics):
        if (
            existing.component == event.component
            and existing.reason == event.reason
            and existing.error_type == event.error_type
            and existing.stream_name == event.stream_name
        ):
            existing.count += 1
            existing.timestamp = utc_now()
            existing.message = event.message or existing.message
            existing.metadata = {**existing.metadata, **event.metadata}
            state.diagnostics = state.diagnostics[-50:]
            state.updated_at = utc_now()
            return existing

    state.diagnostics.append(event)
    if len(state.diagnostics) > 50:
        state.diagnostics = state.diagnostics[-50:]
    state.updated_at = utc_now()
    return event


class MemoryCaptureEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    memory_namespace: str
    user_messages: list[str] = Field(default_factory=list)
    final_assistant_messages: list[str] = Field(default_factory=list)
    explicit_corrections: list[str] = Field(default_factory=list)
    positive_reinforcement: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)
    trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryInjectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    summary: str
    facts: tuple[str, ...] = ()
    causal_chains: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    confidence: float = 0.0

    def render_fenced(self) -> str:
        lines = [
            f"namespace={self.namespace}",
            f"confidence={self.confidence:.3f}",
            f"summary={self.summary}",
        ]
        if self.facts:
            lines.append("facts:")
            lines.extend(f"- {fact}" for fact in self.facts)
        if self.causal_chains:
            lines.append("causal_chains:")
            lines.extend(f"- {chain}" for chain in self.causal_chains)
        if self.evidence:
            lines.append("evidence:")
            lines.extend(f"- {item}" for item in self.evidence)
        return "<memory_context>\n" + "\n".join(lines) + "\n</memory_context>"


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_limit: int = 20
    max_limit: int = 100
    bm25_weight: float = 0.3
    vector_weight: float = 0.4
    graph_weight: float = 0.2
    temporal_weight: float = 0.1
    rrf_k: int = 60
    enable_adaptive_weights: bool = True
    enable_mmr: bool = True
    mmr_lambda: float = 0.72
    enable_cache: bool = True
    cache_ttl: int = 300
    cache_max_entries: int = 100

    @field_validator("mmr_lambda")
    @classmethod
    def _bound_mmr_lambda(cls, value: float) -> float:
        return bounded_float(value, default=0.72)

    @field_validator("cache_ttl", "cache_max_entries")
    @classmethod
    def _bound_cache_limits(cls, value: int) -> int:
        return max(int(value), 0)


class UpdateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debounce_min_window: int = 5
    debounce_max_window: int = 60
    debounce_default_window: int = 30
    confidence_threshold: float = 0.7
    batch_size: int = 10
    rate_limit_delay: float = 0.5


class CompressionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_compression: bool = True
    default_level: int = 1
    level1_threshold: int = 500
    level2_threshold: int = 250
    level3_threshold: int = 120
    enable_llm_compression: bool = False
    fallback_to_deterministic: bool = True


class ForgettingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_auto_forgetting: bool = True
    retention_threshold: float = 0.15
    decay_lambda: float = 0.01
    default_ttl_days: int | None = None
    low_importance_ttl_days: int = 180


class HCMSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    storage_backend: str = "hybrid"
    base_dir: Path = Field(default_factory=lambda: Path("~/.hcms").expanduser())
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    forgetting: ForgettingConfig = Field(default_factory=ForgettingConfig)
    llm_provider: str = "none"
    llm_model: str = "zero-llm"
    embedding_model: str = "deterministic-hash"

    @field_serializer("base_dir")
    def _serialize_base_dir(self, value: Path) -> str:
        return str(value)

    @field_validator("storage_backend")
    @classmethod
    def _normalize_storage_backend(cls, value: str) -> str:
        normalized = str(value or "hybrid").strip().lower().replace("-", "_")
        aliases = {
            "md": "hybrid",
            "markdown": "hybrid",
            "file": "filesystem",
            "files": "filesystem",
            "json": "filesystem",
            "local": "filesystem",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"hybrid", "filesystem"}:
            raise ValueError("storage_backend must be one of: hybrid, filesystem")
        return normalized


class MemoryStore(Protocol):
    def load(self, namespace: str) -> MemoryState: ...

    def save(self, namespace: str, memory_state: MemoryState) -> None: ...

    def invalidate(self, namespace: str) -> None: ...

    def list_namespaces(self) -> list[str]: ...


class MemoryQueue(Protocol):
    def enqueue(self, envelope: MemoryCaptureEnvelope) -> None: ...

    async def enqueue_async(self, envelope: MemoryCaptureEnvelope) -> None: ...

    def get_pending(self, namespace: str | None = None) -> list[MemoryCaptureEnvelope]: ...

    async def get_pending_async(self, namespace: str | None = None) -> list[MemoryCaptureEnvelope]: ...

    def pop_next(self, namespace: str | None = None, *, force: bool = True) -> MemoryCaptureEnvelope | None: ...

    async def pop_next_async(
        self,
        namespace: str | None = None,
        *,
        force: bool = True,
    ) -> MemoryCaptureEnvelope | None: ...

    def pending_count(self) -> int: ...

    async def pending_count_async(self) -> int: ...


class MemoryUpdater(Protocol):
    def update(self, current_state: MemoryState, envelope: MemoryCaptureEnvelope) -> MemoryState: ...
