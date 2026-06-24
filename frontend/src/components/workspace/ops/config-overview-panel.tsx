"use client";

import React from "react";
import {
  BotIcon,
  CalendarClockIcon,
  DatabaseZapIcon,
  KeyRoundIcon,
  Layers3Icon,
  PlugIcon,
  PuzzleIcon,
  ServerCogIcon,
  SparklesIcon,
  WrenchIcon,
} from "lucide-react";

import type { ConfigOverviewMetricView, ThreadStateView } from "@/src/core/contracts";
import { useConfigOverview } from "@/src/core/config/hooks";
import { useGatewayHealth } from "@/src/core/system/hooks";
import { Badge, Button, ScrollArea } from "@/src/components/ui";
import { cn } from "@/src/lib/utils";

import type { OpsCopy, OpsSurface } from "./types";

const EMPTY_METRIC: ConfigOverviewMetricView = {
  total: 0,
  source_counts: {},
  enabled_source_counts: {},
};

type ConfigOverviewPanelProps = {
  copy: OpsCopy;
  activeThreadId: string | null;
  threadState: ThreadStateView | null;
  onSelectSurface(surface: OpsSurface): void;
};

type ConfigCardProps = {
  title: string;
  description: string;
  metric: string;
  meta: string;
  openLabel: string;
  icon: React.ReactNode;
  onOpen(): void;
};

function ConfigCard({ title, description, metric, meta, openLabel, icon, onOpen }: ConfigCardProps) {
  return (
    <article className="flex min-h-[190px] flex-col justify-between rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4 shadow-[var(--shadow-card)]">
      <div className="min-w-0 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-2 text-[var(--primary)]">
            {icon}
          </div>
          <div className="text-right">
            <div className="text-2xl font-semibold leading-none text-[var(--ink)]">{metric}</div>
            <div className="mt-1 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{meta}</div>
          </div>
        </div>
        <div>
          <h3 className="text-sm font-semibold text-[var(--ink)]">{title}</h3>
          <p className="mt-1 text-sm leading-6 text-[var(--muted)]">{description}</p>
        </div>
      </div>
      <Button
        aria-label={`${openLabel} ${title}`}
        type="button"
        variant="secondary"
        size="sm"
        className="mt-4 justify-center"
        onClick={onOpen}
      >
        {openLabel}
      </Button>
    </article>
  );
}

