from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import ExtensionStatusView
from .. import services


router = APIRouter(prefix="/extensions", tags=["extensions"])


@router.get("", response_model=list[ExtensionStatusView])
def list_extensions(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[ExtensionStatusView]:
    return services.list_extensions(deps)


@router.post("/{server_id}/refresh", response_model=ExtensionStatusView)
def refresh_extension(
    server_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ExtensionStatusView:
    return services.refresh_extension(deps, server_id)
