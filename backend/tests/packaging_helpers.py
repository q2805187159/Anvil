from __future__ import annotations

import shutil
from pathlib import Path
import os


def copy_backend_source_for_packaging(backend_root: Path, tmp_path: Path) -> Path:
    source_root = tmp_path / "backend-src"

    def ignore(_: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {"build", "anvil_backend.egg-info", "__pycache__", ".pytest_tmp", ".pytest_cache"}
            or name.endswith(".pyc")
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
