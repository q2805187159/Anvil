"""Tests for crystallization service."""

import pytest
from datetime import datetime, timedelta

from anvil.memory_platform.evolution.crystallization_service import CrystallizationService
from anvil.memory_platform.evolution.contracts import (
    ActionType,
    MemoryEvolutionConfig
)


class MockMemoryStore:
    """Mock memory store for testing."""

    def __init__(self):
        self.memories = []

    def add(self, content: str, metadata: dict | None = None):
        """Add memory."""
        self.memories.append({
            "content": content,
            "metadata": metadata or {}
        })


class TestCrystallizationService:
    """Test suite for CrystallizationService."""

    def setup_method(self):
        """Setup test fixtures."""
        self.config = MemoryEvolutionConfig(
            crystallization_enabled=True,
            min_actions_for_crystallization=3
        )
        self.memory_store = MockMemoryStore()
        self.service = CrystallizationService(self.config, self.memory_store)

    def test_start_chain(self):
        """Test starting an action chain."""
        chain_id = self.service.start_chain("Test task")

        assert chain_id != ""
        assert chain_id in self.service.active_chains
        assert self.service.current_chain_id == chain_id

        chain = self.service.get_chain(chain_id)
        assert chain is not None
        assert chain.task_description == "Test task"
        assert len(chain.actions) == 0

    def test_add_action(self):
        """Test adding actions to chain."""
        chain_id = self.service.start_chain("Test task")

        self.service.add_action(
            ActionType.TOOL_CALL,
            "Read file",
            {"tool_name": "Read", "file_path": "test.py"}
        )

        self.service.add_action(
            ActionType.RESULT,
            "File contents retrieved"
        )

        chain = self.service.get_chain(chain_id)
        assert len(chain.actions) == 2
        assert chain.actions[0].action_type == ActionType.TOOL_CALL
        assert chain.actions[1].action_type == ActionType.RESULT

    def test_end_chain_successful_crystallization(self):
        """Test ending chain with successful crystallization."""
        chain_id = self.service.start_chain("Fix bug in auth module")

        # Add enough actions to meet minimum
        self.service.add_action(
            ActionType.TOOL_CALL,
            "Read auth.py",
            {"tool_name": "Read", "file_path": "auth.py"}
        )

        self.service.add_action(
            ActionType.REASONING,
            "Found issue in token validation"
        )

        self.service.add_action(
            ActionType.TOOL_CALL,
            "Edit auth.py",
            {"tool_name": "Edit", "file_path": "auth.py"}
        )

        self.service.add_action(
            ActionType.RESULT,
            "Bug fixed successfully"
        )

        # End chain successfully
        memory_id = self.service.end_chain(success=True)

        assert memory_id is not None
        assert chain_id not in self.service.active_chains
        assert len(self.memory_store.memories) == 1

        # Check stored memory
        stored = self.memory_store.memories[0]
        assert stored["metadata"]["type"] == "crystallized"
        assert stored["metadata"]["memory_id"] == memory_id

    def test_end_chain_insufficient_actions(self):
        """Test ending chain with too few actions."""
        chain_id = self.service.start_chain("Small task")

        # Add only 2 actions (below minimum of 3)
        self.service.add_action(ActionType.TOOL_CALL, "Read file")
        self.service.add_action(ActionType.RESULT, "Done")

        memory_id = self.service.end_chain(success=True)

        assert memory_id is None
        assert chain_id not in self.service.active_chains
        assert len(self.memory_store.memories) == 0

    def test_end_chain_unsuccessful(self):
        """Test ending chain with failure."""
        chain_id = self.service.start_chain("Failed task")

        # Add enough actions
        for i in range(4):
            self.service.add_action(ActionType.TOOL_CALL, f"Action {i}")

        memory_id = self.service.end_chain(success=False)

        assert memory_id is None
        assert len(self.memory_store.memories) == 0

    def test_multiple_chains(self):
        """Test managing multiple chains."""
        chain1 = self.service.start_chain("Task 1")
        self.service.add_action(ActionType.TOOL_CALL, "Action 1")

        chain2 = self.service.start_chain("Task 2")
        self.service.add_action(ActionType.TOOL_CALL, "Action 2")

        assert len(self.service.get_active_chains()) == 2
        assert self.service.current_chain_id == chain2

        # Add to specific chain
        self.service.add_action(
            ActionType.RESULT,
            "Result 1",
            chain_id=chain1
        )

        chain1_obj = self.service.get_chain(chain1)
        assert len(chain1_obj.actions) == 2

    def test_clear_stale_chains(self):
        """Test clearing stale chains."""
        # Create chain with old timestamp
        chain_id = self.service.start_chain("Old task")
        chain = self.service.get_chain(chain_id)
        chain.start_time = datetime.now() - timedelta(hours=25)

        # Create recent chain
        recent_id = self.service.start_chain("Recent task")

        cleared = self.service.clear_stale_chains(max_age_hours=24)

        assert cleared == 1
        assert chain_id not in self.service.active_chains
        assert recent_id in self.service.active_chains

    def test_disabled_crystallization(self):
        """Test service with crystallization disabled."""
        config = MemoryEvolutionConfig(crystallization_enabled=False)
        service = CrystallizationService(config, self.memory_store)

        chain_id = service.start_chain("Test")
        assert chain_id == ""

        service.add_action(ActionType.TOOL_CALL, "Action")
        assert len(service.active_chains) == 0

        memory_id = service.end_chain(success=True)
        assert memory_id is None

    def test_pattern_extraction(self):
        """Test that patterns are correctly extracted."""
        chain_id = self.service.start_chain("Multi-file refactor")

        # Simulate complex task
        self.service.add_action(
            ActionType.TOOL_CALL,
            "Read utils.py",
            {"tool_name": "Read", "file_path": "utils.py"}
        )

        self.service.add_action(
            ActionType.REASONING,
            "Need to extract common logic"
        )

        self.service.add_action(
            ActionType.TOOL_CALL,
            "Write helpers.py",
            {"tool_name": "Write", "file_path": "helpers.py"}
        )

        self.service.add_action(
            ActionType.TOOL_CALL,
            "Edit utils.py",
            {"tool_name": "Edit", "file_path": "utils.py"}
        )

        self.service.add_action(
            ActionType.RESULT,
            "Refactoring complete, code is cleaner"
        )

        memory_id = self.service.end_chain(success=True)

        assert memory_id is not None

        # Check that memory contains extracted information
        stored = self.memory_store.memories[0]
        content = stored["content"]

        assert "utils.py" in content
        assert "helpers.py" in content
        assert "Read" in content or "Write" in content or "Edit" in content
