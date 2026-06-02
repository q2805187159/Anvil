"use client";

import React from "react";

import { useLinkPreview } from "@/src/core/link-previews/hooks";

export function LinkPreviewCard({ url }: { url: string }) {
  const preview = useLinkPreview(url);

  if (!/^https?:\/\//i.test(url)) {
    return null;
  }

  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="my-3 block rounded-[1rem] border border-[var(--line)] bg-[var(--panel)] p-4 shadow-[var(--shadow-card)]"
    >
      <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
        {preview.data?.hostname ?? url}
      </div>
      <div className="mt-2 font-medium text-[var(--ink)]">
        {preview.data?.title ?? url}
      </div>
      <div className="mt-2 text-sm text-[var(--muted)]">
        {preview.data?.description ?? "Link preview unavailable."}
      </div>
      {preview.data ? (
        <div className="mt-2 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
          {preview.data.preview_status}
        </div>
      ) : null}
    </a>
  );
}
