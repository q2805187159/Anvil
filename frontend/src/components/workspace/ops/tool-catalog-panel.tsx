"use client";

import React, { useEffect, useMemo, useState } from "react";

import type { ToolCatalogEntryView } from "@/src/core/contracts";
import { useCatalogTools, useToolCatalog, useToolCatalogEntry } from "@/src/core/catalog/hooks";
import { Button } from "@/src/components/ui/button";
import { Input } from "@/src/components/ui/input";
import { NativeSelect } from "@/src/components/ui/native-select";
import { ScrollArea } from "@/src/components/ui/scroll-area";

import { OpsEmptyState, OpsJsonBlock, OpsPanelCard, OpsSelectableItem } from "./shared";
import type { OpsCopy } from "./types";

type ToolCatalogPanelProps = {
  copy: OpsCopy;
  selectedItem: string | null;
  onSelectItem(item: string | null): void;
};

function listValues(entries: ToolCatalogEntryView[], key: "source_kind" | "capability_group") {
  return Array.from(new Set(entries.map((entry) => entry[key]).filter(Boolean))).sort();
}

export function ToolCatalogPanel({ copy, selectedItem, onSelectItem }: ToolCatalogPanelProps) {
  const [search, setSearch] = useState("");
  const [sourceKind, setSourceKind] = useState<string>("");
  const [capabilityGroup, setCapabilityGroup] = useState<string>("");
  const [catalogPath, setCatalogPath] = useState<"tools" | "catalog">("tools");

  const filters = useMemo(
    () => ({
      query: search,
      sourceKind: sourceKind || null,
      capabilityGroup: capabilityGroup || null,
      path: catalogPath,
    }),
    [capabilityGroup, catalogPath, search, sourceKind],
  );
  const catalogFilters = useMemo(
    () => ({
      query: search,
      sourceKind: sourceKind || null,
      capabilityGroup: capabilityGroup || null,
    }),
    [capabilityGroup, search, sourceKind],
  );
  const toolsQuery = useToolCatalog(filters, { enabled: catalogPath === "tools" });
  const catalogQuery = useCatalogTools(catalogFilters, { enabled: catalogPath === "catalog" });
  const effectiveEntries = catalogPath === "catalog" ? catalogQuery.data ?? [] : toolsQuery.data ?? [];

  useEffect(() => {
    if (!effectiveEntries.length) {
      return;
    }
    if (!selectedItem || !effectiveEntries.some((entry) => entry.capability_id === selectedItem || entry.name === selectedItem)) {
      onSelectItem(effectiveEntries[0]?.capability_id ?? effectiveEntries[0]?.name ?? null);
    }
  }, [effectiveEntries, onSelectItem, selectedItem]);

  const selectedInCurrentList = Boolean(
    selectedItem && effectiveEntries.some((entry) => entry.capability_id === selectedItem || entry.name === selectedItem),
  );
  const detailQuery = useToolCatalogEntry(selectedInCurrentList ? selectedItem : null, catalogPath, {
    enabled: selectedInCurrentList,
  });
  const sourceKinds = useMemo(() => listValues(effectiveEntries, "source_kind"), [effectiveEntries]);
  const capabilityGroups = useMemo(() => listValues(effectiveEntries, "capability_group"), [effectiveEntries]);

  return (
    <div className="grid h-full min-h-0 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <section
        aria-label={copy.toolPanel.title}
        className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)] shadow-[var(--shadow-card)]"
      >
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          <div className="space-y-4 pr-2">
            <OpsPanelCard title={copy.toolPanel.title}>
              <div className="grid min-w-0 gap-3">
                <label className="grid min-w-0 gap-2 text-sm text-[var(--muted)]">
                  <span>{copy.filters.search}</span>
                  <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={copy.filters.search} />
                </label>
                <div className="grid min-w-0 gap-3">
                  <label className="grid min-w-0 gap-2 text-sm text-[var(--muted)]">
                    <span>{copy.filters.sourceKind}</span>
                    <NativeSelect
                      className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap"
                      value={sourceKind}
                      onChange={(event) => setSourceKind(event.target.value)}
                    >
                      <option value="">{copy.filters.all}</option>
                      {sourceKinds.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </NativeSelect>
                  </label>
                  <label className="grid min-w-0 gap-2 text-sm text-[var(--muted)]">
                    <span>{copy.filters.capabilityGroup}</span>
                    <NativeSelect
                      className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap"
                      value={capabilityGroup}
                      onChange={(event) => setCapabilityGroup(event.target.value)}
                    >
                      <option value="">{copy.filters.all}</option>
                      {capabilityGroups.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </NativeSelect>
                  </label>
                </div>
                <div className="flex min-w-0 flex-wrap gap-2">
                  <Button variant={catalogPath === "tools" ? "primary" : "secondary"} size="sm" onClick={() => setCatalogPath("tools")}>
                    `/tools`
                  </Button>
                  <Button variant={catalogPath === "catalog" ? "primary" : "secondary"} size="sm" onClick={() => setCatalogPath("catalog")}>
                    `/catalog`
                  </Button>
                </div>
              </div>
            </OpsPanelCard>

            <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-3 shadow-[var(--shadow-card)]">
              <div className="space-y-3">
                {effectiveEntries.length === 0 ? <OpsEmptyState text={copy.toolPanel.noResults} /> : null}
                {effectiveEntries.map((entry) => {
                  const active = selectedItem === entry.capability_id || selectedItem === entry.name;
                  return (
                    <OpsSelectableItem
                      key={entry.capability_id}
                      active={active}
                      title={entry.display_name || entry.name}
                      subtitle={entry.summary}
                      meta={`${entry.source_kind} · ${entry.capability_group} · ${entry.visibility}`}
                      onClick={() => onSelectItem(entry.capability_id)}
                    />
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </section>

      <ScrollArea className="h-full min-h-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-4 shadow-[var(--shadow-card)]" hideHorizontalScrollbar>
        {!detailQuery.data ? <OpsEmptyState text={copy.toolPanel.noDetail} /> : null}
        {detailQuery.data ? (
          <div className="min-w-0 space-y-4">
            <div className="min-w-0">
              <div className="truncate text-lg font-semibold text-[var(--ink)]">{detailQuery.data.display_name || detailQuery.data.name}</div>
              <div className="mt-1 overflow-hidden text-sm leading-6 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:3]">
                {detailQuery.data.summary}
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
                <span>{detailQuery.data.source_kind}</span>
                <span>{detailQuery.data.capability_group}</span>
                <span>{detailQuery.data.visibility}</span>
                <span>{detailQuery.data.stability}</span>
                <span>{detailQuery.data.deferred ? "deferred" : "materialized"}</span>
              </div>
            </div>

            <OpsPanelCard title={copy.toolPanel.catalogPath}>
              <OpsJsonBlock
                value={{
                  path: catalogPath,
                  capability_id: detailQuery.data.capability_id,
                  source_id: detailQuery.data.source_id,
                  risk_category: detailQuery.data.risk_category ?? null,
                }}
                emptyLabel={copy.common.none}
              />
            </OpsPanelCard>

            <OpsPanelCard title={copy.toolPanel.approval}>
              <OpsJsonBlock value={detailQuery.data.approval} emptyLabel={copy.common.none} />
            </OpsPanelCard>
            <OpsPanelCard title={copy.toolPanel.dependencies}>
              <OpsJsonBlock value={detailQuery.data.dependencies} emptyLabel={copy.common.none} />
            </OpsPanelCard>
            <OpsPanelCard title={copy.toolPanel.provenance}>
              <OpsJsonBlock value={detailQuery.data.provenance} emptyLabel={copy.common.none} />
            </OpsPanelCard>
            <OpsPanelCard title={copy.toolPanel.health}>
              <OpsJsonBlock value={detailQuery.data.health} emptyLabel={copy.common.none} />
            </OpsPanelCard>
            <OpsPanelCard title={copy.toolPanel.resources}>
              <OpsJsonBlock value={detailQuery.data.resources} emptyLabel={copy.common.none} />
            </OpsPanelCard>
            <OpsPanelCard title={copy.toolPanel.prompts}>
              <OpsJsonBlock value={detailQuery.data.prompts} emptyLabel={copy.common.none} />
            </OpsPanelCard>
          </div>
        ) : null}
      </ScrollArea>
    </div>
  );
}
