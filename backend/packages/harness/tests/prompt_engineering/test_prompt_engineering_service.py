"""Tests for prompt engineering service."""

from __future__ import annotations

import pytest

from anvil.prompt_engineering.contracts import (
    AdaptivePromptContext,
    PromptEngineeringConfig,
)
from anvil.prompt_engineering.prompt_engineering_service import PromptEngineeringService


@pytest.fixture
def config():
    """Create test configuration."""
    return PromptEngineeringConfig()


@pytest.fixture
def service(config):
    """Create service instance."""
    return PromptEngineeringService(config)


class TestPromptEngineeringService:
    """Tests for PromptEngineeringService."""

    def test_optimize_tool_description(self, service):
        """Test optimizing single tool description."""
        original = "Search the web using the configured provider."
        optimized = service.optimize_tool_description(original)

        assert len(optimized) <= len(original)
        assert "configured provider" not in optimized.lower()

    def test_optimize_tool_descriptions_batch(self, service):
        """Test batch tool description optimization."""
        descriptions = {
            "web_search": "Search the web using the configured provider.",
            "web_fetch": "Fetch a web page and return content.",
        }

        optimized = service.optimize_tool_descriptions_batch(descriptions)

        assert len(optimized) == 2
        assert all(len(optimized[k]) <= len(descriptions[k]) for k in descriptions)

    def test_optimize_system_prompt(self, service):
        """Test system prompt optimization."""
        original = "You are a helpful assistant. Always be polite."
        optimized = service.optimize_system_prompt(original)

        assert len(optimized) <= len(original)

    def test_optimize_context(self, service):
        """Test context optimization."""
        items = [
            {"content": "Item 1 with some content"},
            {"content": "Item 2 with different content"},
            {"content": "Item 1 with some content"},  # Duplicate
            {"content": ""},  # Empty
            {"content": "x"},  # Too short
        ]

        optimized_items, result = service.optimize_context(items)

        assert len(optimized_items) < len(items)
        assert result.deduplication_count > 0
        assert result.noise_removed_count > 0
        assert result.token_savings > 0

    def test_context_deduplication(self, service):
        """Test context deduplication."""
        items = [
            {"content": "Same content here"},
            {"content": "Same content here"},
            {"content": "Different content"},
        ]

        optimized_items, result = service.optimize_context(items)

        assert len(optimized_items) == 2
        assert result.deduplication_count == 1

    def test_context_noise_removal(self, service):
        """Test noise removal from context."""
        items = [
            {"content": "Good content with enough text"},
            {"content": ""},
            {"content": "x"},
            {"content": "Another good item with text"},
        ]

        optimized_items, result = service.optimize_context(items)

        assert len(optimized_items) == 2
        assert result.noise_removed_count == 2

    def test_context_truncation(self, service):
        """Test context truncation to budget."""
        items = [
            {"content": "Item " + "word " * 100} for _ in range(10)
        ]

        optimized_items, result = service.optimize_context(items, max_tokens=500)

        assert len(optimized_items) < len(items)
        assert result.optimized_tokens <= 500

    def test_generate_adaptive_prompt(self, service):
        """Test adaptive prompt generation."""
        base_prompt = "Complete the task."
        context = AdaptivePromptContext(
            task_type="coding",
            learned_patterns=["Use read_file before editing", "Run tests after changes"],
            suggested_tools=["read_file", "edit_file", "run_tests"],
            recent_failures=["Don't forget to save files"],
        )

        adapted = service.generate_adaptive_prompt(base_prompt, context)

        assert len(adapted) > len(base_prompt)
        assert "read_file" in adapted.lower()

    def test_adaptive_prompt_disabled(self):
        """Test adaptive prompt when disabled."""
        config = PromptEngineeringConfig(enable_dynamic_adaptation=False)
        service = PromptEngineeringService(config)

        base_prompt = "Complete the task."
        context = AdaptivePromptContext(
            task_type="coding",
            learned_patterns=["Pattern 1"],
        )

        adapted = service.generate_adaptive_prompt(base_prompt, context)

        assert adapted == base_prompt

    def test_comprehensive_metrics(self, service):
        """Test comprehensive metrics collection."""
        service.optimize_tool_description("Search the web using provider.")
        service.optimize_system_prompt("You are helpful.")
        service.optimize_context([{"content": "test"}])

        metrics = service.get_comprehensive_metrics()

        assert metrics.tool_descriptions.total_descriptions_optimized > 0
        assert metrics.system_prompts.total_descriptions_optimized > 0
        assert len(metrics.context_optimizations) > 0

    def test_optimization_summary(self, service):
        """Test optimization summary generation."""
        service.optimize_tool_description("Search the web.")
        service.optimize_system_prompt("You are helpful.")

        summary = service.get_optimization_summary()

        assert "tool_descriptions" in summary
        assert "system_prompts" in summary
        assert "overall" in summary

    def test_metrics_reset(self, service):
        """Test metrics reset."""
        service.optimize_tool_description("Search the web.")
        service.reset_metrics()

        metrics = service.get_comprehensive_metrics()

        assert metrics.tool_descriptions.total_descriptions_optimized == 0

    def test_optimization_disabled(self):
        """Test all optimizations can be disabled."""
        config = PromptEngineeringConfig(
            optimize_tool_descriptions=False,
            optimize_system_prompts=False,
            optimize_context=False,
        )
        service = PromptEngineeringService(config)

        tool_desc = service.optimize_tool_description("Search the web.")
        system_prompt = service.optimize_system_prompt("You are helpful.")
        items, result = service.optimize_context([{"content": "test"}])

        assert tool_desc == "Search the web."
        assert system_prompt == "You are helpful."
        assert len(items) == 1
        assert result.token_savings == 0

    def test_token_estimation(self, service):
        """Test token estimation."""
        item = {"content": "This is a test with ten words in it."}
        tokens = service._estimate_tokens(item)

        assert tokens > 0

    def test_context_optimization_tracking(self, service):
        """Test context optimizations are tracked."""
        service.optimize_context([{"content": "test"}])
        service.optimize_context([{"content": "test2"}])

        assert len(service.context_optimizations) == 2
