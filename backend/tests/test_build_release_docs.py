from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_docs_builder_module():
    script_path = REPO_ROOT / "scripts" / "build-release-docs.py"
    spec = importlib.util.spec_from_file_location("build_release_docs", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_docs_builder_uses_per_run_output_root(monkeypatch, contract_tmp_path) -> None:
    module = _load_docs_builder_module()

    monkeypatch.setattr(module, "release_docs_timestamp", lambda: "20260624T120000Z")
    monkeypatch.setattr(module.os, "getpid", lambda: 4242)

    assert module.choose_release_docs_site_dir(contract_tmp_path) == contract_tmp_path / "docs-20260624T120000Z-4242"


def test_release_docs_builder_invokes_mkdocs_with_isolated_site_dir(monkeypatch, contract_tmp_path) -> None:
    module = _load_docs_builder_module()
    calls: list[tuple[list[str], Path]] = []

    def fake_run(command, *, cwd):
        calls.append((list(command), Path(cwd)))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.main(["--site-dir", str(contract_tmp_path / "docs-out")])

    assert result == 0
    assert calls == [
        (
            [sys.executable, "-m", "mkdocs", "build", "--site-dir", str((contract_tmp_path / "docs-out").resolve())],
            module.REPO_ROOT,
        )
    ]