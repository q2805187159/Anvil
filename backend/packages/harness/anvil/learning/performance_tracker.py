"""Learning system performance tracking and metrics."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from .contracts import LearningConfig, LearningMetrics

logger = logging.getLogger(__name__)


class LearningPerformanceTracker:
    """Tracks learning system performance and effectiveness.

    Features:
    - Feedback collection metrics
    - Pattern detection metrics
    - Failure analysis metrics
    - Adaptation effectiveness
    - Performance overhead tracking
    - Improvement measurement
    """

    def __init__(self, config: LearningConfig):
        """Initialize performance tracker.

        Args:
            config: Learning configuration
        """
        self.config = config

        # Current metrics
        self.metrics = LearningMetrics()

        # Performance tracking
        self.overhead_samples: list[float] = []

        # Baseline tracking
        self.baseline_success_rate: float | None = None
        self.baseline_sample_count = 0

    def record_feedback_collected(self, outcome: str) -> None:
        """Record feedback collection.

        Args:
            outcome: Execution outcome
        """
        self.metrics.total_feedback_collected += 1
        self.metrics.feedback_by_outcome[outcome] = (
            self.metrics.feedback_by_outcome.get(outcome, 0) + 1
        )

    def record_pattern_detected(self, pattern_type: str, confidence: float) -> None:
        """Record pattern detection.

        Args:
            pattern_type: Type of pattern
            confidence: Pattern confidence
        """
        self.metrics.total_patterns_detected += 1
        self.metrics.patterns_by_type[pattern_type] = (
            self.metrics.patterns_by_type.get(pattern_type, 0) + 1
        )

        # Update average confidence
        total = self.metrics.total_patterns_detected
        current_avg = self.metrics.average_pattern_confidence
        self.metrics.average_pattern_confidence = (
            (current_avg * (total - 1) + confidence) / total
        )

    def record_failure_analyzed(self, category: str) -> None:
        """Record failure analysis.

        Args:
            category: Failure category
        """
        self.metrics.total_failures_analyzed += 1
        self.metrics.failures_by_category[category] = (
            self.metrics.failures_by_category.get(category, 0) + 1
        )

    def record_failure_prevented(self, was_prevented: bool) -> None:
        """Record failure prevention attempt.

        Args:
            was_prevented: Whether failure was prevented
        """
        # Track prevention success rate
        if was_prevented:
            prevented = self.metrics.prevention_success_rate * self.metrics.total_failures_analyzed
            prevented += 1
            total = self.metrics.total_failures_analyzed + 1
            self.metrics.prevention_success_rate = prevented / total if total > 0 else 0.0

    def record_adaptation_applied(self, adaptation_type: str, success: bool) -> None:
        """Record adaptation application.

        Args:
            adaptation_type: Type of adaptation
            success: Whether adaptation was successful
        """
        self.metrics.total_adaptations += 1
        self.metrics.adaptations_by_type[adaptation_type] = (
            self.metrics.adaptations_by_type.get(adaptation_type, 0) + 1
        )

        # Update success rate
        if success:
            successes = self.metrics.adaptation_success_rate * (self.metrics.total_adaptations - 1)
            successes += 1
            self.metrics.adaptation_success_rate = successes / self.metrics.total_adaptations

    def record_overhead(self, overhead_ms: float) -> None:
        """Record learning overhead.

        Args:
            overhead_ms: Overhead in milliseconds
        """
        self.overhead_samples.append(overhead_ms)

        # Update metrics
        self.metrics.average_overhead_ms = sum(self.overhead_samples) / len(self.overhead_samples)
        self.metrics.max_overhead_ms = max(self.overhead_samples)

        # Trim old samples (keep last 1000)
        if len(self.overhead_samples) > 1000:
            self.overhead_samples = self.overhead_samples[-1000:]

    def record_execution_outcome(self, success: bool, is_baseline: bool = False) -> None:
        """Record execution outcome for improvement tracking.

        Args:
            success: Whether execution was successful
            is_baseline: Whether this is baseline measurement
        """
        if is_baseline:
            # Record baseline
            self.baseline_sample_count += 1
            if self.baseline_success_rate is None:
                self.baseline_success_rate = 1.0 if success else 0.0
            else:
                total = self.baseline_sample_count
                current = self.baseline_success_rate * (total - 1)
                self.baseline_success_rate = (current + (1.0 if success else 0.0)) / total

            self.metrics.success_rate_before_learning = self.baseline_success_rate
        else:
            # Record with learning
            # This would be tracked separately in actual implementation
            pass

    def calculate_improvement(self) -> float:
        """Calculate improvement percentage.

        Returns:
            Improvement percentage
        """
        if self.metrics.success_rate_before_learning == 0.0:
            return 0.0

        improvement = (
            (self.metrics.success_rate_after_learning - self.metrics.success_rate_before_learning)
            / self.metrics.success_rate_before_learning
            * 100.0
        )

        self.metrics.improvement_percentage = improvement
        return improvement

    def check_overhead_budget(self) -> bool:
        """Check if overhead is within budget.

        Returns:
            True if within budget
        """
        if not self.overhead_samples:
            return True

        return self.metrics.average_overhead_ms <= self.config.learning_overhead_budget_ms

    def get_metrics(self) -> LearningMetrics:
        """Get current metrics.

        Returns:
            Learning metrics
        """
        self.metrics.measurement_end = datetime.now()
        return self.metrics

    def get_summary(self) -> dict[str, Any]:
        """Get metrics summary.

        Returns:
            Summary dictionary
        """
        return {
            "feedback": {
                "total": self.metrics.total_feedback_collected,
                "by_outcome": self.metrics.feedback_by_outcome
            },
            "patterns": {
                "total": self.metrics.total_patterns_detected,
                "by_type": self.metrics.patterns_by_type,
                "average_confidence": self.metrics.average_pattern_confidence
            },
            "failures": {
                "total_analyzed": self.metrics.total_failures_analyzed,
                "by_category": self.metrics.failures_by_category,
                "prevention_rate": self.metrics.prevention_success_rate
            },
            "adaptations": {
                "total": self.metrics.total_adaptations,
                "by_type": self.metrics.adaptations_by_type,
                "success_rate": self.metrics.adaptation_success_rate
            },
            "performance": {
                "average_overhead_ms": self.metrics.average_overhead_ms,
                "max_overhead_ms": self.metrics.max_overhead_ms,
                "within_budget": self.check_overhead_budget()
            },
            "improvement": {
                "before": self.metrics.success_rate_before_learning,
                "after": self.metrics.success_rate_after_learning,
                "percentage": self.metrics.improvement_percentage
            },
            "period": {
                "start": self.metrics.measurement_start.isoformat(),
                "end": self.metrics.measurement_end.isoformat(),
                "duration_hours": (
                    self.metrics.measurement_end - self.metrics.measurement_start
                ).total_seconds() / 3600
            }
        }

    def reset_metrics(self) -> None:
        """Reset metrics for new measurement period."""
        self.metrics = LearningMetrics()
        self.overhead_samples.clear()
        logger.info("Reset learning metrics")

    def export_metrics(self) -> dict[str, Any]:
        """Export metrics for external analysis.

        Returns:
            Exportable metrics dictionary
        """
        return {
            "metrics": self.metrics.model_dump(mode="json"),
            "summary": self.get_summary(),
            "config": {
                "overhead_budget_ms": self.config.learning_overhead_budget_ms,
                "min_pattern_frequency": self.config.min_pattern_frequency,
                "min_pattern_success_rate": self.config.min_pattern_success_rate,
                "adaptation_confidence_threshold": self.config.adaptation_confidence_threshold
            }
        }
