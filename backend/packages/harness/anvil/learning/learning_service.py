"""Unified learning service coordinating all learning components."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .behavior_adapter import BehaviorAdapter
from .contracts import (
    AdaptationType,
    ExecutionFeedback,
    FailureAnalysis,
    LearningConfig,
    OutcomeType,
    Pattern,
)
from .failure_analyzer import FailureAnalyzer
from .feedback_collector import FeedbackCollector
from .pattern_detector import PatternDetector
from .performance_tracker import LearningPerformanceTracker

logger = logging.getLogger(__name__)


class LearningService:
    """Unified learning service coordinating all learning mechanisms.

    Features:
    - Feedback collection and storage
    - Pattern detection and recognition
    - Failure analysis and prevention
    - Adaptive behavior management
    - Performance tracking and metrics
    """

    def __init__(self, config: LearningConfig):
        """Initialize learning service.

        Args:
            config: Learning configuration
        """
        self.config = config

        # Initialize components
        self.feedback_collector = FeedbackCollector(config)
        self.pattern_detector = PatternDetector(config)
        self.failure_analyzer = FailureAnalyzer(config)
        self.behavior_adapter = BehaviorAdapter(config)
        self.performance_tracker = LearningPerformanceTracker(config)

    def record_execution(
        self,
        task_description: str,
        outcome: OutcomeType,
        tools_used: list[str],
        files_modified: list[str] | None = None,
        errors: list[str] | None = None,
        duration_seconds: float = 0.0,
        context_snapshot: dict[str, Any] | None = None,
        memory_accessed: list[str] | None = None,
        session_id: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> ExecutionFeedback:
        """Record execution for learning.

        Args:
            task_description: What was being attempted
            outcome: Execution outcome
            tools_used: Tools invoked
            files_modified: Files changed
            errors: Error messages
            duration_seconds: Execution duration
            context_snapshot: Relevant context
            memory_accessed: Memory IDs accessed
            session_id: Session identifier
            thread_id: Thread identifier
            run_id: Run identifier

        Returns:
            Collected feedback
        """
        start_time = datetime.now()

        # Collect feedback
        feedback = self.feedback_collector.collect(
            task_description=task_description,
            outcome=outcome,
            tools_used=tools_used,
            files_modified=files_modified,
            errors=errors,
            duration_seconds=duration_seconds,
            context_snapshot=context_snapshot,
            memory_accessed=memory_accessed,
            session_id=session_id,
            thread_id=thread_id,
            run_id=run_id
        )

        # Track metrics
        self.performance_tracker.record_feedback_collected(outcome)

        # Analyze failures
        if outcome == OutcomeType.FAILURE and self.config.enable_failure_analysis:
            self._analyze_failure(feedback)

        # Detect patterns periodically
        if self.config.enable_pattern_detection:
            self._detect_patterns_if_needed()

        # Record overhead
        overhead_ms = (datetime.now() - start_time).total_seconds() * 1000
        self.performance_tracker.record_overhead(overhead_ms)

        return feedback

    def _analyze_failure(self, feedback: ExecutionFeedback) -> None:
        """Analyze failure and propose adaptations.

        Args:
            feedback: Failure feedback
        """
        # Get feedback history
        history = self.feedback_collector.list_feedback(limit=1000)

        # Analyze failure
        analysis = self.failure_analyzer.analyze_failure(feedback, history)

        # Track metrics
        self.performance_tracker.record_failure_analyzed(analysis.category)

        # Propose adaptations based on recommendations
        if analysis.prevention_recommendations and self.config.enable_adaptation:
            self._propose_adaptations_from_analysis(analysis)

    def _detect_patterns_if_needed(self) -> None:
        """Detect patterns if enough new feedback accumulated."""
        # Get recent feedback
        history = self.feedback_collector.list_feedback(limit=1000)

        # Detect patterns
        patterns = self.pattern_detector.detect_patterns(history)

        # Track metrics
        for pattern in patterns:
            self.performance_tracker.record_pattern_detected(
                pattern.pattern_type,
                pattern.confidence
            )

        # Propose adaptations based on patterns
        if self.config.enable_adaptation:
            for pattern in patterns:
                if pattern.strength >= 0.7:
                    self._propose_adaptations_from_pattern(pattern)

    def _propose_adaptations_from_analysis(self, analysis: FailureAnalysis) -> None:
        """Propose adaptations based on failure analysis.

        Args:
            analysis: Failure analysis
        """
        # Error handling adaptations
        if analysis.prevention_recommendations:
            self.behavior_adapter.propose_adaptation(
                adaptation_type=AdaptationType.ERROR_HANDLING,
                description=f"Prevention for {analysis.category}",
                before_value={},
                after_value={
                    "category": analysis.category,
                    "recommendations": analysis.prevention_recommendations
                },
                trigger_analysis=analysis,
                confidence=analysis.prevention_confidence
            )

    def _propose_adaptations_from_pattern(self, pattern: Pattern) -> None:
        """Propose adaptations based on detected pattern.

        Args:
            pattern: Detected pattern
        """
        # Tool sequence adaptations
        if pattern.pattern_type == "tool_sequence" and pattern.tool_sequence:
            self.behavior_adapter.propose_adaptation(
                adaptation_type=AdaptationType.TOOL_SELECTION,
                description=f"Prefer tool sequence: {' → '.join(pattern.tool_sequence[:3])}",
                before_value={},
                after_value={
                    "preferred_sequence": pattern.tool_sequence,
                    "success_rate": pattern.success_rate
                },
                trigger_pattern=pattern,
                confidence=pattern.confidence
            )

    def get_active_adaptations(self) -> dict[str, Any]:
        """Get all active adaptations.

        Returns:
            Active adaptations by type
        """
        return {
            "tool_selection": self.behavior_adapter.get_tool_selection_adaptations(),
            "context_loading": self.behavior_adapter.get_context_loading_adaptations(),
            "cache_warming": self.behavior_adapter.get_cache_warming_adaptations(),
            "error_handling": self.behavior_adapter.get_error_handling_adaptations(),
            "timeouts": self.behavior_adapter.get_timeout_adaptations()
        }

    def get_learning_metrics(self) -> dict[str, Any]:
        """Get comprehensive learning metrics.

        Returns:
            Learning metrics and statistics
        """
        return {
            "feedback": self.feedback_collector.get_statistics(),
            "patterns": self.pattern_detector.get_statistics(),
            "failures": self.failure_analyzer.get_statistics(),
            "adaptations": self.behavior_adapter.get_statistics(),
            "performance": self.performance_tracker.get_summary()
        }

    def cleanup(self) -> dict[str, int]:
        """Cleanup old learning data.

        Returns:
            Cleanup statistics
        """
        feedback_removed = self.feedback_collector.cleanup_old_feedback()

        return {
            "feedback_removed": feedback_removed
        }

    def export_learning_state(self) -> dict[str, Any]:
        """Export complete learning state for analysis.

        Returns:
            Complete learning state
        """
        return {
            "config": self.config.model_dump(mode="json"),
            "metrics": self.get_learning_metrics(),
            "active_adaptations": self.get_active_adaptations(),
            "performance": self.performance_tracker.export_metrics()
        }
