# Release Verification

Use this checklist before treating the repository as publish-ready.

## Unified Readiness Runner

Use the release readiness runner when you need a repeatable gate instead of a manual checklist:

```bash
python scripts/run-release-readiness.py --profile quick
python scripts/run-release-readiness.py --profile full
python scripts/run-release-readiness.py --profile full --dry-run --json
```

Makefile shortcuts:

```bash
make release-readiness
make release-readiness-full
```

The quick profile runs Docker mount safety, contract drift check, release-facing backend smoke/packaging tests, focused HCMS V2 / Runtime Context V2 backend tests, evaluation/trace replay report checks, deterministic HCMS recall/latency/fallback benchmark gates, frontend package-script preflight, `npm test`, frontend typecheck, and local smoke. The full profile replaces backend smoke with deterministic backend full-suite shards, runs the same HCMS benchmark gate with the full iteration count, and adds frontend build plus docs build. The full-profile `docs-build` stage uses `scripts/build-release-docs.py`, which builds MkDocs into a per-run `.omx/release-docs/docs-<timestamp>-<pid>` directory so release evidence does not depend on deleting an older local `site/` tree.

For focused verification, select stages explicitly:

```bash
python scripts/run-release-readiness.py --stage contracts --stage local-smoke
python scripts/run-release-readiness.py --profile full --stage backend-full --json
python scripts/run-release-readiness.py --profile full --stage backend-full-3 --json
```

Use `--stage-timeout-seconds` when diagnosing long-running gates. A timed-out stage exits with a machine-readable `timed_out` result instead of leaving callers waiting indefinitely. The default stage timeout is the release evidence timeout; reducing it is useful for diagnostics but is not a substitute for a passing full profile. `backend-full` is a compatibility selector that expands to all backend shards (`backend-full-1` through `backend-full-16`); use a numbered shard id to rerun or isolate one backend shard. The full-suite backend gate uses 16 deterministic shards so stream/runtime-heavy modules remain below the default release timeout without weakening the timeout gate.

The `hcms-benchmark` stage runs `scripts/run-hcms-benchmark-report.py` as a deterministic release regression gate. It fails release readiness when recall@10 drops below `0.85`, warm cached p95 latency reaches `200ms`, degraded retrieval does not fail open, or the semantic-negative probe scores above `0.05`. The generated report is a medium-fixture gate for release regressions, not a production-scale corpus or soak-test substitute.

## Backend Verification

Makefile path:

```bash
make test-backend
make test-backend-cov
```

From `backend/`:

```bash
python -m pytest -q
python -m app.doctor --config ../config.yaml
python -m app.smoke local --config ../config.yaml
```

If you installed the backend package, equivalent entrypoints are:

```bash
anvil-doctor --config ./config.yaml
anvil-smoke local --config ./config.yaml
```

The repository wrapper keeps backend tests on a stable temp path and supports deterministic full-suite sharding from the repository root:

```bash
python scripts/run-backend-tests.py --backend-shard-index 1 --backend-shard-count 16 -q
python scripts/run-backend-tests.py --backend-shard-index 1 --backend-shard-count 16 --collect-only -q
```

## Frontend Verification

Makefile path:

```bash
make test-frontend
make typecheck
make build-frontend
```

From `frontend/`:

```bash
node scripts/frontend-process-preflight.cjs
npm test
npm run typecheck
npm run build
```

The process preflight verifies the release frontend test contract: `package.json` must route tests through `scripts/vitest-node-pipe-shim.cjs` and Vitest native config loading, and required Next/TypeScript/Vitest packages must resolve. Some locked-down Windows runners block nested Node `child_process` spawn from inside Node; in that case the preflight emits a warning and the authoritative verification remains `npm test`, `npm run typecheck`, and `npm run build`. A failure in those stages still blocks release readiness.

## Docker Workspace Verification

Static mount-safety check:

