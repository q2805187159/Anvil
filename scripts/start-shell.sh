#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"

export PYTHONPATH="$BACKEND:$BACKEND/packages/harness${PYTHONPATH:+:$PYTHONPATH}"
cd "$BACKEND"
python -m app.shell.main "$@"
