"""Tests for system prompt optimizer."""

from __future__ import annotations

import pytest

from anvil.prompt_engineering.contracts import PromptEngineeringConfig
from anvil.prompt_engineering.system_prompt_optimizer import SystemPromptOptimizer


@pytest.fixture
def config():
    """Create test configuration."""
    return PromptEngineeringConfig(
        remove_personality_content=True,
        remove_thinking_instructions=True,
    )


@pytest.fixture
def optimizer(config):
    """Create optimizer instance."""
    return SystemPromptOptimizer(config)


class TestSystemPromptOptimizer:
    """Tests for SystemPromptOptimizer."""

    def test_remove_personality(self, optimizer):
        """Test removing personality content."""
        original = "You are a helpful AI assistant. Always be polite and professional."
        result = optimizer.optimize(original)

        assert "helpful" not in result.optimized.lower() or len(result.optimized) < len(original)
        assert result.token_savings >= 0

    def test_remove_thinking_instructions(self, optimizer):
        """Test removing thinking instructions."""
        original = "When you encounter an error, you should read it carefully and try to fix it."
        result = optimizer.optimize(original)

        assert len(result.optimized) < len(original)
        assert result.token_savings > 0

    def test_remove_examples(self, optimizer):
        """Test removing example sections."""
        original = """You are an assistant.

For example:
- Do this
- Do that

Use the tools available."""
        result = optimizer.optimize(original)

        assert "for example" not in result.optimized.lower()
        assert result.token_savings > 0

    def test_clean_formatting(self, optimizer):
        """Test formatting cleanup."""
        original = "Line 1\n\n\n\nLine 2\n\n\n\nLine 3"
        result = optimizer.optimize(original)

        # Should reduce multiple blank lines
        assert "\n\n\n" not in result.optimized

    def test_optimization_disabled(self):
        """Test optimization can be disabled."""
        config = PromptEngineeringConfig(
            optimize_system_prompts=False,
        )
        optimizer = SystemPromptOptimizer(config)

        original = "You are a helpful assistant. Always be polite."
        result = optimizer.optimize(original)

        assert result.optimized == original
        assert result.token_savings == 0

    def test_create_optimized_template(self, optimizer):
        """Test creating optimized template."""
        template = optimizer.create_optimized_template(
            role="AI assistant for coding tasks",
            capabilities=["Read files", "Write code", "Run tests"],
            constraints=["No destructive operations", "Ask before commits"],
            context="Working on Python project",
        )

        assert "Role:" in template.template
        assert "Capabilities:" in template.template
        assert "Constraints:" in template.template
        assert template.estimated_tokens > 0

    def test_metrics_tracking(self, optimizer):
        """Test metrics are tracked."""
        optimizer.optimize("You are a helpful assistant. Always be polite.")
        optimizer.optimize("When you see an error, fix it carefully.")

        metrics = optimizer.get_metrics()

        assert metrics.total_descriptions_optimized == 2
        assert metrics.total_token_savings >= 0

    def test_metrics_reset(self, optimizer):
        """Test metrics can be reset."""
        optimizer.optimize("You are a helpful assistant.")
        optimizer.reset_metrics()

        metrics = optimizer.get_metrics()

        assert metrics.total_descriptions_optimized == 0

    def test_optimization_report(self, optimizer):
        """Test optimization report generation."""
        optimizer.optimize("You are a helpful assistant. Always be polite.")

        report = optimizer.get_optimization_report()

        assert "total_optimized" in report
        assert "token_savings" in report
        assert "savings_percent" in report

    def test_empty_prompt(self, optimizer):
        """Test handling empty prompt."""
        result = optimizer.optimize("")

        assert result.optimized == ""
        assert result.token_savings == 0

    def test_token_counting(self, optimizer):
        """Test token counting."""
        text = "This is a test with ten words in it."
        tokens = optimizer._count_tokens(text)

        assert tokens > 0

    def test_real_world_example(self, optimizer):
        """Test real-world system prompt."""
        original = """You are a helpful AI assistant. You should always be polite and professional.

When you encounter an error, you should:
1. Read the error message carefully
2. Identify the root cause
3. Try to fix the issue

If you don't understand, ask for clarification.

For example:
- When reading files, check if they exist first
- When writing code, follow best practices

Use the available tools to complete tasks."""

        result = optimizer.optimize(original)

        assert result.token_savings > 10
        assert len(result.optimized) < len(original)

    def test_preserve_essential_content(self, optimizer):
        """Test that essential content is preserved."""
        original = "Role: AI coding assistant\nCapabilities:\n- Read files\n- Write code"
        result = optimizer.optimize(original)

        # Essential structure should be preserved
        assert "role" in result.optimized.lower() or "ai" in result.optimized.lower()

    def test_rules_applied_tracking(self, optimizer):
        """Test rules applied are tracked."""
        original = "You are a helpful assistant. When you see errors, fix them carefully."
        result = optimizer.optimize(original)

        assert isinstance(result.rules_applied, list)
        assert len(result.rules_applied) > 0
