from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_generate_contracts_check_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate-contracts.py"), "--check"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (REPO_ROOT / "backend" / "app" / "generated" / "contracts.schema.json").exists()
    assert (REPO_ROOT / "frontend" / "src" / "core" / "contracts.generated.ts").exists()


def test_generated_backend_packaging_artifacts_are_gitignored() -> None:
    build_ignore = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "backend/build/lib/example.py"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    egg_info_ignore = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "backend/example.egg-info/PKG-INFO"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert build_ignore.returncode == 0
    assert egg_info_ignore.returncode == 0


def test_run_backend_tests_windows_shim_uses_stable_repo_local_tmp(monkeypatch) -> None:
    script_path = REPO_ROOT / "scripts" / "run-backend-tests.py"
    spec = importlib.util.spec_from_file_location("run_backend_tests", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fake_call(command, *, cwd, env):
        assert cwd == module.BACKEND_ROOT
        assert env[module.BACKEND_TEST_TMP_ENV] == str(module.BACKEND_TEST_TMP)
        assert env["TMP"] == str(module.BACKEND_TEST_TMP)
        assert env["TEMP"] == str(module.BACKEND_TEST_TMP)
        assert env["TMPDIR"] == str(module.BACKEND_TEST_TMP)
        assert str(module.BACKEND_TEST_TMP / module.BACKEND_TEST_SHIM_NAME) in env["PYTHONPATH"]
        assert command[:3] == [sys.executable, "-m", "pytest"]
        assert command[3:5] == ["-p", "no:cacheprovider"]
        return 0

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.delenv(module.BACKEND_TEST_TMP_ENV, raising=False)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **_: pytest.fail("Windows shim must not use random mkdtemp"))
    monkeypatch.setattr(module, "_assert_test_tmp_usable", lambda root: None)
    monkeypatch.setattr(module.subprocess, "call", fake_call)

    assert module.main(["tests/test_tool_registry.py", "-q"]) == 0
    assert module.BACKEND_TEST_SHIM == module.BACKEND_TEST_TMP / "pytest-shim"


def test_run_backend_tests_falls_back_when_repo_tmp_rejects_sqlite(monkeypatch, contract_tmp_path) -> None:
    script_path = REPO_ROOT / "scripts" / "run-backend-tests.py"
    spec = importlib.util.spec_from_file_location("run_backend_tests", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fallback_root = contract_tmp_path / "system-temp" / "anvil-backend-tests"
    attempts: list[Path] = []

    def fake_assert_test_tmp_usable(root: Path) -> None:
        attempts.append(root)
        if root == module.BACKEND_TEST_TMP:
            raise OSError("disk I/O error")

    def fake_call(command, *, cwd, env):
        assert env[module.BACKEND_TEST_TMP_ENV] == str(fallback_root)
        assert env["TMP"] == str(fallback_root)
        assert env["TEMP"] == str(fallback_root)
        assert env["TMPDIR"] == str(fallback_root)
        assert str(fallback_root / module.BACKEND_TEST_SHIM_NAME) in env["PYTHONPATH"]
        assert command[:3] == [sys.executable, "-m", "pytest"]
        return 0

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.delenv(module.BACKEND_TEST_TMP_ENV, raising=False)
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(contract_tmp_path / "system-temp"))
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **_: pytest.fail("Windows shim must not use random mkdtemp"))
    monkeypatch.setattr(module, "_assert_test_tmp_usable", fake_assert_test_tmp_usable)
    monkeypatch.setattr(module.subprocess, "call", fake_call)

    assert module.main(["tests/test_sandbox_provider.py", "-q"]) == 0
    assert attempts == [module.BACKEND_TEST_TMP, fallback_root]


def test_conftest_tmp_selector_falls_back_when_repo_tmp_rejects_sqlite(monkeypatch, contract_tmp_path) -> None:
    import conftest as backend_conftest

    fallback_root = (contract_tmp_path / "system-temp" / "anvil-backend-tests").resolve()
    attempts: list[Path] = []

    def fake_assert_local_tmp_usable(root: Path) -> None:
        attempts.append(root)
        if root == backend_conftest.BACKEND_ROOT / ".pytest_tmp":
            raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.delenv(backend_conftest.BACKEND_TEST_TMP_ENV, raising=False)
    monkeypatch.setattr(backend_conftest.tempfile, "gettempdir", lambda: str(contract_tmp_path / "system-temp"))
    monkeypatch.setattr(backend_conftest, "_assert_local_tmp_usable", fake_assert_local_tmp_usable)

    assert backend_conftest._select_local_tmp() == fallback_root
    assert attempts == [backend_conftest.BACKEND_ROOT / ".pytest_tmp", fallback_root]


