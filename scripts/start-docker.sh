#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${ANVIL_BACKEND_PORT:-18000}"
FRONTEND_PORT="${ANVIL_FRONTEND_PORT:-13200}"
COMPOSE_FILE_ARGS=(-f docker-compose.yml)
cd "$ROOT"

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "${COMPOSE_FILE_ARGS[@]}" "$@"
  else
    docker-compose "${COMPOSE_FILE_ARGS[@]}" "$@"
  fi
}

yaml_scalar() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"
}

build_host_path_override() {
  local anvil_home="${ANVIL_HOME_HOST:-${HOME}/.anvil}"
  local shared_workspace="${ANVIL_WORKSPACE_HOST:-${anvil_home}/workspace}"
  mkdir -p "${anvil_home}" "${shared_workspace}"
  anvil_home="$(cd "${anvil_home}" && pwd)"
  shared_workspace="$(cd "${shared_workspace}" && pwd)"

  local bridges=("shared_workspace|${shared_workspace}|/mnt/host-workspaces/harness")
  local mounts=()
  local system
  system="$(uname -s | tr '[:upper:]' '[:lower:]')"

  if [[ "${system}" == darwin* ]]; then
    for host_path in "${HOME}" "${HOME}/Desktop" "${HOME}/Documents" "${HOME}/Downloads"; do
      [[ -d "${host_path}" ]] || continue
      local alias
      alias="$(basename "${host_path}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g')"
      [[ -n "${alias}" ]] || alias="home"
      mounts+=("${host_path}|/mnt/host/${alias}")
      bridges+=("${alias}|${host_path}|/mnt/host/${alias}")
    done
    if [[ -d /Volumes ]]; then
      while IFS= read -r volume; do
        [[ -d "${volume}" ]] || continue
        local name alias
        name="$(basename "${volume}")"
        alias="volume_$(printf '%s' "${name}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g')"
        mounts+=("${volume}|/mnt/host/${alias}")
        bridges+=("${alias}|${volume}|/mnt/host/${alias}")
      done < <(find /Volumes -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
    fi
  else
    for host_path in "${HOME}" "$(dirname "${ROOT}")"; do
      [[ -d "${host_path}" ]] || continue
      local alias
      alias="$(basename "${host_path}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g')"
      [[ -n "${alias}" ]] || alias="home"
      mounts+=("${host_path}|/mnt/host/${alias}")
      bridges+=("${alias}|${host_path}|/mnt/host/${alias}")
    done
    for mount_root in /mnt /media; do
      [[ -d "${mount_root}" ]] || continue
      while IFS= read -r mounted; do
        [[ -d "${mounted}" ]] || continue
        case "${mounted}" in
          /mnt/user-data|/mnt/user-data/*|/mnt/worker-data|/mnt/worker-data/*|/mnt/host-workspaces|/mnt/host-workspaces/*)
            continue
            ;;
        esac
        local alias
        alias="$(basename "${mounted}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g')"
        [[ -n "${alias}" ]] || continue
        mounts+=("${mounted}|/mnt/host/${mount_root##*/}_${alias}")
        bridges+=("${mount_root##*/}_${alias}|${mounted}|/mnt/host/${mount_root##*/}_${alias}")
      done < <(find "${mount_root}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
    done
  fi

  local override_path="${anvil_home}/docker-compose.host-paths.yml"
  local bridges_joined
  bridges_joined="$(IFS=';'; echo "${bridges[*]}")"
  export ANVIL_HOME_HOST="${anvil_home}"
  export ANVIL_WORKSPACE_HOST="${shared_workspace}"
  export ANVIL_PATH_BRIDGES="${bridges_joined}"
  {
    echo "services:"
    echo "  backend:"
    echo "    environment:"
    echo "      ANVIL_HOME: /app/.anvil"
    printf "      ANVIL_PATH_BRIDGES: "
    yaml_scalar "${bridges_joined}"
    printf "\n"
    echo "    volumes:"
    echo "      - type: bind"
    printf "        source: "
    yaml_scalar "${anvil_home}"
    printf "\n"
    echo "        target: /app/.anvil"
    echo "        read_only: false"
    echo "      - type: bind"
    printf "        source: "
    yaml_scalar "${shared_workspace}"
    printf "\n"
    echo "        target: /mnt/host-workspaces/harness"
    echo "        read_only: false"
    if [[ "${#mounts[@]}" -gt 0 ]]; then
      for mount in "${mounts[@]}"; do
        local source="${mount%%|*}"
        local target="${mount#*|}"
        echo "      - type: bind"
        printf "        source: "
        yaml_scalar "${source}"
        printf "\n"
        echo "        target: ${target}"
        echo "        read_only: false"
      done
    fi
  } > "${override_path}"
  COMPOSE_FILE_ARGS=(-f docker-compose.yml -f "${override_path}")
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

check_http() {
  local name="$1"
  local url="$2"
  if command -v curl >/dev/null 2>&1; then
    if curl --fail --silent --show-error --output /dev/null "$url"; then
      echo "${name} check: reachable"
      return 0
    fi
  elif command -v wget >/dev/null 2>&1; then
    if wget -qO- "$url" >/dev/null; then
      echo "${name} check: reachable"
      return 0
    fi
  else
    echo "Install curl or wget to verify ${name}."
    return 0
  fi
  echo "${name} check failed: ${url} is not reachable."
  return 1
}

BUILD_FLAG="${1:-}"
build_host_path_override
if [[ "${BUILD_FLAG}" == "--build" || "${BUILD_FLAG}" == "-Build" ]]; then
  compose up -d --build
else
  echo "Docker images are being reused. After source changes, run this script with --build to refresh the frontend/backend images."
  compose up -d
fi

BACKEND_URL=""
FRONTEND_URL=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  BACKEND_URL="$(published_endpoint backend 18000 || true)"
  FRONTEND_URL="$(published_endpoint frontend 13200 || true)"
  if [[ "${BACKEND_URL}" == http* && "${FRONTEND_URL}" == http* ]]; then
    break
  fi
  sleep 1
done
[[ -z "${BACKEND_URL}" ]] && BACKEND_URL="unavailable (compose status could not be read)"
[[ -z "${FRONTEND_URL}" ]] && FRONTEND_URL="unavailable (compose status could not be read)"

cat <<'EOF'
Anvil local Docker workspace is starting.
EOF
cat <<EOF
Frontend: ${FRONTEND_URL}
Backend:  ${BACKEND_URL}
Health:   $(if [[ "${BACKEND_URL}" == http* ]]; then echo "${BACKEND_URL}/health"; else echo "not available until backend port is published"; fi)
Path bridge: ${ANVIL_PATH_BRIDGES}
EOF

endpoint_failures=0
if [[ "${BACKEND_URL}" == http* ]]; then
  check_http "Backend health" "${BACKEND_URL}/health" || endpoint_failures=1
else
  echo "Backend target port 18000 is not published."
  endpoint_failures=1
fi
if [[ "${FRONTEND_URL}" == http* ]]; then
  check_http "Frontend" "${FRONTEND_URL}" || endpoint_failures=1
else
  echo "Frontend target port 13200 is not published."
  endpoint_failures=1
fi
if [[ "${endpoint_failures}" -ne 0 ]]; then
  echo "Docker workspace started, but published endpoint verification failed." >&2
  exit 1
fi
