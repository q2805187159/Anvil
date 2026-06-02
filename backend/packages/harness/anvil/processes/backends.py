from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import shutil
import subprocess
from typing import Any, Protocol

from .contracts import TerminalBackendCapabilities, TerminalBackendKind, TerminalBackendMount, TerminalBackendSpec


@dataclass(frozen=True)
class ProcessLaunch:
    popen_args: str | list[str]
    display_command: str
    cwd: str | None
    env: dict[str, str]
    shell: bool
    executable: str | None = None


class TerminalBackendAdapter(Protocol):
    spec: TerminalBackendSpec

    def capabilities(self) -> TerminalBackendCapabilities: ...

    def prepare_launch(self, *, thread_id: str, command: str, cwd: str, env: dict[str, Any]) -> ProcessLaunch: ...


class LocalTerminalBackendAdapter:
    def __init__(self, spec: TerminalBackendSpec) -> None:
        self.spec = spec

    def capabilities(self) -> TerminalBackendCapabilities:
        shell_name = "cmd.exe" if os.name == "nt" else "sh"
        return _base_capabilities(
            self.spec,
            launch_mode="local_process",
            workspace_sync="local",
            required_executables=[shell_name],
        )

    def prepare_launch(self, *, thread_id: str, command: str, cwd: str, env: dict[str, Any]) -> ProcessLaunch:
        process_env = _process_env({**os.environ, **self.spec.env, **env})
        translated_command = _prefixed_command(self.spec.command_prefix, command)
        return ProcessLaunch(
            popen_args=translated_command,
            display_command=translated_command,
            cwd=str(Path(cwd)),
            env=process_env,
            shell=True,
            executable=_windows_shell_executable(process_env),
        )


class DockerTerminalBackendAdapter:
    def __init__(self, spec: TerminalBackendSpec, path_service: Any | None = None) -> None:
        self.spec = spec
        self.path_service = path_service

    def capabilities(self) -> TerminalBackendCapabilities:
        docker_available = shutil.which("docker") is not None
        notes = list(self.spec.notes)
        if not docker_available:
            notes.append("docker executable is not available on PATH.")
        return _base_capabilities(
            self.spec,
            executable=docker_available,
            launch_mode="docker_run",
            workspace_sync=_workspace_sync_mode(self.spec),
            required_executables=["docker"],
            missing_executables=[] if docker_available else ["docker"],
            notes=notes,
        )

    def prepare_launch(self, *, thread_id: str, command: str, cwd: str, env: dict[str, Any]) -> ProcessLaunch:
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("docker executable is required for docker terminal backend")
        image = self.spec.image or "python:3.12-slim"
        process_env = _process_env({**os.environ, **self.spec.env, **env})
        docker_cwd = self.spec.working_dir or self.spec.default_cwd or cwd or "/mnt/user-data/workspace"
        args = [
            docker,
            "run",
            "--rm",
            "-i",
            "-w",
            docker_cwd,
        ]
        args.extend(_docker_resource_args(self.spec.resource_limits))
        for mount in _merge_mounts([*self.spec.mounts, *_thread_virtual_mounts(self.path_service, thread_id)]):
            args.extend(["-v", _docker_mount_spec(mount)])
        for key, value in _backend_env_vars(process_env, self.spec).items():
            args.extend(["-e", f"{key}={value}"])
        args.extend([image, "sh", "-lc", _prefixed_command(self.spec.command_prefix, command)])
        return ProcessLaunch(
            popen_args=args,
            display_command=shlex.join(args),
            cwd=None,
            env=process_env,
            shell=False,
        )


