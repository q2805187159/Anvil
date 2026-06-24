from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.gateway.app import make_gateway_app


def test_gateway_updates_model_selection_in_config_and_hot_reloads(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
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
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.patch("/models/openai/selection", json={"model_name": "gpt-5.5"})

    assert response.status_code == 200
    body = response.json()
    assert body["selected_model"] == "gpt-5.5"
    assert body["model"]["selected_model"] == "gpt-5.5"
    assert body["model"]["default_model"] == "gpt-5.5"

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    provider = payload["llm"]["providers"]["openai"]
    assert provider["model"] == ["gpt-5.4", "gpt-5.5"]
    assert provider["selected_model"] == "gpt-5.5"
    assert provider["model_name"] == "gpt-5.5"
    assert provider["default_model"] == "gpt-5.5"


def test_gateway_rejects_unconfigured_model_selection(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
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
      default_model: gpt-5.4
      api_key: ${OPENAI_API_KEY}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.patch("/models/openai/selection", json={"model_name": "gpt-5.5"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_model_selection"


def test_gateway_updates_default_reasoning_effort_in_config(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
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
      supports_reasoning_effort: true
      api_key: ${OPENAI_API_KEY}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.patch(
            "/models/openai/selection",
            json={"model_name": "gpt-5.4", "default_reasoning_effort": "high"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["default_reasoning_effort"] == "high"
    assert body["model"]["default_reasoning_effort"] == "high"

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    provider = payload["llm"]["providers"]["openai"]
    assert provider["default_reasoning_effort"] == "high"


def test_gateway_sets_unique_internal_task_model_and_compatibility_fields(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: openai
  providers:
    openai:
      provider: openai
      model: gpt-5.4
      api_key: ${OPENAI_API_KEY}
    minimax:
      provider: minimax
      model: MiniMax-M2.7
      api_key: ${MINIMAX_API_KEY}
  subsystems:
    title: openai
    session_search: openai
    memory_updater: openai
    memory_rerank: openai
summarization:
  enabled: true
  model_name: openai
title:
  enabled: true
  model_name: openai
hcms:
  enabled: true
  session_search:
    model_name: openai
  recall:
    rerank_model_name: openai
  updater:
    model_name: openai
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.patch("/models/minimax/selection", json={"model_name": "MiniMax-M2.7", "internal_task_default": True})
        listed = client.get("/models")

    assert response.status_code == 200
    body = response.json()
    assert body["model"]["internal_task_default"] is True
    assert listed.status_code == 200
    by_name = {item["name"]: item for item in listed.json()}
    assert by_name["minimax"]["internal_task_default"] is True
    assert by_name["openai"]["internal_task_default"] is False

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    subsystems = payload["llm"]["subsystems"]
    for subsystem in ["summarization", "title", "session_search", "memory_updater", "memory_rerank"]:
        assert subsystems[subsystem] == "minimax"
    for subsystem in [
        "memory_reflection",
        "memory_governance",
        "memory_maintenance",
        "skill_curator",
        "skill_extraction",
        "procedure_learning",
        "scheduled_automation",
        "trajectory_compression",
    ]:
        assert subsystems[subsystem] == "minimax"
    assert payload["summarization"]["model_name"] == "minimax"
    assert payload["title"]["model_name"] == "minimax"
    assert payload["hcms"]["session_search"]["model_name"] == "minimax"
    assert payload["hcms"]["recall"]["rerank_model_name"] == "minimax"
    assert payload["hcms"]["updater"]["model_name"] == "minimax"
    assert payload["scheduled_tasks"]["default_model"] == "minimax"


def test_gateway_internal_task_model_selection_does_not_change_provider_default(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: openai
  providers:
    openai:
      provider: openai
      model: gpt-5.4
      api_key: ${OPENAI_API_KEY}
    minimax:
      provider: minimax
      model:
        - mimo-v2-flash
        - MiniMax-M2.7
      default_model: MiniMax-M2.7
      api_key: ${MINIMAX_API_KEY}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.patch("/models/minimax/selection", json={"model_name": "mimo-v2-flash", "internal_task_default": True})
        listed = client.get("/models")

    assert response.status_code == 200
    body = response.json()
    assert body["model"]["internal_task_default"] is True
    assert body["model"]["internal_task_selected_model"] == "mimo-v2-flash"
    by_name = {item["name"]: item for item in listed.json()}
    assert by_name["minimax"]["default_model"] == "MiniMax-M2.7"
    assert by_name["minimax"]["selected_model"] == "MiniMax-M2.7"
    assert by_name["minimax"]["internal_task_selected_model"] == "mimo-v2-flash"

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    provider = payload["llm"]["providers"]["minimax"]
    assert provider["default_model"] == "MiniMax-M2.7"
    assert provider.get("selected_model") is None
    assert provider.get("model_name") is None
    assert payload["llm"]["internal_task_model"] == "mimo-v2-flash"
    assert payload["llm"]["subsystems"]["title"] == "minimax"


def test_gateway_tests_model_provider_health(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: minimax
  providers:
    minimax:
      provider: minimax
      model:
        - mimo-v2-flash
        - MiniMax-M2.7
      default_model: MiniMax-M2.7
      api_key: ${MINIMAX_API_KEY}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    calls: dict[str, object] = {}

    class FakeHealthModel:
        def invoke(self, prompt: str, config=None):
            calls["prompt"] = prompt
            calls["config"] = config
            return type("Response", (), {"content": "OK"})()

    def fake_create_chat_model(model_config, **kwargs):
        calls["model_name"] = model_config.model_name
        calls["thinking_enabled"] = kwargs.get("thinking_enabled")
        return FakeHealthModel()

    monkeypatch.setattr("app.gateway.services.create_chat_model", fake_create_chat_model)

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.post("/models/minimax/test", json={"model_name": "mimo-v2-flash", "subsystem": "background_tasks"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["model_name"] == "mimo-v2-flash"
    assert calls["model_name"] == "mimo-v2-flash"
    assert calls["thinking_enabled"] is False


def test_gateway_marks_single_provider_as_internal_task_default(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: openai
  providers:
    openai:
      provider: openai
      model: gpt-5.4
      api_key: ${OPENAI_API_KEY}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        listed = client.get("/models")

    assert listed.status_code == 200
    assert listed.json()[0]["internal_task_default"] is True


def test_gateway_upserts_model_provider_config_and_env(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text("llm:\n  default:\n  providers: {}\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        presets = client.get("/models/presets")
        response = client.put(
            "/models/openrouter",
            json={
                "provider": "openrouter",
                "api_key": "test-openrouter-key",
                "models": ["openai/gpt-5.4", "anthropic/claude-sonnet-4.5"],
                "default_model": "openai/gpt-5.4",
                "default_reasoning_effort": "medium",
                "context_window_tokens": 200000,
            },
        )

    assert presets.status_code == 200
    assert any(item["provider"] == "openrouter" for item in presets.json())
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "openrouter"
    assert body["model"]["available"] is True
    assert body["model"]["selected_model"] == "openai/gpt-5.4"
    assert body["model"]["default_reasoning_effort"] == "medium"

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["llm"]["default"] == "openrouter"
    provider = payload["llm"]["providers"]["openrouter"]
    assert provider["api_key"] == "${OPENROUTER_API_KEY}"
    assert provider["model"] == ["openai/gpt-5.4", "anthropic/claude-sonnet-4.5"]
    assert provider["context_window_tokens"] == 200000
    assert (config_path.parent / ".env").read_text(encoding="utf-8").strip() == "OPENROUTER_API_KEY=test-openrouter-key"


def test_gateway_model_provider_upsert_rejects_global_default_flag(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: openai
  providers:
    openai:
      provider: openai
      model: gpt-5.4
      api_key: ${OPENAI_API_KEY}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.put(
            "/models/openrouter",
            json={
                "provider": "openrouter",
                "models": ["openai/gpt-5.4"],
                "default_model": "openai/gpt-5.4",
                "set_default": True,
            },
        )

    assert response.status_code == 422
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["llm"]["default"] == "openai"


def test_gateway_model_provider_upsert_does_not_change_existing_default_provider(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: openai
  providers:
    openai:
      provider: openai
      model: gpt-5.4
      api_key: ${OPENAI_API_KEY}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.put(
            "/models/openrouter",
            json={
                "provider": "openrouter",
                "models": ["openai/gpt-5.4"],
                "default_model": "openai/gpt-5.4",
            },
        )

    assert response.status_code == 200
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["llm"]["default"] == "openai"
    assert payload.get("default_model") in {None, "openai"}
    assert "openrouter" in payload["llm"]["providers"]


def test_gateway_editing_model_provider_preserves_existing_advanced_config(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
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
      selected_model: gpt-5.5
      model_name: gpt-5.5
      api_key: ${CUSTOM_OPENAI_KEY}
      api_key_env: CUSTOM_OPENAI_KEY
      provider_settings:
        organization: org-test
      when_thinking_disabled:
        reasoning: null
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CUSTOM_OPENAI_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.put(
            "/models/openai",
            json={
                "provider": "openai",
                "models": ["gpt-5.4", "gpt-5.5"],
                "default_model": "gpt-5.4",
                "base_url": "https://api.example.test/v1",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["model"]["api_key_env"] == "CUSTOM_OPENAI_KEY"
    assert body["model"]["selected_model"] == "gpt-5.5"

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    provider = payload["llm"]["providers"]["openai"]
    assert provider["api_key"] == "${CUSTOM_OPENAI_KEY}"
    assert provider["api_key_env"] == "CUSTOM_OPENAI_KEY"
    assert provider["selected_model"] == "gpt-5.5"
    assert provider["model_name"] == "gpt-5.5"
    assert provider["provider_settings"] == {"organization": "org-test"}
    assert provider["when_thinking_disabled"] == {"reasoning": None}
    assert provider["base_url"] == "https://api.example.test/v1"


def test_gateway_deletes_model_provider_config(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  default: openai
  providers:
    openai:
      provider: openai
      model: gpt-5.4
      api_key: ${OPENAI_API_KEY}
    deepseek:
      provider: deepseek
      model: deepseek-chat
      api_key: ${DEEPSEEK_API_KEY}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.delete("/models/deepseek")
        listed = client.get("/models")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["openai"]
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "deepseek" not in payload["llm"]["providers"]
