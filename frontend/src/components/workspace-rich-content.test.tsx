import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkspaceRichContent } from "./workspace/workspace-rich-content";

describe("WorkspaceRichContent", () => {
  it("renders markdown, highlighted code affordances, math fallback text, and mermaid placeholder", () => {
    render(
      <WorkspaceRichContent
        content={
          "# Heading\n\nParagraph with **bold** text.\n\n```ts\nconst value = 1;\n```\n\n$$E=mc^2$$\n\n```mermaid\ngraph TD;\nA-->B;\n```"
        }
      />,
    );

    expect(screen.getByText("Heading")).toBeInTheDocument();
    expect(screen.getByText(/Paragraph with/i)).toBeInTheDocument();
    expect(screen.getByText(/ts/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy code/i })).toBeInTheDocument();
    expect(screen.getByText(/E=mc\^2/i)).toBeTruthy();
    expect(screen.getByText(/mermaid diagram/i)).toBeInTheDocument();
  });

  it("keeps plain URLs as inline links instead of expanding preview cards", () => {
    render(<WorkspaceRichContent content="Open https://example.com/docs for details." />);

    const link = screen.getByRole("link", { name: "https://example.com/docs" });
    expect(link).toHaveAttribute("href", "https://example.com/docs");
    expect(screen.queryByText(/link preview/i)).not.toBeInTheDocument();
  });
});
