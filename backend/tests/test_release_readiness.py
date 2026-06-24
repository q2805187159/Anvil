from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


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
    backend_full_shards = [
        f"backend-full-{index}" for index in range(1, module.BACKEND_FULL_SHARD_COUNT + 1)
    ]

    plan = module.build_plan(profile="full", selected=(), skipped=(), python="py", npm="npm", npx="npx")

    assert module.BACKEND_FULL_SHARD_COUNT == 16
    assert [stage.stage_id for stage in plan] == [
        "docker-mount-safety",
        "contracts",
        *backend_full_shards,
        "hcms-benchmark",
        "frontend-process-preflight",
        "frontend-tests",
        "frontend-typecheck",
        "frontend-build",
        "docs-build",
        "local-smoke",
    ]
    assert plan[0].command == ("py", "scripts/check-docker-mount-safety.py")
    assert plan[1].command == ("py", "scripts/generate-contracts.py", "--check")
    assert plan[2].command == (
        "py",
        "scripts/run-backend-tests.py",
        "--backend-shard-index",
        "1",
        "--backend-shard-count",
        str(module.BACKEND_FULL_SHARD_COUNT),
        "-q",
    )
    benchmark_stage = next(stage for stage in plan if stage.stage_id == "hcms-benchmark")
    assert benchmark_stage.command == (
        "py",
        "scripts/run-hcms-benchmark-report.py",
        "--iterations",
        "120",
        "--fail-under-recall",
        "0.85",
        "--fail-over-p95-ms",
        "200",
    )
    preflight_stage = next(stage for stage in plan if stage.stage_id == "frontend-process-preflight")
    frontend_tests_stage = next(stage for stage in plan if stage.stage_id == "frontend-tests")
    frontend_typecheck_stage = next(stage for stage in plan if stage.stage_id == "frontend-typecheck")
    docs_build_stage = next(stage for stage in plan if stage.stage_id == "docs-build")
    local_smoke_stage = next(stage for stage in plan if stage.stage_id == "local-smoke")
    assert preflight_stage.cwd == module.FRONTEND_ROOT
    assert preflight_stage.command == ("node", "scripts/frontend-process-preflight.cjs")
    assert frontend_tests_stage.command == ("npm", "test")
    assert frontend_typecheck_stage.command == ("npm", "run", "typecheck")
    assert docs_build_stage.command == ("py", "scripts/build-release-docs.py")
    assert local_smoke_stage.cwd == module.BACKEND_ROOT
    assert local_smoke_stage.env_updates["PYTHONPATH"].startswith(str(module.BACKEND_ROOT))


def test_release_readiness_backend_full_selected_alias_expands_to_shards() -> None:
    module = _load_release_readiness_module()
    backend_full_shards = [
        f"backend-full-{index}" for index in range(1, module.BACKEND_FULL_SHARD_COUNT + 1)
    ]

    plan = module.build_plan(profile="full", selected=("backend-full",), skipped=(), python="py", npm="npm", npx="npx")

    assert [stage.stage_id for stage in plan] == backend_full_shards


def test_release_readiness_can_skip_backend_full_alias() -> None:
    module = _load_release_readiness_module()

    plan = module.build_plan(profile="full", selected=(), skipped=("backend-full",), python="py", npm="npm", npx="npx")

    assert "backend-full" not in {stage.stage_id for stage in plan}
    assert all(not stage.stage_id.startswith("backend-full-") for stage in plan)


