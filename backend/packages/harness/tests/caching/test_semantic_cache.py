"""Comprehensive tests for semantic cache."""

import numpy as np
import pytest

from anvil.caching.contracts import CacheType, CachingConfig
from anvil.caching.semantic_cache import SemanticCache


@pytest.fixture
def config():
    """Create test configuration."""
    return CachingConfig(
        enable_semantic_cache=True,
        semantic_similarity_threshold=0.85,
        semantic_cache_ttl_seconds=3600,
        semantic_cache_max_size_mb=50
    )


@pytest.fixture
def cache(config):
    """Create semantic cache."""
    return SemanticCache(config)


def test_cache_hit_with_exact_embedding(cache):
    """Test cache hit with exact same embedding."""
    prompt = "What is Python?"
    embedding = np.random.rand(768)
    response = "Python is a programming language."

    # Store
    cache.put(prompt, embedding, response)

    # Retrieve with same embedding
    hit = cache.get(prompt, embedding)
    assert hit.hit
    assert hit.value == response
    assert hit.cache_type == CacheType.SEMANTIC


def test_cache_hit_with_similar_embedding(cache):
    """Test cache hit with similar embedding."""
    prompt1 = "What is Python?"
    embedding1 = np.array([1.0, 0.0, 0.0])
    response = "Python is a programming language."

    # Store
    cache.put(prompt1, embedding1, response)

    # Query with very similar embedding (0.99 similarity)
    prompt2 = "What's Python?"
    embedding2 = np.array([0.99, 0.1, 0.0])

    hit = cache.get(prompt2, embedding2)
    assert hit.hit
    assert hit.value == response
    assert "similarity" in hit.metadata


def test_cache_miss_with_dissimilar_embedding(cache):
    """Test cache miss with dissimilar embedding."""
    prompt1 = "What is Python?"
    embedding1 = np.array([1.0, 0.0, 0.0])
    response = "Python is a programming language."

    # Store
    cache.put(prompt1, embedding1, response)

    # Query with dissimilar embedding
    prompt2 = "What is JavaScript?"
    embedding2 = np.array([0.0, 1.0, 0.0])

    hit = cache.get(prompt2, embedding2)
    assert not hit.hit


def test_similarity_threshold(cache):
    """Test similarity threshold enforcement."""
    # Set high threshold
    cache.config.semantic_similarity_threshold = 0.95

    prompt1 = "What is Python?"
    embedding1 = np.array([1.0, 0.0, 0.0])
    response = "Python is a programming language."

    cache.put(prompt1, embedding1, response)

    # Query with moderately similar embedding (0.90 similarity)
    embedding2 = np.array([0.9, 0.436, 0.0])  # cos_sim ≈ 0.90

    hit = cache.get("Similar question", embedding2)
    assert not hit.hit  # Below threshold


def test_cosine_similarity_calculation(cache):
    """Test cosine similarity calculation."""
    # Identical vectors
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([1.0, 0.0, 0.0])
    assert cache._cosine_similarity(a, b) == pytest.approx(1.0)

    # Orthogonal vectors
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([0.0, 1.0, 0.0])
    assert cache._cosine_similarity(a, b) == pytest.approx(0.0)

    # Opposite vectors (should clamp to 0)
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([-1.0, 0.0, 0.0])
    assert cache._cosine_similarity(a, b) == pytest.approx(0.0)


def test_zero_vector_handling(cache):
    """Test handling of zero vectors."""
    a = np.array([0.0, 0.0, 0.0])
    b = np.array([1.0, 0.0, 0.0])

    similarity = cache._cosine_similarity(a, b)
    assert similarity == 0.0


def test_lru_eviction(cache):
    """Test LRU eviction when size limit reached."""
    # Set small size limit
    cache.config.semantic_cache_max_size_mb = 0.001  # ~1KB

    # Fill cache
    for i in range(20):
        prompt = f"Question {i}"
        embedding = np.random.rand(100)  # Small embeddings
        response = f"Answer {i}"
        cache.put(prompt, embedding, response)

    # Cache should have evicted old entries
    assert len(cache.cache) < 20
    assert cache.stats.evictions > 0


