# ADR-001: Architecture Baseline and Extension Policy

## Context

Anvil needs a stable architecture policy so later work does not drift into a blurry mix of unrelated styles.

Without an explicit policy, the project would gradually accumulate:

- inconsistent runtime ownership
- adapter-heavy business logic
- unstable capability boundaries
- heavier control-plane behavior than the product actually needs

That would break the core goals already frozen for the project:

- reusable harness first
- thin adapters
- strong isolation and persistence contracts
- no `harness -> app` inversion

## Decision

Anvil adopts the following architecture policy:

1. one lightweight harness-first baseline governs the runtime shape
2. runtime/shell enhancements are accepted only where they clearly improve operator experience or safety
3. contract/control-plane enhancements are accepted only where they clearly improve provenance, approvals, or future extensibility

The default answer remains:

- harness/app separation
- composition-root clarity
- middleware-first runtime structure
- thread isolation
- path and persistence contracts
- thin gateway discipline

Selective overlays are allowed for:

- prompt cache stability
- schema hygiene
- shell/profile ergonomics
- typed approval, permission, and control-plane rigor
- future-compatible contract seams when justified

Anvil explicitly rejects an equal-weight "average blend" of multiple outside design philosophies.

## Consequences

- later phases inherit one consistent harness-first runtime structure by default
- optional overlays must be justified at the subsystem level
- architecture reviews can ask whether a change preserves the current baseline before accepting extra weight
- future protocol-heavy or shell-heavy work requires explicit justification instead of quiet drift

## Rejected Alternatives

### Alternative: Equal-weight mix of several architectural styles

Rejected because it creates ambiguous ownership, blurred boundaries, and a heavier system than the current release needs.

### Alternative: Shell/product concerns as the default baseline

Rejected because operator-shell concerns are important, but they should stay downstream of the harness core rather than define it.

### Alternative: Control-plane rigor as the default baseline for everything

Rejected because strong contract/control-plane patterns are valuable, but using them everywhere by default would add unnecessary weight to the v1 baseline.

## Downstream Implications

- governance and skeleton work must preserve the harness-first shape
- contracts and runtime backbone should default to the current baseline unless a later doc explicitly calls out a justified overlay
- any future proposal that changes the project's default answer for a core subsystem should add a new ADR
