from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import LinkPreviewView
from .. import services


router = APIRouter(prefix="/link-previews", tags=["link-previews"])


@router.get("/metadata", response_model=LinkPreviewView)
def get_link_preview_metadata(
    url: str = Query(...),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> LinkPreviewView:
    return services.get_link_preview(deps, url)
