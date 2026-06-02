import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PluginInspectorPanel } from "./plugin-inspector-panel";
import { opsCopy } from "./types";

const hookCalls = {
  installed: [] as Array<{ enabled?: boolean }>,
  catalog: [] as Array<{ enabled?: boolean }>,
  registries: [] as Array<{ enabled?: boolean }>,
};

vi.mock("@/src/core/plugins/hooks", () => ({
  usePlugins: (options?: { enabled?: boolean }) => {
    hookCalls.installed.push({ enabled: options?.enabled });
    return {
      isLoading: false,
      data: options?.enabled
        ? [
            {
              plugin_id: "installed-plugin",
              enabled: true,
              source_path: "/plugins/installed",
              skill_roots: [],
              tool_count: 1,
              tool_names: ["tool"],
              resources: [],
              prompts: [],
              memory_providers: [],
              catalog_metadata: {},
              discovery_source: "local",
            },
          ]
        : undefined,
    };
  },
  usePluginCatalog: (options?: { enabled?: boolean }) => {
    hookCalls.catalog.push({ enabled: options?.enabled });
    return {
      isLoading: false,
      data: options?.enabled
        ? [
            {
              plugin_id: "catalog-plugin",
              name: "Catalog Plugin",
              description: "Catalog entry",
              source: "registry://catalog-plugin",
              tags: [],
              installed: false,
              enabled: false,
              installable: true,
              skill_roots: [],
              tool_names: ["catalog_tool"],
              mcp_servers: [],
              memory_providers: [],
              permissions: [],
              catalog_metadata: {},
            },
          ]
        : undefined,
    };
  },
  usePluginRegistries: (options?: { enabled?: boolean }) => {
    hookCalls.registries.push({ enabled: options?.enabled });
    return {
      isLoading: false,
      data: options?.enabled
        ? [
            {
              registry_id: "local",
              name: "Local Registry",
              source: "/plugins/catalog.json",
              source_kind: "file",
              enabled: true,
              readonly: false,
              entry_count: 1,
              diagnostics: [],
            },
          ]
        : undefined,
    };
  },
}));

describe("PluginInspectorPanel", () => {
  beforeEach(() => {
    hookCalls.installed = [];
    hookCalls.catalog = [];
    hookCalls.registries = [];
  });

  it("queries only the visible plugin mode", () => {
    render(
      <PluginInspectorPanel
        copy={opsCopy("en-US")}
        selectedItem={null}
        onSelectItem={vi.fn()}
        onAction={vi.fn()}
        onInstallCatalog={vi.fn()}
      />,
    );

    expect(last(hookCalls.catalog)?.enabled).toBe(true);
    expect(last(hookCalls.installed)?.enabled).toBe(false);
    expect(last(hookCalls.registries)?.enabled).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Installed" }));

    expect(last(hookCalls.catalog)?.enabled).toBe(false);
    expect(last(hookCalls.installed)?.enabled).toBe(true);
    expect(last(hookCalls.registries)?.enabled).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Sources" }));

    expect(last(hookCalls.catalog)?.enabled).toBe(false);
    expect(last(hookCalls.installed)?.enabled).toBe(false);
    expect(last(hookCalls.registries)?.enabled).toBe(true);
  });
});

function last<T>(items: T[]): T | undefined {
  return items.at(-1);
}
