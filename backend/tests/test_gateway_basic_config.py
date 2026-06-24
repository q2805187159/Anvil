from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.gateway.app import make_gateway_app


def test_gateway_basic_config_exposes_required_git_token(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
git:
  token_env: GITHUB_TOKEN
        """.strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("GITHUB_TOKEN", "test-git-token")

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.get("/config/basics")

    assert response.status_code == 200
    body = response.json()
    assert body["config_path"] == str(config_path)
    assert body["required_count"] == 1
    assert body["configured_required_count"] == 1
    assert body["missing_required_count"] == 0
    required = {item["item_id"]: item for item in body["required_items"]}
    assert required["git_token"]["required"] is True
    assert required["git_token"]["configured"] is True
    assert required["git_token"]["token_env"] == "GITHUB_TOKEN"
    assert required["git_token"]["value"] is None
    assert body["extension_items"]


def test_gateway_basic_config_update_writes_git_config_and_env(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text("llm:\n  default:\n  providers: {}\n", encoding="utf-8")
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("ANVIL_GIT_TOKEN", raising=False)

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        response = client.patch(
            "/config/basics",
            json={
                "git_token_env": "ANVIL_GIT_TOKEN",
                "git_token": "test-git-token",
                "git_user_name": "Anvil Operator",
                "git_user_email": "operator@example.test",
                "git_remote_url": "https://github.com/example/anvil-memory.git",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["dotenv_path"] == str(config_path.parent / ".env")
    assert body["basics"]["configured_required_count"] == 1

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert payload["git"]["enabled"] is True
    assert payload["git"]["required"] is True
    assert payload["git"]["provider"] == "github"
    assert payload["git"]["token_env"] == "ANVIL_GIT_TOKEN"
    assert payload["git"]["user_name"] == "Anvil Operator"
    assert payload["git"]["user_email"] == "operator@example.test"
    assert payload["git"]["remote_url"] == "https://github.com/example/anvil-memory.git"
    assert (config_path.parent / ".env").read_text(encoding="utf-8").strip() == "ANVIL_GIT_TOKEN=test-git-token"


def test_gateway_basic_config_test_reports_missing_and_configured_git_token(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
git:
  token_env: ANVIL_GIT_TOKEN
        """.strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("ANVIL_GIT_TOKEN", raising=False)

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        missing = client.post("/config/basics/test", json={"item_id": "git_token"})
        monkeypatch.setenv("ANVIL_GIT_TOKEN", "test-git-token")
        configured = client.post("/config/basics/test", json={"item_id": "git_token"})

    assert missing.status_code == 200
    assert missing.json()["ok"] is False
    assert missing.json()["status"] == "missing"
    assert "ANVIL_GIT_TOKEN" in missing.json()["message"]
    assert configured.status_code == 200
    assert configured.json()["ok"] is True
    assert configured.json()["status"] == "ready"


def test_gateway_basic_config_exposes_and_tests_git_remote_extension(
    contract_tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = contract_tmp_path / "config.yaml"
    config_path.write_text(
        """
git:
  token_env: GITHUB_TOKEN
  remote_url: https://github.com/example/anvil-memory.git
        """.strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANVIL_CONFIG_PATH", str(config_path))

    app = make_gateway_app(thread_root=contract_tmp_path / "threads", state_db_path=contract_tmp_path / "gateway.sqlite3")
    with TestClient(app) as client:
        overview = client.get("/config/basics")
        remote_test = client.post("/config/basics/test", json={"item_id": "git_remote"})

    assert overview.status_code == 200
    extension_items = {item["item_id"]: item for item in overview.json()["extension_items"]}
    assert extension_items["git_remote"]["category"] == "extension"
    assert extension_items["git_remote"]["required"] is False
    assert extension_items["git_remote"]["configured"] is True
    assert extension_items["git_remote"]["testable"] is True
    assert extension_items["git_remote"]["value"] == "https://github.com/example/anvil-memory.git"

    assert remote_test.status_code == 200
    assert remote_test.json()["ok"] is True
    assert remote_test.json()["status"] == "ready"
    assert "https://github.com/example/anvil-memory.git" in remote_test.json()["message"]
