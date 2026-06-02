<p align="center">
  <img src="docs/assets/logo.png" alt="Anvil operator workspace" width="100%">
</p>

<p align="center">
  <a href="./README.md">English</a> |
  <a href="./README_zh.md">中文</a>
</p>

# Anvil

<p align="center">
  <a href="https://github.com/q2805187159/Anvil/actions/workflows/ci.yml"><img src="https://github.com/q2805187159/Anvil/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="./backend/pyproject.toml"><img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="./frontend/package.json"><img src="https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white" alt="Node.js 22+"></a>
  <a href="./docker-compose.yml"><img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker Compose"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
</p>

Anvil is a harness-first agent runtime for building reliable operator
workspaces, agent tools, long-running memory, MCP extensions, isolated
execution surfaces, and self-improving agent workflows from one source of
runtime truth.

It is designed for teams that need more than a chat window: the reusable
harness, FastAPI gateway, embedded SDK, shell/TUI, Next.js workbench, memory
platform, tools catalog, approval control plane, and deployment scripts all
consume the same runtime contracts.

## Why Anvil

Anvil is built like an operating layer for agents, not a thin chat wrapper.
One runtime owns memory, tools, approvals, process execution, context budgets,
delegation, and telemetry, then exposes that same truth through HTTP, SDK, CLI,
TUI, and the browser workbench.

| Advantage | What makes it different |
| --- | --- |
| Harness-owned truth | The real platform lives in `backend/packages/harness/anvil`; the FastAPI gateway, embedded SDK, shell/TUI, and Next.js UI are clients of one contract instead of competing implementations. |
| Memory that can be governed | Session archive, curated runtime memory, profile facets, recall evidence, review queues, conflict resolution, staleness checks, maintenance jobs, and reflection jobs make memory durable without becoming an unreviewed prompt junk drawer. |
| Context engineering built in | Prompt snapshots, context ledgers, JIT context loading, semantic code maps, deferred tool schemas, capability search, output budgets, token usage accounting, semantic compression, and compaction diagnostics keep large workspaces usable. |
| Tool universe with guardrails | Filesystem, terminal/process, web, browser, media, document, Google Workspace, MCP, skills, memory, automation, planning, and delegation surfaces are assembled through typed toolsets with approval and safety metadata. |
| Real operator workbench | Threads, uploads, artifacts, transcript projection, approvals, memory governance, skill governance, plugin inspection, MCP console, model configuration, and tool catalog panels sit in one dense bilingual workspace. |
| Parallel and scheduled work | Bounded subagents, batch delegation records, task dependencies, scheduled tasks, execution history, and follow-up queues let Anvil run more like an operator team than a single chat tab. |
| Multi-environment execution | Local shell, Docker, SSH, Singularity/Apptainer, Modal, Daytona, and Vercel sandbox adapters share process contracts, mount metadata, and capability reporting. |
| Research and training runway | Scrubbed trajectory export, ShareGPT-style conversion, tool-call parsing, quality reports, memory recall benchmarks, contract generation, smoke tests, and release-readiness gates make the runtime measurable instead of mystical. |

## Screenshots

| Workspace | Session Details | Ops Console |
| --- | --- | --- |
| <img src="docs/assets/screenshots/home.png" alt="Workspace home" width="100%"> | <img src="docs/assets/screenshots/session-details.png" alt="Session details" width="100%"> | <img src="docs/assets/screenshots/configuration-center.png" alt="Ops Console" width="100%"> |

## Quick Start

Prerequisites:

- Python `3.12+`
- Node.js `22+`
- Docker Engine with Compose v2 for the recommended full-stack path
- `make` on Linux, macOS, WSL, or Git Bash

```bash
git clone https://github.com/q2805187159/Anvil.git
cd Anvil
make config
```

Edit `.env` for secrets and `config.yaml` for model routing, then start the
full stack:

```bash
make docker-start
```

Default endpoints:

- Frontend: `http://127.0.0.1:13200`
- Backend: `http://127.0.0.1:18000`
- Health: `http://127.0.0.1:18000/health`

