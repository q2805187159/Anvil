# Frontend Constraints

This file governs `frontend/` and all of its children.

## Role

This area contains the optional Phase 7 frontend operator workbench.

It consumes stable gateway contracts only.

## Hard Rules

- Do not import harness runtime modules, `backend/packages/harness`, or `backend/app` internals.
- Keep baseline separation between typed API wrappers and domain hooks.
- Components may consume hooks; they may not duplicate transport logic.
- Frontend may present runtime truth, but it may not invent capability truth, approval semantics, or path rules.
- Use plain HTTP plus streaming against stable gateway endpoints only.

## Source Of Truth

- `docs/guides/usage.md`
- `docs/guides/configuration.md`
- `docs/guides/release-verification.md`
- `docs/adr/ADR-001-architecture-baseline-and-extension-policy.md`
- `docs/adr/ADR-003-execution-control-and-capability-truth.md`

