from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from pathlib import PurePosixPath
import shlex

import yaml
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import checkboxlist_dialog, input_dialog, radiolist_dialog
from prompt_toolkit.styles import Style

from anvil.agents import ThreadExecutionMode

from app.sdk import EmbeddedClient, EmbeddedClientConfig, EmbeddedRunRequest

from .commands import ShellCommandAutoSuggest, ShellCommandCompleter, render_command_catalog_text, resolve_command
from .profile import ShellProfile


@dataclass
class ShellSessionState:
    active_profile: str
    current_thread_id: str | None = None
    last_output: str | None = None
    execution_mode: str = ThreadExecutionMode.AGENT.value
    selected_model: str | None = None
    plan_mode: bool = False


class AnvilShell:
    def __init__(
        self,
        *,
        profile: ShellProfile,
        client: EmbeddedClient | None = None,
        client_config: EmbeddedClientConfig | None = None,
    ) -> None:
        self.profile = profile
        self.client = client or EmbeddedClient(client_config)
        self.session = ShellSessionState(active_profile=profile.name)
        self._prompt_session: PromptSession | None = None
        self._style = Style.from_dict(
            {
                "prompt.app": "#5bc0be bold",
                "prompt.meta": "#8aa0b5",
                "prompt.mode": "#cbedf6",
                "toolbar": "#8aa0b5",
                "toolbar.key": "#5bc0be bold",
            }
        )
        self._history_path = self.profile.sessions_dir / "history.txt"
        self._last_result_streamed = False

    def execute_input(self, value: str) -> str:
        self._last_result_streamed = False
        stripped = value.strip()
        if not stripped:
            return ""
        if stripped.startswith("/"):
            output = self._execute_command(stripped)
        else:
            output = self._submit_message(stripped)
        self.session.last_output = output
        self._write_session_snapshot()
        return output

    def render_help(self) -> str:
        return render_command_catalog_text(scope="tui")

    def run_interactive(self) -> None:
        if self._prompt_session is None:
            self._prompt_session = PromptSession(
                history=FileHistory(str(self._history_path)),
                completer=ShellCommandCompleter(scope="tui"),
                auto_suggest=ShellCommandAutoSuggest(scope="tui"),
                complete_while_typing=True,
                enable_history_search=True,
                multiline=True,
                prompt_continuation="... ",
            )
        while True:
            try:
                text = self._prompt_session.prompt(
                    self._prompt_fragments(),
                    bottom_toolbar=self._toolbar_text,
                    style=self._style,
                )
            except KeyboardInterrupt:
                if self.session.current_thread_id is not None:
                    print(self._handle_stop())
                continue
            except EOFError:
                break
            result = self.execute_input(text)
            if result == "__QUIT__":
                break
            if result and not self._last_result_streamed:
                print(result)

    def close(self) -> None:
        self.client.close()

    def _execute_command(self, value: str) -> str:
        command_token, _, raw_args = value.partition(" ")
        raw_command = resolve_command(command_token)
        if raw_command is not None and raw_command.name == "run":
            return self._handle_run(raw_args)

        parts = shlex.split(value)
        if not parts:
            return ""
        command = resolve_command(parts[0])
        if command is None:
            return f"Unknown command: {parts[0]}"

        args = parts[1:]
        if command.name == "new":
            requested_thread_id = args[0] if args else None
            thread = self.client.create_thread(thread_id=requested_thread_id)
            self.session.current_thread_id = thread.thread_id
            return f"Active thread: {thread.thread_id}"
        if command.name == "use":
            if not args:
                return "Usage: /use <thread_id>"
            thread = self.client.get_thread(args[0])
            self.session.current_thread_id = thread.thread_id
            return f"Switched to thread: {thread.thread_id}"
        if command.name == "threads":
            threads = self.client.list_threads()
            if not threads:
                return "No threads yet."
            return "\n".join(f"{thread.thread_id} [{thread.status}]" for thread in threads)
        if command.name == "resume":
            if self.session.current_thread_id is None:
                return "No active thread. Use /new first."
            detail = self.client.get_thread_detail(self.session.current_thread_id)
            self.session.current_thread_id = detail.thread.thread_id
            self.session.execution_mode = detail.state.execution_mode.value
            self.session.selected_model = detail.state.selected_model
            self.session.plan_mode = detail.state.is_plan_mode
            return f"{detail.thread.thread_id} [{detail.thread.status}] messages={len(detail.messages)}"
        if command.name == "state":
            thread_id = args[0] if args else self.session.current_thread_id
            if thread_id is None:
                return "No active thread. Use /new first."
            return self.client.get_thread_state(thread_id).model_dump_json(indent=2)
        if command.name == "mode":
            return self._handle_mode(args)
        if command.name == "model":
            return self._handle_model(args)
        if command.name == "plan":
            return self._handle_plan(args)
        if command.name == "stream":
            return self._submit_message(" ".join(args), stream=True)
        if command.name == "models":
            return "\n".join(model.name for model in self.client.list_models())
        if command.name == "tools":
            query = " ".join(args) if args else None
            items = self.client.list_tool_catalog(query=query)
            if not items:
                return "No tools matched."
            return "\n".join(f"{item.name} [{item.capability_group}] {item.summary}" for item in items)
        if command.name == "skills":
            return "\n".join(skill.skill_id for skill in self.client.list_skills())
        if command.name == "memory":
            overview = self.client.get_memory_overview()
            stores = self.client.list_memory_stores()
            lines = [
                f"Active engine: {overview.active_engine_id or 'none'}",
                f"Archive turns: {overview.archive_turn_count}",
                f"Reflection jobs: {overview.reflection_job_count}",
            ]
            lines.extend(f"- {store.store_id} ({store.entry_count} entries)" for store in stores)
            return "\n".join(lines)
        if command.name == "memory-engine":
            if args:
                engine = next((item for item in self.client.list_memory_engines() if item.engine_id == args[0]), None)
                if engine is None:
                    return f"Unknown engine: {args[0]}"
                return f"{engine.engine_id} [{engine.family}] active={engine.active}"
            engines = self.client.list_memory_engines()
            return "\n".join(f"{item.engine_id} [{item.family}] active={item.active}" for item in engines)
        if command.name == "memory-search":
            if not args:
                return "Usage: /memory-search <query>"
            result = self.client.search_memory_archive(" ".join(args))
            if not result.hits:
                return "No archive hits."
            return "\n".join(f"{hit.thread_id}: {hit.excerpt}" for hit in result.hits)
        if command.name == "memory-reflect":
            if args:
                result = self.client.run_reflection_job(args[0])
                return f"{result.job_id}: {result.status} ({result.entries_written} writes)"
            jobs = self.client.list_reflection_jobs()
            return "\n".join(f"{job.job_id} [{job.template}] enabled={job.enabled}" for job in jobs)
        if command.name == "extensions":
            return "\n".join(f"{item.server_id} [{item.status}]" for item in self.client.list_extensions())
        if command.name == "refresh":
            if not args:
                return "Usage: /refresh <server_id>"
            item = self.client.refresh_extension(args[0])
            return f"Refreshed {item.server_id}: {item.status}"
        if command.name == "mcp":
            return "\n".join(f"{item.server_id} [{item.status}] tools={item.tool_count}" for item in self.client.list_mcp_servers())
        if command.name == "plugins":
            return "\n".join(f"{item.plugin_id} enabled={item.enabled} tools={item.tool_count}" for item in self.client.list_plugins())
        if command.name == "terminal":
            return self._handle_terminal()
        if command.name == "run":
            return self._handle_run(" ".join(args))
        if command.name == "scheduled":
            tasks = self.client.list_scheduled_tasks()
            if not tasks:
                return "No scheduled automations."
            return "\n".join(f"{task.task_id} [{task.status}] next={task.next_run_at}" for task in tasks)
        if command.name == "setup":
            return self._handle_setup(args)
        if command.name == "approve":
            thread_id = args[0] if args else self.session.current_thread_id
            if thread_id is None:
                return "No active thread. Use /new first."
            result = self.client.approve(thread_id)
            return result.assistant_message or result.last_error or result.status
        if command.name == "answer":
            return self._handle_answer(args)
        if command.name == "stop":
            return self._handle_stop()
        if command.name == "subagents":
            thread_id = args[0] if args else self.session.current_thread_id
            if thread_id is None:
                return "No active thread. Use /new first."
            tasks = self.client.list_subagent_tasks(thread_id)
            if not tasks:
                return "No subagent tasks."
            return "\n".join(f"{task.task_id} [{task.status}]" for task in tasks)
        if command.name == "cancel-task":
            if not args:
                return "Usage: /cancel-task <task_id>"
            if self.session.current_thread_id is None:
                return "No active thread. Use /new first."
            task = self.client.cancel_subagent_task(self.session.current_thread_id, args[0])
            return f"Cancelled {task.task_id}: {task.status}"
        if command.name == "processes":
            if self.session.current_thread_id is None:
                return "No active thread. Use /new first."
            sessions = self.client.list_process_sessions(self.session.current_thread_id)
            if not sessions:
                return "No process sessions."
            return "\n".join(f"{item.session_id} [{item.status}] {item.command}" for item in sessions)
        if command.name in {"process-log", "tail"}:
            return self._handle_process_log(args)
        if command.name == "stdin":
            return self._handle_stdin(args)
        if command.name == "interrupt":
            return self._handle_interrupt(args)
        if command.name == "resize":
            return self._handle_resize(args)
        if command.name == "context":
            return self._handle_context()
        if command.name == "history":
            return self._handle_history()
        if command.name == "keys":
            return self._handle_keys()
        if command.name == "profile":
            return f"Profile: {self.profile.name}\nHome: {self.profile.home}"
        if command.name == "help":
            return self.render_help()
        if command.name == "quit":
            return "__QUIT__"
        return f"Unhandled command: /{command.name}"

    def _submit_message(self, message: str, *, stream: bool = False) -> str:
        if not message.strip():
            return "Usage: /stream <message>" if stream else ""
        if self.session.current_thread_id is None:
            thread = self.client.create_thread()
            self.session.current_thread_id = thread.thread_id

        request = EmbeddedRunRequest(
            thread_id=self.session.current_thread_id,
            message=message,
            execution_mode=ThreadExecutionMode(self.session.execution_mode),
            profile=None,
        )
        if stream:
            return self._run_streaming(request)
        result = self.client.run(request)
        if result.status == "awaiting_clarification":
            state = self.client.get_thread_state(self.session.current_thread_id)
            if state.pending_user_interaction is not None:
                return _render_user_interaction_prompt(state.pending_user_interaction)
        return result.assistant_message or result.last_error or result.status

    def _run_streaming(self, request: EmbeddedRunRequest) -> str:
        self._last_result_streamed = True
        lines: list[str] = []
        try:
            for event in self.client.stream(request):
                if event.event in {"step_started", "step_updated"}:
                    title = str(event.data.get("title") or event.data.get("type") or event.event)
                    status = str(event.data.get("status") or "")
                    if title:
                        line = f"{event.event}: {title} {status}".strip()
                        print(line)
                        lines.append(line)
                elif event.event == "step_delta":
                    delta = str(event.data.get("delta") or event.data.get("payload") or "")
                    if delta:
                        print(delta, end="", flush=True)
                        lines.append(delta)
                elif event.event == "run_completed":
                    message = str(event.data.get("assistant_message") or event.data.get("last_error") or event.data.get("status") or "")
                    if message:
                        print(message)
                        lines.append(message)
                elif event.event == "run_failed":
                    message = str(event.data.get("error") or event.data.get("kind") or "run failed")
                    print(message)
                    lines.append(message)
            state = self.client.get_thread_state(request.thread_id)
            if state.pending_user_interaction is not None:
                message = _render_user_interaction_prompt(state.pending_user_interaction)
                print(message)
                lines.append(message)
        except KeyboardInterrupt:
            message = "Stream display interrupted. Use /resume to reload durable state or /stop to interrupt pending approval/process work."
            print(message)
            lines.append(message)
        return "\n".join(item for item in lines if item)

    def _handle_answer(self, args: list[str]) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        state = self.client.get_thread_state(self.session.current_thread_id)
        if state.pending_user_interaction is None:
            return "No pending structured interaction."
        if not args:
            return self._prompt_user_interaction(state.pending_user_interaction)
        try:
            parsed = _parse_interaction_answer_args(args)
        except ValueError as exc:
            return str(exc)
        from app.contracts import UserInteractionResumeRequest

        body = UserInteractionResumeRequest(
            request_id=state.pending_user_interaction.request_id,
            selected_option_ids=parsed["choices"],
            custom_response=parsed["custom"],
            free_text=parsed["free_text"],
            field_responses=parsed["fields"],
        )
        result = self.client.resume_user_interaction(self.session.current_thread_id, body)
        return result.assistant_message or result.last_error or result.status

    def _prompt_user_interaction(self, interaction) -> str:
        try:
            parsed = _collect_user_interaction_with_prompt_toolkit(interaction, style=self._style)
        except KeyboardInterrupt:
            return "Structured interaction cancelled."
        if parsed is None:
            return "Structured interaction cancelled."
        from app.contracts import UserInteractionResumeRequest

        body = UserInteractionResumeRequest(
            request_id=interaction.request_id,
            selected_option_ids=parsed["choices"],
            custom_response=parsed["custom"],
            free_text=parsed["free_text"],
            field_responses=parsed["fields"],
        )
        result = self.client.resume_user_interaction(self.session.current_thread_id, body)
        return result.assistant_message or result.last_error or result.status

    def _handle_mode(self, args: list[str]) -> str:
        if self.session.current_thread_id is None:
            return f"Mode: {self.session.execution_mode}"
        if not args:
            return f"Mode: {self.session.execution_mode}"
        value = args[0].lower()
        try:
            execution_mode = ThreadExecutionMode(value)
        except ValueError:
            return "Usage: /mode [chat|agent|full_access]"
        settings = self.client.update_thread_settings(
            self.session.current_thread_id,
            self._settings_update(execution_mode=execution_mode),
        )
        self.session.execution_mode = settings.execution_mode.value
        return f"Mode: {self.session.execution_mode}"

    def _handle_model(self, args: list[str]) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        if not args:
            settings = self.client.get_thread_settings(self.session.current_thread_id)
            return f"Model: {settings.selected_model or 'default'}"
        model = args[0]
        settings = self.client.update_thread_settings(
            self.session.current_thread_id,
            self._settings_update(selected_model=model),
        )
        self.session.selected_model = settings.selected_model
        return f"Model: {settings.selected_model or 'default'}"

    def _handle_plan(self, args: list[str]) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        if not args:
            settings = self.client.get_thread_settings(self.session.current_thread_id)
            return f"Plan mode: {'on' if settings.is_plan_mode else 'off'}"
        value = args[0].lower()
        if value not in {"on", "off"}:
            return "Usage: /plan [on|off]"
        settings = self.client.update_thread_settings(
            self.session.current_thread_id,
            self._settings_update(is_plan_mode=value == "on"),
        )
        self.session.plan_mode = settings.is_plan_mode
        return f"Plan mode: {'on' if settings.is_plan_mode else 'off'}"

    def _handle_stop(self) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        state = self.client.get_thread_state(self.session.current_thread_id)
        if state.has_pending_approval:
            cancelled = self.client.cancel_approval(self.session.current_thread_id)
            return f"Cancelled pending approval: {cancelled.status}"
        running_processes = [item for item in self.client.list_process_sessions(self.session.current_thread_id) if item.status == "running"]
        if running_processes:
            session = self.client.interrupt_process_session(self.session.current_thread_id, running_processes[-1].session_id)
            return f"Interrupted process {session.session_id}: {session.status}"
        return "No pending approval or running process to stop."

    def _handle_process_log(self, args: list[str]) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        if not args:
            return "Usage: /process-log <session_id> [cursor]"
        cursor = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        log = self.client.read_process_log(self.session.current_thread_id, args[0], cursor=cursor)
        return log.output or f"No new output. next={log.next_offset}"

    def _handle_terminal(self) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        capabilities = self.client.get_process_capabilities(self.session.current_thread_id)
        notes = "\n".join(f"- {note}" for note in capabilities.notes)
        missing_config = ", ".join(capabilities.missing_config)
        missing_exec = ", ".join(capabilities.missing_executables)
        return "\n".join(
            item
            for item in [
                f"Backend: {capabilities.backend_id} ({capabilities.kind})",
                f"Label: {capabilities.label}",
                f"Launch: {capabilities.launch_mode}",
                f"Workspace sync: {capabilities.workspace_sync}",
                f"Remote: {capabilities.remote}",
                f"Isolated: {capabilities.isolated}",
                f"Configured: {capabilities.configured}",
                f"Interactive: {capabilities.interactive}",
                f"Persistent sessions: {capabilities.persistent_sessions}",
                f"PTY: {capabilities.pty}",
                f"Executable: {capabilities.executable}",
                f"Missing config: {missing_config}" if missing_config else "",
                f"Missing executables: {missing_exec}" if missing_exec else "",
                f"Notes:\n{notes}" if notes else "",
            ]
            if item
        )

    def _handle_run(self, command_text: str) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        command_text = command_text.strip()
        if not command_text:
            return "Usage: /run <command>"
        session = self.client.spawn_process_session(self.session.current_thread_id, command=command_text)
        return f"Started {session.session_id} [{session.status}] cwd={session.cwd}\nUse /tail {session.session_id} to read output or /interrupt {session.session_id} to stop."

    def _handle_stdin(self, args: list[str]) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        if len(args) < 2:
            return "Usage: /stdin <session_id> <text>"
        session_id = args[0]
        data = " ".join(args[1:])
        session = self.client.write_process_stdin(self.session.current_thread_id, session_id, data, submit=True)
        return f"Wrote stdin to {session.session_id}: {session.status}"

    def _handle_interrupt(self, args: list[str]) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        if not args:
            return "Usage: /interrupt <session_id>"
        session = self.client.interrupt_process_session(self.session.current_thread_id, args[0])
        return f"Interrupted {session.session_id}: {session.status}"

    def _handle_resize(self, args: list[str]) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        if len(args) != 3 or not args[1].isdigit() or not args[2].isdigit():
            return "Usage: /resize <session_id> <cols> <rows>"
        session = self.client.resize_process_session(
            self.session.current_thread_id,
            args[0],
            columns=int(args[1]),
            rows=int(args[2]),
        )
        return f"Resized {session.session_id}: {session.columns}x{session.rows}"

    def _handle_context(self) -> str:
        if self.session.current_thread_id is None:
            return "No active thread. Use /new first."
        state = self.client.get_thread_state(self.session.current_thread_id)
        workspace_root = next((root for root in state.runtime_path_roots if root.kind == "workspace"), None)
        workspace_label = (
            f"{workspace_root.virtual_path} [{state.workspace_mode}]"
            if workspace_root is not None
            else f"/mnt/user-data/workspace [{state.workspace_mode}]"
        )
        context_file_lines = [
            (
                f"- {self._virtual_context_path(state.thread_id, item.virtual_path, item.relative_path)} applies_to={item.applies_to} "
                f"scope={item.scope}{' truncated' if item.truncated else ''}"
            )
            for item in state.project_context_files
        ] or ["- none"]
        return "\n".join(
            [
                f"Thread: {state.thread_id}",
                f"Prompt snapshot: {state.prompt_snapshot_id or 'none'}",
                f"Prompt hash: {state.prompt_snapshot_hash or 'none'}",
                f"Project context: {state.project_context_fingerprint or 'none'}",
                f"Workspace: {workspace_label}",
                "Runtime roots:",
                *[
                    f"- {root.virtual_path} [{root.kind}]{' -> ' + root.display_root if root.display_root else ''}"
                    for root in state.runtime_path_roots
                ],
                "Context files:",
                *context_file_lines,
                f"Skills: {len(state.enabled_skill_ids)}",
                f"Visible tools: {len(state.visible_tool_names)}",
            ]
        )

    def _handle_setup(self, args: list[str] | None = None) -> str:
        args = args or []
        if args:
            try:
                options = _parse_setup_options(args)
                if options.get("help"):
                    return _render_setup_usage()
                token_env = _clean_env_name(options.get("git_token_env")) or "GITHUB_TOKEN"
                payload = _read_shell_config(self.profile.config_path)
                git_payload = payload.setdefault("git", {})
                if not isinstance(git_payload, dict):
                    return "Setup failed: config key 'git' must be a mapping"
                git_payload["enabled"] = True
                git_payload["required"] = True
                git_payload["provider"] = str(options.get("git_provider") or git_payload.get("provider") or "github").strip().lower()
                git_payload["token_env"] = token_env
                _set_optional_config_value(git_payload, "user_name", options.get("git_user_name"))
                _set_optional_config_value(git_payload, "user_email", options.get("git_user_email"))
                _set_optional_config_value(git_payload, "remote_url", options.get("git_remote_url"))

                dotenv_path = self.profile.config_path.parent / ".env"
                if options.get("git_token"):
                    _upsert_shell_dotenv_value(dotenv_path, token_env, str(options["git_token"]))
                    os.environ[token_env] = str(options["git_token"])

                _write_shell_config(self.profile.config_path, payload)
            except ValueError as exc:
                return f"Setup failed: {exc}\n{_render_setup_usage()}"
            lines = [
                "Git token configured for HCMS version control.",
                f"Config: {self.profile.config_path}",
                f"Token env: {token_env}",
            ]
            if options.get("git_token"):
                lines.append(f"Dotenv: {dotenv_path}")
            lines.append("Open Configuration Center > Basic Configuration to test required and extension items.")
            return "\n".join(lines)
        return "\n".join(
            [
                "First-run setup checklist:",
                "1. Model provider: run anvil setup --provider <provider> --api-key-env <ENV>.",
                "2. Git token: configure GITHUB_TOKEN or run anvil setup --git-token-env GITHUB_TOKEN --git-token <token>.",
                "3. HCMS version control: Git token is required for memory version metadata.",
                "4. Browser: open Configuration Center > Basic Configuration to edit and test each item.",
                "5. TUI: run /setup --git-token-env GITHUB_TOKEN --git-token <token> to save Git base config for this profile.",
            ]
        )

    def _handle_history(self) -> str:
        if not self._history_path.exists():
            return "No shell history yet."
        lines = self._history_path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[-30:]) if lines else "No shell history yet."

    def _virtual_context_path(self, thread_id: str, value: str, relative_path: str | None = None) -> str:
        path_service = self.client.deps.path_service
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

    def _handle_keys(self) -> str:
        return "\n".join(
            [
                "Enter: submit current input",
                "Esc Enter: submit multiline input",
                "Enter: insert newline while composing multiline input",
                "Tab: complete slash commands",
                "Up/Down: history navigation",
                "Ctrl-C: interrupt the shell prompt; use /stop for active runtime work",
            ]
        )

    def _settings_update(self, **kwargs):
        from app.contracts import ThreadSettingsUpdateRequest

        return ThreadSettingsUpdateRequest(**kwargs)

    def _prompt_label(self) -> str:
        thread_label = self.session.current_thread_id or "new"
        return f"anvil {self.profile.name}/{thread_label} {self.session.execution_mode} > "

    def _prompt_fragments(self) -> list[tuple[str, str]]:
        thread_label = self.session.current_thread_id or "new"
        return [
            ("class:prompt.app", "anvil "),
            ("class:prompt.meta", f"{self.profile.name}/{thread_label} "),
            ("class:prompt.mode", f"{self.session.execution_mode} "),
            ("class:prompt.app", "> "),
        ]

    def _toolbar_text(self) -> str:
        thread_label = self.session.current_thread_id or "none"
        model = self.session.selected_model or "default"
        plan = "plan:on" if self.session.plan_mode else "plan:off"
        return [
            ("class:toolbar", " "),
            ("class:toolbar.key", "Tab"),
            ("class:toolbar", " completes /commands  "),
            ("class:toolbar.key", "Ctrl-C"),
            ("class:toolbar", f" interrupt  profile={self.profile.name} thread={thread_label} model={model} {plan} "),
        ]

    def _write_session_snapshot(self) -> None:
        target = self.profile.sessions_dir / "active-session.json"
        payload = asdict(self.session)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_interaction_answer_args(args: list[str]) -> dict[str, object]:
    choices: list[str] = []
    custom_parts: list[str] = []
    free_text_parts: list[str] = []
    fields: list[dict[str, object]] = []
    current: str | None = None
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"--choice", "-c"}:
            index += 1
            if index >= len(args):
                raise ValueError("Usage: /answer --choice <option_id> [--custom <text>] [--free-text <text>]")
            choices.append(args[index])
            current = None
        elif token == "--custom":
            current = "custom"
        elif token == "--free-text":
            current = "free_text"
        elif token == "--field":
            index += 1
            if index >= len(args):
                raise ValueError("Usage: /answer --field stack=vite --field notes:Keep it simple")
            fields.append(_parse_interaction_field_arg(args[index]))
            current = None
        elif current == "custom":
            custom_parts.append(token)
        elif current == "free_text":
            free_text_parts.append(token)
        elif _looks_like_field_response(token):
            fields.append(_parse_interaction_field_arg(token))
        else:
            choices.append(token)
        index += 1
    return {
        "choices": choices,
        "custom": " ".join(custom_parts).strip() or None,
        "free_text": " ".join(free_text_parts).strip() or None,
        "fields": fields,
    }


