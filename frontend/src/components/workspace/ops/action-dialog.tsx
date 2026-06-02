"use client";

import React, { useEffect, useMemo, useState } from "react";

import type { Locale } from "@/src/core/i18n";
import { Button } from "@/src/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/src/components/ui/dialog";
import { Input } from "@/src/components/ui/input";

import { JsonArgumentEditor } from "./json-argument-editor";
import type { OpsSurface } from "./types";
import { opsCopy } from "./types";

type ActionDialogSubmitPayload = {
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
};

type ActionDialogProps = {
  open: boolean;
  locale: Locale;
  surface: OpsSurface;
  action: string | null;
  skillId?: string | null;
  serverId?: string | null;
  itemId?: string | null;
  pending: boolean;
  onOpenChange(open: boolean): void;
  onSubmit(payload: ActionDialogSubmitPayload): Promise<void>;
};

function actionLabel(locale: Locale, surface: OpsSurface, action: string | null) {
  const copy = opsCopy(locale);
  if (!action) {
    return copy.actions.title;
  }
  if (surface === "skills") {
    return (copy.skills as Record<string, string>)[action] ?? action;
  }
  if (surface === "mcp") {
    const mcpLabels: Record<string, string> = {
      add: copy.mcp.add,
      delete: copy.mcp.delete,
      refresh: copy.mcp.refresh,
      reconnect: copy.mcp.reconnect,
      render: copy.mcp.render,
    };
    return mcpLabels[action] ?? action;
  }
  if (surface === "plugins") {
    const pluginLabels: Record<string, string> = {
      install: copy.plugins.install,
      addRegistry: copy.plugins.addRegistry,
      refreshRegistry: copy.plugins.refreshRegistry,
      deleteRegistry: copy.plugins.deleteRegistry,
    };
    return pluginLabels[action] ?? action;
  }
  return action;
}

const MCP_CONFIG_EXAMPLE = `// 示例：把 your-server 改成实际 MCP server 名称。
{
  "mcpServers": {
    "your-server": {
      "enabled": true,
      "type": "stdio",
      "command": "your-command",
      "args": [
        "--help"
      ],
      "env": {},
      "description": "Describe what this MCP server provides"
    }
  }
}`;

function mcpConfigTemplate(serverId?: string | null) {
  return MCP_CONFIG_EXAMPLE;
}

