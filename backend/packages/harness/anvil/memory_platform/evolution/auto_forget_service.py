"""Auto-forget service for memory cleanup coordination."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from .auto_forget import AutoForget
from .contracts import MemoryEvolutionConfig, MemoryToForget

if TYPE_CHECKING:
    from ..stores.base import MemoryStore

logger = logging.getLogger(__name__)


class AutoForgetService:
    """Service for managing automatic memory cleanup.

    Responsibilities:
    - Schedule periodic cleanup
    - Coordinate cleanup operations
    - Track deletion history
    - Ensure safe deletions
    - Maintain audit trail
    """

    def __init__(
        self,
        config: MemoryEvolutionConfig,
        memory_store: MemoryStore
    ):
        """Initialize auto-forget service.

        Args:
            config: Memory evolution configuration
            memory_store: Memory store for cleanup operations
        """
        self.config = config
        self.memory_store = memory_store
        self.auto_forget = AutoForget(config)

        self.last_cleanup: datetime | None = None
        self.deletion_history: list[dict] = []

    def should_run_cleanup(self) -> bool:
        """Check if cleanup should run.

        Returns:
            True if cleanup should run
        """
        if not self.config.auto_forget_enabled:
            return False

        if self.last_cleanup is None:
            return True

        hours_since = (datetime.now() - self.last_cleanup).total_seconds() / 3600
        return self.auto_forget.should_run(hours_since)

    def run_cleanup(self) -> dict:
        """Run memory cleanup process.

        Returns:
            Cleanup statistics
        """
        if not self.config.auto_forget_enabled:
            return {"status": "disabled"}

        try:
            # Get all memories from store
            memories = self._get_all_memories()

            if not memories:
                logger.info("No memories to clean up")
                return {"status": "no_memories", "deleted": 0}

            # Run cleanup analysis
            to_forget = self.auto_forget.run_cleanup(memories)

            # Execute deletions
            deleted_count = 0
            for item in to_forget:
                if self._delete_memory(item):
                    deleted_count += 1
                    self._record_deletion(item)

            self.last_cleanup = datetime.now()

            logger.info(f"Cleanup complete: deleted {deleted_count} memories")

            return {
                "status": "success",
                "deleted": deleted_count,
                "total_analyzed": len(memories),
                "timestamp": self.last_cleanup.isoformat()
            }

        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            return {"status": "error", "error": str(e)}

    def _get_all_memories(self) -> list[dict]:
        """Get all memories from store.

        Returns:
            List of memory dictionaries
        """
        # This is a simplified implementation
        # Real implementation would query the memory store
        # For now, return empty list as placeholder
        return []

    def _delete_memory(self, item: MemoryToForget) -> bool:
        """Delete a memory from store.

        Args:
            item: Memory to delete

        Returns:
            True if deleted successfully
        """
        try:
            # Placeholder for actual deletion
            # Real implementation would call memory_store.delete(item.memory_id)
            logger.debug(f"Deleted memory: {item.memory_id} (reason: {item.reason})")
            return True
        except Exception as e:
            logger.error(f"Failed to delete memory {item.memory_id}: {e}")
            return False

    def _record_deletion(self, item: MemoryToForget) -> None:
        """Record deletion in audit trail.

        Args:
            item: Deleted memory info
        """
        self.deletion_history.append({
            "memory_id": item.memory_id,
            "reason": item.reason,
            "confidence": item.confidence,
            "replaced_by": item.replaced_by,
            "deleted_at": datetime.now().isoformat()
        })

        # Keep only recent history (last 100 deletions)
        if len(self.deletion_history) > 100:
            self.deletion_history = self.deletion_history[-100:]

    def get_deletion_history(self, limit: int = 20) -> list[dict]:
        """Get recent deletion history.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of deletion records
        """
        return self.deletion_history[-limit:]

    def get_statistics(self) -> dict:
        """Get cleanup statistics.

        Returns:
            Statistics dictionary
        """
        # Count deletions by reason
        by_reason = {}
        for record in self.deletion_history:
            reason = record["reason"]
            by_reason[reason] = by_reason.get(reason, 0) + 1

        return {
            "total_deletions": len(self.deletion_history),
            "by_reason": by_reason,
            "last_cleanup": self.last_cleanup.isoformat() if self.last_cleanup else None,
            "enabled": self.config.auto_forget_enabled
        }

    def force_cleanup(self) -> dict:
        """Force cleanup to run immediately.

        Returns:
            Cleanup statistics
        """
        logger.info("Forcing cleanup run")
        return self.run_cleanup()

    def undo_last_deletion(self) -> bool:
        """Undo the last deletion (if possible).

        Returns:
            True if undone successfully
        """
        if not self.deletion_history:
            logger.warning("No deletions to undo")
            return False

        last_deletion = self.deletion_history[-1]
        memory_id = last_deletion["memory_id"]

        # Placeholder for actual restoration
        # Real implementation would restore from backup or archive
        logger.info(f"Undo deletion not yet implemented for: {memory_id}")
        return False
