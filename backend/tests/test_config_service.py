from __future__ import annotations

import os
import pytest
import yaml

from anvil.config import (
    ConfigLayer,
    ConfigLayerKind,
    ConfigMutationError,
    ConfigService,
    McpTransportKind,
    ProviderKind,
    build_default_config_layers,
    normalize_loaded_config,
    resolve_internal_task_concrete_model_name,
)


def test_config_service_applies_layer_precedence_and_tracks_origins() -> None:
    service = ConfigService()
    layers = [
        ConfigLayer(
            name="project",
            kind=ConfigLayerKind.PROJECT,
            data={
                "default_model": "project-model",
                "subsystem_models": {"memory": "memory-model"},
                "extensions": {"skills": {"web-search": False}},
            },
        ),
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "default-model",
                "models": {
                    "default-model": {"name": "default-model", "provider": "openai"},
                    "project-model": {"name": "project-model", "provider": "openai"},
                    "memory-model": {"name": "memory-model", "provider": "openai"},
                    "request-model": {"name": "request-model", "provider": "anthropic"},
                },
                "extensions": {
                    "skills": {"web-search": True},
                    "mcp_servers": {
                        "github": {
                            "enabled": True,
                            "transport_kind": "stdio",
                        }
                    },
                },
            },
        ),
        ConfigLayer(
            name="request",
            kind=ConfigLayerKind.REQUEST,
            data={"default_model": "request-model"},
        ),
    ]

    result = service.resolve(layers)

    assert result.effective_config.default_model == "request-model"
    assert result.effective_config.subsystem_models["memory"] == "memory-model"
    assert result.effective_config.extensions.skills["web-search"] is False
    assert (
        result.effective_config.extensions.mcp_servers["github"].transport_kind
        == McpTransportKind.STDIO
    )
    assert result.origins["default_model"].layer_name == "request"
    assert result.origins["extensions.skills.web-search"].layer_name == "project"


def test_config_service_fingerprint_changes_with_effective_config() -> None:
    service = ConfigService()
    base_layers = [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "base-model",
                "models": {"base-model": {"name": "base-model", "provider": "openai"}},
            },
        )
    ]

    first = service.resolve(base_layers)
    second = service.resolve(
        [
            base_layers[0],
            ConfigLayer(
                name="request",
                kind=ConfigLayerKind.REQUEST,
                data={"default_model": "override-model", "models": {"override-model": {"name": "override-model", "provider": "openai"}}},
            ),
        ]
    )

    assert first.fingerprint != second.fingerprint


def test_config_service_ignores_legacy_scheduled_task_generation_strategy() -> None:
    result = ConfigService().resolve(
        [
            ConfigLayer(
                name="legacy",
                kind=ConfigLayerKind.PROJECT,
                data={
                    "models": {"minimax": {"name": "minimax", "provider": "openai"}},
                    "default_model": "minimax",
                    "scheduled_tasks": {
                        "default_model": "minimax",
                        "generation_strategy": "truncate",
                    },
                },
            )
        ]
    )

    assert result.effective_config.scheduled_tasks.default_model == "minimax"


def test_config_service_rejects_invalid_effective_config() -> None:
    service = ConfigService()
    invalid_layers = [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "extensions": {
                    "mcp_servers": {
                        "broken": {
                            "transport_kind": "not-a-transport",
                        }
                    }
                }
            },
        )
    ]

    with pytest.raises(ValueError, match="invalid effective config"):
        service.resolve(invalid_layers)


def test_config_service_keeps_deprecated_fallback_model_names_without_validation() -> None:
    service = ConfigService()

    result = service.resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "primary",
                    "models": {
                        "primary": {"name": "primary", "provider": "openai"},
                    },
                    "llm": {"fallback_models": ["missing-backup"]},
                },
            )
        ]
    )

    assert result.effective_config.llm.fallback_models == ["missing-backup"]


