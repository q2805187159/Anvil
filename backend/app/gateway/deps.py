from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import os
import time

from fastapi import Request

from anvil import (
    CapabilityAssemblyService,
    Checkpointer,
    ConfigLayer,
    ConfigResolutionResult,
    ConfigService,
    ExtensionsService,
    MemoryManager,
    MemoryService,
    PathService,
    RunEngine,
    RuntimeFeatureSet,
    SkillsService,
    Store,
    SubagentService,
    TracingService,
    UploadService,
)
from anvil.processes import ProcessService
from anvil.runtime.runs import RunEventLogStore
from anvil.runtime.thread_service import ThreadRuntimeService

from app.runtime_deps import (
    ConfigCoordinator,
    RuntimeDepsBundle,
    build_default_config_layers_from_env,
    build_runtime_deps_bundle,
    close_runtime_services,
)
from .streaming_runs import BackgroundRunStreamManager


@dataclass
class AppRuntimeDeps:
    config_layers: list[ConfigLayer]
    config_service: ConfigService
    config_coordinator: ConfigCoordinator
    config_result: ConfigResolutionResult
    effective_config: Any
    feature_set: RuntimeFeatureSet
    harness_factory: Any
    path_service: PathService
    checkpointer: Checkpointer
    store: Store
    thread_service: ThreadRuntimeService
    run_engine: RunEngine
    run_event_log_store: RunEventLogStore
    skills_service: SkillsService
    memory_service: MemoryService | None
    memory_manager: MemoryManager
    extensions_service: ExtensionsService
    capability_assembly_service: CapabilityAssemblyService
    upload_service: UploadService
    subagent_service: SubagentService
    process_service: ProcessService
    scheduled_task_service: Any
    tracing_service: TracingService
    system_event_bus: Any
    stream_run_manager: BackgroundRunStreamManager
    _skill_mtimes: dict[str, float] | None = None
    _skill_last_scan_at: float = 0.0
    _skill_scan_task: asyncio.Task | None = None
    _capability_preview_cache: dict[str, object] | None = None
    _runtime_view_cache: dict[str, dict[str, object]] = field(default_factory=dict)

    def close(self) -> None:
        self.stream_run_manager.close()
        close_runtime_services(
            [
                ("run_engine_background_tasks", self._wait_for_run_engine_background_tasks),
                ("process_service", self.process_service.close),
                ("scheduled_task_service", self._close_scheduled_task_service),
                ("memory_manager", self.memory_manager.shutdown),
                ("subagent_service", self.subagent_service.close),
                ("checkpointer", self.checkpointer.close),
                ("store", self.store.close),
            ]
        )

    async def refresh_if_needed(self) -> bool:
        try:
            config_result, changed = self.config_coordinator.refresh_if_needed()
        except Exception as exc:  # noqa: BLE001
            await self.system_event_bus.publish(
                "reload_failed",
                {"scope": "config", "error": str(exc)},
            )
            return False
        if changed:
            self.config_layers = self.config_coordinator.config_layers
            self.config_result = config_result
            self.effective_config = config_result.effective_config
            self.invalidate_runtime_caches()
            await self.system_event_bus.publish(
                "config_reloaded",
                {
                    "config_fingerprint": config_result.fingerprint,
                },
            )
        return changed

    async def refresh_skills_if_needed(self, *, block: bool = True) -> bool:
        if not self.skill_mtime_watch_enabled():
            return False
        interval = max(float(self.effective_config.config_freshness.watch_interval_seconds), 1.0)
        now = time.monotonic()
        if (now - self._skill_last_scan_at) < interval:
            return False
        if self._skill_scan_task is not None and not self._skill_scan_task.done():
            return False
        self._skill_last_scan_at = now
        if not block:
            self._skill_scan_task = asyncio.create_task(self._refresh_skills_if_needed_background_scan())
            return False
        return await self._refresh_skills_if_needed_blocking_scan()

    async def _refresh_skills_if_needed_background_scan(self) -> None:
        try:
            await self._refresh_skills_if_needed_blocking_scan()
        except Exception as exc:  # noqa: BLE001
            await self.system_event_bus.publish(
                "reload_failed",
                {"scope": "skills", "error": str(exc)},
            )

    async def _refresh_skills_if_needed_blocking_scan(self) -> bool:
        current = await asyncio.to_thread(self._collect_skill_mtimes)
        if self._skill_mtimes is None:
            self._skill_mtimes = current
            return False
        if current == self._skill_mtimes:
            return False
        previous = self._skill_mtimes
        self._skill_mtimes = current
        self.skills_service.cache.invalidate()
        self.invalidate_runtime_caches()
        changed_paths = sorted(set(previous).symmetric_difference(current) | {
            path for path, mtime in current.items() if previous.get(path) != mtime
        })
        await self.system_event_bus.publish(
            "skills_changed",
            {
                "skills_fingerprint": self.config_result.fingerprint,
                "changed_paths": changed_paths,
            },
        )
        return True

    async def run_skill_curator_automation_if_due(self, *, force_run: bool = False) -> bool:
        result = await asyncio.to_thread(self._run_skill_curator_automation, force_run=force_run)
        if not result.ran:
            return False
        await self._publish_skill_curator_automation(result)
        self._skill_mtimes = await asyncio.to_thread(self._collect_skill_mtimes)
        return True

    def run_skill_curator_automation_sync(self, *, force_run: bool = False):
        result = self._run_skill_curator_automation(force_run=force_run)
        if result.ran:
            self._skill_mtimes = self._collect_skill_mtimes()
            self.invalidate_runtime_caches()
        return result

    def _run_skill_curator_automation(self, *, force_run: bool = False):
        return self.skills_service.run_curator_automation_if_due(
            config=self.effective_config,
            force_run=force_run,
        )

    def get_capability_preview(self, factory: Callable[[], object], *, ttl_seconds: float = 5.0) -> object:
        now = time.monotonic()
        cache = self._capability_preview_cache
        if (
            cache is not None
            and cache.get("fingerprint") == self.config_result.fingerprint
            and now < float(cache.get("expires_at", 0.0))
        ):
            return cache["value"]
        value = factory()
        expires_at = time.monotonic() + max(float(ttl_seconds), 0.0)
        self._capability_preview_cache = {
            "fingerprint": self.config_result.fingerprint,
            "expires_at": expires_at,
            "value": value,
        }
        return value

    def invalidate_capability_preview_cache(self) -> None:
        self._capability_preview_cache = None

    def get_runtime_view_cache(
        self,
        name: str,
        factory: Callable[[], object],
        *,
        ttl_seconds: float = 2.0,
        cache_key: str = "",
    ) -> object:
        now = time.monotonic()
        fingerprint = f"{self.config_result.fingerprint}:{cache_key}"
        cached = self._runtime_view_cache.get(name)
        if (
            cached is not None
            and cached.get("fingerprint") == fingerprint
            and now < float(cached.get("expires_at", 0.0))
        ):
            return cached["value"]
        value = factory()
        expires_at = time.monotonic() + max(float(ttl_seconds), 0.0)
        self._runtime_view_cache[name] = {
            "fingerprint": fingerprint,
            "expires_at": expires_at,
            "value": value,
        }
        return value

    def invalidate_runtime_view_cache(self, name: str | None = None) -> None:
        if name is None:
            self._runtime_view_cache.clear()
        else:
            self._runtime_view_cache.pop(name, None)

    def invalidate_runtime_caches(self) -> None:
        self.invalidate_capability_preview_cache()
        self.invalidate_runtime_view_cache()

    def _close_scheduled_task_service(self) -> None:
        close = getattr(self.scheduled_task_service, "close", None)
        if callable(close):
            close()

    def _wait_for_run_engine_background_tasks(self) -> None:
        wait = getattr(self.run_engine, "wait_for_background_tasks", None)
        if callable(wait):
            wait(timeout_seconds=10)

    async def _publish_skill_curator_automation(self, result) -> None:
        if not result.ran:
            return
        report = result.report or {}
        recommendations = report.get("recommendations") if isinstance(report.get("recommendations"), list) else []
        await self.system_event_bus.publish(
            "skills_changed",
            {
                "action": "curator_automation",
                "curator": True,
                "skills_fingerprint": self.config_result.fingerprint,
                "run_id": report.get("run_id"),
                "counts": report.get("counts"),
                "recommendation_count": len(recommendations),
                "recommendations": recommendations[:5],
                "next_run_at": result.next_run_at,
            },
        )

    def _collect_skill_mtimes(self) -> dict[str, float]:
        mtimes: dict[str, float] = {}
        for root_path in self.skills_service.resolve_roots(self.effective_config):
            if not root_path.exists():
                continue
            for path in root_path.rglob("SKILL.md"):
                try:
                    mtimes[str(path)] = path.stat().st_mtime
                except OSError:
                    continue
        return mtimes

    def skill_curator_watch_interval_seconds(self) -> float:
        config = self.effective_config.skills_config.curator
        return max(float(config.tick_seconds), 10.0)

    def skill_curator_watch_enabled(self) -> bool:
        return bool(
            self.effective_config.skills_config.enabled
            and self.effective_config.skills_config.curator.automation_enabled
        )

    def skill_mtime_watch_enabled(self) -> bool:
        return bool(
            self.effective_config.skills_config.enabled
            and self.effective_config.skills_config.watch_enabled
        )

    async def run_skill_curator_watch_loop(self) -> None:
        while True:
            await asyncio.sleep(self.skill_curator_watch_interval_seconds())
            with contextlib.suppress(Exception):
                await self.run_skill_curator_automation_if_due()

    def memory_maintenance_watch_enabled(self) -> bool:
        maintenance = self.effective_config.hcms.maintenance
        return bool(
            self.effective_config.hcms.enabled
            and maintenance.enabled
            and maintenance.automation_enabled
        )

    def memory_maintenance_watch_interval_seconds(self) -> float:
        return max(float(self.effective_config.hcms.maintenance.tick_seconds), 10.0)

    async def run_memory_maintenance_automation_if_due(self, *, force_run: bool = False) -> bool:
        result = self.memory_manager.run_maintenance_automation_if_due(force_run=force_run)
        if not result.ran:
            return False
        await self._publish_memory_maintenance_automation(result)
        return True

    def run_memory_maintenance_automation_sync(self, *, force_run: bool = False):
        return self.memory_manager.run_maintenance_automation_if_due(force_run=force_run)

    async def _publish_memory_maintenance_automation(self, result) -> None:
        if not result.ran or result.report is None:
            return
        report = result.report
        await self.system_event_bus.publish(
            "memory_changed",
            {
                "action": "memory_maintenance_automation",
                "run_id": report.run_id,
                "status": report.status,
                "dry_run": report.dry_run,
                "policy": report.policy,
                "source": report.source,
                "next_run_at": result.next_run_at.isoformat() if result.next_run_at is not None else None,
                "counts": {
                    "update_queue_pending": report.update_queue_pending,
                    "update_queue_drained": report.update_queue_drained,
                    "reflection_jobs_due": report.reflection_jobs_due,
                    "reflection_jobs_run": report.reflection_jobs_run,
                    "governance_candidates": report.governance.candidate_count,
                    "governance_executed": report.governance.executed_count,
                    "governance_skipped": report.governance.skipped_count,
                },
                "actions_executed": dict(report.actions_executed),
                "skipped_actions": dict(report.skipped_actions),
                "error_count": len(report.errors),
            },
        )

    async def run_memory_maintenance_watch_loop(self) -> None:
        while True:
            await asyncio.sleep(self.memory_maintenance_watch_interval_seconds())
            with contextlib.suppress(Exception):
                await self.run_memory_maintenance_automation_if_due()

    def scheduled_tasks_watch_enabled(self) -> bool:
        return bool(self.effective_config.scheduled_tasks.enabled)

    def scheduled_tasks_watch_interval_seconds(self) -> float:
        return max(float(self.effective_config.scheduled_tasks.tick_seconds), 10.0)

    async def run_scheduled_tasks_watch_loop(self) -> None:
        while True:
            await asyncio.sleep(self.scheduled_tasks_watch_interval_seconds())
            with contextlib.suppress(Exception):
                result = self.scheduled_task_service.run_automation_due(
                    limit=max(int(self.effective_config.scheduled_tasks.max_due_per_tick), 1),
                    tick_seconds=self.effective_config.scheduled_tasks.tick_seconds,
                )
                for item in result.results:
                    if item.ran:
                        await self.system_event_bus.publish(
                            "scheduled_task_run",
                            {
                                "task_id": item.task.task_id,
                                "execution_id": item.execution.execution_id if item.execution else None,
                                "status": item.execution.status if item.execution else None,
                                "next_run_at": item.task.next_run_at,
                            },
                        )


