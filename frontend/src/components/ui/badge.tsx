"use client";

import * as React from "react";

import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/src/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em]",
  {
    variants: {
      tone: {
        neutral: "border-[var(--line)] bg-[var(--panel)] text-[var(--muted)]",
        accent: "border-transparent bg-[var(--primary-soft)] text-[var(--primary)]",
        success: "border-transparent bg-[color-mix(in_srgb,var(--success)_16%,transparent)] text-[var(--success)]",
        warning: "border-transparent bg-[var(--warning-soft)] text-[var(--warning)]",
        danger: "border-transparent bg-[color-mix(in_srgb,var(--danger)_14%,transparent)] text-[var(--danger)]",
      },
    },
    defaultVariants: {
      tone: "neutral",
    },
  },
);

export function Badge({
  className,
  tone,
  children,
}: React.PropsWithChildren<{ className?: string } & VariantProps<typeof badgeVariants>>) {
  return <span className={cn(badgeVariants({ tone }), className)}>{children}</span>;
}
