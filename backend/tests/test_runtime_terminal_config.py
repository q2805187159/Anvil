from __future__ import annotations

from pathlib import Path

import anvil.processes.backends as terminal_backends
from anvil import ConfigLayer, ConfigLayerKind, EffectiveConfig
from app.runtime_deps import _candidate_auto_host_roots, _is_safe_auto_host_root, build_auto_host_path_bridges, build_path_bridges, build_runtime_deps_bundle


def test_runtime_deps_wires_terminal_backend_config(contract_tmp_path) -> None:
    original_which = terminal_backends.shutil.which
    terminal_backends.shutil.which = lambda name: None
    bundle = build_runtime_deps_bundle(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "terminal": {
                        "active_backend": "docker_lab",
                        "backends": {
                            "docker_lab": {
                                "kind": "docker",
                                "label": "Docker Lab",
                                "enabled": True,
                                "env_passthrough": ["EXTRA_ALLOWED"],
                                "env_prefix_passthrough": ["PROJECT_"],
                                "mounts": [
                                    {
                                        "host_path": str(contract_tmp_path / "custom-cache"),
                                        "container_path": "/cache",
                                        "read_only": True,
                                    }
                                ],
                                "notes": ["configured by test"],
                            }
                        },
                    },
                },
            )
        ],
        thread_root=contract_tmp_path / "threads",
        state_db_path=contract_tmp_path / "gateway.sqlite3",
    )
    try:
        capabilities = bundle.process_service.capabilities()
        assert capabilities.kind == "docker"
        assert capabilities.backend_id == "docker_lab"
        assert capabilities.label == "Docker Lab"
        assert capabilities.isolated is True
        assert capabilities.executable is False
        assert capabilities.launch_mode == "docker_run"
        assert capabilities.required_executables == ["docker"]
        assert capabilities.missing_executables == ["docker"]
        assert capabilities.env_passthrough == ["EXTRA_ALLOWED"]
        assert capabilities.env_prefix_passthrough == ["PROJECT_"]
        assert "configured by test" in capabilities.notes
        assert any("docker executable is not available" in note for note in capabilities.notes)
        spec = bundle.process_service.backend_adapter.spec
        assert spec.image is None
        assert len(spec.mounts) == 1
        assert spec.mounts[0].host_path == str(contract_tmp_path / "custom-cache")
        assert spec.mounts[0].container_path == "/cache"
        assert spec.mounts[0].read_only is True
        assert spec.env_passthrough == ["EXTRA_ALLOWED"]
    finally:
        terminal_backends.shutil.which = original_which
        bundle.close()


def test_runtime_deps_wires_configured_workspace_path_bridges(contract_tmp_path) -> None:
    bridge_root = contract_tmp_path / "drive-e-project"
    (bridge_root / "src").mkdir(parents=True)
    bundle = build_runtime_deps_bundle(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "workspace": {
                        "mode": "thread",
                        "auto_host_drives": False,
                        "path_bridges": [
                            {
                                "alias": "e_drive_project",
                                "display_root": "E:/projects/demo-app",
                                "actual_root": str(bridge_root),
                            }
                        ],
                    },
                },
            )
        ],
        thread_root=contract_tmp_path / "threads",
        state_db_path=contract_tmp_path / "gateway.sqlite3",
    )
    try:
        bundle.thread_service.create_thread(thread_id="bridge-thread")
        assert bundle.path_service.resolve_virtual_path(
            "bridge-thread",
            "/mnt/user-data/workspace/_host/e_drive_project/src",
        ) == (bridge_root / "src").resolve()
        assert "_host" in bundle.path_service.list_virtual_dir("bridge-thread", "/mnt/user-data/workspace")
        assert bundle.path_service.list_virtual_dir("bridge-thread", "/mnt/user-data/workspace/_host") == ["e_drive_project"]
        assert bundle.path_service.translate_user_text_to_runtime(
            "edit E:/projects/demo-app/src",
            thread_id="bridge-thread",
        ) == "edit /mnt/user-data/workspace/_host/e_drive_project/src"
    finally:
        bundle.close()


