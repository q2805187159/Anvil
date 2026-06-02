import type {
  PluginCatalogEntryView,
  PluginInstallRequest,
  PluginInstallView,
  PluginRegistryDeleteView,
  PluginRegistryUpsertRequest,
  PluginRegistryUpsertView,
  PluginRegistryView,
  PluginView,
} from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function listPlugins() {
  return apiRequest<PluginView[]>("/plugins");
}

export function listPluginCatalog() {
  return apiRequest<PluginCatalogEntryView[]>("/plugins/catalog");
}

export function listPluginRegistries() {
  return apiRequest<PluginRegistryView[]>("/plugins/registries");
}

export function upsertPluginRegistry(body: PluginRegistryUpsertRequest) {
  return apiRequest<PluginRegistryUpsertView>("/plugins/registries", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function refreshPluginRegistry(registryId: string) {
  return apiRequest<PluginRegistryUpsertView>(`/plugins/registries/${encodeURIComponent(registryId)}/refresh`, {
    method: "POST",
  });
}

export function deletePluginRegistry(registryId: string) {
  return apiRequest<PluginRegistryDeleteView>(`/plugins/registries/${encodeURIComponent(registryId)}`, {
    method: "DELETE",
  });
}

export function installPlugin(body: PluginInstallRequest) {
  return apiRequest<PluginInstallView>("/plugins/install", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
