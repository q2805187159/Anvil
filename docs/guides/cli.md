# CLI Reference

The `anvil` command is installed by the backend package.

```bash
cd backend
python -m pip install -e ".[observability,test,docs]"
anvil --help
```

Global options:

| Option | Description |
| --- | --- |
| `--profile <name>` | Select an Anvil profile. |
| `--anvil-home <path>` | Override the Anvil Home/profile state root. |
| `--config <path>` | Use a specific `config.yaml`. |

## `anvil setup`

Initialize or update local configuration.

```bash
anvil setup --provider openai --model gpt-5.4 --api-key-env OPENAI_API_KEY
anvil setup --provider minimax --api-key-env MINIMAX_API_KEY
anvil setup --non-interactive --force
```

Important options:

| Option | Description |
| --- | --- |
| `--provider` | Provider preset name. |
| `--model` | Provider model name. |
| `--api-key` | Secret value to write into `.env`. Use carefully. |
| `--api-key-env` | Environment variable used by config. |
| `--base-url` | OpenAI-compatible base URL override. |
| `--non-interactive` | Do not prompt for missing values. |
| `--force` | Replace existing config with a minimal config first. |

## `anvil step`

Run one agent step from the CLI.

```bash
anvil step "Summarize this repository."
anvil step --thread release-thread --stream "Run a release checklist."
anvil step --mode agent "Inspect available tools."
```

Options:

| Option | Description |
| --- | --- |
| `--thread` | Use or create a specific thread id. |
| `--mode` | `chat`, `agent`, or `full_access`. |
| `--model` | Select a model for this thread. |
| `--stream` | Print structured streaming output. |
| `--choice` | Answer a pending structured interaction option. |
| `--custom` | Submit a custom interaction response. |
| `--free-text` | Submit free text for an interaction. |
| `--field` | Submit multi-field interaction responses. |
| `--interactive` | Open the keyboard-driven interaction selector. |

## `anvil model`

Manage model providers.

```bash
anvil model list
anvil model show openai
anvil model use minimax --thread release-thread
anvil model add local --provider openai --model local-model --base-url http://127.0.0.1:8000/v1
anvil model delete local
```

## `anvil tools`

Inspect tool catalog entries.

```bash
anvil tools list
anvil tools list filesystem
anvil tools show read_file
```

## `anvil skills`

Inspect discovered skills.

```bash
anvil skills list
anvil skills show release-readiness
anvil skills content release-readiness
anvil skills files release-readiness
```

## `anvil mcp`

Inspect MCP configuration and exposed surfaces.

```bash
anvil mcp list
anvil mcp config
anvil mcp tools filesystem
anvil mcp resources filesystem
anvil mcp prompts prompts.chat
anvil mcp provenance github
```

## `anvil plugins`

```bash
anvil plugins list
```

## `anvil memory`

```bash
anvil memory overview
anvil memory stores
anvil memory providers
anvil memory search "release checklist" --limit 5
anvil memory reflections
```

## `anvil context`

```bash
anvil context show --thread release-thread
```

Shows runtime path roots, context files, prompt snapshot identity, enabled
skills, and visible tool count.

## `anvil scheduled`

```bash
anvil scheduled list
anvil scheduled executions --limit 20
```

## `anvil config`

```bash
anvil config path
anvil config roots
anvil config show
anvil config set guardrails.enabled true
anvil config check
```

## `anvil shell`

Starts the interactive TUI shell. See [TUI Guide](./tui.md).
