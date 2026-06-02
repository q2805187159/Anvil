from __future__ import annotations

import importlib.util
from pathlib import Path

from langchain_core.messages import AIMessage

from anvil.config import ConfigLayer, ConfigLayerKind
from fake_models import BindableFakeMessagesListChatModel


def test_shell_command_registry_resolves_aliases_and_help_groups() -> None:
    assert importlib.util.find_spec("app.shell") is not None

    from app.shell import command_catalog_public_dict, command_help_sections, complete_commands, known_command_tokens, resolve_command

    threads_command = resolve_command("/threads")
    assert threads_command is not None
    assert threads_command.name == "threads"

    alias_command = resolve_command("/ls")
    assert alias_command is not None
    assert alias_command.name == "threads"

    sections = command_help_sections()
    assert "Session" in sections
    assert any(command.name == "threads" for command in sections["Session"])
    assert resolve_command("/memory-provider") is not None
    assert resolve_command("/memory-search") is not None
    assert resolve_command("/memory-reflect") is not None
    assert resolve_command("/ps").name == "processes"
    assert resolve_command("/stream").stream_output is True
    assert resolve_command("/term").name == "terminal"
    assert resolve_command("/run").stream_output is True
    assert resolve_command("/tail").name == "tail"

    completions = complete_commands("/mo", scope="tui")
    assert [command.name for command in completions] == ["mode", "model", "models"]

    public_catalog = command_catalog_public_dict(scope="gateway")
    assert public_catalog["total"] >= 30
    assert "Capability" in public_catalog["groups"]
    assert "/stream" in known_command_tokens(scope="tui")
    assert all(str(item["name"]).startswith("/") for item in public_catalog["commands"])


def test_shell_help_mentions_top_level_anvil_commands() -> None:
    from app.shell.commands import render_command_catalog_text

    output = render_command_catalog_text(scope="tui")

    assert "anvil step" in output
    assert "anvil model" in output
    assert "anvil tools" in output
    assert "anvil skills" in output
    assert "anvil mcp" in output
    assert "anvil memory" in output
    assert "anvil config" in output


def test_shell_profile_home_bootstrap_and_sticky_active_profile(contract_tmp_path: Path) -> None:
    assert importlib.util.find_spec("app.shell") is not None

    from app.shell import bootstrap_profile_home, read_active_profile, write_active_profile

    anvil_home = contract_tmp_path / ".anvil-home"
    default_profile = bootstrap_profile_home(anvil_home=anvil_home)
    assert default_profile.name == "default"
    assert default_profile.home == anvil_home
    assert default_profile.config_path.exists()
    assert default_profile.cache_dir.exists()
    assert default_profile.log_dir.exists()
    assert default_profile.sessions_dir.exists()

    named_profile = bootstrap_profile_home("coder", anvil_home=anvil_home)
    assert named_profile.home == anvil_home / "profiles" / "coder"
    assert named_profile.home.exists()
    assert not (named_profile.home / "profiles").exists()

    write_active_profile("coder", anvil_home=anvil_home)
    assert read_active_profile(anvil_home=anvil_home) == "coder"


def test_shell_executes_via_embedded_client_and_keeps_runtime_paths_separate(contract_tmp_path: Path) -> None:
    assert importlib.util.find_spec("app.shell") is not None

    from app.sdk import EmbeddedClientConfig
    from app.shell import AnvilShell, bootstrap_profile_home

    anvil_home = contract_tmp_path / ".anvil-home"
    profile = bootstrap_profile_home("coder", anvil_home=anvil_home)
    runtime_thread_root = contract_tmp_path / "runtime-threads"
    runtime_state_db = contract_tmp_path / "runtime.sqlite3"

    shell = AnvilShell(
        profile=profile,
        client_config=EmbeddedClientConfig(
            config_layers=[
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
                            }
                        },
                    },
                )
            ],
            thread_root=runtime_thread_root,
            state_db_path=runtime_state_db,
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello from shell")]),
        ),
    )

    thread_output = shell.execute_input("/new shell-thread")
    assert "shell-thread" in thread_output
    assert shell.session.current_thread_id == "shell-thread"

    mode_output = shell.execute_input("/mode chat")
    assert "chat" in mode_output
    assert shell.session.execution_mode == "chat"

    plan_output = shell.execute_input("/plan on")
    assert "on" in plan_output
    assert shell.session.plan_mode is True

    message_output = shell.execute_input("hello shell")
    assert "hello from shell" in message_output
    assert shell.client.deps.path_service.base_root == runtime_thread_root
    assert profile.home not in shell.client.deps.path_service.base_root.parents

    shell.close()


