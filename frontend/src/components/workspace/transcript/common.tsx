"use client";

import React from "react";

import type { ApprovalView, ArtifactRefView } from "@/src/core/contracts";
import { useI18n } from "@/src/core/i18n";
import { resolveGatewayUrl } from "@/src/core/api/client";
import { Badge } from "@/src/components/ui/badge";
import { Button } from "@/src/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/src/components/ui/dialog";
import { cn } from "@/src/lib/utils";
import { DownloadIcon, FileTextIcon, ImageIcon, ShieldAlertIcon } from "lucide-react";

export function labelForRole(role: string, t: ReturnType<typeof useI18n>["t"]) {
  if (role === "human" || role === "user") {
    return t.transcript.user;
  }
  if (role === "tool") {
    return t.transcript.tool;
  }
  if (role === "system") {
    return t.transcript.system;
  }
  return t.transcript.assistant;
}

export function ApprovalCard({
  approval,
  compact = false,
}: {
  approval: ApprovalView;
  compact?: boolean;
}) {
  const { t } = useI18n();

  return (
    <div className="rounded-[1rem] border border-[var(--warning)]/25 bg-[var(--warning-soft)] px-4 py-4 text-[var(--ink)]">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <ShieldAlertIcon className="size-4" />
        <span>{t.transcript.approvalRequired}</span>
      </div>
      {approval.reason ? <div className="mt-2 text-sm text-[var(--muted)]">{approval.reason}</div> : null}
      {!compact && approval.requested_permissions.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {approval.requested_permissions.map((permission) => (
            <Badge key={permission} tone="warning">
              {permission}
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function ArtifactRefList({
  artifactRefs,
  onSelectArtifact,
  className,
}: {
  artifactRefs: ArtifactRefView[];
  onSelectArtifact?(artifact: ArtifactRefView): void;
  className?: string;
}) {
  const [previewArtifact, setPreviewArtifact] = React.useState<ArtifactRefView | null>(null);
  const visibleArtifactRefs = artifactRefs.filter((artifact) => !artifact.internal);

  if (visibleArtifactRefs.length === 0) {
    return null;
  }

  return (
    <>
      <div className={cn("mt-2 flex min-w-0 flex-wrap gap-2", className)}>
        {visibleArtifactRefs.map((artifact, index) => {
          const label = artifact.label || artifact.virtual_path || `${artifact.kind}-${index + 1}`;
          const key = `${artifact.kind}-${label}-${index}`;
          const url = artifact.artifact_url ? resolveGatewayUrl(artifact.artifact_url) : null;
          if (url && isImageArtifact(artifact, label)) {
            return (
              <button
                key={key}
                type="button"
                className="group relative inline-flex size-16 shrink-0 overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)] shadow-[0_10px_22px_rgba(0,0,0,0.06)] transition hover:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--ring)]"
                aria-label={`Preview ${label}`}
                onClick={() => setPreviewArtifact(artifact)}
              >
                <img src={url} alt={label} className="h-full w-full object-cover transition group-hover:scale-[1.02]" />
                <span className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center gap-1 bg-gradient-to-t from-black/55 to-transparent px-1.5 pb-1 pt-3 text-[10px] font-medium text-white opacity-0 transition group-hover:opacity-100">
                  <ImageIcon className="size-3 shrink-0" />
                  <span className="truncate">{label}</span>
                </span>
              </button>
            );
          }
          if (url) {
            return (
              <a
                key={key}
                href={url}
                download={label}
                onClick={(event) => {
                  event.preventDefault();
                  void downloadArtifact(url, label);
                }}
                className="inline-flex h-16 min-w-0 max-w-[24rem] items-center gap-3 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-3 text-left text-[var(--ink)] shadow-[0_10px_22px_rgba(0,0,0,0.06)] transition hover:border-[var(--accent)] hover:bg-[var(--panel-strong)]"
              >
                <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[var(--primary)] text-white">
                  <FileTextIcon className="size-5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-semibold leading-5">{label}</span>
                  <span className="mt-0.5 flex items-center gap-1 text-[12px] text-[var(--muted)]">
                    <DownloadIcon className="size-3.5 shrink-0" />
                    <span>{artifact.kind || "file"}</span>
                  </span>
                </span>
              </a>
            );
          }
          return (
            <Button
              key={key}
              size="sm"
              variant="secondary"
              onClick={() => onSelectArtifact?.(artifact)}
              className="h-10 max-w-full justify-start gap-2 px-3"
            >
              <FileTextIcon className="size-4 shrink-0" />
              <span className="min-w-0 truncate">{label}</span>
            </Button>
          );
        })}
      </div>
      <ArtifactImageDialog artifact={previewArtifact} onClose={() => setPreviewArtifact(null)} />
    </>
  );
}

function ArtifactImageDialog({
  artifact,
  onClose,
}: {
  artifact: ArtifactRefView | null;
  onClose(): void;
}) {
  const label = artifact?.label || artifact?.virtual_path || "image";
  const url = artifact?.artifact_url ? resolveGatewayUrl(artifact.artifact_url) : "";
  return (
    <Dialog open={Boolean(artifact)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[min(92vw,72rem)] rounded-3xl border-0 bg-transparent p-0 shadow-none">
        <DialogTitle className="sr-only">{label}</DialogTitle>
        <DialogDescription className="sr-only">Image preview</DialogDescription>
        {artifact ? (
          <img
            src={url}
            alt={label}
            className="max-h-[86dvh] max-w-full rounded-3xl object-contain shadow-[0_28px_80px_rgba(0,0,0,0.28)]"
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function isImageArtifact(artifact: ArtifactRefView, fallbackLabel: string) {
  return [artifact.extension, fallbackLabel, artifact.virtual_path, artifact.artifact_url].some((value) =>
    IMAGE_EXTENSIONS.has(normalizedExtension(value || "")),
  );
}

function normalizedExtension(value: string) {
  const clean = value.split(/[?#]/)[0]?.trim() ?? "";
  const match = /\.([a-zA-Z0-9]+)$/.exec(clean);
  return (match?.[1] || clean.replace(/^\./, "")).toLowerCase();
}

async function downloadArtifact(url: string, label: string) {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Download failed: ${response.status}`);
    }
    const objectUrl = URL.createObjectURL(await response.blob());
    triggerDownload(objectUrl, label);
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
  } catch {
    triggerDownload(url, label);
  }
}

function triggerDownload(url: string, label: string) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = label;
  anchor.rel = "noreferrer";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp", "gif", "bmp", "svg"]);
