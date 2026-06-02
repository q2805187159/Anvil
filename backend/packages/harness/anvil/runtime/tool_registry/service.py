from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from anvil.agents.features import RuntimeFeatureSet
from anvil.config import ConfigResolutionResult, ResolvedModelRoute
from anvil.extensions import ExtensionsService
from anvil.processes import ProcessService
from anvil.skills import SkillsService
from anvil.skills.service import normalize_skill_id
from anvil.subagents import SubagentService
from anvil.tools.assembly import assemble_runtime_tools

from .catalog import CapabilityCatalogService
from .contracts import CapabilityBundle, DeferredCapabilityPromotion, ToolRegistryEntry
from .delegation_factory import DelegationToolFactory
from .operator_factory import CORE_EXEMPT_TOOL_NAMES, OperatorToolFactory
from .registry import ToolRegistry

MENTION_PATTERN = re.compile(r"[@$]([A-Za-z0-9._-]+)")


@dataclass
class MentionResolution:
    promoted_tool_names: tuple[str, ...] = ()
    mentioned_skill_ids: tuple[str, ...] = ()


@dataclass
class CapabilityAssemblyResult:
    registry: ToolRegistry
    bundle: CapabilityBundle
    mention_resolution: MentionResolution
    promotion_state: set[str] = field(default_factory=set)


@dataclass
class _AssemblyTimer:
    started_at: float = field(default_factory=time.perf_counter)
    last_at: float = field(default_factory=time.perf_counter)
    durations_ms: dict[str, int] = field(default_factory=dict)

    def mark(self, stage: str) -> None:
        now = time.perf_counter()
        self.durations_ms[stage] = max(int((now - self.last_at) * 1000), 0)
        self.last_at = now

    def total_ms(self) -> int:
        return max(int((time.perf_counter() - self.started_at) * 1000), 0)


