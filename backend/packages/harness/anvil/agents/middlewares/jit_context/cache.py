"""Context cache with TTL and eviction strategies."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .contracts import CacheEntry

if TYPE_CHECKING:
    from .contracts import JITContextConfig

logger = logging.getLogger(__name__)


class ContextCache:
    """LRU cache for loaded context with TTL and size limits.

    Features:
    - LRU eviction strategy
    - TTL-based expiration
    - Size-based eviction
    - Access tracking
    - Hit/miss metrics
    """

    def __init__(self, config: JITContextConfig):
        """Initialize context cache.

        Args:
            config: JIT context configuration
        """
        self.config = config
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.max_size_bytes = config.max_cache_size_mb * 1024 * 1024
        self.current_size_bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> str | None:
        """Get cached content by key.

        Args:
            key: Cache key (format: "type:identifier")

        Returns:
            Cached content if found and not expired, None otherwise
        """
        if not self.config.cache_enabled:
            return None

        entry = self.cache.get(key)
        if entry is None:
            self.misses += 1
            return None

        # Check TTL expiration
        age = (datetime.now() - entry.created_at).total_seconds()
        if age > entry.ttl_seconds:
            logger.debug(f"Cache entry expired: {key} (age: {age:.1f}s)")
            self._remove(key)
            self.misses += 1
            return None

        # Update access tracking
        entry.last_accessed = datetime.now()
        entry.access_count += 1

        # Move to end (LRU)
        self.cache.move_to_end(key)

        self.hits += 1
        logger.debug(f"Cache hit: {key} (age: {age:.1f}s, accesses: {entry.access_count})")
        return entry.content

    def put(self, key: str, content: str, tokens: int, ttl_seconds: int | None = None) -> None:
        """Put content into cache.

        Args:
            key: Cache key (format: "type:identifier")
            content: Content to cache
            tokens: Token count
            ttl_seconds: TTL override (None = use config default)
        """
        if not self.config.cache_enabled:
            return

        size_bytes = len(content.encode('utf-8'))

        # Check if adding this would exceed max size
        if size_bytes > self.max_size_bytes:
            logger.warning(f"Content too large for cache: {key} ({size_bytes} bytes)")
            return

        # Evict old entries if needed
        while self.current_size_bytes + size_bytes > self.max_size_bytes and self.cache:
            self._evict_lru()

        # Remove existing entry if present
        if key in self.cache:
            self._remove(key)

        # Create new entry
        entry = CacheEntry(
            key=key,
            content=content,
            tokens=tokens,
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            access_count=0,
            size_bytes=size_bytes,
            ttl_seconds=ttl_seconds or self.config.cache_ttl_seconds
        )

        self.cache[key] = entry
        self.current_size_bytes += size_bytes

        logger.debug(
            f"Cache put: {key} ({size_bytes} bytes, {tokens} tokens, "
            f"cache size: {self.current_size_bytes}/{self.max_size_bytes})"
        )

    def _remove(self, key: str) -> None:
        """Remove entry from cache.

        Args:
            key: Cache key to remove
        """
        entry = self.cache.pop(key, None)
        if entry:
            self.current_size_bytes -= entry.size_bytes

    def _evict_lru(self) -> None:
        """Evict least recently used entry."""
        if not self.cache:
            return

        # OrderedDict maintains insertion order, first item is LRU
        key, entry = self.cache.popitem(last=False)
        self.current_size_bytes -= entry.size_bytes
        logger.debug(f"Evicted LRU entry: {key} ({entry.size_bytes} bytes)")

    def clear(self) -> None:
        """Clear all cached entries."""
        self.cache.clear()
        self.current_size_bytes = 0
        logger.info("Cache cleared")

    def cleanup_expired(self) -> int:
        """Remove expired entries.

        Returns:
            Number of entries removed
        """
        now = datetime.now()
        expired_keys = []

        for key, entry in self.cache.items():
            age = (now - entry.created_at).total_seconds()
            if age > entry.ttl_seconds:
                expired_keys.append(key)

        for key in expired_keys:
            self._remove(key)

        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")

        return len(expired_keys)

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        total_requests = self.hits + self.misses
        hit_rate = self.hits / total_requests if total_requests > 0 else 0.0

        return {
            "enabled": self.config.cache_enabled,
            "entries": len(self.cache),
            "size_bytes": self.current_size_bytes,
            "size_mb": self.current_size_bytes / (1024 * 1024),
            "max_size_mb": self.config.max_cache_size_mb,
            "utilization": self.current_size_bytes / self.max_size_bytes if self.max_size_bytes > 0 else 0.0,
            "hits": self.hits,
            "misses": self.misses,
            "total_requests": total_requests,
            "hit_rate": hit_rate,
        }
