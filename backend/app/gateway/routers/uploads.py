from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import UploadResult
from .. import services


router = APIRouter(prefix="/threads/{thread_id}/uploads", tags=["uploads"])


@router.post("", response_model=UploadResult)
async def upload_files(
    thread_id: str,
    files: list[UploadFile] = File(...),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> UploadResult:
    translated = [(upload.filename or "", await upload.read()) for upload in files]
    return services.upload_files(deps, thread_id, translated)


@router.get("", response_model=UploadResult)
def list_uploads(
    thread_id: str,
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> UploadResult:
    return services.list_uploads(deps, thread_id)
