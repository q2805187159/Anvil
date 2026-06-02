"use client";

import React, { useId } from "react";
import { cn } from "@/src/lib/utils";

/**
 * Section Card Component
 *
 * A consistent card container for grouping related content in drawers and panels.
 * Provides a clean, minimal design with optional title and consistent spacing.
 */
export interface SectionCardProps {
  title?: string;
  children: React.ReactNode;
  className?: string;
  contentClassName?: string;
}

export function SectionCard({ title, children, className, contentClassName }: SectionCardProps) {
  return (
    <div className={cn("space-y-2", className)}>
      {title ? (
        <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--muted)]">
          {title}
        </h3>
      ) : null}
      <div className={cn("space-y-2", contentClassName)}>{children}</div>
    </div>
  );
}

/**
 * Info Row Component
 *
 * A consistent key-value pair display for metadata and settings.
 * Supports monospace formatting for technical values.
 */
export interface InfoRowProps {
  label: string;
  value: string | React.ReactNode;
  mono?: boolean;
  className?: string;
}

function HoverRevealText({ value, className }: { value: string; className?: string }) {
  const tooltipId = useId();
  return (
    <span className="group/hover-reveal relative block min-w-0 max-w-full">
      <span
        className={cn("block min-w-0 max-w-full truncate", className)}
        title={value}
        aria-describedby={tooltipId}
      >
        {value}
      </span>
      <span
        id={tooltipId}
        role="tooltip"
        className="pointer-events-none invisible absolute bottom-[calc(100%+0.35rem)] left-0 z-50 block max-w-[min(30rem,86vw)] whitespace-normal break-words rounded-lg border border-[var(--line)] bg-[var(--panel-strong)] px-3 py-2 text-xs leading-5 text-[var(--ink)] opacity-0 shadow-[var(--panel-shadow)] transition-opacity group-hover/hover-reveal:visible group-hover/hover-reveal:opacity-100"
      >
        {value}
      </span>
    </span>
  );
}

export function InfoRow({ label, value, mono = false, className }: InfoRowProps) {
  const stringValue = typeof value === "string" ? value : null;
  return (
    <div className={cn("grid min-w-0 grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] items-start gap-3 py-1.5", className)}>
      <HoverRevealText value={label} className="text-[13px] text-[var(--muted)]" />
      <div className={cn("min-w-0 text-right text-[13px] text-[var(--ink)]", mono && "font-mono")}>
        {stringValue ? <HoverRevealText value={stringValue} className={cn("text-right", mono && "font-mono")} /> : value}
      </div>
    </div>
  );
}

/**
 * Empty Panel Text Component
 *
 * A consistent empty state message for panels and sections.
 */
export interface EmptyPanelTextProps {
  text: string;
  className?: string;
}

export function EmptyPanelText({ text, className }: EmptyPanelTextProps) {
  return (
    <div
      className={cn(
        "rounded-[0.8rem] border border-dashed border-[var(--line)] bg-[var(--panel-muted)] px-3 py-5 text-center text-[13px] text-[var(--muted)]",
        className
      )}
    >
      {text}
    </div>
  );
}

/**
 * Panel Container Component
 *
 * A consistent container for panel content with proper overflow handling.
 */
export interface PanelContainerProps {
  children: React.ReactNode;
  className?: string;
}

export function PanelContainer({ children, className }: PanelContainerProps) {
  return (
    <div
      className={cn(
        "min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-2.5 py-2 pr-2",
        className
      )}
    >
      {children}
    </div>
  );
}

/**
 * Panel Header Component
 *
 * A consistent header for panels and drawers with title and actions.
 */
export interface PanelHeaderProps {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  className?: string;
}

export function PanelHeader({ title, subtitle, actions, className }: PanelHeaderProps) {
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-between border-b border-[var(--line)] px-3 py-3",
        className
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-[var(--ink)]">{title}</div>
        {subtitle ? (
          <HoverRevealText value={subtitle} className="mt-0.5 text-xs uppercase tracking-[0.08em] text-[var(--muted)]" />
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/**
 * Data Card Component
 *
 * A consistent card for displaying data items with optional badges and actions.
 */
export interface DataCardProps {
  children: React.ReactNode;
  className?: string;
  onClick?: () => void;
  interactive?: boolean;
}

export function DataCard({ children, className, onClick, interactive = false }: DataCardProps) {
  const Component = onClick ? "button" : "div";

  return (
    <Component
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={cn(
        "rounded-[0.8rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2.5 text-left shadow-[var(--shadow-card)]",
        interactive && "transition-[background,border-color,transform] duration-200 ease-[var(--ease-smooth)] hover:border-[color-mix(in_srgb,var(--primary)_24%,var(--line))] hover:bg-[var(--panel-strong)] active:translate-y-px",
        className
      )}
    >
      {children}
    </Component>
  );
}

/**
 * Metric Display Component
 *
 * A consistent display for metrics and statistics.
 */
export interface MetricProps {
  label: string;
  value: string | number;
  unit?: string;
  trend?: "up" | "down" | "neutral";
  className?: string;
}

export function Metric({ label, value, unit, trend, className }: MetricProps) {
  return (
    <div className={cn("space-y-1", className)}>
      <div className="text-xs uppercase tracking-[0.08em] text-[var(--muted)]">{label}</div>
      <div className="flex items-baseline gap-1">
        <span className="text-xl font-semibold tracking-tight text-[var(--ink)]">{value}</span>
        {unit ? <span className="text-xs text-[var(--muted)]">{unit}</span> : null}
      </div>
      {trend ? (
        <div
          className={cn(
            "text-xs font-medium",
            trend === "up" && "text-green-600",
            trend === "down" && "text-red-600",
            trend === "neutral" && "text-[var(--muted)]"
          )}
        >
          {trend === "up" ? "↑" : trend === "down" ? "↓" : "→"}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Divider Component
 *
 * A consistent visual separator for sections.
 */
export interface DividerProps {
  label?: string;
  className?: string;
}

export function Divider({ label, className }: DividerProps) {
  if (label) {
    return (
      <div className={cn("flex items-center gap-3", className)}>
        <div className="h-px flex-1 bg-[var(--line)]" />
        <span className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{label}</span>
        <div className="h-px flex-1 bg-[var(--line)]" />
      </div>
    );
  }

  return <div className={cn("h-px bg-[var(--line)]", className)} />;
}

/**
 * Status Indicator Component
 *
 * A consistent status indicator with color coding.
 */
export interface StatusIndicatorProps {
  status: "success" | "warning" | "error" | "info" | "neutral";
  label?: string;
  pulse?: boolean;
  className?: string;
}

export function StatusIndicator({ status, label, pulse = false, className }: StatusIndicatorProps) {
  const colorMap = {
    success: "bg-green-500",
    warning: "bg-yellow-500",
    error: "bg-red-500",
    info: "bg-blue-500",
    neutral: "bg-gray-400",
  };

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="relative">
        <div className={cn("size-2 rounded-full", colorMap[status])} />
        {pulse ? (
          <div
            className={cn(
              "absolute inset-0 size-2 animate-ping rounded-full opacity-75",
              colorMap[status]
            )}
          />
        ) : null}
      </div>
      {label ? <span className="text-[13px] text-[var(--ink)]">{label}</span> : null}
    </div>
  );
}
