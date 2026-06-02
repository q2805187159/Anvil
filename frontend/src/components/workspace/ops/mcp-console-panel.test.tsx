import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { McpConsolePanel } from "./mcp-console-panel";
import { opsCopy } from "./types";

const hookCalls = {
  tools: [] as Array<{ serverId: string | null; enabled?: boolean }>,
  resources: [] as Array<{ serverId?: string | null; enabled?: boolean }>,
  prompts: [] as Array<{ serverId?: string | null; enabled?: boolean }>,
  provenance: [] as Array<{ serverId: string | null; enabled?: boolean }>,
  resource: [] as Array<{ serverId: string | null; resourceId: string | null; enabled?: boolean }>,
};

vi.mock("@/src/core/mcp/hooks", () => ({
  useMcpConfigOverview: () => ({
    data: {
      config_path: "/workspace/.anvil/mcp.json",
      server_count: 1,
      ready_count: 1,
      auth_required_count: 0,
      hidden_from_model_count: 0,
    },
  }),
  useMcpServers: () => ({
    data: [
      {
        server_id: "github",
        source_kind: "project",
        status: "ready",
        description: "GitHub MCP",
        error: null,
        tool_names: ["repo_read"],
        transport_kind: "stdio",
        enabled: true,
        ready: true,
        auth_required: false,
        tool_count: 1,
        resource_count: 1,
        prompt_count: 1,
        diagnostics: [],
        metadata: {},
        config_source: "project",
      },
    ],
  }),
  useReloadMcpQueries: () => ({ isPending: false, mutate: vi.fn() }),
  useMcpServerTools: (serverId: string | null, options?: { enabled?: boolean }) => {
    hookCalls.tools.push({ serverId, enabled: options?.enabled });
    return { data: options?.enabled ? { server_id: serverId ?? "", status: "ready", tool_names: ["repo_read"] } : undefined };
  },
  useMcpResources: (serverId?: string | null, options?: { enabled?: boolean }) => {
    hookCalls.resources.push({ serverId, enabled: options?.enabled });
    return {
      data: options?.enabled
        ? [
            {
              resource_id: "repo",
              title: "Repository",
              description: "Repository metadata",
              server_id: serverId,
              metadata: {},
              discovery_source: "server",
              supports_read: true,
            },
          ]
        : undefined,
    };
  },
  useMcpPrompts: (serverId?: string | null, options?: { enabled?: boolean }) => {
    hookCalls.prompts.push({ serverId, enabled: options?.enabled });
    return {
      data: options?.enabled
        ? [
            {
              prompt_id: "summary",
              title: "Summary",
              description: "Summarize repo",
              server_id: serverId,
              arguments: ["repo"],
              metadata: {},
              input_schema: {},
              rendered: "",
            },
          ]
        : undefined,
    };
  },
  useMcpServerProvenance: (serverId: string | null, options?: { enabled?: boolean }) => {
    hookCalls.provenance.push({ serverId, enabled: options?.enabled });
    return { data: options?.enabled ? { server_id: serverId ?? "", provenance: "project", transport_kind: "stdio" } : undefined };
  },
  useMcpResource: (serverId: string | null, resourceId: string | null, options?: { enabled?: boolean }) => {
    hookCalls.resource.push({ serverId, resourceId, enabled: options?.enabled });
    return { data: options?.enabled ? { resource_id: resourceId ?? "", title: "Repository", metadata: {}, content: "{}" } : undefined };
  },
}));

describe("McpConsolePanel", () => {
  beforeEach(() => {
    hookCalls.tools = [];
    hookCalls.resources = [];
    hookCalls.prompts = [];
    hookCalls.provenance = [];
    hookCalls.resource = [];
  });

  it("queries only the active MCP detail tab", () => {
    render(
      <McpConsolePanel
        copy={opsCopy("en-US")}
        selectedServer="github"
        selectedItem={null}
        onSelectServer={vi.fn()}
        onSelectItem={vi.fn()}
        onAction={vi.fn()}
      />,
    );

    expect(last(hookCalls.tools)?.enabled).toBe(true);
    expect(last(hookCalls.resources)?.enabled).toBe(false);
    expect(last(hookCalls.prompts)?.enabled).toBe(false);
    expect(last(hookCalls.provenance)?.enabled).toBe(false);

    fireEvent.click(screen.getByRole("tab", { name: "Resources" }));

    expect(last(hookCalls.tools)?.enabled).toBe(false);
    expect(last(hookCalls.resources)?.enabled).toBe(true);
    expect(last(hookCalls.prompts)?.enabled).toBe(false);
    expect(last(hookCalls.provenance)?.enabled).toBe(false);
  });

  it("temporarily loads resources and prompts to classify a deep-linked MCP item", () => {
    render(
      <McpConsolePanel
        copy={opsCopy("en-US")}
        selectedServer="github"
        selectedItem="repo"
        onSelectServer={vi.fn()}
        onSelectItem={vi.fn()}
        onAction={vi.fn()}
      />,
    );

    expect(hookCalls.resources.some((call) => call.enabled)).toBe(true);
    expect(hookCalls.prompts.some((call) => call.enabled)).toBe(true);
    expect(last(hookCalls.tools)?.enabled).toBe(false);
    expect(last(hookCalls.resources)?.enabled).toBe(true);
    expect(last(hookCalls.prompts)?.enabled).toBe(false);
    expect(last(hookCalls.provenance)?.enabled).toBe(false);
  });
});

function last<T>(items: T[]): T | undefined {
  return items.at(-1);
}
