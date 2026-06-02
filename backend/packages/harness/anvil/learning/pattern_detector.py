"""Pattern detection and recognition."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .contracts import ExecutionFeedback, LearningConfig, OutcomeType, Pattern, PatternType

logger = logging.getLogger(__name__)


class PatternDetector:
    """Detects recurring patterns in execution history.

    Features:
    - Tool sequence detection
    - File workflow recognition
    - Error recovery patterns
    - Context configuration patterns
    - Frequency and success rate tracking
    """

    def __init__(self, config: LearningConfig):
        """Initialize pattern detector.

        Args:
            config: Learning configuration
        """
        self.config = config
        self.storage_path = Path(config.pattern_storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Pattern cache
        self.patterns: dict[str, Pattern] = {}
        self._load_patterns()

    def detect_patterns(
        self,
        feedback_history: list[ExecutionFeedback]
    ) -> list[Pattern]:
        """Detect patterns from feedback history.

        Args:
            feedback_history: List of execution feedback

        Returns:
            List of detected patterns
        """
        detected: list[Pattern] = []

        # Detect tool sequence patterns
        detected.extend(self._detect_tool_sequences(feedback_history))

        # Detect file workflow patterns
        detected.extend(self._detect_file_workflows(feedback_history))

        # Detect error recovery patterns
        detected.extend(self._detect_error_recovery(feedback_history))

        # Detect context patterns
        detected.extend(self._detect_context_patterns(feedback_history))

        # Update existing patterns or create new ones
        for pattern in detected:
            self._update_or_create_pattern(pattern)

        return detected

    def _detect_tool_sequences(
        self,
        feedback_history: list[ExecutionFeedback]
    ) -> list[Pattern]:
        """Detect tool sequence patterns.

        Args:
            feedback_history: Feedback history

        Returns:
            Detected tool sequence patterns
        """
        patterns: list[Pattern] = []

        # Group by successful executions
        successful = [
            fb for fb in feedback_history
            if fb.outcome == OutcomeType.SUCCESS and len(fb.tools_used) >= 2
        ]

        # Count tool sequences
        sequence_counter: Counter[tuple[str, ...]] = Counter()
        sequence_feedback: defaultdict[tuple[str, ...], list[str]] = defaultdict(list)

        for fb in successful:
            sequence = tuple(fb.tools_used)
            sequence_counter[sequence] += 1
            sequence_feedback[sequence].append(fb.feedback_id)

        # Create patterns for frequent sequences
        for sequence, count in sequence_counter.items():
            if count >= self.config.min_pattern_frequency:
                # Calculate success rate
                total_with_sequence = sum(
                    1 for fb in feedback_history
                    if tuple(fb.tools_used) == sequence
                )
                success_rate = count / total_with_sequence if total_with_sequence > 0 else 0.0

                if success_rate >= self.config.min_pattern_success_rate:
                    pattern = self._create_tool_sequence_pattern(
                        sequence=list(sequence),
                        frequency=count,
                        success_rate=success_rate,
                        evidence_ids=sequence_feedback[sequence],
                        feedback_history=feedback_history
                    )
                    patterns.append(pattern)

        return patterns

    def _detect_file_workflows(
        self,
        feedback_history: list[ExecutionFeedback]
    ) -> list[Pattern]:
        """Detect file workflow patterns.

        Args:
            feedback_history: Feedback history

        Returns:
            Detected file workflow patterns
        """
        patterns: list[Pattern] = []

        # Group by file modifications
        successful = [
            fb for fb in feedback_history
            if fb.outcome == OutcomeType.SUCCESS and fb.files_modified
        ]

        # Extract file patterns (extensions, directories)
        file_pattern_counter: Counter[str] = Counter()
        pattern_feedback: defaultdict[str, list[str]] = defaultdict(list)

        for fb in successful:
            # Extract patterns from file paths
            for file_path in fb.files_modified:
                # Extension pattern
                if "." in file_path:
                    ext = file_path.split(".")[-1]
                    pattern_key = f"*.{ext}"
                    file_pattern_counter[pattern_key] += 1
                    pattern_feedback[pattern_key].append(fb.feedback_id)

                # Directory pattern
                if "/" in file_path:
                    dir_path = "/".join(file_path.split("/")[:-1])
                    pattern_key = f"{dir_path}/*"
                    file_pattern_counter[pattern_key] += 1
                    pattern_feedback[pattern_key].append(fb.feedback_id)

        # Create patterns for frequent file workflows
        for pattern_key, count in file_pattern_counter.items():
            if count >= self.config.min_pattern_frequency:
                pattern = self._create_file_workflow_pattern(
                    file_pattern=pattern_key,
                    frequency=count,
                    evidence_ids=pattern_feedback[pattern_key],
                    feedback_history=feedback_history
                )
                patterns.append(pattern)

        return patterns

    def _detect_error_recovery(
        self,
        feedback_history: list[ExecutionFeedback]
    ) -> list[Pattern]:
        """Detect error recovery patterns.

        Args:
            feedback_history: Feedback history

        Returns:
            Detected error recovery patterns
        """
        patterns: list[Pattern] = []

        # Find sequences: failure → success
        for i in range(len(feedback_history) - 1):
            current = feedback_history[i]
            next_fb = feedback_history[i + 1]

            if (current.outcome == OutcomeType.FAILURE and
                next_fb.outcome == OutcomeType.SUCCESS and
                current.errors):

                # This is a recovery pattern
                pattern = self._create_error_recovery_pattern(
                    error_feedback=current,
                    recovery_feedback=next_fb,
                    feedback_history=feedback_history
                )
                patterns.append(pattern)

        return patterns

    def _detect_context_patterns(
        self,
        feedback_history: list[ExecutionFeedback]
    ) -> list[Pattern]:
        """Detect context configuration patterns.

        Args:
            feedback_history: Feedback history

        Returns:
            Detected context patterns
        """
        patterns: list[Pattern] = []

        # Group by successful executions with context
        successful = [
            fb for fb in feedback_history
            if fb.outcome == OutcomeType.SUCCESS and fb.context_snapshot
        ]

        # Extract common context configurations
        context_counter: Counter[str] = Counter()
        context_feedback: defaultdict[str, list[str]] = defaultdict(list)

        for fb in successful:
            # Create context signature
            context_sig = self._create_context_signature(fb.context_snapshot)
            context_counter[context_sig] += 1
            context_feedback[context_sig].append(fb.feedback_id)

        # Create patterns for frequent contexts
        for context_sig, count in context_counter.items():
            if count >= self.config.min_pattern_frequency:
                pattern = self._create_context_pattern(
                    context_signature=context_sig,
                    frequency=count,
                    evidence_ids=context_feedback[context_sig],
                    feedback_history=feedback_history
                )
                patterns.append(pattern)

        return patterns

    def _create_tool_sequence_pattern(
        self,
        sequence: list[str],
        frequency: int,
        success_rate: float,
        evidence_ids: list[str],
        feedback_history: list[ExecutionFeedback]
    ) -> Pattern:
        """Create tool sequence pattern.

        Args:
            sequence: Tool sequence
            frequency: Occurrence count
            success_rate: Success rate
            evidence_ids: Supporting evidence
            feedback_history: Full feedback history

        Returns:
            Pattern
        """
        signature = self._generate_signature(f"tool_seq:{','.join(sequence)}")

        # Calculate recency score
        recency_score = self._calculate_recency_score(evidence_ids, feedback_history)

        # Calculate overall strength
        strength = self._calculate_pattern_strength(
            frequency=frequency,
            success_rate=success_rate,
            recency_score=recency_score
        )

        # Get temporal bounds
        first_seen, last_seen = self._get_temporal_bounds(evidence_ids, feedback_history)

        return Pattern(
            pattern_id=signature,
            pattern_type=PatternType.TOOL_SEQUENCE,
            signature=signature,
            description=f"Tool sequence: {' → '.join(sequence)}",
            tool_sequence=sequence,
            frequency=frequency,
            success_rate=success_rate,
            confidence=min(0.9, success_rate * (1 + frequency * 0.05)),
            strength=strength,
            first_seen=first_seen,
            last_seen=last_seen,
            recency_score=recency_score,
            evidence_ids=evidence_ids
        )

    def _create_file_workflow_pattern(
        self,
        file_pattern: str,
        frequency: int,
        evidence_ids: list[str],
        feedback_history: list[ExecutionFeedback]
    ) -> Pattern:
        """Create file workflow pattern.

        Args:
            file_pattern: File pattern
            frequency: Occurrence count
            evidence_ids: Supporting evidence
            feedback_history: Full feedback history

        Returns:
            Pattern
        """
        signature = self._generate_signature(f"file_workflow:{file_pattern}")

        # Calculate success rate
        total = len([fb for fb in feedback_history if any(file_pattern.replace("*", "") in f for f in fb.files_modified)])
        success_rate = frequency / total if total > 0 else 0.0

        recency_score = self._calculate_recency_score(evidence_ids, feedback_history)
        strength = self._calculate_pattern_strength(frequency, success_rate, recency_score)
        first_seen, last_seen = self._get_temporal_bounds(evidence_ids, feedback_history)

        return Pattern(
            pattern_id=signature,
            pattern_type=PatternType.FILE_WORKFLOW,
            signature=signature,
            description=f"File workflow: {file_pattern}",
            file_patterns=[file_pattern],
            frequency=frequency,
            success_rate=success_rate,
            confidence=min(0.85, success_rate * (1 + frequency * 0.03)),
            strength=strength,
            first_seen=first_seen,
            last_seen=last_seen,
            recency_score=recency_score,
            evidence_ids=evidence_ids
        )

    def _create_error_recovery_pattern(
        self,
        error_feedback: ExecutionFeedback,
        recovery_feedback: ExecutionFeedback,
        feedback_history: list[ExecutionFeedback]
    ) -> Pattern:
        """Create error recovery pattern.

        Args:
            error_feedback: Failure feedback
            recovery_feedback: Recovery feedback
            feedback_history: Full feedback history

        Returns:
            Pattern
        """
        error_sig = error_feedback.errors[0] if error_feedback.errors else "unknown"
        recovery_tools = recovery_feedback.tools_used

        signature = self._generate_signature(f"recovery:{error_sig}:{','.join(recovery_tools)}")

        return Pattern(
            pattern_id=signature,
            pattern_type=PatternType.ERROR_RECOVERY,
            signature=signature,
            description=f"Recovery from '{error_sig[:50]}' using {', '.join(recovery_tools)}",
            tool_sequence=recovery_tools,
            frequency=1,  # Will be updated if pattern repeats
            success_rate=1.0,
            confidence=0.7,
            strength=0.7,
            first_seen=error_feedback.timestamp,
            last_seen=recovery_feedback.timestamp,
            recency_score=1.0,
            evidence_ids=[error_feedback.feedback_id, recovery_feedback.feedback_id]
        )

    def _create_context_pattern(
        self,
        context_signature: str,
        frequency: int,
        evidence_ids: list[str],
        feedback_history: list[ExecutionFeedback]
    ) -> Pattern:
        """Create context configuration pattern.

        Args:
            context_signature: Context signature
            frequency: Occurrence count
            evidence_ids: Supporting evidence
            feedback_history: Full feedback history

        Returns:
            Pattern
        """
        signature = self._generate_signature(f"context:{context_signature}")

        # Calculate success rate
        total = len([fb for fb in feedback_history if self._create_context_signature(fb.context_snapshot) == context_signature])
        success_rate = frequency / total if total > 0 else 0.0

        recency_score = self._calculate_recency_score(evidence_ids, feedback_history)
        strength = self._calculate_pattern_strength(frequency, success_rate, recency_score)
        first_seen, last_seen = self._get_temporal_bounds(evidence_ids, feedback_history)

        return Pattern(
            pattern_id=signature,
            pattern_type=PatternType.CONTEXT_CONFIG,
            signature=signature,
            description=f"Context configuration: {context_signature[:100]}",
            frequency=frequency,
            success_rate=success_rate,
            confidence=min(0.8, success_rate * (1 + frequency * 0.04)),
            strength=strength,
            first_seen=first_seen,
            last_seen=last_seen,
            recency_score=recency_score,
            evidence_ids=evidence_ids
        )

    def _generate_signature(self, content: str) -> str:
        """Generate pattern signature.

        Args:
            content: Content to hash

        Returns:
            Signature hash
        """
        return hashlib.sha256(content.encode()).hexdigest()

    def _create_context_signature(self, context: dict[str, Any]) -> str:
        """Create signature from context snapshot.

        Args:
            context: Context snapshot

        Returns:
            Context signature
        """
        # Extract key context elements
        keys = sorted(context.keys())
        return ",".join(keys)

    def _calculate_recency_score(
        self,
        evidence_ids: list[str],
        feedback_history: list[ExecutionFeedback]
    ) -> float:
        """Calculate recency score for pattern.

        Args:
            evidence_ids: Evidence feedback IDs
            feedback_history: Full feedback history

        Returns:
            Recency score (0.0-1.0)
        """
        if not evidence_ids:
            return 0.0

        # Find most recent evidence
        recent_timestamps = [
            fb.timestamp for fb in feedback_history
            if fb.feedback_id in evidence_ids
        ]

        if not recent_timestamps:
            return 0.0

        most_recent = max(recent_timestamps)
        age_days = (datetime.now() - most_recent).days

        # Exponential decay
        decay_rate = self.config.pattern_recency_weight
        recency = max(0.0, 1.0 - (age_days * decay_rate / 30))

        return recency

    def _calculate_pattern_strength(
        self,
        frequency: int,
        success_rate: float,
        recency_score: float
    ) -> float:
        """Calculate overall pattern strength.

        Args:
            frequency: Occurrence count
            success_rate: Success rate
            recency_score: Recency score

        Returns:
            Strength score (0.0-1.0)
        """
        # Weighted combination
        freq_score = min(1.0, frequency / 10.0)
        strength = (
            freq_score * 0.3 +
            success_rate * 0.5 +
            recency_score * 0.2
        )

        return min(1.0, strength)

    def _get_temporal_bounds(
        self,
        evidence_ids: list[str],
        feedback_history: list[ExecutionFeedback]
    ) -> tuple[datetime, datetime]:
        """Get first and last seen timestamps.

        Args:
            evidence_ids: Evidence feedback IDs
            feedback_history: Full feedback history

        Returns:
            (first_seen, last_seen)
        """
        timestamps = [
            fb.timestamp for fb in feedback_history
            if fb.feedback_id in evidence_ids
        ]

        if not timestamps:
            now = datetime.now()
            return now, now

        return min(timestamps), max(timestamps)

    def _update_or_create_pattern(self, pattern: Pattern) -> None:
        """Update existing pattern or create new one.

        Args:
            pattern: Pattern to update/create
        """
        if pattern.pattern_id in self.patterns:
            # Update existing
            existing = self.patterns[pattern.pattern_id]
            existing.frequency += pattern.frequency
            existing.last_seen = pattern.last_seen
            existing.evidence_ids.extend(pattern.evidence_ids)
            existing.updated_at = datetime.now()

            # Recalculate scores
            existing.recency_score = pattern.recency_score
            existing.strength = pattern.strength

            logger.debug(f"Updated pattern: {pattern.pattern_id[:8]}")
        else:
            # Create new
            self.patterns[pattern.pattern_id] = pattern
            logger.info(f"Created new pattern: {pattern.pattern_id[:8]} ({pattern.pattern_type})")

        # Store pattern
        self._store_pattern(self.patterns[pattern.pattern_id])

    def _store_pattern(self, pattern: Pattern) -> None:
        """Store pattern to disk.

        Args:
            pattern: Pattern to store
        """
        pattern_file = self.storage_path / f"{pattern.pattern_id}.json"
        with open(pattern_file, "w") as f:
            json.dump(pattern.model_dump(mode="json"), f, indent=2)

    def _load_patterns(self) -> None:
        """Load patterns from disk."""
        for pattern_file in self.storage_path.glob("*.json"):
            try:
                with open(pattern_file) as f:
                    data = json.load(f)
                    pattern = Pattern(**data)
                    self.patterns[pattern.pattern_id] = pattern
            except Exception as e:
                logger.warning(f"Failed to load pattern {pattern_file}: {e}")

        logger.info(f"Loaded {len(self.patterns)} patterns")

    def get_pattern(self, pattern_id: str) -> Pattern | None:
        """Get pattern by ID.

        Args:
            pattern_id: Pattern identifier

        Returns:
            Pattern or None
        """
        return self.patterns.get(pattern_id)

    def list_patterns(
        self,
        pattern_type: PatternType | None = None,
        min_strength: float | None = None,
        min_frequency: int | None = None,
        limit: int = 100
    ) -> list[Pattern]:
        """List patterns matching criteria.

        Args:
            pattern_type: Filter by type
            min_strength: Minimum strength
            min_frequency: Minimum frequency
            limit: Maximum results

        Returns:
            List of patterns
        """
        results = []

        for pattern in self.patterns.values():
            # Apply filters
            if pattern_type and pattern.pattern_type != pattern_type:
                continue
            if min_strength and pattern.strength < min_strength:
                continue
            if min_frequency and pattern.frequency < min_frequency:
                continue

            results.append(pattern)

        # Sort by strength
        results.sort(key=lambda p: p.strength, reverse=True)

        return results[:limit]

    def get_statistics(self) -> dict[str, Any]:
        """Get pattern detection statistics.

        Returns:
            Statistics dictionary
        """
        by_type: dict[str, int] = {}
        total_strength = 0.0

        for pattern in self.patterns.values():
            by_type[pattern.pattern_type] = by_type.get(pattern.pattern_type, 0) + 1
            total_strength += pattern.strength

        avg_strength = total_strength / len(self.patterns) if self.patterns else 0.0

        return {
            "total_patterns": len(self.patterns),
            "by_type": by_type,
            "average_strength": avg_strength
        }
