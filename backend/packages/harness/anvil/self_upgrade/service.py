from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from anvil.config import EffectiveConfig
from anvil.memory import MemoryManager
from anvil.skills import SkillsService
from anvil.trajectory import (
    ThreadTrajectoryExporter,
    TrajectoryCompressionConfig,
    TrajectoryExportEntry,
    TrajectoryExportFormat,
    TrajectoryExportOptions,
)

from .contracts import (
    SelfUpgradeBacklogItem,
    SelfUpgradeDomainHealth,
    SelfUpgradeHealthReport,
    SelfUpgradeHealthSnapshot,
)


class SelfUpgradeHealthService:
    """Read-only health synthesis for Anvil's self-upgrade surfaces."""

    def report(
        self,
        *,
        config: EffectiveConfig,
        memory_manager: MemoryManager | None = None,
        skills_service: SkillsService | None = None,
        checkpointer: Any | None = None,
        trajectory_export_root: str | Path | None = None,
        fingerprint: str = "self-upgrade",
        candidate_audit_limit: int = 50,
    ) -> SelfUpgradeHealthReport:
        domains = (
            self._memory_domain(
                config=config,
                memory_manager=memory_manager,
                candidate_audit_limit=candidate_audit_limit,
            ),
            self._skills_domain(
                config=config,
                skills_service=skills_service,
                fingerprint=fingerprint,
            ),
            self._trajectory_domain(
                config=config,
                checkpointer=checkpointer,
                export_root=trajectory_export_root,
            ),
        )
        backlog = tuple(item for domain in domains for item in domain[1])
        domain_health = tuple(domain[0] for domain in domains)
        enabled_domains = [domain for domain in domain_health if domain.status not in {"disabled", "unavailable"}]
        if enabled_domains:
            score = _clamp(sum(domain.score for domain in enabled_domains) / len(enabled_domains))
            if any(domain.status == "needs_attention" for domain in enabled_domains):
                status = "needs_attention"
            elif any(domain.status == "watch" for domain in enabled_domains) or backlog:
                status = "watch"
            else:
                status = "healthy"
        elif any(domain.status == "unavailable" for domain in domain_health):
            score = 0.0
            status = "unavailable"
        else:
            score = 1.0
            status = "disabled"
        recommendations = tuple(
            dict.fromkeys(item.recommendation for item in backlog if item.recommendation)
        )
        return SelfUpgradeHealthReport(
            status=status,
            score=score,
            fingerprint=fingerprint,
            domains=domain_health,
            backlog=backlog,
            recommendations=recommendations[:8],
        )

    def record_snapshot(
        self,
        *,
        report: SelfUpgradeHealthReport,
        state_root: str | Path,
        label: str = "",
        source: str = "ops",
    ) -> SelfUpgradeHealthSnapshot:
        existing = self.list_snapshots(state_root=state_root, limit=200)
        previous = existing[0] if existing else None
        previous_domains = {domain.domain_id: domain for domain in previous.report.domains} if previous else {}
        domain_score_delta: dict[str, float] = {}
        for domain in report.domains:
            previous_domain = previous_domains.get(domain.domain_id)
            if previous_domain is None:
                continue
            domain_score_delta[domain.domain_id] = _clamp_delta(domain.score - previous_domain.score)
        score_delta = _clamp_delta(report.score - previous.report.score) if previous else 0.0
        backlog_delta = len(report.backlog) - len(previous.report.backlog) if previous else 0
        improved = previous is None or (
            _status_rank(report.status) <= _status_rank(previous.report.status)
            and (score_delta > 0 or backlog_delta < 0 or any(delta > 0 for delta in domain_score_delta.values()))
        )
        snapshot = SelfUpgradeHealthSnapshot(
            snapshot_id=f"self-upgrade-snapshot-{uuid4().hex[:16]}",
            label=str(label or "").strip()[:160],
            source=str(source or "ops").strip()[:80],
            report=report,
            previous_snapshot_id=previous.snapshot_id if previous else None,
            previous_score=previous.report.score if previous else None,
            score_delta=score_delta,
            backlog_delta=backlog_delta,
            domain_score_delta=domain_score_delta,
            improved=improved,
        )
        self._write_snapshots(state_root=state_root, snapshots=(snapshot, *existing[:199]))
        return snapshot

    def list_snapshots(
        self,
        *,
        state_root: str | Path,
        limit: int = 20,
    ) -> tuple[SelfUpgradeHealthSnapshot, ...]:
        path = self._snapshots_path(state_root)
        if not path.exists():
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return ()
        snapshots: list[SelfUpgradeHealthSnapshot] = []
        for raw_item in raw_items:
            try:
                snapshots.append(SelfUpgradeHealthSnapshot.model_validate(raw_item))
            except Exception:
                continue
        snapshots.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(snapshots[: max(1, min(limit, 200))])

    def _write_snapshots(
        self,
        *,
        state_root: str | Path,
        snapshots: tuple[SelfUpgradeHealthSnapshot, ...],
    ) -> None:
        path = self._snapshots_path(state_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"items": [item.model_dump(mode="json") for item in snapshots[:200]]}
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)

    def _snapshots_path(self, state_root: str | Path) -> Path:
        return Path(state_root).expanduser().resolve() / "snapshots" / "self-upgrade-health.json"

    def _memory_domain(
        self,
        *,
        config: EffectiveConfig,
        memory_manager: MemoryManager | None,
        candidate_audit_limit: int,
    ) -> tuple[SelfUpgradeDomainHealth, tuple[SelfUpgradeBacklogItem, ...]]:
        if not config.hcms.enabled:
            return (
                SelfUpgradeDomainHealth(
                    domain_id="memory",
                    label="HCMS Memory",
                    status="disabled",
                    score=1.0,
                    enabled=False,
                    metrics={"configured": False},
                    recommendations=("Enable HCMS memory to participate in self-upgrade health.",),
                ),
                (),
            )
        if memory_manager is None:
            return (
                SelfUpgradeDomainHealth(
                    domain_id="memory",
                    label="HCMS Memory",
                    status="unavailable",
                    score=0.0,
                    enabled=True,
                    metrics={"configured": True},
                    issues=("memory_manager_missing",),
                    recommendations=("Provide an HCMS MemoryManager to inspect memory self-upgrade health.",),
                ),
                (
                    SelfUpgradeBacklogItem(
                        item_id="memory:manager_missing",
                        domain="memory",
                        severity="critical",
                        title="Memory manager unavailable",
                        summary="HCMS memory is enabled but no MemoryManager was supplied to the health service.",
                        metric="memory_manager",
                        count=1,
                        recommendation="Wire an HCMS MemoryManager into the self-upgrade health composition root.",
                    ),
                ),
            )

        service = getattr(memory_manager, "hcms_service", None)
        if service is None:
            return (
                SelfUpgradeDomainHealth(
                    domain_id="memory",
                    label="HCMS Memory",
                    status="unavailable",
                    score=0.0,
                    enabled=True,
                    metrics={"configured": True, "hcms_service": False},
                    issues=("hcms_service_missing",),
                    recommendations=("Provide an HCMS MemoryManager with hcms_service.",),
                ),
                (
                    SelfUpgradeBacklogItem(
                        item_id="memory:hcms_service_missing",
                        domain="memory",
                        severity="critical",
                        title="HCMS service unavailable",
                        summary="Memory is enabled but the supplied manager does not expose hcms_service.",
                        metric="hcms_service",
                        count=1,
                        recommendation="Wire anvil.memory.MemoryManager into the self-upgrade health composition root.",
                    ),
                ),
            )

        state = service.prefetch("global/default")
        health = memory_manager.health_report()
        pending_updates = int(getattr(service.queue, "pending_count", lambda: 0)())
        cost_reduction = float(getattr(service.queue, "cost_reduction_ratio", lambda: 0.0)())
        active_count = len(state.active_memories())
        low_confidence_count = sum(int(getattr(store, "low_confidence_count", 0)) for store in health.stores)
        quality_issue_count = len(tuple(getattr(health, "issues", ()) or ()))
        archived_count = sum(1 for item in state.memories if item.state.value == "archived")
        forgotten_count = sum(1 for item in state.memories if item.state.value == "forgotten")
        relation_count = len(state.relations)
        causal_edge_count = len(state.causal_edges)
        benchmark_suites = tuple(memory_manager.list_recall_benchmark_suites())
        benchmark_runs = tuple(memory_manager.list_recall_benchmark_runs(limit=200))
        latest_benchmark = benchmark_runs[-1] if benchmark_runs else None
        latest_benchmark_score = float(latest_benchmark.report.score) if latest_benchmark else 0.0
        passing_benchmark_runs = sum(1 for run in benchmark_runs if run.report.passed)
        unrun_benchmark_suites = sum(1 for suite in benchmark_suites if suite.latest_run_id is None)
        metrics: dict[str, int | float | str | bool] = {
            "configured": True,
            "hcms_service": True,
            "active_memory_count": active_count,
            "archived_memory_count": archived_count,
            "forgotten_memory_count": forgotten_count,
            "low_confidence_count": low_confidence_count,
            "quality_issue_count": quality_issue_count,
            "observation_count": len(state.observations),
            "entity_count": len(state.entities),
            "relation_count": relation_count,
            "causal_edge_count": causal_edge_count,
            "update_queue_pending": pending_updates,
            "llm_calls_avoided": state.metrics.llm_calls_avoided,
            "deterministic_updates": state.metrics.deterministic_updates,
            "cost_reduction_ratio": cost_reduction,
            "last_latency_ms": state.metrics.last_latency_ms,
            "recall_hit_rate": state.metrics.recall_hit_rate,
            "recall_benchmark_suite_count": len(benchmark_suites),
            "recall_benchmark_unrun_suite_count": unrun_benchmark_suites,
            "recall_benchmark_run_count": len(benchmark_runs),
            "recall_benchmark_passed_count": passing_benchmark_runs,
            "latest_recall_benchmark_score": latest_benchmark_score,
            "latest_recall_benchmark_passed": bool(latest_benchmark.report.passed) if latest_benchmark else False,
        }

        backlog: list[SelfUpgradeBacklogItem] = []
        if pending_updates:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="memory:update_queue_pending",
                    domain="memory",
                    severity="watch",
                    title="HCMS update queue has pending observations",
                    summary="Captured observations are waiting for the adaptive debounce queue to drain.",
                    metric="update_queue_pending",
                    count=pending_updates,
                    recommendation="Flush HCMS memory before judging memory freshness.",
                )
            )
        if active_count == 0:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="memory:empty_hcms",
                    domain="memory",
                    severity="watch",
                    title="HCMS has no active memories",
                    summary="Memory is enabled but no active durable memories are available for recall.",
                    metric="active_memory_count",
                    count=0,
                    recommendation="Run an end-to-end capture flow and verify the zero-LLM updater stores useful observations.",
                )
            )
        if quality_issue_count:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="memory:quality_issues",
                    domain="memory",
                    severity="watch",
                    title="HCMS memory quality issues need review",
                    summary="HCMS health found low confidence or missing-evidence memories.",
                    metric="quality_issue_count",
                    count=quality_issue_count,
                    recommendation="Review HCMS confidence, evidence, and retention signals before promotion.",
                )
            )
        if unrun_benchmark_suites:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="memory:recall_benchmark_unrun",
                    domain="memory",
                    severity="watch",
                    title="HCMS recall benchmark suites need a run",
                    summary="One or more configured HCMS recall benchmark suites have no recorded run.",
                    metric="recall_benchmark_unrun_suite_count",
                    count=unrun_benchmark_suites,
                    recommendation="Run HCMS recall benchmarks before judging recall quality.",
                )
            )

        score = 1.0
        score = _penalize(score, pending_updates, 0.02, 0.10)
        score = _penalize(score, quality_issue_count, 0.02, 0.12)
        score = _penalize(score, unrun_benchmark_suites, 0.01, 0.05)
        if active_count == 0:
            score = _clamp(score - 0.15)
        if latest_benchmark is not None:
            score = _clamp(score + min(max(latest_benchmark_score, 0.0), 1.0) * 0.01)
        status = _status(score=score, critical=False, watch=bool(backlog))
        return (
            SelfUpgradeDomainHealth(
                domain_id="memory",
                label="HCMS Memory",
                status=status,
                score=score,
                enabled=True,
                metrics=metrics,
                issues=tuple(item.item_id for item in backlog[:12]),
                recommendations=tuple(dict.fromkeys(item.recommendation for item in backlog if item.recommendation))[:8],
            ),
            tuple(backlog),
        )

    def _skills_domain(
        self,
        *,
        config: EffectiveConfig,
        skills_service: SkillsService | None,
        fingerprint: str,
    ) -> tuple[SelfUpgradeDomainHealth, tuple[SelfUpgradeBacklogItem, ...]]:
        if not config.skills_config.enabled:
            return (
                SelfUpgradeDomainHealth(
                    domain_id="skills",
                    label="Skills Curator",
                    status="disabled",
                    score=1.0,
                    enabled=False,
                    metrics={"configured": False},
                    recommendations=("Enable skills_config to participate in self-upgrade health.",),
                ),
                (),
            )
        if skills_service is None:
            return (
                SelfUpgradeDomainHealth(
                    domain_id="skills",
                    label="Skills Curator",
                    status="unavailable",
                    score=0.0,
                    enabled=True,
                    metrics={"configured": True},
                    issues=("skills_service_missing",),
                    recommendations=("Provide SkillsService to inspect skills self-upgrade health.",),
                ),
                (
                    SelfUpgradeBacklogItem(
                        item_id="skills:service_missing",
                        domain="skills",
                        severity="critical",
                        title="Skills service unavailable",
                        summary="Skills are enabled but no SkillsService was supplied to the health service.",
                        metric="skills_service",
                        count=1,
                        recommendation="Wire SkillsService into the self-upgrade health composition root.",
                    ),
                ),
            )

        discovery = skills_service.discover(config=config, fingerprint=fingerprint)
        curator_report = _as_dict(skills_service.manage_curator(config=config, action="report"))
        procedure_report = _as_dict(skills_service.manage_curator(config=config, action="procedures", outcome="all"))
        automation_status = _as_dict(skills_service.curator_automation_status(config=config))

        curator_counts = _as_dict(curator_report.get("counts"))
        procedure_counts = _as_dict(procedure_report.get("counts"))
        recommendations = _sequence(curator_report.get("recommendations"))
        procedure_items = [_as_dict(item) for item in _sequence(procedure_report.get("items"))]
        procedures_with_blockers = sum(
            1
            for item in procedure_items
            if _sequence(_as_dict(item.get("promotion_readiness")).get("blockers"))
        )
        discovery_issue_count = len(discovery.issues)
        collision_count = len(discovery.collisions)
        recommendation_count = len(recommendations)
        last_recommendation_count = _int(automation_status.get("last_recommendation_count"))
        observe_count = _int(curator_counts.get("observe"))
        stale_count = _int(curator_counts.get("stale"))
        archived_count = _int(curator_counts.get("archived"))
        rejected_procedures = _int(procedure_counts.get("rejected"))

        metrics: dict[str, int | float | str | bool] = {
            "skill_discovered_count": len(discovery.all_summaries),
            "skill_enabled_count": len(discovery.enabled_summaries),
            "skill_issue_count": discovery_issue_count,
            "skill_collision_count": collision_count,
            "curator_tracked_count": _int(curator_counts.get("tracked")),
            "curator_active_count": _int(curator_counts.get("active")),
            "curator_core_count": _int(curator_counts.get("core")),
            "curator_observe_count": observe_count,
            "curator_stale_count": stale_count,
            "curator_archived_count": archived_count,
            "curator_recommendation_count": recommendation_count,
            "procedures_total": _int(procedure_counts.get("total")),
            "procedures_returned": _int(procedure_counts.get("returned")),
            "procedures_promotable": _int(procedure_counts.get("promotable")),
            "procedures_promoted": _int(procedure_counts.get("promoted")),
            "procedures_rejected": rejected_procedures,
            "procedures_with_blockers": procedures_with_blockers,
            "curator_automation_enabled": bool(automation_status.get("enabled")),
            "curator_last_recommendation_count": last_recommendation_count,
        }

        backlog: list[SelfUpgradeBacklogItem] = []
        if discovery_issue_count:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="skills:discovery_issues",
                    domain="skills",
                    severity="warning",
                    title="Skill discovery reported issues",
                    summary="Some skill manifests or support files could not be loaded cleanly.",
                    metric="skill_issue_count",
                    count=discovery_issue_count,
                    recommendation="Inspect skill discovery issues before relying on these skills for self-upgrade.",
                )
            )
        if collision_count:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="skills:collisions",
                    domain="skills",
                    severity="critical",
                    title="Skill identity collisions detected",
                    summary="Multiple skill sources resolve to the same skill identity.",
                    metric="skill_collision_count",
                    count=collision_count,
                    recommendation="Resolve duplicate skill identities so capability selection stays deterministic.",
                )
            )
        if recommendation_count:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="skills:curator_recommendations",
                    domain="skills",
                    severity="watch",
                    title="Curator has actionable recommendations",
                    summary="The skills curator found review, merge, template, core, archive, or procedure actions.",
                    metric="curator_recommendation_count",
                    count=recommendation_count,
                    recommendation="Execute bounded curator maintenance or inspect the ranked next_tool_call recommendations.",
                )
            )
        if observe_count or stale_count:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="skills:low_utility_or_stale",
                    domain="skills",
                    severity="watch",
                    title="Some skills are stale or under observation",
                    summary="Curator usage signals indicate weak, low-utility, or inactive skills.",
                    metric="curator_observe_count",
                    count=observe_count + stale_count,
                    recommendation="Let curator maintenance review stale/observe skills and archive only through the governance lane.",
                    metadata={"observe_count": observe_count, "stale_count": stale_count},
                )
            )
        if procedures_with_blockers:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="skills:procedure_blockers",
                    domain="skills",
                    severity="warning",
                    title="Learned procedures have promotion blockers",
                    summary="Procedure candidates exist but quality, evidence, or outcome signals block promotion.",
                    metric="procedures_with_blockers",
                    count=procedures_with_blockers,
                    recommendation="Improve evidence, expected outcomes, verification signals, or reject weak candidates before promotion.",
                )
            )
        if rejected_procedures:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="skills:rejected_procedures",
                    domain="skills",
                    severity="info",
                    title="Rejected procedures remain in audit history",
                    summary="Rejected learned procedures are retained for audit and can be restored explicitly.",
                    metric="procedures_rejected",
                    count=rejected_procedures,
                    recommendation="Keep rejected procedure history unless an explicit governance cleanup is needed.",
                )
            )
        if last_recommendation_count:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="skills:last_automation_recommendations",
                    domain="skills",
                    severity="watch",
                    title="Last curator automation run produced recommendations",
                    summary="Curator automation has recently found follow-up actions.",
                    metric="curator_last_recommendation_count",
                    count=last_recommendation_count,
                    recommendation="Inspect the automation status recommendations before the next maintenance cycle.",
                )
            )

        score = 1.0
        score = _penalize(score, discovery_issue_count, 0.08, 0.30)
        score = _penalize(score, collision_count, 0.25, 0.50)
        score = _penalize(score, recommendation_count, 0.04, 0.20)
        score = _penalize(score, procedures_with_blockers, 0.08, 0.25)
        score = _penalize(score, observe_count + stale_count, 0.03, 0.18)
        score = _penalize(score, rejected_procedures, 0.03, 0.15)
        status = _status(score=score, critical=bool(collision_count), watch=bool(backlog))
        return (
            SelfUpgradeDomainHealth(
                domain_id="skills",
                label="Skills Curator",
                status=status,
                score=score,
                enabled=True,
                metrics=metrics,
                issues=tuple(str(issue) for issue in discovery.issues[:12]),
                recommendations=tuple(
                    str(item.get("reason") or item.get("action") or item.get("title") or "")
                    for item in recommendations[:8]
                    if isinstance(item, dict)
                ),
            ),
            tuple(backlog),
        )

    def _trajectory_domain(
        self,
        *,
        config: EffectiveConfig,
        checkpointer: Any | None,
        export_root: str | Path | None,
    ) -> tuple[SelfUpgradeDomainHealth, tuple[SelfUpgradeBacklogItem, ...]]:
        if not config.trajectory_export.enabled:
            return (
                SelfUpgradeDomainHealth(
                    domain_id="trajectory",
                    label="Trajectory Quality",
                    status="disabled",
                    score=1.0,
                    enabled=False,
                    metrics={"configured": False},
                    recommendations=("Enable trajectory_export to include trajectory quality in self-upgrade health.",),
                ),
                (),
            )
        if checkpointer is None:
            return (
                SelfUpgradeDomainHealth(
                    domain_id="trajectory",
                    label="Trajectory Quality",
                    status="unavailable",
                    score=0.0,
                    enabled=True,
                    metrics={"configured": True},
                    issues=("checkpointer_missing",),
                    recommendations=("Provide the runtime checkpointer to inspect trajectory quality without exporting files.",),
                ),
                (
                    SelfUpgradeBacklogItem(
                        item_id="trajectory:checkpointer_missing",
                        domain="trajectory",
                        severity="critical",
                        title="Trajectory checkpointer unavailable",
                        summary="Trajectory export is enabled but no checkpointer was supplied to the health service.",
                        metric="checkpointer",
                        count=1,
                        recommendation="Wire the runtime checkpointer into the self-upgrade health composition root.",
                    ),
                ),
            )

        thread_ids = list(dict.fromkeys(str(thread_id) for thread_id in checkpointer.list_thread_ids()))
        states = []
        missing: list[str] = []
        for thread_id in thread_ids:
            state = checkpointer.get_thread_state(thread_id)
            if state is None:
                missing.append(thread_id)
                continue
            states.append(state)

        result = ThreadTrajectoryExporter().export_threads(
            states,
            path=None,
            options=_trajectory_export_options_from_config(config),
        )
        stats = _trajectory_health_stats(result.entries)
        thread_count = len(thread_ids)
        failed_count = _int(stats.get("quality_failed_count"))
        warning_count = _int(stats.get("quality_warning_count"))
        passed_count = _int(stats.get("quality_passed_count"))
        error_issue_count = _int(stats.get("quality_error_issue_count"))
        warning_issue_count = _int(stats.get("quality_warning_issue_count"))
        interrupted_count = _int(stats.get("interrupted_count"))
        tool_error_count = _int(stats.get("tool_error_count"))
        minimum_status = str(config.trajectory_export.batch_min_quality_status_default)
        quality_filtered_count = sum(
            1
            for entry in result.entries
            if _trajectory_quality_rank(str(entry.quality.status or "failed")) < _trajectory_quality_rank(minimum_status)
        )
        procedure_learning_summary = _as_dict(stats.get("procedure_learning"))
        procedure_learning_enabled = bool(procedure_learning_summary.get("enabled"))
        procedure_learning_accepted = _int(procedure_learning_summary.get("accepted_count"))
        procedure_learning_skipped = _int(procedure_learning_summary.get("skipped_count"))
        quality_score_sum = sum(float(entry.quality.score or 0.0) for entry in result.entries)
        average_quality_score = _clamp(quality_score_sum / max(len(result.entries), 1)) if result.entries else 1.0
        skipped_count = int(result.skipped_count) + len(missing)
        diagnostics = [*result.diagnostics, *(f"{thread_id}: thread not found" for thread_id in missing)]

        metrics: dict[str, int | float | str | bool] = {
            "configured": True,
            "thread_count": thread_count,
            "exported_count": int(result.exported_count),
            "skipped_count": skipped_count,
            "diagnostic_count": len(diagnostics),
            "quality_passed_count": passed_count,
            "quality_warning_count": warning_count,
            "quality_failed_count": failed_count,
            "quality_error_issue_count": error_issue_count,
            "quality_warning_issue_count": warning_issue_count,
            "quality_filtered_count": quality_filtered_count,
            "tool_call_count": _int(stats.get("tool_call_count")),
            "tool_error_count": tool_error_count,
            "interrupted_count": interrupted_count,
            "artifact_count": _int(stats.get("artifact_count")),
            "approval_count": _int(stats.get("approval_count")),
            "average_quality_score": average_quality_score,
            "minimum_quality_status": minimum_status,
            "procedure_learning_enabled": procedure_learning_enabled,
            "procedure_learning_accepted_count": procedure_learning_accepted,
            "procedure_learning_skipped_count": procedure_learning_skipped,
        }

        backlog: list[SelfUpgradeBacklogItem] = []
        if failed_count:
            failed_thread_ids = [entry.thread_id for entry in result.entries if entry.quality.status == "failed"]
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="trajectory:quality_failed",
                    domain="trajectory",
                    severity="warning",
                    title="Trajectory quality failures detected",
                    summary="One or more durable thread trajectories fail the export quality gates.",
                    metric="quality_failed_count",
                    count=failed_count,
                    recommendation="Inspect failed trajectory quality issues before using these runs for procedure learning or evaluation.",
                    metadata={"thread_ids": failed_thread_ids[:12]},
                )
            )
        if warning_count:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="trajectory:quality_warnings",
                    domain="trajectory",
                    severity="watch",
                    title="Trajectory quality warnings detected",
                    summary="Some trajectories are usable but carry warning-level quality issues.",
                    metric="quality_warning_count",
                    count=warning_count,
                    recommendation="Review warning issue codes before raising trajectory export quality thresholds.",
                )
            )
        if quality_filtered_count:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="trajectory:quality_filtered",
                    domain="trajectory",
                    severity="watch",
                    title="Trajectory batch quality gate would filter runs",
                    summary="Current batch quality settings would exclude some trajectories from normal dataset export.",
                    metric="quality_filtered_count",
                    count=quality_filtered_count,
                    recommendation="Fix trajectory quality issues or intentionally lower the batch quality gate with audit evidence.",
                    metadata={"minimum_quality_status": minimum_status},
                )
            )
        if tool_error_count or interrupted_count:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="trajectory:execution_noise",
                    domain="trajectory",
                    severity="warning",
                    title="Trajectory evidence includes execution noise",
                    summary="Tool errors or interrupted runs reduce the reliability of learned procedures and eval datasets.",
                    metric="tool_error_count",
                    count=tool_error_count + interrupted_count,
                    recommendation="Prefer completed successful trajectories for automatic procedure learning and evaluation snapshots.",
                    metadata={"tool_error_count": tool_error_count, "interrupted_count": interrupted_count},
                )
            )
        if skipped_count or diagnostics:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="trajectory:export_diagnostics",
                    domain="trajectory",
                    severity="watch",
                    title="Trajectory health skipped some threads",
                    summary="Some durable thread states could not be converted into health-quality trajectory entries.",
                    metric="skipped_count",
                    count=max(skipped_count, len(diagnostics)),
                    recommendation="Inspect checkpointer consistency and empty conversations before using these threads for eval datasets.",
                    metadata={"diagnostics": diagnostics[:12]},
                )
            )
        if procedure_learning_enabled and procedure_learning_skipped:
            backlog.append(
                SelfUpgradeBacklogItem(
                    item_id="trajectory:procedure_learning_skipped",
                    domain="trajectory",
                    severity="watch",
                    title="Trajectory procedure learning skipped candidates",
                    summary="Backfill learning skipped some kept trajectories.",
                    metric="procedure_learning_skipped_count",
                    count=procedure_learning_skipped,
                    recommendation="Inspect trajectory learning skip reasons before changing procedure learning thresholds.",
                    metadata={"reasons": _as_dict(procedure_learning_summary.get("reasons"))},
                )
            )

        score = average_quality_score
        score = _penalize(score, failed_count, 0.18, 0.45)
        score = _penalize(score, warning_count, 0.05, 0.20)
        score = _penalize(score, quality_filtered_count, 0.06, 0.24)
        score = _penalize(score, tool_error_count + interrupted_count, 0.08, 0.24)
        score = _penalize(score, skipped_count, 0.04, 0.16)
        issues = tuple(
            dict.fromkeys(
                issue.code
                for entry in result.entries
                for issue in entry.quality.issues
                if issue.severity in {"error", "warning"}
            )
        )
        status = _status(score=score, critical=False, watch=bool(backlog))
        return (
            SelfUpgradeDomainHealth(
                domain_id="trajectory",
                label="Trajectory Quality",
                status=status,
                score=score,
                enabled=True,
                metrics=metrics,
                issues=issues[:12],
                recommendations=tuple(item.recommendation for item in backlog[:8] if item.recommendation),
            ),
            tuple(backlog),
        )


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: object) -> list[Any]:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return value
    return []


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 4)))


