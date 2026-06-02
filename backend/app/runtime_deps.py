from __future__ import annotations

import os
import platform
import re
import time
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from threading import Lock
from uuid import uuid4

from anvil import (
    ArchiveSearchResult,
    build_default_config_layers,
    build_env_bootstrap_config_layers_from_env,
    CapabilityAssemblyService,
    ConfigLayer,
    ConfigResolutionResult,
    ConfigService,
    ExtensionsService,
    FileMemoryStore,
    HeuristicMemoryUpdater,
    MemoryManager,
    MemoryService,
    MemoryPlatformConfig,
    PathBridge,
    PathService,
    ProcessService,
    RunEngine,
    RunRequest,
    RuntimeFeatureSet,
    ScheduledTask,
    ScheduledTaskExecution,
    ScheduledTaskService,
    ScheduledTaskStore,
    TerminalBackendKind,
    TerminalBackendMount,
    TerminalBackendSpec,
    resolve_feature_set,
    SkillsService,
    SubagentService,
    SqliteSubagentRegistry,
    TracingService,
    UploadService,
    create_checkpointer,
    create_store,
    default_anvil_config_dir,
    get_repo_root,
    resolve_config_path,
    resolve_workspace_root,
)
from anvil.agents import make_lead_agent
from anvil.agents.factory import clone_chat_model_override_for_subagent
from anvil.agents import ThreadLifecycleStatus, ThreadMetadataView, ThreadState
from anvil.config import ModelRouteRequest, RequiredModelCapabilities, resolve_model_route
from anvil.memory import DebouncedMemoryQueue
from anvil.processes.backends import create_terminal_backend_adapter
from anvil.runtime.checkpointers import Checkpointer, CheckpointerBackend
from anvil.runtime.runs import JsonlRunEventLogStore, RunEventLogStore
from anvil.runtime.serialization import serialize_messages
from anvil.runtime.store import Store, StoreBackend
from anvil.runtime.system_events import SystemEventBus
from anvil.runtime.thread_service import ThreadRuntimeService
from anvil.sandbox import create_sandbox_provider
from anvil.subagents import InheritedThreadPathService, SubagentEvent, SubagentEventBroker, SubagentResult, SubagentTaskStatus


def close_runtime_services(steps: list[tuple[str, Callable[[], None]]]) -> None:
    errors: list[Exception] = []
    for name, close_step in steps:
        try:
            close_step()
        except Exception as exc:  # noqa: BLE001
            error = RuntimeError(f"{name} close failed")
            error.__cause__ = exc
            errors.append(error)
    if errors:
        raise ExceptionGroup("runtime dependency close failures", errors)


@dataclass
class RuntimeDepsBundle:
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
    scheduled_task_service: ScheduledTaskService
    tracing_service: TracingService
    system_event_bus: SystemEventBus

    def close(self) -> None:
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

    def _close_scheduled_task_service(self) -> None:
        close = getattr(self.scheduled_task_service, "close", None)
        if callable(close):
            close()

    def _wait_for_run_engine_background_tasks(self) -> None:
        wait = getattr(self.run_engine, "wait_for_background_tasks", None)
        if callable(wait):
            wait(timeout_seconds=10)


class _SubagentEphemeralStore:
    backend = StoreBackend.IN_MEMORY
    is_durable = False

    def put_thread_metadata(self, metadata: ThreadMetadataView) -> ThreadMetadataView:
        return metadata

    def get_thread_metadata(self, thread_id: str) -> ThreadMetadataView | None:
        return None

    def delete_thread(self, thread_id: str) -> None:
        return None

    def list_threads(self) -> list[ThreadMetadataView]:
        return []

    def reset(self) -> None:
        return None

    def close(self) -> None:
        return None


