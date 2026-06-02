"""Semantic cache using embeddings for similarity matching."""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime
from typing import Any

import numpy as np

from .cache_key_generator import CacheKeyGenerator
from .contracts import CacheEntry, CacheHit, CacheStats, CacheType, CachingConfig

logger = logging.getLogger(__name__)


class SemanticCache:
    """Semantic cache using embedding similarity.

    Features:
    - Embedding-based similarity matching
    - Configurable similarity threshold
    - Handles paraphrased queries
    - LRU eviction
    """

    def __init__(self, config: CachingConfig):
        """Initialize semantic cache.

        Args:
            config: Caching configuration
        """
        self.config = config
        self.key_generator = CacheKeyGenerator()

        # Cache: embedding_key -> (embedding, entry)
        self.cache: OrderedDict[str, tuple[np.ndarray, CacheEntry]] = OrderedDict()

        # Statistics
        self.stats = CacheStats(cache_type=CacheType.SEMANTIC)

        # Embedding dimension (will be set on first entry)
        self.embedding_dim: int | None = None

    def get(
        self,
        prompt: str,
        embedding: np.ndarray | None = None,
        **kwargs: Any
    ) -> CacheHit:
        """Get cached response by semantic similarity.

        Args:
            prompt: User prompt
            embedding: Pre-computed embedding (optional)
            **kwargs: Additional parameters

        Returns:
            Cache hit result
        """
        start_time = datetime.now()

        self.stats.total_requests += 1

        # Need embedding for semantic search
        if embedding is None:
            logger.debug("No embedding provided for semantic cache lookup")
            self.stats.cache_misses += 1
            return CacheHit(
                hit=False,
                cache_type=CacheType.SEMANTIC,
                latency_ms=0.0
            )

        # Validate embedding dimension
        if self.embedding_dim is not None:
            if embedding.shape[0] != self.embedding_dim:
                logger.warning(
                    f"Embedding dimension mismatch: expected {self.embedding_dim}, "
                    f"got {embedding.shape[0]}"
                )
                self.stats.cache_misses += 1
                return CacheHit(
                    hit=False,
                    cache_type=CacheType.SEMANTIC,
                    latency_ms=0.0
                )

        # Search for similar embeddings
        best_match_key: str | None = None
        best_similarity: float = 0.0

        for cache_key, (cached_embedding, entry) in self.cache.items():
            # Skip expired entries
            if entry.is_expired():
                continue

            # Calculate cosine similarity
            similarity = self._cosine_similarity(embedding, cached_embedding)

            if similarity > best_similarity:
                best_similarity = similarity
                best_match_key = cache_key

        # Check if similarity exceeds threshold
        if best_similarity >= self.config.semantic_similarity_threshold:
            if best_match_key:
                _, entry = self.cache[best_match_key]

                # Touch entry and move to end (LRU)
                entry.touch()
                self.cache.move_to_end(best_match_key)

                self.stats.cache_hits += 1
                latency = (datetime.now() - start_time).total_seconds() * 1000

                logger.info(
                    f"Semantic cache hit: similarity={best_similarity:.3f}, "
                    f"key={best_match_key[:8]}"
                )

                return CacheHit(
                    hit=True,
                    value=entry.value,
                    cache_type=CacheType.SEMANTIC,
                    latency_ms=latency,
                    metadata={"similarity": best_similarity}
                )

        # Cache miss
        self.stats.cache_misses += 1
        latency = (datetime.now() - start_time).total_seconds() * 1000

        logger.debug(
            f"Semantic cache miss: best_similarity={best_similarity:.3f}, "
            f"threshold={self.config.semantic_similarity_threshold}"
        )

        return CacheHit(
            hit=False,
            cache_type=CacheType.SEMANTIC,
            latency_ms=latency
        )

    def put(
        self,
        prompt: str,
        embedding: np.ndarray,
        response: Any,
        **kwargs: Any
    ) -> None:
        """Store response with embedding.

        Args:
            prompt: User prompt
            embedding: Prompt embedding
            response: LLM response
            **kwargs: Additional parameters
        """
        # Set embedding dimension on first entry
        if self.embedding_dim is None:
            self.embedding_dim = embedding.shape[0]
            logger.info(f"Semantic cache initialized with embedding_dim={self.embedding_dim}")

        # Validate embedding dimension
        if embedding.shape[0] != self.embedding_dim:
            logger.warning(
                f"Embedding dimension mismatch: expected {self.embedding_dim}, "
                f"got {embedding.shape[0]}"
            )
            return

        # Generate cache key (hash of embedding)
        cache_key = self.key_generator.generate_embedding_key(embedding)

        # Calculate size
        size_bytes = embedding.nbytes + len(str(response))

        # Create entry
        entry = CacheEntry(
            cache_key=cache_key,
            value=response,
            ttl_seconds=self.config.semantic_cache_ttl_seconds,
            size_bytes=size_bytes,
            metadata={
                "prompt": prompt[:100],  # Store truncated prompt for debugging
                "embedding_dim": self.embedding_dim
            }
        )

        # Check size limits
        total_size = sum(e.size_bytes for _, e in self.cache.values())
        max_size_bytes = self.config.semantic_cache_max_size_mb * 1024 * 1024

        # Evict if needed (LRU)
        while total_size + size_bytes > max_size_bytes and self.cache:
            evicted_key, (_, evicted_entry) = self.cache.popitem(last=False)
            total_size -= evicted_entry.size_bytes
            self.stats.evictions += 1
            logger.debug(f"Evicted semantic cache entry: {evicted_key[:8]}")

        # Store entry with embedding
        self.cache[cache_key] = (embedding, entry)
        self.stats.entry_count = len(self.cache)
        self.stats.total_size_bytes = total_size + size_bytes

        logger.debug(
            f"Cached semantic entry: {cache_key[:8]} "
            f"({size_bytes} bytes, {len(self.cache)} entries)"
        )

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors.

        Args:
            a: First vector
            b: Second vector

        Returns:
            Cosine similarity (0.0 to 1.0)
        """
        # Normalize vectors
        a_norm = np.linalg.norm(a)
        b_norm = np.linalg.norm(b)

        if a_norm == 0 or b_norm == 0:
            return 0.0

        # Calculate cosine similarity
        similarity = np.dot(a, b) / (a_norm * b_norm)

        # Clamp to [0, 1] range
        return float(max(0.0, min(1.0, similarity)))

    def clear(self) -> None:
        """Clear all cache entries."""
        count = len(self.cache)
        self.cache.clear()
        self.stats.entry_count = 0
        self.stats.total_size_bytes = 0
        logger.info(f"Cleared {count} semantic cache entries")

    def get_stats(self) -> CacheStats:
        """Get cache statistics.

        Returns:
            Statistics
        """
        return self.stats

    def cleanup_expired(self) -> int:
        """Remove expired entries.

        Returns:
            Number of entries removed
        """
        keys_to_remove = []

        for key, (_, entry) in self.cache.items():
            if entry.is_expired():
                keys_to_remove.append(key)

        for key in keys_to_remove:
            _, entry = self.cache[key]
            del self.cache[key]
            self.stats.total_size_bytes -= entry.size_bytes
            self.stats.evictions += 1

        self.stats.entry_count = len(self.cache)

        if keys_to_remove:
            logger.info(f"Cleaned up {len(keys_to_remove)} expired semantic cache entries")

        return len(keys_to_remove)
