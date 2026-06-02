"""Caching infrastructure contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CacheType(str, Enum):
    """Types of caches."""
    RESPONSE = "response"
    TOOL_RESULT = "tool_result"
    SEMANTIC = "semantic"


class CachingConfig(BaseModel):
    """Configuration for caching infrastructure."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # Response Cache
    response_cache_enabled: bool = True
    response_cache_ttl_seconds: int = 3600  # 1 hour
    response_cache_max_entries: int = 1000

    # Tool Result Cache
    tool_cache_enabled: bool = True
    tool_cache_ttl_seconds: dict[str, int] = Field(default_factory=lambda: {
        "Read": 300,
        "WebFetch": 3600,
        "Bash": 600,
        "Grep": 300,
        "Glob": 300
    })
    tool_cache_max_size_mb: int = 100

    # Semantic Cache
    semantic_cache_enabled: bool = True
    semantic_similarity_threshold: float = 0.95
    semantic_cache_max_entries: int = 500

    # Cache Warming
    cache_warming_enabled: bool = True
    warming_on_session_start: bool = True
    warming_pattern_detection: bool = True


class CacheEntry(BaseModel):
    """Single cache entry."""
    model_config = ConfigDict(extra="forbid")

    cache_key: str
    value: Any
    created_at: datetime = Field(default_factory=datetime.now)
    last_accessed: datetime = Field(default_factory=datetime.now)
    access_count: int = 0
    ttl_seconds: int = 3600
    size_bytes: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if entry is expired.

        Returns:
            True if expired
        """
        age = (datetime.now() - self.created_at).total_seconds()
        return age > self.ttl_seconds

    def touch(self) -> None:
        """Update last accessed time and increment count."""
        self.last_accessed = datetime.now()
        self.access_count += 1


class CacheHit(BaseModel):
    """Cache hit result."""
    model_config = ConfigDict(extra="forbid")

    hit: bool
    value: Any = None
    cache_type: CacheType
    latency_ms: float = 0.0
    similarity_score: float | None = None  # For semantic cache


class CacheStats(BaseModel):
    """Cache statistics."""
    model_config = ConfigDict(extra="forbid")

    cache_type: CacheType
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    evictions: int = 0
    total_size_bytes: int = 0
    entry_count: int = 0
    avg_latency_ms: float = 0.0

    @property
    def hit_rate(self) -> float:
        """Calculate hit rate.

        Returns:
            Hit rate (0.0-1.0)
        """
        if self.total_requests == 0:
            return 0.0
        return self.cache_hits / self.total_requests

    @property
    def miss_rate(self) -> float:
        """Calculate miss rate.

        Returns:
            Miss rate (0.0-1.0)
        """
        return 1.0 - self.hit_rate


class WarmingPattern(BaseModel):
    """Cache warming pattern."""
    model_config = ConfigDict(extra="forbid")

    pattern_id: str
    pattern_type: str  # session_start, repeated_sequence, time_based
    queries: list[str]
    confidence: float = 0.5
    last_seen: datetime = Field(default_factory=datetime.now)
    frequency: int = 0
