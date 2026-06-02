from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib import error, request

from .contracts import (
    ArchiveSearchResult,
    ArchiveTurnRecord,
    CuratedEntry,
    MemoryProvider,
    MemoryProviderManifest,
    MemoryProviderTestResult,
    MemoryWriteEvent,
    RecallEvidence,
)


VALID_PROVIDER_ROLES: tuple[str, ...] = (
    "recall",
    "sync",
    "index",
    "reflection",
    "explain",
    "memory_write",
    "session_end",
    "pre_compact",
    "delegation",
    "system_prompt",
)
PASSIVE_PROVIDER_ROLES: tuple[str, ...] = ("sync", "index", "reflection", "explain", "memory_write", "session_end", "pre_compact", "delegation")


@dataclass(frozen=True)
class ProviderTemplate:
    provider_id: str
    display_name: str
    family: str
    description: str
    roles: tuple[str, ...] = PASSIVE_PROVIDER_ROLES
    kind: str = "local_curated"
    origin: str = "builtin"


PROVIDER_TEMPLATES: tuple[ProviderTemplate, ...] = (
    ProviderTemplate("local_curated", "Local Curated Memory", "local_curated", "Built-in local curated/archive memory provider.", roles=PASSIVE_PROVIDER_ROLES),
    ProviderTemplate("anvil_dialect", "Anvil Dialect", "dialectic", "Dialectic recall tuned for user alignment and preference framing."),
    ProviderTemplate("anvil_tiered", "Anvil Tiered", "tiered", "Tiered memory retrieval that prefers compact overviews before deep recall."),
    ProviderTemplate("anvil_extract", "Anvil Extract", "semantic_extract", "Semantic extraction provider optimized for harvesting durable facts from turns."),
    ProviderTemplate("anvil_reflect", "Anvil Reflect", "knowledge_reflect", "Knowledge-graph oriented provider that favors synthesis and reflective recall."),
    ProviderTemplate("anvil_factgraph", "Anvil FactGraph", "local_fact_graph", "Local-first fact graph provider using archive-backed heuristics."),
    ProviderTemplate("anvil_hybrid", "Anvil Hybrid", "hybrid_db", "Hybrid retrieval provider blending structured facts with archive search."),
    ProviderTemplate("anvil_tree", "Anvil Tree", "knowledge_tree", "Knowledge-tree provider shaped for hierarchical memory organization."),
    ProviderTemplate("anvil_semgraph", "Anvil SemGraph", "semantic_graph", "Semantic graph provider for broad associative recall."),
)


