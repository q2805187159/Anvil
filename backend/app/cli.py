from __future__ import annotations

import argparse
import os
from pathlib import Path
from pathlib import PurePosixPath
import sys
from typing import Any

import yaml

from anvil.agents import ThreadExecutionMode
from anvil.config import (
    build_default_config_layers,
    ConfigService,
    build_config_layers_from_file,
    get_anvil_home,
    get_repo_root,
    llm_provider_preset,
    llm_provider_presets,
    load_dotenv_file,
    read_config_file,
    resolve_anvil_config_path,
    resolve_anvil_profile_home,
    resolve_anvil_profile_name,
    resolve_config_path,
    resolve_plugin_config_paths,
)
from anvil.skills import (
    default_installed_skill_root,
    default_repo_skill_root,
)

from app.sdk import EmbeddedClient, EmbeddedClientConfig, EmbeddedRunRequest
from app.gateway.services import GatewayAdapterError
from app.shell.main import run_shell
from app.shell.profile import ShellProfile, bootstrap_profile_home, read_active_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil", description="Anvil command line interface.")
    parser.add_argument("--profile", default=None, help="Profile name for shell state.")
    parser.add_argument("--anvil-home", default=None, help="Override ANVIL_HOME/profile state root.")
    parser.add_argument("--config", default=None, help="Path to config.yaml.")
    subparsers = parser.add_subparsers(dest="command")

    setup = subparsers.add_parser("setup", help="Initialize or update local Anvil configuration.")
    setup.add_argument("--provider", default=None, help="LLM provider preset, for example minimax/openai/openrouter/deepseek/mimo.")
    setup.add_argument("--model", default=None, help="Provider model name.")
    setup.add_argument("--api-key", default=None, help="Provider API key value to write into the active profile .env.")
    setup.add_argument("--api-key-env", default=None, help="Environment variable that stores the provider API key.")
    setup.add_argument("--base-url", default=None, help="OpenAI-compatible base URL override.")
    setup.add_argument("--non-interactive", action="store_true", help="Do not prompt for missing values.")
    setup.add_argument("--force", action="store_true", help="Replace existing config with a minimal Anvil config before applying setup values.")

    step = subparsers.add_parser("step", help="Run one agent step from the CLI.")
    step.add_argument("message", nargs="*", help="Prompt message. Reads stdin when omitted and stdin is piped.")
    step.add_argument("--thread", default=None, help="Thread id to use or create.")
    step.add_argument("--mode", choices=[item.value for item in ThreadExecutionMode], default=ThreadExecutionMode.AGENT.value)
    step.add_argument("--model", default=None, help="Selected model for this thread.")
    step.add_argument("--stream", action="store_true", help="Stream structured step output.")
    step.add_argument("--choice", action="append", default=[], help="Option id for a pending structured interaction. Repeat for multi-select.")
    step.add_argument("--custom", default=None, help="Custom response for a pending structured interaction.")
    step.add_argument("--free-text", default=None, help="Free-text response for a pending structured interaction.")
    step.add_argument("--interactive", action="store_true", help="Open the keyboard-driven TUI selector for a pending structured interaction.")
    step.add_argument(
        "--field",
        action="append",
        default=[],
        help="Field response for a multi-field interaction, for example stack=vite, scope=routing,tests, notes:Keep it simple.",
    )

    model = subparsers.add_parser("model", help="List, inspect, select, or add model providers.")
    model_sub = model.add_subparsers(dest="model_command")
    model_sub.add_parser("list", help="List configured model providers.")
    model_show = model_sub.add_parser("show", help="Show one model provider.")
    model_show.add_argument("name")
    model_use = model_sub.add_parser("use", help="Set selected model for a thread.")
    model_use.add_argument("name")
    model_use.add_argument("--thread", default=None)
    model_add = model_sub.add_parser("add", help="Add/update a model provider in config.yaml.")
    model_add.add_argument("name")
    model_add.add_argument("--provider", default=None)
    model_add.add_argument("--model", dest="model_name", default=None)
    model_add.add_argument("--api-key", default=None)
    model_add.add_argument("--api-key-env", default=None)
    model_add.add_argument("--base-url", default=None)
    model_delete = model_sub.add_parser("delete", help="Delete a model provider from config.yaml.")
    model_delete.add_argument("name")

    tools = subparsers.add_parser("tools", help="List or inspect runtime tools.")
    tools_sub = tools.add_subparsers(dest="tools_command")
    tools_list = tools_sub.add_parser("list", help="List tool catalog entries.")
    tools_list.add_argument("query", nargs="*", help="Optional search query.")
    tools_show = tools_sub.add_parser("show", help="Show one tool by name or capability id.")
    tools_show.add_argument("name")

    skills = subparsers.add_parser("skills", help="List or inspect discovered skills.")
    skills_sub = skills.add_subparsers(dest="skills_command")
    skills_list = skills_sub.add_parser("list", help="List discovered skills.")
    skills_list.add_argument("query", nargs="*", help="Optional search query.")
    skills_show = skills_sub.add_parser("show", help="Show one skill manifest.")
    skills_show.add_argument("skill_id")
    skills_content = skills_sub.add_parser("content", help="Show one skill SKILL.md body.")
    skills_content.add_argument("skill_id")
    skills_files = skills_sub.add_parser("files", help="List files owned by one skill.")
    skills_files.add_argument("skill_id")

    mcp = subparsers.add_parser("mcp", help="List or inspect MCP servers.")
    mcp_sub = mcp.add_subparsers(dest="mcp_command")
    mcp_sub.add_parser("list", help="List configured MCP servers.")
    mcp_sub.add_parser("config", help="Show MCP config overview.")
    mcp_tools = mcp_sub.add_parser("tools", help="List tools exposed by one MCP server.")
    mcp_tools.add_argument("server_id")
    mcp_provenance = mcp_sub.add_parser("provenance", help="Show redacted MCP server provenance.")
    mcp_provenance.add_argument("server_id")
    mcp_resources = mcp_sub.add_parser("resources", help="List MCP resources.")
    mcp_resources.add_argument("server_id", nargs="?")
    mcp_prompts = mcp_sub.add_parser("prompts", help="List MCP prompts.")
    mcp_prompts.add_argument("server_id", nargs="?")

    plugins = subparsers.add_parser("plugins", help="List installed plugins.")
    plugins_sub = plugins.add_subparsers(dest="plugins_command")
    plugins_sub.add_parser("list", help="List installed plugins.")

    memory = subparsers.add_parser("memory", help="Inspect memory stores, providers, and archive search.")
    memory_sub = memory.add_subparsers(dest="memory_command")
    memory_sub.add_parser("overview", help="Show memory overview.")
    memory_sub.add_parser("stores", help="List memory stores.")
    memory_sub.add_parser("providers", help="List memory providers.")
    memory_search = memory_sub.add_parser("search", help="Search archived memory turns.")
    memory_search.add_argument("query", nargs="+")
    memory_search.add_argument("--limit", type=int, default=5)
    memory_sub.add_parser("reflections", help="List reflection jobs.")

    context = subparsers.add_parser("context", help="Show thread context and runtime path roots.")
    context_sub = context.add_subparsers(dest="context_command")
    context_show = context_sub.add_parser("show", help="Show one thread context snapshot.")
    context_show.add_argument("--thread", default=None)

    scheduled = subparsers.add_parser("scheduled", help="List scheduled automations.")
    scheduled_sub = scheduled.add_subparsers(dest="scheduled_command")
    scheduled_sub.add_parser("list", help="List scheduled automations.")
    scheduled_executions = scheduled_sub.add_parser("executions", help="List scheduled automation executions.")
    scheduled_executions.add_argument("--task-id", default=None)
    scheduled_executions.add_argument("--limit", type=int, default=50)

    config = subparsers.add_parser("config", help="Inspect or update config.yaml.")
    config_sub = config.add_subparsers(dest="config_command")
    config_sub.add_parser("path", help="Print the active config path.")
    config_sub.add_parser("roots", help="Print Anvil config, skill, MCP, and plugin roots.")
    config_sub.add_parser("show", help="Print normalized effective config.")
    config_set = config_sub.add_parser("set", help="Set a scalar config value by dotted path.")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_sub.add_parser("check", help="Validate the active config.")

    subparsers.add_parser("shell", help="Start the interactive Anvil TUI shell.")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "shell"
    profile = _profile(args)
    config_path = _config_path(args.config, profile=profile)

    if command == "shell":
        run_shell(
            profile_name=args.profile,
            anvil_home=_optional_path(args.anvil_home),
            client_config=_client_config(config_path, profile=profile),
        )
        return
    if command == "setup":
        print(_handle_setup(args, config_path=config_path))
        return
    if command == "step":
        print(_handle_step(args, config_path=config_path, profile=profile))
        return
    if command == "model":
        print(_handle_model(args, config_path=config_path, profile=profile))
        return
    if command == "tools":
        print(_handle_tools(args, config_path=config_path, profile=profile))
        return
    if command == "skills":
        print(_handle_skills(args, config_path=config_path, profile=profile))
        return
    if command == "mcp":
        print(_handle_mcp(args, config_path=config_path, profile=profile))
        return
    if command == "plugins":
        print(_handle_plugins(args, config_path=config_path, profile=profile))
        return
    if command == "memory":
        print(_handle_memory(args, config_path=config_path, profile=profile))
        return
    if command == "context":
        print(_handle_context(args, config_path=config_path, profile=profile))
        return
    if command == "scheduled":
        print(_handle_scheduled(args, config_path=config_path, profile=profile))
        return
    if command == "config":
        print(_handle_config(args, config_path=config_path))
        return
    parser.print_help()


