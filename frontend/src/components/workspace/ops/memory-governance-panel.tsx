"use client";

import React from "react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  CheckCircle2Icon,
  DatabaseIcon,
  FlameIcon,
  GitCompareArrowsIcon,
  ListChecksIcon,
  RefreshCwIcon,
  RotateCwIcon,
  ShieldCheckIcon,
  SnowflakeIcon,
  ThermometerSunIcon,
  XCircleIcon,
} from "lucide-react";

import type {
  MemoryConflictView,
  MemoryCandidateAuditEntryView,
  MemoryGovernancePlanItemView,
  MemoryMaintenanceAutomationRunResponse,
  MemoryMaintenanceAutomationStatusResponse,
  MemoryMaintenanceResponse,
  ProfileFacetAuditEntryView,
  ProfileFacetView,
  MemoryQualityIssueView,
  MemoryRecallBenchmarkCaseResultView,
  MemoryRecallBenchmarkRunView,
  MemoryRecallBenchmarkSuiteView,
  MemoryReviewItemView,
  MemoryStalenessEntryView,
  MemoryStoreHealthView,
  RecallEvidenceView,
} from "@/src/core/contracts";
import {
  useApproveMemoryReview,
  useBatchGovernMemory,
  useBatchMemoryReview,
  useFlushMemory,
  useMemoryAdminAudit,
  useMemoryConflicts,
  useGovernMemory,
  useMemoryHealth,
  useMemoryLayerEntries,
  useMemoryMaintenanceAutomation,
  useMemoryOverview,
  useMemoryBenchmarkRuns,
  useMemoryBenchmarkSuites,
  useMemoryProviders,
  useMemoryReview,
  useMemoryStaleness,
  useProfileFacetAudit,
  useProfileFacets,
  useGovernProfileFacet,
  useRebuildProfileFacets,
  useRejectMemoryReview,
  useResolveMemoryConflict,
  useRunMemoryMaintenanceAutomation,
  useRunMemoryMaintenance,
  useRunMemoryBenchmark,
  useRunMemoryBenchmarkSuite,
} from "@/src/core/memory/hooks";
import { Badge, Button, ScrollArea } from "@/src/components/ui";
import { cn } from "@/src/lib/utils";

import { OpsEmptyState, OpsPanelCard } from "./shared";
import type { OpsCopy } from "./types";

type MemoryGovernancePanelProps = {
  copy: OpsCopy;
};

type MemoryGovernanceSection = "overview" | "profile" | "review" | "benchmark" | "maintenance";
const MEMORY_GOVERNANCE_SECTIONS: MemoryGovernanceSection[] = ["overview", "profile", "review", "benchmark", "maintenance"];
type MemoryOverviewDrilldown = "health" | "providers" | "audit";
const MEMORY_OVERVIEW_DRILLDOWNS: MemoryOverviewDrilldown[] = ["health", "providers", "audit"];

function pct(value: number | null | undefined) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function statusTone(status: string | null | undefined): "success" | "warning" | "danger" | "neutral" {
  const normalized = (status ?? "").toLowerCase();
  if (normalized === "healthy" || normalized === "ok" || normalized === "ready") {
    return "success";
  }
  if (normalized === "critical" || normalized === "error" || normalized === "failed") {
    return "danger";
  }
  if (normalized === "warning" || normalized === "degraded") {
    return "warning";
  }
  return "neutral";
}

function actionTone(action: string | null | undefined): "success" | "warning" | "danger" | "neutral" {
  const normalized = (action ?? "").toLowerCase();
  if (normalized === "write") {
    return "success";
  }
  if (normalized === "review") {
    return "warning";
  }
  if (normalized === "skip" || normalized === "error") {
    return "danger";
  }
  return "neutral";
}

function severityTone(severity: string | null | undefined): "success" | "warning" | "danger" | "neutral" {
  const normalized = (severity ?? "").toLowerCase();
  if (normalized === "critical" || normalized === "error") {
    return "danger";
  }
  if (normalized === "warning") {
    return "warning";
  }
  return "neutral";
}

function profileFacetTone(facet: ProfileFacetView): "success" | "warning" | "danger" | "neutral" {
  if (facet.user_state === "forgotten" || facet.state === "dropped") {
    return "danger";
  }
  if (facet.state === "active") {
    return "success";
  }
  if (facet.state === "provisional" || facet.source_polluted) {
    return "warning";
  }
  return "neutral";
}

function profileStateLabel(copy: OpsCopy, state: string) {
  if (state === "active") {
    return copy.memory.active;
  }
  if (state === "provisional") {
    return copy.memory.provisional;
  }
  if (state === "candidate") {
    return copy.memory.candidate;
  }
  if (state === "dropped") {
    return copy.memory.forgotten;
  }
  return state;
}

function CompactMetric({ label, value, sub }: { label: string; value: string; sub?: string | null }) {
  return (
    <div className="min-w-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 py-3">
      <div className="truncate text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{label}</div>
      <div className="mt-2 truncate font-mono text-xl font-semibold leading-none text-[var(--ink)]">{value}</div>
      {sub ? <div className="mt-1 truncate text-xs text-[var(--muted)]">{sub}</div> : null}
    </div>
  );
}

function formatDate(value: string | null | undefined, emptyLabel: string) {
  if (!value) {
    return emptyLabel;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function formatDuration(seconds: number | null | undefined, emptyLabel: string) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return emptyLabel;
  }
  const normalized = Math.max(0, Math.round(seconds));
  if (normalized >= 86400 && normalized % 86400 === 0) {
    return `${normalized / 86400}d`;
  }
  if (normalized >= 3600 && normalized % 3600 === 0) {
    return `${normalized / 3600}h`;
  }
  if (normalized >= 60 && normalized % 60 === 0) {
    return `${normalized / 60}m`;
  }
  return `${normalized}s`;
}

function tierTone(tier: string | null | undefined): "success" | "warning" | "danger" | "neutral" {
  const normalized = (tier ?? "").toLowerCase();
  if (normalized === "hot") {
    return "success";
  }
  if (normalized === "warm") {
    return "warning";
  }
  if (normalized === "cold") {
    return "danger";
  }
  return "neutral";
}

function clampPct(value: number | null | undefined) {
  return Math.min(100, Math.max(0, Math.round((value ?? 0) * 100)));
}

