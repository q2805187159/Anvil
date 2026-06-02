"""Learning mechanisms contracts and data models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class OutcomeType(str, Enum):
    """Execution outcome types."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class FailureCategory(str, Enum):
    """Failure categorization."""

    TOOL_ERROR = "tool_error"
    CONTEXT_INSUFFICIENT = "context_insufficient"
    LOGIC_ERROR = "logic_error"
    RESOURCE_LIMIT = "resource_limit"
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    UNKNOWN = "unknown"


class PatternType(str, Enum):
    """Pattern types for recognition."""

    TOOL_SEQUENCE = "tool_sequence"
    FILE_WORKFLOW = "file_workflow"
    ERROR_RECOVERY = "error_recovery"
    CONTEXT_CONFIG = "context_config"
    CACHE_PATTERN = "cache_pattern"


class AdaptationType(str, Enum):
    """Types of behavioral adaptations."""

    TOOL_SELECTION = "tool_selection"
    CONTEXT_LOADING = "context_loading"
    CACHE_WARMING = "cache_warming"
    ERROR_HANDLING = "error_handling"
    TIMEOUT_ADJUSTMENT = "timeout_adjustment"


class ExecutionFeedback(BaseModel):
    """Feedback from task execution.

    Captures outcomes, evidence, and context for learning.
    """

    feedback_id: str = Field(description="Unique feedback identifier")
    timestamp: datetime = Field(default_factory=datetime.now)

    # Task context
    task_description: str = Field(description="What was being attempted")
    outcome: OutcomeType = Field(description="Execution outcome")

    # Evidence
    tools_used: list[str] = Field(default_factory=list, description="Tools invoked")
    files_modified: list[str] = Field(default_factory=list, description="Files changed")
    errors: list[str] = Field(default_factory=list, description="Error messages")
    duration_seconds: float = Field(description="Execution duration")

    # Scoring
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in outcome")
    salience: float = Field(ge=0.0, le=1.0, description="Importance/relevance")

    # Context
    context_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="Relevant context at execution time"
    )
    memory_accessed: list[str] = Field(
        default_factory=list,
        description="Memory IDs accessed"
    )

    # Metadata
    session_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None


class Pattern(BaseModel):
    """Recognized pattern from execution history.

    Represents a recurring successful workflow or behavior.
    """

    pattern_id: str = Field(description="Unique pattern identifier")
    pattern_type: PatternType = Field(description="Type of pattern")

    # Pattern definition
    signature: str = Field(description="Pattern signature/fingerprint")
    description: str = Field(description="Human-readable description")

    # Components
    tool_sequence: list[str] = Field(
        default_factory=list,
        description="Sequence of tools"
    )
    file_patterns: list[str] = Field(
        default_factory=list,
        description="File path patterns"
    )
    context_requirements: dict[str, Any] = Field(
        default_factory=dict,
        description="Required context"
    )

    # Scoring
    frequency: int = Field(ge=0, description="Number of occurrences")
    success_rate: float = Field(ge=0.0, le=1.0, description="Success percentage")
    confidence: float = Field(ge=0.0, le=1.0, description="Pattern confidence")
    strength: float = Field(ge=0.0, le=1.0, description="Overall strength")

    # Temporal
    first_seen: datetime = Field(description="First occurrence")
    last_seen: datetime = Field(description="Most recent occurrence")
    recency_score: float = Field(ge=0.0, le=1.0, description="Recency weight")

    # Evidence
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Feedback IDs supporting pattern"
    )

    # Metadata
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class FailureAnalysis(BaseModel):
    """Analysis of a failure event.

    Categorizes failure and provides prevention recommendations.
    """

    analysis_id: str = Field(description="Unique analysis identifier")
    feedback_id: str = Field(description="Source feedback ID")
    timestamp: datetime = Field(default_factory=datetime.now)

    # Categorization
    category: FailureCategory = Field(description="Failure category")
    root_cause: str = Field(description="Identified root cause")

    # Similar failures
    similar_failures: list[str] = Field(
        default_factory=list,
        description="Similar past failure IDs"
    )
    similarity_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Similarity to past failures"
    )

    # Prevention
    prevention_recommendations: list[str] = Field(
        default_factory=list,
        description="How to prevent recurrence"
    )
    prevention_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in recommendations"
    )

    # Recovery
    recovery_patterns: list[str] = Field(
        default_factory=list,
        description="Pattern IDs that recovered from similar failures"
    )

    # Metadata
    analyzed_by: str = Field(default="system", description="Analyzer identifier")


