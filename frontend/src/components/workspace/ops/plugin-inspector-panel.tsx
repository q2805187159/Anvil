"use client";

import React, { useEffect, useMemo, useState } from "react";
import { DatabaseIcon, PackagePlusIcon, PlugIcon, RefreshCcwIcon, Trash2Icon } from "lucide-react";

import type { PluginCatalogEntryView, PluginRegistryView, PluginView } from "@/src/core/contracts";
import { usePluginCatalog, usePluginRegistries, usePlugins } from "@/src/core/plugins/hooks";
import { Button } from "@/src/components/ui/button";
import { Input } from "@/src/components/ui/input";
import { ScrollArea } from "@/src/components/ui/scroll-area";
import { cn } from "@/src/lib/utils";

import { OpsEmptyState, OpsJsonBlock, OpsPanelCard, OpsSelectableItem, OpsTagList } from "./shared";
import type { OpsCopy } from "./types";

type PluginInspectorPanelProps = {
  copy: OpsCopy;
  selectedItem: string | null;
  installPending?: boolean;
  onSelectItem(item: string | null): void;
  onAction(action: string, pluginId?: string | null): void;
  onInstallCatalog(entry: PluginCatalogEntryView): void;
};

type PluginMode = "catalog" | "installed" | "sources";

export function PluginInspectorPanel({
  copy,
  selectedItem,
  installPending = false,
  onSelectItem,
  onAction,
  onInstallCatalog,
}: PluginInspectorPanelProps) {
  const [mode, setMode] = useState<PluginMode>("catalog");
  const pluginsQuery = usePlugins({ enabled: mode === "installed" });
  const catalogQuery = usePluginCatalog({ enabled: mode === "catalog" });
  const registriesQuery = usePluginRegistries({ enabled: mode === "sources" });
  const plugins = pluginsQuery.data ?? [];
  const catalog = catalogQuery.data ?? [];
  const registries = registriesQuery.data ?? [];
  const [query, setQuery] = useState("");
  const [selectedRegistry, setSelectedRegistry] = useState<string | null>(null);
  const installedById = useMemo(() => new Map(plugins.map((plugin) => [plugin.plugin_id, plugin])), [plugins]);
  const filteredCatalog = useMemo(() => filterCatalog(catalog, query), [catalog, query]);
  const pluginListItems = mode === "catalog" ? filteredCatalog : plugins;
  const visibleCount = mode === "catalog" ? filteredCatalog.length : mode === "installed" ? plugins.length : registries.length;

  useEffect(() => {
    if (mode === "sources") {
      if (!registries.length) {
        if (selectedRegistry !== null) {
          setSelectedRegistry(null);
        }
        return;
      }
      if (!selectedRegistry || !registries.some((registry) => registry.registry_id === selectedRegistry)) {
        setSelectedRegistry(registries[0]?.registry_id ?? null);
      }
      return;
    }
    if (!pluginListItems.length) {
      if (selectedItem !== null) {
        onSelectItem(null);
      }
      return;
    }
    const hasSelection = pluginListItems.some((plugin) => getPluginId(plugin) === selectedItem);
    if (!selectedItem || !hasSelection) {
      onSelectItem(getPluginId(pluginListItems[0]) ?? null);
    }
  }, [mode, onSelectItem, pluginListItems, registries, selectedItem, selectedRegistry]);

  const catalogDetail = mode === "catalog" ? filteredCatalog.find((plugin) => plugin.plugin_id === selectedItem) ?? null : null;
  const installedDetail = mode === "installed" ? plugins.find((plugin) => plugin.plugin_id === selectedItem) ?? null : null;
  const registryDetail = mode === "sources" ? registries.find((registry) => registry.registry_id === selectedRegistry) ?? null : null;
  const installedOverlay = catalogDetail ? installedById.get(catalogDetail.plugin_id) ?? null : null;

  return (
    <div className="grid h-full min-h-0 gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
      <ScrollArea className="h-full min-h-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-3 shadow-[var(--shadow-card)]" hideHorizontalScrollbar>
        <div className="space-y-3">
          <div className="grid grid-cols-3 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-1">
            <button
              type="button"
              className={tabClass(mode === "catalog")}
              onClick={() => setMode("catalog")}
            >
              {copy.plugins.catalog}
            </button>
            <button
              type="button"
              className={tabClass(mode === "installed")}
              onClick={() => setMode("installed")}
            >
              {copy.plugins.installed}
            </button>
            <button
              type="button"
              className={tabClass(mode === "sources")}
              onClick={() => setMode("sources")}
            >
              {copy.plugins.sources}
            </button>
          </div>

          <Button type="button" size="sm" variant="primary" className="w-full" onClick={() => onAction("addRegistry", null)}>
            <DatabaseIcon className="size-4" />
            {copy.plugins.addRegistry}
          </Button>
          <Button type="button" size="sm" variant="secondary" className="w-full" onClick={() => onAction("install", selectedItem)}>
            <PackagePlusIcon className="size-4" />
            {copy.plugins.advancedInstall}
          </Button>

          {mode === "catalog" ? (
            <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={copy.plugins.search} />
          ) : null}
          {mode === "catalog" && catalogQuery.isLoading ? <OpsEmptyState text={copy.common.loading} /> : null}
          {mode === "installed" && pluginsQuery.isLoading ? <OpsEmptyState text={copy.common.loading} /> : null}
          {mode === "sources" && registriesQuery.isLoading ? <OpsEmptyState text={copy.common.loading} /> : null}
          {!visibleCount && !(catalogQuery.isLoading || pluginsQuery.isLoading || registriesQuery.isLoading) ? (
            <OpsEmptyState text={mode === "catalog" ? copy.plugins.catalogEmpty : mode === "installed" ? copy.plugins.installedEmpty : copy.plugins.sourcesEmpty} />
          ) : null}

          {mode === "catalog"
            ? filteredCatalog.map((plugin) => (
                <CatalogListItem
                  key={plugin.plugin_id}
                  plugin={plugin}
                  copy={copy}
                  active={selectedItem === plugin.plugin_id}
                  installed={Boolean(installedById.get(plugin.plugin_id) ?? plugin.installed)}
                  onClick={() => onSelectItem(plugin.plugin_id)}
                />
              ))
            : mode === "installed"
              ? plugins.map((plugin) => (
                <OpsSelectableItem
                  key={plugin.plugin_id}
                  active={selectedItem === plugin.plugin_id}
                  title={plugin.plugin_id}
                  subtitle={plugin.source_path ?? undefined}
                  meta={`${plugin.enabled ? copy.common.enabled : copy.common.disabled} · ${plugin.tool_count} tools · ${plugin.memory_provider_count ?? 0} memory`}
                  onClick={() => onSelectItem(plugin.plugin_id)}
                />
                ))
              : registries.map((registry) => (
                <RegistryListItem
                  key={registry.registry_id}
                  registry={registry}
                  copy={copy}
                  active={selectedRegistry === registry.registry_id}
                  onClick={() => setSelectedRegistry(registry.registry_id)}
                />
              ))}
        </div>
      </ScrollArea>

      <ScrollArea className="h-full min-h-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-4 shadow-[var(--shadow-card)]" hideHorizontalScrollbar>
        {!catalogDetail && !installedDetail && !registryDetail ? <OpsEmptyState text={copy.plugins.noDetail} /> : null}
        {catalogDetail ? (
          <CatalogDetail
            copy={copy}
            plugin={catalogDetail}
            installedPlugin={installedOverlay}
            installPending={installPending}
            onInstall={() => onInstallCatalog(catalogDetail)}
          />
        ) : null}
        {installedDetail ? <InstalledDetail copy={copy} plugin={installedDetail} /> : null}
        {registryDetail ? (
          <RegistryDetail
            copy={copy}
            registry={registryDetail}
            onRefresh={() => onAction("refreshRegistry", registryDetail.registry_id)}
            onDelete={() => onAction("deleteRegistry", registryDetail.registry_id)}
          />
        ) : null}
      </ScrollArea>
    </div>
  );
}

