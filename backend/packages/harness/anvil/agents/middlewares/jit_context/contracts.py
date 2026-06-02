"""Contracts for JIT context loading."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContextType(str, Enum):
    """Types of context that can be loaded."""
    MEMORY = "memory"
    FILE = "file"
    SKILL = "skill"
    TOOL = "tool"
    CONVERSATION = "conversation"
    PROJECT = "project"


class Priority(str, Enum):
    """Priority levels for context loading."""
    HIGH = "high"      # Load immediately, block if needed
    MEDIUM = "medium"  # Load soon, prefetch candidate
    LOW = "low"        # Load if time permits


class LoadStrategy(str, Enum):
    """Strategy for loading context."""
    IMMEDIATE = "immediate"  # Load right now
    LAZY = "lazy"           # Load when referenced
    PREFETCH = "prefetch"   # Load in background
    CACHED = "cached"       # Already loaded


class ContextRequest(BaseModel):
    """Request to load specific context."""
    model_config = ConfigDict(extra="forbid")

    context_type: ContextType
    identifier: str  # Memory ID, file path, skill name, etc.
    priority: Priority = Priority.MEDIUM
    required: bool = True  # If False, skip on error
    strategy: LoadStrategy = LoadStrategy.LAZY
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextResponse(BaseModel):
    """Response from context loading."""
    model_config = ConfigDict(extra="forbid")

    context_type: ContextType
    identifier: str
    content: str
    tokens: int
    cached: bool
    load_time_ms: float
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CacheEntry(BaseModel):
    """Cached context entry."""
    model_config = ConfigDict(extra="forbid")

    key: str  # Cache key (type:identifier)
    content: str
    tokens: int
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    size_bytes: int
    ttl_seconds: int


class PrefetchPrediction(BaseModel):
    """Prediction for prefetching context."""
    model_config = ConfigDict(extra="forbid")

    context_type: ContextType
    identifier: str
    confidence: float  # 0.0-1.0
    reason: str  # Why this prediction was made
    priority: Priority = Priority.LOW


class JITContextConfig(BaseModel):
    """Configuration for JIT context loading."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # Lazy loading toggles
    lazy_memory: bool = True
    lazy_files: bool = True
    lazy_skills: bool = True
    lazy_tools: bool = True
    lazy_conversation: bool = False  # Keep recent conversation loaded

    # Prefetching
    prefetch_enabled: bool = True
    prefetch_threshold: float = 0.7  # Confidence threshold
    prefetch_max_items: int = 5

    # Caching
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300  # 5 minutes
    max_cache_size_mb: int = 50
    cache_strategy: str = "lru"  # lru, lfu, fifo

    # Performance
    max_load_time_ms: float = 500.0
    parallel_loading: bool = True
    max_parallel_loads: int = 3

    # Metrics
    collect_metrics: bool = True


class JITContextMetrics(BaseModel):
    """Metrics for JIT context loading."""
    model_config = ConfigDict(extra="forbid")

    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    prefetch_hits: int = 0
    prefetch_misses: int = 0
    total_load_time_ms: float = 0.0
    total_tokens_loaded: int = 0
    total_tokens_saved: int = 0  # Tokens saved by lazy loading

    @property
    def cache_hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0

    @property
    def prefetch_accuracy(self) -> float:
        """Calculate prefetch prediction accuracy."""
        total = self.prefetch_hits + self.prefetch_misses
        return self.prefetch_hits / total if total > 0 else 0.0

    @property
    def avg_load_time_ms(self) -> float:
        """Calculate average load time."""
        return self.total_load_time_ms / self.total_requests if self.total_requests > 0 else 0.0

    @property
    def token_reduction_rate(self) -> float:
        """Calculate token reduction rate."""
        total = self.total_tokens_loaded + self.total_tokens_saved
        return self.total_tokens_saved / total if total > 0 else 0.0
