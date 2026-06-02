from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage
import yaml


def test_anvil_cli_setup_and_config_commands(contract_tmp_path: Path, capsys) -> None:
    from app.cli import main

    config_path = contract_tmp_path / "config.yaml"

    main(
        [
            "--anvil-home",
            str(contract_tmp_path / "home"),
            "--config",
            str(config_path),
            "setup",
            "--provider",
            "openai",
            "--model",
            "gpt-5.4",
            "--api-key",
            "test-openai-key",
            "--api-key-env",
            "OPENAI_API_KEY",
            "--non-interactive",
        ]
    )
    setup_output = capsys.readouterr().out
    assert "Config ready:" in setup_output

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["llm"]["default"] == "openai"
    assert payload["llm"]["providers"]["openai"]["model"] == "gpt-5.4"
    assert payload["llm"]["providers"]["openai"]["model_name"] == "gpt-5.4"
    assert payload["llm"]["providers"]["openai"]["api_key"] == "${OPENAI_API_KEY}"
    assert (config_path.parent / ".env").read_text(encoding="utf-8").strip() == "OPENAI_API_KEY=test-openai-key"

    main(["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path), "config", "set", "terminal.active_backend", "local"])
    assert "Set terminal.active_backend" in capsys.readouterr().out
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["terminal"]["active_backend"] == "local"

    main(["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path), "config", "check"])
    assert "Config ok" in capsys.readouterr().out


def test_anvil_cli_config_roots_uses_anvil_paths(contract_tmp_path: Path, capsys) -> None:
    from app.cli import main

    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        "anvil:\n  home: ./.anvil\nagents:\n  repo_root: ./.anvil\n  user_root: ~/.anvil\n",
        encoding="utf-8",
    )

    main(["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path), "config", "roots"])
    output = capsys.readouterr().out

    assert ".anvil" in output
    assert ".agents" not in output


def test_anvil_cli_model_and_tools_list(contract_tmp_path: Path, capsys) -> None:
    from app.cli import main

    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: openai
models:
  openai:
    name: openai
    provider: openai
    provider_kind: openai_compatible
    model_name: gpt-5.4
        """.strip(),
        encoding="utf-8",
    )

    main(["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path), "model"])
    model_output = capsys.readouterr().out
    assert "openai [openai]" in model_output

    main(["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path), "tools", "list", "file"])
    tools_output = capsys.readouterr().out
    assert "file" in tools_output.lower()


def test_anvil_cli_model_add_and_delete_updates_config(contract_tmp_path: Path, capsys) -> None:
    from app.cli import main

    config_path = contract_tmp_path / "config.yaml"
    common = ["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path)]

    main([
        *common,
        "model",
        "add",
        "openrouter",
        "--provider",
        "openrouter",
        "--model",
        "openai/gpt-5.4",
        "--api-key",
        "test-router-key",
    ])
    assert "Model provider saved: openrouter" in capsys.readouterr().out
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["llm"]["default"] == "openrouter"
    assert payload["llm"]["providers"]["openrouter"]["api_key"] == "${OPENROUTER_API_KEY}"
    assert (config_path.parent / ".env").read_text(encoding="utf-8").strip() == "OPENROUTER_API_KEY=test-router-key"

    main([*common, "model", "delete", "openrouter"])
    assert "Model provider deleted: openrouter" in capsys.readouterr().out
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "openrouter" not in payload["llm"]["providers"]


def test_anvil_cli_runtime_capability_commands(contract_tmp_path: Path, capsys) -> None:
    from app.cli import main

    config_path = contract_tmp_path / "config.yaml"
    skill_repo = contract_tmp_path / "repo-skills"
    skill_root = skill_repo / "demo-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("# Demo Skill\n\nUse when testing the CLI skill list.\n", encoding="utf-8")

    config_path.write_text(
        f"""
default_model: openai
models:
  openai:
    name: openai
    provider: openai
    provider_kind: openai_compatible
    model_name: gpt-5.4
memory_platform:
  enabled: true
  stores:
    runtime_memory:
      display_name: Runtime Memory
      max_chars: 1200
      injection_chars: 500
skills_config:
  external_dirs:
    - "{skill_repo.as_posix()}"
        """.strip(),
        encoding="utf-8",
    )

    common = ["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path)]

    main([*common, "skills", "list", "demo"])
    assert "demo-skill" in capsys.readouterr().out

    main([*common, "mcp", "config"])
    assert "server_count" in capsys.readouterr().out

    main([*common, "plugins", "list"])
    assert "No plugins installed." in capsys.readouterr().out

    main([*common, "memory", "overview"])
    assert "Stores:" in capsys.readouterr().out

    main([*common, "scheduled", "list"])
    assert "No scheduled automations." in capsys.readouterr().out

    main([*common, "context", "show"])
    assert "No active thread" in capsys.readouterr().out


def test_anvil_cli_step_renders_pending_structured_interaction(contract_tmp_path: Path, capsys, monkeypatch) -> None:
    from app.cli import main
    from fake_models import BindableFakeMessagesListChatModel

    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: openai
models:
  openai:
    name: openai
    provider: openai
    provider_kind: openai_compatible
    model_name: gpt-5.4
        """.strip(),
        encoding="utf-8",
    )
    common = ["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path)]
    model = BindableFakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "args": {
                            "title": "Choose deck style",
                            "question": "Which style should I use?",
                            "selection_mode": "single",
                            "options": [
                                {"id": "modern", "label": "Modern", "recommended": True},
                                {"id": "classic", "label": "Classic"},
                            ],
                        },
                        "id": "call-style",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )

    original_client_config = __import__("app.cli", fromlist=["_client_config"])._client_config

    def client_config_with_model(config_path_arg, *, profile):
        config = original_client_config(config_path_arg, profile=profile)
        config.chat_model_override = model
        config.thread_root = contract_tmp_path / "threads"
        config.state_db_path = contract_tmp_path / "runtime.sqlite3"
        return config

    monkeypatch.setattr("app.cli._client_config", client_config_with_model)

    main([*common, "step", "--thread", "cli-interaction", "make slides"])
    first = capsys.readouterr().out
    assert "Which style should I use?" in first

    main([*common, "step", "--thread", "cli-interaction"])
    second = capsys.readouterr().out
    assert "Input needed:" in second
    assert "Request: call-style" in second
    assert "- modern: Modern (recommended)" in second


def test_anvil_cli_step_answers_multi_field_structured_interaction(contract_tmp_path: Path, capsys, monkeypatch) -> None:
    from app.cli import main
    from fake_models import BindableFakeMessagesListChatModel

    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: openai
models:
  openai:
    name: openai
    provider: openai
    provider_kind: openai_compatible
    model_name: gpt-5.4
        """.strip(),
        encoding="utf-8",
    )
    common = ["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path)]
    model = BindableFakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "args": {
                            "title": "Frontend decisions",
                            "question": "Choose app scaffold details.",
                            "fields": [
                                {
                                    "id": "stack",
                                    "label": "Framework",
                                    "selection_mode": "single",
                                    "options": [{"id": "vite", "label": "Vite", "recommended": True}],
                                },
                                {
                                    "id": "scope",
                                    "label": "Completeness",
                                    "selection_mode": "multiple",
                                    "options": [{"id": "tests", "label": "Tests"}],
                                },
                                {"id": "notes", "label": "Notes", "selection_mode": "text", "required": False},
                            ],
                        },
                        "id": "call-form",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Using Vite with tests."),
        ]
    )

    original_client_config = __import__("app.cli", fromlist=["_client_config"])._client_config

    def client_config_with_model(config_path_arg, *, profile):
        config = original_client_config(config_path_arg, profile=profile)
        config.chat_model_override = model
        config.thread_root = contract_tmp_path / "threads"
        config.state_db_path = contract_tmp_path / "runtime.sqlite3"
        return config

    monkeypatch.setattr("app.cli._client_config", client_config_with_model)

    main([*common, "step", "--thread", "cli-form", "build app"])
    first = capsys.readouterr().out
    assert "Choose app scaffold details." in first

    main([*common, "step", "--thread", "cli-form"])
    pending = capsys.readouterr().out
    assert "Input needed:" in pending
    assert "Request: call-form" in pending
    assert "Fields:" in pending
    assert "- stack: Framework [single]" in pending
    assert "scope: Completeness" in pending

    main(
        [
            *common,
            "step",
            "--thread",
            "cli-form",
            "--field",
            "stack=vite",
            "--field",
            "scope=tests",
            "--field",
            "notes:Keep it quiet",
        ]
    )
    resumed = capsys.readouterr().out
    assert "Using Vite with tests." in resumed


def test_anvil_cli_step_interactive_uses_keyboard_selector(contract_tmp_path: Path, capsys, monkeypatch) -> None:
    from app.cli import main
    from fake_models import BindableFakeMessagesListChatModel
    import app.shell.tui as tui

    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: openai
models:
  openai:
    name: openai
    provider: openai
    provider_kind: openai_compatible
    model_name: gpt-5.4
        """.strip(),
        encoding="utf-8",
    )
    common = ["--anvil-home", str(contract_tmp_path / "home"), "--config", str(config_path)]
    model = BindableFakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "args": {
                            "title": "Choose deck style",
                            "question": "Which style should I use?",
                            "selection_mode": "single",
                            "options": [
                                {"id": "modern", "label": "Modern", "recommended": True},
                                {"id": "classic", "label": "Classic"},
                            ],
                        },
                        "id": "call-style",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Using Classic."),
        ]
    )

    original_client_config = __import__("app.cli", fromlist=["_client_config"])._client_config

    def client_config_with_model(config_path_arg, *, profile):
        config = original_client_config(config_path_arg, profile=profile)
        config.chat_model_override = model
        config.thread_root = contract_tmp_path / "threads"
        config.state_db_path = contract_tmp_path / "runtime.sqlite3"
        return config

    def fake_collect(interaction, *, style):
        assert interaction.request_id == "call-style"
        return {"choices": ["classic"], "custom": None, "free_text": None, "fields": []}

    monkeypatch.setattr("app.cli._client_config", client_config_with_model)
    monkeypatch.setattr(tui, "_collect_user_interaction_with_prompt_toolkit", fake_collect)

    main([*common, "step", "--thread", "cli-interactive", "make slides"])
    capsys.readouterr()

    main([*common, "step", "--thread", "cli-interactive", "--interactive"])
    resumed = capsys.readouterr().out
    assert "Using Classic." in resumed