function getPluginId(plugin: PluginCatalogEntryView | PluginView | undefined): string | null {
  if (!plugin) {
    return null;
  }
  return plugin.plugin_id;
}

function filterCatalog(catalog: PluginCatalogEntryView[], query: string) {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return catalog;
  }
  return catalog.filter((plugin) => {
    const haystack = [
      plugin.plugin_id,
      plugin.name,
      plugin.description,
      plugin.registry_name,
      plugin.registry_source,
      ...plugin.tags,
      ...plugin.tool_names,
      ...(plugin.memory_providers ?? []),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
}

function tabClass(active: boolean) {
  return cn(
    "h-8 rounded-[0.7rem] px-3 text-xs font-semibold transition",
    active ? "bg-[var(--panel)] text-[var(--ink)] shadow-[var(--shadow-card)]" : "text-[var(--muted)] hover:text-[var(--ink)]",
  );
}

function RegistryListItem({
  registry,
  copy,
  active,
  onClick,
}: {
  registry: PluginRegistryView;
  copy: OpsCopy;
  active: boolean;
  onClick(): void;
}) {
  const meta = [
    registry.readonly ? copy.plugins.readonly : copy.common.enabled,
    registry.source_kind,
    `${registry.entry_count} plugins`,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full min-w-0 max-w-full overflow-hidden rounded-xl border px-3 py-3 text-left transition-[background,border-color,box-shadow,transform] duration-200 ease-[var(--ease-smooth)] active:translate-y-px",
        active
          ? "border-[color-mix(in_srgb,var(--secondary)_58%,var(--line))] bg-[var(--accent-soft)] shadow-[var(--shadow-card)]"
          : "border-[var(--line)] bg-[var(--panel)] hover:border-[color-mix(in_srgb,var(--primary)_26%,var(--line))] hover:bg-[var(--panel-muted)]",
      )}
    >
      <div className="flex min-w-0 items-start gap-2">
        <DatabaseIcon className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" />
        <div className="min-w-0 flex-1">
          <div className="min-w-0 truncate text-sm font-semibold text-[var(--ink)]">{registry.name}</div>
          <div className="mt-1 min-w-0 max-w-full overflow-hidden break-all text-xs leading-5 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2]">
            {registry.source}
          </div>
        </div>
      </div>
      <div className="mt-2 min-w-0 max-w-full truncate text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{meta}</div>
      {registry.error ? <div className="mt-2 break-words text-xs text-[var(--danger)]">{registry.error}</div> : null}
    </button>
  );
}

