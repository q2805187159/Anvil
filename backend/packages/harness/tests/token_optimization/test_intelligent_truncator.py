"""Tests for intelligent truncator."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from anvil.token_optimization.contracts import ContextItem, TokenOptimizationConfig, TruncationStrategy
from anvil.token_optimization.intelligent_truncator import IntelligentTruncator


@pytest.fixture
def config():
    """Create test configuration."""
    return TokenOptimizationConfig()


@pytest.fixture
def truncator(config):
    """Create truncator instance."""
    return IntelligentTruncator(config)


@pytest.fixture
def sample_items():
    """Create sample context items."""
    now = datetime.now()
    return [
        ContextItem(content="High priority item", priority="high", tokens=10, timestamp=now),
        ContextItem(content="Medium priority item 1", priority="medium", tokens=15, timestamp=now - timedelta(minutes=5)),
        ContextItem(content="Low priority item", priority="low", tokens=20, timestamp=now - timedelta(minutes=10)),
        ContextItem(content="Medium priority item 2", priority="medium", tokens=12, timestamp=now - timedelta(minutes=2)),
        ContextItem(content="High priority item 2", priority="high", tokens=8, timestamp=now - timedelta(minutes=1)),
    ]


class TestIntelligentTruncator:
    """Tests for IntelligentTruncator."""

    def test_truncate_by_priority(self, truncator, sample_items):
        """Test truncation by priority."""
        # Total tokens = 65, so max_tokens=25 will force truncation
        # But min_context_items=5 keeps all, so use max_tokens=40 with 6 items
        extra_item = ContextItem(content="Extra low priority", priority="low", tokens=10, timestamp=datetime.now())
        items = sample_items + [extra_item]

        truncated, result = truncator.truncate(items, max_tokens=40)

        assert len(truncated) < len(items)
        assert result.token_savings > 0
        assert result.strategy_used == "priority"

        # High priority items should be preserved
        priorities = [item.priority for item in truncated]
        assert "high" in priorities

    def test_truncate_sliding_window(self, sample_items):
        """Test sliding window truncation."""
        config = TokenOptimizationConfig(truncation_strategy=TruncationStrategy.SLIDING)
        truncator = IntelligentTruncator(config)

        # Add extra item to have 6 items
        extra_item = ContextItem(content="Extra old item", priority="medium", tokens=10, timestamp=datetime.now() - timedelta(minutes=20))
        items = sample_items + [extra_item]

        truncated, result = truncator.truncate(items, max_tokens=40)

        assert len(truncated) < len(items)
        assert result.strategy_used == "sliding"

        # Recent items should be preserved
        if truncated:
            assert truncated[-1].timestamp >= items[0].timestamp

    def test_truncate_hybrid(self, sample_items):
        """Test hybrid truncation."""
        config = TokenOptimizationConfig(truncation_strategy=TruncationStrategy.HYBRID)
        truncator = IntelligentTruncator(config)

        # Add extra item to have 6 items
        extra_item = ContextItem(content="Extra item", priority="low", tokens=10, timestamp=datetime.now())
        items = sample_items + [extra_item]

        truncated, result = truncator.truncate(items, max_tokens=40)

        assert len(truncated) < len(items)
        assert result.strategy_used == "hybrid"

    def test_truncate_respects_min_items(self, truncator, sample_items):
        """Test that minimum items are preserved."""
        truncated, result = truncator.truncate(sample_items, max_tokens=10)

        # Should keep at least min_context_items even if over budget
        assert len(truncated) >= truncator.config.min_context_items

    def test_truncate_no_change_if_under_budget(self, truncator, sample_items):
        """Test no truncation if under budget."""
        total_tokens = sum(item.tokens for item in sample_items)
        truncated, result = truncator.truncate(sample_items, max_tokens=total_tokens + 100)

        assert len(truncated) == len(sample_items)
        assert result.items_removed == 0

    def test_truncate_empty_items(self, truncator):
        """Test truncating empty list."""
        truncated, result = truncator.truncate([], max_tokens=100)

        assert len(truncated) == 0
        assert result.items_removed == 0

    def test_truncation_disabled(self, sample_items):
        """Test truncation can be disabled."""
        config = TokenOptimizationConfig(enable_intelligent_truncation=False)
        truncator = IntelligentTruncator(config)

        truncated, result = truncator.truncate(sample_items, max_tokens=10)

        assert len(truncated) == len(sample_items)
        assert result.strategy_used == "none"

    def test_truncation_preserves_order(self, truncator, sample_items):
        """Test that chronological order is preserved."""
        truncated, result = truncator.truncate(sample_items, max_tokens=30)

        # Check timestamps are in order
        for i in range(len(truncated) - 1):
            assert truncated[i].timestamp <= truncated[i + 1].timestamp

    def test_truncation_time_tracking(self, truncator, sample_items):
        """Test truncation time is tracked."""
        truncated, result = truncator.truncate(sample_items, max_tokens=30)

        assert result.truncation_time_ms >= 0

    def test_truncation_metrics(self, truncator, sample_items):
        """Test truncation result metrics."""
        truncated, result = truncator.truncate(sample_items, max_tokens=30)

        assert result.original_count == len(sample_items)
        assert result.truncated_count == len(truncated)
        assert result.items_removed == len(sample_items) - len(truncated)
        assert result.original_tokens == sum(item.tokens for item in sample_items)
        assert result.truncated_tokens == sum(item.tokens for item in truncated)