def _looks_like_field_response(value: str) -> bool:
    return ("=" in value or ":" in value) and not value.startswith("--")


def _parse_interaction_field_arg(raw_value: str) -> dict[str, object]:
    raw = str(raw_value or "").strip()
    separator = ":" if ":" in raw and ("=" not in raw or raw.index(":") < raw.index("=")) else "="
    if separator not in raw:
        raise ValueError("Field responses must use field=value or field:text")
    field_id, value = raw.split(separator, 1)
    field_id = field_id.strip()
    value = value.strip()
    if not field_id:
        raise ValueError("Field id must not be empty")
    if separator == ":":
        return {"field_id": field_id, "free_text": value}
    return {"field_id": field_id, "selected_option_ids": [item.strip() for item in value.split(",") if item.strip()]}


def _collect_user_interaction_with_prompt_toolkit(interaction, *, style: Style) -> dict[str, object] | None:
    fields = list(getattr(interaction, "fields", None) or [])
    if not fields:
        fields = [_legacy_interaction_as_field(interaction)]
    field_responses: list[dict[str, object]] = []
    for field in fields:
        response = _collect_interaction_field_response(interaction, field, style=style)
        if response is None:
            return None
        field_responses.append(response)
    first = field_responses[0] if field_responses else {}
    return {
        "choices": list(first.get("selected_option_ids") or []),
        "custom": first.get("custom_response"),
        "free_text": first.get("free_text"),
        "fields": field_responses if getattr(interaction, "fields", None) else [],
    }


