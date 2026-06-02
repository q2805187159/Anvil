"""Context metrics package for comprehensive tracking and monitoring.

This package implements tracking of context usage, token consumption, and
optimization effectiveness to provide insights for data-driven decisions.

Key components:
- TokenTracker: Token usage tracking
- ContextTracker: Context composition tracking
- PerformanceTracker: Timing and resource tracking
- MetricsAggregator: Aggregation and reporting
- ContextMetricsService: Core coordination service

Design principles:
- Comprehensive tracking: All context operations
- Low overhead: <5ms per turn
- Actionable insights: Clear visualization and recommendations
- Harness-first: Clean integration
"""

from __future__ import annotations

from .contracts import (
    ContextMetricsConfig,
    TurnMetrics,
    SessionMetrics,
    MetricsSnapshot,
)

__all__ = [
    "ContextMetricsConfig",
    "TurnMetrics",
    "SessionMetrics",
    "MetricsSnapshot",
]
