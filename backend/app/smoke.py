from __future__ import annotations

import argparse
from contextlib import contextmanager
import gc
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from app.contracts import EvaluationReportOptionsView, EvaluationReportRequestView
from app.gateway.deps import build_app_runtime_deps
from app.gateway.app import make_gateway_app
from app.sdk import EmbeddedClient, EmbeddedClientConfig, EmbeddedRunRequest
from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService, build_default_config_layers
from anvil.config.env_refs import env_ref_name, is_env_ref, iter_env_ref_names


class BindableFakeMessagesListChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class SmokeArtifact:
    kind: str
    path: str
    detail: str = ""


@dataclass
class SmokeReport:
    checks: list[SmokeCheck] = field(default_factory=list)
    artifacts: list[SmokeArtifact] = field(default_factory=list)

    def ok(self) -> bool:
        return all(check.status in {"ok", "skip"} for check in self.checks)


def run_local_smoke(
    *,
    config_path: str | Path | None = None,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    report_dir: str | Path | None = None,
) -> SmokeReport:
    report = SmokeReport()
    report_json_path, report_markdown_path = _resolve_smoke_report_paths(
        report_json_path=report_json_path,
        report_markdown_path=report_markdown_path,
        report_dir=report_dir,
        stem="local-smoke",
    )
    config_layers = _build_smoke_config_layers(config_path=config_path)
    config_result = ConfigService().resolve(config_layers)
    if not config_result.effective_config.default_model or not config_result.effective_config.models:
        config_layers = [
            ConfigLayer(
                name="smoke-default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "memory": {"enabled": False},
                    "memory_platform": {"enabled": False, "archive": {"fts_enabled": False}},
                },
            )
        ]
    with _smoke_temp_root() as tmp:
        root = Path(tmp)
        gateway_deps = build_app_runtime_deps(
            config_layers=config_layers,
            thread_root=root / "threads",
            state_db_path=root / "gateway.sqlite3",
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="smoke hello")]),
        )
        app = make_gateway_app(
            config_layers=config_layers,
            thread_root=root / "threads",
            state_db_path=root / "gateway.sqlite3",
            chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="smoke hello")]),
            runtime_deps=gateway_deps,
        )
        with TestClient(app) as client:
            health = client.get("/health")
            report.checks.append(
                SmokeCheck(
                    name="gateway_health",
                    status="ok" if health.status_code == 200 else "fail",
                    detail=f"/health -> {health.status_code}",
                )
            )
        if hasattr(app.state, "runtime_deps") and hasattr(app.state.runtime_deps, "close"):
            app.state.runtime_deps.close()

        sdk = EmbeddedClient(
            EmbeddedClientConfig(
                config_layers=config_layers,
                thread_root=root / "sdk-threads",
                state_db_path=root / "sdk.sqlite3",
                chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="smoke hello")]),
            )
        )
        try:
            thread = sdk.create_thread(thread_id="smoke-thread")
            report.checks.append(
                SmokeCheck(name="embedded_sdk_thread", status="ok", detail=f"created {thread.thread_id}")
            )
            result = sdk.run(EmbeddedRunRequest(thread_id="smoke-thread", message="hello"))
            report.checks.append(
                SmokeCheck(
                    name="embedded_sdk_run",
                    status="ok" if result.status == "completed" else "fail",
                    detail=f"run status: {result.status}",
                )
            )
            if report_json_path is not None or report_markdown_path is not None:
                _append_evaluation_report_artifacts(
                    report,
                    sdk,
                    thread_id="smoke-thread",
                    output_json_path=report_json_path,
                    output_markdown_path=report_markdown_path,
                )
        finally:
            sdk.close()
        gc.collect()
    return report


def run_provider_smoke(
    *,
    message: str = "Reply with OK only.",
    config_path: str | Path | None = None,
    model_name: str | None = None,
    chat_model_override=None,
    expect_trace: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    report_dir: str | Path | None = None,
) -> SmokeReport:
    return run_provider_smoke_with_options(
        message=message,
        config_path=config_path,
        model_name=model_name,
        chat_model_override=chat_model_override,
        expect_trace=expect_trace,
        report_json_path=report_json_path,
        report_markdown_path=report_markdown_path,
        report_dir=report_dir,
    )


