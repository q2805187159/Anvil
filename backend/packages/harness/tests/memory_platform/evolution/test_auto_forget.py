"""Tests for auto-forget."""

import pytest
from datetime import datetime, timedelta

from anvil.memory_platform.evolution.auto_forget import AutoForget
from anvil.memory_platform.evolution.contracts import MemoryEvolutionConfig


class TestAutoForget:
    """Test suite for AutoForget."""

    def setup_method(self):
        """Setup test fixtures."""
        self.config = MemoryEvolutionConfig(
            auto_forget_enabled=True,
            auto_forget_interval_hours=24,
            default_ttl_days=30,
            contradiction_threshold=0.8,
            low_value_threshold=0.3
        )
        self.auto_forget = AutoForget(self.config)

    def test_should_run_first_time(self):
        """Test should run on first call."""
        assert self.auto_forget.should_run(hours_since_last=100) is True

    def test_should_run_after_interval(self):
        """Test should run after interval."""
        assert self.auto_forget.should_run(hours_since_last=25) is True

    def test_should_not_run_too_soon(self):
        """Test should not run too soon."""
        assert self.auto_forget.should_run(hours_since_last=12) is False

    def test_should_run_disabled(self):
        """Test should not run when disabled."""
        config = MemoryEvolutionConfig(auto_forget_enabled=False)
        auto_forget = AutoForget(config)

        assert auto_forget.should_run(hours_since_last=100) is False

    def test_identify_expired_memories(self):
        """Test identifying expired memories."""
        now = datetime.now()
        old_date = now - timedelta(days=35)

        memories = [
            {
                "memory_id": "old_1",
                "created_at": old_date.isoformat(),
                "importance": 0.5
            },
            {
                "memory_id": "recent_1",
                "created_at": now.isoformat(),
                "importance": 0.5
            },
            {
                "memory_id": "old_critical",
                "created_at": old_date.isoformat(),
                "importance": 0.9  # Critical, should not expire
            }
        ]

        to_forget = self.auto_forget.identify_expired_memories(memories, now)

        # Should only mark old_1 for deletion
        assert len(to_forget) == 1
        assert to_forget[0].memory_id == "old_1"
        assert to_forget[0].reason == "expired"

    def test_detect_contradictions(self):
        """Test detecting contradicting memories."""
        memories = [
            {
                "memory_id": "newer",
                "type": "preference",
                "description": "User prefers async code",
                "created_at": "2026-05-20T10:00:00"
            },
            {
                "memory_id": "older",
                "type": "preference",
                "description": "User does not prefer async code",
                "created_at": "2026-05-10T10:00:00"
            }
        ]

        to_forget = self.auto_forget.detect_contradictions(memories)

        # Should detect contradiction and mark older for deletion
        assert len(to_forget) > 0
        assert to_forget[0].reason == "contradiction"

    def test_is_contradiction(self):
        """Test contradiction detection logic."""
        # Clear contradiction
        assert self.auto_forget._is_contradiction(
            "user prefers async code patterns",
            "user does not prefer async code patterns"
        ) is True

        # Not a contradiction
        assert self.auto_forget._is_contradiction(
            "user prefers async code",
            "user prefers type hints"
        ) is False

    def test_identify_low_value_memories(self):
        """Test identifying low-value memories."""
        now = datetime.now()

        memories = [
            {
                "memory_id": "low_value",
                "importance": 0.1,
                "reuse_count": 0,
                "confidence": 0.2,
                "created_at": (now - timedelta(days=20)).isoformat()
            },
            {
                "memory_id": "high_value",
                "importance": 0.8,
                "reuse_count": 5,
                "confidence": 0.9,
                "created_at": now.isoformat()
            }
        ]

        to_forget = self.auto_forget.identify_low_value_memories(memories)

        # Should only mark low_value for deletion
        assert len(to_forget) == 1
        assert to_forget[0].memory_id == "low_value"
        assert to_forget[0].reason == "low_value"

    def test_calculate_value_score(self):
        """Test value score calculation."""
        now = datetime.now()

        # High value memory
        high_value = {
            "importance": 0.9,
            "reuse_count": 10,
            "confidence": 0.9,
            "created_at": now.isoformat()
        }

        # Low value memory
        low_value = {
            "importance": 0.1,
            "reuse_count": 0,
            "confidence": 0.2,
            "created_at": (now - timedelta(days=25)).isoformat()
        }

        high_score = self.auto_forget._calculate_value_score(high_value)
        low_score = self.auto_forget._calculate_value_score(low_value)

        assert high_score > low_score
        assert 0.0 <= high_score <= 1.0
        assert 0.0 <= low_score <= 1.0

    def test_filter_safe_to_delete(self):
        """Test filtering for safe deletions."""
        from anvil.memory_platform.evolution.contracts import MemoryToForget

        memories = [
            {
                "memory_id": "safe",
                "importance": 0.5,
                "reuse_count": 2
            },
            {
                "memory_id": "critical",
                "importance": 0.95,  # Too critical
                "reuse_count": 1
            },
            {
                "memory_id": "frequently_used",
                "importance": 0.6,
                "reuse_count": 15  # Too frequently used
            }
        ]

        to_forget = [
            MemoryToForget(memory_id="safe", reason="expired"),
            MemoryToForget(memory_id="critical", reason="expired"),
            MemoryToForget(memory_id="frequently_used", reason="low_value")
        ]

        safe = self.auto_forget.filter_safe_to_delete(to_forget, memories)

        # Should only keep "safe" for deletion
        assert len(safe) == 1
        assert safe[0].memory_id == "safe"

    def test_run_cleanup_full_workflow(self):
        """Test complete cleanup workflow."""
        now = datetime.now()

        memories = [
            # Expired
            {
                "memory_id": "expired_1",
                "created_at": (now - timedelta(days=35)).isoformat(),
                "importance": 0.5,
                "reuse_count": 0
            },
            # Low value
            {
                "memory_id": "low_value_1",
                "created_at": (now - timedelta(days=10)).isoformat(),
                "importance": 0.1,
                "reuse_count": 0,
                "confidence": 0.2
            },
            # Should keep - recent and valuable
            {
                "memory_id": "keep_1",
                "created_at": now.isoformat(),
                "importance": 0.8,
                "reuse_count": 5
            },
            # Should keep - critical
            {
                "memory_id": "keep_2",
                "created_at": (now - timedelta(days=35)).isoformat(),
                "importance": 0.95,
                "reuse_count": 0
            }
        ]

        to_forget = self.auto_forget.run_cleanup(memories, now)

        # Should identify expired_1 and low_value_1
        assert len(to_forget) >= 1
        memory_ids = {item.memory_id for item in to_forget}
        assert "keep_1" not in memory_ids
        assert "keep_2" not in memory_ids

    def test_run_cleanup_empty(self):
        """Test cleanup with no memories."""
        to_forget = self.auto_forget.run_cleanup([])

        assert len(to_forget) == 0

    def test_run_cleanup_deduplication(self):
        """Test that cleanup deduplicates results."""
        now = datetime.now()

        # Memory that matches multiple criteria
        memories = [
            {
                "memory_id": "multi_match",
                "created_at": (now - timedelta(days=35)).isoformat(),
                "importance": 0.1,
                "reuse_count": 0,
                "confidence": 0.1
            }
        ]

        to_forget = self.auto_forget.run_cleanup(memories, now)

        # Should only appear once despite matching multiple criteria
        memory_ids = [item.memory_id for item in to_forget]
        assert memory_ids.count("multi_match") == 1