def _clamp_delta(value: float) -> float:
    return round(max(-1.0, min(1.0, float(value))), 4)


def _status_rank(status: str) -> int:
    return {
        "healthy": 0,
        "watch": 1,
        "needs_attention": 2,
        "unavailable": 3,
        "disabled": 3,
    }.get(status, 3)


def _penalize(score: float, count: int, per_item: float, cap: float) -> float:
    if count <= 0:
        return _clamp(score)
    return _clamp(score - min(float(count) * per_item, cap))


def _status(*, score: float, critical: bool = False, watch: bool = False) -> str:
    if critical or score < 0.55:
        return "needs_attention"
    if watch or score < 0.82:
        return "watch"
    return "healthy"


def _trajectory_quality_rank(status: str) -> int:
    return {
        "failed": 0,
        "warning": 1,
        "passed": 2,
    }.get(str(status or "failed").strip().lower(), 0)


def _trajectory_export_options_from_config(config: EffectiveConfig) -> TrajectoryExportOptions:
    trajectory_config = config.trajectory_export
    try:
        export_format = TrajectoryExportFormat(str(trajectory_config.default_format))
    except ValueError:
        export_format = TrajectoryExportFormat.ANVIL
    return TrajectoryExportOptions(
        format=export_format,
        include_system=bool(trajectory_config.include_system),
        include_tools=bool(trajectory_config.include_tools),
        include_tool_args=bool(trajectory_config.include_tool_args),
        include_metadata=bool(trajectory_config.include_metadata),
        include_reasoning=bool(trajectory_config.include_reasoning),
        include_parsed_tool_calls=bool(trajectory_config.include_parsed_tool_calls),
        include_hidden_steps=bool(trajectory_config.include_hidden_steps),
        include_artifacts=bool(trajectory_config.include_artifacts),
        include_approvals=bool(trajectory_config.include_approvals),
        include_token_usage=bool(trajectory_config.include_token_usage),
        scrub_secrets=bool(trajectory_config.scrub_secrets),
        compression=TrajectoryCompressionConfig(
            enabled=bool(trajectory_config.compression.enabled),
            max_turns=trajectory_config.compression.max_turns,
            keep_first_turns=int(trajectory_config.compression.keep_first_turns),
            keep_last_turns=int(trajectory_config.compression.keep_last_turns),
            max_message_chars=int(trajectory_config.compression.max_message_chars),
            max_tool_result_chars=int(trajectory_config.compression.max_tool_result_chars),
            max_metadata_chars=int(trajectory_config.compression.max_metadata_chars),
        ),
    )


