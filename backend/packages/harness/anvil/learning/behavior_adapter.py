"""Adaptive behavior based on learned patterns."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import (
    Adaptation,
    AdaptationType,
    FailureAnalysis,
    LearningConfig,
    Pattern,
)

logger = logging.getLogger(__name__)


class BehaviorAdapter:
    """Adapts runtime behavior based on learned patterns.

    Features:
    - Tool selection optimization
    - Context loading adjustments
    - Cache warming strategies
    - Error handling improvements
    - Timeout adjustments
    - Rollback support
    """

    def __init__(self, config: LearningConfig):
        """Initialize behavior adapter.

        Args:
            config: Learning configuration
        """
        self.config = config
        self.storage_path = Path(config.adaptation_storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Active adaptations
        self.adaptations: dict[str, Adaptation] = {}
        self._load_adaptations()

        # Adaptation application tracking
        self.session_adaptations_count = 0

    def propose_adaptation(
        self,
        adaptation_type: AdaptationType,
        description: str,
        before_value: Any,
        after_value: Any,
        trigger_pattern: Pattern | None = None,
        trigger_analysis: FailureAnalysis | None = None,
        confidence: float = 0.7
    ) -> Adaptation:
        """Propose a behavioral adaptation.

        Args:
            adaptation_type: Type of adaptation
            description: What is being adapted
            before_value: Current value
            after_value: Proposed value
            trigger_pattern: Pattern that triggered adaptation
            trigger_analysis: Failure analysis that triggered adaptation
            confidence: Confidence in adaptation

        Returns:
            Proposed adaptation
        """
        # Generate adaptation ID
        adaptation_id = self._generate_adaptation_id(
            adaptation_type=adaptation_type,
            description=description,
            after_value=after_value
        )

        # Check if adaptation already exists
        if adaptation_id in self.adaptations:
            logger.debug(f"Adaptation already exists: {adaptation_id[:8]}")
            return self.adaptations[adaptation_id]

        # Create adaptation
        adaptation = Adaptation(
            adaptation_id=adaptation_id,
            adaptation_type=adaptation_type,
            description=description,
            before_value=before_value,
            after_value=after_value,
            trigger_pattern_id=trigger_pattern.pattern_id if trigger_pattern else None,
            trigger_analysis_id=trigger_analysis.analysis_id if trigger_analysis else None,
            confidence=confidence,
            enabled=False  # Disabled by default, requires approval
        )

        # Store adaptation
        self._store_adaptation(adaptation)
        self.adaptations[adaptation_id] = adaptation

        logger.info(
            f"Proposed adaptation: {adaptation_id[:8]} "
            f"({adaptation_type}, confidence={confidence:.2f})"
        )

        return adaptation

    def apply_adaptation(
        self,
        adaptation_id: str,
        auto_enable: bool = False
    ) -> bool:
        """Apply an adaptation.

        Args:
            adaptation_id: Adaptation identifier
            auto_enable: Whether to auto-enable (requires config permission)

        Returns:
            True if applied successfully
        """
        adaptation = self.adaptations.get(adaptation_id)
        if not adaptation:
            logger.warning(f"Adaptation not found: {adaptation_id}")
            return False

        # Check if already enabled
        if adaptation.enabled:
            logger.debug(f"Adaptation already enabled: {adaptation_id[:8]}")
            return True

        # Check confidence threshold
        if adaptation.confidence < self.config.adaptation_confidence_threshold:
            logger.warning(
                f"Adaptation confidence too low: {adaptation.confidence:.2f} < "
                f"{self.config.adaptation_confidence_threshold}"
            )
            return False

        # Check automatic adaptation permission
        if auto_enable and not self.config.allow_automatic_adaptation:
            logger.warning("Automatic adaptation not allowed by configuration")
            return False

        # Check session limit
        if self.session_adaptations_count >= self.config.max_adaptations_per_session:
            logger.warning(
                f"Session adaptation limit reached: "
                f"{self.session_adaptations_count}/{self.config.max_adaptations_per_session}"
            )
            return False

        # Enable adaptation
        adaptation.enabled = True
        adaptation.updated_at = datetime.now()
        self._store_adaptation(adaptation)

        self.session_adaptations_count += 1

        logger.info(f"Applied adaptation: {adaptation_id[:8]} ({adaptation.adaptation_type})")

        return True

    def record_adaptation_outcome(
        self,
        adaptation_id: str,
        success: bool
    ) -> None:
        """Record outcome of adaptation application.

        Args:
            adaptation_id: Adaptation identifier
            success: Whether adaptation was successful
        """
        adaptation = self.adaptations.get(adaptation_id)
        if not adaptation:
            return

        adaptation.applied_count += 1
        if success:
            adaptation.success_count += 1
        else:
            adaptation.failure_count += 1

        adaptation.updated_at = datetime.now()
        self._store_adaptation(adaptation)

        success_rate = adaptation.success_count / adaptation.applied_count
        logger.info(
            f"Adaptation outcome: {adaptation_id[:8]} "
            f"(success={success}, rate={success_rate:.2f})"
        )

        # Disable if success rate drops too low
        if adaptation.applied_count >= 5 and success_rate < 0.5:
            logger.warning(
                f"Disabling low-performing adaptation: {adaptation_id[:8]} "
                f"(success_rate={success_rate:.2f})"
            )
            adaptation.enabled = False
            self._store_adaptation(adaptation)

    def rollback_adaptation(self, adaptation_id: str) -> bool:
        """Rollback an adaptation.

        Args:
            adaptation_id: Adaptation identifier

        Returns:
            True if rolled back successfully
        """
        adaptation = self.adaptations.get(adaptation_id)
        if not adaptation:
            logger.warning(f"Adaptation not found: {adaptation_id}")
            return False

        if not adaptation.rollback_available:
            logger.warning(f"Rollback not available: {adaptation_id[:8]}")
            return False

        # Disable adaptation
        adaptation.enabled = False
        adaptation.updated_at = datetime.now()
        self._store_adaptation(adaptation)

        logger.info(f"Rolled back adaptation: {adaptation_id[:8]}")

        return True

    def get_active_adaptations(
        self,
        adaptation_type: AdaptationType | None = None
    ) -> list[Adaptation]:
        """Get active adaptations.

        Args:
            adaptation_type: Filter by type

        Returns:
            List of active adaptations
        """
        results = []

        for adaptation in self.adaptations.values():
            if not adaptation.enabled:
                continue

            if adaptation_type and adaptation.adaptation_type != adaptation_type:
                continue

            results.append(adaptation)

        return results

    def get_tool_selection_adaptations(self) -> dict[str, Any]:
        """Get tool selection adaptations.

        Returns:
            Tool selection preferences
        """
        adaptations = self.get_active_adaptations(AdaptationType.TOOL_SELECTION)

        preferences: dict[str, Any] = {}
        for adaptation in adaptations:
            # Extract tool preferences from adaptation
            if isinstance(adaptation.after_value, dict):
                preferences.update(adaptation.after_value)

        return preferences

    def get_context_loading_adaptations(self) -> dict[str, Any]:
        """Get context loading adaptations.

        Returns:
            Context loading preferences
        """
        adaptations = self.get_active_adaptations(AdaptationType.CONTEXT_LOADING)

        preferences: dict[str, Any] = {}
        for adaptation in adaptations:
            if isinstance(adaptation.after_value, dict):
                preferences.update(adaptation.after_value)

        return preferences

    def get_cache_warming_adaptations(self) -> dict[str, Any]:
        """Get cache warming adaptations.

        Returns:
            Cache warming preferences
        """
        adaptations = self.get_active_adaptations(AdaptationType.CACHE_WARMING)

        preferences: dict[str, Any] = {}
        for adaptation in adaptations:
            if isinstance(adaptation.after_value, dict):
                preferences.update(adaptation.after_value)

        return preferences

    def get_error_handling_adaptations(self) -> dict[str, Any]:
        """Get error handling adaptations.

        Returns:
            Error handling strategies
        """
        adaptations = self.get_active_adaptations(AdaptationType.ERROR_HANDLING)

        strategies: dict[str, Any] = {}
        for adaptation in adaptations:
            if isinstance(adaptation.after_value, dict):
                strategies.update(adaptation.after_value)

        return strategies

    def get_timeout_adaptations(self) -> dict[str, float]:
        """Get timeout adaptations.

        Returns:
            Timeout adjustments by operation
        """
        adaptations = self.get_active_adaptations(AdaptationType.TIMEOUT_ADJUSTMENT)

        timeouts: dict[str, float] = {}
        for adaptation in adaptations:
            if isinstance(adaptation.after_value, dict):
                timeouts.update(adaptation.after_value)

        return timeouts

    def _generate_adaptation_id(
        self,
        adaptation_type: AdaptationType,
        description: str,
        after_value: Any
    ) -> str:
        """Generate adaptation ID.

        Args:
            adaptation_type: Adaptation type
            description: Description
            after_value: After value

        Returns:
            Adaptation ID
        """
        content = f"{adaptation_type}:{description}:{str(after_value)}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _store_adaptation(self, adaptation: Adaptation) -> None:
        """Store adaptation to disk.

        Args:
            adaptation: Adaptation to store
        """
        adaptation_file = self.storage_path / f"{adaptation.adaptation_id}.json"
        with open(adaptation_file, "w") as f:
            json.dump(adaptation.model_dump(mode="json"), f, indent=2)

    def _load_adaptations(self) -> None:
        """Load adaptations from disk."""
        for adaptation_file in self.storage_path.glob("*.json"):
            try:
                with open(adaptation_file) as f:
                    data = json.load(f)
                    adaptation = Adaptation(**data)
                    self.adaptations[adaptation.adaptation_id] = adaptation
            except Exception as e:
                logger.warning(f"Failed to load adaptation {adaptation_file}: {e}")

        logger.info(f"Loaded {len(self.adaptations)} adaptations")

    def get_adaptation(self, adaptation_id: str) -> Adaptation | None:
        """Get adaptation by ID.

        Args:
            adaptation_id: Adaptation identifier

        Returns:
            Adaptation or None
        """
        return self.adaptations.get(adaptation_id)

    def list_adaptations(
        self,
        adaptation_type: AdaptationType | None = None,
        enabled_only: bool = False,
        min_confidence: float | None = None,
        limit: int = 100
    ) -> list[Adaptation]:
        """List adaptations matching criteria.

        Args:
            adaptation_type: Filter by type
            enabled_only: Only enabled adaptations
            min_confidence: Minimum confidence
            limit: Maximum results

        Returns:
            List of adaptations
        """
        results = []

        for adaptation in self.adaptations.values():
            # Apply filters
            if adaptation_type and adaptation.adaptation_type != adaptation_type:
                continue
            if enabled_only and not adaptation.enabled:
                continue
            if min_confidence and adaptation.confidence < min_confidence:
                continue

            results.append(adaptation)

        # Sort by confidence
        results.sort(key=lambda a: a.confidence, reverse=True)

        return results[:limit]

    def get_statistics(self) -> dict[str, Any]:
        """Get adaptation statistics.

        Returns:
            Statistics dictionary
        """
        by_type: dict[str, int] = {}
        enabled_count = 0
        total_applied = 0
        total_success = 0

        for adaptation in self.adaptations.values():
            by_type[adaptation.adaptation_type] = by_type.get(adaptation.adaptation_type, 0) + 1

            if adaptation.enabled:
                enabled_count += 1

            total_applied += adaptation.applied_count
            total_success += adaptation.success_count

        success_rate = total_success / total_applied if total_applied > 0 else 0.0

        return {
            "total_adaptations": len(self.adaptations),
            "by_type": by_type,
            "enabled_count": enabled_count,
            "total_applied": total_applied,
            "total_success": total_success,
            "success_rate": success_rate,
            "session_count": self.session_adaptations_count
        }
