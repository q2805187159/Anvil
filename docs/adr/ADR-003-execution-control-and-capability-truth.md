# ADR-003: Execution Control and Capability Truth

## Context

Agent systems often drift into unsafe and unmaintainable behavior when:

- shells or routers decide what capabilities are "really" available
- prompt text becomes the only enforcement layer for approvals or limits
- network permission is treated as a side effect of shell-command approval
- retry, sandboxing, and policy checks are buried in adapter handlers

Phase 0 and Phase 1 both require Anvil to avoid this drift and keep the harness reusable.

## Decision

Anvil adopts two permanent rules:

1. Capability truth belongs to harness runtime contracts.
2. Sensitive execution follows a typed control plane.

Capability truth is determined by:

- central tool registry
- effective capability bundle
- request-local visibility filters
- typed approval and permission state

It is not determined by:

- shell command menus
- router-local flags
- frontend assumptions
- prompt prose alone

Sensitive execution must follow an explicit control-plane order:

1. capability lookup
2. policy / guardrail evaluation
3. approval classification
4. sandbox selection
5. permission transform or grant application
6. execution
7. denial or failure classification
8. optional approved retry

Approval and permission states are typed:

- `ApprovalDecision`
  - `skip`
  - `needs_user_approval`
  - `forbidden`
- `PermissionGrant`
  - scoped file and network permissions
- `NetworkApprovalDecision`
  - network-specific allow/deny/prompt semantics

Network approval is a distinct surface, not a hidden variant of generic shell approval.

## Consequences

- runtime capability visibility is centralized and testable
- shells, routers, and future frontend surfaces become presentation layers over runtime truth
- approval, sandbox, and retry logic can evolve without moving business logic into adapters
- subagents inherit bounded capabilities through runtime intersection instead of prompt suggestion alone

## Rejected Alternatives

### Alternative: Let routers or shells decide runtime capability truth

Rejected because it creates split-brain behavior and breaks harness reuse.

### Alternative: Enforce approvals only through prompt instructions

Rejected because prompt-only enforcement is insufficient for powerful tools and autonomous workflows.

### Alternative: Treat network access as part of a generic shell-approval boolean

Rejected because network access needs its own scoped permission model and its own auditability.

### Alternative: Put retry and approval sequencing inside one shell handler

Rejected because that makes the control plane adapter-specific and non-reusable.

## Downstream Implications

- Phase 3 contracts must include typed approval and permission concepts.
- Phase 4 runtime backbone must place policy and approval control in middleware/control-plane wiring, not in routers.
- Phase 5 capability and subagent systems must use request-local visibility and typed execution control.
- Any future shell, frontend, or protocol work must consume the runtime control plane rather than redefining it.

