from __future__ import annotations

import os
import re
import shutil
from typing import Any


SAFE_STDIO_ENV_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "USERNAME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "SHELL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SystemRoot",
        "ComSpec",
        "PATHEXT",
    }
)

CREDENTIAL_PATTERN = re.compile(
    r"(?:"
    r"ghp_[A-Za-z0-9_]{1,255}"
    r"|sk-[A-Za-z0-9_]{1,255}"
    r"|Bearer\s+\S+"
    r"|token=[^\s&,;\"']{1,255}"
    r"|key=[^\s&,;\"']{1,255}"
    r"|API_KEY=[^\s&,;\"']{1,255}"
    r"|password=[^\s&,;\"']{1,255}"
    r"|secret=[^\s&,;\"']{1,255}"
    r")",
    re.IGNORECASE,
)

ENV_REF_PATTERN = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))")
PLACEHOLDER_MARKERS = (
    "/path/to/",
    "<",
    ">",
    "localhost/mydb",
    "your-",
)


def has_real_transport_config(server: Any) -> bool:
    transport = str(server.transport_kind.value if hasattr(server.transport_kind, "value") else server.transport_kind)
    connection_config = getattr(server, "connection_config", {}) or {}
    if transport == "stdio":
        if not connection_config.get("command"):
            return False
        return not mcp_server_placeholder_config_reason(server)
    if transport in {"http", "sse"}:
        if not connection_config.get("url"):
            return False
        return not mcp_server_placeholder_config_reason(server)
    return False


def mcp_server_placeholder_config_reason(server: Any) -> str | None:
    connection_config = getattr(server, "connection_config", {}) or {}
    if not isinstance(connection_config, dict):
        return None
    values: list[str] = []
    for key in ("url", "command", "cwd", "bearer_token_env_var"):
        value = connection_config.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("args", "allowed_paths"):
        value = connection_config.get(key)
        if isinstance(value, list | tuple):
            values.extend(str(item) for item in value if isinstance(item, str))
    env = connection_config.get("env")
    if isinstance(env, dict):
        values.extend(str(value) for value in env.values() if isinstance(value, str))
    for value in values:
        normalized = value.strip().lower()
        if not normalized:
            continue
        if any(marker in normalized for marker in PLACEHOLDER_MARKERS):
            return f"placeholder MCP connection value: {value}"
    return None


def mcp_server_missing_env_names(server: Any) -> tuple[str, ...]:
    connection_config = getattr(server, "connection_config", {}) or {}
    names: set[str] = set()
    if isinstance(connection_config, dict):
        sensitive_config = {
            key: connection_config[key]
            for key in (
                "env",
                "headers",
                "http_headers",
                "env_http_headers",
                "bearer_token_env_var",
                "url",
            )
            if key in connection_config
        }
        names.update(_iter_env_ref_names(sensitive_config))
    names.update(_iter_env_ref_names(getattr(server, "header_templates", {}) or {}))
    bearer_token_env_var = connection_config.get("bearer_token_env_var") if isinstance(connection_config, dict) else None
    if bearer_token_env_var:
        names.add(str(bearer_token_env_var))
    env_http_headers = connection_config.get("env_http_headers") if isinstance(connection_config, dict) else None
    if isinstance(env_http_headers, dict):
        names.update(str(value) for value in env_http_headers.values() if str(value).strip())
    env_resolution = getattr(server, "env_resolution", {}) or {}
    missing: list[str] = []
    for name in sorted(names):
        if not name:
            continue
        if isinstance(env_resolution, dict) and env_resolution.get(name):
            continue
        if not os.getenv(name):
            missing.append(name)
    return tuple(missing)


