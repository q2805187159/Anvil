"""Tool description optimizer for token reduction."""

from __future__ import annotations

import re
from typing import Any

from .contracts import (
    OptimizationMetrics,
    OptimizationRule,
    OptimizationStrategy,
    OptimizedDescription,
    PromptEngineeringConfig,
)


class ToolDescriptionOptimizer:
    """Optimizes tool descriptions to reduce token usage.

    Core principle: Describe WHAT tools do, not HOW to use them.
    Modern LLMs are capable enough to figure out usage themselves.
    """

    def __init__(self, config: PromptEngineeringConfig):
        """Initialize optimizer.

        Args:
            config: Prompt engineering configuration
        """
        self.config = config
        self.metrics = OptimizationMetrics()
        self._rules = self._build_optimization_rules()

    def _build_optimization_rules(self) -> list[OptimizationRule]:
        """Build optimization rules based on strategy.

        Returns:
            List of optimization rules
        """
        # Base rules that apply to all strategies
        base_rules = [
            OptimizationRule(
                name="remove_implementation_details",
                pattern=r"\s+(using|through|via|with)\s+(?:the\s+)?configured\s+\w+(?:\s+\w+)*",
                replacement="",
                description="Remove implementation details like 'using the configured provider'",
                token_savings_estimate=5,
            ),
            OptimizationRule(
                name="remove_file_paths",
                pattern=r"\s+(?:under|in|at|to)\s+/[\w\-/]+",
                replacement="",
                description="Remove specific file paths",
                token_savings_estimate=4,
            ),
            OptimizationRule(
                name="remove_output_format_details",
                pattern=r"\s+and\s+return\s+(?:compact|normalized|detailed)\s+",
                replacement=" return ",
                description="Remove output format adjectives",
                token_savings_estimate=2,
            ),
            OptimizationRule(
                name="simplify_return_statements",
                pattern=r"\s+and\s+return\s+",
                replacement=", return ",
                description="Simplify 'and return' to comma",
                token_savings_estimate=1,
            ),
            OptimizationRule(
                name="remove_usage_examples",
                pattern=r"\s+such\s+as\s+[^.]+",
                replacement="",
                description="Remove usage examples like 'such as @e1'",
                token_savings_estimate=4,
            ),
            OptimizationRule(
                name="remove_redundant_articles",
                pattern=r"\bthe\s+current\s+",
                replacement="",
                description="Remove 'the current'",
                token_savings_estimate=2,
            ),
            OptimizationRule(
                name="remove_redundant_one",
                pattern=r"\s+one\s+",
                replacement=" ",
                description="Remove redundant 'one'",
                token_savings_estimate=1,
            ),
            OptimizationRule(
                name="simplify_list_verbs",
                pattern=r"List\s+(?:all\s+)?(?:available\s+)?",
                replacement="List ",
                description="Simplify list verb phrases",
                token_savings_estimate=2,
            ),
            OptimizationRule(
                name="remove_prefer_hints",
                pattern=r"\.\s+Prefer\s+[^.]+\.",
                replacement=".",
                description="Remove 'Prefer' usage hints",
                token_savings_estimate=5,
            ),
            OptimizationRule(
                name="simplify_inspect",
                pattern=r"Inspect\s+(?:a\s+)?single\s+",
                replacement="Inspect ",
                description="Simplify inspect phrases",
                token_savings_estimate=2,
            ),
        ]

        # Aggressive strategy adds more rules
        if self.config.optimization_strategy == OptimizationStrategy.AGGRESSIVE:
            base_rules.extend([
                OptimizationRule(
                    name="remove_all_articles",
                    pattern=r"\b(?:a|an|the)\s+",
                    replacement="",
                    description="Remove all articles",
                    token_savings_estimate=1,
                ),
                OptimizationRule(
                    name="simplify_conjunctions",
                    pattern=r"\s+and\s+",
                    replacement=", ",
                    description="Replace 'and' with comma",
                    token_savings_estimate=1,
                ),
                OptimizationRule(
                    name="remove_plus_metadata",
                    pattern=r"\s+plus\s+metadata",
                    replacement="",
                    description="Remove 'plus metadata'",
                    token_savings_estimate=2,
                ),
            ])

        # Add custom rules from config
        base_rules.extend(self.config.custom_rules)

        return [rule for rule in base_rules if rule.enabled]

    def optimize(self, description: str) -> OptimizedDescription:
        """Optimize a tool description.

        Args:
            description: Original description

        Returns:
            Optimization result
        """
        if not self.config.optimize_tool_descriptions:
            return OptimizedDescription(
                original=description,
                optimized=description,
                original_tokens=self._count_tokens(description),
                optimized_tokens=self._count_tokens(description),
                token_savings=0,
                token_savings_percent=0.0,
            )

        original_tokens = self._count_tokens(description)
        optimized = description
        rules_applied = []

        # Apply optimization rules
        for rule in self._rules:
            before = optimized
            optimized = re.sub(rule.pattern, rule.replacement, optimized, flags=re.IGNORECASE)
            if before != optimized:
                rules_applied.append(rule.name)
                self.metrics.rules_applied_count[rule.name] = (
                    self.metrics.rules_applied_count.get(rule.name, 0) + 1
                )

        # Clean up extra spaces
        optimized = re.sub(r'\s+', ' ', optimized).strip()

        # Ensure first letter is capitalized
        if optimized:
            optimized = optimized[0].upper() + optimized[1:]

        optimized_tokens = self._count_tokens(optimized)
        token_savings = original_tokens - optimized_tokens
        token_savings_percent = (token_savings / original_tokens * 100) if original_tokens > 0 else 0.0

        # Update metrics
        self.metrics.total_descriptions_optimized += 1
        self.metrics.total_original_tokens += original_tokens
        self.metrics.total_optimized_tokens += optimized_tokens
        self.metrics.total_token_savings += token_savings
        self.metrics.average_savings_percent = (
            (self.metrics.total_token_savings / self.metrics.total_original_tokens * 100)
            if self.metrics.total_original_tokens > 0
            else 0.0
        )

        return OptimizedDescription(
            original=description,
            optimized=optimized,
            original_tokens=original_tokens,
            optimized_tokens=optimized_tokens,
            token_savings=token_savings,
            token_savings_percent=token_savings_percent,
            rules_applied=rules_applied,
        )

    def optimize_batch(self, descriptions: list[str]) -> list[OptimizedDescription]:
        """Optimize multiple descriptions.

        Args:
            descriptions: List of descriptions

        Returns:
            List of optimization results
        """
        return [self.optimize(desc) for desc in descriptions]

    def _count_tokens(self, text: str) -> int:
        """Estimate token count for text.

        Uses simple word-based estimation: ~0.75 words per token.

        Args:
            text: Text to count

        Returns:
            Estimated token count
        """
        if not text:
            return 0
        words = len(text.split())
        return int(words / 0.75)  # Approximate: 1 token ≈ 0.75 words

    def get_metrics(self) -> OptimizationMetrics:
        """Get optimization metrics.

        Returns:
            Current metrics
        """
        return self.metrics

    def reset_metrics(self) -> None:
        """Reset metrics to zero."""
        self.metrics = OptimizationMetrics()

    def get_optimization_report(self) -> dict[str, Any]:
        """Get detailed optimization report.

        Returns:
            Report with metrics and statistics
        """
        return {
            "total_optimized": self.metrics.total_descriptions_optimized,
            "original_tokens": self.metrics.total_original_tokens,
            "optimized_tokens": self.metrics.total_optimized_tokens,
            "token_savings": self.metrics.total_token_savings,
            "savings_percent": self.metrics.average_savings_percent,
            "rules_applied": dict(self.metrics.rules_applied_count),
            "top_rules": sorted(
                self.metrics.rules_applied_count.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5],
            "average_original_tokens": (
                self.metrics.total_original_tokens / self.metrics.total_descriptions_optimized
                if self.metrics.total_descriptions_optimized > 0
                else 0
            ),
            "average_optimized_tokens": (
                self.metrics.total_optimized_tokens / self.metrics.total_descriptions_optimized
                if self.metrics.total_descriptions_optimized > 0
                else 0
            ),
        }
