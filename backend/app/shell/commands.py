from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


@dataclass(frozen=True)
class ShellCommandArgumentDef:
    name: str
    placeholder: str
    required: bool = False
    repeatable: bool = False
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShellCommandDef:
    name: str
    description: str
    category: str
    aliases: tuple[str, ...] = ()
    args_hint: str = ""
    action: str = ""
    scopes: tuple[str, ...] = ("tui", "gateway")
    keybinding: str | None = None
    arguments: tuple[ShellCommandArgumentDef, ...] = ()
    stream_output: bool = False
    stateful: bool = False

    @property
    def slash_name(self) -> str:
        return f"/{self.name}"

    @property
    def slash_aliases(self) -> tuple[str, ...]:
        return tuple(f"/{alias}" for alias in self.aliases)

    def matches_scope(self, scope: str | None) -> bool:
        return scope is None or scope in self.scopes


COMMAND_REGISTRY: tuple[ShellCommandDef, ...] = (
    ShellCommandDef(
        "new",
        "Create a new thread and make it active",
        "Session",
        aliases=("n",),
        args_hint="[thread_id]",
        action="thread.create",
        stateful=True,
        arguments=(ShellCommandArgumentDef("thread_id", "thread-id"),),
    ),
    ShellCommandDef(
        "use",
        "Switch to an existing thread",
        "Session",
        aliases=("switch",),
        args_hint="<thread_id>",
        action="thread.switch",
        stateful=True,
        arguments=(ShellCommandArgumentDef("thread_id", "thread-id", required=True),),
    ),
    ShellCommandDef("threads", "List known threads", "Session", aliases=("ls",), action="thread.list"),
    ShellCommandDef("resume", "Reload the current thread detail from durable state", "Session", action="thread.detail"),
    ShellCommandDef("state", "Show authoritative thread state", "Runtime", args_hint="[thread_id]", action="thread.state"),
    ShellCommandDef(
        "mode",
        "Set or show execution mode",
        "Runtime",
        args_hint="[chat|agent|full_access]",
        action="thread.settings.execution_mode",
        stateful=True,
        arguments=(ShellCommandArgumentDef("mode", "mode", values=("chat", "agent", "full_access")),),
    ),
    ShellCommandDef(
        "model",
        "Set or show the selected model for the active thread",
        "Runtime",
        args_hint="[model]",
        action="thread.settings.model",
        stateful=True,
        arguments=(ShellCommandArgumentDef("model", "model"),),
    ),
    ShellCommandDef(
        "plan",
        "Toggle plan mode for the active thread",
        "Runtime",
        args_hint="[on|off]",
        action="thread.settings.plan_mode",
        stateful=True,
        arguments=(ShellCommandArgumentDef("value", "on|off", values=("on", "off")),),
    ),
    ShellCommandDef(
        "stream",
        "Send a prompt through the structured stream path",
        "Run",
        args_hint="<message>",
        action="run.stream",
        stream_output=True,
        stateful=True,
        arguments=(ShellCommandArgumentDef("message", "message", required=True, repeatable=True),),
    ),
    ShellCommandDef(
        "approve",
        "Resume a thread that is awaiting approval",
        "Run",
        args_hint="[thread_id]",
        action="approval.approve",
        stateful=True,
        arguments=(ShellCommandArgumentDef("thread_id", "thread-id"),),
    ),
    ShellCommandDef(
        "answer",
        "Submit a response for a structured decision prompt; no args opens keyboard selection",
        "Run",
        args_hint="[--choice id ...] [--field id=value ...] [--custom text] [--free-text text]",
        action="interaction.answer",
        stateful=True,
    ),
    ShellCommandDef("stop", "Cancel pending approval or interrupt the newest running process", "Run", action="run.stop", keybinding="Ctrl-C"),
    ShellCommandDef("models", "List configured models", "Capability", action="models.list"),
    ShellCommandDef("tools", "List runtime tool catalog entries", "Capability", action="tools.list", args_hint="[query]"),
    ShellCommandDef("skills", "List discovered skills", "Capability", action="skills.list"),
    ShellCommandDef("memory", "Show memory platform overview or stores", "Capability", action="memory.overview"),
    ShellCommandDef("memory-provider", "List memory providers", "Capability", action="memory.providers", args_hint="[provider_id]"),
    ShellCommandDef("memory-search", "Search archived memory turns", "Capability", action="memory.archive_search", args_hint="<query>"),
    ShellCommandDef("memory-reflect", "List or run reflection jobs", "Capability", action="memory.reflections", args_hint="[job_id]"),
    ShellCommandDef("scheduled", "List scheduled automations", "Capability", action="scheduled_tasks.list"),
    ShellCommandDef("extensions", "List extension status", "Capability", action="extensions.list"),
    ShellCommandDef("refresh", "Refresh one extension", "Capability", args_hint="<server_id>", action="extensions.refresh"),
    ShellCommandDef("mcp", "List configured MCP servers", "Capability", action="mcp.servers"),
    ShellCommandDef("plugins", "List installed plugins", "Capability", action="plugins.list"),
    ShellCommandDef("terminal", "Show active terminal backend capabilities", "Terminal", aliases=("term",), action="processes.capabilities"),
    ShellCommandDef("run", "Start a persistent terminal process", "Terminal", args_hint="<command>", action="processes.spawn", stream_output=True, stateful=True),
    ShellCommandDef("subagents", "List subagent tasks for a thread", "Runtime", args_hint="[thread_id]", action="subagents.list"),
    ShellCommandDef("cancel-task", "Cancel one subagent task", "Runtime", args_hint="<task_id>", action="subagents.cancel"),
    ShellCommandDef("processes", "List process sessions for the active thread", "Terminal", aliases=("ps",), action="processes.list"),
    ShellCommandDef("process-log", "Read a process session log", "Terminal", args_hint="<session_id> [cursor]", action="processes.log", stream_output=True),
    ShellCommandDef("tail", "Read the next process log chunk", "Terminal", args_hint="<session_id> [cursor]", action="processes.log", stream_output=True),
    ShellCommandDef("stdin", "Write stdin to an interactive process", "Terminal", args_hint="<session_id> <text>", action="processes.stdin"),
    ShellCommandDef("interrupt", "Send interrupt to a process session", "Terminal", args_hint="<session_id>", action="processes.interrupt", keybinding="Ctrl-C"),
    ShellCommandDef("resize", "Record terminal dimensions for a process session", "Terminal", args_hint="<session_id> <cols> <rows>", action="processes.resize"),
    ShellCommandDef("context", "Show active thread context and prompt snapshot metadata", "Info", action="context.summary"),
    ShellCommandDef("history", "Show shell input history for this profile", "Info", action="shell.history"),
    ShellCommandDef("keys", "Show TUI keybindings", "Info", action="shell.keybindings"),
    ShellCommandDef("profile", "Show active profile information", "Info", action="profile.show"),
    ShellCommandDef("help", "Show available shell commands", "Info", aliases=("h",), action="shell.help"),
    ShellCommandDef("quit", "Exit the TUI shell", "Exit", aliases=("exit", "q"), action="shell.quit"),
)

