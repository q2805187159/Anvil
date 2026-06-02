from __future__ import annotations

import pytest
import asyncio
import threading
from fastapi.testclient import TestClient

from anvil.config import ConfigLayer, ConfigLayerKind
from anvil.runtime.runs import InMemoryRunEventLogStore
from app.gateway.deps import AppRuntimeDeps
from app.gateway.streaming_runs import BackgroundRunStreamManager
from app.runtime_deps import RuntimeDepsBundle


class CloseTracker:
    def __init__(self, name: str, calls: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.calls = calls
        self.fail = fail

    def close(self) -> None:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} failed")


class ShutdownTracker:
    def __init__(self, name: str, calls: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.calls = calls
        self.fail = fail

    def shutdown(self) -> None:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} failed")


class RunEngineShutdownTracker:
    def __init__(self, calls: list[str], *, fail: bool = False) -> None:
        self.calls = calls
        self.fail = fail

    def wait_for_background_tasks(self, timeout_seconds: float = 5.0) -> None:
        self.calls.append(f"run_engine_background_tasks:{timeout_seconds:g}")
        if self.fail:
            raise RuntimeError("run_engine_background_tasks failed")


class SkillCuratorConfigStub:
    automation_enabled = False
    tick_seconds = 60


class SkillsConfigStub:
    enabled = False
    watch_enabled = False
    curator = SkillCuratorConfigStub()


class ConfigFreshnessStub:
    watch_interval_seconds = 1


class EffectiveConfigStub:
    skills_config = SkillsConfigStub()
    config_freshness = ConfigFreshnessStub()


class EventBusStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def publish(self, name: str, payload: dict[str, object]) -> None:
        self.events.append((name, payload))


def test_gateway_deps_lifecycle_builds_runtime_bundle(gateway_app_factory) -> None:
    app = gateway_app_factory()
    with TestClient(app):
        deps = app.state.runtime_deps
        assert deps.config_result is not None
        assert deps.effective_config is not None
        assert deps.harness_factory is not None
        assert deps.path_service is not None
        assert deps.checkpointer is not None
        assert deps.store is not None
        assert deps.run_engine is not None
        assert deps.skills_service is not None
        assert deps.memory_service is not None
        assert deps.extensions_service is not None


def test_runtime_deps_are_reused_through_app_state(gateway_client) -> None:
    app = gateway_client.app
    first = app.state.runtime_deps
    response = gateway_client.get("/health")
    assert response.status_code == 200
    second = app.state.runtime_deps
    assert first is second


def test_gateway_runtime_deps_runs_skill_curator_automation(gateway_app_factory, contract_tmp_path, monkeypatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {"openai": {"name": "openai", "provider": "openai", "model_name": "gpt-5.4"}},
                    "skills_config": {
                        "enabled": True,
                        "governance_root": str(contract_tmp_path / "governance"),
                        "curator": {"automation_enabled": True, "interval_seconds": 60, "dry_run": True},
                    },
                },
            )
        ]
    )
    with TestClient(app):
        deps = app.state.runtime_deps
        assert deps.skill_curator_watch_enabled() is True
        assert deps.skill_curator_watch_interval_seconds() == 60.0

        deps.skills_service.manage_curator(
            config=deps.effective_config,
            action="create",
            skill_id="agent-gateway-automation",
            title="Agent Gateway Automation",
            summary="Exercise gateway automation events.",
            body="Use when automation events should carry governance recommendations.",
        )
        deps.skills_service.manage_curator(
            config=deps.effective_config,
            action="feedback",
            skill_id="agent-gateway-automation",
            outcome="failure",
            rationale="Gateway event should surface the next review step.",
            feedback_source="user",
            confidence=1.0,
        )
        result = deps.run_skill_curator_automation_sync(force_run=True)
        assert result.ran is True
        assert result.report["recommendations"][0]["next_tool_call"] == {
            "action": "review_plan",
            "skill_id": "agent-gateway-automation",
        }


def test_gateway_runtime_deps_hot_reloads_new_skill_manifests(gateway_app_factory, contract_tmp_path, monkeypatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_repo_skill_root", lambda: contract_tmp_path / "empty-repo-skills")
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {"openai": {"name": "openai", "provider": "openai", "model_name": "gpt-5.4"}},
                    "config_freshness": {"watch_interval_seconds": 1},
                    "skills_config": {
                        "enabled": True,
                        "watch_enabled": True,
                        "external_dirs": [str(workspace_skills)],
                    },
                },
            )
        ]
    )
    with TestClient(app):
        deps = app.state.runtime_deps
        deps._skill_last_scan_at = -10.0
        initial_ids = set(deps.skills_service.discover(config=deps.effective_config, fingerprint="initial").enabled_ids)
        assert "hot-skill" not in initial_ids
        assert asyncio.run(deps.refresh_skills_if_needed()) is False

        skill_dir = workspace_skills / "hot-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Hot Skill\n\nUse when testing hot reload.\n", encoding="utf-8")
        deps._skill_last_scan_at = -10.0

        assert asyncio.run(deps.refresh_skills_if_needed()) is True
        assert "hot-skill" in deps.skills_service.discover(config=deps.effective_config, fingerprint="after").enabled_ids