class SshTerminalBackendAdapter:
    def __init__(self, spec: TerminalBackendSpec) -> None:
        self.spec = spec

    def capabilities(self) -> TerminalBackendCapabilities:
        ssh_available = shutil.which("ssh") is not None
        missing_config = [] if self.spec.host else ["host"]
        notes = list(self.spec.notes)
        if not self.spec.host:
            notes.append("ssh host is not configured.")
        if not ssh_available:
            notes.append("ssh executable is not available on PATH.")
        return _base_capabilities(
            self.spec,
            executable=bool(self.spec.host and ssh_available),
            configured=not missing_config,
            launch_mode="ssh",
            workspace_sync=_workspace_sync_mode(self.spec),
            required_config=["host"],
            missing_config=missing_config,
            required_executables=["ssh"],
            missing_executables=[] if ssh_available else ["ssh"],
            notes=notes,
        )

    def prepare_launch(self, *, thread_id: str, command: str, cwd: str, env: dict[str, Any]) -> ProcessLaunch:
        ssh = shutil.which("ssh")
        if ssh is None:
            raise RuntimeError("ssh executable is required for ssh terminal backend")
        if not self.spec.host:
            raise RuntimeError("ssh terminal backend requires host")
        target = f"{self.spec.username}@{self.spec.host}" if self.spec.username else self.spec.host
        remote_cwd = self.spec.working_dir or self.spec.default_cwd or _default_remote_cwd(cwd)
        remote_command = _remote_command(command=_prefixed_command(self.spec.command_prefix, command), cwd=remote_cwd, env={**self.spec.env, **env}, spec=self.spec)
        args = [ssh, target, remote_command]
        process_env = _process_env(dict(os.environ))
        return ProcessLaunch(
            popen_args=args,
            display_command=shlex.join(args),
            cwd=None,
            env=process_env,
            shell=False,
        )


class SingularityTerminalBackendAdapter:
    def __init__(self, spec: TerminalBackendSpec, path_service: Any | None = None) -> None:
        self.spec = spec
        self.path_service = path_service

    def capabilities(self) -> TerminalBackendCapabilities:
        binary = shutil.which("singularity") or shutil.which("apptainer")
        missing_config = [] if self.spec.image else ["image"]
        notes = list(self.spec.notes)
        if not self.spec.image:
            notes.append("singularity image is not configured.")
        if binary is None:
            notes.append("singularity/apptainer executable is not available on PATH.")
        return _base_capabilities(
            self.spec,
            executable=bool(self.spec.image and binary),
            configured=not missing_config,
            launch_mode="singularity_exec",
            workspace_sync=_workspace_sync_mode(self.spec),
            required_config=["image"],
            missing_config=missing_config,
            required_executables=["singularity|apptainer"],
            missing_executables=[] if binary else ["singularity|apptainer"],
            notes=notes,
        )

    def prepare_launch(self, *, thread_id: str, command: str, cwd: str, env: dict[str, Any]) -> ProcessLaunch:
        binary = shutil.which("singularity") or shutil.which("apptainer")
        if binary is None:
            raise RuntimeError("singularity or apptainer executable is required for singularity terminal backend")
        if not self.spec.image:
            raise RuntimeError("singularity terminal backend requires image")
        process_env = _process_env({**os.environ, **self.spec.env, **env})
        runtime_cwd = self.spec.working_dir or self.spec.default_cwd or cwd or "/mnt/user-data/workspace"
        args = [binary, "exec", "--pwd", runtime_cwd]
        for mount in _merge_mounts([*self.spec.mounts, *_thread_virtual_mounts(self.path_service, thread_id)]):
            args.extend(["--bind", _singularity_mount_spec(mount)])
        args.extend([self.spec.image, "sh", "-lc", _prefixed_command(self.spec.command_prefix, command)])
        return ProcessLaunch(
            popen_args=args,
            display_command=shlex.join(args),
            cwd=None,
            env=process_env,
            shell=False,
        )


class UnsupportedTerminalBackendAdapter:
    def __init__(self, spec: TerminalBackendSpec) -> None:
        self.spec = spec

    def capabilities(self) -> TerminalBackendCapabilities:
        notes = [
            *self.spec.notes,
            f"{self.spec.kind.value} terminal backend is configured but its execution adapter is not installed.",
        ]
        return _base_capabilities(self.spec, executable=False, notes=notes)

    def prepare_launch(self, *, thread_id: str, command: str, cwd: str, env: dict[str, Any]) -> ProcessLaunch:
        raise RuntimeError(f"{self.spec.kind.value} terminal backend execution adapter is not installed")


