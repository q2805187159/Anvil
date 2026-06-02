"""Response cache for LLM responses."""

from __future__ import annotations

import logging
import sys
from collections import OrderedDict
from datetime import datetime
from typing import Any

from .cache_key_generator import CacheKeyGenerator
from .contracts import CacheEntry, CacheHit, CacheStats, CacheType, CachingConfig

logger = logging.getLogger(__name__)


class ResponseCache:
    """LRU cache for LLM responses.

    Features:
    - Exact match caching
    - TTL-based expiration
    - LRU eviction
    - Hit rate tracking
    """

    def __init__(self, config: CachingConfig):
        """Initialize response cache.

        Args:
            config: Caching configuration
        """
        self.config = config
        self.key_generator = CacheKeyGenerator()

        # LRU cache using OrderedDict
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()

        # Statistics
        self.stats = CacheStats(cache_type=CacheType.RESPONSE)

    def get(
        self,
        prompt: str,
        model: str = "",
        temperature: float = 0.0,
        system_prompt: str = "",
        **kwargs: Any
    ) -> CacheHit:
        """Get cached response.

        Args:
            prompt: User prompt
            model: Model name
            temperature: Temperature setting
            system_prompt: System prompt
            **kwargs: Additional parameters

        Returns:
            Cache hit result
        """
        start_time = datetime.now()

        # Generate cache key
        cache_key = self.key_generator.generate_response_key(
            prompt, model, temperature, system_prompt, **kwargs
        )

        # Update stats
        self.stats.total_requests += 1

        # Check cache
        if cache_key in self.cache:
            entry = self.cache[cache_key]

            # Check expiration
            if entry.is_expired():
                logger.debug(f"Cache entry expired: {cache_key[:8]}")
                del self.cache[cache_key]
                self.stats.cache_misses += 1
                self.stats.evictions += 1

                latency = (datetime.now() - start_time).total_seconds() * 1000
                return CacheHit(
                    hit=False,
                    cache_type=CacheType.RESPONSE,
                    latency_ms=latency
                )

            # Cache hit
            entry.touch()
            self.cache.move_to_end(cache_key)  # Mark as recently used

            self.stats.cache_hits += 1
            latency = (datetime.now() - start_time).total_seconds() * 1000

            logger.debug(f"Cache hit: {cache_key[:8]} (accessed {entry.access_count} times)")

            return CacheHit(
                hit=True,
                value=entry.value,
                cache_type=CacheType.RESPONSE,
                latency_ms=latency
            )

        # Cache miss
        self.stats.cache_misses += 1
        latency = (datetime.now() - start_time).total_seconds() * 1000

        logger.debug(f"Cache miss: {cache_key[:8]}")

        return CacheHit(
            hit=False,
            cache_type=CacheType.RESPONSE,
            latency_ms=latency
        )

    def put(
        self,
        prompt: str,
        response: Any,
        model: str = "",
        temperature: float = 0.0,
        system_prompt: str = "",
        **kwargs: Any
    ) -> None:
        """Store response in cache.

        Args:
            prompt: User prompt
            response: LLM response
            model: Model name
            temperature: Temperature setting
            system_prompt: System prompt
            **kwargs: Additional parameters
        """
        # Generate cache key
        cache_key = self.key_generator.generate_response_key(
            prompt, model, temperature, system_prompt, **kwargs
        )

        # Calculate size
        size_bytes = sys.getsizeof(response)

        # Create entry
        entry = CacheEntry(
            cache_key=cache_key,
            value=response,
            ttl_seconds=self.config.response_cache_ttl_seconds,
            size_bytes=size_bytes,
            metadata={
                "model": model,
                "temperature": temperature
            }
        )

        # Check if we need to evict
        if len(self.cache) >= self.config.response_cache_max_entries:
            # Evict least recently used
            evicted_key, evicted_entry = self.cache.popitem(last=False)
            self.stats.evictions += 1
            self.stats.total_size_bytes -= evicted_entry.size_bytes
            logger.debug(f"Evicted LRU entry: {evicted_key[:8]}")

        # Store entry
        self.cache[cache_key] = entry
        self.stats.entry_count = len(self.cache)
        self.stats.total_size_bytes += size_bytes

        logger.debug(f"Cached response: {cache_key[:8]} ({size_bytes} bytes)")

    def invalidate(self, cache_key: str) -> bool:
        """Invalidate specific cache entry.

        Args:
            cache_key: Cache key to invalidate

        Returns:
            True if entry was removed
        """
        if cache_key in self.cache:
            entry = self.cache[cache_key]
            del self.cache[cache_key]
            self.stats.entry_count = len(self.cache)
            self.stats.total_size_bytes -= entry.size_bytes
            logger.debug(f"Invalidated cache entry: {cache_key[:8]}")
            return True
        return False

    def clear(self) -> None:
        """Clear all cache entries."""
        count = len(self.cache)
        self.cache.clear()
        self.stats.entry_count = 0
        self.stats.total_size_bytes = 0
        logger.info(f"Cleared {count} cache entries")

    def cleanup_expired(self) -> int:
        """Remove expired entries.

        Returns:
            Number of entries removed
        """
        expired_keys = [
            key for key, entry in self.cache.items()
            if entry.is_expired()
        ]

        for key in expired_keys:
            entry = self.cache[key]
            del self.cache[key]
            self.stats.total_size_bytes -= entry.size_bytes
            self.stats.evictions += 1

        self.stats.entry_count = len(self.cache)

        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired entries")

        return len(expired_keys)

    def get_stats(self) -> CacheStats:
        """Get cache statistics.

        Returns:
            Cache statistics
        """
        # Update average latency (simplified)
        if self.stats.total_requests > 0:
            # Estimate: hits are fast (~1ms), misses are slower (~5ms)
            total_latency = (self.stats.cache_hits * 1.0) + (self.stats.cache_misses * 5.0)
            self.stats.avg_latency_ms = total_latency / self.stats.total_requests

        return self.stats

    def get_entry_info(self, cache_key: str) -> dict | None:
        """Get information about cache entry.

        Args:
            cache_key: Cache key

        Returns:
            Entry information or None
        """
        if cache_key not in self.cache:
            return None

        entry = self.cache[cache_key]
        return {
            "cache_key": cache_key,
            "created_at": entry.created_at.isoformat(),
            "last_accessed": entry.last_accessed.isoformat(),
            "access_count": entry.access_count,
            "size_bytes": entry.size_bytes,
            "ttl_seconds": entry.ttl_seconds,
            "is_expired": entry.is_expired(),
            "metadata": entry.metadata
        }
