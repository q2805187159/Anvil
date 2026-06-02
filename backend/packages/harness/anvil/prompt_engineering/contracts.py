"""Contracts for prompt engineering and token optimization."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OptimizationStrategy(str, Enum):
    """Optimization strategy types."""

    AGGRESSIVE = "aggressive"  # Maximum token reduction
    BALANCED = "balanced"  # Balance clarity and tokens
    CONSERVATIVE = "conservative"  # Minimal changes, preserve clarity


class OptimizationRule(BaseModel):
    """Rule for optimizing descriptions."""

    name: str
    pattern: str  # Regex pattern to match
    replacement: str  # Replacement text
    description: str
    token_savings_estimate: int = 0
    enabled: bool = True


class OptimizedDescription(BaseModel):
    """Result of description optimization."""

    original: str
    optimized: str
    original_tokens: int
    optimized_tokens: int
    token_savings: int
    token_savings_percent: float
    rules_applied: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)


class OptimizationMetrics(BaseModel):
    """Metrics for prompt optimization."""

    total_descriptions_optimized: int = 0
    total_original_tokens: int = 0
    total_optimized_tokens: int = 0
    total_token_savings: int = 0
    average_savings_percent: float = 0.0
    rules_applied_count: dict[str, int] = Field(default_factory=dict)
    optimization_timestamp: datetime = Field(default_factory=datetime.now)


class PromptEngineeringConfig(BaseModel):
    """Configuration for prompt engineering."""

    # Optimization settings
    enable_optimization: bool = True
    optimization_strategy: OptimizationStrategy = OptimizationStrategy.BALANCED

    # Tool description optimization
    optimize_tool_descriptions: bool = True
    max_tool_description_tokens: int = 20  # Target max tokens per description
    preserve_essential_info: bool = True

    # System prompt optimization
    optimize_system_prompts: bool = True
    max_system_prompt_tokens: int = 500  # Target max tokens
    remove_personality_content: bool = True
    remove_thinking_instructions: bool = True

    # Context optimization
    optimize_context: bool = True
    deduplicate_context: bool = True
    summarize_long_histories: bool = True
    max_context_tokens: int = 5000

    # Dynamic adaptation
    enable_dynamic_adaptation: bool = True
    use_learned_patterns: bool = True
    adapt_to_task_type: bool = True

    # Token budget targets
    target_prompt_tokens: int = 500
    target_tool_tokens: int = 2000
    target_context_tokens: int = 5000
    target_total_tokens: int = 7500

    # Optimization rules
    custom_rules: list[OptimizationRule] = Field(default_factory=list)

    # Metrics
    track_metrics: bool = True
    metrics_retention_days: int = 30


class SystemPromptTemplate(BaseModel):
    """Template for system prompts."""

    name: str
    template: str
    variables: dict[str, str] = Field(default_factory=dict)
    estimated_tokens: int = 0
    description: str = ""


class ContextOptimizationResult(BaseModel):
    """Result of context optimization."""

    original_tokens: int
    optimized_tokens: int
    token_savings: int
    token_savings_percent: float
    deduplication_count: int = 0
    summarization_count: int = 0
    noise_removed_count: int = 0
    optimizations_applied: list[str] = Field(default_factory=list)


class PromptEngineeringMetrics(BaseModel):
    """Comprehensive metrics for prompt engineering."""

    # Tool description metrics
    tool_descriptions: OptimizationMetrics

    # System prompt metrics
    system_prompts: OptimizationMetrics

    # Context metrics
    context_optimizations: list[ContextOptimizationResult] = Field(default_factory=list)

    # Overall metrics
    total_token_savings: int = 0
    total_savings_percent: float = 0.0

    # Performance
    optimization_overhead_ms: float = 0.0

    # Timestamp
    collected_at: datetime = Field(default_factory=datetime.now)


class AdaptivePromptContext(BaseModel):
    """Context for adaptive prompt generation."""

    task_type: str  # e.g., "coding", "research", "browser_automation"
    learned_patterns: list[str] = Field(default_factory=list)
    user_preferences: dict[str, Any] = Field(default_factory=dict)
    recent_failures: list[str] = Field(default_factory=list)
    suggested_tools: list[str] = Field(default_factory=list)
    confidence: float = 0.0
