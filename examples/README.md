# Examples

These examples are safe starting templates for release users.

They are:

- redacted
- env-driven
- intentionally minimal

They are not the architecture source of truth. For binding rules and decision ownership, see:

- [`docs/architecture/source-of-truth.md`](../docs/architecture/source-of-truth.md)
- [`docs/architecture/extension-model.md`](../docs/architecture/extension-model.md)

## Included Examples

- [`config/openai-compatible.config.yaml`](./config/openai-compatible.config.yaml)
- [`config/minimax-anthropic.config.yaml`](./config/minimax-anthropic.config.yaml)
- [`config/vllm-local.config.yaml`](./config/vllm-local.config.yaml)
- [`tracing/langsmith.env.example`](./tracing/langsmith.env.example)
- [`skills/minimal-operator-skill/SKILL.md`](./skills/minimal-operator-skill/SKILL.md)
- [`plugins/catalog.json`](./plugins/catalog.json)
- [`plugins/demo-operator-plugin`](./plugins/demo-operator-plugin)

Copy these into your local workspace or adapt them into `config.yaml` and `.env` as needed.

The plugin example is a local, offline validation fixture for the Ops Console plugin registry. It is not a bundled official marketplace.