class LocalCuratedMemoryProvider(MemoryProvider):
    def __init__(
        self,
        template: ProviderTemplate,
        *,
        active: bool = False,
        enabled: bool = True,
        configured: bool = True,
        roles: tuple[str, ...] | None = None,
    ) -> None:
        self._template = template
        self._active = active
        self._enabled = enabled
        self._configured = configured
        self._roles = _normalize_roles(roles if roles is not None else template.roles)
        self._diagnostics: list[str] = []
        self._last_sync_at: datetime | None = None

    def set_active(self, active: bool) -> None:
        self._active = active and self._enabled

    def has_role(self, role: str) -> bool:
        if not self._enabled:
            return False
        if role == "recall":
            return self._active
        return role in self._roles

    def manifest(self) -> MemoryProviderManifest:
        roles = ("recall", *self._roles) if self._active else self._roles
        return MemoryProviderManifest(
            provider_id=self._template.provider_id,
            display_name=self._template.display_name,
            kind=self._template.kind,
            origin=self._template.origin,
            family=self._template.family,
            description=self._template.description,
            active=self._active,
            configured=self._configured,
            available=self._enabled,
            supports_prefetch=self._active,
            supports_sync="sync" in self._roles,
            supports_index="index" in self._roles,
            supports_reflection="reflection" in self._roles,
            supports_explain="explain" in self._roles,
            roles=tuple(dict.fromkeys(roles)),
            health="ok" if self._enabled else "disabled",
            diagnostics=tuple(self._diagnostics[-10:]),
            last_sync_at=self._last_sync_at,
        )

    def system_prompt_block(self) -> str:
        if not self.has_role("system_prompt"):
            return ""
        return f"{self._template.display_name} is available for local memory lifecycle sync."

    def prefetch(
        self,
        query: str,
        *,
        thread_id: str,
        archive: ArchiveSearchResult,
        curated_matches: tuple[CuratedEntry, ...],
    ) -> tuple[str, ...]:
        notes: list[str] = []
        if curated_matches:
            notes.append(
                f"{self._template.display_name} prioritized {len(curated_matches)} curated memory matches for '{query}'."
            )
        if archive.hits:
            notes.append(
                f"{self._template.display_name} found {len(archive.hits)} archive hits and boosted thread {archive.hits[0].thread_id}."
            )
        return tuple(notes)

    def sync_turn(self, record: ArchiveTurnRecord) -> None:
        self._last_sync_at = datetime.now(timezone.utc)
        return None

    def queue_prefetch(self, query: str, *, thread_id: str) -> None:
        return None

    def index_write(self, *, entry: CuratedEntry | None = None, record: ArchiveTurnRecord | None = None) -> tuple[str, ...]:
        if entry is not None:
            return (f"{self._template.display_name} indexed memory {entry.memory_id or entry.entry_id}.",)
        if record is not None:
            return (f"{self._template.display_name} indexed archive turn {record.archive_id}.",)
        return ()

    def on_session_end(self, *, thread_id: str, messages: list[dict], reason: str = "session_end", allow_network: bool = True) -> tuple[str, ...]:
        if not messages:
            return ()
        self._last_sync_at = datetime.now(timezone.utc)
        return (f"{self._template.display_name} sealed session {thread_id} ({reason}) with {len(messages)} messages.",)

    def on_pre_compact(self, messages: list[dict]) -> str:
        if not messages:
            return ""
        return f"{self._template.display_name} preserved {min(len(messages), 5)} recent messages before compaction."

    def on_delegation(self, *, parent_thread_id: str, task: dict[str, Any], result: dict[str, Any], status: str) -> tuple[str, ...]:
        self._last_sync_at = datetime.now(timezone.utc)
        return (f"{self._template.display_name} synced delegation {task.get('task_id') or task.get('id') or 'task'} for {parent_thread_id}.",)

    def test(self) -> MemoryProviderTestResult:
        manifest = self.manifest()
        return MemoryProviderTestResult(
            provider_id=manifest.provider_id,
            ok=manifest.available,
            health=manifest.health,
            diagnostics=manifest.diagnostics,
        )

    def explain(self, *, query: str, evidence: tuple[RecallEvidence, ...]) -> tuple[str, ...]:
        if not evidence:
            return ()
        return (
            f"{self._template.display_name} surfaced {len(evidence)} evidence items for '{query}'.",
        )

    def on_memory_write(self, event: MemoryWriteEvent) -> None:
        return None

    def shutdown(self, *, allow_network: bool = False) -> None:
        return None


HeuristicMemoryProvider = LocalCuratedMemoryProvider


