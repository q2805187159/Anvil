"""Token budget enforcer for hard limits and graceful degradation."""

from __future__ import annotations

import logging
from typing import Any

from .contracts import ContextItem, TokenBudget, TokenOptimizationConfig

logger = logging.getLogger(__name__)


class TokenBudgetEnforcer:
    """Enforces token budget limits with graceful degradation.

    Monitors token usage and applies emergency measures when
    budget is exceeded.
    """

    def __init__(self, config: TokenOptimizationConfig):
        """Initialize enforcer.

        Args:
            config: Token optimization configuration
        """
        self.config = config
        self.budget = TokenBudget(
            system_prompt=config.system_prompt_budget,
            tool_descriptions=config.tool_description_budget,
            context=config.context_budget,
            response_buffer=config.response_buffer,
            total=config.total_budget,
        )

    def check_budget(self) -> dict[str, Any]:
        """Check current budget status.

        Returns:
            Budget status information
        """
        return {
            "total_budget": self.budget.total,
            "used": self.budget.current_system_prompt + self.budget.current_tool_descriptions + self.budget.current_context,
            "remaining": self.budget.remaining,
            "is_exceeded": self.budget.is_exceeded,
            "breakdown": {
                "system_prompt": {
                    "budget": self.budget.system_prompt,
                    "used": self.budget.current_system_prompt,
                    "remaining": self.budget.system_prompt - self.budget.current_system_prompt,
                },
                "tool_descriptions": {
                    "budget": self.budget.tool_descriptions,
                    "used": self.budget.current_tool_descriptions,
                    "remaining": self.budget.tool_descriptions - self.budget.current_tool_descriptions,
                },
                "context": {
                    "budget": self.budget.context,
                    "used": self.budget.current_context,
                    "remaining": self.budget.context - self.budget.current_context,
                },
            },
        }

    def update_usage(
        self,
        system_prompt_tokens: int | None = None,
        tool_description_tokens: int | None = None,
        context_tokens: int | None = None,
    ) -> None:
        """Update token usage.

        Args:
            system_prompt_tokens: System prompt token count
            tool_description_tokens: Tool description token count
            context_tokens: Context token count
        """
        if system_prompt_tokens is not None:
            self.budget.current_system_prompt = system_prompt_tokens

        if tool_description_tokens is not None:
            self.budget.current_tool_descriptions = tool_description_tokens

        if context_tokens is not None:
            self.budget.current_context = context_tokens

    def enforce_budget(
        self,
        system_prompt: str | None = None,
        tool_descriptions: list[str] | None = None,
        context_items: list[ContextItem] | None = None,
    ) -> dict[str, Any]:
        """Enforce budget limits with emergency measures if needed.

        Args:
            system_prompt: System prompt to check
            tool_descriptions: Tool descriptions to check
            context_items: Context items to check

        Returns:
            Enforcement result with actions taken
        """
        actions_taken = []

        # Check system prompt
        if system_prompt:
            tokens = self._count_tokens(system_prompt)
            if tokens > self.budget.system_prompt:
                logger.warning(
                    f"System prompt exceeds budget: {tokens} > {self.budget.system_prompt}"
                )
                actions_taken.append({
                    "component": "system_prompt",
                    "action": "truncate",
                    "original_tokens": tokens,
                    "budget": self.budget.system_prompt,
                })

        # Check tool descriptions
        if tool_descriptions:
            total_tokens = sum(self._count_tokens(desc) for desc in tool_descriptions)
            if total_tokens > self.budget.tool_descriptions:
                logger.warning(
                    f"Tool descriptions exceed budget: {total_tokens} > {self.budget.tool_descriptions}"
                )
                actions_taken.append({
                    "component": "tool_descriptions",
                    "action": "reduce",
                    "original_tokens": total_tokens,
                    "budget": self.budget.tool_descriptions,
                })

        # Check context
        if context_items:
            total_tokens = sum(item.tokens for item in context_items)
            if total_tokens > self.budget.context:
                logger.warning(
                    f"Context exceeds budget: {total_tokens} > {self.budget.context}"
                )
                actions_taken.append({
                    "component": "context",
                    "action": "truncate",
                    "original_tokens": total_tokens,
                    "budget": self.budget.context,
                    "items_count": len(context_items),
                })

        return {
            "budget_exceeded": len(actions_taken) > 0,
            "actions_taken": actions_taken,
            "budget_status": self.check_budget(),
        }

    def emergency_truncate_context(
        self,
        items: list[ContextItem],
        target_tokens: int | None = None,
    ) -> list[ContextItem]:
        """Emergency truncation of context to fit budget.

        Args:
            items: Context items to truncate
            target_tokens: Target token count, defaults to budget

        Returns:
            Truncated context items
        """
        target = target_tokens or self.budget.context

        # Sort by priority and recency
        priority_order = {"high": 3, "medium": 2, "low": 1}
        sorted_items = sorted(
            items,
            key=lambda x: (priority_order.get(x.priority, 0), x.timestamp),
            reverse=True
        )

        # Take items until budget met
        truncated = []
        current_tokens = 0

        for item in sorted_items:
            if current_tokens + item.tokens <= target:
                truncated.append(item)
                current_tokens += item.tokens
            else:
                break

        # Restore chronological order
        truncated.sort(key=lambda x: x.timestamp)

        logger.info(
            f"Emergency truncation: {len(items)} → {len(truncated)} items, "
            f"{sum(i.tokens for i in items)} → {current_tokens} tokens"
        )

        return truncated

    def _count_tokens(self, text: str) -> int:
        """Estimate token count.

        Args:
            text: Text to count

        Returns:
            Estimated token count
        """
        if not text:
            return 0
        words = len(text.split())
        return int(words / 0.75)

    def reset_budget(self) -> None:
        """Reset budget to initial state."""
        self.budget.current_system_prompt = 0
        self.budget.current_tool_descriptions = 0
        self.budget.current_context = 0
