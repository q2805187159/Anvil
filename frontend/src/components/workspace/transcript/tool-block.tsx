"use client";

import React, { useMemo, useState } from "react";
import { FolderOpenIcon, HardDriveUploadIcon, NotebookPenIcon, SearchIcon, SquareTerminalIcon, WrenchIcon } from "lucide-react";

import type { ArtifactRefView, ToolActivityView } from "@/src/core/contracts";
import { useI18n } from "@/src/core/i18n";
import { Badge } from "@/src/components/ui/badge";
import { ArtifactRefList } from "./common";
import { WorkspaceRichContent } from "../workspace-rich-content";

function tryParseJson(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

function getPath(args: Record<string, unknown>) {
  return typeof args.path === "string" ? args.path : null;
}

function getContent(args: Record<string, unknown>) {
  return typeof args.content === "string" ? args.content : null;
}

function getCommand(args: Record<string, unknown>, parsedResult: unknown) {
  if (typeof args.command === "string" && args.command.trim()) {
    return args.command;
  }
  if (parsedResult && typeof parsedResult === "object" && !Array.isArray(parsedResult) && typeof (parsedResult as Record<string, unknown>).command === "string") {
    return String((parsedResult as Record<string, unknown>).command);
  }
  return null;
}

function getCwd(args: Record<string, unknown>, parsedResult: unknown) {
  if (typeof args.cwd === "string" && args.cwd.trim()) {
    return args.cwd;
  }
  if (parsedResult && typeof parsedResult === "object" && !Array.isArray(parsedResult) && typeof (parsedResult as Record<string, unknown>).cwd === "string") {
    return String((parsedResult as Record<string, unknown>).cwd);
  }
  return null;
}

function getOutput(parsedResult: unknown) {
  if (!parsedResult || typeof parsedResult !== "object" || Array.isArray(parsedResult)) {
    return null;
  }
  const payload = parsedResult as Record<string, unknown>;
  if (typeof payload.output === "string" && payload.output.trim()) {
    return payload.output;
  }
  if (typeof payload.stdout === "string" && payload.stdout.trim()) {
    return payload.stdout;
  }
  return null;
}

function getExitCode(parsedResult: unknown) {
  if (!parsedResult || typeof parsedResult !== "object" || Array.isArray(parsedResult)) {
    return null;
  }
  const payload = parsedResult as Record<string, unknown>;
  return typeof payload.exit_code === "number" ? payload.exit_code : null;
}

function getWarnings(parsedResult: unknown) {
  if (!parsedResult || typeof parsedResult !== "object" || Array.isArray(parsedResult)) {
    return [];
  }
  const payload = parsedResult as Record<string, unknown>;
  return Array.isArray(payload.warnings) ? payload.warnings.map(String) : [];
}

export function ToolBlock({
  tool,
  live = false,
  artifactRefs = [],
}: {
  tool: ToolActivityView;
  live?: boolean;
  artifactRefs?: ArtifactRefView[];
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(live || tool.status !== "completed");
  const parsedResult = useMemo(() => tryParseJson(tool.result_text), [tool.result_text]);
  const path = getPath(tool.args);
  const content = getContent(tool.args);
  const command = getCommand(tool.args, parsedResult);
  const cwd = getCwd(tool.args, parsedResult);
  const output = getOutput(parsedResult);
  const exitCode = getExitCode(parsedResult);
  const warnings = getWarnings(parsedResult);

  const headerLabel = tool.display_name ?? tool.name ?? t.transcript.tool;
  const toolName = tool.name ?? null;
  const icon =
    toolName === "write_file"
      ? NotebookPenIcon
      : toolName === "read_file"
        ? HardDriveUploadIcon
        : toolName === "list_dir"
          ? FolderOpenIcon
          : toolName === "web_search"
            ? SearchIcon
            : toolName === "bash" || toolName === "run_command" || toolName === "process"
              ? SquareTerminalIcon
              : WrenchIcon;
  const Icon = icon;

  return (
    <div className="min-w-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] shadow-[var(--shadow-card)]">
      <button
        type="button"
        onClick={() => setExpanded((current) => !current)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
      >
        <span className="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-[var(--ink)]">
          <Icon className="size-4 text-[var(--primary)]" />
          <span className="min-w-0 break-words [overflow-wrap:anywhere]">{headerLabel}</span>
        </span>
        <div className="flex shrink-0 items-center gap-2">
          {tool.capability_group ? <Badge tone="neutral">{tool.capability_group}</Badge> : null}
          <span className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{tool.status}</span>
        </div>
      </button>
      {expanded ? (
        <div className="min-w-0 space-y-2 border-t border-[var(--line)] px-3 py-3">
          {command ? (
            <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
              <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">Command</div>
              <pre className="mt-2 max-w-full overflow-auto whitespace-pre-wrap break-all text-xs text-[var(--ink)]/90">{command}</pre>
            </div>
          ) : null}

          {cwd ? (
            <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 text-sm text-[var(--ink)]">
              <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">cwd</div>
              <div className="mt-1 break-all">{cwd}</div>
            </div>
          ) : null}

          {path ? (
            <div className="min-w-0 break-all rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 text-sm text-[var(--ink)] [overflow-wrap:anywhere]">
              {path}
            </div>
          ) : null}

          {tool.name === "list_dir" && Array.isArray(parsedResult) ? (
            <div className="flex flex-wrap gap-2">
              {parsedResult.map((item) => (
                <Badge key={String(item)} tone="accent">
                  {String(item)}
                </Badge>
              ))}
            </div>
          ) : null}

          {tool.name === "write_file" && content ? (
            <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
              <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{t.transcript.contentLabel}</div>
              <div className="mt-2 min-w-0 break-words text-sm text-[var(--ink)]/90 [overflow-wrap:anywhere]">{content.slice(0, 240)}</div>
            </div>
          ) : null}

          {tool.name === "read_file" && tool.result_text ? (
            <WorkspaceRichContent content={tool.result_text} />
          ) : null}

          {tool.name === "extract_document" && parsedResult && typeof parsedResult === "object" && !Array.isArray(parsedResult) ? (
            <div className="space-y-3">
              <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 text-sm text-[var(--ink)]">
                <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">Provider</div>
                <div className="mt-1">{String((parsedResult as Record<string, unknown>).provider ?? "unknown")}</div>
              </div>
              {typeof (parsedResult as Record<string, unknown>).content === "string" ? (
                <WorkspaceRichContent content={String((parsedResult as Record<string, unknown>).content)} />
              ) : null}
            </div>
          ) : null}

          {tool.name === "export_document" && parsedResult && typeof parsedResult === "object" && !Array.isArray(parsedResult) ? (
            <div className="space-y-3">
              <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 text-sm text-[var(--ink)]">
                <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">Output</div>
                <div className="mt-1 break-all">{String((parsedResult as Record<string, unknown>).output_path ?? "")}</div>
              </div>
              {warnings.length > 0 ? (
                <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]">
                  {warnings.map((warning) => (
                    <div key={warning}>{warning}</div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {!["read_file", "list_dir", "run_command", "process", "extract_document", "export_document"].includes(tool.name ?? "") && Object.keys(tool.args).length > 0 ? (
            <pre className="max-w-full overflow-auto whitespace-pre-wrap break-all rounded-xl bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]">
              {JSON.stringify(tool.args, null, 2)}
            </pre>
          ) : null}

          {output ? (
            <pre className="max-w-full overflow-auto whitespace-pre-wrap break-all rounded-xl bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]">
              {output}
            </pre>
          ) : null}

          {tool.result_text && output === null && !["read_file", "list_dir", "extract_document", "export_document"].includes(tool.name ?? "") ? (
            <pre className="max-w-full overflow-auto whitespace-pre-wrap break-all rounded-xl bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]">
              {tool.result_text}
            </pre>
          ) : null}

          {exitCode !== null ? (
            <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">exit {exitCode}</div>
          ) : null}

          {tool.duration_ms !== null ? (
            <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{tool.duration_ms}ms</div>
          ) : null}

          <ArtifactRefList artifactRefs={artifactRefs} />
        </div>
      ) : null}
    </div>
  );
}
