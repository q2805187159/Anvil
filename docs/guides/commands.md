# Command Reference

This page collects repository-level commands for setup, development,
verification, Docker, docs, and release checks.

## Setup

| Command | Description |
| --- | --- |
| `make config` | Create `.env` and `config.yaml` from examples when missing. |
| `make install` | Install backend and frontend dependencies. |
| `make install-backend` | Install backend with observability extras. |
| `make install-backend-dev` | Install backend with observability, tests, and docs extras. |
| `make install-frontend` | Run `npm ci` in `frontend/`. |

Windows without `make`:

```powershell
copy .env.example .env
.\scripts\init-config.ps1
cd frontend
npm ci
```

Backend install:

```powershell
cd backend
python -m pip install -e ".[observability,test,docs]"
```

## Run

| Command | Description |
| --- | --- |
| `make backend` | Start the FastAPI gateway on `127.0.0.1:18000`. |
| `make frontend` | Start the frontend workbench. |
| `make dev` | Start backend and frontend with the script wrapper. |
| `make shell` | Start the TUI shell. |
| `make docker-start` | Start Docker Compose. |
| `make docker-status` | Show Docker Compose status. |
| `make docker-stop` | Stop Docker Compose. |

Direct scripts are available as `.sh` and `.ps1` files:

```bash
scripts/start-backend.sh
scripts/start-frontend.sh
scripts/start-fullstack.sh
scripts/start-shell.sh
scripts/start-docker.sh
scripts/status-docker.sh
scripts/stop-docker.sh
```

## Verification

| Command | Description |
| --- | --- |
| `make contracts` | Regenerate and check backend/frontend contracts. |
| `make check-docker-mounts` | Validate Docker mount safety. |
| `make test-backend` | Run backend tests. |
| `make test-backend-cov` | Run backend tests with coverage XML. |
| `make test-frontend` | Run frontend tests. |
| `make typecheck` | Run frontend TypeScript typecheck. |
| `make build-frontend` | Build the frontend release artifact. |
| `make docs` | Build the MkDocs site. |
| `make check` | Run the standard local gate. |

## Release

| Command | Description |
| --- | --- |
| `make release-readiness` | Run quick release readiness gates. |
| `make release-readiness-full` | Run full release readiness gates. |
| `python scripts/run-release-readiness.py --profile quick --json` | Emit machine-readable quick gate results. |
| `python scripts/run-release-readiness.py --profile full --json` | Emit machine-readable full gate results. |

## Cleanup

```bash
make clean
```

`scripts/clean-dev-artifacts.py` removes caches and build outputs, but skips
tracked files unless they are explicitly safe generated artifacts.
