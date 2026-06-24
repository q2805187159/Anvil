import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
HARNESS_ROOT = BACKEND_ROOT / "packages" / "harness"
FRONTEND_ROOT = REPO_ROOT / "frontend"
DEFAULT_STAGE_TIMEOUT_SECONDS = 600.0
# Keep deterministic file-modulo shards below the release timeout even when
# stream/runtime-heavy test modules land in the same full-suite window.
BACKEND_FULL_SHARD_COUNT = 16
BACKEND_FULL_STAGE_ALIAS = "backend-full"


@dataclass(frozen=True)
class ReleaseStage:
    stage_id: str
    description: str
    command: tuple[str, ...]
    cwd: Path = REPO_ROOT
    env_updates: dict[str, str] = field(default_factory=dict)


def build_plan(
    *,
    profile: str,
    selected: Sequence[str],
    skipped: Sequence[str],
    python: str,
    npm: str,
    npx: str,
) -> list[ReleaseStage]:
    stages_by_profile = {
        "quick": _quick_stages(python=python, npm=npm, npx=npx),
        "full": _full_stages(python=python, npm=npm, npx=npx),
    }
    if profile not in stages_by_profile:
        raise ValueError(f"unknown profile: {profile}")
    plan = stages_by_profile[profile]
    known_ids = {stage.stage_id for stage in plan}
    if selected:
        selected_set, missing = _expand_stage_selectors(selected, known_ids)
        if missing:
            raise ValueError(f"unknown selected stage(s): {', '.join(sorted(missing))}")
        plan = [stage for stage in plan if stage.stage_id in selected_set]
    if skipped:
        known_ids = {stage.stage_id for stage in plan}
        skipped_set, missing = _expand_stage_selectors(skipped, known_ids)
        if missing:
            raise ValueError(f"unknown skipped stage(s): {', '.join(sorted(missing))}")
        plan = [stage for stage in plan if stage.stage_id not in skipped_set]
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Anvil release readiness gates with selectable stages and a machine-readable summary."
    )
    parser.add_argument("--profile", choices=("quick", "full"), default="quick")
    parser.add_argument("--stage", action="append", default=[], help="Run only this stage id. May be repeated.")
    parser.add_argument("--skip", action="append", default=[], help="Skip this stage id. May be repeated.")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned stages without executing commands.")
    parser.add_argument("--json", action="store_true", help="Emit a JSON summary instead of text lines.")
    parser.add_argument("--python", default=sys.executable, help="Python executable to use for Python gates.")
    parser.add_argument("--npm", default="npm", help="npm executable to use for package-script gates.")
    parser.add_argument("--npx", default="npx", help="npx executable to use for frontend binary gates.")
    parser.add_argument(
        "--stage-timeout-seconds",
        type=float,
        default=DEFAULT_STAGE_TIMEOUT_SECONDS,
        help="Maximum seconds to allow a single readiness stage to run before reporting a timeout.",
    )
    args = parser.parse_args(argv)

    try:
        plan = build_plan(
            profile=args.profile,
            selected=tuple(args.stage),
            skipped=tuple(args.skip),
            python=args.python,
            npm=args.npm,
            npx=args.npx,
        )
    except ValueError as exc:
        if args.json:
            _emit_json({"ok": False, "error": str(exc), "stages": []})
        else:
            print(f"release readiness plan error: {exc}", file=sys.stderr)
        return 2

    results: list[dict[str, object]] = []
    ok = True
    for stage in plan:
        if args.dry_run:
            results.append(_stage_result(stage, status="planned", returncode=None, elapsed_seconds=0.0))
            continue
        started = time.perf_counter()
        env = dict(os.environ)
        env.update(stage.env_updates)
        try:
            completed = _run_stage_process(
                list(_subprocess_command(stage.command, env)),
                cwd=stage.cwd,
                env=env,
                capture_output=bool(args.json),
                timeout=args.stage_timeout_seconds,
            )
        except FileNotFoundError as exc:
            elapsed = time.perf_counter() - started
            ok = False
            results.append(
                _stage_result(
                    stage,
                    status="failed",
                    returncode=None,
                    elapsed_seconds=elapsed,
                    stderr=f"command not found: {exc.filename or stage.command[0]}",
                )
            )
            break
        except subprocess.TimeoutExpired as exc:
            elapsed = time.perf_counter() - started
            ok = False
            stderr = f"stage timed out after {args.stage_timeout_seconds:g} seconds"
            timeout_stderr = _stringify_stream(exc.stderr)
            if timeout_stderr:
                stderr = f"{stderr}\n{timeout_stderr}"
            results.append(
                _stage_result(
                    stage,
                    status="timed_out",
                    returncode=None,
                    elapsed_seconds=elapsed,
                    stdout=_stringify_stream(exc.output),
                    stderr=stderr,
                )
            )
            break
        elapsed = time.perf_counter() - started
        status = "passed" if completed.returncode == 0 else "failed"
        ok = ok and completed.returncode == 0
        results.append(
            _stage_result(
                stage,
                status=status,
                returncode=completed.returncode,
                elapsed_seconds=elapsed,
                stdout=completed.stdout if args.json else "",
                stderr=completed.stderr if args.json else "",
            )
        )
        if not args.json:
            print(f"[{status}] {stage.stage_id} ({elapsed:.2f}s)")
        if completed.returncode != 0:
            break

    payload = {"ok": ok, "profile": args.profile, "dry_run": args.dry_run, "stages": results}
    if args.json:
        _emit_json(payload)
    return 0 if ok else 1


