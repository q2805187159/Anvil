from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE_DOCS_ROOT = REPO_ROOT / ".omx" / "release-docs"


def release_docs_timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def choose_release_docs_site_dir(root: Path = DEFAULT_RELEASE_DOCS_ROOT) -> Path:
    return root / f"docs-{release_docs_timestamp()}-{os.getpid()}"


def resolve_site_dir(site_dir: Path | None) -> Path:
    chosen = site_dir if site_dir is not None else choose_release_docs_site_dir()
    if not chosen.is_absolute():
        chosen = REPO_ROOT / chosen
    return chosen.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build MkDocs release docs into an isolated per-run output directory."
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        default=None,
        help="Override the MkDocs output directory. Defaults to .omx/release-docs/docs-<timestamp>-<pid>.",
    )
    args = parser.parse_args(argv)

    site_dir = resolve_site_dir(args.site_dir)
    site_dir.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "mkdocs", "build", "--site-dir", str(site_dir)]
    completed = subprocess.run(command, cwd=REPO_ROOT)
    if completed.returncode == 0:
        print(f"[build-release-docs] wrote {site_dir}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())