def test_runtime_deps_auto_project_candidates_cover_common_platforms(monkeypatch) -> None:
    monkeypatch.setattr("app.runtime_deps.platform.system", lambda: "Windows")
    assert ("c_drive", "C:", "C:\\") in _candidate_auto_host_roots(["C"])
    assert ("e_drive", "E:", "E:\\") in _candidate_auto_host_roots(["E"])

    monkeypatch.setattr("app.runtime_deps.platform.system", lambda: "Darwin")
    mac_aliases = {alias for alias, _, _ in _candidate_auto_host_roots([])}
    assert {"home", "desktop", "documents", "downloads"}.issubset(mac_aliases)

    monkeypatch.setattr("app.runtime_deps.platform.system", lambda: "Linux")
    linux_aliases = {alias for alias, _, _ in _candidate_auto_host_roots([])}
    assert {"home", "workspace_parent"}.issubset(linux_aliases)


def test_runtime_deps_auto_projects_existing_local_roots(monkeypatch, contract_tmp_path) -> None:
    monkeypatch.setattr(
        "app.runtime_deps._candidate_auto_host_roots",
        lambda requested: [
            ("e_drive", "E:", str(contract_tmp_path)),
            ("missing_drive", "Z:", str(contract_tmp_path / "missing")),
        ],
    )
    effective = EffectiveConfig(workspace={"auto_host_drives": True})

    aliases = {bridge.alias for bridge in build_path_bridges(effective)}

    assert "e_drive" in aliases
    assert "missing_drive" not in aliases


def test_runtime_deps_prefers_docker_host_mount_candidates(monkeypatch, contract_tmp_path) -> None:
    host_root = contract_tmp_path / "host"
    e_drive = host_root / "e_drive"
    c_drive = host_root / "c_drive"
    workspace = host_root / "workspace"
    e_drive.mkdir(parents=True)
    c_drive.mkdir()
    workspace.mkdir()

    monkeypatch.setenv("ANVIL_DOCKER_HOST_ROOT", str(host_root))

    assert _candidate_auto_host_roots(["E"]) == [("e_drive", "E:", str(e_drive))]

    effective = EffectiveConfig(workspace={"auto_host_drives": True, "auto_host_drive_letters": ["E"]})
    aliases = {bridge.alias: bridge for bridge in build_auto_host_path_bridges(effective)}

    assert set(aliases) == {"e_drive"}
    assert aliases["e_drive"].display_root == "E:"
    assert aliases["e_drive"].actual_root == str(e_drive.resolve())


def test_auto_host_roots_skip_internal_runtime_mounts(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.runtime_deps._candidate_auto_host_roots",
        lambda requested: [
            ("user_data", "/mnt/user-data", "/mnt/user-data"),
            ("worker_data", "/mnt/worker-data", "/mnt/worker-data"),
            ("host_workspaces", "/mnt/host-workspaces", "/mnt/host-workspaces"),
        ],
    )
    effective = EffectiveConfig(workspace={"auto_host_drives": True})

    assert build_auto_host_path_bridges(effective) == []


def test_auto_host_roots_do_not_expose_filesystem_root_or_runtime_parents() -> None:
    assert _is_safe_auto_host_root(Path("/")) is False
    assert _is_safe_auto_host_root(Path("/mnt")) is False
    assert _is_safe_auto_host_root(Path("/mnt/user-data")) is False
    assert _is_safe_auto_host_root(Path("/mnt/worker-data")) is False
    assert _is_safe_auto_host_root(Path("/mnt/host-workspaces")) is False


def test_auto_host_roots_can_be_disabled_without_disabling_manual_bridges(contract_tmp_path) -> None:
    effective = EffectiveConfig(
        workspace={
            "auto_host_drives": False,
            "path_bridges": [
                {
                    "alias": "manual_project",
                    "display_root": str(contract_tmp_path),
                    "actual_root": str(contract_tmp_path),
                }
            ],
        }
    )

    assert [bridge.alias for bridge in build_path_bridges(effective)] == ["manual_project"]