class HttpMemoryProvider(MemoryProvider):
    def __init__(
        self,
        *,
        provider_id: str,
        display_name: str,
        settings: dict[str, Any],
        roles: tuple[str, ...],
        enabled: bool = True,
        origin: str = "plugin",
    ) -> None:
        self.provider_id = provider_id
        self.display_name = display_name
        self.settings = settings
        self._roles = _normalize_roles(roles)
        self._enabled = enabled
        self._origin = origin
        self._active = False
        self._diagnostics: list[str] = []
        self._last_sync_at: datetime | None = None

    def set_active(self, active: bool) -> None:
        self._active = active and self._enabled

    def has_role(self, role: str) -> bool:
        if not self._enabled:
            return False
        if role == "recall":
            return self._active
        return role in self._roles

    def manifest(self) -> MemoryProviderManifest:
        roles = ("recall", *self._roles) if self._active else self._roles
        health = "ok" if self._enabled else "disabled"
        if self._diagnostics:
            health = "degraded" if self._enabled else "disabled"
        return MemoryProviderManifest(
            provider_id=self.provider_id,
            display_name=self.display_name,
            kind="http",
            origin=self._origin,
            family="http_provider",
            description=str(self.settings.get("description") or "HTTP memory provider"),
            active=self._active,
            configured=True,
            available=self._enabled and bool(self._endpoint()),
            supports_prefetch=self._active,
            supports_sync="sync" in self._roles,
            supports_index="index" in self._roles,
            supports_reflection="reflection" in self._roles,
            supports_explain="explain" in self._roles,
            supports_archive_search=False,
            roles=tuple(dict.fromkeys(roles)),
            health=health,
            diagnostics=tuple(self._diagnostics[-10:]),
            last_sync_at=self._last_sync_at,
        )

    def system_prompt_block(self) -> str:
        payload = self._post("system_prompt_block", {})
        if isinstance(payload, dict):
            return str(payload.get("block") or payload.get("content") or "")
        return ""

    def queue_prefetch(self, query: str, *, thread_id: str) -> None:
        self._post("queue_prefetch", {"query": query, "thread_id": thread_id})

    def prefetch(
        self,
        query: str,
        *,
        thread_id: str,
        archive: ArchiveSearchResult,
        curated_matches: tuple[CuratedEntry, ...],
    ) -> tuple[str, ...]:
        payload = self._post(
            "prefetch",
            {
                "query": query,
                "thread_id": thread_id,
                "archive": archive.model_dump(mode="json"),
                "curated_matches": [entry.model_dump(mode="json") for entry in curated_matches],
            },
        )
        return _notes_from_payload(payload)

    def sync_turn(self, record: ArchiveTurnRecord) -> None:
        self._post("sync_turn", {"turn": record.model_dump(mode="json")})
        self._last_sync_at = datetime.now(timezone.utc)

    def index_write(self, *, entry: CuratedEntry | None = None, record: ArchiveTurnRecord | None = None) -> tuple[str, ...]:
        payload = self._post(
            "index_write",
            {
                "entry": entry.model_dump(mode="json") if entry is not None else None,
                "record": record.model_dump(mode="json") if record is not None else None,
            },
        )
        return _notes_from_payload(payload)

    def on_session_end(self, *, thread_id: str, messages: list[dict[str, Any]], reason: str = "session_end", allow_network: bool = True) -> tuple[str, ...]:
        if not allow_network:
            return ()
        payload = self._post("session_end", {"thread_id": thread_id, "messages": messages, "reason": reason})
        self._last_sync_at = datetime.now(timezone.utc)
        return _notes_from_payload(payload)

    def on_pre_compact(self, messages: list[dict[str, Any]]) -> str:
        payload = self._post("pre_compact", {"messages": messages})
        if isinstance(payload, dict):
            return str(payload.get("note") or payload.get("summary") or "")
        return ""

    def on_delegation(self, *, parent_thread_id: str, task: dict[str, Any], result: dict[str, Any], status: str) -> tuple[str, ...]:
        payload = self._post(
            "delegation",
            {"parent_thread_id": parent_thread_id, "task": task, "result": result, "status": status},
        )
        self._last_sync_at = datetime.now(timezone.utc)
        return _notes_from_payload(payload)

    def explain(self, *, query: str, evidence: tuple[RecallEvidence, ...]) -> tuple[str, ...]:
        payload = self._post(
            "explain",
            {"query": query, "evidence": [item.model_dump(mode="json") for item in evidence]},
        )
        return _notes_from_payload(payload)

    def on_memory_write(self, event: MemoryWriteEvent) -> None:
        self._post("memory_write", {"event": event.model_dump(mode="json")})

    def test(self) -> MemoryProviderTestResult:
        payload = self._post("test", {})
        ok = isinstance(payload, dict) and bool(payload.get("ok", True))
        return MemoryProviderTestResult(
            provider_id=self.provider_id,
            ok=ok,
            health="ok" if ok else "failed",
            diagnostics=tuple(self._diagnostics[-10:]),
        )

    def shutdown(self, *, allow_network: bool = False) -> None:
        if allow_network:
            self._post("shutdown", {})

    def _endpoint(self) -> str:
        return str(self.settings.get("endpoint") or self.settings.get("url") or "").strip()

    def _post(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = self._endpoint()
        if not endpoint or not self._enabled:
            return {}
        timeout = float(self.settings.get("timeout_seconds") or self.settings.get("timeout") or 5.0)
        body = json.dumps({"action": action, **payload}, ensure_ascii=False, default=str).encode("utf-8")
        req = request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json", **_str_dict(self.settings.get("headers"))},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - user-configured provider URL.
                raw = response.read(1_000_000).decode("utf-8")
            parsed = json.loads(raw) if raw.strip() else {}
            if not isinstance(parsed, dict):
                raise ValueError("HTTP memory provider response must be a JSON object")
            return parsed
        except (OSError, error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self._diagnostics.append(f"{action}: {exc.__class__.__name__}: {exc}")
            return {}


class ProviderRegistry:
    def __init__(
        self,
        *,
        active_provider_id: str | None = None,
        catalog: dict[str, Any] | None = None,
        plugin_providers: tuple[Any, ...] = (),
    ) -> None:
        catalog = catalog or {}
        self._providers = {
            template.provider_id: self._build_provider(template, active_provider_id=active_provider_id, catalog=catalog)
            for template in PROVIDER_TEMPLATES
        }
        for provider_config in plugin_providers:
            provider = self._build_plugin_provider(provider_config, active_provider_id=active_provider_id)
            if provider is not None:
                self._providers[provider.manifest().provider_id] = provider
        self._active_provider_id = active_provider_id if active_provider_id in self._providers and self._providers[active_provider_id].manifest().available else None

    @property
    def active_provider_id(self) -> str | None:
        return self._active_provider_id

    def list_providers(self) -> tuple[MemoryProviderManifest, ...]:
        return tuple(provider.manifest() for provider in self._providers.values())

    def providers(self) -> tuple[MemoryProvider, ...]:
        return tuple(self._providers.values())

    def providers_for_role(self, role: str) -> tuple[MemoryProvider, ...]:
        return tuple(provider for provider in self._providers.values() if provider.has_role(role))

    def activate(self, provider_id: str) -> MemoryProviderManifest:
        if provider_id not in self._providers:
            raise KeyError(provider_id)
        if not self._providers[provider_id].manifest().available:
            raise KeyError(provider_id)
        for candidate_id, provider in self._providers.items():
            provider.set_active(candidate_id == provider_id)
        self._active_provider_id = provider_id
        return self._providers[provider_id].manifest()

    def active_provider(self) -> MemoryProvider | None:
        if self._active_provider_id is None:
            return None
        return self._providers[self._active_provider_id]

    def shutdown(self, *, allow_network: bool = False) -> None:
        for provider in self._providers.values():
            provider.shutdown(allow_network=allow_network)

    def _build_provider(
        self,
        template: ProviderTemplate,
        *,
        active_provider_id: str | None,
        catalog: dict[str, Any],
    ) -> LocalCuratedMemoryProvider:
        raw_config = catalog.get(template.provider_id)
        enabled = bool(getattr(raw_config, "enabled", True))
        configured = bool(getattr(raw_config, "configured", raw_config is not None or True))
        roles = getattr(raw_config, "roles", None)
        return LocalCuratedMemoryProvider(
            template,
            active=enabled and template.provider_id == active_provider_id,
            enabled=enabled,
            configured=configured,
            roles=tuple(roles) if roles is not None else None,
        )

    def _build_plugin_provider(self, provider_config: Any, *, active_provider_id: str | None) -> MemoryProvider | None:
        provider_id = str(getattr(provider_config, "provider_id", "") or "").strip()
        if not provider_id:
            return None
        kind = str(getattr(provider_config, "kind", "http") or "http").strip().lower()
        settings = dict(getattr(provider_config, "settings", {}) or {})
        roles = tuple(str(role) for role in getattr(provider_config, "roles", ()) or ())
        enabled = bool(getattr(provider_config, "enabled", True))
        display_name = str(getattr(provider_config, "display_name", None) or provider_id)
        if kind == "http":
            provider = HttpMemoryProvider(
                provider_id=provider_id,
                display_name=display_name,
                settings=settings,
                roles=roles,
                enabled=enabled,
                origin="plugin",
            )
            provider.set_active(provider_id == active_provider_id)
            return provider
        if kind == "local_curated":
            provider = LocalCuratedMemoryProvider(
                ProviderTemplate(
                    provider_id=provider_id,
                    display_name=display_name,
                    family="plugin_local_curated",
                    description=str(settings.get("description") or "Plugin local memory provider"),
                    roles=roles or PASSIVE_PROVIDER_ROLES,
                    kind="local_curated",
                    origin="plugin",
                ),
                active=enabled and provider_id == active_provider_id,
                enabled=enabled,
                configured=True,
                roles=roles or None,
            )
            return provider
        return None


def _normalize_roles(roles: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for role in roles:
        value = str(role).strip().lower()
        if value in VALID_PROVIDER_ROLES and value != "recall" and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _notes_from_payload(payload: dict[str, Any]) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("notes") or payload.get("provider_notes") or payload.get("messages") or ()
    if isinstance(raw, str):
        return (raw,) if raw.strip() else ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw if str(item).strip())
    note = payload.get("note") or payload.get("summary")
    return (str(note),) if note else ()


def _str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}
