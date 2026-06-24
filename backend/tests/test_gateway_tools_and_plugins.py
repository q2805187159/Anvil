from __future__ import annotations

import asyncio
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
import yaml

from anvil.config import ConfigLayer, ConfigLayerKind


def write_skill(root: Path, slug: str, title: str, summary: str, *, extra_frontmatter: str = "") -> Path:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n"
        f"title: {title}\n"
        f"summary: {summary}\n"
        f"version: 1.0.0\n"
        f"trust: trusted\n"
        f"{extra_frontmatter}"
        f"---\n\n"
        f"# {title}\n\n"
        f"{summary}\n",
        encoding="utf-8",
    )
    return skill_dir


def build_skill_archive(skill_dir: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(skill_dir.parent)).replace("\\", "/"))
    return destination


def build_custom_layers(base_path: Path) -> list[ConfigLayer]:
    repo_skills = base_path / "skills"
    write_skill(repo_skills, "demo-skill", "Demo Skill", "Use the demo workflow")
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
                    "external_dirs": [str(repo_skills)],
                    "governance_root": str(base_path / "governance"),
                    "quarantine_root": str(base_path / "governance" / "quarantine"),
                    "history_root": str(base_path / "governance" / "history"),
                    "curator": {
                        "automation_enabled": True,
                        "interval_seconds": 60,
                        "dry_run": True,
                    },
                },
                "subagents": {"enabled": True},
                "extensions": {
                    "mcp_servers": {
                        "github": {
                            "enabled": True,
                            "transport_kind": "stdio",
                            "refresh_policy": "dynamic",
                            "tool_prefix": "gh_",
                            "tool_allowlist": ["ext_search"],
                            "connection_config": {
                                "inline_tools": [
                                    {
                                        "name": "ext_search",
                                        "display_name": "External Search",
                                        "capability_group": "research",
                                        "deferred": True,
                                    }
                                ],
                                "inline_resources": [
                                    {
                                        "resource_id": "playbook",
                                        "title": "Playbook",
                                        "path": str(base_path / "playbook.md"),
                                    }
                                ],
                                "inline_prompts": [
                                    {
                                        "prompt_id": "triage",
                                        "title": "Triage",
                                        "arguments": ["repo"],
                                        "template": "triage {repo}",
                                    }
                                ],
                            },
                        }
                    },
                    "plugins": {
                        "ops": {
                            "enabled": True,
                            "source_path": "plugins/ops",
                            "skill_roots": [str(base_path / "plugin-skills")],
                            "inline_tools": [
                                {
                                    "name": "ops_summary",
                                    "display_name": "Ops Summary",
                                    "capability_group": "plugin",
                                }
                            ],
                            "resources": [
                                {
                                    "resource_id": "ops-guide",
                                    "title": "Ops Guide",
                                    "path": str(base_path / "ops-guide.md"),
                                }
                            ],
                            "prompts": [
                                {
                                    "prompt_id": "ops-prompt",
                                    "title": "Ops Prompt",
                                    "arguments": ["target"],
                                    "template": "ops {target}",
                                }
                            ],
                            "catalog_metadata": {"tier": "trusted"},
                        }
                    },
                },
                "guardrails": {"enabled": True},
                "additional_settings": {
                    "web_tools": {
                        "mock_search_results": {"anvil": [{"title": "Anvil", "url": "https://example.com/anvil"}]},
                        "mock_fetch_results": {"https://example.com/anvil": {"content": "Anvil page"}},
                        "mock_image_results": {"anvil": [{"title": "Anvil image", "url": "https://example.com/anvil.png"}]},
                        "search_providers": ["tavily", "duckduckgo_html"],
                        "fetch_providers": ["tavily", "direct"],
                        "image_providers": ["wikimedia"],
                    }
                },
            },
        )
    ]


def home_config_path(base_path: Path) -> Path:
    return base_path / ".anvil-home" / "config.yaml"


def project_mcp_config_path(base_path: Path) -> Path:
    return base_path / ".anvil" / "mcp.json"


def home_plugin_config_path(base_path: Path) -> Path:
    return base_path / ".anvil-home" / "plugins.json"


