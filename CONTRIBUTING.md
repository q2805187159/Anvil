# Contributing

Thank you for helping improve Anvil.

## Development Setup

```bash
make config
make install-backend-dev
make install-frontend
```

Start services:

```bash
make backend
make frontend
```

Or use Docker:

```bash
make docker-start
```

## Verification

Run focused checks before opening a pull request:

```bash
make contracts
make test-backend
make test-frontend
make typecheck
make docs
```

For coverage:

```bash
make test-backend-cov
```

## Engineering Boundaries

- Harness code owns runtime behavior.
- Gateway, shell, SDK, and frontend stay thin.
- Frontend types come from generated contracts.
- Secrets belong in `.env`, never in examples or docs.
- Root `skills/` contains Anvil's bundled starter skills and is part of the
  public release surface.
- User-local skill packs belong in Anvil Home or reviewed external directories,
  not in committed runtime state.
- Large tool outputs and durable memory writes must stay auditable.
- Keep runtime safety defaults conservative.

## Pull Request Checklist

- Explain the user-visible behavior change.
- Add or update tests for changed behavior.
- Regenerate contracts when backend contract models change.
- Update docs when startup, config, memory, tool, or API behavior changes.
- Do not commit local runtime state, IDE files, caches, internal future notes,
  user-local skill packs, debug screenshots, or secrets.