function CatalogListItem({
  plugin,
  copy,
  active,
  installed,
  onClick,
}: {
  plugin: PluginCatalogEntryView;
  copy: OpsCopy;
  active: boolean;
  installed: boolean;
  onClick(): void;
}) {
  const meta = [
    installed ? copy.plugins.installed : copy.plugins.available,
    plugin.tool_count ? `${plugin.tool_count} tools` : null,
    plugin.mcp_server_count ? `${plugin.mcp_server_count} MCP` : null,
    plugin.memory_provider_count ? `${plugin.memory_provider_count} memory` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full min-w-0 max-w-full overflow-hidden rounded-xl border px-3 py-3 text-left transition-[background,border-color,box-shadow,transform] duration-200 ease-[var(--ease-smooth)] active:translate-y-px",
        active
          ? "border-[color-mix(in_srgb,var(--secondary)_58%,var(--line))] bg-[var(--accent-soft)] shadow-[var(--shadow-card)]"
          : "border-[var(--line)] bg-[var(--panel)] hover:border-[color-mix(in_srgb,var(--primary)_26%,var(--line))] hover:bg-[var(--panel-muted)]",
      )}
    >
      <div className="flex min-w-0 items-start gap-2">
        <PlugIcon className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" />
        <div className="min-w-0 flex-1">
          <div className="min-w-0 truncate text-sm font-semibold text-[var(--ink)]">{plugin.name || plugin.plugin_id}</div>
          <div className="mt-1 min-w-0 max-w-full overflow-hidden break-words text-xs leading-5 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2]">
            {plugin.description || plugin.source}
          </div>
        </div>
      </div>
      <div className="mt-2 min-w-0 max-w-full truncate text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{meta}</div>
      {plugin.registry_name ? <div className="mt-1 truncate text-xs text-[var(--muted)]">{plugin.registry_name}</div> : null}
    </button>
  );
}

function CatalogDetail({
  copy,
  plugin,
  installedPlugin,
  installPending,
  onInstall,
}: {
  copy: OpsCopy;
  plugin: PluginCatalogEntryView;
  installedPlugin: PluginView | null;
  installPending: boolean;
  onInstall(): void;
}) {
  const installed = Boolean(installedPlugin ?? plugin.installed);
  return (
    <div className="min-w-0 space-y-4">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="break-words text-lg font-semibold text-[var(--ink)]">{plugin.name || plugin.plugin_id}</div>
          <div className="mt-1 break-words text-sm leading-6 text-[var(--muted)]">{plugin.description || copy.plugins.noDetail}</div>
        </div>
        <Button
          type="button"
          size="sm"
          variant={installed ? "secondary" : "primary"}
          disabled={!plugin.installable || installPending}
          onClick={onInstall}
          className="shrink-0"
        >
          {installed ? <RefreshCcwIcon className="size-4" /> : <PackagePlusIcon className="size-4" />}
          {installed ? copy.plugins.reinstall : copy.plugins.installSelected}
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label={copy.common.status} value={installed ? copy.plugins.installed : copy.plugins.available} />
        <Metric label={copy.plugins.sourceKind} value={plugin.source_kind ?? copy.common.none} />
        <Metric label={copy.plugins.version} value={plugin.version ?? copy.common.none} />
        <Metric label={copy.plugins.trust} value={plugin.trust_level ?? copy.common.none} />
      </div>

      <OpsPanelCard title={copy.plugins.counts}>
        <div className="grid gap-2 text-sm text-[var(--ink)] sm:grid-cols-2 lg:grid-cols-3">
          <Metric label={copy.plugins.tools} value={String(plugin.tool_count)} compact />
          <Metric label={copy.plugins.skillRoots} value={String(plugin.skill_count)} compact />
          <Metric label={copy.plugins.mcpServers} value={String(plugin.mcp_server_count)} compact />
          <Metric label={copy.plugins.memoryProviders} value={String(plugin.memory_provider_count ?? 0)} compact />
          <Metric label={copy.plugins.resources} value={String(plugin.resource_count)} compact />
          <Metric label={copy.plugins.prompts} value={String(plugin.prompt_count)} compact />
        </div>
      </OpsPanelCard>
      <OpsPanelCard title={copy.common.source}>
        <OpsJsonBlock
          value={{
            source: plugin.source,
            registry: plugin.registry_name,
            registry_source: plugin.registry_source,
            homepage: plugin.homepage,
            author: plugin.author,
          }}
          emptyLabel={copy.common.none}
        />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.tags}>
        <OpsTagList items={plugin.tags} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.permissions}>
        <OpsTagList items={plugin.permissions} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.skillRoots}>
        <OpsTagList items={plugin.skill_roots} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.tools}>
        <OpsTagList items={plugin.tool_names} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.mcpServers}>
        <OpsTagList items={plugin.mcp_servers} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.memoryProviders}>
        <OpsTagList items={plugin.memory_providers ?? []} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.catalogMetadata}>
        <OpsJsonBlock value={plugin.catalog_metadata} emptyLabel={copy.common.none} />
      </OpsPanelCard>
    </div>
  );
}

