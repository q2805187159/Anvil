from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .deps import AppRuntimeDeps, build_app_runtime_deps
from .models import ErrorResponse, HealthView
from .services import GatewayAdapterError
from .routers import approvals, artifacts, catalog, config, extensions, interactions, link_previews, mcp, memory, models, plugins, processes, scheduled_tasks, self_upgrade, shell, thread_runs, threads, tools, uploads, skills, subagents, system


DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:13200",
    "http://localhost:13200",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
)


def resolve_cors_origins() -> list[str]:
    raw = os.getenv("ANVIL_GATEWAY_CORS_ORIGINS")
    if raw is None:
        return list(DEFAULT_CORS_ORIGINS)

    normalized = raw.strip()
    if not normalized or normalized.lower() == "none":
        return []

    return [origin.strip() for origin in normalized.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    deps_factory = app.state.deps_factory
    app.state.runtime_deps = deps_factory()
    runtime_deps: AppRuntimeDeps = app.state.runtime_deps
    watcher_task: asyncio.Task | None = None
    curator_task: asyncio.Task | None = None
    memory_maintenance_task: asyncio.Task | None = None
    scheduled_task: asyncio.Task | None = None
    if runtime_deps.effective_config.config_freshness.mtime_watch_enabled or runtime_deps.skill_mtime_watch_enabled():
        async def watch_runtime_freshness() -> None:
            interval = max(runtime_deps.effective_config.config_freshness.watch_interval_seconds, 1)
            while True:
                await asyncio.sleep(interval)
                if runtime_deps.effective_config.config_freshness.mtime_watch_enabled:
                    await runtime_deps.refresh_if_needed()
                if runtime_deps.skill_mtime_watch_enabled():
                    await runtime_deps.refresh_skills_if_needed()

        watcher_task = asyncio.create_task(watch_runtime_freshness())
    if runtime_deps.skill_curator_watch_enabled():
        curator_task = asyncio.create_task(runtime_deps.run_skill_curator_watch_loop())
    if runtime_deps.memory_maintenance_watch_enabled():
        memory_maintenance_task = asyncio.create_task(runtime_deps.run_memory_maintenance_watch_loop())
    if runtime_deps.scheduled_tasks_watch_enabled():
        scheduled_task = asyncio.create_task(runtime_deps.run_scheduled_tasks_watch_loop())
    try:
        yield
    finally:
        if watcher_task is not None:
            watcher_task.cancel()
        if curator_task is not None:
            curator_task.cancel()
        if memory_maintenance_task is not None:
            memory_maintenance_task.cancel()
        if scheduled_task is not None:
            scheduled_task.cancel()
        runtime_deps.close()


def make_gateway_app(
    *,
    config_layers=None,
    feature_set=None,
    thread_root: Path | None = None,
    state_db_path: Path | None = None,
    chat_model_override: Any | None = None,
    subagent_service=None,
    tracing_service=None,
    runtime_deps: AppRuntimeDeps | None = None,
) -> FastAPI:
    app = FastAPI(title="Anvil Gateway", lifespan=lifespan)
    cors_origins = resolve_cors_origins()
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    if runtime_deps is not None:
        app.state.deps_factory = lambda: runtime_deps
    else:
        app.state.deps_factory = lambda: build_app_runtime_deps(
            config_layers=config_layers,
            feature_set=feature_set,
            thread_root=thread_root,
            state_db_path=state_db_path,
            chat_model_override=chat_model_override,
            subagent_service=subagent_service,
            tracing_service=tracing_service,
        )

    @app.exception_handler(GatewayAdapterError)
    async def gateway_adapter_error_handler(_: Request, exc: GatewayAdapterError):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response().model_dump(mode="json"),
        )

    @app.get("/health", response_model=HealthView)
    def health() -> HealthView:
        return HealthView()

    for router in (
        threads.router,
        thread_runs.router,
        approvals.router,
        interactions.router,
        subagents.router,
        processes.router,
        scheduled_tasks.router,
        shell.router,
        uploads.router,
        artifacts.router,
        link_previews.router,
        config.router,
        models.router,
        tools.router,
        catalog.router,
        skills.router,
        self_upgrade.router,
        plugins.router,
        memory.router,
        extensions.router,
        mcp.router,
        system.router,
    ):
        app.include_router(router)

    return app


app = make_gateway_app()
