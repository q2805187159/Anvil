"""Tests for tool description optimizer."""

from __future__ import annotations

import pytest

from anvil.prompt_engineering.contracts import (
    OptimizationStrategy,
    PromptEngineeringConfig,
)
from anvil.prompt_engineering.tool_description_optimizer import ToolDescriptionOptimizer


@pytest.fixture
def config():
    """Create test configuration."""
    return PromptEngineeringConfig(
        optimization_strategy=OptimizationStrategy.BALANCED,
    )


@pytest.fixture
def optimizer(config):
    """Create optimizer instance."""
    return ToolDescriptionOptimizer(config)


class TestToolDescriptionOptimizer:
    """Tests for ToolDescriptionOptimizer."""

    def test_remove_implementation_details(self, optimizer):
        """Test removing implementation details."""
        original = "Search the web using the configured provider adapter."
        result = optimizer.optimize(original)

        assert "configured provider" not in result.optimized.lower()
        assert result.token_savings > 0
        assert "remove_implementation_details" in result.rules_applied

    def test_remove_file_paths(self, optimizer):
        """Test removing file paths."""
        original = "Generate speech audio and write to /mnt/user-data/outputs."
        result = optimizer.optimize(original)

        assert "/mnt/user-data" not in result.optimized
        assert result.token_savings > 0
        assert "remove_file_paths" in result.rules_applied

    def test_remove_usage_examples(self, optimizer):
        """Test removing usage examples."""
        original = "Click element by ref such as @e1 or by css= selector."
        result = optimizer.optimize(original)

        assert "such as" not in result.optimized.lower()
        assert result.token_savings > 0
        assert "remove_usage_examples" in result.rules_applied

    def test_remove_prefer_hints(self, optimizer):
        """Test removing prefer hints."""
        original = "Delegate tasks in batch. Prefer tasks=[{prompt,...}] format."
        result = optimizer.optimize(original)

        assert "prefer" not in result.optimized.lower()
        assert result.token_savings > 0
        assert "remove_prefer_hints" in result.rules_applied

    def test_simplify_return_statements(self, optimizer):
        """Test simplifying return statements."""
        original = "Fetch web page and return normalized content."
        result = optimizer.optimize(original)

        assert result.token_savings >= 0
        # Should convert "and return" to comma or similar

    def test_remove_redundant_articles(self, optimizer):
        """Test removing redundant articles."""
        original = "Browse the current capability catalog."
        result = optimizer.optimize(original)

        assert "the current" not in result.optimized.lower()
        assert result.token_savings > 0

    def test_capitalization_preserved(self, optimizer):
        """Test that first letter is capitalized."""
        original = "search the web and return results."
        result = optimizer.optimize(original)

        assert result.optimized[0].isupper()

    def test_multiple_rules_applied(self, optimizer):
        """Test multiple rules can be applied."""
        original = "Search the web using the configured provider and return compact results."
        result = optimizer.optimize(original)

        assert len(result.rules_applied) > 1
        assert result.token_savings > 0

    def test_aggressive_strategy(self):
        """Test aggressive optimization strategy."""
        config = PromptEngineeringConfig(
            optimization_strategy=OptimizationStrategy.AGGRESSIVE,
        )
        optimizer = ToolDescriptionOptimizer(config)

        original = "Read a file and return the content."
        result = optimizer.optimize(original)

        # Aggressive should remove articles
        assert result.token_savings > 0

    def test_optimization_disabled(self):
        """Test optimization can be disabled."""
        config = PromptEngineeringConfig(
            optimize_tool_descriptions=False,
        )
        optimizer = ToolDescriptionOptimizer(config)

        original = "Search the web using the configured provider."
        result = optimizer.optimize(original)

        assert result.optimized == original
        assert result.token_savings == 0

    def test_batch_optimization(self, optimizer):
        """Test batch optimization."""
        descriptions = [
            "Search the web using the configured provider.",
            "Fetch a web page and return normalized content.",
            "Click element by ref such as @e1.",
        ]

        results = optimizer.optimize_batch(descriptions)

        assert len(results) == 3
        assert all(r.token_savings >= 0 for r in results)

    def test_metrics_tracking(self, optimizer):
        """Test metrics are tracked."""
        optimizer.optimize("Search the web using the configured provider.")
        optimizer.optimize("Fetch a web page and return content.")

        metrics = optimizer.get_metrics()

        assert metrics.total_descriptions_optimized == 2
        assert metrics.total_token_savings > 0
        assert metrics.average_savings_percent > 0

    def test_metrics_reset(self, optimizer):
        """Test metrics can be reset."""
        optimizer.optimize("Search the web.")
        optimizer.reset_metrics()

        metrics = optimizer.get_metrics()

        assert metrics.total_descriptions_optimized == 0
        assert metrics.total_token_savings == 0

    def test_optimization_report(self, optimizer):
        """Test optimization report generation."""
        optimizer.optimize("Search the web using the configured provider.")
        optimizer.optimize("Fetch a web page and return content.")

        report = optimizer.get_optimization_report()

        assert "total_optimized" in report
        assert "token_savings" in report
        assert "savings_percent" in report
        assert "top_rules" in report

    def test_empty_description(self, optimizer):
        """Test handling empty description."""
        result = optimizer.optimize("")

        assert result.optimized == ""
        assert result.token_savings == 0

    def test_short_description(self, optimizer):
        """Test handling short description."""
        original = "Search web."
        result = optimizer.optimize(original)

        # Should still capitalize
        assert result.optimized[0].isupper()

    def test_token_counting(self, optimizer):
        """Test token counting is reasonable."""
        text = "This is a test with ten words in the sentence."
        tokens = optimizer._count_tokens(text)

        # Should be approximately 10 / 0.75 = 13-14 tokens
        assert 12 <= tokens <= 15

    def test_real_world_example_1(self, optimizer):
        """Test real-world example: delegate_batch."""
        original = "Delegate several bounded tasks in one batch and return task descriptors. Prefer tasks=[{prompt,...}] or prompts=[...]."
        result = optimizer.optimize(original)

        assert result.token_savings > 5
        assert "prefer" not in result.optimized.lower()

    def test_real_world_example_2(self, optimizer):
        """Test real-world example: web_search."""
        original = "Search the web and return compact result snippets using the configured provider adapter."
        result = optimizer.optimize(original)

        assert "configured provider" not in result.optimized.lower()
        assert result.token_savings > 3

    def test_real_world_example_3(self, optimizer):
        """Test real-world example: browser_click."""
        original = "Click a browser element by snapshot ref such as @e1 or by css= selector."
        result = optimizer.optimize(original)

        assert "such as" not in result.optimized.lower()
        assert result.token_savings > 3

    def test_rules_applied_tracking(self, optimizer):
        """Test that rules applied are tracked."""
        original = "Search the web using the configured provider and return compact results."
        result = optimizer.optimize(original)

        assert len(result.rules_applied) > 0
        assert all(isinstance(rule, str) for rule in result.rules_applied)

    def test_savings_percent_calculation(self, optimizer):
        """Test savings percent is calculated correctly."""
        original = "This is a very long description with many words."
        result = optimizer.optimize(original)

        expected_percent = (result.token_savings / result.original_tokens * 100) if result.original_tokens > 0 else 0
        assert abs(result.token_savings_percent - expected_percent) < 0.01
