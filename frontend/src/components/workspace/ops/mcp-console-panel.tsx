"use client";

import React, { useEffect, useMemo, useState } from "react";

import type { McpServerView } from "@/src/core/contracts";
import {
  useMcpConfigOverview,
  useMcpPrompts,
  useMcpResource,
  useMcpResources,
  useMcpServerProvenance,
  useMcpServerTools,
  useMcpServers,
  useReloadMcpQueries,
} from "@/src/core/mcp/hooks";
import { Button } from "@/src/components/ui/button";
import { ScrollArea } from "@/src/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/src/components/ui/tabs";
import { cn } from "@/src/lib/utils";

import { OpsEmptyState, OpsJsonBlock, OpsPanelCard, OpsSelectableItem, OpsTagList } from "./shared";
import type { OpsCopy } from "./types";

type McpConsolePanelProps = {
  copy: OpsCopy;
  selectedServer: string | null;
  selectedItem: string | null;
  onSelectServer(serverId: string | null): void;
  onSelectItem(itemId: string | null): void;
  onAction(action: string, payload?: { server?: string | null; item?: string | null }): void;
};

type StatusTone = "ready" | "warning" | "muted" | "danger";

export function McpConsolePanel({
  copy,
  selectedServer,
  selectedItem,
  onSelectServer,
  onSelectItem,
  onAction,
}: McpConsolePanelProps) {
  const [activeTab, setActiveTab] = useState<"tools" | "resources" | "prompts" | "provenance">("tools");
  const overviewQuery = useMcpConfigOverview();
  const serversQuery = useMcpServers();
  const reloadMcpQueries = useReloadMcpQueries();
  const servers = serversQuery.data ?? [];
  const overview = overviewQuery.data ?? null;

  useEffect(() => {
    if (!servers.length) {
      return;
    }
    if (!selectedServer || !servers.some((server) => server.server_id === selectedServer)) {
      onSelectServer(servers[0]?.server_id ?? null);
    }
  }, [onSelectServer, selectedServer, servers]);

  const selectedServerView = useMemo(
    () => servers.find((server) => server.server_id === selectedServer) ?? null,
    [selectedServer, servers],
  );
  const needsSelectedItemClassification = Boolean(selectedItem) && activeTab === "tools";
  const toolsQuery = useMcpServerTools(selectedServer, { enabled: activeTab === "tools" });
  const provenanceQuery = useMcpServerProvenance(selectedServer, { enabled: activeTab === "provenance" });
  const resourcesQuery = useMcpResources(selectedServer, { enabled: activeTab === "resources" || needsSelectedItemClassification });
  const promptsQuery = useMcpPrompts(selectedServer, { enabled: activeTab === "prompts" || needsSelectedItemClassification });

  const resourceIds = useMemo(() => (resourcesQuery.data ?? []).map((item) => item.resource_id), [resourcesQuery.data]);
  const promptIds = useMemo(() => (promptsQuery.data ?? []).map((item) => item.prompt_id), [promptsQuery.data]);
  const selectedResourceId = resourceIds.includes(selectedItem ?? "") ? selectedItem : null;
  const selectedPromptId = promptIds.includes(selectedItem ?? "") ? selectedItem : null;
  const resourceQuery = useMcpResource(selectedServer, selectedResourceId, { enabled: activeTab === "resources" });

  useEffect(() => {
    if (selectedResourceId) {
      setActiveTab("resources");
      return;
    }
    if (selectedPromptId) {
      setActiveTab("prompts");
    }
  }, [selectedPromptId, selectedResourceId]);

  return (
    <div className="grid h-full min-h-0 gap-4 lg:grid-cols-[330px_minmax(0,1fr)]">
      <ScrollArea className="h-full min-h-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-3 shadow-[var(--shadow-card)]" hideHorizontalScrollbar>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <Button type="button" size="sm" variant="primary" onClick={() => onAction("add", { server: null })}>
              {copy.mcp.add}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => reloadMcpQueries.mutate()}
              disabled={reloadMcpQueries.isPending}
            >
              {copy.mcp.reload}
            </Button>
          </div>

          <OpsPanelCard title={copy.mcp.overview} className="shadow-none">
            <div className="grid grid-cols-2 gap-2">
              <Metric label={copy.overview.total} value={overview?.server_count ?? servers.length} />
              <Metric label={copy.mcp.ready} value={overview?.ready_count ?? servers.filter((server) => server.ready).length} tone="ready" />
              <Metric label={copy.mcp.authRequired} value={overview?.auth_required_count ?? 0} tone="warning" />
              <Metric label={copy.mcp.hiddenFromModel} value={overview?.hidden_from_model_count ?? 0} tone="muted" />
            </div>
            <div className="mt-3 min-w-0 truncate rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-2.5 py-2 text-[11px] text-[var(--muted)]">
              {copy.mcp.configPath}: {overview?.config_path ?? copy.common.none}
            </div>
          </OpsPanelCard>

          {servers.length === 0 ? <OpsEmptyState text={copy.mcp.noServers} /> : null}
          {servers.map((server) => (
            <OpsSelectableItem
              key={server.server_id}
              active={selectedServer === server.server_id}
              title={server.server_id}
              subtitle={server.error || server.description}
              meta={`${server.transport_kind ?? "unknown"} · ${server.status} · ${server.tool_count} ${copy.mcp.tools}`}
              onClick={() => {
                onSelectServer(server.server_id);
                onSelectItem(null);
              }}
            />
          ))}
        </div>
      </ScrollArea>

      <ScrollArea className="h-full min-h-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-4 shadow-[var(--shadow-card)]" hideHorizontalScrollbar>
        {!selectedServerView ? <OpsEmptyState text={copy.mcp.noSelection} /> : null}
        {selectedServerView ? (
          <div className="min-w-0 space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="truncate text-lg font-semibold text-[var(--ink)]">{selectedServerView.server_id}</div>
                  <Badge tone={serverTone(selectedServerView)}>
                    {modelVisible(selectedServerView) ? copy.mcp.visibleToModel : copy.mcp.notVisibleToModel}
                  </Badge>
                </div>
                <div className="mt-1 text-sm text-[var(--muted)]">
                  {copy.mcp.status}: {selectedServerView.status}
                </div>
                <div className="mt-1 text-sm leading-5 text-[var(--muted)]">{selectedServerView.description || copy.common.none}</div>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <Button size="sm" variant="secondary" onClick={() => onAction("add", { server: null })}>
                  {copy.mcp.add}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => onAction("delete", { server: selectedServerView.server_id })}>
                  {copy.mcp.delete}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => onAction("refresh", { server: selectedServerView.server_id })}>
                  {copy.mcp.refresh}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => onAction("reconnect", { server: selectedServerView.server_id })}>
                  {copy.mcp.reconnect}
                </Button>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <OpsPanelCard title={copy.mcp.counts}>
                <div className="grid grid-cols-3 gap-2">
                  <Metric label={copy.mcp.tools} value={selectedServerView.tool_count ?? 0} />
                  <Metric label={copy.mcp.resources} value={selectedServerView.resource_count ?? 0} />
                  <Metric label={copy.mcp.prompts} value={selectedServerView.prompt_count ?? 0} />
                </div>
              </OpsPanelCard>
              <OpsPanelCard title={copy.mcp.connection}>
                <div className="grid gap-2 text-sm">
                  <DetailRow label={copy.mcp.enabled} value={selectedServerView.enabled ? copy.common.yes : copy.common.no} />
                  <DetailRow label={copy.mcp.ready} value={selectedServerView.ready ? copy.common.yes : copy.common.no} />
                  <DetailRow label={copy.mcp.authRequired} value={selectedServerView.auth_required ? copy.common.yes : copy.common.no} />
                  <DetailRow label={copy.common.source} value={selectedServerView.config_source ?? copy.common.none} />
                </div>
              </OpsPanelCard>
            </div>

            <OpsPanelCard title={copy.mcp.diagnostics}>
              {selectedServerView.error ? <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{selectedServerView.error}</div> : null}
              {(selectedServerView.diagnostics ?? []).length ? (
                <ul className="space-y-2 text-sm text-[var(--muted)]">
                  {selectedServerView.diagnostics.map((item) => (
                    <li key={item} className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2">
                      {item}
                    </li>
                  ))}
                </ul>
              ) : (
                <OpsEmptyState text={copy.mcp.noDiagnostics} />
              )}
            </OpsPanelCard>

            <Tabs
              value={activeTab}
              onValueChange={(value) => {
                const next = value as "tools" | "resources" | "prompts" | "provenance";
                setActiveTab(next);
                if (next === "tools" || next === "provenance") {
                  onSelectItem(null);
                }
              }}
            >
              <TabsList>
                <TabsTrigger value="tools">{copy.mcp.tools}</TabsTrigger>
                <TabsTrigger value="resources">{copy.mcp.resources}</TabsTrigger>
                <TabsTrigger value="prompts">{copy.mcp.prompts}</TabsTrigger>
                <TabsTrigger value="provenance">{copy.mcp.provenance}</TabsTrigger>
              </TabsList>

              <TabsContent value="tools" className="mt-4">
                <OpsPanelCard title={copy.mcp.tools}>
                  <OpsTagList items={toolsQuery.data?.tool_names ?? []} emptyLabel={copy.common.none} />
                </OpsPanelCard>
              </TabsContent>

              <TabsContent value="resources" className="mt-4">
                <div className="space-y-4">
                  <OpsPanelCard title={copy.mcp.resources}>
                    {(resourcesQuery.data ?? []).length === 0 ? <OpsEmptyState text={copy.common.none} /> : null}
                    {(resourcesQuery.data ?? []).map((resource) => (
                      <div key={resource.resource_id} className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold text-[var(--ink)]">{resource.title}</div>
                            <div className="mt-1 overflow-hidden text-xs leading-5 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2]">
                              {resource.description}
                            </div>
                            <div className="mt-2 truncate text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
                              {resource.discovery_source} · {resource.supports_read ? copy.mcp.read : copy.common.none}
                            </div>
                          </div>
                          <Button size="sm" variant="secondary" onClick={() => onSelectItem(resource.resource_id)}>
                            {copy.mcp.read}
                          </Button>
                        </div>
                      </div>
                    ))}
                  </OpsPanelCard>
                  {selectedResourceId ? (
                    <OpsPanelCard title={`${copy.mcp.read} · ${selectedResourceId}`}>
                      <OpsJsonBlock value={resourceQuery.data} emptyLabel={copy.common.none} />
                    </OpsPanelCard>
                  ) : null}
                </div>
              </TabsContent>

              <TabsContent value="prompts" className="mt-4">
                <OpsPanelCard title={copy.mcp.prompts}>
                  {(promptsQuery.data ?? []).length === 0 ? <OpsEmptyState text={copy.common.none} /> : null}
                  {(promptsQuery.data ?? []).map((prompt) => (
                    <div key={prompt.prompt_id} className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold text-[var(--ink)]">{prompt.title}</div>
                          <div className="mt-1 overflow-hidden text-xs leading-5 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2]">
                            {prompt.description}
                          </div>
                          <div className="mt-2 truncate text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
                            {prompt.discovery_source} · {(prompt.arguments ?? []).join(", ") || copy.common.none}
                          </div>
                        </div>
                        <Button size="sm" variant="secondary" onClick={() => onAction("render", { server: selectedServerView.server_id, item: prompt.prompt_id })}>
                          {copy.mcp.render}
                        </Button>
                      </div>
                    </div>
                  ))}
                </OpsPanelCard>
              </TabsContent>

              <TabsContent value="provenance" className="mt-4">
                <OpsPanelCard title={copy.mcp.provenance}>
                  <OpsJsonBlock value={provenanceQuery.data} emptyLabel={copy.common.none} />
                </OpsPanelCard>
              </TabsContent>
            </Tabs>
          </div>
        ) : null}
      </ScrollArea>
    </div>
  );
}