def run_provider_catalog_smoke(
    *,
    message: str = "Reply with OK only.",
    config_path: str | Path | None = None,
    chat_model_override=None,
    expect_trace: bool = False,
    skip_missing_secrets: bool = True,
    stop_on_failure: bool = False,
    report_dir: str | Path | None = None,
) -> SmokeReport:
    report = SmokeReport()
    config_result = ConfigService().resolve(build_default_config_layers(config_path=config_path))
    if not config_result.effective_config.models:
        report.checks.append(
            SmokeCheck(
                name="provider_config",
                status="fail",
                detail="No provider is configured through config.yaml or ANVIL_* fallback.",
            )
        )
        return report

    model_names = _ordered_model_names(config_result.effective_config.default_model, config_result.effective_config.models)
    attempted = 0
    for model_name in model_names:
        model = config_result.effective_config.models[model_name]
        missing_envs = _missing_secret_envs_for_model(model)
        if missing_envs:
            report.checks.append(
                SmokeCheck(
                    name=f"provider_secret:{model_name}",
                    status="skip" if skip_missing_secrets else "fail",
                    detail=f"missing env-backed secret(s): {', '.join(missing_envs)}",
                )
            )
            if skip_missing_secrets:
                continue
            return report

        attempted += 1
        single = run_provider_smoke_with_options(
            message=message,
            config_path=config_path,
            model_name=model_name,
            chat_model_override=chat_model_override,
            expect_trace=expect_trace,
            report_dir=report_dir,
        )
        for check in single.checks:
            report.checks.append(
                SmokeCheck(
                    name=f"{check.name}:{model_name}",
                    status=check.status,
                    detail=check.detail,
                )
            )
        if stop_on_failure and not single.ok():
            return report

    if attempted == 0:
        report.checks.append(
            SmokeCheck(
                name="provider_reachability",
                status="fail",
                detail="no configured model had all required env-backed secrets available",
            )
        )
    return report


def run_provider_smoke_with_options(
    *,
    message: str = "Reply with OK only.",
    config_path: str | Path | None = None,
    model_name: str | None = None,
    chat_model_override=None,
    expect_trace: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    report_dir: str | Path | None = None,
) -> SmokeReport:
    report = SmokeReport()
    config_layers = build_default_config_layers(config_path=config_path)
    config_layers = _with_smoke_storage_layers(config_layers)
    config_result = ConfigService().resolve(config_layers)
    model_ready = bool(config_result.effective_config.default_model and config_result.effective_config.models)
    if not model_ready:
        report.checks.append(
            SmokeCheck(
                name="provider_config",
                status="fail",
                detail="No provider is configured through config.yaml or ANVIL_* fallback.",
            )
        )
        return report

    if model_name is not None:
        if model_name not in config_result.effective_config.models:
            report.checks.append(
                SmokeCheck(
                    name="provider_model",
                    status="fail",
                    detail=f"requested model '{model_name}' is not defined",
                )
            )
            return report
        config_layers = [
            *config_layers,
            ConfigLayer(
                name="smoke_model_selection",
                kind=ConfigLayerKind.REQUEST,
                data={"default_model": model_name},
                source="smoke",
            ),
        ]
        config_result = ConfigService().resolve(config_layers)

    selected_model_name = config_result.effective_config.default_model
    selected_model = config_result.effective_config.models.get(selected_model_name) if selected_model_name else None
    report_json_path, report_markdown_path = _resolve_smoke_report_paths(
        report_json_path=report_json_path,
        report_markdown_path=report_markdown_path,
        report_dir=report_dir,
        stem=f"provider-smoke-{_safe_filename(selected_model_name or 'unknown-model')}",
    )
    missing_envs = _missing_secret_envs_for_model(selected_model)
    report.checks.append(
        SmokeCheck(
            name="provider_model",
            status="ok" if selected_model_name is not None else "fail",
            detail=f"selected model: {selected_model_name}" if selected_model_name is not None else "no model selected",
        )
    )
    if missing_envs:
        report.checks.append(
            SmokeCheck(
                name="provider_secret",
                status="fail",
                detail=f"missing env-backed secret(s): {', '.join(missing_envs)}",
            )
        )
        return report

    with _smoke_temp_root() as tmp:
        root = Path(tmp)
        sdk = EmbeddedClient(
            EmbeddedClientConfig(
                config_layers=config_layers,
                thread_root=root / "sdk-threads",
                state_db_path=root / "sdk.sqlite3",
                chat_model_override=chat_model_override,
            )
        )
        try:
            sdk.create_thread(thread_id="provider-smoke")
            result = sdk.run(EmbeddedRunRequest(thread_id="provider-smoke", message=message))
            report.checks.append(
                SmokeCheck(
                    name="provider_run",
                    status="ok" if result.status == "completed" else "fail",
                    detail=_provider_run_detail(result),
                )
            )
            if report_json_path is not None or report_markdown_path is not None:
                _append_evaluation_report_artifacts(
                    report,
                    sdk,
                    thread_id="provider-smoke",
                    output_json_path=report_json_path,
                    output_markdown_path=report_markdown_path,
                )
            if expect_trace:
                tracing_service = sdk.deps.tracing_service
                tracing_settings = tracing_service.settings
                report.checks.append(
                    SmokeCheck(
                        name="provider_trace",
                        status="ok" if tracing_service.enabled() else "fail",
                        detail=(
                            f"trace ready via {tracing_settings.enabled_source or 'config'} in project {tracing_settings.project or 'anvil'}"
                            if tracing_service.enabled()
                            else "tracing was requested but no active tracing service was built"
                        ),
                    )
                )
        finally:
            sdk.close()
    gc.collect()
    return report