def test_shell_renders_and_resumes_structured_user_interaction(contract_tmp_path: Path) -> None:
    from app.sdk import EmbeddedClientConfig
    from app.shell import AnvilShell, bootstrap_profile_home

    profile = bootstrap_profile_home("coder", anvil_home=contract_tmp_path / ".anvil-home")
    runtime_thread_root = contract_tmp_path / "runtime-threads"
    shell = AnvilShell(
        profile=profile,
        client_config=EmbeddedClientConfig(
            config_layers=[
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
                            }
                        },
                    },
                )
            ],
            thread_root=runtime_thread_root,
            state_db_path=contract_tmp_path / "runtime.sqlite3",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "ask_clarification",
                                "args": {
                                    "title": "Choose stack",
                                    "question": "Which frontend stack?",
                                    "selection_mode": "single",
                                    "options": [
                                        {"id": "vite", "label": "Vite", "recommended": True},
                                        {"id": "next", "label": "Next.js"},
                                    ],
                                },
                                "id": "call-stack",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="Using Vite."),
                ]
            ),
        ),
    )

    try:
        shell.execute_input("/new interaction-shell")
        output = shell.execute_input("build app")
        assert "Input needed:" in output
        assert "Request: call-stack" in output
        assert "- vite: Vite (recommended)" in output

        resumed = shell.execute_input("/answer --choice vite")
        assert "Using Vite." in resumed
        state = shell.client.get_thread_state("interaction-shell")
        assert state.pending_user_interaction is None
    finally:
        shell.close()


def test_shell_answers_multi_field_structured_user_interaction(contract_tmp_path: Path) -> None:
    from app.sdk import EmbeddedClientConfig
    from app.shell import AnvilShell, bootstrap_profile_home

    profile = bootstrap_profile_home("coder", anvil_home=contract_tmp_path / ".anvil-home")
    runtime_thread_root = contract_tmp_path / "runtime-threads"
    shell = AnvilShell(
        profile=profile,
        client_config=EmbeddedClientConfig(
            config_layers=[
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
                            }
                        },
                    },
                )
            ],
            thread_root=runtime_thread_root,
            state_db_path=contract_tmp_path / "runtime.sqlite3",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "ask_clarification",
                                "args": {
                                    "title": "Frontend decisions",
                                    "question": "Choose scaffold details.",
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
                                        {
                                            "id": "notes",
                                            "label": "Notes",
                                            "selection_mode": "text",
                                            "required": False,
                                        },
                                    ],
                                },
                                "id": "call-form",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="Using Vite with tests."),
                ]
            ),
        ),
    )

    try:
        shell.execute_input("/new interaction-form-shell")
        output = shell.execute_input("build app")
        assert "Choose scaffold details." in output
        assert "Fields:" in output
        assert "- stack: Framework [single]" in output
        assert "- scope: Completeness [multiple]" in output

        resumed = shell.execute_input("/answer --field stack=vite --field scope=tests --field notes:Keep it quiet")
        assert "Using Vite with tests." in resumed
        state = shell.client.get_thread_state("interaction-form-shell")
        assert state.pending_user_interaction is None
    finally:
        shell.close()


def test_shell_answer_without_args_opens_keyboard_selector(contract_tmp_path: Path, monkeypatch) -> None:
    from app.sdk import EmbeddedClientConfig
    from app.shell import AnvilShell, bootstrap_profile_home
    import app.shell.tui as tui

    profile = bootstrap_profile_home("coder", anvil_home=contract_tmp_path / ".anvil-home")
    runtime_thread_root = contract_tmp_path / "runtime-threads"
    shell = AnvilShell(
        profile=profile,
        client_config=EmbeddedClientConfig(
            config_layers=[
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
                            }
                        },
                    },
                )
            ],
            thread_root=runtime_thread_root,
            state_db_path=contract_tmp_path / "runtime.sqlite3",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "ask_clarification",
                                "args": {
                                    "title": "Choose stack",
                                    "question": "Which frontend stack?",
                                    "selection_mode": "single",
                                    "options": [
                                        {"id": "vite", "label": "Vite", "recommended": True},
                                        {"id": "next", "label": "Next.js"},
                                    ],
                                },
                                "id": "call-stack",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="Using Next.js."),
                ]
            ),
        ),
    )

    def fake_collect(interaction, *, style):
        assert interaction.request_id == "call-stack"
        return {
            "choices": ["next"],
            "custom": None,
            "free_text": None,
            "fields": [],
        }

    monkeypatch.setattr(tui, "_collect_user_interaction_with_prompt_toolkit", fake_collect)

    try:
        shell.execute_input("/new interaction-selector-shell")
        shell.execute_input("build app")
        resumed = shell.execute_input("/answer")
        assert "Using Next.js." in resumed
        state = shell.client.get_thread_state("interaction-selector-shell")
        assert state.pending_user_interaction is None
    finally:
        shell.close()


