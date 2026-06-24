from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stable_context_id(prefix: str, *parts: object, size: int = 16) -> str:
    seed = "\0".join(str(part or "") for part in parts)
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()
    return f"{prefix}:{digest[:size]}"


def stable_prompt_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()


def bounded_score(value: float | int | None, *, default: float = 0.5) -> float:
    try:
        numeric = float(default if value is None else value)
    except (TypeError, ValueError):
        numeric = default
    return round(min(max(numeric, 0.0), 1.0), 4)


class ContextSourceKind(str, Enum):
    PROMPT = "prompt"
    MEMORY = "memory"
    CAPABILITY = "capability"
    WORKSPACE = "workspace"
    EVENT = "event"
    TOOL_RESULT = "tool_result"
    MCP = "mcp"
    SKILL = "skill"
    SYSTEM = "system"


class ContextSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ContextSourceKind
    name: str
    ref: str | None = None
    trust_level: str = "trusted"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    source_kind: str = "unknown"
    source_id: str | None = None
    span: str | None = None
    confidence: float = 0.5
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def _bound_confidence(cls, value: float) -> float:
        return bounded_score(value)


class InjectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: bool = True
    protected: bool = False
    reason: str | None = None
    max_tokens: int | None = None
    requires_warning: bool = False


class CompressionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_compression: bool = True
    allow_reference: bool = True
    min_tokens: int = 24
    summary: str | None = None
    ref: str | None = None


class ContextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    block_type: str
    source: ContextSource
    title: str
    content: str
    token_cost: int = 0
    priority: float = 0.5
    salience: float = 0.5
    confidence: float = 0.5
    position_hint: str | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()
    conflict_state: str = "none"
    privacy_level: str = "internal"
    injection_policy: InjectionPolicy = Field(default_factory=InjectionPolicy)
    compression_policy: CompressionPolicy = Field(default_factory=CompressionPolicy)
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("priority", "salience", "confidence")
    @classmethod
    def _bound_scores(cls, value: float) -> float:
        return bounded_score(value)


class AttentionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_context_tokens: int = 8192
    reserved_response_tokens: int = 1024
    soft_limit_ratio: float = 0.9
    per_layer_token_budget: dict[str, int] = Field(default_factory=dict)

    @property
    def hard_context_tokens(self) -> int:
        return max(int(self.max_context_tokens) - int(self.reserved_response_tokens), 1)

    @property
    def soft_context_tokens(self) -> int:
        return max(int(self.hard_context_tokens * self.soft_limit_ratio), 1)


class ContextBlockTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    block_type: str
    source_kind: str
    token_cost: int
    selected: bool
    compressed: bool = False
    deferred: bool = False
    dropped: bool = False
    reason: str | None = None
    score: float = 0.0


class DropDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    reason: str
    token_cost: int = 0
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextAssemblyTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(default_factory=lambda: f"ctx_trace_{uuid4().hex[:16]}")
    prompt_hash: str = ""
    candidate_block_ids: tuple[str, ...] = ()
    selected_block_ids: tuple[str, ...] = ()
    compressed_block_ids: tuple[str, ...] = ()
    deferred_block_ids: tuple[str, ...] = ()
    dropped_block_ids: tuple[str, ...] = ()
    layer_token_usage: dict[str, int] = Field(default_factory=dict)
    selected_capabilities: tuple[str, ...] = ()
    selected_tools: tuple[str, ...] = ()
    selected_mcp_tools: tuple[str, ...] = ()
    selected_skills: tuple[str, ...] = ()
    selected_memory: tuple[str, ...] = ()
    selected_workspace: tuple[str, ...] = ()
    selected_events: tuple[str, ...] = ()
    selected_tool_results: tuple[str, ...] = ()
    selected_tool_result_refs: tuple[str, ...] = ()
    retrieval_scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    block_traces: tuple[ContextBlockTrace, ...] = ()
    drop_decisions: tuple[DropDecision, ...] = ()
    total_tokens: int = 0
    budget: AttentionBudget = Field(default_factory=AttentionBudget)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AssembledContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rendered_context: str
    blocks: tuple[ContextBlock, ...]
    trace: ContextAssemblyTrace
    fallback_used: bool = False
    diagnostics: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AssembledContext",
    "AttentionBudget",
    "CompressionPolicy",
    "ContextAssemblyTrace",
    "ContextBlock",
    "ContextBlockTrace",
    "ContextSource",
    "ContextSourceKind",
    "DropDecision",
    "EvidenceRef",
    "InjectionPolicy",
    "bounded_score",
    "stable_context_id",
    "stable_prompt_hash",
    "utc_now",
]
