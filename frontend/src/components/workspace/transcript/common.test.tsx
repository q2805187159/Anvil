import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ArtifactRefView } from "@/src/core/contracts";
import { TooltipProvider } from "@/src/components/ui/tooltip";

import { ArtifactRefList } from "./common";

beforeEach(() => {
  vi.restoreAllMocks();
});

function artifact(patch: Partial<ArtifactRefView>): ArtifactRefView {
  return {
    kind: "upload",
    label: "attachment",
    artifact_url: null,
    virtual_path: null,
    source_scope: null,
    internal: false,
    extension: null,
    markdown_file: null,
    markdown_virtual_path: null,
    markdown_artifact_url: null,
    companions: [],
    extraction: null,
    outline: [],
    outline_preview: [],
    converter_used: null,
    ocr_used: false,
    conversion_error: null,
    ...patch,
  };
}

function renderList(artifacts: ArtifactRefView[]) {
  return render(
    <TooltipProvider>
      <ArtifactRefList artifactRefs={artifacts} />
    </TooltipProvider>,
  );
}

describe("ArtifactRefList", () => {
  it("does not render internal artifact references", () => {
    renderList([
      artifact({
        label: "visible.txt",
        artifact_url: "/threads/thread-a/artifacts/uploads/visible.txt",
      }),
      artifact({
        label: "internal-view-image.png",
        artifact_url: "/threads/thread-a/artifacts/tool-results/internal-view-image.png",
        extension: "png",
        internal: true,
      }),
    ]);

    expect(screen.getByRole("link", { name: /visible\.txt/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /internal-view-image\.png/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/internal-view-image\.png/i)).not.toBeInTheDocument();
  });

  it("previews image artifacts in-page instead of opening a new tab", () => {
    renderList([
      artifact({
        label: "diagram.png",
        artifact_url: "/threads/thread-a/artifacts/uploads/diagram.png",
        extension: "png",
      }),
    ]);

    fireEvent.click(screen.getByRole("button", { name: /preview diagram\.png/i }));

    const previewImage = screen.getByRole("img", { name: "diagram.png" });
    expect(previewImage).toHaveAttribute(
      "src",
      "http://127.0.0.1:18000/threads/thread-a/artifacts/uploads/diagram.png",
    );
    expect(screen.queryByRole("link", { name: /diagram\.png/i })).not.toBeInTheDocument();
  });

  it("renders non-image artifacts as direct downloads", async () => {
    const preventDefault = vi.fn();
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderList([
      artifact({
        label: "brief.pdf",
        artifact_url: "/threads/thread-a/artifacts/uploads/brief.pdf",
        extension: "pdf",
      }),
    ]);

    const link = screen.getByRole("link", { name: /brief\.pdf/i });
    expect(link).toHaveAttribute("download", "brief.pdf");
    expect(link).not.toHaveAttribute("target");
    fireEvent.click(link, { preventDefault });
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
  });
});
