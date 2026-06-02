from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess

from .provider import SandboxHandle


@dataclass
class ContainerSandboxHandle(SandboxHandle):
    image: str = "python:3.12-slim"
    network_access: bool = False
    max_execution_time: int = 30

    def execute_command(
        self,
        *,
        command: str,
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("docker executable is required for isolated sandbox mode")
        command_timeout = timeout_seconds or self.max_execution_time
        runtime_env = []
        for key in ("ANVIL_WORKSPACE", "ANVIL_UPLOADS", "ANVIL_OUTPUTS", "ANVIL_SCRATCH"):
            if key in env:
                runtime_env.extend(["-e", f"{key}={env[key]}"])
        volume_args = self._virtual_root_volume_args()
        args = [
            docker,
            "run",
            "--rm",
            "-w",
            cwd,
            *volume_args,
            "-v",
            f"{Path.cwd().resolve().parent.joinpath('skills').as_posix()}:/mnt/skills:ro",
            *([] if self.network_access else ["--network", "none"]),
            *runtime_env,
            self.image,
            "sh",
            "-lc",
            command,
        ]
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={k: v for k, v in os.environ.items() if k not in env} | env,
            timeout=command_timeout,
        )

    def _virtual_root_volume_args(self) -> list[str]:
        path_map = self.path_service.virtual_path_map(self.thread_id)
        required_roots = (
            "/mnt/user-data/workspace",
            "/mnt/user-data/uploads",
            "/mnt/user-data/outputs",
            "/mnt/worker-data",
        )
        volume_args: list[str] = []
        for virtual_root in required_roots:
            host_root = Path(path_map[virtual_root]).resolve()
            host_root.mkdir(parents=True, exist_ok=True)
            volume_args.extend(["-v", f"{host_root.as_posix()}:{virtual_root}"])
        return volume_args


class IsolatedSandboxProvider:
    provider_mode = "isolated"

    def __init__(self, *, image: str, network_access: bool, max_execution_time: int) -> None:
        self.image = image
        self.network_access = network_access
        self.max_execution_time = max_execution_time
        self._handles: dict[str, ContainerSandboxHandle] = {}

    def acquire(self, *, thread_id: str, path_service) -> ContainerSandboxHandle:
        existing = self._handles.get(thread_id)
        if existing is not None:
            return existing

        thread_data = path_service.bootstrap_thread_paths(thread_id)
        projection = path_service.to_sandbox_projection(
            thread_id,
            writable_kinds=("workspace", "outputs"),
        )
        handle = ContainerSandboxHandle(
            thread_id=thread_id,
            provider_mode=self.provider_mode,
            sandbox_id=f"isolated:{thread_id}",
            thread_data=thread_data,
            projection=projection,
            path_service=path_service,
            image=self.image,
            network_access=self.network_access,
            max_execution_time=self.max_execution_time,
        )
        self._handles[thread_id] = handle
        return handle

    def get(self, thread_id: str) -> ContainerSandboxHandle | None:
        return self._handles.get(thread_id)

    def release(self, thread_id: str) -> None:
        self._handles.pop(thread_id, None)
