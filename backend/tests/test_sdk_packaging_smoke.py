from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from packaging_helpers import copy_backend_source_for_packaging, packaging_env


def test_sdk_imports_from_installed_artifact(contract_tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    package_root = copy_backend_source_for_packaging(backend_root, contract_tmp_path)
    target = contract_tmp_path / "pkg"
    env = packaging_env(contract_tmp_path)

    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "-t",
            str(target),
        ],
        cwd=package_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert install.returncode == 0, install.stderr or install.stdout

    env["PYTHONPATH"] = os.pathsep.join([str(target), env.get("PYTHONPATH", "")])
    import_smoke = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.path = [{str(target)!r}] + [p for p in sys.path if p and 'Anvil\\\\backend' not in p]; "
                "from app.sdk import EmbeddedClient; "
                "print(EmbeddedClient.__name__)"
            ),
        ],
        cwd=contract_tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert import_smoke.returncode == 0, import_smoke.stderr or import_smoke.stdout
    assert import_smoke.stdout.strip() == "EmbeddedClient"