def _build_smoke_config_layers(*, config_path: str | Path | None = None) -> list[ConfigLayer]:
    return _with_smoke_storage_layers(build_default_config_layers(config_path=config_path))


def _with_smoke_storage_layers(config_layers: list[ConfigLayer]) -> list[ConfigLayer]:
    return [
        *config_layers,
        ConfigLayer(
            name="smoke_storage",
            kind=ConfigLayerKind.REQUEST,
            data={"memory": {"enabled": False}, "memory_platform": {"enabled": False, "archive": {"fts_enabled": False}}},
            source="smoke",
        ),
        ConfigLayer(
            name="smoke_storage_paths",
            kind=ConfigLayerKind.REQUIREMENTS,
            data={
                "memory": {"store_path": str(Path(os.environ.get("ANVIL_BACKEND_TEST_TMP") or tempfile.gettempdir()) / "anvil-smoke-memory")},
                "memory_platform": {
                    "archive": {"sqlite_path": None},
                    "transcript": {"sqlite_path": None},
                    "prompt_snapshot": {"store_path": None},
                    "session_snapshot": {"store_path": None},
                },
            },
            source="smoke",
        ),
    ]


@contextmanager
def _smoke_temp_root():
    base = Path(os.environ.get("ANVIL_BACKEND_TEST_TMP") or tempfile.gettempdir()) / "anvil-smoke"
    base.mkdir(parents=True, exist_ok=True)
    root = base / f"tmp-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def render_smoke_report(report: SmokeReport) -> str:
    lines = [f"[{check.status}] {check.name}: {check.detail}" for check in report.checks]
    lines.extend(
        f"[artifact] {artifact.kind}: {artifact.path}{f' ({artifact.detail})' if artifact.detail else ''}"
        for artifact in report.artifacts
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run Anvil smoke checks.")
    parser.add_argument("mode", nargs="?", default="local", choices=["local", "provider"])
    parser.add_argument("--config", dest="config_path", default=None)
    parser.add_argument("--model", dest="model_name", default=None)
    parser.add_argument("--message", default="Reply with OK only.")
    parser.add_argument("--expect-trace", action="store_true")
    parser.add_argument("--report-json", default=None, help="Write the generated smoke evaluation report JSON to this path.")
    parser.add_argument("--report-md", default=None, help="Write the generated smoke evaluation report Markdown to this path.")
    parser.add_argument(
        "--report-dir",
        default=None,
        help="Write smoke evaluation JSON and Markdown artifacts under this directory. Required for --all-configured reports.",
    )
    parser.add_argument(
        "--all-configured",
        action="store_true",
        help="In provider mode, run every configured model whose env-backed secrets are available.",
    )
    parser.add_argument(
        "--include-missing",
        action="store_true",
        help="In --all-configured mode, fail on missing provider secrets instead of skipping them.",
    )
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        help="In --all-configured mode, stop after the first provider failure.",
    )
    args = parser.parse_args(argv)
    if args.all_configured and (args.report_json or args.report_md):
        parser.error("--report-json/--report-md are single-run outputs; use --report-dir with --all-configured")
    if args.mode == "local":
        report = run_local_smoke(
            config_path=args.config_path,
            report_json_path=args.report_json,
            report_markdown_path=args.report_md,
            report_dir=args.report_dir,
        )
    elif args.all_configured:
        report = run_provider_catalog_smoke(
            message=args.message,
            config_path=args.config_path,
            expect_trace=args.expect_trace,
            skip_missing_secrets=not args.include_missing,
            stop_on_failure=args.stop_on_failure,
            report_dir=args.report_dir,
        )
    else:
        report = run_provider_smoke_with_options(
            message=args.message,
            config_path=args.config_path,
            model_name=args.model_name,
            expect_trace=args.expect_trace,
            report_json_path=args.report_json,
            report_markdown_path=args.report_md,
            report_dir=args.report_dir,
        )
    print(render_smoke_report(report))
    raise SystemExit(0 if report.ok() else 1)


