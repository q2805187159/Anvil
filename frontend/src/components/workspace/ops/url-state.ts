"use client";

import type { OpsSurface, OpsUrlState } from "./types";

export const DEFAULT_OPS_SURFACE: OpsSurface = "overview";

export const DEFAULT_OPS_URL_STATE: OpsUrlState = {
  open: false,
  surface: DEFAULT_OPS_SURFACE,
  item: null,
  action: null,
  server: null,
};

function normalizeSurface(value: string | null): OpsSurface {
  if (
    value === "overview" ||
    value === "basics" ||
    value === "models" ||
    value === "tools" ||
    value === "skills" ||
    value === "memory" ||
    value === "selfUpgrade" ||
    value === "mcp" ||
    value === "plugins" ||
    value === "scheduled"
  ) {
    return value;
  }
  return DEFAULT_OPS_SURFACE;
}

function normalizeText(value: string | null): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

export function parseOpsUrlState(search: string): OpsUrlState {
  const params = new URLSearchParams(search);
  return {
    open: params.get("ops") === "1",
    surface: normalizeSurface(params.get("surface")),
    item: normalizeText(params.get("item")),
    action: normalizeText(params.get("action")),
    server: normalizeText(params.get("server")),
  };
}

export function mergeOpsUrlState(
  current: OpsUrlState,
  patch: Partial<OpsUrlState>,
): OpsUrlState {
  return {
    open: patch.open ?? current.open,
    surface: patch.surface ? normalizeSurface(patch.surface) : current.surface,
    item: patch.item === undefined ? current.item : normalizeText(patch.item),
    action: patch.action === undefined ? current.action : normalizeText(patch.action),
    server: patch.server === undefined ? current.server : normalizeText(patch.server),
  };
}

export function applyOpsStateToSearch(currentSearch: string, state: OpsUrlState): string {
  const params = new URLSearchParams(currentSearch);

  if (!state.open) {
    params.delete("ops");
    params.delete("surface");
    params.delete("item");
    params.delete("action");
    params.delete("server");
  } else {
    params.set("ops", "1");
    params.set("surface", state.surface);
    if (state.item) {
      params.set("item", state.item);
    } else {
      params.delete("item");
    }
    if (state.action) {
      params.set("action", state.action);
    } else {
      params.delete("action");
    }
    if (state.server) {
      params.set("server", state.server);
    } else {
      params.delete("server");
    }
  }

  const next = params.toString();
  return next ? `?${next}` : "";
}
