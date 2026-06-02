from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import ToolCatalogEntryView


router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("/catalog", response_model=list[ToolCatalogEntryView])
def list_tool_catalog(
    query: str | None = None,
    source_kind: str | None = None,
    capability_group: str | None = None,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> list[ToolCatalogEntryView]:
    return services.list_tools_catalog(
        deps,
        query=query,
        source_kind=source_kind,
        capability_group=capability_group,
    )


@router.get("/{name_or_capability_id}", response_model=ToolCatalogEntryView)
def get_tool_catalog_entry(
    name_or_capability_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ToolCatalogEntryView:
    return services.get_tool_catalog_entry(deps, name_or_capability_id)
