import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { usePluginCatalog, usePluginRegistries, usePlugins } from "./hooks";

function wrapper({ children }: Readonly<{ children: React.ReactNode }>) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("plugin hooks", () => {
  it("does not call plugin list surfaces when their query gates are closed", async () => {
    const installed = vi.spyOn(api, "listPlugins").mockResolvedValue([]);
    const catalog = vi.spyOn(api, "listPluginCatalog").mockResolvedValue([]);
    const registries = vi.spyOn(api, "listPluginRegistries").mockResolvedValue([]);

    renderHook(
      () => {
        usePlugins({ enabled: false });
        usePluginCatalog({ enabled: false });
        usePluginRegistries({ enabled: false });
      },
      { wrapper },
    );

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(installed).not.toHaveBeenCalled();
    expect(catalog).not.toHaveBeenCalled();
    expect(registries).not.toHaveBeenCalled();
  });

  it("calls only the visible plugin surface", async () => {
    const installed = vi.spyOn(api, "listPlugins").mockResolvedValue([]);
    const catalog = vi.spyOn(api, "listPluginCatalog").mockResolvedValue([]);
    const registries = vi.spyOn(api, "listPluginRegistries").mockResolvedValue([]);

    renderHook(
      () => {
        usePlugins({ enabled: false });
        usePluginCatalog({ enabled: true });
        usePluginRegistries({ enabled: false });
      },
      { wrapper },
    );

    await waitFor(() => expect(catalog).toHaveBeenCalledTimes(1));
    expect(installed).not.toHaveBeenCalled();
    expect(registries).not.toHaveBeenCalled();
  });
});
