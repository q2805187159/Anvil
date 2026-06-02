from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Iterable

from .contracts import (
    CuratedEntry,
    CuratedStoreView,
    MemoryConflict,
    MemoryHealthReport,
    MemoryProviderManifest,
    MemoryQualityIssue,
    MemoryStalenessView,
    MemoryStoreHealth,
    utc_now,
)
from .retention import retention_metrics


_INACTIVE_STATUSES = {"archived", "rejected", "superseded"}


def build_memory_health_report(
    *,
    stores: tuple[CuratedStoreView, ...],
    entries_by_store: dict[str, tuple[CuratedEntry, ...]],
    conflicts: tuple[MemoryConflict, ...],
    staleness: tuple[MemoryStalenessView, ...],
    pending_review_count: int,
    archive_turn_count: int,
    providers: tuple[MemoryProviderManifest, ...],
) -> MemoryHealthReport:
    conflicts_by_memory = _group_conflicts(conflicts)
    stale_by_memory = {item.memory_id: item for item in staleness}
    store_reports: list[MemoryStoreHealth] = []
    global_issues: list[MemoryQualityIssue] = []

    for store in stores:
        entries = entries_by_store.get(store.store_id, ())
        report = _store_health(
            store=store,
            entries=entries,
            conflicts_by_memory=conflicts_by_memory,
            stale_by_memory=stale_by_memory,
        )
        store_reports.append(report)
        global_issues.extend(report.issues)

    global_issues.extend(_provider_issues(providers))
    if pending_review_count:
        global_issues.append(
            MemoryQualityIssue(
                issue_id="memory-review-pending",
                severity="watch" if pending_review_count < 5 else "warning",
                kind="pending_review",
                message=f"{pending_review_count} memory review item(s) are waiting for a decision.",
                recommendation="Review or reject pending memory items before relying on them for long-running personalization.",
                score=min(1.0, pending_review_count / 10),
            )
        )

    quality_score = _bounded_score(
        sum(report.quality_score for report in store_reports) / max(len(store_reports), 1)
        - min(0.20, pending_review_count * 0.015)
        - min(0.20, len(conflicts) * 0.025)
    )
    status = _status_from_score(quality_score, global_issues)
    recommendations = _recommendations(global_issues)
    return MemoryHealthReport(
        status=status,
        quality_score=quality_score,
        archive_turn_count=archive_turn_count,
        pending_review_count=pending_review_count,
        conflict_count=len(conflicts),
        stale_count=len(staleness),
        provider_count=len(providers),
        provider_health={provider.provider_id: provider.health for provider in providers},
        stores=tuple(store_reports),
        issues=tuple(sorted(global_issues, key=_issue_sort_key)[:50]),
        recommendations=tuple(recommendations[:12]),
        generated_at=utc_now(),
    )


def _store_health(
    *,
    store: CuratedStoreView,
    entries: tuple[CuratedEntry, ...],
    conflicts_by_memory: dict[str, tuple[MemoryConflict, ...]],
    stale_by_memory: dict[str, MemoryStalenessView],
) -> MemoryStoreHealth:
    active = [entry for entry in entries if entry.status not in _INACTIVE_STATUSES]
    inactive_count = len(entries) - len(active)
    low_confidence = [entry for entry in active if float(entry.confidence or 0.0) < 0.45]
    low_salience = [entry for entry in active if float(entry.salience or 0.0) < 0.30]
    missing_evidence = [
        entry
        for entry in active
        if _needs_evidence(entry) and not entry.evidence_refs and not (entry.source_ref or "").strip()
    ]
    duplicate_clusters = _duplicate_clusters(active)
    retention = [retention_metrics(entry) for entry in active]
    accessed_count = sum(1 for item in retention if int(item["access_count"]) > 0)
    hot_count = sum(1 for item in retention if item["tier"] == "hot")
    warm_count = sum(1 for item in retention if item["tier"] == "warm")
    cold_count = sum(1 for item in retention if item["tier"] == "cold")
    retention_average = round(
        sum(float(item["retention_score"]) for item in retention) / max(len(retention), 1),
        4,
    )
    stale_entries = [
        entry
        for entry in active
        if (entry.memory_id or entry.entry_id) in stale_by_memory or entry.entry_id in stale_by_memory
    ]
    conflict_entries = [
        entry
        for entry in active
        if (entry.memory_id or entry.entry_id) in conflicts_by_memory or entry.entry_id in conflicts_by_memory
    ]
    injection_pressure = round(
        min(1.0, store.actual_injection_tokens / max(store.effective_injection_tokens, 1)),
        4,
    )

    issues: list[MemoryQualityIssue] = []
    issues.extend(_low_quality_issues(store.store_id, low_confidence, "low_confidence"))
    issues.extend(_low_quality_issues(store.store_id, low_salience, "low_salience"))
    issues.extend(_missing_evidence_issues(store.store_id, missing_evidence))
    issues.extend(_duplicate_issues(store.store_id, duplicate_clusters))
    issues.extend(_stale_issues(store.store_id, stale_entries, stale_by_memory))
    issues.extend(_conflict_issues(store.store_id, conflict_entries, conflicts_by_memory))
    if injection_pressure >= 0.92:
        issues.append(
            MemoryQualityIssue(
                issue_id=f"{store.store_id}-injection-pressure",
                severity="warning",
                kind="injection_pressure",
                store_id=store.store_id,
                message=f"{store.display_name} is using {store.actual_injection_tokens}/{store.effective_injection_tokens} injection tokens.",
                recommendation="Consolidate low-value entries or reduce entries before the prompt memory block becomes crowded.",
                score=injection_pressure,
            )
        )

    penalty = (
        len(low_confidence) * 0.04
        + len(low_salience) * 0.025
        + len(missing_evidence) * 0.03
        + len(duplicate_clusters) * 0.08
        + len(conflict_entries) * 0.12
        + len(stale_entries) * 0.025
        + max(0.0, injection_pressure - 0.80) * 0.35
        + inactive_count * 0.005
    )
    quality_score = _bounded_score(1.0 - min(0.95, penalty))
    return MemoryStoreHealth(
        store_id=store.store_id,
        layer_id=_layer_for_store(store.store_id),
        status=_status_from_score(quality_score, issues),
        entry_count=len(entries),
        active_count=len(active),
        inactive_count=inactive_count,
        low_confidence_count=len(low_confidence),
        low_salience_count=len(low_salience),
        missing_evidence_count=len(missing_evidence),
        duplicate_cluster_count=len(duplicate_clusters),
        conflict_count=len(conflict_entries),
        stale_count=len(stale_entries),
        accessed_count=accessed_count,
        hot_count=hot_count,
        warm_count=warm_count,
        cold_count=cold_count,
        retention_average=retention_average,
        injection_token_pressure=injection_pressure,
        quality_score=quality_score,
        issues=tuple(sorted(issues, key=_issue_sort_key)[:20]),
    )


