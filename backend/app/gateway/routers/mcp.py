from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import (
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
)
from .. import services


router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/servers")
async def list_mcp_servers(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[McpServerView]:
    return await services.list_mcp_servers(deps)


@router.get("/config", response_model=McpConfigOverviewView)
async def get_mcp_config_overview(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> McpConfigOverviewView:
    return await services.get_mcp_config_overview(deps)


@router.post("/servers/batch", response_model=McpServerBatchUpsertView)
async def upsert_mcp_servers(
    body: McpServerBatchUpsertRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> McpServerBatchUpsertView:
    return await services.upsert_mcp_servers(deps, body)


@router.delete("/servers/{server_id}", response_model=McpServerDeleteView)
async def delete_mcp_server(
    server_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> McpServerDeleteView:
    return await services.delete_mcp_server(deps, server_id)


@router.get("/servers/{server_id}/tools")
async def get_mcp_server_tools(
    server_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> McpServerToolsView:
    return await services.get_mcp_server_tools(deps, server_id)


@router.post("/servers/{server_id}/refresh")
async def refresh_mcp_server(
    server_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ExtensionStatusView:
    return await services.refresh_mcp_server(deps, server_id)


@router.post("/servers/{server_id}/reconnect")
async def reconnect_mcp_server(
    server_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ExtensionStatusView:
    return await services.reconnect_mcp_server(deps, server_id)


@router.get("/servers/{server_id}/provenance")
async def get_mcp_server_provenance(
    server_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> McpServerProvenanceView:
    return await services.get_mcp_server_provenance(deps, server_id)


@router.get("/resources")
async def list_mcp_resources(
    server_id: str | None = None,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[CapabilityResourceView]:
    return services.list_mcp_resources(deps, server_id=server_id)


@router.get("/servers/{server_id}/resources")
async def list_mcp_server_resources(
    server_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[CapabilityResourceView]:
    return services.list_mcp_resources(deps, server_id=server_id)


@router.get("/servers/{server_id}/resources/{resource_id}")
async def read_mcp_server_resource(
    server_id: str,
    resource_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> McpResourceContentView:
    return services.read_mcp_resource(deps, server_id=server_id, resource_id=resource_id)


@router.get("/prompts")
async def list_mcp_prompts(
    server_id: str | None = None,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[CapabilityPromptView]:
    return services.list_mcp_prompts(deps, server_id=server_id)


@router.get("/servers/{server_id}/prompts")
async def list_mcp_server_prompts(
    server_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[CapabilityPromptView]:
    return services.list_mcp_prompts(deps, server_id=server_id)


@router.post("/servers/{server_id}/prompts/{prompt_id}")
async def render_mcp_server_prompt(
    server_id: str,
    prompt_id: str,
    body: McpPromptRenderRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> McpPromptRenderView:
    return services.get_mcp_prompt(
        deps,
        server_id=server_id,
        prompt_id=prompt_id,
        arguments=body.arguments,
    )
