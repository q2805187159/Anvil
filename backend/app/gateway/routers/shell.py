from __future__ import annotations

from fastapi import APIRouter, Query

from app.shell.commands import command_catalog_public_dict, completion_catalog_public_dict

from ..models import ShellCommandCatalogView


router = APIRouter(prefix="/shell", tags=["shell"])


@router.get("/commands", response_model=ShellCommandCatalogView)
def list_shell_commands(
    scope: str | None = Query(default=None),
    query: str | None = Query(default=None),
) -> ShellCommandCatalogView:
    return ShellCommandCatalogView.model_validate(command_catalog_public_dict(scope=scope, query=query))


@router.get("/commands/complete", response_model=ShellCommandCatalogView)
def complete_shell_commands(
    prefix: str = Query(default=""),
    scope: str | None = Query(default=None),
) -> ShellCommandCatalogView:
    return ShellCommandCatalogView.model_validate(completion_catalog_public_dict(prefix, scope=scope))