def test_release_readiness_quick_plan_uses_targeted_release_smoke() -> None:
    module = _load_release_readiness_module()

    plan = module.build_plan(profile="quick", selected=(), skipped=(), python="py", npm="npm", npx="npx")

    assert [stage.stage_id for stage in plan] == [
        "docker-mount-safety",
        "contracts",
        "backend-release-smoke",
        "backend-v2-runtime",
        "hcms-benchmark",
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
    v2_stage = next(stage for stage in plan if stage.stage_id == "backend-v2-runtime")
    assert v2_stage.command == (
        "py",
        "scripts/run-backend-tests.py",
        "tests/test_hcms_v2.py",
        "tests/test_runtime_context_v2.py",
        "tests/test_runtime_state_v2.py",
        "tests/test_tool_output_budget_middleware.py",
        "tests/test_capability_bundle_service.py",
        "tests/test_skills_service.py",
        "tests/test_tool_registry.py",
        "tests/test_middleware_chain.py",
        "tests/test_prompt_assembly.py",
        "tests/test_trajectory_export.py",
        "tests/test_export_evaluation_report_script.py",
        "-k",
        "context_v2 or hcms or runtime_assembly or memory_context or capability "
        "or tool_budget or tool_output or skill_selection_feedback or capability_registry "
        "or salience or conflict_alert or evaluation_suite or trace_replay "
        "or evaluation_report or evaluation_batch_report",
        "-q",
    )
    benchmark_stage = next(stage for stage in plan if stage.stage_id == "hcms-benchmark")
    assert benchmark_stage.command == (
        "py",
        "scripts/run-hcms-benchmark-report.py",
        "--iterations",
        "30",
        "--fail-under-recall",
        "0.85",
        "--fail-over-p95-ms",
        "200",
    )
    preflight_stage = next(stage for stage in plan if stage.stage_id == "frontend-process-preflight")
    assert preflight_stage.command == ("node", "scripts/frontend-process-preflight.cjs")
    assert preflight_stage.cwd == module.FRONTEND_ROOT


def test_release_readiness_executes_selected_stages_and_reports_json(monkeypatch, capsys) -> None:
    module = _load_release_readiness_module()
    calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def fake_run(command, *, cwd, env, capture_output, timeout):
        assert timeout == module.DEFAULT_STAGE_TIMEOUT_SECONDS
        calls.append((tuple(command), Path(cwd), dict(env)))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(module, "_run_stage_process", fake_run)

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


def test_release_readiness_reports_stage_timeout_as_json_failure(monkeypatch, capsys) -> None:
    module = _load_release_readiness_module()

    def timeout_run(command, *, cwd, env, capture_output, timeout):
        assert timeout == 7.5
        raise subprocess.TimeoutExpired(command, timeout=timeout, output="partial stdout", stderr="partial stderr")

    monkeypatch.setattr(module, "_run_stage_process", timeout_run)

    result = module.main(
        [
            "--stage",
            "contracts",
            "--stage-timeout-seconds",
            "7.5",
            "--json",
            "--python",
            "py",
        ]
    )

    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["stages"][0]["stage_id"] == "contracts"
    assert payload["stages"][0]["status"] == "timed_out"
    assert payload["stages"][0]["returncode"] is None
    assert payload["stages"][0]["stdout"] == "partial stdout"
    assert "stage timed out after 7.5 seconds" in payload["stages"][0]["stderr"]
    assert "partial stderr" in payload["stages"][0]["stderr"]


def test_stage_process_timeout_terminates_process_tree(monkeypatch) -> None:
    module = _load_release_readiness_module()
    terminated: list[int] = []

    class FakeProcess:
        pid = 4242
        returncode = -9

        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                assert timeout == 0.25
                raise subprocess.TimeoutExpired(
                    ("py", "slow.py"),
                    timeout=timeout,
                    output="partial stdout",
                    stderr="partial stderr",
                )
            return ("final stdout", "final stderr")

    fake_process = FakeProcess()

    def fake_popen(command, **kwargs):
        assert command == ["py", "slow.py"]
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.PIPE
        assert kwargs["text"] is True
        return fake_process

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module, "_terminate_process_tree", lambda pid: terminated.append(pid))

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        module._run_stage_process(
            ["py", "slow.py"],
            cwd=module.REPO_ROOT,
            env={},
            capture_output=True,
            timeout=0.25,
        )

    assert terminated == [4242]
    assert exc_info.value.output == "partial stdout\nfinal stdout"
    assert exc_info.value.stderr == "partial stderr\nfinal stderr"


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

    monkeypatch.setattr(module, "_run_stage_process", missing_run)

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
