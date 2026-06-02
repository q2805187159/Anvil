import type {
  CapabilityPromptView,
  CapabilityResourceView,
  ExtensionStatusView,
  McpConfigOverviewView,
  McpPromptRenderRequest,
  McpPromptRenderView,
  McpResourceContentView,
  McpServerBatchUpsertRequest,
  McpServerBatchUpsertView,
  McpServerDeleteView,
  McpServerProvenanceView,
  McpServerToolsView,
  McpServerView,
} from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function listMcpServers() {
  return apiRequest<McpServerView[]>("/mcp/servers");
}

export function getMcpConfigOverview() {
  return apiRequest<McpConfigOverviewView>("/mcp/config");
}

export function upsertMcpServers(body: McpServerBatchUpsertRequest) {
  return apiRequest<McpServerBatchUpsertView>("/mcp/servers/batch", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteMcpServer(serverId: string) {
  return apiRequest<McpServerDeleteView>(`/mcp/servers/${encodeURIComponent(serverId)}`, {
    method: "DELETE",
  });
}

export function getMcpServerTools(serverId: string) {
  return apiRequest<McpServerToolsView>(`/mcp/servers/${encodeURIComponent(serverId)}/tools`);
}

export function refreshMcpServer(serverId: string) {
  return apiRequest<ExtensionStatusView>(`/mcp/servers/${encodeURIComponent(serverId)}/refresh`, {
    method: "POST",
  });
}

export function reconnectMcpServer(serverId: string) {
  return apiRequest<ExtensionStatusView>(`/mcp/servers/${encodeURIComponent(serverId)}/reconnect`, {
    method: "POST",
  });
}

export function getMcpServerProvenance(serverId: string) {
  return apiRequest<McpServerProvenanceView>(`/mcp/servers/${encodeURIComponent(serverId)}/provenance`);
}

export function listMcpResources(serverId?: string | null) {
  const query = serverId ? `?server_id=${encodeURIComponent(serverId)}` : "";
  return apiRequest<CapabilityResourceView[]>(`/mcp/resources${query}`);
}

export function readMcpResource(serverId: string, resourceId: string) {
  return apiRequest<McpResourceContentView>(
    `/mcp/servers/${encodeURIComponent(serverId)}/resources/${encodeURIComponent(resourceId)}`,
  );
}

export function listMcpPrompts(serverId?: string | null) {
  const query = serverId ? `?server_id=${encodeURIComponent(serverId)}` : "";
  return apiRequest<CapabilityPromptView[]>(`/mcp/prompts${query}`);
}

export function renderMcpPrompt(serverId: string, promptId: string, body: McpPromptRenderRequest) {
  return apiRequest<McpPromptRenderView>(
    `/mcp/servers/${encodeURIComponent(serverId)}/prompts/${encodeURIComponent(promptId)}`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}
