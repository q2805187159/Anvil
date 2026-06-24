from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from anvil.memory import utc_now

SelfUpgradeHealthStatus = Literal["healthy", "watch", "needs_attention", "disabled", "unavailable"]
SelfUpgradeBacklogSeverity = Literal["info", "watch", "warning", "critical"]


class SelfUpgradeBacklogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    domain: str
    severity: SelfUpgradeBacklogSeverity = "watch"
    title: str
    summary: str = ""
    metric: str | None = None
    count: int = 0
    recommendation: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelfUpgradeDomainHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_id: str
    label: str
    status: SelfUpgradeHealthStatus = "healthy"
    score: float = 1.0
    enabled: bool = True
    metrics: dict[str, int | float | str | bool] = Field(default_factory=dict)
    issues: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()


class SelfUpgradeHealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "self_upgrade_health"
    status: SelfUpgradeHealthStatus = "healthy"
    score: float = 1.0
    fingerprint: str = "self-upgrade"
    domains: tuple[SelfUpgradeDomainHealth, ...] = ()
    backlog: tuple[SelfUpgradeBacklogItem, ...] = ()
    recommendations: tuple[str, ...] = ()
    generated_at: datetime = Field(default_factory=utc_now)


class SelfUpgradeHealthSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    label: str = ""
    source: str = "ops"
    report: SelfUpgradeHealthReport
    previous_snapshot_id: str | None = None
    previous_score: float | None = None
    score_delta: float = 0.0
    backlog_delta: int = 0
    domain_score_delta: dict[str, float] = Field(default_factory=dict)
    improved: bool = False
    created_at: datetime = Field(default_factory=utc_now)
