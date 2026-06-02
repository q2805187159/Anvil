"""Auto-forget for intelligent memory cleanup."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .contracts import MemoryEvolutionConfig, MemoryToForget

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AutoForget:
    """Handles intelligent memory cleanup.

    Based on agentmemory auto-forget pattern:
    - TTL-based expiration
    - Contradiction detection
    - Low-value cleanup
    - Safe deletion (never lose critical info)
    """

    def __init__(self, config: MemoryEvolutionConfig):
        """Initialize auto-forget.

        Args:
            config: Memory evolution configuration
        """
        self.config = config

    def should_run(self, hours_since_last: float) -> bool:
        """Check if auto-forget should run.

        Args:
            hours_since_last: Hours since last run

        Returns:
            True if should run
        """
        if not self.config.auto_forget_enabled:
            return False

        return hours_since_last >= self.config.auto_forget_interval_hours

    def identify_expired_memories(
        self,
        memories: list[dict],
        current_time: datetime | None = None
    ) -> list[MemoryToForget]:
        """Identify memories that have exceeded their TTL.

        Args:
            memories: List of memories to check
            current_time: Current time (defaults to now)

        Returns:
            List of memories to forget
        """
        if current_time is None:
            current_time = datetime.now()

        to_forget = []
        ttl_days = self.config.default_ttl_days

        for memory in memories:
            # Get creation time
            created_at_str = memory.get("created_at")
            if not created_at_str:
                continue

            try:
                created_at = datetime.fromisoformat(created_at_str)
            except (ValueError, TypeError):
                continue

            # Check if expired
            age = current_time - created_at
            if age.days > ttl_days:
                # Check if memory is critical (high importance)
                importance = memory.get("importance", 0.5)
                if importance < 0.8:  # Don't auto-expire critical memories
                    to_forget.append(MemoryToForget(
                        memory_id=memory.get("memory_id", ""),
                        reason="expired",
                        confidence=1.0
                    ))

        logger.info(f"Found {len(to_forget)} expired memories")
        return to_forget

    def detect_contradictions(
        self,
        memories: list[dict],
        similarity_threshold: float | None = None
    ) -> list[MemoryToForget]:
        """Detect contradicting memories.

        Args:
            memories: List of memories to check
            similarity_threshold: Threshold for contradiction detection

        Returns:
            List of memories to forget
        """
        if similarity_threshold is None:
            similarity_threshold = self.config.contradiction_threshold

        to_forget = []

        # Group memories by type
        by_type = {}
        for memory in memories:
            mem_type = memory.get("type", "unknown")
            if mem_type not in by_type:
                by_type[mem_type] = []
            by_type[mem_type].append(memory)

        # Check for contradictions within each type
        for mem_type, type_memories in by_type.items():
            contradictions = self._find_contradictions_in_group(
                type_memories,
                similarity_threshold
            )
            to_forget.extend(contradictions)

        logger.info(f"Found {len(to_forget)} contradicting memories")
        return to_forget

    def _find_contradictions_in_group(
        self,
        memories: list[dict],
        threshold: float
    ) -> list[MemoryToForget]:
        """Find contradictions within a group of similar memories.

        Args:
            memories: Memories to check
            threshold: Similarity threshold

        Returns:
            List of memories to forget
        """
        to_forget = []

        # Sort by creation time (newest first)
        sorted_memories = sorted(
            memories,
            key=lambda m: m.get("created_at", ""),
            reverse=True
        )

        # Check for contradicting patterns
        for i, newer in enumerate(sorted_memories):
            newer_desc = newer.get("description", "").lower()

            for older in sorted_memories[i + 1:]:
                older_desc = older.get("description", "").lower()

                # Simple contradiction detection
                # Look for negation patterns
                if self._is_contradiction(newer_desc, older_desc):
                    # Keep newer, forget older
                    to_forget.append(MemoryToForget(
                        memory_id=older.get("memory_id", ""),
                        reason="contradiction",
                        confidence=threshold,
                        replaced_by=newer.get("memory_id")
                    ))

        return to_forget

    def _is_contradiction(self, text1: str, text2: str) -> bool:
        """Check if two texts contradict each other.

        Args:
            text1: First text
            text2: Second text

        Returns:
            True if contradiction detected
        """
        # Simple heuristic: look for negation patterns
        negation_words = ["not", "no", "never", "don't", "doesn't", "didn't"]

        # Extract key terms (simple approach)
        words1 = set(text1.split())
        words2 = set(text2.split())

        # Check if one has negation and they share key terms
        has_negation1 = any(neg in words1 for neg in negation_words)
        has_negation2 = any(neg in words2 for neg in negation_words)

        if has_negation1 != has_negation2:
            # One has negation, other doesn't
            # Check if they share significant terms
            common_terms = words1 & words2
            if len(common_terms) >= 3:
                return True

        return False

    def identify_low_value_memories(
        self,
        memories: list[dict],
        threshold: float | None = None
    ) -> list[MemoryToForget]:
        """Identify low-value memories for cleanup.

        Args:
            memories: List of memories to check
            threshold: Value threshold

        Returns:
            List of memories to forget
        """
        if threshold is None:
            threshold = self.config.low_value_threshold

        to_forget = []

        for memory in memories:
            # Calculate value score
            value_score = self._calculate_value_score(memory)

            if value_score < threshold:
                to_forget.append(MemoryToForget(
                    memory_id=memory.get("memory_id", ""),
                    reason="low_value",
                    confidence=1.0 - value_score
                ))

        logger.info(f"Found {len(to_forget)} low-value memories")
        return to_forget

    def _calculate_value_score(self, memory: dict) -> float:
        """Calculate value score for a memory.

        Args:
            memory: Memory to score

        Returns:
            Value score (0.0-1.0)
        """
        score = 0.5  # Base score

        # Importance contributes to value
        importance = memory.get("importance", 0.5)
        score += importance * 0.3

        # Reuse count contributes to value
        reuse_count = memory.get("reuse_count", 0)
        if reuse_count > 0:
            score += min(reuse_count * 0.1, 0.3)

        # Confidence contributes to value (for patterns)
        confidence = memory.get("confidence", 0.5)
        score += confidence * 0.2

        # Recency contributes to value
        created_at_str = memory.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
                age_days = (datetime.now() - created_at).days
                recency_score = max(0, 1.0 - (age_days / 30))  # Decay over 30 days
                score += recency_score * 0.2
            except (ValueError, TypeError):
                pass

        return min(score, 1.0)

    def filter_safe_to_delete(
        self,
        to_forget: list[MemoryToForget],
        memories: list[dict]
    ) -> list[MemoryToForget]:
        """Filter out memories that are not safe to delete.

        Args:
            to_forget: Candidate memories to forget
            memories: All memories (for context)

        Returns:
            Filtered list of safe deletions
        """
        safe_to_delete = []
        memory_by_id = {m.get("memory_id"): m for m in memories}

        for candidate in to_forget:
            memory = memory_by_id.get(candidate.memory_id)
            if not memory:
                continue

            # Never delete critical memories
            importance = memory.get("importance", 0.5)
            if importance >= 0.9:
                logger.debug(f"Skipping critical memory: {candidate.memory_id}")
                continue

            # Never delete recently used memories
            reuse_count = memory.get("reuse_count", 0)
            if reuse_count > 10:
                logger.debug(f"Skipping frequently used memory: {candidate.memory_id}")
                continue

            # Safe to delete
            safe_to_delete.append(candidate)

        logger.info(f"Filtered to {len(safe_to_delete)} safe deletions")
        return safe_to_delete

    def run_cleanup(
        self,
        memories: list[dict],
        current_time: datetime | None = None
    ) -> list[MemoryToForget]:
        """Run complete cleanup process.

        Args:
            memories: All memories to analyze
            current_time: Current time (defaults to now)

        Returns:
            List of memories to forget
        """
        all_to_forget = []

        # Find expired memories
        expired = self.identify_expired_memories(memories, current_time)
        all_to_forget.extend(expired)

        # Detect contradictions
        contradictions = self.detect_contradictions(memories)
        all_to_forget.extend(contradictions)

        # Find low-value memories
        low_value = self.identify_low_value_memories(memories)
        all_to_forget.extend(low_value)

        # Deduplicate by memory_id
        seen = set()
        unique_to_forget = []
        for item in all_to_forget:
            if item.memory_id not in seen:
                seen.add(item.memory_id)
                unique_to_forget.append(item)

        # Filter for safety
        safe_to_delete = self.filter_safe_to_delete(unique_to_forget, memories)

        logger.info(f"Cleanup complete: {len(safe_to_delete)} memories to forget")
        return safe_to_delete