def home_plugin_registry_config_path(base_path: Path) -> Path:
    return base_path / ".anvil-home" / "plugin-registries.json"


def drain_system_events(queue) -> list:
    events = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return events


def assert_mcp_reload_events_match_response(events: list, payload: dict) -> None:
    event_names = [event.event for event in events]
    assert "config_reloaded" in event_names
    assert "capabilities_changed" in event_names
    capabilities_event = next(event for event in events if event.event == "capabilities_changed")
    assert (
        capabilities_event.data["mcp_servers_connected"]
        == payload["reload"]["mcp_servers_connected"]
    )


def test_config_overview_tool_count_matches_catalog(
    contract_tmp_path,
    gateway_app_factory,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.governance.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    (contract_tmp_path / "playbook.md").write_text("playbook", encoding="utf-8")
    (contract_tmp_path / "ops-guide.md").write_text("ops guide", encoding="utf-8")

    app = gateway_app_factory(config_layers=build_custom_layers(contract_tmp_path))
    with TestClient(app) as client:
        overview = client.get("/config/overview")
        catalog = client.get("/tools/catalog")

    assert overview.status_code == 200
    assert catalog.status_code == 200
    payload = overview.json()
    catalog_items = catalog.json()
    visible_items = [item for item in catalog_items if item["visibility"] == "visible"]
    deferred_items = [
        item
        for item in catalog_items
        if item["deferred"] and item["visibility"] != "visible"
    ]
    assert payload["tools"]["total"] == len(catalog_items)
    assert payload["tools"]["enabled"] == len(visible_items)
    assert payload["tools"]["disabled"] == len(deferred_items)


def test_config_overview_and_catalog_share_capability_preview_cache(
    contract_tmp_path,
    gateway_app_factory,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.governance.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    app = gateway_app_factory(config_layers=build_custom_layers(contract_tmp_path))
    with TestClient(app) as client:
        deps = client.app.state.runtime_deps
        assemble_count = 0
        original_assemble = deps.capability_assembly_service.assemble

        def counted_assemble(*args, **kwargs):
            nonlocal assemble_count
            assemble_count += 1
            return original_assemble(*args, **kwargs)

        monkeypatch.setattr(deps.capability_assembly_service, "assemble", counted_assemble)
        overview = client.get("/config/overview")
        catalog = client.get("/tools/catalog")

    assert overview.status_code == 200
    assert catalog.status_code == 200
    assert assemble_count == 1


def test_config_overview_uses_short_lived_runtime_view_cache(
    contract_tmp_path,
    gateway_app_factory,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.governance.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    app = gateway_app_factory(config_layers=build_custom_layers(contract_tmp_path))
    with TestClient(app) as client:
        deps = client.app.state.runtime_deps
        assemble_count = 0
        discover_count = 0
        original_assemble = deps.capability_assembly_service.assemble
        original_discover = deps.skills_service.discover

        def counted_assemble(*args, **kwargs):
            nonlocal assemble_count
            assemble_count += 1
            return original_assemble(*args, **kwargs)

        def counted_discover(*args, **kwargs):
            nonlocal discover_count
            discover_count += 1
            return original_discover(*args, **kwargs)

        monkeypatch.setattr(deps.capability_assembly_service, "assemble", counted_assemble)
        monkeypatch.setattr(deps.skills_service, "discover", counted_discover)
        first = client.get("/config/overview")
        counts_after_first = (assemble_count, discover_count)
        second = client.get("/config/overview")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert counts_after_first[0] == 1
    assert counts_after_first[1] >= 1
    assert (assemble_count, discover_count) == counts_after_first


def test_config_overview_and_catalog_do_not_probe_live_mcp(gateway_app_factory, monkeypatch) -> None:
    live_probe_count = 0

    def fail_live_probe(*args, **kwargs):
        nonlocal live_probe_count
        live_probe_count += 1
        raise AssertionError("configuration pages must not perform live MCP discovery")

    monkeypatch.setattr(
        "anvil.extensions.materializer.ExtensionsMaterializer._discover_live_capabilities",
        fail_live_probe,
    )
    app = gateway_app_factory(
        config_layers=[
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {"openai": {"name": "openai", "provider": "openai", "model_name": "gpt-5.4"}},
                    "extensions": {
                        "mcp_servers": {
                            "remote_prompts": {
                                "enabled": True,
                                "transport_kind": "http",
                                "connection_config": {"url": "https://example.invalid/mcp"},
                            }
                        }
                    },
                },
            )
        ]
    )

    with TestClient(app) as client:
        overview = client.get("/config/overview")
        catalog = client.get("/tools/catalog")

    assert overview.status_code == 200
    assert catalog.status_code == 200
    assert live_probe_count == 0


def test_gateway_exposes_tools_plugins_mcp_and_skill_governance(
    contract_tmp_path,
    gateway_app_factory,
    monkeypatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.governance.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)

    (contract_tmp_path / "playbook.md").write_text("playbook", encoding="utf-8")
    (contract_tmp_path / "ops-guide.md").write_text("ops guide", encoding="utf-8")
    governed_src = write_skill(
        contract_tmp_path / "skill-packages",
        "governed-skill",
        "Governed Skill",
        "Governed summary",
    )
    (governed_src / "references").mkdir(parents=True, exist_ok=True)
    (governed_src / "references" / "guide.md").write_text("reference guide", encoding="utf-8")
    governed_archive = build_skill_archive(governed_src, contract_tmp_path / "archives" / "governed-skill.skill")

    app = gateway_app_factory(config_layers=build_custom_layers(contract_tmp_path))
    with TestClient(app) as client:
        tools_catalog = client.get("/tools/catalog")
        assert tools_catalog.status_code == 200
        tool_names = {item["name"] for item in tools_catalog.json()}
        assert {"tool_catalog", "tool_view", "skills_list", "web_search", "web_fetch", "web_extract", "image_search"}.issubset(tool_names)
        skill_manage_tool = next(item for item in tools_catalog.json() if item["name"] == "skill_manage")
        assert skill_manage_tool["risk_category"] == "skill_curator"
        assert "gh_ext_search" in tool_names

        tool_detail = client.get("/tools/gh_ext_search")
        assert tool_detail.status_code == 200
        assert tool_detail.json()["source_kind"] == "mcp"
        assert tool_detail.json()["visibility"] == "materialized"
        assert "tool_kind" not in tool_detail.json()
        assert "tool_contract" not in tool_detail.json()

        plugin_catalog = client.get("/catalog/tools", params={"source_kind": "plugin"})
        assert plugin_catalog.status_code == 200
        assert plugin_catalog.json()[0]["name"] == "ops_summary"
        assert "tool_kind" not in plugin_catalog.json()[0]

        plugins = client.get("/plugins")
        assert plugins.status_code == 200
        assert plugins.json()[0]["plugin_id"] == "ops"

        extensions = client.get("/extensions")
        assert extensions.status_code == 200
        assert [item["server_id"] for item in extensions.json()] == ["github"]
        assert extensions.json()[0]["discovery_source"] == "inline_fallback"

        mcp_resources = client.get("/mcp/resources")
        assert mcp_resources.status_code == 200
        assert mcp_resources.json()[0]["resource_id"] == "playbook"
        assert mcp_resources.json()[0]["discovery_source"] == "inline_fallback"

        provenance = client.get("/mcp/servers/github/provenance")
        assert provenance.status_code == 200
        assert provenance.json()["tool_prefix"] == "gh_"
        assert provenance.json()["tool_allowlist_active"] is False

        prompt_render = client.post("/mcp/servers/github/prompts/triage", json={"arguments": {"repo": "anvil"}})
        assert prompt_render.status_code == 200
        assert prompt_render.json()["rendered"] == "triage anvil"

        workspace_governed = workspace_skills / "governed-skill"
        workspace_governed.mkdir(parents=True, exist_ok=True)
        for path in governed_src.rglob("*"):
            if path.is_dir():
                continue
            relative = path.relative_to(governed_src)
            target = workspace_governed / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

        disable = client.post(
            "/skills/manage",
            json={"action": "disable", "skill_id": "governed-skill"},
        )
        assert disable.status_code == 200
        enable = client.post(
            "/skills/manage",
            json={"action": "enable", "skill_id": "governed-skill"},
        )
        assert enable.status_code == 200

        skill_detail = client.get("/skills/governed-skill")
        assert skill_detail.status_code == 200
        assert skill_detail.json()["title"] == "Governed Skill"
        assert skill_detail.json()["valid"] is True

        skill_content = client.get("/skills/governed-skill/content")
        assert skill_content.status_code == 200
        assert "Governed summary" in skill_content.json()["body"]
        assert skill_content.json()["file_count"] >= 2

        skill_files = client.get("/skills/governed-skill/files")
        assert skill_files.status_code == 200
        assert any(item["path"] == "references/guide.md" for item in skill_files.json()["files"])

        skill_file = client.post(
            "/skills/governed-skill/files/read",
            json={"relative_path": "references/guide.md"},
        )
        assert skill_file.status_code == 200
        assert skill_file.json()["content"] == "reference guide"

        deps = client.app.state.runtime_deps
        event_queue = deps.system_event_bus.subscribe()
        curator_create = client.post(
            "/skills/curator",
            json={
                "action": "create",
                "skill_id": "agent-note",
                "title": "Agent Note",
                "summary": "Capture durable agent lessons",
                "body": "Use when the session reveals a reusable coding lesson.",
                "rationale": "agent learned a reusable workflow",
                "tags": ["agent", "memory"],
            },
        )
        assert curator_create.status_code == 200
        assert curator_create.json()["accepted"] is True
        assert (workspace_skills / "agent-note" / "SKILL.md").exists()

        curator_patch = client.post(
            "/skills/curator",
            json={
                "action": "patch",
                "skill_id": "agent-note",
                "old_text": "reusable coding lesson",
                "new_text": "reusable project lesson",
            },
        )
        assert curator_patch.status_code == 200
        assert curator_patch.json()["change_action"] == "patch"

        curator_file = client.post(
            "/skills/curator",
            json={
                "action": "write_file",
                "skill_id": "agent-note",
                "file_path": "references/checklist.md",
                "content": "- Inspect\n- Verify\n",
            },
        )
        assert curator_file.status_code == 200
        assert (workspace_skills / "agent-note" / "references" / "checklist.md").exists()

        curator_duplicate = client.post(
            "/skills/curator",
            json={
                "action": "create",
                "skill_id": "agent-note-copy",
                "title": "Agent Note",
                "summary": "Capture durable agent lessons",
                "body": "Use when duplicate durable agent lessons should be consolidated.",
            },
        )
        assert curator_duplicate.status_code == 200
        merge_plan = client.post("/skills/curator", json={"action": "merge_plan", "skill_id": "agent-note-copy"})
        assert merge_plan.status_code == 200
        assert merge_plan.json()["accepted"] is True
        assert Path(merge_plan.json()["proposal_path"]).exists()
        merge_apply = client.post(
            "/skills/curator",
            json={"action": "merge_apply", "revision": merge_plan.json()["proposal_id"]},
        )
        assert merge_apply.status_code == 200
        assert merge_apply.json()["archived_skill_ids"] == ["agent-note-copy"]
        assert not (workspace_skills / "agent-note-copy").exists()

        curator_report = client.post("/skills/curator", json={"action": "report"})
        assert curator_report.status_code == 200
        assert curator_report.json()["mode"] == "curator"
        assert curator_report.json()["counts"]["tracked"] >= 1

        curator_feedback = client.post(
            "/skills/curator",
            json={
                "action": "feedback",
                "skill_id": "agent-note",
                "outcome": "success",
                "rationale": "Skill captured a reusable project lesson.",
                "feedback_source": "user",
                "confidence": 0.8,
            },
        )
        assert curator_feedback.status_code == 200
        assert curator_feedback.json()["accepted"] is True
        assert curator_feedback.json()["outcome"] == "success"
        assert curator_feedback.json()["feedback_source"] == "user"
        assert curator_feedback.json()["confidence"] == 0.8

        curator_curate = client.post("/skills/curator", json={"action": "curate", "dry_run": True})
        assert curator_curate.status_code == 200
        assert Path(curator_curate.json()["run_json_path"]).exists()
        assert Path(curator_curate.json()["report_path"]).exists()

        curator_automation = client.get("/skills/curator/automation")
        assert curator_automation.status_code == 200
        assert curator_automation.json()["enabled"] is True

        curator_automation_run = client.post("/skills/curator/automation/run", json={"force_run": True})
        assert curator_automation_run.status_code == 200
        assert curator_automation_run.json()["ran"] is True
        assert curator_automation_run.json()["reason"] == "forced"
        assert curator_automation_run.json()["report"]["accepted"] is True
        assert "recommendations" in curator_automation_run.json()["report"]

        events = drain_system_events(event_queue)
        deps.system_event_bus.unsubscribe(event_queue)
        assert any(event.event == "skills_changed" and event.data.get("curator") is True for event in events)
        curator_events = [event for event in events if event.event == "skills_changed" and event.data.get("curator") is True]
        assert any("recommendations" in event.data for event in curator_events)
        assert any("recommendation_count" in event.data for event in curator_events)

        uninstall = client.post("/skills/manage", json={"action": "uninstall", "skill_id": "governed-skill"})
        assert uninstall.status_code == 200


def test_gateway_upserts_json_mcp_servers(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.gateway.services._runtime_repo_root", lambda: contract_tmp_path)
    app = gateway_app_factory()

    with TestClient(app) as client:
        response = client.post(
            "/mcp/servers/batch",
            json={
                "config_text": """
// 示例:
{
  "mcpServers": {
    "fetch": {
      "enabled": false,
      "type": "sse",
      "url": "https://mcp.api-inference.modelscope.net/2ade34b743da4d/sse"
    }
  }
}
""",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["upserted"] == ["fetch"]
        assert project_mcp_config_path(contract_tmp_path).exists()
        saved = json.loads(project_mcp_config_path(contract_tmp_path).read_text(encoding="utf-8"))
        assert saved["mcpServers"]["fetch"]["type"] == "sse"
        assert not home_config_path(contract_tmp_path).exists()
        assert any(item["server_id"] == "fetch" for item in payload["servers"])

        delete_response = client.delete("/mcp/servers/fetch")
        assert delete_response.status_code == 200
        delete_payload = delete_response.json()
        assert delete_payload["deleted"] is True
        assert delete_payload["server_id"] == "fetch"
        assert all(item["server_id"] != "fetch" for item in delete_payload["servers"])
        listed_after_delete = client.get("/mcp/servers")
        assert listed_after_delete.status_code == 200
        assert all(item["server_id"] != "fetch" for item in listed_after_delete.json())
        saved_after_delete = json.loads(project_mcp_config_path(contract_tmp_path).read_text(encoding="utf-8"))
        assert "fetch" not in saved_after_delete["mcpServers"]


def test_gateway_delete_mcp_server_preserves_remaining_configured_servers(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.gateway.services._runtime_repo_root", lambda: contract_tmp_path)
    app = gateway_app_factory()

    with TestClient(app) as client:
        response = client.post(
            "/mcp/servers/batch",
            json={
                "config_text": json.dumps(
                    {
                        "mcpServers": {
                            "fetch": {
                                "transport": "sse",
                                "url": "https://example.test/fetch-sse",
                            },
                            "docs": {
                                "transport": "streamable_http",
                                "url": "https://example.test/docs-mcp",
                            },
                        }
                    }
                )
            },
        )
        assert response.status_code == 200

        deps = client.app.state.runtime_deps
        event_queue = deps.system_event_bus.subscribe()
        delete_response = client.delete("/mcp/servers/fetch")
        events = drain_system_events(event_queue)
        deps.system_event_bus.unsubscribe(event_queue)

        assert delete_response.status_code == 200
        payload = delete_response.json()
        assert_mcp_reload_events_match_response(events, payload)
        by_id = {item["server_id"]: item for item in payload["servers"]}
        assert "fetch" not in by_id
        assert by_id["docs"]["transport_kind"] == "http"
        assert payload["reload"]["scope"] == "all"
        saved = json.loads(project_mcp_config_path(contract_tmp_path).read_text(encoding="utf-8"))
        assert sorted(saved["mcpServers"]) == ["docs"]

        listed = client.get("/mcp/servers")
        assert listed.status_code == 200
        listed_by_id = {item["server_id"]: item for item in listed.json()}
        assert "fetch" not in listed_by_id
        assert listed_by_id["docs"]["transport_kind"] == "http"


def test_gateway_upserts_streamable_http_mcp_server(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.gateway.services._runtime_repo_root", lambda: contract_tmp_path)
    app = gateway_app_factory()

    with TestClient(app) as client:
        response = client.post(
            "/mcp/servers/batch",
            json={
                "config_text": json.dumps(
                    {
                        "mcpServers": {
                            "ChatPPT-MCP": {
                                "type": "streamable_http",
                                "url": "https://mcp.api-inference.modelscope.net//mcp",
                            }
                        }
                    }
                )
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["upserted"] == ["ChatPPT-MCP"]
        server = next(item for item in payload["servers"] if item["server_id"] == "ChatPPT-MCP")
        assert server["transport_kind"] == "http"
        assert server["enabled"] is True


def test_gateway_upsert_reload_normalizes_mcp_transport_field(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.gateway.services._runtime_repo_root", lambda: contract_tmp_path)
    app = gateway_app_factory()

    with TestClient(app) as client:
        deps = client.app.state.runtime_deps
        event_queue = deps.system_event_bus.subscribe()
        response = client.post(
            "/mcp/servers/batch",
            json={
                "config_text": json.dumps(
                    {
                        "mcpServers": {
                            "events": {
                                "transport": "sse",
                                "url": "https://example.test/sse",
                            },
                            "chatppt": {
                                "transport": "streamable_http",
                                "url": "https://example.test/mcp",
                            },
                            "direct_kind": {
                                "transport_kind": "streamable_http",
                                "url": "https://example.test/direct-mcp",
                            },
                        }
                    }
                )
            },
        )
        events = drain_system_events(event_queue)
        deps.system_event_bus.unsubscribe(event_queue)

        assert response.status_code == 200
        payload = response.json()
        assert_mcp_reload_events_match_response(events, payload)
        by_id = {item["server_id"]: item for item in payload["servers"]}
        assert by_id["events"]["transport_kind"] == "sse"
        assert by_id["chatppt"]["transport_kind"] == "http"
        assert by_id["direct_kind"]["transport_kind"] == "http"
        assert payload["reload"]["scope"] == "all"
        assert "mcp_servers_connected" in payload["reload"]

        saved = json.loads(project_mcp_config_path(contract_tmp_path).read_text(encoding="utf-8"))
        assert saved["mcpServers"]["events"]["transport"] == "sse"
        assert saved["mcpServers"]["chatppt"]["transport"] == "streamable_http"
        assert saved["mcpServers"]["direct_kind"]["transport_kind"] == "streamable_http"

        listed = client.get("/mcp/servers")
        assert listed.status_code == 200
        listed_by_id = {item["server_id"]: item for item in listed.json()}
        assert listed_by_id["events"]["transport_kind"] == "sse"
        assert listed_by_id["chatppt"]["transport_kind"] == "http"
        assert listed_by_id["direct_kind"]["transport_kind"] == "http"


def test_gateway_installs_local_plugin_and_bundled_mcp(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.gateway.services._runtime_repo_root", lambda: contract_tmp_path)
    plugin_source = contract_tmp_path / "plugin-source"
    plugin_source.mkdir(parents=True)
    (plugin_source / "plugin.yaml").write_text(
        "name: demo-plugin\n"
        "inline_tools:\n"
        "  - name: demo_plugin_tool\n"
        "    display_name: Demo Plugin Tool\n",
        encoding="utf-8",
    )
    (plugin_source / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"sample-mcp": {"enabled": False, "command": "echo"}}}),
        encoding="utf-8",
    )

    app = gateway_app_factory()
    with TestClient(app) as client:
        response = client.post(
            "/plugins/install",
            json={"source": str(plugin_source), "enable": True, "force": True},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["plugin_id"] == "demo-plugin"
        assert payload["tool_count"] == 1
        assert payload["bundled_mcp_servers"] == ["sample-mcp"]
        assert home_plugin_config_path(contract_tmp_path).exists()
        assert home_config_path(contract_tmp_path).exists()
        plugins = client.get("/plugins")
        assert plugins.status_code == 200
        assert any(item["plugin_id"] == "demo-plugin" for item in plugins.json())


def test_gateway_ignores_legacy_plugin_memory_metadata_and_keeps_engine_catalog_builtin(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.gateway.services._runtime_repo_root", lambda: contract_tmp_path)
    plugin_source = contract_tmp_path / "memory-plugin"
    plugin_source.mkdir(parents=True)
    (plugin_source / "plugin.json").write_text(
        json.dumps(
            {
                "name": "legacy-memory-metadata-plugin",
                "display_name": "Legacy Memory Metadata Plugin",
                "memory_providers": [
                    {
                        "provider_id": "plugin-local-memory",
                        "display_name": "Plugin Local Memory",
                        "kind": "hcms",
                        "roles": ["sync", "session_end", "delegation"],
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    app = gateway_app_factory()
    with TestClient(app) as client:
        install = client.post(
            "/plugins/install",
            json={"source": str(plugin_source), "enable": True, "force": True},
        )
        assert install.status_code == 200

        plugins = client.get("/plugins")
        assert plugins.status_code == 200
        installed = next(item for item in plugins.json() if item["plugin_id"] == "legacy-memory-metadata-plugin")
        assert "memory_provider_count" not in installed
        assert "memory_providers" not in installed

        engines = client.post("/memory/engines/reload")
        assert engines.status_code == 200
        assert {item["engine_id"] for item in engines.json()} == {"hcms"}

        test = client.post("/memory/engines/hcms/test")
        assert test.status_code == 200
        assert test.json()["ok"] is True


def test_gateway_lists_plugin_catalog_and_marks_installed(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.gateway.services._runtime_repo_root", lambda: contract_tmp_path)
    plugin_source = contract_tmp_path / "plugins" / "catalog-demo"
    plugin_source.mkdir(parents=True)
    (plugin_source / "plugin.json").write_text(
        json.dumps(
            {
                "name": "catalog-demo",
                "display_name": "Catalog Demo",
                "description": "Installable demo plugin",
                "version": "0.1.0",
                "inline_tools": [{"name": "catalog_demo_tool", "display_name": "Catalog Demo Tool"}],
                "resources": [{"resource_id": "catalog-demo-readme", "title": "Catalog Demo README"}],
                "prompts": [{"prompt_id": "catalog-demo-prompt", "title": "Catalog Demo Prompt"}],
                "catalog_metadata": {"publisher": "tests", "trust_level": "local-test"},
            }
        ),
        encoding="utf-8",
    )
    (plugin_source / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"catalog-demo-mcp": {"enabled": False, "command": "echo"}}}),
        encoding="utf-8",
    )
    catalog_path = contract_tmp_path / "plugins" / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "plugin_id": "catalog-demo",
                        "name": "Catalog Demo",
                        "description": "Catalog entry",
                        "source": "./catalog-demo",
                        "tags": ["test"],
                        "permissions": ["local plugin files"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    app = gateway_app_factory()
    with TestClient(app) as client:
        catalog = client.get("/plugins/catalog")
        assert catalog.status_code == 200
        entry = next(item for item in catalog.json() if item["plugin_id"] == "catalog-demo")
        assert entry["plugin_id"] == "catalog-demo"
        assert entry["installed"] is False
        assert entry["source"].endswith("catalog-demo")
        assert entry["tool_names"] == ["catalog_demo_tool"]
        assert entry["mcp_servers"] == ["catalog-demo-mcp"]

        install = client.post(
            "/plugins/install",
            json={"source": entry["source"], "plugin_id": entry["plugin_id"], "enable": True, "force": True},
        )
        assert install.status_code == 200

        catalog_after_install = client.get("/plugins/catalog")
        assert catalog_after_install.status_code == 200
        installed_entry = next(item for item in catalog_after_install.json() if item["plugin_id"] == "catalog-demo")
        assert installed_entry["installed"] is True
        assert installed_entry["enabled"] is True


def test_gateway_manages_plugin_registries_and_scans_plugin_directories(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.gateway.services._runtime_repo_root", lambda: contract_tmp_path)
    registry_root = contract_tmp_path / "team-plugins"
    plugin_source = registry_root / "team-plugin"
    plugin_source.mkdir(parents=True)
    (plugin_source / "plugin.yaml").write_text(
        "name: team-plugin\n"
        "display_name: Team Plugin\n"
        "description: Directory-scanned plugin\n"
        "version: 0.2.0\n"
        "inline_tools:\n"
        "  - name: team_plugin_tool\n"
        "    display_name: Team Plugin Tool\n",
        encoding="utf-8",
    )

    app = gateway_app_factory()
    with TestClient(app) as client:
        add = client.post(
            "/plugins/registries",
            json={
                "registry_id": "team",
                "name": "Team Registry",
                "source": str(registry_root),
                "trust_level": "team",
            },
        )
        assert add.status_code == 200
        add_payload = add.json()
        assert add_payload["registry"]["registry_id"] == "team"
        assert add_payload["registry"]["entry_count"] == 1
        assert home_plugin_registry_config_path(contract_tmp_path).exists()

        registries = client.get("/plugins/registries")
        assert registries.status_code == 200
        assert any(item["registry_id"] == "team" for item in registries.json())

        catalog = client.get("/plugins/catalog")
        assert catalog.status_code == 200
        team_entry = next(item for item in catalog.json() if item["plugin_id"] == "team-plugin")
        assert team_entry["registry_id"] == "team"
        assert team_entry["registry_name"] == "Team Registry"
        assert team_entry["tool_names"] == ["team_plugin_tool"]

        refresh = client.post("/plugins/registries/team/refresh")
        assert refresh.status_code == 200
        assert refresh.json()["registry"]["entry_count"] == 1

        delete = client.delete("/plugins/registries/team")
        assert delete.status_code == 200
        assert delete.json()["deleted"] is True
        after_delete = client.get("/plugins/catalog")
        assert after_delete.status_code == 200
        assert all(item["plugin_id"] != "team-plugin" for item in after_delete.json())


def test_gateway_scans_plugin_cache_sources(
    gateway_app_factory,
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.gateway.services._runtime_repo_root", lambda: contract_tmp_path)
    registry_root = contract_tmp_path / "plugin-cache"
    plugin_source = registry_root / "browser" / "26.0.0"
    plugin_source.mkdir(parents=True)
    (plugin_source / "anvil.plugin.json").write_text(
        json.dumps(
            {
                "name": "browser",
                "version": "26.0.0",
                "description": "Root fallback description",
                "author": {"name": "OpenAI"},
                "keywords": ["browser", "automation"],
                "interface": {
                    "displayName": "Browser",
                    "shortDescription": "Control the in-app browser with Anvil",
                    "developerName": "OpenAI",
                },
            }
        ),
        encoding="utf-8",
    )

    app = gateway_app_factory()
    with TestClient(app) as client:
        add = client.post(
            "/plugins/registries",
            json={
                "registry_id": "plugin-cache",
                "name": "Plugin cache",
                "source": str(registry_root),
                "trust_level": "curated",
            },
        )
        assert add.status_code == 200
        assert add.json()["registry"]["entry_count"] == 1

        catalog = client.get("/plugins/catalog")
        assert catalog.status_code == 200
        entry = next(item for item in catalog.json() if item["plugin_id"] == "browser")
        assert entry["name"] == "Browser"
        assert entry["description"] == "Control the in-app browser with Anvil"
        assert entry["author"] == "OpenAI"
        assert entry["tags"] == ["browser", "automation"]
        assert entry["source"].endswith(str(Path("browser") / "26.0.0"))
