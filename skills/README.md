# Repo-Local Skills

Anvil treats `skills/` as the repo-local skill seam.

Phase 9 keeps this directory documentation-first:

- it explains where repo-local skills belong
- it does not ship opinionated runtime behavior as part of release polish

If you want a safe example to start from, copy and adapt:

- [`examples/skills/minimal-operator-skill/SKILL.md`](../examples/skills/minimal-operator-skill/SKILL.md)

## Rules

- skills are inputs to the harness skills subsystem
- skills do not bypass runtime capability assembly
- callable tools contributed by future skills must still enter through the central registry and capability bundle path

For the architecture view, see:

- [`docs/architecture/extension-model.md`](../docs/architecture/extension-model.md)
- [`docs/architecture/source-of-truth.md`](../docs/architecture/source-of-truth.md)