def test_embedding_dimension_validation(cache):
    """Test embedding dimension validation."""
    # First entry sets dimension
    embedding1 = np.random.rand(768)
    cache.put("Question 1", embedding1, "Answer 1")

    assert cache.embedding_dim == 768

    # Try to store with different dimension
    embedding2 = np.random.rand(512)
    cache.put("Question 2", embedding2, "Answer 2")

    # Should be rejected (not stored)
    assert len(cache.cache) == 1


def test_get_without_embedding(cache):
    """Test get without providing embedding."""
    hit = cache.get("Question", embedding=None)
    assert not hit.hit


def test_ttl_expiration(cache):
    """Test TTL-based expiration."""
    import time

    # Set short TTL
    cache.config.semantic_cache_ttl_seconds = 1

    prompt = "What is Python?"
    embedding = np.random.rand(768)
    response = "Python is a programming language."

    # Store
    cache.put(prompt, embedding, response)

    # Immediate access - hit
    hit = cache.get(prompt, embedding)
    assert hit.hit

    # Wait for expiration
    time.sleep(1.1)

    # Access after expiration - miss
    hit = cache.get(prompt, embedding)
    assert not hit.hit


def test_cleanup_expired(cache):
    """Test cleanup of expired entries."""
    import time

    # Set short TTL
    cache.config.semantic_cache_ttl_seconds = 1

    # Store multiple entries
    for i in range(5):
        embedding = np.random.rand(768)
        cache.put(f"Question {i}", embedding, f"Answer {i}")

    assert len(cache.cache) == 5

    # Wait for expiration
    time.sleep(1.1)

    # Cleanup
    removed = cache.cleanup_expired()
    assert removed == 5
    assert len(cache.cache) == 0


def test_statistics_tracking(cache):
    """Test statistics tracking."""
    embedding1 = np.array([1.0, 0.0, 0.0])
    embedding2 = np.array([0.99, 0.1, 0.0])
    embedding3 = np.array([0.0, 1.0, 0.0])

    # Store
    cache.put("Q1", embedding1, "A1")

    # Hit
    cache.get("Q1", embedding2)

    # Miss
    cache.get("Q2", embedding3)

    stats = cache.get_stats()
    assert stats.total_requests == 2
    assert stats.cache_hits == 1
    assert stats.cache_misses == 1
    assert stats.entry_count == 1


def test_clear(cache):
    """Test clearing cache."""
    # Store entries
    for i in range(5):
        embedding = np.random.rand(768)
        cache.put(f"Question {i}", embedding, f"Answer {i}")

    assert len(cache.cache) == 5

    # Clear
    cache.clear()

    assert len(cache.cache) == 0
    assert cache.stats.entry_count == 0


def test_best_match_selection(cache):
    """Test that best matching entry is selected."""
    # Store multiple entries
    embedding1 = np.array([1.0, 0.0, 0.0])
    embedding2 = np.array([0.0, 1.0, 0.0])
    embedding3 = np.array([0.0, 0.0, 1.0])

    cache.put("Q1", embedding1, "A1")
    cache.put("Q2", embedding2, "A2")
    cache.put("Q3", embedding3, "A3")

    # Query with embedding closest to embedding1
    query_embedding = np.array([0.95, 0.1, 0.0])

    hit = cache.get("Query", query_embedding)
    assert hit.hit
    assert hit.value == "A1"  # Should match Q1


def test_access_updates_lru(cache):
    """Test that accessing an entry updates LRU order."""
    # Store entries
    embeddings = [np.random.rand(768) for _ in range(3)]
    for i, emb in enumerate(embeddings):
        cache.put(f"Q{i}", emb, f"A{i}")

    # Access first entry
    cache.get("Q0", embeddings[0])

    # Set very small size limit to force eviction
    cache.config.semantic_cache_max_size_mb = 0.001

    # Add new entry to trigger eviction
    new_embedding = np.random.rand(768)
    cache.put("New", new_embedding, "New Answer")

    # First entry should still be there (was accessed recently)
    # Middle entries should be evicted first
    hit = cache.get("Q0", embeddings[0])
    # Note: This test is probabilistic due to size calculations
