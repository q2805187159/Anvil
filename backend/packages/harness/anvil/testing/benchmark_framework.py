"""Anvil Custom Benchmark Testing Framework

Comprehensive benchmark testing for validating optimization improvements.
Provides detailed metrics, reports, and analysis for all optimization components.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel


class TestStatus(str, Enum):
    """Test execution status."""
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    SKIPPED = "skipped"


class TestCategory(str, Enum):
    """Test category classification."""
    CONTEXT_MANAGEMENT = "context_management"
    MEMORY_EVOLUTION = "memory_evolution"
    CACHING = "caching"
    LEARNING = "learning"
    PROMPT_OPTIMIZATION = "prompt_optimization"
    TOKEN_OPTIMIZATION = "token_optimization"
    INTEGRATION = "integration"
    REAL_WORLD = "real_world"


@dataclass
class TokenMetrics:
    """Token usage metrics."""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    saved_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def savings_percentage(self) -> float:
        if self.total_tokens == 0:
            return 0.0
        return (self.saved_tokens / (self.total_tokens + self.saved_tokens)) * 100


@dataclass
class PerformanceMetrics:
    """Performance timing metrics."""
    execution_time_ms: float = 0.0
    overhead_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return (self.cache_hits / total) * 100


@dataclass
class OptimizationMetrics:
    """Optimization impact metrics."""
    compaction_saved: int = 0
    jit_loading_saved: int = 0
    prompt_opt_saved: int = 0
    token_opt_saved: int = 0
    caching_saved: int = 0

    @property
    def total_saved(self) -> int:
        return (
            self.compaction_saved +
            self.jit_loading_saved +
            self.prompt_opt_saved +
            self.token_opt_saved +
            self.caching_saved
        )


@dataclass
class ToolCallMetrics:
    """Tool call tracking."""
    tool_name: str
    purpose: str
    result: str
    duration_ms: float = 0.0
    success: bool = True


@dataclass
class SkillUsageMetrics:
    """Skill usage tracking."""
    skill_name: str
    when_used: str
    outcome: str
    success: bool = True


@dataclass
class MemoryMetrics:
    """Memory operation metrics."""
    extracted_count: int = 0
    injected_count: int = 0
    quality_score: float = 0.0
    extracted_items: list[str] = field(default_factory=list)
    injected_items: list[str] = field(default_factory=list)


@dataclass
class TestMetrics:
    """Complete test metrics."""
    tokens: TokenMetrics = field(default_factory=TokenMetrics)
    performance: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    optimization: OptimizationMetrics = field(default_factory=OptimizationMetrics)
    tool_calls: list[ToolCallMetrics] = field(default_factory=list)
    skill_usage: list[SkillUsageMetrics] = field(default_factory=list)
    memory: MemoryMetrics = field(default_factory=MemoryMetrics)


@dataclass
class TestResult:
    """Test execution result."""
    test_name: str
    category: TestCategory
    description: str
    status: TestStatus
    quality_score: float  # 0-10
    duration_ms: float
    metrics: TestMetrics
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "test_name": self.test_name,
            "category": self.category.value,
            "description": self.description,
            "status": self.status.value,
            "quality_score": self.quality_score,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "metrics": {
                "tokens": {
                    "input": self.metrics.tokens.input_tokens,
                    "output": self.metrics.tokens.output_tokens,
                    "cached": self.metrics.tokens.cached_tokens,
                    "saved": self.metrics.tokens.saved_tokens,
                    "total": self.metrics.tokens.total_tokens,
                    "savings_pct": self.metrics.tokens.savings_percentage,
                },
                "performance": {
                    "execution_ms": self.metrics.performance.execution_time_ms,
                    "overhead_ms": self.metrics.performance.overhead_ms,
                    "cache_hits": self.metrics.performance.cache_hits,
                    "cache_misses": self.metrics.performance.cache_misses,
                    "cache_hit_rate": self.metrics.performance.cache_hit_rate,
                },
                "optimization": {
                    "compaction": self.metrics.optimization.compaction_saved,
                    "jit_loading": self.metrics.optimization.jit_loading_saved,
                    "prompt_opt": self.metrics.optimization.prompt_opt_saved,
                    "token_opt": self.metrics.optimization.token_opt_saved,
                    "caching": self.metrics.optimization.caching_saved,
                    "total": self.metrics.optimization.total_saved,
                },
                "tool_calls": [
                    {
                        "tool": tc.tool_name,
                        "purpose": tc.purpose,
                        "result": tc.result,
                        "duration_ms": tc.duration_ms,
                        "success": tc.success,
                    }
                    for tc in self.metrics.tool_calls
                ],
                "skill_usage": [
                    {
                        "skill": su.skill_name,
                        "when": su.when_used,
                        "outcome": su.outcome,
                        "success": su.success,
                    }
                    for su in self.metrics.skill_usage
                ],
                "memory": {
                    "extracted_count": self.metrics.memory.extracted_count,
                    "injected_count": self.metrics.memory.injected_count,
                    "quality_score": self.metrics.memory.quality_score,
                    "extracted_items": self.metrics.memory.extracted_items,
                    "injected_items": self.metrics.memory.injected_items,
                },
            },
            "issues": self.issues,
            "recommendations": self.recommendations,
        }


class BenchmarkTest:
    """Base class for benchmark tests."""

    def __init__(
        self,
        name: str,
        category: TestCategory,
        description: str,
    ):
        self.name = name
        self.category = category
        self.description = description
        self.metrics = TestMetrics()
        self.issues: list[str] = []
        self.recommendations: list[str] = []

    def setup(self) -> None:
        """Prepare test environment. Override in subclasses."""
        pass

    def execute(self) -> TestStatus:
        """
        Run the test. Override in subclasses.

        Returns:
            TestStatus indicating success/failure/partial
        """
        raise NotImplementedError("Subclasses must implement execute()")

    def measure(self) -> None:
        """Collect metrics. Override in subclasses to add custom metrics."""
        pass

    def validate(self) -> tuple[TestStatus, float]:
        """
        Check success criteria. Override in subclasses.

        Returns:
            Tuple of (status, quality_score)
        """
        raise NotImplementedError("Subclasses must implement validate()")

    def cleanup(self) -> None:
        """Clean up test environment. Override in subclasses."""
        pass

    def run(self) -> TestResult:
        """Execute complete test workflow."""
        start_time = time.time()

        try:
            # Setup
            self.setup()

            # Execute
            status = self.execute()

            # Measure
            self.measure()

            # Validate
            validated_status, quality_score = self.validate()

            # Use validated status if execute returned success
            if status == TestStatus.SUCCESS:
                status = validated_status

        except Exception as e:
            status = TestStatus.FAILURE
            quality_score = 0.0
            self.issues.append(f"Test execution failed: {str(e)}")

        finally:
            # Cleanup
            try:
                self.cleanup()
            except Exception as e:
                self.issues.append(f"Cleanup failed: {str(e)}")

        duration_ms = (time.time() - start_time) * 1000

        return TestResult(
            test_name=self.name,
            category=self.category,
            description=self.description,
            status=status,
            quality_score=quality_score,
            duration_ms=duration_ms,
            metrics=self.metrics,
            issues=self.issues,
            recommendations=self.recommendations,
        )


class BenchmarkSuite:
    """Collection of benchmark tests."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.tests: list[BenchmarkTest] = []
        self.results: list[TestResult] = []

    def add_test(self, test: BenchmarkTest) -> None:
        """Add a test to the suite."""
        self.tests.append(test)

    def run_all(self, verbose: bool = True) -> list[TestResult]:
        """Run all tests in the suite."""
        self.results = []

        for i, test in enumerate(self.tests, 1):
            if verbose:
                print(f"Running test {i}/{len(self.tests)}: {test.name}")

            result = test.run()
            self.results.append(result)

            if verbose:
                status_symbol = "✅" if result.status == TestStatus.SUCCESS else "❌"
                print(f"  {status_symbol} {result.status.value} - Score: {result.quality_score:.1f}/10")

        return self.results

    def get_summary(self) -> dict[str, Any]:
        """Get aggregate summary of all test results."""
        if not self.results:
            return {}

        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == TestStatus.SUCCESS)
        failed = sum(1 for r in self.results if r.status == TestStatus.FAILURE)
        partial = sum(1 for r in self.results if r.status == TestStatus.PARTIAL)

        avg_quality = sum(r.quality_score for r in self.results) / total
        avg_duration = sum(r.duration_ms for r in self.results) / total

        # Aggregate metrics
        total_tokens_saved = sum(r.metrics.tokens.saved_tokens for r in self.results)
        total_tokens_used = sum(r.metrics.tokens.total_tokens for r in self.results)

        total_cache_hits = sum(r.metrics.performance.cache_hits for r in self.results)
        total_cache_misses = sum(r.metrics.performance.cache_misses for r in self.results)

        avg_overhead = sum(r.metrics.performance.overhead_ms for r in self.results) / total

        return {
            "suite_name": self.name,
            "description": self.description,
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "partial": partial,
            "pass_rate": (passed / total) * 100,
            "average_quality": avg_quality,
            "average_duration_ms": avg_duration,
            "metrics": {
                "tokens": {
                    "total_saved": total_tokens_saved,
                    "total_used": total_tokens_used,
                    "savings_pct": (total_tokens_saved / (total_tokens_used + total_tokens_saved)) * 100 if total_tokens_used > 0 else 0,
                },
                "performance": {
                    "cache_hits": total_cache_hits,
                    "cache_misses": total_cache_misses,
                    "cache_hit_rate": (total_cache_hits / (total_cache_hits + total_cache_misses)) * 100 if (total_cache_hits + total_cache_misses) > 0 else 0,
                    "avg_overhead_ms": avg_overhead,
                },
            },
            "by_category": self._get_category_breakdown(),
        }

    def _get_category_breakdown(self) -> dict[str, Any]:
        """Get results broken down by category."""
        breakdown = {}

        for category in TestCategory:
            category_results = [r for r in self.results if r.category == category]
            if not category_results:
                continue

            total = len(category_results)
            passed = sum(1 for r in category_results if r.status == TestStatus.SUCCESS)
            avg_quality = sum(r.quality_score for r in category_results) / total

            breakdown[category.value] = {
                "total": total,
                "passed": passed,
                "pass_rate": (passed / total) * 100,
                "avg_quality": avg_quality,
            }

        return breakdown
