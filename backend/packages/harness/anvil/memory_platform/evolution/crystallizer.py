"""Crystallizer for converting action chains into reusable patterns."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from .contracts import Action, ActionChain, CrystallizedMemory

if TYPE_CHECKING:
    from .contracts import MemoryEvolutionConfig

logger = logging.getLogger(__name__)


class Crystallizer:
    """Converts action chains into crystallized memories.

    Based on agentmemory crystallization pattern:
    - Tracks action sequences
    - Identifies successful patterns
    - Extracts key results and lessons
    - Creates reusable memories
    """

    def __init__(self, config: MemoryEvolutionConfig):
        """Initialize crystallizer.

        Args:
            config: Memory evolution configuration
        """
        self.config = config
        self.active_chains: dict[str, ActionChain] = {}

    def should_crystallize(self, chain: ActionChain) -> bool:
        """Check if chain should be crystallized.

        Args:
            chain: Action chain to check

        Returns:
            True if should crystallize
        """
        if not self.config.crystallization_enabled:
            return False

        # Must have minimum actions
        if len(chain.actions) < self.config.min_actions_for_crystallization:
            return False

        # Must be successful
        if not chain.success:
            return False

        return True

    def crystallize(self, chain: ActionChain) -> CrystallizedMemory:
        """Crystallize action chain into memory.

        Args:
            chain: Action chain to crystallize

        Returns:
            Crystallized memory
        """
        # Extract narrative
        narrative = self._extract_narrative(chain)

        # Extract key results
        key_results = self._extract_key_results(chain)

        # Extract lessons
        lessons = self._extract_lessons(chain)

        # Extract files and tools
        files = self._extract_files(chain)
        tools = self._extract_tools(chain)

        # Generate pattern signature
        signature = self._generate_signature(chain)

        # Calculate importance
        importance = self._calculate_importance(chain)

        memory = CrystallizedMemory(
            memory_id=str(uuid4()),
            narrative=narrative,
            key_results=key_results,
            lessons_learned=lessons,
            files_touched=files,
            tools_used=tools,
            pattern_signature=signature,
            importance=importance
        )

        logger.info(f"Crystallized memory: {memory.narrative[:100]}")
        return memory

    def _extract_narrative(self, chain: ActionChain) -> str:
        """Extract narrative summary from chain.

        Args:
            chain: Action chain

        Returns:
            Narrative summary
        """
        # Simple implementation - can be enhanced with LLM
        task = chain.task_description or "Task"
        action_count = len(chain.actions)
        return f"{task} completed successfully using {action_count} actions"

    def _extract_key_results(self, chain: ActionChain) -> list[str]:
        """Extract key results from chain.

        Args:
            chain: Action chain

        Returns:
            List of key results
        """
        results = []

        # Extract from result actions
        for action in chain.actions:
            if action.action_type.value == "result":
                # Extract first sentence or first 100 chars
                content = action.content[:100]
                if content:
                    results.append(content)

        return results[:5]  # Limit to top 5

    def _extract_lessons(self, chain: ActionChain) -> list[str]:
        """Extract lessons learned from chain.

        Args:
            chain: Action chain

        Returns:
            List of lessons
        """
        lessons = []

        # Pattern: successful tool usage
        tools_used = self._extract_tools(chain)
        if tools_used:
            lessons.append(f"Successfully used tools: {', '.join(tools_used[:3])}")

        # Pattern: file operations
        files = self._extract_files(chain)
        if files:
            lessons.append(f"Worked with files: {', '.join(files[:3])}")

        return lessons

    def _extract_files(self, chain: ActionChain) -> list[str]:
        """Extract files touched in chain.

        Args:
            chain: Action chain

        Returns:
            List of file paths
        """
        files = set()

        for action in chain.actions:
            # Extract file paths from metadata
            if "file_path" in action.metadata:
                files.add(action.metadata["file_path"])

        return list(files)

    def _extract_tools(self, chain: ActionChain) -> list[str]:
        """Extract tools used in chain.

        Args:
            chain: Action chain

        Returns:
            List of tool names
        """
        tools = set()

        for action in chain.actions:
            if action.action_type.value == "tool_call":
                # Extract tool name from metadata
                if "tool_name" in action.metadata:
                    tools.add(action.metadata["tool_name"])

        return list(tools)

    def _generate_signature(self, chain: ActionChain) -> str:
        """Generate pattern signature for similarity matching.

        Args:
            chain: Action chain

        Returns:
            Pattern signature (hash)
        """
        # Create signature from action types and tools
        signature_parts = []

        for action in chain.actions:
            signature_parts.append(action.action_type.value)
            if "tool_name" in action.metadata:
                signature_parts.append(action.metadata["tool_name"])

        signature_str = "|".join(signature_parts)
        return hashlib.md5(signature_str.encode()).hexdigest()

    def _calculate_importance(self, chain: ActionChain) -> float:
        """Calculate importance score for memory.

        Args:
            chain: Action chain

        Returns:
            Importance score (0.0-1.0)
        """
        score = 0.5  # Base score

        # More actions = more important
        if len(chain.actions) > 5:
            score += 0.1

        # File operations = more important
        if self._extract_files(chain):
            score += 0.2

        # Multiple tools = more important
        if len(self._extract_tools(chain)) > 2:
            score += 0.2

        return min(score, 1.0)
