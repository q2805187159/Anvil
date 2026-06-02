"""Crystallization service for coordinating memory crystallization."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from .contracts import Action, ActionChain, ActionType, MemoryEvolutionConfig
from .crystallizer import Crystallizer

if TYPE_CHECKING:
    from ..stores.base import MemoryStore

logger = logging.getLogger(__name__)


class CrystallizationService:
    """Service for managing action chain crystallization.

    Responsibilities:
    - Track active action chains
    - Trigger crystallization on task completion
    - Store crystallized memories
    - Coordinate with memory platform
    """

    def __init__(
        self,
        config: MemoryEvolutionConfig,
        memory_store: MemoryStore
    ):
        """Initialize crystallization service.

        Args:
            config: Memory evolution configuration
            memory_store: Memory store for persisting crystallized memories
        """
        self.config = config
        self.memory_store = memory_store
        self.crystallizer = Crystallizer(config)
        self.active_chains: dict[str, ActionChain] = {}
        self.current_chain_id: str | None = None

    def start_chain(self, task_description: str = "") -> str:
        """Start tracking a new action chain.

        Args:
            task_description: Description of the task being performed

        Returns:
            Chain ID
        """
        if not self.config.crystallization_enabled:
            return ""

        chain_id = str(uuid4())
        chain = ActionChain(
            chain_id=chain_id,
            actions=[],
            start_time=datetime.now(),
            task_description=task_description
        )

        self.active_chains[chain_id] = chain
        self.current_chain_id = chain_id

        logger.debug(f"Started action chain: {chain_id}")
        return chain_id

    def add_action(
        self,
        action_type: ActionType,
        content: str,
        metadata: dict | None = None,
        chain_id: str | None = None
    ) -> None:
        """Add action to current or specified chain.

        Args:
            action_type: Type of action
            content: Action content
            metadata: Optional metadata (tool names, file paths, etc.)
            chain_id: Chain ID (uses current if not specified)
        """
        if not self.config.crystallization_enabled:
            return

        target_chain_id = chain_id or self.current_chain_id
        if not target_chain_id or target_chain_id not in self.active_chains:
            logger.warning(f"No active chain found: {target_chain_id}")
            return

        action = Action(
            action_type=action_type,
            content=content,
            metadata=metadata or {}
        )

        self.active_chains[target_chain_id].actions.append(action)
        logger.debug(f"Added {action_type.value} action to chain {target_chain_id}")

    def end_chain(
        self,
        success: bool = True,
        chain_id: str | None = None
    ) -> str | None:
        """End action chain and trigger crystallization if appropriate.

        Args:
            success: Whether the task completed successfully
            chain_id: Chain ID (uses current if not specified)

        Returns:
            Memory ID if crystallized, None otherwise
        """
        if not self.config.crystallization_enabled:
            return None

        target_chain_id = chain_id or self.current_chain_id
        if not target_chain_id or target_chain_id not in self.active_chains:
            logger.warning(f"No active chain found: {target_chain_id}")
            return None

        chain = self.active_chains[target_chain_id]
        chain.end_time = datetime.now()
        chain.success = success

        # Clear current chain if it's the one being ended
        if target_chain_id == self.current_chain_id:
            self.current_chain_id = None

        # Check if should crystallize
        if not self.crystallizer.should_crystallize(chain):
            logger.debug(f"Chain {target_chain_id} does not meet crystallization criteria")
            del self.active_chains[target_chain_id]
            return None

        # Crystallize
        try:
            memory = self.crystallizer.crystallize(chain)

            # Store in memory platform
            self._store_crystallized_memory(memory)

            logger.info(f"Crystallized chain {target_chain_id} -> memory {memory.memory_id}")

            # Cleanup
            del self.active_chains[target_chain_id]

            return memory.memory_id

        except Exception as e:
            logger.error(f"Failed to crystallize chain {target_chain_id}: {e}")
            del self.active_chains[target_chain_id]
            return None

    def _store_crystallized_memory(self, memory) -> None:
        """Store crystallized memory in memory platform.

        Args:
            memory: Crystallized memory to store
        """
        # Convert to memory platform format
        memory_content = {
            "type": "crystallized_pattern",
            "narrative": memory.narrative,
            "key_results": memory.key_results,
            "lessons_learned": memory.lessons_learned,
            "files_touched": memory.files_touched,
            "tools_used": memory.tools_used,
            "pattern_signature": memory.pattern_signature,
            "importance": memory.importance,
            "reuse_count": memory.reuse_count,
            "created_at": memory.created_at.isoformat()
        }

        # Store with high importance to ensure retention
        self.memory_store.add(
            content=str(memory_content),
            metadata={
                "memory_id": memory.memory_id,
                "type": "crystallized",
                "importance": memory.importance,
                "pattern_signature": memory.pattern_signature
            }
        )

    def get_active_chains(self) -> list[str]:
        """Get list of active chain IDs.

        Returns:
            List of chain IDs
        """
        return list(self.active_chains.keys())

    def get_chain(self, chain_id: str) -> ActionChain | None:
        """Get action chain by ID.

        Args:
            chain_id: Chain ID

        Returns:
            Action chain or None
        """
        return self.active_chains.get(chain_id)

    def clear_stale_chains(self, max_age_hours: int = 24) -> int:
        """Clear chains that have been active too long.

        Args:
            max_age_hours: Maximum age in hours

        Returns:
            Number of chains cleared
        """
        now = datetime.now()
        stale_chains = []

        for chain_id, chain in self.active_chains.items():
            age_hours = (now - chain.start_time).total_seconds() / 3600
            if age_hours > max_age_hours:
                stale_chains.append(chain_id)

        for chain_id in stale_chains:
            logger.warning(f"Clearing stale chain: {chain_id}")
            del self.active_chains[chain_id]

        return len(stale_chains)
