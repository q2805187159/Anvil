from __future__ import annotations

from pathlib import Path

import pytest

from anvil.sandbox import ArtifactKind, PathBridge, PathService


def test_bootstrap_thread_paths_creates_expected_layout(contract_tmp_path) -> None:
    service = PathService(contract_tmp_path)

    thread_data = service.bootstrap_thread_paths("thread-1")

    assert Path(thread_data.workspace_path).is_dir()
    assert Path(thread_data.uploads_path).is_dir()
    assert Path(thread_data.outputs_path).is_dir()
    assert Path(thread_data.external_agent_workspace_root).is_dir()


def test_resolve_virtual_path_and_round_trip(contract_tmp_path) -> None:
    service = PathService(contract_tmp_path)
    thread_data = service.bootstrap_thread_paths("thread-1")
    host_file = Path(thread_data.workspace_path) / "notes.txt"
    host_file.write_text("hello", encoding="utf-8")

    resolved = service.resolve_virtual_path("thread-1", "/mnt/user-data/workspace/notes.txt")
    virtual = service.to_virtual_path("thread-1", resolved)

    assert resolved == host_file.resolve()
    assert virtual == "/mnt/user-data/workspace/notes.txt"


def test_translate_actual_thread_path_accepts_forward_slashes(contract_tmp_path) -> None:
    service = PathService(contract_tmp_path)
    thread_data = service.bootstrap_thread_paths("thread-1")
    workspace_posix = Path(thread_data.workspace_path).as_posix()

    assert service.translate_user_text_to_runtime(f"open {workspace_posix}/notes.txt", "thread-1") == "open /mnt/user-data/workspace/notes.txt"


def test_rejects_invalid_prefix_and_traversal(contract_tmp_path) -> None:
    service = PathService(contract_tmp_path)
    service.bootstrap_thread_paths("thread-1")

    with pytest.raises(ValueError, match="unsupported virtual path prefix"):
        service.resolve_virtual_path("thread-1", "/tmp/notes.txt")

    with pytest.raises(ValueError, match="path escapes allowed root"):
        service.resolve_virtual_path("thread-1", "/mnt/user-data/workspace/../secret.txt")


def test_list_virtual_dir_accepts_exact_user_data_root_aliases(contract_tmp_path) -> None:
    service = PathService(contract_tmp_path)
    service.bootstrap_thread_paths("thread-1")

    for virtual_root in ("/mnt/user-data", "/mnt/user-data/", "/mnt/user-data/."):
        assert service.list_virtual_dir("thread-1", virtual_root) == [
            "outputs",
            "uploads",
            "workspace",
        ]


def test_list_virtual_dir_and_file_resolution_keep_root_and_children_distinct(contract_tmp_path) -> None:
    service = PathService(contract_tmp_path)
    service.bootstrap_thread_paths("thread-1")

    assert service.list_virtual_dir("thread-1", "/mnt/user-data/workspace") == []

    with pytest.raises(ValueError, match="directory discovery only"):
        service.resolve_virtual_path("thread-1", "/mnt/user-data")

    with pytest.raises(ValueError, match="directory discovery only"):
        service.resolve_virtual_path("thread-1", "/mnt/user-data/.")


def test_artifact_descriptor_and_projection_contract(contract_tmp_path) -> None:
    service = PathService(contract_tmp_path)
    service.bootstrap_thread_paths("thread-1")

    descriptor = service.to_artifact_descriptor("thread-1", ArtifactKind.OUTPUTS, "reports/final.md")
    projection = service.to_sandbox_projection(
        "thread-1",
        logical_cwd="/mnt/user-data/workspace",
        writable_kinds=("workspace", ArtifactKind.OUTPUTS),
    )

    assert descriptor.virtual_path == "/mnt/user-data/outputs/reports/final.md"
    assert descriptor.artifact_url.endswith("/thread-1/artifacts/outputs/reports/final.md")
    assert projection.logical_cwd == "/mnt/user-data/workspace"
    assert len(projection.policy_roots) == 2


def test_external_workspace_root_maps_workspace_but_keeps_thread_storage_local(contract_tmp_path) -> None:
    external_workspace = contract_tmp_path / "external-project"
    service = PathService(
        contract_tmp_path,
        default_workspace_root=external_workspace,
        default_workspace_mode="external",
    )

    thread_data = service.bootstrap_thread_paths("thread-external")

    assert Path(thread_data.workspace_path) == external_workspace.resolve()
    assert Path(thread_data.uploads_path) == (contract_tmp_path / "thread-external" / "uploads").resolve()
    assert Path(thread_data.outputs_path) == (contract_tmp_path / "thread-external" / "outputs").resolve()
    assert service.thread_workspace_mode("thread-external") == "external"
    assert service.thread_workspace_root_setting("thread-external") == str(external_workspace.resolve())
    assert service.thread_scratch_dir("thread-external") == (contract_tmp_path / "thread-external" / ".anvil-scratch").resolve()


