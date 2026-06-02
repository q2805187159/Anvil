from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from anvil.config.env_refs import env_ref_name, is_env_ref


BUNDLED_MCP_CONFIG_FILE_NAMES = (".mcp.json", "mcp.json")
SENSITIVE_CONFIG_KEYWORDS = ("api_key", "apikey", "token", "secret", "password", "authorization", "bearer", "credential")

DEFAULT_MCP_SERVERS: dict[str, dict[str, Any]] = {
    "filesystem": {
        "enabled": True,
        "type": "stdio",
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/path/to/allowed/files",
        ],
        "env": {},
        "description": "Provides filesystem access within allowed directories",
    },
    "github": {
        "enabled": True,
        "type": "stdio",
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-github",
        ],
        "env": {
            "GITHUB_TOKEN": "$GITHUB_TOKEN",
        },
        "description": "GitHub MCP server for repository operations",
    },
    "postgres": {
        "enabled": True,
        "type": "stdio",
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-postgres",
            "postgresql://localhost/mydb",
        ],
        "env": {},
        "description": "PostgreSQL database access",
    },
    "prompts.chat": {
        "enabled": True,
        "type": "http",
        "url": "https://prompts.chat/api/mcp",
        "description": "Remote prompt catalog MCP endpoint",
    },
}


@dataclass(frozen=True)
class McpServerVisibilityInput:
    enabled: bool
    ready: bool
    auth_required: bool
    status: str


def default_mcp_config_payload() -> dict[str, Any]:
    """Return default MCP servers in the Anvil config.yaml shape."""

    return {"mcp_servers": json.loads(json.dumps(DEFAULT_MCP_SERVERS))}


def mcp_server_model_visible(server: McpServerVisibilityInput | object) -> bool:
    return bool(
        bool(getattr(server, "enabled", False))
        and bool(getattr(server, "ready", False))
        and not bool(getattr(server, "auth_required", False))
        and str(getattr(server, "status", "")).lower() in {"enabled", "ready"}
    )


def iter_env_refs_in_config(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        if is_env_ref(value):
            refs.add(env_ref_name(value))
        return refs
    if isinstance(value, dict):
        for item in value.values():
            refs.update(iter_env_refs_in_config(item))
        return refs
    if isinstance(value, list | tuple):
        for item in value:
            refs.update(iter_env_refs_in_config(item))
    return refs


def redact_sensitive_config(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): redact_sensitive_config_value(str(key), item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_config(item) for item in value]
    return value


def redact_sensitive_config_value(key: str, value: object) -> object:
    if is_sensitive_config_key(key):
        if isinstance(value, str) and is_env_ref(value):
            return value
        if value in {None, ""}:
            return value
        return "[REDACTED]"
    return redact_sensitive_config(value)


def is_sensitive_config_key(key: str) -> bool:
    normalized = key.replace("-", "_").lower()
    return any(keyword in normalized for keyword in SENSITIVE_CONFIG_KEYWORDS)


def read_mcp_config_file(path: str | Path) -> dict[str, Any]:
    resolved_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in mcp config file '{resolved_path}': {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"mcp config file '{resolved_path}' must contain a mapping at the root")

    if "mcpServers" in payload:
        return {"mcpServers": payload["mcpServers"]}
    if "mcp" in payload or "extensions" in payload:
        return payload
    if "servers" in payload:
        return {"mcp": payload}
    if "mcp_servers" in payload:
        return {"extensions": {"mcp_servers": payload["mcp_servers"]}}
    return {"mcp": {"servers": []}}


def parse_mcp_servers_config_text(config_text: str) -> dict[str, object]:
    raw_text = config_text.strip()
    if not raw_text:
        raise ValueError("MCP config cannot be empty")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        payload = json.loads("\n".join(line for line in raw_text.splitlines() if not line.strip().startswith("//")))
    if not isinstance(payload, dict):
        raise ValueError("MCP config root must be an object")
    servers = extract_mcp_servers_from_payload(payload)
    if not servers:
        raise ValueError("MCP config must include at least one mcpServers entry")
    for server_id, server_config in servers.items():
        if not str(server_id).strip():
            raise ValueError("MCP server names must not be empty")
        if not isinstance(server_config, dict):
            raise ValueError(f"MCP server '{server_id}' must be an object")
    return servers


def extract_mcp_servers_from_payload(payload: dict[str, object]) -> dict[str, object]:
    if isinstance(payload.get("mcpServers"), dict):
        return dict(payload["mcpServers"])  # type: ignore[index]
    if isinstance(payload.get("mcp_servers"), dict):
        return dict(payload["mcp_servers"])  # type: ignore[index]
    extensions = payload.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get("mcp_servers"), dict):
        return dict(extensions["mcp_servers"])
    mcp = payload.get("mcp")
    if isinstance(mcp, dict):
        if isinstance(mcp.get("mcpServers"), dict):
            return dict(mcp["mcpServers"])
        if isinstance(mcp.get("servers"), dict):
            return dict(mcp["servers"])
        if isinstance(mcp.get("servers"), list):
            return {
                str(item["id"]): {key: value for key, value in item.items() if key != "id"}
                for item in mcp["servers"]
                if isinstance(item, dict) and item.get("id")
            }
    if isinstance(payload.get("servers"), list):
        return {
            str(item["id"]): {key: value for key, value in item.items() if key != "id"}
            for item in payload["servers"]  # type: ignore[index]
            if isinstance(item, dict) and item.get("id")
        }
    return {}


def normalize_mcp_server_mapping(raw_servers: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(server_id): normalize_mcp_server_config(server_config)
        for server_id, server_config in raw_servers.items()
        if str(server_id).strip() and isinstance(server_config, dict)
    }


