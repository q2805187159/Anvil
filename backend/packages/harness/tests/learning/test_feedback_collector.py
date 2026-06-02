"""Comprehensive tests for feedback collector."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from anvil.learning.contracts import LearningConfig, OutcomeType
from anvil.learning.feedback_collector import FeedbackCollector


@pytest.fixture
def config():
    """Create test configuration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield LearningConfig(
            feedback_storage_path=str(Path(tmpdir) / "feedback"),
            feedback_retention_days=30,
            min_confidence_threshold=0.5
        )


@pytest.fixture
def collector(config):
    """Create feedback collector."""
    return FeedbackCollector(config)


def test_collect_success_feedback(collector):
    """Test collecting successful execution feedback."""
    feedback = collector.collect(
        task_description="Test task",
        outcome=OutcomeType.SUCCESS,
        tools_used=["Read", "Write"],
        files_modified=["test.py"],
        duration_seconds=1.5
    )

    assert feedback.outcome == OutcomeType.SUCCESS
    assert feedback.tools_used == ["Read", "Write"]
    assert feedback.files_modified == ["test.py"]
    assert feedback.confidence > 0.5
    assert feedback.salience > 0.0


def test_collect_failure_feedback(collector):
    """Test collecting failure feedback."""
    feedback = collector.collect(
        task_description="Failed task",
        outcome=OutcomeType.FAILURE,
        tools_used=["Bash"],
        errors=["Command not found"],
        duration_seconds=0.5
    )

    assert feedback.outcome == OutcomeType.FAILURE
    assert len(feedback.errors) == 1
    assert feedback.confidence > 0.0


def test_confidence_calculation(collector):
    """Test confidence score calculation."""
    # Success with evidence
    high_conf = collector.collect(
        task_description="Complex task",
        outcome=OutcomeType.SUCCESS,
        tools_used=["Read", "Edit", "Write"],
        files_modified=["file1.py", "file2.py"],
        duration_seconds=5.0
    )

    # Failure with no evidence
    low_conf = collector.collect(
        task_description="Simple task",
        outcome=OutcomeType.TIMEOUT,
        tools_used=[],
        duration_seconds=0.1
    )

    assert high_conf.confidence > low_conf.confidence


def test_salience_calculation(collector):
    """Test salience score calculation."""
    # High salience: complex task
    high_sal = collector.collect(
        task_description="Very complex task with many details",
        outcome=OutcomeType.SUCCESS,
        tools_used=["Read", "Edit", "Write", "Bash"],
        files_modified=["f1.py", "f2.py", "f3.py", "f4.py", "f5.py", "f6.py"]
    )

    # Low salience: simple task
    low_sal = collector.collect(
        task_description="Simple",
        outcome=OutcomeType.SUCCESS,
        tools_used=["Read"]
    )

    assert high_sal.salience > low_sal.salience


def test_feedback_storage(collector):
    """Test feedback persistence."""
    feedback = collector.collect(
        task_description="Test storage",
        outcome=OutcomeType.SUCCESS,
        tools_used=["Read"]
    )

    # Retrieve feedback
    retrieved = collector.get_feedback(feedback.feedback_id)
    assert retrieved is not None
    assert retrieved.feedback_id == feedback.feedback_id
    assert retrieved.task_description == "Test storage"


def test_list_feedback_by_outcome(collector):
    """Test listing feedback by outcome."""
    # Create mixed feedback
    collector.collect("Success 1", OutcomeType.SUCCESS, ["Read"])
    collector.collect("Success 2", OutcomeType.SUCCESS, ["Write"])
    collector.collect("Failure 1", OutcomeType.FAILURE, ["Bash"], errors=["Error"])

    # List successes
    successes = collector.list_feedback(outcome=OutcomeType.SUCCESS)
    assert len(successes) == 2
    assert all(fb.outcome == OutcomeType.SUCCESS for fb in successes)

    # List failures
    failures = collector.list_feedback(outcome=OutcomeType.FAILURE)
    assert len(failures) == 1
    assert failures[0].outcome == OutcomeType.FAILURE


def test_list_feedback_by_confidence(collector):
    """Test listing feedback by confidence threshold."""
    # Create feedback with varying confidence
    collector.collect("High conf", OutcomeType.SUCCESS, ["Read", "Write"], duration_seconds=2.0)
    collector.collect("Low conf", OutcomeType.TIMEOUT, [], duration_seconds=0.01)

    # List high confidence
    high_conf = collector.list_feedback(min_confidence=0.6)
    assert all(fb.confidence >= 0.6 for fb in high_conf)


