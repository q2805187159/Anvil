"""Unified prompt engineering service coordinating all optimization components."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .contracts import (
    AdaptivePromptContext,
    ContextOptimizationResult,
    PromptEngineeringConfig,
    PromptEngineeringMetrics,
)
from .system_prompt_optimizer import SystemPromptOptimizer
from .tool_description_optimizer import ToolDescriptionOptimizer

logger = logging.getLogger(__name__)


class PromptEngineeringService:
    """Unified service for prompt engineering and token optimization.

    Features:
    - Tool description optimization
    - System prompt optimization
    - Context noise reduction
    - Dynamic adaptation based on learning
    - Comprehensive metrics tracking
    """

    def __init__(self, config: PromptEngineeringConfig):
        """Initialize prompt engineering service.

        Args:
            config: Prompt engineering configuration
        """
        self.config = config

        # Initialize optimizers
        self.tool_optimizer = ToolDescriptionOptimizer(config)
        self.system_optimizer = SystemPromptOptimizer(config)

        # Metrics
        self.context_optimizations: list[ContextOptimizationResult] = []

    def optimize_tool_description(self, description: str) -> str:
        """Optimize a single tool description.

        Args:
            description: Original description

        Returns:
            Optimized description
        """
        if not self.config.optimize_tool_descriptions:
            return description

        result = self.tool_optimizer.optimize(description)
        return result.optimized

    def optimize_tool_descriptions_batch(self, descriptions: dict[str, str]) -> dict[str, str]:
        """Optimize multiple tool descriptions.

        Args:
            descriptions: Dict of tool_name -> description

        Returns:
            Dict of tool_name -> optimized description
        """
        if not self.config.optimize_tool_descriptions:
            return descriptions

        optimized = {}
        for tool_name, description in descriptions.items():
            result = self.tool_optimizer.optimize(description)
            optimized[tool_name] = result.optimized

        return optimized

    def optimize_system_prompt(self, system_prompt: str) -> str:
        """Optimize a system prompt.

        Args:
            system_prompt: Original system prompt

        Returns:
            Optimized system prompt
        """
        if not self.config.optimize_system_prompts:
            return system_prompt

        result = self.system_optimizer.optimize(system_prompt)
        return result.optimized

    def optimize_context(
        self,
        context_items: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> tuple[list[dict[str, Any]], ContextOptimizationResult]:
        """Optimize context by removing noise and deduplication.

        Args:
            context_items: List of context items
            max_tokens: Optional max token limit

        Returns:
            Tuple of (optimized items, optimization result)
        """
        if not self.config.optimize_context:
            return context_items, ContextOptimizationResult(
                original_tokens=0,
                optimized_tokens=0,
                token_savings=0,
                token_savings_percent=0.0,
            )

        original_tokens = sum(self._estimate_tokens(item) for item in context_items)
        optimized_items = context_items.copy()
        optimizations_applied = []
        deduplication_count = 0
        noise_removed_count = 0

        # Deduplicate similar items
        if self.config.deduplicate_context:
            optimized_items, dup_count = self._deduplicate_items(optimized_items)
            deduplication_count = dup_count
            if dup_count > 0:
                optimizations_applied.append(f"deduplicated_{dup_count}_items")

        # Remove noise
        optimized_items, noise_count = self._remove_noise(optimized_items)
        noise_removed_count = noise_count
        if noise_count > 0:
            optimizations_applied.append(f"removed_{noise_count}_noise_items")

        # Truncate if needed
        max_tokens = max_tokens or self.config.max_context_tokens
        if max_tokens:
            optimized_items = self._truncate_to_budget(optimized_items, max_tokens)
            optimizations_applied.append(f"truncated_to_{max_tokens}_tokens")

        optimized_tokens = sum(self._estimate_tokens(item) for item in optimized_items)
        token_savings = original_tokens - optimized_tokens
        token_savings_percent = (token_savings / original_tokens * 100) if original_tokens > 0 else 0.0

        result = ContextOptimizationResult(
            original_tokens=original_tokens,
            optimized_tokens=optimized_tokens,
            token_savings=token_savings,
            token_savings_percent=token_savings_percent,
            deduplication_count=deduplication_count,
            noise_removed_count=noise_removed_count,
            optimizations_applied=optimizations_applied,
        )

        self.context_optimizations.append(result)

        return optimized_items, result

    def generate_adaptive_prompt(
        self,
        base_prompt: str,
        context: AdaptivePromptContext,
    ) -> str:
        """Generate adaptive prompt based on learned patterns.

        Args:
            base_prompt: Base prompt template
            context: Adaptive context with learned patterns

        Returns:
            Adapted prompt
        """
        if not self.config.enable_dynamic_adaptation:
            return base_prompt

        adapted = base_prompt

        # Add learned patterns if available
        if context.learned_patterns and self.config.use_learned_patterns:
            patterns_text = "\n".join([f"- {pattern}" for pattern in context.learned_patterns[:3]])
            adapted += f"\n\nSuccessful patterns:\n{patterns_text}"

        # Add suggested tools if available
        if context.suggested_tools:
            tools_text = ", ".join(context.suggested_tools[:5])
            adapted += f"\n\nRelevant tools: {tools_text}"

        # Add failure warnings if available
        if context.recent_failures:
            failures_text = "\n".join([f"- Avoid: {failure}" for failure in context.recent_failures[:2]])
            adapted += f"\n\nRecent issues:\n{failures_text}"

        return adapted

    def _deduplicate_items(
        self,
        items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Remove duplicate or very similar items.

        Args:
            items: Context items

        Returns:
            Tuple of (deduplicated items, count removed)
        """
        seen_content = set()
        deduplicated = []
        removed_count = 0

        for item in items:
            # Create a simple hash of the item content
            content_str = str(item.get("content", ""))
            content_hash = hash(content_str[:200])  # Use first 200 chars

            if content_hash not in seen_content:
                seen_content.add(content_hash)
                deduplicated.append(item)
            else:
                removed_count += 1

        return deduplicated, removed_count

    def _remove_noise(
        self,
        items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Remove noise items (empty, very short, etc.).

        Args:
            items: Context items

        Returns:
            Tuple of (cleaned items, count removed)
        """
        cleaned = []
        removed_count = 0

        for item in items:
            content = str(item.get("content", "")).strip()

            # Skip empty items
            if not content:
                removed_count += 1
                continue

            # Skip very short items (likely noise)
            if len(content) < 10:
                removed_count += 1
                continue

            cleaned.append(item)

        return cleaned, removed_count

    def _truncate_to_budget(
        self,
        items: list[dict[str, Any]],
        max_tokens: int,
    ) -> list[dict[str, Any]]:
        """Truncate items to fit within token budget.

        Args:
            items: Context items
            max_tokens: Maximum tokens allowed

        Returns:
            Truncated items
        """
        truncated = []
        current_tokens = 0

        # Prioritize recent items (assume items are in chronological order)
        for item in reversed(items):
            item_tokens = self._estimate_tokens(item)
            if current_tokens + item_tokens <= max_tokens:
                truncated.insert(0, item)
                current_tokens += item_tokens
            else:
                break

        return truncated

    def _estimate_tokens(self, item: dict[str, Any]) -> int:
        """Estimate token count for a context item.

        Args:
            item: Context item

        Returns:
            Estimated token count
        """
        content = str(item.get("content", ""))
        words = len(content.split())
        return int(words / 0.75)  # Approximate: 1 token ≈ 0.75 words

    def get_comprehensive_metrics(self) -> PromptEngineeringMetrics:
        """Get comprehensive metrics for all optimizations.

        Returns:
            Complete metrics
        """
        tool_metrics = self.tool_optimizer.get_metrics()
        system_metrics = self.system_optimizer.get_metrics()

        total_savings = (
            tool_metrics.total_token_savings +
            system_metrics.total_token_savings +
            sum(opt.token_savings for opt in self.context_optimizations)
        )

        total_original = (
            tool_metrics.total_original_tokens +
            system_metrics.total_original_tokens +
            sum(opt.original_tokens for opt in self.context_optimizations)
        )

        total_savings_percent = (
            (total_savings / total_original * 100) if total_original > 0 else 0.0
        )

        return PromptEngineeringMetrics(
            tool_descriptions=tool_metrics,
            system_prompts=system_metrics,
            context_optimizations=self.context_optimizations.copy(),
            total_token_savings=total_savings,
            total_savings_percent=total_savings_percent,
        )

    def get_optimization_summary(self) -> dict[str, Any]:
        """Get summary of all optimizations.

        Returns:
            Summary dict
        """
        metrics = self.get_comprehensive_metrics()

        return {
            "tool_descriptions": self.tool_optimizer.get_optimization_report(),
            "system_prompts": self.system_optimizer.get_optimization_report(),
            "context_optimizations": {
                "total_optimizations": len(self.context_optimizations),
                "total_token_savings": sum(opt.token_savings for opt in self.context_optimizations),
                "average_savings_percent": (
                    sum(opt.token_savings_percent for opt in self.context_optimizations) /
                    len(self.context_optimizations)
                    if self.context_optimizations else 0.0
                ),
            },
            "overall": {
                "total_token_savings": metrics.total_token_savings,
                "total_savings_percent": metrics.total_savings_percent,
            },
        }

    def reset_metrics(self) -> None:
        """Reset all metrics."""
        self.tool_optimizer.reset_metrics()
        self.system_optimizer.reset_metrics()
        self.context_optimizations.clear()
