"""JIT context loading package for on-demand context retrieval.

This package implements Just-In-Time context loading to minimize upfront token usage
while maintaining task effectiveness. Context is loaded progressively as needed.

Key components:
- ContextLoader: Lazy loading logic for different context types
- ContextCache: Caching layer with TTL and eviction
- ContextPredictor: Prefetch prediction based on conversation flow
- JITContextService: Core service coordinating loading, caching, prefetching
- JITContextMetrics: Performance tracking and optimization

Design principles:
- Lazy loading: Load only when referenced or needed
- Progressive disclosure: Start minimal, expand as needed
- Smart prefetching: Predict and preload likely needs
- Harness-first: Clean integration with existing services
"""

from __future__ import annotations

from .contracts import (
    CacheEntry,
    ContextRequest,
    ContextResponse,
    ContextType,
    JITContextConfig,
    JITContextMetrics,
    LoadStrategy,
    PrefetchPrediction,
    Priority,
)

__all__ = [
    "ContextRequest",
    "ContextResponse",
    "ContextType",
    "Priority",
    "LoadStrategy",
    "CacheEntry",
    "PrefetchPrediction",
    "JITContextConfig",
    "JITContextMetrics",
]