def normalize_mcp_server_config(raw: dict[str, Any]) -> dict[str, Any]:
    connection_config = dict(raw.get("connection_config") or {})
    command = raw.get("command")
    if isinstance(command, list):
        if command:
            connection_config.setdefault("command", command[0])
        if len(command) > 1:
            connection_config.setdefault("args", command[1:])
    elif command is not None:
        connection_config.setdefault("command", command)
    for key in (
        "args",
        "env",
        "cwd",
        "url",
        "headers",
        "http_headers",
        "env_http_headers",
        "bearer_token_env_var",
        "allowed_paths",
        "inline_tools",
        "inline_resources",
        "inline_prompts",
        "capability_group",
        "deferred",
        "timeout",
        "connect_timeout",
    ):
        if raw.get(key) is not None:
            target_key = "headers" if key == "http_headers" else key
            connection_config.setdefault(target_key, raw[key])

    resource_policy = dict(raw.get("resource_policy") or {})
    prompt_policy = dict(raw.get("prompt_policy") or {})
    tools_policy = raw.get("tools")
    tool_allowlist = raw.get("tool_allowlist")
    tool_denylist = raw.get("tool_denylist")
    tool_allowlist_active = bool(raw.get("tool_allowlist_active", False))
    if isinstance(tools_policy, dict):
        if "include" in tools_policy:
            tool_allowlist = _string_list(tools_policy.get("include"))
            tool_allowlist_active = True
        if "exclude" in tools_policy:
            tool_denylist = _string_list(tools_policy.get("exclude"))
        if "resources" in tools_policy:
            resource_policy.setdefault("enabled", _bool_like(tools_policy.get("resources")))
        if "prompts" in tools_policy:
            prompt_policy.setdefault("enabled", _bool_like(tools_policy.get("prompts")))

    transport_kind = raw.get("transport_kind")
    if transport_kind is None:
        raw_type = str(raw.get("type") or raw.get("transport") or "").lower().strip()
        if raw_type in {"sse", "http", "streamable_http", "streamable-http"}:
            transport_kind = "http" if raw_type in {"streamable_http", "streamable-http"} else raw_type
        elif connection_config.get("url"):
            transport_kind = "http"
        else:
            transport_kind = "stdio"
    elif str(transport_kind).lower().strip() in {"streamable_http", "streamable-http", "streamable/http"}:
        transport_kind = "http"

    normalized = {
        "enabled": raw.get("enabled", True),
        "transport_kind": transport_kind,
        "startup_policy": raw.get("startup_policy", "lazy"),
        "refresh_policy": raw.get("refresh_policy", "fingerprint"),
        "approval_policy": raw.get("approval_policy", "runtime"),
        "connection_config": connection_config,
        "tool_allowlist_active": tool_allowlist_active,
    }
    for key in (
        "description",
        "tool_prefix",
        "collision_policy",
        "oauth",
        "env_resolution",
        "header_templates",
        "reconnect_policy",
        "healthcheck",
    ):
        if raw.get(key) is not None:
            normalized[key] = raw[key]
    if resource_policy:
        normalized["resource_policy"] = resource_policy
    if prompt_policy:
        normalized["prompt_policy"] = prompt_policy
    if tool_allowlist is not None:
        normalized["tool_allowlist"] = _string_list(tool_allowlist)
    elif raw.get("enabled_tools") is not None:
        normalized["tool_allowlist"] = _string_list(raw["enabled_tools"])
        normalized["tool_allowlist_active"] = True
    if tool_denylist is not None:
        normalized["tool_denylist"] = _string_list(tool_denylist)
    elif raw.get("disabled_tools") is not None:
        normalized["tool_denylist"] = _string_list(raw["disabled_tools"])
    return normalized


def read_bundled_mcp_servers(plugin_dir: str | Path) -> dict[str, object]:
    plugin_path = Path(plugin_dir).expanduser().resolve()
    for name in BUNDLED_MCP_CONFIG_FILE_NAMES:
        path = plugin_path / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            servers = extract_mcp_servers_from_payload(payload)
            if servers:
                return servers
    return {}


def upsert_mcp_servers_in_config_file(config_path: str | Path, incoming_servers: dict[str, object]) -> Path:
    path = Path(config_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_payload = _read_existing_writable_mcp_payload(path)
    current_servers = _extract_writable_mcp_servers(existing_payload)
    current_servers.update(incoming_servers)
    _write_writable_mcp_payload(path, existing_payload, current_servers)
    return path


def delete_mcp_server_from_config_file(config_path: str | Path, server_id: str) -> Path:
    path = Path(config_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_payload = _read_existing_writable_mcp_payload(path)
    current_servers = _extract_writable_mcp_servers(existing_payload)
    if server_id not in current_servers:
        raise KeyError(server_id)
    del current_servers[server_id]
    _write_writable_mcp_payload(path, existing_payload, current_servers)
    return path


def _read_existing_writable_mcp_payload(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid existing MCP config: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid existing MCP config: {exc}") from exc
    if not isinstance(payload, dict):
        return {}
    return payload


def _extract_writable_mcp_servers(payload: dict[str, object]) -> dict[str, object]:
    servers = extract_mcp_servers_from_payload(payload)
    return dict(servers)


def _write_writable_mcp_payload(path: Path, payload: dict[str, object], servers: dict[str, object]) -> None:
    if path.suffix.lower() == ".json":
        payload.pop("mcp_servers", None)
        payload["mcpServers"] = servers
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    payload.pop("mcpServers", None)
    payload["mcp_servers"] = servers
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _bool_like(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)