def test_gateway_runtime_deps_nonblocking_skill_refresh_returns_before_scan_completes(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_repo_skill_root", lambda: contract_tmp_path / "empty-repo-skills")
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {"openai": {"name": "openai", "provider": "openai", "model_name": "gpt-5.4"}},
                    "config_freshness": {"watch_interval_seconds": 1},
                    "skills_config": {
                        "enabled": True,
                        "watch_enabled": True,
                        "external_dirs": [str(workspace_skills)],
                    },
                },
            )
        ]
    )
    with TestClient(app):
        deps = app.state.runtime_deps
        scan_started = threading.Event()
        release_scan = threading.Event()

        async def exercise() -> bool:
            deps._skill_last_scan_at = -10.0

            def slow_collect() -> dict[str, float]:
                scan_started.set()
                release_scan.wait(timeout=1)
                return {}

            monkeypatch.setattr(deps, "_collect_skill_mtimes", slow_collect)
            result = await deps.refresh_skills_if_needed(block=False)
            assert result is False
            assert await asyncio.to_thread(scan_started.wait, 1.0) is True
            assert deps._skill_scan_task is not None
            assert not deps._skill_scan_task.done()
            release_scan.set()
            await asyncio.wait_for(deps._skill_scan_task, timeout=1)
            return True

        assert asyncio.run(exercise()) is True


def test_gateway_enables_local_frontend_cors_by_default(gateway_app_factory, monkeypatch) -> None:
    monkeypatch.delenv("ANVIL_GATEWAY_CORS_ORIGINS", raising=False)
    app = gateway_app_factory()

    with TestClient(app) as client:
        response = client.options(
            "/threads",
            headers={
                "Origin": "http://127.0.0.1:13200",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:13200"


def build_tracked_deps(deps_type, calls: list[str], *, fail_on: set[str] | None = None):
    fail_on = fail_on or set()
    kwargs = dict(
        config_layers=[],
        config_service=object(),
        config_coordinator=object(),
        config_result=object(),
        effective_config=EffectiveConfigStub(),
        feature_set=object(),
        harness_factory=object(),
        path_service=object(),
        checkpointer=CloseTracker(
            "checkpointer",
            calls,
            fail="checkpointer" in fail_on,
        ),
        store=CloseTracker("store", calls, fail="store" in fail_on),
        thread_service=object(),
        run_engine=RunEngineShutdownTracker(
            calls,
            fail="run_engine_background_tasks" in fail_on,
        ),
        run_event_log_store=InMemoryRunEventLogStore(),
        skills_service=object(),
        memory_service=None,
        memory_manager=ShutdownTracker(
            "memory_manager",
            calls,
            fail="memory_manager" in fail_on,
        ),
        extensions_service=object(),
        capability_assembly_service=object(),
        upload_service=object(),
        subagent_service=CloseTracker(
            "subagent_service",
            calls,
            fail="subagent_service" in fail_on,
        ),
        process_service=CloseTracker(
            "process_service",
            calls,
            fail="process_service" in fail_on,
        ),
        scheduled_task_service=CloseTracker(
            "scheduled_task_service",
            calls,
            fail="scheduled_task_service" in fail_on,
        ),
        tracing_service=object(),
        system_event_bus=object(),
    )
    if deps_type is AppRuntimeDeps:
        kwargs["stream_run_manager"] = BackgroundRunStreamManager()
    return deps_type(**kwargs)


@pytest.mark.parametrize("deps_type", [AppRuntimeDeps, RuntimeDepsBundle])
def test_runtime_deps_close_lifecycle_order_matches_runtime_bundle(deps_type) -> None:
    calls: list[str] = []
    deps = build_tracked_deps(deps_type, calls)

    deps.close()

    assert calls == [
        "run_engine_background_tasks:10",
        "process_service",
        "scheduled_task_service",
        "memory_manager",
        "subagent_service",
        "checkpointer",
        "store",
    ]


@pytest.mark.parametrize("deps_type", [AppRuntimeDeps, RuntimeDepsBundle])
def test_runtime_deps_close_attempts_all_services_before_raising(deps_type) -> None:
    calls: list[str] = []
    deps = build_tracked_deps(
        deps_type,
        calls,
        fail_on={"process_service", "subagent_service"},
    )

    with pytest.raises(ExceptionGroup) as raised:
        deps.close()

    assert calls == [
        "run_engine_background_tasks:10",
        "process_service",
        "scheduled_task_service",
        "memory_manager",
        "subagent_service",
        "checkpointer",
        "store",
    ]
    messages = [str(exc) for exc in raised.value.exceptions]
    assert messages == [
        "process_service close failed",
        "subagent_service close failed",
    ]
