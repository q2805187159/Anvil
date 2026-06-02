"""Metrics collection for context compaction."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .contracts import CompactionEvent

logger = logging.getLogger(__name__)


class CompactionMetrics:
    """Track compaction performance and effectiveness.

    Metrics tracked:
    - Compression ratio (tokens saved)
    - Message reduction (messages removed)
    - Facts preserved
    - Compression time
    - Frequency of compaction events
    """

    def __init__(self, collect_metrics: bool = True):
        """Initialize metrics collector.

        Args:
            collect_metrics: Enable metrics collection
        """
        self.collect_metrics = collect_metrics
        self.events: list[CompactionEvent] = []

    def record_compaction(
        self,
        original_count: int,
        compacted_count: int,
        original_tokens: int,
        compacted_tokens: int,
        compression_time: float,
        facts_preserved: int
    ) -> CompactionEvent:
        """Record a compaction event.

        Args:
            original_count: Original message count
            compacted_count: Compacted message count
            original_tokens: Original token count
            compacted_tokens: Compacted token count
            compression_time: Time taken for compression (seconds)
            facts_preserved: Number of critical facts preserved

        Returns:
            CompactionEvent with calculated metrics
        """
        if not self.collect_metrics:
            return self._create_event(
                original_count, compacted_count,
                original_tokens, compacted_tokens,
                compression_time, facts_preserved
            )

        # Calculate metrics
        compression_ratio = 1.0 - (compacted_tokens / max(original_tokens, 1))
        message_reduction = 1.0 - (compacted_count / max(original_count, 1))

        event = CompactionEvent(
            timestamp=datetime.now(),
            original_message_count=original_count,
            compacted_message_count=compacted_count,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            compression_ratio=compression_ratio,
            message_reduction=message_reduction,
            compression_time_seconds=compression_time,
            facts_preserved=facts_preserved
        )

        # Store event
        self.events.append(event)

        # Log metrics
        logger.info(
            f"Context compaction: {compression_ratio:.1%} token reduction "
            f"({event.tokens_saved} tokens saved), "
            f"{message_reduction:.1%} message reduction, "
            f"{facts_preserved} facts preserved, "
            f"{compression_time:.2f}s"
        )

        return event

    def get_summary(self) -> dict:
        """Get summary statistics across all compaction events.

        Returns:
            Dictionary with summary metrics
        """
        if not self.events:
            return {
                "total_events": 0,
                "avg_compression_ratio": 0.0,
                "avg_message_reduction": 0.0,
                "total_tokens_saved": 0,
                "avg_compression_time": 0.0,
                "total_facts_preserved": 0
            }

        return {
            "total_events": len(self.events),
            "avg_compression_ratio": sum(e.compression_ratio for e in self.events) / len(self.events),
            "avg_message_reduction": sum(e.message_reduction for e in self.events) / len(self.events),
            "total_tokens_saved": sum(e.tokens_saved for e in self.events),
            "avg_compression_time": sum(e.compression_time_seconds for e in self.events) / len(self.events),
            "total_facts_preserved": sum(e.facts_preserved for e in self.events)
        }

    def _create_event(
        self,
        original_count: int,
        compacted_count: int,
        original_tokens: int,
        compacted_tokens: int,
        compression_time: float,
        facts_preserved: int
    ) -> CompactionEvent:
        """Create event without storing (when metrics disabled)."""
        compression_ratio = 1.0 - (compacted_tokens / max(original_tokens, 1))
        message_reduction = 1.0 - (compacted_count / max(original_count, 1))

        return CompactionEvent(
            timestamp=datetime.now(),
            original_message_count=original_count,
            compacted_message_count=compacted_count,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            compression_ratio=compression_ratio,
            message_reduction=message_reduction,
            compression_time_seconds=compression_time,
            facts_preserved=facts_preserved
        )
