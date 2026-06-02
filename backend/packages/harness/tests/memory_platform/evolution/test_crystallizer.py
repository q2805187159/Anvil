"""Tests for crystallizer."""

import pytest
from datetime import datetime

from anvil.memory_platform.evolution.crystallizer import Crystallizer
from anvil.memory_platform.evolution.contracts import (
    Action,
    ActionChain,
    ActionType,
    MemoryEvolutionConfig
)


class TestCrystallizer:
    """Test suite for Crystallizer."""

    def setup_method(self):
        """Setup test fixtures."""
        self.config = MemoryEvolutionConfig(
            crystallization_enabled=True,
            min_actions_for_crystallization=3
        )
        self.crystallizer = Crystallizer(self.config)

    def test_should_crystallize_success(self):
        """Test crystallization criteria - successful case."""
        chain = ActionChain(
            chain_id="test",
            actions=[
                Action(ActionType.TOOL_CALL, "action1"),
                Action(ActionType.TOOL_CALL, "action2"),
                Action(ActionType.RESULT, "action3")
            ],
            start_time=datetime.now(),
            success=True
        )

        assert self.crystallizer.should_crystallize(chain) is True

    def test_should_crystallize_insufficient_actions(self):
        """Test crystallization criteria - too few actions."""
        chain = ActionChain(
            chain_id="test",
            actions=[
                Action(ActionType.TOOL_CALL, "action1"),
                Action(ActionType.RESULT, "action2")
            ],
            start_time=datetime.now(),
            success=True
        )

        assert self.crystallizer.should_crystallize(chain) is False

    def test_should_crystallize_failed(self):
        """Test crystallization criteria - failed task."""
        chain = ActionChain(
            chain_id="test",
            actions=[
                Action(ActionType.TOOL_CALL, "action1"),
                Action(ActionType.TOOL_CALL, "action2"),
                Action(ActionType.TOOL_CALL, "action3")
            ],
            start_time=datetime.now(),
            success=False
        )

        assert self.crystallizer.should_crystallize(chain) is False

    def test_should_crystallize_disabled(self):
        """Test crystallization when disabled."""
        config = MemoryEvolutionConfig(crystallization_enabled=False)
        crystallizer = Crystallizer(config)

        chain = ActionChain(
            chain_id="test",
            actions=[Action(ActionType.TOOL_CALL, "a") for _ in range(5)],
            start_time=datetime.now(),
            success=True
        )

        assert crystallizer.should_crystallize(chain) is False

    def test_crystallize_basic(self):
        """Test basic crystallization."""
        chain = ActionChain(
            chain_id="test",
            task_description="Fix authentication bug",
            actions=[
                Action(ActionType.TOOL_CALL, "Read auth.py", metadata={"tool_name": "Read"}),
                Action(ActionType.REASONING, "Found token validation issue"),
                Action(ActionType.TOOL_CALL, "Edit auth.py", metadata={"tool_name": "Edit"}),
                Action(ActionType.RESULT, "Bug fixed successfully")
            ],
            start_time=datetime.now(),
            success=True
        )

        memory = self.crystallizer.crystallize(chain)

        assert memory.memory_id is not None
        assert "Fix authentication bug" in memory.narrative
        assert len(memory.key_results) > 0
        assert len(memory.lessons_learned) > 0
        assert memory.importance > 0

    def test_extract_narrative(self):
        """Test narrative extraction."""
        chain = ActionChain(
            chain_id="test",
            task_description="Refactor user module",
            actions=[Action(ActionType.TOOL_CALL, "a") for _ in range(5)],
            start_time=datetime.now(),
            success=True
        )

        narrative = self.crystallizer._extract_narrative(chain)

        assert "Refactor user module" in narrative
        assert "5 actions" in narrative

    def test_extract_key_results(self):
        """Test key results extraction."""
        chain = ActionChain(
            chain_id="test",
            actions=[
                Action(ActionType.TOOL_CALL, "action1"),
                Action(ActionType.RESULT, "Successfully created new API endpoint"),
                Action(ActionType.RESULT, "All tests passing"),
                Action(ActionType.RESULT, "Documentation updated")
            ],
            start_time=datetime.now(),
            success=True
        )

        results = self.crystallizer._extract_key_results(chain)

        assert len(results) > 0
        assert any("API endpoint" in r for r in results)

    def test_extract_lessons(self):
        """Test lessons extraction."""
        chain = ActionChain(
            chain_id="test",
            actions=[
                Action(
                    ActionType.TOOL_CALL,
                    "Read file",
                    metadata={"tool_name": "Read", "file_path": "test.py"}
                ),
                Action(
                    ActionType.TOOL_CALL,
                    "Edit file",
                    metadata={"tool_name": "Edit", "file_path": "test.py"}
                ),
                Action(ActionType.RESULT, "Done")
            ],
            start_time=datetime.now(),
            success=True
        )

        lessons = self.crystallizer._extract_lessons(chain)

        assert len(lessons) > 0
        assert any("tools" in lesson.lower() for lesson in lessons)
        assert any("files" in lesson.lower() for lesson in lessons)

    def test_extract_files(self):
        """Test file extraction."""
        chain = ActionChain(
            chain_id="test",
            actions=[
                Action(ActionType.TOOL_CALL, "a", metadata={"file_path": "auth.py"}),
                Action(ActionType.TOOL_CALL, "b", metadata={"file_path": "utils.py"}),
                Action(ActionType.TOOL_CALL, "c", metadata={"file_path": "auth.py"}),
            ],
            start_time=datetime.now(),
            success=True
        )

        files = self.crystallizer._extract_files(chain)

        assert len(files) == 2  # Deduplicated
        assert "auth.py" in files
        assert "utils.py" in files

    def test_extract_tools(self):
        """Test tool extraction."""
        chain = ActionChain(
            chain_id="test",
            actions=[
                Action(ActionType.TOOL_CALL, "a", metadata={"tool_name": "Read"}),
                Action(ActionType.TOOL_CALL, "b", metadata={"tool_name": "Edit"}),
                Action(ActionType.TOOL_CALL, "c", metadata={"tool_name": "Read"}),
                Action(ActionType.REASONING, "thinking"),
            ],
            start_time=datetime.now(),
            success=True
        )

        tools = self.crystallizer._extract_tools(chain)

        assert len(tools) == 2  # Deduplicated
        assert "Read" in tools
        assert "Edit" in tools

    def test_generate_signature(self):
        """Test pattern signature generation."""
        chain1 = ActionChain(
            chain_id="test1",
            actions=[
                Action(ActionType.TOOL_CALL, "a", metadata={"tool_name": "Read"}),
                Action(ActionType.TOOL_CALL, "b", metadata={"tool_name": "Edit"}),
            ],
            start_time=datetime.now(),
            success=True
        )

        chain2 = ActionChain(
            chain_id="test2",
            actions=[
                Action(ActionType.TOOL_CALL, "x", metadata={"tool_name": "Read"}),
                Action(ActionType.TOOL_CALL, "y", metadata={"tool_name": "Edit"}),
            ],
            start_time=datetime.now(),
            success=True
        )

        sig1 = self.crystallizer._generate_signature(chain1)
        sig2 = self.crystallizer._generate_signature(chain2)

        # Same pattern should generate same signature
        assert sig1 == sig2
        assert len(sig1) == 32  # MD5 hash length

    def test_calculate_importance(self):
        """Test importance calculation."""
        # Simple chain
        simple_chain = ActionChain(
            chain_id="simple",
            actions=[Action(ActionType.TOOL_CALL, "a") for _ in range(3)],
            start_time=datetime.now(),
            success=True
        )

        # Complex chain with files and multiple tools
        complex_chain = ActionChain(
            chain_id="complex",
            actions=[
                Action(ActionType.TOOL_CALL, "a", metadata={"tool_name": "Read", "file_path": "a.py"}),
                Action(ActionType.TOOL_CALL, "b", metadata={"tool_name": "Edit", "file_path": "b.py"}),
                Action(ActionType.TOOL_CALL, "c", metadata={"tool_name": "Write", "file_path": "c.py"}),
                Action(ActionType.TOOL_CALL, "d", metadata={"tool_name": "Bash"}),
                Action(ActionType.REASONING, "thinking"),
                Action(ActionType.RESULT, "done"),
            ],
            start_time=datetime.now(),
            success=True
        )

        simple_importance = self.crystallizer._calculate_importance(simple_chain)
        complex_importance = self.crystallizer._calculate_importance(complex_chain)

        assert 0.0 <= simple_importance <= 1.0
        assert 0.0 <= complex_importance <= 1.0
        assert complex_importance > simple_importance

    def test_crystallize_full_workflow(self):
        """Test complete crystallization workflow."""
        chain = ActionChain(
            chain_id="workflow",
            task_description="Implement user authentication",
            actions=[
                Action(
                    ActionType.TOOL_CALL,
                    "Read existing auth code",
                    metadata={"tool_name": "Read", "file_path": "auth.py"}
                ),
                Action(
                    ActionType.REASONING,
                    "Need to add JWT token support"
                ),
                Action(
                    ActionType.TOOL_CALL,
                    "Install JWT library",
                    metadata={"tool_name": "Bash"}
                ),
                Action(
                    ActionType.TOOL_CALL,
                    "Update auth module",
                    metadata={"tool_name": "Edit", "file_path": "auth.py"}
                ),
                Action(
                    ActionType.TOOL_CALL,
                    "Write tests",
                    metadata={"tool_name": "Write", "file_path": "test_auth.py"}
                ),
                Action(
                    ActionType.RESULT,
                    "Authentication implemented with JWT tokens, all tests passing"
                )
            ],
            start_time=datetime.now(),
            success=True
        )

        memory = self.crystallizer.crystallize(chain)

        # Verify all components
        assert memory.memory_id is not None
        assert "authentication" in memory.narrative.lower()
        assert len(memory.key_results) > 0
        assert len(memory.lessons_learned) > 0
        assert "auth.py" in memory.files_touched
        assert "test_auth.py" in memory.files_touched
        assert len(memory.tools_used) >= 3
        assert memory.pattern_signature is not None
        assert memory.importance > 0.5  # Complex task should have high importance
        assert memory.reuse_count == 0