def _legacy_interaction_as_field(interaction) -> object:
    class LegacyField:
        field_id = "response"
        label = getattr(interaction, "question", "Response")
        description = getattr(interaction, "description", None)
        selection_mode = getattr(interaction, "selection_mode", "single")
        options = getattr(interaction, "options", [])
        min_selections = getattr(interaction, "min_selections", 1)
        max_selections = getattr(interaction, "max_selections", 1)
        allow_custom = getattr(interaction, "allow_custom", False)
        custom_label = getattr(interaction, "custom_label", None)
        placeholder = getattr(interaction, "placeholder", None)
        required = getattr(interaction, "required", True)

    return LegacyField()


def _collect_interaction_field_response(interaction, field, *, style: Style) -> dict[str, object] | None:
    title = interaction.title or "Input needed"
    text = _field_dialog_text(interaction, field)
    if field.selection_mode == "text":
        result = input_dialog(
            title=title,
            text=text,
            ok_text="Submit",
            cancel_text="Cancel",
            password=False,
            style=style,
        ).run()
        if result is None:
            return None
        return {"field_id": field.field_id, "free_text": str(result).strip()}
    values = [
        (option.id, _option_dialog_label(option))
        for option in field.options
        if not getattr(option, "disabled", False)
    ]
    if getattr(field, "allow_custom", False):
        values.append(("__custom__", field.custom_label or "Other"))
    if field.selection_mode == "multiple":
        default_values = [option.id for option in field.options if getattr(option, "recommended", False) and not getattr(option, "disabled", False)]
        selected = checkboxlist_dialog(
            title=title,
            text=text,
            values=values,
            default_values=default_values,
            ok_text="Submit",
            cancel_text="Cancel",
            style=style,
        ).run()
        if selected is None:
            return None
        selected_ids = [str(item) for item in selected if item != "__custom__"]
        custom_response = _collect_custom_response(title, field, style=style) if "__custom__" in selected else None
        if "__custom__" in selected and custom_response is None:
            return None
        return {"field_id": field.field_id, "selected_option_ids": selected_ids, "custom_response": custom_response}
    default = next((option.id for option in field.options if getattr(option, "recommended", False) and not getattr(option, "disabled", False)), None)
    if default is None and values:
        default = str(values[0][0])
    selected = radiolist_dialog(
        title=title,
        text=text,
        values=values,
        default=default,
        ok_text="Submit",
        cancel_text="Cancel",
        style=style,
    ).run()
    if selected is None:
        return None
    if selected == "__custom__":
        custom_response = _collect_custom_response(title, field, style=style)
        if custom_response is None:
            return None
        return {"field_id": field.field_id, "custom_response": custom_response}
    return {"field_id": field.field_id, "selected_option_ids": [str(selected)]}


