"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { CheckIcon, ChevronsUpDownIcon } from "lucide-react";
import { cn } from "@/src/lib/utils";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps {
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  compact?: boolean;
  "aria-label"?: string;
}

export function Select({
  options,
  value,
  onChange,
  placeholder = "Select an option",
  disabled = false,
  className,
  compact = false,
  "aria-label": ariaLabel,
}: SelectProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    function handlePointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open]);

  const currentOption = useMemo(
    () => options.find((option) => option.value === value),
    [options, value]
  );

  const currentLabel = currentOption?.label ?? placeholder;

  return (
    <div
      ref={containerRef}
      className={cn("relative min-w-0", compact ? "w-[10.5rem]" : "w-full", className)}
    >
      <button
        type="button"
        disabled={disabled}
        aria-label={ariaLabel ?? placeholder}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => !disabled && setOpen((current) => !current)}
        className={cn(
          "flex w-full items-center justify-between gap-2 rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel)] px-2.5 text-left text-[13px] text-[var(--ink)] shadow-[var(--shadow-card)] transition-[background,border-color,box-shadow,transform] duration-200 ease-[var(--ease-smooth)]",
          compact ? "h-8" : "h-9",
          disabled
            ? "cursor-not-allowed opacity-50"
            : "hover:border-[color-mix(in_srgb,var(--primary)_26%,var(--line))] hover:bg-[var(--panel-strong)] active:translate-y-px"
        )}
      >
        <span className="min-w-0 whitespace-normal break-words leading-4">{currentLabel}</span>
        <ChevronsUpDownIcon className="size-4 shrink-0 text-[var(--muted)]" />
      </button>

      {open && !disabled ? (
        <div
          role="listbox"
          aria-label={ariaLabel ?? placeholder}
          className="absolute bottom-[calc(100%+0.5rem)] left-0 z-50 max-h-72 w-full overflow-hidden rounded-[0.8rem] border border-[var(--line)] bg-[var(--panel-strong)] p-1 shadow-[var(--panel-shadow)] animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-2 duration-200"
        >
          <div className="max-h-64 overflow-y-auto overscroll-contain pr-1">
            <div className="space-y-1">
              {options.map((option) => {
                const active = option.value === value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    role="option"
                    aria-selected={active}
                    disabled={option.disabled}
                    onClick={() => {
                      if (!option.disabled) {
                        onChange(option.value);
                        setOpen(false);
                      }
                    }}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-[0.7rem] px-2.5 py-1.5 text-left text-[13px] transition-all duration-150",
                      active
                        ? "bg-[var(--accent-soft)] font-medium text-[var(--ink)]"
                        : "text-[var(--ink)] hover:bg-[var(--panel-muted)]",
                      option.disabled && "cursor-not-allowed opacity-50"
                    )}
                  >
                    <span className="min-w-0 whitespace-normal break-words">{option.label}</span>
                    {active ? <CheckIcon className="size-4 shrink-0 text-[var(--primary)]" /> : null}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Form Field Wrapper
 *
 * Provides consistent spacing and layout for form fields with labels and helper text.
 */
export interface FormFieldProps {
  label?: string;
  helperText?: string;
  error?: string;
  required?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function FormField({
  label,
  helperText,
  error,
  required,
  children,
  className,
}: FormFieldProps) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      {label ? (
        <label className="text-[13px] font-medium text-[var(--ink)]">
          {label}
          {required ? <span className="ml-1 text-[var(--danger)]">*</span> : null}
        </label>
      ) : null}
      {children}
      {helperText && !error ? (
        <p className="text-xs text-[var(--muted)]">{helperText}</p>
      ) : null}
      {error ? (
        <p className="text-xs text-[var(--danger)]">{error}</p>
      ) : null}
    </div>
  );
}
