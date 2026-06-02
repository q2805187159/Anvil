"use client";

import React, { useMemo } from "react";
import { Select, type SelectOption } from "@/src/components/ui/select";

export interface ModelPickerProps {
  models: Array<Record<string, unknown>>;
  selectedModelName: string;
  onSelectedModelNameChange: (value: string) => void;
  placeholder?: string;
  compact?: boolean;
  className?: string;
}

/**
 * Model Picker Component
 *
 * A specialized select component for choosing AI models.
 * Wraps the unified Select component with model-specific logic.
 */
export function ModelPicker({
  models,
  selectedModelName,
  onSelectedModelNameChange,
  placeholder = "Select model",
  compact = false,
  className,
}: ModelPickerProps) {
  const options: SelectOption[] = useMemo(
    () =>
      Array.from(
        new Set(
          models
            .map((model) => String(model.name ?? "").trim())
            .filter(Boolean),
        ),
      ).map((name) => ({ value: name, label: name })),
    [models]
  );

  return (
    <Select
      options={options}
      value={selectedModelName}
      onChange={onSelectedModelNameChange}
      placeholder={placeholder}
      disabled={options.length === 0}
      compact={compact}
      className={className}
      aria-label={placeholder}
    />
  );
}
