from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from anvil import ConfigService, TracingSettings, build_default_config_layers, resolve_config_path
from anvil.config.env_refs import env_ref_name, is_env_ref
from app.shell.profile import get_anvil_home


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    remediation: str | None = None


@dataclass
class DoctorReport:
    checks: list[DoctorCheck] = field(default_factory=list)

    def ok(self) -> bool:
        return all(check.status != "fail" for check in self.checks)


def collect_doctor_report(*, config_path: str | Path | None = None) -> DoctorReport:
    report = DoctorReport()
    python_ok = sys.version_info >= (3, 12)
    report.checks.append(
        DoctorCheck(
            name="python",
            status="ok" if python_ok else "fail",
            detail=f"Python {platform.python_version()}",
            remediation="Install Python 3.12+." if not python_ok else None,
        )
    )

    backend_imports_ok = importlib.util.find_spec("anvil") is not None and importlib.util.find_spec("app.sdk") is not None
    report.checks.append(
        DoctorCheck(
            name="backend_imports",
            status="ok" if backend_imports_ok else "fail",
            detail="backend packages importable" if backend_imports_ok else "backend package imports are broken",
            remediation="Install the backend package or fix PYTHONPATH." if not backend_imports_ok else None,
        )
    )

    node_present = shutil.which("node") is not None
    npm_present = shutil.which("npm") is not None
    report.checks.append(
        DoctorCheck(
            name="node",
            status="ok" if node_present else "warn",
            detail="node found" if node_present else "node not found",
            remediation="Install Node.js 22+ if you want the frontend." if not node_present else None,
        )
    )
    report.checks.append(
        DoctorCheck(
            name="npm",
            status="ok" if npm_present else "warn",
            detail="npm found" if npm_present else "npm not found",
            remediation="Install npm if you want the frontend." if not npm_present else None,
        )
    )

    resolved_config_path = resolve_config_path(config_path)
    config_result = ConfigService().resolve(build_default_config_layers(config_path=config_path))
    model_ready = bool(config_result.effective_config.default_model and config_result.effective_config.models)
    report.checks.append(
        DoctorCheck(
            name="model_config",
            status="ok" if model_ready else "warn",
            detail=(
                f"default model: {config_result.effective_config.default_model}"
                if model_ready
                else "no default model configured via config.yaml or ANVIL_* fallback"
            ),
            remediation=(
                "Create config.yaml from config.example.yaml, or set ANVIL_* fallback variables."
                if not model_ready
                else None
            ),
        )
    )
    report.checks.append(
        DoctorCheck(
            name="config_path",
            status="ok" if resolved_config_path is not None else "warn",
            detail=str(resolved_config_path) if resolved_config_path is not None else "no config.yaml found; env bootstrap fallback will be used",
            remediation="Run scripts/init-config.ps1 or scripts/init-config.sh to create config.yaml." if resolved_config_path is None else None,
        )
    )
    report.checks.append(
        DoctorCheck(
            name="available_models",
            status="ok" if config_result.effective_config.models else "warn",
            detail=(
                "available models: " + ", ".join(sorted(config_result.effective_config.models))
                if config_result.effective_config.models
                else "no models are currently configured"
            ),
        )
    )

    missing_secret_envs = _collect_missing_model_secret_envs(config_result)
    report.checks.append(
        DoctorCheck(
            name="model_secrets",
            status="ok" if not missing_secret_envs else "warn",
            detail=(
                "all env-backed model secrets are present"
                if not missing_secret_envs
                else "; ".join(
                    f"{model_name}: missing {', '.join(env_names)}"
                    for model_name, env_names in sorted(missing_secret_envs.items())
                )
            ),
            remediation=(
                "Set the missing environment variables in .env or the current shell."
                if missing_secret_envs
                else None
            ),
        )
    )

    gateway_url = importlib.util.find_spec("app.gateway.app") is not None
    report.checks.append(
        DoctorCheck(
            name="frontend_gateway_env",
            status="ok" if gateway_url else "warn",
            detail="frontend can use NEXT_PUBLIC_ANVIL_GATEWAY_URL or default to http://127.0.0.1:18000",
        )
    )

    tracing_settings = TracingSettings.from_env_and_config(
        config_result.effective_config.additional_settings.get("tracing")
    )
    langsmith_installed = importlib.util.find_spec("langsmith") is not None
    report.checks.append(
        DoctorCheck(
            name="langsmith",
            status="ok" if (not tracing_settings.enabled or langsmith_installed) else "warn",
            detail=(
                f"LangSmith tracing enabled via {tracing_settings.enabled_source or 'config'} and package importable"
                if tracing_settings.enabled and langsmith_installed
                else "LangSmith tracing disabled"
                if not tracing_settings.enabled
                else "LangSmith env enabled but package is not installed"
            ),
            remediation=(
                "Install the langsmith package or disable tracing."
                if tracing_settings.enabled and not langsmith_installed
                else None
            ),
        )
    )

    anvil_home = get_anvil_home()
    report.checks.append(
        DoctorCheck(
            name="shell_home",
            status="ok",
            detail=f"default shell home: {anvil_home}",
        )
    )
    return report


def render_doctor_report(report: DoctorReport) -> str:
    lines = []
    for check in report.checks:
        prefix = {"ok": "[ok]", "warn": "[warn]", "fail": "[fail]"}[check.status]
        lines.append(f"{prefix} {check.name}: {check.detail}")
        if check.remediation:
            lines.append(f"      remediation: {check.remediation}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run Anvil environment diagnostics.")
    parser.add_argument("--config", dest="config_path", default=None)
    args = parser.parse_args(argv)
    report = collect_doctor_report(config_path=args.config_path)
    print(render_doctor_report(report))
    raise SystemExit(0 if report.ok() else 1)


def _collect_missing_model_secret_envs(config_result) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for model_name, model in config_result.effective_config.models.items():
        env_names: list[str] = []
        if model.api_key and is_env_ref(model.api_key):
            env_name = env_ref_name(model.api_key)
            if not os.getenv(env_name):
                env_names.append(env_name)
        if model.api_key_env and not os.getenv(model.api_key_env):
            env_names.append(model.api_key_env)
        if env_names:
            missing[model_name] = list(dict.fromkeys(env_names))
    return missing
if __name__ == "__main__":
    main()
