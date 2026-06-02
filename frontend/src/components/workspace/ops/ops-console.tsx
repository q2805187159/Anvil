"use client";

import React, { useMemo } from "react";

import type { PluginCatalogEntryView, ThreadStateView } from "@/src/core/contracts";
import type { Locale } from "@/src/core/i18n";
import { useDeleteMcpServer, useReconnectMcpServer, useRefreshMcpServer, useRenderMcpPrompt, useUpsertMcpServers } from "@/src/core/mcp/hooks";
import { useDeletePluginRegistry, useInstallPlugin, useRefreshPluginRegistry, useUpsertPluginRegistry } from "@/src/core/plugins/hooks";
import { useManageSkill, useReloadSkills } from "@/src/core/skills/hooks";
import { usePauseScheduledTask, useResumeScheduledTask, useRunScheduledTask, useRunScheduledTaskAutomation, useScheduledTaskAutomation, useScheduledTasks } from "@/src/core/threads/hooks";
import { XIcon } from "lucide-react";
import { Button } from "@/src/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/src/components/ui/dialog";

import { ActionDialog } from "./action-dialog";
import { ConfigOverviewPanel } from "./config-overview-panel";
import { MemoryGovernancePanel } from "./memory-governance-panel";
import { McpConsolePanel } from "./mcp-console-panel";
import { ModelConfigPanel } from "./model-config-panel";
import { OpsSurfaceNav } from "./ops-surface-nav";
import { PluginInspectorPanel } from "./plugin-inspector-panel";
import { ScheduledAutomationPanel } from "./scheduled-automation-panel";
import { SelfUpgradePanel } from "./self-upgrade-panel";
import { SkillGovernancePanel } from "./skill-governance-panel";
import { ToolCatalogPanel } from "./tool-catalog-panel";
import type { OpsSurface, OpsUrlState } from "./types";
import { opsCopy } from "./types";

type OpsConsoleProps = {
  open: boolean;
  locale: Locale;
  urlState: OpsUrlState;
  activeThreadId: string | null;
  threadState: ThreadStateView | null;
  onOpenChange(open: boolean): void;
  onStateChange(patch: Partial<OpsUrlState>, replace?: boolean): void;
};

type OpsConsoleContentProps = OpsConsoleProps & {
  copy: ReturnType<typeof opsCopy>;
};

function buildDefaultState(threadState: ThreadStateView | null): Partial<OpsUrlState> {
  void threadState;
  return { surface: "overview", item: null, server: null, action: null };
}

export function OpsConsole({
  open,
  locale,
  urlState,
  activeThreadId,
  threadState,
  onOpenChange,
  onStateChange,
}: OpsConsoleProps) {
  const copy = useMemo(() => opsCopy(locale), [locale]);

  if (!open) {
    return null;
  }

  return (
    <OpsConsoleContent
      open={open}
      locale={locale}
      urlState={urlState}
      activeThreadId={activeThreadId}
      threadState={threadState}
      onOpenChange={onOpenChange}
      onStateChange={onStateChange}
      copy={copy}
    />
  );
}

