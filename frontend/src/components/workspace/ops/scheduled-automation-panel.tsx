"use client";

import React from "react";
import { PlayIcon, RefreshCwIcon, PauseIcon, RotateCcwIcon } from "lucide-react";

import type { ScheduledTaskAutomationStatusResponse, ScheduledTaskView } from "@/src/core/contracts";
import { Badge, Button, EmptyPanelText, ScrollArea } from "@/src/components/ui";
import { cn } from "@/src/lib/utils";

import type { OpsCopy } from "./types";

type ScheduledAutomationPanelProps = {
  copy: OpsCopy;
  tasks: ScheduledTaskView[];
  automation: ScheduledTaskAutomationStatusResponse | undefined;
  loading: boolean;
  pending: boolean;
  onRefresh(): void;
  onRunDue(): void;
  onRun(taskId: string): void;
  onPause(taskId: string): void;
  onResume(taskId: string): void;
};

function formatDate(value: string | null | undefined) {
  if (!value) {
    return "none";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function statusTone(status: string) {
  if (status === "running") {
    return "border-blue-300 bg-blue-50 text-blue-700";
  }
  if (status === "failed") {
    return "border-red-300 bg-red-50 text-red-700";
  }
  if (status === "paused") {
    return "border-amber-300 bg-amber-50 text-amber-700";
  }
  return "border-emerald-300 bg-emerald-50 text-emerald-700";
}

export function ScheduledAutomationPanel({
  copy,
  tasks,
  automation,
  loading,
  pending,
  onRefresh,
  onRunDue,
  onRun,
  onPause,
  onResume,
}: ScheduledAutomationPanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[var(--ink)]">{copy.scheduled.title}</h2>
          <p className="text-xs text-[var(--muted)]">{tasks.length} tasks</p>
        </div>
        <Button type="button" variant="secondary" size="sm" onClick={onRefresh} disabled={loading || pending}>
          <RefreshCwIcon className={cn("mr-2 size-3.5", loading ? "animate-spin" : "")} />
          {copy.scheduled.refresh}
        </Button>
      </div>
      <section className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3 shadow-sm">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
              {copy.scheduled.automation}
            </h3>
            <p className="mt-1 text-xs text-[var(--muted)]">
              {automation?.enabled ? automation.reason : automation?.reason ?? "disabled"}
            </p>
          </div>
          <Button type="button" variant="secondary" size="sm" onClick={onRunDue} disabled={pending || loading}>
            <PlayIcon className="mr-2 size-3.5" />
            {copy.scheduled.forceDue}
          </Button>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-[var(--muted)] md:grid-cols-4">
          <div>
            <dt className="font-medium text-[var(--ink)]">{copy.scheduled.enabled}</dt>
            <dd>{automation ? `${automation.enabled_task_count}/${automation.task_count}` : "n/a"}</dd>
          </div>
          <div>
            <dt className="font-medium text-[var(--ink)]">{copy.scheduled.due}</dt>
            <dd>{automation?.due_count ?? "n/a"}</dd>
          </div>
          <div>
            <dt className="font-medium text-[var(--ink)]">{copy.scheduled.running}</dt>
            <dd>{automation?.running_count ?? "n/a"}</dd>
          </div>
          <div>
            <dt className="font-medium text-[var(--ink)]">{copy.scheduled.failed}</dt>
            <dd>{automation?.failed_count ?? "n/a"}</dd>
          </div>
          <div>
            <dt className="font-medium text-[var(--ink)]">{copy.scheduled.nextRun}</dt>
            <dd>{formatDate(automation?.next_run_at)}</dd>
          </div>
          <div>
            <dt className="font-medium text-[var(--ink)]">{copy.scheduled.lastStatus}</dt>
            <dd>{automation?.last_status ?? copy.common.none}</dd>
          </div>
        </dl>
      </section>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 pr-2">
          {tasks.length ? (
            tasks.map((task) => (
              <article
                key={task.task_id}
                className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3 shadow-sm"
              >
                <div className="flex min-w-0 items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <h3 className="truncate text-sm font-semibold text-[var(--ink)]">{task.name}</h3>
                      <Badge className={cn("border text-[11px]", statusTone(task.status))}>{task.status}</Badge>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-[var(--muted)]">{task.prompt}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={copy.scheduled.run}
                      title={copy.scheduled.run}
                      disabled={pending}
                      onClick={() => onRun(task.task_id)}
                    >
                      <PlayIcon className="size-4" />
                    </Button>
                    {task.enabled ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={copy.scheduled.pause}
                        title={copy.scheduled.pause}
                        disabled={pending}
                        onClick={() => onPause(task.task_id)}
                      >
                        <PauseIcon className="size-4" />
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={copy.scheduled.resume}
                        title={copy.scheduled.resume}
                        disabled={pending}
                        onClick={() => onResume(task.task_id)}
                      >
                        <RotateCcwIcon className="size-4" />
                      </Button>
                    )}
                  </div>
                </div>
                <dl className="mt-3 grid gap-2 text-xs text-[var(--muted)] sm:grid-cols-2">
                  <div>
                    <dt className="font-medium text-[var(--ink)]">{copy.scheduled.schedule}</dt>
                    <dd>{task.schedule.display}</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-[var(--ink)]">{copy.scheduled.thread}</dt>
                    <dd>{task.thread_id ?? `scheduled-${task.task_id}`}</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-[var(--ink)]">{copy.scheduled.nextRun}</dt>
                    <dd>{formatDate(task.next_run_at)}</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-[var(--ink)]">{copy.scheduled.lastStatus}</dt>
                    <dd>{task.last_status ?? copy.common.none}</dd>
                  </div>
                </dl>
              </article>
            ))
          ) : (
            <EmptyPanelText text={loading ? copy.common.loading : copy.scheduled.noResults} />
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
