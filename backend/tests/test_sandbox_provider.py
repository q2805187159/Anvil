from __future__ import annotations

import subprocess

import pytest

from anvil.config import EffectiveConfig, SandboxMode
from anvil.sandbox import PathBridge, PathService, create_sandbox_provider
from anvil.sandbox.factory import ConfigurationError


def test_local_provider_acquire_get_release(contract_tmp_path) -> None:
    provider = create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.LOCAL))
    path_service = PathService(contract_tmp_path)

    handle = provider.acquire(thread_id="thread-1", path_service=path_service)

    assert provider.get("thread-1") is handle
    provider.release("thread-1")
    assert provider.get("thread-1") is None


def test_local_provider_uses_path_service_projection(contract_tmp_path) -> None:
    provider = create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.LOCAL))
    path_service = PathService(contract_tmp_path)

    handle = provider.acquire(thread_id="thread-1", path_service=path_service)
    expected_projection = path_service.to_sandbox_projection("thread-1", writable_kinds=("workspace", "outputs"))

    assert handle.projection == expected_projection
    assert handle.projection.logical_cwd == "/mnt/user-data/workspace"
    assert handle.projection.policy_roots == [
        str((contract_tmp_path / "thread-1" / "workspace").resolve()),
        str((contract_tmp_path / "thread-1" / "outputs").resolve()),
    ]


def test_local_provider_patches_existing_files(contract_tmp_path) -> None:
    provider = create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.LOCAL))
    path_service = PathService(contract_tmp_path)
    handle = provider.acquire(thread_id="thread-1", path_service=path_service)

    workspace_file = contract_tmp_path / "thread-1" / "workspace" / "notes.txt"
    workspace_file.parent.mkdir(parents=True, exist_ok=True)
    workspace_file.write_text("alpha\nbeta\n", encoding="utf-8")

    result = handle.patch_file(
        "/mnt/user-data/workspace/notes.txt",
        [
            {
                "action": "replace_lines",
                "start_line": 2,
                "end_line": 2,
                "content": "gamma\n",
                "expected_old_text": "beta\n",
            }
        ],
    )

    assert result["operations_applied"] == 1
    assert workspace_file.read_text(encoding="utf-8") == "alpha\ngamma\n"


def test_local_provider_supports_structured_file_crud(contract_tmp_path) -> None:
    provider = create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.LOCAL))
    path_service = PathService(contract_tmp_path)
    handle = provider.acquire(thread_id="thread-1", path_service=path_service)

    make_result = handle.make_dir("/mnt/user-data/workspace/data")
    write_result = handle.write_file("/mnt/user-data/workspace/data/source.txt", "alpha\nbeta\ngamma\n", overwrite=False)
    read_result = handle.read_file_window("/mnt/user-data/workspace/data/source.txt", start_line=2, max_lines=1)
    info_result = handle.file_info("/mnt/user-data/workspace/data/source.txt")
    listed = handle.list_dir_structured("/mnt/user-data/workspace/data", limit=1)
    copy_result = handle.move_path(
        "/mnt/user-data/workspace/data/source.txt",
        "/mnt/user-data/workspace/data/copy.txt",
        copy=True,
    )
    move_result = handle.move_path(
        "/mnt/user-data/workspace/data/copy.txt",
        "/mnt/user-data/workspace/final.txt",
    )
    delete_result = handle.delete_path("/mnt/user-data/workspace/data/source.txt")

    assert make_result == {"path": "/mnt/user-data/workspace/data", "existed": False}
    assert write_result["operation"] == "created"
    assert read_result["content"] == "beta\n"
    assert read_result["truncated"] is True
    assert info_result["kind"] == "file"
    assert info_result["line_count"] == 3
    assert listed["total_count"] == 1
    assert listed["entries"][0]["path"] == "/mnt/user-data/workspace/data/source.txt"
    assert listed["entries"][0]["kind"] == "file"
    assert copy_result["operation"] == "copied"
    assert move_result["operation"] == "moved"
    assert delete_result == {"path": "/mnt/user-data/workspace/data/source.txt", "kind": "file", "recursive": False}
    assert (path_service.thread_workspace_dir("thread-1") / "final.txt").read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n"