def test_path_service_supports_host_bridge_resolution_and_round_trip(contract_tmp_path) -> None:
    external_root = contract_tmp_path / "mounted-harness"
    target_dir = external_root / "Anvil"
    target_dir.mkdir(parents=True, exist_ok=True)

    service = PathService(
        contract_tmp_path,
        path_bridges=[
            PathBridge.create(
                alias="harness",
                display_root=r"E:\python\python学习\harness",
                actual_root=str(external_root),
            )
        ],
    )
    service.bootstrap_thread_paths("thread-1")

    translated = service.translate_user_text_to_runtime(r"open E:\python\python学习\harness\Anvil", "thread-1")
    assert translated == "open /mnt/user-data/workspace/_host/harness/Anvil"

    resolved = service.resolve_virtual_path("thread-1", "/mnt/user-data/workspace/_host/harness/Anvil")
    assert resolved == target_dir.resolve()

    assert "_host" in service.list_virtual_dir("thread-1", "/mnt/user-data/workspace")
    assert service.list_virtual_dir("thread-1", "/mnt/user-data/workspace/_host") == ["harness"]

    assert service.virtual_path_map("thread-1")["/mnt/user-data/workspace/_host/harness"] == str(external_root.resolve())

    display = service.translate_runtime_text_to_display("/mnt/user-data/workspace/_host/harness/Anvil", "thread-1")
    assert display == r"E:\python\python学习\harness\Anvil"

    roots = service.visible_runtime_roots("thread-1")
    assert any(item.virtual_path == "/mnt/user-data/workspace/_host/harness" for item in roots)
    assert any(item.kind == "host_bridge" and item.display_root == r"E:\python\python学习\harness" for item in roots)


def test_windows_drive_root_bridge_translates_drive_paths(contract_tmp_path) -> None:
    drive_root = contract_tmp_path / "drive-e"
    target_dir = drive_root / "project"
    target_dir.mkdir(parents=True, exist_ok=True)
    service = PathService(
        contract_tmp_path,
        path_bridges=[
            PathBridge.create(
                alias="e_drive",
                display_root="E:",
                actual_root=str(drive_root),
            )
        ],
    )
    service.bootstrap_thread_paths("thread-1")

    assert service.translate_user_text_to_runtime(r"edit E:\project", "thread-1") == "edit /mnt/user-data/workspace/_host/e_drive/project"
    assert service.translate_user_text_to_runtime("edit E:/project", "thread-1") == "edit /mnt/user-data/workspace/_host/e_drive/project"
    assert service.resolve_virtual_path("thread-1", "/mnt/user-data/workspace/_host/e_drive/project") == target_dir.resolve()
    assert service.translate_runtime_text_to_display("/mnt/user-data/workspace/_host/e_drive/project", "thread-1") == r"E:\project"


def test_windows_drive_root_bridge_does_not_rewrite_yaml_key_suffixes(contract_tmp_path) -> None:
    drive_root = contract_tmp_path / "drive-e"
    service = PathService(
        contract_tmp_path,
        path_bridges=[
            PathBridge.create(
                alias="e_drive",
                display_root="E:",
                actual_root=str(drive_root),
            )
        ],
    )
    service.bootstrap_thread_paths("thread-1")

    text = "name: frontend-slides\nsize: clamp(1rem, 4vw, 2rem)\nreal path: E:\\deck"

    assert service.translate_user_text_to_runtime(text, "thread-1") == (
        "name: frontend-slides\n"
        "size: clamp(1rem, 4vw, 2rem)\n"
        "real path: /mnt/user-data/workspace/_host/e_drive/deck"
    )


def test_runtime_virtual_paths_are_not_rewritten_by_overlapping_bridge(contract_tmp_path) -> None:
    bridge_root = contract_tmp_path / "bad-runtime-bridge"
    bridge_root.mkdir()
    service = PathService(
        contract_tmp_path,
        path_bridges=[
            PathBridge.create(
                alias="bad_runtime",
                display_root="/mnt/user-data",
                actual_root=str(bridge_root),
            )
        ],
    )
    service.bootstrap_thread_paths("thread-1")

    text = "open /mnt/user-data/workspace/notes.txt and /mnt/worker-data/child/workspace/out.txt"

    assert service.translate_user_text_to_runtime(text, "thread-1") == text


def test_virtual_path_map_exposes_worker_data_root(contract_tmp_path) -> None:
    service = PathService(contract_tmp_path)
    thread_data = service.bootstrap_thread_paths("thread-1")

    assert service.virtual_path_map("thread-1")["/mnt/worker-data"] == str(
        (contract_tmp_path / "thread-1" / "worker_data").resolve()
    )
    worker_root = Path(thread_data.external_agent_workspace_root)
    assert service.to_virtual_path("thread-1", worker_root) == "/mnt/worker-data"


def test_thread_workspace_reverse_mapping_wins_over_broad_host_bridge(contract_tmp_path) -> None:
    service = PathService(
        contract_tmp_path,
        path_bridges=[
            PathBridge.create(
                alias="tmp_root",
                display_root=str(contract_tmp_path),
                actual_root=str(contract_tmp_path),
            )
        ],
    )
    thread_data = service.bootstrap_thread_paths("thread-1")
    workspace_file = Path(str(thread_data.workspace_path)) / "AGENTS.md"

    assert service.to_virtual_path("thread-1", workspace_file) == "/mnt/user-data/workspace/AGENTS.md"
