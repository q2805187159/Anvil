import type { ToolCatalogEntryView } from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export type ToolCatalogFilters = {
  query?: string;
  sourceKind?: string | null;
  capabilityGroup?: string | null;
  path?: "tools" | "catalog";
};

function buildCatalogQuery(filters: ToolCatalogFilters = {}) {
  const params = new URLSearchParams();
  if (filters.query?.trim()) {
    params.set("query", filters.query.trim());
  }
  if (filters.sourceKind?.trim()) {
    params.set("source_kind", filters.sourceKind.trim());
  }
  if (filters.capabilityGroup?.trim()) {
    params.set("capability_group", filters.capabilityGroup.trim());
  }
  const suffix = params.toString();
  return suffix ? `?${suffix}` : "";
}

export function listToolCatalog(filters: ToolCatalogFilters = {}) {
  const path = filters.path === "catalog" ? "/catalog/tools" : "/tools/catalog";
  return apiRequest<ToolCatalogEntryView[]>(`${path}${buildCatalogQuery(filters)}`);
}

export function getToolCatalogEntry(nameOrCapabilityId: string, path: "tools" | "catalog" = "tools") {
  const prefix = path === "catalog" ? "/catalog/tools" : "/tools";
  return apiRequest<ToolCatalogEntryView>(`${prefix}/${encodeURIComponent(nameOrCapabilityId)}`);
}
