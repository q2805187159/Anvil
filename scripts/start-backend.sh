#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-127.0.0.1}"
PORT="${2:-18000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"

export PYTHONPATH="$BACKEND:$BACKEND/packages/harness${PYTHONPATH:+:$PYTHONPATH}"
cd "$BACKEND"
python -m app.gateway.main --host "$HOST" --port "$PORT"