def _handle_setup(args: argparse.Namespace, *, config_path: Path) -> str:
    if config_path.exists() and args.force:
        payload = _minimal_config_payload()
    else:
        payload = _read_config_or_empty(config_path)
    if not config_path.exists():
        payload = _minimal_config_payload()
    if not args.non_interactive and not args.provider:
        print(_render_provider_choices())
    provider = args.provider or (None if args.non_interactive else _prompt("Provider preset", default=str(payload.get("default_model") or "minimax")))
    if provider:
        provider_name = _provider_name(provider)
        preset = llm_provider_preset(provider_name)
        default_url = str(preset.get("base_url") or preset.get("api_base") or "")
        default_model = _preset_default_model(preset)
        api_key_env = args.api_key_env or (None if args.non_interactive else _prompt("API key env", default=_default_key_env(provider_name, preset)))
        model_name = args.model or (None if args.non_interactive else _prompt("Model", default=default_model))
        base_url = args.base_url
        if base_url is None and not args.non_interactive:
            base_url = _prompt("Base URL", default=default_url)
        api_key = args.api_key
        if api_key is None and not args.non_interactive and api_key_env:
            api_key = _prompt_secret(f"{api_key_env} value", default="")
        _upsert_model_provider(
            payload,
            name=provider_name,
            provider=provider_name,
            model_name=model_name or None,
            api_key_env=api_key_env or None,
            base_url=base_url,
            bootstrap_default=not _has_default_model_provider(payload),
        )
        if api_key and api_key_env:
            _upsert_dotenv_value(config_path.parent / ".env", api_key_env, api_key)
            os.environ[api_key_env] = api_key
    _write_config(config_path, payload)
    load_dotenv_file(config_path.parent / ".env", override=True)
    return f"Config ready: {config_path}"


