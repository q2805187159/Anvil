from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from packaging_helpers import copy_backend_source_for_packaging, packaging_env


def test_packaging_source_copy_excludes_local_virtualenv(contract_tmp_path: Path) -> None:
    backend_root = contract_tmp_path / "backend"
    app_package = backend_root / "app"
    virtualenv_package = backend_root / ".venv" / "Lib" / "site-packages"
    app_package.mkdir(parents=True)
    virtualenv_package.mkdir(parents=True)
    (app_package / "__init__.py").write_text("", encoding="utf-8")
    (virtualenv_package / "slow_copy_sentinel.py").write_text("SENTINEL = True\n", encoding="utf-8")

    package_root = copy_backend_source_for_packaging(backend_root, contract_tmp_path)

    assert (package_root / "app" / "__init__.py").exists()
    assert not (package_root / ".venv").exists()


def test_installed_artifact_declares_release_entrypoints(contract_tmp_path: Path) -> None:
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

    dist_info = next(target.glob("anvil_backend-*.dist-info"))
    entry_points = (dist_info / "entry_points.txt").read_text(encoding="utf-8")
    assert "anvil" in entry_points
    assert "anvil-gateway" in entry_points
    assert "anvil-shell" in entry_points
    assert "anvil-doctor" in entry_points
    assert "anvil-smoke" in entry_points


def test_installed_artifact_imports_doctor_and_smoke_modules(contract_tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    package_root = copy_backend_source_for_packaging(backend_root, contract_tmp_path)
    target = contract_tmp_path / "pkg"
    env = packaging_env(contract_tmp_path)
    source_paths = {
        str(backend_root.resolve()),
        str((backend_root / "packages" / "harness").resolve()),
    }

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
    smoke = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; "
                f"_sources = {source_paths!r}; "
                f"sys.path = [{str(target)!r}] + "
                "[p for p in sys.path if p and str(Path(p).resolve()) not in _sources]; "
                "from app.doctor import collect_doctor_report; "
                "from app.smoke import run_local_smoke; "
                "print(bool(collect_doctor_report().checks), run_local_smoke().ok())"
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
    assert smoke.returncode == 0, smoke.stderr or smoke.stdout
    assert "True True" in smoke.stdout
