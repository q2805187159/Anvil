"""Intelligent truncator for priority-based context reduction."""

from __future__ import annotations

from datetime import datetime

from .contracts import ContextItem, TokenOptimizationConfig, TruncationResult, TruncationStrategy


class IntelligentTruncator:
    """Truncates context intelligently based on priority and relevance.

    Preserves high-priority content while removing low-priority items
    to fit within token budget.
    """

    def __init__(self, config: TokenOptimizationConfig):
        """Initialize truncator.

        Args:
            config: Token optimization configuration
        """
        self.config = config

    def truncate(
        self,
        items: list[ContextItem],
        max_tokens: int,
        task_type: str | None = None,
    ) -> tuple[list[ContextItem], TruncationResult]:
        """Truncate items to fit within token budget.

        Args:
            items: Context items to truncate
            max_tokens: Maximum tokens allowed
            task_type: Optional task type for context-aware truncation

        Returns:
            Tuple of (truncated items, truncation result)
        """
        if not self.config.enable_intelligent_truncation:
            original_tokens = sum(item.tokens for item in items)
            return items, TruncationResult(
                original_count=len(items),
                truncated_count=len(items),
                items_removed=0,
                original_tokens=original_tokens,
                truncated_tokens=original_tokens,
                token_savings=0,
                strategy_used="none",
            )

        start_time = datetime.now()

        # Calculate original metrics
        original_count = len(items)
        original_tokens = sum(item.tokens for item in items)

        # Choose truncation strategy
        strategy = self.config.truncation_strategy

        if strategy == TruncationStrategy.PRIORITY:
            truncated = self._truncate_by_priority(items, max_tokens)
        elif strategy == TruncationStrategy.SLIDING:
            truncated = self._truncate_sliding_window(items, max_tokens)
        else:  # HYBRID
            truncated = self._truncate_hybrid(items, max_tokens, task_type)

        # Calculate result metrics
        truncated_count = len(truncated)
        truncated_tokens = sum(item.tokens for item in truncated)
        items_removed = original_count - truncated_count
        token_savings = original_tokens - truncated_tokens

        truncation_time = (datetime.now() - start_time).total_seconds() * 1000

        result = TruncationResult(
            original_count=original_count,
            truncated_count=truncated_count,
            items_removed=items_removed,
            original_tokens=original_tokens,
            truncated_tokens=truncated_tokens,
            token_savings=token_savings,
            strategy_used=strategy.value,
            truncation_time_ms=truncation_time,
        )

        return truncated, result

    def _truncate_by_priority(self, items: list[ContextItem], max_tokens: int) -> list[ContextItem]:
        """Truncate by priority, keeping high-priority items.

        Args:
            items: Items to truncate
            max_tokens: Maximum tokens

        Returns:
            Truncated items
        """
        # Sort by priority (high > medium > low) and recency
        priority_order = {"high": 3, "medium": 2, "low": 1}

        sorted_items = sorted(
            items,
            key=lambda x: (priority_order.get(x.priority, 0), x.timestamp),
            reverse=True
        )

        # Take items until budget exceeded
        truncated = []
        current_tokens = 0

        for item in sorted_items:
            if current_tokens + item.tokens <= max_tokens:
                truncated.append(item)
                current_tokens += item.tokens
            elif len(truncated) < self.config.min_context_items:
                # Always keep minimum items
                truncated.append(item)
                current_tokens += item.tokens

        # Restore chronological order
        truncated.sort(key=lambda x: x.timestamp)

        return truncated

    def _truncate_sliding_window(self, items: list[ContextItem], max_tokens: int) -> list[ContextItem]:
        """Truncate using sliding window, keeping recent items.

        Args:
            items: Items to truncate
            max_tokens: Maximum tokens

        Returns:
            Truncated items
        """
        # Sort by timestamp (most recent first)
        sorted_items = sorted(items, key=lambda x: x.timestamp, reverse=True)

        # Take recent items until budget exceeded
        truncated = []
        current_tokens = 0

        for item in sorted_items:
            if current_tokens + item.tokens <= max_tokens:
                truncated.append(item)
                current_tokens += item.tokens
            elif len(truncated) < self.config.min_context_items:
                truncated.append(item)
                current_tokens += item.tokens

        # Restore chronological order
        truncated.sort(key=lambda x: x.timestamp)

        return truncated

    def _truncate_hybrid(
        self,
        items: list[ContextItem],
        max_tokens: int,
        task_type: str | None,
    ) -> list[ContextItem]:
        """Hybrid truncation combining priority and recency.

        Args:
            items: Items to truncate
            max_tokens: Maximum tokens
            task_type: Task type for context-aware truncation

        Returns:
            Truncated items
        """
        # Calculate score combining priority and recency
        priority_order = {"high": 3, "medium": 2, "low": 1}
        now = datetime.now()

        scored_items = []
        for item in items:
            priority_score = priority_order.get(item.priority, 0) * 100
            recency_score = 1.0 / (1.0 + (now - item.timestamp).total_seconds() / 3600)  # Decay over hours
            combined_score = priority_score + recency_score * 10

            scored_items.append((combined_score, item))

        # Sort by combined score
        scored_items.sort(key=lambda x: x[0], reverse=True)

        # Take items until budget exceeded
        truncated = []
        current_tokens = 0

        for score, item in scored_items:
            if current_tokens + item.tokens <= max_tokens:
                truncated.append(item)
                current_tokens += item.tokens
            elif len(truncated) < self.config.min_context_items:
                truncated.append(item)
                current_tokens += item.tokens

        # Restore chronological order
        truncated.sort(key=lambda x: x.timestamp)

        return truncated
