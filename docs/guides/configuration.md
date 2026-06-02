# Configuration Reference

Anvil uses two local files:

- `.env` stores secrets and machine-specific values.
- `config.yaml` stores model routing, runtime behavior, tools, memory,
  guardrails, terminal backends, MCP servers, and extensions.

Create both from examples:

```bash
make config
```

Do not commit `.env` or `config.yaml`.

## Environment Variables

Common `.env` values:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI or OpenAI-compatible provider secret. |
| `OPENAI_BASE_URL` | OpenAI-compatible base URL. |
| `ANTHROPIC_API_KEY` | Anthropic-compatible provider secret. |
| `MINIMAX_API_KEY` | MiniMax provider secret. |
| `GITHUB_TOKEN` | Optional GitHub MCP token. |
| `TAVILY_API_KEY` | Optional web search provider. |
| `EXA_API_KEY` | Optional web search provider. |
| `FIRECRAWL_API_KEY` | Optional fetch/crawl provider. |
| `NEXT_PUBLIC_ANVIL_GATEWAY_URL` | Browser-visible backend gateway URL. |
| `LANGSMITH_API_KEY` | Optional tracing secret. |
| `BROWSER_CDP_URL` | Optional browser automation CDP endpoint. |
| `GOOGLE_ACCESS_TOKEN` | Optional Google Workspace OAuth token. |

Use `replace-me` only in examples. Real values belong in `.env` or deployment
secret stores.

## Top-Level Runtime Fields

| Field | Purpose |
| --- | --- |
| `default_model` / `llm.default` | Default model provider used by runs. |
| `models` / `llm.providers` | Model provider definitions and catalogs. |
| `sandbox_mode` | Filesystem/process isolation mode. |
| `workspace` | Runtime workspace roots and path bridge behavior. |
| `context_files` | Project instruction file discovery settings. |
| `summarization` | Transcript and compaction summary behavior. |
| `scheduled_tasks` | Generic scheduled automation runtime settings. |
| `loop_detection` | Repeated-turn loop guard settings. |

## Model Providers

Provider entries declare:

| Field | Purpose |
| --- | --- |
| `display_name` | Human-readable name. |
| `use` | Model adapter import path. |
| `model` / `default_model` | Model name. |
| `models` | Allowed model catalog. |
| `base_url` | Provider endpoint. |
| `api_key` | Secret env reference, for example `${OPENAI_API_KEY}`. |
| `max_tokens` | Per-response output cap. |
| `context_window_tokens` | Provider context window estimate. |
| `auto_compact_threshold_tokens` | Threshold for compaction pressure. |
| `temperature` / `top_p` | Sampling controls when supported. |

See [Model Provider Configuration](./model-provider-configuration.md).

## Memory Fields

| Field | Purpose |
| --- | --- |
| `memory.enabled` | Enables legacy memory prefetch. |
| `memory.injection_token_budget` | Token budget for memory injection. |
| `memory_platform.enabled` | Enables the structured memory platform. |
| `memory_platform.stores` | Runtime memory and user profile store budgets. |
| `memory_platform.archive` | Session archive SQLite and FTS settings. |
| `memory_platform.providers` | Memory provider catalog and active provider. |
| `memory_platform.recall` | Candidate, evidence, rerank, and token budgets. |
| `memory_platform.review` | Review and auto-accept thresholds. |
| `memory_platform.maintenance` | Bounded automatic maintenance policy. |
| `memory_platform.onboarding` | Workspace bootstrap extraction policy. |

## Tool Budget Fields

| Field | Purpose |
| --- | --- |
| `tool_output_budget.enabled` | Enables output truncation and artifact spillover. |
| `tool_output_budget.default_token_budget` | Default model-visible output token budget. |
| `tool_output_budget.hard_token_budget` | Hard upper bound for tool output. |
| `tool_output_budget.raw_failure_artifacts` | Preserve raw failure output as artifacts. |
| `tool_visibility_budget.enabled` | Enables visible schema budget enforcement. |
| `tool_visibility_budget.visible_schema_token_budget` | Visible tool schema token cap. |
| `tool_visibility_budget.action_prefilter_enabled` | Enables task-aware prefilter for large catalogs. |

