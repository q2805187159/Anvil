from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any, Iterable
import warnings

from anvil.config import EffectiveConfig, McpServerConfig
from anvil.mcp import (
    build_mcp_client,
    build_server_params,
    has_real_transport_config,
    mcp_server_placeholder_config_reason,
    mcp_server_missing_env_names,
    resolve_connection_config,
    sanitize_mcp_error,
)
from anvil.runtime.tool_registry.contracts import CapabilityPrompt, CapabilityResource, ToolRegistryEntry, ToolSourceKind

from .contracts import ExtensionMaterialization, ExternalCapabilityStatus

LEGACY_AGENT_SKILL_DOWNLOAD_NEEDLES = (
    "get an agent skill by id",
    ".claude/skills",
)


def is_legacy_agent_skill_download_tool(tool_entry: ToolRegistryEntry) -> bool:
    haystack = " ".join(
        str(value or "")
        for value in (
            tool_entry.name,
            tool_entry.display_name,
            tool_entry.summary,
            tool_entry.capability_group,
        )
    ).lower()
    return all(needle in haystack for needle in LEGACY_AGENT_SKILL_DOWNLOAD_NEEDLES)


class ExtensionsMaterializer:
    def materialize(self, config: EffectiveConfig, server_id: str, *, live: bool = True) -> ExtensionMaterialization:
        server = config.extensions.mcp_servers[server_id]
        if not server.enabled:
            return ExtensionMaterialization(
                server_id=server_id,
                status=ExternalCapabilityStatus.CONFIGURED,
                transport_kind=server.transport_kind.value,
                startup_policy=server.startup_policy,
                refresh_policy=server.refresh_policy,
                connected=False,
                ready=False,
            )

        if server.connection_config.get("fail_materialization"):
            error = str(server.connection_config.get("fail_materialization"))
            return ExtensionMaterialization(
                server_id=server_id,
                status=ExternalCapabilityStatus.FAILED,
                transport_kind=server.transport_kind.value,
                startup_policy=server.startup_policy,
                refresh_policy=server.refresh_policy,
                diagnostics=(error,),
                error=error,
            )

        missing_env_names = mcp_server_missing_env_names(server)
        if missing_env_names:
            error = "missing required environment variables: " + ", ".join(missing_env_names)
            return ExtensionMaterialization(
                server_id=server_id,
                status=ExternalCapabilityStatus.AUTH_REQUIRED,
                transport_kind=server.transport_kind.value,
                startup_policy=server.startup_policy,
                refresh_policy=server.refresh_policy,
                diagnostics=(error,),
                error=error,
                auth_required=True,
                discovery_source="configuration",
            )

        placeholder_reason = mcp_server_placeholder_config_reason(server)
        if placeholder_reason:
            return ExtensionMaterialization(
                server_id=server_id,
                status=ExternalCapabilityStatus.CONFIGURED,
                transport_kind=server.transport_kind.value,
                startup_policy=server.startup_policy,
                refresh_policy=server.refresh_policy,
                diagnostics=(placeholder_reason,),
                error=placeholder_reason,
                discovery_source="configuration",
            )

        try:
            if has_real_transport_config(server) and live:
                tools, resources, prompts = self._discover_live_capabilities(server_id, server)
                discovery_source = "live"
            else:
                resources = tuple(self._build_inline_resources(server_id, server))
                prompts = tuple(self._build_inline_prompts(server_id, server))
                tools = tuple(self._build_inline_tools(server_id, server, resources=resources, prompts=prompts))
                discovery_source = "inline_fallback"
        except Exception as exc:  # noqa: BLE001
            error = sanitize_mcp_error(exc)
            status = ExternalCapabilityStatus.AUTH_REQUIRED if self._is_auth_error(error) else ExternalCapabilityStatus.FAILED
            return ExtensionMaterialization(
                server_id=server_id,
                status=status,
                transport_kind=server.transport_kind.value,
                startup_policy=server.startup_policy,
                refresh_policy=server.refresh_policy,
                diagnostics=(error,),
                error=error,
                auth_required=status is ExternalCapabilityStatus.AUTH_REQUIRED,
                discovery_source="live" if has_real_transport_config(server) else "inline_fallback",
            )

        status = ExternalCapabilityStatus.READY if tools or resources or prompts else ExternalCapabilityStatus.ENABLED
        return ExtensionMaterialization(
            server_id=server_id,
            status=status,
            transport_kind=server.transport_kind.value,
            startup_policy=server.startup_policy,
            refresh_policy=server.refresh_policy,
            discovery_source=discovery_source,
            tools=tools,
            resources=resources,
            prompts=prompts,
            connected=bool(tools or resources or prompts),
            ready=bool(tools or resources or prompts),
            diagnostics=(),
            metadata={
                "description": server.description,
                "oauth": dict(server.oauth),
                "resource_policy": dict(server.resource_policy),
                "prompt_policy": dict(server.prompt_policy),
            },
        )

    def read_live_resource(self, server_id: str, server: McpServerConfig, resource_id: str) -> dict[str, object]:
        client = build_mcp_client(server_id, server)
        payload = self._invoke_live_resource_read(client=client, server_id=server_id, resource_id=resource_id)
        return self._normalize_resource_content(server_id=server_id, resource_id=resource_id, payload=payload)

    def render_live_prompt(
        self,
        server_id: str,
        server: McpServerConfig,
        prompt_id: str,
        arguments: dict[str, object] | None = None,
    ) -> dict[str, object]:
        client = build_mcp_client(server_id, server)
        payload = self._invoke_live_prompt_render(
            client=client,
            server_id=server_id,
            prompt_id=prompt_id,
            arguments=arguments or {},
        )
        return self._normalize_prompt_render(server_id=server_id, prompt_id=prompt_id, arguments=arguments or {}, payload=payload)

    def _discover_live_capabilities(
        self,
        server_id: str,
        server: McpServerConfig,
    ) -> tuple[tuple[ToolRegistryEntry, ...], tuple[CapabilityResource, ...], tuple[CapabilityPrompt, ...]]:
        client = build_mcp_client(server_id, server)
        raw_tools = tuple(self._run_async(client.get_tools()))
        resources = tuple(self._discover_live_resources(client=client, server_id=server_id, server=server))
        prompts = tuple(self._discover_live_prompts(client=client, server_id=server_id, server=server))
        tools = tuple(self._build_live_tools(server_id, server, raw_tools=raw_tools, resources=resources, prompts=prompts))
        return tools, resources, prompts

    def _build_inline_tools(
        self,
        server_id: str,
        server: McpServerConfig,
        *,
        resources: tuple[CapabilityResource, ...],
        prompts: tuple[CapabilityPrompt, ...],
    ) -> list[ToolRegistryEntry]:
        tools: list[ToolRegistryEntry] = []
        for tool_spec in server.connection_config.get("inline_tools", []):
            from langchain_core.tools import tool

            name = str(tool_spec["name"])
            if server.tool_prefix:
                name = f"{server.tool_prefix}{name}"
            display_name = str(tool_spec.get("display_name", name))
            capability_group = str(tool_spec.get("capability_group", "external"))
            deferred = bool(tool_spec.get("deferred", False))
            response = tool_spec.get("response", {"server": server_id, "tool": name})

            @tool(name, description=display_name)
            def _handler(response_payload=json.dumps(response)) -> str:
                return response_payload

            tools.append(
                ToolRegistryEntry(
                    name=name,
                    display_name=display_name,
                    source_kind=ToolSourceKind.MCP,
                    source_id=server_id,
                    capability_group=capability_group,
                    summary=str(tool_spec.get("summary", display_name)),
                    handler=_handler,
                    input_schema=dict(tool_spec.get("schema", {})),
                    provenance={
                        "origin": "inline_fallback",
                        "server_id": server_id,
                        "collision_policy": server.collision_policy,
                        **dict(tool_spec.get("metadata") or {}),
                    },
                    resources=resources,
                    prompts=prompts,
                    deferred=deferred,
                )
            )
        return self._filter_tools(server, tools)

    def _build_live_tools(
        self,
        server_id: str,
        server: McpServerConfig,
        *,
        raw_tools: tuple[Any, ...],
        resources: tuple[CapabilityResource, ...],
        prompts: tuple[CapabilityPrompt, ...],
    ) -> list[ToolRegistryEntry]:
        entries: list[ToolRegistryEntry] = []
        for raw_tool in raw_tools:
            self._ensure_sync_invoke(raw_tool)
            raw_name = str(getattr(raw_tool, "name", server_id))
            name = f"{server.tool_prefix}{raw_name}" if server.tool_prefix else raw_name
            entries.append(
                ToolRegistryEntry(
                    name=name,
                    display_name=str(getattr(raw_tool, "description", None) or raw_name),
                    source_kind=ToolSourceKind.MCP,
                    source_id=server_id,
                    capability_group=str(server.connection_config.get("capability_group", "external")),
                    summary=str(getattr(raw_tool, "description", None) or raw_name),
                    handler=raw_tool,
                    input_schema=self._extract_schema(raw_tool),
                    provenance={
                        "origin": "live_transport",
                        "server_id": server_id,
                        "transport_kind": server.transport_kind.value,
                        "collision_policy": server.collision_policy,
                        "approval_policy": server.approval_policy,
                    },
                    resources=resources,
                    prompts=prompts,
                    deferred=bool(server.connection_config.get("deferred", False)),
                )
            )
        return self._filter_tools(server, entries)

    def _has_real_transport_config(self, server: McpServerConfig) -> bool:
        return has_real_transport_config(server)

    def _build_server_params(self, server_id: str, server: McpServerConfig) -> dict[str, Any]:
        return build_server_params(server_id, server)

    def _build_mcp_client(self, server_id: str, server: McpServerConfig):
        return build_mcp_client(server_id, server)

    def _discover_live_resources(self, *, client, server_id: str, server: McpServerConfig) -> list[CapabilityResource]:
        raw_resources = self._invoke_live_resource_listing(client=client, server_id=server_id)
        items = [
            self._coerce_resource(server_id=server_id, raw=item, discovery_source="live")
            for item in raw_resources
        ]
        return self._filter_resources(server, items)

    def _discover_live_prompts(self, *, client, server_id: str, server: McpServerConfig) -> list[CapabilityPrompt]:
        raw_prompts = self._invoke_live_prompt_listing(client=client, server_id=server_id)
        items = [
            self._coerce_prompt(server_id=server_id, raw=item, discovery_source="live")
            for item in raw_prompts
        ]
        return self._filter_prompts(server, items)

    def _invoke_live_resource_listing(self, *, client, server_id: str) -> list[Any]:
        for candidate in [
            lambda: self._run_async(client.get_resources()),
            lambda: self._run_async(client.list_resources()),
            lambda: self._run_async(self._maybe_session_call(client, server_id, "list_resources")),
        ]:
            try:
                payload = candidate()
            except Exception:
                continue
            if payload is not None:
                return list(payload)
        return []

    def _invoke_live_prompt_listing(self, *, client, server_id: str) -> list[Any]:
        for candidate in [
            lambda: self._run_async(client.get_prompts()),
            lambda: self._run_async(client.list_prompts()),
            lambda: self._run_async(self._maybe_session_call(client, server_id, "list_prompts")),
        ]:
            try:
                payload = candidate()
            except Exception:
                continue
            if payload is not None:
                return list(payload)
        return []

    def _invoke_live_resource_read(self, *, client, server_id: str, resource_id: str) -> Any:
        candidates = [
            lambda: self._run_async(client.read_resource(server_id=server_id, resource_id=resource_id)),
            lambda: self._run_async(client.read_resource(resource_id)),
            lambda: self._run_async(self._maybe_session_call(client, server_id, "read_resource", resource_id)),
            lambda: self._run_async(self._maybe_session_call(client, server_id, "read_resource", {"uri": resource_id})),
        ]
        for candidate in candidates:
            try:
                payload = candidate()
            except Exception:
                continue
            if payload is not None:
                return payload
        raise ValueError(f"unknown resource '{resource_id}' on MCP server '{server_id}'")

    def _invoke_live_prompt_render(
        self,
        *,
        client,
        server_id: str,
        prompt_id: str,
        arguments: dict[str, object],
    ) -> Any:
        candidates = [
            lambda: self._run_async(client.get_prompt(server_id=server_id, prompt_id=prompt_id, arguments=arguments)),
            lambda: self._run_async(client.get_prompt(prompt_id, arguments)),
            lambda: self._run_async(self._maybe_session_call(client, server_id, "get_prompt", prompt_id, arguments)),
            lambda: self._run_async(self._maybe_session_call(client, server_id, "get_prompt", {"name": prompt_id, "arguments": arguments})),
        ]
        for candidate in candidates:
            try:
                payload = candidate()
            except Exception:
                continue
            if payload is not None:
                return payload
        raise ValueError(f"unknown prompt '{prompt_id}' on MCP server '{server_id}'")

    async def _maybe_session_call(self, client, server_id: str, method: str, *args):
        session = None
        if hasattr(client, "session"):
            session = client.session(server_id)  # type: ignore[call-arg]
            if asyncio.iscoroutine(session):
                session = await session
        elif hasattr(client, "sessions"):
            sessions = getattr(client, "sessions")
            if callable(sessions):
                sessions = sessions()
                if asyncio.iscoroutine(sessions):
                    sessions = await sessions
            if isinstance(sessions, dict):
                session = sessions.get(server_id)
        if session is None:
            raise AttributeError(method)
        attr = getattr(session, method)
        result = attr(*args)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    def _extract_schema(self, raw_tool) -> dict[str, Any]:
        args_schema = getattr(raw_tool, "args_schema", None)
        if args_schema is None:
            return {}
        if hasattr(args_schema, "model_json_schema"):
            return args_schema.model_json_schema()
        if hasattr(args_schema, "schema"):
            return args_schema.schema()
        if isinstance(args_schema, dict):
            return dict(args_schema)
        return {}

    def _ensure_sync_invoke(self, raw_tool) -> None:
        if getattr(raw_tool, "func", None) is not None or getattr(raw_tool, "coroutine", None) is None:
            return

        coroutine = raw_tool.coroutine

        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(asyncio.run, coroutine(*args, **kwargs))
                    return future.result()
            return asyncio.run(coroutine(*args, **kwargs))

        raw_tool.func = sync_wrapper

    def _run_async(self, awaitable):
        if awaitable is None:
            return None
        if not asyncio.iscoroutine(awaitable):
            return awaitable
        return self._run_mcp_adapter_awaitable(awaitable)

    def _run_mcp_adapter_awaitable(self, awaitable):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._run_mcp_adapter_coroutine, awaitable)
                return future.result()
        return self._run_mcp_adapter_coroutine(awaitable)

    def _run_mcp_adapter_coroutine(self, awaitable):
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Use `streamable_http_client` instead\.",
                category=DeprecationWarning,
            )
            return asyncio.run(awaitable)

    def _resolve_connection_config(self, connection_config: dict[str, Any], server: McpServerConfig) -> dict[str, Any]:
        return resolve_connection_config(connection_config, server)

    def _build_inline_resources(self, server_id: str, server: McpServerConfig) -> list[CapabilityResource]:
        items: list[CapabilityResource] = []
        for item in server.connection_config.get("inline_resources", []):
            resource_id = str(item.get("resource_id") or item.get("name") or "")
            if not resource_id:
                continue
            items.append(
                CapabilityResource(
                    resource_id=resource_id,
                    title=str(item.get("title") or resource_id),
                    description=str(item.get("description") or ""),
                    server_id=server_id,
                    path=str(item.get("path")) if item.get("path") is not None else None,
                    metadata={
                        "discovery_source": "inline_fallback",
                        "supports_read": True,
                        **{str(key): value for key, value in item.items() if key not in {"resource_id", "name", "title", "description", "path"}},
                    },
                )
            )
        return self._filter_resources(server, items)

    def _build_inline_prompts(self, server_id: str, server: McpServerConfig) -> list[CapabilityPrompt]:
        items: list[CapabilityPrompt] = []
        for item in server.connection_config.get("inline_prompts", []):
            prompt_id = str(item.get("prompt_id") or item.get("name") or "")
            if not prompt_id:
                continue
            items.append(
                CapabilityPrompt(
                    prompt_id=prompt_id,
                    title=str(item.get("title") or prompt_id),
                    description=str(item.get("description") or ""),
                    server_id=server_id,
                    arguments=tuple(str(arg) for arg in item.get("arguments", []) if str(arg).strip()),
                    metadata={
                        "discovery_source": "inline_fallback",
                        "supports_render": True,
                        **{str(key): value for key, value in item.items() if key not in {"prompt_id", "name", "title", "description", "arguments"}},
                    },
                )
            )
        return self._filter_prompts(server, items)

    def _filter_tools(self, server: McpServerConfig, tools: list[ToolRegistryEntry]) -> list[ToolRegistryEntry]:
        allowlist = set(server.tool_allowlist)
        denylist = set(server.tool_denylist)
        filtered: list[ToolRegistryEntry] = []
        for tool_entry in tools:
            if is_legacy_agent_skill_download_tool(tool_entry):
                continue
            raw_name = tool_entry.name.removeprefix(server.tool_prefix or "")
            if server.tool_allowlist_active and raw_name not in allowlist and tool_entry.name not in allowlist:
                continue
            if not server.tool_allowlist_active and server.tool_prefix and allowlist and raw_name not in allowlist and tool_entry.name not in allowlist:
                continue
            if raw_name in denylist or tool_entry.name in denylist:
                continue
            filtered.append(tool_entry)
        return filtered

    def _filter_resources(self, server: McpServerConfig, items: list[CapabilityResource]) -> list[CapabilityResource]:
        policy = dict(server.resource_policy)
        if policy.get("enabled") is False:
            return []
        allowlist = {str(item) for item in policy.get("allowlist", [])}
        denylist = {str(item) for item in policy.get("denylist", [])}
        results: list[CapabilityResource] = []
        for item in items:
            if allowlist and item.resource_id not in allowlist:
                continue
            if item.resource_id in denylist:
                continue
            results.append(item)
        return results

    def _filter_prompts(self, server: McpServerConfig, items: list[CapabilityPrompt]) -> list[CapabilityPrompt]:
        policy = dict(server.prompt_policy)
        if policy.get("enabled") is False:
            return []
        allowlist = {str(item) for item in policy.get("allowlist", [])}
        denylist = {str(item) for item in policy.get("denylist", [])}
        results: list[CapabilityPrompt] = []
        for item in items:
            if allowlist and item.prompt_id not in allowlist:
                continue
            if item.prompt_id in denylist:
                continue
            results.append(item)
        return results

    def _coerce_resource(self, *, server_id: str, raw: Any, discovery_source: str) -> CapabilityResource:
        if isinstance(raw, dict):
            payload = dict(raw)
        else:
            payload = {
                key: getattr(raw, key)
                for key in dir(raw)
                if not key.startswith("_") and key in {"uri", "name", "title", "description", "mimeType", "mime_type", "metadata", "path"}
            }
        resource_id = str(payload.get("resource_id") or payload.get("uri") or payload.get("name") or payload.get("title") or "")
        return CapabilityResource(
            resource_id=resource_id,
            title=str(payload.get("title") or payload.get("name") or resource_id),
            description=str(payload.get("description") or ""),
            server_id=server_id,
            path=str(payload.get("path")) if payload.get("path") is not None else None,
            metadata={
                "discovery_source": discovery_source,
                "uri": payload.get("uri"),
                "mime_type": payload.get("mimeType") or payload.get("mime_type"),
                "supports_read": True,
                **{str(key): value for key, value in payload.items() if key not in {"resource_id", "uri", "name", "title", "description", "path"}},
            },
        )

    def _coerce_prompt(self, *, server_id: str, raw: Any, discovery_source: str) -> CapabilityPrompt:
        if isinstance(raw, dict):
            payload = dict(raw)
        else:
            payload = {
                key: getattr(raw, key)
                for key in dir(raw)
                if not key.startswith("_") and key in {"name", "title", "description", "arguments", "inputSchema", "input_schema", "template", "metadata"}
            }
        prompt_id = str(payload.get("prompt_id") or payload.get("name") or payload.get("title") or "")
        arguments = payload.get("arguments") or ()
        if isinstance(arguments, dict):
            arguments = tuple(str(key) for key in arguments)
        elif not isinstance(arguments, (list, tuple)):
            arguments = ()
        return CapabilityPrompt(
            prompt_id=prompt_id,
            title=str(payload.get("title") or payload.get("name") or prompt_id),
            description=str(payload.get("description") or ""),
            server_id=server_id,
            arguments=tuple(str(arg) for arg in arguments if str(arg).strip()),
            metadata={
                "discovery_source": discovery_source,
                "template": payload.get("template"),
                "input_schema": payload.get("inputSchema") or payload.get("input_schema"),
                "supports_render": True,
                **{str(key): value for key, value in payload.items() if key not in {"prompt_id", "name", "title", "description", "arguments"}},
            },
        )

    def _normalize_resource_content(self, *, server_id: str, resource_id: str, payload: Any) -> dict[str, object]:
        if isinstance(payload, dict):
            body = dict(payload)
        else:
            body = {"content": payload}
        content = body.get("content")
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        return {
            "server_id": server_id,
            "resource_id": resource_id,
            "title": str(body.get("title") or resource_id),
            "description": str(body.get("description") or ""),
            "path": body.get("path"),
            "metadata": {
                "discovery_source": "live",
                **dict(body.get("metadata") or {}),
            },
            "content": content,
        }

    def _normalize_prompt_render(
        self,
        *,
        server_id: str,
        prompt_id: str,
        arguments: dict[str, object],
        payload: Any,
    ) -> dict[str, object]:
        if isinstance(payload, dict):
            body = dict(payload)
        else:
            body = {"rendered": payload}
        rendered = body.get("rendered")
        if rendered is None and body.get("messages"):
            rendered = json.dumps(body.get("messages"), ensure_ascii=False)
        return {
            "server_id": server_id,
            "prompt_id": prompt_id,
            "title": str(body.get("title") or prompt_id),
            "description": str(body.get("description") or ""),
            "arguments": list(body.get("arguments") or arguments.keys()),
            "metadata": {
                "discovery_source": "live",
                **dict(body.get("metadata") or {}),
            },
            "rendered": str(rendered or ""),
        }

    def _is_auth_error(self, error: str) -> bool:
        normalized = error.lower()
        return any(token in normalized for token in {"401", "403", "unauthorized", "forbidden", "oauth", "auth", "token"})
