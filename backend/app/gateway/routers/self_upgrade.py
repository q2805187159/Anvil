from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import services
from ..deps import AppRuntimeDeps, get_runtime_deps
from ..models import SelfUpgradeHealthResponse


router = APIRouter(prefix="/self-upgrade", tags=["self-upgrade"])


@router.get("/health", response_model=SelfUpgradeHealthResponse)
def get_self_upgrade_health(
    candidate_audit_limit: int = Query(50, ge=1, le=200),
    deps: AppRuntimeDeps = Depends(get_runtime_deps),
) -> SelfUpgradeHealthResponse:
    return services.get_self_upgrade_health(
        deps,
        candidate_audit_limit=candidate_audit_limit,
    )