def _trajectory_health_stats(entries: list[TrajectoryExportEntry]) -> dict[str, object]:
    totals: dict[str, object] = {
        "message_count": 0,
        "exported_turn_count": 0,
        "original_turn_count": 0,
        "omitted_turn_count": 0,
        "quality_failed_count": 0,
        "quality_warning_count": 0,
        "quality_passed_count": 0,
        "quality_error_issue_count": 0,
        "quality_warning_issue_count": 0,
        "quality_info_issue_count": 0,
        "tool_call_count": 0,
        "tool_success_count": 0,
        "tool_error_count": 0,
        "approval_count": 0,
        "artifact_count": 0,
        "completed_count": 0,
        "interrupted_count": 0,
        "procedure_learning": {
            "enabled": False,
            "accepted_count": 0,
            "skipped_count": 0,
            "reasons": {},
            "procedure_ids": [],
        },
    }
    tool_counts: dict[str, int] = {}
    models: dict[str, int] = {}
    for entry in entries:
        stats = entry.stats
        totals["message_count"] = _int(totals.get("message_count")) + stats.message_count
        totals["exported_turn_count"] = _int(totals.get("exported_turn_count")) + stats.exported_turn_count
        totals["original_turn_count"] = _int(totals.get("original_turn_count")) + stats.original_turn_count
        totals["omitted_turn_count"] = _int(totals.get("omitted_turn_count")) + stats.omitted_turn_count
        if entry.quality.status == "failed":
            totals["quality_failed_count"] = _int(totals.get("quality_failed_count")) + 1
        elif entry.quality.status == "warning":
            totals["quality_warning_count"] = _int(totals.get("quality_warning_count")) + 1
        else:
            totals["quality_passed_count"] = _int(totals.get("quality_passed_count")) + 1
        totals["quality_error_issue_count"] = _int(totals.get("quality_error_issue_count")) + sum(
            1 for issue in entry.quality.issues if issue.severity == "error"
        )
        totals["quality_warning_issue_count"] = _int(totals.get("quality_warning_issue_count")) + sum(
            1 for issue in entry.quality.issues if issue.severity == "warning"
        )
        totals["quality_info_issue_count"] = _int(totals.get("quality_info_issue_count")) + sum(
            1 for issue in entry.quality.issues if issue.severity == "info"
        )
        totals["tool_call_count"] = _int(totals.get("tool_call_count")) + stats.tool_call_count
        totals["tool_success_count"] = _int(totals.get("tool_success_count")) + stats.tool_success_count
        totals["tool_error_count"] = _int(totals.get("tool_error_count")) + stats.tool_error_count
        totals["approval_count"] = _int(totals.get("approval_count")) + stats.approval_count
        totals["artifact_count"] = _int(totals.get("artifact_count")) + stats.artifact_count
        totals["completed_count"] = _int(totals.get("completed_count")) + (1 if stats.completed else 0)
        totals["interrupted_count"] = _int(totals.get("interrupted_count")) + (1 if stats.interrupted else 0)
        if entry.model:
            models[entry.model] = models.get(entry.model, 0) + 1
        for tool_name, tool_stats in stats.tool_stats.items():
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + tool_stats.count
    totals["models"] = dict(sorted(models.items()))
    totals["tools"] = dict(sorted(tool_counts.items()))
    return totals