export function ActionDialog({
  open,
  locale,
  surface,
  action,
  skillId,
  serverId,
  itemId,
  pending,
  onOpenChange,
  onSubmit,
}: ActionDialogProps) {
  const copy = useMemo(() => opsCopy(locale), [locale]);
  const [source, setSource] = useState("");
  const [pluginId, setPluginId] = useState("");
  const [registryId, setRegistryId] = useState("");
  const [registryName, setRegistryName] = useState("");
  const [trustLevel, setTrustLevel] = useState("third-party");
  const [enableOnInstall, setEnableOnInstall] = useState(true);
  const [force, setForce] = useState(true);
  const [argumentsText, setArgumentsText] = useState("{}");
  const [configText, setConfigText] = useState(MCP_CONFIG_EXAMPLE);
  const [jsonError, setJsonError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setPluginId("");
    setRegistryId("");
    setRegistryName("");
    setTrustLevel("third-party");
    setEnableOnInstall(true);
    setForce(true);
    setArgumentsText("{}");
    setConfigText(mcpConfigTemplate(serverId));
    setJsonError(null);
  }, [action, open, serverId, surface]);

  async function handleSubmit() {
    const payload: ActionDialogSubmitPayload = {};
    if (surface === "plugins" && action === "install") {
      payload.source = source || null;
      payload.plugin_id = pluginId || null;
      payload.enable = enableOnInstall;
      payload.force = force;
    }
    if (surface === "plugins" && action === "addRegistry") {
      payload.source = source || null;
      payload.registry_id = registryId || null;
      payload.name = registryName || null;
      payload.trust_level = trustLevel || null;
      payload.enable = true;
    }
    if (surface === "mcp" && action === "add") {
      payload.config_text = configText;
    }
    if (surface === "mcp" && action === "render") {
      try {
        payload.arguments = JSON.parse(argumentsText || "{}") as Record<string, unknown>;
        setJsonError(null);
      } catch {
        setJsonError(copy.actions.invalidJson);
        return;
      }
    }

    await onSubmit(payload);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-[min(92vw,42rem)] overflow-hidden p-0">
        <DialogHeader>
          <DialogTitle>{actionLabel(locale, surface, action)}</DialogTitle>
          <DialogDescription>{copy.actions.description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 overflow-auto px-5 py-4">
          {surface === "skills" && skillId ? (
            <label className="grid gap-2 text-sm text-[var(--muted)]">
              <span>{copy.actions.skillId}</span>
              <Input value={skillId} readOnly />
            </label>
          ) : null}

          {surface === "mcp" && action !== "add" && serverId ? (
            <label className="grid gap-2 text-sm text-[var(--muted)]">
              <span>{copy.common.server}</span>
              <Input value={serverId} readOnly />
            </label>
          ) : null}

          {surface === "mcp" && itemId ? (
            <label className="grid gap-2 text-sm text-[var(--muted)]">
              <span>{copy.common.path}</span>
              <Input value={itemId} readOnly />
            </label>
          ) : null}

          {surface === "plugins" && action === "install" ? (
            <>
              <label className="grid gap-2 text-sm text-[var(--muted)]">
                <span>{copy.actions.source}</span>
                <Input value={source} onChange={(event) => setSource(event.target.value)} placeholder="owner/repo, https://github.com/org/plugin.git, or C:\\path\\plugin" />
              </label>
              <label className="grid gap-2 text-sm text-[var(--muted)]">
                <span>{copy.actions.pluginId}</span>
                <Input value={pluginId} onChange={(event) => setPluginId(event.target.value)} placeholder="optional-plugin-id" />
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--ink)]">
                <input
                  type="checkbox"
                  checked={enableOnInstall}
                  onChange={(event) => setEnableOnInstall(event.target.checked)}
                />
                <span>{copy.actions.enableOnInstall}</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--ink)]">
                <input
                  type="checkbox"
                  checked={force}
                  onChange={(event) => setForce(event.target.checked)}
                />
                <span>{copy.actions.force}</span>
              </label>
            </>
          ) : null}

          {surface === "plugins" && action === "addRegistry" ? (
            <>
              <label className="grid gap-2 text-sm text-[var(--muted)]">
                <span>{copy.plugins.registrySource}</span>
                <Input value={source} onChange={(event) => setSource(event.target.value)} placeholder="https://example.com/plugin-catalog.json or C:\\path\\plugins" />
              </label>
              <label className="grid gap-2 text-sm text-[var(--muted)]">
                <span>{copy.actions.registryName}</span>
                <Input value={registryName} onChange={(event) => setRegistryName(event.target.value)} placeholder="Team plugin catalog" />
              </label>
              <label className="grid gap-2 text-sm text-[var(--muted)]">
                <span>{copy.actions.registryId}</span>
                <Input value={registryId} onChange={(event) => setRegistryId(event.target.value)} placeholder="optional-source-id" />
              </label>
              <label className="grid gap-2 text-sm text-[var(--muted)]">
                <span>{copy.actions.trustLevel}</span>
                <Input value={trustLevel} onChange={(event) => setTrustLevel(event.target.value)} placeholder="third-party" />
              </label>
            </>
          ) : null}

          {surface === "plugins" && (action === "refreshRegistry" || action === "deleteRegistry") && itemId ? (
            <label className="grid gap-2 text-sm text-[var(--muted)]">
              <span>{copy.actions.registryId}</span>
              <Input value={itemId} readOnly />
            </label>
          ) : null}

          {surface === "mcp" && action === "add" ? (
            <div className="grid gap-2 text-sm text-[var(--muted)]">
              <span>{copy.actions.configJson}</span>
              <JsonArgumentEditor value={configText} onChange={setConfigText} placeholder={MCP_CONFIG_EXAMPLE} error={jsonError} />
            </div>
          ) : null}

          {surface === "mcp" && action === "render" ? (
            <div className="grid gap-2 text-sm text-[var(--muted)]">
              <span>{copy.actions.arguments}</span>
              <JsonArgumentEditor value={argumentsText} onChange={setArgumentsText} error={jsonError} />
            </div>
          ) : null}
        </div>

        <div className="flex justify-end gap-2 border-t border-[var(--line)] px-5 py-4">
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)} disabled={pending}>
            {copy.actions.cancel}
          </Button>
          <Button type="button" variant="primary" onClick={() => void handleSubmit()} disabled={pending}>
            {copy.actions.submit}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
