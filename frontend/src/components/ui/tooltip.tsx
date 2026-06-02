"use client";

import React, { useId } from "react";

import { cn } from "@/src/lib/utils";

export function TooltipProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  return <>{children}</>;
}

export function Tooltip({
  children,
  content,
  className,
}: Readonly<{ children: React.ReactNode; content: React.ReactNode; className?: string }>) {
  const tooltipId = useId();
  const trigger = React.isValidElement<{ "aria-describedby"?: string }>(children)
    ? React.cloneElement(children, { "aria-describedby": tooltipId })
    : children;

  return (
    <span className="group/tooltip relative inline-flex min-w-0 align-middle">
      {trigger}
      <span
        id={tooltipId}
        role="tooltip"
        className={cn(
          "pointer-events-none invisible absolute bottom-full left-1/2 z-[140] mb-2 w-max min-w-[12rem] max-w-[min(24rem,calc(100vw-2rem))] -translate-x-1/2 whitespace-normal rounded-lg border border-[var(--line)] bg-[var(--panel-strong)] px-3 py-2 text-left text-xs leading-relaxed text-[var(--ink)] opacity-0 shadow-[var(--panel-shadow)] transition [overflow-wrap:break-word] [word-break:normal] group-hover/tooltip:visible group-hover/tooltip:opacity-100 group-focus-within/tooltip:visible group-focus-within/tooltip:opacity-100",
          className,
        )}
      >
        {content}
      </span>
    </span>
  );
}
