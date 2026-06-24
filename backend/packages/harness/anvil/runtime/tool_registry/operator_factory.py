from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from langchain_core.tools import StructuredTool

from anvil.browser_tools import BrowserToolsService
from anvil.config import ConfigResolutionResult, ResolvedModelRoute
from anvil.google_workspace import GoogleWorkspaceService
from anvil.media_tools import MediaToolsService
from anvil.runtime.tool_registry.catalog import CapabilityCatalogService
from anvil.runtime.tool_registry.contracts import (
    CapabilityBundle,
    CapabilitySearchRequest,
    SchemaSanitizerDiagnostics,
    ToolRegistryEntry,
    ToolSourceKind,
    TypedApprovalPolicy,
    sanitize_tool_input_schema,
)
from anvil.runtime.tool_registry.toolsets import ToolsetCatalogService
from anvil.skills.service import normalize_skill_id
from anvil.web_tools import WebToolsService

CORE_EXEMPT_TOOL_NAMES = {
    "ask_clarification",
    "capability_search",
    "write_todos",
    "tool_catalog",
    "toolset_catalog",
    "toolset_view",
    "tool_view",
    "skills_list",
    "skill_view",
    "skill_content",
    "skill_files",
    "skill_read_file",
    "delegated_task",
    "subagent",
    "delegate_batch",
    "delegate_status",
    "delegate_cancel",
}


def _structured_tool_handler(*, name: str, description: str, func, input_schema: dict[str, object]) -> StructuredTool:
    clean_schema = sanitize_tool_input_schema(input_schema, diagnostics=SchemaSanitizerDiagnostics())
    return StructuredTool(name=name, description=description, func=func, args_schema=clean_schema)


def _with_structured_handler(entry: ToolRegistryEntry, *, description: str, func) -> ToolRegistryEntry:
    return entry.model_copy(
        update={
            "handler": _structured_tool_handler(
                name=entry.name,
                description=description,
                func=func,
                input_schema=entry.input_schema,
            )
        }
    )


def _is_minimax_image_model(model_config: Any) -> bool:
    provider = str(getattr(model_config, "provider", "") or "").lower()
    name = str(getattr(model_config, "name", "") or "").lower()
    display_name = str(getattr(model_config, "display_name", "") or "").lower()
    base_url = str(getattr(model_config, "base_url", "") or getattr(model_config, "api_base", "") or "").lower()
    return (
        "minimax" in provider
        or "minimax" in name
        or "minimax" in display_name
        or "api.minimax" in base_url
        or "api.minimaxi" in base_url
    )