def test_contract_tmp_path_supports_sqlite_databases(contract_tmp_path) -> None:
    sqlite_path = contract_tmp_path / "probe.sqlite3"
    connection = sqlite3.connect(sqlite_path)
    try:
        connection.execute("CREATE TABLE probe (value TEXT NOT NULL)")
        connection.execute("INSERT INTO probe(value) VALUES (?)", ("ok",))
        connection.commit()
        assert connection.execute("SELECT value FROM probe").fetchone() == ("ok",)
    finally:
        connection.close()


def test_clean_dev_artifacts_removes_generated_artifacts_and_keeps_persistent_paths(contract_tmp_path, monkeypatch) -> None:
    script_path = REPO_ROOT / "scripts" / "clean-dev-artifacts.py"
    spec = importlib.util.spec_from_file_location("clean_dev_artifacts", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    repo_root = contract_tmp_path / "cleanup-repo"
    (repo_root / "backend" / "build" / "lib").mkdir(parents=True, exist_ok=True)
    (repo_root / "backend" / ".pytest_cache").mkdir(parents=True, exist_ok=True)
    (repo_root / "backend" / ".pytest_tmp").mkdir(parents=True, exist_ok=True)
    (repo_root / "backend" / "_tmp_test").mkdir(parents=True, exist_ok=True)
    (repo_root / "backend" / "pytest-cache-files-stale").mkdir(parents=True, exist_ok=True)
    (repo_root / "backend" / ".anvil").mkdir(parents=True, exist_ok=True)
    (repo_root / ".venv").mkdir(parents=True, exist_ok=True)
    (repo_root / "frontend" / "coverage").mkdir(parents=True, exist_ok=True)
    (repo_root / "frontend" / ".next").mkdir(parents=True, exist_ok=True)
    (repo_root / "frontend" / "node_modules" / ".vite").mkdir(parents=True, exist_ok=True)
    (repo_root / "frontend" / "node_modules" / "keep").mkdir(parents=True, exist_ok=True)
    (repo_root / "backend" / "_tmp_test" / "nested").mkdir(parents=True, exist_ok=True)

    tracked_file = repo_root / "backend" / "build" / "lib" / "tracked.txt"
    tracked_file.write_text("tracked", encoding="utf-8")
    (repo_root / "backend" / ".pytest_cache" / "cache.json").write_text("{}", encoding="utf-8")
    (repo_root / "backend" / ".pytest_tmp" / "tmp.txt").write_text("tmp", encoding="utf-8")
    (repo_root / "backend" / "_tmp_test" / "tmp.txt").write_text("tmp", encoding="utf-8")
    (repo_root / "backend" / "pytest-cache-files-stale" / "tmp.txt").write_text("tmp", encoding="utf-8")
    (repo_root / "backend" / ".anvil" / "state.json").write_text("{}", encoding="utf-8")
    (repo_root / ".venv" / "pyvenv.cfg").write_text("home = python", encoding="utf-8")
    (repo_root / "frontend" / "coverage" / "summary.json").write_text("{}", encoding="utf-8")
    (repo_root / "frontend" / ".next" / "build.txt").write_text("build", encoding="utf-8")
    (repo_root / "frontend" / "node_modules" / ".vite" / "cache.txt").write_text("vite", encoding="utf-8")
    (repo_root / "frontend" / "node_modules" / "keep" / "index.js").write_text("keep", encoding="utf-8")
    readonly_file = repo_root / "backend" / "_tmp_test" / "nested" / "readonly.txt"
    readonly_file.write_text("locked", encoding="utf-8")
    os.chmod(readonly_file, stat.S_IREAD)

    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "add", "backend/build/lib/tracked.txt"], cwd=repo_root, check=True, capture_output=True)

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)

    assert module.main() == 0
    assert not tracked_file.exists()
    assert not (repo_root / "backend" / "build").exists()
    assert not (repo_root / "backend" / ".pytest_cache").exists()
    assert not (repo_root / "backend" / ".pytest_tmp").exists()
    assert not (repo_root / "backend" / "_tmp_test").exists()
    assert not (repo_root / "backend" / "pytest-cache-files-stale").exists()
    assert not (repo_root / "frontend" / "coverage").exists()
    assert not (repo_root / "frontend" / ".next").exists()
    assert not (repo_root / "frontend" / "node_modules" / ".vite").exists()
    assert (repo_root / "backend" / ".anvil").exists()
    assert (repo_root / ".venv").exists()
    assert (repo_root / "frontend" / "node_modules" / "keep" / "index.js").exists()
