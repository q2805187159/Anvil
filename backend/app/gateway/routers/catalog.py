from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import ToolCatalogEntryView


router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/tools", response_model=list[ToolCatalogEntryView])
def list_catalog_tools(
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


@router.get("/tools/{name_or_capability_id}", response_model=ToolCatalogEntryView)
def get_catalog_tool(
    name_or_capability_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> ToolCatalogEntryView:
    return services.get_tool_catalog_entry(deps, name_or_capability_id)