def test_list_feedback_since(collector):
    """Test listing feedback since timestamp."""
    # Create old feedback
    old_feedback = collector.collect("Old", OutcomeType.SUCCESS, ["Read"])

    # Manually set old timestamp
    old_feedback.timestamp = datetime.now() - timedelta(days=10)
    collector._store_feedback(old_feedback)

    # Create new feedback
    collector.collect("New", OutcomeType.SUCCESS, ["Write"])

    # List recent only
    recent = collector.list_feedback(since=datetime.now() - timedelta(days=5))
    assert len(recent) >= 1
    assert all(fb.task_description != "Old" for fb in recent)


def test_cleanup_old_feedback(collector):
    """Test cleanup of old feedback."""
    # Create old feedback
    old_feedback = collector.collect("Old", OutcomeType.SUCCESS, ["Read"])
    old_feedback.timestamp = datetime.now() - timedelta(days=100)
    collector._store_feedback(old_feedback)

    # Create recent feedback
    collector.collect("Recent", OutcomeType.SUCCESS, ["Write"])

    # Cleanup
    removed = collector.cleanup_old_feedback()
    assert removed >= 1

    # Old feedback should be gone
    assert collector.get_feedback(old_feedback.feedback_id) is None


def test_statistics(collector):
    """Test statistics collection."""
    # Create varied feedback
    collector.collect("S1", OutcomeType.SUCCESS, ["Read"])
    collector.collect("S2", OutcomeType.SUCCESS, ["Write"])
    collector.collect("F1", OutcomeType.FAILURE, ["Bash"], errors=["Error"])

    stats = collector.get_statistics()
    assert stats["total_feedback"] == 3
    assert stats["by_outcome"][OutcomeType.SUCCESS] == 2
    assert stats["by_outcome"][OutcomeType.FAILURE] == 1
    assert stats["average_confidence"] > 0.0


def test_context_snapshot(collector):
    """Test context snapshot storage."""
    context = {
        "session_id": "test-session",
        "thread_id": "test-thread",
        "memory_count": 5
    }

    feedback = collector.collect(
        task_description="With context",
        outcome=OutcomeType.SUCCESS,
        tools_used=["Read"],
        context_snapshot=context
    )

    assert feedback.context_snapshot == context


def test_memory_accessed(collector):
    """Test memory access tracking."""
    memory_ids = ["mem1", "mem2", "mem3"]

    feedback = collector.collect(
        task_description="With memory",
        outcome=OutcomeType.SUCCESS,
        tools_used=["Read"],
        memory_accessed=memory_ids
    )

    assert feedback.memory_accessed == memory_ids


def test_session_thread_run_ids(collector):
    """Test session/thread/run ID tracking."""
    feedback = collector.collect(
        task_description="With IDs",
        outcome=OutcomeType.SUCCESS,
        tools_used=["Read"],
        session_id="sess-123",
        thread_id="thread-456",
        run_id="run-789"
    )

    assert feedback.session_id == "sess-123"
    assert feedback.thread_id == "thread-456"
    assert feedback.run_id == "run-789"


def test_feedback_id_uniqueness(collector):
    """Test that feedback IDs are unique."""
    fb1 = collector.collect("Task 1", OutcomeType.SUCCESS, ["Read"])
    fb2 = collector.collect("Task 2", OutcomeType.SUCCESS, ["Read"])

    assert fb1.feedback_id != fb2.feedback_id


def test_recent_feedback_cache(collector):
    """Test recent feedback caching."""
    feedback = collector.collect("Cached", OutcomeType.SUCCESS, ["Read"])

    # Should be in cache
    assert feedback.feedback_id in collector.recent_feedback
    assert collector.recent_feedback[feedback.feedback_id] == feedback


def test_list_feedback_limit(collector):
    """Test feedback listing limit."""
    # Create many feedback items
    for i in range(20):
        collector.collect(f"Task {i}", OutcomeType.SUCCESS, ["Read"])

    # List with limit
    results = collector.list_feedback(limit=10)
    assert len(results) == 10


def test_confidence_threshold_logging(collector, caplog):
    """Test that low confidence feedback is logged."""
    collector.config.min_confidence_threshold = 0.9

    # Create low confidence feedback
    collector.collect(
        task_description="Low conf",
        outcome=OutcomeType.TIMEOUT,
        tools_used=[],
        duration_seconds=0.01
    )

    # Should log warning about low confidence
    assert any("below confidence threshold" in record.message for record in caplog.records)
