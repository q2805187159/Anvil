from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import Lock
from urllib.request import urlopen
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from anvil.config import EffectiveConfig
from anvil.config.loader import default_anvil_config_dir, resolve_anvil_config_path
from anvil.mcp import has_real_transport_config, read_bundled_mcp_servers, upsert_mcp_servers_in_config_file
from anvil.runtime.tool_registry.contracts import CapabilityPrompt, CapabilityResource, ToolRegistryEntry, ToolSourceKind

from .contracts import ExtensionDiscoveryResult, ExtensionMaterialization, ExternalCapabilityStatus
from .lifecycle import McpLifecycleManager
from .loader import ExtensionsLoader
from .materializer import ExtensionsMaterializer


DEFAULT_PLUGIN_PACKAGE_SCAN_LIMIT = 5_000
MAX_PLUGIN_PACKAGE_SCAN_LIMIT = 50_000
DEFAULT_PLUGIN_TREE_SCAN_LIMIT = 5_000
MAX_PLUGIN_TREE_SCAN_LIMIT = 50_000
MAX_PLUGIN_PACKAGE_UNCOMPRESSED_BYTES = 50 * 1024 * 1024

_PLUGIN_MANIFEST_PATHS = (
    "anvil.plugin.json",
    "plugin.json",
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
    "plugin.yaml",
    "plugin.yml",
)


@dataclass(frozen=True)
class _PluginPackageEntry:
    info: zipfile.ZipInfo
    filename: str
    parts: tuple[str, ...]


@dataclass(frozen=True)
class _PluginPackageScan:
    entries: tuple[_PluginPackageEntry, ...]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool
    total_uncompressed_bytes: int
    max_uncompressed_bytes: int = MAX_PLUGIN_PACKAGE_UNCOMPRESSED_BYTES


@dataclass(frozen=True)
class _PluginTreeEntry:
    relative_path: str
    source_path: str
    is_dir: bool
    is_file: bool


@dataclass(frozen=True)
class _PluginTreeScan:
    entries: tuple[_PluginTreeEntry, ...]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool


class ExtensionsService:
    def __init__(
        self,
        *,
        loader: ExtensionsLoader | None = None,
        materializer: ExtensionsMaterializer | None = None,
        lifecycle: McpLifecycleManager | None = None,
    ) -> None:
        self.loader = loader or ExtensionsLoader()
        self.materializer = materializer or ExtensionsMaterializer()
        self.lifecycle = lifecycle or McpLifecycleManager()
        self._cache: dict[tuple[str, str, str], ExtensionMaterialization] = {}
        self._refresh_lock = Lock()

    def discover(
        self,
        *,
        config: EffectiveConfig,
        fingerprint: str,
        live: bool = True,
        materialization_mode: str = "live",
    ) -> ExtensionDiscoveryResult:
        server_ids = self.loader.configured_server_ids(config)
        materializations: list[ExtensionMaterialization] = []
        effective_servers: list[str] = []
        effective_plugins: list[str] = []
        for server_id in server_ids:
            server = config.extensions.mcp_servers[server_id]
            has_real_transport = has_real_transport_config(server)
            should_materialize_live = live and (
                materialization_mode == "live"
                or (materialization_mode == "lazy_safe" and server.startup_policy == "eager")
            )
            cache_mode = "live" if should_materialize_live else "config"
            if materialization_mode == "lazy_safe" and live and not should_materialize_live and has_real_transport:
                cache_mode = "lazy_config"
            cache_key = (fingerprint, server_id, cache_mode)
            materialization = self._cache.get(cache_key)
            if materialization is None:
                if cache_mode == "lazy_config":
                    materialization = ExtensionMaterialization(
                        server_id=server_id,
                        status=ExternalCapabilityStatus.CONFIGURED,
                        transport_kind=server.transport_kind.value,
                        startup_policy=server.startup_policy,
                        refresh_policy=server.refresh_policy,
                        discovery_source="configuration",
                        diagnostics=("lazy startup deferred until explicit MCP refresh or eager startup policy",),
                        metadata={
                            "description": server.description,
                            "oauth": dict(server.oauth),
                            "resource_policy": dict(server.resource_policy),
                            "prompt_policy": dict(server.prompt_policy),
                        },
                    )
                else:
                    if should_materialize_live:
                        self.lifecycle.mark_starting(server_id)
                    materialization = self.materializer.materialize(config, server_id, live=should_materialize_live)
                materialization = self._merge_runtime_state(config, materialization)
                self._cache[cache_key] = materialization
            materializations.append(materialization)
            if materialization.status in {
                ExternalCapabilityStatus.READY,
                ExternalCapabilityStatus.ENABLED,
                ExternalCapabilityStatus.MATERIALIZED,
                ExternalCapabilityStatus.VISIBLE,
            }:
                effective_servers.append(server_id)
        for plugin_id in self.loader.configured_plugin_ids(config):
            plugin = config.extensions.plugins[plugin_id]
            if not plugin.enabled:
                continue
            materialization = self._materialize_plugin(plugin_id, plugin)
            materializations.append(materialization)
            effective_plugins.append(plugin_id)
        return ExtensionDiscoveryResult(
            materializations=tuple(materializations),
            effective_mcp_servers=tuple(sorted(effective_servers)),
            effective_plugin_ids=tuple(sorted(effective_plugins)),
        )

    def refresh_server(self, *, config: EffectiveConfig, fingerprint: str, server_id: str) -> ExtensionMaterialization:
        with self._refresh_lock:
            cache_key = (fingerprint, server_id, "live")
            owner = f"refresh:{fingerprint}:{server_id}"
            self.lifecycle.claim_refresh(server_id, owner)
            materialization = self.materializer.materialize(config, server_id)
            materialization = self._merge_runtime_state(config, materialization, refreshed=True, refresh_owner=owner)
            self._cache[cache_key] = materialization
            return materialization

    def reconnect_server(self, *, config: EffectiveConfig, fingerprint: str, server_id: str) -> ExtensionMaterialization:
        self.lifecycle.mark_reconnected(server_id)
        return self.refresh_server(config=config, fingerprint=fingerprint, server_id=server_id)

    def get_server(self, *, config: EffectiveConfig, fingerprint: str, server_id: str, live: bool = True) -> ExtensionMaterialization | None:
        result = self.discover(config=config, fingerprint=fingerprint, live=live)
        return next((item for item in result.materializations if item.server_id == server_id and item.source_kind == "mcp"), None)

    def list_resources(self, *, config: EffectiveConfig, fingerprint: str, server_id: str | None = None, live: bool = True) -> tuple[CapabilityResource, ...]:
        result = self.discover(config=config, fingerprint=fingerprint, live=live)
        items: list[CapabilityResource] = []
        for materialization in result.materializations:
            if materialization.source_kind != "mcp":
                continue
            if server_id and materialization.server_id != server_id:
                continue
            items.extend(materialization.resources)
        return tuple(items)

    def read_resource(self, *, config: EffectiveConfig, fingerprint: str, server_id: str, resource_id: str) -> dict[str, object]:
        materialization = self.get_server(config=config, fingerprint=fingerprint, server_id=server_id)
        if materialization is None:
            raise ValueError(f"unknown MCP server '{server_id}'")
        server = config.extensions.mcp_servers.get(server_id)
        if server is None:
            raise ValueError(f"unknown MCP server '{server_id}'")
        if materialization.discovery_source == "live" and self.materializer._has_real_transport_config(server):  # noqa: SLF001
            return self.materializer.read_live_resource(server_id, server, resource_id)
        for resource in materialization.resources:
            if resource.resource_id != resource_id:
                continue
            content = None
            path = resource.path
            if path:
                try:
                    content = Path(path).expanduser().read_text(encoding="utf-8")
                except OSError as exc:
                    content = f"resource read failed: {exc}"
            return {
                "server_id": server_id,
                "resource_id": resource.resource_id,
                "title": resource.title,
                "description": resource.description,
                "path": resource.path,
                "metadata": resource.metadata,
                "content": content,
            }
        raise ValueError(f"unknown resource '{resource_id}' on MCP server '{server_id}'")

    def list_prompts(self, *, config: EffectiveConfig, fingerprint: str, server_id: str | None = None, live: bool = True) -> tuple[CapabilityPrompt, ...]:
        result = self.discover(config=config, fingerprint=fingerprint, live=live)
        items: list[CapabilityPrompt] = []
        for materialization in result.materializations:
            if materialization.source_kind != "mcp":
                continue
            if server_id and materialization.server_id != server_id:
                continue
            items.extend(materialization.prompts)
        return tuple(items)

    def get_prompt(self, *, config: EffectiveConfig, fingerprint: str, server_id: str, prompt_id: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
        materialization = self.get_server(config=config, fingerprint=fingerprint, server_id=server_id)
        if materialization is None:
            raise ValueError(f"unknown MCP server '{server_id}'")
        server = config.extensions.mcp_servers.get(server_id)
        if server is None:
            raise ValueError(f"unknown MCP server '{server_id}'")
        if materialization.discovery_source == "live" and self.materializer._has_real_transport_config(server):  # noqa: SLF001
            return self.materializer.render_live_prompt(server_id, server, prompt_id, arguments)
        for prompt in materialization.prompts:
            if prompt.prompt_id != prompt_id:
                continue
            payload = dict(prompt.metadata)
            template = str(payload.get("template") or prompt.description or prompt.title)
            rendered = template
            for key, value in (arguments or {}).items():
                rendered = rendered.replace(f"{{{key}}}", str(value))
            return {
                "server_id": server_id,
                "prompt_id": prompt.prompt_id,
                "title": prompt.title,
                "description": prompt.description,
                "arguments": list(prompt.arguments),
                "metadata": prompt.metadata,
                "rendered": rendered,
            }
        raise ValueError(f"unknown prompt '{prompt_id}' on MCP server '{server_id}'")

    def list_plugins(self, *, config: EffectiveConfig) -> tuple[dict[str, object], ...]:
        items: list[dict[str, object]] = []
        for plugin_id in self.loader.configured_plugin_ids(config):
            plugin = config.extensions.plugins[plugin_id]
            items.append(
                {
                    "plugin_id": plugin_id,
                    "enabled": plugin.enabled,
                    "source_path": plugin.source_path,
                    "skill_roots": list(plugin.skill_roots),
                    "tool_count": len(plugin.inline_tools),
                    "tool_names": [str(item.get("name")) for item in plugin.inline_tools if item.get("name")],
                    "resources": list(plugin.resources),
                    "prompts": list(plugin.prompts),
                    "memory_providers": [provider.model_dump(mode="json") for provider in plugin.memory_providers],
                    "memory_provider_count": len(plugin.memory_providers),
                    "catalog_metadata": dict(plugin.catalog_metadata),
                    "discovery_source": "plugin_config",
                }
            )
        return tuple(items)

    def list_plugin_catalog(self, *, repo_root: Path, config: EffectiveConfig) -> tuple[dict[str, object], ...]:
        installed_plugins = {
            plugin_id: config.extensions.plugins[plugin_id]
            for plugin_id in self.loader.configured_plugin_ids(config)
        }
        entries_by_id: dict[str, dict[str, object]] = {}
        for registry in self._plugin_catalog_sources(repo_root):
            if not bool(registry.get("enabled", True)):
                continue
            read_result = self._read_plugin_registry_source(repo_root=repo_root, registry=registry)
            for raw_entry in read_result["entries"]:
                entry = self._plugin_catalog_entry_from_raw(
                    raw_entry,
                    catalog_source=str(read_result["catalog_source"] or registry["source"]),
                    catalog_base=read_result["catalog_base"],
                    repo_root=repo_root,
                    registry=registry,
                    installed_plugins=installed_plugins,
                )
                if entry is None:
                    continue
                plugin_id = str(entry["plugin_id"])
                existing = entries_by_id.get(plugin_id)
                if existing is None or self._registry_precedence(entry) >= self._registry_precedence(existing):
                    entries_by_id[plugin_id] = entry

        for plugin_id, plugin in installed_plugins.items():
            if plugin_id in entries_by_id:
                entries_by_id[plugin_id]["installed"] = True
                entries_by_id[plugin_id]["enabled"] = bool(plugin.enabled)
                continue
            entries_by_id[plugin_id] = self._plugin_catalog_entry_from_installed(plugin_id, plugin)

        return tuple(
            sorted(
                entries_by_id.values(),
                key=lambda item: (
                    0 if bool(item.get("installed")) else 1,
                    str(item.get("name") or item.get("plugin_id") or "").lower(),
                ),
            )
        )

    def list_plugin_registries(self, *, repo_root: Path) -> tuple[dict[str, object], ...]:
        items: list[dict[str, object]] = []
        for registry in self._plugin_catalog_sources(repo_root):
            read_result = self._read_plugin_registry_source(repo_root=repo_root, registry=registry)
            items.append(
                {
                    **registry,
                    "entry_count": len(read_result["entries"]),
                    "cached": bool(read_result["cached"]),
                    "cache_path": read_result["cache_path"],
                    "error": read_result["error"],
                    "diagnostics": read_result["diagnostics"],
                    "last_checked_at": read_result["last_checked_at"],
                }
            )
        return tuple(items)

    def upsert_plugin_registry(
        self,
        *,
        repo_root: Path,
        source: str,
        registry_id: str | None = None,
        name: str | None = None,
        enabled: bool = True,
        trust_level: str | None = None,
    ) -> dict[str, object]:
        source = source.strip()
        if not source:
            raise ValueError("plugin registry source is required")
        resolved_registry_id = self._safe_plugin_id(registry_id or self._registry_id_from_source(source))
        registry = {
            "registry_id": resolved_registry_id,
            "name": name.strip() if name and name.strip() else self._registry_name_from_source(source),
            "source": source,
            "source_kind": self._registry_source_kind(source),
            "enabled": bool(enabled),
            "readonly": False,
            "trust_level": trust_level or "third-party",
        }
        read_result = self._read_plugin_registry_source(repo_root=repo_root, registry=registry, force_refresh=True)
        if read_result["error"]:
            raise ValueError(str(read_result["error"]))
        config_path, registries = self._upsert_plugin_registry_config(repo_root, registry)
        return {
            **registry,
            "config_path": str(config_path),
            "registries": registries,
            "entry_count": len(read_result["entries"]),
            "cached": bool(read_result["cached"]),
            "cache_path": read_result["cache_path"],
            "error": read_result["error"],
            "diagnostics": read_result["diagnostics"],
            "last_checked_at": read_result["last_checked_at"],
        }

    def delete_plugin_registry(self, *, repo_root: Path, registry_id: str) -> dict[str, object]:
        registry_id = registry_id.strip()
        if not registry_id:
            raise ValueError("plugin registry id cannot be empty")
        config_path, remaining = self._delete_plugin_registry_config(repo_root, registry_id)
        return {
            "registry_id": registry_id,
            "deleted": True,
            "config_path": str(config_path),
            "registries": remaining,
        }

    def refresh_plugin_registry(self, *, repo_root: Path, registry_id: str) -> dict[str, object]:
        registry_id = registry_id.strip()
        registry = next((item for item in self._plugin_catalog_sources(repo_root) if item.get("registry_id") == registry_id), None)
        if registry is None:
            raise KeyError(registry_id)
        read_result = self._read_plugin_registry_source(repo_root=repo_root, registry=registry, force_refresh=True)
        return {
            **registry,
            "entry_count": len(read_result["entries"]),
            "cached": bool(read_result["cached"]),
            "cache_path": read_result["cache_path"],
            "error": read_result["error"],
            "diagnostics": read_result["diagnostics"],
            "last_checked_at": read_result["last_checked_at"],
        }

    def install_plugin(
        self,
        *,
        repo_root: Path,
        source: str,
        plugin_id: str | None = None,
        enable: bool = True,
        force: bool = False,
    ) -> dict[str, object]:
        source = source.strip()
        if not source:
            raise ValueError("plugin source is required")

        config_dir = default_anvil_config_dir(repo_root)
        plugins_dir = config_dir / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)

        tmp_dir = plugins_dir / f".install-{uuid4().hex}"
        tmp_dir.mkdir(parents=True, exist_ok=False)
        try:
            staged = tmp_dir / "plugin"
            install_source = self._materialize_plugin_source(source, staged)
            manifest = self._read_plugin_manifest(install_source)
            resolved_plugin_id = self._safe_plugin_id(plugin_id or str(manifest.get("plugin_id") or manifest.get("id") or manifest.get("name") or self._plugin_name_from_source(source)))
            target = (plugins_dir / resolved_plugin_id).resolve()
            plugins_root = plugins_dir.resolve()
            try:
                target.relative_to(plugins_root)
            except ValueError as exc:
                raise ValueError("plugin destination escapes the plugin directory") from exc

            if target.exists():
                if not force:
                    raise ValueError(f"plugin '{resolved_plugin_id}' already exists; pass force=true to reinstall")
                shutil.rmtree(target)
            shutil.copytree(install_source, target)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        installed_manifest = self._read_plugin_manifest(target)
        plugin_config = self._plugin_config_from_manifest(
            plugin_id=resolved_plugin_id,
            target=target,
            manifest=installed_manifest,
            enable=enable,
            source=source,
        )
        config_path = self._upsert_plugin_config(config_dir, resolved_plugin_id, plugin_config)
        bundled_mcp_servers = self._read_bundled_mcp_servers(target)
        if bundled_mcp_servers:
            self._upsert_bundled_mcp_config(repo_root, bundled_mcp_servers)

        return {
            "plugin_id": resolved_plugin_id,
            "installed": True,
            "enabled": enable,
            "source": source,
            "path": str(target),
            "config_path": str(config_path),
            "skill_roots": plugin_config.get("skill_roots", []),
            "tool_count": len(plugin_config.get("inline_tools", [])),
            "bundled_mcp_servers": sorted(bundled_mcp_servers),
        }

    def _plugin_registry_config_path(self, repo_root: Path) -> Path:
        return default_anvil_config_dir(repo_root) / "plugin-registries.json"

    def _plugin_registry_cache_dir(self, repo_root: Path) -> Path:
        return default_anvil_config_dir(repo_root) / "plugin-registry-cache"

    def _plugin_catalog_sources(self, repo_root: Path) -> tuple[dict[str, object], ...]:
        configured = self._read_configured_plugin_registries(repo_root)
        sources_by_id: dict[str, dict[str, object]] = {}
        for registry in configured:
            sources_by_id[str(registry["registry_id"])] = registry
        for registry in self._builtin_plugin_registry_sources(repo_root):
            sources_by_id.setdefault(str(registry["registry_id"]), registry)
        return tuple(
            sorted(
                sources_by_id.values(),
                key=lambda item: (0 if bool(item.get("readonly")) else 1, str(item.get("name") or "").lower()),
            )
        )

    def _builtin_plugin_registry_sources(self, repo_root: Path) -> tuple[dict[str, object], ...]:
        config_dir = default_anvil_config_dir(repo_root)
        candidates: list[dict[str, object]] = [
            {
                "registry_id": "workspace",
                "name": "Workspace catalog",
                "source": str(config_dir / "plugin-catalog.json"),
                "trust_level": "workspace",
            },
            {
                "registry_id": "project",
                "name": "Project catalog",
                "source": str(repo_root / "plugin-catalog.json"),
                "trust_level": "project",
            },
            {
                "registry_id": "project-plugins",
                "name": "Anvil curated plugins",
                "source": str(repo_root / "plugins" / "catalog.json"),
                "trust_level": "curated",
            },
        ]
        if sys.platform == "win32" or Path.home().name not in {"root", "app", "container"}:
            for registry_id, name, relative_parts in (
                ("codex-openai-bundled", "Codex bundled plugins", (".codex", "plugins", "cache", "openai-bundled")),
                ("codex-openai-curated", "Codex curated plugins", (".codex", "plugins", "cache", "openai-curated")),
                ("codex-primary-runtime", "Codex primary runtime plugins", (".codex", "plugins", "cache", "openai-primary-runtime")),
                ("claude-official-plugins", "Claude official plugins", (".codex", "plugins", "cache", "claude-plugins-official")),
            ):
                candidates.append(
                    {
                        "registry_id": registry_id,
                        "name": name,
                        "source": str(Path.home().joinpath(*relative_parts)),
                        "trust_level": "curated",
                    }
                )
        items: list[dict[str, object]] = []
        for candidate in candidates:
            source_path = Path(str(candidate["source"]))
            if not source_path.exists():
                continue
            items.append(
                {
                    **candidate,
                    "source_kind": self._registry_source_kind(str(candidate["source"])),
                    "enabled": True,
                    "readonly": True,
                    "config_path": None,
                }
            )
        return tuple(items)

    def _read_configured_plugin_registries(self, repo_root: Path) -> tuple[dict[str, object], ...]:
        path = self._plugin_registry_config_path(repo_root)
        if not path.exists():
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        raw_registries = payload.get("registries") if isinstance(payload, dict) else None
        if isinstance(raw_registries, dict):
            raw_items = [
                {"registry_id": registry_id, **registry}
                for registry_id, registry in raw_registries.items()
                if isinstance(registry, dict)
            ]
        elif isinstance(raw_registries, list):
            raw_items = [item for item in raw_registries if isinstance(item, dict)]
        else:
            raw_items = []
        registries: list[dict[str, object]] = []
        for item in raw_items:
            source = str(item.get("source") or "").strip()
            if not source:
                continue
            registry_id = self._safe_plugin_id(str(item.get("registry_id") or item.get("id") or self._registry_id_from_source(source)))
            registries.append(
                {
                    "registry_id": registry_id,
                    "name": str(item.get("name") or self._registry_name_from_source(source)),
                    "source": source,
                    "source_kind": self._registry_source_kind(source),
                    "enabled": bool(item.get("enabled", True)),
                    "readonly": False,
                    "trust_level": item.get("trust_level") or item.get("trust") or "third-party",
                    "config_path": str(path),
                }
            )
        return tuple(registries)

    def _upsert_plugin_registry_config(self, repo_root: Path, registry: dict[str, object]) -> tuple[Path, list[dict[str, object]]]:
        path = self._plugin_registry_config_path(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        registries = [dict(item) for item in self._read_configured_plugin_registries(repo_root)]
        next_items = [item for item in registries if item.get("registry_id") != registry.get("registry_id")]
        next_items.append(
            {
                "registry_id": registry["registry_id"],
                "name": registry["name"],
                "source": registry["source"],
                "enabled": registry["enabled"],
                "trust_level": registry.get("trust_level"),
            }
        )
        payload = {"registries": sorted(next_items, key=lambda item: str(item.get("name") or item.get("registry_id")).lower())}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path, list(self._read_configured_plugin_registries(repo_root))

    def _delete_plugin_registry_config(self, repo_root: Path, registry_id: str) -> tuple[Path, list[dict[str, object]]]:
        path = self._plugin_registry_config_path(repo_root)
        configured = [dict(item) for item in self._read_configured_plugin_registries(repo_root)]
        if not any(item.get("registry_id") == registry_id for item in configured):
            raise KeyError(registry_id)
        next_items = [item for item in configured if item.get("registry_id") != registry_id]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "registries": [
                        {
                            "registry_id": item["registry_id"],
                            "name": item["name"],
                            "source": item["source"],
                            "enabled": item.get("enabled", True),
                            "trust_level": item.get("trust_level"),
                        }
                        for item in next_items
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return path, list(self._read_configured_plugin_registries(repo_root))

    def _read_plugin_catalog(self, path: Path) -> tuple[dict[str, object], ...]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        return self._extract_plugin_catalog_entries(payload)

    def _extract_plugin_catalog_entries(self, payload: object) -> tuple[dict[str, object], ...]:
        raw_items: object
        if isinstance(payload, dict):
            raw_items = payload.get("plugins", [])
        else:
            raw_items = payload
        if not isinstance(raw_items, list):
            return ()
        return tuple(item for item in raw_items if isinstance(item, dict))

    def _read_plugin_registry_source(
        self,
        *,
        repo_root: Path,
        registry: dict[str, object],
        force_refresh: bool = False,
    ) -> dict[str, object]:
        source = str(registry.get("source") or "").strip()
        result: dict[str, object] = {
            "entries": (),
            "catalog_source": source,
            "catalog_base": source,
            "cached": False,
            "cache_path": None,
            "error": None,
            "diagnostics": [],
            "last_checked_at": datetime.now(timezone.utc),
        }
        if not source:
            result["error"] = "registry source is empty"
            return result
        if source.startswith(("http://", "https://")):
            return self._read_remote_plugin_registry(repo_root=repo_root, registry=registry, force_refresh=force_refresh)

        local_path = self._local_source_path(source)
        if local_path is None:
            candidate = Path(source).expanduser()
            if not candidate.is_absolute():
                candidate = (repo_root / candidate).resolve()
            if candidate.exists():
                local_path = candidate.resolve()
        if local_path is None:
            result["error"] = f"registry source '{source}' was not found"
            return result
        result["catalog_source"] = str(local_path)
        result["catalog_base"] = local_path
        if local_path.is_file():
            result["entries"] = self._read_plugin_catalog(local_path)
            result["catalog_base"] = local_path
            return result
        if local_path.is_dir():
            for catalog_name in ("catalog.json", "plugin-catalog.json", "plugins.json"):
                catalog_path = local_path / catalog_name
                if catalog_path.exists() and catalog_path.is_file():
                    result["entries"] = self._read_plugin_catalog(catalog_path)
                    result["catalog_source"] = str(catalog_path)
                    result["catalog_base"] = catalog_path
                    return result
            result["entries"] = self._scan_plugin_directory(local_path)
            result["catalog_base"] = local_path
            return result
        result["error"] = f"registry source '{source}' is not a file or directory"
        return result

    def _read_remote_plugin_registry(
        self,
        *,
        repo_root: Path,
        registry: dict[str, object],
        force_refresh: bool = False,
    ) -> dict[str, object]:
        source = str(registry["source"])
        cache_dir = self._plugin_registry_cache_dir(repo_root)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{self._safe_plugin_id(str(registry['registry_id']))}.json"
        payload: object | None = None
        error: str | None = None
        cached = False
        if force_refresh or not cache_path.exists():
            try:
                with urlopen(source, timeout=12) as response:  # noqa: S310 - user-configured plugin registry URL.
                    body = response.read(2_000_000)
                text = body.decode("utf-8")
                payload = json.loads(text)
                cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        if payload is None and cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                cached = True
            except (OSError, json.JSONDecodeError) as exc:
                error = error or str(exc)
        entries = self._extract_plugin_catalog_entries(payload) if payload is not None else ()
        return {
            "entries": entries,
            "catalog_source": source,
            "catalog_base": source,
            "cached": cached,
            "cache_path": str(cache_path),
            "error": error,
            "diagnostics": [error] if error else [],
            "last_checked_at": datetime.now(timezone.utc),
        }

    def _scan_plugin_directory(self, directory: Path) -> tuple[dict[str, object], ...]:
        candidates = [directory]
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            candidates.append(child)
            for grandchild in sorted(child.iterdir()):
                if grandchild.is_dir():
                    candidates.append(grandchild)
        entries: list[dict[str, object]] = []
        for candidate in candidates:
            manifest = self._read_plugin_manifest(candidate)
            if not manifest:
                continue
            plugin_id = manifest.get("plugin_id") or manifest.get("id") or manifest.get("name") or candidate.name
            entries.append(
                {
                    "plugin_id": str(plugin_id),
                    "name": self._plugin_display_name(manifest, str(plugin_id)),
                    "description": self._plugin_description(manifest),
                    "source": str(candidate),
                    "version": manifest.get("version"),
                    "author": self._plugin_author(manifest),
                    "homepage": manifest.get("homepage") or manifest.get("repository"),
                    "tags": self._list_str_items(manifest.get("tags") or manifest.get("keywords")),
                    "trust_level": manifest.get("trust"),
                    "inline_tools": manifest.get("inline_tools") or manifest.get("tools") or [],
                    "resources": manifest.get("resources") or [],
                    "prompts": manifest.get("prompts") or [],
                    "memory_providers": manifest.get("memory_providers") or manifest.get("memoryProviders") or [],
                    "permissions": dict(manifest.get("catalog_metadata") or {}).get("permissions", []),
                    "catalog_metadata": manifest.get("catalog_metadata") or {},
                }
            )
        return tuple(entries)

    def _plugin_catalog_entry_from_raw(
        self,
        raw_entry: dict[str, object],
        *,
        catalog_source: str,
        catalog_base: object,
        repo_root: Path,
        registry: dict[str, object],
        installed_plugins: dict[str, object],
    ) -> dict[str, object] | None:
        source = str(raw_entry.get("source") or raw_entry.get("url") or "").strip()
        if not source:
            return None
        resolved_source = self._resolve_catalog_source(source, catalog_base=catalog_base, repo_root=repo_root)
        local_source = self._local_source_path(resolved_source)
        manifest: dict[str, object] = {}
        bundled_mcp_servers: dict[str, object] = {}
        if local_source is not None and local_source.is_dir():
            manifest = self._read_plugin_manifest(local_source)
            bundled_mcp_servers = self._read_bundled_mcp_servers(local_source)
        plugin_id_value = (
            raw_entry.get("plugin_id")
            or raw_entry.get("id")
            or manifest.get("plugin_id")
            or manifest.get("id")
            or manifest.get("name")
            or self._plugin_name_from_source(source)
        )
        plugin_id = self._safe_plugin_id(str(plugin_id_value))
        installed_plugin = installed_plugins.get(plugin_id)
        inline_tools = self._list_manifest_items(
            manifest.get("inline_tools")
            or manifest.get("tools")
            or raw_entry.get("inline_tools")
            or raw_entry.get("tools")
        )
        resources = self._list_manifest_items(manifest.get("resources") or raw_entry.get("resources"))
        prompts = self._list_manifest_items(manifest.get("prompts") or raw_entry.get("prompts"))
        memory_provider_items = self._list_manifest_items(
            manifest.get("memory_providers")
            or manifest.get("memoryProviders")
            or raw_entry.get("memory_providers")
            or raw_entry.get("memoryProviders")
        )
        skill_roots = self._list_str_items(manifest.get("skill_roots") or raw_entry.get("skill_roots"))
        if local_source is not None and (local_source / "skills").is_dir():
            skill_roots.append(str(local_source / "skills"))
        tool_names = [
            str(item.get("name"))
            for item in inline_tools
            if isinstance(item, dict) and item.get("name") is not None and str(item.get("name")).strip()
        ]
        mcp_servers = self._list_str_items(raw_entry.get("mcp_servers"))
        memory_providers = [
            str(item.get("provider_id") or item.get("id") or item.get("name"))
            for item in memory_provider_items
            if isinstance(item, dict) and str(item.get("provider_id") or item.get("id") or item.get("name") or "").strip()
        ]
        for server_id in sorted(bundled_mcp_servers):
            if server_id not in mcp_servers:
                mcp_servers.append(server_id)
        catalog_metadata = {
            **dict(manifest.get("catalog_metadata") or {}),
            **dict(raw_entry.get("catalog_metadata") or {}),
            "catalog_source": catalog_source,
            "registry_id": str(registry.get("registry_id") or ""),
            "registry_name": str(registry.get("name") or ""),
        }
        author = raw_entry.get("author") or raw_entry.get("publisher") or self._plugin_author(manifest) or catalog_metadata.get("publisher")
        return {
            "plugin_id": plugin_id,
            "name": str(raw_entry.get("name") or raw_entry.get("display_name") or self._plugin_display_name(manifest, plugin_id)),
            "description": str(raw_entry.get("description") or raw_entry.get("summary") or self._plugin_description(manifest)),
            "source": resolved_source,
            "source_kind": self._plugin_source_kind(resolved_source),
            "version": raw_entry.get("version") or manifest.get("version"),
            "author": author,
            "homepage": raw_entry.get("homepage") or manifest.get("homepage") or manifest.get("repository"),
            "tags": self._list_str_items(raw_entry.get("tags") or manifest.get("tags") or manifest.get("keywords")),
            "trust_level": raw_entry.get("trust_level") or raw_entry.get("trust") or manifest.get("trust") or catalog_metadata.get("trust_level") or registry.get("trust_level"),
            "registry_id": registry.get("registry_id"),
            "registry_name": registry.get("name"),
            "registry_source": registry.get("source"),
            "registry_kind": registry.get("source_kind"),
            "installed": installed_plugin is not None,
            "enabled": bool(getattr(installed_plugin, "enabled", False)) if installed_plugin is not None else False,
            "installable": bool(raw_entry.get("installable", True)),
            "skill_count": len(skill_roots),
            "tool_count": len(tool_names),
            "mcp_server_count": len(mcp_servers),
            "resource_count": len(resources),
            "prompt_count": len(prompts),
            "memory_provider_count": len(memory_providers),
            "skill_roots": skill_roots,
            "tool_names": tool_names,
            "mcp_servers": mcp_servers,
            "memory_providers": memory_providers,
            "permissions": self._list_str_items(raw_entry.get("permissions") or catalog_metadata.get("permissions")),
            "catalog_metadata": catalog_metadata,
            "discovery_source": "catalog",
        }

    def _plugin_catalog_entry_from_installed(self, plugin_id: str, plugin) -> dict[str, object]:
        catalog_metadata = dict(plugin.catalog_metadata)
        source = str(catalog_metadata.get("source") or plugin.source_path or plugin_id)
        tool_names = [str(item.get("name")) for item in plugin.inline_tools if item.get("name")]
        memory_providers = [
            str(provider.provider_id)
            for provider in plugin.memory_providers
            if str(provider.provider_id).strip()
        ]
        return {
            "plugin_id": plugin_id,
            "name": str(catalog_metadata.get("display_name") or catalog_metadata.get("name") or plugin_id),
            "description": str(catalog_metadata.get("description") or ""),
            "source": source,
            "source_kind": self._plugin_source_kind(source),
            "version": catalog_metadata.get("version"),
            "author": catalog_metadata.get("publisher") or catalog_metadata.get("author"),
            "homepage": catalog_metadata.get("homepage"),
            "tags": self._list_str_items(catalog_metadata.get("tags")),
            "trust_level": catalog_metadata.get("trust_level") or catalog_metadata.get("trust"),
            "registry_id": "installed",
            "registry_name": "Installed plugins",
            "registry_source": plugin.source_path,
            "registry_kind": "installed",
            "installed": True,
            "enabled": bool(plugin.enabled),
            "installable": True,
            "skill_count": len(plugin.skill_roots),
            "tool_count": len(tool_names),
            "mcp_server_count": 0,
            "resource_count": len(plugin.resources),
            "prompt_count": len(plugin.prompts),
            "memory_provider_count": len(memory_providers),
            "skill_roots": [str(root) for root in plugin.skill_roots],
            "tool_names": tool_names,
            "mcp_servers": [],
            "memory_providers": memory_providers,
            "permissions": self._list_str_items(catalog_metadata.get("permissions")),
            "catalog_metadata": catalog_metadata,
            "discovery_source": "plugin_config",
        }

    def _registry_precedence(self, entry: dict[str, object]) -> int:
        if bool(entry.get("installed")):
            return 100
        trust = str(entry.get("trust_level") or "")
        if trust in {"workspace", "project"}:
            return 80
        if trust == "official":
            return 70
        if trust == "third-party":
            return 50
        if trust == "example":
            return 10
        return 40

    def _resolve_catalog_source(self, source: str, *, catalog_base: object, repo_root: Path) -> str:
        if source.startswith(("https://", "http://", "git@", "ssh://", "file://")):
            return source
        if isinstance(catalog_base, str) and catalog_base.startswith(("http://", "https://")):
            return urljoin(catalog_base, source)
        candidate = Path(source).expanduser()
        if candidate.is_absolute():
            return str(candidate)
        local_base = catalog_base if isinstance(catalog_base, Path) else repo_root
        if local_base.is_file():
            local_base = local_base.parent
        for base in (local_base, repo_root):
            resolved = (base / source).resolve()
            if resolved.exists():
                return str(resolved)
        return source

    def _registry_id_from_source(self, source: str) -> str:
        parsed = source.rstrip("/\\")
        if parsed.startswith(("http://", "https://")):
            url = urlparse(parsed)
            parsed = f"{url.netloc}-{Path(url.path).stem or 'catalog'}"
        else:
            parsed = self._plugin_name_from_source(parsed)
        return self._safe_plugin_id(parsed or "registry")

    def _registry_name_from_source(self, source: str) -> str:
        if source.startswith(("http://", "https://")):
            parsed = urlparse(source)
            return parsed.netloc or "Plugin registry"
        return self._plugin_name_from_source(source).replace("-", " ").replace("_", " ").strip().title() or "Plugin registry"

    def _registry_source_kind(self, source: str) -> str:
        if source.startswith(("http://", "https://")):
            return "remote_json"
        local_path = self._local_source_path(source)
        if local_path is not None:
            if local_path.is_dir():
                return "local_directory"
            if local_path.is_file():
                return "local_catalog"
        if Path(source).suffix.lower() == ".json":
            return "local_catalog"
        return "unknown"

    def _plugin_source_kind(self, source: str) -> str:
        local_path = self._local_source_path(source)
        if local_path is not None:
            if local_path.is_dir():
                return "local"
            if local_path.suffix.lower() in {".zip", ".skill"}:
                return "archive"
        if source.startswith(("https://", "http://", "git@", "ssh://")) or source.count("/") == 1:
            return "git"
        return "unknown"

    def _list_str_items(self, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, dict):
            return [str(key) for key in value if str(key).strip()]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item).strip()]
        if str(value).strip():
            return [str(value)]
        return []

    def _merge_runtime_state(
        self,
        config: EffectiveConfig,
        materialization: ExtensionMaterialization,
        *,
        refreshed: bool = False,
        refresh_owner: str | None = None,
    ) -> ExtensionMaterialization:
        server_config = config.extensions.mcp_servers.get(materialization.server_id)
        backoff_seconds = int((server_config.reconnect_policy or {}).get("backoff_seconds", 30)) if server_config else 30

        if materialization.status is ExternalCapabilityStatus.AUTH_REQUIRED:
            state = self.lifecycle.mark_auth_required(materialization.server_id, materialization.error or "auth required")
        elif materialization.status is ExternalCapabilityStatus.FAILED:
            state = self.lifecycle.mark_failed(materialization.server_id, materialization.error or "materialization failed", backoff_seconds=backoff_seconds)
        elif refreshed:
            state = self.lifecycle.mark_refreshed(materialization.server_id)
        elif materialization.status in {ExternalCapabilityStatus.READY, ExternalCapabilityStatus.MATERIALIZED, ExternalCapabilityStatus.VISIBLE, ExternalCapabilityStatus.ENABLED}:
            state = self.lifecycle.mark_ready(materialization.server_id)
        else:
            state = self.lifecycle.snapshot(materialization.server_id)

        self.lifecycle.release_refresh(materialization.server_id, refresh_owner)
        effective_status = materialization.status
        if materialization.status is ExternalCapabilityStatus.MATERIALIZED:
            effective_status = ExternalCapabilityStatus.READY
        return materialization.model_copy(
            update={
                "status": effective_status,
                "connected": state.connected,
                "ready": state.ready,
                "auth_required": state.auth_required,
                "refresh_owner": state.refresh_owner,
                "last_started_at": state.last_started_at,
                "last_refreshed_at": state.last_refreshed_at,
                "backoff_until": state.backoff_until,
                "reconnect_count": state.reconnect_count,
                "diagnostics": tuple(dict.fromkeys([*materialization.diagnostics, *state.diagnostics])),
            }
        )

    def _materialize_plugin(self, plugin_id: str, plugin) -> ExtensionMaterialization:
        tools: list[ToolRegistryEntry] = []
        resources = tuple(
            CapabilityResource(
                resource_id=str(item.get("resource_id") or item.get("name") or ""),
                title=str(item.get("title") or item.get("resource_id") or item.get("name") or ""),
                description=str(item.get("description") or ""),
                server_id=plugin_id,
                path=str(item.get("path")) if item.get("path") is not None else None,
                metadata={
                    "discovery_source": "plugin_config",
                    **{str(key): value for key, value in item.items() if key not in {"resource_id", "name", "title", "description", "path"}},
                },
            )
            for item in plugin.resources
            if str(item.get("resource_id") or item.get("name") or "").strip()
        )
        prompts = tuple(
            CapabilityPrompt(
                prompt_id=str(item.get("prompt_id") or item.get("name") or ""),
                title=str(item.get("title") or item.get("prompt_id") or item.get("name") or ""),
                description=str(item.get("description") or ""),
                server_id=plugin_id,
                arguments=tuple(str(arg) for arg in item.get("arguments", []) if str(arg).strip()),
                metadata={
                    "discovery_source": "plugin_config",
                    **{str(key): value for key, value in item.items() if key not in {"prompt_id", "name", "title", "description", "arguments"}},
                },
            )
            for item in plugin.prompts
            if str(item.get("prompt_id") or item.get("name") or "").strip()
        )
        for tool_spec in plugin.inline_tools:
            from langchain_core.tools import tool

            name = str(tool_spec["name"])
            display_name = str(tool_spec.get("display_name", name))
            response = tool_spec.get("response", {"plugin_id": plugin_id, "tool": name})

            @tool(name, description=display_name)
            def _handler(response_payload=json.dumps(response, ensure_ascii=False)) -> str:
                return response_payload

            tools.append(
                ToolRegistryEntry(
                    name=name,
                    display_name=display_name,
                    source_kind=ToolSourceKind.PLUGIN,
                    source_id=plugin_id,
                    capability_group=str(tool_spec.get("capability_group", "plugin")),
                    summary=str(tool_spec.get("summary", display_name)),
                    handler=_handler,
                    input_schema=dict(tool_spec.get("schema", {})),
                    provenance={
                        "origin": "plugin_config",
                        "plugin_id": plugin_id,
                        "source_path": plugin.source_path,
                        "catalog_metadata": plugin.catalog_metadata,
                    },
                    resources=resources,
                    prompts=prompts,
                )
            )
        has_memory_providers = bool(getattr(plugin, "memory_providers", ()))
        return ExtensionMaterialization(
            server_id=plugin_id,
            source_kind="plugin",
            status=ExternalCapabilityStatus.READY if (tools or resources or prompts or has_memory_providers) else ExternalCapabilityStatus.ENABLED,
            discovery_source="plugin_config",
            tools=tuple(tools),
            resources=resources,
            prompts=prompts,
            connected=bool(tools or resources or prompts or has_memory_providers),
            ready=bool(tools or resources or prompts or has_memory_providers),
            metadata={
                "catalog_metadata": dict(plugin.catalog_metadata),
                "source_path": plugin.source_path,
                "memory_providers": [provider.model_dump(mode="json") for provider in plugin.memory_providers],
            },
        )

    def _materialize_plugin_source(self, source: str, staged: Path) -> Path:
        local_path = self._local_source_path(source)
        if local_path is not None:
            if local_path.is_dir():
                _copy_plugin_tree(local_path, staged)
                return staged
            if local_path.is_file() and local_path.suffix.lower() in {".zip", ".skill"}:
                staged.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(local_path) as archive:
                    scan = _scan_plugin_package_entries(archive)
                    _extract_plugin_package(archive, staged, scan)
                children = _top_level_plugin_package_dirs(staged, scan)
                if len(children) == 1 and not any((staged / name).exists() for name in _PLUGIN_MANIFEST_PATHS):
                    return children[0]
                return staged
            raise ValueError(f"unsupported plugin source path '{source}'")

        git_url = self._resolve_git_url(source)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", git_url, str(staged)],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "git clone failed").strip()
            raise ValueError(detail)
        return staged

    def _local_source_path(self, source: str) -> Path | None:
        if source.startswith("file://"):
            return Path(urlparse(source).path).expanduser().resolve()
        candidate = Path(source).expanduser()
        if candidate.exists():
            return candidate.resolve()
        return None

    def _resolve_git_url(self, source: str) -> str:
        if source.startswith(("https://", "http://", "git@", "ssh://", "file://")):
            return source
        parts = source.strip("/").split("/")
        if len(parts) == 2 and all(parts):
            return f"https://github.com/{parts[0]}/{parts[1]}.git"
        raise ValueError("plugin source must be a local path, Git URL, or owner/repo shorthand")

    def _plugin_name_from_source(self, source: str) -> str:
        parsed = source.rstrip("/")
        if parsed.endswith(".git"):
            parsed = parsed[:-4]
        if "/" in parsed:
            parsed = parsed.rsplit("/", 1)[-1]
        if "\\" in parsed:
            parsed = parsed.rsplit("\\", 1)[-1]
        if ":" in parsed:
            parsed = parsed.rsplit(":", 1)[-1]
        return parsed or "plugin"

    def _safe_plugin_id(self, value: str) -> str:
        plugin_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
        if not plugin_id or plugin_id in {".", ".."} or "/" in plugin_id or "\\" in plugin_id:
            raise ValueError("plugin_id must contain only letters, numbers, dot, underscore, and dash")
        return plugin_id

    def _read_plugin_manifest(self, plugin_dir: Path) -> dict[str, object]:
        for name in (
            "anvil.plugin.json",
            "plugin.json",
            ".codex-plugin/plugin.json",
            ".claude-plugin/plugin.json",
            ".cursor-plugin/plugin.json",
        ):
            path = plugin_dir / name
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                return payload if isinstance(payload, dict) else {}
        for name in ("plugin.yaml", "plugin.yml"):
            path = plugin_dir / name
            if path.exists():
                try:
                    import yaml

                    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    return payload if isinstance(payload, dict) else {}
                except Exception:
                    return {}
        return {}

    def _plugin_display_name(self, manifest: dict[str, object], plugin_id: str) -> str:
        interface = manifest.get("interface")
        if isinstance(interface, dict):
            display_name = interface.get("displayName") or interface.get("display_name")
            if display_name:
                return str(display_name)
        return str(manifest.get("display_name") or manifest.get("name") or plugin_id)

    def _plugin_description(self, manifest: dict[str, object]) -> str:
        interface = manifest.get("interface")
        if isinstance(interface, dict):
            description = interface.get("shortDescription") or interface.get("short_description") or interface.get("longDescription")
            if description:
                return str(description)
        return str(manifest.get("description") or manifest.get("summary") or "")

    def _plugin_author(self, manifest: dict[str, object]) -> object:
        author = manifest.get("author")
        if isinstance(author, dict):
            return author.get("name") or author.get("email") or author
        if author:
            return author
        interface = manifest.get("interface")
        if isinstance(interface, dict):
            return interface.get("developerName") or interface.get("developer_name")
        return None

    def _plugin_config_from_manifest(
        self,
        *,
        plugin_id: str,
        target: Path,
        manifest: dict[str, object],
        enable: bool,
        source: str,
    ) -> dict[str, object]:
        skill_roots = self._list_manifest_items(manifest.get("skill_roots"))
        if (target / "skills").is_dir() and str(target / "skills") not in skill_roots:
            skill_roots.append(str(target / "skills"))
        inline_tools = self._list_manifest_items(manifest.get("inline_tools") or manifest.get("tools"))
        resources = self._list_manifest_items(manifest.get("resources"))
        prompts = self._list_manifest_items(manifest.get("prompts"))
        memory_providers = self._list_manifest_items(manifest.get("memory_providers") or manifest.get("memoryProviders"))
        catalog_metadata = dict(manifest.get("catalog_metadata") or {})
        catalog_metadata.setdefault("source", source)
        catalog_metadata.setdefault("installed_by", "ops_console")
        return {
            "enabled": enable,
            "source_path": str(target),
            "skill_roots": [str(Path(root).expanduser()) for root in skill_roots],
            "inline_tools": inline_tools,
            "resources": resources,
            "prompts": prompts,
            "memory_providers": memory_providers,
            "catalog_metadata": {
                "plugin_id": plugin_id,
                **catalog_metadata,
            },
        }

    def _list_manifest_items(self, value: object) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return [value]

    def _upsert_plugin_config(self, config_dir: Path, plugin_id: str, plugin_config: dict[str, object]) -> Path:
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / "plugins.json"
        payload: dict[str, object]
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {}
        else:
            payload = {}
        plugins = payload.get("plugins")
        if not isinstance(plugins, dict):
            extensions = payload.get("extensions")
            if isinstance(extensions, dict) and isinstance(extensions.get("plugins"), dict):
                plugins = dict(extensions["plugins"])
            else:
                plugins = {}
        plugins[plugin_id] = plugin_config
        payload = {"plugins": plugins}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _read_bundled_mcp_servers(self, plugin_dir: Path) -> dict[str, object]:
        return read_bundled_mcp_servers(plugin_dir)

    def _upsert_bundled_mcp_config(self, repo_root: Path, servers: dict[str, object]) -> Path:
        _ = repo_root
        config_path = resolve_anvil_config_path()
        return upsert_mcp_servers_in_config_file(config_path, servers)


