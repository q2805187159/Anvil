from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from anvil.agents.features import RuntimeFeatureSet
from anvil.agents.lead_agent.context_files import ProjectContextSnapshot
from anvil.agents.lead_agent.prompt import (
    PromptInjectionView,
    PromptSnapshot,
    PromptSnapshotCacheStats,
    RuntimePathContextSnapshot,
    prompt_snapshot_cache_stats,
)
from anvil.config import ResolvedModelRoute
from anvil.runtime.token_budget import TokenBudgetService
from anvil.runtime.tool_registry.contracts import CapabilityBundle


class RuntimeModelSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subsystem: str
    model_name: str
    source: str
    provider: str
    provider_kind: str
    reasoning_effort: str | None = None
    capabilities: dict[str, bool] = Field(default_factory=dict)


class RuntimePromptAssemblySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    prompt_hash: str
    version: str
    stable_section_names: tuple[str, ...] = ()
    volatile_section_names: tuple[str, ...] = ()
    stable_section_tokens: dict[str, int] = Field(default_factory=dict)
    volatile_section_tokens: dict[str, int] = Field(default_factory=dict)
    stable_prompt_tokens: int = 0
    volatile_prompt_tokens: int = 0
    project_context_fingerprint: str | None = None
    project_context_files: tuple[dict[str, Any], ...] = ()
    project_context_file_count: int = 0
    project_context_truncated_file_count: int = 0
    project_context_total_chars: int = 0
    project_context_cache_status: str | None = None
    project_context_discovery_scanned_path_count: int = 0
    project_context_discovery_max_scanned_paths: int = 0
    project_context_discovery_scan_truncated: bool = False
    runtime_path_fingerprint: str | None = None
    runtime_path_root_count: int = 0
    runtime_path_host_bridge_count: int = 0
    runtime_path_cache_status: str | None = None
    cache: dict[str, Any] = Field(default_factory=dict)
    cache_delta: dict[str, Any] = Field(default_factory=dict)


class RuntimeCapabilityAssemblySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    catalog_fingerprint: str
    visible_tool_names: tuple[str, ...] = ()
    deferred_tool_names: tuple[str, ...] = ()
    discovered_tool_names: tuple[str, ...] = ()
    enabled_skill_ids: tuple[str, ...] = ()
    effective_mcp_servers: tuple[str, ...] = ()
    effective_extension_sources: tuple[str, ...] = ()
    effective_plugin_ids: tuple[str, ...] = ()
    active_promotions: tuple[str, ...] = ()
    assembly_diagnostics: dict[str, Any] = Field(default_factory=dict)


class RuntimeAssemblyDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    changed_paths: tuple[str, ...] = ()
    added: dict[str, tuple[Any, ...]] = Field(default_factory=dict)
    removed: dict[str, tuple[Any, ...]] = Field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


class RuntimeAssemblySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    run_id: str | None = None
    execution_mode: str
    config_fingerprint: str
    model: RuntimeModelSnapshot
    prompt: RuntimePromptAssemblySnapshot
    capabilities: RuntimeCapabilityAssemblySnapshot
    middleware_names: tuple[str, ...] = ()
    memory_injection_diagnostics: dict[str, Any] = Field(default_factory=dict)
    enabled_feature_flags: tuple[str, ...] = ()
    disabled_feature_flags: tuple[str, ...] = ()
    service_flags: dict[str, bool] = Field(default_factory=dict)

    @classmethod
    def from_runtime_parts(
        cls,
        *,
        thread_id: str,
        run_id: str | None,
        execution_mode: str,
        config_fingerprint: str,
        resolved_route: ResolvedModelRoute,
        prompt_snapshot: PromptSnapshot,
        prompt_injection_view: PromptInjectionView,
        project_context_snapshot: ProjectContextSnapshot | None = None,
        runtime_path_snapshot: RuntimePathContextSnapshot | None = None,
        capability_bundle: CapabilityBundle,
        middleware_chain: list[Any],
        feature_set: RuntimeFeatureSet,
        service_flags: dict[str, bool] | None = None,
        prompt_cache_before: PromptSnapshotCacheStats | None = None,
        prompt_cache_after: PromptSnapshotCacheStats | None = None,
    ) -> "RuntimeAssemblySnapshot":
        capability_context = capability_bundle.capability_context
        active_promotions = (
            capability_context.active_promotions
            if capability_context is not None
            else ()
        )
        cache_after = prompt_cache_after or prompt_snapshot_cache_stats()
        stable_section_tokens = _section_token_counts(prompt_snapshot.stable_sections)
        volatile_sections = prompt_injection_view.sections()
        volatile_section_tokens = _section_token_counts(volatile_sections)
        return cls(
            thread_id=thread_id,
            run_id=run_id,
            execution_mode=execution_mode,
            config_fingerprint=config_fingerprint,
            model=RuntimeModelSnapshot(
                subsystem=resolved_route.subsystem,
                model_name=resolved_route.model_name,
                source=resolved_route.source.value,
                provider=resolved_route.provider,
                provider_kind=resolved_route.provider_kind.value,
                reasoning_effort=resolved_route.reasoning_effort,
                capabilities=resolved_route.capabilities.model_dump(mode="json"),
            ),
            prompt=RuntimePromptAssemblySnapshot(
                snapshot_id=prompt_snapshot.snapshot_id,
                prompt_hash=prompt_snapshot.snapshot_key.digest(),
                version=prompt_snapshot.version,
                stable_section_names=tuple(section.name for section in prompt_snapshot.stable_sections),
                volatile_section_names=tuple(section.name for section in volatile_sections),
                stable_section_tokens=stable_section_tokens,
                volatile_section_tokens=volatile_section_tokens,
                stable_prompt_tokens=sum(stable_section_tokens.values()),
                volatile_prompt_tokens=sum(volatile_section_tokens.values()),
                cache=_prompt_cache_stats_payload(cache_after),
                cache_delta=_prompt_cache_delta_payload(prompt_cache_before, cache_after),
                project_context_fingerprint=project_context_snapshot.fingerprint if project_context_snapshot is not None else None,
                project_context_file_count=len(project_context_snapshot.files) if project_context_snapshot is not None else 0,
                project_context_truncated_file_count=sum(1 for item in project_context_snapshot.files if item.truncated) if project_context_snapshot is not None else 0,
                project_context_total_chars=project_context_snapshot.total_chars if project_context_snapshot is not None else 0,
                project_context_cache_status=project_context_snapshot.cache_status if project_context_snapshot is not None else None,
                project_context_discovery_scanned_path_count=project_context_snapshot.discovery_scanned_path_count if project_context_snapshot is not None else 0,
                project_context_discovery_max_scanned_paths=project_context_snapshot.discovery_max_scanned_paths if project_context_snapshot is not None else 0,
                project_context_discovery_scan_truncated=project_context_snapshot.discovery_scan_truncated if project_context_snapshot is not None else False,
                runtime_path_fingerprint=runtime_path_snapshot.fingerprint if runtime_path_snapshot is not None else None,
                runtime_path_root_count=runtime_path_snapshot.root_count if runtime_path_snapshot is not None else 0,
                runtime_path_host_bridge_count=runtime_path_snapshot.host_bridge_count if runtime_path_snapshot is not None else 0,
                runtime_path_cache_status=runtime_path_snapshot.cache_status if runtime_path_snapshot is not None else None,
                project_context_files=(
                    tuple(
                        {
                            "virtual_path": item.virtual_path,
                            "relative_path": item.relative_path,
                            "applies_to": item.applies_to,
                            "scope": item.scope,
                            "truncated": item.truncated,
                        }
                        for item in project_context_snapshot.files
                    )
                    if project_context_snapshot is not None
                    else ()
                ),
            ),
            capabilities=RuntimeCapabilityAssemblySnapshot(
                fingerprint=capability_bundle.fingerprint,
                catalog_fingerprint=capability_bundle.catalog_fingerprint,
                visible_tool_names=tuple(entry.name for entry in capability_bundle.visible_tools),
                deferred_tool_names=tuple(entry.name for entry in capability_bundle.deferred_tools),
                discovered_tool_names=tuple(entry.name for entry in capability_bundle.discovered_tools),
                enabled_skill_ids=capability_bundle.enabled_skill_ids,
                effective_mcp_servers=capability_bundle.effective_mcp_servers,
                effective_extension_sources=capability_bundle.effective_extension_sources,
                effective_plugin_ids=capability_bundle.effective_plugin_ids,
                active_promotions=active_promotions,
                assembly_diagnostics=capability_bundle.assembly_diagnostics.model_dump(mode="json"),
            ),
            middleware_names=tuple(getattr(middleware, "name", type(middleware).__name__) for middleware in middleware_chain),
            memory_injection_diagnostics={},
            enabled_feature_flags=_feature_flags(feature_set, enabled=True),
            disabled_feature_flags=_feature_flags(feature_set, enabled=False),
            service_flags=dict(sorted((service_flags or {}).items())),
        )

    def diff(self, other: "RuntimeAssemblySnapshot") -> RuntimeAssemblyDiff:
        changes: dict[str, dict[str, Any]] = {}
        added: dict[str, tuple[Any, ...]] = {}
        removed: dict[str, tuple[Any, ...]] = {}
        fields = (
            "execution_mode",
            "config_fingerprint",
            "model",
            "prompt",
            "capabilities",
            "middleware_names",
            "enabled_feature_flags",
            "disabled_feature_flags",
            "service_flags",
        )
        for field_name in fields:
            before = getattr(self, field_name)
            after = getattr(other, field_name)
            before_json = _jsonable(before)
            after_json = _jsonable(after)
            if field_name == "prompt":
                before_json = _without_prompt_cache_diagnostics(before_json)
                after_json = _without_prompt_cache_diagnostics(after_json)
            if field_name == "capabilities":
                before_json = _without_capability_timing_diagnostics(before_json)
                after_json = _without_capability_timing_diagnostics(after_json)
            if before_json != after_json:
                _collect_changes(
                    changes,
                    path=field_name,
                    before=before_json,
                    after=after_json,
                )
                _collect_sequence_delta(
                    added,
                    removed,
                    path=field_name,
                    before=before_json,
                    after=after_json,
                )
        return RuntimeAssemblyDiff(
            changes=changes,
            changed_paths=tuple(changes),
            added=added,
            removed=removed,
        )


