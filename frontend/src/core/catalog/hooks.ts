"use client";

import { useQuery } from "@tanstack/react-query";

import { getToolCatalogEntry, listToolCatalog, type ToolCatalogFilters } from "./api";

export function useToolCatalog(filters: ToolCatalogFilters, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["tool-catalog", filters],
    queryFn: () => listToolCatalog(filters),
    enabled: options.enabled ?? true,
    staleTime: 30000,
  });
}

export function useCatalogTools(filters: Omit<ToolCatalogFilters, "path">, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["catalog-tools", filters],
    queryFn: () => listToolCatalog({ ...filters, path: "catalog" }),
    enabled: options.enabled ?? true,
    staleTime: 30000,
  });
}

export function useToolCatalogEntry(
  nameOrCapabilityId: string | null,
  path: "tools" | "catalog" = "tools",
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: ["tool-catalog-entry", path, nameOrCapabilityId],
    queryFn: () => getToolCatalogEntry(nameOrCapabilityId!, path),
    enabled: Boolean(nameOrCapabilityId) && (options.enabled ?? true),
    staleTime: 60000,
  });
}