def build_app_runtime_deps(
    *,
    config_layers: list[ConfigLayer] | None = None,
    feature_set: RuntimeFeatureSet | None = None,
    thread_root: Path | None = None,
    state_db_path: Path | None = None,
    chat_model_override: Any | None = None,
    subagent_service: SubagentService | None = None,
    tracing_service: TracingService | None = None,
) -> AppRuntimeDeps:
    bundle = build_runtime_deps_bundle(
        config_layers=config_layers,
        feature_set=feature_set,
        thread_root=thread_root,
        state_db_path=state_db_path,
        chat_model_override=chat_model_override,
        subagent_service=subagent_service,
        tracing_service=tracing_service,
    )
    return _bundle_to_app_runtime_deps(bundle)


async def get_runtime_deps(request: Request) -> AppRuntimeDeps:
    deps = request.app.state.runtime_deps
    await deps.refresh_if_needed()
    await deps.refresh_skills_if_needed(block=False)
    return deps


def _bundle_to_app_runtime_deps(bundle: RuntimeDepsBundle) -> AppRuntimeDeps:
    return AppRuntimeDeps(
        config_layers=bundle.config_layers,
        config_service=bundle.config_service,
        config_coordinator=bundle.config_coordinator,
        config_result=bundle.config_result,
        effective_config=bundle.effective_config,
        feature_set=bundle.feature_set,
        harness_factory=bundle.harness_factory,
        path_service=bundle.path_service,
        checkpointer=bundle.checkpointer,
        store=bundle.store,
        thread_service=bundle.thread_service,
        run_engine=bundle.run_engine,
        run_event_log_store=bundle.run_event_log_store,
        skills_service=bundle.skills_service,
        memory_service=bundle.memory_service,
        memory_manager=bundle.memory_manager,
        extensions_service=bundle.extensions_service,
        capability_assembly_service=bundle.capability_assembly_service,
        upload_service=bundle.upload_service,
        subagent_service=bundle.subagent_service,
        process_service=bundle.process_service,
        scheduled_task_service=bundle.scheduled_task_service,
        tracing_service=bundle.tracing_service,
        system_event_bus=bundle.system_event_bus,
        stream_run_manager=BackgroundRunStreamManager(),
    )
