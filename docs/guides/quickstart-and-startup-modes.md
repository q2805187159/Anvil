# Quickstart and Startup Modes

This guide is the fastest route from clone to a working Anvil surface.

## Prerequisites

- Python `3.12+`
- Node.js `22+` if you want the frontend
- backend dependencies installed from `backend/`
- frontend dependencies installed from `frontend/` when needed

## Step 1: Create Local Config

With `make`:

```bash
make config
```

Windows PowerShell:

```powershell
copy .env.example .env
.\scripts\init-config.ps1
```

Linux, macOS, or WSL:

```bash
cp .env.example .env
./scripts/init-config.sh
```

Then edit `config.yaml` and put real secrets in `.env`.

## Step 2: Validate the Environment

From `backend/`:

```bash
python -m app.doctor --config ../config.yaml
python -m app.smoke local
```

If you installed the package entrypoints, you can also use:

```bash
anvil-doctor --config ./config.yaml
anvil-smoke local --config ./config.yaml
```

## Step 3: Choose a Run Mode

| Mode | Best for | Command |
| --- | --- | --- |
| Gateway only | backend API work, automation, frontend integration | `scripts/start-backend.*` |
| Shell first | TUI/operator flow over the embedded client | `scripts/start-shell.*` |
| Frontend workbench | operator UI over the gateway | `scripts/start-frontend.*` |
| Local full stack | backend + frontend together | `scripts/start-fullstack.*` |
| Docker Compose | publish-like full stack startup | `docker compose up -d` |
| Docker workspace scripts | repeatable local operator console startup | `scripts/start-docker.*` |
| Makefile | Linux/macOS/WSL/Git Bash repeatable workflow | `make backend`, `make frontend`, `make docker-start` |

## Startup Paths

### Gateway only

Windows PowerShell:

```powershell
.\scripts\start-backend.ps1
```

Linux, macOS, or WSL:

```bash
./scripts/start-backend.sh
```

Health check:

```text
http://127.0.0.1:18000/health
```

### Shell first

Windows PowerShell:

```powershell
.\scripts\start-shell.ps1
```

Linux, macOS, or WSL:

```bash
./scripts/start-shell.sh
```

The shell uses the embedded SDK and must not be treated as a second runtime control plane.

### Frontend workbench

Start the gateway first, then:

Windows PowerShell:

```powershell
.\scripts\start-frontend.ps1
```

Linux, macOS, or WSL:

```bash
./scripts/start-frontend.sh
```

Once the frontend is running:

- `/` opens the chat-first workspace
- `/threads/<threadId>` opens a specific thread
- add `?ops=1&surface=tools|skills|mcp|plugins` to open the full `Ops Console`
- use `item`, `action`, and `server` query params for deep links into skill governance and MCP inspection flows

### Local full stack

Windows PowerShell:

```powershell
.\scripts\start-fullstack.ps1
```

Linux, macOS, or WSL:

```bash
./scripts/start-fullstack.sh
```

### Docker Compose

```bash
docker compose up -d
```

Compose reads the local `.env`, mounts `config.yaml` read-only into the backend container at `/app/config.yaml`, mounts `.omx/reports` at `/app/.omx/reports` for smoke/evaluation artifacts, exposes the gateway on `http://127.0.0.1:18000`, exposes the frontend on `http://127.0.0.1:13200` by default, and preserves runtime state in Docker volumes. Set `ANVIL_FRONTEND_PORT` or `ANVIL_BACKEND_PORT` if you need a different published port. The defaults are five-digit values to reduce collisions with common local development services.

Set `ANVIL_BACKEND_PORT` when the backend host port must change. Also set `NEXT_PUBLIC_ANVIL_GATEWAY_URL` to the matching browser-visible URL so the frontend calls the correct gateway.
Use `docker compose up -d --build` only when you intentionally want to rebuild local images.

### Docker workspace scripts

Windows PowerShell:

```powershell
.\scripts\start-docker.ps1
.\scripts\status-docker.ps1
.\scripts\stop-docker.ps1
```

Linux, macOS, or WSL:

```bash
./scripts/start-docker.sh
./scripts/status-docker.sh
./scripts/stop-docker.sh
```

The workspace scripts reuse existing images by default. Pass `-Build` on
PowerShell or `--build` on bash when Docker images must be rebuilt.

## Where To Go Next

- For model config details: [Model and Provider Configuration](./model-provider-configuration.md)
- For verification and tracing: [Doctor, Smoke, and Tracing](./doctor-smoke-and-tracing.md)
- For the local Docker operator flow: [Local Docker Workspace](./local-docker-workspace.md)
- For a broader release checklist: [Release Verification](./release-verification.md)

