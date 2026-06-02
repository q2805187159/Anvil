"""Tests for response cache."""

import pytest
from datetime import datetime, timedelta

from anvil.caching.response_cache import ResponseCache
from anvil.caching.contracts import CachingConfig


class TestResponseCache:
    """Test suite for ResponseCache."""

    def setup_method(self):
        """Setup test fixtures."""
        self.config = CachingConfig(
            response_cache_enabled=True,
            response_cache_ttl_seconds=3600,
            response_cache_max_entries=100
        )
        self.cache = ResponseCache(self.config)

    def test_cache_miss(self):
        """Test cache miss on first request."""
        result = self.cache.get(
            prompt="What is Python?",
            model="gpt-4",
            temperature=0.0
        )

        assert result.hit is False
        assert result.value is None

    def test_cache_hit(self):
        """Test cache hit after storing."""
        prompt = "What is Python?"
        response = "Python is a programming language."

        # Store response
        self.cache.put(
            prompt=prompt,
            response=response,
            model="gpt-4",
            temperature=0.0
        )

        # Retrieve from cache
        result = self.cache.get(
            prompt=prompt,
            model="gpt-4",
            temperature=0.0
        )

        assert result.hit is True
        assert result.value == response

    def test_cache_key_normalization(self):
        """Test that similar prompts generate same key."""
        response = "Test response"

        # Store with extra whitespace
        self.cache.put(
            prompt="What   is    Python?",
            response=response,
            model="gpt-4"
        )

        # Retrieve with normalized whitespace
        result = self.cache.get(
            prompt="what is python?",
            model="gpt-4"
        )

        assert result.hit is True
        assert result.value == response

    def test_different_parameters_different_keys(self):
        """Test that different parameters create different cache keys."""
        prompt = "What is Python?"

        # Store with temperature 0.0
        self.cache.put(
            prompt=prompt,
            response="Response 1",
            model="gpt-4",
            temperature=0.0
        )

        # Store with temperature 0.7
        self.cache.put(
            prompt=prompt,
            response="Response 2",
            model="gpt-4",
            temperature=0.7
        )

        # Retrieve with temperature 0.0
        result1 = self.cache.get(prompt=prompt, model="gpt-4", temperature=0.0)
        assert result1.value == "Response 1"

        # Retrieve with temperature 0.7
        result2 = self.cache.get(prompt=prompt, model="gpt-4", temperature=0.7)
        assert result2.value == "Response 2"

    def test_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        # Fill cache to max
        for i in range(self.config.response_cache_max_entries):
            self.cache.put(
                prompt=f"Prompt {i}",
                response=f"Response {i}",
                model="gpt-4"
            )

        # Add one more (should evict oldest)
        self.cache.put(
            prompt="New prompt",
            response="New response",
            model="gpt-4"
        )

        # First entry should be evicted
        result = self.cache.get(prompt="Prompt 0", model="gpt-4")
        assert result.hit is False

        # Last entry should be present
        result = self.cache.get(prompt="New prompt", model="gpt-4")
        assert result.hit is True

    def test_ttl_expiration(self):
        """Test TTL-based expiration."""
        # Create cache with short TTL
        config = CachingConfig(response_cache_ttl_seconds=1)
        cache = ResponseCache(config)

        # Store entry
        cache.put(
            prompt="Test prompt",
            response="Test response",
            model="gpt-4"
        )

        # Should hit immediately
        result = cache.get(prompt="Test prompt", model="gpt-4")
        assert result.hit is True

        # Manually expire entry
        for entry in cache.cache.values():
            entry.created_at = datetime.now() - timedelta(seconds=2)

        # Should miss after expiration
        result = cache.get(prompt="Test prompt", model="gpt-4")
        assert result.hit is False

    def test_access_count_tracking(self):
        """Test that access count is tracked."""
        prompt = "Test prompt"

        self.cache.put(prompt=prompt, response="Response", model="gpt-4")

        # Access multiple times
        for _ in range(5):
            self.cache.get(prompt=prompt, model="gpt-4")

        # Check access count
        cache_key = self.cache.key_generator.generate_response_key(
            prompt=prompt,
            model="gpt-4",
            temperature=0.0,
            system_prompt=""
        )

        entry = self.cache.cache[cache_key]
        assert entry.access_count == 5

    def test_statistics_tracking(self):
        """Test statistics tracking."""
        # Generate some hits and misses
        self.cache.put(prompt="Cached", response="Response", model="gpt-4")

        self.cache.get(prompt="Cached", model="gpt-4")  # Hit
        self.cache.get(prompt="Not cached", model="gpt-4")  # Miss

        stats = self.cache.get_stats()

        assert stats.total_requests == 2
        assert stats.cache_hits == 1
        assert stats.cache_misses == 1
        assert stats.hit_rate == 0.5

    def test_clear_cache(self):
        """Test clearing cache."""
        # Add entries
        for i in range(10):
            self.cache.put(prompt=f"Prompt {i}", response=f"Response {i}", model="gpt-4")

        assert len(self.cache.cache) == 10

        # Clear
        self.cache.clear()

        assert len(self.cache.cache) == 0
        assert self.cache.stats.entry_count == 0

    def test_cleanup_expired(self):
        """Test cleanup of expired entries."""
        # Add entries with short TTL
        config = CachingConfig(response_cache_ttl_seconds=1)
        cache = ResponseCache(config)

        for i in range(5):
            cache.put(prompt=f"Prompt {i}", response=f"Response {i}", model="gpt-4")

        # Expire some entries
        for i, entry in enumerate(cache.cache.values()):
            if i < 3:
                entry.created_at = datetime.now() - timedelta(seconds=2)

        # Cleanup
        removed = cache.cleanup_expired()

        assert removed == 3
        assert len(cache.cache) == 2

    def test_invalidate_entry(self):
        """Test invalidating specific entry."""
        self.cache.put(prompt="Test", response="Response", model="gpt-4")

        cache_key = self.cache.key_generator.generate_response_key(
            prompt="Test",
            model="gpt-4",
            temperature=0.0,
            system_prompt=""
        )

        # Invalidate
        result = self.cache.invalidate(cache_key)
        assert result is True

        # Should miss after invalidation
        hit = self.cache.get(prompt="Test", model="gpt-4")
        assert hit.hit is False

    def test_get_entry_info(self):
        """Test getting entry information."""
        self.cache.put(prompt="Test", response="Response", model="gpt-4")

        cache_key = self.cache.key_generator.generate_response_key(
            prompt="Test",
            model="gpt-4",
            temperature=0.0,
            system_prompt=""
        )

        info = self.cache.get_entry_info(cache_key)

        assert info is not None
        assert info["cache_key"] == cache_key
        assert info["access_count"] == 0
        assert "created_at" in info
        assert "size_bytes" in info
