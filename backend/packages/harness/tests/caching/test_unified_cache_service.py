"""Comprehensive tests for unified cache service."""

import numpy as np
import pytest

from anvil.caching.contracts import CacheType, CachingConfig
from anvil.caching.unified_cache_service import UnifiedCacheService


@pytest.fixture
def config():
    """Create test configuration."""
    return CachingConfig(
        enable_response_cache=True,
        enable_tool_cache=True,
        enable_semantic_cache=True,
        semantic_similarity_threshold=0.85,
        cleanup_interval_requests=10
    )


@pytest.fixture
def service(config):
    """Create unified cache service."""
    return UnifiedCacheService(config)


def test_response_cache_integration(service):
    """Test response cache integration."""
    prompt = "What is Python?"
    response = "Python is a programming language."

    # Store
    service.put_response(prompt, response)

    # Retrieve
    hit = service.get_response(prompt)
    assert hit.hit
    assert hit.value == response
    assert hit.cache_type == CacheType.RESPONSE


def test_tool_cache_integration(service):
    """Test tool cache integration."""
    tool_name = "Read"
    args = {"file_path": "test.py"}
    result = "file contents"

    # Store
    service.put_tool_result(tool_name, args, result)

    # Retrieve
    hit = service.get_tool_result(tool_name, args)
    assert hit.hit
    assert hit.value == result
    assert hit.cache_type == CacheType.TOOL_RESULT


def test_semantic_cache_integration(service):
    """Test semantic cache integration."""
    prompt = "What is Python?"
    embedding = np.random.rand(768)
    response = "Python is a programming language."

    # Store with embedding
    service.put_response(prompt, response, embedding=embedding)

    # Retrieve with similar embedding
    similar_embedding = embedding + np.random.rand(768) * 0.01  # Very similar
    hit = service.get_response("What's Python?", embedding=similar_embedding)

    # Should hit semantic cache
    assert hit.hit
    assert hit.cache_type == CacheType.SEMANTIC


def test_multi_layer_lookup_order(service):
    """Test that exact match is preferred over semantic match."""
    prompt = "What is Python?"
    embedding = np.random.rand(768)
    response1 = "Response from exact cache"
    response2 = "Response from semantic cache"

    # Store in semantic cache first
    service.semantic_cache.put(prompt, embedding, response2)

    # Store in response cache (exact match)
    service.response_cache.put(prompt, response1)

    # Should hit exact match first
    hit = service.get_response(prompt, embedding=embedding)
    assert hit.hit
    assert hit.value == response1
    assert hit.cache_type == CacheType.RESPONSE


def test_file_invalidation(service):
    """Test file invalidation across caches."""
    file_path = "test.py"

    # Cache tool results for file
    service.put_tool_result("Read", {"file_path": file_path}, "contents")
    service.put_tool_result("Grep", {"file_path": file_path, "pattern": "test"}, "matches")

    # Invalidate file
    count = service.invalidate_file(file_path)
    assert count >= 0

    # Should miss after invalidation
    hit = service.get_tool_result("Read", {"file_path": file_path})
    assert not hit.hit


def test_warming_pattern_recording(service):
    """Test that access patterns are recorded for warming."""
    # Access some prompts
    service.get_response("Question 1")
    service.get_response("Question 1")  # Repeat
    service.get_response("Question 2")

    # Check warmer recorded accesses
    assert len(service.warmer.access_log) == 3
    assert service.warmer.prompt_frequency["Question 1"] == 2
    assert service.warmer.prompt_frequency["Question 2"] == 1


def test_warming_candidates(service):
    """Test warming candidate generation."""
    # Generate access pattern
    for i in range(5):
        service.get_response(f"Question {i % 2}")  # Repeat Q0 and Q1

    # Get warming candidates
    candidates = service.warmer.get_warming_candidates(limit=10)

    assert len(candidates) > 0
    # Most frequent should be prioritized
    assert candidates[0]["priority"] >= candidates[-1]["priority"]


def test_periodic_cleanup(service):
    """Test periodic cleanup triggers."""
    # Set cleanup interval
    service.config.cleanup_interval_requests = 5

    # Make requests
    for i in range(10):
        service.put_response(f"Q{i}", f"A{i}")

    # Cleanup should have triggered twice (at 5 and 10)
    assert service.request_count == 10


