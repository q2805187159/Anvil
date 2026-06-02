"""Memory evolution contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemoryEvolutionConfig(BaseModel):
    """Configuration for memory evolution."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # Crystallization
    crystallization_enabled: bool = True
    min_actions_for_crystallization: int = 3
    crystallization_trigger: str = "task_complete"  # task_complete, manual

    # Consolidation
    consolidation_enabled: bool = True
    consolidation_interval_turns: int = 10
    consolidation_min_observations: int = 5

    # Auto-forget
    auto_forget_enabled: bool = True
    auto_forget_interval_hours: int = 24
    default_ttl_days: int = 30
    contradiction_threshold: float = 0.8
    low_value_threshold: float = 0.3


class ActionType(str, Enum):
    """Types of actions in a chain."""
    TOOL_CALL = "tool_call"
    REASONING = "reasoning"
    DECISION = "decision"
    RESULT = "result"


class Action(BaseModel):
    """Single action in a chain."""
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionChain(BaseModel):
    """Sequence of actions forming a pattern."""
    model_config = ConfigDict(extra="forbid")

    chain_id: str
    actions: list[Action] = Field(default_factory=list)
    start_time: datetime
    end_time: datetime | None = None
    success: bool = False
    task_description: str = ""


class CrystallizedMemory(BaseModel):
    """Crystallized pattern from action chain."""
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    narrative: str  # Human-readable summary
    key_results: list[str]  # Important outcomes
    lessons_learned: list[str]  # What worked/didn't work
    files_touched: list[str]  # Files involved
    tools_used: list[str]  # Tools used
    pattern_signature: str  # Hash for similarity matching
    importance: float = 0.5  # 0.0-1.0
    created_at: datetime = Field(default_factory=datetime.now)
    reuse_count: int = 0


class ConsolidatedPattern(BaseModel):
    """Consolidated pattern from multiple observations."""
    model_config = ConfigDict(extra="forbid")

    pattern_id: str
    pattern_type: str  # preference, architecture, workflow, bug
    description: str
    evidence: list[str]  # Supporting observations
    confidence: float = 0.5  # 0.0-1.0
    created_at: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)


class MemoryToForget(BaseModel):
    """Memory marked for deletion."""
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    reason: str  # expired, contradiction, low_value
    confidence: float = 1.0
    replaced_by: str | None = None  # If superseded by newer memory
