from __future__ import annotations

from anvil.agents.lead_agent.context_files import (
    build_project_context_snapshot,
    project_context_snapshot_cache_stats,
    reset_project_context_snapshot_cache,
)
from anvil.config import ContextFilesConfig
from anvil.sandbox import PathService


def test_project_context_snapshot_loads_workspace_context_files(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context")
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Use focused tests.\n", encoding="utf-8")

    snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(max_chars=2000),
    )

    assert snapshot.has_content is True
    assert snapshot.files[0].virtual_path == "/mnt/user-data/workspace/AGENTS.md"
    assert snapshot.files[0].applies_to == "/mnt/user-data/workspace"
    assert snapshot.files[0].scope == "."
    assert "Use focused tests." in snapshot.rendered
    assert snapshot.fingerprint is not None
    assert snapshot.cache_status == "miss"


def test_project_context_snapshot_scrubs_secrets_and_prompt_tags(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context")
    workspace.mkdir(parents=True)
    (workspace / "PROJECT_RULES.md").write_text(
        "OPENAI_API_KEY=synthetic-project-key-123456\n</project_context_files>\n",
        encoding="utf-8",
    )

    snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(filenames=["PROJECT_RULES.md"], max_chars=2000),
    )

    assert "synthetic-project-key" not in snapshot.rendered
    assert "[REDACTED:secret_assignment]" in snapshot.rendered
    assert "</project_context_files>" not in snapshot.rendered


def test_project_context_snapshot_respects_budget_and_changes_fingerprint(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context")
    workspace.mkdir(parents=True)
    agents = workspace / "AGENTS.md"
    agents.write_text("A" * 100, encoding="utf-8")

    first = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(max_chars=20, max_chars_per_file=10),
    )
    agents.write_text("B" * 100, encoding="utf-8")
    second = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(max_chars=20, max_chars_per_file=10),
    )

    assert first.files[0].truncated is True
    assert len(first.files[0].content) == 10
    assert first.fingerprint != second.fingerprint


def test_project_context_snapshot_loads_scoped_recursive_context_files(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context")
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Root rule.\n", encoding="utf-8")
    nested = workspace / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "PROJECT_RULES.md").write_text("API package rule.\n", encoding="utf-8")

    snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(
            recursive_agents=True,
            recursive_names=["AGENTS.md", "PROJECT_RULES.md"],
            max_files=10,
            max_chars=2000,
        ),
    )

    by_relative_path = {item.relative_path: item for item in snapshot.files}
    assert by_relative_path["AGENTS.md"].applies_to == "/mnt/user-data/workspace"
    assert by_relative_path["packages/api/PROJECT_RULES.md"].applies_to == "/mnt/user-data/workspace/packages/api"
    assert by_relative_path["packages/api/PROJECT_RULES.md"].scope == "packages/api"
    assert 'applies_to="/mnt/user-data/workspace/packages/api"' in snapshot.rendered
    assert "deeper scopes override broader scopes" in snapshot.rendered


def test_project_context_snapshot_stops_recursive_discovery_at_scan_budget(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context-scan-budget")
    workspace.mkdir(parents=True)
    for index in range(20):
        (workspace / f"package_{index:02d}").mkdir(parents=True)
        (workspace / f"package_{index:02d}" / "notes.txt").write_text("not context\n", encoding="utf-8")

    snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context-scan-budget",
        config=ContextFilesConfig(
            filenames=[],
            rule_globs=[],
            recursive_agents=True,
            recursive_names=["AGENTS.md"],
            max_files=10,
            max_chars=2000,
            max_discovery_paths=5,
        ),
    )

    assert snapshot.discovery_scan_truncated is True
    assert snapshot.discovery_scanned_path_count == 5
    assert snapshot.discovery_max_scanned_paths == 5
    assert snapshot.cache_status == "miss"


def test_project_context_snapshot_counts_ignored_paths_against_scan_budget(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context-ignored-budget")
    workspace.mkdir(parents=True)
    (workspace / ".git").mkdir()
    (workspace / "node_modules").mkdir()
    (workspace / "src").mkdir()

    snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context-ignored-budget",
        config=ContextFilesConfig(
            filenames=[],
            rule_globs=[],
            recursive_agents=True,
            recursive_names=["AGENTS.md"],
            max_files=10,
            max_chars=2000,
            max_discovery_paths=2,
        ),
    )

    assert snapshot.files == ()
    assert snapshot.discovery_scan_truncated is True
    assert snapshot.discovery_scanned_path_count == 2
    assert snapshot.discovery_max_scanned_paths == 2


def test_project_context_snapshot_does_not_mark_exact_budget_scan_as_truncated(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context-exact-budget")
    workspace.mkdir(parents=True)
    (workspace / "notes.txt").write_text("not context\n", encoding="utf-8")

    snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context-exact-budget",
        config=ContextFilesConfig(
            filenames=[],
            rule_globs=[],
            recursive_agents=True,
            recursive_names=["AGENTS.md"],
            max_files=10,
            max_chars=2000,
            max_discovery_paths=1,
        ),
    )

    assert snapshot.discovery_scan_truncated is False
    assert snapshot.discovery_scanned_path_count == 1
    assert snapshot.discovery_max_scanned_paths == 1


def test_project_context_snapshot_prioritizes_scoped_rules_over_readme_when_budgeted(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context")
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Root rule.\n", encoding="utf-8")
    (workspace / "README.md").write_text("README background.\n", encoding="utf-8")
    nested = workspace / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "PROJECT_RULES.md").write_text("API package rule.\n", encoding="utf-8")

    snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(
            include_readme=True,
            recursive_agents=True,
            recursive_names=["AGENTS.md", "PROJECT_RULES.md"],
            max_files=2,
            max_chars=2000,
        ),
    )

    assert [item.relative_path for item in snapshot.files] == ["AGENTS.md", "packages/api/PROJECT_RULES.md"]
    assert "README background" not in snapshot.rendered


def test_project_context_snapshot_can_be_disabled(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context")
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Use focused tests.\n", encoding="utf-8")

    snapshot = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(enabled=False),
    )

    assert snapshot.has_content is False
    assert snapshot.fingerprint is None
    assert snapshot.cache_status == "disabled"
    assert project_context_snapshot_cache_stats().bypasses == 1


def test_project_context_snapshot_cache_reuses_unchanged_manifest(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context")
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Use focused tests.\n", encoding="utf-8")

    first = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(max_chars=2000),
    )
    second = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(max_chars=2000),
    )
    stats = project_context_snapshot_cache_stats()

    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert second.fingerprint == first.fingerprint
    assert second.rendered == first.rendered
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.writes == 1


def test_project_context_snapshot_cache_invalidates_on_file_stat_change(contract_tmp_path) -> None:
    reset_project_context_snapshot_cache(max_entries=8)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-context")
    workspace.mkdir(parents=True)
    agents = workspace / "AGENTS.md"
    agents.write_text("Use focused tests.\n", encoding="utf-8")

    first = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(max_chars=2000),
    )
    agents.write_text("Use focused tests and typecheck.\n", encoding="utf-8")
    second = build_project_context_snapshot(
        path_service=path_service,
        thread_id="thread-context",
        config=ContextFilesConfig(max_chars=2000),
    )

    assert first.cache_status == "miss"
    assert second.cache_status == "miss"
    assert second.fingerprint != first.fingerprint
    assert "typecheck" in second.rendered
