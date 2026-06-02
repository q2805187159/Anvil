"""Tests for prompt engineering contracts."""

from __future__ import annotations

import pytest

from anvil.prompt_engineering.contracts import (
    AdaptivePromptContext,
    ContextOptimizationResult,
    OptimizationMetrics,
    OptimizationRule,
    OptimizationStrategy,
    OptimizedDescription,
    PromptEngineeringConfig,
    SystemPromptTemplate,
)


class TestContracts:
    """Tests for prompt engineering contracts."""

    def test_optimization_strategy_enum(self):
        """Test OptimizationStrategy enum."""
        assert OptimizationStrategy.AGGRESSIVE == "aggressive"
        assert OptimizationStrategy.BALANCED == "balanced"
        assert OptimizationStrategy.CONSERVATIVE == "conservative"

    def test_optimization_rule_creation(self):
        """Test OptimizationRule creation."""
        rule = OptimizationRule(
            name="test_rule",
            pattern=r"\btest\b",
            replacement="",
            description="Test rule",
            token_savings_estimate=5,
        )

        assert rule.name == "test_rule"
        assert rule.enabled is True

    def test_optimized_description_creation(self):
        """Test OptimizedDescription creation."""
        desc = OptimizedDescription(
            original="Original text",
            optimized="Optimized",
            original_tokens=10,
            optimized_tokens=5,
            token_savings=5,
            token_savings_percent=50.0,
        )

        assert desc.token_savings == 5
        assert desc.token_savings_percent == 50.0

    def test_optimization_metrics_defaults(self):
        """Test OptimizationMetrics defaults."""
        metrics = OptimizationMetrics()

        assert metrics.total_descriptions_optimized == 0
        assert metrics.total_token_savings == 0
        assert metrics.average_savings_percent == 0.0

    def test_prompt_engineering_config_defaults(self):
        """Test PromptEngineeringConfig defaults."""
        config = PromptEngineeringConfig()

        assert config.enable_optimization is True
        assert config.optimization_strategy == OptimizationStrategy.BALANCED
        assert config.max_tool_description_tokens == 20
        assert config.target_total_tokens == 7500

    def test_system_prompt_template_creation(self):
        """Test SystemPromptTemplate creation."""
        template = SystemPromptTemplate(
            name="test_template",
            template="Role: {role}\nCapabilities: {capabilities}",
            variables={"role": "assistant", "capabilities": "coding"},
            estimated_tokens=50,
        )

        assert template.name == "test_template"
        assert "role" in template.variables

    def test_context_optimization_result_creation(self):
        """Test ContextOptimizationResult creation."""
        result = ContextOptimizationResult(
            original_tokens=1000,
            optimized_tokens=500,
            token_savings=500,
            token_savings_percent=50.0,
            deduplication_count=5,
            noise_removed_count=3,
        )

        assert result.token_savings == 500
        assert result.deduplication_count == 5

    def test_adaptive_prompt_context_creation(self):
        """Test AdaptivePromptContext creation."""
        context = AdaptivePromptContext(
            task_type="coding",
            learned_patterns=["pattern1", "pattern2"],
            suggested_tools=["tool1", "tool2"],
            confidence=0.85,
        )

        assert context.task_type == "coding"
        assert len(context.learned_patterns) == 2
        assert context.confidence == 0.85

    def test_config_serialization(self):
        """Test config can be serialized."""
        config = PromptEngineeringConfig()
        data = config.model_dump(mode="json")

        assert "enable_optimization" in data
        assert "optimization_strategy" in data
