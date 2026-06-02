from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import ConfigOverviewView


router = APIRouter(prefix="/config", tags=["config"])


@router.get("/overview", response_model=ConfigOverviewView)
def get_config_overview(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ConfigOverviewView:
    return services.get_config_overview(deps)
