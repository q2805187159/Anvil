"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deletePluginRegistry,
  installPlugin,
  listPluginCatalog,
  listPluginRegistries,
  listPlugins,
  refreshPluginRegistry,
  upsertPluginRegistry,
} from "./api";

type QueryGateOptions = {
  enabled?: boolean;
};

const PLUGIN_LIST_STALE_TIME_MS = 30_000;
const PLUGIN_CATALOG_STALE_TIME_MS = 60_000;

function queryEnabled(options?: QueryGateOptions) {
  return options?.enabled ?? true;
}

export function usePlugins(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["plugins"],
    queryFn: listPlugins,
    enabled: queryEnabled(options),
    staleTime: PLUGIN_LIST_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function usePluginCatalog(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["plugins", "catalog"],
    queryFn: listPluginCatalog,
    enabled: queryEnabled(options),
    staleTime: PLUGIN_CATALOG_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function usePluginRegistries(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["plugins", "registries"],
    queryFn: listPluginRegistries,
    enabled: queryEnabled(options),
    staleTime: PLUGIN_LIST_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

function invalidatePluginSurfaces(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ["plugins"] }),
    queryClient.invalidateQueries({ queryKey: ["plugins", "catalog"] }),
    queryClient.invalidateQueries({ queryKey: ["plugins", "registries"] }),
    queryClient.invalidateQueries({ queryKey: ["config-overview"] }),
    queryClient.invalidateQueries({ queryKey: ["skills"] }),
    queryClient.invalidateQueries({ queryKey: ["mcp-servers"] }),
    queryClient.invalidateQueries({ queryKey: ["catalog-tools"] }),
    queryClient.invalidateQueries({ queryKey: ["tool-catalog"] }),
    queryClient.invalidateQueries({ queryKey: ["threads"] }),
  ]);
}

export function useUpsertPluginRegistry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: upsertPluginRegistry,
    onSuccess: async () => {
      await invalidatePluginSurfaces(queryClient);
    },
  });
}

export function useRefreshPluginRegistry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: refreshPluginRegistry,
    onSuccess: async () => {
      await invalidatePluginSurfaces(queryClient);
    },
  });
}

export function useDeletePluginRegistry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deletePluginRegistry,
    onSuccess: async () => {
      await invalidatePluginSurfaces(queryClient);
    },
  });
}

export function useInstallPlugin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: installPlugin,
    onSuccess: async () => {
      await invalidatePluginSurfaces(queryClient);
    },
  });
}
