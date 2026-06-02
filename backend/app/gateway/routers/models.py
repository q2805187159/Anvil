from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import (
    ModelHealthCheckRequest,
    ModelHealthCheckView,
    ModelProviderDeleteView,
    ModelProviderPresetView,
    ModelProviderUpsertRequest,
    ModelProviderUpsertView,
    ModelSelectionUpdateRequest,
    ModelSelectionUpdateView,
    ModelView,
)
from .. import services


router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelView])
def list_models(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[ModelView]:
    return services.list_models(deps)


@router.get("/presets", response_model=list[ModelProviderPresetView])
def list_model_provider_presets() -> list[ModelProviderPresetView]:
    return services.list_model_provider_presets()


@router.put("/{name}", response_model=ModelProviderUpsertView)
async def upsert_model_provider(
    name: str,
    request: ModelProviderUpsertRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ModelProviderUpsertView:
    return await services.upsert_model_provider(deps, request.model_copy(update={"name": request.name or name}))


@router.delete("/{name}", response_model=ModelProviderDeleteView)
async def delete_model_provider(
    name: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ModelProviderDeleteView:
    return await services.delete_model_provider(deps, name)


@router.patch("/{name}/selection", response_model=ModelSelectionUpdateView)
async def update_model_selection(
    name: str,
    request: ModelSelectionUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ModelSelectionUpdateView:
    return await services.update_model_selection(deps, name, request)


@router.post("/{name}/test", response_model=ModelHealthCheckView)
async def test_model_provider(
    name: str,
    request: ModelHealthCheckRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ModelHealthCheckView:
    return await services.test_model_provider(deps, name, request)
