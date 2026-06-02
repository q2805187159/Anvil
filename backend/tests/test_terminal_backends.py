from __future__ import annotations

from pathlib import Path

import pytest

from anvil.processes import TerminalBackendKind, TerminalBackendMount, TerminalBackendSpec
from anvil.processes.backends import (
    DaytonaTerminalBackendAdapter,
    DockerTerminalBackendAdapter,
    ModalTerminalBackendAdapter,
    SshTerminalBackendAdapter,
    SingularityTerminalBackendAdapter,
    VercelTerminalBackendAdapter,
    create_terminal_backend_adapter,
)


class FakePathService:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def virtual_path_map(self, thread_id: str) -> dict[str, str]:
        return dict(self.mapping)


def test_docker_backend_builds_isolated_run_command(contract_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anvil.processes.backends.shutil.which", lambda name: "docker" if name == "docker" else None)
    spec = TerminalBackendSpec(
        kind=TerminalBackendKind.DOCKER,
        backend_id="docker_lab",
        label="Docker Lab",
        image="python:3.12-slim",
        working_dir="/mnt/user-data/workspace",
        env={"CUSTOM_TOKEN": "configured"},
        env_passthrough=["EXTRA_ALLOWED"],
        resource_limits={"cpus": "2", "memory": "4g", "pids_limit": "256"},
        mounts=[
            TerminalBackendMount(
                host_path=str(contract_tmp_path / "workspace"),
                container_path="/mnt/user-data/workspace",
            ),
            TerminalBackendMount(
                host_path=str(contract_tmp_path / "uploads"),
                container_path="/mnt/user-data/uploads",
                read_only=True,
            ),
        ],
    )

    adapter = DockerTerminalBackendAdapter(spec)
    launch = adapter.prepare_launch(
        thread_id="thread-1",
        command="python app.py",
        cwd="/mnt/user-data/workspace",
        env={"ANVIL_WORKSPACE": "/mnt/user-data/workspace", "EXTRA_ALLOWED": "yes", "BLOCKED_TOKEN": "no"},
    )

    assert launch.shell is False
    assert isinstance(launch.popen_args, list)
    assert launch.popen_args[:6] == ["docker", "run", "--rm", "-i", "-w", "/mnt/user-data/workspace"]
    assert "--cpus" in launch.popen_args
    assert "--memory" in launch.popen_args
    assert "--pids-limit" in launch.popen_args
    assert "python:3.12-slim" in launch.popen_args
    assert launch.popen_args[-4:] == ["python:3.12-slim", "sh", "-lc", "python app.py"]
    volume_specs = [launch.popen_args[index + 1] for index, item in enumerate(launch.popen_args) if item == "-v"]
    assert f"{(contract_tmp_path / 'workspace').resolve().as_posix()}:/mnt/user-data/workspace" in volume_specs
    assert f"{(contract_tmp_path / 'uploads').resolve().as_posix()}:/mnt/user-data/uploads:ro" in volume_specs
    env_specs = [launch.popen_args[index + 1] for index, item in enumerate(launch.popen_args) if item == "-e"]
    assert "CUSTOM_TOKEN=configured" in env_specs
    assert "EXTRA_ALLOWED=yes" in env_specs
    assert "BLOCKED_TOKEN=no" not in env_specs
    assert adapter.capabilities().executable is True
    assert adapter.capabilities().launch_mode == "docker_run"
    assert adapter.capabilities().workspace_sync == "bind_mount"


def test_docker_backend_mounts_thread_virtual_roots_and_host_bridges(contract_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anvil.processes.backends.shutil.which", lambda name: "docker" if name == "docker" else None)
    bridge_root = contract_tmp_path / "drive-e"
    path_service = FakePathService(
        {
            "/mnt/user-data/workspace": str(contract_tmp_path / "workspace"),
            "/mnt/user-data/uploads": str(contract_tmp_path / "uploads"),
            "/mnt/user-data/outputs": str(contract_tmp_path / "outputs"),
            "/mnt/worker-data": str(contract_tmp_path / "worker-data"),
            "/mnt/user-data/workspace/_host/e_drive": str(bridge_root),
            "/mnt/skills": str(contract_tmp_path / "skills"),
        }
    )
    spec = TerminalBackendSpec(kind=TerminalBackendKind.DOCKER, backend_id="docker_lab")

    adapter = DockerTerminalBackendAdapter(spec, path_service=path_service)
    launch = adapter.prepare_launch(thread_id="thread-1", command="pwd", cwd="/mnt/user-data/workspace", env={})

    assert isinstance(launch.popen_args, list)
    volume_specs = [launch.popen_args[index + 1] for index, item in enumerate(launch.popen_args) if item == "-v"]
    assert f"{bridge_root.resolve().as_posix()}:/mnt/user-data/workspace/_host/e_drive" in volume_specs
    assert not any("/mnt/skills" in item for item in volume_specs)


def test_ssh_backend_builds_remote_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anvil.processes.backends.shutil.which", lambda name: "ssh" if name == "ssh" else None)
    spec = TerminalBackendSpec(
        kind=TerminalBackendKind.SSH,
        backend_id="ssh_prod",
        host="example.internal",
        username="agent",
        working_dir="/srv/anvil/thread-1/workspace",
        env={"ANVIL_WORKSPACE": "/srv/anvil/thread-1/workspace"},
    )

    adapter = SshTerminalBackendAdapter(spec)
    launch = adapter.prepare_launch(thread_id="thread-1", command="pwd && ls", cwd="/mnt/user-data/workspace", env={"PYTHONUTF8": "1"})

    assert launch.shell is False
    assert launch.popen_args[0:2] == ["ssh", "agent@example.internal"]
    assert "cd /srv/anvil/thread-1/workspace" in launch.popen_args[2]
    assert "sh -lc" in launch.popen_args[2]
    assert "pwd && ls" in launch.popen_args[2]
    capabilities = adapter.capabilities()
    assert capabilities.launch_mode == "ssh"
    assert capabilities.required_config == ["host"]
    assert capabilities.missing_config == []


def test_singularity_backend_builds_exec_command(contract_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anvil.processes.backends.shutil.which", lambda name: "apptainer" if name == "apptainer" else None)
    spec = TerminalBackendSpec(
        kind=TerminalBackendKind.SINGULARITY,
        backend_id="singularity_hpc",
        image="/images/anvil.sif",
        working_dir="/workspace",
        mounts=[
            TerminalBackendMount(
                host_path=str(contract_tmp_path / "workspace"),
                container_path="/workspace",
            )
        ],
    )

    adapter = SingularityTerminalBackendAdapter(spec)
    launch = adapter.prepare_launch(thread_id="thread-1", command="python train.py", cwd="/workspace", env={})

    assert launch.shell is False
    assert isinstance(launch.popen_args, list)
    assert launch.popen_args[:4] == ["apptainer", "exec", "--pwd", "/workspace"]
    assert "/images/anvil.sif" in launch.popen_args
    assert launch.popen_args[-4:] == ["/images/anvil.sif", "sh", "-lc", "python train.py"]
    bind_index = launch.popen_args.index("--bind")
    assert launch.popen_args[bind_index + 1] == f"{(contract_tmp_path / 'workspace').resolve().as_posix()}:/workspace"


def test_modal_backend_uses_explicit_runner_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anvil.processes.backends.shutil.which", lambda name: "modal" if name == "modal" else None)
    spec = TerminalBackendSpec(
        kind=TerminalBackendKind.MODAL,
        backend_id="modal_lab",
        command_prefix=["modal", "run", "anvil_modal_runner.py::main", "--command"],
        sync={"mode": "volume"},
    )

    adapter = create_terminal_backend_adapter(spec)
    launch = adapter.prepare_launch(thread_id="thread-1", command="python task.py", cwd="/workspace", env={})

    assert isinstance(adapter, ModalTerminalBackendAdapter)
    assert launch.shell is False
    assert launch.popen_args == ["modal", "run", "anvil_modal_runner.py::main", "--command", "python task.py"]
    capabilities = adapter.capabilities()
    assert capabilities.executable is True
    assert capabilities.configured is True
    assert capabilities.launch_mode == "remote_cli"
    assert capabilities.workspace_sync == "volume"


def test_modal_backend_requires_runner_prefix() -> None:
    adapter = create_terminal_backend_adapter(TerminalBackendSpec(kind=TerminalBackendKind.MODAL, backend_id="modal_lab"))

    capabilities = adapter.capabilities()

    assert isinstance(adapter, ModalTerminalBackendAdapter)
    assert capabilities.executable is False
    assert capabilities.configured is False
    assert capabilities.missing_config == ["command_prefix"]
    assert any("command_prefix" in note for note in capabilities.notes)


@pytest.mark.parametrize(
    ("kind", "adapter_type", "binary", "expected_prefix", "launch_mode"),
    [
        (TerminalBackendKind.DAYTONA, DaytonaTerminalBackendAdapter, "daytona", ["daytona", "ssh", "sandbox-123"], "daytona_ssh"),
        (TerminalBackendKind.VERCEL, VercelTerminalBackendAdapter, "sandbox", ["sandbox", "exec", "--workdir", "/workspace"], "vercel_sandbox_exec"),
    ],
)
def test_provider_sandbox_backends_build_cli_launch(
    monkeypatch: pytest.MonkeyPatch,
    kind: TerminalBackendKind,
    adapter_type: type,
    binary: str,
    expected_prefix: list[str],
    launch_mode: str,
) -> None:
    monkeypatch.setattr("anvil.processes.backends.shutil.which", lambda name: name if name == binary else None)
    spec = TerminalBackendSpec(
        kind=kind,
        backend_id=f"{kind.value}_lab",
        sandbox_id="sandbox-123",
        working_dir="/workspace",
        env={"ANVIL_WORKSPACE": "/workspace"},
    )

    adapter = create_terminal_backend_adapter(spec)
    launch = adapter.prepare_launch(thread_id="thread-1", command="pwd && ls", cwd="/mnt/user-data/workspace", env={"PYTHONUTF8": "1"})

    assert isinstance(adapter, adapter_type)
    assert launch.shell is False
    assert launch.popen_args[: len(expected_prefix)] == expected_prefix
    if kind is TerminalBackendKind.VERCEL:
        assert "sandbox-123" in launch.popen_args
        assert launch.popen_args[-3:] == ["sh", "-lc", "pwd && ls"]
    else:
        assert "cd /workspace" in launch.popen_args[-1]
    assert "pwd && ls" in launch.popen_args[-1]
    capabilities = adapter.capabilities()
    assert capabilities.executable is True
    assert capabilities.launch_mode == launch_mode
    assert capabilities.workspace_sync == "provider_workspace"


@pytest.mark.parametrize("kind", [TerminalBackendKind.DAYTONA, TerminalBackendKind.VERCEL])
def test_provider_sandbox_backends_report_missing_sandbox(kind: TerminalBackendKind) -> None:
    adapter = create_terminal_backend_adapter(TerminalBackendSpec(kind=kind, backend_id=f"{kind.value}_lab"))

    capabilities = adapter.capabilities()

    assert capabilities.executable is False
    assert capabilities.configured is False
    assert capabilities.missing_config == ["sandbox_id"]