class ConfigCoordinator:
    def __init__(self, *, config_service: ConfigService, config_layers: list[ConfigLayer], auto_reload: bool) -> None:
        self.config_service = config_service
        self.config_layers = config_layers
        self.auto_reload = auto_reload
        self._lock = Lock()
        self._config_path = None
        self._config_mtime = None
        self._last_poll_at = 0.0
        self._poll_interval_seconds = 1.0
        self._current_snapshot = self.config_service.resolve(self._rebuild_layers())
        self._snapshot_hash = self._current_snapshot.fingerprint
        self._poll_interval_seconds = max(
            float(self._current_snapshot.effective_config.config_freshness.watch_interval_seconds),
            1.0,
        )

    def resolve(self) -> ConfigResolutionResult:
        return self.get_current()

    def get_current(self) -> ConfigResolutionResult:
        with self._lock:
            return self._current_snapshot

    def refresh_if_needed(self) -> tuple[ConfigResolutionResult, bool]:
        with self._lock:
            changed = self._config_changed()
            if not changed:
                return self._current_snapshot, False
            return self._reload_unlocked(force=True), True

    def reload(self) -> ConfigResolutionResult:
        with self._lock:
            return self._reload_unlocked(force=True)

    def _config_changed(self) -> bool:
        if not self.auto_reload:
            return False
        now = time.monotonic()
        if (now - self._last_poll_at) < self._poll_interval_seconds:
            return False
        self._last_poll_at = now
        current_path = resolve_config_path()
        current_mtime = current_path.stat().st_mtime if current_path and current_path.exists() else None
        changed = current_path != self._config_path or current_mtime != self._config_mtime
        self._config_path = current_path
        self._config_mtime = current_mtime
        return changed

    def _rebuild_layers(self, force: bool = False) -> list[ConfigLayer]:
        if force and self.auto_reload:
            self.config_layers = build_default_config_layers()
        return self.config_layers

    def _reload_unlocked(self, *, force: bool) -> ConfigResolutionResult:
        layers = self._rebuild_layers(force=force)
        self._current_snapshot = self.config_service.resolve(layers)
        self._snapshot_hash = self._current_snapshot.fingerprint
        self._poll_interval_seconds = max(
            float(self._current_snapshot.effective_config.config_freshness.watch_interval_seconds),
            1.0,
        )
        return self._current_snapshot
def build_default_config_layers_from_env() -> list[ConfigLayer]:
    return build_env_bootstrap_config_layers_from_env()


def build_path_bridges_from_env() -> list[PathBridge]:
    raw = (os.getenv("ANVIL_PATH_BRIDGES") or "").strip()
    if not raw:
        return []

    bridges: list[PathBridge] = []
    for chunk in raw.split(";"):
        entry = chunk.strip()
        if not entry:
            continue
        parts = entry.split("|")
        if len(parts) != 3:
            raise ValueError(
                "ANVIL_PATH_BRIDGES entries must use 'alias|display_root|actual_root' format"
            )
        alias, display_root, actual_root = (part.strip() for part in parts)
        bridges.append(
            PathBridge.create(
                alias=alias,
                display_root=display_root,
                actual_root=actual_root,
            )
        )
    return bridges


def build_path_bridges_from_config(effective_config) -> list[PathBridge]:
    workspace_config = getattr(effective_config, "workspace", None)
    bridge_configs = getattr(workspace_config, "path_bridges", ()) or ()
    bridges: list[PathBridge] = []
    for item in bridge_configs:
        if not getattr(item, "enabled", True):
            continue
        actual_root = getattr(item, "actual_root", None) or getattr(item, "display_root", None)
        if not actual_root:
            raise ValueError(f"workspace.path_bridges entry '{item.alias}' must define display_root or actual_root")
        bridges.append(
            PathBridge.create(
                alias=str(item.alias),
                display_root=str(item.display_root),
                actual_root=str(actual_root),
            )
        )
    return bridges


def build_auto_host_path_bridges(effective_config) -> list[PathBridge]:
    workspace_config = getattr(effective_config, "workspace", None)
    if not bool(getattr(workspace_config, "auto_host_drives", True)):
        return []
    requested_letters = [
        str(item).strip().rstrip(":\\/").upper()
        for item in (getattr(workspace_config, "auto_host_drive_letters", None) or [])
        if str(item).strip()
    ]
    bridges: list[PathBridge] = []
    for alias, display_root, actual_root in _candidate_auto_host_roots(requested_letters):
        try:
            root_path = Path(actual_root).expanduser()
            if _is_internal_runtime_mount(root_path):
                continue
            if not root_path.exists() or not root_path.is_dir():
                continue
            bridges.append(
                PathBridge.create(
                    alias=alias,
                    display_root=display_root,
                    actual_root=str(root_path.resolve()),
                )
            )
        except (OSError, ValueError):
            continue
    return bridges


