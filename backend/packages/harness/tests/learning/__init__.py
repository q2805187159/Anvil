"""Tests for learning mechanisms package initialization."""

from anvil.learning import (
    Adaptation,
    AdaptationType,
    ExecutionFeedback,
    FailureAnalysis,
    FailureAnalyzer,
    FailureCategory,
    FeedbackCollector,
    LearningConfig,
    LearningMetrics,
    OutcomeType,
    Pattern,
    PatternDetector,
    PatternType,
)


def test_imports():
    """Test that all exports are importable."""
    # Contracts
    assert LearningConfig is not None
    assert OutcomeType is not None
    assert FailureCategory is not None
    assert PatternType is not None
    assert AdaptationType is not None
    assert ExecutionFeedback is not None
    assert Pattern is not None
    assert FailureAnalysis is not None
    assert Adaptation is not None
    assert LearningMetrics is not None

    # Implementations
    assert FeedbackCollector is not None
    assert PatternDetector is not None
    assert FailureAnalyzer is not None


def test_config_defaults():
    """Test default configuration values."""
    config = LearningConfig()

    assert config.enable_feedback_collection is True
    assert config.feedback_retention_days == 90
    assert config.min_confidence_threshold == 0.6

    assert config.enable_pattern_detection is True
    assert config.min_pattern_frequency == 3
    assert config.min_pattern_success_rate == 0.8

    assert config.enable_failure_analysis is True
    assert config.failure_similarity_threshold == 0.85

    assert config.enable_adaptation is True
    assert config.adaptation_confidence_threshold == 0.75
    assert config.allow_automatic_adaptation is False


def test_outcome_types():
    """Test outcome type enumeration."""
    assert OutcomeType.SUCCESS == "success"
    assert OutcomeType.FAILURE == "failure"
    assert OutcomeType.PARTIAL == "partial"
    assert OutcomeType.TIMEOUT == "timeout"
    assert OutcomeType.CANCELLED == "cancelled"


def test_failure_categories():
    """Test failure category enumeration."""
    assert FailureCategory.TOOL_ERROR == "tool_error"
    assert FailureCategory.CONTEXT_INSUFFICIENT == "context_insufficient"
    assert FailureCategory.LOGIC_ERROR == "logic_error"
    assert FailureCategory.RESOURCE_LIMIT == "resource_limit"
    assert FailureCategory.TIMEOUT == "timeout"
    assert FailureCategory.PERMISSION_DENIED == "permission_denied"
    assert FailureCategory.UNKNOWN == "unknown"


def test_pattern_types():
    """Test pattern type enumeration."""
    assert PatternType.TOOL_SEQUENCE == "tool_sequence"
    assert PatternType.FILE_WORKFLOW == "file_workflow"
    assert PatternType.ERROR_RECOVERY == "error_recovery"
    assert PatternType.CONTEXT_CONFIG == "context_config"
    assert PatternType.CACHE_PATTERN == "cache_pattern"


def test_adaptation_types():
    """Test adaptation type enumeration."""
    assert AdaptationType.TOOL_SELECTION == "tool_selection"
    assert AdaptationType.CONTEXT_LOADING == "context_loading"
    assert AdaptationType.CACHE_WARMING == "cache_warming"
    assert AdaptationType.ERROR_HANDLING == "error_handling"
    assert AdaptationType.TIMEOUT_ADJUSTMENT == "timeout_adjustment"
