#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE="$ROOT/config.example.yaml"
TARGET="$ROOT/config.yaml"

if [[ ! -f "$EXAMPLE" ]]; then
  echo "Missing config.example.yaml" >&2
  exit 1
fi

if [[ ! -f "$TARGET" ]]; then
  cp "$EXAMPLE" "$TARGET"
  echo "Created $TARGET from config.example.yaml"
else
  echo "config.yaml already exists: $TARGET"
fi
