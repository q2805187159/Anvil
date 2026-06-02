"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { McpPromptRenderRequest } from "@/src/core/contracts";
import { reloadSystem } from "@/src/core/system/api";

import {
  deleteMcpServer,
  getMcpConfigOverview,
  getMcpServerProvenance,
  getMcpServerTools,
  listMcpPrompts,
  listMcpResources,
  listMcpServers,
  readMcpResource,
  reconnectMcpServer,
  refreshMcpServer,
  renderMcpPrompt,
  upsertMcpServers,
} from "./api";

type QueryGateOptions = {
  enabled?: boolean;
};

const MCP_LIST_STALE_TIME_MS = 30_000;
const MCP_DETAIL_STALE_TIME_MS = 60_000;

function queryEnabled(options?: QueryGateOptions) {
  return options?.enabled ?? true;
}

async function invalidateMcpQueries(queryClient: ReturnType<typeof useQueryClient>) {
  await queryClient.invalidateQueries({ queryKey: ["extensions"] });
  await queryClient.invalidateQueries({ queryKey: ["mcp-config"] });
  await queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
  await queryClient.invalidateQueries({ queryKey: ["mcp-server-tools"] });
  await queryClient.invalidateQueries({ queryKey: ["mcp-server-provenance"] });
  await queryClient.invalidateQueries({ queryKey: ["mcp-resources"] });
  await queryClient.invalidateQueries({ queryKey: ["mcp-prompts"] });
  await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
  await queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
  await queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
}

export function useMcpServers(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["mcp-servers"],
    queryFn: listMcpServers,
    enabled: queryEnabled(options),
    staleTime: MCP_LIST_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useMcpConfigOverview(options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["mcp-config"],
    queryFn: getMcpConfigOverview,
    enabled: queryEnabled(options),
    staleTime: MCP_LIST_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useReloadMcpQueries() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => reloadSystem("mcp"),
    onSuccess: async () => {
      await invalidateMcpQueries(queryClient);
      await queryClient.invalidateQueries({ queryKey: ["plugins"] });
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function useMcpServerTools(serverId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["mcp-server-tools", serverId],
    queryFn: () => getMcpServerTools(serverId!),
    enabled: Boolean(serverId) && queryEnabled(options),
    staleTime: MCP_DETAIL_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useMcpServerProvenance(serverId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["mcp-server-provenance", serverId],
    queryFn: () => getMcpServerProvenance(serverId!),
    enabled: Boolean(serverId) && queryEnabled(options),
    staleTime: MCP_DETAIL_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useMcpResources(serverId?: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["mcp-resources", serverId ?? null],
    queryFn: () => listMcpResources(serverId),
    enabled: queryEnabled(options),
    staleTime: MCP_DETAIL_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useMcpResource(serverId: string | null, resourceId: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["mcp-resource", serverId, resourceId],
    queryFn: () => readMcpResource(serverId!, resourceId!),
    enabled: Boolean(serverId && resourceId) && queryEnabled(options),
    staleTime: MCP_DETAIL_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useMcpPrompts(serverId?: string | null, options: QueryGateOptions = {}) {
  return useQuery({
    queryKey: ["mcp-prompts", serverId ?? null],
    queryFn: () => listMcpPrompts(serverId),
    enabled: queryEnabled(options),
    staleTime: MCP_DETAIL_STALE_TIME_MS,
    refetchOnWindowFocus: false,
  });
}

export function useRenderMcpPrompt(serverId: string | null, promptId: string | null) {
  return useMutation({
    mutationFn: (body: McpPromptRenderRequest) => renderMcpPrompt(serverId!, promptId!, body),
  });
}

export function useRefreshMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (serverId: string) => refreshMcpServer(serverId),
    onSuccess: async () => {
      await invalidateMcpQueries(queryClient);
    },
  });
}

export function useReconnectMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (serverId: string) => reconnectMcpServer(serverId),
    onSuccess: async () => {
      await invalidateMcpQueries(queryClient);
    },
  });
}

export function useUpsertMcpServers() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: upsertMcpServers,
    onSuccess: async () => {
      await invalidateMcpQueries(queryClient);
      await queryClient.invalidateQueries({ queryKey: ["plugins"] });
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}

export function useDeleteMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteMcpServer,
    onSuccess: async () => {
      await invalidateMcpQueries(queryClient);
      await queryClient.invalidateQueries({ queryKey: ["plugins"] });
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });
}