function ScoreBar({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | null | undefined;
  tone?: "success" | "warning" | "danger" | "neutral";
}) {
  const percent = clampPct(value);
  const fillClass =
    tone === "success"
      ? "bg-[var(--success)]"
      : tone === "warning"
        ? "bg-[var(--warning)]"
        : tone === "danger"
          ? "bg-[var(--danger)]"
          : "bg-[var(--primary)]";
  return (
    <div className="min-w-0">
      <div className="mb-1 flex min-w-0 items-center justify-between gap-2 text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
        <span className="truncate">{label}</span>
        <span className="font-mono">{percent}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--panel-muted)]">
        <div className={cn("h-full rounded-full", fillClass)} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function StoreHealthCard({ copy, store }: { copy: OpsCopy; store: MemoryStoreHealthView }) {
  const issueCount = store.issues?.length ?? 0;
  return (
    <article className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4 shadow-[var(--shadow-card)]">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{store.store_id}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">{store.layer_id ?? copy.common.none}</div>
        </div>
        <Badge tone={statusTone(store.status)}>{store.status ?? "unknown"}</Badge>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
        <CompactMetric label={copy.memory.qualityScore} value={pct(store.quality_score)} />
        <CompactMetric label={copy.memory.entries} value={String(store.entry_count ?? 0)} sub={`${copy.memory.active} ${store.active_count ?? 0}`} />
        <CompactMetric label={copy.memory.duplicates} value={String(store.duplicate_cluster_count ?? 0)} />
        <CompactMetric label={copy.memory.tokenPressure} value={pct(store.injection_token_pressure)} />
      </div>
      <div className="mt-3 grid gap-3 rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <ScoreBar label={copy.memory.retentionScore} value={store.retention_average} tone={tierTone((store.cold_count ?? 0) > (store.hot_count ?? 0) ? "cold" : "hot")} />
        <ScoreBar label={copy.memory.tokenPressure} value={store.injection_token_pressure} tone={(store.injection_token_pressure ?? 0) > 0.82 ? "danger" : (store.injection_token_pressure ?? 0) > 0.62 ? "warning" : "neutral"} />
        <div className="flex min-w-0 flex-wrap gap-2 md:col-span-2">
          <Badge tone={(store.hot_count ?? 0) > 0 ? "success" : "neutral"}>
            <FlameIcon className="size-3" />
            {copy.memory.hot}: {store.hot_count ?? 0}
          </Badge>
          <Badge tone={(store.warm_count ?? 0) > 0 ? "warning" : "neutral"}>
            <ThermometerSunIcon className="size-3" />
            {copy.memory.warm}: {store.warm_count ?? 0}
          </Badge>
          <Badge tone={(store.cold_count ?? 0) > 0 ? "danger" : "neutral"}>
            <SnowflakeIcon className="size-3" />
            {copy.memory.cold}: {store.cold_count ?? 0}
          </Badge>
          <Badge tone={(store.accessed_count ?? 0) > 0 ? "accent" : "neutral"}>
            {copy.memory.accessed}: {store.accessed_count ?? 0}
          </Badge>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Badge tone={(store.low_confidence_count ?? 0) > 0 ? "warning" : "neutral"}>
          {copy.memory.lowConfidence}: {store.low_confidence_count ?? 0}
        </Badge>
        <Badge tone={(store.low_salience_count ?? 0) > 0 ? "warning" : "neutral"}>
          {copy.memory.lowSalience}: {store.low_salience_count ?? 0}
        </Badge>
        <Badge tone={(store.missing_evidence_count ?? 0) > 0 ? "warning" : "neutral"}>
          {copy.memory.missingEvidence}: {store.missing_evidence_count ?? 0}
        </Badge>
        <Badge tone={(store.conflict_count ?? 0) > 0 ? "danger" : "neutral"}>
          {copy.memory.conflicts}: {store.conflict_count ?? 0}
        </Badge>
        <Badge tone={(store.stale_count ?? 0) > 0 ? "warning" : "neutral"}>
          {copy.memory.stale}: {store.stale_count ?? 0}
        </Badge>
      </div>
      {issueCount > 0 ? (
        <div className="mt-3 space-y-2">
          {store.issues.slice(0, 3).map((issue) => (
            <IssueRow key={issue.issue_id} issue={issue} />
          ))}
        </div>
      ) : null}
    </article>
  );
}

function ReviewItemCard({
  copy,
  item,
  approvePending,
  rejectPending,
  onApprove,
  onReject,
}: {
  copy: OpsCopy;
  item: MemoryReviewItemView;
  approvePending: boolean;
  rejectPending: boolean;
  onApprove(reviewId: string): void;
  onReject(reviewId: string): void;
}) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{item.category}</div>
          <div className="mt-1 truncate text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
            {item.layer_id} · {item.action} · {item.store_id}
          </div>
        </div>
        <Badge tone={item.status === "pending" ? "warning" : "neutral"}>{item.status}</Badge>
      </div>
      <div className="mt-2 overflow-hidden text-sm leading-6 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:3]">
        {item.content}
      </div>
      {item.rationale ? <div className="mt-2 text-xs leading-5 text-[var(--muted)]">{item.rationale}</div> : null}
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <ScoreBar label={copy.memory.confidence} value={item.confidence} />
        <ScoreBar label={copy.memory.salience} value={item.salience} />
        <CompactMetric label={copy.memory.evidence} value={String(item.evidence_refs?.length ?? 0)} />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="primary" disabled={approvePending || rejectPending} onClick={() => onApprove(item.review_id)}>
          <CheckCircle2Icon className="size-4" />
          {copy.memory.approve}
        </Button>
        <Button size="sm" variant="secondary" disabled={approvePending || rejectPending} onClick={() => onReject(item.review_id)}>
          <XCircleIcon className="size-4" />
          {copy.memory.reject}
        </Button>
      </div>
    </div>
  );
}

function ConflictCard({
  copy,
  conflict,
  pending,
  onResolve,
}: {
  copy: OpsCopy;
  conflict: MemoryConflictView;
  pending: boolean;
  onResolve(conflictId: string, action: string): void;
}) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{conflict.reason}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">
            {conflict.memory_id} {"<->"} {conflict.conflicting_memory_id}
          </div>
        </div>
        <Badge tone={conflict.resolved ? "success" : "danger"}>{conflict.resolved ? copy.memory.resolved : copy.memory.conflicts}</Badge>
      </div>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <div className="min-w-0 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
          <div className="mb-1 text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.memory.leftMemory}</div>
          <div className="overflow-hidden text-sm leading-6 text-[var(--ink)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:4]">
            {conflict.memory_content || conflict.memory_id}
          </div>
        </div>
        <div className="min-w-0 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
          <div className="mb-1 text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.memory.rightMemory}</div>
          <div className="overflow-hidden text-sm leading-6 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:4]">
            {conflict.conflicting_content || conflict.conflicting_memory_id}
          </div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" disabled={pending || conflict.resolved} onClick={() => onResolve(conflict.conflict_id, "keep_both")}>
          {copy.memory.keepBoth}
        </Button>
        <Button size="sm" variant="secondary" disabled={pending || conflict.resolved} onClick={() => onResolve(conflict.conflict_id, "keep_memory")}>
          {copy.memory.keepLeft}
        </Button>
        <Button size="sm" variant="secondary" disabled={pending || conflict.resolved} onClick={() => onResolve(conflict.conflict_id, "keep_conflicting")}>
          {copy.memory.keepRight}
        </Button>
      </div>
    </div>
  );
}