def _needs_evidence(entry: CuratedEntry) -> bool:
    if entry.source_kind in {"manual", "migration"}:
        return False
    if entry.category.startswith("observation:"):
        return True
    return entry.source_kind in {"tool_observation", "turn_sync", "reflection", "review_approved"}


def _low_quality_issues(store_id: str, entries: Iterable[CuratedEntry], kind: str) -> list[MemoryQualityIssue]:
    issues: list[MemoryQualityIssue] = []
    for entry in list(entries)[:10]:
        score = float(entry.confidence if kind == "low_confidence" else entry.salience or 0.0)
        issues.append(
            MemoryQualityIssue(
                issue_id=f"{kind}-{entry.memory_id or entry.entry_id}",
                severity="watch",
                kind=kind,
                store_id=store_id,
                layer_id=entry.layer_id,
                memory_id=entry.memory_id or entry.entry_id,
                message=f"Memory has {kind.replace('_', ' ')} score {score:.2f}.",
                recommendation="Keep it under review until stronger evidence or repeated use confirms it.",
                score=round(score, 4),
            )
        )
    return issues


def _missing_evidence_issues(store_id: str, entries: Iterable[CuratedEntry]) -> list[MemoryQualityIssue]:
    return [
        MemoryQualityIssue(
            issue_id=f"missing-evidence-{entry.memory_id or entry.entry_id}",
            severity="watch",
            kind="missing_evidence",
            store_id=store_id,
            layer_id=entry.layer_id,
            memory_id=entry.memory_id or entry.entry_id,
            message="Memory was created by an automated or observational path without evidence refs.",
            recommendation="Attach evidence refs, reinforce it through repeated observations, or archive it.",
            score=0.5,
        )
        for entry in list(entries)[:10]
    ]


def _duplicate_issues(store_id: str, clusters: list[list[CuratedEntry]]) -> list[MemoryQualityIssue]:
    issues: list[MemoryQualityIssue] = []
    for cluster in clusters[:10]:
        ids = tuple(entry.memory_id or entry.entry_id for entry in cluster)
        issues.append(
            MemoryQualityIssue(
                issue_id=f"duplicate-{_digest('|'.join(ids))}",
                severity="warning",
                kind="near_duplicate",
                store_id=store_id,
                layer_id=cluster[0].layer_id,
                memory_id=ids[0],
                related_memory_ids=ids[1:],
                message=f"{len(cluster)} memory entries appear to describe the same fact or preference.",
                recommendation="Merge the strongest entry and archive or supersede the weaker duplicates.",
                score=min(1.0, len(cluster) / 5),
            )
        )
    return issues


def _stale_issues(
    store_id: str,
    entries: Iterable[CuratedEntry],
    stale_by_memory: dict[str, MemoryStalenessView],
) -> list[MemoryQualityIssue]:
    issues: list[MemoryQualityIssue] = []
    for entry in list(entries)[:10]:
        key = entry.memory_id or entry.entry_id
        stale = stale_by_memory.get(key) or stale_by_memory.get(entry.entry_id)
        issues.append(
            MemoryQualityIssue(
                issue_id=f"stale-{key}",
                severity="watch",
                kind="stale",
                store_id=store_id,
                layer_id=entry.layer_id,
                memory_id=key,
                message=stale.reason if stale else "Memory has not been accessed recently.",
                recommendation="Refresh, consolidate, or archive stale memory before it pollutes future context.",
                score=round(float(stale.stale_score if stale else 0.5), 4),
            )
        )
    return issues