def _collect_custom_response(title: str, field, *, style: Style) -> str | None:
    result = input_dialog(
        title=title,
        text=field.placeholder or field.custom_label or "Custom response",
        ok_text="Submit",
        cancel_text="Cancel",
        style=style,
    ).run()
    if result is None:
        return None
    return str(result).strip()


def _field_dialog_text(interaction, field) -> str:
    parts = [str(getattr(field, "label", "") or interaction.question)]
    if getattr(field, "description", None):
        parts.append(str(field.description))
    if field.selection_mode == "multiple":
        parts.append("Use Up/Down to move, Space to toggle, Enter to submit.")
    elif field.selection_mode == "single":
        parts.append("Use Up/Down to choose, Enter to submit.")
    else:
        parts.append(field.placeholder or "Type your response, then submit.")
    return "\n".join(part for part in parts if part)


def _option_dialog_label(option) -> str:
    label = str(option.label)
    if getattr(option, "recommended", False):
        label = f"{label} (recommended)"
    if getattr(option, "description", None):
        label = f"{label} - {option.description}"
    return label


def _render_user_interaction_prompt(interaction) -> str:
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
        lines.append("Submit: /answer --field stack=vite --field scope=routing,tests --field notes:Keep it simple")
    else:
        lines.append("Submit: /answer --choice <option_id> [--choice <option_id>] [--custom <text>] [--free-text <text>]")
    return "\n".join(lines)