class CapabilityAssemblyService:
    def __init__(
        self,
        *,
        skills_service: SkillsService | None = None,
        extensions_service: ExtensionsService | None = None,
        subagent_service: SubagentService | None = None,
        process_service: ProcessService | None = None,
        scheduled_task_service=None,
        capability_catalog_service: CapabilityCatalogService | None = None,
    ) -> None:
        self.skills_service = skills_service or SkillsService()
        self.extensions_service = extensions_service or ExtensionsService()
        self.subagent_service = subagent_service or SubagentService()
        self.process_service = process_service
        self.scheduled_task_service = scheduled_task_service
        self.capability_catalog_service = capability_catalog_service or CapabilityCatalogService()
        self.operator_tool_factory = OperatorToolFactory(
            skills_service=self.skills_service,
            extensions_service=self.extensions_service,
            capability_catalog_service=self.capability_catalog_service,
        )
        self.delegation_tool_factory = DelegationToolFactory(subagent_service=self.subagent_service)

    def assemble(
        self,
        *,
        sandbox_handle=None,
        sandbox_provider=None,
        path_service=None,
        thread_id: str | None = None,
        memory_manager=None,
        config_result: ConfigResolutionResult,
        feature_set: RuntimeFeatureSet,
        execution_mode=None,
        request_context: str | None = None,
        promoted_capabilities: tuple[str, ...] = (),
        parent_visible_tool_names: tuple[str, ...] | None = None,
        promote_all_deferred: bool = False,
        tracing_service=None,
        run_trace_id: str | None = None,
        run_id: str | None = None,
        live_extensions: bool = True,
        resolved_route: ResolvedModelRoute | None = None,
    ) -> CapabilityAssemblyResult:
        timer = _AssemblyTimer()
        promotion_state = set(promoted_capabilities)
        if sandbox_handle is not None:
            class _HandleProvider:
                def acquire(self, *, thread_id, path_service):
                    return sandbox_handle

            sandbox_provider = sandbox_provider or _HandleProvider()
            path_service = path_service or sandbox_handle.path_service
            thread_id = thread_id or sandbox_handle.thread_id
        if sandbox_provider is None or path_service is None or thread_id is None:
            raise ValueError("capability assembly requires either sandbox_handle or sandbox_provider + path_service + thread_id")

        registry, _ = assemble_runtime_tools(
            sandbox_provider=sandbox_provider,
            path_service=path_service,
            thread_id=thread_id,
            memory_manager=memory_manager,
            process_service=self.process_service,
            scheduled_task_service=self.scheduled_task_service,
            uploads_config=config_result.effective_config.uploads,
            documents_config=config_result.effective_config.documents,
            code_semantics_config=config_result.effective_config.code_semantics,
            effective_config_fingerprint=config_result.fingerprint,
            vision_enabled=bool(getattr(getattr(resolved_route, "capabilities", None), "vision", False)),
        )
        timer.mark("runtime_tools")

        skills_result = self.skills_service.discover(
            config=config_result.effective_config,
            fingerprint=config_result.fingerprint,
        ) if feature_set.skills else None
        timer.mark("skills_discovery")

        extensions_result = self.extensions_service.discover(
            config=config_result.effective_config,
            fingerprint=config_result.fingerprint,
            live=live_extensions,
            materialization_mode="lazy_safe",
        ) if feature_set.extensions else None
        timer.mark("extensions_discovery")
        if extensions_result is not None:
            for materialization in extensions_result.materializations:
                for tool in materialization.tools:
                    registry.register(tool)
        timer.mark("extension_tool_registration")

        bundle_ref: dict[str, CapabilityBundle | None] = {"bundle": None}
        for tool_entry in self.operator_tool_factory.build_tools(
            registry=registry,
            bundle_ref=bundle_ref,
            promotion_state=promotion_state,
            config_result=config_result,
            skills_result=skills_result,
            thread_id=thread_id,
            path_service=path_service,
            resolved_route=resolved_route,
        ):
            registry.register(tool_entry)
        timer.mark("operator_tools")

        mention_resolution = self.resolve_mentions(
            request_context=request_context,
            registry=registry,
            enabled_skill_ids=skills_result.enabled_ids if skills_result is not None else (),
            explicit_promotions=promoted_capabilities,
        )
        if feature_set.capability_mentions:
            promotion_state.update(mention_resolution.promoted_tool_names)
        if promote_all_deferred:
            promotion_state.update(
                entry.name
                for entry in registry.entries()
                if entry.deferred and entry.is_available()
            )
        timer.mark("mention_resolution")

        delegated_parent_visible_tool_names = parent_visible_tool_names
        preliminary_bundle = self.rebuild_bundle(
            registry=registry,
            config_result=config_result,
            request_context=request_context,
            promoted_capabilities=tuple(sorted(promotion_state)),
            parent_visible_tool_names=parent_visible_tool_names,
            skills_result=skills_result,
            enabled_skill_ids=skills_result.enabled_ids if skills_result is not None else (),
            effective_mcp_servers=extensions_result.effective_mcp_servers if extensions_result is not None else (),
            effective_extension_sources=tuple(materialization.server_id for materialization in extensions_result.materializations) if extensions_result is not None else (),
            effective_plugin_ids=extensions_result.effective_plugin_ids if extensions_result is not None else (),
        )
        timer.mark("preliminary_bundle")
        if feature_set.subagents:
            if delegated_parent_visible_tool_names is None:
                delegated_parent_visible_tool_names = tuple(
                    sorted(
                        entry.name
                        for entry in preliminary_bundle.visible_tools
                        if entry.name not in {"delegated_task", "subagent", "ask_clarification", "delegate_batch", "delegate_status", "delegate_cancel"}
                    )
                )
            registry.register(
                self.subagent_service.build_tool(
                    thread_id=thread_id,
                    config_result=config_result,
                    feature_set=feature_set,
                    parent_visible_tool_names=delegated_parent_visible_tool_names or (),
                    execution_mode=execution_mode,
                    parent_run_id=run_id or run_trace_id,
                    trace_id=run_trace_id,
                )
            )
            registry.register(
                self.subagent_service.build_control_tool(
                    thread_id=thread_id,
                    parent_run_id=run_id or run_trace_id,
                )
            )
            for tool_entry in self.delegation_tool_factory.build_tools(
                config_result=config_result,
                thread_id=thread_id,
                parent_visible_tool_names=delegated_parent_visible_tool_names or (),
                execution_mode=execution_mode,
                feature_set=feature_set,
                parent_run_id=run_id or run_trace_id,
                trace_id=run_trace_id,
            ):
                registry.register(tool_entry)
        timer.mark("subagent_delegation_tools")

        bundle = self.rebuild_bundle(
            registry=registry,
            config_result=config_result,
            request_context=request_context,
            promoted_capabilities=tuple(sorted(promotion_state)),
            parent_visible_tool_names=parent_visible_tool_names,
            skills_result=skills_result,
            enabled_skill_ids=skills_result.enabled_ids if skills_result is not None else (),
            effective_mcp_servers=extensions_result.effective_mcp_servers if extensions_result is not None else (),
            effective_extension_sources=tuple(materialization.server_id for materialization in extensions_result.materializations) if extensions_result is not None else (),
            effective_plugin_ids=extensions_result.effective_plugin_ids if extensions_result is not None else (),
        )
        timer.mark("final_bundle")

        prompt_safe_summaries = list(bundle.prompt_safe_summaries)
        if skills_result is not None:
            visible_skill_ids = (
                mention_resolution.mentioned_skill_ids
                if mention_resolution.mentioned_skill_ids
                else skills_result.enabled_ids
            )
            if mention_resolution.mentioned_skill_ids:
                visible_skill_summaries = [
                    f"${summary.skill_id}: {summary.summary}"
                    for summary in skills_result.enabled_summaries
                    if summary.skill_id in mention_resolution.mentioned_skill_ids
                ]
                prompt_safe_summaries.extend(visible_skill_summaries)
                prompt_safe_summaries.extend(
                    self.skills_service.mentioned_skill_content_summaries(
                        config=config_result.effective_config,
                        fingerprint=config_result.fingerprint,
                        skill_ids=mention_resolution.mentioned_skill_ids,
                        discovery_result=skills_result,
                    )
                )
        timer.mark("prompt_safe_skill_summaries")

        stage_durations = dict(sorted(timer.durations_ms.items()))
        stage_durations["total"] = timer.total_ms()
        slowest_stage, slowest_stage_duration_ms = _slowest_stage(stage_durations)
        skills_discovery_diagnostics = (
            skills_result.discovery_diagnostics
            if skills_result is not None
            else None
        )

        bundle = bundle.model_copy(
            update={
                "enabled_skill_ids": tuple(visible_skill_ids) if skills_result is not None else (),
                "mentioned_skill_ids": mention_resolution.mentioned_skill_ids,
                "prompt_safe_summaries": tuple(prompt_safe_summaries),
                "capability_context": bundle.capability_context.model_copy(update={"active_promotions": tuple(sorted(promotion_state))}) if bundle.capability_context is not None else None,
                "assembly_diagnostics": bundle.assembly_diagnostics.model_copy(
                    update={
                        "assembly_stage_durations_ms": stage_durations,
                        "slowest_assembly_stage": slowest_stage,
                        "slowest_assembly_stage_duration_ms": slowest_stage_duration_ms,
                        "skills_discovery_cache_hit": (
                            skills_discovery_diagnostics.cache_hit
                            if skills_discovery_diagnostics is not None
                            else None
                        ),
                        "skills_discovery_watch_enabled": (
                            skills_discovery_diagnostics.watch_enabled
                            if skills_discovery_diagnostics is not None
                            else None
                        ),
                        "skills_discovery_root_count": (
                            skills_discovery_diagnostics.root_count
                            if skills_discovery_diagnostics is not None
                            else 0
                        ),
                        "skills_discovery_manifest_count": (
                            skills_discovery_diagnostics.manifest_count
                            if skills_discovery_diagnostics is not None
                            else 0
                        ),
                        "skills_discovery_enabled_count": (
                            skills_discovery_diagnostics.enabled_count
                            if skills_discovery_diagnostics is not None
                            else 0
                        ),
                        "skills_discovery_package_count": (
                            skills_discovery_diagnostics.package_count
                            if skills_discovery_diagnostics is not None
                            else 0
                        ),
                        "skills_discovery_stage_durations_ms": (
                            skills_discovery_diagnostics.stage_durations_ms
                            if skills_discovery_diagnostics is not None
                            else {}
                        ),
                        "slowest_skills_discovery_stage": (
                            skills_discovery_diagnostics.slowest_stage
                            if skills_discovery_diagnostics is not None
                            else None
                        ),
                        "slowest_skills_discovery_stage_duration_ms": (
                            skills_discovery_diagnostics.slowest_stage_duration_ms
                            if skills_discovery_diagnostics is not None
                            else None
                        ),
                    }
                ),
            }
        )
        bundle_ref["bundle"] = bundle
        return CapabilityAssemblyResult(
            registry=registry,
            bundle=bundle,
            mention_resolution=MentionResolution(
                promoted_tool_names=tuple(sorted(promotion_state)),
                mentioned_skill_ids=mention_resolution.mentioned_skill_ids,
            ),
            promotion_state=promotion_state,
        )

    def rebuild_bundle(
        self,
        *,
        registry: ToolRegistry,
        config_result: ConfigResolutionResult,
        request_context: str | None = None,
        promoted_capabilities: tuple[str, ...] = (),
        parent_visible_tool_names: tuple[str, ...] | None = None,
        skills_result=None,
        enabled_skill_ids: tuple[str, ...] = (),
        effective_mcp_servers: tuple[str, ...] = (),
        effective_extension_sources: tuple[str, ...] = (),
        effective_plugin_ids: tuple[str, ...] = (),
    ) -> CapabilityBundle:
        skill_allowed = self.skills_service.allowed_tool_names(
            config=config_result.effective_config,
            fingerprint=config_result.fingerprint,
            include_core=set(CORE_EXEMPT_TOOL_NAMES),
            discovery_result=skills_result,
        )
        parent_allowed = set(parent_visible_tool_names) if parent_visible_tool_names else None
        if skill_allowed is not None and parent_allowed is not None:
            effective_allowed = skill_allowed & parent_allowed
        else:
            effective_allowed = skill_allowed or parent_allowed
        if effective_allowed is not None:
            effective_allowed.update(CORE_EXEMPT_TOOL_NAMES)
        return registry.build_bundle(
            effective_config_fingerprint=config_result.fingerprint,
            request_context=request_context,
            promoted_names=DeferredCapabilityPromotion(promoted_names=tuple(sorted(set(promoted_capabilities)))),
            enabled_skill_ids=enabled_skill_ids,
            effective_mcp_servers=effective_mcp_servers,
            effective_extension_sources=effective_extension_sources,
            effective_plugin_ids=effective_plugin_ids,
            allowed_tool_names=effective_allowed,
            visible_schema_token_budget=(
                config_result.effective_config.tool_visibility_budget.visible_schema_token_budget
                if config_result.effective_config.tool_visibility_budget.enabled
                else None
            ),
            action_prefilter=(
                {
                    "enabled": config_result.effective_config.tool_visibility_budget.action_prefilter_enabled,
                    "min_tools": config_result.effective_config.tool_visibility_budget.action_prefilter_min_tools,
                    "max_visible": config_result.effective_config.tool_visibility_budget.action_prefilter_max_visible,
                    "min_score": config_result.effective_config.tool_visibility_budget.action_prefilter_min_score,
                }
                if config_result.effective_config.tool_visibility_budget.enabled
                else None
            ),
            always_visible_names=set(CORE_EXEMPT_TOOL_NAMES),
        )

    def resolve_mentions(
        self,
        *,
        request_context: str | None,
        registry: ToolRegistry,
        enabled_skill_ids: tuple[str, ...],
        explicit_promotions: tuple[str, ...] = (),
    ) -> MentionResolution:
        tokens = set(explicit_promotions)
        if request_context:
            tokens.update(MENTION_PATTERN.findall(request_context))
        tokens = {normalize_skill_id(token) for token in tokens}

        deferred_names = {entry.name for entry in registry.entries() if entry.deferred}
        promoted_tool_names = tuple(sorted(name for name in tokens if name in deferred_names))
        mentioned_skill_ids = tuple(sorted(skill_id for skill_id in enabled_skill_ids if skill_id in tokens))
        return MentionResolution(
            promoted_tool_names=promoted_tool_names,
            mentioned_skill_ids=mentioned_skill_ids,
        )


def _slowest_stage(stage_durations: dict[str, int]) -> tuple[str | None, int | None]:
    candidates = {
        stage: duration
        for stage, duration in stage_durations.items()
        if stage != "total" and isinstance(duration, int) and duration >= 0
    }
    if not candidates:
        return None, None
    stage, duration = max(candidates.items(), key=lambda item: (item[1], item[0]))
    return stage, duration