def _bounded_plugin_package_scan_limit() -> int:
    configured = DEFAULT_PLUGIN_PACKAGE_SCAN_LIMIT
    if configured < 1:
        return 1
    return min(configured, MAX_PLUGIN_PACKAGE_SCAN_LIMIT)


def _bounded_plugin_tree_scan_limit() -> int:
    configured = DEFAULT_PLUGIN_TREE_SCAN_LIMIT
    if configured < 1:
        return 1
    return min(configured, MAX_PLUGIN_TREE_SCAN_LIMIT)


def _normalize_plugin_archive_path(filename: str) -> tuple[str, tuple[str, ...]]:
    normalized = str(filename).replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("plugin package contains an empty path")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("plugin package contains path traversal")
    return normalized, tuple(path.parts)


def _is_plugin_package_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _scan_plugin_package_entries(archive: zipfile.ZipFile) -> _PluginPackageScan:
    max_scanned_paths = _bounded_plugin_package_scan_limit()
    entries: list[_PluginPackageEntry] = []
    scanned_path_count = 0
    scan_truncated = False
    total_uncompressed_bytes = 0
    for info in archive.filelist:
        if scanned_path_count >= max_scanned_paths:
            scan_truncated = True
            break
        scanned_path_count += 1
        filename, parts = _normalize_plugin_archive_path(info.filename)
        if _is_plugin_package_symlink(info):
            raise ValueError("plugin package contains symlink entries")
        total_uncompressed_bytes += int(info.file_size)
        entries.append(_PluginPackageEntry(info=info, filename=filename, parts=parts))
        if total_uncompressed_bytes > MAX_PLUGIN_PACKAGE_UNCOMPRESSED_BYTES:
            break
    return _PluginPackageScan(
        entries=tuple(entries),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
        total_uncompressed_bytes=total_uncompressed_bytes,
        max_uncompressed_bytes=MAX_PLUGIN_PACKAGE_UNCOMPRESSED_BYTES,
    )


