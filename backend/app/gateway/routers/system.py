from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from ..deps import AppRuntimeDeps, get_runtime_deps
from .. import services


router = APIRouter(tags=["system"])


@router.get("/events/system")
async def stream_system_events(
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> StreamingResponse:
    return StreamingResponse(services.system_event_stream(deps), media_type="text/event-stream")


@router.post("/admin/reload")
async def admin_reload(
    scope: str = Query("all"),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> dict[str, object]:
    return await services.admin_reload(deps, scope=scope)
