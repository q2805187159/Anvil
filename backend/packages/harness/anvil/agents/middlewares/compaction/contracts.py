"""Data contracts for context compaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


@dataclass
class CompactionConfig:
    """Configuration for context compaction.

    Attributes:
        enabled: Master switch for compaction feature
        trigger_threshold: Fraction of max context (0.0-1.0) that triggers compaction
        summary_token_budget: Maximum tokens for compressed summary
        min_recent_messages: Minimum number of recent messages to preserve
        compression_model_name: Model to use for compression (None = default)
        compression_timeout_seconds: Timeout for compression operation
        collect_metrics: Enable metrics collection
    """

    enabled: bool = True
    trigger_threshold: float = 0.7  # Compact at 70% full
    summary_token_budget: int = 800
    min_recent_messages: int = 10  # Last 5 user+assistant pairs
    compression_model_name: str | None = None
    compression_timeout_seconds: float = 30.0
    collect_metrics: bool = True


class ClassifiedMessages(BaseModel):
    """Messages classified by priority tier.

    Priority tiers:
    - HIGH: System constraints, active tasks, unresolved issues (never discard)
    - MEDIUM: Recent history, non-redundant tool results (trim if needed)
    - LOW: Early history, redundant results, verbose explanations (aggressive compression)
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    high_priority: list[BaseMessage] = Field(default_factory=list)
    medium_priority: list[BaseMessage] = Field(default_factory=list)
    low_priority: list[BaseMessage] = Field(default_factory=list)


class CompactionEvent(BaseModel):
    """Record of a single compaction event."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    original_message_count: int
    compacted_message_count: int
    original_tokens: int
    compacted_tokens: int
    compression_ratio: float
    message_reduction: float
    compression_time_seconds: float
    facts_preserved: int

    @property
    def tokens_saved(self) -> int:
        return self.original_tokens - self.compacted_tokens


class CriticalFact(BaseModel):
    """A critical fact that must be preserved during compaction."""

    model_config = ConfigDict(extra="forbid")

    content: str
    source_message_index: int
    fact_type: str  # "decision", "bug", "constraint", "implementation", "identifier"
    confidence: float = 1.0
