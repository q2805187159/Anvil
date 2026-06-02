from __future__ import annotations

import json
import sys
import types
import warnings
import zipfile
from pathlib import Path

import pytest

import anvil.extensions.service as extensions_service_module
from anvil.config import EffectiveConfig, ExtensionsConfig, McpServerConfig, PluginConfig, normalize_loaded_config
from anvil.extensions import ExternalCapabilityStatus, ExtensionsService


def make_config() -> EffectiveConfig:
    return EffectiveConfig(
        extensions=ExtensionsConfig(
            mcp_servers={
                "github": McpServerConfig(
                    enabled=True,
                    transport_kind="stdio",
                    connection_config={
                        "inline_tools": [
                            {
                                "name": "repo_search",
                                "display_name": "Repo Search",
                                "capability_group": "web",
                            }
                        ]
                    },
                ),
                "broken": McpServerConfig(
                    enabled=True,
                    transport_kind="http",
                    connection_config={"fail_materialization": True},
                ),
            }
        )
    )


def test_extensions_service_handles_materialized_and_failed_servers() -> None:
    service = ExtensionsService()
    result = service.discover(config=make_config(), fingerprint="cfg-1")

    statuses = {item.server_id: item.status for item in result.materializations}
    assert statuses["github"] is ExternalCapabilityStatus.READY
    assert statuses["broken"] is ExternalCapabilityStatus.FAILED
    assert result.effective_mcp_servers == ("github",)


def test_extensions_refresh_rebuilds_one_server_only() -> None:
    service = ExtensionsService()
    config = make_config()
    service.discover(config=config, fingerprint="cfg-1")
    config.extensions.mcp_servers["github"].connection_config["inline_tools"][0]["display_name"] = "Repo Search v2"

    refreshed = service.refresh_server(config=config, fingerprint="cfg-1", server_id="github")

    assert refreshed.tools[0].display_name == "Repo Search v2"


def test_extensions_service_materializes_stdio_http_and_sse_servers_via_mcp_client(monkeypatch) -> None:
    class FakeTool:
        def __init__(self, name: str, description: str) -> None:
            self.name = name
            self.description = description
            self.func = lambda **kwargs: f"{name}:{kwargs}"
            self.args_schema = {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            }

    class FakeClient:
        def __init__(self, servers_config, tool_interceptors=None, tool_name_prefix=True):
            self.servers_config = servers_config

        async def get_tools(self):
            assert len(self.servers_config) == 1
            server_id = next(iter(self.servers_config))
            return [
                FakeTool(f"{server_id}_tool", f"{server_id} tool"),
            ]

    fake_module = types.ModuleType("langchain_mcp_adapters.client")
    fake_module.MultiServerMCPClient = FakeClient
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_module)

    config = EffectiveConfig(
        extensions=ExtensionsConfig(
            mcp_servers={
                "stdio_server": McpServerConfig(
                    enabled=True,
                    transport_kind="stdio",
                    connection_config={
                        "command": "python",
                        "args": ["-m", "fake_stdio_server"],
                    },
                ),
                "http_server": McpServerConfig(
                    enabled=True,
                    transport_kind="http",
                    connection_config={
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer demo"},
                    },
                ),
                "sse_server": McpServerConfig(
                    enabled=True,
                    transport_kind="sse",
                    connection_config={
                        "url": "https://example.com/mcp-sse",
                    },
                ),
            }
        )
    )

    result = ExtensionsService().discover(config=config, fingerprint="cfg-transport")

    statuses = {item.server_id: item.status for item in result.materializations}
    assert statuses == {
        "http_server": ExternalCapabilityStatus.READY,
        "sse_server": ExternalCapabilityStatus.READY,
        "stdio_server": ExternalCapabilityStatus.READY,
    }
    assert result.effective_mcp_servers == ("http_server", "sse_server", "stdio_server")
    assert all(item.tools for item in result.materializations)
    assert [item.tools[0].name for item in result.materializations] == [
        "http_server_tool",
        "sse_server_tool",
        "stdio_server_tool",
    ]


