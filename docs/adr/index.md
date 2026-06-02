# ADR Index

This index is the quickest way to find Anvil's accepted architecture decision records.

Use ADRs for stable architectural choices that either:

- lock a baseline philosophy
- record a long-term exception
- prevent repeated re-litigation of an important tradeoff

For routine behavior and boundaries, prefer the release-facing guides and the implementation tests.

## Accepted ADRs

| ADR | Status | What it settles |
| --- | --- | --- |
| `ADR-001-architecture-baseline-and-extension-policy.md` | accepted | the project keeps one harness-first baseline and only accepts selective overlays where they clearly improve the system |
| `ADR-002-v1-protocol-deferral-and-upgrade-seam.md` | accepted | v1 is not protocol-first and generated SDK/schema work remains deferred behind an explicit future seam |
| `ADR-003-execution-control-and-capability-truth.md` | accepted | capability truth belongs to runtime contracts and execution control uses typed approval/permission models |

## When To Read Which ADR

- Start with ADR-001 if you are deciding whether a new idea belongs in the current baseline or should be treated as an overlay.
- Read ADR-002 before proposing any protocol package, generated SDK, or wire-first redesign.
- Read ADR-003 before changing approvals, permissions, sandbox policy, delegation, or capability ownership.

## When To Add A New ADR

Add a new ADR only if the decision:

- changes a cross-cutting architecture rule
- introduces a durable exception to the current baseline
- would be expensive to rediscover in a later session

Do not add ADRs for:

- routine doc cleanup
- test-only adjustments
- example content
- simple implementation details already covered by existing architecture docs

