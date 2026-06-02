"use client";

import React, { useEffect, useMemo, useState } from "react";

import { cn } from "@/src/lib/utils";
import { Tooltip } from "@/src/components/ui";
import { WorkspaceRichContent } from "./workspace-rich-content";

type ArtifactPreviewPanelProps = {
  label: string;
  artifactUrl: string;
};

type PreviewKind = "image" | "html" | "markdown" | "json" | "text";

function HoverRevealText({ value, className }: { value: string; className?: string }) {
  return (
    <Tooltip
      content={<span className="block max-w-[min(28rem,82vw)] whitespace-normal break-words">{value}</span>}
      className="max-w-[min(30rem,86vw)]"
    >
      <span className={cn("block min-w-0 max-w-full truncate align-bottom", className)} title={value}>
        {value}
      </span>
    </Tooltip>
  );
}

export function ArtifactPreviewPanel({ label, artifactUrl }: ArtifactPreviewPanelProps) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const previewKind = useMemo(() => detectPreviewKind(label), [label]);
  const jsonValue = useMemo(() => parseJsonObject(content), [content]);
  const presentationManifest = useMemo(() => normalizePresentationManifest(jsonValue), [jsonValue]);
  const browserEvidenceManifest = useMemo(() => normalizePresentationBrowserEvidenceManifest(jsonValue), [jsonValue]);
  const companionHtmlUrl = useMemo(
    () => (presentationManifest ? deriveCompanionHtmlUrl(artifactUrl) : null),
    [artifactUrl, presentationManifest],
  );

  useEffect(() => {
    if (!artifactUrl || previewKind === "image" || previewKind === "html") {
      setContent(null);
      setError(null);
      return;
    }

    let active = true;
    async function load() {
      try {
        const response = await fetch(artifactUrl);
        if (!response.ok) {
          throw new Error(`artifact preview failed: ${response.status}`);
        }
        const text = await response.text();
        if (active) {
          setContent(text);
          setError(null);
        }
      } catch {
        if (active) {
          setError("Preview unavailable.");
        }
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, [artifactUrl, previewKind]);

  return (
    <div className="rounded-[1rem] border border-[var(--line)] bg-[var(--panel)] p-4">
      <div className="mb-3 flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0 text-sm font-medium text-[var(--ink)]">
          <HoverRevealText value={label} />
          {presentationManifest ? (
            <span className="mt-1 block text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
              Presentation {presentationManifest.kind}
            </span>
          ) : null}
          {browserEvidenceManifest ? (
            <span className="mt-1 block text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
              Presentation browser evidence
            </span>
          ) : null}
        </div>
        {companionHtmlUrl ? (
          <a
            href={companionHtmlUrl}
            target="_blank"
            rel="noreferrer"
            className="shrink-0 rounded-full border border-[var(--line)] px-2.5 py-1 text-xs font-medium text-[var(--ink)] transition hover:border-[var(--primary)] hover:text-[var(--primary)]"
          >
            Open report
          </a>
        ) : null}
      </div>
      {previewKind === "image" ? (
        <img src={artifactUrl} alt={label} className="max-h-[360px] w-full rounded-xl object-contain" />
      ) : null}
      {previewKind === "html" ? (
        <iframe title={label} src={artifactUrl} className="h-[360px] w-full rounded-xl border border-[var(--line)] bg-white" />
      ) : null}
      {previewKind === "markdown" && content ? <WorkspaceRichContent content={content} /> : null}
      {previewKind === "json" && presentationManifest ? (
        <PresentationManifestPreview manifest={presentationManifest} companionHtmlUrl={companionHtmlUrl} />
      ) : null}
      {previewKind === "json" && browserEvidenceManifest ? (
        <PresentationBrowserEvidencePreview manifest={browserEvidenceManifest} />
      ) : null}
      {previewKind === "json" && content && !presentationManifest && !browserEvidenceManifest ? (
        <pre className="max-h-[420px] overflow-auto rounded-xl bg-[var(--panel-muted)] p-4 text-xs text-[var(--muted)]">{formatJsonPreview(content)}</pre>
      ) : null}
      {previewKind === "text" && content ? (
        <pre className="max-h-[420px] overflow-auto rounded-xl bg-[var(--panel-muted)] p-4 text-xs text-[var(--muted)]">{content}</pre>
      ) : null}
      {error ? <div className="text-sm text-[var(--muted)]">{error}</div> : null}
    </div>
  );
}

function PresentationBrowserEvidencePreview({ manifest }: { manifest: PresentationBrowserEvidenceManifest }) {
  const comparison = manifest.comparison;
  const pixelDelta = comparison?.pixelDelta;
  const topCells = pixelDelta?.topCells ?? [];
  const imageItems = [
    manifest.screenshotArtifactUrl ? { label: "Screenshot", url: manifest.screenshotArtifactUrl } : null,
    manifest.baselineScreenshotArtifactUrl ? { label: "Baseline", url: manifest.baselineScreenshotArtifactUrl } : null,
    manifest.candidateScreenshotArtifactUrl ? { label: "Candidate", url: manifest.candidateScreenshotArtifactUrl } : null,
    manifest.overlayArtifactUrl ? { label: "Overlay diff", url: manifest.overlayArtifactUrl } : null,
  ].filter(Boolean) as Array<{ label: string; url: string }>;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <MetricPill label="Status" value={manifest.status} tone={statusTone(manifest.status)} />
        <MetricPill label="Kind" value={manifest.kind === "browser-diff" ? "Browser diff" : "Browser snapshot"} />
        {comparison ? (
          <>
            <MetricPill label="Screenshot" value={comparison.bytesChanged ? "changed" : "same"} tone={comparison.bytesChanged ? "warning" : "success"} />
            <MetricPill label="Pixel diff" value={formatPixelDeltaValue(pixelDelta)} tone={comparison.pixelsChanged ? "warning" : "success"} />
          </>
        ) : (
          <>
            <MetricPill label="Format" value={manifest.format ?? "unknown"} />
            <MetricPill label="Bytes" value={formatCompactValue(manifest.bytes)} />
          </>
        )}
      </div>

      <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
        <div className="mb-2 text-xs font-medium uppercase tracking-[0.06em] text-[var(--muted)]">Evidence</div>
        <div className="grid gap-1.5 text-xs text-[var(--muted)]">
          {manifest.reportUrl ? <EvidenceRow label="Report" value={manifest.reportUrl} href={manifest.reportUrl} /> : null}
          {manifest.baselineReportUrl ? <EvidenceRow label="Baseline report" value={manifest.baselineReportUrl} href={manifest.baselineReportUrl} /> : null}
          {manifest.candidateReportUrl ? <EvidenceRow label="Candidate report" value={manifest.candidateReportUrl} href={manifest.candidateReportUrl} /> : null}
          {manifest.navigationTitle ? <EvidenceRow label="Title" value={manifest.navigationTitle} /> : null}
          {comparison ? <EvidenceRow label="Byte delta" value={formatCompactValue(comparison.byteDelta)} /> : null}
          {comparison ? <EvidenceRow label="Snapshot" value={comparison.snapshotChanged ? "changed" : "same"} /> : null}
          {pixelDelta?.available ? (
            <>
              <EvidenceRow label="Image size" value={`${pixelDelta.width ?? "?"} × ${pixelDelta.height ?? "?"}`} />
              <EvidenceRow label="Changed pixels" value={`${pixelDelta.changedPixels ?? 0} / ${pixelDelta.totalPixels ?? 0}`} />
              <EvidenceRow label="Mean intensity" value={formatCompactValue(pixelDelta.meanIntensity)} />
            </>
          ) : null}
          {pixelDelta && !pixelDelta.available ? <EvidenceRow label="Pixel diff" value={pixelDelta.reason ?? "unavailable"} /> : null}
        </div>
      </div>

      {imageItems.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {imageItems.map((item) => (
            <EvidenceImage key={item.label} label={item.label} url={item.url} />
          ))}
        </div>
      ) : null}

      {topCells.length ? (
        <ArtifactList title="Changed cells" total={topCells.length}>
          {topCells.slice(0, 6).map((cell, index) => (
            <ArtifactListItem
              key={`${cell.cell.join("-")}-${index}`}
              title={`Cell ${cell.cell[0]}, ${cell.cell[1]} · ${formatPercent(cell.changedRatio)}`}
              detail={`x:${cell.bounds.x} y:${cell.bounds.y} w:${cell.bounds.width} h:${cell.bounds.height} · pixels ${cell.changedPixels}`}
              badge={formatCompactValue(cell.meanIntensity)}
            />
          ))}
        </ArtifactList>
      ) : null}
    </div>
  );
}

