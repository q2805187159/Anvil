"""Consolidation service for pattern detection and memory consolidation."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from .consolidator import Consolidator
from .contracts import ConsolidatedPattern, MemoryEvolutionConfig

if TYPE_CHECKING:
    from ..stores.base import MemoryStore

logger = logging.getLogger(__name__)


class ConsolidationService:
    """Service for managing memory consolidation.

    Responsibilities:
    - Track observations over time
    - Trigger periodic consolidation
    - Detect and store patterns
    - Update profile facets
    - Coordinate with memory platform
    """

    def __init__(
        self,
        config: MemoryEvolutionConfig,
        memory_store: MemoryStore
    ):
        """Initialize consolidation service.

        Args:
            config: Memory evolution configuration
            memory_store: Memory store for persisting patterns
        """
        self.config = config
        self.memory_store = memory_store
        self.consolidator = Consolidator(config)

        self.observations: list[dict] = []
        self.patterns: dict[str, ConsolidatedPattern] = {}
        self.turns_since_consolidation = 0
        self.last_consolidation: datetime | None = None

    def add_observation(
        self,
        observation_type: str,
        content: str,
        metadata: dict | None = None
    ) -> None:
        """Add observation for future consolidation.

        Args:
            observation_type: Type of observation (tool_call, reasoning, etc.)
            content: Observation content
            metadata: Optional metadata
        """
        if not self.config.consolidation_enabled:
            return

        observation = {
            "type": observation_type,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **(metadata or {})
        }

        self.observations.append(observation)
        logger.debug(f"Added observation: {observation_type}")

    def on_turn_complete(self) -> list[ConsolidatedPattern]:
        """Called after each turn completes.

        Returns:
            List of new patterns if consolidation ran, empty list otherwise
        """
        if not self.config.consolidation_enabled:
            return []

        self.turns_since_consolidation += 1

        # Check if should consolidate
        if not self.consolidator.should_consolidate(
            len(self.observations),
            self.turns_since_consolidation
        ):
            return []

        # Run consolidation
        return self.consolidate()

    def consolidate(self) -> list[ConsolidatedPattern]:
        """Run consolidation on accumulated observations.

        Returns:
            List of detected patterns
        """
        if not self.observations:
            logger.debug("No observations to consolidate")
            return []

        try:
            # Get existing patterns
            existing_patterns = list(self.patterns.values())

            # Run consolidation
            new_patterns = self.consolidator.consolidate(
                self.observations,
                existing_patterns
            )

            # Store new/updated patterns
            for pattern in new_patterns:
                self._store_pattern(pattern)
                self.patterns[pattern.pattern_id] = pattern

            # Clear observations after successful consolidation
            self.observations.clear()
            self.turns_since_consolidation = 0
            self.last_consolidation = datetime.now()

            logger.info(f"Consolidation complete: {len(new_patterns)} patterns")
            return new_patterns

        except Exception as e:
            logger.error(f"Consolidation failed: {e}")
            return []

    def _store_pattern(self, pattern: ConsolidatedPattern) -> None:
        """Store consolidated pattern in memory platform.

        Args:
            pattern: Pattern to store
        """
        # Convert to memory platform format
        memory_content = {
            "type": "consolidated_pattern",
            "pattern_type": pattern.pattern_type,
            "description": pattern.description,
            "evidence": pattern.evidence,
            "confidence": pattern.confidence,
            "created_at": pattern.created_at.isoformat(),
            "last_updated": pattern.last_updated.isoformat()
        }

        # Store with confidence as importance
        self.memory_store.add(
            content=str(memory_content),
            metadata={
                "pattern_id": pattern.pattern_id,
                "type": "consolidated",
                "pattern_type": pattern.pattern_type,
                "confidence": pattern.confidence
            }
        )

    def get_patterns_by_type(self, pattern_type: str) -> list[ConsolidatedPattern]:
        """Get patterns of specific type.

        Args:
            pattern_type: Type of pattern (preference, architecture, workflow, bug)

        Returns:
            List of matching patterns
        """
        return [
            p for p in self.patterns.values()
            if p.pattern_type == pattern_type
        ]

    def get_high_confidence_patterns(self, threshold: float = 0.7) -> list[ConsolidatedPattern]:
        """Get patterns above confidence threshold.

        Args:
            threshold: Minimum confidence (0.0-1.0)

        Returns:
            List of high-confidence patterns
        """
        return [
            p for p in self.patterns.values()
            if p.confidence >= threshold
        ]

    def get_pattern(self, pattern_id: str) -> ConsolidatedPattern | None:
        """Get pattern by ID.

        Args:
            pattern_id: Pattern ID

        Returns:
            Pattern or None
        """
        return self.patterns.get(pattern_id)

    def clear_low_confidence_patterns(self, threshold: float = 0.3) -> int:
        """Remove patterns below confidence threshold.

        Args:
            threshold: Minimum confidence to keep

        Returns:
            Number of patterns removed
        """
        to_remove = [
            pid for pid, pattern in self.patterns.items()
            if pattern.confidence < threshold
        ]

        for pid in to_remove:
            del self.patterns[pid]
            logger.debug(f"Removed low-confidence pattern: {pid}")

        return len(to_remove)

    def get_statistics(self) -> dict:
        """Get consolidation statistics.

        Returns:
            Statistics dictionary
        """
        pattern_types = {}
        for pattern in self.patterns.values():
            pattern_types[pattern.pattern_type] = pattern_types.get(pattern.pattern_type, 0) + 1

        return {
            "total_patterns": len(self.patterns),
            "pattern_types": pattern_types,
            "pending_observations": len(self.observations),
            "turns_since_consolidation": self.turns_since_consolidation,
            "last_consolidation": self.last_consolidation.isoformat() if self.last_consolidation else None,
            "avg_confidence": sum(p.confidence for p in self.patterns.values()) / len(self.patterns) if self.patterns else 0.0
        }