function StalenessCard({
  copy,
  item,
  pending,
  onGovern,
}: {
  copy: OpsCopy;
  item: MemoryStalenessEntryView;
  pending: boolean;
  onGovern(memoryId: string, action: string): void;
}) {
  const tone = tierTone(item.tier);
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{item.memory_id}</div>
          <div className="mt-1 truncate text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{item.layer_id}</div>
        </div>
        <Badge tone={tone}>{item.tier ?? "cold"}</Badge>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <ScoreBar label={copy.memory.staleScore} value={item.stale_score} tone={(item.stale_score ?? 0) > 0.72 ? "danger" : "warning"} />
        <ScoreBar label={copy.memory.retentionScore} value={item.retention_score} tone={tone} />
        <ScoreBar label={copy.memory.reinforcement} value={item.reinforcement_boost} tone="success" />
        <ScoreBar label={copy.memory.temporalDecay} value={item.temporal_decay} tone="warning" />
      </div>
      <div className="mt-3 text-sm leading-6 text-[var(--muted)]">{item.reason}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        <Badge tone="neutral">
          {copy.memory.accessCount}: {item.access_count ?? 0}
        </Badge>
        <Badge tone="neutral">
          {copy.memory.salience}: {pct(item.salience)}
        </Badge>
        <Badge tone="neutral">
          {copy.memory.lastAccessed}: {formatDate(item.last_accessed_at, copy.common.none)}
        </Badge>
        <Badge tone="neutral">
          {copy.memory.expiresAt}: {formatDate(item.expires_at, copy.common.none)}
        </Badge>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" disabled={pending} onClick={() => onGovern(item.memory_id, "reinforce")}>
          {copy.memory.reinforceMemory}
        </Button>
        <Button size="sm" variant="secondary" disabled={pending} onClick={() => onGovern(item.memory_id, "refresh")}>
          <RotateCwIcon className="size-4" />
          {copy.memory.refreshMemory}
        </Button>
        <Button size="sm" variant="secondary" disabled={pending} onClick={() => onGovern(item.memory_id, "review")}>
          <ListChecksIcon className="size-4" />
          {copy.memory.reviewMemory}
        </Button>
        <Button size="sm" variant="secondary" disabled={pending} onClick={() => onGovern(item.memory_id, "archive")}>
          {copy.memory.archiveMemory}
        </Button>
      </div>
    </div>
  );
}

function GovernancePlanItemCard({ copy, item }: { copy: OpsCopy; item: MemoryGovernancePlanItemView }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{item.memory_id}</div>
          <div className="mt-1 truncate text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
            {item.layer_id ?? copy.common.none} · {item.store_id}
          </div>
        </div>
        <Badge tone={item.action === "archive" ? "danger" : item.action === "review" ? "warning" : "success"}>{item.action}</Badge>
      </div>
      <div className="mt-2 line-clamp-2 text-sm leading-6 text-[var(--muted)]">{item.reason}</div>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <ScoreBar label={copy.memory.staleScore} value={item.stale_score} tone={(item.stale_score ?? 0) > 0.72 ? "danger" : "warning"} />
        <ScoreBar label={copy.memory.retentionScore} value={item.retention_score} tone={tierTone(item.tier)} />
        <CompactMetric label={copy.memory.accessCount} value={String(item.access_count ?? 0)} />
      </div>
    </div>
  );
}