function EvidenceRow({ label, value, href }: { label: string; value: string; href?: string }) {
  const content = href ? (
    <a href={href} target="_blank" rel="noreferrer" className="block min-w-0 font-medium text-[var(--primary)] underline-offset-2 hover:underline">
      <HoverRevealText value={value} />
    </a>
  ) : (
    <HoverRevealText value={value} className="font-medium text-[var(--ink)]" />
  );
  return (
    <div className="flex min-w-0 items-center justify-between gap-3">
      <span className="shrink-0 text-[var(--muted)]">{label}</span>
      <span className="min-w-0 text-right">{content}</span>
    </div>
  );
}

function EvidenceImage({ label, url }: { label: string; url: string }) {
  return (
    <a href={url} target="_blank" rel="noreferrer" className="block rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-2 transition hover:border-[var(--primary)]">
      <HoverRevealText value={label} className="mb-2 text-xs font-medium uppercase tracking-[0.06em] text-[var(--muted)]" />
      <img src={url} alt={label} className="h-44 w-full rounded-lg border border-[var(--line)] bg-white object-contain" />
    </a>
  );
}

function PresentationManifestPreview({
  manifest,
  companionHtmlUrl,
}: {
  manifest: PresentationManifest;
  companionHtmlUrl: string | null;
}) {
  const pageIssues = manifest.pages.flatMap((page) =>
    page.issues.map((issue) => ({
      ...issue,
      pageName: page.name || page.path || `Page ${page.index ?? ""}`.trim(),
    })),
  );
  const visibleIssues = pageIssues.slice(0, 6);
  const visibleAnnotations = manifest.annotations.slice(0, 6);

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <MetricPill label="Status" value={manifest.status || "unknown"} tone={statusTone(manifest.status)} />
        <MetricPill label="Pages" value={String(manifest.pages.length || manifest.checkedCount || 0)} />
        <MetricPill label="Issues" value={String(manifest.issueCount)} tone={manifest.issueCount > 0 ? "warning" : "success"} />
        <MetricPill label="Annotations" value={String(manifest.annotations.length)} tone={manifest.annotations.length > 0 ? "accent" : "neutral"} />
      </div>

      {manifest.summaryEntries.length ? (
        <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
          <div className="mb-2 text-xs font-medium uppercase tracking-[0.06em] text-[var(--muted)]">Summary</div>
          <div className="grid gap-1.5 text-xs text-[var(--muted)]">
            {manifest.summaryEntries.slice(0, 8).map(([key, value]) => (
              <div key={key} className="grid min-w-0 grid-cols-[minmax(0,45%)_minmax(0,55%)] items-center gap-3">
                <HoverRevealText value={humanizeKey(key)} />
                <HoverRevealText value={formatCompactValue(value)} className="font-medium text-[var(--ink)]" />
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {companionHtmlUrl ? (
        <iframe
          title="Presentation artifact report"
          src={companionHtmlUrl}
          className="h-[260px] w-full rounded-xl border border-[var(--line)] bg-white"
        />
      ) : null}

      {visibleAnnotations.length ? (
        <ArtifactList title="Annotations" total={manifest.annotations.length}>
          {visibleAnnotations.map((annotation, index) => (
            <ArtifactListItem
              key={`${annotation.code}-${annotation.pageName}-${index}`}
              title={`${annotation.pageName} · ${annotation.code}`}
              detail={annotation.message}
              badge={annotation.severity}
            />
          ))}
        </ArtifactList>
      ) : null}

      {visibleIssues.length ? (
        <ArtifactList title="Issues" total={pageIssues.length}>
          {visibleIssues.map((issue, index) => (
            <ArtifactListItem
              key={`${issue.code}-${issue.pageName}-${index}`}
              title={`${issue.pageName} · ${issue.code}`}
              detail={issue.message}
              badge={issue.severity}
            />
          ))}
        </ArtifactList>
      ) : null}

      {manifest.recommendations.length ? (
        <ArtifactList title="Recommendations" total={manifest.recommendations.length}>
          {manifest.recommendations.slice(0, 5).map((recommendation, index) => (
            <ArtifactListItem key={`${recommendation}-${index}`} title={recommendation} />
          ))}
        </ArtifactList>
      ) : null}
    </div>
  );
}

function MetricPill({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "success" | "warning" | "danger" | "accent";
}) {
  return (
    <div className={`rounded-xl border px-3 py-2 ${metricToneClass(tone)}`}>
      <div className="text-[11px] uppercase tracking-[0.06em] opacity-70">{label}</div>
      <HoverRevealText value={value} className="mt-1 text-sm font-semibold" />
    </div>
  );
}

function ArtifactList({ title, total, children }: { title: string; total: number; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="text-xs font-medium uppercase tracking-[0.06em] text-[var(--muted)]">{title}</div>
        <div className="text-xs text-[var(--muted)]">{total}</div>
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function ArtifactListItem({ title, detail, badge }: { title: string; detail?: string; badge?: string }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <HoverRevealText value={title} className="text-sm font-medium text-[var(--ink)]" />
        {badge ? (
          <span className="shrink-0 rounded-full border border-[var(--line)] px-2 py-0.5 text-[11px] text-[var(--muted)]">{badge}</span>
        ) : null}
      </div>
      {detail ? (
        <Tooltip
          content={<span className="block max-w-[min(30rem,82vw)] whitespace-normal break-words">{detail}</span>}
          className="max-w-[min(32rem,86vw)]"
        >
          <span className="mt-1 block min-w-0 max-w-full line-clamp-2 text-xs leading-5 text-[var(--muted)]" title={detail}>
            {detail}
          </span>
        </Tooltip>
      ) : null}
    </div>
  );
}

type PresentationManifest = {
  kind: "template" | "review" | "diff" | "annotation";
  status: string;
  checkedCount: number;
  issueCount: number;
  pages: PresentationManifestPage[];
  annotations: PresentationAnnotationPreview[];
  recommendations: string[];
  summaryEntries: Array<[string, unknown]>;
};

type PresentationManifestPage = {
  index?: number;
  name?: string;
  path?: string;
  issues: PresentationIssuePreview[];
};

type PresentationIssuePreview = {
  severity: string;
  code: string;
  message: string;
};

type PresentationAnnotationPreview = PresentationIssuePreview & {
  pageName: string;
};

type PresentationBrowserEvidenceManifest = {
  kind: "browser-snapshot" | "browser-diff";
  status: string;
  reportUrl?: string;
  baselineReportUrl?: string;
  candidateReportUrl?: string;
  screenshotArtifactUrl?: string;
  baselineScreenshotArtifactUrl?: string;
  candidateScreenshotArtifactUrl?: string;
  overlayArtifactUrl?: string;
  format?: string;
  bytes?: number;
  navigationTitle?: string;
  comparison?: PresentationBrowserComparison;
};

type PresentationBrowserComparison = {
  bytesChanged: boolean;
  snapshotChanged: boolean;
  pixelsChanged: boolean;
  byteDelta?: number;
  pixelDelta?: PresentationPixelDelta;
};

type PresentationPixelDelta = {
  available: boolean;
  changed: boolean;
  reason?: string;
  width?: number;
  height?: number;
  changedPixels?: number;
  totalPixels?: number;
  changedRatio?: number;
  meanIntensity?: number;
  maxIntensity?: number;
  topCells: PresentationPixelCell[];
};

type PresentationPixelCell = {
  cell: [number, number];
  bounds: { x: number; y: number; width: number; height: number };
  changedPixels: number;
  changedRatio: number;
  meanIntensity: number;
};

function detectPreviewKind(label: string): PreviewKind {
  const normalized = label.toLowerCase();
  if (/\.(png|jpg|jpeg|gif|webp|svg)$/.test(normalized)) {
    return "image";
  }
  if (/\.html?$/.test(normalized)) {
    return "html";
  }
  if (/\.md$/.test(normalized)) {
    return "markdown";
  }
  if (/\.json$/.test(normalized)) {
    return "json";
  }
  return "text";
}

function parseJsonObject(content: string | null): Record<string, unknown> | null {
  if (!content) {
    return null;
  }
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function normalizePresentationManifest(value: Record<string, unknown> | null): PresentationManifest | null {
  if (!value || !isPresentationManifest(value)) {
    return null;
  }
  const pages = arrayOfObjects(value.pages).map((page) => ({
    index: typeof page.index === "number" ? page.index : undefined,
    name: stringValue(page.name),
    path: stringValue(page.path) ?? stringValue(page.source_path) ?? stringValue(page.candidate_path) ?? stringValue(page.baseline_path),
    issues: arrayOfObjects(page.issues).map(normalizeIssue).filter(Boolean) as PresentationIssuePreview[],
  }));
  const annotations = arrayOfObjects(value.annotations)
    .map((annotation) => {
      const issue = normalizeIssue(annotation);
      if (!issue) {
        return null;
      }
      return {
        ...issue,
        pageName: stringValue(annotation.page_name) ?? stringValue(annotation.path) ?? "Page",
      };
    })
    .filter(Boolean) as PresentationAnnotationPreview[];
  const issueCount = pages.reduce((count, page) => count + page.issues.length, 0);
  const summary = objectValue(value.summary);
  const recommendations = arrayOfStrings(value.recommendations);
  return {
    kind: detectPresentationManifestKind(value),
    status: stringValue(value.status) ?? "unknown",
    checkedCount: numberValue(value.checked_count) ?? pages.length,
    issueCount,
    pages,
    annotations,
    recommendations,
    summaryEntries: summary ? Object.entries(summary).filter(([, entryValue]) => isScalarPreviewValue(entryValue)) : [],
  };
}

function normalizePresentationBrowserEvidenceManifest(value: Record<string, unknown> | null): PresentationBrowserEvidenceManifest | null {
  if (!value || !isPresentationBrowserEvidenceManifest(value)) {
    return null;
  }
  const comparison = objectValue(value.comparison);
  const normalizedComparison = comparison ? normalizeBrowserComparison(comparison) : undefined;
  const status = stringValue(value.status) ?? (booleanValue(value.success) === false ? "failed" : normalizedComparison?.bytesChanged || normalizedComparison?.snapshotChanged || normalizedComparison?.pixelsChanged ? "changed" : "ready");
  return {
    kind: comparison ? "browser-diff" : "browser-snapshot",
    status,
    reportUrl: stringValue(value.report_url),
    baselineReportUrl: stringValue(value.baseline_report_url),
    candidateReportUrl: stringValue(value.candidate_report_url),
    screenshotArtifactUrl: stringValue(value.screenshot_artifact_url) ?? stringValue(value.artifact_url),
    baselineScreenshotArtifactUrl: stringValue(value.baseline_screenshot_artifact_url),
    candidateScreenshotArtifactUrl: stringValue(value.candidate_screenshot_artifact_url),
    overlayArtifactUrl: stringValue(value.overlay_artifact_url),
    format: stringValue(value.format),
    bytes: numberValue(value.bytes),
    navigationTitle: stringValue(objectValue(value.navigation)?.title),
    comparison: normalizedComparison,
  };
}

function isPresentationBrowserEvidenceManifest(value: Record<string, unknown>): boolean {
  const hasSnapshotEvidence = typeof value.report_url === "string" && typeof value.screenshot_artifact_url === "string";
  const hasDiffEvidence =
    typeof value.baseline_report_url === "string" &&
    typeof value.candidate_report_url === "string" &&
    typeof value.baseline_screenshot_artifact_url === "string" &&
    typeof value.candidate_screenshot_artifact_url === "string" &&
    Boolean(objectValue(value.comparison));
  return hasSnapshotEvidence || hasDiffEvidence;
}

function normalizeBrowserComparison(value: Record<string, unknown>): PresentationBrowserComparison {
  const pixelDelta = objectValue(value.pixel_delta);
  return {
    bytesChanged: booleanValue(value.bytes_changed) ?? false,
    snapshotChanged: booleanValue(value.snapshot_changed) ?? false,
    pixelsChanged: booleanValue(value.pixels_changed) ?? booleanValue(pixelDelta?.changed) ?? false,
    byteDelta: numberValue(value.byte_delta),
    pixelDelta: pixelDelta ? normalizePixelDelta(pixelDelta) : undefined,
  };
}

function normalizePixelDelta(value: Record<string, unknown>): PresentationPixelDelta {
  return {
    available: booleanValue(value.available) ?? false,
    changed: booleanValue(value.changed) ?? false,
    reason: stringValue(value.reason),
    width: numberValue(value.width),
    height: numberValue(value.height),
    changedPixels: numberValue(value.changed_pixels),
    totalPixels: numberValue(value.total_pixels),
    changedRatio: numberValue(value.changed_ratio),
    meanIntensity: numberValue(value.mean_intensity),
    maxIntensity: numberValue(value.max_intensity),
    topCells: arrayOfObjects(value.top_cells).map(normalizePixelCell).filter(Boolean) as PresentationPixelCell[],
  };
}

function normalizePixelCell(value: Record<string, unknown>): PresentationPixelCell | null {
  const rawCell = Array.isArray(value.cell) ? value.cell : [];
  const x = numberValue(rawCell[0]);
  const y = numberValue(rawCell[1]);
  const bounds = objectValue(value.bounds);
  if (x === undefined || y === undefined || !bounds) {
    return null;
  }
  return {
    cell: [x, y],
    bounds: {
      x: numberValue(bounds.x) ?? 0,
      y: numberValue(bounds.y) ?? 0,
      width: numberValue(bounds.width) ?? 0,
      height: numberValue(bounds.height) ?? 0,
    },
    changedPixels: numberValue(value.changed_pixels) ?? 0,
    changedRatio: numberValue(value.changed_ratio) ?? 0,
    meanIntensity: numberValue(value.mean_intensity) ?? 0,
  };
}

function isPresentationManifest(value: Record<string, unknown>): boolean {
  if (!Array.isArray(value.pages)) {
    return false;
  }
  return (
    typeof value.status === "string" &&
    (typeof value.template_id === "string" ||
      typeof value.target === "string" ||
      typeof value.baseline === "string" ||
      typeof value.candidate === "string" ||
      typeof value.mode === "string" ||
      Array.isArray(value.annotations))
  );
}

function detectPresentationManifestKind(value: Record<string, unknown>): PresentationManifest["kind"] {
  if (Array.isArray(value.annotations) || typeof value.mode === "string") {
    return "annotation";
  }
  if (typeof value.baseline === "string" || typeof value.candidate === "string") {
    return "diff";
  }
  if (typeof value.target === "string") {
    return "review";
  }
  return "template";
}

function normalizeIssue(value: Record<string, unknown>): PresentationIssuePreview | null {
  const code = stringValue(value.code);
  const message = stringValue(value.message);
  if (!code || !message) {
    return null;
  }
  return {
    severity: stringValue(value.severity) ?? "info",
    code,
    message,
  };
}

function deriveCompanionHtmlUrl(artifactUrl: string): string | null {
  if (!artifactUrl || !/\/manifest\.json(?:$|[?#])/.test(artifactUrl)) {
    return null;
  }
  return artifactUrl.replace(/\/manifest\.json($|[?#].*)/, "/index.html$1");
}

function arrayOfObjects(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function arrayOfStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => stringValue(item)).filter(Boolean) as string[] : [];
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function booleanValue(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function isScalarPreviewValue(value: unknown): boolean {
  return value === null || ["string", "number", "boolean"].includes(typeof value) || Array.isArray(value);
}

function humanizeKey(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatCompactValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.slice(0, 4).map((item) => String(item)).join(", ") + (value.length > 4 ? ` +${value.length - 4}` : "");
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  if (value === null || value === undefined) {
    return "n/a";
  }
  return String(value);
}

function formatPercent(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "n/a";
  }
  return `${Math.round(value * 1000) / 10}%`;
}

function formatPixelDeltaValue(value: PresentationPixelDelta | undefined): string {
  if (!value) {
    return "n/a";
  }
  if (!value.available) {
    return "unavailable";
  }
  return value.changed ? formatPercent(value.changedRatio) : "same";
}

function formatJsonPreview(content: string): string {
  const parsed = parseJsonObject(content);
  return parsed ? JSON.stringify(parsed, null, 2) : content;
}

function statusTone(status: string): "neutral" | "success" | "warning" | "danger" | "accent" {
  if (status === "passed" || status === "ready") {
    return "success";
  }
  if (status === "failed" || status === "error") {
    return "danger";
  }
  if (status === "warning" || status === "mixed") {
    return "warning";
  }
  if (status === "changed") {
    return "warning";
  }
  return "neutral";
}

function metricToneClass(tone: "neutral" | "success" | "warning" | "danger" | "accent") {
  if (tone === "success") {
    return "border-[color-mix(in_srgb,var(--success)_35%,var(--line))] bg-[color-mix(in_srgb,var(--success)_9%,var(--panel))] text-[var(--ink)]";
  }
  if (tone === "warning") {
    return "border-[color-mix(in_srgb,var(--warning)_38%,var(--line))] bg-[color-mix(in_srgb,var(--warning)_10%,var(--panel))] text-[var(--ink)]";
  }
  if (tone === "danger") {
    return "border-[color-mix(in_srgb,var(--danger)_35%,var(--line))] bg-[color-mix(in_srgb,var(--danger)_9%,var(--panel))] text-[var(--ink)]";
  }
  if (tone === "accent") {
    return "border-[color-mix(in_srgb,var(--primary)_38%,var(--line))] bg-[color-mix(in_srgb,var(--primary)_9%,var(--panel))] text-[var(--ink)]";
  }
  return "border-[var(--line)] bg-[var(--panel-muted)] text-[var(--ink)]";
}