function modelVisible(server: McpServerView) {
  return Boolean(server.enabled && server.ready && !server.auth_required && ["enabled", "ready"].includes(String(server.status).toLowerCase()));
}

function serverTone(server: McpServerView): StatusTone {
  if (modelVisible(server)) {
    return "ready";
  }
  if (server.auth_required) {
    return "warning";
  }
  if (!server.enabled) {
    return "muted";
  }
  return "danger";
}

function Metric({ label, value, tone = "muted" }: { label: string; value: number | string; tone?: StatusTone }) {
  return (
    <div className={cn("rounded-lg border px-2.5 py-2", metricToneClass(tone))}>
      <div className="text-[11px] text-[var(--muted)]">{label}</div>
      <div className="mt-1 text-base font-semibold text-[var(--ink)]">{value}</div>
    </div>
  );
}

function Badge({ children, tone = "muted" }: React.PropsWithChildren<{ tone?: StatusTone }>) {
  return <span className={cn("rounded-full border px-2 py-0.5 text-[11px] font-medium", badgeToneClass(tone))}>{children}</span>;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3 text-sm">
      <div className="text-[var(--muted)]">{label}</div>
      <div className="min-w-0 truncate text-[var(--ink)]">{value}</div>
    </div>
  );
}

function metricToneClass(tone: StatusTone) {
  if (tone === "ready") {
    return "border-emerald-200 bg-emerald-50";
  }
  if (tone === "warning") {
    return "border-amber-200 bg-amber-50";
  }
  if (tone === "danger") {
    return "border-red-200 bg-red-50";
  }
  return "border-[var(--line)] bg-[var(--panel-muted)]";
}

function badgeToneClass(tone: StatusTone) {
  if (tone === "ready") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (tone === "warning") {
    return "border-amber-200 bg-amber-50 text-amber-700";
  }
  if (tone === "danger") {
    return "border-red-200 bg-red-50 text-red-700";
  }
  return "border-[var(--line)] bg-[var(--panel-muted)] text-[var(--muted)]";
}