class ModalTerminalBackendAdapter:
    def __init__(self, spec: TerminalBackendSpec) -> None:
        self.spec = spec

    def capabilities(self) -> TerminalBackendCapabilities:
        modal_available = shutil.which("modal") is not None
        missing_config = [] if self.spec.command_prefix else ["command_prefix"]
        notes = list(self.spec.notes)
        if not self.spec.command_prefix:
            notes.append("modal backend requires command_prefix for an operator-provided Modal runner.")
        if not modal_available:
            notes.append("modal executable is not available on PATH.")
        return _base_capabilities(
            self.spec,
            executable=bool(modal_available and not missing_config),
            configured=not missing_config,
            launch_mode="remote_cli",
            workspace_sync=_workspace_sync_mode(self.spec, default="operator_managed"),
            required_config=["command_prefix"],
            missing_config=missing_config,
            required_executables=["modal"],
            missing_executables=[] if modal_available else ["modal"],
            notes=notes,
        )

    def prepare_launch(self, *, thread_id: str, command: str, cwd: str, env: dict[str, Any]) -> ProcessLaunch:
        if not self.spec.command_prefix:
            raise RuntimeError("modal terminal backend requires command_prefix for a Modal runner")
        return _remote_cli_launch(self.spec, command=command, env=env, executable_name="modal")


class DaytonaTerminalBackendAdapter:
    def __init__(self, spec: TerminalBackendSpec) -> None:
        self.spec = spec

    def capabilities(self) -> TerminalBackendCapabilities:
        daytona_available = shutil.which("daytona") is not None
        missing_config = [] if self.spec.sandbox_id else ["sandbox_id"]
        notes = list(self.spec.notes)
        if not self.spec.sandbox_id:
            notes.append("daytona backend requires sandbox_id.")
        if not daytona_available:
            notes.append("daytona executable is not available on PATH.")
        return _base_capabilities(
            self.spec,
            executable=bool(daytona_available and not missing_config),
            configured=not missing_config,
            launch_mode="daytona_ssh",
            workspace_sync=_workspace_sync_mode(self.spec, default="provider_workspace"),
            required_config=["sandbox_id"],
            missing_config=missing_config,
            required_executables=["daytona"],
            missing_executables=[] if daytona_available else ["daytona"],
            notes=notes,
        )

    def prepare_launch(self, *, thread_id: str, command: str, cwd: str, env: dict[str, Any]) -> ProcessLaunch:
        if not self.spec.sandbox_id:
            raise RuntimeError("daytona terminal backend requires sandbox_id")
        args = _sandbox_cli_args(
            spec=self.spec,
            executable_name="daytona",
            builtin_prefix=["daytona", "ssh", self.spec.sandbox_id],
            command=command,
            cwd=cwd,
            env=env,
        )
        process_env = _process_env({**os.environ, **self.spec.env})
        return ProcessLaunch(
            popen_args=args,
            display_command=shlex.join(args),
            cwd=None,
            env=process_env,
            shell=False,
        )