class OperatorToolFactory:
    def __init__(
        self,
        *,
        skills_service,
        extensions_service,
        capability_catalog_service: CapabilityCatalogService | None = None,
    ) -> None:
        self.skills_service = skills_service
        self.extensions_service = extensions_service
        self.capability_catalog_service = capability_catalog_service or CapabilityCatalogService()
        self.toolset_catalog_service = ToolsetCatalogService()
        self.browser_tools_service = BrowserToolsService()
        self.google_workspace_service = GoogleWorkspaceService()
        self.media_tools_service = MediaToolsService()
        self.web_tools_service = WebToolsService()

    def build_tools(
        self,
        *,
        registry,
        bundle_ref: dict[str, CapabilityBundle | None],
        promotion_state: set[str],
        config_result: ConfigResolutionResult,
        skills_result,
        thread_id: str,
        path_service,
        resolved_route: ResolvedModelRoute | None = None,
    ) -> list[ToolRegistryEntry]:
        entries = [
            self.build_capability_search_tool(registry=registry, promotion_state=promotion_state),
            self.build_tool_catalog_tool(registry=registry, bundle_ref=bundle_ref),
            self.build_tool_view_tool(registry=registry, bundle_ref=bundle_ref),
            self.build_toolset_catalog_tool(registry=registry, bundle_ref=bundle_ref),
            self.build_toolset_view_tool(registry=registry, bundle_ref=bundle_ref),
            self.build_skills_list_tool(config_result=config_result, skills_result=skills_result),
            self.build_skill_view_tool(config_result=config_result),
            self.build_skill_content_tool(config_result=config_result),
            self.build_skill_files_tool(config_result=config_result),
            self.build_skill_read_file_tool(config_result=config_result),
            self.build_skill_manage_tool(config_result=config_result),
            self.build_mcp_manage_tool(config_result=config_result),
            self.build_mcp_list_resources_tool(config_result=config_result),
            self.build_mcp_read_resource_tool(config_result=config_result),
            self.build_mcp_list_prompts_tool(config_result=config_result),
            self.build_mcp_get_prompt_tool(config_result=config_result),
            self.build_web_search_tool(config_result=config_result),
            self.build_web_fetch_tool(config_result=config_result),
            self.build_web_extract_tool(config_result=config_result),
            self.build_web_crawl_tool(config_result=config_result),
            self.build_image_search_tool(config_result=config_result),
            self.build_browser_navigate_tool(config_result=config_result),
            self.build_browser_snapshot_tool(config_result=config_result),
            self.build_browser_click_tool(config_result=config_result),
            self.build_browser_type_tool(config_result=config_result),
            self.build_browser_scroll_tool(config_result=config_result),
            self.build_browser_back_tool(config_result=config_result),
            self.build_browser_press_tool(config_result=config_result),
            self.build_browser_console_tool(config_result=config_result),
            self.build_browser_get_images_tool(config_result=config_result),
            self.build_browser_screenshot_tool(config_result=config_result, path_service=path_service, thread_id=thread_id),
            self.build_browser_vision_tool(config_result=config_result, path_service=path_service, thread_id=thread_id),
            self.build_browser_cdp_tool(config_result=config_result),
            self.build_browser_dialog_tool(config_result=config_result),
            self.build_browser_close_tool(config_result=config_result),
            self.build_gmail_search_tool(config_result=config_result),
            self.build_gmail_read_tool(config_result=config_result),
            self.build_gmail_labels_tool(config_result=config_result),
            self.build_gmail_send_tool(config_result=config_result),
            self.build_gmail_create_draft_tool(config_result=config_result),
            self.build_calendar_list_events_tool(config_result=config_result),
            self.build_calendar_create_event_tool(config_result=config_result),
            self.build_calendar_update_event_tool(config_result=config_result),
            self.build_calendar_delete_event_tool(config_result=config_result),
            self.build_calendar_free_busy_tool(config_result=config_result),
            self.build_text_to_speech_tool(config_result=config_result, path_service=path_service, thread_id=thread_id),
            self.build_speech_to_text_tool(config_result=config_result, path_service=path_service, thread_id=thread_id),
            self.build_js_repl_tool(path_service=path_service, thread_id=thread_id),
        ]
        image_generation_model_name = self._image_generation_model_name(
            config_result=config_result,
            resolved_route=resolved_route,
        )
        route_capabilities = getattr(resolved_route, "capabilities", None)
        route_can_call_tools = bool(getattr(route_capabilities, "tool_calling", True))
        route_is_image_generation_model = bool(getattr(route_capabilities, "image_generation", False))
        if image_generation_model_name is not None and (route_can_call_tools or route_is_image_generation_model):
            entries.insert(
                -1,
                self.build_image_generate_tool(
                    config_result=config_result,
                    path_service=path_service,
                    thread_id=thread_id,
                    image_generation_model_name=image_generation_model_name,
                ),
            )
        return entries

    def _image_generation_model_name(
        self,
        *,
        config_result: ConfigResolutionResult,
        resolved_route: ResolvedModelRoute | None,
    ) -> str | None:
        models = config_result.effective_config.models
        route_model_name = getattr(resolved_route, "model_name", None)
        if route_model_name:
            route_model = models.get(route_model_name)
            if route_model is not None and route_model.capabilities.image_generation:
                return route_model.name
        configured_name = config_result.effective_config.subsystem_models.get("image_generation")
        if configured_name:
            configured_model = models.get(configured_name)
            if configured_model is not None and configured_model.capabilities.image_generation:
                return configured_model.name
            return None
        for model in models.values():
            if model.capabilities.image_generation:
                return model.name
        return None

    def build_capability_search_tool(self, *, registry, promotion_state: set[str]) -> ToolRegistryEntry:
        description = "Discover deferred capabilities by name or keyword and promote them for this runtime."

        def capability_search(query: str, max_results: int = 5, include_visible: bool = False) -> str:
            result = registry.search(
                CapabilitySearchRequest(
                    query=query,
                    max_results=max_results,
                    include_visible=include_visible,
                    promote=True,
                )
            )
            promotion_state.update(result.promotion.promoted_names)
            payload = {
                "query": query,
                "promoted_names": list(result.promotion.promoted_names),
                "total_matches": result.total_matches,
                "returned_count": len(result.matches),
                "match_traces": {
                    name: trace.model_dump(mode="json")
                    for name, trace in result.match_traces.items()
                },
                "matches": [
                    entry.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude={"handler", "availability_check"},
                    )
                    for entry in result.matches
                ],
            }
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                "include_visible": {"type": "boolean"},
            },
            "required": ["query"],
        }
        return ToolRegistryEntry(
            name="capability_search",
            display_name="Capability Search",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="capability_discovery",
            summary="Promote deferred capabilities by searching names, groups, summaries, and provenance before guessing tool names.",
            handler=_structured_tool_handler(name="capability_search", description=description, func=capability_search, input_schema=input_schema),
            input_schema=input_schema,
        )

    def build_tool_catalog_tool(self, *, registry, bundle_ref: dict[str, CapabilityBundle | None]) -> ToolRegistryEntry:
        description = "Browse the current capability catalog across builtins, skills, MCP, and plugins."

        def tool_catalog(
            query: str = "",
            source_kind: str | None = None,
            capability_group: str | None = None,
            names_only: bool = False,
            include_match_traces: bool = False,
        ) -> str:
            bundle = bundle_ref.get("bundle")
            if bundle is None:
                empty_payload: object
                if names_only or include_match_traces:
                    empty_payload = {"items": [], "total": 0}
                    if include_match_traces:
                        empty_payload["match_traces"] = {}
                else:
                    empty_payload = []
                return json.dumps(empty_payload)
            items = self.capability_catalog_service.list_entries(
                registry=registry,
                bundle=bundle,
                query=query,
                source_kind=source_kind,
                capability_group=capability_group,
            )
            if names_only:
                serialized_items = [
                    {
                        "capability_id": item.capability_id,
                        "name": item.name,
                        "display_name": item.display_name,
                        "summary": item.summary,
                        "source_kind": item.source_kind.value,
                        "source_id": item.source_id,
                        "capability_group": item.capability_group,
                        "visibility": item.visibility.value,
                        "deferred": item.deferred,
                    }
                    for item in items
                ]
                payload = {
                    "items": serialized_items,
                    "total": len(serialized_items),
                }
                if include_match_traces:
                    traces = self.capability_catalog_service.explain_matches(
                        entries=items,
                        query=query,
                    )
                    payload["match_traces"] = {name: trace.to_payload() for name, trace in traces.items()}
                return json.dumps(payload, ensure_ascii=False)

            serialized_items = [item.model_dump(mode="json") for item in items]
            if not include_match_traces:
                return json.dumps(serialized_items, ensure_ascii=False)
            traces = self.capability_catalog_service.explain_matches(
                entries=items,
                query=query,
            )
            payload = {
                "items": serialized_items,
                "match_traces": {name: trace.to_payload() for name, trace in traces.items()},
            }
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "source_kind": {"type": ["string", "null"]},
                "capability_group": {"type": ["string", "null"]},
                "names_only": {"type": "boolean", "default": False},
                "include_match_traces": {"type": "boolean", "default": False},
            },
        }
        return ToolRegistryEntry(
            name="tool_catalog",
            display_name="Tool Catalog",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="capability_discovery",
            summary="Browse callable and deferred capability summaries with visibility, risk, provenance, and source grouping.",
            handler=_structured_tool_handler(name="tool_catalog", description=description, func=tool_catalog, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=8000,
        )

    def build_tool_view_tool(self, *, registry, bundle_ref: dict[str, CapabilityBundle | None]) -> ToolRegistryEntry:
        description = "Inspect one capability entry from the runtime catalog by name or capability_id."

        def tool_view(name_or_capability_id: str) -> str:
            bundle = bundle_ref.get("bundle")
            if bundle is None:
                return json.dumps({"error": "capability bundle unavailable"}, ensure_ascii=False)
            item = self.capability_catalog_service.get_entry(
                registry=registry,
                bundle=bundle,
                name_or_capability_id=name_or_capability_id,
            )
            if item is None:
                return json.dumps({"error": f"unknown capability '{name_or_capability_id}'"}, ensure_ascii=False)
            return json.dumps(item.model_dump(mode="json"), ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {"name_or_capability_id": {"type": "string"}},
            "required": ["name_or_capability_id"],
        }
        return ToolRegistryEntry(
            name="tool_view",
            display_name="Tool View",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="capability_discovery",
            summary="Inspect one capability's full metadata, including approval, dependencies, prompts, and health.",
            handler=_structured_tool_handler(name="tool_view", description=description, func=tool_view, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=6000,
        )

    def build_toolset_catalog_tool(self, *, registry, bundle_ref: dict[str, CapabilityBundle | None]) -> ToolRegistryEntry:
        description = "List runtime toolsets and the currently materialized/visible tools in each group."

        def toolset_catalog(query: str = "") -> str:
            return json.dumps(
                self.toolset_catalog_service.list_toolsets(
                    registry=registry,
                    bundle=bundle_ref.get("bundle"),
                    query=query,
                ),
                ensure_ascii=False,
            )

        input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        return ToolRegistryEntry(
            name="toolset_catalog",
            display_name="Toolset Catalog",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="capability_discovery",
            summary="Browse logical toolsets such as file, terminal, web, skills, memory, automation, MCP, delegation, coding, research, and safe.",
            handler=_structured_tool_handler(name="toolset_catalog", description=description, func=toolset_catalog, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=8000,
        )

    def build_toolset_view_tool(self, *, registry, bundle_ref: dict[str, CapabilityBundle | None]) -> ToolRegistryEntry:
        description = "Inspect one logical toolset and see included, visible, deferred, and missing tools."

        def toolset_view(name: str) -> str:
            return json.dumps(
                self.toolset_catalog_service.describe_toolset(
                    name=name,
                    registry=registry,
                    bundle=bundle_ref.get("bundle"),
                ),
                ensure_ascii=False,
            )

        input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        return ToolRegistryEntry(
            name="toolset_view",
            display_name="Toolset View",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="capability_discovery",
            summary="Inspect one logical toolset with resolved materialized, visible, deferred, and missing tools.",
            handler=_structured_tool_handler(name="toolset_view", description=description, func=toolset_view, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=6000,
        )

    def build_skills_list_tool(self, *, config_result: ConfigResolutionResult, skills_result) -> ToolRegistryEntry:
        description = "List discovered skills as compact Level 1 metadata. Use skill_view or skill_read_file for details."

        def skills_list(query: str = "", enabled_only: bool = False, limit: int = 200, include_descriptions: bool = False) -> str:
            result = skills_result or self.skills_service.discover(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
            )
            normalized_query = query.strip().lower()
            if enabled_only:
                source = result.enabled_summaries
            else:
                source = result.all_summaries
            items = []
            for summary in source:
                haystack = " ".join(
                    [
                        summary.skill_id.lower(),
                        summary.title.lower(),
                        summary.summary.lower(),
                        (summary.description or "").lower(),
                        (summary.domain or "").lower(),
                        (summary.task_type or "").lower(),
                        (summary.risk_level or "").lower(),
                        " ".join(tag.lower() for tag in summary.tags),
                        " ".join(requirement.lower() for requirement in summary.input_requirements),
                    ]
                )
                if normalized_query and normalized_query not in haystack:
                    continue
                items.append(_model_safe_skill_level1_payload(summary.model_dump(mode="json"), include_description=include_descriptions))
            safe_limit = min(max(int(limit or 200), 1), 500)
            returned_items = items[:safe_limit]
            return json.dumps(
                {
                    "total": len(items),
                    "returned": len(returned_items),
                    "truncated": len(items) > len(returned_items),
                    "items": returned_items,
                    "progressive_disclosure": {
                        "level_1": "skills_list metadata",
                        "level_2": "skill_view or skill_read_file reads SKILL.md only when a skill is relevant",
                        "level_3": "skill_files and skill_read_file read referenced resources on demand",
                    },
                    "read_hint": "Use skill_view for one manifest or skill_read_file(skill_id, relative_path='SKILL.md') for triggered instructions.",
                },
                ensure_ascii=False,
            )

        entry = ToolRegistryEntry(
            name="skills_list",
            display_name="Skills List",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="skill_governance",
            summary="List governed skills with enabled state, trust, summary text, and manifest metadata.",
            handler=skills_list,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "enabled_only": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
                    "include_descriptions": {"type": "boolean", "default": False},
                },
            },
            output_budget=12000,
        )
        return _with_structured_handler(entry, description=description, func=skills_list)

    def build_skill_view_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Inspect a single skill manifest by skill_id."

        def skill_view(skill_id: str) -> str:
            skill_id = normalize_skill_id(skill_id)
            manifest = self.skills_service.get_skill(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
                skill_id=skill_id,
            )
            if manifest is None:
                return json.dumps({"error": f"unknown skill '{skill_id}'"}, ensure_ascii=False)
            return json.dumps(_model_safe_skill_payload(manifest.model_dump(mode="json")), ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="skill_view",
            display_name="Skill View",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="skill_governance",
            summary="Inspect one skill's manifest, validation status, dependencies, and governance metadata.",
            handler=skill_view,
            input_schema={
                "type": "object",
                "properties": {"skill_id": {"type": "string"}},
                "required": ["skill_id"],
            },
        )
        return _with_structured_handler(entry, description=description, func=skill_view)

    def build_skill_content_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Read the main SKILL.md body for one skill."

        def skill_content(skill_id: str) -> str:
            skill_id = normalize_skill_id(skill_id)
            payload = self.skills_service.get_skill_content(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
                skill_id=skill_id,
            )
            return json.dumps(_model_safe_skill_content_payload(payload.model_dump(mode="json")), ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="skill_content",
            display_name="Skill Content",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="skill_governance",
            summary="Read the primary SKILL.md body for one governed skill before using or editing it.",
            handler=skill_content,
            input_schema={"type": "object", "properties": {"skill_id": {"type": "string"}}, "required": ["skill_id"]},
            output_budget=12000,
        )
        return _with_structured_handler(entry, description=description, func=skill_content)

    def build_skill_files_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "List supporting files for one skill."

        def skill_files(skill_id: str) -> str:
            skill_id = normalize_skill_id(skill_id)
            payload = self.skills_service.list_skill_files(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
                skill_id=skill_id,
            )
            return json.dumps(_model_safe_skill_file_index_payload(payload.model_dump(mode="json")), ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="skill_files",
            display_name="Skill Files",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="skill_governance",
            summary="List a skill's SKILL.md plus assets, templates, scripts, and references without leaving the governed root.",
            handler=skill_files,
            input_schema={"type": "object", "properties": {"skill_id": {"type": "string"}}, "required": ["skill_id"]},
        )
        return _with_structured_handler(entry, description=description, func=skill_files)

    def build_skill_read_file_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Read one supporting file from a skill by relative path."

        def skill_read_file(skill_id: str, relative_path: str, max_bytes: int = 64000) -> str:
            skill_id = normalize_skill_id(skill_id)
            payload = self.skills_service.read_skill_file(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
                skill_id=skill_id,
                relative_path=relative_path,
                max_bytes=max_bytes,
            )
            return json.dumps(_model_safe_skill_file_read_payload(payload.model_dump(mode="json")), ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="skill_read_file",
            display_name="Skill Read File",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="skill_governance",
            summary="Read one governed skill support file by relative path after inspecting the skill file index.",
            handler=skill_read_file,
            input_schema={
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string"},
                    "relative_path": {"type": "string"},
                    "max_bytes": {"type": "integer", "minimum": 1, "maximum": 262144},
                },
                "required": ["skill_id", "relative_path"],
            },
            output_budget=16000,
        )
        return _with_structured_handler(entry, description=description, func=skill_read_file)

    def build_skill_manage_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = (
            "Self-improve skills through the curator lane. Supports report, create, update, "
            "archive, restore, backup, rollback, curate, maintenance, quality_plan, review_apply, merge_plan, merge_apply, feedback, learn_procedure, procedures, promote_procedure, reject_procedure, restore_procedure, pin, and unpin for agent-created workspace skills. "
            "It does not enable, disable, uninstall, or modify repo/user skills."
        )

        def skill_manage(
            action: str,
            skill_id: str | None = None,
            title: str | None = None,
            summary: str | None = None,
            body: str | None = None,
            rationale: str | None = None,
            tags: list[str] | None = None,
            allowed_tools: list[str] | None = None,
            file_path: str | None = None,
            content: str | None = None,
            old_text: str | None = None,
            new_text: str | None = None,
            absorbed_into: str | None = None,
            revision: str | None = None,
            outcome: str | None = None,
            feedback_source: str | None = None,
            confidence: float | None = None,
            trigger: str | None = None,
            steps: list[str] | None = None,
            expected_outcome: str | None = None,
            evidence_refs: list[str] | None = None,
            source_ref: str | None = None,
            procedure_id: str | None = None,
            dry_run: bool = False,
            force: bool = False,
        ) -> str:
            payload = self.skills_service.manage_curator(
                config=config_result.effective_config,
                action=action,
                skill_id=skill_id,
                title=title,
                summary=summary,
                body=body,
                rationale=rationale,
                tags=tags,
                allowed_tools=allowed_tools,
                file_path=file_path,
                content=content,
                old_text=old_text,
                new_text=new_text,
                absorbed_into=absorbed_into,
                revision=revision,
                outcome=outcome,
                feedback_source=feedback_source,
                confidence=confidence,
                trigger=trigger,
                steps=steps,
                expected_outcome=expected_outcome,
                evidence_refs=evidence_refs,
                source_ref=source_ref,
                procedure_id=procedure_id,
                dry_run=dry_run,
                force=force,
            )
            return json.dumps(payload, ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="skill_manage",
            display_name="Skill Curator",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="skill_governance",
            summary=(
                "Create, update, patch, write support files, archive, restore, backup, rollback, curate, run bounded maintenance, plan/apply bounded quality reviews, plan/apply duplicate merges, record success/failure feedback, learn/promote/reject/restore procedural candidates, pin, or report on agent-created workspace skills "
                "through the curator lane. Direct enable/disable/uninstall remains Ops/API-only."
            ),
            handler=skill_manage,
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "report",
                            "curate",
                            "maintenance",
                            "create",
                            "update",
                            "patch",
                            "write_file",
                            "remove_file",
                            "archive",
                            "restore",
                            "backup",
                            "rollback",
                            "quality_plan",
                            "review_apply",
                            "merge_plan",
                            "merge_apply",
                            "feedback",
                            "learn_procedure",
                            "procedures",
                            "promote_procedure",
                            "reject_procedure",
                            "restore_procedure",
                            "pin",
                            "unpin",
                        ],
                    },
                    "skill_id": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "summary": {"type": ["string", "null"]},
                    "body": {"type": ["string", "null"]},
                    "rationale": {"type": ["string", "null"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "allowed_tools": {"type": "array", "items": {"type": "string"}},
                    "file_path": {"type": ["string", "null"]},
                    "content": {"type": ["string", "null"]},
                    "old_text": {"type": ["string", "null"]},
                    "new_text": {"type": ["string", "null"]},
                    "absorbed_into": {"type": ["string", "null"]},
                    "revision": {"type": ["string", "null"]},
                    "outcome": {"type": ["string", "null"], "enum": ["success", "failure", "neutral", None]},
                    "feedback_source": {"type": ["string", "null"]},
                    "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                    "trigger": {"type": ["string", "null"]},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "expected_outcome": {"type": ["string", "null"]},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "source_ref": {"type": ["string", "null"]},
                    "procedure_id": {"type": ["string", "null"]},
                    "dry_run": {"type": "boolean"},
                    "force": {"type": "boolean"},
                },
                "required": ["action"],
            },
            risk_category="skill_curator",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="skill_curator"),
            output_budget=12000,
        )
        return _with_structured_handler(entry, description=description, func=skill_manage)

    def build_mcp_manage_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Manage MCP servers: list, get, refresh, reconnect, tools, resources, prompts."

        def mcp_manage(action: str, server_id: str | None = None) -> str:
            normalized = action.strip().lower()
            if normalized == "list":
                result = self.extensions_service.discover(
                    config=config_result.effective_config,
                    fingerprint=config_result.fingerprint,
                )
                items = [
                    item.model_dump(mode="json")
                    for item in result.materializations
                    if item.source_kind == "mcp"
                ]
                return json.dumps(items, ensure_ascii=False)
            if not server_id:
                return json.dumps({"error": "server_id is required"}, ensure_ascii=False)
            if normalized == "get":
                item = self.extensions_service.get_server(
                    config=config_result.effective_config,
                    fingerprint=config_result.fingerprint,
                    server_id=server_id,
                )
                return json.dumps(item.model_dump(mode="json") if item is not None else {"error": "not found"}, ensure_ascii=False)
            if normalized == "refresh":
                item = self.extensions_service.refresh_server(
                    config=config_result.effective_config,
                    fingerprint=config_result.fingerprint,
                    server_id=server_id,
                )
                return json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
            if normalized == "reconnect":
                item = self.extensions_service.reconnect_server(
                    config=config_result.effective_config,
                    fingerprint=config_result.fingerprint,
                    server_id=server_id,
                )
                return json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
            if normalized == "tools":
                item = self.extensions_service.get_server(
                    config=config_result.effective_config,
                    fingerprint=config_result.fingerprint,
                    server_id=server_id,
                )
                return json.dumps(
                    [tool.model_dump(mode="json", exclude={"handler", "availability_check"}) for tool in (item.tools if item is not None else ())],
                    ensure_ascii=False,
                )
            if normalized == "resources":
                return json.dumps(
                    [item.model_dump(mode="json") for item in self.extensions_service.list_resources(config=config_result.effective_config, fingerprint=config_result.fingerprint, server_id=server_id)],
                    ensure_ascii=False,
                )
            if normalized == "prompts":
                return json.dumps(
                    [item.model_dump(mode="json") for item in self.extensions_service.list_prompts(config=config_result.effective_config, fingerprint=config_result.fingerprint, server_id=server_id)],
                    ensure_ascii=False,
                )
            return json.dumps({"error": f"unsupported action: {action}"}, ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="mcp_manage",
            display_name="MCP Manage",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="external_capability_governance",
            summary="Inspect or control configured MCP servers, including runtime state, reconnects, tools, resources, and prompts.",
            handler=mcp_manage,
            input_schema={"type": "object", "properties": {"action": {"type": "string"}, "server_id": {"type": ["string", "null"]}}, "required": ["action"]},
        )
        return _with_structured_handler(entry, description=description, func=mcp_manage)

    def build_mcp_list_resources_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "List resource capabilities synthesized from configured MCP servers."

        def mcp_list_resources(server_id: str | None = None) -> str:
            items = self.extensions_service.list_resources(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
                server_id=server_id,
            )
            return json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="mcp_list_resources",
            display_name="MCP List Resources",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="external_capability_governance",
            summary="List MCP resource surfaces for one server without invoking arbitrary remote tools.",
            handler=mcp_list_resources,
            input_schema={"type": "object", "properties": {"server_id": {"type": ["string", "null"]}}},
        )
        return _with_structured_handler(entry, description=description, func=mcp_list_resources)

    def build_mcp_read_resource_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Read one synthesized MCP resource by server_id and resource_id."

        def mcp_read_resource(server_id: str, resource_id: str) -> str:
            payload = self.extensions_service.read_resource(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
                server_id=server_id,
                resource_id=resource_id,
            )
            return json.dumps(payload, ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="mcp_read_resource",
            display_name="MCP Read Resource",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="external_capability_governance",
            summary="Read one MCP resource surface by server and resource identifier.",
            handler=mcp_read_resource,
            input_schema={"type": "object", "properties": {"server_id": {"type": "string"}, "resource_id": {"type": "string"}}, "required": ["server_id", "resource_id"]},
        )
        return _with_structured_handler(entry, description=description, func=mcp_read_resource)

    def build_mcp_list_prompts_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "List prompt templates synthesized from configured MCP servers."

        def mcp_list_prompts(server_id: str | None = None) -> str:
            items = self.extensions_service.list_prompts(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
                server_id=server_id,
            )
            return json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="mcp_list_prompts",
            display_name="MCP List Prompts",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="external_capability_governance",
            summary="List MCP prompt surfaces and their declared arguments before rendering them.",
            handler=mcp_list_prompts,
            input_schema={"type": "object", "properties": {"server_id": {"type": ["string", "null"]}}},
        )
        return _with_structured_handler(entry, description=description, func=mcp_list_prompts)

    def build_mcp_get_prompt_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Render one MCP prompt surface using optional arguments."

        def mcp_get_prompt(server_id: str, prompt_id: str, arguments: dict[str, object] | None = None) -> str:
            payload = self.extensions_service.get_prompt(
                config=config_result.effective_config,
                fingerprint=config_result.fingerprint,
                server_id=server_id,
                prompt_id=prompt_id,
                arguments=arguments,
            )
            return json.dumps(payload, ensure_ascii=False)

        entry = ToolRegistryEntry(
            name="mcp_get_prompt",
            display_name="MCP Get Prompt",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="external_capability_governance",
            summary="Render one MCP prompt with explicit arguments and inspect the final prompt payload.",
            handler=mcp_get_prompt,
            input_schema={"type": "object", "properties": {"server_id": {"type": "string"}, "prompt_id": {"type": "string"}, "arguments": {"type": ["object", "null"]}}, "required": ["server_id", "prompt_id"]},
        )
        return _with_structured_handler(entry, description=description, func=mcp_get_prompt)

    def build_web_search_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Search the web and return compact result snippets using the configured provider adapter."

        def web_search(query: str, max_results: int = 5) -> str:
            return json.dumps(
                self.web_tools_service.search(
                    config_result=config_result,
                    query=query,
                    max_results=max_results,
                ),
                ensure_ascii=False,
            )

        input_schema = {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": ["query"]}
        return ToolRegistryEntry(
            name="web_search",
            display_name="Web Search",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="research",
            summary="Search the public web when the runtime exposes web access and you need current external information.",
            handler=_structured_tool_handler(name="web_search", description=description, func=web_search, input_schema=input_schema),
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=8000,
        )

    def build_web_fetch_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Fetch a web page and return normalized content plus metadata."

        def web_fetch(url: str, timeout_seconds: int = 20, max_chars: int = 8000) -> str:
            return json.dumps(
                self.web_tools_service.fetch(
                    config_result=config_result,
                    url=url,
                    timeout_seconds=timeout_seconds,
                    max_chars=max_chars,
                ),
                ensure_ascii=False,
            )

        input_schema = {"type": "object", "properties": {"url": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60}, "max_chars": {"type": "integer", "minimum": 100, "maximum": 20000}}, "required": ["url"]}
        return ToolRegistryEntry(
            name="web_fetch",
            display_name="Web Fetch",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="research",
            summary="Fetch one web document and normalize the content for follow-up reading or extraction.",
            handler=_structured_tool_handler(name="web_fetch", description=description, func=web_fetch, input_schema=input_schema),
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=12000,
        )

    def build_web_extract_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Extract readable content from one or more web pages with optional links and images."

        def web_extract(
            url: str | None = None,
            urls: list[str] | None = None,
            format: str = "markdown",
            timeout_seconds: int = 20,
            max_chars: int = 12000,
            include_links: bool = False,
            include_images: bool = False,
        ) -> str:
            return json.dumps(
                self.web_tools_service.extract(
                    config_result=config_result,
                    url=url,
                    urls=urls,
                    format=format,
                    timeout_seconds=timeout_seconds,
                    max_chars=max_chars,
                    include_links=include_links,
                    include_images=include_images,
                ),
                ensure_ascii=False,
            )

        input_schema = {
            "type": "object",
            "properties": {
                "url": {"type": ["string", "null"]},
                "urls": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 20},
                "format": {"type": "string", "enum": ["markdown", "text", "json"]},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
                "max_chars": {"type": "integer", "minimum": 100, "maximum": 60000},
                "include_links": {"type": "boolean"},
                "include_images": {"type": "boolean"},
            },
            "anyOf": [{"required": ["url"]}, {"required": ["urls"]}],
        }
        return ToolRegistryEntry(
            name="web_extract",
            display_name="Web Extract",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="research",
            summary="Extract one or more web pages into readable text or markdown, with optional link and image metadata.",
            handler=_structured_tool_handler(name="web_extract", description=description, func=web_extract, input_schema=input_schema),
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=16000,
        )

    def build_web_crawl_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Crawl a small bounded set of same-site web pages with optional task instructions."

        def web_crawl(
            url: str,
            instructions: str = "",
            max_pages: int = 5,
            max_chars: int = 20000,
            timeout_seconds: int = 20,
        ) -> str:
            return json.dumps(
                self.web_tools_service.crawl(
                    config_result=config_result,
                    url=url,
                    instructions=instructions,
                    max_pages=max_pages,
                    max_chars=max_chars,
                    timeout_seconds=timeout_seconds,
                ),
                ensure_ascii=False,
            )

        input_schema = {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "instructions": {"type": "string"},
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 20},
                "max_chars": {"type": "integer", "minimum": 500, "maximum": 100000},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            "required": ["url"],
        }
        return ToolRegistryEntry(
            name="web_crawl",
            display_name="Web Crawl",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="research",
            summary="Crawl a bounded set of pages from a site using Tavily, Firecrawl, or same-origin direct extraction.",
            handler=_structured_tool_handler(name="web_crawl", description=description, func=web_crawl, input_schema=input_schema),
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=20000,
        )

    def build_image_search_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Search images through a provider adapter and return URLs plus metadata."

        def image_search(query: str, max_results: int = 5) -> str:
            return json.dumps(
                self.web_tools_service.image_search(
                    config_result=config_result,
                    query=query,
                    max_results=max_results,
                ),
                ensure_ascii=False,
            )

        input_schema = {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": ["query"]}
        return ToolRegistryEntry(
            name="image_search",
            display_name="Image Search",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="research",
            summary="Search public image sources when the task needs visual references instead of text pages.",
            handler=_structured_tool_handler(name="image_search", description=description, func=image_search, input_schema=input_schema),
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=8000,
        )

    def _browser_entry(
        self,
        *,
        name: str,
        display_name: str,
        summary: str,
        handler,
        input_schema: dict[str, object],
        output_budget: int = 8000,
    ) -> ToolRegistryEntry:
        return ToolRegistryEntry(
            name=name,
            display_name=display_name,
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="browser",
            summary=summary,
            handler=handler,
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=output_budget,
        )

    def build_browser_navigate_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Navigate an isolated browser session to a URL and return a compact page snapshot."

        def browser_navigate(url: str, session_id: str = "default") -> str:
            payload = self.browser_tools_service.navigate(config_result=config_result, session_id=session_id, url=url)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"url": {"type": "string"}, "session_id": {"type": "string"}}, "required": ["url"]}
        return self._browser_entry(
            name="browser_navigate",
            display_name="Browser Navigate",
            summary="Open a URL in a governed browser session and return title, URL, and compact interactive snapshot.",
            handler=_structured_tool_handler(name="browser_navigate", description=description, func=browser_navigate, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=14000,
        )

    def build_browser_snapshot_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Return the current browser page snapshot with refs for clickable or typeable elements."

        def browser_snapshot(session_id: str = "default", full: bool = False) -> str:
            payload = self.browser_tools_service.snapshot(config_result=config_result, session_id=session_id, full=full)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"session_id": {"type": "string"}, "full": {"type": "boolean"}}}
        return self._browser_entry(
            name="browser_snapshot",
            display_name="Browser Snapshot",
            summary="Inspect the current browser page as text, links, and stable element refs before interacting.",
            handler=_structured_tool_handler(name="browser_snapshot", description=description, func=browser_snapshot, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=16000,
        )

    def build_browser_click_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Click a browser element by snapshot ref such as @e1 or by css= selector."

        def browser_click(ref: str, session_id: str = "default") -> str:
            payload = self.browser_tools_service.click(config_result=config_result, session_id=session_id, ref=ref)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"ref": {"type": "string"}, "session_id": {"type": "string"}}, "required": ["ref"]}
        return self._browser_entry(
            name="browser_click",
            display_name="Browser Click",
            summary="Click a referenced browser element from browser_snapshot or an explicit css= selector.",
            handler=_structured_tool_handler(name="browser_click", description=description, func=browser_click, input_schema=input_schema),
            input_schema=input_schema,
        )

    def build_browser_type_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Fill or type text into a browser element by snapshot ref such as @e1 or by css= selector."

        def browser_type(ref: str, text: str, session_id: str = "default") -> str:
            payload = self.browser_tools_service.type_text(config_result=config_result, session_id=session_id, ref=ref, text=text)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"ref": {"type": "string"}, "text": {"type": "string"}, "session_id": {"type": "string"}}, "required": ["ref", "text"]}
        return self._browser_entry(
            name="browser_type",
            display_name="Browser Type",
            summary="Type text into a referenced input, textarea, select, contenteditable node, or css= selector.",
            handler=_structured_tool_handler(name="browser_type", description=description, func=browser_type, input_schema=input_schema),
            input_schema=input_schema,
        )

    def build_browser_scroll_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Scroll the current browser page up or down."

        def browser_scroll(direction: str = "down", session_id: str = "default") -> str:
            payload = self.browser_tools_service.scroll(config_result=config_result, session_id=session_id, direction=direction)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"direction": {"type": "string", "enum": ["up", "down"]}, "session_id": {"type": "string"}}}
        return self._browser_entry(
            name="browser_scroll",
            display_name="Browser Scroll",
            summary="Scroll the current browser page up or down before requesting another snapshot.",
            handler=_structured_tool_handler(name="browser_scroll", description=description, func=browser_scroll, input_schema=input_schema),
            input_schema=input_schema,
        )

    def build_browser_back_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Navigate the browser session back in history."

        def browser_back(session_id: str = "default") -> str:
            payload = self.browser_tools_service.back(config_result=config_result, session_id=session_id)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"session_id": {"type": "string"}}}
        return self._browser_entry(
            name="browser_back",
            display_name="Browser Back",
            summary="Go back in the browser session history.",
            handler=_structured_tool_handler(name="browser_back", description=description, func=browser_back, input_schema=input_schema),
            input_schema=input_schema,
        )

    def build_browser_press_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Send a keyboard key to the current browser page, such as Enter, Escape, Tab, or ArrowDown."

        def browser_press(key: str, session_id: str = "default") -> str:
            payload = self.browser_tools_service.press(config_result=config_result, session_id=session_id, key=key)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"key": {"type": "string"}, "session_id": {"type": "string"}}, "required": ["key"]}
        return self._browser_entry(
            name="browser_press",
            display_name="Browser Press",
            summary="Send a keyboard key to the current browser page.",
            handler=_structured_tool_handler(name="browser_press", description=description, func=browser_press, input_schema=input_schema),
            input_schema=input_schema,
        )

    def build_browser_console_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Read browser console messages or evaluate a short JavaScript expression on the page."

        def browser_console(session_id: str = "default", clear: bool = False, expression: str | None = None) -> str:
            payload = self.browser_tools_service.console(config_result=config_result, session_id=session_id, clear=clear, expression=expression)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"session_id": {"type": "string"}, "clear": {"type": "boolean"}, "expression": {"type": ["string", "null"]}}}
        return self._browser_entry(
            name="browser_console",
            display_name="Browser Console",
            summary="Inspect console messages or run bounded page JavaScript through the configured browser backend.",
            handler=_structured_tool_handler(name="browser_console", description=description, func=browser_console, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=12000,
        )

    def build_browser_get_images_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "List non-data images visible in the current browser page."

        def browser_get_images(session_id: str = "default") -> str:
            payload = self.browser_tools_service.get_images(config_result=config_result, session_id=session_id)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"session_id": {"type": "string"}}}
        return self._browser_entry(
            name="browser_get_images",
            display_name="Browser Get Images",
            summary="Return image URLs, alt text, and dimensions from the current browser page.",
            handler=_structured_tool_handler(name="browser_get_images", description=description, func=browser_get_images, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=10000,
        )

    def build_browser_screenshot_tool(self, *, config_result: ConfigResolutionResult, path_service, thread_id: str) -> ToolRegistryEntry:
        description = "Capture a browser screenshot artifact under /mnt/user-data/outputs/browser."

        def browser_screenshot(output_path: str | None = None, session_id: str = "default", full_page: bool = True, format: str = "png") -> str:
            output_virtual_path = output_path or self.browser_tools_service.default_screenshot_virtual_path(image_format=format)
            if not output_virtual_path.startswith("/mnt/user-data/outputs/"):
                return json.dumps({"success": False, "error": "output_path must be under /mnt/user-data/outputs", "output_path": output_virtual_path}, ensure_ascii=False)
            resolved_output_path = path_service.resolve_virtual_path(thread_id, output_virtual_path)
            payload = self.browser_tools_service.screenshot(
                config_result=config_result,
                session_id=session_id,
                output_path=resolved_output_path,
                output_virtual_path=output_virtual_path,
                full_page=full_page,
                format=format,
            )
            if payload.get("success"):
                relative_path = output_virtual_path.removeprefix("/mnt/user-data/outputs/")
                payload["artifact_url"] = path_service.to_artifact_descriptor(thread_id, "outputs", relative_path).artifact_url
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {
                "output_path": {"type": ["string", "null"]},
                "session_id": {"type": "string"},
                "full_page": {"type": "boolean"},
                "format": {"type": "string", "enum": ["png", "jpeg", "jpg"]},
            },
        }
        return self._browser_entry(
            name="browser_screenshot",
            display_name="Browser Screenshot",
            summary="Capture a PNG/JPEG browser screenshot and return the governed artifact URL.",
            handler=_structured_tool_handler(name="browser_screenshot", description=description, func=browser_screenshot, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=8000,
        )

    def build_browser_vision_tool(self, *, config_result: ConfigResolutionResult, path_service, thread_id: str) -> ToolRegistryEntry:
        description = "Capture a browser screenshot plus compact snapshot for visual reasoning by the model."

        def browser_vision(question: str | None = None, output_path: str | None = None, session_id: str = "default", full_page: bool = True) -> str:
            output_virtual_path = output_path or self.browser_tools_service.default_screenshot_virtual_path(image_format="png")
            if not output_virtual_path.startswith("/mnt/user-data/outputs/"):
                return json.dumps({"success": False, "error": "output_path must be under /mnt/user-data/outputs", "output_path": output_virtual_path}, ensure_ascii=False)
            resolved_output_path = path_service.resolve_virtual_path(thread_id, output_virtual_path)
            payload = self.browser_tools_service.vision(
                config_result=config_result,
                session_id=session_id,
                output_path=resolved_output_path,
                output_virtual_path=output_virtual_path,
                question=question,
                full_page=full_page,
            )
            if payload.get("success"):
                relative_path = output_virtual_path.removeprefix("/mnt/user-data/outputs/")
                payload["artifact_url"] = path_service.to_artifact_descriptor(thread_id, "outputs", relative_path).artifact_url
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"question": {"type": ["string", "null"]}, "output_path": {"type": ["string", "null"]}, "session_id": {"type": "string"}, "full_page": {"type": "boolean"}}}
        return self._browser_entry(
            name="browser_vision",
            display_name="Browser Vision",
            summary="Capture screenshot evidence and a text snapshot for multimodal or human visual inspection.",
            handler=_structured_tool_handler(name="browser_vision", description=description, func=browser_vision, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=12000,
        )

    def build_browser_cdp_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Run a low-level Chrome DevTools Protocol method against the current browser session."

        def browser_cdp(method: str, params: dict | None = None, session_id: str = "default") -> str:
            payload = self.browser_tools_service.cdp(config_result=config_result, session_id=session_id, method=method, params=params)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"method": {"type": "string"}, "params": {"type": ["object", "null"], "additionalProperties": True}, "session_id": {"type": "string"}}, "required": ["method"]}
        return self._browser_entry(
            name="browser_cdp",
            display_name="Browser CDP",
            summary="Advanced escape hatch for Chrome DevTools Protocol calls when first-class browser tools are insufficient.",
            handler=_structured_tool_handler(name="browser_cdp", description=description, func=browser_cdp, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=12000,
        )

    def build_browser_dialog_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Accept or dismiss the active browser JavaScript dialog."

        def browser_dialog(accept: bool = True, prompt_text: str | None = None, session_id: str = "default") -> str:
            payload = self.browser_tools_service.dialog(config_result=config_result, session_id=session_id, accept=accept, prompt_text=prompt_text)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"accept": {"type": "boolean"}, "prompt_text": {"type": ["string", "null"]}, "session_id": {"type": "string"}}}
        return self._browser_entry(
            name="browser_dialog",
            display_name="Browser Dialog",
            summary="Accept or dismiss a JavaScript alert/confirm/prompt in the active browser session.",
            handler=_structured_tool_handler(name="browser_dialog", description=description, func=browser_dialog, input_schema=input_schema),
            input_schema=input_schema,
        )

    def build_browser_close_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Close the governed browser session."

        def browser_close(session_id: str = "default") -> str:
            payload = self.browser_tools_service.close(config_result=config_result, session_id=session_id)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"session_id": {"type": "string"}}}
        return self._browser_entry(
            name="browser_close",
            display_name="Browser Close",
            summary="Close a browser session and release its runtime state.",
            handler=_structured_tool_handler(name="browser_close", description=description, func=browser_close, input_schema=input_schema),
            input_schema=input_schema,
        )

    def _workspace_entry(
        self,
        *,
        name: str,
        display_name: str,
        summary: str,
        handler,
        input_schema: dict[str, object],
        output_budget: int = 10000,
    ) -> ToolRegistryEntry:
        return ToolRegistryEntry(
            name=name,
            display_name=display_name,
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="workspace",
            summary=summary,
            handler=handler,
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=output_budget,
        )

    def build_gmail_search_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Search Gmail messages using a query and return compact message summaries."

        def gmail_search(query: str = "", max_results: int = 10, label_ids: list[str] | None = None, include_spam_trash: bool = False, user_id: str | None = None) -> str:
            payload = self.google_workspace_service.gmail_search(
                config_result=config_result,
                query=query,
                max_results=max_results,
                label_ids=label_ids,
                include_spam_trash=include_spam_trash,
                user_id=user_id,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
                "label_ids": {"type": ["array", "null"], "items": {"type": "string"}},
                "include_spam_trash": {"type": "boolean"},
                "user_id": {"type": ["string", "null"]},
            },
        }
        return self._workspace_entry(
            name="gmail_search",
            display_name="Gmail Search",
            summary="Search Gmail messages through Google Workspace REST or mock provider and return compact summaries.",
            handler=_structured_tool_handler(name="gmail_search", description=description, func=gmail_search, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=14000,
        )

    def build_gmail_read_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Read one Gmail message by id and return headers plus body text."

        def gmail_read(message_id: str, user_id: str | None = None, max_chars: int = 12000) -> str:
            payload = self.google_workspace_service.gmail_read(
                config_result=config_result,
                message_id=message_id,
                user_id=user_id,
                max_chars=max_chars,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"message_id": {"type": "string"}, "user_id": {"type": ["string", "null"]}, "max_chars": {"type": "integer", "minimum": 500, "maximum": 50000}}, "required": ["message_id"]}
        return self._workspace_entry(
            name="gmail_read",
            display_name="Gmail Read",
            summary="Read one Gmail message with body extraction and secret scrubbing.",
            handler=_structured_tool_handler(name="gmail_read", description=description, func=gmail_read, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=18000,
        )

    def build_gmail_labels_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "List Gmail labels for the configured user."

        def gmail_labels(user_id: str | None = None) -> str:
            payload = self.google_workspace_service.gmail_labels(config_result=config_result, user_id=user_id)
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"user_id": {"type": ["string", "null"]}}}
        return self._workspace_entry(
            name="gmail_labels",
            display_name="Gmail Labels",
            summary="List Gmail labels for discovery and filtering.",
            handler=_structured_tool_handler(name="gmail_labels", description=description, func=gmail_labels, input_schema=input_schema),
            input_schema=input_schema,
        )

    def build_gmail_send_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Send an email through Gmail using the configured Google Workspace token."

        def gmail_send(to: str, subject: str, body: str, cc: str | None = None, bcc: str | None = None, user_id: str | None = None) -> str:
            payload = self.google_workspace_service.gmail_send(
                config_result=config_result,
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
                user_id=user_id,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "cc": {"type": ["string", "null"]}, "bcc": {"type": ["string", "null"]}, "user_id": {"type": ["string", "null"]}}, "required": ["to", "subject", "body"]}
        return self._workspace_entry(
            name="gmail_send",
            display_name="Gmail Send",
            summary="Send an email through Gmail; governed as network_request and suitable for explicit user-requested sends.",
            handler=_structured_tool_handler(name="gmail_send", description=description, func=gmail_send, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=8000,
        )

    def build_gmail_create_draft_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Create a Gmail draft instead of immediately sending an email."

        def gmail_create_draft(to: str, subject: str, body: str, cc: str | None = None, bcc: str | None = None, user_id: str | None = None) -> str:
            payload = self.google_workspace_service.gmail_create_draft(
                config_result=config_result,
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
                user_id=user_id,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "cc": {"type": ["string", "null"]}, "bcc": {"type": ["string", "null"]}, "user_id": {"type": ["string", "null"]}}, "required": ["to", "subject", "body"]}
        return self._workspace_entry(
            name="gmail_create_draft",
            display_name="Gmail Create Draft",
            summary="Create a Gmail draft for review before sending.",
            handler=_structured_tool_handler(name="gmail_create_draft", description=description, func=gmail_create_draft, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=8000,
        )

    def build_calendar_list_events_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "List Google Calendar events in a time range."

        def calendar_list_events(calendar_id: str | None = None, time_min: str | None = None, time_max: str | None = None, query: str | None = None, max_results: int = 10) -> str:
            payload = self.google_workspace_service.calendar_list_events(
                config_result=config_result,
                calendar_id=calendar_id,
                time_min=time_min,
                time_max=time_max,
                query=query,
                max_results=max_results,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"calendar_id": {"type": ["string", "null"]}, "time_min": {"type": ["string", "null"]}, "time_max": {"type": ["string", "null"]}, "query": {"type": ["string", "null"]}, "max_results": {"type": "integer", "minimum": 1, "maximum": 100}}}
        return self._workspace_entry(
            name="calendar_list_events",
            display_name="Calendar List Events",
            summary="List Google Calendar events with time range and query filters.",
            handler=_structured_tool_handler(name="calendar_list_events", description=description, func=calendar_list_events, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=14000,
        )

    def build_calendar_create_event_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Create a Google Calendar event."

        def calendar_create_event(
            summary: str,
            start: str,
            end: str,
            calendar_id: str | None = None,
            description: str | None = None,
            location: str | None = None,
            attendees: list[str] | None = None,
            time_zone: str | None = None,
            send_updates: str = "none",
            create_meet_link: bool = False,
        ) -> str:
            payload = self.google_workspace_service.calendar_create_event(
                config_result=config_result,
                summary=summary,
                start=start,
                end=end,
                calendar_id=calendar_id,
                description=description,
                location=location,
                attendees=attendees,
                time_zone=time_zone,
                send_updates=send_updates,
                create_meet_link=create_meet_link,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "calendar_id": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "location": {"type": ["string", "null"]},
                "attendees": {"type": ["array", "null"], "items": {"type": "string"}},
                "time_zone": {"type": ["string", "null"]},
                "send_updates": {"type": "string", "enum": ["none", "all", "externalOnly", "externalonly"]},
                "create_meet_link": {"type": "boolean"},
            },
            "required": ["summary", "start", "end"],
        }
        return self._workspace_entry(
            name="calendar_create_event",
            display_name="Calendar Create Event",
            summary="Create a Google Calendar event with optional attendees and Meet link.",
            handler=_structured_tool_handler(name="calendar_create_event", description=description, func=calendar_create_event, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=10000,
        )

    def build_calendar_update_event_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Patch a Google Calendar event by id."

        def calendar_update_event(
            event_id: str,
            calendar_id: str | None = None,
            summary: str | None = None,
            start: str | None = None,
            end: str | None = None,
            description: str | None = None,
            location: str | None = None,
            attendees: list[str] | None = None,
            time_zone: str | None = None,
            status: str | None = None,
            send_updates: str = "none",
        ) -> str:
            payload = self.google_workspace_service.calendar_update_event(
                config_result=config_result,
                event_id=event_id,
                calendar_id=calendar_id,
                summary=summary,
                start=start,
                end=end,
                description=description,
                location=location,
                attendees=attendees,
                time_zone=time_zone,
                status=status,
                send_updates=send_updates,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "calendar_id": {"type": ["string", "null"]},
                "summary": {"type": ["string", "null"]},
                "start": {"type": ["string", "null"]},
                "end": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "location": {"type": ["string", "null"]},
                "attendees": {"type": ["array", "null"], "items": {"type": "string"}},
                "time_zone": {"type": ["string", "null"]},
                "status": {"type": ["string", "null"]},
                "send_updates": {"type": "string", "enum": ["none", "all", "externalOnly", "externalonly"]},
            },
            "required": ["event_id"],
        }
        return self._workspace_entry(
            name="calendar_update_event",
            display_name="Calendar Update Event",
            summary="Patch a Google Calendar event by id.",
            handler=_structured_tool_handler(name="calendar_update_event", description=description, func=calendar_update_event, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=10000,
        )

    def build_calendar_delete_event_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Delete a Google Calendar event by id."

        def calendar_delete_event(event_id: str, calendar_id: str | None = None, send_updates: str = "none") -> str:
            payload = self.google_workspace_service.calendar_delete_event(
                config_result=config_result,
                event_id=event_id,
                calendar_id=calendar_id,
                send_updates=send_updates,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"event_id": {"type": "string"}, "calendar_id": {"type": ["string", "null"]}, "send_updates": {"type": "string", "enum": ["none", "all", "externalOnly", "externalonly"]}}, "required": ["event_id"]}
        return self._workspace_entry(
            name="calendar_delete_event",
            display_name="Calendar Delete Event",
            summary="Delete a Google Calendar event by id.",
            handler=_structured_tool_handler(name="calendar_delete_event", description=description, func=calendar_delete_event, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=6000,
        )

    def build_calendar_free_busy_tool(self, *, config_result: ConfigResolutionResult) -> ToolRegistryEntry:
        description = "Query Google Calendar free/busy ranges for one or more calendars."

        def calendar_free_busy(time_min: str, time_max: str, calendar_ids: list[str] | None = None, time_zone: str | None = None) -> str:
            payload = self.google_workspace_service.calendar_free_busy(
                config_result=config_result,
                time_min=time_min,
                time_max=time_max,
                calendar_ids=calendar_ids,
                time_zone=time_zone,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"time_min": {"type": "string"}, "time_max": {"type": "string"}, "calendar_ids": {"type": ["array", "null"], "items": {"type": "string"}}, "time_zone": {"type": ["string", "null"]}}, "required": ["time_min", "time_max"]}
        return self._workspace_entry(
            name="calendar_free_busy",
            display_name="Calendar Free Busy",
            summary="Check free/busy ranges for Google Calendars.",
            handler=_structured_tool_handler(name="calendar_free_busy", description=description, func=calendar_free_busy, input_schema=input_schema),
            input_schema=input_schema,
            output_budget=12000,
        )

    def build_text_to_speech_tool(self, *, config_result: ConfigResolutionResult, path_service, thread_id: str) -> ToolRegistryEntry:
        description = "Generate speech audio from text through configured media providers and write the artifact under /mnt/user-data/outputs."

        def text_to_speech(
            text: str,
            output_path: str | None = None,
            provider: str | None = None,
            voice: str | None = None,
            model: str | None = None,
            response_format: str | None = None,
            speed: float = 1.0,
            instructions: str | None = None,
        ) -> str:
            normalized_format = response_format or "mp3"
            output_virtual_path = output_path or self.media_tools_service.default_tts_virtual_path(response_format=normalized_format)
            if not output_virtual_path.startswith("/mnt/user-data/outputs/"):
                return json.dumps(
                    {
                        "success": False,
                        "error": "output_path must be under /mnt/user-data/outputs",
                        "output_path": output_virtual_path,
                    },
                    ensure_ascii=False,
                )
            resolved_output_path = path_service.resolve_virtual_path(thread_id, output_virtual_path)
            payload = self.media_tools_service.text_to_speech(
                config_result=config_result,
                text=text,
                output_path=resolved_output_path,
                output_virtual_path=output_virtual_path,
                provider=provider,
                voice=voice,
                model=model,
                response_format=normalized_format,
                speed=speed,
                instructions=instructions,
            )
            if payload.get("success"):
                relative_path = output_virtual_path.removeprefix("/mnt/user-data/outputs/")
                payload["artifact_url"] = path_service.to_artifact_descriptor(thread_id, "outputs", relative_path).artifact_url
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "output_path": {"type": ["string", "null"]},
                "provider": {"type": ["string", "null"]},
                "voice": {"type": ["string", "null"]},
                "model": {"type": ["string", "null"]},
                "response_format": {"type": ["string", "null"], "enum": ["mp3", "opus", "aac", "flac", "wav", "pcm", None]},
                "speed": {"type": "number", "minimum": 0.25, "maximum": 4.0},
                "instructions": {"type": ["string", "null"]},
            },
            "required": ["text"],
        }
        return ToolRegistryEntry(
            name="text_to_speech",
            display_name="Text To Speech",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="media",
            summary="Generate TTS audio artifacts with mock, OpenAI, MiniMax, or optional Edge providers through governed output paths.",
            handler=_structured_tool_handler(name="text_to_speech", description=description, func=text_to_speech, input_schema=input_schema),
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=6000,
        )

    def build_speech_to_text_tool(self, *, config_result: ConfigResolutionResult, path_service, thread_id: str) -> ToolRegistryEntry:
        description = "Transcribe an audio file under /mnt/user-data using configured speech-to-text providers."

        def speech_to_text(
            input_path: str,
            provider: str | None = None,
            model: str | None = None,
            language: str | None = None,
            prompt: str | None = None,
            response_format: str = "json",
        ) -> str:
            resolved_input_path = path_service.resolve_virtual_path(thread_id, input_path)
            payload = self.media_tools_service.speech_to_text(
                config_result=config_result,
                input_path=resolved_input_path,
                input_virtual_path=input_path,
                provider=provider,
                model=model,
                language=language,
                prompt=prompt,
                response_format=response_format,
            )
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {
                "input_path": {"type": "string"},
                "provider": {"type": ["string", "null"]},
                "model": {"type": ["string", "null"]},
                "language": {"type": ["string", "null"]},
                "prompt": {"type": ["string", "null"]},
                "response_format": {"type": "string", "enum": ["json", "text", "verbose_json", "srt", "vtt", "diarized_json"]},
            },
            "required": ["input_path"],
        }
        return ToolRegistryEntry(
            name="speech_to_text",
            display_name="Speech To Text",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="media",
            summary="Transcribe audio files with mock, OpenAI-compatible, Groq, or Mistral providers while preserving virtual path boundaries.",
            handler=_structured_tool_handler(name="speech_to_text", description=description, func=speech_to_text, input_schema=input_schema),
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=8000,
        )

    def build_image_generate_tool(
        self,
        *,
        config_result: ConfigResolutionResult,
        path_service,
        thread_id: str,
        image_generation_model_name: str,
    ) -> ToolRegistryEntry:
        description = "Generate a raster image with the configured image generation provider and write the artifact under /mnt/user-data/outputs."
        model_config = config_result.effective_config.models.get(image_generation_model_name)
        model_image_generation = dict(model_config.image_generation or {}) if model_config is not None else {}
        if model_config is not None:
            for key in ("base_url", "api_key", "api_key_env"):
                value = getattr(model_config, key, None)
                if value and key not in model_image_generation:
                    model_image_generation[key] = value
            if "providers" not in model_image_generation and "provider" not in model_image_generation and _is_minimax_image_model(model_config):
                model_image_generation["providers"] = ["minimax"]
                model_image_generation.setdefault("model", "image-01")

        def image_generate(
            prompt: str,
            output_path: str | None = None,
            provider: str | None = None,
            model: str | None = None,
            response_format: str | None = None,
            size: str | None = None,
            quality: str | None = None,
            background: str | None = None,
            n: int = 1,
        ) -> str:
            configured_format = response_format or model_image_generation.get("output_format") or model_image_generation.get("response_format")
            normalized_format = str(configured_format or "png")
            output_virtual_path = output_path or self.media_tools_service.default_image_virtual_path(response_format=normalized_format)
            if not output_virtual_path.startswith("/mnt/user-data/outputs/"):
                return json.dumps(
                    {
                        "success": False,
                        "error": "output_path must be under /mnt/user-data/outputs",
                        "output_path": output_virtual_path,
                    },
                    ensure_ascii=False,
                )
            resolved_output_path = path_service.resolve_virtual_path(thread_id, output_virtual_path)
            payload = self.media_tools_service.image_generate(
                config_result=config_result,
                prompt=prompt,
                output_path=resolved_output_path,
                output_virtual_path=output_virtual_path,
                provider=provider,
                model=model,
                response_format=normalized_format,
                size=size,
                quality=quality,
                background=background,
                n=n,
                model_image_generation=model_image_generation,
            )
            if payload.get("success"):
                relative_path = output_virtual_path.removeprefix("/mnt/user-data/outputs/")
                payload["artifact_url"] = path_service.to_artifact_descriptor(thread_id, "outputs", relative_path).artifact_url
            return json.dumps(payload, ensure_ascii=False)

        input_schema = {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "output_path": {"type": ["string", "null"]},
                "provider": {"type": ["string", "null"]},
                "model": {"type": ["string", "null"]},
                "response_format": {"type": ["string", "null"], "enum": ["png", "jpeg", "jpg", "webp", None]},
                "size": {"type": ["string", "null"]},
                "quality": {"type": ["string", "null"]},
                "background": {"type": ["string", "null"]},
                "n": {"type": "integer", "minimum": 1, "maximum": 4},
            },
            "required": ["prompt"],
        }
        return ToolRegistryEntry(
            name="image_generate",
            display_name="Image Generate",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="media",
            summary="Generate image artifacts through the active image-generation-capable model route and return governed output artifact refs.",
            handler=_structured_tool_handler(name="image_generate", description=description, func=image_generate, input_schema=input_schema),
            input_schema=input_schema,
            approval_profile="network_request",
            risk_category="network_request",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="network_request", requires_network=True),
            output_budget=6000,
        )

    def build_js_repl_tool(self, *, path_service, thread_id: str) -> ToolRegistryEntry:
        description = "Execute small JavaScript snippets with the local Node.js runtime when available."

        def js_repl(script: str | None = None, expression: str | None = None, timeout_seconds: int = 10) -> str:
            node = shutil.which("node")
            if node is None:
                return json.dumps({"error": "node runtime is unavailable"}, ensure_ascii=False)
            effective_script = script
            if effective_script is None and expression is not None:
                effective_script = f"console.log(JSON.stringify((() => ({expression}))()));"
            if not effective_script:
                return json.dumps({"error": "script or expression is required"}, ensure_ascii=False)
            completed = subprocess.run(
                [node, "-e", effective_script],
                capture_output=True,
                text=True,
                cwd=str(path_service.thread_workspace_dir(thread_id)),
                timeout=timeout_seconds,
            )
            return json.dumps({"exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}, ensure_ascii=False)

        input_schema = {"type": "object", "properties": {"script": {"type": ["string", "null"]}, "expression": {"type": ["string", "null"]}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60}}}
        return ToolRegistryEntry(
            name="js_repl",
            display_name="JS REPL",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="execution",
            summary="Execute small JavaScript snippets for quick calculations or parsing instead of shelling out.",
            handler=_structured_tool_handler(name="js_repl", description=description, func=js_repl, input_schema=input_schema),
            input_schema=input_schema,
            approval_profile="shell_command",
            risk_category="shell_execution",
            typed_approval=TypedApprovalPolicy(mode="runtime", risk_category="shell_execution"),
            output_budget=6000,
        )


def _model_safe_skill_payload(payload: dict[str, object]) -> dict[str, object]:
    skill_id = str(payload.get("skill_id") or "").strip()
    safe = _sanitize_skill_paths(payload, skill_id=skill_id)
    if skill_id:
        safe["path"] = "SKILL.md"
        safe["source_root"] = f"skill://{skill_id}"
        safe["read_tool"] = "skill_read_file"
        safe["read_hint"] = "Use skill_read_file with skill_id and relative_path; do not call read_file on returned skill paths."
    return safe


def _model_safe_skill_level1_payload(payload: dict[str, object], *, include_description: bool = False) -> dict[str, object]:
    safe = _model_safe_skill_payload(payload)
    summary = _clip_metadata_text(safe.get("summary"), limit=360)
    item: dict[str, object] = {
        "skill_id": safe.get("skill_id"),
        "name": safe.get("name") or safe.get("skill_id"),
        "title": safe.get("title") or safe.get("name") or safe.get("skill_id"),
        "summary": summary,
        "enabled": safe.get("enabled"),
        "valid": safe.get("valid"),
        "trust": safe.get("trust"),
        "tags": safe.get("tags") or [],
        "read_tool": safe.get("read_tool"),
        "read_hint": safe.get("read_hint"),
        "path": safe.get("path"),
        "source_root": safe.get("source_root"),
    }
    if include_description:
        item["description"] = _clip_metadata_text(safe.get("description"), limit=1024)
    routing = _skill_routing_metadata(safe)
    if routing:
        item["routing"] = routing
    allowed_tools = safe.get("allowed_tools") or []
    if allowed_tools:
        item["allowed_tools"] = allowed_tools
    if safe.get("readiness") is not None:
        item["readiness"] = safe.get("readiness")
    if safe.get("curator"):
        item["curator"] = safe.get("curator")
    return item


def _skill_routing_metadata(payload: dict[str, object]) -> dict[str, object]:
    routing: dict[str, object] = {}
    for key in ("domain", "task_type", "risk_level"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            routing[key] = str(value).strip()
    input_requirements = payload.get("input_requirements") or []
    if isinstance(input_requirements, list):
        normalized = [str(item).strip() for item in input_requirements if str(item).strip()]
        if normalized:
            routing["input_requirements"] = normalized
    return routing


def _clip_metadata_text(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 1, 0)].rstrip()}…"


