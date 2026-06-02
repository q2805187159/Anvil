# Changelog

All notable changes to Anvil are tracked here.

The project follows semantic versioning once public releases begin.

## [0.1.0] - 2026-06-02

### Added

- Public release baseline for the harness-first Anvil agent runtime.
- FastAPI gateway, embedded SDK, shell/TUI, and Next.js operator workspace.
- Runtime surfaces for tools, memory, skills, MCP extensions, approvals,
  uploads, artifacts, process sessions, scheduled tasks, and tracing.
- Docker Compose, Makefile workflows, release readiness checks, CI, docs,
  issue templates, pull request template, and security policy.

### Changed

- Release package now excludes local runtime state, debug databases, generated
  planning ledgers, internal future notes, and unreviewed local skill packs.
- Root documentation is split into focused usage, deployment, CLI, TUI,
  command, configuration, and open-source release guides.

### Security

- Local `.env`, `config.yaml`, Anvil Home, runtime SQLite state, and generated
  artifacts are ignored by default.
- Dependency review, Dependabot, and CodeQL configuration are included for
  the public repository.
