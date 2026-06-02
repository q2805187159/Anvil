from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_release_readiness_module():
    script_path = REPO_ROOT / "scripts" / "run-release-readiness.py"
    spec = importlib.util.spec_from_file_location("run_release_readiness", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_readiness_full_plan_contains_publish_gates() -> None:
    module = _load_release_readiness_module()

    plan = module.build_plan(profile="full", selected=(), skipped=(), python="py", npm="npm", npx="npx")

    assert [stage.stage_id for stage in plan] == [
        "docker-mount-safety",
        "contracts",
        "backend-full",
        "frontend-process-preflight",
        "frontend-tests",
        "frontend-typecheck",
        "frontend-build",
        "docs-build",
        "local-smoke",
    ]
    assert plan[0].command == ("py", "scripts/check-docker-mount-safety.py")
    assert plan[1].command == ("py", "scripts/generate-contracts.py", "--check")
    assert plan[2].command == ("py", "scripts/run-backend-tests.py", "-q")
    assert plan[3].cwd == module.FRONTEND_ROOT
    assert plan[3].command == ("node", "scripts/frontend-process-preflight.cjs")
    assert plan[4].command == ("npx", "vitest", "run")
    assert plan[5].command == ("npx", "tsc", "--noEmit", "--pretty", "false")
    assert plan[8].cwd == module.BACKEND_ROOT
    assert plan[8].env_updates["PYTHONPATH"].startswith(str(module.BACKEND_ROOT))


def test_release_readiness_quick_plan_uses_targeted_release_smoke() -> None:
    module = _load_release_readiness_module()

    plan = module.build_plan(profile="quick", selected=(), skipped=(), python="py", npm="npm", npx="npx")

    assert [stage.stage_id for stage in plan] == [
        "docker-mount-safety",
        "contracts",
        "backend-release-smoke",
        "frontend-process-preflight",
        "frontend-tests",
        "frontend-typecheck",
        "local-smoke",
    ]
    smoke_stage = next(stage for stage in plan if stage.stage_id == "backend-release-smoke")
    assert smoke_stage.command == (
        "py",
        "scripts/run-backend-tests.py",
        "tests/test_doctor_smoke.py",
        "tests/test_release_entrypoints.py",
        "tests/test_sdk_packaging_smoke.py",
        "-q",
    )
    preflight_stage = next(stage for stage in plan if stage.stage_id == "frontend-process-preflight")
    assert preflight_stage.command == ("node", "scripts/frontend-process-preflight.cjs")
    assert preflight_stage.cwd == module.FRONTEND_ROOT


def test_release_readiness_executes_selected_stages_and_reports_json(monkeypatch, capsys) -> None:
    module = _load_release_readiness_module()
    calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def fake_run(command, *, cwd, env, capture_output, text, encoding, errors, check):
        calls.append((tuple(command), Path(cwd), dict(env)))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.main(
        [
            "--stage",
            "contracts",
            "--stage",
            "local-smoke",
            "--json",
            "--python",
            "py",
            "--npx",
            "npx",
            "--npm",
            "npm",
        ]
    )

    assert result == 0
    assert [call[0] for call in calls] == [
        ("py", "scripts/generate-contracts.py", "--check"),
        ("py", "-m", "app.smoke", "local"),
    ]
    assert calls[0][1] == module.REPO_ROOT
    assert calls[1][1] == module.BACKEND_ROOT
    assert str(module.HARNESS_ROOT) in calls[1][2]["PYTHONPATH"]

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert [stage["stage_id"] for stage in payload["stages"]] == ["contracts", "local-smoke"]
    assert {stage["status"] for stage in payload["stages"]} == {"passed"}


def test_release_readiness_dry_run_does_not_execute(monkeypatch, capsys) -> None:
    module = _load_release_readiness_module()

    def fail_run(*_args, **_kwargs):
        raise AssertionError("dry-run must not execute commands")

    monkeypatch.setattr(module.subprocess, "run", fail_run)

    result = module.main(["--profile", "quick", "--dry-run", "--json"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert all(stage["status"] == "planned" for stage in payload["stages"])


def test_release_readiness_reports_missing_command_as_json_failure(monkeypatch, capsys) -> None:
    module = _load_release_readiness_module()

    def missing_run(command, **_kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(module.subprocess, "run", missing_run)

    result = module.main(["--stage", "contracts", "--json", "--python", "missing-python"])

    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["stages"][0]["stage_id"] == "contracts"
    assert payload["stages"][0]["status"] == "failed"
    assert payload["stages"][0]["returncode"] is None
    assert "missing-python" in payload["stages"][0]["stderr"]


def test_release_readiness_wraps_windows_powershell_shims(monkeypatch, contract_tmp_path) -> None:
    module = _load_release_readiness_module()
    shim = contract_tmp_path / "npx.ps1"
    shim.write_text("Write-Output ok", encoding="utf-8")

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module.shutil, "which", lambda name, path=None: str(shim) if name == "npx" else None)
    monkeypatch.setattr(module, "_powershell_executable", lambda: "pwsh")

    assert module._subprocess_command(("npx", "vitest", "run"), {}) == (
        "pwsh",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(shim),
        "vitest",
        "run",
    )


def test_release_readiness_resolves_windows_powershell_shim_from_path(monkeypatch, contract_tmp_path) -> None:
    module = _load_release_readiness_module()
    shim = contract_tmp_path / "npx.ps1"
    shim.write_text("Write-Output ok", encoding="utf-8")

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(module, "_powershell_executable", lambda: "pwsh")

    assert module._subprocess_command(("npx", "vitest", "run"), {"PATH": str(contract_tmp_path)}) == (
        "pwsh",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(shim),
        "vitest",
        "run",
    )


def test_release_readiness_wraps_windows_cmd_shims(monkeypatch, contract_tmp_path) -> None:
    module = _load_release_readiness_module()
    shim = contract_tmp_path / "npx.CMD"
    shim.write_text("@echo ok", encoding="utf-8")

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module.shutil, "which", lambda name, path=None: str(shim) if name == "npx" else None)
    monkeypatch.setenv("ComSpec", "cmd.exe")

    assert module._subprocess_command(("npx", "vitest", "run"), {}) == (
        "cmd.exe",
        "/d",
        "/c",
        str(shim),
        "vitest",
        "run",
    )
