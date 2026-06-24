# TUI Guide

The Anvil shell is a keyboard-first operator surface over the embedded SDK.
It is useful when you want local agent runs without opening the browser
workbench.

Start it with:

```bash
make shell
```

Or directly:

```bash
anvil shell
```

## Keyboard Basics

| Key | Action |
| --- | --- |
| `Tab` | Complete slash commands. |
| `Up` / `Down` | Navigate shell history. |
| `Enter` | Submit current input. |
| `Esc` then `Enter` | Submit multiline input. |
| `Ctrl-C` | Interrupt the shell prompt. Use `/stop` for active runtime work. |

## Thread Commands

| Command | Purpose |
| --- | --- |
| `/new` | Create a new thread. |
| `/threads` | List known threads. |
| `/thread <id>` | Switch to an existing thread. |
| `/history` | Show recent shell input history. |
| `/context` | Show runtime path roots, context files, prompt snapshot, and visible tools. |

## Execution Commands

| Command | Purpose |
| --- | --- |
| `/mode [chat|agent|full_access]` | Show or change execution mode. |
| `/model [name]` | Show or set the thread model. |
| `/plan [on|off]` | Toggle plan mode for the current thread. |
| `/stop` | Cancel a pending approval or interrupt the latest running process. |

## Structured Interaction Commands

| Command | Purpose |
| --- | --- |
| `/answer` | Open the interactive selector for pending user input. |
| `/answer --choice <id>` | Submit an option response. |
| `/answer --custom <text>` | Submit a custom response. |
| `/answer --free-text <text>` | Submit free text. |
| `/answer --field key=value` | Submit field responses for multi-field input. |

## Terminal Commands

| Command | Purpose |
| --- | --- |
| `/terminal` | Show active terminal backend capabilities. |
| `/run <command>` | Start a process session in the active thread. |
| `/tail <session_id>` | Read process output. |
| `/stdin <session_id> <text>` | Write stdin to a process session. |
| `/interrupt <session_id>` | Stop a running process. |
| `/resize <session_id> <cols> <rows>` | Resize a PTY session. |

## Discovery Commands

| Command | Purpose |
| --- | --- |
| `/tools [query]` | List tool catalog entries. |
| `/tool <name>` | Show one tool. |
| `/skills [query]` | List skills. |
| `/skill <id>` | Show one skill. |
| `/mcp` | List MCP servers. |
| `/memory` | Show memory overview. |
| `/memory-search <query>` | Search archived memory turns. |
| `/setup` | Show the first-run setup checklist, including required HCMS Git token configuration. |
| `/setup --git-token-env GITHUB_TOKEN --git-token <token>` | Save required Git base configuration for the active profile without echoing the token. |

The shell is intentionally not a separate runtime. It presents harness-owned
state through the embedded SDK.
