"""Tests for consolidator."""

import pytest

from anvil.memory_platform.evolution.consolidator import Consolidator
from anvil.memory_platform.evolution.contracts import (
    ConsolidatedPattern,
    MemoryEvolutionConfig
)


class TestConsolidator:
    """Test suite for Consolidator."""

    def setup_method(self):
        """Setup test fixtures."""
        self.config = MemoryEvolutionConfig(
            consolidation_enabled=True,
            consolidation_min_observations=5,
            consolidation_interval_turns=10
        )
        self.consolidator = Consolidator(self.config)

    def test_should_consolidate_success(self):
        """Test consolidation criteria - should consolidate."""
        assert self.consolidator.should_consolidate(
            observation_count=10,
            turns_since_last=15
        ) is True

    def test_should_consolidate_insufficient_observations(self):
        """Test consolidation criteria - too few observations."""
        assert self.consolidator.should_consolidate(
            observation_count=3,
            turns_since_last=15
        ) is False

    def test_should_consolidate_too_soon(self):
        """Test consolidation criteria - too soon since last."""
        assert self.consolidator.should_consolidate(
            observation_count=10,
            turns_since_last=5
        ) is False

    def test_should_consolidate_disabled(self):
        """Test consolidation when disabled."""
        config = MemoryEvolutionConfig(consolidation_enabled=False)
        consolidator = Consolidator(config)

        assert consolidator.should_consolidate(
            observation_count=100,
            turns_since_last=100
        ) is False

    def test_detect_preferences_tool_usage(self):
        """Test detecting tool usage preferences."""
        observations = [
            {"type": "tool_call", "tool_name": "Read", "content": "read file"},
            {"type": "tool_call", "tool_name": "Read", "content": "read another"},
            {"type": "tool_call", "tool_name": "Edit", "content": "edit file"},
            {"type": "tool_call", "tool_name": "Read", "content": "read third"},
            {"type": "tool_call", "tool_name": "Bash", "content": "run command"},
        ]

        patterns = self.consolidator._detect_preferences(observations)

        # Should detect Read as frequently used (3/5 = 60%)
        read_patterns = [p for p in patterns if "Read" in p.description]
        assert len(read_patterns) > 0
        assert read_patterns[0].pattern_type == "preference"

    def test_detect_preferences_coding_style(self):
        """Test detecting coding style preferences."""
        observations = [
            {"type": "code", "content": "async def fetch_data():\n    await client.get()"},
            {"type": "code", "content": "def process(data: str) -> int:\n    return len(data)"},
            {"type": "code", "content": 'async def save():\n    """Save data."""\n    await db.save()'},
        ]

        patterns = self.consolidator._detect_preferences(observations)

        # Should detect async and type hints preferences
        style_patterns = [p for p in patterns if p.pattern_type == "preference"]
        assert len(style_patterns) > 0

    def test_detect_architecture_patterns(self):
        """Test detecting architectural patterns."""
        observations = [
            {"type": "tool_call", "file_path": "src/services/auth.py"},
            {"type": "tool_call", "file_path": "src/services/user.py"},
            {"type": "tool_call", "content": "class AuthService:\n    def authenticate(self):"},
            {"type": "tool_call", "content": "class UserService:\n    def get_user(self):"},
        ]

        patterns = self.consolidator._detect_architecture_patterns(observations)

        # Should detect service layer pattern
        service_patterns = [p for p in patterns if "service" in p.description.lower()]
        assert len(service_patterns) > 0
        assert service_patterns[0].pattern_type == "architecture"

    def test_detect_workflow_patterns(self):
        """Test detecting workflow patterns."""
        observations = [
            {"action_type": "tool_call"},
            {"action_type": "reasoning"},
            {"action_type": "tool_call"},
            {"action_type": "result"},
            {"action_type": "tool_call"},
            {"action_type": "reasoning"},
            {"action_type": "tool_call"},
            {"action_type": "result"},
        ]

        patterns = self.consolidator._detect_workflow_patterns(observations)

        # Should detect repeated workflow
        assert len(patterns) > 0
        assert patterns[0].pattern_type == "workflow"

    def test_detect_workflow_tdd(self):
        """Test detecting TDD workflow."""
        observations = [
            {"file_path": "tests/test_auth.py", "content": "test code"},
            {"file_path": "src/auth.py", "content": "implementation"},
            {"file_path": "tests/test_user.py", "content": "test code"},
            {"file_path": "src/user.py", "content": "implementation"},
        ]

        patterns = self.consolidator._detect_workflow_patterns(observations)

        # Should detect TDD pattern
        tdd_patterns = [p for p in patterns if "tdd" in p.pattern_id]
        assert len(tdd_patterns) > 0

    def test_detect_bug_patterns(self):
        """Test detecting bug patterns."""
        observations = [
            {"content": "ImportError: No module named 'requests'"},
            {"content": "ModuleNotFoundError: No module named 'pandas'"},
            {"content": "TypeError: expected str, got int"},
        ]

        patterns = self.consolidator._detect_bug_patterns(observations)

        # Should detect import error pattern
        bug_patterns = [p for p in patterns if p.pattern_type == "bug"]
        assert len(bug_patterns) > 0

    def test_consolidate_full_workflow(self):
        """Test complete consolidation workflow."""
        observations = [
            {"type": "tool_call", "tool_name": "Read", "content": "read"},
            {"type": "tool_call", "tool_name": "Read", "content": "read"},
            {"type": "tool_call", "tool_name": "Edit", "content": "edit"},
            {"type": "code", "content": "async def test(): pass"},
            {"type": "code", "content": "def func(x: int) -> str: return str(x)"},
            {"file_path": "src/services/auth.py"},
            {"file_path": "src/services/user.py"},
            {"action_type": "tool_call"},
            {"action_type": "result"},
        ]

        patterns = self.consolidator.consolidate(observations)

        assert len(patterns) > 0
        # Should have multiple pattern types
        pattern_types = {p.pattern_type for p in patterns}
        assert len(pattern_types) > 1

    def test_merge_patterns_new(self):
        """Test merging with new patterns."""
        new_patterns = [
            ConsolidatedPattern(
                pattern_id="test_1",
                pattern_type="preference",
                description="Test pattern",
                evidence=["evidence 1"]
            )
        ]

        merged = self.consolidator._merge_patterns(new_patterns, [])

        assert len(merged) == 1
        assert merged[0].pattern_id == "test_1"

    def test_merge_patterns_update_existing(self):
        """Test merging with existing patterns."""
        existing = [
            ConsolidatedPattern(
                pattern_id="test_1",
                pattern_type="preference",
                description="Test pattern",
                evidence=["old evidence"],
                confidence=0.5
            )
        ]

        new_patterns = [
            ConsolidatedPattern(
                pattern_id="test_1",
                pattern_type="preference",
                description="Test pattern",
                evidence=["new evidence"],
                confidence=0.6
            )
        ]

        merged = self.consolidator._merge_patterns(new_patterns, existing)

        assert len(merged) == 1
        assert len(merged[0].evidence) == 2  # Combined evidence
        assert merged[0].confidence > 0.5  # Increased confidence

    def test_merge_patterns_mixed(self):
        """Test merging with mix of new and existing."""
        existing = [
            ConsolidatedPattern(
                pattern_id="existing_1",
                pattern_type="preference",
                description="Existing",
                evidence=["old"]
            )
        ]

        new_patterns = [
            ConsolidatedPattern(
                pattern_id="existing_1",
                pattern_type="preference",
                description="Existing",
                evidence=["new"]
            ),
            ConsolidatedPattern(
                pattern_id="new_1",
                pattern_type="architecture",
                description="New",
                evidence=["evidence"]
            )
        ]

        merged = self.consolidator._merge_patterns(new_patterns, existing)

        assert len(merged) == 2
        pattern_ids = {p.pattern_id for p in merged}
        assert "existing_1" in pattern_ids
        assert "new_1" in pattern_ids
