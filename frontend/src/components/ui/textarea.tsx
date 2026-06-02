"use client";

import * as React from "react";

import { cn } from "@/src/lib/utils";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "min-h-20 w-full rounded-[0.8rem] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-[13px] leading-5 text-[var(--ink)] outline-none transition-[border-color,box-shadow,background] duration-200 ease-[var(--ease-smooth)] placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:ring-2 focus:ring-[var(--ring)]",
      className,
    )}
    {...props}
  />
));

Textarea.displayName = "Textarea";
