"use client";

import * as React from "react";
import { ChevronsUpDownIcon } from "lucide-react";

import { cn } from "@/src/lib/utils";

export function NativeSelect({
  className,
  compact = false,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & {
  compact?: boolean;
}) {
  return (
    <div className="relative min-w-0">
      <select
        className={cn(
          "w-full min-w-0 appearance-none rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel)] pl-2.5 pr-9 text-[13px] text-[var(--ink)] shadow-[var(--shadow-card)] outline-none transition-[background,border-color,box-shadow] duration-200 ease-[var(--ease-smooth)] hover:border-[color-mix(in_srgb,var(--primary)_26%,var(--line))] hover:bg-[var(--panel-strong)] focus-visible:ring-2 focus-visible:ring-[var(--ring)]",
          compact ? "h-8" : "h-9",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronsUpDownIcon className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted)]" />
    </div>
  );
}
