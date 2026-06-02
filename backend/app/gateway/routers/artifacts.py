from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..deps import AppRuntimeDeps, get_runtime_deps
from .. import services


router = APIRouter(prefix="/threads/{thread_id}/artifacts", tags=["artifacts"])


@router.get("/{kind}/{path:path}")
def get_artifact(
    thread_id: str,
    kind: str,
    path: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> Response:
    content, media_type = services.get_artifact_content(deps, thread_id, kind, path)
    return Response(content=content, media_type=media_type)
