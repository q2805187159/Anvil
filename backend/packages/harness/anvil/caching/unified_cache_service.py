"""Unified caching service coordinating all cache types."""

from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np

from .cache_warmer import CacheWarmer
from .contracts import CacheHit, CacheStats, CacheType, CachingConfig
from .response_cache import ResponseCache
from .semantic_cache import SemanticCache
from .tool_result_cache import ToolResultCache

logger = logging.getLogger(__name__)


class UnifiedCacheService:
    """Unified service coordinating all cache types.

    Features:
    - Multi-layer caching (response, tool, semantic)
    - Automatic cache selection
    - Predictive warming
    - Unified statistics
    """

    def __init__(self, config: CachingConfig):
        """Initialize unified cache service.

        Args:
            config: Caching configuration
        """
        self.config = config

        # Initialize cache layers
        self.response_cache = ResponseCache(config)
        self.tool_cache = ToolResultCache(config)
        self.semantic_cache = SemanticCache(config)
        self.warmer = CacheWarmer(config)

        # Request counter for periodic cleanup
        self.request_count = 0

    def get_response(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
        embedding: np.ndarray | None = None,
        **kwargs: Any
    ) -> CacheHit:
        """Get cached response with multi-layer lookup.

        Lookup order:
        1. Exact match (response cache)
        2. Semantic match (if embedding provided)

        Args:
            prompt: User prompt
            context: Conversation context
            embedding: Optional prompt embedding
            **kwargs: Additional parameters

        Returns:
            Cache hit result
        """
        # Record access for warming
        self.warmer.record_access("prompt", prompt[:50], {"full_prompt": prompt})

        # Try exact match first
        hit = self.response_cache.get(prompt, context, **kwargs)
        if hit.hit:
            logger.debug(f"Response cache hit: {prompt[:50]}")
            return hit

        # Try semantic match if embedding provided
        if embedding is not None and self.config.enable_semantic_cache:
            hit = self.semantic_cache.get(prompt, embedding, **kwargs)
            if hit.hit:
                logger.debug(f"Semantic cache hit: {prompt[:50]}")
                return hit

        return CacheHit(hit=False, cache_type=CacheType.RESPONSE, latency_ms=0.0)

    def put_response(
        self,
        prompt: str,
        response: Any,
        context: dict[str, Any] | None = None,
        embedding: np.ndarray | None = None,
        **kwargs: Any
    ) -> None:
        """Store response in appropriate caches.

        Args:
            prompt: User prompt
            response: LLM response
            context: Conversation context
            embedding: Optional prompt embedding
            **kwargs: Additional parameters
        """
        # Store in response cache (exact match)
        self.response_cache.put(prompt, response, context, **kwargs)

        # Store in semantic cache if embedding provided
        if embedding is not None and self.config.enable_semantic_cache:
            self.semantic_cache.put(prompt, embedding, response, **kwargs)

        # Periodic cleanup
        self._periodic_cleanup()

    def get_tool_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        **kwargs: Any
    ) -> CacheHit:
        """Get cached tool result.

        Args:
            tool_name: Name of tool
            args: Tool arguments
            **kwargs: Additional parameters

        Returns:
            Cache hit result
        """
        # Record access for warming
        self.warmer.record_access("tool", tool_name, {"args": args})

        return self.tool_cache.get(tool_name, args, **kwargs)

    def put_tool_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        **kwargs: Any
    ) -> None:
        """Store tool result in cache.

        Args:
            tool_name: Name of tool
            args: Tool arguments
            result: Tool result
            **kwargs: Additional parameters
        """
        self.tool_cache.put(tool_name, args, result, **kwargs)

        # Periodic cleanup
        self._periodic_cleanup()

    def invalidate_file(self, file_path: str) -> int:
        """Invalidate all caches related to a file.

        Args:
            file_path: Path to file

        Returns:
            Total number of entries invalidated
        """
        count = 0

        # Invalidate tool cache
        count += self.tool_cache.invalidate_file(file_path)

        # Could also invalidate response cache if it tracks files
        # For now, tool cache is sufficient

        if count > 0:
            logger.info(f"Invalidated {count} cache entries for file: {file_path}")

        return count

    def warm_cache(
        self,
        executor: Callable[[dict[str, Any]], Any] | None = None
    ) -> int:
        """Warm cache with predicted entries.

        Args:
            executor: Optional function to execute warming (e.g., call LLM)

        Returns:
            Number of entries warmed
        """
        if not self.warmer.should_warm():
            return 0

        self.warmer.start_warming()

        try:
            candidates = self.warmer.get_warming_candidates(
                limit=self.config.cache_warming_max_entries
            )

            warmed_count = 0

            for candidate in candidates:
                # If executor provided, actually warm the cache
                if executor:
                    try:
                        result = executor(candidate)
                        if result:
                            warmed_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to warm cache entry: {e}")
                else:
                    # Just count candidates without executing
                    warmed_count += 1

            self.warmer.finish_warming(warmed_count)
            return warmed_count

        except Exception as e:
            logger.error(f"Cache warming failed: {e}")
            self.warmer.finish_warming(0)
            return 0

    def get_all_stats(self) -> dict[str, Any]:
        """Get statistics from all cache layers.

        Returns:
            Unified statistics
        """
        return {
            "response_cache": self.response_cache.get_stats().model_dump(),
            "tool_cache": {
                tool_name: stats.model_dump()
                for tool_name, stats in self.tool_cache.get_stats().items()
            },
            "semantic_cache": self.semantic_cache.get_stats().model_dump(),
            "warmer": self.warmer.get_stats(),
            "total_requests": self.request_count
        }

    def clear_all(self) -> None:
        """Clear all caches."""
        self.response_cache.clear()
        self.tool_cache.clear()
        self.semantic_cache.clear()
        self.warmer.clear_patterns()
        logger.info("Cleared all caches")

    def _periodic_cleanup(self) -> None:
        """Perform periodic cleanup of expired entries."""
        self.request_count += 1

        # Cleanup every N requests
        if self.request_count % self.config.cleanup_interval_requests == 0:
            logger.debug("Running periodic cache cleanup")

            # Cleanup response cache
            self.response_cache.cleanup_expired()

            # Cleanup semantic cache
            self.semantic_cache.cleanup_expired()

            # Check if warming should run
            if self.warmer.should_warm():
                logger.info("Cache warming triggered by periodic check")
                # Note: actual warming requires executor, just log for now
