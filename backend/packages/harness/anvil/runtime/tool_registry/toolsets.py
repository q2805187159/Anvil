from __future__ import annotations

from dataclasses import dataclass

from .contracts import CapabilityBundle, ToolRegistryEntry
from .registry import ToolRegistry
from .tool_names import CODING_TOOL_NAMES


@dataclass(frozen=True)
class ToolsetDefinition:
    name: str
    description: str
    capability_groups: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()


BUILTIN_TOOLSETS: tuple[ToolsetDefinition, ...] = (
    ToolsetDefinition(
        name="file",
        description="Read, discover, search, write, patch, and export files through governed virtual paths.",
        capability_groups=("filesystem",),
    ),
    ToolsetDefinition(
        name="terminal",
        description="Command execution and persistent terminal/process sessions.",
        capability_groups=("execution", "process"),
    ),
    ToolsetDefinition(
        name="web",
        description="Web and image research tools with provider adapters and network approval metadata.",
        tools=("web_search", "web_fetch", "web_extract", "web_crawl", "image_search"),
    ),
    ToolsetDefinition(
        name="browser",
        description="Stateful browser automation, screenshots, console inspection, and CDP escape hatch.",
        capability_groups=("browser",),
    ),
    ToolsetDefinition(
        name="media",
        description="Speech, audio, and image generation tools through governed artifacts.",
        capability_groups=("media",),
    ),
    ToolsetDefinition(
        name="google-workspace",
        description="Gmail and Google Calendar tools for mail search/read/draft/send and event scheduling.",
        capability_groups=("workspace",),
    ),
    ToolsetDefinition(
        name="skills",
        description="Skill discovery, governed skill reading, and agent-curated workspace skill improvement.",
        capability_groups=("skill_governance",),
    ),
    ToolsetDefinition(
        name="memory",
        description="Persistent memory, session recall, and memory trace inspection.",
        capability_groups=("memory",),
    ),
    ToolsetDefinition(
        name="automation",
        description="Scheduled task creation, updates, pause/resume, execution, and history.",
        capability_groups=("automation",),
    ),
    ToolsetDefinition(
        name="mcp",
        description="MCP server management plus MCP resources and prompt surfaces.",
        capability_groups=("external_capability_governance",),
        tools=("mcp_manage", "mcp_list_resources", "mcp_read_resource", "mcp_list_prompts", "mcp_get_prompt"),
    ),
    ToolsetDefinition(
        name="delegation",
        description="Bounded subagent and batch delegation tools when subagents are enabled.",
        capability_groups=("delegation",),
    ),
    ToolsetDefinition(
        name="planning",
        description="Todo planning and user clarification tools.",
        capability_groups=("planning", "control_flow", "capability_discovery"),
    ),
    ToolsetDefinition(
        name="coding",
        description="Coding-specific workflow: compact code index, narrow symbol/reference lookup, focus blast radius, code health, security, patterns, docs graph, file tools, and terminal fallback.",
        tools=tuple(sorted(CODING_TOOL_NAMES)),
        includes=("file", "terminal", "planning"),
    ),
    ToolsetDefinition(
        name="research",
        description="Research workflow: web, MCP inspection, memory recall, and file reading.",
        capability_groups=("research",),
        includes=("web", "browser", "google-workspace", "mcp", "memory", "file"),
    ),
    ToolsetDefinition(
        name="safe",
        description="Read-only discovery and research surfaces without shell execution or filesystem writes.",
        tools=(
            "ask_clarification",
            "capability_search",
            "extract_document",
            "file_info",
            "image_search",
            "list_dir",
            "glob_files",
            "grep_files",
            "memory",
            "memory_trace",
            "mcp_get_prompt",
            "mcp_list_prompts",
            "mcp_list_resources",
            "mcp_manage",
            "mcp_read_resource",
            "read_file",
            "search_files",
            "session_search",
            "gmail_labels",
            "gmail_read",
            "gmail_search",
            "calendar_free_busy",
            "calendar_list_events",
            "speech_to_text",
            "skill_content",
            "skill_files",
            "skill_read_file",
            "skill_view",
            "skills_list",
            "tool_catalog",
            "tool_view",
            "web_crawl",
            "web_fetch",
            "web_extract",
            "web_search",
            "write_todos",
        ),
    ),
    ToolsetDefinition(
        name="anvil-default",
        description="Harness-first default surface assembled from core runtime, tools, skills, MCP, memory, automation, and delegation.",
        includes=("coding", "research", "browser", "media", "google-workspace", "skills", "memory", "automation", "mcp", "delegation"),
    ),
)


