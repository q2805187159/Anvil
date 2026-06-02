# App Adapters

This directory contains Anvil's app-layer adapter surfaces.

The current Phase 7 shape is:

- `backend/app/gateway/`
  - `app.py`
  - `deps.py`
  - `services.py`
  - `routers/`
- `backend/app/sdk/`
  - embedded Python client seam over stable app contracts
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

Phase 7 note:

- `sdk/` preserves gateway-aligned view shapes for in-process callers
- `shell/` remains a wrapper/package boundary and must not become a second runtime control plane
- `/shell/commands` exposes app command metadata only; runtime tools and approvals still come from harness contracts

Release readiness note:

- packaged entrypoints should be the preferred user-facing startup surface
- gateway/shell/smoke/doctor should work both from an installed package and via repo-local scripts

Source of truth:

- `docs/architecture/source-of-truth.md`
- `docs/architecture/10-app-adapters.md`
- `docs/architecture/11-sdk-and-protocol.md`
- `docs/architecture/12-phased-delivery-plan.md`
- `docs/guides/quickstart-and-startup-modes.md`
