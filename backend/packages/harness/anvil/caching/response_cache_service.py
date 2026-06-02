"""Response cache service for coordination."""

from __future__ import annotations

import logging
from typing import Any

from .contracts import CacheHit, CachingConfig
from .response_cache import ResponseCache

logger = logging.getLogger(__name__)


class ResponseCacheService:
    """Service for managing response caching.

    Responsibilities:
    - Coordinate cache operations
    - Handle cache lifecycle
    - Track statistics
    - Periodic cleanup
    """

    def __init__(self, config: CachingConfig):
        """Initialize response cache service.

        Args:
            config: Caching configuration
        """
        self.config = config
        self.cache = ResponseCache(config)
        self.cleanup_counter = 0

    def get_cached_response(
        self,
        prompt: str,
        model: str = "",
        temperature: float = 0.0,
        system_prompt: str = "",
        **kwargs: Any
    ) -> CacheHit:
        """Get cached response if available.

        Args:
            prompt: User prompt
            model: Model name
            temperature: Temperature setting
            system_prompt: System prompt
            **kwargs: Additional parameters

        Returns:
            Cache hit result
        """
        if not self.config.response_cache_enabled:
            return CacheHit(hit=False, cache_type="response")

        return self.cache.get(
            prompt=prompt,
            model=model,
            temperature=temperature,
            system_prompt=system_prompt,
            **kwargs
        )

    def cache_response(
        self,
        prompt: str,
        response: Any,
        model: str = "",
        temperature: float = 0.0,
        system_prompt: str = "",
        **kwargs: Any
    ) -> None:
        """Cache LLM response.

        Args:
            prompt: User prompt
            response: LLM response
            model: Model name
            temperature: Temperature setting
            system_prompt: System prompt
            **kwargs: Additional parameters
        """
        if not self.config.response_cache_enabled:
            return

        self.cache.put(
            prompt=prompt,
            response=response,
            model=model,
            temperature=temperature,
            system_prompt=system_prompt,
            **kwargs
        )

        # Periodic cleanup
        self.cleanup_counter += 1
        if self.cleanup_counter >= 100:
            self.cleanup_expired()
            self.cleanup_counter = 0

    def cleanup_expired(self) -> int:
        """Clean up expired cache entries.

        Returns:
            Number of entries removed
        """
        return self.cache.cleanup_expired()

    def clear_cache(self) -> None:
        """Clear all cache entries."""
        self.cache.clear()

    def get_statistics(self) -> dict:
        """Get cache statistics.

        Returns:
            Statistics dictionary
        """
        stats = self.cache.get_stats()

        return {
            "enabled": self.config.response_cache_enabled,
            "total_requests": stats.total_requests,
            "cache_hits": stats.cache_hits,
            "cache_misses": stats.cache_misses,
            "hit_rate": stats.hit_rate,
            "miss_rate": stats.miss_rate,
            "entry_count": stats.entry_count,
            "total_size_bytes": stats.total_size_bytes,
            "evictions": stats.evictions,
            "avg_latency_ms": stats.avg_latency_ms,
            "max_entries": self.config.response_cache_max_entries,
            "ttl_seconds": self.config.response_cache_ttl_seconds
        }