function MaintenanceRunCard({ copy, run }: { copy: OpsCopy; run: MemoryMaintenanceResponse }) {
  const before = run.health_before?.quality_score;
  const after = run.health_after?.quality_score;
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="min-w-0 truncate text-sm font-semibold text-[var(--ink)]">{run.run_id}</div>
        <Badge tone={run.status === "completed" || run.status === "noop" ? "success" : run.status === "partial" ? "warning" : "neutral"}>
          {run.status}
        </Badge>
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <CompactMetric label={copy.memory.pendingUpdates} value={String(run.update_queue_pending ?? 0)} />
        <CompactMetric label={copy.memory.drainedUpdates} value={String(run.update_queue_drained ?? 0)} />
        <CompactMetric label={copy.memory.reflectionDue} value={String(run.reflection_jobs_due ?? 0)} />
        <CompactMetric label={copy.memory.reflectionJobs} value={String(run.reflection_jobs_run ?? 0)} />
        <CompactMetric label={copy.memory.governanceCandidates} value={String(run.governance?.candidate_count ?? 0)} />
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <Badge tone={run.dry_run ? "neutral" : "success"}>{run.dry_run ? copy.memory.dryRun : copy.memory.executed}</Badge>
        <Badge tone="neutral">
          {copy.memory.executed}: {run.governance?.executed_count ?? 0}
        </Badge>
        <Badge tone={(run.governance?.skipped_count ?? 0) > 0 ? "warning" : "neutral"}>
          {copy.memory.skipped}: {run.governance?.skipped_count ?? 0}
        </Badge>
        {before !== undefined || after !== undefined ? (
          <Badge tone="neutral">
            {copy.memory.qualityScore}: {before !== undefined ? pct(before) : "--"} → {after !== undefined ? pct(after) : "--"}
          </Badge>
        ) : null}
      </div>
      {run.errors?.length ? (
        <div className="mt-2 space-y-1">
          {run.errors.slice(0, 3).map((item) => (
            <div key={item} className="text-xs text-[var(--danger)]">{item}</div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function MaintenanceAutomationCard({
  copy,
  status,
  latestRun,
  pending,
  onRunDueCheck,
}: {
  copy: OpsCopy;
  status: MemoryMaintenanceAutomationStatusResponse | undefined;
  latestRun: MemoryMaintenanceAutomationRunResponse | undefined;
  pending: boolean;
  onRunDueCheck(): void;
}) {
  const lastCounts = status?.last_counts ?? {};
  const countEntries = Object.entries(lastCounts)
    .filter(([, value]) => value !== null && value !== undefined)
    .slice(0, 4);
  const latestReport = latestRun?.report ?? null;
  return (
    <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{copy.memory.automationStatus}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">
            {copy.memory.nextRun}: {formatDate(status?.next_run_at, copy.common.none)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={status?.enabled ? "success" : "neutral"}>
            {status?.enabled ? copy.common.enabled : copy.common.disabled}
          </Badge>
          <Badge tone={status?.dry_run ? "neutral" : "success"}>
            {status?.dry_run ? copy.memory.backgroundDryRun : copy.memory.executed}
          </Badge>
          <Button size="sm" variant="secondary" disabled={pending} onClick={onRunDueCheck}>
            <RotateCwIcon className={cn("size-4", pending ? "animate-spin" : "")} />
            {copy.memory.forceRun}
          </Button>
        </div>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <CompactMetric label={copy.memory.lastStatus} value={status?.last_status ?? latestRun?.reason ?? copy.common.none} />
        <CompactMetric label={copy.memory.lastRun} value={formatDate(status?.last_run_at, copy.common.none)} />
        <CompactMetric label={copy.memory.lastRunId} value={status?.last_run_id ?? latestReport?.run_id ?? copy.common.none} />
        <CompactMetric label={copy.memory.interval} value={formatDuration(status?.interval_seconds, copy.common.none)} />
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <Badge tone="neutral">
          {copy.memory.governancePolicy}: {status?.policy ?? copy.common.none}
        </Badge>
        <Badge tone="neutral">
          {copy.memory.governanceCandidates}: {status?.limit ?? 0}
        </Badge>
        <Badge tone="neutral">
          {copy.memory.errors}: {status?.last_error_count ?? status?.last_errors?.length ?? 0}
        </Badge>
        {countEntries.map(([key, value]) => (
          <Badge key={key} tone="neutral">
            {key}: {String(value)}
          </Badge>
        ))}
      </div>
      {status?.last_errors?.length ? (
        <div className="mt-2 space-y-1">
          {status.last_errors.slice(0, 3).map((item) => (
            <div key={item} className="text-xs text-[var(--danger)]">{item}</div>
          ))}
        </div>
      ) : null}
      {latestReport ? (
        <div className="mt-3">
          <MaintenanceRunCard copy={copy} run={latestReport} />
        </div>
      ) : null}
    </div>
  );
}

function BenchmarkCaseCard({ copy, item }: { copy: OpsCopy; item: MemoryRecallBenchmarkCaseResultView }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{item.query}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">{item.case_id}</div>
        </div>
        <Badge tone={item.passed ? "success" : "danger"}>{item.passed ? copy.memory.benchmarkPassed : copy.memory.benchmarkFailed}</Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <ScoreBar label={copy.memory.score} value={item.score} tone={item.passed ? "success" : "danger"} />
        <CompactMetric label={copy.memory.evidence} value={String(item.evidence_count ?? item.top_evidence?.length ?? 0)} />
        <CompactMetric label={copy.memory.falsePositives} value={String(item.false_positive_count ?? 0)} />
      </div>
      {item.summary ? <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{item.summary}</div> : null}
      {(item.missing_expectations?.length ?? 0) > 0 ? (
        <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.memory.missingExpectations}</div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {item.missing_expectations.map((value) => (
              <Badge key={value} tone="danger">{value}</Badge>
            ))}
          </div>
        </div>
      ) : null}
      {(item.false_positives?.length ?? 0) > 0 ? (
        <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.memory.falsePositives}</div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {item.false_positives.map((value) => (
              <Badge key={value} tone="warning">{value}</Badge>
            ))}
          </div>
        </div>
      ) : null}
      {item.top_evidence?.length ? <EvidenceList copy={copy} evidence={item.top_evidence.slice(0, 3)} /> : null}
    </div>
  );
}

function BenchmarkSuiteCard({
  copy,
  suite,
  latestRun,
  onRun,
  isRunning,
}: {
  copy: OpsCopy;
  suite: MemoryRecallBenchmarkSuiteView;
  latestRun?: MemoryRecallBenchmarkRunView;
  onRun: (suiteId: string) => void;
  isRunning: boolean;
}) {
  const latestPassed = latestRun?.report?.passed ?? suite.latest_passed;
  const latestScore = latestRun?.report?.score ?? suite.latest_score;
  const latestRunAt = latestRun?.created_at ?? suite.latest_run_at;
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{suite.name || suite.suite_id}</div>
          <div className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--muted)]">{suite.description || suite.suite_id}</div>
        </div>
        <Badge tone={suite.enabled ? "success" : "neutral"}>{suite.enabled ? copy.memory.active : copy.memory.inactive}</Badge>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <Badge tone="neutral">
          {copy.memory.entries}: {suite.cases?.length ?? 0}
        </Badge>
        {typeof latestScore === "number" ? (
          <Badge tone={latestPassed ? "success" : "danger"}>
            {copy.memory.latestRun}: {pct(latestScore)}
          </Badge>
        ) : null}
        {latestRunAt ? <Badge tone="neutral">{new Date(latestRunAt).toLocaleString()}</Badge> : null}
      </div>
      <Button
        type="button"
        className="mt-3"
        size="sm"
        variant="secondary"
        disabled={!suite.enabled || !(suite.cases?.length ?? 0) || isRunning}
        onClick={() => onRun(suite.suite_id)}
      >
        <ActivityIcon className={cn("size-4", isRunning ? "animate-spin" : "")} />
        {copy.memory.runSuite}
      </Button>
    </div>
  );
}

function EvidenceList({ copy, evidence }: { copy: OpsCopy; evidence: RecallEvidenceView[] }) {
  return (
    <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
      <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.memory.topEvidence}</div>
      <div className="mt-2 space-y-2">
        {evidence.map((item) => (
          <div key={item.evidence_id} className="min-w-0">
            <div className="flex min-w-0 items-center justify-between gap-2">
              <div className="min-w-0 truncate text-xs font-semibold text-[var(--ink)]">{item.source_kind}</div>
              <Badge tone="neutral">{pct(item.final_score ?? item.score)}</Badge>
            </div>
            <div className="mt-1 overflow-hidden text-xs leading-5 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2]">
              {item.excerpt || item.reason || item.evidence_id}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function IssueRow({ issue }: { issue: MemoryQualityIssueView }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="min-w-0 truncate text-sm font-medium text-[var(--ink)]">{issue.kind}</div>
        <Badge tone={severityTone(issue.severity)}>{issue.severity ?? "info"}</Badge>
      </div>
      <div className="mt-1 text-xs leading-5 text-[var(--muted)]">{issue.message}</div>
      {issue.recommendation ? (
        <div className="mt-1 text-xs leading-5 text-[var(--ink)]">{issue.recommendation}</div>
      ) : null}
    </div>
  );
}

function CandidateAuditCard({ copy, item }: { copy: OpsCopy; item: MemoryCandidateAuditEntryView }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={actionTone(item.action)}>{item.action}</Badge>
            <Badge tone="neutral">
              {copy.memory.qualityScore}: {pct(item.quality_score)}
            </Badge>
            <Badge tone="neutral">
              {copy.memory.evidence}: {item.evidence_count ?? item.evidence_refs?.length ?? 0}
            </Badge>
          </div>
          <div className="mt-2 line-clamp-2 text-sm leading-5 text-[var(--ink)]">{item.candidate_preview || item.category}</div>
        </div>
        <div className="shrink-0 text-right text-[10px] uppercase tracking-wide text-[var(--muted)]">
          {item.created_at ? new Date(item.created_at).toLocaleString() : item.category}
        </div>
      </div>
      <div className="mt-2 space-y-1 text-xs leading-5 text-[var(--muted)]">
        <div>
          {copy.memory.reason}: <span className="text-[var(--ink)]">{item.reason || item.quality_decision}</span>
        </div>
        {item.blockers?.length ? (
          <div>
            {copy.memory.blockers}: <span className="text-[var(--ink)]">{item.blockers.join(", ")}</span>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Badge tone="neutral">{item.store_id || item.layer_id || "memory"}</Badge>
          <Badge tone="neutral">{item.category}</Badge>
          {item.target_id ? <Badge tone="neutral">{item.target_id}</Badge> : null}
        </div>
      </div>
    </div>
  );
}

function ProfileFacetCard({
  copy,
  facet,
  onGovern,
  pending,
}: {
  copy: OpsCopy;
  facet: ProfileFacetView;
  onGovern: (facetId: string, action: string) => void;
  pending: boolean;
}) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium text-[var(--ink)]">{facet.class_id}</span>
            <Badge tone={profileFacetTone(facet)}>{profileStateLabel(copy, facet.state)}</Badge>
            {facet.user_state !== "auto" ? <Badge tone="neutral">{facet.user_state}</Badge> : null}
            {facet.prompt_visible ? <Badge tone="success">{copy.memory.profileVisible}</Badge> : null}
            {facet.source_polluted ? <Badge tone="warning">{copy.memory.polluted}</Badge> : null}
          </div>
          <div className="mt-1 truncate font-mono text-[11px] text-[var(--muted)]">{facet.key}</div>
        </div>
        <div className="shrink-0 text-right font-mono text-xs text-[var(--muted)]">{(facet.stability_score ?? 0).toFixed(2)}</div>
      </div>
      <div className="mt-2 overflow-hidden text-xs leading-5 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:3]">
        {facet.value}
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <Badge tone="neutral">
          {copy.memory.confidence}: {pct(facet.confidence)}
        </Badge>
        <Badge tone="neutral">
          {copy.memory.salience}: {pct(facet.salience)}
        </Badge>
        <Badge tone="neutral">
          {copy.memory.evidence}: {facet.evidence_refs?.length ?? 0}
        </Badge>
        {facet.reason ? <Badge tone="neutral">{facet.reason}</Badge> : null}
      </div>
      {facet.pollution_reasons?.length ? (
        <div className="mt-2 text-xs leading-5 text-[var(--muted)]">{facet.pollution_reasons.join(", ")}</div>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {facet.user_state === "pinned" ? (
          <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={() => onGovern(facet.facet_id, "unpin")}>
            {copy.memory.unpin}
          </Button>
        ) : (
          <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={() => onGovern(facet.facet_id, "pin")}>
            {copy.memory.pin}
          </Button>
        )}
        {facet.user_state === "forgotten" ? (
          <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={() => onGovern(facet.facet_id, "reset")}>
            {copy.memory.resetFacet}
          </Button>
        ) : (
          <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={() => onGovern(facet.facet_id, "forget")}>
            {copy.memory.forget}
          </Button>
        )}
      </div>
    </div>
  );
}

function ProfileFacetAuditRow({ item }: { item: ProfileFacetAuditEntryView }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 text-xs text-[var(--muted)]">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <div className="min-w-0 truncate text-[var(--ink)]">{item.action}</div>
        <div className="shrink-0 font-mono">{new Date(item.created_at).toLocaleString()}</div>
      </div>
      <div className="mt-1 truncate font-mono">{item.facet_id}</div>
      {item.reason ? <div className="mt-1 line-clamp-2">{item.reason}</div> : null}
    </div>
  );
}

export function MemoryGovernancePanel({ copy }: MemoryGovernancePanelProps) {
  const [activeSection, setActiveSection] = React.useState<MemoryGovernanceSection>("overview");
  const [activeOverviewDrilldown, setActiveOverviewDrilldown] = React.useState<MemoryOverviewDrilldown>("health");
  const overviewVisible = activeSection === "overview";
  const overviewHealthVisible = overviewVisible && activeOverviewDrilldown === "health";
  const overviewProvidersVisible = overviewVisible && activeOverviewDrilldown === "providers";
  const overviewAuditVisible = overviewVisible && activeOverviewDrilldown === "audit";
  const profileVisible = activeSection === "profile";
  const reviewVisible = activeSection === "review";
  const benchmarkVisible = activeSection === "benchmark";
  const maintenanceVisible = activeSection === "maintenance";
  const overview = useMemoryOverview({ enabled: overviewVisible });
  const audit = useMemoryAdminAudit({ enabled: overviewAuditVisible });
  const health = useMemoryHealth({ enabled: overviewHealthVisible });
  const providers = useMemoryProviders({ enabled: overviewProvidersVisible });
  const profileEntries = useMemoryLayerEntries("user", { enabled: benchmarkVisible });
  const workspaceEntries = useMemoryLayerEntries("workspace", { enabled: benchmarkVisible });
  const profileFacets = useProfileFacets({ enabled: profileVisible });
  const profileFacetAudit = useProfileFacetAudit(8, { enabled: profileVisible });
  const review = useMemoryReview({ enabled: reviewVisible });
  const conflicts = useMemoryConflicts({ enabled: reviewVisible });
  const staleness = useMemoryStaleness({ enabled: maintenanceVisible });
  const flushMemory = useFlushMemory();
  const benchmark = useRunMemoryBenchmark();
  const benchmarkSuites = useMemoryBenchmarkSuites({ enabled: benchmarkVisible });
  const benchmarkRuns = useMemoryBenchmarkRuns(null, { enabled: benchmarkVisible });
  const runBenchmarkSuite = useRunMemoryBenchmarkSuite();
  const batchGovernMemory = useBatchGovernMemory();
  const maintenance = useRunMemoryMaintenance();
  const maintenanceAutomation = useMemoryMaintenanceAutomation({ enabled: maintenanceVisible });
  const runMaintenanceAutomation = useRunMemoryMaintenanceAutomation();
  const approveReview = useApproveMemoryReview();
  const rejectReview = useRejectMemoryReview();
  const batchReview = useBatchMemoryReview();
  const resolveConflict = useResolveMemoryConflict();
  const governMemory = useGovernMemory();
  const governProfileFacet = useGovernProfileFacet();
  const rebuildProfileFacets = useRebuildProfileFacets();

  const report = health.data;
  const stores = report?.stores ?? [];
  const issues = report?.issues ?? [];
  const recommendations = report?.recommendations ?? [];
  const providerHealth = Object.entries(report?.provider_health ?? {});
  const facetItems = profileFacets.data?.items ?? [];
  const profilePolicy = profileFacets.data?.policy;
  const reviewItems = review.data ?? [];
  const candidateAudit = audit.data?.candidate_audit ?? [];
  const conflictItems = conflicts.data ?? [];
  const staleItems = staleness.data ?? [];
  const hotStores = stores.reduce((total, store) => total + (store.hot_count ?? 0), 0);
  const warmStores = stores.reduce((total, store) => total + (store.warm_count ?? 0), 0);
  const coldStores = stores.reduce((total, store) => total + (store.cold_count ?? 0), 0);
  const providerCount = report?.provider_count ?? (overviewProvidersVisible ? providers.data?.length : undefined);
  const busy =
    (overviewVisible && overview.isFetching) ||
    (overviewHealthVisible && health.isFetching) ||
    (overviewAuditVisible && audit.isFetching) ||
    (overviewProvidersVisible && providers.isFetching) ||
    (profileVisible && (profileFacets.isFetching || profileFacetAudit.isFetching)) ||
    (reviewVisible && (review.isFetching || conflicts.isFetching)) ||
    (benchmarkVisible && (profileEntries.isFetching || workspaceEntries.isFetching || benchmarkSuites.isFetching || benchmarkRuns.isFetching)) ||
    (maintenanceVisible && (staleness.isFetching || maintenanceAutomation.isFetching));
  const benchmarkCases = React.useMemo(() => {
    const entries = [...(profileEntries.data ?? []), ...(workspaceEntries.data ?? [])];
    return entries.slice(0, 4).map((entry, index) => {
      const phrase = compactBenchmarkPhrase(entry.content);
      return {
        case_id: `ops-${entry.entry_id || index}`,
        query: phrase || entry.category || "memory",
        thread_id: "ops-benchmark",
        expected_terms: phrase ? [phrase] : [entry.category],
        expected_memory_ids: entry.memory_id ? [entry.memory_id] : entry.entry_id ? [entry.entry_id] : [],
        expected_archive_thread_ids: [],
        forbidden_terms: [],
        forbidden_memory_ids: [],
        min_score: 0.5,
      };
    });
  }, [profileEntries.data, workspaceEntries.data]);

  async function refreshAll() {
    if (overviewVisible) {
      const refetches: Array<Promise<unknown>> = [overview.refetch()];
      if (overviewHealthVisible) {
        refetches.push(health.refetch());
      }
      if (overviewAuditVisible) {
        refetches.push(audit.refetch());
      }
      if (overviewProvidersVisible) {
        refetches.push(providers.refetch());
      }
      await Promise.all(refetches);
      return;
    }
    if (profileVisible) {
      await Promise.all([profileFacets.refetch(), profileFacetAudit.refetch()]);
      return;
    }
    if (reviewVisible) {
      await Promise.all([review.refetch(), conflicts.refetch()]);
      return;
    }
    if (benchmarkVisible) {
      await Promise.all([profileEntries.refetch(), workspaceEntries.refetch(), benchmarkSuites.refetch(), benchmarkRuns.refetch()]);
      return;
    }
    if (maintenanceVisible) {
      await Promise.all([staleness.refetch(), maintenanceAutomation.refetch()]);
    }
  }

  function runMaintenance(dryRun: boolean) {
    void maintenance.mutateAsync({
      dry_run: dryRun,
      policy: "balanced",
      layer_id: null,
      limit: 12,
      source: "ops",
      run_reflection_due_jobs: true,
    });
  }

  function runMaintenanceDueCheck() {
    void runMaintenanceAutomation.mutateAsync({ force_run: true });
  }

  function approveReviewItem(reviewId: string) {
    void approveReview.mutateAsync(reviewId);
  }

  function rejectReviewItem(reviewId: string) {
    void rejectReview.mutateAsync(reviewId);
  }

  function batchReviewItems(action: "approve" | "reject") {
    const ids = reviewItems.map((item) => item.review_id).filter(Boolean);
    if (!ids.length) {
      return;
    }
    void batchReview.mutateAsync(action === "approve" ? { approve: ids, reject: [] } : { approve: [], reject: ids });
  }

  function resolveConflictItem(conflictId: string, action: string) {
    void resolveConflict.mutateAsync({ conflictId, action });
  }

  function governStaleMemory(memoryId: string, action: string) {
    void governMemory.mutateAsync({ memoryId, action });
  }

  function governFacet(facetId: string, action: string) {
    void governProfileFacet.mutateAsync({ facetId, action, reason: `ops profile facet ${action}` });
  }

  function runGovernancePlan(dryRun: boolean) {
    void batchGovernMemory.mutateAsync({
      policy: "balanced",
      layer_id: null,
      limit: 12,
      dry_run: dryRun,
      source: "ops",
    });
  }

  function runStoredBenchmarkSuite(suiteId: string) {
    void runBenchmarkSuite.mutateAsync({ suiteId, evidenceLimit: 4 });
  }

  return (
    <ScrollArea className="h-full min-h-0" hideHorizontalScrollbar>
      <div className="space-y-4 pr-2">
        <section className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5 shadow-[var(--shadow-card)]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <DatabaseIcon className="size-4 text-[var(--primary)]" />
                <h2 className="text-base font-semibold text-[var(--ink)]">{copy.memory.title}</h2>
              </div>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">{copy.memory.description}</p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <Button type="button" size="sm" variant="secondary" onClick={() => void refreshAll()} disabled={busy}>
                <RefreshCwIcon className={cn("size-4", busy ? "animate-spin" : "")} />
                {copy.memory.refresh}
              </Button>
              <Button type="button" size="sm" variant="primary" onClick={() => void flushMemory.mutateAsync({})} disabled={flushMemory.isPending}>
                <RotateCwIcon className={cn("size-4", flushMemory.isPending ? "animate-spin" : "")} />
                {copy.memory.flush}
              </Button>
            </div>
          </div>

          <div className="mt-5 grid gap-3 md:grid-cols-3 xl:grid-cols-6">
            <CompactMetric label={copy.memory.health} value={report?.status ?? copy.common.loading} />
            <CompactMetric label={copy.memory.qualityScore} value={report ? pct(report.quality_score) : "--"} />
            <CompactMetric label={copy.memory.stores} value={String(overview.data?.store_count ?? stores.length)} />
            <CompactMetric label={copy.memory.pendingReview} value={String(report?.pending_review_count ?? reviewItems.length)} />
            <CompactMetric label={copy.memory.conflicts} value={String(report?.conflict_count ?? conflictItems.length)} />
            <CompactMetric label={copy.memory.archiveTurns} value={String(report?.archive_turn_count ?? overview.data?.archive_turn_count ?? 0)} />
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <Badge tone={statusTone(report?.status)}>
              {copy.memory.health}: {report?.status ?? copy.common.loading}
            </Badge>
            <Badge tone={(report?.stale_count ?? staleItems.length ?? 0) > 0 ? "warning" : "neutral"}>
              {copy.memory.stale}: {report?.stale_count ?? staleItems.length ?? 0}
            </Badge>
            <Badge tone="neutral">
              {copy.memory.providers}: {providerCount ?? "--"}
            </Badge>
            <Badge tone={hotStores ? "success" : "neutral"}>
              <FlameIcon className="size-3" />
              {copy.memory.hot}: {hotStores}
            </Badge>
            <Badge tone={warmStores ? "warning" : "neutral"}>
              <ThermometerSunIcon className="size-3" />
              {copy.memory.warm}: {warmStores}
            </Badge>
            <Badge tone={coldStores ? "danger" : "neutral"}>
              <SnowflakeIcon className="size-3" />
              {copy.memory.cold}: {coldStores}
            </Badge>
            {report?.generated_at ? (
              <Badge tone="neutral">
                {copy.memory.generatedAt}: {new Date(report.generated_at).toLocaleString()}
              </Badge>
            ) : null}
          </div>

          <div className="mt-5 flex flex-wrap gap-2">
            {MEMORY_GOVERNANCE_SECTIONS.map((section) => (
              <Button
                key={section}
                type="button"
                size="sm"
                variant={activeSection === section ? "primary" : "secondary"}
                onClick={() => setActiveSection(section)}
              >
                {memoryGovernanceSectionLabel(copy, section)}
              </Button>
            ))}
          </div>
          {overviewVisible ? (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-[var(--line)] pt-3">
              {MEMORY_OVERVIEW_DRILLDOWNS.map((section) => (
                <Button
                  key={section}
                  type="button"
                  size="sm"
                  variant={activeOverviewDrilldown === section ? "primary" : "secondary"}
                  onClick={() => setActiveOverviewDrilldown(section)}
                >
                  {memoryOverviewDrilldownLabel(copy, section)}
                </Button>
              ))}
            </div>
          ) : null}
        </section>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
          <div className="space-y-4">
            {overviewHealthVisible ? (
              <>
                <OpsPanelCard title={copy.memory.stores}>
                  {!stores.length ? <OpsEmptyState text={copy.memory.noStores} /> : null}
                  <div className="space-y-3">
                    {stores.map((store) => (
                      <StoreHealthCard key={store.store_id} copy={copy} store={store} />
                    ))}
                  </div>
                </OpsPanelCard>

                <OpsPanelCard title={copy.memory.issues}>
                  {!issues.length ? <OpsEmptyState text={copy.memory.noIssues} /> : null}
                  <div className="space-y-2">
                    {issues.slice(0, 12).map((issue) => (
                      <IssueRow key={issue.issue_id} issue={issue} />
                    ))}
                  </div>
                </OpsPanelCard>

              </>
            ) : null}
            {overviewAuditVisible ? (
              <OpsPanelCard title={copy.memory.candidateAudit}>
                {!candidateAudit.length ? <OpsEmptyState text={copy.memory.noCandidateAudit} /> : null}
                {candidateAudit.length ? (
                  <div className="space-y-2">
                    {candidateAudit.slice(0, 8).map((item) => (
                      <CandidateAuditCard key={item.audit_id} copy={copy} item={item} />
                    ))}
                  </div>
                ) : null}
              </OpsPanelCard>
            ) : null}
          </div>

          <div className="space-y-4">
            {overviewProvidersVisible ? (
              <OpsPanelCard title={copy.memory.providers}>
                {!providerHealth.length && !providers.data?.length ? <OpsEmptyState text={copy.common.none} /> : null}
                <div className="space-y-2">
                  {providerHealth.map(([providerId, status]) => (
                    <div key={providerId} className="flex min-w-0 items-center justify-between gap-3 rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
                      <div className="min-w-0 truncate text-sm text-[var(--ink)]">{providerId}</div>
                      <Badge tone={statusTone(status)}>{status}</Badge>
                    </div>
                  ))}
                  {!providerHealth.length
                    ? (providers.data ?? []).map((provider) => (
                        <div key={provider.provider_id} className="flex min-w-0 items-center justify-between gap-3 rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
                          <div className="min-w-0">
                            <div className="truncate text-sm text-[var(--ink)]">{provider.display_name || provider.provider_id}</div>
                            <div className="truncate text-xs text-[var(--muted)]">{provider.family}</div>
                          </div>
                          <Badge tone={statusTone(provider.health)}>{provider.health}</Badge>
                        </div>
                      ))
                    : null}
                </div>
              </OpsPanelCard>
            ) : null}

            {profileVisible ? (
            <OpsPanelCard title={copy.memory.profileFacets}>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={() => void rebuildProfileFacets.mutateAsync()}
                  disabled={rebuildProfileFacets.isPending}
                >
                  <RefreshCwIcon className={cn("size-4", rebuildProfileFacets.isPending ? "animate-spin" : "")} />
                  {copy.memory.rebuildFacets}
                </Button>
                <Badge tone="success">
                  {copy.memory.active}: {facetItems.filter((facet) => facet.state === "active").length}
                </Badge>
                <Badge tone="warning">
                  {copy.memory.provisional}: {facetItems.filter((facet) => facet.state === "provisional").length}
                </Badge>
                <Badge tone="neutral">
                  {copy.memory.candidate}: {facetItems.filter((facet) => facet.state === "candidate").length}
                </Badge>
                <Badge tone="danger">
                  {copy.memory.forgotten}: {facetItems.filter((facet) => facet.user_state === "forgotten" || facet.state === "dropped").length}
                </Badge>
                <Badge tone="neutral">
                  {copy.memory.profileVisible}: {facetItems.filter((facet) => facet.prompt_visible).length}
                </Badge>
                <Badge tone={facetItems.some((facet) => facet.source_polluted) ? "warning" : "neutral"}>
                  {copy.memory.polluted}: {facetItems.filter((facet) => facet.source_polluted).length}
                </Badge>
              </div>

              {profilePolicy ? (
                <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{copy.memory.profilePolicy}</div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    <CompactMetric label={copy.memory.active} value={(profilePolicy.active_threshold ?? 0).toFixed(2)} />
                    <CompactMetric label={copy.memory.provisional} value={(profilePolicy.provisional_threshold ?? 0).toFixed(2)} />
                    <CompactMetric label={copy.memory.candidate} value={(profilePolicy.candidate_threshold ?? 0).toFixed(2)} />
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Badge tone="neutral">
                      {copy.memory.profileBudget}: {Object.entries(profilePolicy.class_budgets ?? {}).map(([key, value]) => `${key}:${value}`).join(", ")}
                    </Badge>
                    <Badge tone="neutral">
                      {copy.memory.profileClass}: {(profilePolicy.require_review_classes ?? []).join(", ") || copy.common.none}
                    </Badge>
                  </div>
                </div>
              ) : null}

              {!facetItems.length ? <OpsEmptyState text={copy.common.none} /> : null}
              <div className="space-y-2">
                {facetItems.slice(0, 10).map((facet) => (
                  <ProfileFacetCard
                    key={facet.facet_id}
                    copy={copy}
                    facet={facet}
                    onGovern={governFacet}
                    pending={governProfileFacet.isPending}
                  />
                ))}
              </div>

              {profileFacetAudit.data?.items?.length ? (
                <div className="space-y-2 pt-2">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{copy.memory.facetAudit}</div>
                  {profileFacetAudit.data.items.slice(0, 4).map((item) => (
                    <ProfileFacetAuditRow key={item.audit_id} item={item} />
                  ))}
                </div>
              ) : null}
            </OpsPanelCard>
            ) : null}

            {benchmarkVisible ? (
            <OpsPanelCard title={copy.memory.recallBenchmark}>
              <div className="flex flex-col gap-3">
                <div className="space-y-2">
                  <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.memory.benchmarkSuites}</div>
                  {!(benchmarkSuites.data?.length ?? 0) ? <OpsEmptyState text={copy.memory.noBenchmarkSuites} /> : null}
                  {benchmarkSuites.data?.slice(0, 4).map((suite) => (
                    <BenchmarkSuiteCard
                      key={suite.suite_id}
                      copy={copy}
                      suite={suite}
                      latestRun={benchmarkRuns.data?.find((run) => run.suite_id === suite.suite_id)}
                      onRun={runStoredBenchmarkSuite}
                      isRunning={runBenchmarkSuite.isPending}
                    />
                  ))}
                </div>
                <div className="grid gap-2">
                  <CompactMetric label={copy.memory.entries} value={String(benchmarkCases.length)} />
                  <CompactMetric
                    label={copy.memory.qualityScore}
                    value={benchmark.data ? pct(benchmark.data.score) : "--"}
                    sub={benchmark.data ? (benchmark.data.passed ? copy.memory.benchmarkPassed : copy.memory.benchmarkFailed) : null}
                  />
                  <CompactMetric label={copy.memory.hitRate} value={benchmark.data ? pct(benchmark.data.recall_hit_rate) : "--"} />
                  <CompactMetric label={copy.memory.falsePositiveRate} value={benchmark.data ? pct(benchmark.data.false_positive_rate) : "--"} />
                  <CompactMetric label={copy.memory.averageEvidence} value={benchmark.data ? String((benchmark.data.average_evidence_count ?? 0).toFixed(1)) : "--"} />
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={!benchmarkCases.length || benchmark.isPending}
                  onClick={() =>
                    void benchmark.mutateAsync({
                      suite_id: "ops-memory-smoke",
                      cases: benchmarkCases,
                      evidence_limit: 4,
                    })
                  }
                >
                  <ActivityIcon className={cn("size-4", benchmark.isPending ? "animate-spin" : "")} />
                  {copy.memory.runBenchmark}
                </Button>
                {!benchmarkCases.length ? <OpsEmptyState text={copy.memory.noBenchmarkCases} /> : null}
                {benchmark.data?.cases?.length ? (
                  <div className="space-y-2">
                    {benchmark.data.cases.slice(0, 4).map((item) => (
                      <BenchmarkCaseCard key={item.case_id} copy={copy} item={item} />
                    ))}
                  </div>
                ) : null}
              </div>
            </OpsPanelCard>
            ) : null}

            {overviewHealthVisible ? (
              <OpsPanelCard title={copy.memory.recommendations}>
                {!recommendations.length ? <OpsEmptyState text={copy.memory.noRecommendations} /> : null}
                <div className="space-y-2">
                  {recommendations.map((item) => (
                    <div key={item} className="flex gap-2 rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 text-sm leading-6 text-[var(--muted)]">
                      <CheckCircle2Icon className="mt-1 size-4 shrink-0 text-[var(--success)]" />
                      <span>{item}</span>
                    </div>
                  ))}
                </div>
              </OpsPanelCard>
            ) : null}

            {reviewVisible ? (
            <OpsPanelCard title={copy.memory.pendingReview}>
              <div className="grid gap-2 sm:grid-cols-3">
                <CompactMetric label={copy.memory.pendingReview} value={String(reviewItems.length)} />
                <CompactMetric label={copy.memory.conflicts} value={String(conflictItems.length)} />
                <CompactMetric label={copy.memory.stale} value={String(staleItems.length)} />
              </div>
              {reviewItems.length ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button size="sm" variant="primary" disabled={approveReview.isPending || rejectReview.isPending || batchReview.isPending} onClick={() => batchReviewItems("approve")}>
                    <ListChecksIcon className="size-4" />
                    {copy.memory.approveAll}
                  </Button>
                  <Button size="sm" variant="secondary" disabled={approveReview.isPending || rejectReview.isPending || batchReview.isPending} onClick={() => batchReviewItems("reject")}>
                    <XCircleIcon className="size-4" />
                    {copy.memory.rejectAll}
                  </Button>
                </div>
              ) : null}
              {reviewItems.length > 0 ? (
                <div className="mt-3 space-y-3">
                  {reviewItems.slice(0, 6).map((item) => (
                    <ReviewItemCard
                      key={item.review_id}
                      copy={copy}
                      item={item}
                      approvePending={approveReview.isPending || batchReview.isPending}
                      rejectPending={rejectReview.isPending || batchReview.isPending}
                      onApprove={approveReviewItem}
                      onReject={rejectReviewItem}
                    />
                  ))}
                </div>
              ) : (
                <OpsEmptyState text={copy.memory.noReviewItems} />
              )}
            </OpsPanelCard>
            ) : null}

            {reviewVisible ? (
            <OpsPanelCard title={copy.memory.conflicts}>
              <div className="flex items-center gap-2 text-sm text-[var(--muted)]">
                <GitCompareArrowsIcon className="size-4 text-[var(--primary)]" />
                <span>{copy.memory.conflictHelp}</span>
              </div>
              {conflictItems.length ? (
                <div className="mt-3 space-y-3">
                  {conflictItems.slice(0, 6).map((item) => (
                    <ConflictCard
                      key={item.conflict_id}
                      copy={copy}
                      conflict={item}
                      pending={resolveConflict.isPending}
                      onResolve={resolveConflictItem}
                    />
                  ))}
                </div>
              ) : (
                <OpsEmptyState text={copy.memory.noConflicts} />
              )}
            </OpsPanelCard>
            ) : null}

            {maintenanceVisible ? (
            <OpsPanelCard title={copy.memory.retention}>
              <div className="grid gap-2 sm:grid-cols-3">
                <CompactMetric label={copy.memory.hot} value={String(hotStores)} />
                <CompactMetric label={copy.memory.warm} value={String(warmStores)} />
                <CompactMetric label={copy.memory.cold} value={String(coldStores)} />
              </div>
              <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-[var(--ink)]">{copy.memory.maintenance}</div>
                    <div className="mt-1 truncate text-xs text-[var(--muted)]">{copy.memory.maintenanceHint}</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="secondary" disabled={maintenance.isPending} onClick={() => runMaintenance(true)}>
                      <ListChecksIcon className="size-4" />
                      {copy.memory.planMaintenance}
                    </Button>
                    <Button size="sm" variant="primary" disabled={maintenance.isPending} onClick={() => runMaintenance(false)}>
                      <RotateCwIcon className={cn("size-4", maintenance.isPending ? "animate-spin" : "")} />
                      {copy.memory.runMaintenance}
                    </Button>
                  </div>
                </div>
                <MaintenanceAutomationCard
                  copy={copy}
                  status={maintenanceAutomation.data}
                  latestRun={runMaintenanceAutomation.data}
                  pending={runMaintenanceAutomation.isPending}
                  onRunDueCheck={runMaintenanceDueCheck}
                />
                {maintenance.data ? <div className="mt-3"><MaintenanceRunCard copy={copy} run={maintenance.data} /></div> : null}
              </div>
              <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-[var(--ink)]">{copy.memory.governancePlan}</div>
                    <div className="mt-1 truncate text-xs text-[var(--muted)]">
                      {copy.memory.governancePolicy}: balanced · {copy.memory.governanceCandidates}: {batchGovernMemory.data?.candidate_count ?? 0}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="secondary" disabled={batchGovernMemory.isPending} onClick={() => runGovernancePlan(true)}>
                      <ListChecksIcon className="size-4" />
                      {copy.memory.planGovernance}
                    </Button>
                    <Button size="sm" variant="primary" disabled={batchGovernMemory.isPending || !batchGovernMemory.data?.items?.length} onClick={() => runGovernancePlan(false)}>
                      <CheckCircle2Icon className="size-4" />
                      {copy.memory.executeGovernance}
                    </Button>
                  </div>
                </div>
                {batchGovernMemory.data?.errors?.length ? (
                  <div className="mt-2 space-y-1">
                    {batchGovernMemory.data.errors.slice(0, 3).map((item) => (
                      <div key={item} className="text-xs text-[var(--danger)]">{item}</div>
                    ))}
                  </div>
                ) : null}
                {batchGovernMemory.data?.items?.length ? (
                  <div className="mt-3 space-y-2">
                    {batchGovernMemory.data.items.slice(0, 5).map((item) => (
                      <GovernancePlanItemCard key={`${item.memory_id}-${item.action}`} copy={copy} item={item} />
                    ))}
                  </div>
                ) : null}
              </div>
              {staleItems.length ? (
                <div className="mt-3 space-y-3">
                  {staleItems.slice(0, 8).map((item) => (
                    <StalenessCard
                      key={`${item.memory_id}-${item.layer_id}`}
                      copy={copy}
                      item={item}
                      pending={governMemory.isPending}
                      onGovern={governStaleMemory}
                    />
                  ))}
                </div>
              ) : (
                <OpsEmptyState text={copy.memory.noStaleItems} />
              )}
            </OpsPanelCard>
            ) : null}

            {overviewHealthVisible ? (
              <OpsPanelCard title={copy.memory.health}>
                <div className="grid gap-3">
                  <div className="flex items-center gap-2 text-sm text-[var(--muted)]">
                    <ShieldCheckIcon className="size-4 text-[var(--primary)]" />
                    <span>
                      {copy.memory.qualityScore}: {report ? pct(report.quality_score) : copy.common.loading}
                    </span>
                  </div>
                  <ScoreBar label={copy.memory.qualityScore} value={report?.quality_score} tone={statusTone(report?.status)} />
                </div>
              </OpsPanelCard>
            ) : null}
          </div>
        </div>
      </div>
    </ScrollArea>
  );
}

function compactBenchmarkPhrase(content: string) {
  return content
    .replace(/\s+/g, " ")
    .trim()
    .split(/[。！？.!?]/)[0]
    ?.slice(0, 96)
    .trim() ?? "";
}

function memoryGovernanceSectionLabel(copy: OpsCopy, section: MemoryGovernanceSection) {
  switch (section) {
    case "profile":
      return copy.memory.profileFacets;
    case "review":
      return copy.memory.pendingReview;
    case "benchmark":
      return copy.memory.recallBenchmark;
    case "maintenance":
      return copy.memory.retention;
    case "overview":
    default:
      return copy.surfaces.overview;
  }
}

function memoryOverviewDrilldownLabel(copy: OpsCopy, section: MemoryOverviewDrilldown) {
  switch (section) {
    case "providers":
      return copy.memory.providers;
    case "audit":
      return copy.memory.candidateAudit;
    case "health":
    default:
      return copy.memory.health;
  }
}