def _candidate_auto_host_roots(requested_windows_letters: list[str]) -> list[tuple[str, str, str]]:
    system = platform.system().lower()
    docker_host_roots = _candidate_docker_host_bridge_roots(requested_windows_letters)
    if docker_host_roots:
        return docker_host_roots
    try:
        repo_parent = str(get_repo_root().parent)
    except Exception:
        repo_parent = str(Path.cwd())
    if system == "windows":
        letters = requested_windows_letters or [chr(code) for code in range(ord("A"), ord("Z") + 1)]
        return [(f"{letter.lower()}_drive", f"{letter}:", f"{letter}:\\") for letter in letters if len(letter) == 1 and letter.isalpha()]
    if system == "darwin":
        candidates = [
            ("home", str(Path.home()), str(Path.home())),
            ("desktop", str(Path.home() / "Desktop"), str(Path.home() / "Desktop")),
            ("documents", str(Path.home() / "Documents"), str(Path.home() / "Documents")),
            ("downloads", str(Path.home() / "Downloads"), str(Path.home() / "Downloads")),
        ]
        volumes_root = Path("/Volumes")
        if volumes_root.exists():
            for volume in sorted(volumes_root.iterdir(), key=lambda item: item.name.casefold()):
                candidates.append((f"volume_{_bridge_alias_token(volume.name)}", str(volume), str(volume)))
        return candidates
    candidates = [
        ("home", str(Path.home()), str(Path.home())),
    ]
    if _is_safe_auto_host_root(Path(repo_parent)):
        candidates.append(("workspace_parent", repo_parent, repo_parent))
    for mount_root in (Path("/mnt"), Path("/media"), Path("/Volumes")):
        if not mount_root.exists():
            continue
        try:
            for child in sorted(mount_root.iterdir(), key=lambda item: item.name.casefold()):
                if _is_safe_auto_host_root(child):
                    candidates.append((f"{mount_root.name}_{_bridge_alias_token(child.name)}", str(child), str(child)))
        except OSError:
            continue
    return candidates