def build_server_params(server_id: str, server: Any) -> dict[str, Any]:
    transport = str(server.transport_kind.value if hasattr(server.transport_kind, "value") else server.transport_kind)
    connection_config = resolve_connection_config(server.connection_config, server)
    params: dict[str, Any] = {"transport": transport}

    if transport == "stdio":
        command = connection_config.get("command")
        if not command:
            raise ValueError(f"MCP server '{server_id}' with stdio transport requires connection_config.command")
        env = build_safe_stdio_env(connection_config.get("env"))
        resolved_command, env = resolve_stdio_command(str(command), env)
        params["command"] = resolved_command
        params["args"] = list(connection_config.get("args", []))
        params["env"] = env
        if connection_config.get("cwd"):
            params["cwd"] = str(connection_config["cwd"])
    elif transport in {"http", "sse"}:
        url = connection_config.get("url")
        if not url:
            raise ValueError(f"MCP server '{server_id}' with {transport} transport requires connection_config.url")
        params["url"] = url
        headers = dict(connection_config.get("headers") or {})
        if connection_config.get("env_http_headers"):
            headers.update(
                {
                    str(name): os.getenv(str(env_name), "")
                    for name, env_name in dict(connection_config["env_http_headers"]).items()
                }
            )
        bearer_token_env_var = connection_config.get("bearer_token_env_var")
        if bearer_token_env_var:
            token = os.getenv(str(bearer_token_env_var), "")
            if token:
                headers.setdefault("Authorization", f"Bearer {token}")
        if headers:
            params["headers"] = headers
    else:
        raise ValueError(f"unsupported MCP transport '{transport}' for server '{server_id}'")

    for key in ("timeout", "connect_timeout"):
        if connection_config.get(key) is not None:
            params[key] = connection_config[key]
    return params


def build_mcp_client(server_id: str, server: Any):
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "langchain-mcp-adapters is required for live MCP transport materialization"
        ) from exc
    return MultiServerMCPClient(
        {server_id: build_server_params(server_id, server)},
        tool_name_prefix=False,
    )


def resolve_connection_config(connection_config: dict[str, Any], server: Any) -> dict[str, Any]:
    env_resolution = {
        **{key: os.getenv(str(key), "") for key in getattr(server, "env_resolution", {})},
        **dict(getattr(server, "env_resolution", {}) or {}),
    }
    resolved = _resolve_env_refs(connection_config, env_resolution)
    header_templates = dict(getattr(server, "header_templates", {}) or {})
    if header_templates:
        headers = dict(resolved.get("headers", {}) or {})
        headers.update(_resolve_env_refs(header_templates, env_resolution))
        resolved["headers"] = headers
    return resolved


def build_safe_stdio_env(user_env: object | None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in SAFE_STDIO_ENV_KEYS or key.startswith("XDG_")
    }
    if isinstance(user_env, dict):
        for key, value in user_env.items():
            env[str(key)] = str(value)
    return env


def resolve_stdio_command(command: str, env: dict[str, str]) -> tuple[str, dict[str, str]]:
    resolved_command = os.path.expanduser(command.strip())
    resolved_env = dict(env)
    if os.sep not in resolved_command and not (os.altsep and os.altsep in resolved_command):
        which_hit = shutil.which(resolved_command, path=resolved_env.get("PATH"))
        if which_hit:
            resolved_command = which_hit

    command_dir = os.path.dirname(resolved_command)
    if command_dir:
        path = resolved_env.get("PATH", "")
        parts = [part for part in path.split(os.pathsep) if part]
        if command_dir not in parts:
            resolved_env["PATH"] = os.pathsep.join([command_dir, *parts]) if parts else command_dir
    return resolved_command, resolved_env


def sanitize_mcp_error(error: object) -> str:
    return CREDENTIAL_PATTERN.sub("[REDACTED]", str(error))


def _resolve_env_refs(value: Any, env_resolution: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {str(key): _resolve_env_refs(nested, env_resolution) for key, nested in value.items()}
    if isinstance(value, list):
        return [_resolve_env_refs(item, env_resolution) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("plain") or ""
        if name in env_resolution:
            return str(env_resolution[name])
        return os.getenv(name, "")

    return ENV_REF_PATTERN.sub(replace, value)


def _iter_env_ref_names(value: Any):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _iter_env_ref_names(nested)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_env_ref_names(item)
        return
    if isinstance(value, str):
        for match in ENV_REF_PATTERN.finditer(value):
            name = match.group("braced") or match.group("plain") or ""
            if name:
                yield name
