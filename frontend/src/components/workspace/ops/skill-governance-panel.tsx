"use client";

import React, { useEffect, useMemo, useState } from "react";
import { ChevronDownIcon, ChevronUpIcon, RefreshCwIcon, SparklesIcon } from "lucide-react";

import type { SkillCuratorAutomationRunResponse, SkillCuratorAutomationStatusResponse, SkillListItemView, SkillView } from "@/src/core/contracts";
import {
  usePromoteSkillProcedure,
  useRejectSkillProcedure,
  useRestoreSkillProcedure,
  useReloadSkills,
  useRunSkillCuratorAutomation,
  useRunSkillCuratorMaintenance,
  useSkillCuratorAutomation,
  useSkill,
  useSkillContent,
  useSkillFile,
  useSkillFiles,
  useSkillProcedures,
  useSkills,
} from "@/src/core/skills/hooks";
import { Badge } from "@/src/components/ui/badge";
import { Button } from "@/src/components/ui/button";
import { ScrollArea } from "@/src/components/ui/scroll-area";

import { OpsEmptyState, OpsJsonBlock, OpsPanelCard, OpsSelectableItem, OpsTagList } from "./shared";
import type { OpsCopy } from "./types";

type SkillGovernancePanelProps = {
  copy: OpsCopy;
  selectedItem: string | null;
  onSelectItem(item: string | null): void;
  onAction(action: string, skillId?: string | null): void;
};

type ProcedureCandidate = {
  procedure_id?: string;
  title?: string;
  trigger?: string;
  expected_outcome?: string;
  status?: string;
  strength?: number;
  confidence?: number;
  frequency?: number;
  steps?: string[];
  evidence_refs?: string[];
  allowed_tools?: string[];
  promoted_skill_id?: string;
  outcome_health?: {
    success_count?: number;
    failure_count?: number;
    success_confidence?: number;
    failure_confidence?: number;
    confidence_success_rate?: number;
  };
  promotion_readiness?: {
    promotable?: boolean;
    readiness_score?: number;
    blockers?: string[];
    recommendation?: string;
    quality_score?: number;
  };
  quality?: {
    quality_score?: number;
    evidence_count?: number;
    source_count?: number;
    step_count?: number;
    tool_count?: number;
    verification_signal?: boolean;
    blockers?: string[];
  };
};

function numberValue(payload: unknown, key: string) {
  if (!payload || typeof payload !== "object") {
    return 0;
  }
  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "number" ? value : 0;
}

function recordCount(payload: unknown) {
  if (!payload || typeof payload !== "object") {
    return 0;
  }
  return Object.values(payload as Record<string, unknown>).reduce<number>(
    (total, value) => total + (typeof value === "number" ? value : 0),
    0,
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

function MaintenanceRunCard({ copy, run }: { copy: OpsCopy; run: Record<string, unknown> | null }) {
  if (!run) {
    return <OpsEmptyState text={copy.common.none} />;
  }
  return (
    <div className="grid gap-2 md:grid-cols-4">
      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
        <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.runId}</div>
        <div className="mt-1 truncate font-mono text-sm font-semibold text-[var(--ink)]">{String(run.run_id ?? run.plan_run_id ?? copy.common.none)}</div>
      </div>
      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
        <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.candidateActions}</div>
        <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{numberValue(run, "candidate_count")}</div>
      </div>
      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
        <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.selectedActions}</div>
        <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{numberValue(run, "selected_count")}</div>
      </div>
      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
        <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.skipped}</div>
        <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{recordCount(run.skipped_actions)}</div>
      </div>
      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 md:col-span-2">
        <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.executed}</div>
        <div className="mt-1 truncate font-mono text-sm font-semibold text-[var(--ink)]">{recordCount(run.actions_executed)}</div>
      </div>
      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 md:col-span-2">
        <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.status}</div>
        <div className="mt-1 truncate text-sm font-semibold text-[var(--ink)]">{String(run.status ?? copy.common.none)}</div>
      </div>
    </div>
  );
}