def _section_token_counts(sections: list[Any]) -> dict[str, int]:
    token_budget = TokenBudgetService()
    counts: dict[str, int] = {}
    for section in sections:
        name = str(getattr(section, "name", "") or "").strip()
        render = getattr(section, "render", None)
        if not name or not callable(render):
            continue
        counts[name] = token_budget.count_text(render())
    return counts


def _prompt_token_ledger_fields() -> tuple[str, ...]:
    return (
        "stable_section_tokens",
        "volatile_section_tokens",
        "stable_prompt_tokens",
        "volatile_prompt_tokens",
    )


def _feature_flags(feature_set: RuntimeFeatureSet, *, enabled: bool) -> tuple[str, ...]:
    names: list[str] = []
    for field_name in type(feature_set).model_fields:
        if field_name in {"middleware", "extra_middlewares"}:
            continue
        value = getattr(feature_set, field_name)
        if isinstance(value, bool) and value is enabled:
            names.append(field_name)
    return tuple(sorted(names))


def _prompt_cache_stats_payload(stats: PromptSnapshotCacheStats) -> dict[str, int]:
    return {
        "max_entries": int(stats.max_entries),
        "size": int(stats.size),
        "hits": int(stats.hits),
        "misses": int(stats.misses),
        "writes": int(stats.writes),
        "evictions": int(stats.evictions),
        "bypasses": int(stats.bypasses),
    }


def _prompt_cache_delta_payload(
    before: PromptSnapshotCacheStats | None,
    after: PromptSnapshotCacheStats,
) -> dict[str, int]:
    if before is None:
        return {}
    return {
        "hits": max(int(after.hits) - int(before.hits), 0),
        "misses": max(int(after.misses) - int(before.misses), 0),
        "writes": max(int(after.writes) - int(before.writes), 0),
        "evictions": max(int(after.evictions) - int(before.evictions), 0),
        "bypasses": max(int(after.bypasses) - int(before.bypasses), 0),
        "size_before": int(before.size),
        "size_after": int(after.size),
        "net_size_change": int(after.size) - int(before.size),
        "max_entries": int(after.max_entries),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return list(value)
    return value


def _without_prompt_cache_diagnostics(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    normalized.pop("cache", None)
    normalized.pop("cache_delta", None)
    normalized.pop("project_context_cache_status", None)
    normalized.pop("runtime_path_cache_status", None)
    normalized.pop("project_context_file_count", None)
    normalized.pop("project_context_truncated_file_count", None)
    normalized.pop("project_context_total_chars", None)
    normalized.pop("project_context_discovery_scanned_path_count", None)
    normalized.pop("project_context_discovery_max_scanned_paths", None)
    normalized.pop("project_context_discovery_scan_truncated", None)
    normalized.pop("runtime_path_root_count", None)
    normalized.pop("runtime_path_host_bridge_count", None)
    for field_name in _prompt_token_ledger_fields():
        normalized.pop(field_name, None)
    return normalized


def _without_capability_timing_diagnostics(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    diagnostics = normalized.get("assembly_diagnostics")
    if isinstance(diagnostics, dict):
        cleaned = dict(diagnostics)
        cleaned.pop("assembly_stage_durations_ms", None)
        cleaned.pop("slowest_assembly_stage", None)
        cleaned.pop("slowest_assembly_stage_duration_ms", None)
        cleaned.pop("skills_discovery_cache_hit", None)
        cleaned.pop("skills_discovery_watch_enabled", None)
        cleaned.pop("skills_discovery_stage_durations_ms", None)
        cleaned.pop("slowest_skills_discovery_stage", None)
        cleaned.pop("slowest_skills_discovery_stage_duration_ms", None)
        normalized["assembly_diagnostics"] = cleaned
    return normalized


def _collect_changes(
    changes: dict[str, dict[str, Any]],
    *,
    path: str,
    before: Any,
    after: Any,
) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before) | set(after))
        for key in keys:
            _collect_changes(
                changes,
                path=f"{path}.{key}",
                before=before.get(key),
                after=after.get(key),
            )
        return
    changes[path] = {"before": before, "after": after}


def _collect_sequence_delta(
    added: dict[str, tuple[Any, ...]],
    removed: dict[str, tuple[Any, ...]],
    *,
    path: str,
    before: Any,
    after: Any,
) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before) | set(after))
        for key in keys:
            _collect_sequence_delta(
                added,
                removed,
                path=f"{path}.{key}",
                before=before.get(key),
                after=after.get(key),
            )
        return
    if not isinstance(before, list) or not isinstance(after, list):
        return
    before_set = set(before)
    after_set = set(after)
    added_items = tuple(item for item in after if item not in before_set)
    removed_items = tuple(item for item in before if item not in after_set)
    if added_items:
        added[path] = added_items
    if removed_items:
        removed[path] = removed_items