def _candidate_docker_host_bridge_roots(requested_windows_letters: list[str]) -> list[tuple[str, str, str]]:
    host_root = Path(os.getenv("ANVIL_DOCKER_HOST_ROOT") or "/mnt/host")
    if not host_root.exists():
        return []
    requested = {item.upper() for item in requested_windows_letters}
    candidates: list[tuple[str, str, str]] = []
    try:
        children = sorted(host_root.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return []
    for child in children:
        if not child.is_dir():
            continue
        name = child.name
        windows_drive = re.fullmatch(r"([A-Za-z])_drive", name)
        if windows_drive:
            letter = windows_drive.group(1).upper()
            if requested and letter not in requested:
                continue
            candidates.append((f"{letter.lower()}_drive", f"{letter}:", str(child)))
            continue
        if name in {"workspace", "app", "user-data", "worker-data"}:
            continue
        candidates.append((_bridge_alias_token(name), str(child), str(child)))
    return candidates


def _is_internal_runtime_mount(path: Path) -> bool:
    candidates = {path.expanduser().as_posix().rstrip("/")}
    try:
        candidates.add(path.expanduser().resolve().as_posix().rstrip("/"))
    except OSError:
        pass
    return any(_is_internal_runtime_mount_text(text) for text in candidates)


def _is_internal_runtime_mount_text(text: str) -> bool:
    return (
        text == "/app"
        or text.startswith("/app/")
        or text == "/mnt/user-data"
        or text.startswith("/mnt/user-data/")
        or text == "/mnt/worker-data"
        or text.startswith("/mnt/worker-data/")
        or text == "/mnt/host-workspaces"
        or text.startswith("/mnt/host-workspaces/")
    )


def _is_safe_auto_host_root(path: Path) -> bool:
    raw_text = path.expanduser().as_posix().rstrip("/")
    if raw_text == "/" or _is_internal_runtime_mount_text(raw_text) or raw_text in {"/mnt", "/media", "/Volumes"}:
        return False
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    if resolved.parent == resolved:
        return False
    text = resolved.as_posix().rstrip("/")
    if text in {"", "/"}:
        return False
    if _is_internal_runtime_mount(resolved):
        return False
    for internal_parent in (Path("/mnt"), Path("/media"), Path("/Volumes")):
        try:
            if resolved == internal_parent.resolve():
                return False
        except OSError:
            continue
    return True


def _bridge_alias_token(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "_" for char in value)
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized[:48] or "root"


def build_path_bridges(effective_config) -> list[PathBridge]:
    by_alias: dict[str, PathBridge] = {}
    for bridge in build_path_bridges_from_config(effective_config):
        by_alias[bridge.alias] = bridge
    for bridge in build_path_bridges_from_env():
        by_alias[bridge.alias] = bridge
    for bridge in build_auto_host_path_bridges(effective_config):
        by_alias.setdefault(bridge.alias, bridge)
    return list(by_alias.values())


def resolve_terminal_backend_settings(effective_config, path_service: PathService | None = None) -> tuple[TerminalBackendSpec, str | None]:
    terminal = effective_config.terminal
    backend_id = terminal.active_backend or "local"
    backend_config = terminal.backends.get(backend_id) or terminal.backends.get("local")
    if backend_config is None:
        return TerminalBackendSpec(), terminal.logs_dir
    kind_value = str(backend_config.kind or backend_id).lower()
    try:
        kind = TerminalBackendKind(kind_value)
    except ValueError as exc:
        raise ValueError(f"unsupported terminal backend kind '{kind_value}' for backend '{backend_id}'") from exc
    if not backend_config.enabled:
        raise ValueError(f"terminal backend '{backend_id}' is disabled")
    notes = list(backend_config.notes)
    spec = TerminalBackendSpec(
        kind=kind,
        backend_id=backend_id,
        label=backend_config.label,
        command_prefix=list(backend_config.command_prefix),
        default_cwd=backend_config.default_cwd,
        env=dict(backend_config.env),
        env_passthrough=list(backend_config.env_passthrough),
        env_prefix_passthrough=list(backend_config.env_prefix_passthrough),
        timeout_seconds=backend_config.timeout_seconds,
        lifetime_seconds=backend_config.lifetime_seconds,
        image=backend_config.image,
        host=backend_config.host,
        username=backend_config.username,
        sandbox_id=backend_config.sandbox_id,
        app=backend_config.app,
        runtime=backend_config.runtime,
        working_dir=backend_config.working_dir,
        resource_limits=dict(backend_config.resource_limits),
        sync=dict(backend_config.sync),
        mounts=[
            TerminalBackendMount(
                host_path=str(mount.host_path),
                container_path=str(mount.container_path),
                read_only=bool(mount.read_only),
            )
            for mount in backend_config.mounts
        ],
        notes=notes,
    )
    return spec, terminal.logs_dir


def build_runtime_deps_bundle(
    *,
    config_layers: list[ConfigLayer] | None = None,
    feature_set: RuntimeFeatureSet | None = None,
    thread_root: Path | None = None,
    state_db_path: Path | None = None,
    chat_model_override: Any | None = None,
    app_state_root: Path | None = None,
    subagent_service: SubagentService | None = None,
    tracing_service: TracingService | None = None,
) -> RuntimeDepsBundle:
    auto_reload_config = config_layers is None
    config_layers = config_layers or build_default_config_layers()
    requested_feature_set = feature_set or RuntimeFeatureSet(title=True)
    config_service = ConfigService()
    config_coordinator = ConfigCoordinator(
        config_service=config_service,
        config_layers=config_layers,
        auto_reload=auto_reload_config,
    )
    config_result = config_coordinator.get_current()
    feature_set = resolve_feature_set(requested_feature_set, config_result.effective_config)
    create_sandbox_provider(config_result.effective_config)

    repo_root = Path(__file__).resolve().parents[2]
    configured_anvil_home = default_anvil_config_dir(repo_root)
    if app_state_root is None:
        if thread_root is not None:
            app_state_root = Path(thread_root).resolve().parent
        elif state_db_path is not None:
            app_state_root = Path(state_db_path).resolve().parent
        else:
            app_state_root = configured_anvil_home
    app_state_root = Path(app_state_root).resolve()
    app_state_root.mkdir(parents=True, exist_ok=True)

    thread_root = (Path(thread_root).resolve() if thread_root is not None else (app_state_root / "sessions").resolve())
    thread_root.mkdir(parents=True, exist_ok=True)
    state_db_path = (Path(state_db_path).resolve() if state_db_path is not None else (app_state_root / "gateway.sqlite3").resolve())
    subagent_db_path = state_db_path.with_name(f"{state_db_path.stem}-subagents.sqlite3")
    process_db_path = state_db_path.with_name(f"{state_db_path.stem}-processes.sqlite3")
    configured_workspace_root = resolve_workspace_root(repo_root=repo_root)
    configured_workspace_mode = str(getattr(config_result.effective_config.workspace, "mode", "thread") or "thread")
    path_service = PathService(
        thread_root,
        path_bridges=build_path_bridges(config_result.effective_config),
        default_workspace_root=configured_workspace_root,
        default_workspace_mode=configured_workspace_mode,
    )
    terminal_backend_spec, terminal_logs_dir = resolve_terminal_backend_settings(config_result.effective_config, path_service)
    process_logs_dir = (
        Path(terminal_logs_dir).expanduser().resolve()
        if terminal_logs_dir
        else app_state_root / f"{state_db_path.stem}-process-logs"
    )
    checkpointer = create_checkpointer(CheckpointerBackend.SQLITE, sqlite_path=state_db_path)
    store = create_store(StoreBackend.SQLITE, sqlite_path=state_db_path)
    thread_service = ThreadRuntimeService(
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
    )

    memory_store_path = config_result.effective_config.memory.store_path or str(app_state_root / "memories" / "legacy")
    legacy_memory_runtime_enabled = (
        config_result.effective_config.memory.enabled
        and not config_result.effective_config.memory_platform.enabled
    )
    memory_service = None
    if legacy_memory_runtime_enabled:
        memory_service = MemoryService(
            store=FileMemoryStore(memory_store_path),
            queue=DebouncedMemoryQueue(),
            updater=HeuristicMemoryUpdater(max_facts=config_result.effective_config.memory.max_facts),
            max_facts=config_result.effective_config.memory.max_facts,
            injection_token_budget=config_result.effective_config.memory.injection_token_budget,
        )
    memory_platform_config = config_result.effective_config.memory_platform
    if not memory_platform_config.enabled and config_result.effective_config.memory.enabled:
        memory_platform_config = MemoryPlatformConfig.from_legacy_memory(config_result.effective_config.memory)
    memory_manager = MemoryManager.from_config(
        config=memory_platform_config,
        base_path=app_state_root / "memories",
        legacy_store_path=memory_store_path if config_result.effective_config.memory.enabled else None,
        effective_config=config_result.effective_config,
    )
    skills_service = SkillsService()
    extensions_service = ExtensionsService()
    tracing_service = tracing_service or TracingService.from_env_and_config(
        config_result.effective_config.additional_settings.get("tracing")
    )
    if not tracing_service.enabled() or tracing_service.settings.fail_open:
        tracing_service.suppress_env_auto_tracing()
    subagent_event_broker = SubagentEventBroker()

    def persist_subagent_event(event: SubagentEvent) -> None:
        state = checkpointer.get_thread_state(event.parent_thread_id)
        if state is None:
            return
        updated = state.model_copy(deep=True)
        updated.durable_subagent_job_history.append(event.model_dump(mode="json"))
        if len(updated.durable_subagent_job_history) > 200:
            updated.durable_subagent_job_history = updated.durable_subagent_job_history[-200:]
        updated.lifecycle.updated_at = datetime.now(timezone.utc)
        checkpointer.put_thread_state(updated)
        store.put_thread_metadata(ThreadMetadataView.from_thread_state(updated))

    def build_subagent_runner_factory():
        def _factory(*, task, prompt, config_result, allowed_tool_names, execution_mode=None):
            def _runner() -> SubagentResult:
                parent_state = checkpointer.get_thread_state(task.parent_thread_id)
                if parent_state is None:
                    return SubagentResult(
                        task_id=task.task_id,
                        status=SubagentTaskStatus.FAILED,
                        summary="",
                        error=f"parent thread '{task.parent_thread_id}' was not found",
                        child_thread_id=task.child_thread_id,
                        child_run_id=task.child_run_id,
                        trace_id=task.trace_id,
                    )

                child_thread_id = task.child_thread_id or task.task_id
                child_path_service = InheritedThreadPathService(
                    base=path_service,
                    child_thread_id=child_thread_id,
                    parent_thread_id=task.parent_thread_id,
                )
                child_store = _SubagentEphemeralStore()
                child_execution_mode = execution_mode or parent_state.execution.execution_mode

                if checkpointer.get_thread_state(child_thread_id) is None:
                    child_state = ThreadState(
                        identity={"thread_id": child_thread_id, "run_id": task.child_run_id},
                        lifecycle={"status": ThreadLifecycleStatus.READY},
                        execution={
                            "execution_mode": child_execution_mode,
                            "selected_model": parent_state.execution.selected_model,
                            "selected_profile": parent_state.execution.selected_profile,
                            "selected_reasoning_effort": parent_state.execution.selected_reasoning_effort,
                            "is_plan_mode": parent_state.execution.is_plan_mode,
                        },
                        thread_data=child_path_service.bootstrap_thread_paths(child_thread_id).model_dump(),
                        artifacts={"uploaded_files": list(parent_state.artifacts.uploaded_files)},
                        conversation={
                            "title": parent_state.conversation.title,
                            "summary": parent_state.conversation.summary,
                        },
                        memory={
                            "memory_namespace": parent_state.memory.memory_namespace,
                            "injected_memory_snapshot_id": parent_state.memory.injected_memory_snapshot_id,
                        },
                    )
                    checkpointer.put_thread_state(child_state)

                child_feature_set = feature_set.model_copy(
                    update={
                        "subagents": False,
                        "subagent_limit": False,
                        "memory": False,
                        "memory_prefetch": False,
                        "memory_capture": False,
                        "title": False,
                        "skills": False,
                        "capability_mentions": False,
                        "extensions": False,
                        "dynamic_mcp_refresh": False,
                    }
                )
                session = run_engine.run_stream(
                    RunRequest(
                        thread_id=child_thread_id,
                        user_message=prompt,
                        config_layers=config_layers,
                        config_result=config_result,
                        path_service=child_path_service,
                        checkpointer=checkpointer,
                        store=child_store,
                        run_id=task.child_run_id,
                        feature_set=child_feature_set,
                        execution_mode=child_execution_mode,
                        selected_model=parent_state.execution.selected_model,
                        selected_reasoning_effort=parent_state.execution.selected_reasoning_effort,
                        profile=parent_state.execution.selected_profile,
                        request_context=f"Delegated task: {prompt}",
                        approval_context=None,
                        upload_context=None,
                        is_plan_mode=parent_state.execution.is_plan_mode,
                        promoted_capabilities=tuple(allowed_tool_names),
                        parent_visible_tool_names=tuple(allowed_tool_names),
                        subagent_service=None,
                        process_service=process_service,
                        scheduled_task_service=scheduled_task_service,
                        memory_manager=memory_manager,
                        skills_service=skills_service,
                        extensions_service=extensions_service,
                        capability_assembly_service=capability_assembly_service,
                        tracing_service=tracing_service,
                        recent_upload_filenames=(),
                        chat_model_override=clone_chat_model_override_for_subagent(chat_model_override),
                        run_event_log_store=run_event_log_store,
                    )
                )

                ai_buffers: dict[str, dict[str, str]] = {}
                show_child_reasoning = bool(config_result.effective_config.subagents.show_child_reasoning)
                for event in session:
                    if event.event == "message_opened" and event.data.get("role") == "ai":
                        ai_buffers[str(event.data.get("message_id"))] = {"content": "", "reasoning": ""}
                    elif event.event == "message_delta":
                        message_id = str(event.data.get("message_id"))
                        if message_id in ai_buffers:
                            ai_buffers[message_id]["content"] += str(event.data.get("delta") or "")
                    elif event.event == "reasoning_delta":
                        message_id = str(event.data.get("message_id"))
                        if message_id in ai_buffers:
                            ai_buffers[message_id]["reasoning"] += str(event.data.get("delta") or "")
                    elif event.event == "message_completed":
                        message_id = str(event.data.get("message_id"))
                        payload = ai_buffers.pop(message_id, None)
                        if show_child_reasoning and payload is not None and (payload["content"] or payload["reasoning"]):
                            subagent_event = SubagentEvent(
                                job_id=task.task_id,
                                parent_thread_id=task.parent_thread_id,
                                parent_run_id=task.parent_run_id,
                                event_type="model_response",
                                payload={
                                    "status": "completed",
                                    "child_thread_id": child_thread_id,
                                    "child_run_id": task.child_run_id,
                                    "message_id": message_id,
                                    "content": payload["content"],
                                    "reasoning": payload["reasoning"],
                                },
                            )
                            subagent_event_broker.publish(subagent_event)
                            persist_subagent_event(subagent_event)
                    elif event.event == "tool_call_started":
                        subagent_event = SubagentEvent(
                            job_id=task.task_id,
                            parent_thread_id=task.parent_thread_id,
                            parent_run_id=task.parent_run_id,
                            event_type="tool_call",
                            payload={
                                "status": event.data.get("status"),
                                "child_thread_id": child_thread_id,
                                "child_run_id": task.child_run_id,
                                "tool_name": event.data.get("name"),
                                "display_name": event.data.get("display_name"),
                                "args": event.data.get("args", {}),
                                "duration_ms": event.data.get("duration_ms"),
                            },
                        )
                        subagent_event_broker.publish(subagent_event)
                        persist_subagent_event(subagent_event)
                    elif event.event == "tool_call_completed":
                        subagent_event = SubagentEvent(
                            job_id=task.task_id,
                            parent_thread_id=task.parent_thread_id,
                            parent_run_id=task.parent_run_id,
                            event_type="tool_result",
                            payload={
                                "status": event.data.get("status"),
                                "child_thread_id": child_thread_id,
                                "child_run_id": task.child_run_id,
                                "tool_name": event.data.get("name"),
                                "display_name": event.data.get("display_name"),
                                "args": event.data.get("args", {}),
                                "result_text": event.data.get("result_text"),
                                "duration_ms": event.data.get("duration_ms"),
                            },
                        )
                        subagent_event_broker.publish(subagent_event)
                        persist_subagent_event(subagent_event)

                final_result = session.final_result
                if final_result is None:
                    return SubagentResult(
                        task_id=task.task_id,
                        status=SubagentTaskStatus.FAILED,
                        summary="",
                        error="subagent finished without a final result",
                        child_thread_id=child_thread_id,
                        child_run_id=task.child_run_id,
                        trace_id=task.trace_id,
                    )

                child_state = final_result.thread_state
                child_status = SubagentTaskStatus.COMPLETED
                approval_payload = None
                error = child_state.lifecycle.last_error
                if child_state.lifecycle.status is ThreadLifecycleStatus.AWAITING_APPROVAL:
                    child_status = SubagentTaskStatus.FAILED
                    approval_payload = {
                        "pending_approval": str(child_state.approvals.pending_approval) if child_state.approvals.pending_approval is not None else None,
                        "approval_request": child_state.approvals.approval_request.model_dump(mode="json") if child_state.approvals.approval_request is not None else None,
                    }
                elif child_state.lifecycle.status is ThreadLifecycleStatus.AWAITING_CLARIFICATION:
                    child_status = SubagentTaskStatus.FAILED
                    error = child_state.lifecycle.last_error or "subagent requested clarification"
                elif child_state.lifecycle.status is ThreadLifecycleStatus.FAILED:
                    child_status = SubagentTaskStatus.FAILED
                elif child_state.lifecycle.status is ThreadLifecycleStatus.CANCELLED:
                    child_status = SubagentTaskStatus.CANCELLED
                elif child_state.lifecycle.status is ThreadLifecycleStatus.TIMED_OUT:
                    child_status = SubagentTaskStatus.TIMED_OUT
                elif child_state.lifecycle.status is ThreadLifecycleStatus.INTERRUPTED:
                    child_status = SubagentTaskStatus.INTERRUPTED

                summary = ""
                for message in reversed(child_state.conversation.messages):
                    if message.get("role") in {"ai", "assistant"} and isinstance(message.get("content"), str) and str(message.get("content")).strip():
                        summary = str(message.get("content"))
                        break
                if not summary:
                    summary = error or f"subagent {child_status.value}"

                artifacts = [
                    {
                        "kind": "output",
                        "label": relative_path,
                        "artifact_url": f"/threads/{child_thread_id}/artifacts/outputs/{relative_path}",
                        "virtual_path": f"/mnt/user-data/outputs/{relative_path}",
                    }
                    for relative_path in child_state.artifacts.output_artifacts
                ]
                tool_args_by_id: dict[str, dict[str, object]] = {}
                for message in child_state.conversation.messages:
                    if message.get("role") not in {"ai", "assistant"}:
                        continue
                    tool_calls = message.get("tool_calls")
                    if not isinstance(tool_calls, list):
                        continue
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            continue
                        tool_call_id = tool_call.get("id")
                        args = tool_call.get("args")
                        if tool_call_id is None or not isinstance(args, dict):
                            continue
                        tool_args_by_id[str(tool_call_id)] = {str(key): value for key, value in args.items()}
                recent_tool_activity = []
                for item in child_state.execution.recent_tool_activity:
                    payload = item.model_dump(mode="json")
                    if (not payload.get("args")) and payload.get("tool_call_id") and str(payload["tool_call_id"]) in tool_args_by_id:
                        payload["args"] = tool_args_by_id[str(payload["tool_call_id"])]
                    recent_tool_activity.append(payload)

                subagent_result = SubagentResult(
                    task_id=task.task_id,
                    status=child_status,
                    summary=summary,
                    child_thread_id=child_thread_id,
                    child_run_id=child_state.identity.run_id,
                    artifacts=tuple(artifacts),
                    messages=tuple(child_state.conversation.messages),
                    recent_tool_activity=tuple(recent_tool_activity),
                    approval_payload=approval_payload,
                    error=error,
                    started_at=child_state.lifecycle.created_at,
                    completed_at=child_state.lifecycle.completed_at,
                    trace_id=task.trace_id,
                )
                try:
                    memory_manager.record_delegation_result(
                        parent_thread_id=task.parent_thread_id,
                        task={
                            "task_id": task.task_id,
                            "prompt": prompt,
                            "child_thread_id": child_thread_id,
                            "child_run_id": task.child_run_id,
                        },
                        result=subagent_result.model_dump(mode="json"),
                        status=child_status.value,
                    )
                except Exception:
                    pass
                return subagent_result

            return _runner

        return _factory

    subagent_service = subagent_service or SubagentService(
        registry=SqliteSubagentRegistry(subagent_db_path),
        tracing_service=tracing_service,
        event_broker=subagent_event_broker,
        event_persister=persist_subagent_event,
    )
    process_service = ProcessService(
        sqlite_path=process_db_path,
        logs_dir=process_logs_dir,
        backend=terminal_backend_spec.kind,
        backend_id=terminal_backend_spec.backend_id,
        backend_label=terminal_backend_spec.label,
        backend_notes=terminal_backend_spec.notes,
        backend_adapter=create_terminal_backend_adapter(terminal_backend_spec, path_service=path_service),
    )
    system_event_bus = SystemEventBus()
    upload_service = UploadService(
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        uploads_config=config_result.effective_config.uploads,
    )
    run_engine = RunEngine(config_service=config_service)
    run_engine._chat_model_override = chat_model_override  # testing-only hook
    run_event_log_store = JsonlRunEventLogStore(app_state_root / "run-events" / "events.jsonl")
    if subagent_service.default_runner_factory is None:
        subagent_service.default_runner_factory = build_subagent_runner_factory()

    scheduled_tasks_config = config_result.effective_config.scheduled_tasks
    scheduled_tasks_state_path = (
        Path(scheduled_tasks_config.state_path).expanduser().resolve()
        if scheduled_tasks_config.state_path
        else app_state_root / "cron" / "tasks.json"
    )

    def execute_scheduled_task(task: ScheduledTask) -> ScheduledTaskExecution:
        thread_id = task.thread_id or f"scheduled-{task.task_id}"
        if checkpointer.get_thread_state(thread_id) is None:
            thread_service.create_thread(thread_id=thread_id)
        started_at = datetime.now(timezone.utc)
        execution_id = f"exec-{uuid4().hex[:12]}"
        prompt = (
            "[SYSTEM: You are running as a scheduled automation. "
            "Complete the task once, report only meaningful changes or results, "
            "and do not create or modify scheduled tasks unless the user explicitly asked for that in the saved task.]\n\n"
            f"{task.prompt}"
        )
        selected_model = task.selected_model or scheduled_tasks_config.default_model
        if not selected_model:
            with contextlib.suppress(Exception):
                route = resolve_model_route(
                    config_result.effective_config,
                    ModelRouteRequest(
                        subsystem="scheduled_automation",
                        required_capabilities=RequiredModelCapabilities(tool_calling=True),
                    ),
                )
                selected_model = route.model_name
        result = run_engine.run(
            RunRequest(
                thread_id=thread_id,
                user_message=prompt,
                config_layers=config_layers,
                config_result=config_result,
                path_service=path_service,
                checkpointer=checkpointer,
                store=store,
                feature_set=feature_set,
                execution_mode=task.execution_mode,
                selected_model=selected_model,
                selected_reasoning_effort=task.selected_reasoning_effort,
                profile=task.selected_profile or scheduled_tasks_config.default_profile,
                request_context=f"Scheduled automation task_id={task.task_id} name={task.name}",
                promoted_capabilities=task.promoted_capabilities,
                subagent_service=subagent_service,
                process_service=process_service,
                scheduled_task_service=scheduled_task_service,
                memory_manager=memory_manager,
                skills_service=skills_service,
                extensions_service=extensions_service,
                capability_assembly_service=capability_assembly_service,
                tracing_service=tracing_service,
                chat_model_override=getattr(run_engine, "_chat_model_override", None),
                run_event_log_store=run_event_log_store,
            )
        )
        completed_at = datetime.now(timezone.utc)
        state = result.thread_state
        summary = ""
        for message in reversed(state.conversation.messages):
            if message.get("role") in {"ai", "assistant"} and isinstance(message.get("content"), str) and str(message.get("content")).strip():
                summary = str(message.get("content")).strip()
                break
        status_value = "completed" if state.lifecycle.status.value in {"ready", "completed"} else state.lifecycle.status.value
        return ScheduledTaskExecution(
            execution_id=execution_id,
            task_id=task.task_id,
            thread_id=thread_id,
            run_id=state.identity.run_id,
            status=status_value,
            started_at=started_at,
            completed_at=completed_at,
            summary=summary[:4000],
            error=state.lifecycle.last_error,
            metadata={
                "thread_status": state.lifecycle.status.value,
                "artifact_count": len(state.artifacts.output_artifacts),
            },
        )

    scheduled_task_service = ScheduledTaskService(
        store=ScheduledTaskStore(scheduled_tasks_state_path),
        executor=execute_scheduled_task,
        enabled=bool(scheduled_tasks_config.enabled),
        prompt_safety_scan_enabled=bool(scheduled_tasks_config.prompt_safety_scan_enabled),
    )
    capability_assembly_service = CapabilityAssemblyService(
        skills_service=skills_service,
        extensions_service=extensions_service,
        subagent_service=subagent_service,
        process_service=process_service,
        scheduled_task_service=scheduled_task_service,
    )

    return RuntimeDepsBundle(
        config_layers=config_layers,
        config_service=config_service,
        config_result=config_result,
        config_coordinator=config_coordinator,
        effective_config=config_result.effective_config,
        feature_set=feature_set,
        harness_factory=make_lead_agent,
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        thread_service=thread_service,
        run_engine=run_engine,
        run_event_log_store=run_event_log_store,
        skills_service=skills_service,
        memory_service=memory_service,
        memory_manager=memory_manager,
        extensions_service=extensions_service,
        capability_assembly_service=capability_assembly_service,
        upload_service=upload_service,
        subagent_service=subagent_service,
        process_service=process_service,
        scheduled_task_service=scheduled_task_service,
        tracing_service=tracing_service,
        system_event_bus=system_event_bus,
    )