```bash
make check-docker-mounts
python scripts/check-docker-mount-safety.py
```

The script checks the repository's existing base and override Compose files by default.

Makefile path:

```bash
make docker-start
make docker-status
```

Windows PowerShell:

```powershell
.\scripts\start-docker.ps1
.\scripts\status-docker.ps1
```

Linux, macOS, or WSL:

```bash
./scripts/start-docker.sh
./scripts/status-docker.sh
```

Confirm:

- compose services are up
- `http://127.0.0.1:13200` opens the operator workspace
- `http://127.0.0.1:13200/threads/<thread-id>` opens a deep-linked thread workspace
- `http://127.0.0.1:18000/health` returns `ok`
- the workspace can create a thread and render transcript / memory / skills / approvals / ops panels
- long thread-card titles and previews stay clipped instead of stretching the sidebar
- assistant responses fill the main transcript column and message copy/edit affordances stay at the lower-right edge
- `Ops Console` filter controls stay within the panel, right-side detail panes scroll, and `Recent Ops Activity` remains visible
- the top bar language switch updates shell copy between English and Chinese

## Structured Stream Verification

With a running backend, verify a real structured stream response:

```bash
curl -N -X POST http://127.0.0.1:18000/threads/<thread-id>/runs/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"Reply with OK only."}'
```

Confirm the stream emits ordered lifecycle events such as:

- `run_started`
- `message_opened`
- `message_delta`
- `message_completed`
- `run_completed`

If the provider exposes thinking content, also confirm:

- `reasoning_opened`
- `reasoning_delta`
- `reasoning_completed`
- the UI collapses completed reasoning into a compact duration summary that can be expanded back into segmented detail

## Thread Detail Verification

Verify durable transcript projection through the detail endpoint:

```bash
curl http://127.0.0.1:18000/threads/<thread-id>/detail
```

Confirm:

- `messages[]` contains the durable transcript in order
- `message_id` is populated for each message
- `tool_calls[*].result_text` is populated when a tool result exists
- `artifact_refs[*]` is populated for uploaded or emitted artifacts
- `stream_capabilities` reports the supported live event types

## Manual Provider Verification

Run only with real configured models and env-backed secrets:

```bash
anvil-smoke provider --config ./config.yaml --model <openai-compatible-model-key> --message "Reply with OK only." --report-dir ./.omx/reports/provider-smoke
anvil-smoke provider --config ./config.yaml --model minimax --message "Reply with OK only." --report-dir ./.omx/reports/provider-smoke
```

Use the actual configured model key shown by doctor `available_models`. Confirm each smoke leaves `provider-smoke-<model>.json` and `provider-smoke-<model>.md` under the report directory. The Markdown report must show runtime phase diagnostics, prompt/context/cache diagnostics, tool calls, enabled skills, memory snapshot/injection status, hidden bug risks, and recommendations. Treat a passing console line without these artifacts as insufficient release evidence.

## Manual Tracing Verification

Enable LangSmith env vars, then run:

```bash
anvil-smoke provider --config ./config.yaml --model <openai-compatible-model-key> --expect-trace --message "Reply with OK only." --report-dir ./.omx/reports/provider-tracing-smoke
```

Confirm:

- smoke reports tracing as active
- the remote LangSmith project contains the expected run(s)

## Documentation Verification

Before release, confirm:

- `make docs` builds the MkDocs site
- `docs/index.md` links to ADRs, guides, and examples
- `docs/guides/deployment.md` matches Docker, Conda, Makefile, and local startup behavior
- `README.md` commands match the real startup and smoke surfaces
- `docs/guides/local-docker-workspace.md` matches the current compose and script flow

## Cleanup Verification

Before cutting a release, remove or keep ignored:

- `backend/_tmp_test`
- repo `__pycache__`
- frontend `.next`
- other generated caches already covered by `.gitignore`

Do not remove tracked `.idea/` in Phase 9. Only prevent future noise from spreading.