def test_extensions_service_keeps_placeholder_mcp_servers_configured_not_visible() -> None:
    config = EffectiveConfig(
        extensions=ExtensionsConfig(
            mcp_servers={
                "filesystem": McpServerConfig(
                    enabled=True,
                    transport_kind="stdio",
                    connection_config={
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/files"],
                    },
                )
            }
        )
    )

    result = ExtensionsService().discover(config=config, fingerprint="cfg-placeholder")
    materialization = result.materializations[0]

    assert materialization.status is ExternalCapabilityStatus.CONFIGURED
    assert materialization.ready is False
    assert materialization.tools == ()
    assert result.effective_mcp_servers == ()
    assert "placeholder" in materialization.diagnostics[0]


def test_extensions_service_filters_known_streamable_http_adapter_deprecation(monkeypatch) -> None:
    class FakeTool:
        name = "live_tool"
        description = "Live tool"
        func = lambda **kwargs: "ok"
        args_schema = {}

    class FakeClient:
        def __init__(self, servers_config, tool_interceptors=None, tool_name_prefix=True):
            self.servers_config = servers_config

        async def get_tools(self):
            globals()["__warningregistry__"] = {}
            original_module = globals().get("__name__")
            globals()["__name__"] = "langchain_mcp_adapters.sessions"
            warnings.warn(
                "Use `streamable_http_client` instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            globals()["__name__"] = original_module
            return [FakeTool()]

    fake_module = types.ModuleType("langchain_mcp_adapters.client")
    fake_module.MultiServerMCPClient = FakeClient
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_module)

    config = EffectiveConfig(
        extensions=ExtensionsConfig(
            mcp_servers={
                "http_server": McpServerConfig(
                    enabled=True,
                    transport_kind="http",
                    connection_config={"url": "https://example.com/mcp"},
                )
            }
        )
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        result = ExtensionsService().discover(config=config, fingerprint="cfg-http-warning")

    assert result.materializations[0].status is ExternalCapabilityStatus.READY
    assert result.materializations[0].tools[0].name == "live_tool"


def test_extensions_service_supports_mcp_filters_and_plugin_materialization() -> None:
    config = EffectiveConfig(
        extensions=ExtensionsConfig(
            mcp_servers={
                "github": McpServerConfig(
                    enabled=True,
                    transport_kind="stdio",
                    tool_prefix="gh_",
                    tool_allowlist=["repo_search"],
                    tool_denylist=["issue_search"],
                    connection_config={
                        "inline_tools": [
                            {"name": "repo_search", "display_name": "Repo Search", "capability_group": "research"},
                            {"name": "issue_search", "display_name": "Issue Search", "capability_group": "research"},
                        ],
                        "inline_resources": [
                            {"resource_id": "playbook", "title": "Playbook", "path": __file__}
                        ],
                        "inline_prompts": [
                            {"prompt_id": "triage", "title": "Triage", "arguments": ["repo"], "template": "triage {repo}"}
                        ],
                    },
                )
            },
            plugins={
                "ops": PluginConfig(
                    enabled=True,
                    source_path="plugins/ops",
                    skill_roots=("skills/ops",),
                    inline_tools=(
                        {"name": "ops_summary", "display_name": "Ops Summary", "capability_group": "plugin"},
                    ),
                    resources=(
                        {"resource_id": "ops-guide", "title": "Ops Guide", "path": __file__},
                    ),
                    prompts=(
                        {"prompt_id": "ops-prompt", "title": "Ops Prompt", "arguments": ["target"], "template": "target={target}"},
                    ),
                    catalog_metadata={"tier": "trusted"},
                )
            },
        )
    )

    service = ExtensionsService()
    result = service.discover(config=config, fingerprint="cfg-plugin")

    github = next(item for item in result.materializations if item.server_id == "github")
    plugin = next(item for item in result.materializations if item.server_id == "ops")

    assert [tool.name for tool in github.tools] == ["gh_repo_search"]
    assert github.resources[0].resource_id == "playbook"
    assert github.prompts[0].prompt_id == "triage"
    assert github.connected is True
    assert plugin.source_kind == "plugin"
    assert [tool.name for tool in plugin.tools] == ["ops_summary"]
    assert result.effective_plugin_ids == ("ops",)


def test_extensions_service_hides_legacy_agent_skill_download_tool() -> None:
    config = EffectiveConfig(
        extensions=ExtensionsConfig(
            mcp_servers={
                "skills_compat": McpServerConfig(
                    enabled=True,
                    transport_kind="stdio",
                    connection_config={
                        "inline_tools": [
                            {
                                "name": "get_agent_skill",
                                "display_name": (
                                    "Get an Agent Skill by ID, including all its files "
                                    "(SKILL.md, reference docs, scripts, etc.). Returns the skill metadata "
                                    "and file contents. Save to .claude/skills/{slug}/SKILL.md and "
                                    ".claude/skills/{slug}/[other files] structure if user asks to download."
                                ),
                                "capability_group": "skill_governance",
                            },
                            {"name": "repo_search", "display_name": "Repo Search", "capability_group": "research"},
                        ],
                    },
                )
            }
        )
    )

    result = ExtensionsService().discover(config=config, fingerprint="cfg-legacy-skill-download")

    tools = result.materializations[0].tools
    assert [tool.name for tool in tools] == ["repo_search"]


def test_extensions_service_supports_hermes_mcp_tools_policy() -> None:
    normalized = normalize_loaded_config(
        {
            "mcpServers": {
                "github": {
                    "enabled": True,
                    "type": "stdio",
                    "inline_tools": [
                        {"name": "repo_search", "display_name": "Repo Search"},
                        {"name": "issue_search", "display_name": "Issue Search"},
                    ],
                    "inline_resources": [{"resource_id": "playbook", "title": "Playbook", "path": __file__}],
                    "inline_prompts": [{"prompt_id": "triage", "title": "Triage"}],
                    "tools": {
                        "include": ["repo_search"],
                        "resources": False,
                        "prompts": False,
                    },
                }
            }
        }
    )
    config = EffectiveConfig(**normalized)

    materialization = ExtensionsService().discover(config=config, fingerprint="cfg-hermes-tools").materializations[0]

    assert [tool.name for tool in materialization.tools] == ["repo_search"]
    assert materialization.resources == ()
    assert materialization.prompts == ()


def test_extensions_service_treats_empty_hermes_include_as_resource_only() -> None:
    normalized = normalize_loaded_config(
        {
            "mcpServers": {
                "docs": {
                    "enabled": True,
                    "inline_tools": [{"name": "search", "display_name": "Search"}],
                    "inline_resources": [{"resource_id": "guide", "title": "Guide", "path": __file__}],
                    "tools": {
                        "include": [],
                        "resources": True,
                        "prompts": False,
                    },
                }
            }
        }
    )
    config = EffectiveConfig(**normalized)

    materialization = ExtensionsService().discover(config=config, fingerprint="cfg-resource-only").materializations[0]

    assert materialization.tools == ()
    assert materialization.resources[0].resource_id == "guide"
    assert materialization.prompts == ()


def test_extensions_service_exposes_resources_prompts_and_reconnect_state() -> None:
    config = EffectiveConfig(
        extensions=ExtensionsConfig(
            mcp_servers={
                "github": McpServerConfig(
                    enabled=True,
                    transport_kind="stdio",
                    connection_config={
                        "inline_tools": [{"name": "repo_search", "display_name": "Repo Search"}],
                        "inline_resources": [{"resource_id": "guide", "title": "Guide", "path": __file__}],
                        "inline_prompts": [{"prompt_id": "triage", "title": "Triage", "template": "triage {repo}", "arguments": ["repo"]}],
                    },
                )
            }
        )
    )

    service = ExtensionsService()
    first = service.discover(config=config, fingerprint="cfg-runtime")
    refreshed = service.reconnect_server(config=config, fingerprint="cfg-runtime", server_id="github")
    resources = service.list_resources(config=config, fingerprint="cfg-runtime", server_id="github")
    prompts = service.list_prompts(config=config, fingerprint="cfg-runtime", server_id="github")
    rendered = service.get_prompt(
        config=config,
        fingerprint="cfg-runtime",
        server_id="github",
        prompt_id="triage",
        arguments={"repo": "anvil"},
    )
    resource_payload = service.read_resource(
        config=config,
        fingerprint="cfg-runtime",
        server_id="github",
        resource_id="guide",
    )

    assert first.materializations[0].connected is True
    assert refreshed.reconnect_count == 1
    assert resources[0].resource_id == "guide"
    assert prompts[0].prompt_id == "triage"
    assert rendered["rendered"] == "triage anvil"
    assert "test_extensions_service.py" in str(resource_payload["content"])


def test_extensions_install_plugin_archive_stops_at_scan_budget_before_extraction(
    contract_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extensions_service_module, "DEFAULT_PLUGIN_PACKAGE_SCAN_LIMIT", 3, raising=False)
    monkeypatch.setattr(extensions_service_module, "MAX_PLUGIN_PACKAGE_SCAN_LIMIT", 3, raising=False)

    def fail_extractall(*_args, **_kwargs) -> None:
        raise AssertionError("plugin package scan truncation must stop before archive extraction")

    monkeypatch.setattr(extensions_service_module.zipfile.ZipFile, "extractall", fail_extractall)

    archive_path = contract_tmp_path / "packages" / "large-plugin.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps({"name": "large-plugin"}))
        for index in range(6):
            archive.writestr(f"docs/ref-{index:02}.md", "reference")

    with pytest.raises(ValueError, match="plugin package scan truncated"):
        ExtensionsService().install_plugin(repo_root=contract_tmp_path, source=str(archive_path), force=True)

    assert not (contract_tmp_path / ".anvil" / "plugins" / "large-plugin").exists()


def test_extensions_install_plugin_archive_blocks_path_traversal(
    contract_tmp_path: Path,
) -> None:
    archive_path = contract_tmp_path / "packages" / "bad-plugin.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps({"name": "bad-plugin"}))
        archive.writestr("../escape.txt", "escape")

    with pytest.raises(ValueError, match="path traversal"):
        ExtensionsService().install_plugin(repo_root=contract_tmp_path, source=str(archive_path), force=True)

    assert not (contract_tmp_path / "escape.txt").exists()
    assert not (contract_tmp_path / ".anvil" / "plugins" / "bad-plugin").exists()


def test_extensions_install_plugin_directory_stops_at_scan_budget(
    contract_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extensions_service_module, "DEFAULT_PLUGIN_TREE_SCAN_LIMIT", 3, raising=False)
    monkeypatch.setattr(extensions_service_module, "MAX_PLUGIN_TREE_SCAN_LIMIT", 3, raising=False)

    plugin_source = contract_tmp_path / "plugin-source"
    plugin_source.mkdir(parents=True)
    (plugin_source / "plugin.json").write_text(json.dumps({"name": "large-dir-plugin"}), encoding="utf-8")
    for index in range(6):
        (plugin_source / f"ref-{index:02}.md").write_text("reference", encoding="utf-8")

    with pytest.raises(ValueError, match="plugin source scan truncated"):
        ExtensionsService().install_plugin(repo_root=contract_tmp_path, source=str(plugin_source), force=True)

    assert not (contract_tmp_path / ".anvil" / "plugins" / "large-dir-plugin").exists()
