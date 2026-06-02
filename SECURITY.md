# Security Policy

Anvil includes high-privilege agent capabilities such as file operations, process execution, MCP commands, uploads, memory writes, and delegated work.

## Supported Versions

The `main` branch is the active development branch until the project starts publishing versioned releases.

## Reporting a Vulnerability

Please avoid posting exploitable security details in public issues. Open a private security advisory on GitHub when available, or contact the maintainer through a trusted private channel.

Include:

- affected commit or version
- deployment mode
- reproduction steps
- impact
- suggested mitigation, if known

## Deployment Guidance

- Do not expose the gateway directly to the public internet without an auth proxy.
- Keep `.env`, `config.yaml`, Anvil Home, runtime state, unreviewed local skill
  packs, and generated artifacts out of Git.
- Use network allowlists and TLS for remote deployments.
- Keep `guardrails.enabled=true`.
- Require approval for shell execution, network access, and filesystem writes in shared environments.
- Review MCP server commands and environment variables before enabling them.
