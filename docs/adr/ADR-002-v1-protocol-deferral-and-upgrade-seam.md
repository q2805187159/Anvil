# ADR-002: v1 Protocol Deferral and Upgrade Seam

## Context

Anvil needs strong protocol and schema discipline, but Phase 0 intentionally froze the project as a backend-first harness with HTTP and embedded seams rather than a protocol-first product from day one.

The architecture still needs a future-compatible upgrade seam so later protocol adoption does not require structural reversal.

## Decision

Anvil v1 does not introduce a full protocol-first app-server or generated SDK/schema pipeline.

Instead, Anvil freezes a minimal upgrade seam:

- harness runtime types remain inside the harness package
- app adapters expose stable view models
- embedded and HTTP seams stay aligned semantically
- if protocol-first becomes justified later, a dedicated protocol package will be added beside adapters rather than inside the harness runtime

Protocol-first promotion requires an explicit future decision and must satisfy at least one of these triggers:

- three or more independently versioned client surfaces need one stable contract
- a non-Python client family becomes a product requirement
- approval, streaming, and capability events need a richer typed remote transport than HTTP alone
- generated schemas or SDKs become materially cheaper than maintaining several hand-kept seams

## Consequences

- Phase 1 and Phase 2 remain lighter and easier to reason about
- Phase 3-6 can build the harness and HTTP app without carrying protocol infrastructure weight
- embedded parity still matters, because it is the future upgrade bridge if protocol-first is added later
- future protocol adoption remains possible without making it part of the baseline

## Rejected Alternatives

### Alternative: Make protocol-first the default for v1

Rejected because it adds weight before the product requires it and shifts focus away from the reusable harness.

### Alternative: Defer protocol-first with no future seam

Rejected because that would invite a later hard rewrite instead of a controlled promotion path.

### Alternative: Generate schemas directly from HTTP adapter payloads later

Rejected because adapter payloads are not the right long-term source of truth for a stable multi-client protocol.

## Downstream Implications

- `11-sdk-and-protocol.md` is authoritative for the v1 seam and future trigger conditions.
- Later phases may build embedded and HTTP parity tests without introducing protocol-first machinery.
- If a future phase wants protocol-first behavior, it must add a new ADR and introduce a dedicated protocol package with translation boundaries.