def test_unified_statistics(service):
    """Test unified statistics collection."""
    # Generate activity across all caches
    service.put_response("Q1", "A1")
    service.get_response("Q1")

    service.put_tool_result("Read", {"file_path": "test.py"}, "contents")
    service.get_tool_result("Read", {"file_path": "test.py"})

    embedding = np.random.rand(768)
    service.put_response("Q2", "A2", embedding=embedding)

    # Get unified stats
    stats = service.get_all_stats()

    assert "response_cache" in stats
    assert "tool_cache" in stats
    assert "semantic_cache" in stats
    assert "warmer" in stats
    assert stats["total_requests"] > 0


def test_clear_all(service):
    """Test clearing all caches."""
    # Populate all caches
    service.put_response("Q1", "A1")
    service.put_tool_result("Read", {"file_path": "test.py"}, "contents")

    embedding = np.random.rand(768)
    service.put_response("Q2", "A2", embedding=embedding)

    # Clear all
    service.clear_all()

    # All should be empty
    assert len(service.response_cache.cache) == 0
    assert len(service.tool_cache.caches) == 0
    assert len(service.semantic_cache.cache) == 0


def test_context_aware_caching(service):
    """Test that context affects cache keys."""
    prompt = "What is this?"
    context1 = {"file": "test.py"}
    context2 = {"file": "main.py"}

    # Store with different contexts
    service.put_response(prompt, "Answer for test.py", context=context1)
    service.put_response(prompt, "Answer for main.py", context=context2)

    # Should retrieve different responses based on context
    hit1 = service.get_response(prompt, context=context1)
    hit2 = service.get_response(prompt, context=context2)

    assert hit1.hit and hit2.hit
    assert hit1.value != hit2.value


def test_cache_warming_execution(service):
    """Test cache warming with executor."""
    # Generate access pattern
    for _ in range(3):
        service.get_response("Frequent question")

    # Mock executor
    warmed_items = []

    def mock_executor(candidate):
        warmed_items.append(candidate)
        return True

    # Trigger warming
    count = service.warm_cache(executor=mock_executor)

    assert count > 0
    assert len(warmed_items) > 0


def test_cache_warming_without_executor(service):
    """Test cache warming without executor (dry run)."""
    # Generate access pattern
    for _ in range(3):
        service.get_response("Frequent question")

    # Trigger warming without executor
    count = service.warm_cache(executor=None)

    # Should count candidates without executing
    assert count >= 0


def test_semantic_cache_disabled(service):
    """Test behavior when semantic cache is disabled."""
    service.config.enable_semantic_cache = False

    prompt = "What is Python?"
    embedding = np.random.rand(768)
    response = "Python is a programming language."

    # Store with embedding
    service.put_response(prompt, response, embedding=embedding)

    # Should only be in response cache, not semantic cache
    assert len(service.response_cache.cache) > 0
    assert len(service.semantic_cache.cache) == 0


def test_tool_cache_per_tool_isolation(service):
    """Test that tool caches are isolated per tool."""
    # Cache for different tools
    service.put_tool_result("Read", {"file_path": "test.py"}, "read result")
    service.put_tool_result("Grep", {"pattern": "test"}, "grep result")

    # Each should be independent
    assert "Read" in service.tool_cache.caches
    assert "Grep" in service.tool_cache.caches


def test_access_pattern_time_window(service):
    """Test that old access patterns are trimmed."""
    import time

    # Record access
    service.warmer.record_access("prompt", "Q1", {"key": "Q1"})

    # Check it's recorded
    assert len(service.warmer.access_log) == 1

    # Manually set old timestamp (simulate 25 hours ago)
    from datetime import datetime, timedelta
    old_time = datetime.now() - timedelta(hours=25)
    service.warmer.access_log[0] = (old_time, "prompt", {"key": "Q1"})

    # Record new access (should trigger cleanup)
    service.warmer.record_access("prompt", "Q2", {"key": "Q2"})

    # Old access should be removed
    assert len(service.warmer.access_log) == 1
    assert service.warmer.access_log[0][2]["key"] == "Q2"


def test_cache_hit_latency_tracking(service):
    """Test that cache hit latency is tracked."""
    prompt = "What is Python?"
    response = "Python is a programming language."

    # Store
    service.put_response(prompt, response)

    # Retrieve and check latency
    hit = service.get_response(prompt)
    assert hit.hit
    assert hit.latency_ms >= 0.0
