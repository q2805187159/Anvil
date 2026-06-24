from __future__ import annotations

import shutil
from pathlib import Path
import os


IGNORED_SOURCE_COPY_NAMES = {
    "build",
    "__pycache__",
    ".pytest_tmp",
    ".pytest_cache",
    ".venv",
    "venv",
    "env",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "htmlcov",
}


def copy_backend_source_for_packaging(backend_root: Path, tmp_path: Path) -> Path:
    source_root = tmp_path / "backend-src"

    def ignore(_: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in IGNORED_SOURCE_COPY_NAMES
            or name.endswith((".egg-info", ".pyc", ".pyo"))
        }

    shutil.copytree(backend_root, source_root, ignore=ignore)
    return source_root


def packaging_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    cache_root = tmp_path / "pip-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    env["PIP_CACHE_DIR"] = str(cache_root)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env
