from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from anvil.runtime.tool_registry.contracts import CapabilityPrompt, CapabilityResource, ToolRegistryEntry


class ExternalCapabilityStatus(str, Enum):
    CONFIGURED = "configured"
    STARTING = "starting"
    READY = "ready"
    REFRESHING = "refreshing"
    BACKOFF = "backoff"
    AUTH_REQUIRED = "auth_required"
    ENABLED = "enabled"
    MATERIALIZED = "materialized"
    VISIBLE = "visible"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class ExtensionMaterialization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: str
    status: ExternalCapabilityStatus
    source_kind: str = "mcp"
    transport_kind: str | None = None
    startup_policy: str | None = None
    refresh_policy: str | None = None
    discovery_source: str = "inline_fallback"
    tools: tuple[ToolRegistryEntry, ...] = ()
    resources: tuple[CapabilityResource, ...] = ()
    prompts: tuple[CapabilityPrompt, ...] = ()
    connected: bool = False
    ready: bool = False
    auth_required: bool = False
    refresh_owner: str | None = None
    last_started_at: str | None = None
    last_refreshed_at: str | None = None
    backoff_until: str | None = None
    reconnect_count: int = 0
    diagnostics: tuple[str, ...] = ()
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtensionDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materializations: tuple[ExtensionMaterialization, ...] = ()
    effective_mcp_servers: tuple[str, ...] = ()
    effective_plugin_ids: tuple[str, ...] = ()
