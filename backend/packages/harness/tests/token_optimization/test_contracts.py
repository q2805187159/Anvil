"""Tests for token optimization contracts."""

from __future__ import annotations

import pytest

from anvil.token_optimization.contracts import (
    CompressionLevel,
    CompressionResult,
    ContextItem,
    SummarizationResult,
    TokenBudget,
    TokenOptimizationConfig,
    TruncationResult,
    TruncationStrategy,
)


class TestContracts:
    """Tests for token optimization contracts."""

    def test_truncation_strategy_enum(self):
        """Test TruncationStrategy enum."""
        assert TruncationStrategy.PRIORITY == "priority"
        assert TruncationStrategy.SLIDING == "sliding"
        assert TruncationStrategy.HYBRID == "hybrid"

    def test_compression_level_enum(self):
        """Test CompressionLevel enum."""
        assert CompressionLevel.LIGHT == "light"
        assert CompressionLevel.MEDIUM == "medium"
        assert CompressionLevel.AGGRESSIVE == "aggressive"

    def test_token_optimization_config_defaults(self):
        """Test TokenOptimizationConfig defaults."""
        config = TokenOptimizationConfig()

        assert config.enable_semantic_compression is True
        assert config.compression_level == CompressionLevel.MEDIUM
        assert config.truncation_strategy == TruncationStrategy.PRIORITY
        assert config.total_budget == 9500

    def test_token_budget_creation(self):
        """Test TokenBudget creation."""
        budget = TokenBudget(
            system_prompt=500,
            tool_descriptions=2000,
            context=5000,
            total=9500,
        )

        assert budget.system_prompt == 500
        assert budget.remaining == 9500

    def test_token_budget_remaining(self):
        """Test TokenBudget remaining calculation."""
        budget = TokenBudget()
        budget.current_system_prompt = 300
        budget.current_tool_descriptions = 1500
        budget.current_context = 3000

        assert budget.remaining == 4700  # 9500 - 4800

    def test_token_budget_is_exceeded(self):
        """Test TokenBudget exceeded check."""
        budget = TokenBudget()
        budget.current_system_prompt = 500
        budget.current_tool_descriptions = 2000
        budget.current_context = 5000

        # Total = 7500, remaining = 2000, buffer = 2000
        assert budget.is_exceeded is False

        budget.current_context = 6000
        # Total = 8500, remaining = 1000, buffer = 2000
        assert budget.is_exceeded is True

    def test_compression_result_creation(self):
        """Test CompressionResult creation."""
        result = CompressionResult(
            original="Original text here",
            compressed="Compressed",
            original_tokens=10,
            compressed_tokens=5,
            token_savings=5,
            compression_ratio=0.5,
        )

        assert result.token_savings == 5
        assert result.compression_ratio == 0.5

    def test_truncation_result_creation(self):
        """Test TruncationResult creation."""
        result = TruncationResult(
            original_count=10,
            truncated_count=5,
            items_removed=5,
            original_tokens=1000,
            truncated_tokens=500,
            token_savings=500,
            strategy_used="priority",
        )

        assert result.items_removed == 5
        assert result.token_savings == 500

    def test_summarization_result_creation(self):
        """Test SummarizationResult creation."""
        result = SummarizationResult(
            original="Long original text",
            summary="Short summary",
            original_tokens=20,
            summary_tokens=5,
            token_savings=15,
            summarization_level="brief",
        )

        assert result.token_savings == 15
        assert result.summarization_level == "brief"

    def test_context_item_creation(self):
        """Test ContextItem creation."""
        item = ContextItem(
            content="Test content",
            priority="high",
            tokens=10,
        )

        assert item.priority == "high"
        assert item.tokens == 10

    def test_config_serialization(self):
        """Test config can be serialized."""
        config = TokenOptimizationConfig()
        data = config.model_dump(mode="json")

        assert "enable_semantic_compression" in data
        assert "truncation_strategy" in data