class VercelTerminalBackendAdapter:
    def __init__(self, spec: TerminalBackendSpec) -> None:
        self.spec = spec

    def capabilities(self) -> TerminalBackendCapabilities:
        sandbox_available = shutil.which("sandbox") is not None
        missing_config = [] if self.spec.sandbox_id else ["sandbox_id"]
        notes = list(self.spec.notes)
        if not self.spec.sandbox_id:
            notes.append("vercel backend requires sandbox_id.")
        if not sandbox_available:
            notes.append("Vercel Sandbox CLI executable 'sandbox' is not available on PATH.")
        return _base_capabilities(
            self.spec,
            executable=bool(sandbox_available and not missing_config),
            configured=not missing_config,
            launch_mode="vercel_sandbox_exec",
            workspace_sync=_workspace_sync_mode(self.spec, default="provider_workspace"),
            required_config=["sandbox_id"],
            missing_config=missing_config,
            required_executables=["sandbox"],
            missing_executables=[] if sandbox_available else ["sandbox"],
            notes=notes,
        )

    def prepare_launch(self, *, thread_id: str, command: str, cwd: str, env: dict[str, Any]) -> ProcessLaunch:
        if not self.spec.sandbox_id:
            raise RuntimeError("vercel terminal backend requires sandbox_id")
        args = _sandbox_cli_args(
            spec=self.spec,
            executable_name="sandbox",
            builtin_prefix=_vercel_sandbox_prefix(self.spec, cwd=cwd, env=env),
            command=command,
            cwd=cwd,
            env=env,
        )
        process_env = _process_env({**os.environ, **self.spec.env})
        return ProcessLaunch(
            popen_args=args,
            display_command=shlex.join(args),
            cwd=None,
            env=process_env,
            shell=False,
        )


def create_terminal_backend_adapter(spec: TerminalBackendSpec, path_service: Any | None = None) -> TerminalBackendAdapter:
    if spec.kind is TerminalBackendKind.LOCAL:
        return LocalTerminalBackendAdapter(spec)
    if spec.kind is TerminalBackendKind.DOCKER:
        return DockerTerminalBackendAdapter(spec, path_service=path_service)
    if spec.kind is TerminalBackendKind.SSH:
        return SshTerminalBackendAdapter(spec)
    if spec.kind is TerminalBackendKind.SINGULARITY:
        return SingularityTerminalBackendAdapter(spec, path_service=path_service)
    if spec.kind is TerminalBackendKind.MODAL:
        return ModalTerminalBackendAdapter(spec)
    if spec.kind is TerminalBackendKind.DAYTONA:
        return DaytonaTerminalBackendAdapter(spec)
    if spec.kind is TerminalBackendKind.VERCEL:
        return VercelTerminalBackendAdapter(spec)
    return UnsupportedTerminalBackendAdapter(spec)


def _base_capabilities(
    spec: TerminalBackendSpec,
    *,
    executable: bool = True,
    configured: bool = True,
    launch_mode: str | None = None,
    workspace_sync: str | None = None,
    required_config: list[str] | None = None,
    missing_config: list[str] | None = None,
    required_executables: list[str] | None = None,
    missing_executables: list[str] | None = None,
    notes: list[str] | None = None,
) -> TerminalBackendCapabilities:
    return TerminalBackendCapabilities(
        kind=spec.kind,
        backend_id=spec.backend_id,
        label=spec.label or _backend_label(spec.kind),
        interactive=True,
        persistent_sessions=True,
        pty=False,
        stdin=True,
        incremental_log=True,
        interrupt=True,
        remote=spec.kind in {TerminalBackendKind.SSH, TerminalBackendKind.MODAL, TerminalBackendKind.DAYTONA, TerminalBackendKind.VERCEL},
        isolated=spec.kind
        in {TerminalBackendKind.DOCKER, TerminalBackendKind.SINGULARITY, TerminalBackendKind.MODAL, TerminalBackendKind.DAYTONA, TerminalBackendKind.VERCEL},
        configured=configured,
        executable=executable,
        launch_mode=launch_mode or _default_launch_mode(spec.kind),
        workspace_sync=workspace_sync or _workspace_sync_mode(spec),
        required_config=list(required_config or []),
        missing_config=list(missing_config or []),
        required_executables=list(required_executables or []),
        missing_executables=list(missing_executables or []),
        env_passthrough=list(spec.env_passthrough),
        env_prefix_passthrough=list(spec.env_prefix_passthrough),
        notes=list(notes if notes is not None else spec.notes),
    )


def _prefixed_command(prefix: list[str], command: str) -> str:
    if not prefix:
        return command
    return " ".join([*prefix, command])


