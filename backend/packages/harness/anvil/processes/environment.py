from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anvil.sandbox import PathService


RESERVED_PROCESS_ENV_KEYS = {
    "ANVIL_WORKSPACE",
    "ANVIL_UPLOADS",
    "ANVIL_OUTPUTS",
    "ANVIL_SCRATCH",
    "ANVIL_VIRTUAL_PATH_MAP",
    "PYTHONPATH",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "SystemRoot",
    "ComSpec",
}


def build_process_env(
    *,
    path_service: "PathService",
    thread_id: str,
    base_env: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base_env if base_env is not None else os.environ)
    if os.name == "nt":
        env.setdefault("SystemRoot", os.environ.get("SystemRoot", r"C:\Windows"))
        env.setdefault("ComSpec", os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"))

    pythonpath = [str(python_virtual_path_shim_dir())]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])

    env.update(
        {
            "ANVIL_WORKSPACE": str(path_service.thread_workspace_dir(thread_id)),
            "ANVIL_UPLOADS": str(path_service.thread_uploads_dir(thread_id)),
            "ANVIL_OUTPUTS": str(path_service.thread_outputs_dir(thread_id)),
            "ANVIL_SCRATCH": str(path_service.thread_scratch_dir(thread_id)),
            "ANVIL_VIRTUAL_PATH_MAP": json.dumps(path_service.virtual_path_map(thread_id), ensure_ascii=False),
            "PYTHONPATH": os.pathsep.join(pythonpath),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    env.update(
        {
            str(key): str(value)
            for key, value in (extra_env or {}).items()
            if str(key) not in RESERVED_PROCESS_ENV_KEYS
        }
    )
    return env


def python_virtual_path_shim_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "sandbox" / "python_virtual_path_shim"
