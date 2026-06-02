import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ToolActivityView } from "@/src/core/contracts";
import { I18nProvider } from "@/src/core/i18n";

import { ToolBlock } from "./tool-block";

function wrap(ui: React.ReactNode) {
  return render(<I18nProvider>{ui}</I18nProvider>);
}

describe("ToolBlock", () => {
  it("renders write_file path and content preview", () => {
    const tool: ToolActivityView = {
      tool_call_id: "tool-1",
      message_id: "message-1",
      name: "write_file",
      display_name: "Write File",
      source_kind: "builtin",
      source_id: "core",
      capability_group: "filesystem",
      tool_execution_mode: "sync",
      args: {
        path: "/mnt/user-data/workspace/report.md",
        content: "Hello world from writer",
      },
      status: "completed",
      result_text: "WROTE:/mnt/user-data/workspace/report.md",
      started_at: null,
      completed_at: null,
      duration_ms: 12,
    };

    wrap(<ToolBlock tool={tool} />);

    fireEvent.click(screen.getByRole("button", { name: /Write File/i }));

    expect(screen.getByText("/mnt/user-data/workspace/report.md")).toBeInTheDocument();
    expect(screen.getAllByText(/Hello world from writer/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/WROTE:\/mnt\/user-data\/workspace\/report.md/i)).toBeInTheDocument();
  });

  it("renders list_dir results as chips", () => {
    const tool: ToolActivityView = {
      tool_call_id: "tool-2",
      message_id: "message-2",
      name: "list_dir",
      display_name: "List Directory",
      source_kind: "builtin",
      source_id: "core",
      capability_group: "filesystem",
      tool_execution_mode: "sync",
      args: {
        path: "/mnt/user-data/workspace",
      },
      status: "completed",
      result_text: '["a.txt","b.txt","notes"]',
      started_at: null,
      completed_at: null,
      duration_ms: 8,
    };

    wrap(<ToolBlock tool={tool} />);

    fireEvent.click(screen.getByRole("button", { name: /List Directory/i }));

    expect(screen.getByText("a.txt")).toBeInTheDocument();
    expect(screen.getByText("b.txt")).toBeInTheDocument();
    expect(screen.getByText("notes")).toBeInTheDocument();
  });
});