def _handle_step(args: argparse.Namespace, *, config_path: Path, profile: ShellProfile) -> str:
    message = " ".join(args.message).strip()
    has_interaction_response = bool(args.choice or args.custom or args.free_text or args.field)
    if not message and not has_interaction_response and not sys.stdin.isatty():
        try:
            message = sys.stdin.read().strip()
        except OSError:
            message = ""
    if not message and not has_interaction_response and not args.thread:
        return "Usage: anvil step <message>"
    with EmbeddedClient(_client_config(config_path, profile=profile)) as client:
        thread_id = args.thread or client.create_thread().thread_id
        if args.thread:
            try:
                client.get_thread(thread_id)
            except GatewayAdapterError as exc:
                if exc.error != "thread_not_found":
                    raise
                client.create_thread(thread_id=thread_id)
        if args.model:
            from app.contracts import ThreadSettingsUpdateRequest

            client.update_thread_settings(thread_id, ThreadSettingsUpdateRequest(selected_model=args.model))
        state = client.get_thread_state(thread_id)
        if state.pending_user_interaction is not None:
            if args.interactive and not has_interaction_response:
                from app.shell.tui import AnvilShell

                shell = AnvilShell(profile=profile, client=client)
                shell.session.current_thread_id = thread_id
                return shell.execute_input("/answer")
            if not has_interaction_response:
                return _render_user_interaction_prompt(state.pending_user_interaction)
            from app.contracts import UserInteractionResumeRequest

            body = UserInteractionResumeRequest(
                request_id=state.pending_user_interaction.request_id,
                selected_option_ids=list(args.choice or []),
                custom_response=args.custom,
                free_text=args.free_text,
                field_responses=_parse_interaction_field_args(args.field or []),
            )
            if args.stream:
                lines: list[str] = []
                for event in client.stream_user_interaction(thread_id, body):
                    rendered = _render_stream_event(event.event, event.data)
                    if rendered:
                        print(rendered)
                        lines.append(rendered)
                return "\n".join(lines)
            result = client.resume_user_interaction(thread_id, body)
            return result.assistant_message or result.last_error or result.status
        if not message:
            return "Usage: anvil step <message>"
        request = EmbeddedRunRequest(
            thread_id=thread_id,
            message=message,
            execution_mode=ThreadExecutionMode(args.mode),
        )
        if args.stream:
            lines: list[str] = []
            for event in client.stream(request):
                rendered = _render_stream_event(event.event, event.data)
                if rendered:
                    print(rendered)
                    lines.append(rendered)
            state = client.get_thread_state(thread_id)
            if state.pending_user_interaction is not None:
                rendered = _render_user_interaction_prompt(state.pending_user_interaction)
                print(rendered)
                lines.append(rendered)
            return "\n".join(lines)
        result = client.run(request)
        return result.assistant_message or result.last_error or result.status


