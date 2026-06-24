# Minimal Operator Skill

Use this example as a documentation-only starting point for a repo-local skill.

## Purpose

Summarize the current thread state and list the next operator action in plain language.

## Guidance

- Do not claim capability truth that the runtime has not exposed.
- Prefer reading current thread state, visible tools, and pending approval status before suggesting action.
- If approval is pending, explain that the operator should resolve approval before continuing tool-heavy work.

## References

- `docs/guides/commands.md`
- `docs/guides/extensions-and-capability-surfaces.md`
- `docs/guides/plugins.md`
