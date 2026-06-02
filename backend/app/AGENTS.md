# App Adapter Constraints

This file governs `backend/app/` and all of its children.

## Role

This area contains Anvil's thin app adapters.

It may depend on the harness package. The harness package may not depend on it.

## Hard Rules

- Keep adapters thin: request translation, response translation, lifecycle composition, error translation.
- Do not duplicate harness business logic in routers or services.
- Do not reimplement prompt assembly, path resolution, tool availability, approval logic, or capability truth here.
- Read runtime dependencies from app dependency bundles instead of reconstructing them in handlers.
- Channels or future shells must follow the same thin-adapter rule.

## Source Of Truth

Read these docs before changing this area:

- `docs/guides/usage.md`
- `docs/guides/cli.md`
- `docs/guides/tui.md`
- `docs/guides/configuration.md`
- `docs/adr/ADR-001-architecture-baseline-and-extension-policy.md`
- `docs/adr/ADR-002-v1-protocol-deferral-and-upgrade-seam.md`
- `docs/adr/ADR-003-execution-control-and-capability-truth.md`

## Implementation Bias

- Prefer harness public interfaces over app-local shortcuts.
- Prefer clear view models over leaking runtime internals.
- Prefer dependency bundle access over ad hoc imports and global state.
