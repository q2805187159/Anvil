#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${1:-127.0.0.1}"
PORT="${2:-13200}"
cd "$ROOT/frontend"
npm run dev -- --hostname "$HOST" --port "$PORT"