def _backend_env_vars(env: dict[str, Any], spec: TerminalBackendSpec) -> dict[str, str]:
    allowed_prefixes = ("ANVIL_", "PYTHON")
    allowed_names = {"PATH", "HOME", "LANG", "LC_ALL", "TERM"}
    exact_names = allowed_names | set(spec.env.keys()) | set(spec.env_passthrough)
    prefixes = (*allowed_prefixes, *spec.env_prefix_passthrough)
    return {
        key: str(value)
        for key, value in env.items()
        if key in exact_names or any(key.startswith(prefix) for prefix in prefixes)
    }


def _docker_resource_args(resource_limits: dict[str, Any]) -> list[str]:
    args: list[str] = []
    if cpus := resource_limits.get("cpus"):
        args.extend(["--cpus", str(cpus)])
    if memory := resource_limits.get("memory"):
        args.extend(["--memory", str(memory)])
    if pids_limit := resource_limits.get("pids_limit"):
        args.extend(["--pids-limit", str(pids_limit)])
    return args


def _docker_mount_spec(mount: TerminalBackendMount) -> str:
    suffix = ":ro" if mount.read_only else ""
    return f"{Path(mount.host_path).resolve().as_posix()}:{mount.container_path}{suffix}"


def _singularity_mount_spec(mount: TerminalBackendMount) -> str:
    suffix = ":ro" if mount.read_only else ""
    return f"{Path(mount.host_path).resolve().as_posix()}:{mount.container_path}{suffix}"


def _thread_virtual_mounts(path_service: Any | None, thread_id: str) -> list[TerminalBackendMount]:
    if path_service is None:
        return []
    path_map = path_service.virtual_path_map(thread_id)
    mounts: list[TerminalBackendMount] = []
    for virtual_root, host_path in sorted(path_map.items()):
        if not _mountable_virtual_root(virtual_root):
            continue
        if not host_path:
            continue
        host_root = Path(host_path).resolve()
        host_root.mkdir(parents=True, exist_ok=True)
        mounts.append(TerminalBackendMount(host_path=str(host_root), container_path=virtual_root))
    return mounts


def _merge_mounts(mounts: list[TerminalBackendMount]) -> list[TerminalBackendMount]:
    by_container: dict[str, TerminalBackendMount] = {}
    for mount in mounts:
        by_container[mount.container_path] = mount
    return list(by_container.values())


def _remote_command(*, command: str, cwd: str | None, env: dict[str, Any], spec: TerminalBackendSpec) -> str:
    exports = " ".join(f"{key}={shlex.quote(str(value))}" for key, value in _backend_env_vars(env, spec).items())
    chunks = []
    if cwd:
        chunks.append(f"cd {shlex.quote(cwd)}")
    if exports:
        chunks.append(f"env {exports} sh -lc {shlex.quote(command)}")
    else:
        chunks.append(f"sh -lc {shlex.quote(command)}")
    return " && ".join(chunks)


def _windows_shell_executable(env: dict[str, str] | None) -> str | None:
    if os.name != "nt":
        return None
    return (
        (env or {}).get("ComSpec")
        or os.environ.get("ComSpec")
        or str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe")
    )


def _process_env(env: dict[str, Any] | None) -> dict[str, str]:
    process_env = {str(key): str(value) for key, value in (env or os.environ).items()}
    process_env.setdefault("PYTHONUTF8", "1")
    process_env.setdefault("PYTHONIOENCODING", "utf-8")
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        process_env.setdefault("SystemRoot", system_root)
        process_env.setdefault("ComSpec", os.environ.get("ComSpec", str(Path(system_root) / "System32" / "cmd.exe")))
        process_env.setdefault("PATH", os.environ.get("PATH", ""))
    return process_env


def _remote_cli_launch(spec: TerminalBackendSpec, *, command: str, env: dict[str, Any], executable_name: str) -> ProcessLaunch:
    executable = shutil.which(executable_name)
    if executable is None:
        raise RuntimeError(f"{executable_name} executable is required for {spec.kind.value} terminal backend")
    args = [*spec.command_prefix, _prefixed_command([], command)]
    process_env = _process_env({**os.environ, **spec.env, **env})
    return ProcessLaunch(
        popen_args=args,
        display_command=shlex.join(args),
        cwd=None,
        env=process_env,
        shell=False,
    )


