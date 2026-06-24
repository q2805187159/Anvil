from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from anvil.config import (
    bootstrap_anvil_profile_home,
    ConfigLayer,
    ConfigLayerKind,
    ConfigService,
    build_config_layers_from_file,
    build_mcp_config_layer_from_file,
    build_default_config_layers,
    build_env_bootstrap_config_layers_from_env,
    init_config_from_example,
    McpTransportKind,
    resolve_anvil_config_path,
    resolve_config_path,
    resolve_mcp_config_paths,
    resolve_plugin_config_paths,
)


def test_resolve_config_path_prefers_explicit_then_env_then_home(monkeypatch: pytest.MonkeyPatch, contract_tmp_path: Path) -> None:
    explicit = contract_tmp_path / "explicit.yaml"
    explicit.write_text("default_model: explicit\nmodels: []\n", encoding="utf-8")
    anvil_home = contract_tmp_path / ".anvil-home"
    home_config = anvil_home / "config.yaml"
    home_config.parent.mkdir(parents=True)
    home_config.write_text("default_model: home\nmodels: {}\n", encoding="utf-8")
    env_config = contract_tmp_path / "env.yaml"
    env_config.write_text("default_model: env\nmodels: []\n", encoding="utf-8")

    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(env_config))

    assert resolve_config_path(explicit) == explicit.resolve()
    assert resolve_config_path() == env_config.resolve()

    monkeypatch.delenv("ANVIL_CONFIG_PATH")
    assert resolve_config_path() == home_config.resolve()


