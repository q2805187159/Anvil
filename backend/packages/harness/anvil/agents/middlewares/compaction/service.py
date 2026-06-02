"""Core compaction service implementing priority-based context compression."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage

from anvil.runtime.token_budget import TokenBudgetService

from .analyzer import ContentAnalyzer
from .compressor import LLMCompressor
from .contracts import CompactionConfig
from .metrics import CompactionMetrics
from .priority import PriorityClassifier

if TYPE_CHECKING:
    from anvil.agents.lead_agent.types import LeadAgentContext
    from anvil.config import EffectiveConfig

logger = logging.getLogger(__name__)


class CompactionService:
    """Core compaction logic following harness-first principles.

    Implements multi-tier context compression based on JavaGuide principles:
    1. Classify messages by priority (HIGH/MEDIUM/LOW)
    2. Preserve high-priority content (never discard)
    3. Compress medium-priority content (trim if needed)
    4. Discard low-priority content (aggressive compression)
    5. Assemble compacted message list

    Design:
    - Priority-based retention (40% threshold principle)
    - LLM-powered semantic compression
    - Critical fact preservation
    - Metrics-driven optimization
    """

    def __init__(
        self,
        config: CompactionConfig,
        effective_config: EffectiveConfig | None = None
    ):
        """Initialize compaction service.

        Args:
            config: Compaction configuration
            effective_config: Application configuration for model access
        """
        self.config = config
        self.effective_config = effective_config
        self.analyzer = ContentAnalyzer()
        self.compressor = LLMCompressor(
            effective_config=effective_config,
            model_name=config.compression_model_name,
            timeout_seconds=config.compression_timeout_seconds
        )
        self.priority = PriorityClassifier(
            min_recent_messages=config.min_recent_messages
        )
        self.metrics = CompactionMetrics(collect_metrics=config.collect_metrics)
        self.token_budget = TokenBudgetService()

    def compact(
        self,
        messages: list[BaseMessage],
        max_tokens: int,
        context: LeadAgentContext | None = None
    ) -> list[BaseMessage]:
        """Main compaction algorithm.

        Args:
            messages: List of messages to compact
            max_tokens: Maximum tokens for compacted context
            context: Runtime context (optional)

        Returns:
            Compacted list of messages
        """
        start_time = time.time()

        # Count original tokens
        original_tokens = self.token_budget.count_messages(messages)
        original_count = len(messages)

        logger.info(
            f"Starting compaction: {original_count} messages, "
            f"{original_tokens} tokens (max: {max_tokens})"
        )

        # Step 1: Classify messages by priority
        classified = self.priority.classify_messages(messages)

        logger.debug(
            f"Classified: {len(classified.high_priority)} high, "
            f"{len(classified.medium_priority)} medium, "
            f"{len(classified.low_priority)} low priority"
        )

        # Step 2: Always keep recent messages (last N messages)
        recent_messages = messages[-self.config.min_recent_messages:]

        # Step 3: Identify compactable content (exclude recent messages)
        compactable_indices = set(range(len(messages) - self.config.min_recent_messages))
        compactable = [
            msg for i, msg in enumerate(messages)
            if i in compactable_indices and msg not in classified.high_priority
        ]

        # Step 4: Extract critical facts from compactable content
        critical_facts = self.analyzer.extract_critical_facts(compactable)

        logger.debug(f"Extracted {len(critical_facts)} critical facts")

        # Step 5: LLM-based compression of compactable content
        try:
            compressed_summary = self.compressor.compress(
                messages=compactable,
                preserve_facts=critical_facts,
                max_summary_tokens=self.config.summary_token_budget
            )
        except Exception as e:
            logger.error(f"Compression failed: {e}")
            # Fallback: Keep more messages instead of compressing
            compressed_summary = None

        # Step 6: Assemble final message list
        if compressed_summary:
            compacted = [
                *classified.high_priority,  # System constraints, active tasks
                compressed_summary,          # Compressed historical context
                *recent_messages            # Recent conversation
            ]
        else:
            # Fallback: Keep high priority + recent messages only
            compacted = [
                *classified.high_priority,
                *recent_messages
            ]

        # Step 7: Verify token budget
        compacted_tokens = self.token_budget.count_messages(compacted)

        # If still over budget, trim medium-priority messages
        if compacted_tokens > max_tokens:
            logger.warning(
                f"Compacted context still over budget: {compacted_tokens} > {max_tokens}"
            )
            compacted = self._emergency_trim(compacted, max_tokens)
            compacted_tokens = self.token_budget.count_messages(compacted)

        # Step 8: Record metrics
        compression_time = time.time() - start_time
        self.metrics.record_compaction(
            original_count=original_count,
            compacted_count=len(compacted),
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            compression_time=compression_time,
            facts_preserved=len(critical_facts)
        )

        logger.info(
            f"Compaction complete: {len(compacted)} messages, "
            f"{compacted_tokens} tokens "
            f"({(1 - compacted_tokens/original_tokens):.1%} reduction)"
        )

        return compacted

    def _emergency_trim(
        self,
        messages: list[BaseMessage],
        max_tokens: int
    ) -> list[BaseMessage]:
        """Emergency trimming when compacted context still exceeds budget.

        Strategy: Keep first message (usually system) + last N messages
        """
        logger.warning("Applying emergency trim")

        if not messages:
            return []

        # Always keep first message (system prompt)
        first_message = messages[0]

        # Keep as many recent messages as fit in budget
        remaining_budget = max_tokens - self.token_budget.count_messages([first_message])

        trimmed = [first_message]
        for msg in reversed(messages[1:]):
            msg_tokens = self.token_budget.count_messages([msg])
            if msg_tokens <= remaining_budget:
                trimmed.insert(1, msg)  # Insert after first message
                remaining_budget -= msg_tokens
            else:
                break

        return trimmed

    def should_compact(self, messages: list[BaseMessage], max_tokens: int) -> bool:
        """Check if compaction should be triggered.

        Args:
            messages: Current message list
            max_tokens: Maximum allowed tokens

        Returns:
            True if compaction should be triggered
        """
        if not self.config.enabled:
            return False

        current_tokens = self.token_budget.count_messages(messages)
        threshold = max_tokens * self.config.trigger_threshold

        return current_tokens > threshold
