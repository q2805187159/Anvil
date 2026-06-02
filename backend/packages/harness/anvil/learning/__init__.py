"""Learning mechanisms package."""

from .behavior_adapter import BehaviorAdapter
from .contracts import (
    Adaptation,
    AdaptationType,
    ExecutionFeedback,
    FailureAnalysis,
    FailureCategory,
    LearningConfig,
    LearningMetrics,
    OutcomeType,
    Pattern,
    PatternType,
)
from .failure_analyzer import FailureAnalyzer
from .feedback_collector import FeedbackCollector
from .learning_service import LearningService
from .pattern_detector import PatternDetector
from .performance_tracker import LearningPerformanceTracker

__all__ = [
    # Contracts
    "LearningConfig",
    "OutcomeType",
    "FailureCategory",
    "PatternType",
    "AdaptationType",
    "ExecutionFeedback",
    "Pattern",
    "FailureAnalysis",
    "Adaptation",
    "LearningMetrics",
    # Implementations
    "FeedbackCollector",
    "PatternDetector",
    "FailureAnalyzer",
    "BehaviorAdapter",
    "LearningPerformanceTracker",
    "LearningService",
]