class Adaptation(BaseModel):
    """Behavioral adaptation based on learning.

    Represents a change in runtime behavior.
    """

    adaptation_id: str = Field(description="Unique adaptation identifier")
    adaptation_type: AdaptationType = Field(description="Type of adaptation")
    timestamp: datetime = Field(default_factory=datetime.now)

    # Trigger
    trigger_pattern_id: str | None = Field(
        None,
        description="Pattern that triggered adaptation"
    )
    trigger_analysis_id: str | None = Field(
        None,
        description="Failure analysis that triggered adaptation"
    )

    # Adaptation details
    description: str = Field(description="What is being adapted")
    before_value: Any = Field(description="Value before adaptation")
    after_value: Any = Field(description="Value after adaptation")

    # Confidence
    confidence: float = Field(ge=0.0, le=1.0, description="Adaptation confidence")

    # Effectiveness tracking
    applied_count: int = Field(default=0, description="Times applied")
    success_count: int = Field(default=0, description="Successful applications")
    failure_count: int = Field(default=0, description="Failed applications")

    # Control
    enabled: bool = Field(default=True, description="Whether adaptation is active")
    rollback_available: bool = Field(
        default=True,
        description="Whether rollback is possible"
    )

    # Metadata
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class LearningMetrics(BaseModel):
    """Metrics for learning system performance.

    Tracks effectiveness and overhead of learning mechanisms.
    """

    # Feedback collection
    total_feedback_collected: int = 0
    feedback_by_outcome: dict[str, int] = Field(default_factory=dict)

    # Pattern recognition
    total_patterns_detected: int = 0
    patterns_by_type: dict[str, int] = Field(default_factory=dict)
    average_pattern_confidence: float = 0.0

    # Failure analysis
    total_failures_analyzed: int = 0
    failures_by_category: dict[str, int] = Field(default_factory=dict)
    prevention_success_rate: float = 0.0

    # Adaptations
    total_adaptations: int = 0
    adaptations_by_type: dict[str, int] = Field(default_factory=dict)
    adaptation_success_rate: float = 0.0

    # Performance
    average_overhead_ms: float = 0.0
    max_overhead_ms: float = 0.0

    # Improvement
    success_rate_before_learning: float = 0.0
    success_rate_after_learning: float = 0.0
    improvement_percentage: float = 0.0

    # Temporal
    measurement_start: datetime = Field(default_factory=datetime.now)
    measurement_end: datetime = Field(default_factory=datetime.now)


class LearningConfig(BaseModel):
    """Configuration for learning mechanisms."""

    # Feedback collection
    enable_feedback_collection: bool = True
    feedback_retention_days: int = 90
    min_confidence_threshold: float = 0.6

    # Pattern recognition
    enable_pattern_detection: bool = True
    min_pattern_frequency: int = 3
    min_pattern_success_rate: float = 0.8
    pattern_recency_weight: float = 0.3
    max_patterns: int = 1000

    # Failure analysis
    enable_failure_analysis: bool = True
    failure_similarity_threshold: float = 0.85
    max_failure_history: int = 1000

    # Adaptive behavior
    enable_adaptation: bool = True
    adaptation_confidence_threshold: float = 0.75
    max_adaptations_per_session: int = 10
    allow_automatic_adaptation: bool = False  # Require explicit approval

    # Performance
    learning_overhead_budget_ms: float = 50.0
    metrics_aggregation_interval_seconds: int = 300

    # Storage
    feedback_storage_path: str = ".anvil/learning/feedback"
    pattern_storage_path: str = ".anvil/learning/patterns"
    analysis_storage_path: str = ".anvil/learning/analysis"
    adaptation_storage_path: str = ".anvil/learning/adaptations"