def _missing_secret_envs_for_model(model) -> list[str]:
    if model is None:
        return []
    missing: list[str] = []
    if model.api_key and is_env_ref(model.api_key):
        env_name = env_ref_name(model.api_key)
        if not os.getenv(env_name):
            missing.append(env_name)
    if model.api_key_env and not os.getenv(model.api_key_env):
        missing.append(model.api_key_env)
    for env_name in iter_env_ref_names(model.provider_settings):
        if not os.getenv(env_name):
            missing.append(env_name)
    return list(dict.fromkeys(missing))


def _ordered_model_names(default_model: str | None, models: dict[str, object]) -> list[str]:
    names = list(models)
    if default_model in models:
        names.remove(default_model)
        return [default_model, *names]
    return names


def _provider_run_detail(result) -> str:
    detail = f"provider run status: {result.status}"
    if result.last_error:
        detail += f"; error: {result.last_error}"
    return detail


def _resolve_smoke_report_paths(
    *,
    report_json_path: str | Path | None,
    report_markdown_path: str | Path | None,
    report_dir: str | Path | None,
    stem: str,
) -> tuple[Path | None, Path | None]:
    json_path = Path(report_json_path).expanduser().resolve() if report_json_path else None
    markdown_path = Path(report_markdown_path).expanduser().resolve() if report_markdown_path else None
    if report_dir is not None:
        root = Path(report_dir).expanduser().resolve()
        if json_path is None:
            json_path = root / f"{stem}.json"
        if markdown_path is None:
            markdown_path = root / f"{stem}.md"
    return json_path, markdown_path


def _append_evaluation_report_artifacts(
    report: SmokeReport,
    sdk: EmbeddedClient,
    *,
    thread_id: str,
    output_json_path: Path | None,
    output_markdown_path: Path | None,
) -> None:
    try:
        batch_report = sdk.build_evaluation_report(
            EvaluationReportRequestView(
                thread_ids=[thread_id],
                options=EvaluationReportOptionsView(include_markdown=output_markdown_path is not None),
                write_markdown=output_markdown_path is not None,
                output_path=str(output_markdown_path) if output_markdown_path is not None else None,
            )
        )
        if output_json_path is not None:
            output_json_path.parent.mkdir(parents=True, exist_ok=True)
            output_json_path.write_text(
                json.dumps(batch_report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            report.artifacts.append(
                SmokeArtifact(
                    kind="evaluation_json",
                    path=str(output_json_path),
                    detail=f"score={batch_report.score:.4f}",
                )
            )
        if output_markdown_path is not None:
            output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
            if not output_markdown_path.exists() and batch_report.markdown:
                output_markdown_path.write_text(batch_report.markdown, encoding="utf-8", newline="\n")
            report.artifacts.append(
                SmokeArtifact(
                    kind="evaluation_markdown",
                    path=str(output_markdown_path),
                    detail=f"score={batch_report.score:.4f}",
                )
            )
        report.checks.append(
            SmokeCheck(
                name="evaluation_report",
                status="ok",
                detail=(
                    f"report_id={batch_report.report_id} "
                    f"threads={batch_report.summary.get('thread_count', len(batch_report.thread_reports))} "
                    f"score={batch_report.score:.4f}"
                ),
            )
        )
    except Exception as exc:  # pragma: no cover - defensive CLI error surface
        report.checks.append(
            SmokeCheck(
                name="evaluation_report",
                status="fail",
                detail=f"{type(exc).__name__}: {exc}",
            )
        )


def _safe_filename(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in {"-", "_", "."} else "-" for character in value)
    cleaned = cleaned.strip(".-_")
    return cleaned or "smoke"


if __name__ == "__main__":
    main()