def _sandbox_cli_args(
    *,
    spec: TerminalBackendSpec,
    executable_name: str,
    builtin_prefix: list[str],
    command: str,
    cwd: str,
    env: dict[str, Any],
) -> list[str]:
    executable = shutil.which(executable_name)
    if executable is None:
        raise RuntimeError(f"{executable_name} executable is required for {spec.kind.value} terminal backend")
    remote_cwd = spec.working_dir or spec.default_cwd or _default_remote_cwd(cwd)
    prefix = spec.command_prefix or builtin_prefix
    if spec.kind is TerminalBackendKind.VERCEL and not spec.command_prefix:
        return [*prefix, "sh", "-lc", _prefixed_command([], command)]
    remote_command = _remote_command(command=_prefixed_command([], command), cwd=remote_cwd, env={**spec.env, **env}, spec=spec)
    return [*prefix, remote_command]


def _vercel_sandbox_prefix(spec: TerminalBackendSpec, *, cwd: str, env: dict[str, Any]) -> list[str]:
    remote_cwd = spec.working_dir or spec.default_cwd or _default_remote_cwd(cwd)
    args = ["sandbox", "exec", "--workdir", remote_cwd]
    for key, value in _backend_env_vars({**spec.env, **env}, spec).items():
        args.extend(["--env", f"{key}={value}"])
    if spec.sandbox_id:
        args.append(spec.sandbox_id)
    return args


def _workspace_sync_mode(spec: TerminalBackendSpec, *, default: str | None = None) -> str:
    sync_mode = (spec.sync or {}).get("mode")
    if sync_mode:
        return str(sync_mode)
    if default is not None:
        return default
    if spec.kind in {TerminalBackendKind.LOCAL}:
        return "local"
    if spec.kind in {TerminalBackendKind.DOCKER, TerminalBackendKind.SINGULARITY}:
        return "bind_mount"
    if spec.kind is TerminalBackendKind.SSH:
        return "remote_cwd"
    return "operator_managed"


def _default_launch_mode(kind: TerminalBackendKind) -> str:
    modes = {
        TerminalBackendKind.LOCAL: "local_process",
        TerminalBackendKind.DOCKER: "docker_run",
        TerminalBackendKind.SSH: "ssh",
        TerminalBackendKind.SINGULARITY: "singularity_exec",
        TerminalBackendKind.MODAL: "remote_cli",
        TerminalBackendKind.DAYTONA: "daytona_ssh",
        TerminalBackendKind.VERCEL: "vercel_sandbox_exec",
    }
    return modes[kind]


def _mountable_virtual_root(virtual_root: str) -> bool:
    candidate = PurePosixPath(virtual_root)
    mount_roots = {
        PurePosixPath("/mnt/user-data/workspace"),
        PurePosixPath("/mnt/user-data/uploads"),
        PurePosixPath("/mnt/user-data/outputs"),
        PurePosixPath("/mnt/worker-data"),
    }
    if candidate in mount_roots:
        return True
    return str(candidate).startswith("/mnt/user-data/workspace/_host/")


def _backend_label(kind: TerminalBackendKind) -> str:
    labels = {
        TerminalBackendKind.LOCAL: "Local shell",
        TerminalBackendKind.DOCKER: "Docker shell",
        TerminalBackendKind.SSH: "SSH shell",
        TerminalBackendKind.SINGULARITY: "Singularity shell",
        TerminalBackendKind.MODAL: "Modal shell",
        TerminalBackendKind.DAYTONA: "Daytona shell",
        TerminalBackendKind.VERCEL: "Vercel sandbox shell",
    }
    return labels[kind]


def _default_remote_cwd(cwd: str) -> str:
    if cwd.startswith("/"):
        return cwd
    return "~"
