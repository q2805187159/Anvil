"""Cache warming service for predictive pre-loading."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from .contracts import CachingConfig

logger = logging.getLogger(__name__)


class CacheWarmer:
    """Predictive cache warming based on usage patterns.

    Features:
    - Pattern detection from access logs
    - Predictive pre-loading
    - Time-based warming (morning, after deploy)
    - Frequency-based prioritization
    """

    def __init__(self, config: CachingConfig):
        """Initialize cache warmer.

        Args:
            config: Caching configuration
        """
        self.config = config

        # Access pattern tracking
        self.access_log: list[tuple[datetime, str, dict[str, Any]]] = []

        # Pattern statistics
        self.prompt_frequency: Counter[str] = Counter()
        self.tool_frequency: Counter[str] = Counter()
        self.sequence_patterns: list[list[str]] = []

        # Warming state
        self.last_warm_time: datetime | None = None
        self.warming_in_progress = False

    def record_access(
        self,
        access_type: str,
        key: str,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """Record cache access for pattern learning.

        Args:
            access_type: Type of access (prompt, tool, etc.)
            key: Cache key or identifier
            metadata: Additional metadata
        """
        timestamp = datetime.now()
        self.access_log.append((timestamp, access_type, metadata or {}))

        # Update frequency counters
        if access_type == "prompt":
            self.prompt_frequency[key] += 1
        elif access_type == "tool":
            self.tool_frequency[key] += 1

        # Trim old logs (keep last 24 hours)
        cutoff = timestamp - timedelta(hours=24)
        self.access_log = [
            (ts, typ, meta)
            for ts, typ, meta in self.access_log
            if ts > cutoff
        ]

    def should_warm(self) -> bool:
        """Check if cache warming should be triggered.

        Returns:
            True if warming should run
        """
        if self.warming_in_progress:
            return False

        # Check if enough time has passed since last warming
        if self.last_warm_time:
            elapsed = datetime.now() - self.last_warm_time
            if elapsed.total_seconds() < self.config.cache_warming_interval_seconds:
                return False

        # Check if we have enough data
        if len(self.access_log) < 10:
            return False

        return True

    def get_warming_candidates(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get candidates for cache warming.

        Args:
            limit: Maximum number of candidates

        Returns:
            List of warming candidates with metadata
        """
        candidates: list[dict[str, Any]] = []

        # 1. Most frequent prompts
        for prompt_key, count in self.prompt_frequency.most_common(limit // 2):
            candidates.append({
                "type": "prompt",
                "key": prompt_key,
                "priority": count,
                "reason": f"frequent_access_{count}x"
            })

        # 2. Most frequent tools
        for tool_key, count in self.tool_frequency.most_common(limit // 4):
            candidates.append({
                "type": "tool",
                "key": tool_key,
                "priority": count,
                "reason": f"frequent_tool_{count}x"
            })

        # 3. Recent patterns (last hour)
        recent_cutoff = datetime.now() - timedelta(hours=1)
        recent_accesses = [
            (ts, typ, meta)
            for ts, typ, meta in self.access_log
            if ts > recent_cutoff
        ]

        recent_keys = [meta.get("key") for _, _, meta in recent_accesses if "key" in meta]
        recent_counter = Counter(recent_keys)

        for key, count in recent_counter.most_common(limit // 4):
            if key:
                candidates.append({
                    "type": "recent",
                    "key": key,
                    "priority": count * 2,  # Boost recent items
                    "reason": f"recent_pattern_{count}x"
                })

        # Sort by priority and limit
        candidates.sort(key=lambda x: x["priority"], reverse=True)
        return candidates[:limit]

    def detect_sequences(self) -> list[list[str]]:
        """Detect common access sequences.

        Returns:
            List of access sequences
        """
        sequences: list[list[str]] = []

        # Look for sequences of 3+ accesses
        window_size = 3
        for i in range(len(self.access_log) - window_size + 1):
            window = self.access_log[i:i + window_size]

            # Extract keys
            keys = []
            for _, _, meta in window:
                if "key" in meta:
                    keys.append(meta["key"])

            if len(keys) == window_size:
                sequences.append(keys)

        # Find most common sequences
        sequence_counter = Counter(tuple(seq) for seq in sequences)
        common_sequences = [
            list(seq)
            for seq, count in sequence_counter.most_common(10)
            if count >= 2  # Must occur at least twice
        ]

        return common_sequences

    def start_warming(self) -> None:
        """Mark warming as started."""
        self.warming_in_progress = True
        logger.info("Cache warming started")

    def finish_warming(self, warmed_count: int) -> None:
        """Mark warming as finished.

        Args:
            warmed_count: Number of entries warmed
        """
        self.warming_in_progress = False
        self.last_warm_time = datetime.now()
        logger.info(f"Cache warming finished: {warmed_count} entries warmed")

    def get_stats(self) -> dict[str, Any]:
        """Get warming statistics.

        Returns:
            Statistics dictionary
        """
        return {
            "access_log_size": len(self.access_log),
            "unique_prompts": len(self.prompt_frequency),
            "unique_tools": len(self.tool_frequency),
            "last_warm_time": self.last_warm_time.isoformat() if self.last_warm_time else None,
            "warming_in_progress": self.warming_in_progress,
            "top_prompts": dict(self.prompt_frequency.most_common(5)),
            "top_tools": dict(self.tool_frequency.most_common(5))
        }

    def clear_patterns(self) -> None:
        """Clear learned patterns."""
        self.access_log.clear()
        self.prompt_frequency.clear()
        self.tool_frequency.clear()
        self.sequence_patterns.clear()
        logger.info("Cleared cache warming patterns")
