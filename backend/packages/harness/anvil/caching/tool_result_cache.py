"""Tool result cache for expensive operations."""

from __future__ import annotations

import logging
import os
import sys
from collections import OrderedDict
from datetime import datetime
from typing import Any

from .cache_key_generator import CacheKeyGenerator
from .contracts import CacheEntry, CacheHit, CacheStats, CacheType, CachingConfig

logger = logging.getLogger(__name__)


class ToolResultCache:
    """LRU cache for tool execution results.

    Features:
    - Per-tool caching with configurable TTL
    - File-based invalidation (mtime tracking)
    - Size limits
    - Bypass for non-cacheable operations
    """

    def __init__(self, config: CachingConfig):
        """Initialize tool result cache.

        Args:
            config: Caching configuration
        """
        self.config = config
        self.key_generator = CacheKeyGenerator()

        # Separate cache per tool type
        self.caches: dict[str, OrderedDict[str, CacheEntry]] = {}

        # Statistics per tool
        self.stats: dict[str, CacheStats] = {}

        # File modification time tracking
        self.file_mtimes: dict[str, float] = {}

    def get(
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
        start_time = datetime.now()

        # Check if tool is cacheable
        if not self._is_cacheable(tool_name, args):
            return CacheHit(
                hit=False,
                cache_type=CacheType.TOOL_RESULT,
                latency_ms=0.0
            )

        # Initialize cache for tool if needed
        if tool_name not in self.caches:
            self.caches[tool_name] = OrderedDict()
            self.stats[tool_name] = CacheStats(cache_type=CacheType.TOOL_RESULT)

        # Generate cache key
        cache_key = self.key_generator.generate_tool_key(tool_name, args, **kwargs)

        # Update stats
        self.stats[tool_name].total_requests += 1

        # Check cache
        cache = self.caches[tool_name]
        if cache_key in cache:
            entry = cache[cache_key]

            # Check expiration
            if entry.is_expired():
                logger.debug(f"Tool cache entry expired: {tool_name}/{cache_key[:8]}")
                del cache[cache_key]
                self.stats[tool_name].cache_misses += 1
                self.stats[tool_name].evictions += 1

                latency = (datetime.now() - start_time).total_seconds() * 1000
                return CacheHit(
                    hit=False,
                    cache_type=CacheType.TOOL_RESULT,
                    latency_ms=latency
                )

            # Check file invalidation for file-based tools
            if tool_name in ["Read", "Glob", "Grep"] and "file_path" in args:
                if self._is_file_modified(args["file_path"], entry):
                    logger.debug(f"File modified, invalidating cache: {args['file_path']}")
                    del cache[cache_key]
                    self.stats[tool_name].cache_misses += 1
                    self.stats[tool_name].evictions += 1

                    latency = (datetime.now() - start_time).total_seconds() * 1000
                    return CacheHit(
                        hit=False,
                        cache_type=CacheType.TOOL_RESULT,
                        latency_ms=latency
                    )

            # Cache hit
            entry.touch()
            cache.move_to_end(cache_key)

            self.stats[tool_name].cache_hits += 1
            latency = (datetime.now() - start_time).total_seconds() * 1000

            logger.debug(f"Tool cache hit: {tool_name}/{cache_key[:8]}")

            return CacheHit(
                hit=True,
                value=entry.value,
                cache_type=CacheType.TOOL_RESULT,
                latency_ms=latency
            )

        # Cache miss
        self.stats[tool_name].cache_misses += 1
        latency = (datetime.now() - start_time).total_seconds() * 1000

        logger.debug(f"Tool cache miss: {tool_name}/{cache_key[:8]}")

        return CacheHit(
            hit=False,
            cache_type=CacheType.TOOL_RESULT,
            latency_ms=latency
        )

    def put(
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
        # Check if tool is cacheable
        if not self._is_cacheable(tool_name, args):
            return

        # Initialize cache for tool if needed
        if tool_name not in self.caches:
            self.caches[tool_name] = OrderedDict()
            self.stats[tool_name] = CacheStats(cache_type=CacheType.TOOL_RESULT)

        # Generate cache key
        cache_key = self.key_generator.generate_tool_key(tool_name, args, **kwargs)

        # Calculate size
        size_bytes = sys.getsizeof(result)

        # Get TTL for this tool
        ttl = self.config.tool_cache_ttl_seconds.get(tool_name, 600)

        # Track file mtime for file-based tools
        file_mtime = None
        if tool_name in ["Read", "Glob", "Grep"] and "file_path" in args:
            file_path = args["file_path"]
            if os.path.exists(file_path):
                file_mtime = os.path.getmtime(file_path)
                self.file_mtimes[file_path] = file_mtime

        # Create entry
        entry = CacheEntry(
            cache_key=cache_key,
            value=result,
            ttl_seconds=ttl,
            size_bytes=size_bytes,
            metadata={
                "tool_name": tool_name,
                "file_mtime": file_mtime
            }
        )

        # Check size limits
        cache = self.caches[tool_name]
        total_size = sum(e.size_bytes for e in cache.values())
        max_size_bytes = self.config.tool_cache_max_size_mb * 1024 * 1024

        # Evict if needed
        while total_size + size_bytes > max_size_bytes and cache:
            evicted_key, evicted_entry = cache.popitem(last=False)
            total_size -= evicted_entry.size_bytes
            self.stats[tool_name].evictions += 1
            logger.debug(f"Evicted tool cache entry: {tool_name}/{evicted_key[:8]}")

        # Store entry
        cache[cache_key] = entry
        self.stats[tool_name].entry_count = len(cache)
        self.stats[tool_name].total_size_bytes = total_size + size_bytes

        logger.debug(f"Cached tool result: {tool_name}/{cache_key[:8]} ({size_bytes} bytes)")

    def _is_cacheable(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Check if tool operation is cacheable.

        Args:
            tool_name: Name of tool
            args: Tool arguments

        Returns:
            True if cacheable
        """
        # Non-cacheable tools
        non_cacheable = ["Write", "Edit", "Bash"]  # Write operations
        if tool_name in non_cacheable:
            return False

        # Bash is only cacheable for read-only operations
        if tool_name == "Bash":
            command = args.get("command", "")
            # Simple heuristic: check for write indicators
            write_indicators = ["rm", "mv", "cp", "mkdir", "touch", ">", ">>"]
            if any(indicator in command for indicator in write_indicators):
                return False

        return True

    def _is_file_modified(self, file_path: str, entry: CacheEntry) -> bool:
        """Check if file has been modified since caching.

        Args:
            file_path: Path to file
            entry: Cache entry

        Returns:
            True if file was modified
        """
        if not os.path.exists(file_path):
            return True

        current_mtime = os.path.getmtime(file_path)
        cached_mtime = entry.metadata.get("file_mtime")

        if cached_mtime is None:
            return False

        return current_mtime > cached_mtime

    def invalidate_file(self, file_path: str) -> int:
        """Invalidate all cache entries for a file.

        Args:
            file_path: Path to file

        Returns:
            Number of entries invalidated
        """
        count = 0

        for tool_name, cache in self.caches.items():
            keys_to_remove = []

            for key, entry in cache.items():
                # Check if entry is related to this file
                if "file_path" in entry.metadata:
                    if entry.metadata["file_path"] == file_path:
                        keys_to_remove.append(key)

            for key in keys_to_remove:
                entry = cache[key]
                del cache[key]
                self.stats[tool_name].total_size_bytes -= entry.size_bytes
                self.stats[tool_name].entry_count = len(cache)
                count += 1

        if count > 0:
            logger.info(f"Invalidated {count} cache entries for file: {file_path}")

        return count

    def clear(self, tool_name: str | None = None) -> None:
        """Clear cache entries.

        Args:
            tool_name: Specific tool to clear, or None for all
        """
        if tool_name:
            if tool_name in self.caches:
                count = len(self.caches[tool_name])
                self.caches[tool_name].clear()
                self.stats[tool_name].entry_count = 0
                self.stats[tool_name].total_size_bytes = 0
                logger.info(f"Cleared {count} cache entries for tool: {tool_name}")
        else:
            total = sum(len(cache) for cache in self.caches.values())
            self.caches.clear()
            self.stats.clear()
            logger.info(f"Cleared {total} cache entries for all tools")

    def get_stats(self, tool_name: str | None = None) -> dict[str, CacheStats] | CacheStats:
        """Get cache statistics.

        Args:
            tool_name: Specific tool, or None for all

        Returns:
            Statistics
        """
        if tool_name:
            return self.stats.get(tool_name, CacheStats(cache_type=CacheType.TOOL_RESULT))

        return self.stats
