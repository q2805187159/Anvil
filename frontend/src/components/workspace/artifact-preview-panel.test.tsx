import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/src/components/ui/tooltip";
import { ArtifactPreviewPanel } from "./artifact-preview-panel";

function renderArtifactPreview(element: React.ReactElement) {
  return render(<TooltipProvider>{element}</TooltipProvider>);
}

describe("ArtifactPreviewPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders presentation annotation manifests as structured review artifacts", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "warning",
          mode: "diff",
          summary: {
            annotation_count: 2,
            warning_count: 2,
            palette: ["#3A506B", "#5BC0BE"],
          },
          recommendations: ["Fix changed viewBox before export"],
          annotations: [
            {
              page_name: "01_cover.svg",
              severity: "warning",
              code: "viewbox_changed",
              message: "Candidate viewBox differs from baseline.",
            },
            {
              page_name: "01_cover.svg",
              severity: "warning",
              code: "text_density_regressed",
              message: "Candidate text density increased.",
            },
          ],
          pages: [
            {
              index: 1,
              name: "01_cover.svg",
              path: "01_cover.svg",
              issues: [
                {
                  severity: "warning",
                  code: "viewbox_changed",
                  message: "Candidate viewBox differs from baseline.",
                },
              ],
            },
          ],
        }),
      ),
    );

    renderArtifactPreview(
      <ArtifactPreviewPanel
        label="manifest.json"
        artifactUrl="http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/presentation-annotations/launch/manifest.json"
      />,
    );

    expect(await screen.findByText("Presentation annotation")).toBeInTheDocument();
    expect(screen.getAllByText("Annotations").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/viewbox_changed/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Fix changed viewBox before export").length).toBeGreaterThan(0);

    await waitFor(() => {
      const iframe = screen.getByTitle("Presentation artifact report");
      expect(iframe).toHaveAttribute(
        "src",
        "http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/presentation-annotations/launch/index.html",
      );
    });
  });

  it("keeps non-presentation json artifacts as formatted json", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ ok: true, value: 3 })));

    renderArtifactPreview(<ArtifactPreviewPanel label="result.json" artifactUrl="http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/result.json" />);

    expect(await screen.findByText(/"ok": true/i)).toBeInTheDocument();
    expect(screen.queryByText(/Presentation/i)).not.toBeInTheDocument();
  });

  it("renders presentation browser diff manifests with overlay evidence", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          status: "changed",
          baseline_report_url: "http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/presentation-review-reports/base/index.html",
          candidate_report_url: "http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/presentation-review-reports/candidate/index.html",
          baseline_screenshot_artifact_url: "http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/presentation-browser-diffs/run-a/baseline.png",
          candidate_screenshot_artifact_url: "http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/presentation-browser-diffs/run-a/candidate.png",
          overlay_artifact_url: "http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/presentation-browser-diffs/run-a/overlay.png",
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
        }),
      ),
    );

    renderArtifactPreview(
      <ArtifactPreviewPanel
        label="manifest.json"
        artifactUrl="http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/presentation-browser-diffs/run-a/manifest.json"
      />,
    );

    expect(await screen.findByText("Presentation browser evidence")).toBeInTheDocument();
    expect(screen.getAllByText("Browser diff").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Overlay diff").length).toBeGreaterThan(0);
    expect(screen.getByText("Changed cells")).toBeInTheDocument();
    expect(screen.getAllByText(/Cell 3, 2/).length).toBeGreaterThan(0);
    expect(screen.getByAltText("Overlay diff")).toHaveAttribute(
      "src",
      "http://127.0.0.1:18000/threads/thread-a/artifacts/outputs/presentation-browser-diffs/run-a/overlay.png",
    );
  });
});
