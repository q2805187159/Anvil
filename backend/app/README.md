# App Adapters

This directory contains Anvil's app-layer adapter surfaces.

The current release shape is:

- `backend/app/gateway/`
  - `app.py`
  - `deps.py`
  - `services.py`
  - `routers/`
- `backend/app/sdk/`
  - embedded Python client over stable app contracts
- `backend/app/shell/`
  - TUI-first shell wrapper over the embedded client
  - central slash command registry for help/autocomplete/gateway command discovery
- `backend/app/gateway/main.py`
  - packaged gateway launcher
- `backend/app/doctor.py`
  - release readiness diagnostics
- `backend/app/smoke.py`
  - local smoke verification

This layer is intentionally thin.

It owns:

- request translation
- response translation
- lifecycle composition
- error translation

It does not own:

- runtime capability truth
- prompt assembly
- sandbox/path rules
- approval and permission policy
- storage business logic
- frontend state truth

Release readiness note:

- packaged entrypoints should be the preferred user-facing startup surface
- gateway/shell/smoke/doctor should work both from an installed package and via repo-local scripts
- `/shell/commands` exposes app command metadata only; runtime tools and approvals still come from harness contracts

Source of truth:

- `README.md`
- `docs/guides/usage.md`
- `docs/guides/cli.md`
- `docs/guides/tui.md`
- `docs/guides/configuration.md`
- `docs/guides/release-verification.md`
- `docs/adr/ADR-001-architecture-baseline-and-extension-policy.md`
- `docs/adr/ADR-003-execution-control-and-capability-truth.md`