function OpsConsoleContent({
  open,
  locale,
  urlState,
  activeThreadId,
  threadState,
  onOpenChange,
  onStateChange,
  copy,
}: OpsConsoleContentProps) {
  const detailState = urlState.open ? urlState : { ...urlState, ...buildDefaultState(threadState) };
  const manageSkill = useManageSkill();
  const reloadSkills = useReloadSkills();
  const refreshMcpServer = useRefreshMcpServer();
  const reconnectMcpServer = useReconnectMcpServer();
  const upsertMcpServers = useUpsertMcpServers();
  const deleteMcpServer = useDeleteMcpServer();
  const installPlugin = useInstallPlugin();
  const upsertPluginRegistry = useUpsertPluginRegistry();
  const refreshPluginRegistry = useRefreshPluginRegistry();
  const deletePluginRegistry = useDeletePluginRegistry();
  const renderPrompt = useRenderMcpPrompt(urlState.server, urlState.action === "render" ? urlState.item : null);
  const scheduledTasks = useScheduledTasks({
    enabled: detailState.surface === "scheduled",
  });
  const scheduledAutomation = useScheduledTaskAutomation({
    enabled: detailState.surface === "scheduled",
  });
  const runScheduledAutomation = useRunScheduledTaskAutomation();
  const runScheduledTask = useRunScheduledTask();
  const pauseScheduledTask = usePauseScheduledTask();
  const resumeScheduledTask = useResumeScheduledTask();

  const pending =
    manageSkill.isPending ||
    reloadSkills.isPending ||
    refreshMcpServer.isPending ||
    reconnectMcpServer.isPending ||
    upsertMcpServers.isPending ||
    deleteMcpServer.isPending ||
    installPlugin.isPending ||
    upsertPluginRegistry.isPending ||
    refreshPluginRegistry.isPending ||
    deletePluginRegistry.isPending ||
    renderPrompt.isPending ||
    runScheduledAutomation.isPending ||
    runScheduledTask.isPending ||
    pauseScheduledTask.isPending ||
    resumeScheduledTask.isPending;

  async function handleActionSubmit(payload: {
    source?: string | null;
    plugin_id?: string | null;
    registry_id?: string | null;
    name?: string | null;
    trust_level?: string | null;
    enable?: boolean | null;
    force?: boolean | null;
    revision?: string | null;
    destination?: string | null;
    arguments?: Record<string, unknown>;
    config_text?: string | null;
  }) {
    try {
      if (urlState.surface === "skills" && urlState.action) {
        if (urlState.action === "reload") {
          await reloadSkills.mutateAsync();
        } else {
          await manageSkill.mutateAsync({
            action: urlState.action,
            skill_id: urlState.item,
          });
        }
      }
      if (urlState.surface === "mcp" && urlState.action) {
        if (urlState.action === "add" && payload.config_text) {
          await upsertMcpServers.mutateAsync({ config_text: payload.config_text });
        }
        if (urlState.action === "refresh" && urlState.server) {
          await refreshMcpServer.mutateAsync(urlState.server);
        }
        if (urlState.action === "reconnect" && urlState.server) {
          await reconnectMcpServer.mutateAsync(urlState.server);
        }
        if (urlState.action === "delete" && urlState.server) {
          await deleteMcpServer.mutateAsync(urlState.server);
        }
        if (urlState.action === "render" && urlState.server && urlState.item) {
          await renderPrompt.mutateAsync({
            arguments: payload.arguments ?? {},
          });
        }
      }
      if (urlState.surface === "plugins" && urlState.action === "install") {
        await installPlugin.mutateAsync({
          source: payload.source ?? "",
          plugin_id: payload.plugin_id ?? null,
          enable: payload.enable ?? true,
          force: payload.force ?? true,
        });
      }
      if (urlState.surface === "plugins" && urlState.action === "addRegistry") {
        await upsertPluginRegistry.mutateAsync({
          source: payload.source ?? "",
          registry_id: payload.registry_id ?? null,
          name: payload.name ?? null,
          enabled: true,
          trust_level: payload.trust_level ?? "third-party",
        });
      }
      if (urlState.surface === "plugins" && urlState.action === "refreshRegistry" && urlState.item) {
        await refreshPluginRegistry.mutateAsync(urlState.item);
      }
      if (urlState.surface === "plugins" && urlState.action === "deleteRegistry" && urlState.item) {
        await deletePluginRegistry.mutateAsync(urlState.item);
      }
      onStateChange({ action: null }, true);
    } catch (error) {
      console.error(error instanceof Error ? error.message : copy.common.error);
    }
  }

  async function handleCatalogPluginInstall(entry: PluginCatalogEntryView) {
    try {
      await installPlugin.mutateAsync({
        source: entry.source,
        plugin_id: entry.plugin_id,
        enable: true,
        force: true,
      });
      onStateChange({ item: entry.plugin_id, action: null }, true);
    } catch (error) {
      console.error(error instanceof Error ? error.message : copy.common.error);
    }
  }

  function openAction(action: string, patch: Partial<OpsUrlState> = {}) {
    onStateChange({ ...patch, action }, false);
  }

  function selectSurface(surface: OpsSurface) {
    onStateChange({ surface, item: null, action: null, server: null, open: true }, false);
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent hideCloseButton className="flex h-[92vh] max-h-[92vh] w-[min(96vw,96rem)] max-w-[96rem] flex-col overflow-hidden p-0">
          <DialogHeader className="shrink-0 px-6 py-5">
            <div className="flex min-w-0 items-start gap-4">
              <div className="min-w-0 flex-1">
                <DialogTitle>{copy.title}</DialogTitle>
                <DialogDescription>{copy.description}</DialogDescription>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="shrink-0"
                aria-label={copy.close}
                onClick={() => onOpenChange(false)}
              >
                <XIcon className="size-4" />
              </Button>
            </div>
          </DialogHeader>

          <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)] overflow-hidden">
            <OpsSurfaceNav
              copy={copy}
              activeSurface={detailState.surface}
              onSelect={selectSurface}
            />
            <div className="min-h-0 bg-[var(--canvas)]">
              <div className="h-full min-h-0 overflow-hidden px-4 py-4">
                {detailState.surface === "overview" ? (
                  <ConfigOverviewPanel
                    copy={copy}
                    activeThreadId={activeThreadId}
                    threadState={threadState}
                    onSelectSurface={selectSurface}
                  />
                ) : null}
                {detailState.surface === "models" ? (
                  <ModelConfigPanel copy={copy} />
                ) : null}
                {detailState.surface === "tools" ? (
                  <ToolCatalogPanel
                    copy={copy}
                    selectedItem={detailState.item}
                    onSelectItem={(item) => onStateChange({ item }, false)}
                  />
                ) : null}
                {detailState.surface === "skills" ? (
                  <SkillGovernancePanel
                    copy={copy}
                    selectedItem={detailState.item}
                    onSelectItem={(item) => onStateChange({ item }, false)}
                    onAction={(action, skillId) => openAction(action, { item: skillId ?? null })}
                  />
                ) : null}
                {detailState.surface === "memory" ? (
                  <MemoryGovernancePanel copy={copy} />
                ) : null}
                {detailState.surface === "selfUpgrade" ? (
                  <SelfUpgradePanel copy={copy} />
                ) : null}
                {detailState.surface === "mcp" ? (
                  <McpConsolePanel
                    copy={copy}
                    selectedServer={detailState.server}
                    selectedItem={detailState.item}
                    onSelectServer={(server) => onStateChange({ server, item: null }, false)}
                    onSelectItem={(item) => onStateChange({ item }, false)}
                    onAction={(action, payload) =>
                      openAction(action, {
                        server: payload?.server ?? detailState.server,
                        item: payload?.item ?? null,
                      })
                    }
                  />
                ) : null}
                {detailState.surface === "plugins" ? (
                  <PluginInspectorPanel
                    copy={copy}
                    selectedItem={detailState.item}
                    installPending={installPlugin.isPending}
                    onSelectItem={(item) => onStateChange({ item }, false)}
                    onAction={(action, pluginId) => openAction(action, { item: pluginId ?? null })}
                    onInstallCatalog={handleCatalogPluginInstall}
                  />
                ) : null}
                {detailState.surface === "scheduled" ? (
                  <ScheduledAutomationPanel
                    copy={copy}
                    tasks={scheduledTasks.data?.items ?? []}
                    automation={scheduledAutomation.data}
                    loading={scheduledTasks.isLoading || scheduledTasks.isFetching || scheduledAutomation.isFetching}
                    pending={pending}
                    onRefresh={() => {
                      void scheduledTasks.refetch();
                      void scheduledAutomation.refetch();
                    }}
                    onRunDue={() => void runScheduledAutomation.mutateAsync()}
                    onRun={(taskId) => void runScheduledTask.mutateAsync(taskId)}
                    onPause={(taskId) => void pauseScheduledTask.mutateAsync(taskId)}
                    onResume={(taskId) => void resumeScheduledTask.mutateAsync(taskId)}
                  />
                ) : null}
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <ActionDialog
        open={open && Boolean(urlState.action)}
        locale={locale}
        surface={urlState.surface}
        action={urlState.action}
        skillId={urlState.surface === "skills" ? urlState.item : null}
        serverId={urlState.surface === "mcp" ? urlState.server : null}
        itemId={urlState.surface === "mcp" || urlState.surface === "plugins" ? urlState.item : null}
        pending={pending}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            onStateChange({ action: null }, true);
          }
        }}
        onSubmit={handleActionSubmit}
      />
    </>
  );
}
