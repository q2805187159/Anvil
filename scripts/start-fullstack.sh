#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${ANVIL_BACKEND_PORT:-18000}"
FRONTEND_PORT="${ANVIL_FRONTEND_PORT:-13200}"
export NEXT_PUBLIC_ANVIL_GATEWAY_URL="${NEXT_PUBLIC_ANVIL_GATEWAY_URL:-http://127.0.0.1:${BACKEND_PORT}}"
"$ROOT/scripts/start-backend.sh" 127.0.0.1 "$BACKEND_PORT" &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT

cd "$ROOT/frontend"
npm run dev -- --hostname 127.0.0.1 --port "$FRONTEND_PORT"
