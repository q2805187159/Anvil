# Local Docker Workspace

Use this guide when you want the Anvil operator workspace and gateway running together through Docker Compose.

## Prerequisites

- Docker Desktop or a compatible local Docker engine
- `.env` present at the repo root
- `config.yaml` present at the repo root

## Startup

Windows PowerShell:

```powershell
.\scripts\start-docker.ps1
```

Linux, macOS, or WSL:

```bash
./scripts/start-docker.sh
```

The scripts use `docker compose` when available and fall back to standalone `docker-compose` on older local Docker installations. After Compose starts, the startup script verifies the actually published host ports before reporting usable URLs; if Docker reports a container port without a published host port, the script exits with a clear endpoint verification error instead of printing a stale URL.

Normal startup reuses existing images and does not rebuild. Pass `-Build` on
PowerShell or `--build` on bash when image contents must be refreshed. Backend
dependency installation is cached separately from backend source files, and the
frontend image uses a cached npm install layer; Docker only reinstalls
dependencies when the dependency manifests, build args, or pinned base images
change.

Compose builds the backend from `./backend` and the frontend from `./frontend`.
Each service has its own `.dockerignore`, so local-only artifacts such as
`backend/.venv`, Python bytecode caches, `frontend/node_modules`, and
`frontend/.next` are not sent to unrelated image builds. The repo-root
`.dockerignore` remains as a safety net for manual root-context builds, but the
default Compose path should stay service-scoped so a frontend refresh cannot be
blocked by multi-GB backend development caches.

The base backend service mounts only the narrow release-smoke inputs and
outputs from the repository root:

- `./config.yaml` -> `/app/config.yaml` read-only
- `./.omx/reports` -> `/app/.omx/reports` read-write

Do not replace these with a read-write repository-root bind mount. Agent
workspace writes continue to use the dedicated Docker workspace volume.

Default endpoints:

- frontend: `http://127.0.0.1:13200`
- backend: `http://127.0.0.1:18000`
- health: `http://127.0.0.1:18000/health`

If you need different published ports, set `ANVIL_FRONTEND_PORT` or `ANVIL_BACKEND_PORT` before running the startup script. The default Docker ports are five-digit values to reduce collisions with common local development services.

The Docker startup scripts also export one local path bridge:

- display root: your local `Anvil/` repository root
- runtime mount: `/mnt/host-workspaces/harness`
- agent-facing translated path family: `/mnt/user-data/workspace/_host/harness/...`

This keeps the Docker runtime scoped to the Anvil repository itself instead of mounting sibling projects from the outer `harness/` workspace. You can still type local host paths such as `E:\python\python学习\harness\Anvil` in the UI while the runtime translates them into internal virtual paths before agent execution, then translates them back for history and results.

## Stop

Windows PowerShell:

```powershell
.\scripts\stop-docker.ps1
```

Linux, macOS, or WSL:

```bash
./scripts/stop-docker.sh
```

## Status

Windows PowerShell:

```powershell
.\scripts\status-docker.ps1
```

Linux, macOS, or WSL:

```bash
./scripts/status-docker.sh
```

This prints:

- `docker compose ps`
- frontend/backend/health URLs from the actual published host ports
- backend health check result
- common verification commands

## UI Test Flow

1. Open `http://127.0.0.1:13200`
   - If you set `ANVIL_FRONTEND_PORT`, use that port instead.
2. Click `Create thread` only if you want to start a draft conversation; no durable thread is created yet
3. Send a message from the composer and confirm the durable transcript updates and the new thread appears in the sidebar
4. Open a thread directly through `/threads/<thread-id>` and confirm the same transcript reloads
5. Expand a reasoning panel when the provider returns thinking content, then confirm it auto-collapses to a compact `thought for <duration>` summary after the final answer completes and can be expanded again
6. Open the `Approvals` tab and approve a pending guarded run when needed
7. Confirm tool calls render as grouped tool blocks rather than raw event names
8. Open the `Memory` tab and verify:
   - stores load
   - provider list loads
   - archive search returns hits
   - reflection jobs can be run
9. Open the `Skills` tab and verify discovered skills appear
10. Switch between English and Chinese from the top bar and confirm shell copy changes
11. Open the `Ops` tab and verify long filter values stay inside the `Tools Catalog`, right-side detail panes scroll independently, and `Recent Ops Activity` remains reachable
12. Confirm long thread titles or previews do not deform the left rail cards, assistant replies use the full transcript width, and copy or edit actions sit at the lower-right edge of each message card

## Manual Smoke

Backend:

```bash
cd backend
python -m pytest -q
python -m app.doctor --config ../config.yaml
python -m app.smoke local --config ../config.yaml
```

Frontend:

```bash
cd frontend
npm test
npm run typecheck
npm run build
```

Provider and tracing:

```bash
docker compose exec -T backend python -m app.doctor --config /app/config.yaml
docker compose exec -T backend python -m app.smoke local --config /app/config.yaml --report-dir /app/.omx/reports/docker-local-smoke
docker compose exec -T backend python -m app.smoke provider --config /app/config.yaml --model minimax --message "Reply with OK only." --report-dir /app/.omx/reports/docker-provider-smoke
docker compose exec -T backend python -m app.smoke provider --config /app/config.yaml --model <openai-compatible-model-key> --message "Reply with OK only." --report-dir /app/.omx/reports/docker-provider-smoke
docker compose exec -T backend python -m app.smoke provider --config /app/config.yaml --model <openai-compatible-model-key> --expect-trace --message "Reply with OK only." --report-dir /app/.omx/reports/docker-provider-tracing-smoke
```

The `--report-dir` artifacts are part of the Docker smoke gate. Keep the JSON and Markdown reports so runtime timings, prompt/cache/context diagnostics, tool calls, skills, memory status, hidden risks, and recommendations can be compared against source changes.
Use the actual configured model key reported by `app.doctor` under `available_models`; do not pass the provider kind name `openai_compatible` unless that is also the model key in `config.yaml`.

## Common Issues

- Backend health is unreachable:
  - confirm Docker Desktop / engine is running
  - run `docker compose ps`
  - inspect container logs with `docker compose logs backend`
- Frontend target port is not published:
  - run `docker compose config` and confirm `frontend.ports` includes the expected `ANVIL_FRONTEND_PORT`
  - run `docker compose ps --format json` and inspect the frontend `Publishers` entry
  - recreate the frontend container only when you intentionally want to refresh Docker state
- Frontend loads but actions fail:
  - confirm `NEXT_PUBLIC_ANVIL_GATEWAY_URL` points at `http://127.0.0.1:18000`
  - confirm backend health is `ok`
- Provider smoke fails:
  - verify `.env` secrets
  - verify `config.yaml` model names match the smoke command
- Tracing smoke fails:
  - verify LangSmith env vars
  - confirm the target LangSmith project receives runs