def test_config_service_writes_model_selection_without_gateway_adapter_logic(contract_tmp_path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: openai
  providers:
    openai:
      provider: openai
      model:
        - gpt-5.4
        - gpt-5.5
      default_model: gpt-5.4
      api_key: ${OPENAI_API_KEY}
""",
        encoding="utf-8",
    )
    service = ConfigService()
    result = service.resolve(
        [
            ConfigLayer(
                name="project",
                kind=ConfigLayerKind.PROJECT,
                source=str(config_path),
                data=normalize_loaded_config(yaml.safe_load(config_path.read_text(encoding="utf-8"))),
            )
        ]
    )

    mutation = service.write_model_selection(
        config_path=config_path,
        effective_config=result.effective_config,
        provider_name="openai",
        selected_model="gpt-5.5",
    )

    assert mutation.provider_name == "openai"
    assert mutation.selected_model == "gpt-5.5"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    provider = payload["llm"]["providers"]["openai"]
    assert provider["model"] == ["gpt-5.4", "gpt-5.5"]
    assert provider["selected_model"] == "gpt-5.5"
    assert provider["model_name"] == "gpt-5.5"
    assert provider["default_model"] == "gpt-5.5"


def test_config_service_binds_all_internal_task_subsystems(contract_tmp_path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: openai
  providers:
    openai:
      provider: openai
      model: gpt-5.4
    minimax:
      provider: minimax
      model: MiniMax-M2.7
        """.strip(),
        encoding="utf-8",
    )
    service = ConfigService()
    result = service.resolve(
        [
            ConfigLayer(
                name="project",
                kind=ConfigLayerKind.PROJECT,
                source=str(config_path),
                data=normalize_loaded_config(yaml.safe_load(config_path.read_text(encoding="utf-8"))),
            )
        ]
    )

    mutation = service.write_model_selection(
        config_path=config_path,
        effective_config=result.effective_config,
        provider_name="minimax",
        selected_model="MiniMax-M2.7",
        internal_task_default=True,
    )

    assert mutation.internal_task_default is True
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for subsystem in [
        "summarization",
        "title",
        "session_search",
        "memory_updater",
        "memory_rerank",
        "memory_reflection",
        "memory_governance",
        "memory_maintenance",
        "skill_curator",
        "skill_extraction",
        "procedure_learning",
        "scheduled_automation",
        "trajectory_compression",
    ]:
        assert payload["llm"]["subsystems"][subsystem] == "minimax"
        assert payload["subsystem_models"][subsystem] == "minimax"
    assert payload["scheduled_tasks"]["default_model"] == "minimax"


def test_config_service_internal_task_selection_does_not_change_provider_default(contract_tmp_path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: openai
  providers:
    openai:
      provider: openai
      model:
        - gpt-5.4
        - gpt-5.5
      default_model: gpt-5.4
    minimax:
      provider: minimax
      model:
        - mimo-v2-flash
        - MiniMax-M2.7
      default_model: MiniMax-M2.7
        """.strip(),
        encoding="utf-8",
    )
    service = ConfigService()
    result = service.resolve(
        [
            ConfigLayer(
                name="project",
                kind=ConfigLayerKind.PROJECT,
                source=str(config_path),
                data=normalize_loaded_config(yaml.safe_load(config_path.read_text(encoding="utf-8"))),
            )
        ]
    )

    mutation = service.write_internal_task_model_selection(
        config_path=config_path,
        effective_config=result.effective_config,
        provider_name="minimax",
        selected_model="mimo-v2-flash",
    )
    updated = service.resolve(
        [
            ConfigLayer(
                name="project",
                kind=ConfigLayerKind.PROJECT,
                source=str(config_path),
                data=normalize_loaded_config(yaml.safe_load(config_path.read_text(encoding="utf-8"))),
            )
        ]
    )

    assert mutation.provider_selection_changed is False
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    provider = payload["llm"]["providers"]["minimax"]
    assert provider["default_model"] == "MiniMax-M2.7"
    assert provider.get("selected_model") is None
    assert provider.get("model_name") is None
    assert payload["llm"]["internal_task_model"] == "mimo-v2-flash"
    assert resolve_internal_task_concrete_model_name(updated.effective_config) == "mimo-v2-flash"


def test_config_service_rejects_unconfigured_model_selection(contract_tmp_path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
models:
  openai:
    name: openai
    provider: openai
    model:
      - gpt-5.4
    default_model: gpt-5.4
""",
        encoding="utf-8",
    )
    service = ConfigService()
    result = service.resolve(
        [
            ConfigLayer(
                name="project",
                kind=ConfigLayerKind.PROJECT,
                source=str(config_path),
                data=normalize_loaded_config(yaml.safe_load(config_path.read_text(encoding="utf-8"))),
            )
        ]
    )

    with pytest.raises(ConfigMutationError) as exc_info:
        service.write_model_selection(
            config_path=config_path,
            effective_config=result.effective_config,
            provider_name="openai",
            selected_model="gpt-5.5",
        )

    assert exc_info.value.code == "invalid_model_selection"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["models"]["openai"]["default_model"] == "gpt-5.4"


