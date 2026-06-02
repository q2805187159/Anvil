from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import (
    PluginCatalogEntryView,
    PluginInstallRequest,
    PluginInstallView,
    PluginRegistryDeleteView,
    PluginRegistryUpsertRequest,
    PluginRegistryUpsertView,
    PluginRegistryView,
    PluginView,
)


router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("", response_model=list[PluginView])
def list_plugins(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[PluginView]:
    return services.list_plugins(deps)


@router.get("/catalog", response_model=list[PluginCatalogEntryView])
def list_plugin_catalog(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[PluginCatalogEntryView]:
    return services.list_plugin_catalog(deps)


@router.get("/registries", response_model=list[PluginRegistryView])
def list_plugin_registries(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[PluginRegistryView]:
    return services.list_plugin_registries(deps)


@router.post("/registries", response_model=PluginRegistryUpsertView)
async def upsert_plugin_registry(
    body: PluginRegistryUpsertRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> PluginRegistryUpsertView:
    return await services.upsert_plugin_registry(deps, body)


@router.post("/registries/{registry_id}/refresh", response_model=PluginRegistryUpsertView)
async def refresh_plugin_registry(
    registry_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> PluginRegistryUpsertView:
    return await services.refresh_plugin_registry(deps, registry_id)


@router.delete("/registries/{registry_id}", response_model=PluginRegistryDeleteView)
async def delete_plugin_registry(
    registry_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> PluginRegistryDeleteView:
    return await services.delete_plugin_registry(deps, registry_id)


@router.post("/install", response_model=PluginInstallView)
async def install_plugin(
    body: PluginInstallRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> PluginInstallView:
    return await services.install_plugin(deps, body)
