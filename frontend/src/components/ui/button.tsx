"use client";

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/src/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-[0.7rem] border text-[13px] font-semibold transition-[background,border-color,color,box-shadow,transform] duration-200 ease-[var(--ease-smooth)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:pointer-events-none disabled:opacity-50 active:translate-y-px",
  {
    variants: {
      variant: {
        primary:
          "border-transparent bg-[var(--primary)] text-white shadow-[var(--shadow-card)] hover:bg-[var(--primary-strong)]",
        secondary:
          "border-[var(--line)] bg-[var(--panel)] text-[var(--ink)] shadow-[var(--shadow-card)] hover:border-[color-mix(in_srgb,var(--primary)_26%,var(--line))] hover:bg-[var(--panel-strong)]",
        ghost:
          "border-transparent bg-transparent text-[var(--muted)] hover:bg-[var(--primary-soft)] hover:text-[var(--ink)]",
        danger:
          "border-transparent bg-[var(--danger)] text-white shadow-[var(--shadow-card)] hover:brightness-95",
      },
      size: {
        sm: "h-8 px-2.5",
        md: "h-9 px-3",
        lg: "h-10 px-4",
        icon: "size-8",
      },
    },
    defaultVariants: {
      variant: "secondary",
      size: "md",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button
      className={cn(buttonVariants({ variant, size }), className)}
      ref={ref}
      {...props}
    />
  ),
);

Button.displayName = "Button";