class ToolsetCatalogService:
    def __init__(self, definitions: tuple[ToolsetDefinition, ...] = BUILTIN_TOOLSETS) -> None:
        self._definitions = {definition.name: definition for definition in definitions}

    def list_toolsets(
        self,
        *,
        registry: ToolRegistry,
        bundle: CapabilityBundle | None,
        query: str = "",
    ) -> list[dict[str, object]]:
        items = [self.describe_toolset(name=name, registry=registry, bundle=bundle) for name in sorted(self._definitions)]
        normalized_query = self._normalize(query)
        if not normalized_query:
            return items
        return [
            item
            for item in items
            if normalized_query in self._normalize(
                " ".join(
                    [
                        str(item["name"]),
                        str(item["description"]),
                        " ".join(str(group) for group in item["capability_groups"]),
                        " ".join(str(tool) for tool in item["materialized_tools"]),
                        " ".join(str(tool) for tool in item["missing_tools"]),
                    ]
                )
            )
        ]

    def describe_toolset(
        self,
        *,
        name: str,
        registry: ToolRegistry,
        bundle: CapabilityBundle | None,
    ) -> dict[str, object]:
        if name not in self._definitions:
            return {"error": f"unknown toolset '{name}'"}
        definition = self._definitions[name]
        resolved_names = self.resolve_tool_names(name=name, registry=registry)
        entries_by_name = {entry.name: entry for entry in registry.entries()}
        visible_names = {entry.name for entry in bundle.visible_tools} if bundle is not None else set()
        deferred_names = {entry.name for entry in bundle.deferred_tools} if bundle is not None else set()
        materialized_names = {entry.name for entry in bundle.materialized_tools} if bundle is not None else set(entries_by_name)
        materialized = [tool_name for tool_name in resolved_names if tool_name in materialized_names]
        return {
            "name": definition.name,
            "description": definition.description,
            "includes": list(definition.includes),
            "capability_groups": list(definition.capability_groups),
            "declared_tools": list(definition.tools),
            "materialized_tools": materialized,
            "visible_tools": [tool_name for tool_name in materialized if tool_name in visible_names],
            "deferred_tools": [tool_name for tool_name in materialized if tool_name in deferred_names],
            "unavailable_tools": [tool_name for tool_name in resolved_names if tool_name in entries_by_name and tool_name not in materialized_names],
            "missing_tools": [tool_name for tool_name in resolved_names if tool_name not in entries_by_name],
            "total_materialized": len(materialized),
            "total_visible": len([tool_name for tool_name in materialized if tool_name in visible_names]),
        }

    def resolve_tool_names(self, *, name: str, registry: ToolRegistry) -> tuple[str, ...]:
        if name not in self._definitions:
            return ()
        entries = registry.entries()
        resolved = self._resolve_definition(name=name, entries=entries, seen=set())
        return tuple(sorted(resolved))

    def _resolve_definition(
        self,
        *,
        name: str,
        entries: tuple[ToolRegistryEntry, ...],
        seen: set[str],
    ) -> set[str]:
        if name in seen or name not in self._definitions:
            return set()
        seen.add(name)
        definition = self._definitions[name]
        resolved = set(definition.tools)
        for group in definition.capability_groups:
            resolved.update(entry.name for entry in entries if entry.capability_group == group)
        for included in definition.includes:
            resolved.update(self._resolve_definition(name=included, entries=entries, seen=seen))
        return resolved

    def _normalize(self, value: str) -> str:
        return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())
