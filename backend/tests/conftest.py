from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from uuid import uuid4

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
HARNESS_SRC = Path(__file__).resolve().parents[1] / "packages" / "harness"
BACKEND_TEST_TMP_ENV = "ANVIL_BACKEND_TEST_TMP"

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGCHAIN_TRACING"] = "false"
os.environ.pop("LANGSMITH_API_KEY", None)
os.environ.pop("LANGCHAIN_API_KEY", None)


def _select_local_tmp() -> Path:
    candidates = []
    env_value = os.environ.get(BACKEND_TEST_TMP_ENV)
    if env_value:
        candidates.append(Path(env_value))
    candidates.extend(
        [
            BACKEND_ROOT / ".pytest_tmp",
            Path(tempfile.gettempdir()) / "anvil-backend-tests",
        ]
    )
    seen: set[str] = set()
    failures: list[str] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        marker = str(resolved).casefold()
        if marker in seen:
            continue
        seen.add(marker)
        try:
            _assert_local_tmp_usable(resolved)
        except (OSError, sqlite3.Error) as exc:
            failures.append(f"{resolved}: {exc}")
            continue
        return resolved
    raise RuntimeError("no usable backend test temp directory; " + "; ".join(failures))


def _assert_local_tmp_usable(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    file_probe = root / f".anvil-write-probe-{token}.txt"
    sqlite_probe = root / f".anvil-sqlite-probe-{token}.sqlite3"
    try:
        file_probe.write_text("ok", encoding="utf-8")
        connection = sqlite3.connect(sqlite_probe)
        try:
            connection.execute("CREATE TABLE probe (value TEXT NOT NULL)")
            connection.execute("INSERT INTO probe(value) VALUES (?)", ("ok",))
            connection.commit()
        finally:
            connection.close()
    finally:
        _unlink_probe(file_probe)
        _unlink_probe(sqlite_probe)
        _unlink_probe(sqlite_probe.with_suffix(sqlite_probe.suffix + "-journal"))
        _unlink_probe(sqlite_probe.with_suffix(sqlite_probe.suffix + "-wal"))
        _unlink_probe(sqlite_probe.with_suffix(sqlite_probe.suffix + "-shm"))


def _unlink_probe(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass


LOCAL_TMP = _select_local_tmp()

LOCAL_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(LOCAL_TMP)
os.environ["TEMP"] = str(LOCAL_TMP)
os.environ["TMPDIR"] = str(LOCAL_TMP)

if str(HARNESS_SRC) not in sys.path:
    sys.path.insert(0, str(HARNESS_SRC))

from fastapi.testclient import TestClient

from anvil.agents.features import RuntimeFeatureSet
from anvil.config import ConfigLayer, ConfigLayerKind
from app.gateway.app import make_gateway_app


@pytest.fixture
def contract_tmp_path() -> Path:
    path = LOCAL_TMP / f"tmp-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    previous_anvil_home = os.environ.get("ANVIL_HOME")
    os.environ["ANVIL_HOME"] = str(path / ".anvil-home")
    try:
        yield path
    finally:
        if previous_anvil_home is None:
            os.environ.pop("ANVIL_HOME", None)
        else:
            os.environ["ANVIL_HOME"] = previous_anvil_home
        shutil.rmtree(path, ignore_errors=True)


def write_test_skill(root: Path, slug: str, title: str, body: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


def build_gateway_config_layers(base_path: Path, *, refresh_policy: str = "dynamic") -> list[ConfigLayer]:
    skills_root = base_path / "skills"
    write_test_skill(skills_root, "demo-skill", "Demo Skill", "Use the demo workflow")
    return [
        ConfigLayer(
            name="default",
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
                "hcms": {"enabled": True},
                "skills_config": {
                    "enabled": True,
                    "external_dirs": [str(skills_root)],
                },
                "subagents": {"enabled": True},
                "extensions": {
                    "mcp_servers": {
                        "github": {
                            "enabled": True,
                            "transport_kind": "stdio",
                            "refresh_policy": refresh_policy,
                            "connection_config": {
                                "inline_tools": [
                                    {
                                        "name": "ext_search",
                                        "display_name": "External Search",
                                        "capability_group": "research",
                                        "deferred": True,
                                    }
                                ]
                            },
                        }
                    }
                },
                "guardrails": {"enabled": True},
            },
        )
    ]


@pytest.fixture
def gateway_app_factory(contract_tmp_path):
    def _make_app(
        *,
        config_layers=None,
        chat_model_override=None,
        refresh_policy: str = "dynamic",
        subagent_service=None,
        tracing_service=None,
    ):
        layers = config_layers or build_gateway_config_layers(contract_tmp_path, refresh_policy=refresh_policy)
        return make_gateway_app(
            config_layers=layers,
            feature_set=RuntimeFeatureSet(
                memory=True,
                memory_prefetch=True,
                skills=True,
                capability_mentions=True,
                extensions=True,
                subagents=True,
                guardrails=True,
                title=True,
            ),
            thread_root=contract_tmp_path / "threads",
            state_db_path=contract_tmp_path / "gateway.sqlite3",
            chat_model_override=chat_model_override,
            subagent_service=subagent_service,
            tracing_service=tracing_service,
        )

    return _make_app


@pytest.fixture
def gateway_client(gateway_app_factory):
    app = gateway_app_factory()
    with TestClient(app) as client:
        yield client
