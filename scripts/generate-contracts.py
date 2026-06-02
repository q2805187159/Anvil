from __future__ import annotations

import argparse
import os
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
HARNESS_ROOT = BACKEND_ROOT / "packages" / "harness"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

if os.name == "nt":
    sys.platform = "linux"
    sys.modules.setdefault("readline", types.ModuleType("readline"))

from app.contract_generation import main as generate_main  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Anvil backend schema bundle and frontend TypeScript contracts.")
    parser.add_argument("--check", action="store_true", help="Fail when generated artifacts are out of date.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(generate_main(check=args.check))
