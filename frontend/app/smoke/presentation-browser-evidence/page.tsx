import React from "react";

import { ArtifactPreviewPanel } from "@/src/components/workspace/artifact-preview-panel";

const RED_PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==";
const BLUE_PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPj/HwADAgH/5ncLrgAAAABJRU5ErkJggg==";

const browserDiffManifest = {
  success: true,
  status: "changed",
  baseline_report_url: "https://example.test/presentation/base/index.html",
  candidate_report_url: "https://example.test/presentation/candidate/index.html",
  baseline_screenshot_artifact_url: RED_PIXEL,
  candidate_screenshot_artifact_url: BLUE_PIXEL,
  overlay_artifact_url: BLUE_PIXEL,
  comparison: {
    bytes_changed: true,
    byte_delta: 12,
    snapshot_changed: true,
    pixels_changed: true,
    pixel_delta: {
      available: true,
      changed: true,
      width: 160,
      height: 90,
      changed_pixels: 144,
      total_pixels: 14400,
      changed_ratio: 0.01,
      mean_intensity: 128.5,
      top_cells: [
        {
          cell: [3, 2],
          bounds: { x: 30, y: 18, width: 10, height: 9 },
          changed_pixels: 90,
          changed_ratio: 1,
          mean_intensity: 140,
        },
      ],
    },
  },
};

const manifestUrl = `data:application/json,${encodeURIComponent(JSON.stringify(browserDiffManifest))}`;

export default function PresentationBrowserEvidenceSmokePage() {
  return (
    <main className="min-h-screen bg-[var(--bg)] p-8 text-[var(--ink)]">
      <section className="mx-auto max-w-3xl space-y-4" data-smoke-id="presentation-browser-evidence">
        <div>
          <h1 className="text-lg font-semibold">Presentation Browser Evidence Smoke</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Browser-rendered artifact preview for presentation evidence manifests.
          </p>
        </div>
        <ArtifactPreviewPanel label="manifest.json" artifactUrl={manifestUrl} />
      </section>
    </main>
  );
}
