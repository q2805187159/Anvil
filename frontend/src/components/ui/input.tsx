"use client";

import * as React from "react";

import { cn } from "@/src/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel)] px-2.5 text-[13px] text-[var(--ink)] shadow-[inset_0_1px_0_rgba(255,255,255,0.16)] outline-none transition-[border-color,box-shadow,background] duration-200 ease-[var(--ease-smooth)] placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--ring)]",
        className,
      )}
      {...props}
    />
  ),
);

Input.displayName = "Input";
