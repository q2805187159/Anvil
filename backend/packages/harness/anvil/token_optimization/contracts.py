"""Contracts for token optimization."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TruncationStrategy(str, Enum):
    """Truncation strategy types."""

    PRIORITY = "priority"  # Truncate by priority
    SLIDING = "sliding"  # Sliding window
    HYBRID = "hybrid"  # Combination


class CompressionLevel(str, Enum):
    """Compression level."""

    LIGHT = "light"  # 10-20% reduction
    MEDIUM = "medium"  # 20-40% reduction
    AGGRESSIVE = "aggressive"  # 40-60% reduction


class TokenOptimizationConfig(BaseModel):
    """Configuration for token optimization."""

    # Compression
    enable_semantic_compression: bool = True
    compression_level: CompressionLevel = CompressionLevel.MEDIUM
    compression_ratio: float = 0.7  # Target 70% of original
    preserve_facts: bool = True

    # Truncation
    enable_intelligent_truncation: bool = True
    truncation_strategy: TruncationStrategy = TruncationStrategy.PRIORITY
    min_context_items: int = 5
    max_context_items: int = 50

    # Summarization
    enable_pattern_summarization: bool = True
    use_learned_patterns: bool = True
    summarization_levels: int = 3  # detailed, brief, ultra-brief

    # Budget
    enforce_token_budget: bool = True
    system_prompt_budget: int = 500
    tool_description_budget: int = 2000
    context_budget: int = 5000
    response_buffer: int = 2000
    total_budget: int = 9500

    # Integration
    integrate_with_learning: bool = True
    integrate_with_caching: bool = True
    adapt_to_task_type: bool = True

    # Performance
    max_compression_time_ms: float = 10.0
    max_truncation_time_ms: float = 5.0
    max_summarization_time_ms: float = 15.0


class TokenBudget(BaseModel):
    """Token budget allocation."""

    system_prompt: int = 500
    tool_descriptions: int = 2000
    context: int = 5000
    response_buffer: int = 2000
    total: int = 9500

    current_system_prompt: int = 0
    current_tool_descriptions: int = 0
    current_context: int = 0

    @property
    def remaining(self) -> int:
        """Calculate remaining budget."""
        used = self.current_system_prompt + self.current_tool_descriptions + self.current_context
        return self.total - used

    @property
    def is_exceeded(self) -> bool:
        """Check if budget is exceeded."""
        return self.remaining < self.response_buffer


class CompressionResult(BaseModel):
    """Result of compression operation."""

    original: str
    compressed: str
    original_tokens: int
    compressed_tokens: int
    token_savings: int
    compression_ratio: float
    facts_preserved: int = 0
    compression_time_ms: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


class TruncationResult(BaseModel):
    """Result of truncation operation."""

    original_count: int
    truncated_count: int
    items_removed: int
    original_tokens: int
    truncated_tokens: int
    token_savings: int
    strategy_used: str
    truncation_time_ms: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


class SummarizationResult(BaseModel):
    """Result of summarization operation."""

    original: str
    summary: str
    original_tokens: int
    summary_tokens: int
    token_savings: int
    summarization_level: str  # detailed, brief, ultra-brief
    pattern_used: str | None = None
    summarization_time_ms: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


class TokenOptimizationMetrics(BaseModel):
    """Metrics for token optimization."""

    # Compression
    total_compressions: int = 0
    total_compression_savings: int = 0
    average_compression_ratio: float = 0.0
    average_compression_time_ms: float = 0.0

    # Truncation
    total_truncations: int = 0
    total_truncation_savings: int = 0
    average_items_removed: float = 0.0
    average_truncation_time_ms: float = 0.0

    # Summarization
    total_summarizations: int = 0
    total_summarization_savings: int = 0
    average_summarization_ratio: float = 0.0
    average_summarization_time_ms: float = 0.0

    # Overall
    total_token_savings: int = 0
    total_operations: int = 0
    average_operation_time_ms: float = 0.0

    # Budget
    budget_exceeded_count: int = 0
    emergency_truncations: int = 0

    collected_at: datetime = Field(default_factory=datetime.now)


class ContextItem(BaseModel):
    """Context item for optimization."""

    content: str
    priority: str = "medium"  # high, medium, low
    tokens: int = 0
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)
