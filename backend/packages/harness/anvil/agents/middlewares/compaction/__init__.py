"""Context compaction package for managing context window size.

This package implements multi-tier context compression to prevent context overflow
and optimize token usage in long-running conversations.

Key components:
- CompactionMiddleware: Monitors context size and triggers compaction
- CompactionService: Core compaction logic with priority-based retention
- PriorityClassifier: Classifies messages into HIGH/MEDIUM/LOW priority
- LLMCompressor: Uses LLM to compress historical context
- CompactionMetrics: Tracks compaction performance

Design principles:
- Harness-first: Integrates cleanly with existing middleware chain
- Priority-based: Preserves critical information, discards redundant content
- LLM-powered: Uses model intelligence for semantic compression
- Metrics-driven: Tracks compression ratio, fact preservation, performance
"""

from __future__ import annotations

__all__ = [
    "CompactionMiddleware",
    "CompactionService",
    "CompactionConfig",
    "CompactionMetrics",
]