def _conflict_issues(
    store_id: str,
    entries: Iterable[CuratedEntry],
    conflicts_by_memory: dict[str, tuple[MemoryConflict, ...]],
) -> list[MemoryQualityIssue]:
    issues: list[MemoryQualityIssue] = []
    for entry in list(entries)[:10]:
        key = entry.memory_id or entry.entry_id
        conflicts = conflicts_by_memory.get(key) or conflicts_by_memory.get(entry.entry_id) or ()
        related = tuple(
            conflict.conflicting_memory_id if conflict.memory_id == key else conflict.memory_id
            for conflict in conflicts
        )
        issues.append(
            MemoryQualityIssue(
                issue_id=f"conflict-{key}",
                severity="critical",
                kind="conflict",
                store_id=store_id,
                layer_id=entry.layer_id,
                memory_id=key,
                related_memory_ids=tuple(dict.fromkeys(item for item in related if item)),
                message="Memory conflicts with another active memory entry.",
                recommendation="Resolve the conflict before this memory is trusted for planning or personalization.",
                score=1.0,
            )
        )
    return issues


def _provider_issues(providers: tuple[MemoryProviderManifest, ...]) -> list[MemoryQualityIssue]:
    issues: list[MemoryQualityIssue] = []
    for provider in providers:
        if provider.available and provider.configured and provider.health in {"ok", "healthy", "unknown"}:
            continue
        severity = "warning" if provider.active else "info"
        issues.append(
            MemoryQualityIssue(
                issue_id=f"provider-{provider.provider_id}",
                severity=severity,
                kind="provider_health",
                message=f"Memory provider '{provider.provider_id}' reports health '{provider.health}'.",
                recommendation="Check provider configuration if this provider is expected to participate in recall or indexing.",
                score=0.7 if provider.active else 0.3,
            )
        )
    return issues


def _duplicate_clusters(entries: list[CuratedEntry]) -> list[list[CuratedEntry]]:
    grouped: dict[str, list[CuratedEntry]] = defaultdict(list)
    for entry in entries:
        fingerprint = str((entry.metadata or {}).get("fingerprint") or "").strip()
        key = fingerprint or _similarity_key(entry.content)
        if key:
            grouped[key].append(entry)
    return [cluster for cluster in grouped.values() if len(cluster) > 1]


def _similarity_key(content: str) -> str:
    tokens = re.findall(r"[a-z0-9_/-]{2,}|[\u4e00-\u9fff]", str(content or "").lower())
    filtered = [token for token in tokens if token not in {"user", "memory", "note", "the", "and"}]
    return " ".join(filtered[:16])


def _group_conflicts(conflicts: tuple[MemoryConflict, ...]) -> dict[str, tuple[MemoryConflict, ...]]:
    grouped: dict[str, list[MemoryConflict]] = defaultdict(list)
    for conflict in conflicts:
        grouped[conflict.memory_id].append(conflict)
        grouped[conflict.conflicting_memory_id].append(conflict)
    return {key: tuple(value) for key, value in grouped.items()}


def _recommendations(issues: list[MemoryQualityIssue]) -> list[str]:
    by_kind: dict[str, int] = defaultdict(int)
    for issue in issues:
        by_kind[issue.kind] += 1
    recommendations: list[str] = []
    if by_kind["conflict"]:
        recommendations.append("Resolve conflicting memories before using memory as a planning source.")
    if by_kind["near_duplicate"]:
        recommendations.append("Merge duplicate or near-duplicate entries to reduce prompt noise.")
    if by_kind["missing_evidence"]:
        recommendations.append("Attach evidence refs to automated observations or archive unsupported memories.")
    if by_kind["low_confidence"]:
        recommendations.append("Keep low-confidence memories out of stable profile context until reinforced.")
    if by_kind["stale"]:
        recommendations.append("Refresh or archive stale memories during the next curator pass.")
    if by_kind["injection_pressure"]:
        recommendations.append("Consolidate memory stores with high prompt injection pressure.")
    if by_kind["pending_review"]:
        recommendations.append("Process pending memory review items to keep recall deterministic.")
    return recommendations


def _status_from_score(score: float, issues: Iterable[MemoryQualityIssue]) -> str:
    severities = {issue.severity for issue in issues}
    if "critical" in severities or score < 0.45:
        return "needs_attention"
    if "warning" in severities or score < 0.75:
        return "watch"
    return "healthy"


def _issue_sort_key(issue: MemoryQualityIssue) -> tuple[int, float, str]:
    severity_rank = {"critical": 0, "warning": 1, "watch": 2, "info": 3}
    return (severity_rank.get(issue.severity, 4), -float(issue.score or 0.0), issue.issue_id)


def _layer_for_store(store_id: str) -> str:
    return "user" if store_id == "user_profile" else "workspace"


def _bounded_score(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 4)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]
