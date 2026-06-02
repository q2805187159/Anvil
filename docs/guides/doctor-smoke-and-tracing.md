# Doctor, Smoke, and Tracing

This guide covers the release-facing diagnostics and smoke surfaces.

## Doctor

Doctor is the first stop when the project is not starting cleanly.

Installed entrypoint:

```bash
anvil-doctor --config ./config.yaml
```

Module form from `backend/`:

```bash
python -m app.doctor --config ../config.yaml
```

Doctor reports:

- Python and backend importability
- Node/npm presence for frontend work
- resolved config path
- default and available models
- missing env-backed model secrets
- LangSmith enablement and installability
- shell home path

## Local Smoke

Local smoke verifies the gateway and embedded client path with a fake model.

Installed entrypoint:

```bash
anvil-smoke local --config ./config.yaml
```

Module form from `backend/`:

```bash
python -m app.smoke local --config ../config.yaml --report-dir ../.omx/reports/local-smoke
```

Use this before any paid-provider smoke.

When `--report-dir` is set, smoke writes an evaluation JSON and Markdown report for the generated smoke thread. The report is built from durable runtime state through the existing evaluation-report contract and includes runtime timings, prompt/cache/context diagnostics, tool calls, skills, memory injection, risks, and recommendations.

## Provider Smoke

Provider smoke hits a real configured model using the same harness-owned config path.

```bash
anvil-smoke provider --config ./config.yaml --model <model-key> --message "Reply with OK only." --report-dir ./.omx/reports/provider-smoke
```

Notes:

- this is a manual/pre-release verification path
- the selected model must already exist in `config.yaml`
- use the configured model key reported by `anvil-doctor` / `app.doctor`, not the provider kind name unless they are intentionally the same
- env-backed secrets must be present in `.env` or the current shell
- every real provider smoke should keep the generated evaluation JSON/Markdown artifacts; do not rely only on the short OK/FAIL console summary

## LangSmith Tracing

LangSmith is optional.

Anvil accepts either LangSmith-native names or LangChain-compatible aliases:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=replace-me
LANGSMITH_PROJECT=anvil
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

or:

```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=replace-me
LANGCHAIN_PROJECT=anvil
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

Tracing smoke:

```bash
anvil-smoke provider --config ./config.yaml --model <model-key> --expect-trace --message "Reply with OK only." --report-dir ./.omx/reports/provider-tracing-smoke
```

The `--expect-trace` flag verifies that the runtime built an active tracing service. Manual release verification should additionally confirm the run appears in LangSmith.

## Recommended Verification Order

1. `anvil-doctor --config ./config.yaml`
2. `anvil-smoke local --config ./config.yaml --report-dir ./.omx/reports/local-smoke`
3. `anvil-smoke provider --config ./config.yaml --model <name> --message "Reply with OK only." --report-dir ./.omx/reports/provider-smoke`
4. optional tracing smoke with `--expect-trace`

## Common Failure Cases

- missing secret env
  - doctor should flag the exact missing variable
- missing `langsmith` package with tracing enabled
  - doctor flags this before a run
- unknown model name in smoke
  - smoke fails before execution rather than guessing a fallback

