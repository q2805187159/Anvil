"""Failure analysis and prevention."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import (
    ExecutionFeedback,
    FailureAnalysis,
    FailureCategory,
    LearningConfig,
    OutcomeType,
)

logger = logging.getLogger(__name__)


class FailureAnalyzer:
    """Analyzes failures to identify patterns and prevention strategies.

    Features:
    - Categorizes failures by type
    - Finds similar past failures
    - Recommends prevention strategies
    - Tracks recovery patterns
    """

    def __init__(self, config: LearningConfig):
        """Initialize failure analyzer.

        Args:
            config: Learning configuration
        """
        self.config = config
        self.storage_path = Path(config.analysis_storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Analysis cache
        self.analyses: dict[str, FailureAnalysis] = {}
        self._load_analyses()

    def analyze_failure(
        self,
        feedback: ExecutionFeedback,
        feedback_history: list[ExecutionFeedback]
    ) -> FailureAnalysis:
        """Analyze a failure event.

        Args:
            feedback: Failure feedback
            feedback_history: Historical feedback for comparison

        Returns:
            Failure analysis
        """
        # Categorize failure
        category = self._categorize_failure(feedback)

        # Identify root cause
        root_cause = self._identify_root_cause(feedback, category)

        # Find similar failures
        similar_failures, similarity_scores = self._find_similar_failures(
            feedback,
            feedback_history
        )

        # Generate prevention recommendations
        recommendations = self._generate_prevention_recommendations(
            feedback,
            category,
            similar_failures,
            feedback_history
        )

        # Calculate prevention confidence
        prevention_confidence = self._calculate_prevention_confidence(
            similar_failures,
            recommendations
        )

        # Find recovery patterns
        recovery_patterns = self._find_recovery_patterns(
            feedback,
            feedback_history
        )

        # Create analysis
        analysis_id = self._generate_analysis_id(feedback)

        analysis = FailureAnalysis(
            analysis_id=analysis_id,
            feedback_id=feedback.feedback_id,
            category=category,
            root_cause=root_cause,
            similar_failures=similar_failures,
            similarity_scores=similarity_scores,
            prevention_recommendations=recommendations,
            prevention_confidence=prevention_confidence,
            recovery_patterns=recovery_patterns
        )

        # Store analysis
        self._store_analysis(analysis)
        self.analyses[analysis_id] = analysis

        logger.info(
            f"Analyzed failure: {analysis_id[:8]} "
            f"(category={category}, confidence={prevention_confidence:.2f})"
        )

        return analysis

    def _categorize_failure(self, feedback: ExecutionFeedback) -> FailureCategory:
        """Categorize failure type.

        Args:
            feedback: Failure feedback

        Returns:
            Failure category
        """
        if not feedback.errors:
            return FailureCategory.UNKNOWN

        # Check error messages for patterns
        error_text = " ".join(feedback.errors).lower()

        # Tool errors
        if any(keyword in error_text for keyword in [
            "tool", "command", "execution", "not found", "failed to execute"
        ]):
            return FailureCategory.TOOL_ERROR

        # Context errors
        if any(keyword in error_text for keyword in [
            "context", "missing", "not available", "undefined", "not loaded"
        ]):
            return FailureCategory.CONTEXT_INSUFFICIENT

        # Resource errors
        if any(keyword in error_text for keyword in [
            "timeout", "limit", "exceeded", "too large", "memory"
        ]):
            return FailureCategory.RESOURCE_LIMIT

        # Permission errors
        if any(keyword in error_text for keyword in [
            "permission", "denied", "forbidden", "unauthorized", "access"
        ]):
            return FailureCategory.PERMISSION_DENIED

        # Timeout
        if feedback.outcome == OutcomeType.TIMEOUT:
            return FailureCategory.TIMEOUT

        # Logic errors (default)
        return FailureCategory.LOGIC_ERROR

    def _identify_root_cause(
        self,
        feedback: ExecutionFeedback,
        category: FailureCategory
    ) -> str:
        """Identify root cause of failure.

        Args:
            feedback: Failure feedback
            category: Failure category

        Returns:
            Root cause description
        """
        if not feedback.errors:
            return f"{category}: No error message available"

        # Use first error as primary cause
        primary_error = feedback.errors[0]

        # Add context based on category
        if category == FailureCategory.TOOL_ERROR:
            if feedback.tools_used:
                return f"Tool '{feedback.tools_used[-1]}' failed: {primary_error}"
            return f"Tool execution failed: {primary_error}"

        elif category == FailureCategory.CONTEXT_INSUFFICIENT:
            return f"Insufficient context: {primary_error}"

        elif category == FailureCategory.RESOURCE_LIMIT:
            return f"Resource limit exceeded: {primary_error}"

        elif category == FailureCategory.PERMISSION_DENIED:
            return f"Permission denied: {primary_error}"

        elif category == FailureCategory.TIMEOUT:
            return f"Operation timed out after {feedback.duration_seconds:.1f}s"

        else:
            return f"Logic error: {primary_error}"

    def _find_similar_failures(
        self,
        feedback: ExecutionFeedback,
        feedback_history: list[ExecutionFeedback]
    ) -> tuple[list[str], dict[str, float]]:
        """Find similar past failures.

        Args:
            feedback: Current failure
            feedback_history: Historical feedback

        Returns:
            (similar_failure_ids, similarity_scores)
        """
        similar: list[tuple[str, float]] = []

        # Get past failures
        past_failures = [
            fb for fb in feedback_history
            if fb.outcome == OutcomeType.FAILURE and fb.feedback_id != feedback.feedback_id
        ]

        for past_fb in past_failures:
            # Calculate similarity
            similarity = self._calculate_failure_similarity(feedback, past_fb)

            if similarity >= self.config.failure_similarity_threshold:
                similar.append((past_fb.feedback_id, similarity))

        # Sort by similarity
        similar.sort(key=lambda x: x[1], reverse=True)

        # Limit results
        similar = similar[:10]

        similar_ids = [fb_id for fb_id, _ in similar]
        similarity_scores = {fb_id: score for fb_id, score in similar}

        return similar_ids, similarity_scores

    def _calculate_failure_similarity(
        self,
        feedback1: ExecutionFeedback,
        feedback2: ExecutionFeedback
    ) -> float:
        """Calculate similarity between two failures.

        Args:
            feedback1: First failure
            feedback2: Second failure

        Returns:
            Similarity score (0.0-1.0)
        """
        similarity = 0.0

        # Error message similarity
        if feedback1.errors and feedback2.errors:
            error_sim = self._text_similarity(
                " ".join(feedback1.errors),
                " ".join(feedback2.errors)
            )
            similarity += error_sim * 0.5

        # Tool similarity
        tools1 = set(feedback1.tools_used)
        tools2 = set(feedback2.tools_used)
        if tools1 and tools2:
            tool_sim = len(tools1 & tools2) / len(tools1 | tools2)
            similarity += tool_sim * 0.3

        # File similarity
        files1 = set(feedback1.files_modified)
        files2 = set(feedback2.files_modified)
        if files1 and files2:
            file_sim = len(files1 & files2) / len(files1 | files2)
            similarity += file_sim * 0.2

        return min(1.0, similarity)

    def _text_similarity(self, text1: str, text2: str) -> float:
        """Calculate text similarity using simple word overlap.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score (0.0-1.0)
        """
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def _generate_prevention_recommendations(
        self,
        feedback: ExecutionFeedback,
        category: FailureCategory,
        similar_failures: list[str],
        feedback_history: list[ExecutionFeedback]
    ) -> list[str]:
        """Generate prevention recommendations.

        Args:
            feedback: Failure feedback
            category: Failure category
            similar_failures: Similar past failures
            feedback_history: Historical feedback

        Returns:
            List of recommendations
        """
        recommendations: list[str] = []

        # Category-specific recommendations
        if category == FailureCategory.TOOL_ERROR:
            recommendations.append("Verify tool availability before execution")
            recommendations.append("Check tool input parameters")
            if feedback.tools_used:
                recommendations.append(f"Consider alternative to '{feedback.tools_used[-1]}'")

        elif category == FailureCategory.CONTEXT_INSUFFICIENT:
            recommendations.append("Load additional context before execution")
            recommendations.append("Verify required context is available")
            recommendations.append("Use JIT context loading for missing data")

        elif category == FailureCategory.RESOURCE_LIMIT:
            recommendations.append("Reduce operation scope or batch size")
            recommendations.append("Increase timeout or resource limits")
            recommendations.append("Use streaming or chunked processing")

        elif category == FailureCategory.PERMISSION_DENIED:
            recommendations.append("Verify file/resource permissions")
            recommendations.append("Request user approval for sensitive operations")
            recommendations.append("Check authentication/authorization status")

        elif category == FailureCategory.TIMEOUT:
            recommendations.append(f"Increase timeout beyond {feedback.duration_seconds:.1f}s")
            recommendations.append("Break operation into smaller steps")
            recommendations.append("Use async/background processing")

        # Learn from similar failures
        if similar_failures:
            # Find what worked after similar failures
            for similar_id in similar_failures[:3]:
                # Find feedback after this failure
                similar_idx = next(
                    (i for i, fb in enumerate(feedback_history) if fb.feedback_id == similar_id),
                    None
                )

                if similar_idx is not None and similar_idx < len(feedback_history) - 1:
                    next_fb = feedback_history[similar_idx + 1]
                    if next_fb.outcome == OutcomeType.SUCCESS:
                        recommendations.append(
                            f"Try approach that worked before: {', '.join(next_fb.tools_used[:2])}"
                        )

        return recommendations

    def _calculate_prevention_confidence(
        self,
        similar_failures: list[str],
        recommendations: list[str]
    ) -> float:
        """Calculate confidence in prevention recommendations.

        Args:
            similar_failures: Similar past failures
            recommendations: Generated recommendations

        Returns:
            Confidence score (0.0-1.0)
        """
        confidence = 0.3  # Base confidence

        # More similar failures = higher confidence
        if similar_failures:
            confidence += min(0.4, len(similar_failures) * 0.1)

        # More recommendations = higher confidence
        if recommendations:
            confidence += min(0.3, len(recommendations) * 0.05)

        return min(1.0, confidence)

    def _find_recovery_patterns(
        self,
        feedback: ExecutionFeedback,
        feedback_history: list[ExecutionFeedback]
    ) -> list[str]:
        """Find patterns that recovered from similar failures.

        Args:
            feedback: Failure feedback
            feedback_history: Historical feedback

        Returns:
            List of recovery pattern IDs
        """
        # This would integrate with PatternDetector
        # For now, return empty list
        return []

    def _generate_analysis_id(self, feedback: ExecutionFeedback) -> str:
        """Generate analysis ID.

        Args:
            feedback: Feedback

        Returns:
            Analysis ID
        """
        content = f"analysis:{feedback.feedback_id}:{datetime.now().isoformat()}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _store_analysis(self, analysis: FailureAnalysis) -> None:
        """Store analysis to disk.

        Args:
            analysis: Analysis to store
        """
        analysis_file = self.storage_path / f"{analysis.analysis_id}.json"
        with open(analysis_file, "w") as f:
            json.dump(analysis.model_dump(mode="json"), f, indent=2)

    def _load_analyses(self) -> None:
        """Load analyses from disk."""
        for analysis_file in self.storage_path.glob("*.json"):
            try:
                with open(analysis_file) as f:
                    data = json.load(f)
                    analysis = FailureAnalysis(**data)
                    self.analyses[analysis.analysis_id] = analysis
            except Exception as e:
                logger.warning(f"Failed to load analysis {analysis_file}: {e}")

        logger.info(f"Loaded {len(self.analyses)} failure analyses")

    def get_analysis(self, analysis_id: str) -> FailureAnalysis | None:
        """Get analysis by ID.

        Args:
            analysis_id: Analysis identifier

        Returns:
            Analysis or None
        """
        return self.analyses.get(analysis_id)

    def list_analyses(
        self,
        category: FailureCategory | None = None,
        min_confidence: float | None = None,
        limit: int = 100
    ) -> list[FailureAnalysis]:
        """List analyses matching criteria.

        Args:
            category: Filter by category
            min_confidence: Minimum prevention confidence
            limit: Maximum results

        Returns:
            List of analyses
        """
        results = []

        for analysis in self.analyses.values():
            # Apply filters
            if category and analysis.category != category:
                continue
            if min_confidence and analysis.prevention_confidence < min_confidence:
                continue

            results.append(analysis)

        # Sort by confidence
        results.sort(key=lambda a: a.prevention_confidence, reverse=True)

        return results[:limit]

    def get_statistics(self) -> dict[str, Any]:
        """Get failure analysis statistics.

        Returns:
            Statistics dictionary
        """
        by_category: dict[str, int] = {}
        total_confidence = 0.0

        for analysis in self.analyses.values():
            by_category[analysis.category] = by_category.get(analysis.category, 0) + 1
            total_confidence += analysis.prevention_confidence

        avg_confidence = total_confidence / len(self.analyses) if self.analyses else 0.0

        return {
            "total_analyses": len(self.analyses),
            "by_category": by_category,
            "average_prevention_confidence": avg_confidence
        }
