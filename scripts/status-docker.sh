#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${ANVIL_BACKEND_PORT:-18000}"
FRONTEND_PORT="${ANVIL_FRONTEND_PORT:-13200}"
cd "$ROOT"

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

published_endpoint() {
  local service="$1"
  local target_port="$2"
  local ps_json
  ps_json="$(compose ps --format json 2>/dev/null || true)"
  if [[ -z "${ps_json}" ]]; then
    return 1
  fi
  if command -v python3 >/dev/null 2>&1; then
    PS_JSON="${ps_json}" python3 - "$service" "$target_port" <<'PY'
import json
import os
import sys

service = sys.argv[1]
target = int(sys.argv[2])
for line in os.environ.get("PS_JSON", "").splitlines():
    if not line.strip():
        continue
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        continue
    entries = parsed if isinstance(parsed, list) else [parsed]
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("Service") != service:
            continue
        for publisher in entry.get("Publishers") or []:
            if int(publisher.get("TargetPort") or 0) != target:
                continue
            published = int(publisher.get("PublishedPort") or 0)
            if published <= 0:
                print("not-published")
                raise SystemExit(0)
            host = publisher.get("URL") or "127.0.0.1"
            if host in {"0.0.0.0", "::"}:
                host = "127.0.0.1"
            print(f"http://{host}:{published}")
            raise SystemExit(0)
        print("not-published")
        raise SystemExit(0)
raise SystemExit(1)
PY
    return $?
  fi
  return 1
}

BACKEND_URL="$(published_endpoint backend 18000 || true)"
FRONTEND_URL="$(published_endpoint frontend 13200 || true)"
[[ -z "${BACKEND_URL}" ]] && BACKEND_URL="unavailable (compose status could not be read)"
[[ -z "${FRONTEND_URL}" ]] && FRONTEND_URL="unavailable (compose status could not be read)"

echo "== Compose Services =="
if compose_output="$(compose ps -a 2>&1)"; then
  printf '%s\n' "${compose_output}"
else
  echo "Docker compose is unavailable or the local engine is not running."
  printf '%s\n' "${compose_output}"
fi
echo
echo "== Endpoints =="
echo "Frontend: ${FRONTEND_URL}"
echo "Backend:  ${BACKEND_URL}"
if [[ "${BACKEND_URL}" == http* ]]; then
  echo "Health:   ${BACKEND_URL}/health"
else
  echo "Health:   not available until backend port is published"
fi
echo
echo "== Health Check =="
if [[ "${BACKEND_URL}" != http* ]]; then
  echo "Backend target port 18000 is not published."
elif command -v curl >/dev/null 2>&1; then
  curl --silent --show-error "${BACKEND_URL}/health" || echo "Backend health endpoint is not reachable."
elif command -v wget >/dev/null 2>&1; then
  wget -qO- "${BACKEND_URL}/health" || echo "Backend health endpoint is not reachable."
else
  echo "Install curl or wget to run the HTTP health check."
fi
echo
echo "== Frontend Check =="
if [[ "${FRONTEND_URL}" != http* ]]; then
  echo "Frontend target port 13200 is not published."
elif command -v curl >/dev/null 2>&1; then
  curl --silent --show-error --output /dev/null --write-out "Frontend status: %{http_code}\n" "${FRONTEND_URL}" || echo "Frontend endpoint is not reachable."
elif command -v wget >/dev/null 2>&1; then
  wget -qO- "${FRONTEND_URL}" >/dev/null && echo "Frontend status: 200" || echo "Frontend endpoint is not reachable."
else
  echo "Install curl or wget to run the HTTP frontend check."
fi
echo
echo
echo "== Verification =="
echo "backend:  python -m pytest -q"
echo "frontend: npm test"
echo "frontend: npm run typecheck"
echo "frontend: npm run build"