_LOOKUP = {name: command for command in COMMAND_REGISTRY for name in (command.name, *command.aliases)}


def _validate_registry() -> None:
    seen: dict[str, str] = {}
    for command in COMMAND_REGISTRY:
        names = (command.name, *command.aliases)
        for name in names:
            normalized = name.strip().lower().lstrip("/")
            if not normalized:
                raise ValueError(f"empty shell command name in {command!r}")
            existing = seen.get(normalized)
            if existing is not None:
                raise ValueError(f"duplicate shell command token {normalized!r}: {existing} and {command.name}")
            seen[normalized] = command.name


_validate_registry()


def resolve_command(value: str) -> ShellCommandDef | None:
    return _LOOKUP.get(value.lstrip("/").strip().lower())


def iter_commands(*, scope: str | None = None, query: str | None = None) -> Iterable[ShellCommandDef]:
    normalized_query = (query or "").strip().lower().lstrip("/")
    for command in COMMAND_REGISTRY:
        if not command.matches_scope(scope):
            continue
        if normalized_query and not _command_matches(command, normalized_query):
            continue
        yield command


def command_help_sections(*, scope: str | None = None, query: str | None = None) -> dict[str, list[ShellCommandDef]]:
    sections: dict[str, list[ShellCommandDef]] = {}
    for command in iter_commands(scope=scope, query=query):
        sections.setdefault(command.category, []).append(command)
    return sections


def complete_commands(prefix: str, *, scope: str | None = None, limit: int = 20) -> list[ShellCommandDef]:
    normalized = prefix.strip().lower()
    command_prefix = normalized.startswith("/")
    if normalized.startswith("/"):
        normalized = normalized[1:]
    token = normalized.split(maxsplit=1)[0] if normalized else ""
    matches: list[ShellCommandDef] = []
    for command in iter_commands(scope=scope):
        names = (command.name, *command.aliases)
        if any(name.startswith(token) for name in names):
            matches.append(command)
            continue
        if token and not command_prefix and token in command.description.lower():
            matches.append(command)
    return matches[: max(limit, 0)]


