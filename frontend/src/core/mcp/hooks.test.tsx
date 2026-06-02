import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import * as api from "./api";
import {
  useMcpConfigOverview,
  useMcpPrompts,
  useMcpResources,
  useMcpServerProvenance,
  useMcpServers,
  useMcpServerTools,
} from "./hooks";

function wrapper({ children }: Readonly<{ children: React.ReactNode }>) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("mcp hooks", () => {
  it("does not call gateway MCP list/detail APIs when the query gate is closed", async () => {
    const listServers = vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    const overview = vi.spyOn(api, "getMcpConfigOverview").mockResolvedValue({
      server_count: 0,
      ready_count: 0,
      auth_required_count: 0,
      hidden_from_model_count: 0,
      config_path: "",
    });
    const tools = vi.spyOn(api, "getMcpServerTools").mockResolvedValue({
      server_id: "github",
      status: "ready",
      tool_names: [],
    });
    const resources = vi.spyOn(api, "listMcpResources").mockResolvedValue([]);
    const prompts = vi.spyOn(api, "listMcpPrompts").mockResolvedValue([]);
    const provenance = vi.spyOn(api, "getMcpServerProvenance").mockResolvedValue({
      server_id: "github",
      provenance: "project",
      transport_kind: "stdio",
      tool_allowlist: [],
      tool_denylist: [],
      oauth: {},
      env_resolution: {},
      header_templates: {},
      resource_policy: {},
      prompt_policy: {},
      reconnect_policy: {},
      healthcheck: {},
      connection_config: {},
    });

    renderHook(
      () => {
        useMcpServers({ enabled: false });
        useMcpConfigOverview({ enabled: false });
        useMcpServerTools("github", { enabled: false });
        useMcpResources("github", { enabled: false });
        useMcpPrompts("github", { enabled: false });
        useMcpServerProvenance("github", { enabled: false });
      },
      { wrapper },
    );

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(listServers).not.toHaveBeenCalled();
    expect(overview).not.toHaveBeenCalled();
    expect(tools).not.toHaveBeenCalled();
    expect(resources).not.toHaveBeenCalled();
    expect(prompts).not.toHaveBeenCalled();
    expect(provenance).not.toHaveBeenCalled();
  });

  it("calls only enabled MCP queries", async () => {
    const listServers = vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    const tools = vi.spyOn(api, "getMcpServerTools").mockResolvedValue({
      server_id: "github",
      status: "ready",
      tool_names: ["repo_read"],
    });
    const prompts = vi.spyOn(api, "listMcpPrompts").mockResolvedValue([]);

    renderHook(
      () => {
        useMcpServers();
        useMcpServerTools("github", { enabled: true });
        useMcpPrompts("github", { enabled: false });
      },
      { wrapper },
    );

    await waitFor(() => expect(listServers).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(tools).toHaveBeenCalledWith("github"));
    expect(prompts).not.toHaveBeenCalled();
  });
});
