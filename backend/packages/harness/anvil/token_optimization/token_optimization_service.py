"""Unified token optimization service coordinating all components."""

from __future__ import annotations

import logging
from typing import Any

from .contracts import (
    ContextItem,
    TokenOptimizationConfig,
    TokenOptimizationMetrics,
)
from .intelligent_truncator import IntelligentTruncator
from .pattern_summarizer import PatternSummarizer
from .semantic_compressor import SemanticCompressor
from .token_budget_enforcer import TokenBudgetEnforcer

logger = logging.getLogger(__name__)


class TokenOptimizationService:
    """Unified service for token optimization.

    Coordinates compression, truncation, summarization, and budget enforcement
    to achieve maximum token reduction while preserving quality.
    """

    def __init__(self, config: TokenOptimizationConfig):
        """Initialize token optimization service.

        Args:
            config: Token optimization configuration
        """
        self.config = config

        # Initialize components
        self.compressor = SemanticCompressor(config)
        self.truncator = IntelligentTruncator(config)
        self.summarizer = PatternSummarizer(config)
        self.budget_enforcer = TokenBudgetEnforcer(config)

        # Metrics
        self.metrics = TokenOptimizationMetrics()

    def optimize_content(
        self,
        content: str,
        target_ratio: float | None = None,
    ) -> str:
        """Optimize content using compression.

        Args:
            content: Content to optimize
            target_ratio: Target compression ratio

        Returns:
            Optimized content
        """
        result = self.compressor.compress(content, target_ratio)

        # Update metrics
        self.metrics.total_compressions += 1
        self.metrics.total_compression_savings += result.token_savings
        self.metrics.average_compression_ratio = (
            (self.metrics.average_compression_ratio * (self.metrics.total_compressions - 1) +
             result.compression_ratio) / self.metrics.total_compressions
        )
        self.metrics.average_compression_time_ms = (
            (self.metrics.average_compression_time_ms * (self.metrics.total_compressions - 1) +
             result.compression_time_ms) / self.metrics.total_compressions
        )

        return result.compressed

    def optimize_context(
        self,
        items: list[ContextItem],
        max_tokens: int,
        task_type: str | None = None,
    ) -> list[ContextItem]:
        """Optimize context using truncation.

        Args:
            items: Context items to optimize
            max_tokens: Maximum tokens allowed
            task_type: Optional task type

        Returns:
            Optimized context items
        """
        truncated, result = self.truncator.truncate(items, max_tokens, task_type)

        # Update metrics
        self.metrics.total_truncations += 1
        self.metrics.total_truncation_savings += result.token_savings
        self.metrics.average_items_removed = (
            (self.metrics.average_items_removed * (self.metrics.total_truncations - 1) +
             result.items_removed) / self.metrics.total_truncations
        )
        self.metrics.average_truncation_time_ms = (
            (self.metrics.average_truncation_time_ms * (self.metrics.total_truncations - 1) +
             result.truncation_time_ms) / self.metrics.total_truncations
        )

        return truncated

    def summarize_content(
        self,
        content: str,
        level: str = "brief",
    ) -> str:
        """Summarize content using patterns.

        Args:
            content: Content to summarize
            level: Summarization level

        Returns:
            Summarized content
        """
        result = self.summarizer.summarize(content, level)

        # Update metrics
        self.metrics.total_summarizations += 1
        self.metrics.total_summarization_savings += result.token_savings
        ratio = result.summary_tokens / result.original_tokens if result.original_tokens > 0 else 1.0
        self.metrics.average_summarization_ratio = (
            (self.metrics.average_summarization_ratio * (self.metrics.total_summarizations - 1) +
             ratio) / self.metrics.total_summarizations
        )
        self.metrics.average_summarization_time_ms = (
            (self.metrics.average_summarization_time_ms * (self.metrics.total_summarizations - 1) +
             result.summarization_time_ms) / self.metrics.total_summarizations
        )

        return result.summary

    def enforce_budget(
        self,
        system_prompt: str | None = None,
        tool_descriptions: list[str] | None = None,
        context_items: list[ContextItem] | None = None,
    ) -> dict[str, Any]:
        """Enforce token budget.

        Args:
            system_prompt: System prompt to check
            tool_descriptions: Tool descriptions to check
            context_items: Context items to check

        Returns:
            Enforcement result
        """
        result = self.budget_enforcer.enforce_budget(
            system_prompt=system_prompt,
            tool_descriptions=tool_descriptions,
            context_items=context_items,
        )

        if result["budget_exceeded"]:
            self.metrics.budget_exceeded_count += 1

        return result

    def optimize_full_context(
        self,
        system_prompt: str,
        tool_descriptions: list[str],
        context_items: list[ContextItem],
    ) -> dict[str, Any]:
        """Optimize full context (system prompt, tools, context).

        Args:
            system_prompt: System prompt
            tool_descriptions: Tool descriptions
            context_items: Context items

        Returns:
            Optimized components and metrics
        """
        # Compress system prompt if needed
        optimized_system_prompt = system_prompt
        if len(system_prompt.split()) > self.config.system_prompt_budget * 0.75:
            optimized_system_prompt = self.optimize_content(
                system_prompt,
                target_ratio=0.7
            )

        # Compress tool descriptions if needed
        optimized_tools = tool_descriptions
        total_tool_tokens = sum(len(desc.split()) / 0.75 for desc in tool_descriptions)
        if total_tool_tokens > self.config.tool_description_budget:
            optimized_tools = [
                self.optimize_content(desc, target_ratio=0.7)
                for desc in tool_descriptions
            ]

        # Truncate context to budget
        optimized_context = self.optimize_context(
            context_items,
            self.config.context_budget
        )

        # Check final budget
        budget_result = self.enforce_budget(
            system_prompt=optimized_system_prompt,
            tool_descriptions=optimized_tools,
            context_items=optimized_context,
        )

        # If still exceeded, emergency truncation
        if budget_result["budget_exceeded"]:
            self.metrics.emergency_truncations += 1
            optimized_context = self.budget_enforcer.emergency_truncate_context(
                optimized_context
            )

        return {
            "system_prompt": optimized_system_prompt,
            "tool_descriptions": optimized_tools,
            "context_items": optimized_context,
            "budget_status": budget_result,
            "metrics": self.get_metrics_summary(),
        }

    def get_metrics_summary(self) -> dict[str, Any]:
        """Get metrics summary.

        Returns:
            Metrics summary
        """
        # Calculate total savings
        self.metrics.total_token_savings = (
            self.metrics.total_compression_savings +
            self.metrics.total_truncation_savings +
            self.metrics.total_summarization_savings
        )

        # Calculate total operations
        self.metrics.total_operations = (
            self.metrics.total_compressions +
            self.metrics.total_truncations +
            self.metrics.total_summarizations
        )

        # Calculate average operation time
        if self.metrics.total_operations > 0:
            total_time = (
                self.metrics.average_compression_time_ms * self.metrics.total_compressions +
                self.metrics.average_truncation_time_ms * self.metrics.total_truncations +
                self.metrics.average_summarization_time_ms * self.metrics.total_summarizations
            )
            self.metrics.average_operation_time_ms = total_time / self.metrics.total_operations

        return {
            "compression": {
                "total": self.metrics.total_compressions,
                "savings": self.metrics.total_compression_savings,
                "avg_ratio": self.metrics.average_compression_ratio,
                "avg_time_ms": self.metrics.average_compression_time_ms,
            },
            "truncation": {
                "total": self.metrics.total_truncations,
                "savings": self.metrics.total_truncation_savings,
                "avg_items_removed": self.metrics.average_items_removed,
                "avg_time_ms": self.metrics.average_truncation_time_ms,
            },
            "summarization": {
                "total": self.metrics.total_summarizations,
                "savings": self.metrics.total_summarization_savings,
                "avg_ratio": self.metrics.average_summarization_ratio,
                "avg_time_ms": self.metrics.average_summarization_time_ms,
            },
            "overall": {
                "total_operations": self.metrics.total_operations,
                "total_savings": self.metrics.total_token_savings,
                "avg_operation_time_ms": self.metrics.average_operation_time_ms,
                "budget_exceeded_count": self.metrics.budget_exceeded_count,
                "emergency_truncations": self.metrics.emergency_truncations,
            },
        }

    def reset_metrics(self) -> None:
        """Reset all metrics."""
        self.metrics = TokenOptimizationMetrics()
        self.budget_enforcer.reset_budget()