def test_config_service_resolves_env_backed_provider_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANVIL_OPENAI_COMPAT_BASE_URL", "https://example.test/v1")
    service = ConfigService()

    result = service.resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                            "base_url": "$ANVIL_OPENAI_COMPAT_BASE_URL",
                            "api_key_env": "$ANVIL_OPENAI_COMPAT_API_KEY",
                            "default_reasoning_effort": "xhigh",
                        }
                    },
                },
            )
        ]
    )

    model = result.effective_config.models["openai"]
    assert model.provider_kind == ProviderKind.OPENAI_COMPATIBLE
    assert model.base_url == "https://example.test/v1"
    assert model.api_key_env == "ANVIL_OPENAI_COMPAT_API_KEY"
    assert result.origins["models.openai.base_url"].layer_name == "default"


def test_config_service_preserves_provider_secret_refs_until_model_instantiation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "replace-me")
    service = ConfigService()

    result = service.resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "gemini",
                    "models": {
                        "gemini": {
                            "name": "gemini",
                            "provider": "google",
                            "use": "fake_chat_provider:CapturingChatModel",
                            "model_name": "gemini-2.5-pro",
                            "provider_settings": {"gemini_api_key": "$GEMINI_API_KEY"},
                        }
                    },
                },
            )
        ]
    )

    model = result.effective_config.models["gemini"]
    assert model.provider_settings["gemini_api_key"] == "$GEMINI_API_KEY"


def test_build_default_config_layers_merges_dot_anvil_mcp_json(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = contract_tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "config.yaml").write_text(
        "default_model: openai\nmodels:\n  - name: openai\n    provider: openai\n",
        encoding="utf-8",
    )
    anvil_dir = repo_root / ".anvil"
    anvil_dir.mkdir(parents=True, exist_ok=True)
    (anvil_dir / "mcp.json").write_text(
        "{\n"
        '  "servers": [\n'
        '    {\n'
        '      "id": "filesystem-mcp",\n'
        '      "enabled": true,\n'
        '      "command": ["uvx", "filesystem-mcp"],\n'
        '      "startup_policy": "eager",\n'
        '      "refresh_policy": "dynamic"\n'
        "    }\n"
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo_root)
    monkeypatch.delenv("ANVIL_CONFIG_PATH", raising=False)

    layers = build_default_config_layers(repo_root=repo_root)
    result = ConfigService().resolve(layers)

    server = result.effective_config.extensions.mcp_servers["filesystem-mcp"]
    assert server.transport_kind == McpTransportKind.STDIO
    assert server.connection_config["command"] == "uvx"
    assert server.connection_config["args"] == ["filesystem-mcp"]
    assert server.startup_policy == "eager"
    assert server.refresh_policy == "dynamic"
