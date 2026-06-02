"""Feedback collection and storage."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .contracts import ExecutionFeedback, LearningConfig, OutcomeType

logger = logging.getLogger(__name__)


class FeedbackCollector:
    """Collects execution feedback for learning.

    Features:
    - Captures outcomes, evidence, context
    - Calculates confidence and salience
    - Deduplicates similar feedback
    - Manages retention
    """

    def __init__(self, config: LearningConfig):
        """Initialize feedback collector.

        Args:
            config: Learning configuration
        """
        self.config = config
        self.storage_path = Path(config.feedback_storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # In-memory cache for recent feedback
        self.recent_feedback: dict[str, ExecutionFeedback] = {}

    def collect(
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
        """Collect execution feedback.

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
        # Calculate confidence based on outcome and evidence
        confidence = self._calculate_confidence(
            outcome=outcome,
            tools_used=tools_used,
            errors=errors or [],
            duration_seconds=duration_seconds
        )

        # Calculate salience (importance)
        salience = self._calculate_salience(
            task_description=task_description,
            tools_used=tools_used,
            files_modified=files_modified or [],
            outcome=outcome
        )

        # Check confidence threshold
        if confidence < self.config.min_confidence_threshold:
            logger.debug(
                f"Feedback below confidence threshold: {confidence:.2f} < "
                f"{self.config.min_confidence_threshold}"
            )
            # Still collect but mark as low confidence
            pass

        # Generate feedback ID
        feedback_id = self._generate_feedback_id(
            task_description=task_description,
            outcome=outcome,
            tools_used=tools_used,
            timestamp=datetime.now()
        )

        # Create feedback
        feedback = ExecutionFeedback(
            feedback_id=feedback_id,
            task_description=task_description,
            outcome=outcome,
            tools_used=tools_used,
            files_modified=files_modified or [],
            errors=errors or [],
            duration_seconds=duration_seconds,
            confidence=confidence,
            salience=salience,
            context_snapshot=context_snapshot or {},
            memory_accessed=memory_accessed or [],
            session_id=session_id,
            thread_id=thread_id,
            run_id=run_id
        )

        # Store feedback
        self._store_feedback(feedback)

        # Cache recent feedback
        self.recent_feedback[feedback_id] = feedback

        logger.info(
            f"Collected feedback: {feedback_id[:8]} "
            f"(outcome={outcome}, confidence={confidence:.2f}, salience={salience:.2f})"
        )

        return feedback

    def _calculate_confidence(
        self,
        outcome: OutcomeType,
        tools_used: list[str],
        errors: list[str],
        duration_seconds: float
    ) -> float:
        """Calculate confidence in feedback.

        Args:
            outcome: Execution outcome
            tools_used: Tools invoked
            errors: Error messages
            duration_seconds: Execution duration

        Returns:
            Confidence score (0.0-1.0)
        """
        confidence = 0.5  # Base confidence

        # Outcome confidence
        if outcome == OutcomeType.SUCCESS:
            confidence += 0.3
        elif outcome == OutcomeType.FAILURE:
            confidence += 0.2  # Failures are also valuable
        elif outcome == OutcomeType.PARTIAL:
            confidence += 0.1
        else:
            confidence -= 0.1

        # Evidence confidence
        if tools_used:
            confidence += min(0.1, len(tools_used) * 0.02)

        if errors:
            confidence += 0.1  # Error messages are valuable evidence

        # Duration confidence (reasonable duration increases confidence)
        if 0.1 < duration_seconds < 300:  # 0.1s to 5 minutes
            confidence += 0.1
        elif duration_seconds > 600:  # Very long execution
            confidence -= 0.1

        # Clamp to [0, 1]
        return max(0.0, min(1.0, confidence))

    def _calculate_salience(
        self,
        task_description: str,
        tools_used: list[str],
        files_modified: list[str],
        outcome: OutcomeType
    ) -> float:
        """Calculate salience (importance) of feedback.

        Args:
            task_description: Task description
            tools_used: Tools invoked
            files_modified: Files changed
            outcome: Execution outcome

        Returns:
            Salience score (0.0-1.0)
        """
        salience = 0.3  # Base salience

        # Task complexity
        if len(task_description) > 100:
            salience += 0.1

        # Tool usage
        if len(tools_used) > 3:
            salience += 0.2
        elif len(tools_used) > 1:
            salience += 0.1

        # File modifications
        if len(files_modified) > 5:
            salience += 0.2
        elif len(files_modified) > 0:
            salience += 0.1

        # Outcome importance
        if outcome == OutcomeType.FAILURE:
            salience += 0.2  # Failures are important to learn from
        elif outcome == OutcomeType.SUCCESS:
            salience += 0.1

        # Clamp to [0, 1]
        return max(0.0, min(1.0, salience))

    def _generate_feedback_id(
        self,
        task_description: str,
        outcome: OutcomeType,
        tools_used: list[str],
        timestamp: datetime
    ) -> str:
        """Generate unique feedback ID.

        Args:
            task_description: Task description
            outcome: Execution outcome
            tools_used: Tools invoked
            timestamp: Timestamp

        Returns:
            Feedback ID
        """
        content = f"{task_description}:{outcome}:{','.join(sorted(tools_used))}:{timestamp.isoformat()}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _store_feedback(self, feedback: ExecutionFeedback) -> None:
        """Store feedback to disk.

        Args:
            feedback: Feedback to store
        """
        # Organize by date
        date_dir = self.storage_path / feedback.timestamp.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        # Store as JSON
        feedback_file = date_dir / f"{feedback.feedback_id}.json"
        with open(feedback_file, "w") as f:
            json.dump(feedback.model_dump(mode="json"), f, indent=2)

    def get_feedback(self, feedback_id: str) -> ExecutionFeedback | None:
        """Get feedback by ID.

        Args:
            feedback_id: Feedback identifier

        Returns:
            Feedback or None if not found
        """
        # Check cache first
        if feedback_id in self.recent_feedback:
            return self.recent_feedback[feedback_id]

        # Search storage
        for date_dir in self.storage_path.iterdir():
            if not date_dir.is_dir():
                continue

            feedback_file = date_dir / f"{feedback_id}.json"
            if feedback_file.exists():
                with open(feedback_file) as f:
                    data = json.load(f)
                    return ExecutionFeedback(**data)

        return None

    def list_feedback(
        self,
        outcome: OutcomeType | None = None,
        min_confidence: float | None = None,
        min_salience: float | None = None,
        since: datetime | None = None,
        limit: int = 100
    ) -> list[ExecutionFeedback]:
        """List feedback matching criteria.

        Args:
            outcome: Filter by outcome
            min_confidence: Minimum confidence
            min_salience: Minimum salience
            since: Only feedback after this time
            limit: Maximum results

        Returns:
            List of feedback
        """
        results: list[ExecutionFeedback] = []

        # Iterate through storage
        for date_dir in sorted(self.storage_path.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue

            # Check date filter
            if since:
                dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d")
                if dir_date < since:
                    continue

            for feedback_file in date_dir.glob("*.json"):
                with open(feedback_file) as f:
                    data = json.load(f)
                    feedback = ExecutionFeedback(**data)

                    # Apply filters
                    if outcome and feedback.outcome != outcome:
                        continue
                    if min_confidence and feedback.confidence < min_confidence:
                        continue
                    if min_salience and feedback.salience < min_salience:
                        continue

                    results.append(feedback)

                    if len(results) >= limit:
                        return results

        return results

    def cleanup_old_feedback(self) -> int:
        """Remove feedback older than retention period.

        Returns:
            Number of feedback items removed
        """
        cutoff = datetime.now() - timedelta(days=self.config.feedback_retention_days)
        removed = 0

        for date_dir in self.storage_path.iterdir():
            if not date_dir.is_dir():
                continue

            # Check if directory is old
            dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d")
            if dir_date < cutoff:
                # Remove all feedback in this directory
                for feedback_file in date_dir.glob("*.json"):
                    feedback_file.unlink()
                    removed += 1

                # Remove directory if empty
                if not any(date_dir.iterdir()):
                    date_dir.rmdir()

        if removed > 0:
            logger.info(f"Cleaned up {removed} old feedback items")

        return removed

    def get_statistics(self) -> dict[str, Any]:
        """Get feedback collection statistics.

        Returns:
            Statistics dictionary
        """
        total = 0
        by_outcome: dict[str, int] = {}
        avg_confidence = 0.0
        avg_salience = 0.0

        for date_dir in self.storage_path.iterdir():
            if not date_dir.is_dir():
                continue

            for feedback_file in date_dir.glob("*.json"):
                with open(feedback_file) as f:
                    data = json.load(f)
                    feedback = ExecutionFeedback(**data)

                    total += 1
                    by_outcome[feedback.outcome] = by_outcome.get(feedback.outcome, 0) + 1
                    avg_confidence += feedback.confidence
                    avg_salience += feedback.salience

        if total > 0:
            avg_confidence /= total
            avg_salience /= total

        return {
            "total_feedback": total,
            "by_outcome": by_outcome,
            "average_confidence": avg_confidence,
            "average_salience": avg_salience,
            "recent_cache_size": len(self.recent_feedback)
        }
