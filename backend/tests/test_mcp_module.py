from __future__ import annotations

from anvil.config import ConfigService, McpTransportKind, normalize_loaded_config
from anvil.mcp import (
    DEFAULT_MCP_SERVERS,
    delete_mcp_server_from_config_file,
    has_real_transport_config,
    mcp_server_placeholder_config_reason,
    mcp_server_missing_env_names,
    default_mcp_config_payload,
    mcp_server_model_visible,
    normalize_mcp_server_mapping,
    parse_mcp_servers_config_text,
    redact_sensitive_config,
    upsert_mcp_servers_in_config_file,
)


def test_default_mcp_payload_contains_enabled_user_editable_servers() -> None:
    payload = default_mcp_config_payload()

    assert sorted(payload["mcp_servers"]) == ["filesystem", "github", "postgres", "prompts.chat"]
    assert payload["mcp_servers"]["filesystem"]["enabled"] is True
    assert payload["mcp_servers"]["github"]["env"] == {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    assert payload["mcp_servers"]["prompts.chat"]["url"] == "https://prompts.chat/api/mcp"
    assert all(server["enabled"] is True for server in DEFAULT_MCP_SERVERS.values())


def test_mcp_visibility_and_redaction_are_harness_owned() -> None:
    assert mcp_server_model_visible(type("Visible", (), {"enabled": True, "ready": True, "auth_required": False, "status": "ready"})())
    assert not mcp_server_model_visible(type("Hidden", (), {"enabled": True, "ready": False, "auth_required": True, "status": "auth_required"})())
    redacted = redact_sensitive_config(
        {
            "headers": {"Authorization": "Bearer secret", "X-Trace": "ok"},
            "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN", "OPENAI_API_KEY": "secret"},
        }
    )
    assert redacted["headers"]["Authorization"] == "[REDACTED]"
    assert redacted["headers"]["X-Trace"] == "ok"
    assert redacted["env"]["GITHUB_TOKEN"] == "$GITHUB_TOKEN"
    assert redacted["env"]["OPENAI_API_KEY"] == "[REDACTED]"


