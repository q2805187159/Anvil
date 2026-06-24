from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import (
    BasicConfigOverviewView,
    BasicConfigTestRequest,
    BasicConfigTestView,
    BasicConfigUpdateRequest,
    BasicConfigUpdateView,
    ConfigOverviewView,
)


router = APIRouter(prefix="/config", tags=["config"])


@router.get("/overview", response_model=ConfigOverviewView)
def get_config_overview(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ConfigOverviewView:
    return services.get_config_overview(deps)


@router.get("/basics", response_model=BasicConfigOverviewView)
def get_basic_config(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> BasicConfigOverviewView:
    return services.get_basic_config(deps)


@router.patch("/basics", response_model=BasicConfigUpdateView)
async def update_basic_config(
    body: BasicConfigUpdateRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> BasicConfigUpdateView:
    return await services.update_basic_config(deps, body)


@router.post("/basics/test", response_model=BasicConfigTestView)
def test_basic_config(
    body: BasicConfigTestRequest,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> BasicConfigTestView:
    return services.test_basic_config(deps, body)
