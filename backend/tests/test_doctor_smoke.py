from __future__ import annotations

import json
from pathlib import Path
import os
import subprocess
import sys

from langchain_core.messages import AIMessage

from app.doctor import collect_doctor_report, render_doctor_report
from app.smoke import (
    BindableFakeMessagesListChatModel,
    render_smoke_report,
    run_local_smoke,
    run_provider_catalog_smoke,
    run_provider_smoke,
)


def test_doctor_report_contains_core_checks() -> None:
    report = collect_doctor_report()
    names = {check.name for check in report.checks}
    assert {"python", "backend_imports", "model_config", "langsmith", "shell_home"} <= names
    rendered = render_doctor_report(report)
    assert "python" in rendered
    assert "backend_imports" in rendered


def test_local_smoke_runs_without_real_provider_config() -> None:
    report = run_local_smoke()
    assert report.ok() is True
    rendered = render_smoke_report(report)
    assert "gateway_health" in rendered
    assert "embedded_sdk_run" in rendered


def test_doctor_reports_config_models_and_missing_secret_env(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: provider-one
models:
  - name: provider-one
    use: langchain_openai:ChatOpenAI
    model: gpt-5.4
    api_key: $MISSING_PROVIDER_KEY
        """.strip(),
        encoding="utf-8",
    )

    report = collect_doctor_report(config_path=config_path)
    checks = {check.name: check for check in report.checks}

    assert str(config_path) in checks["config_path"].detail
    assert "provider-one" in checks["available_models"].detail
    assert "MISSING_PROVIDER_KEY" in checks["model_secrets"].detail


def test_doctor_reports_braced_model_secret_env(contract_tmp_path: Path) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: provider-one
models:
  - name: provider-one
    use: langchain_openai:ChatOpenAI
    model: gpt-5.4
    api_key: ${MISSING_BRACED_PROVIDER_KEY}
        """.strip(),
        encoding="utf-8",
    )

    report = collect_doctor_report(config_path=config_path)
    checks = {check.name: check for check in report.checks}

    assert "MISSING_BRACED_PROVIDER_KEY" in checks["model_secrets"].detail
    assert "{MISSING_BRACED_PROVIDER_KEY}" not in checks["model_secrets"].detail


def test_provider_smoke_uses_explicit_config_and_model_selection(
    monkeypatch,
    contract_tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: provider-one
models:
  - name: provider-one
    use: langchain_openai:ChatOpenAI
    model: gpt-5.4
    api_key: $OPENAI_API_KEY
  - name: provider-two
    use: langchain_openai:ChatOpenAI
    model: gpt-4.1
    api_key: $OPENAI_API_KEY
        """.strip(),
        encoding="utf-8",
    )

    report = run_provider_smoke(
        config_path=config_path,
        model_name="provider-two",
        message="Reply with OK only.",
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="OK")]),
    )

    assert report.ok() is True
    rendered = render_smoke_report(report)
    assert "provider-two" in rendered


def test_provider_smoke_writes_evaluation_report_artifacts(
    monkeypatch,
    contract_tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config_path = contract_tmp_path / "config.yaml"
    report_json = contract_tmp_path / "reports" / "provider-smoke.json"
    report_md = contract_tmp_path / "reports" / "provider-smoke.md"
    config_path.write_text(
        """
default_model: provider-one
models:
  - name: provider-one
    use: langchain_openai:ChatOpenAI
    model: gpt-5.4
    api_key: $OPENAI_API_KEY
        """.strip(),
        encoding="utf-8",
    )

    report = run_provider_smoke(
        config_path=config_path,
        model_name="provider-one",
        message="Reply with OK only.",
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="OK")]),
        report_json_path=report_json,
        report_markdown_path=report_md,
    )

    assert report.ok() is True
    assert any(check.name == "evaluation_report" and check.status == "ok" for check in report.checks)
    assert any(artifact.kind == "evaluation_json" and artifact.path == str(report_json.resolve()) for artifact in report.artifacts)
    assert any(artifact.kind == "evaluation_markdown" and artifact.path == str(report_md.resolve()) for artifact in report.artifacts)
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert payload["summary"]["thread_count"] == 1
    assert payload["thread_reports"][0]["thread_id"] == "provider-smoke"
    assert payload["thread_reports"][0]["task_preview"] == "Reply with OK only."
    assert report_md.read_text(encoding="utf-8").startswith("# Anvil Evaluation Report")
    rendered = render_smoke_report(report)
    assert "evaluation_json" in rendered
    assert "evaluation_markdown" in rendered


def test_provider_catalog_smoke_skips_missing_secrets_and_runs_available_models(
    monkeypatch,
    contract_tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
default_model: provider-one
models:
  - name: provider-one
    use: langchain_openai:ChatOpenAI
    model: gpt-5.4
    api_key: $OPENAI_API_KEY
  - name: provider-two
    use: langchain_openai:ChatOpenAI
    model: gpt-4.1
    api_key: $MISSING_PROVIDER_TWO_KEY
  - name: gemini-native
    use: fake_chat_provider:CapturingChatModel
    model: gemini-2.5-pro
    provider_settings:
      gemini_api_key: $MISSING_GEMINI_KEY
        """.strip(),
        encoding="utf-8",
    )

    report = run_provider_catalog_smoke(
        config_path=config_path,
        message="Reply with OK only.",
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="OK")]),
    )

    assert any(check.name == "provider_run:provider-one" and check.status == "ok" for check in report.checks)
    assert any(check.name == "provider_secret:provider-two" and check.status == "skip" for check in report.checks)
    assert any(check.name == "provider_secret:gemini-native" and check.status == "skip" for check in report.checks)
    assert report.ok() is True


def test_python_m_app_smoke_executes_main() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([".", "packages/harness", env.get("PYTHONPATH", "")])
    result = subprocess.run(
        [sys.executable, "-m", "app.smoke", "local"],
        cwd=backend_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "gateway_health" in result.stdout


def test_python_m_app_smoke_writes_report_dir(contract_tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    report_dir = contract_tmp_path / "smoke-reports"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([".", "packages/harness", env.get("PYTHONPATH", "")])
    result = subprocess.run(
        [sys.executable, "-m", "app.smoke", "local", "--report-dir", str(report_dir)],
        cwd=backend_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "evaluation_report" in result.stdout
    assert (report_dir / "local-smoke.json").exists()
    assert (report_dir / "local-smoke.md").exists()


def test_python_m_app_doctor_executes_main() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([".", "packages/harness", env.get("PYTHONPATH", "")])
    result = subprocess.run(
        [sys.executable, "-m", "app.doctor"],
        cwd=backend_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "python" in result.stdout