def test_local_provider_rejects_runtime_root_delete_or_move(contract_tmp_path) -> None:
    provider = create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.LOCAL))
    path_service = PathService(contract_tmp_path)
    handle = provider.acquire(thread_id="thread-1", path_service=path_service)

    with pytest.raises(ValueError, match="cannot delete a runtime root"):
        handle.delete_path("/mnt/user-data/workspace", recursive=True)

    with pytest.raises(ValueError, match="cannot move a runtime root"):
        handle.move_path("/mnt/user-data/outputs", "/mnt/user-data/workspace/old-outputs")

    source = path_service.thread_workspace_dir("thread-1") / "source.txt"
    source.write_text("source", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot overwrite a runtime root"):
        handle.move_path("/mnt/user-data/workspace/source.txt", "/mnt/user-data/outputs", overwrite=True)


def test_local_provider_lists_structured_host_bridge_aliases(contract_tmp_path) -> None:
    bridge_root = contract_tmp_path / "external" / "repo"
    bridge_root.mkdir(parents=True)
    path_service = PathService(
        contract_tmp_path / "threads",
        path_bridges=[
            PathBridge.create(alias="e_drive_project", display_root="E:/project", actual_root=str(bridge_root)),
        ],
    )
    provider = create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.LOCAL))
    handle = provider.acquire(thread_id="thread-bridge", path_service=path_service)

    listed = handle.list_dir_structured("/mnt/user-data/workspace/_host")

    assert listed["path"] == "/mnt/user-data/workspace/_host"
    assert listed["entries"] == [
        {
            "name": "e_drive_project",
            "path": "/mnt/user-data/workspace/_host/e_drive_project",
            "kind": "directory",
            "size_bytes": None,
            "modified_at": None,
        }
    ]


def test_local_provider_rejects_host_bridge_root_delete(contract_tmp_path) -> None:
    bridge_root = contract_tmp_path / "external" / "repo"
    bridge_root.mkdir(parents=True)
    path_service = PathService(
        contract_tmp_path / "threads",
        path_bridges=[
            PathBridge.create(alias="e_drive_project", display_root="E:/project", actual_root=str(bridge_root)),
        ],
    )
    provider = create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.LOCAL))
    handle = provider.acquire(thread_id="thread-bridge", path_service=path_service)

    with pytest.raises(ValueError, match="cannot delete a runtime root"):
        handle.delete_path("/mnt/user-data/workspace/_host/e_drive_project", recursive=True)


def test_isolated_provider_can_be_constructed(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.ISOLATED))
    handle = provider.acquire(thread_id="thread-1", path_service=path_service)
    assert handle.provider_mode == "isolated"


def test_isolated_provider_mounts_virtual_roots_without_parent_workspace_escape(contract_tmp_path, monkeypatch) -> None:
    base_root = contract_tmp_path / "threads"
    external_workspace = contract_tmp_path / "host-projects" / "repo"
    path_service = PathService(
        base_root,
        default_workspace_root=external_workspace,
        default_workspace_mode="external",
    )
    provider = create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.ISOLATED))
    handle = provider.acquire(thread_id="thread-external", path_service=path_service)
    captured: dict[str, object] = {}

    monkeypatch.setattr("anvil.sandbox.container_provider.shutil.which", lambda name: "docker" if name == "docker" else None)

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("anvil.sandbox.container_provider.subprocess.run", fake_run)

    handle.execute_command(command="pwd", cwd="/mnt/user-data/workspace", env={}, timeout_seconds=1)

    args = captured["args"]
    assert isinstance(args, list)
    volume_specs = [args[index + 1] for index, item in enumerate(args) if item == "-v"]
    assert f"{external_workspace.resolve().as_posix()}:/mnt/user-data/workspace" in volume_specs
    assert f"{(base_root / 'thread-external' / 'uploads').resolve().as_posix()}:/mnt/user-data/uploads" in volume_specs
    assert f"{(base_root / 'thread-external' / 'outputs').resolve().as_posix()}:/mnt/user-data/outputs" in volume_specs
    assert f"{external_workspace.resolve().parent.as_posix()}:/mnt/user-data" not in volume_specs


def test_external_mode_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="unsupported"):
        create_sandbox_provider(EffectiveConfig(sandbox_mode=SandboxMode.EXTERNAL))