def _model_safe_skill_content_payload(payload: dict[str, object]) -> dict[str, object]:
    safe = _model_safe_skill_payload(payload)
    safe["path"] = "SKILL.md"
    return safe


def _model_safe_skill_file_index_payload(payload: dict[str, object]) -> dict[str, object]:
    safe = _model_safe_skill_payload(payload)
    safe["path"] = "."
    return safe


def _model_safe_skill_file_read_payload(payload: dict[str, object]) -> dict[str, object]:
    safe = _model_safe_skill_payload(payload)
    relative_path = str(payload.get("relative_path") or "").strip() or "SKILL.md"
    safe["path"] = relative_path
    return safe


def _sanitize_skill_paths(value: object, *, skill_id: str) -> object:
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str == "source_root" and skill_id:
                sanitized[key_str] = f"skill://{skill_id}"
                continue
            if key_str == "path":
                sanitized[key_str] = _relative_skill_path(item)
                continue
            sanitized[key_str] = _sanitize_skill_paths(item, skill_id=skill_id)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_skill_paths(item, skill_id=skill_id) for item in value]
    return value


def _relative_skill_path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("\\", "/")
    marker = "/skills/"
    if marker in normalized:
        after_marker = normalized.split(marker, 1)[1]
        parts = [part for part in after_marker.split("/") if part]
        return "/".join(parts[1:]) if len(parts) > 1 else "SKILL.md"
    if normalized.startswith("/"):
        return normalized.rsplit("/", 1)[-1] or "SKILL.md"
    return normalized
