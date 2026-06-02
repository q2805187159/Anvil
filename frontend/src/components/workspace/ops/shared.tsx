"use client";

import React from "react";

import { cn } from "@/src/lib/utils";

export function OpsEmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-[var(--line)] bg-[var(--panel-muted)] px-4 py-6 text-sm text-[var(--muted)]">
      {text}
    </div>
  );
}

export function OpsPanelCard({
  title,
  children,
  className,
}: React.PropsWithChildren<{
  title: string;
  className?: string;
}>) {
  return (
    <section className={cn("min-w-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] shadow-[var(--shadow-card)]", className)}>
      <div className="truncate border-b border-[var(--line)] px-4 py-3 text-sm font-semibold text-[var(--ink)]">{title}</div>
      <div className="min-w-0 max-w-full space-y-3 overflow-hidden px-4 py-4">{children}</div>
    </section>
  );
}

export function OpsJsonBlock({
  value,
  emptyLabel,
  className,
}: {
  value: unknown;
  emptyLabel: string;
  className?: string;
}) {
  const serialized =
    value === null || value === undefined || value === ""
      ? ""
      : typeof value === "string"
        ? value
        : JSON.stringify(value, null, 2);

  if (!serialized) {
    return <OpsEmptyState text={emptyLabel} />;
  }

  return (
    <pre
      className={cn(
        "max-h-[260px] overflow-auto whitespace-pre-wrap break-all rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]",
        className,
      )}
    >
      {serialized}
    </pre>
  );
}

export function OpsSelectableItem({
  active,
  title,
  subtitle,
  meta,
  onClick,
}: {
  active: boolean;
  title: string;
  subtitle?: string | null;
  meta?: string | null;
  onClick(): void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "box-border block w-full min-w-0 max-w-full overflow-hidden rounded-xl border px-3 py-3 text-left transition-[background,border-color,box-shadow,transform] duration-200 ease-[var(--ease-smooth)] active:translate-y-px [contain:inline-size] [inline-size:100%] [max-inline-size:100%] [min-inline-size:0]",
        active
          ? "border-[color-mix(in_srgb,var(--secondary)_58%,var(--line))] bg-[var(--accent-soft)] shadow-[var(--shadow-card)]"
          : "border-[var(--line)] bg-[var(--panel)] hover:border-[color-mix(in_srgb,var(--primary)_26%,var(--line))] hover:bg-[var(--panel-muted)]",
      )}
    >
      <div className="block w-full min-w-0 max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-sm font-semibold text-[var(--ink)] [overflow-wrap:anywhere]">
        {title}
      </div>
      {subtitle ? (
        <div className="mt-1 w-full min-w-0 max-w-full overflow-hidden text-xs leading-5 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] [overflow-wrap:anywhere]">
          {subtitle}
        </div>
      ) : null}
      {meta ? (
        <div className="mt-2 block w-full min-w-0 max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-[11px] uppercase tracking-[0.06em] text-[var(--muted)] [overflow-wrap:anywhere]">
          {meta}
        </div>
      ) : null}
    </button>
  );
}

export function OpsTagList({
  items,
  emptyLabel,
}: {
  items: string[] | undefined | null;
  emptyLabel: string;
}) {
  if (!items?.length) {
    return <span className="text-sm text-[var(--muted)]">{emptyLabel}</span>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <span
          key={item}
          className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] px-2.5 py-1 text-xs text-[var(--ink)]"
        >
          {item}
        </span>
      ))}
    </div>
  );
}