Local development:

```bash
make install-backend-dev
make install-frontend
make backend
```

In another terminal:

```bash
make frontend
```

## Core Capabilities

- Agent run engine with structured streaming, durable thread state, transcript
  projection, prompt snapshots, and execution modes.
- Memory platform with session archive, runtime memory, user profile, review
  queues, recall evidence, freshness scoring, conflict handling, profile facets,
  memory health checks, retention, reflection, and maintenance jobs.
- Tools catalog with capability search, schema deferral, output budgeting,
  artifact spillover, filesystem tools, web/media/document/browser tools, and
  process sessions.
- Code intelligence tools for compact project maps, semantic indexes, symbol
  lookup, reference search, impact analysis, security scans, and docs graphs.
- Extension layer for MCP servers, local skills, plugin registries, memory
  providers, model routing, terminal backends, and generated contracts.
- Operator workbench with threads, uploads, artifacts, approvals, Memory
  Workspace, Skills governance, MCP console, Tools Catalog, and bilingual UI.
- CLI and TUI surfaces for setup, one-shot steps, model selection, memory
  search, terminal sessions, approvals, and structured user interactions.
- Evaluation surfaces for trajectory export, batch reports, memory recall
  benchmarks, smoke tests, docs builds, and release-readiness checks.

## Documentation

Start with these guides:

- [Usage Guide](./docs/guides/usage.md)
- [Deployment Guide](./docs/guides/deployment.md)
- [TUI Guide](./docs/guides/tui.md)
- [CLI Reference](./docs/guides/cli.md)
- [Command Reference](./docs/guides/commands.md)
- [Configuration Reference](./docs/guides/configuration.md)
- [Model Provider Configuration](./docs/guides/model-provider-configuration.md)
- [Extensions and Capability Surfaces](./docs/guides/extensions-and-capability-surfaces.md)
- [Open Source Release Checklist](./docs/guides/open-source-release.md)
- [Release Verification](./docs/guides/release-verification.md)

Build the documentation site:

```bash
make install-backend-dev
make docs
```

## Verification

```bash
make contracts
make check-docker-mounts
make test-backend
make test-frontend
make typecheck
make docs
```

Release-facing quick gate:

```bash
make release-readiness
```

## Project Layout

```text
Anvil/
|-- .github/               # CI, CodeQL, templates, Dependabot, CODEOWNERS
|-- backend/               # Gateway, embedded SDK, shell, harness package, tests
|-- docs/                  # Release-facing documentation
|-- docs/assets/           # README-safe visual assets
|-- examples/              # Secret-free examples and plugin fixtures
|-- frontend/              # Next.js operator workbench
|-- plugins/               # Reviewed example plugin packages
|-- scripts/               # Startup, cleanup, contracts, readiness scripts
|-- docker-compose.yml
|-- Makefile
|-- mkdocs.yml
`-- README_zh.md
```

Local runtime state, debug databases, internal planning logs, and unreviewed
local skill packs are intentionally ignored for public releases.

## Security

Anvil can execute tools, read and write files, call MCP servers, process
uploads, manage memory, spawn processes, and delegate work. Treat it as a
trusted-environment system unless you add your own authentication and sandbox
boundary.

Recommended baseline:

- Keep `.env`, `config.yaml`, Anvil Home, runtime state, and generated artifacts out of Git.
- Keep `guardrails.enabled=true`.
- Require approval for shell execution, network access, and filesystem writes in shared environments.
- Review MCP commands and environment variables before enabling them.
- Put public deployments behind authentication, TLS, and network allowlists.

Security reporting instructions are in [SECURITY.md](./SECURITY.md).

## Community

- Issues: https://github.com/q2805187159/Anvil/issues
- Discussions: https://github.com/q2805187159/Anvil/discussions
- Pull requests: https://github.com/q2805187159/Anvil/pulls
- Contributing guide: [CONTRIBUTING.md](./CONTRIBUTING.md)
- Code of conduct: [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)

## License

Anvil is released under the [MIT License](./LICENSE).