def command_public_dict(command: ShellCommandDef) -> dict[str, object]:
    return {
        "name": command.slash_name,
        "bare_name": command.name,
        "aliases": list(command.slash_aliases),
        "description": command.description,
        "category": command.category,
        "args_hint": command.args_hint,
        "action": command.action,
        "scopes": list(command.scopes),
        "keybinding": command.keybinding,
        "stream_output": command.stream_output,
        "stateful": command.stateful,
        "arguments": [
            {
                "name": argument.name,
                "placeholder": argument.placeholder,
                "required": argument.required,
                "repeatable": argument.repeatable,
                "values": list(argument.values),
            }
            for argument in command.arguments
        ],
    }


def command_catalog_public_dict(*, scope: str | None = None, query: str | None = None) -> dict[str, object]:
    commands = list(iter_commands(scope=scope, query=query))
    return {
        "commands": [command_public_dict(command) for command in commands],
        "groups": _group_counts(commands),
        "default_scope": scope or "all",
        "total": len(commands),
    }


def completion_catalog_public_dict(prefix: str, *, scope: str | None = None, limit: int = 20) -> dict[str, object]:
    commands = complete_commands(prefix, scope=scope, limit=limit)
    return {
        "commands": [command_public_dict(command) for command in commands],
        "groups": _group_counts(commands),
        "default_scope": scope or "all",
        "total": len(commands),
    }


def render_command_catalog_text(*, scope: str | None = None, query: str | None = None) -> str:
    sections = [
        "Anvil CLI: anvil step | anvil model | anvil tools | anvil skills | anvil mcp | anvil memory | anvil config",
        "TUI commands use slash form inside `anvil shell`.",
    ]
    for category, commands in command_help_sections(scope=scope, query=query).items():
        sections.append(category)
        for command in commands:
            alias_text = f" (aliases: {', '.join(command.slash_aliases)})" if command.aliases else ""
            usage = f" {command.args_hint}" if command.args_hint else ""
            keybinding = f" [{command.keybinding}]" if command.keybinding else ""
            sections.append(f"  {command.slash_name}{usage} - {command.description}{alias_text}{keybinding}")
    return "\n".join(sections)


def known_command_tokens(*, scope: str | None = None) -> tuple[str, ...]:
    tokens: list[str] = []
    for command in iter_commands(scope=scope):
        tokens.append(command.slash_name)
        tokens.extend(command.slash_aliases)
    return tuple(tokens)


class ShellCommandCompleter(Completer):
    def __init__(self, *, scope: str | None = "tui") -> None:
        self.scope = scope

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if " " in text:
            yield from self._argument_completions(text)
            return
        for command in complete_commands(text, scope=self.scope):
            replacement = command.slash_name
            yield Completion(
                replacement,
                start_position=-len(text),
                display=replacement,
                display_meta=command.description,
            )

    def _argument_completions(self, text: str):
        command_token, _, arg_prefix = text.partition(" ")
        command = resolve_command(command_token)
        if command is None:
            return
        argument = next((item for item in command.arguments if item.values), None)
        if argument is None:
            return
        normalized_prefix = arg_prefix.strip().lower()
        for value in argument.values:
            if value.startswith(normalized_prefix):
                yield Completion(value, start_position=-len(arg_prefix), display=value, display_meta=argument.placeholder)


class ShellCommandAutoSuggest(AutoSuggest):
    def __init__(self, *, scope: str | None = "tui") -> None:
        self.scope = scope

    def get_suggestion(self, buffer, document: Document) -> Suggestion | None:
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return None
        match = next(iter(complete_commands(text, scope=self.scope, limit=1)), None)
        if match is None:
            return None
        suggestion = match.slash_name
        if suggestion == text or not suggestion.startswith(text):
            return None
        return Suggestion(suggestion[len(text) :])


def _command_matches(command: ShellCommandDef, query: str) -> bool:
    haystack = " ".join(
        [
            command.name,
            *command.aliases,
            command.description,
            command.category,
            command.action,
            command.args_hint,
        ]
    ).lower()
    return query in haystack


def _group_counts(commands: list[ShellCommandDef]) -> dict[str, int]:
    groups: dict[str, int] = {}
    for command in commands:
        groups[command.category] = groups.get(command.category, 0) + 1
    return groups
