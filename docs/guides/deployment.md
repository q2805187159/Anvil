# Deployment

This guide covers repeatable installation and deployment paths for Anvil.

## Supported Environments

| Environment | Status | Best for |
| --- | --- | --- |
| Docker Compose on Linux/macOS/Windows | Recommended | Local full-stack operation and publish-like validation |
| Local Python + Node.js | Supported | Backend/frontend development |
| Conda | Supported | Python dependency isolation on Windows, Linux, and macOS |
| Makefile workflow | Supported on Linux/macOS/WSL/Git Bash | Repeatable setup, tests, contracts, and docs |

## Prerequisites

- Python `3.12+`
- Node.js `22+`
- Docker Engine with Compose v2 for container deployment
- `make` for the Makefile workflow
- Tesseract OCR when running document OCR locally outside Docker

## Configuration

Create local config from the checked-in examples:

```bash
make config
```

This creates `.env` and `config.yaml` only when they do not already exist. Keep secrets in `.env` and keep model/provider routing in `config.yaml`.

For restricted networks, set mirrors before installing or building:

```bash
export PIP_INDEX_URL=https://pypi.org/simple
export NPM_REGISTRY=https://registry.npmmirror.com
```

The frontend Docker image defaults `NPM_REGISTRY` to `https://registry.npmmirror.com`
because `frontend/package-lock.json` is locked to that mirror. Override the
variable only when your environment requires a different registry.

## Docker Compose

Start the full stack:

```bash
make docker-start
```

Equivalent direct command:

```bash
docker compose up -d
```

The startup scripts automatically fall back to standalone `docker-compose` when the v2 `docker compose` plugin is unavailable.
Normal startup reuses existing images and avoids pulling Docker Hub base layers.
Rebuild explicitly only when image contents must be refreshed:

```bash
scripts/start-docker.sh --build
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-docker.ps1 -Build
```

Default endpoints:

- Backend: `http://127.0.0.1:18000`
- Frontend: `http://127.0.0.1:13200`
- Health: `http://127.0.0.1:18000/health`

Override ports with:

```bash
ANVIL_BACKEND_PORT=18010 \
ANVIL_FRONTEND_PORT=13210 \
NEXT_PUBLIC_ANVIL_GATEWAY_URL=http://127.0.0.1:18010 \
make docker-start
```

Docker build arguments are read from the environment:

- `PYTHON_BASE_IMAGE`
- `NODE_BASE_IMAGE`
- `PIP_INDEX_URL`
- `PIP_TRUSTED_HOST`
- `NPM_REGISTRY`
- `NEXT_PUBLIC_ANVIL_GATEWAY_URL`

`PYTHON_BASE_IMAGE` and `NODE_BASE_IMAGE` default to digest-pinned images so
repeat builds do not chase mutable upstream tags. Override them only when you
intend to update the base runtime.

Stop the stack:

```bash
make docker-stop
```

Check status:

```bash
make docker-status
```

## Local Development

Install dependencies:

```bash
make install-backend-dev
make install-frontend
```

Start the backend:

```bash
make backend
```

Start the frontend in another terminal:

```bash
make frontend
```

Or start both through the existing script:

```bash
make dev
```

## Conda

Create or update the backend environment:

```bash
conda env update -f backend/environment.yml --prune
conda activate anvil-backend
python -m pip install -e "backend[observability,test,docs]"
```

Then install frontend dependencies:

```bash
cd frontend
npm ci
```

## Verification

Run the core checks:

```bash
make contracts
make test-backend
make test-frontend
make typecheck
make docs
```

Run backend coverage:

```bash
make test-backend-cov
```

This writes `backend/coverage.xml`, which is ignored locally and uploaded by CI.

## Local Docker Host Paths

Use the bundled Docker startup scripts for local desktop installs:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-docker.ps1
```

```bash
scripts/start-docker.sh
```

Pass `-Build` on PowerShell or `--build` on bash only when you intentionally
want Docker to rebuild local images.

The scripts generate an Anvil Home profile Compose override at
`~/.anvil/docker-compose.host-paths.yml` and pass explicit
path bridges into the backend container. On Windows this exposes available
drives as virtual roots such as `/mnt/user-data/workspace/_host/c_drive` and
`/mnt/user-data/workspace/_host/e_drive`; user-facing paths like `E:\project`
are translated to those runtime roots. On macOS and Linux the scripts expose
common user/workspace roots that are actually mounted into the container.

If you launch Docker Compose manually, generate the override first by running
one of the scripts once, then include the generated file:

```bash
docker compose -f docker-compose.yml -f ~/.anvil/docker-compose.host-paths.yml up -d
```

`workspace.path_bridges` and `ANVIL_PATH_BRIDGES` describe paths that already
exist inside the backend process. They do not mount host disks by themselves;
Docker must bind-mount those directories into the container first.

## Production Notes

Anvil exposes high-privilege capabilities such as file tools, process execution, MCP servers, memory writes, and sub-agent delegation. Production deployments should place Anvil behind authentication and network controls.

Recommended baseline:

- Bind public deployments behind a reverse proxy with authentication.
- Keep `~/.anvil/.env`, Home `config.yaml`, and runtime state out of Git.
- Prefer containerized execution for untrusted tasks.
- Keep `guardrails.enabled=true`.
- Review MCP server commands before enabling them.
- Use a dedicated workspace volume for runtime state and generated artifacts.