function SkillAutomationStatusCard({
  copy,
  status,
  latestRun,
  pending,
  onRunDueCheck,
}: {
  copy: OpsCopy;
  status: SkillCuratorAutomationStatusResponse | undefined;
  latestRun: SkillCuratorAutomationRunResponse | undefined;
  pending: boolean;
  onRunDueCheck(): void;
}) {
  const latestReport = latestRun?.report ?? null;
  const lastCounts = status?.last_counts ?? {};
  const countEntries = Object.entries(lastCounts)
    .filter(([, value]) => value !== null && value !== undefined)
    .slice(0, 4);
  const recommendations = status?.last_recommendations ?? [];
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{copy.skills.automationStatus}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">
            {copy.skills.nextRun}: {formatDate(status?.next_run_at, copy.common.none)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={status?.enabled ? "success" : "neutral"}>{status?.enabled ? copy.common.enabled : copy.common.disabled}</Badge>
          <Badge tone={status?.dry_run ? "neutral" : "success"}>{status?.dry_run ? copy.skills.backgroundDryRun : copy.skills.executed}</Badge>
          <Button size="sm" variant="secondary" disabled={pending} onClick={onRunDueCheck}>
            <RefreshCwIcon className={pending ? "size-4 animate-spin" : "size-4"} />
            {copy.skills.forceRun}
          </Button>
        </div>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.lastStatus}</div>
          <div className="mt-1 truncate text-sm font-semibold text-[var(--ink)]">{status?.last_status ?? latestRun?.reason ?? copy.common.none}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.lastRun}</div>
          <div className="mt-1 truncate text-sm font-semibold text-[var(--ink)]">{formatDate(status?.last_run_at, copy.common.none)}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.lastRunId}</div>
          <div className="mt-1 truncate font-mono text-sm font-semibold text-[var(--ink)]">{status?.last_run_id ?? String(latestReport?.run_id ?? copy.common.none)}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.interval}</div>
          <div className="mt-1 truncate font-mono text-sm font-semibold text-[var(--ink)]">{formatDuration(status?.interval_seconds, copy.common.none)}</div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <Badge tone="neutral">
          {copy.skills.autoMerge}: {status?.auto_merge ? copy.common.yes : copy.common.no}
        </Badge>
        <Badge tone={status?.pin_protection ? "success" : "warning"}>
          {copy.skills.pinProtection}: {status?.pin_protection ? copy.common.yes : copy.common.no}
        </Badge>
        <Badge tone="neutral">
          {copy.skills.recommendations}: {status?.last_recommendation_count ?? recommendations.length}
        </Badge>
        {countEntries.map(([key, value]) => (
          <Badge key={key} tone="neutral">
            {key}: {String(value)}
          </Badge>
        ))}
      </div>
      {recommendations.length ? (
        <div className="mt-3 space-y-2">
          {recommendations.slice(0, 3).map((item, index) => (
            <div key={`${String(item.action ?? "recommendation")}-${index}`} className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-xs leading-5 text-[var(--muted)]">
              <span className="font-semibold text-[var(--ink)]">{String(item.action ?? copy.skills.recommendations)}</span>
              {item.skill_id ? <span> · {String(item.skill_id)}</span> : null}
              {item.procedure_id ? <span> · {String(item.procedure_id)}</span> : null}
              {item.reason ? <div className="mt-1 line-clamp-2">{String(item.reason)}</div> : null}
            </div>
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

function pathSummary(skill: SkillListItemView | SkillView) {
  return [
    skill.enabled ? "enabled" : "disabled",
    skill.source_scope ?? null,
    skill.trust ?? null,
    skill.version ?? null,
  ].filter(Boolean).join(" · ");
}

function procedureItems(payload: unknown): ProcedureCandidate[] {
  if (!payload || typeof payload !== "object") {
    return [];
  }
  const items = (payload as { items?: unknown }).items;
  if (!Array.isArray(items)) {
    return [];
  }
  return items.filter((item): item is ProcedureCandidate => Boolean(item && typeof item === "object"));
}

function pct(value: number | null | undefined) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function procedureStatus(copy: OpsCopy, procedure: ProcedureCandidate) {
  const promoted = procedure.status === "promoted";
  const rejected = procedure.status === "rejected";
  const promotable = !promoted && Boolean(procedure.promotion_readiness?.promotable ?? ((procedure.strength ?? 0) >= 0.72 || (procedure.frequency ?? 0) >= 3));
  return {
    promoted,
    rejected,
    tone: promoted ? "success" as const : rejected ? "danger" as const : promotable ? "warning" as const : "neutral" as const,
    label: promoted
      ? copy.skills.promoted
      : rejected
        ? copy.skills.rejected
        : promotable
          ? copy.skills.promotable
          : procedure.status ?? "candidate",
  };
}

function ProcedureCandidateCard({
  copy,
  procedure,
  promotePending,
  rejectPending,
  restorePending,
  onPromote,
  onReject,
  onRestore,
}: {
  copy: OpsCopy;
  procedure: ProcedureCandidate;
  promotePending: boolean;
  rejectPending: boolean;
  restorePending: boolean;
  onPromote(procedureId: string): void;
  onReject(procedureId: string): void;
  onRestore(procedureId: string): void;
}) {
  const id = procedure.procedure_id ?? "";
  const status = procedureStatus(copy, procedure);
  const qualityScore = procedure.quality?.quality_score ?? procedure.promotion_readiness?.quality_score;
  const readinessBlockers = procedure.promotion_readiness?.blockers ?? [];
  const qualityBlockers = procedure.quality?.blockers ?? [];
  const mergedBlockers = Array.from(new Set([...readinessBlockers, ...qualityBlockers])).slice(0, 4);
  const promoteDisabled = !id || status.promoted || status.rejected || promotePending || rejectPending || restorePending;
  const rejectDisabled = !id || status.promoted || status.rejected || promotePending || rejectPending || restorePending;
  const restoreDisabled = !id || !status.rejected || restorePending || rejectPending || promotePending;

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{procedure.title || id}</div>
          <div className="mt-1 text-xs leading-5 text-[var(--muted)]">
            {copy.skills.procedureId}: {id || copy.common.none}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Badge tone={status.tone}>{status.label}</Badge>
          <Button size="sm" variant="primary" disabled={promoteDisabled} onClick={() => onPromote(id)}>
            {copy.skills.promote}
          </Button>
          <Button size="sm" variant="ghost" disabled={rejectDisabled} onClick={() => onReject(id)}>
            {copy.skills.reject}
          </Button>
          {status.rejected ? (
            <Button size="sm" variant="secondary" disabled={restoreDisabled} onClick={() => onRestore(id)}>
              {copy.skills.restoreProcedure}
            </Button>
          ) : null}
        </div>
      </div>
      <div className="mt-3 grid gap-2 md:grid-cols-4">
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.strength}</div>
          <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{pct(procedure.strength)}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.qualityScore}</div>
          <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{pct(qualityScore)}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.frequency}</div>
          <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{procedure.frequency ?? 0}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.confidence}</div>
          <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{pct(procedure.confidence)}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.allowedTools}</div>
          <div className="mt-1 truncate text-sm font-semibold text-[var(--ink)]">{procedure.allowed_tools?.length ?? 0}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.success}</div>
          <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{procedure.outcome_health?.success_count ?? 0} · {pct(procedure.outcome_health?.success_confidence)}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.failure}</div>
          <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{procedure.outcome_health?.failure_count ?? 0} · {pct(procedure.outcome_health?.failure_confidence)}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.promotionReadiness}</div>
          <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{pct(procedure.promotion_readiness?.readiness_score)}</div>
        </div>
        <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.skills.blockers}</div>
          <div className="mt-1 truncate text-sm font-semibold text-[var(--ink)]">{mergedBlockers.length ? mergedBlockers.join(", ") : copy.common.none}</div>
        </div>
      </div>
      <div className="mt-3 text-sm leading-6 text-[var(--muted)]">
        <span className="font-medium text-[var(--ink)]">{copy.skills.trigger}: </span>
        {procedure.trigger || copy.common.none}
      </div>
      {procedure.expected_outcome ? (
        <div className="mt-1 text-sm leading-6 text-[var(--muted)]">
          <span className="font-medium text-[var(--ink)]">{copy.skills.expectedOutcome}: </span>
          {procedure.expected_outcome}
        </div>
      ) : null}
      {procedure.steps?.length ? (
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm leading-6 text-[var(--muted)]">
          {procedure.steps.slice(0, 5).map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

export function SkillGovernancePanel({
  copy,
  selectedItem,
  onSelectItem,
  onAction,
}: SkillGovernancePanelProps) {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [proceduresOpen, setProceduresOpen] = useState(true);
  const skillsQuery = useSkills();
  const reloadSkills = useReloadSkills();
  const proceduresQuery = useSkillProcedures();
  const promoteProcedure = usePromoteSkillProcedure();
  const rejectProcedure = useRejectSkillProcedure();
  const restoreProcedure = useRestoreSkillProcedure();
  const automation = useSkillCuratorAutomation();
  const runAutomation = useRunSkillCuratorAutomation();
  const maintenance = useRunSkillCuratorMaintenance();
  const skills = skillsQuery.data ?? [];
  const procedures = procedureItems(proceduresQuery.data);

  useEffect(() => {
    if (selectedItem && !skills.some((skill) => skill.skill_id === selectedItem)) {
      onSelectItem(null);
    }
  }, [onSelectItem, selectedItem, skills]);

  const skillQuery = useSkill(selectedItem);
  const detail = skillQuery.data;
  const activeSkillId = detail?.skill_id ?? null;
  const contentQuery = useSkillContent(activeSkillId);
  const filesQuery = useSkillFiles(activeSkillId);
  const fileOptions = useMemo(
    () => (filesQuery.data?.files ?? []).filter((item) => item.path !== "SKILL.md"),
    [filesQuery.data?.files],
  );
  const effectiveSelectedFile = useMemo(() => {
    if (!selectedFile) {
      return fileOptions[0]?.path ?? null;
    }
    return fileOptions.some((item) => item.path === selectedFile) ? selectedFile : fileOptions[0]?.path ?? null;
  }, [fileOptions, selectedFile]);
  const fileQuery = useSkillFile(activeSkillId, effectiveSelectedFile);

  useEffect(() => {
    if (!fileOptions.length) {
      setSelectedFile(null);
      return;
    }
    if (!selectedFile || !fileOptions.some((item) => item.path === selectedFile)) {
      setSelectedFile(fileOptions[0]?.path ?? null);
    }
  }, [fileOptions, selectedFile]);

  function promoteProcedureCandidate(procedureId: string) {
    void promoteProcedure
      .mutateAsync({ procedureId, force: false })
      .then(() => proceduresQuery.refetch());
  }

  function rejectProcedureCandidate(procedureId: string) {
    void rejectProcedure
      .mutateAsync({ procedureId, rationale: "Rejected from Ops Console." })
      .then(() => proceduresQuery.refetch());
  }

  function restoreProcedureCandidate(procedureId: string) {
    void restoreProcedure
      .mutateAsync({ procedureId, rationale: "Restored from Ops Console." })
      .then(() => proceduresQuery.refetch());
  }

  function runMaintenance(dryRun: boolean) {
    void maintenance
      .mutateAsync({ dry_run: dryRun, force: false, source: "ops" })
      .then(() => Promise.all([proceduresQuery.refetch(), automation.refetch()]));
  }

  function runAutomationDueCheck() {
    void runAutomation
      .mutateAsync({ force_run: true })
      .then(() => Promise.all([proceduresQuery.refetch(), automation.refetch()]));
  }

  return (
    <div className="grid h-full min-h-0 min-w-0 gap-4 overflow-hidden lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
      <section
        aria-label={copy.skills.title}
        className="box-border flex h-full min-h-0 min-w-0 max-w-full flex-col overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)] shadow-[var(--shadow-card)] [inline-size:100%] [max-inline-size:100%]"
      >
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--line)] px-3 py-3">
          <div className="min-w-0 truncate text-sm font-semibold text-[var(--ink)]">{copy.skills.title}</div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void reloadSkills.mutateAsync()}
            disabled={reloadSkills.isPending || skillsQuery.isFetching}
          >
            <RefreshCwIcon className="size-4" />
            {copy.skills.refresh}
          </Button>
        </div>
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden px-3 py-3 [inline-size:100%]">
          <div className="grid min-w-0 max-w-full gap-3 overflow-hidden pr-2 [inline-size:100%]">
            {skills.length === 0 ? <OpsEmptyState text={copy.skills.noResults} /> : null}
            {skills.map((skill) => (
              <OpsSelectableItem
                key={skill.skill_id}
                active={selectedItem === skill.skill_id}
                title={skill.title || skill.skill_id}
                subtitle={skill.summary}
                meta={pathSummary(skill)}
                onClick={() => onSelectItem(skill.skill_id)}
              />
            ))}
          </div>
        </div>
      </section>

      <ScrollArea className="h-full min-h-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-4 shadow-[var(--shadow-card)]" hideHorizontalScrollbar>
        <div className="min-w-0 space-y-4">
          <OpsPanelCard title={copy.skills.procedureCandidates}>
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-semibold text-[var(--ink)]">
                  <SparklesIcon className="size-4 text-[var(--primary)]" />
                  <span>{copy.skills.procedures}</span>
                  <Badge tone={procedures.length ? "accent" : "neutral"}>{procedures.length}</Badge>
                </div>
                <p className="mt-1 text-sm leading-6 text-[var(--muted)]">{copy.skills.procedureCandidatesDescription}</p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void proceduresQuery.refetch()}
                  disabled={proceduresQuery.isFetching}
                >
                  <RefreshCwIcon className={proceduresQuery.isFetching ? "size-4 animate-spin" : "size-4"} />
                  {copy.skills.refresh}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setProceduresOpen((value) => !value)}>
                  {proceduresOpen ? <ChevronUpIcon className="size-4" /> : <ChevronDownIcon className="size-4" />}
                </Button>
              </div>
            </div>
            {proceduresOpen ? (
              <div className="mt-3 space-y-3">
                {!procedures.length ? <OpsEmptyState text={copy.common.none} /> : null}
                {procedures.map((procedure) => {
                  const id = procedure.procedure_id ?? "";
                  return (
                    <ProcedureCandidateCard
                      key={id || procedure.title}
                      copy={copy}
                      procedure={procedure}
                      promotePending={promoteProcedure.isPending}
                      rejectPending={rejectProcedure.isPending}
                      restorePending={restoreProcedure.isPending}
                      onPromote={promoteProcedureCandidate}
                      onReject={rejectProcedureCandidate}
                      onRestore={restoreProcedureCandidate}
                    />
                  );
                })}
              </div>
            ) : null}
          </OpsPanelCard>

          <OpsPanelCard title={copy.skills.maintenance}>
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <p className="min-w-0 text-sm leading-6 text-[var(--muted)]">{copy.skills.maintenanceHint}</p>
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button size="sm" variant="secondary" disabled={maintenance.isPending} onClick={() => runMaintenance(true)}>
                  {copy.skills.planMaintenance}
                </Button>
                <Button size="sm" variant="primary" disabled={maintenance.isPending} onClick={() => runMaintenance(false)}>
                  {copy.skills.runMaintenance}
                </Button>
              </div>
            </div>
            <div className="mt-3">
              <SkillAutomationStatusCard
                copy={copy}
                status={automation.data}
                latestRun={runAutomation.data}
                pending={runAutomation.isPending || automation.isFetching}
                onRunDueCheck={runAutomationDueCheck}
              />
            </div>
            <div className="mt-3">
              <MaintenanceRunCard copy={copy} run={(maintenance.data as Record<string, unknown> | undefined) ?? null} />
            </div>
          </OpsPanelCard>

          {!selectedItem ? <OpsEmptyState text={copy.skills.noDetail} /> : null}
          {selectedItem && !detail && skillQuery.isFetching ? <OpsEmptyState text={copy.common.loading} /> : null}
          {selectedItem && !detail && !skillQuery.isFetching ? <OpsEmptyState text={copy.skills.noDetail} /> : null}
          {detail ? (
            <>
              <div className="min-w-0">
                <div className="truncate text-lg font-semibold text-[var(--ink)]">{detail.title}</div>
                <div className="mt-1 overflow-hidden text-sm leading-6 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:3]">
                  {detail.summary}
                </div>
              </div>

              <div className="flex min-w-0 flex-wrap gap-2">
                <Button size="sm" onClick={() => onAction(detail.enabled ? "disable" : "enable", detail.skill_id)}>
                  {detail.enabled ? copy.skills.disable : copy.skills.enable}
                </Button>
                <Button size="sm" variant="primary" onClick={() => onAction("reload", detail.skill_id)}>
                  {copy.skills.reload}
                </Button>
                {detail.can_uninstall ? (
                  <Button size="sm" variant="danger" onClick={() => onAction("uninstall", detail.skill_id)}>
                    {copy.skills.uninstall}
                  </Button>
                ) : null}
              </div>

              <OpsPanelCard title={copy.skills.version}>
                <OpsJsonBlock
                  value={{
                    version: detail.version ?? null,
                    trust: detail.trust ?? null,
                    enabled: detail.enabled,
                    valid: detail.valid,
                    source_scope: detail.source_scope ?? null,
                    read_only: detail.read_only,
                    can_uninstall: detail.can_uninstall,
                    source_root: detail.source_root ?? null,
                    path: detail.path,
                  }}
                  emptyLabel={copy.common.none}
                />
              </OpsPanelCard>

              <OpsPanelCard title={copy.skills.validation}>
                <OpsJsonBlock
                  value={{
                    valid: detail.valid,
                    issue_counts: detail.issue_counts,
                    issues: detail.issues,
                  }}
                  emptyLabel={copy.common.none}
                />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.allowedTools}>
                <OpsTagList items={detail.allowed_tools} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.tags}>
                <OpsTagList items={detail.tags} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.dependencies}>
                <OpsJsonBlock value={detail.dependencies} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.readiness}>
                <OpsJsonBlock value={detail.readiness} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.content}>
                <OpsJsonBlock
                  value={
                    contentQuery.data
                      ? {
                          body_preview: contentQuery.data.body_preview,
                          file_count: contentQuery.data.file_count,
                          body: contentQuery.data.body,
                        }
                      : null
                  }
                  emptyLabel={copy.common.none}
                />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.files}>
                {!fileOptions.length ? <OpsEmptyState text={copy.common.none} /> : null}
                {fileOptions.map((file) => (
                  <div
                    key={file.path}
                    className="flex min-w-0 items-center justify-between gap-3 overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-[var(--ink)]">{file.path}</div>
                      <div className="mt-1 text-xs text-[var(--muted)]">
                        {file.kind} · {file.size_bytes} bytes · {file.is_binary ? "binary" : "text"}
                      </div>
                    </div>
                    <Button
                      size="sm"
                      variant={effectiveSelectedFile === file.path ? "primary" : "secondary"}
                      onClick={() => setSelectedFile(file.path)}
                    >
                      {copy.common.metadata}
                    </Button>
                  </div>
                ))}
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.selectedFile}>
                <OpsJsonBlock value={fileQuery.data} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.config}>
                <OpsJsonBlock value={detail.config} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.platforms}>
                <OpsTagList items={detail.platforms} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.assets}>
                <OpsTagList items={detail.asset_paths} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.templates}>
                <OpsTagList items={detail.template_paths} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.scripts}>
                <OpsTagList items={detail.script_paths} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.references}>
                <OpsTagList items={detail.reference_paths} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.skills.package}>
                <OpsJsonBlock value={detail.package} emptyLabel={copy.common.none} />
              </OpsPanelCard>
            </>
          ) : null}
        </div>
      </ScrollArea>
    </div>
  );
}
