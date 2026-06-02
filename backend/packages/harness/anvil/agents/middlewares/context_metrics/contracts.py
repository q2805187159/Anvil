"""Contracts for context metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContextMetricsConfig(BaseModel):
    """Configuration for context metrics."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # Tracking toggles
    track_tokens: bool = True
    track_context: bool = True
    track_performance: bool = True
    track_quality: bool = False  # Requires external data

    # Storage
    storage_backend: str = "sqlite"  # sqlite, postgres, memory
    storage_path: str | None = None
    retention_days: int = 30

    # Aggregation
    aggregate_interval_seconds: int = 300  # 5 minutes
    aggregate_levels: list[str] = Field(default_factory=lambda: ["turn", "session", "day"])

    # Performance
    async_collection: bool = True
    batch_size: int = 100
    flush_interval_seconds: int = 60


class TurnMetrics(BaseModel):
    """Metrics for a single turn."""
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.now)

    # Token metrics
    input_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0
    total_tokens: int = 0
    tokens_saved_compaction: int = 0
    tokens_saved_jit: int = 0

    # Context metrics
    message_count: int = 0
    context_size_bytes: int = 0
    compaction_triggered: bool = False
    compaction_ratio: float | None = None
    jit_cache_hits: int = 0
    jit_cache_misses: int = 0

    # Performance metrics
    compaction_time_ms: float | None = None
    jit_load_time_ms: float | None = None
    total_overhead_ms: float = 0.0

    # Quality metrics
    error_occurred: bool = False
    retry_count: int = 0

    @property
    def token_savings_rate(self) -> float:
        """Calculate token savings rate."""
        total = self.total_tokens + self.tokens_saved_compaction + self.tokens_saved_jit
        if total == 0:
            return 0.0
        return (self.tokens_saved_compaction + self.tokens_saved_jit) / total

    @property
    def jit_cache_hit_rate(self) -> float:
        """Calculate JIT cache hit rate."""
        total = self.jit_cache_hits + self.jit_cache_misses
        if total == 0:
            return 0.0
        return self.jit_cache_hits / total


class SessionMetrics(BaseModel):
    """Aggregated metrics for a session."""
    model_config = ConfigDict(extra="forbid")

    session_id: str
    start_time: datetime
    end_time: datetime | None = None

    # Aggregated token metrics
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_tokens_saved: int = 0
    token_savings_rate: float = 0.0

    # Aggregated context metrics
    total_turns: int = 0
    avg_context_size: float = 0.0
    compaction_count: int = 0
    avg_compaction_ratio: float = 0.0
    jit_cache_hit_rate: float = 0.0

    # Aggregated performance
    avg_overhead_ms: float = 0.0
    total_overhead_ms: float = 0.0

    # Cost estimation (assuming $0.01 per 1K tokens)
    estimated_cost_usd: float = 0.0
    estimated_savings_usd: float = 0.0

    def update_from_turn(self, turn: TurnMetrics) -> None:
        """Update session metrics from turn metrics."""
        self.total_turns += 1
        self.total_input_tokens += turn.input_tokens
        self.total_output_tokens += turn.output_tokens
        self.total_tokens += turn.total_tokens
        self.total_tokens_saved += turn.tokens_saved_compaction + turn.tokens_saved_jit

        if turn.compaction_triggered:
            self.compaction_count += 1

        self.total_overhead_ms += turn.total_overhead_ms

        # Recalculate averages
        if self.total_turns > 0:
            self.avg_overhead_ms = self.total_overhead_ms / self.total_turns

        # Recalculate rates
        total_with_savings = self.total_tokens + self.total_tokens_saved
        if total_with_savings > 0:
            self.token_savings_rate = self.total_tokens_saved / total_with_savings

        # Estimate costs (assuming $0.01 per 1K tokens)
        self.estimated_cost_usd = self.total_tokens * 0.00001
        self.estimated_savings_usd = self.total_tokens_saved * 0.00001


class MetricsSnapshot(BaseModel):
    """Snapshot of current metrics state."""
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=datetime.now)
    session_id: str | None = None

    # Current turn metrics
    current_turn: TurnMetrics | None = None

    # Session metrics
    session: SessionMetrics | None = None

    # Real-time stats
    tokens_per_minute: float = 0.0
    turns_per_minute: float = 0.0
    avg_tokens_per_turn: float = 0.0

    # Optimization effectiveness
    compaction_effectiveness: float = 0.0  # % of tokens saved by compaction
    jit_effectiveness: float = 0.0  # % of tokens saved by JIT
    overall_optimization: float = 0.0  # Combined effectiveness