function RegistryDetail({
  copy,
  registry,
  onRefresh,
  onDelete,
}: {
  copy: OpsCopy;
  registry: PluginRegistryView;
  onRefresh(): void;
  onDelete(): void;
}) {
  return (
    <div className="min-w-0 space-y-4">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="break-words text-lg font-semibold text-[var(--ink)]">{registry.name}</div>
          <div className="mt-1 break-all text-sm leading-6 text-[var(--muted)]">{registry.source}</div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button type="button" size="sm" variant="secondary" onClick={onRefresh}>
            <RefreshCcwIcon className="size-4" />
            {copy.plugins.refreshRegistry}
          </Button>
          <Button type="button" size="sm" variant="danger" onClick={onDelete} disabled={registry.readonly}>
            <Trash2Icon className="size-4" />
            {copy.plugins.deleteRegistry}
          </Button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label={copy.plugins.registryKind} value={registry.source_kind} />
        <Metric label={copy.plugins.trust} value={registry.trust_level ?? copy.common.none} />
        <Metric label={copy.plugins.counts} value={`${registry.entry_count}`} />
        <Metric label={copy.plugins.readonly} value={registry.readonly ? copy.common.enabled : copy.common.disabled} />
      </div>

      <OpsPanelCard title={copy.plugins.registrySource}>
        <OpsJsonBlock
          value={{
            registry_id: registry.registry_id,
            source: registry.source,
            cached: registry.cached,
            cache_path: registry.cache_path,
            config_path: registry.config_path,
            last_checked_at: registry.last_checked_at,
          }}
          emptyLabel={copy.common.none}
        />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.diagnostics}>
        <OpsJsonBlock value={registry.error ? { error: registry.error, diagnostics: registry.diagnostics } : registry.diagnostics} emptyLabel={copy.common.none} />
      </OpsPanelCard>
    </div>
  );
}

function InstalledDetail({ copy, plugin }: { copy: OpsCopy; plugin: PluginView }) {
  return (
    <div className="min-w-0 space-y-4">
      <div className="min-w-0">
        <div className="break-words text-lg font-semibold text-[var(--ink)]">{plugin.plugin_id}</div>
        <div className="text-sm text-[var(--muted)]">{plugin.enabled ? copy.common.enabled : copy.common.disabled}</div>
      </div>

      <OpsPanelCard title={copy.common.path}>
        <OpsJsonBlock value={{ source_path: plugin.source_path, discovery_source: plugin.discovery_source }} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.skillRoots}>
        <OpsTagList items={plugin.skill_roots} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.tools}>
        <OpsTagList items={plugin.tool_names} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.memoryProviders}>
        <OpsJsonBlock value={plugin.memory_providers ?? []} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.resources}>
        <OpsJsonBlock value={plugin.resources} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.prompts}>
        <OpsJsonBlock value={plugin.prompts} emptyLabel={copy.common.none} />
      </OpsPanelCard>
      <OpsPanelCard title={copy.plugins.catalogMetadata}>
        <OpsJsonBlock value={plugin.catalog_metadata} emptyLabel={copy.common.none} />
      </OpsPanelCard>
    </div>
  );
}

function Metric({ label, value, compact = false }: { label: string; value: string; compact?: boolean }) {
  return (
    <div className={cn("min-w-0 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2", compact && "px-2 py-1.5")}>
      <div className="truncate text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{label}</div>
      <div className="mt-1 min-w-0 break-words text-sm font-semibold text-[var(--ink)]">{value}</div>
    </div>
  );
}