def _quick_stages(*, python: str, npm: str, npx: str) -> list[ReleaseStage]:
    return [
        _docker_mount_safety_stage(python),
        _contracts_stage(python),
        ReleaseStage(
            stage_id="backend-release-smoke",
            description="Run release-facing backend smoke and packaging tests.",
            command=(
                python,
                "scripts/run-backend-tests.py",
                "tests/test_doctor_smoke.py",
                "tests/test_release_entrypoints.py",
                "tests/test_sdk_packaging_smoke.py",
                "-q",
            ),
        ),
        _backend_v2_runtime_stage(python),
        _hcms_benchmark_stage(python, iterations=30),
        _frontend_process_preflight_stage(),
        _frontend_tests_stage(npm),
        _frontend_typecheck_stage(npm),
        _local_smoke_stage(python),
    ]


def _full_stages(*, python: str, npm: str, npx: str) -> list[ReleaseStage]:
    return [
        _docker_mount_safety_stage(python),
        _contracts_stage(python),
        *_backend_full_stages(python),
        _hcms_benchmark_stage(python, iterations=120),
        _frontend_process_preflight_stage(),
        _frontend_tests_stage(npm),
        _frontend_typecheck_stage(npm),
        ReleaseStage(
            stage_id="frontend-build",
            description="Build the frontend release artifact.",
            command=(npm, "run", "build"),
            cwd=FRONTEND_ROOT,
        ),
        ReleaseStage(
            stage_id="docs-build",
            description="Build the release documentation site.",
            command=(python, "scripts/build-release-docs.py"),
        ),
        _local_smoke_stage(python),
    ]


def _docker_mount_safety_stage(python: str) -> ReleaseStage:
    return ReleaseStage(
        stage_id="docker-mount-safety",
        description="Fail if compose mounts source-tree paths rw into agent-writable targets.",
        command=(python, "scripts/check-docker-mount-safety.py"),
    )


def _contracts_stage(python: str) -> ReleaseStage:
    return ReleaseStage(
        stage_id="contracts",
        description="Check backend schema and generated frontend contracts are in sync.",
        command=(python, "scripts/generate-contracts.py", "--check"),
    )


def _backend_v2_runtime_stage(python: str) -> ReleaseStage:
    return ReleaseStage(
        stage_id="backend-v2-runtime",
        description="Run focused HCMS V2 and Runtime Context V2 backend release tests.",
        command=(
            python,
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
        ),
    )


def _hcms_benchmark_stage(python: str, *, iterations: int) -> ReleaseStage:
    return ReleaseStage(
        stage_id="hcms-benchmark",
        description="Run deterministic HCMS V2 recall, latency, degraded-stream, and negative-retrieval gates.",
        command=(
            python,
            "scripts/run-hcms-benchmark-report.py",
            "--iterations",
            str(iterations),
            "--fail-under-recall",
            "0.85",
            "--fail-over-p95-ms",
            "200",
        ),
    )


def _backend_full_stages(python: str) -> list[ReleaseStage]:
    return [
        ReleaseStage(
            stage_id=f"{BACKEND_FULL_STAGE_ALIAS}-{index}",
            description=(
                "Run a deterministic shard of the full backend test suite through "
                "the sandbox-stable wrapper."
            ),
            command=(
                python,
                "scripts/run-backend-tests.py",
                "--backend-shard-index",
                str(index),
                "--backend-shard-count",
                str(BACKEND_FULL_SHARD_COUNT),
                "-q",
            ),
        )
        for index in range(1, BACKEND_FULL_SHARD_COUNT + 1)
    ]


