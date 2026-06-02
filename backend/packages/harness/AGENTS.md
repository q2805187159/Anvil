# Harness Package Constraints

This file governs `backend/packages/harness/` and all of its children.

## Role

This area contains the reusable Anvil harness core.

It owns runtime contracts, state, path/isolation, sandbox abstraction, tool registry, memory, extensions, subagents, and prompt assembly.

## Hard Rules

- `harness -> app` imports are forbidden.
- Do not import `backend.app`, `app`, or future frontend code from this tree.
- Do not add FastAPI, router, HTTP, shell-menu, or frontend business logic here.
- Runtime capability truth belongs to harness contracts, not shell or adapter metadata.
- Path resolution belongs to the path service, not individual tools.
- Approval, permission, and execution sequencing belong to harness control-plane logic, not adapters.

## Source Of Truth

Read these docs before changing this area:

- `docs/guides/usage.md`
- `docs/guides/configuration.md`
- `docs/guides/release-verification.md`
- `docs/adr/ADR-001-architecture-baseline-and-extension-policy.md`
- `docs/adr/ADR-002-v1-protocol-deferral-and-upgrade-seam.md`
- `docs/adr/ADR-003-execution-control-and-capability-truth.md`

## Implementation Bias

- Prefer reusable harness modules over adapter-local convenience code.
- Prefer explicit contracts over implicit globals.
- Prefer typed state and control-plane data over booleans and ad hoc dicts.
- Prefer small composition roots and explicit middleware ordering.
