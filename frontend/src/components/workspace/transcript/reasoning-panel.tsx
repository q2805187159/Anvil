"use client";

import React, { useEffect, useMemo, useState } from "react";
import { BrainCircuitIcon } from "lucide-react";

import { useI18n } from "@/src/core/i18n";
import { WorkspaceRichContent } from "../workspace-rich-content";

type ReasoningSegment = {
  id: string;
  label: string;
  preview: string;
  content: string;
};

function splitLongSegment(segment: string) {
  const limit = 260;
  const chunks: string[] = [];
  let remaining = segment.trim();

  while (remaining.length > limit) {
    const lineBreak = remaining.lastIndexOf("\n", limit);
    const sentenceBreak = remaining.lastIndexOf(". ", limit);
    const splitAt = Math.max(lineBreak, sentenceBreak) >= 120 ? Math.max(lineBreak, sentenceBreak) + 1 : limit;
    chunks.push(remaining.slice(0, splitAt).trim());
    remaining = remaining.slice(splitAt).trim();
  }

  if (remaining) {
    chunks.push(remaining);
  }

  return chunks;
}

function buildSegments(reasoning: string, locale: "en-US" | "zh-CN"): ReasoningSegment[] {
  const normalized = reasoning.replace(/\r\n?/g, "\n").trim();
  if (!normalized) {
    return [];
  }

  const primarySegments = normalized
    .split(/\n{2,}/)
    .map((segment) => segment.trim())
    .filter(Boolean);
  const sourceSegments =
    primarySegments.length > 1
      ? primarySegments
      : normalized
          .split(/\n+/)
          .map((segment) => segment.trim())
          .filter(Boolean);

  return sourceSegments.flatMap((segment) => splitLongSegment(segment)).map((segment, index) => {
    const preview = segment.replace(/\s+/g, " ").trim();
    return {
      id: `segment-${index}`,
      label: locale === "zh-CN" ? `思考片段 ${index + 1}` : `Thought step ${index + 1}`,
      preview: preview.length > 96 ? `${preview.slice(0, 96).trimEnd()}…` : preview,
      content: segment,
    };
  });
}

function formatReasoningDuration(durationMs: number | null | undefined, locale: "en-US" | "zh-CN") {
  if (durationMs === null || durationMs === undefined) {
    return locale === "zh-CN" ? "已思考数秒" : "Thought for a few seconds";
  }
  const seconds = Math.max(1, Math.round(durationMs / 1000));
  return locale === "zh-CN" ? `已思考 ${seconds} 秒` : `Thought for ${seconds}s`;
}

export function ReasoningPanel({
  reasoning,
  defaultOpen = false,
  collapseWhenComplete = false,
  durationMs = null,
}: {
  reasoning: string;
  defaultOpen?: boolean;
  collapseWhenComplete?: boolean;
  durationMs?: number | null;
}) {
  const { t, locale } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const [expandedSegmentId, setExpandedSegmentId] = useState<string | null>(null);
  const segments = useMemo(() => buildSegments(reasoning, locale), [locale, reasoning]);
  const title = collapseWhenComplete ? formatReasoningDuration(durationMs, locale) : t.transcript.reasoning;

  useEffect(() => {
    if (collapseWhenComplete) {
      setOpen(false);
      setExpandedSegmentId(null);
    }
  }, [collapseWhenComplete]);

  return (
    <div className="mb-2 min-w-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] shadow-[var(--shadow-card)]">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
      >
        <span className="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-[var(--ink)]">
          <BrainCircuitIcon className="size-4 text-[var(--primary)]" />
          <span className="min-w-0 truncate">{title}</span>
        </span>
        <span className="shrink-0 text-xs text-[var(--muted)]">{open ? t.transcript.hide : t.transcript.show}</span>
      </button>
      {open ? (
        <div className="min-w-0 border-t border-[var(--line)] px-3 py-3">
          {collapseWhenComplete && segments.length > 0 ? (
            <div className="space-y-2">
              {segments.map((segment) => {
                const expanded = expandedSegmentId === segment.id;
                return (
                  <div key={segment.id} className="min-w-0 overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel-muted)]">
                    <button
                      type="button"
                      className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
                      onClick={() => setExpandedSegmentId((current) => (current === segment.id ? null : segment.id))}
                    >
                      <div className="min-w-0">
                        <div className="text-[13px] font-semibold text-[var(--ink)]">{segment.label}</div>
                        <div className="mt-1 truncate text-xs text-[var(--muted)]">{segment.preview}</div>
                      </div>
                      <span className="shrink-0 text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
                        {expanded ? t.transcript.hide : t.transcript.show}
                      </span>
                    </button>
                    {expanded ? (
                      <div className="border-t border-[var(--line)] px-3 py-3">
                        <WorkspaceRichContent content={segment.content} />
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <WorkspaceRichContent content={reasoning} />
          )}
        </div>
      ) : null}
    </div>
  );
}