def _parse_setup_options(args: list[str]) -> dict[str, str | bool]:
    flag_map = {
        "--git-token": "git_token",
        "--git-token-env": "git_token_env",
        "--git-provider": "git_provider",
        "--git-user-name": "git_user_name",
        "--git-user-email": "git_user_email",
        "--git-remote-url": "git_remote_url",
    }
    options: dict[str, str | bool] = {}
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"--help", "-h"}:
            options["help"] = True
            index += 1
            continue
        key = flag_map.get(token)
        if key is None:
            raise ValueError(f"unknown setup option {token!r}")
        if index + 1 >= len(args):
            raise ValueError(f"missing value for {token}")
        options[key] = args[index + 1]
        index += 2
    return options


def _render_setup_usage() -> str:
    return "\n".join(
        [
            "Usage: /setup [--git-token-env ENV] [--git-token TOKEN] [--git-provider github]",
            "              [--git-user-name NAME] [--git-user-email EMAIL] [--git-remote-url URL]",
        ]
    )


def _read_shell_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("config root must be a mapping")
    return payload


def _write_shell_config(config_path: Path, payload: dict[str, object]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _clean_env_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if cleaned.startswith("${") and cleaned.endswith("}"):
        cleaned = cleaned[2:-1].strip()
    return cleaned or None


def _set_optional_config_value(payload: dict[str, object], key: str, value: str | bool | None) -> None:
    if value is None:
        return
    cleaned = str(value).strip()
    if cleaned:
        payload[key] = cleaned
    else:
        payload.pop(key, None)


def _upsert_shell_dotenv_value(dotenv_path: Path, key: str, value: str) -> None:
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
        return json.dumps(value, ensure_ascii=False)
    return value