def _expand_stage_selectors(selectors: Sequence[str], known_ids: set[str]) -> tuple[set[str], set[str]]:
    aliases = _stage_aliases()
    expanded: set[str] = set()
    missing: set[str] = set()
    for selector in selectors:
        alias_targets = aliases.get(selector)
        if selector in known_ids:
            expanded.add(selector)
            continue
        if alias_targets:
            present_targets = [target for target in alias_targets if target in known_ids]
            if present_targets:
                expanded.update(present_targets)
                continue
        missing.add(selector)
    return expanded, missing


def _stage_aliases() -> dict[str, tuple[str, ...]]:
    return {
        BACKEND_FULL_STAGE_ALIAS: tuple(
            f"{BACKEND_FULL_STAGE_ALIAS}-{index}" for index in range(1, BACKEND_FULL_SHARD_COUNT + 1)
        )
    }


def _frontend_tests_stage(npm: str) -> ReleaseStage:
    return ReleaseStage(
        stage_id="frontend-tests",
        description="Run frontend unit/component tests through the package script.",
        command=(npm, "test"),
        cwd=FRONTEND_ROOT,
    )


def _frontend_process_preflight_stage() -> ReleaseStage:
    return ReleaseStage(
        stage_id="frontend-process-preflight",
        description="Verify the frontend package-script test environment before Vitest runs.",
        command=("node", "scripts/frontend-process-preflight.cjs"),
        cwd=FRONTEND_ROOT,
    )


def _frontend_typecheck_stage(npm: str) -> ReleaseStage:
    return ReleaseStage(
        stage_id="frontend-typecheck",
        description="Run frontend route type generation and TypeScript typecheck through the package script.",
        command=(npm, "run", "typecheck"),
        cwd=FRONTEND_ROOT,
    )


def _local_smoke_stage(python: str) -> ReleaseStage:
    return ReleaseStage(
        stage_id="local-smoke",
        description="Run fake-model local smoke through app.smoke.",
        command=(python, "-m", "app.smoke", "local"),
        cwd=BACKEND_ROOT,
        env_updates={"PYTHONPATH": os.pathsep.join([str(BACKEND_ROOT), str(HARNESS_ROOT)])},
    )


def _stage_result(
    stage: ReleaseStage,
    *,
    status: str,
    returncode: int | None,
    elapsed_seconds: float,
    stdout: str = "",
    stderr: str = "",
) -> dict[str, object]:
    result: dict[str, object] = {
        "stage_id": stage.stage_id,
        "description": stage.description,
        "status": status,
        "returncode": returncode,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "cwd": str(stage.cwd),
        "command": list(stage.command),
    }
    if stdout:
        result["stdout"] = stdout
    if stderr:
        result["stderr"] = stderr
    return result


def _subprocess_command(command: tuple[str, ...], env: dict[str, str]) -> tuple[str, ...]:
    if not command:
        return command
    executable = shutil.which(command[0], path=env.get("PATH")) or _find_windows_powershell_shim(command[0], env)
    if os.name != "nt" or not executable:
        return command
    lowered = executable.lower()
    if lowered.endswith(".ps1"):
        return (_powershell_executable(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", executable, *command[1:])
    if lowered.endswith((".cmd", ".bat")):
        return (os.environ.get("ComSpec") or "cmd.exe", "/d", "/c", executable, *command[1:])
    return command


def _run_stage_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    capture_output: bool,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    stdout = subprocess.PIPE if capture_output else None
    stderr = subprocess.PIPE if capture_output else None
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    start_new_session = os.name != "nt"
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    try:
        stdout_text, stderr_text = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process.pid)
        final_stdout, final_stderr = process.communicate()
        output = _join_streams(_stringify_stream(exc.output), final_stdout or "")
        stderr_output = _join_streams(_stringify_stream(exc.stderr), final_stderr or "")
        raise subprocess.TimeoutExpired(command, timeout=timeout, output=output, stderr=stderr_output) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout_text or "", stderr_text or "")


def _terminate_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _join_streams(first: str, second: str) -> str:
    parts = [part for part in (first, second) if part]
    return "\n".join(parts)


def _stringify_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _find_windows_powershell_shim(name: str, env: dict[str, str]) -> str | None:
    if os.name != "nt" or any(separator in name for separator in ("/", "\\")):
        return None
    for raw_dir in env.get("PATH", "").split(os.pathsep):
        if not raw_dir:
            continue
        candidate = Path(raw_dir) / f"{name}.ps1"
        if candidate.exists():
            return str(candidate)
    return None


def _powershell_executable() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def _emit_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