export function ConfigOverviewPanel({
  copy,
  activeThreadId,
  threadState,
  onSelectSurface,
}: ConfigOverviewPanelProps) {
  const health = useGatewayHealth();
  const overview = useConfigOverview();
  const metrics = overview.data;
  const typedMetrics = metrics as (typeof metrics & { basics?: ConfigOverviewMetricView }) | undefined;
  const basicMetric = typedMetrics?.basics ?? EMPTY_METRIC;
  const modelMetric = metrics?.models ?? EMPTY_METRIC;
  const toolMetric = metrics?.tools ?? EMPTY_METRIC;
  const skillMetric = metrics?.skills ?? EMPTY_METRIC;
  const memoryMetric = metrics?.memory ?? EMPTY_METRIC;
  const mcpMetric = metrics?.mcp ?? EMPTY_METRIC;
  const pluginMetric = metrics?.plugins ?? EMPTY_METRIC;
  const scheduledMetric = metrics?.scheduled ?? EMPTY_METRIC;
  const repoSkillTotal =
    skillMetric.source_counts.repo_builtin ??
    skillMetric.source_counts.repo ??
    skillMetric.total ??
    0;
  const repoSkillEnabled =
    skillMetric.enabled_source_counts.repo_builtin ??
    skillMetric.enabled_source_counts.repo ??
    skillMetric.enabled ??
    0;
  const memoryQualityScore = memoryMetric.quality_score == null ? null : Math.round(memoryMetric.quality_score * 100);
  const loadingMetric = overview.isLoading ? "..." : "0";

  const cards: ConfigCardProps[] = [
    {
      title: copy.overview.basics,
      description: copy.overview.basicsDescription,
      metric: metrics ? String(basicMetric.issue_count ?? 0) : loadingMetric,
      meta: copy.basic.missingRequired,
      openLabel: copy.overview.openSurface,
      icon: <KeyRoundIcon className="size-4" />,
      onOpen: () => onSelectSurface("basics"),
    },
    {
      title: copy.overview.models,
      description: copy.overview.modelsDescription,
      metric: metrics ? String(modelMetric.total ?? 0) : loadingMetric,
      meta: copy.overview.total,
      openLabel: copy.overview.openSurface,
      icon: <BotIcon className="size-4" />,
      onOpen: () => onSelectSurface("models"),
    },
    {
      title: copy.overview.tools,
      description: copy.overview.toolsDescription,
      metric: metrics ? String(toolMetric.total ?? 0) : loadingMetric,
      meta: copy.overview.total,
      openLabel: copy.overview.openSurface,
      icon: <WrenchIcon className="size-4" />,
      onOpen: () => onSelectSurface("tools"),
    },
    {
      title: copy.overview.skills,
      description: copy.overview.skillsDescription,
      metric: metrics ? `${repoSkillEnabled}/${repoSkillTotal}` : loadingMetric,
      meta: copy.overview.enabled,
      openLabel: copy.overview.openSurface,
      icon: <Layers3Icon className="size-4" />,
      onOpen: () => onSelectSurface("skills"),
    },
    {
      title: copy.overview.memory,
      description: copy.overview.memoryDescription,
      metric: metrics ? (memoryQualityScore == null ? String(memoryMetric.total ?? 0) : `${memoryQualityScore}%`) : loadingMetric,
      meta: memoryQualityScore == null ? copy.memory.stores : copy.memory.qualityScore,
      openLabel: copy.overview.openSurface,
      icon: <DatabaseZapIcon className="size-4" />,
      onOpen: () => onSelectSurface("memory"),
    },
    {
      title: copy.overview.selfUpgrade,
      description: copy.overview.selfUpgradeDescription,
      metric: metrics ? "2" : loadingMetric,
      meta: copy.overview.total,
      openLabel: copy.overview.openSurface,
      icon: <SparklesIcon className="size-4" />,
      onOpen: () => onSelectSurface("selfUpgrade"),
    },
    {
      title: copy.overview.mcp,
      description: copy.overview.mcpDescription,
      metric: metrics ? `${mcpMetric.ready ?? 0}/${mcpMetric.total ?? 0}` : loadingMetric,
      meta: copy.overview.ready,
      openLabel: copy.overview.openSurface,
      icon: <ServerCogIcon className="size-4" />,
      onOpen: () => onSelectSurface("mcp"),
    },
    {
      title: copy.overview.plugins,
      description: copy.overview.pluginsDescription,
      metric: metrics ? `${pluginMetric.enabled ?? 0}/${pluginMetric.total ?? 0}` : loadingMetric,
      meta: copy.overview.enabled,
      openLabel: copy.overview.openSurface,
      icon: <PuzzleIcon className="size-4" />,
      onOpen: () => onSelectSurface("plugins"),
    },
    {
      title: copy.overview.scheduled,
      description: copy.overview.scheduledDescription,
      metric: metrics ? `${scheduledMetric.enabled ?? 0}/${scheduledMetric.total ?? 0}` : loadingMetric,
      meta: copy.overview.enabled,
      openLabel: copy.overview.openSurface,
      icon: <CalendarClockIcon className="size-4" />,
      onOpen: () => onSelectSurface("scheduled"),
    },
  ];

  return (
    <ScrollArea className="h-full min-h-0">
      <div className="space-y-4 pr-2">
        <section className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5 shadow-[var(--shadow-card)]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <PlugIcon className="size-4 text-[var(--primary)]" />
                <h2 className="text-base font-semibold text-[var(--ink)]">{copy.overview.title}</h2>
              </div>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">{copy.overview.description}</p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <Badge tone={health.data?.status === "ok" ? "success" : "neutral"}>
                {copy.overview.globalStatus}: {health.data?.status ?? copy.common.loading}
              </Badge>
              <Badge tone={overview.isError ? "danger" : "neutral"}>
                {copy.overview.configSnapshot}: {overview.isFetching ? copy.common.loading : metrics?.config_fingerprint?.slice(0, 8) ?? copy.common.none}
              </Badge>
              <Badge tone={activeThreadId ? "neutral" : "warning"}>
                {copy.overview.currentThread}: {activeThreadId ?? copy.overview.noThread}
              </Badge>
            </div>
          </div>
          <div className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-4 py-3 text-sm leading-6 text-[var(--muted)]">
            {copy.overview.runtimeDrawerNote}
            {threadState?.execution_mode ? (
              <span className="ml-2 text-[var(--ink)]">
                {threadState.execution_mode}
              </span>
            ) : null}
          </div>
        </section>

        <div className={cn("grid gap-4 md:grid-cols-2 xl:grid-cols-3")}>
          {cards.map((card) => (
            <ConfigCard
              key={card.title}
              title={card.title}
              description={card.description}
              metric={card.metric}
              meta={card.meta}
              openLabel={card.openLabel}
              icon={card.icon}
              onOpen={card.onOpen}
            />
          ))}
        </div>
      </div>
    </ScrollArea>
  );
}