def test_shell_can_start_and_tail_terminal_process(contract_tmp_path: Path) -> None:
    assert importlib.util.find_spec("app.shell") is not None

    import sys

    from app.sdk import EmbeddedClientConfig
    from app.shell import AnvilShell, bootstrap_profile_home

    anvil_home = contract_tmp_path / ".anvil-home"
    profile = bootstrap_profile_home("coder", anvil_home=anvil_home)
    runtime_thread_root = contract_tmp_path / "runtime-threads"
    runtime_state_db = contract_tmp_path / "runtime.sqlite3"
    workspace = runtime_thread_root / "shell-process" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Shell context rule.\n", encoding="utf-8")

    shell = AnvilShell(
        profile=profile,
        client_config=EmbeddedClientConfig(
            config_layers=[
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
                            }
                        },
                    },
                )
            ],
            thread_root=runtime_thread_root,
            state_db_path=runtime_state_db,
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="context ready")]),
        ),
    )

    try:
        shell.execute_input("/new shell-process")
        assert "context ready" in shell.execute_input("prime context")
        terminal_output = shell.execute_input("/terminal")
        assert "Backend:" in terminal_output
        assert "Launch:" in terminal_output
        assert "Workspace sync:" in terminal_output
        run_output = shell.execute_input(f'/run "{sys.executable}" -c "print(\'shell-run-ok\')"')
        assert "Started proc_" in run_output
        session_id = run_output.split()[1]
        shell.client.wait_process_session("shell-process", session_id, timeout_seconds=5)
        tail_output = shell.execute_input(f"/tail {session_id}")
        assert "shell-run-ok" in tail_output
        context_output = shell.execute_input("/context")
        assert "Project context:" in context_output
        assert "/mnt/user-data/workspace/AGENTS.md applies_to=/mnt/user-data/workspace scope=." in context_output
        assert str(runtime_thread_root) not in context_output
    finally:
        shell.close()


def test_shell_virtualizes_context_file_display_paths(contract_tmp_path: Path) -> None:
    from app.sdk import EmbeddedClientConfig
    from app.shell import AnvilShell, bootstrap_profile_home

    profile = bootstrap_profile_home("coder", anvil_home=contract_tmp_path / ".anvil-home")
    runtime_thread_root = contract_tmp_path / "runtime-threads"
    shell = AnvilShell(
        profile=profile,
        client_config=EmbeddedClientConfig(
            thread_root=runtime_thread_root,
            state_db_path=contract_tmp_path / "runtime.sqlite3",
        ),
    )
    try:
        shell.client.deps.path_service.bootstrap_thread_paths("shell-process")
        actual_path = runtime_thread_root / "shell-process" / "workspace" / "AGENTS.md"

        assert (
            shell._virtual_context_path("shell-process", str(actual_path), "AGENTS.md")
            == "/mnt/user-data/workspace/AGENTS.md"
        )
    finally:
        shell.close()


def test_shell_gateway_exposes_command_catalog(gateway_client) -> None:
    response = gateway_client.get("/shell/commands", params={"scope": "gateway"})
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["commands"]}
    assert "/stream" in names
    assert "/run" in names
    assert "/terminal" in names
    assert "/processes" in names
    assert "/skills" in names
    assert payload["groups"]["Terminal"] >= 4

    complete = gateway_client.get("/shell/commands/complete", params={"scope": "gateway", "prefix": "/pro"})
    assert complete.status_code == 200
    completed_names = {item["name"] for item in complete.json()["commands"]}
    assert {"/processes", "/process-log", "/profile"}.issubset(completed_names)
