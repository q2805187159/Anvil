"use client";

import React from "react";
import { RefreshCwIcon } from "lucide-react";

import { Badge, Button, ScrollArea } from "@/src/components/ui";
import type { SelfUpgradeBacklogItemView, SelfUpgradeDomainHealthView } from "@/src/core/contracts";
import { useSelfUpgradeHealth } from "@/src/core/self-upgrade/hooks";

import { OpsEmptyState, OpsJsonBlock, OpsPanelCard, OpsTagList } from "./shared";
import type { OpsCopy } from "./types";

type BadgeTone = "neutral" | "accent" | "success" | "warning" | "danger";

function toneForStatus(status: string | undefined): BadgeTone {
  if (status === "healthy") {
    return "success";
  }
  if (status === "watch") {
    return "warning";
  }
  if (status === "needs_attention" || status === "unavailable") {
    return "danger";
  }
  return "neutral";
}

function formatScore(value: number | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "--";
  }
  return `${Math.round(value * 100)}%`;
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

function DomainCard({ copy, domain }: { copy: OpsCopy; domain: SelfUpgradeDomainHealthView }) {
  const metricEntries = Object.entries(domain.metrics ?? {}).slice(0, 10);
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{domain.label}</div>
          <div className="mt-1 font-mono text-xs text-[var(--muted)]">{domain.domain_id}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge tone={toneForStatus(domain.status)}>{domain.status}</Badge>
          <Badge tone={domain.enabled ? "success" : "neutral"}>{domain.enabled ? copy.common.enabled : copy.common.disabled}</Badge>
          <Badge tone="accent">{formatScore(domain.score)}</Badge>
        </div>
      </div>
      {metricEntries.length ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {metricEntries.map(([key, value]) => (
            <div key={key} className="min-w-0 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
              <div className="truncate text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{key}</div>
              <div className="mt-1 truncate font-mono text-sm font-semibold text-[var(--ink)]">{String(value)}</div>
            </div>
          ))}
        </div>
      ) : null}
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div>
          <div className="mb-2 text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.selfUpgrade.issues}</div>
          <OpsTagList items={[...(domain.issues ?? [])]} emptyLabel={copy.selfUpgrade.noIssues} />
        </div>
        <div>
          <div className="mb-2 text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{copy.selfUpgrade.recommendations}</div>
          <OpsTagList items={[...(domain.recommendations ?? [])]} emptyLabel={copy.selfUpgrade.noRecommendations} />
        </div>
      </div>
    </div>
  );
}

function BacklogItem({ copy, item }: { copy: OpsCopy; item: SelfUpgradeBacklogItemView }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{item.title}</div>
          <div className="mt-1 flex flex-wrap gap-2">
            <Badge tone={item.severity === "critical" ? "danger" : item.severity === "warning" ? "warning" : "neutral"}>{item.severity}</Badge>
            <Badge tone="neutral">{item.domain}</Badge>
            {item.metric ? <Badge tone="accent">{item.metric}: {item.count}</Badge> : null}
          </div>
        </div>
        <div className="max-w-[16rem] truncate font-mono text-xs text-[var(--muted)]">{item.item_id}</div>
      </div>
      {item.summary ? <p className="mt-3 text-sm leading-6 text-[var(--muted)]">{item.summary}</p> : null}
      {item.recommendation ? <p className="mt-2 text-sm leading-6 text-[var(--ink)]">{item.recommendation}</p> : null}
      {Object.keys(item.metadata ?? {}).length ? (
        <div className="mt-3">
          <OpsJsonBlock value={item.metadata} emptyLabel={copy.common.none} className="max-h-[140px]" />
        </div>
      ) : null}
    </div>
  );
}

export function SelfUpgradePanel({ copy }: { copy: OpsCopy }) {
  const health = useSelfUpgradeHealth();
  const report = health.data;
  const domains = report?.domains ?? [];
  const backlog = report?.backlog ?? [];

  return (
    <ScrollArea className="h-full min-h-0">
      <div className="space-y-4 pr-2">
        <OpsPanelCard title={copy.selfUpgrade.title}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={toneForStatus(report?.status)}>{report?.status ?? copy.common.loading}</Badge>
              <Badge tone="accent">{copy.selfUpgrade.score}: {formatScore(report?.score)}</Badge>
              <Badge tone="neutral">{copy.selfUpgrade.generatedAt}: {formatDate(report?.generated_at, copy.common.none)}</Badge>
            </div>
            <Button type="button" size="sm" variant="secondary" disabled={health.isFetching} onClick={() => void health.refetch()}>
              <RefreshCwIcon className={health.isFetching ? "size-4 animate-spin" : "size-4"} />
              {copy.selfUpgrade.refresh}
            </Button>
          </div>
          <div className="text-xs text-[var(--muted)]">{report?.fingerprint ?? copy.common.none}</div>
          {report?.recommendations?.length ? (
            <OpsTagList items={[...report.recommendations]} emptyLabel={copy.selfUpgrade.noRecommendations} />
          ) : (
            <OpsEmptyState text={health.isLoading ? copy.common.loading : copy.selfUpgrade.noRecommendations} />
          )}
        </OpsPanelCard>

        <OpsPanelCard title={copy.selfUpgrade.domains}>
          {domains.length ? (
            <div className="space-y-3">
              {domains.map((domain) => (
                <DomainCard key={domain.domain_id} copy={copy} domain={domain} />
              ))}
            </div>
          ) : (
            <OpsEmptyState text={health.isLoading ? copy.common.loading : copy.common.none} />
          )}
        </OpsPanelCard>

        <OpsPanelCard title={copy.selfUpgrade.backlog}>
          {backlog.length ? (
            <div className="space-y-3">
              {backlog.map((item) => (
                <BacklogItem key={item.item_id} copy={copy} item={item} />
              ))}
            </div>
          ) : (
            <OpsEmptyState text={health.isLoading ? copy.common.loading : copy.selfUpgrade.noBacklog} />
          )}
        </OpsPanelCard>
      </div>
    </ScrollArea>
  );
}