def test_resolve_config_path_ignores_cwd_and_repo_config_without_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
    contract_tmp_path: Path,
) -> None:
    monkeypatch.delenv("ANVIL_CONFIG_PATH", raising=False)
    anvil_home = contract_tmp_path / ".anvil-home"
    home_config = anvil_home / "config.yaml"
    home_config.parent.mkdir(parents=True)
    home_config.write_text("default_model: home\nmodels: {}\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))
    repo_root = contract_tmp_path / "repo"
    repo_root.mkdir()
    repo_config = repo_root / "config.yaml"
    repo_config.write_text("default_model: repo\nmodels: []\n", encoding="utf-8")
    cwd_root = contract_tmp_path / "cwd"
    cwd_root.mkdir()
    cwd_config = cwd_root / "config.yaml"
    cwd_config.write_text("default_model: cwd\nmodels: []\n", encoding="utf-8")

    original_cwd = Path.cwd()
    try:
        os.chdir(cwd_root)
        assert resolve_config_path(repo_root=repo_root) == home_config.resolve()
        assert resolve_config_path() == home_config.resolve()
    finally:
        os.chdir(original_cwd)


def test_get_repo_root_caches_marker_based_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from anvil.config import loader as config_loader_module

    marker_checks = 0
    original_exists = Path.exists

    def count_repo_marker_checks(path: Path) -> bool:
        nonlocal marker_checks
        if path.name in {config_loader_module.CONFIG_EXAMPLE_FILE_NAME, "backend", "README.md"}:
            marker_checks += 1
        return original_exists(path)

    monkeypatch.setattr(config_loader_module, "_REPO_ROOT_CACHE", None, raising=False)
    monkeypatch.setattr(config_loader_module, "_REPO_ROOT_MARKER_MISS_CACHE", False, raising=False)
    monkeypatch.setattr(Path, "exists", count_repo_marker_checks)

    first = config_loader_module.get_repo_root()
    first_marker_checks = marker_checks
    second = config_loader_module.get_repo_root()

    assert second == first
    assert first_marker_checks > 0
    assert marker_checks == first_marker_checks


def test_get_repo_root_caches_marker_miss_without_freezing_cwd(
    monkeypatch: pytest.MonkeyPatch,
    contract_tmp_path: Path,
) -> None:
    from anvil.config import loader as config_loader_module

    cwd_one = contract_tmp_path / "cwd-one"
    cwd_two = contract_tmp_path / "cwd-two"
    cwd_one.mkdir()
    cwd_two.mkdir()
    marker_checks = 0
    original_exists = Path.exists

    def hide_repo_markers(path: Path) -> bool:
        nonlocal marker_checks
        if path.name in {config_loader_module.CONFIG_EXAMPLE_FILE_NAME, "backend", "README.md"}:
            marker_checks += 1
            return False
        return original_exists(path)

    monkeypatch.setattr(config_loader_module, "_REPO_ROOT_CACHE", None, raising=False)
    monkeypatch.setattr(config_loader_module, "_REPO_ROOT_MARKER_MISS_CACHE", False, raising=False)
    monkeypatch.setattr(Path, "exists", hide_repo_markers)

    monkeypatch.chdir(cwd_one)
    first = config_loader_module.get_repo_root()
    first_marker_checks = marker_checks
    monkeypatch.chdir(cwd_two)
    second = config_loader_module.get_repo_root()

    assert first == cwd_one.resolve()
    assert second == cwd_two.resolve()
    assert first_marker_checks > 0
    assert marker_checks == first_marker_checks


def test_build_config_layers_from_file_normalizes_list_based_model_entries(monkeypatch: pytest.MonkeyPatch, contract_tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: openai-main
models:
  - name: openai-main
    display_name: OpenAI Main
    use: langchain_openai:ChatOpenAI
    model: gpt-5.4
    api_key: $OPENAI_API_KEY
    base_url: https://example.test/v1
        """.strip(),
        encoding="utf-8",
    )

    layers = build_config_layers_from_file(config_path)
    result = ConfigService().resolve(layers)
    model = result.effective_config.models["openai-main"]

    assert result.effective_config.default_model == "openai-main"
    assert model.display_name == "OpenAI Main"
    assert model.model == "gpt-5.4"
    assert model.model_name == "gpt-5.4"
    assert model.use == "langchain_openai:ChatOpenAI"
    assert model.api_key == "$OPENAI_API_KEY"
    assert model.base_url == "https://example.test/v1"


def test_compact_llm_providers_expand_to_model_configs(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: minimax
  fallback_models:
    - custom_local
  defaults:
    timeout: 12.5
    max_retries: 4
  providers:
    minimax:
      model:
        - MiniMax-M2.7
        - MiniMax-M2.7-highspeed
        - MiniMax-M2.5
        - MiniMax-M2.5-highspeed
        - MiniMax-M1
      default_model: MiniMax-M2.7
    custom_local:
      provider: custom_openai
      model: local-model
      base_url: http://127.0.0.1:8000/v1
      api_key_env: LOCAL_API_KEY
      supports_vision: true
      when_thinking_enabled:
        extra_body:
          thinking:
            type: enabled
  subsystems:
    title: custom_local
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    minimax = result.effective_config.models["minimax"]
    custom = result.effective_config.models["custom_local"]

    assert result.effective_config.default_model == "minimax"
    assert result.effective_config.llm.fallback_models == ["custom_local"]
    assert result.effective_config.subsystem_models["title"] == "custom_local"
    assert minimax.model == [
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
        "MiniMax-M2.5",
        "MiniMax-M2.5-highspeed",
        "MiniMax-M1",
    ]
    assert minimax.model_catalog == [
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
        "MiniMax-M2.5",
        "MiniMax-M2.5-highspeed",
        "MiniMax-M1",
    ]
    assert minimax.default_model == "MiniMax-M2.7"
    assert minimax.model_name == "MiniMax-M2.7"
    assert minimax.effective_model_name() == "MiniMax-M2.7"
    assert minimax.context_window_tokens == 1048576
    assert minimax.effective_context_window_tokens() == 1048576
    assert minimax.effective_auto_compact_threshold_tokens() == 786432
    assert minimax.api_key == "$MINIMAX_API_KEY"
    assert minimax.timeout == 12.5
    assert minimax.max_retries == 4
    assert custom.model == "local-model"
    assert custom.base_url == "http://127.0.0.1:8000/v1"
    assert custom.api_key == "$LOCAL_API_KEY"
    assert custom.supports_vision is True
    assert custom.when_thinking_enabled == {"extra_body": {"thinking": {"type": "enabled"}}}


def test_curator_config_aliases_are_normalized(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
skills_config:
  enabled: true
  curator:
    enabled: true
    schedule: weekly
    auto_merge: false
    auto_review: false
    pin_protection: false
    min_idle_hours: 2
    core_score_threshold: 250
    observe_score_threshold: 15
    observe_min_age_days: 10
    template_promotion_enabled: true
    template_use_threshold: 2
    template_context_threshold: 2
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    curator = result.effective_config.skills_config.curator

    assert curator.automation_enabled is True
    assert curator.schedule == "weekly"
    assert curator.interval_seconds == 7 * 24 * 60 * 60
    assert curator.auto_merge is False
    assert curator.auto_review is False
    assert curator.pin_protection is False
    assert curator.force is True
    assert curator.min_idle_hours == 2
    assert curator.core_score_threshold == 250
    assert curator.observe_score_threshold == 15
    assert curator.observe_min_age_days == 10
    assert curator.template_promotion_enabled is True
    assert curator.template_use_threshold == 2
    assert curator.template_context_threshold == 2


def test_trajectory_export_config_is_typed(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
trajectory_export:
  enabled: true
  export_root: ./runs/trajectories
  default_format: sharegpt
  batch_include_entries_default: true
  batch_write_jsonl_default: false
  batch_min_quality_status_default: passed
  include_reasoning: true
  include_parsed_tool_calls: false
  scrub_secrets: true
  compression:
    enabled: true
    max_turns: 12
    keep_first_turns: 1
    keep_last_turns: 6
    max_message_chars: 3000
    max_tool_result_chars: 1000
    max_metadata_chars: 500
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    trajectory = result.effective_config.trajectory_export

    assert trajectory.enabled is True
    assert trajectory.export_root == "./runs/trajectories"
    assert trajectory.default_format == "sharegpt"
    assert trajectory.batch_include_entries_default is True
    assert trajectory.batch_write_jsonl_default is False
    assert trajectory.batch_min_quality_status_default == "passed"
    assert trajectory.include_reasoning is True
    assert trajectory.include_parsed_tool_calls is False
    assert trajectory.scrub_secrets is True
    assert trajectory.compression.max_turns == 12
    assert trajectory.compression.keep_last_turns == 6
    assert trajectory.compression.max_tool_result_chars == 1000


def test_trajectory_export_rejects_invalid_batch_quality_gate(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
trajectory_export:
  batch_min_quality_status_default: excellent
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="batch_min_quality_status_default"):
        ConfigService().resolve(build_config_layers_from_file(config_path))


def test_scheduled_tasks_config_is_typed(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
scheduled_tasks:
  enabled: true
  tick_seconds: 30
  state_path: .anvil/scheduled-tasks/tasks.json
  default_execution_mode: full_access
  default_profile: minimax_global
  default_model: MiniMax-M2.5
  max_due_per_tick: 5
  output_root: .anvil/scheduled-task-output
  prompt_safety_scan_enabled: false
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    scheduled_tasks = result.effective_config.scheduled_tasks

    assert scheduled_tasks.enabled is True
    assert scheduled_tasks.tick_seconds == 30
    assert scheduled_tasks.default_execution_mode == "full_access"
    assert scheduled_tasks.default_profile == "minimax_global"
    assert scheduled_tasks.max_due_per_tick == 5
    assert scheduled_tasks.prompt_safety_scan_enabled is False


def test_hcms_maintenance_config_is_typed(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
hcms:
  enabled: true
  maintenance:
    enabled: true
    policy: review
    layer_id: workspace
    limit: 17
    execute: true
    automation_enabled: false
    tick_seconds: 45
    interval_hours: 2
    min_idle_seconds: 90
    max_archive_per_run: 1
    max_quality_inspections_per_run: 6
    max_reinforce_per_run: 4
    min_quality_score_for_execute: 0.66
    max_quality_issues_for_execute: 18
    run_reflection_due_jobs: false
    include_health: false
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    maintenance = result.effective_config.hcms.maintenance

    assert maintenance.enabled is True
    assert maintenance.policy == "review"
    assert maintenance.layer_id == "workspace"
    assert maintenance.limit == 17
    assert maintenance.execute is True
    assert maintenance.automation_enabled is False
    assert maintenance.tick_seconds == 45
    assert maintenance.interval_seconds == 7200
    assert maintenance.min_idle_seconds == 90
    assert maintenance.max_archive_per_run == 1
    assert maintenance.max_quality_inspections_per_run == 6
    assert maintenance.max_reinforce_per_run == 4
    assert maintenance.min_quality_score_for_execute == 0.66
    assert maintenance.max_quality_issues_for_execute == 18
    assert maintenance.run_reflection_due_jobs is False
    assert maintenance.include_health is False


def test_git_config_is_required_and_typed_for_hcms_version_control(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
git:
  enabled: true
  required: true
  provider: gitlab
  token_env: CUSTOM_GIT_TOKEN
  user_name: Anvil Operator
  user_email: operator@example.test
  remote_url: https://gitlab.example.test/team/repo.git
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    git = result.effective_config.git

    assert git.enabled is True
    assert git.required is True
    assert git.provider == "gitlab"
    assert git.token_env == "CUSTOM_GIT_TOKEN"
    assert git.user_name == "Anvil Operator"
    assert git.user_email == "operator@example.test"
    assert git.remote_url == "https://gitlab.example.test/team/repo.git"


def test_git_config_defaults_to_required_github_token_for_hcms() -> None:
    result = ConfigService().resolve([])
    git = result.effective_config.git

    assert git.enabled is True
    assert git.required is True
    assert git.provider == "github"
    assert git.token_env == "GITHUB_TOKEN"


def test_hcms_storage_backend_config_is_typed_and_bounded(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
hcms:
  enabled: true
  storage_backend: markdown
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))

    assert result.effective_config.hcms.storage_backend == "hybrid"

    invalid_path = contract_tmp_path / "invalid-config.yaml"
    invalid_path.write_text(
        """
hcms:
  enabled: true
  storage_backend: remote_magic
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="storage_backend"):
        ConfigService().resolve(build_config_layers_from_file(invalid_path))


def test_hcms_recall_cache_and_mmr_config_is_typed_and_bounded(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
hcms:
  enabled: true
  recall:
    max_candidates: 7
    turn_recall_token_budget: 320
    bm25_weight: 1.7
    vector_weight: -0.2
    graph_weight: 0.3
    temporal_weight: 0.2
    rrf_k: 0
    enable_adaptive_weights: false
    enable_cache: true
    cache_ttl: 9
    cache_max_entries: 2
    enable_mmr: false
    mmr_lambda: 2.5
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    recall = result.effective_config.hcms.recall

    assert recall.max_candidates == 7
    assert recall.turn_recall_token_budget == 320
    assert recall.bm25_weight == 1.0
    assert recall.vector_weight == 0.0
    assert recall.graph_weight == 0.3
    assert recall.temporal_weight == 0.2
    assert recall.rrf_k == 1
    assert recall.enable_adaptive_weights is False
    assert recall.enable_cache is True
    assert recall.cache_ttl == 9
    assert recall.cache_max_entries == 2
    assert recall.enable_mmr is False
    assert recall.mmr_lambda == 1.0


def test_hcms_update_queue_config_is_typed_and_bounded(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
hcms:
  enabled: true
  update_queue:
    enabled: false
    debounce_seconds: -4
    min_window_seconds: 3
    default_window_seconds: 1
    max_window_seconds: 2
    min_batch_turns: 0
    max_batch_turns: 0
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    queue = result.effective_config.hcms.update_queue

    assert queue.enabled is False
    assert queue.debounce_seconds == 0.0
    assert queue.min_window_seconds == 3.0
    assert queue.default_window_seconds == 3.0
    assert queue.max_window_seconds == 3.0
    assert queue.min_batch_turns == 1
    assert queue.max_batch_turns == 1


def test_hcms_updater_config_accepts_structured_mode_and_bounds_threshold(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
hcms:
  enabled: true
  updater:
    mode: json-plan
    fact_confidence_threshold: 1.5
    fail_open: true
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    updater = result.effective_config.hcms.updater

    assert updater.mode == "structured"
    assert updater.fact_confidence_threshold == 1.0
    assert updater.fail_open is True


def test_self_upgrade_defaults_are_automatic_but_bounded(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
hcms:
  enabled: true
skills_config:
  enabled: true
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    memory_maintenance = result.effective_config.hcms.maintenance
    curator = result.effective_config.skills_config.curator

    assert memory_maintenance.automation_enabled is True
    assert memory_maintenance.execute is True
    assert memory_maintenance.max_archive_per_run == 2
    assert memory_maintenance.max_quality_inspections_per_run == 8
    assert memory_maintenance.max_quality_issues_for_execute == 30
    assert curator.automation_enabled is True
    assert curator.dry_run is False
    assert curator.auto_review is True
    assert curator.auto_merge is True
    assert curator.auto_promote_procedures is True
    assert curator.pin_protection is True
    assert curator.max_actions_per_run == 25


def test_code_semantics_config_is_typed(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
code_semantics:
  backend: lsp_jsonrpc
  external_index_path: .anvil/code/semantic-index.json
  lsp_command:
    - python
    - fake_lsp.py
  lsp_cwd: .anvil/code
  lsp_env:
    PYTHONUNBUFFERED: "1"
  lsp_timeout_seconds: 3
  lsp_session_idle_ttl_seconds: 42
  lsp_stderr_max_chars: 512
  lsp_initialization_options:
    index: shallow
  fallback_to_static: false
  validate_freshness: false
  watch_default_auto_recover: false
  watch_state_ttl_seconds: 120
  watch_max_entries: 8
  watch_drift_path_limit: 6
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    code_semantics = result.effective_config.code_semantics

    assert code_semantics.backend == "lsp_jsonrpc"
    assert code_semantics.external_index_path == ".anvil/code/semantic-index.json"
    assert code_semantics.lsp_command == ["python", "fake_lsp.py"]
    assert code_semantics.lsp_cwd == ".anvil/code"
    assert code_semantics.lsp_env == {"PYTHONUNBUFFERED": "1"}
    assert code_semantics.lsp_timeout_seconds == 3
    assert code_semantics.lsp_session_idle_ttl_seconds == 42
    assert code_semantics.lsp_stderr_max_chars == 512
    assert code_semantics.lsp_initialization_options == {"index": "shallow"}
    assert code_semantics.fallback_to_static is False
    assert code_semantics.validate_freshness is False
    assert code_semantics.watch_default_auto_recover is False
    assert code_semantics.watch_state_ttl_seconds == 120
    assert code_semantics.watch_max_entries == 8
    assert code_semantics.watch_drift_path_limit == 6


def test_terminal_backend_config_is_typed(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
terminal:
  active_backend: docker_lab
  logs_dir: ./terminal-logs
  backends:
    docker_lab:
      kind: docker
      label: Docker Lab
      enabled: true
      image: python:3.12-slim
      working_dir: /workspace
      env_passthrough: [EXTRA_ALLOWED]
      env_prefix_passthrough: [PROJECT_]
      resource_limits:
        cpus: 2
        memory: 4g
    ssh_lab:
      kind: ssh
      label: SSH Lab
      enabled: false
      host: example.test
      username: anvil
    daytona_lab:
      kind: daytona
      enabled: false
      sandbox_id: sandbox-123
      sync:
        mode: provider_workspace
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    terminal = result.effective_config.terminal

    assert terminal.active_backend == "docker_lab"
    assert terminal.logs_dir == "./terminal-logs"
    assert terminal.backends["docker_lab"].kind == "docker"
    assert terminal.backends["docker_lab"].label == "Docker Lab"
    assert terminal.backends["docker_lab"].resource_limits["memory"] == "4g"
    assert terminal.backends["docker_lab"].env_passthrough == ["EXTRA_ALLOWED"]
    assert terminal.backends["docker_lab"].env_prefix_passthrough == ["PROJECT_"]
    assert terminal.backends["ssh_lab"].enabled is False
    assert terminal.backends["daytona_lab"].sandbox_id == "sandbox-123"
    assert terminal.backends["daytona_lab"].sync["mode"] == "provider_workspace"
    assert "local" in terminal.backends


def test_context_files_config_is_typed(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
context_files:
  enabled: true
  filenames: [AGENTS.md, PROJECT_RULES.md]
  rule_globs: [.cursor/rules/*.md]
  include_readme: true
  recursive_agents: true
  recursive_names: [AGENTS.md, PROJECT_RULES.md]
  max_files: 4
  max_chars: 5000
  max_chars_per_file: 1200
  max_discovery_paths: 321
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    context_files = result.effective_config.context_files

    assert context_files.enabled is True
    assert context_files.filenames == ["AGENTS.md", "PROJECT_RULES.md"]
    assert context_files.include_readme is True
    assert context_files.recursive_agents is True
    assert context_files.recursive_names == ["AGENTS.md", "PROJECT_RULES.md"]
    assert context_files.max_files == 4
    assert context_files.max_chars == 5000
    assert context_files.max_chars_per_file == 1200
    assert context_files.max_discovery_paths == 321


def test_compact_llm_provider_catalog_expands_common_company_presets(
    monkeypatch: pytest.MonkeyPatch,
    contract_tmp_path: Path,
) -> None:
    monkeypatch.setenv("KIMI_BASE_URL", "https://custom.moonshot.example/v1")
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: doubao
  defaults:
    request_timeout: 45.0
    max_retries: 5
  providers:
    doubao:
      model: doubao-seed-1-8-251228
    claude:
      provider: anthropic
      model: claude-3-5-sonnet-20241022
    gemini_native:
      provider: gemini_native
      model: gemini-2.5-pro
    kimi:
      provider: kimi
      model: kimi-k2.5
      base_url: ${KIMI_BASE_URL}
    novita:
      provider: novita
      model: deepseek/deepseek-v3.2
    minimax_global:
      provider: minimax_global
      model: MiniMax-M2.5
    minimax_cn:
      provider: minimax_cn
      model: MiniMax-M2.7
    MiMo:
      provider: mimo
      model:
        - mimo-v2.5-pro
        - mimo-v2.5
        - mimo-v2-pro
        - mimo-v2-omni
        - mimo-v2-flash
      default_model: mimo-v2.5-pro
      model_context_windows:
        mimo-v2.5-pro: 1048576
        mimo-v2.5: 1048576
        mimo-v2-pro: 1048576
        mimo-v2-omni: 1048576
        mimo-v2-flash: 32768
      model_auto_compact_thresholds:
        mimo-v2.5-pro: 786432
        mimo-v2.5: 786432
        mimo-v2-pro: 786432
        mimo-v2-omni: 786432
        mimo-v2-flash: 24576
    openrouter:
      model: google/gemini-2.5-flash-preview
      provider_settings:
        extra_body:
          provider:
            order: [google]
    qwen_vllm:
      provider: vllm
      model: Qwen/Qwen3-32B
      base_url: http://localhost:8000/v1
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    models = result.effective_config.models

    assert result.effective_config.default_model == "doubao"
    assert models["doubao"].base_url == "https://ark.cn-beijing.volces.com/api/v3"
    assert models["doubao"].api_key == "$VOLCENGINE_API_KEY"
    assert "api_key_env" not in models["doubao"].provider_settings
    assert models["doubao"].supports_thinking is True
    assert models["claude"].provider_kind.value == "anthropic_compatible"
    assert models["claude"].use == "anvil.agents.provider_adapters:AnvilAnthropicChatModel"
    assert models["claude"].api_key == "$ANTHROPIC_API_KEY"
    assert models["gemini_native"].use == "langchain_google_genai:ChatGoogleGenerativeAI"
    assert models["gemini_native"].api_key is None
    assert models["gemini_native"].provider_settings["gemini_api_key"] == "$GEMINI_API_KEY"
    assert models["kimi"].base_url == "https://custom.moonshot.example/v1"
    assert models["kimi"].api_key == "$MOONSHOT_API_KEY"
    assert models["novita"].base_url == "https://api.novita.ai/openai"
    assert models["minimax_global"].base_url == "https://api.minimax.io/v1"
    assert models["minimax_cn"].base_url == "https://api.minimaxi.com/v1"
    assert models["MiMo"].model == [
        "mimo-v2.5-pro",
        "mimo-v2.5",
        "mimo-v2-pro",
        "mimo-v2-omni",
        "mimo-v2-flash",
    ]
    assert models["MiMo"].model_catalog == [
        "mimo-v2.5-pro",
        "mimo-v2.5",
        "mimo-v2-pro",
        "mimo-v2-omni",
        "mimo-v2-flash",
    ]
    assert models["MiMo"].default_model == "mimo-v2.5-pro"
    assert models["MiMo"].model_name == "mimo-v2.5-pro"
    assert models["MiMo"].effective_model_name() == "mimo-v2.5-pro"
    assert models["MiMo"].model_context_windows["mimo-v2-flash"] == 32768
    assert models["MiMo"].model_auto_compact_thresholds["mimo-v2-flash"] == 24576
    assert models["MiMo"].base_url == "https://token-plan-cn.xiaomimimo.com/v1"
    assert models["MiMo"].api_key == "$MIMO_API_KEY"
    assert models["openrouter"].provider_settings["extra_body"]["provider"]["order"] == ["google"]
    assert models["qwen_vllm"].when_thinking_enabled == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": True}}
    }


def test_compact_llm_provider_allows_custom_url_and_braced_env_placeholders(
    monkeypatch: pytest.MonkeyPatch,
    contract_tmp_path: Path,
) -> None:
    monkeypatch.setenv("CUSTOM_BASE", "https://custom-gateway.example/v1")
    monkeypatch.setenv("CUSTOM_KEY_ENV", "CUSTOM_GATEWAY_API_KEY")
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: gateway
  providers:
    gateway:
      provider: custom_openai
      model: vendor/model
      base_url: ${CUSTOM_BASE}
      api_key_env: ${CUSTOM_KEY_ENV}
      supports_image_generation: true
      image_generation:
        endpoint: /images/generations
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    model = result.effective_config.models["gateway"]

    assert model.base_url == "https://custom-gateway.example/v1"
    assert model.api_base == "https://custom-gateway.example/v1"
    assert model.api_key == "$CUSTOM_GATEWAY_API_KEY"
    assert model.api_key_env == "CUSTOM_GATEWAY_API_KEY"
    assert "api_key_env" not in model.provider_settings
    assert model.supports_image_generation is True
    assert model.image_generation == {"endpoint": "/images/generations"}


def test_compact_llm_provider_requires_image_generation_endpoint_when_enabled(
    contract_tmp_path: Path,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: image_model
  providers:
    image_model:
      provider: custom_openai
      model: gpt-image-1
      supports_image_generation: true
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="image_generation.endpoint"):
        ConfigService().resolve(build_config_layers_from_file(config_path))


def test_compact_llm_provider_accepts_model_catalog_with_default_selection(
    contract_tmp_path: Path,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: MiMo
  providers:
    MiMo:
      provider: mimo
      model:
        - mimo-v2.5-pro
        - mimo-v2.5
        - mimo-v2-pro
        - mimo-v2-omni
        - mimo-v2-flash
      default_model: mimo-v2-flash
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    model = result.effective_config.models["MiMo"]

    assert model.model_catalog == [
        "mimo-v2.5-pro",
        "mimo-v2.5",
        "mimo-v2-pro",
        "mimo-v2-omni",
        "mimo-v2-flash",
    ]
    assert model.default_model == "mimo-v2-flash"
    assert model.model_name == "mimo-v2-flash"
    assert model.effective_model_name() == "mimo-v2-flash"


def test_compact_llm_provider_accepts_per_model_context_windows(
    contract_tmp_path: Path,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: gateway
  providers:
    gateway:
      model:
        - fast-model
        - long-model
      default_model: fast-model
      base_url: https://gateway.example/v1
      api_key: ${GATEWAY_API_KEY}
      context_window_tokens: 128000
      auto_compact_threshold_tokens: 96000
      model_context_windows:
        fast-model: 32768
        long-model: 262144
      model_auto_compact_thresholds:
        fast-model: 24576
        long-model: 196608
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    model = result.effective_config.models["gateway"]

    assert model.effective_model_name() == "fast-model"
    assert model.effective_context_window_tokens() == 32768
    assert model.effective_auto_compact_threshold_tokens() == 24576


def test_compact_llm_provider_rejects_default_model_outside_catalog(
    contract_tmp_path: Path,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: MiMo
  providers:
    MiMo:
      provider: mimo
      model:
        - mimo-v2.5-pro
        - mimo-v2.5
      default_model: mimo-typo
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not present in its model catalog"):
        build_config_layers_from_file(config_path)


def test_build_default_config_layers_falls_back_to_env_bootstrap(monkeypatch: pytest.MonkeyPatch, contract_tmp_path: Path) -> None:
    anvil_home = contract_tmp_path / ".anvil-home"
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))
    monkeypatch.delenv("ANVIL_CONFIG_PATH", raising=False)
    monkeypatch.setenv("ANVIL_DEFAULT_MODEL", "openai_compatible")
    monkeypatch.setenv("ANVIL_OPENAI_COMPAT_MODEL", "gpt-5.4")
    monkeypatch.setenv("ANVIL_OPENAI_COMPAT_BASE_URL", "https://example.test/v1")

    layers = build_env_bootstrap_config_layers_from_env()
    result = ConfigService().resolve(layers)
    assert result.effective_config.default_model == "openai_compatible"
    assert result.effective_config.models["openai_compatible"].use == "anvil.agents.provider_adapters:AnvilOpenAIChatModel"


def test_build_default_config_layers_loads_home_dotenv_and_config(monkeypatch: pytest.MonkeyPatch, contract_tmp_path: Path) -> None:
    anvil_home = contract_tmp_path / ".anvil-home"
    anvil_home.mkdir(parents=True)
    (anvil_home / ".env").write_text("ANVIL_DEFAULT_MODEL=ignored\n", encoding="utf-8")
    (anvil_home / "config.yaml").write_text(
        "default_model: home_model\nmodels:\n  home_model:\n    name: home_model\n    provider: openai\n    model_name: gpt-5.4\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))
    monkeypatch.delenv("ANVIL_CONFIG_PATH", raising=False)

    layers = build_default_config_layers()
    result = ConfigService().resolve(layers)
    assert result.effective_config.default_model == "home_model"


def test_only_repo_anvil_mcp_json_and_home_plugin_config_paths_are_discovered(contract_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anvil_home = contract_tmp_path / ".anvil-home"
    config_path = anvil_home / "config.yaml"
    plugin_path = anvil_home / "plugins" / "marketplace.json"
    config_path.parent.mkdir(parents=True)
    plugin_path.parent.mkdir(parents=True)
    config_path.write_text(
        "mcp_servers:\n  fetch:\n    type: sse\n    url: https://example.test/sse\n",
        encoding="utf-8",
    )
    plugin_path.write_text('{"plugins": {"example": {"enabled": true}}}', encoding="utf-8")
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))

    assert resolve_mcp_config_paths(repo_root=contract_tmp_path / "repo") == []
    assert resolve_plugin_config_paths() == [plugin_path.resolve()]


def test_repo_anvil_mcp_json_is_discovered_but_nested_and_legacy_mcp_are_not_auto_discovered(
    contract_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = contract_tmp_path / "repo"
    repo_mcp = repo_root / ".anvil" / "mcp.json"
    nested_repo_mcp = repo_root / ".anvil" / "mcp" / "mcp.json"
    root_mcp = repo_root / "mcp.json"
    legacy_mcp = repo_root / ".agents" / "mcp" / "mcp.json"
    repo_plugin = repo_root / ".anvil" / "plugins" / "marketplace.json"
    legacy_plugin = repo_root / ".agents" / "plugins" / "marketplace.json"
    anvil_home = contract_tmp_path / ".anvil-home"
    repo_root.mkdir()
    repo_mcp.parent.mkdir(parents=True)
    nested_repo_mcp.parent.mkdir(parents=True)
    root_mcp.parent.mkdir(parents=True, exist_ok=True)
    legacy_mcp.parent.mkdir(parents=True)
    repo_plugin.parent.mkdir(parents=True)
    legacy_plugin.parent.mkdir(parents=True)
    repo_mcp.write_text('{"mcpServers": {"repo": {"transport": "sse", "url": "https://example.test/sse"}}}', encoding="utf-8")
    nested_repo_mcp.write_text(
        '{"mcpServers": {"nested": {"transport": "sse", "url": "https://example.test/sse"}}}',
        encoding="utf-8",
    )
    root_mcp.write_text('{"mcpServers": {"root": {"transport": "sse", "url": "https://example.test/sse"}}}', encoding="utf-8")
    legacy_mcp.write_text('{"mcpServers": {"legacy": {"transport": "sse", "url": "https://example.test/sse"}}}', encoding="utf-8")
    repo_plugin.write_text('{"plugins": {"repo": {"enabled": true}}}', encoding="utf-8")
    legacy_plugin.write_text('{"plugins": {"legacy": {"enabled": true}}}', encoding="utf-8")
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))

    assert resolve_mcp_config_paths(repo_root=repo_root) == [repo_mcp.resolve()]
    assert resolve_plugin_config_paths(repo_root=repo_root) == []


def test_build_default_config_layers_bootstraps_home_config_once(contract_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anvil_home = contract_tmp_path / ".anvil-home"
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))
    config_path = anvil_home / "config.yaml"

    layers = build_default_config_layers()
    assert config_path.exists()
    seeded = build_config_layers_from_file(config_path)[0].data
    result = ConfigService().resolve(layers)
    servers = seeded["extensions"]["mcp_servers"]
    assert sorted(servers) == ["filesystem", "github", "postgres", "prompts.chat"]
    assert all(server["enabled"] is True for server in servers.values())
    assert seeded["hcms"]["enabled"] is True
    assert result.effective_config.hcms.enabled is True
    assert any(layer.source == str(config_path.resolve()) for layer in layers)

    config_path.write_text(
        "mcp_servers:\n"
        "  filesystem:\n"
        "    enabled: true\n"
        "    type: stdio\n"
        "    command: npx\n"
        "  postgres:\n"
        "    enabled: true\n"
        "    type: stdio\n"
        "    command: npx\n"
        "  prompts.chat:\n"
        "    enabled: true\n"
        "    type: http\n"
        "    url: https://prompts.chat/api/mcp\n",
        encoding="utf-8",
    )
    build_default_config_layers()

    current = build_config_layers_from_file(config_path)[0].data
    assert "github" not in current["extensions"]["mcp_servers"]


def test_build_default_config_layers_backfills_hcms_for_existing_home_config(
    contract_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anvil_home = contract_tmp_path / ".anvil-home"
    config_path = anvil_home / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("llm:\n  providers: {}\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))
    monkeypatch.delenv("ANVIL_CONFIG_PATH", raising=False)

    result = ConfigService().resolve(build_default_config_layers())

    assert result.effective_config.hcms.enabled is True


def test_config_service_drops_legacy_memory_platform_config() -> None:
    result = ConfigService().resolve(
        [
            ConfigLayer(
                name="project",
                kind=ConfigLayerKind.PROJECT,
                data={
                    "memory_platform": {"enabled": True},
                    "hcms": {"enabled": True},
                    "web_tools": {"enabled": True},
                },
            )
        ]
    )

    assert "memory_platform" not in result.effective_config.additional_settings
    assert "web_tools" in result.effective_config.additional_settings
    assert result.effective_config.hcms.enabled is True


def test_config_service_migrates_legacy_memory_config_to_hcms_recall() -> None:
    result = ConfigService().resolve(
        [
            ConfigLayer(
                name="project",
                kind=ConfigLayerKind.PROJECT,
                data={
                    "memory": {
                        "enabled": True,
                        "max_facts": 12,
                        "injection_token_budget": 1200,
                        "transcript_context_tokens": 4000,
                    },
                    "hcms": {
                        "recall": {
                            "max_candidates": 6,
                        },
                    },
                },
            )
        ]
    )

    assert "memory" not in result.effective_config.additional_settings
    assert result.effective_config.hcms.enabled is True
    assert result.effective_config.hcms.recall.max_candidates == 6
    assert result.effective_config.hcms.recall.turn_recall_token_budget == 1200
    assert result.effective_config.hcms.transcript.transcript_context_tokens == 4000


def test_build_default_config_layers_respects_explicit_hcms_disable(
    contract_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anvil_home = contract_tmp_path / ".anvil-home"
    config_path = anvil_home / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("hcms:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))
    monkeypatch.delenv("ANVIL_CONFIG_PATH", raising=False)

    result = ConfigService().resolve(build_default_config_layers())

    assert result.effective_config.hcms.enabled is False


def test_default_anvil_config_dir_uses_repo_local_root_without_env(
    contract_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anvil.config import default_anvil_config_dir

    repo_root = contract_tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.delenv("ANVIL_HOME", raising=False)

    assert default_anvil_config_dir(repo_root) == (repo_root / ".anvil").resolve()


def test_build_default_config_layers_uses_repo_local_home_when_repo_root_is_explicit(
    contract_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = contract_tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "config.yaml").write_text("hcms:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.delenv("ANVIL_HOME", raising=False)
    monkeypatch.delenv("ANVIL_CONFIG_PATH", raising=False)

    result = ConfigService().resolve(build_default_config_layers(repo_root=repo_root))

    assert (repo_root / ".anvil" / "config.yaml").exists()
    assert (repo_root / ".anvil" / "sessions").is_dir()
    assert result.effective_config.hcms.enabled is True


def test_default_anvil_config_dir_keeps_anvil_home_env_override(
    contract_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anvil.config import default_anvil_config_dir

    repo_root = contract_tmp_path / "repo"
    anvil_home = contract_tmp_path / "explicit-home"
    repo_root.mkdir()
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))

    assert default_anvil_config_dir(repo_root) == anvil_home.resolve()


def test_home_config_mcp_is_runtime_source(contract_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anvil_home = contract_tmp_path / ".anvil-home"
    config_path = anvil_home / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "mcp_servers:\n  shared:\n    enabled: true\n    type: stdio\n    command: home\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))

    result = ConfigService().resolve(build_default_config_layers())
    server = result.effective_config.extensions.mcp_servers["shared"]

    assert server.enabled is True
    assert server.connection_config["command"] == "home"


def test_home_config_mcp_overrides_project_anvil_mcp_when_server_id_matches(
    contract_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = contract_tmp_path / "repo"
    project_mcp_path = repo_root / ".anvil" / "mcp.json"
    anvil_home = contract_tmp_path / ".anvil-home"
    config_path = anvil_home / "config.yaml"
    project_mcp_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    project_mcp_path.write_text(
        '{"mcpServers": {"shared": {"enabled": true, "command": ["project", "arg"]}}}',
        encoding="utf-8",
    )
    config_path.write_text(
        "mcp_servers:\n  shared:\n    enabled: true\n    type: stdio\n    command: home\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_HOME", str(anvil_home))
    monkeypatch.delenv("ANVIL_CONFIG_PATH", raising=False)

    result = ConfigService().resolve(build_default_config_layers(repo_root=repo_root))
    server = result.effective_config.extensions.mcp_servers["shared"]

    assert server.connection_config["command"] == "home"
    assert result.origins["extensions.mcp_servers.shared.connection_config.command"].source == str(config_path.resolve())


def test_mcp_config_transport_field_normalizes_sse_and_streamable_http(contract_tmp_path: Path) -> None:
    mcp_config = contract_tmp_path / "mcp.json"
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "events": {
                        "transport": "sse",
                        "url": "https://example.test/sse",
                    },
                    "chatppt": {
                        "transport": "streamable_http",
                        "url": "https://example.test/mcp",
                    },
                    "direct_kind": {
                        "transport_kind": "streamable_http",
                        "url": "https://example.test/direct-mcp",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = ConfigService().resolve([build_mcp_config_layer_from_file(mcp_config)])

    events = result.effective_config.extensions.mcp_servers["events"]
    chatppt = result.effective_config.extensions.mcp_servers["chatppt"]
    direct_kind = result.effective_config.extensions.mcp_servers["direct_kind"]
    assert events.transport_kind == McpTransportKind.SSE
    assert events.connection_config["url"] == "https://example.test/sse"
    assert chatppt.transport_kind == McpTransportKind.HTTP
    assert chatppt.connection_config["url"] == "https://example.test/mcp"
    assert direct_kind.transport_kind == McpTransportKind.HTTP
    assert direct_kind.connection_config["url"] == "https://example.test/direct-mcp"


def test_invalid_yaml_raises_actionable_error(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text("models: [\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid YAML"):
        build_config_layers_from_file(config_path)


def test_config_example_resolves_as_complete_field_reference() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = ConfigService().resolve(build_config_layers_from_file(repo_root / "config.example.yaml"))

    assert "minimax" in result.effective_config.models
    assert sorted(result.effective_config.extensions.mcp_servers) == ["filesystem", "github", "postgres", "prompts.chat"]
    assert "local_example" in result.effective_config.extensions.plugins
    assert result.effective_config.workspace.path_bridges == []
    assert result.effective_config.token_usage.pricing == {}
    assert "web_tools" in result.effective_config.additional_settings
    assert "tracing" in result.effective_config.additional_settings


def test_nested_extensions_mcp_servers_accept_nested_aliases(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
extensions:
  mcp_servers:
    filesystem:
      enabled: false
      type: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      env: {}
      tools:
        include: [read_file]
        exclude: [write_file]
        resources: true
        prompts: false
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    server = result.effective_config.extensions.mcp_servers["filesystem"]

    assert server.transport_kind is McpTransportKind.STDIO
    assert server.connection_config["command"] == "npx"
    assert server.connection_config["args"][-1] == "/tmp"
    assert server.tool_allowlist_active is True
    assert server.tool_allowlist == ["read_file"]
    assert server.tool_denylist == ["write_file"]
    assert server.resource_policy == {"enabled": True}
    assert server.prompt_policy == {"enabled": False}


def test_minimax_provider_preset_requires_explicit_model_selection(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: minimax
  providers:
    minimax: {}
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))

    assert result.effective_config.default_model == "minimax"
    assert "minimax" not in result.effective_config.models


def test_minimax_provider_preset_allows_minimal_explicit_config(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: minimax
  providers:
    minimax:
      model: MiniMax-M2.7
      api_key: ${MINIMAX_API_KEY}
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    model = result.effective_config.models["minimax"]

    assert result.effective_config.default_model == "minimax"
    assert model.effective_model_name() == "MiniMax-M2.7"
    assert model.api_key == "${MINIMAX_API_KEY}"
    assert model.base_url == "https://api.minimaxi.com/anthropic"
    assert model.use == "anvil.agents.provider_adapters:AnvilAnthropicChatModel"


def test_token_usage_pricing_config_is_typed(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: openai
models:
  openai:
    name: openai
    provider: openai
    model: gpt-5.4
token_usage:
  enabled: true
  include_entries: false
  currency: USD
  cost_precision: 6
  pricing:
    openai/gpt-5.4:
      input_cost_per_million: 1.25
      output_cost_per_million: 10.0
      source: operator-config
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    token_usage = result.effective_config.token_usage

    assert token_usage.enabled is True
    assert token_usage.include_entries is False
    assert token_usage.cost_precision == 6
    assert token_usage.pricing["openai/gpt-5.4"].input_cost_per_million == 1.25
    assert token_usage.pricing["openai/gpt-5.4"].source == "operator-config"


def test_tool_output_budget_command_compaction_config_is_typed(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: openai
models:
  openai:
    name: openai
    provider: openai
    model: gpt-5.4
tool_output_budget:
  enabled: true
  command_compaction_enabled: true
  command_compaction_min_chars: 900
  command_compaction_max_chars: 4800
  raw_failure_artifacts: true
  raw_compaction_artifacts: true
  command_profiles: [test, typecheck, lint, git, package, container]
        """.strip(),
        encoding="utf-8",
    )

    result = ConfigService().resolve(build_config_layers_from_file(config_path))
    tool_output_budget = result.effective_config.tool_output_budget

    assert tool_output_budget.command_compaction_enabled is True
    assert tool_output_budget.command_compaction_min_chars == 900
    assert tool_output_budget.command_compaction_max_chars == 4800
    assert tool_output_budget.raw_failure_artifacts is True
    assert tool_output_budget.raw_compaction_artifacts is True
    assert tool_output_budget.command_profiles == ("test", "typecheck", "lint", "git", "package", "container")


def test_init_config_from_example_copies_template(contract_tmp_path: Path) -> None:
    repo_root = contract_tmp_path / "repo"
    repo_root.mkdir()
    example = repo_root / "config.example.yaml"
    example.write_text("default_model: demo\nmodels: []\n", encoding="utf-8")

    created = init_config_from_example(repo_root=repo_root)
    assert created.exists()
    assert created == (contract_tmp_path / ".anvil-home" / "config.yaml").resolve()
    assert created.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")
