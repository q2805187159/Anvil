# Configuration Reference

Anvil uses two local files:

- `.env` stores secrets and machine-specific values.
- `config.yaml` stores model routing, runtime behavior, tools, HCMS memory,
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
| `GITHUB_TOKEN` | Required default Git token for HCMS Git-like memory version control; also usable by GitHub MCP. |
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
| `git` | Required Git provider/token settings used by HCMS memory version metadata. |

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
| `hcms.enabled` | Enables the structured HCMS memory control plane. |
| `hcms.storage_backend` | HCMS storage backend: `hybrid` by default, or `filesystem` for JSON state storage. |
| `hcms.stores` | HCMS workspace and user layer budgets. |
| `hcms.archive` | Session archive SQLite and FTS settings. |
| `hcms.engines` | HCMS engine catalog and active engine. |
| `hcms.recall` | Candidate, evidence, stream weights, RRF, adaptive weights, cache, MMR diversity, rerank, and token budgets. |
| `hcms.update_queue` | Adaptive capture debounce windows, turn batch bounds, and queue enablement. |
| `hcms.updater` | HCMS memory update mode, confidence thresholds, provider budgets, timeout, and fail-open behavior. |
| `hcms.quality` | Review and auto-accept thresholds. |
| `hcms.maintenance` | Bounded automatic maintenance policy. |
| `hcms.onboarding` | Workspace bootstrap extraction policy. |

`memory_platform` is a removed legacy configuration surface. The config
resolver drops that key during normalization; use `hcms` for all memory
runtime settings.

`hcms.updater.mode` defaults to `heuristic`, which compiles observations through
the deterministic zero-LLM path. `rule_based` applies structured rule-extracted
`MemoryUpdatePlan` updates directly. `structured` enables the structured JSON
update planner contract and falls back to the rule-based path when no valid
provider response is available and `fail_open=true`. When
`hcms.updater.model_name` is set, the agent factory composition root builds that
configured internal task model as the structured update provider; the reusable
memory package still consumes only the provider callback and does not import
agent or gateway adapters.

`hcms.recall.injection_mode` defaults to `context_v2`, so runtime recall enters
Runtime Context V2 as budgeted `ContextBlock` candidates with assembly trace
diagnostics. Legacy aliases such as `legacy_prompt_append` remain accepted as
compatibility inputs, but runtime recall is migrated into Context V2 blocks and
direct prompt append is retired.

## Git Fields

HCMS memory version control requires a Git token. By default Anvil reads
`git.token_env=GITHUB_TOKEN`; the token value belongs in `.env`.

| Field | Purpose |
| --- | --- |
| `git.enabled` | Enables Git configuration for HCMS version metadata. |
| `git.required` | Marks the Git token as a required base configuration item. |
| `git.provider` | Git provider id, `github` by default. |
| `git.token_env` | Environment variable that stores the Git token. |
| `git.user_name` | Optional author name for local HCMS version metadata. |
| `git.user_email` | Optional author email for local HCMS version metadata. |
| `git.remote_url` | Optional remote repository URL for operator metadata. |

The browser Configuration Center exposes **Basic Configuration** for editing
these values and testing each required or extension item.

First-run CLI and TUI setup both write the same required Git base
configuration. Use `anvil setup --git-token-env GITHUB_TOKEN --git-token
<token>` from scripts, or `/setup --git-token-env GITHUB_TOKEN --git-token
<token>` in the TUI for the active profile. The token is stored in `.env`; the
YAML file stores only `git.token_env` and optional author/remote metadata.

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

The root `skills/` directory contains Anvil's bundled starter skills and is
published with the repository. Install user-local skills into Anvil Home or
configure reviewed external directories.

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