## Skills

| Field | Purpose |
| --- | --- |
| `skills_config.enabled` | Enables skill discovery. |
| `skills_config.watch_enabled` | Includes file stamps in discovery cache keys. |
| `skills_config.external_dirs` | Extra read-only skill scan roots. |
| `skills_config.disabled_ids` | Skill ids to disable. |
| `skills_config.governance_root` | Optional governance state root. |
| `skills_config.curator` | Skill review, merge, archive, and procedure promotion policy. |

Public releases exclude unreviewed local `skills/` packs. Install local skills
into Anvil Home or configure reviewed external directories.

## Guardrails and Approvals

| Field | Purpose |
| --- | --- |
| `guardrails.enabled` | Enables runtime guardrail checks. |
| `guardrails.provider` | Guardrail provider id. |
| `guardrails.require_network_approval` | Separates network approval from shell approval. |
| `guardrails.fail_closed` | Fail closed when approval classification cannot complete. |
| `guardrails.default_approval_mode` | Default approval behavior. |
| `guardrails.tool_policies` | Per-capability approval and sandbox policy. |

## Terminal Backends

| Field | Purpose |
| --- | --- |
| `terminal.active_backend` | Active terminal backend id. |
| `terminal.backends.local` | Local shell configuration. |
| `terminal.backends.docker` | Docker-backed shell configuration. |
| `terminal.backends.ssh` | SSH-backed shell configuration. |
| `terminal.backends.singularity` | Singularity-backed shell configuration. |
| `terminal.backends.modal` | Modal-backed shell configuration. |
| `terminal.backends.daytona` | Daytona workspace configuration. |
| `terminal.backends.vercel` | Vercel sandbox configuration. |

Backends can define `env_passthrough`, `env_prefix_passthrough`, resource
limits, mounts, and workspace sync behavior.

## MCP Servers

| Field | Purpose |
| --- | --- |
| `mcp_servers.<id>.enabled` | Enable or disable a server. |
| `mcp_servers.<id>.type` | `stdio` or `http`. |
| `mcp_servers.<id>.command` | Stdio command. |
| `mcp_servers.<id>.args` | Stdio arguments. |
| `mcp_servers.<id>.env` | Environment variables for the server. |
| `mcp_servers.<id>.url` | HTTP server URL. |
| `mcp_servers.<id>.description` | Operator-facing description. |

## Web, Browser, Google, and Media Tools

| Section | Purpose |
| --- | --- |
| `web_tools` | Search, fetch, crawl, image providers, limits, API keys. |
| `browser_tools` | CDP provider, private URL policy, snapshot limits, mock pages. |
| `google_workspace` | Gmail and Calendar provider settings. |
| `media_tools` | TTS/STT providers, limits, model names, and provider secrets. |

## Extensions

| Field | Purpose |
| --- | --- |
| `extensions.skills` | Reserved skill extension config. |
| `extensions.plugins` | Local plugin package config. |
| `extensions.plugins.<id>.source_path` | Plugin source path. |
| `extensions.plugins.<id>.memory_providers` | Memory providers contributed by a plugin. |
| `extensions.plugins.<id>.inline_tools` | Inline tool descriptors. |
| `extensions.plugins.<id>.resources` | Plugin resources. |
| `extensions.plugins.<id>.prompts` | Plugin prompts. |

## Config Freshness and Tracing

| Field | Purpose |
| --- | --- |
| `config_freshness.mtime_watch_enabled` | Watch config file mtimes. |
| `config_freshness.watch_interval_seconds` | Watch interval. |
| `tracing.enabled` | Enables tracing bridge. |
| `tracing.provider` | Tracing provider id. |
| `tracing.project` | Tracing project name. |
| `tracing.endpoint` | Tracing endpoint. |
| `tracing.api_key` | Tracing secret env reference. |
