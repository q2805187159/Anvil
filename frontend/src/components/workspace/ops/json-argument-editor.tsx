"use client";

import React from "react";

import { Textarea } from "@/src/components/ui/textarea";

type JsonArgumentEditorProps = {
  value: string;
  onChange(value: string): void;
  placeholder?: string;
  error?: string | null;
};

export function JsonArgumentEditor({
  value,
  onChange,
  placeholder = "{\n  \n}",
  error,
}: JsonArgumentEditorProps) {
  return (
    <div className="space-y-2">
      <Textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="min-h-[180px] rounded-xl"
      />
      {error ? <div className="text-sm text-[var(--danger)]">{error}</div> : null}
    </div>
  );
}
