from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
FORCE_REMOVABLE_PATHS = {
    "backend/build",
}


def _tracked_paths() -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _contains_tracked(path: Path, tracked: set[str]) -> bool:
    if not path.exists():
        return False
    relative = _relative(path)
    prefix = f"{relative}/"
    return any(item == relative or item.startswith(prefix) for item in tracked)


def _is_force_removable(path: Path) -> bool:
    relative = _relative(path)
    if relative in FORCE_REMOVABLE_PATHS:
        return True
    return relative.startswith("backend/") and relative.endswith(".egg-info")


def _remove_path(path: Path) -> None:
    def handle_remove_readonly(func, target, excinfo) -> None:
        target_path = Path(target)
        os.chmod(target_path, stat.S_IWRITE | stat.S_IREAD)
        func(target)

    if path.is_dir():
        shutil.rmtree(path, onexc=handle_remove_readonly)
        return
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()


def _collect_candidates() -> list[Path]:
    candidates: list[Path] = []

    for root in [REPO_ROOT / "backend", REPO_ROOT / "frontend"]:
        if not root.exists():
            continue
        candidates.extend(root.rglob("__pycache__"))

    explicit_paths = [
        REPO_ROOT / "backend" / ".pytest_cache",
        REPO_ROOT / "backend" / ".pytest_tmp",
        REPO_ROOT / "backend" / "_tmp_test",
        REPO_ROOT / "backend" / "build",
        REPO_ROOT / "frontend" / ".next",
        REPO_ROOT / "frontend" / "coverage",
        REPO_ROOT / "frontend" / "node_modules" / ".vite",
    ]
    candidates.extend(explicit_paths)
    candidates.extend((REPO_ROOT / "backend").glob("pytest-cache-files-*"))
    candidates.extend((REPO_ROOT / "backend").glob("*.egg-info"))
    candidates.extend((REPO_ROOT / "frontend").rglob("*.tsbuildinfo"))

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        unique.append(candidate)
    return sorted(unique, key=lambda item: (len(item.parts), item.as_posix()), reverse=True)


def main() -> int:
    tracked = _tracked_paths()
    removed: list[str] = []
    skipped: list[str] = []

    for candidate in _collect_candidates():
        relative = _relative(candidate)
        if not _is_force_removable(candidate) and _contains_tracked(candidate, tracked):
            skipped.append(relative)
            continue
        _remove_path(candidate)
        removed.append(relative)

    print(
        json.dumps(
            {
                "removed": removed,
                "skipped_tracked": skipped,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