def test_mcp_normalizer_accepts_legacy_server_shapes() -> None:
    normalized = normalize_loaded_config(
        {
            "mcpServers": {
                "github": {
                    "enabled": False,
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"},
                    "description": "GitHub MCP server for repository operations",
                    "tools": {
                        "include": ["search_code"],
                        "resources": False,
                        "prompts": False,
                    },
                },
                "prompts.chat": {
                    "url": "https://prompts.chat/api/mcp",
                },
            }
        }
    )
    result = ConfigService().resolve([_layer(normalized)])

    github = result.effective_config.extensions.mcp_servers["github"]
    prompts = result.effective_config.extensions.mcp_servers["prompts.chat"]

    assert github.enabled is False
    assert github.description == "GitHub MCP server for repository operations"
    assert github.transport_kind == McpTransportKind.STDIO
    assert github.connection_config["command"] == "npx"
    assert github.connection_config["args"] == ["-y", "@modelcontextprotocol/server-github"]
    assert github.connection_config["env"] == {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    assert github.tool_allowlist == ["search_code"]
    assert github.tool_allowlist_active is True
    assert github.resource_policy == {"enabled": False}
    assert github.prompt_policy == {"enabled": False}
    assert prompts.transport_kind == McpTransportKind.HTTP
    assert prompts.connection_config["url"] == "https://prompts.chat/api/mcp"


def test_mcp_config_text_parser_accepts_comments_and_multiple_roots() -> None:
    parsed = parse_mcp_servers_config_text(
        """
// comment
{
  "mcp": {
    "servers": [
      {"id": "fetch", "transport": "sse", "url": "https://example.test/sse"}
    ]
  }
}
"""
    )

    assert parsed == {"fetch": {"transport": "sse", "url": "https://example.test/sse"}}


def test_mcp_config_upsert_delete_uses_home_config_yaml(contract_tmp_path) -> None:
    path = contract_tmp_path / ".anvil" / "config.yaml"
    written = upsert_mcp_servers_in_config_file(
        path,
        {
            "github": {
                "enabled": False,
                "type": "stdio",
                "command": "npx",
            }
        },
    )
    upsert_mcp_servers_in_config_file(written, {"postgres": {"enabled": False, "type": "stdio", "command": "npx"}})
    delete_mcp_server_from_config_file(written, "github")

    import yaml

    payload = yaml.safe_load(written.read_text(encoding="utf-8"))
    assert written == path.resolve()
    assert sorted(payload["mcp_servers"]) == ["postgres"]


def test_mcp_config_upsert_delete_preserves_project_mcp_json_shape(contract_tmp_path) -> None:
    path = contract_tmp_path / ".anvil" / "mcp.json"
    written = upsert_mcp_servers_in_config_file(
        path,
        {
            "fetch": {
                "transport": "sse",
                "url": "https://example.test/sse",
            }
        },
    )
    upsert_mcp_servers_in_config_file(written, {"docs": {"transport": "streamable_http"}})
    delete_mcp_server_from_config_file(written, "fetch")

    import json

    payload = json.loads(written.read_text(encoding="utf-8"))
    assert written == path.resolve()
    assert "mcp_servers" not in payload
    assert sorted(payload["mcpServers"]) == ["docs"]


def test_mcp_server_mapping_normalizes_tools_include_empty_as_active_allowlist() -> None:
    normalized = normalize_mcp_server_mapping(
        {
            "resource_only": {
                "url": "https://example.test/mcp",
                "tools": {"include": [], "resources": True, "prompts": False},
            }
        }
    )

    server = normalized["resource_only"]
    assert server["transport_kind"] == "http"
    assert server["tool_allowlist"] == []
    assert server["tool_allowlist_active"] is True
    assert server["resource_policy"] == {"enabled": True}
    assert server["prompt_policy"] == {"enabled": False}


def _layer(data):
    from anvil.config import ConfigLayer, ConfigLayerKind

    return ConfigLayer(name="test", kind=ConfigLayerKind.USER, data=data)


def test_mcp_client_resolves_env_templates_and_sanitizes_errors(monkeypatch) -> None:
    from anvil.config import McpServerConfig
    from anvil.mcp import build_server_params, sanitize_mcp_error

    monkeypatch.setenv("MCP_TOKEN", "secret-token")
    server = McpServerConfig(
        transport_kind="http",
        connection_config={
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer $MCP_TOKEN"},
        },
        header_templates={"X-Api-Key": "${MCP_TOKEN}"},
    )

    params = build_server_params("remote", server)

    assert params["transport"] == "http"
    assert params["headers"]["Authorization"] == "Bearer secret-token"
    assert params["headers"]["X-Api-Key"] == "secret-token"
    assert sanitize_mcp_error("failed with Bearer secret-token") == "failed with [REDACTED]"


def test_mcp_placeholder_transport_config_is_not_treated_as_live() -> None:
    from anvil.config import McpServerConfig

    filesystem = McpServerConfig(
        transport_kind="stdio",
        connection_config={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/files"],
        },
    )
    postgres = McpServerConfig(
        transport_kind="stdio",
        connection_config={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"],
        },
    )

    assert not has_real_transport_config(filesystem)
    assert "placeholder" in str(mcp_server_placeholder_config_reason(filesystem))
    assert not has_real_transport_config(postgres)
    assert "placeholder" in str(mcp_server_placeholder_config_reason(postgres))


def test_mcp_server_missing_env_names_detects_unresolved_credentials(monkeypatch) -> None:
    from anvil.config import McpServerConfig

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    server = McpServerConfig(
        transport_kind="stdio",
        connection_config={
            "command": "npx",
            "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"},
            "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
        },
    )

    assert mcp_server_missing_env_names(server) == ("GITHUB_TOKEN", "MCP_TOKEN")


def test_mcp_server_missing_env_names_ignores_inline_prompt_placeholders(monkeypatch) -> None:
    from anvil.config import McpServerConfig

    monkeypatch.delenv("topic", raising=False)
    server = McpServerConfig(
        transport_kind="stdio",
        connection_config={
            "command": "npx",
            "inline_prompts": [
                {
                    "name": "summarize",
                    "display_name": "Summarize",
                    "summary": "Prompt with a runtime placeholder.",
                    "template": "Summarize $topic",
                }
            ],
        },
    )

    assert mcp_server_missing_env_names(server) == ()
