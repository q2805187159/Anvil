import argparse
import json
import os
import shutil
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
    if selected:
        selected_set = set(selected)
        plan = [stage for stage in plan if stage.stage_id in selected_set]
        missing = selected_set - {stage.stage_id for stage in plan}
        if missing:
            raise ValueError(f"unknown selected stage(s): {', '.join(sorted(missing))}")
    if skipped:
        skipped_set = set(skipped)
        known_ids = {stage.stage_id for stage in plan}
        missing = skipped_set - known_ids
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
            completed = subprocess.run(
                list(_subprocess_command(stage.command, env)),
                cwd=stage.cwd,
                env=env,
                capture_output=bool(args.json),
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
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
        _frontend_process_preflight_stage(),
        _frontend_tests_stage(npx),
        _frontend_typecheck_stage(npx),
        _local_smoke_stage(python),
    ]


def _full_stages(*, python: str, npm: str, npx: str) -> list[ReleaseStage]:
    return [
        _docker_mount_safety_stage(python),
        _contracts_stage(python),
        ReleaseStage(
            stage_id="backend-full",
            description="Run the full backend test suite through the sandbox-stable wrapper.",
            command=(python, "scripts/run-backend-tests.py", "-q"),
        ),
        _frontend_process_preflight_stage(),
        _frontend_tests_stage(npx),
        _frontend_typecheck_stage(npx),
        ReleaseStage(
            stage_id="frontend-build",
            description="Build the frontend release artifact.",
            command=(npm, "run", "build"),
            cwd=FRONTEND_ROOT,
        ),
        ReleaseStage(
            stage_id="docs-build",
            description="Build the release documentation site.",
            command=(python, "-m", "mkdocs", "build"),
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


def _frontend_tests_stage(npx: str) -> ReleaseStage:
    return ReleaseStage(
        stage_id="frontend-tests",
        description="Run frontend unit/component tests.",
        command=(npx, "vitest", "run"),
        cwd=FRONTEND_ROOT,
    )


def _frontend_process_preflight_stage() -> ReleaseStage:
    return ReleaseStage(
        stage_id="frontend-process-preflight",
        description="Verify Node can spawn child processes and esbuild can start before Vitest runs.",
        command=("node", "scripts/frontend-process-preflight.cjs"),
        cwd=FRONTEND_ROOT,
    )


def _frontend_typecheck_stage(npx: str) -> ReleaseStage:
    return ReleaseStage(
        stage_id="frontend-typecheck",
        description="Run frontend TypeScript typecheck without Next typegen side effects.",
        command=(npx, "tsc", "--noEmit", "--pretty", "false"),
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
    return (executable, *command[1:])


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