def _extract_plugin_package(
    archive: zipfile.ZipFile,
    staged: Path,
    scan: _PluginPackageScan,
) -> None:
    if scan.scan_truncated:
        raise ValueError("plugin package scan truncated before extraction")
    if scan.total_uncompressed_bytes > scan.max_uncompressed_bytes:
        raise ValueError("plugin package exceeds maximum uncompressed size")
    staged_root = staged.resolve()
    for entry in scan.entries:
        target = (staged / entry.filename).resolve()
        if staged_root not in target.parents and target != staged_root:
            raise ValueError("plugin package contains path traversal")
        if entry.filename.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(entry.info) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


def _top_level_plugin_package_dirs(staged: Path, scan: _PluginPackageScan) -> list[Path]:
    names: set[str] = set()
    file_at_root = False
    for entry in scan.entries:
        if len(entry.parts) == 1 and not entry.filename.endswith("/"):
            file_at_root = True
        if entry.parts:
            names.add(entry.parts[0])
    if file_at_root:
        return []
    return [staged / name for name in sorted(names) if (staged / name).is_dir()]


def _scan_plugin_tree(root: Path) -> _PluginTreeScan:
    max_scanned_paths = _bounded_plugin_tree_scan_limit()
    entries: list[_PluginTreeEntry] = []
    stack: list[tuple[str, str]] = [("", os.fspath(root))]
    scanned_path_count = 0
    scan_truncated = False
    while stack:
        relative_dir, absolute_dir = stack.pop()
        try:
            iterator = os.scandir(absolute_dir)
        except OSError:
            continue
        with iterator as children:
            for child in children:
                if scanned_path_count >= max_scanned_paths:
                    scan_truncated = True
                    stack.clear()
                    break
                scanned_path_count += 1
                relative_path = f"{relative_dir}/{child.name}" if relative_dir else child.name
                try:
                    is_dir = child.is_dir(follow_symlinks=False)
                    is_file = child.is_file(follow_symlinks=False)
                    if child.is_symlink():
                        raise ValueError("plugin source contains symlink entries")
                except OSError:
                    is_dir = False
                    is_file = False
                entries.append(
                    _PluginTreeEntry(
                        relative_path=relative_path.replace("\\", "/"),
                        source_path=child.path,
                        is_dir=is_dir,
                        is_file=is_file,
                    )
                )
                if is_dir:
                    stack.append((relative_path, child.path))
        if scan_truncated:
            break
    return _PluginTreeScan(
        entries=tuple(entries),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _copy_plugin_tree(source: Path, destination: Path) -> None:
    scan = _scan_plugin_tree(source)
    if scan.scan_truncated:
        raise ValueError("plugin source scan truncated before install")
    destination.mkdir(parents=True, exist_ok=False)
    for entry in scan.entries:
        target = destination / entry.relative_path
        if entry.is_dir:
            target.mkdir(parents=True, exist_ok=True)
        elif entry.is_file:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry.source_path, target)