def _handle_model(args: argparse.Namespace, *, config_path: Path, profile: ShellProfile) -> str:
    subcommand = args.model_command or "list"
    if subcommand == "add":
        payload = _read_config_or_empty(config_path)
        provider_name = _provider_name(args.provider or args.name)
        preset = llm_provider_preset(provider_name)
        api_key_env = args.api_key_env or _default_key_env(provider_name, preset)
        _upsert_model_provider(
            payload,
            name=args.name,
            provider=provider_name,
            model_name=args.model_name,
            api_key_env=api_key_env,
            base_url=args.base_url,
            bootstrap_default=not _has_default_model_provider(payload),
        )
        if args.api_key:
            _upsert_dotenv_value(config_path.parent / ".env", api_key_env, args.api_key)
            os.environ[api_key_env] = args.api_key
        _write_config(config_path, payload)
        load_dotenv_file(config_path.parent / ".env", override=True)
        return f"Model provider saved: {args.name}"
    if subcommand == "delete":
        payload = _read_config_or_empty(config_path)
        removed = _delete_model_provider_payload(payload, args.name)
        if not removed:
            return f"Unknown model provider: {args.name}"
        _write_config(config_path, payload)
        return f"Model provider deleted: {args.name}"
    with EmbeddedClient(_client_config(config_path, profile=profile)) as client:
        if subcommand == "show":
            model = next((item for item in client.list_models() if item.name == args.name), None)
            if model is None:
                return f"Unknown model: {args.name}"
            return yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        if subcommand == "use":
            thread_id = args.thread or _active_thread_id(profile=profile)
            if thread_id is None:
                thread_id = client.create_thread().thread_id
            from app.contracts import ThreadSettingsUpdateRequest

            settings = client.update_thread_settings(thread_id, ThreadSettingsUpdateRequest(selected_model=args.name))
            return f"Thread {thread_id} model: {settings.selected_model or 'default'}"
        models = client.list_models()
        if not models:
            return "No models configured. Run: anvil setup"
        return "\n".join(
            f"{item.name} [{item.provider}] model={item.model_name or item.default_model or '-'} available={item.available}"
            for item in models
        )


def _handle_tools(args: argparse.Namespace, *, config_path: Path, profile: ShellProfile) -> str:
    subcommand = args.tools_command or "list"
    with EmbeddedClient(_client_config(config_path, profile=profile)) as client:
        if subcommand == "show":
            try:
                item = client.get_tool_catalog_entry(args.name)
            except Exception as exc:
                return f"Unknown tool: {args.name} ({exc})"
            return yaml.safe_dump(item.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        query = " ".join(args.query).strip() or None
        items = client.list_tool_catalog(query=query)
        if not items:
            return "No tools matched."
        return "\n".join(f"{item.name} [{item.capability_group}] {item.summary}" for item in items)


def _handle_skills(args: argparse.Namespace, *, config_path: Path, profile: ShellProfile) -> str:
    subcommand = args.skills_command or "list"
    with EmbeddedClient(_client_config(config_path, profile=profile)) as client:
        if subcommand == "show":
            return yaml.safe_dump(client.get_skill(args.skill_id).model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        if subcommand == "content":
            return client.get_skill_content(args.skill_id).content
        if subcommand == "files":
            files = client.list_skill_files(args.skill_id).files
            if not files:
                return "No skill files."
            return "\n".join(f"{item.path} [{item.kind}] {item.size_bytes} bytes" for item in files)
        query = " ".join(args.query).strip().lower()
        skills = client.list_skills()
        if query:
            skills = [
                item
                for item in skills
                if query in " ".join([item.skill_id, item.title, item.summary, item.source_scope or ""]).lower()
            ]
        if not skills:
            return "No skills matched."
        return "\n".join(
            f"{item.skill_id} [{item.source_scope or 'unknown'}] enabled={item.enabled} valid={item.valid} - {item.summary}"
            for item in skills
        )


def _handle_mcp(args: argparse.Namespace, *, config_path: Path, profile: ShellProfile) -> str:
    subcommand = args.mcp_command or "list"
    with EmbeddedClient(_client_config(config_path, profile=profile)) as client:
        if subcommand == "config":
            return yaml.safe_dump(client.get_mcp_config_overview().model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        if subcommand == "tools":
            item = client.get_mcp_server_tools(args.server_id)
            if not item.tool_names:
                return f"{item.server_id} [{item.status}] exposes no tools."
            return "\n".join(item.tool_names)
        if subcommand == "provenance":
            return yaml.safe_dump(client.get_mcp_server_provenance(args.server_id).model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        if subcommand == "resources":
            resources = client.list_mcp_resources(server_id=args.server_id)
            if not resources:
                return "No MCP resources."
            return "\n".join(f"{item.server_id}/{item.resource_id} {item.title}" for item in resources)
        if subcommand == "prompts":
            prompts = client.list_mcp_prompts(server_id=args.server_id)
            if not prompts:
                return "No MCP prompts."
            return "\n".join(f"{item.server_id}/{item.prompt_id} {item.title}" for item in prompts)
        servers = client.list_mcp_servers()
        if not servers:
            return "No MCP servers configured."
        return "\n".join(
            f"{item.server_id} [{item.status}] enabled={item.enabled} ready={item.ready} tools={item.tool_count} resources={item.resource_count} prompts={item.prompt_count}"
            for item in servers
        )


def _handle_plugins(args: argparse.Namespace, *, config_path: Path, profile: ShellProfile) -> str:
    _ = args.plugins_command or "list"
    with EmbeddedClient(_client_config(config_path, profile=profile)) as client:
        plugins = client.list_plugins()
        if not plugins:
            return "No plugins installed."
        return "\n".join(
            f"{item.plugin_id} enabled={item.enabled} tools={item.tool_count} skills={len(item.skill_roots)} memory={item.memory_provider_count}"
            for item in plugins
        )


def _handle_memory(args: argparse.Namespace, *, config_path: Path, profile: ShellProfile) -> str:
    subcommand = args.memory_command or "overview"
    with EmbeddedClient(_client_config(config_path, profile=profile)) as client:
        if subcommand == "stores":
            stores = client.list_memory_stores()
            if not stores:
                return "No memory stores."
            return "\n".join(f"{item.store_id} entries={item.entry_count} usage={item.usage_tokens} tokens" for item in stores)
        if subcommand == "providers":
            providers = client.list_memory_providers()
            if not providers:
                return "No memory providers."
            return "\n".join(f"{item.provider_id} [{item.family}] active={item.active}" for item in providers)
        if subcommand == "search":
            result = client.search_memory_archive(" ".join(args.query), limit=args.limit)
            if not result.hits:
                return "No archive hits."
            return "\n".join(f"{hit.thread_id}: {hit.excerpt}" for hit in result.hits)
        if subcommand == "reflections":
            jobs = client.list_reflection_jobs()
            if not jobs:
                return "No reflection jobs."
            return "\n".join(f"{item.job_id} [{item.template}] enabled={item.enabled}" for item in jobs)
        overview = client.get_memory_overview()
        lines = [
            f"Provider: {overview.active_provider_id or 'none'}",
            f"Stores: {overview.store_count}",
            f"Archive turns: {overview.archive_turn_count}",
            f"Reflection jobs: {overview.reflection_job_count}",
        ]
        lines.extend(f"- {store.store_id}: {store.entry_count} entries" for store in overview.stores)
        return "\n".join(lines)


def _handle_context(args: argparse.Namespace, *, config_path: Path, profile: ShellProfile) -> str:
    subcommand = args.context_command or "show"
    if subcommand != "show":
        return "Usage: anvil context show [--thread <thread_id>]"
    with EmbeddedClient(_client_config(config_path, profile=profile)) as client:
        thread_id = args.thread or _active_thread_id(profile=profile)
        if thread_id is None:
            return "No active thread. Pass --thread or create a thread first."
        state = client.get_thread_state(thread_id)
        roots = state.runtime_path_roots or []
        context_files = state.project_context_files or []
        lines = [
            f"Thread: {state.thread_id}",
            f"Status: {state.status}",
            f"Workspace mode: {state.workspace_mode}",
            f"Workspace root: {state.workspace_root or 'thread'}",
            f"Resolved workspace: {state.resolved_workspace_path or 'none'}",
            f"Prompt snapshot: {state.prompt_snapshot_id or 'none'}",
            f"Prompt hash: {state.prompt_snapshot_hash or 'none'}",
            f"Project context: {state.project_context_fingerprint or 'none'}",
            f"Visible tools: {len(state.visible_tool_names)}",
            f"Enabled skills: {len(state.enabled_skill_ids)}",
            "Runtime roots:",
        ]
        lines.extend(f"- {root.virtual_path} [{root.kind}] {root.display_root or ''}".rstrip() for root in roots)
        lines.append("Context files:")
        lines.extend(
            f"- {_virtual_context_path(client, state.thread_id, item.virtual_path, item.relative_path)} applies_to={item.applies_to} scope={item.scope}{' truncated' if item.truncated else ''}"
            for item in context_files
        )
        if not context_files:
            lines.append("- none")
        return "\n".join(lines)


def _handle_scheduled(args: argparse.Namespace, *, config_path: Path, profile: ShellProfile) -> str:
    subcommand = args.scheduled_command or "list"
    with EmbeddedClient(_client_config(config_path, profile=profile)) as client:
        if subcommand == "executions":
            executions = client.list_scheduled_task_executions(task_id=args.task_id, limit=args.limit)
            if not executions:
                return "No scheduled task executions."
            return "\n".join(f"{item.execution_id} task={item.task_id} status={item.status} started={item.started_at}" for item in executions)
        tasks = client.list_scheduled_tasks()
        if not tasks:
            return "No scheduled automations."
        return "\n".join(f"{item.task_id} [{item.status}] next={item.next_run_at}" for item in tasks)


def _virtual_context_path(client: EmbeddedClient, thread_id: str, value: str, relative_path: str | None = None) -> str:
    path_service = client.deps.path_service
    try:
        virtual_path = path_service.to_virtual_path(thread_id, value)
    except Exception:
        virtual_path = path_service.translate_runtime_text_to_virtual(value, thread_id=thread_id) or value
    if not virtual_path.startswith("/mnt/") and relative_path:
        normalized_relative = relative_path.replace("\\", "/").strip("/")
        if normalized_relative:
            return (PurePosixPath("/mnt/user-data/workspace") / normalized_relative).as_posix()
    if not virtual_path.startswith("/mnt/"):
        normalized_value = value.replace("\\", "/")
        marker = "/workspace/"
        marker_index = normalized_value.lower().rfind(marker)
        if marker_index >= 0:
            relative = normalized_value[marker_index + len(marker) :].strip("/")
            if relative:
                return (PurePosixPath("/mnt/user-data/workspace") / relative).as_posix()
    return virtual_path


def _handle_config(args: argparse.Namespace, *, config_path: Path) -> str:
    subcommand = args.config_command or "show"
    if subcommand == "path":
        return str(config_path)
    if subcommand == "roots":
        return _render_config_roots(config_path)
    if subcommand == "set":
        payload = _read_config_or_empty(config_path)
        _set_dotted(payload, args.key, _parse_scalar(args.value))
        _write_config(config_path, payload)
        return f"Set {args.key}"
    if subcommand == "check":
        layers = build_config_layers_from_file(config_path)
        ConfigService().resolve(layers)
        return "Config ok"
    payload = _read_config_or_empty(config_path)
    normalized = ConfigService().resolve(build_config_layers_from_file(config_path)).effective_config
    return yaml.safe_dump(normalized.model_dump(mode="json"), sort_keys=False, allow_unicode=True) if payload else "{}"


def _client_config(config_path: Path, *, profile: ShellProfile) -> EmbeddedClientConfig:
    config_layers = build_config_layers_from_file(config_path) if config_path.exists() else build_default_config_layers()
    return EmbeddedClientConfig(
        config_layers=config_layers,
        thread_root=profile.home / "sessions",
        state_db_path=profile.home / "runtime.sqlite3",
    )


def _profile(args: argparse.Namespace) -> ShellProfile:
    anvil_home = _optional_path(getattr(args, "anvil_home", None))
    profile_name = getattr(args, "profile", None) or read_active_profile(anvil_home=anvil_home)
    return bootstrap_profile_home(profile_name, anvil_home=anvil_home)


def _config_path(value: str | None, *, profile: ShellProfile | None = None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    if profile is not None:
        return profile.config_path
    return resolve_config_path() or resolve_anvil_config_path()


def _render_config_roots(config_path: Path) -> str:
    repo_root = get_repo_root()
    profile_name = resolve_anvil_profile_name()
    profile_home = resolve_anvil_profile_home(profile_name)
    payload: dict[str, object] = {
        "config": str(config_path),
        "repo_root": str(repo_root),
        "anvil_home": str(get_anvil_home()),
        "profile": profile_name,
        "profile_home": str(profile_home),
        "skills": {
            "installed": str(default_installed_skill_root()),
            "bundled_source": str(default_repo_skill_root()),
        },
        "mcp": {
            "config": str(config_path),
            "key": "mcp_servers",
        },
        "plugins": {
            "discovered": [str(path) for path in resolve_plugin_config_paths(repo_root=repo_root)],
        },
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _optional_path(value: str | None) -> Path | None:
    return Path(value).expanduser().resolve() if value else None


def _read_config_or_empty(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _minimal_config_payload()
    return read_config_file(path)


def _write_config(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _provider_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _default_key_env(provider_name: str, preset: dict[str, Any] | None = None) -> str:
    if preset:
        api_key = preset.get("api_key")
        if isinstance(api_key, str) and api_key.startswith("$"):
            return api_key.strip("${}")
        provider_settings = preset.get("provider_settings")
        if isinstance(provider_settings, dict):
            for value in provider_settings.values():
                if isinstance(value, str) and value.startswith("$"):
                    return value.strip("${}")
    return {
        "openai": "OPENAI_API_KEY",
        "openai_responses": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "minimax_global": "MINIMAX_API_KEY",
        "mimo": "MIMO_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "qwen": "QWEN_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }.get(provider_name, f"{provider_name.upper()}_API_KEY")


def _upsert_model_provider(
    payload: dict[str, Any],
    *,
    name: str,
    provider: str,
    model_name: str | None,
    api_key_env: str | None,
    base_url: str | None,
    bootstrap_default: bool,
) -> None:
    preset = llm_provider_preset(provider)
    llm = payload.setdefault("llm", {})
    if not isinstance(llm, dict):
        raise ValueError("config key 'llm' must be a mapping")
    providers = llm.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("config key 'llm.providers' must be a mapping")
    entry = {key: value for key, value in preset.items() if value is not None}
    entry.update(dict(providers.get(name) or {}))
    entry["provider"] = provider
    if model_name:
        entry["model"] = model_name
        entry["model_name"] = model_name
        entry["default_model"] = model_name
        entry["selected_model"] = model_name
    if api_key_env:
        entry["api_key"] = f"${{{api_key_env}}}"
    if base_url:
        entry["base_url"] = base_url
    providers[name] = entry
    if bootstrap_default:
        llm["default"] = name
        payload["default_model"] = name


def _has_default_model_provider(payload: dict[str, Any]) -> bool:
    llm = payload.get("llm")
    if isinstance(llm, dict) and llm.get("default"):
        return True
    return bool(payload.get("default_model"))


def _delete_model_provider_payload(payload: dict[str, Any], name: str) -> bool:
    provider_name = _provider_name(name)
    removed = False
    llm = payload.get("llm")
    providers = llm.get("providers") if isinstance(llm, dict) else None
    if isinstance(providers, dict) and provider_name in providers:
        del providers[provider_name]
        removed = True
        if llm.get("default") == provider_name:
            llm["default"] = next(iter(providers), None)
    models = payload.get("models")
    if isinstance(models, dict) and provider_name in models:
        del models[provider_name]
        removed = True
    if payload.get("default_model") == provider_name:
        payload["default_model"] = llm.get("default") if isinstance(llm, dict) else None
    return removed


def _upsert_dotenv_value(dotenv_path: Path, key: str, value: str) -> None:
    dotenv_path.parent.mkdir(parents=True, exist_ok=True)
    lines = dotenv_path.read_text(encoding="utf-8").splitlines() if dotenv_path.exists() else []
    rendered = f"{key}={_dotenv_quote(value)}"
    replaced = False
    next_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                next_lines.append(rendered)
                replaced = True
                continue
        next_lines.append(line)
    if not replaced:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append(rendered)
    dotenv_path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def _dotenv_quote(value: str) -> str:
    if not value or any(char.isspace() for char in value) or any(char in value for char in ['"', "'", "#"]):
        import json

        return json.dumps(value, ensure_ascii=False)
    return value


def _preset_default_model(preset: dict[str, Any]) -> str:
    for key in ("default_model", "model_name", "selected_model"):
        value = preset.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    model = preset.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    if isinstance(model, list):
        for item in model:
            if str(item).strip():
                return str(item).strip()
    catalog = preset.get("model_catalog")
    if isinstance(catalog, list):
        for item in catalog:
            if str(item).strip():
                return str(item).strip()
    return ""


def _render_provider_choices() -> str:
    presets = llm_provider_presets()
    rows = []
    for name, preset in presets.items():
        display = str(preset.get("display_name") or name)
        default_model = _preset_default_model(preset) or "-"
        base_url = str(preset.get("base_url") or preset.get("api_base") or "-")
        rows.append(f"- {name}: {display} model={default_model} url={base_url}")
    return "Available provider presets:\n" + "\n".join(rows)


def _set_dotted(payload: dict[str, Any], key: str, value: Any) -> None:
    parts = [part for part in key.split(".") if part]
    if not parts:
        raise ValueError("config key cannot be empty")
    cursor = payload
    for part in parts[:-1]:
        nested = cursor.setdefault(part, {})
        if not isinstance(nested, dict):
            raise ValueError(f"config key '{part}' is not a mapping")
        cursor = nested
    cursor[parts[-1]] = value


def _parse_scalar(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _minimal_config_payload() -> dict[str, Any]:
    return {
        "llm": {
            "default": None,
            "providers": {},
        },
        "anvil": {
            "profile": "default",
        },
        "workspace": {
            "mode": "thread",
            "path_bridges": [],
        },
        "skills_config": {
            "enabled": True,
            "watch_enabled": True,
            "external_dirs": [],
        },
        "mcp_servers": {},
        "token_usage": {
            "pricing": {},
        },
        "terminal": {
            "active_backend": "local",
            "backends": {
                "local": {
                    "kind": "local",
                    "label": "Local shell",
                    "enabled": True,
                }
            },
        },
    }


def _active_thread_id(*, profile: ShellProfile) -> str | None:
    session_path = profile.sessions_dir / "active-session.json"
    if not session_path.exists():
        return None
    try:
        import json

        payload = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    thread_id = payload.get("current_thread_id")
    return str(thread_id) if thread_id else None


def _prompt(label: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _prompt_secret(label: str, *, default: str = "") -> str:
    if default:
        value = input(f"{label} [hidden default]: ").strip()
    else:
        value = input(f"{label}: ").strip()
    return value or default


def _parse_interaction_field_args(values: list[str]) -> list[dict[str, object]]:
    responses: list[dict[str, object]] = []
    for raw_value in values:
        raw = str(raw_value or "").strip()
        if not raw:
            continue
        separator = ":" if ":" in raw and ("=" not in raw or raw.index(":") < raw.index("=")) else "="
        if separator not in raw:
            raise ValueError("Field responses must use field=value or field:text")
        field_id, value = raw.split(separator, 1)
        field_id = field_id.strip()
        value = value.strip()
        if not field_id:
            raise ValueError("Field id must not be empty")
        if separator == ":":
            responses.append({"field_id": field_id, "free_text": value})
        else:
            selected = [item.strip() for item in value.split(",") if item.strip()]
            responses.append({"field_id": field_id, "selected_option_ids": selected})
    return responses


def _render_user_interaction_prompt(interaction: Any) -> str:
    lines = [
        "Input needed:",
        f"Request: {interaction.request_id}",
    ]
    if interaction.title:
        lines.append(f"Title: {interaction.title}")
    lines.append(f"Question: {interaction.question}")
    if interaction.description:
        lines.append(f"Description: {interaction.description}")
    lines.append(f"Mode: {interaction.selection_mode}")
    if interaction.options:
        lines.append("Options:")
        for option in interaction.options:
            suffix = " (recommended)" if option.recommended else ""
            disabled = " [disabled]" if option.disabled else ""
            detail = f" - {option.description}" if option.description else ""
            lines.append(f"- {option.id}: {option.label}{suffix}{disabled}{detail}")
    if interaction.allow_custom:
        lines.append(f"Custom: {interaction.custom_label or 'allowed'}")
    if interaction.selection_mode == "text" and interaction.placeholder:
        lines.append(f"Placeholder: {interaction.placeholder}")
    if getattr(interaction, "fields", None):
        lines.append("Fields:")
        for field in interaction.fields:
            lines.append(f"- {field.field_id}: {field.label} [{field.selection_mode}]")
            if field.description:
                lines.append(f"  {field.description}")
            for option in field.options:
                suffix = " (recommended)" if option.recommended else ""
                disabled = " [disabled]" if option.disabled else ""
                detail = f" - {option.description}" if option.description else ""
                lines.append(f"  - {option.id}: {option.label}{suffix}{disabled}{detail}")
            if field.allow_custom:
                lines.append(f"  Custom: {field.custom_label or 'allowed'}")
            if field.selection_mode == "text" and field.placeholder:
                lines.append(f"  Placeholder: {field.placeholder}")
        lines.append("Submit: anvil step --thread <thread_id> --field stack=vite --field scope=routing,tests --field notes:Keep it simple")
    else:
        lines.append("Submit: anvil step --thread <thread_id> --choice <option_id> [--choice <option_id>] [--custom <text>] [--free-text <text>]")
    return "\n".join(lines)


def _render_stream_event(event: str, data: dict[str, Any]) -> str:
    if event in {"step_started", "step_updated"}:
        title = str(data.get("title") or data.get("type") or event)
        status = str(data.get("status") or "")
        return f"{event}: {title} {status}".strip()
    if event == "step_delta":
        return str(data.get("delta") or data.get("payload") or "")
    if event == "run_completed":
        return str(data.get("assistant_message") or data.get("last_error") or data.get("status") or "")
    if event == "run_failed":
        return str(data.get("error") or data.get("kind") or "run failed")
    return ""


if __name__ == "__main__":
    main()
