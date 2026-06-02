from __future__ import annotations

import json

from anvil.runtime.tool_registry import (
    CapabilitySearchRequest,
    DeferredCapabilityPromotion,
    ToolRegistry,
    ToolRegistryEntry,
    ToolSourceKind,
)


def test_tool_registry_preserves_built_in_names_and_namespaces_collisions() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="search",
            display_name="Search",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="web",
        )
    )
    external = registry.register(
        ToolRegistryEntry(
            name="search",
            display_name="GitHub Search",
            source_kind=ToolSourceKind.MCP,
            source_id="github",
            capability_group="web",
        )
    )

    assert external.name == "github__search"


def test_tool_registry_builds_per_request_visible_bundle() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="edit",
            display_name="Edit",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="filesystem",
            availability_check=lambda: True,
        )
    )
    registry.register(
        ToolRegistryEntry(
            name="browse",
            display_name="Browse",
            source_kind=ToolSourceKind.MCP,
            source_id="browser",
            capability_group="web",
            availability_check=lambda: False,
        )
    )

    bundle = registry.build_bundle(
        effective_config_fingerprint="cfg-1",
        enabled_source_ids={"core", "browser"},
        allowed_capability_groups={"filesystem"},
    )

    assert [entry.name for entry in bundle.visible_tools] == ["edit"]
    assert bundle.deferred_tools == ()


def test_deferred_capability_search_and_promotion() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="ticket_lookup",
            display_name="Ticket Lookup",
            source_kind=ToolSourceKind.EXTENSION,
            source_id="jira",
            capability_group="support",
            deferred=True,
        )
    )

    before = registry.build_bundle(effective_config_fingerprint="cfg-1")
    search_result = registry.search(CapabilitySearchRequest(query="ticket"))
    after = registry.build_bundle(
        effective_config_fingerprint="cfg-1",
        promoted_names=search_result.promotion,
    )

    assert [entry.name for entry in before.deferred_tools] == ["ticket_lookup"]
    assert [entry.name for entry in search_result.matches] == ["ticket_lookup"]
    assert [entry.name for entry in after.visible_tools] == ["ticket_lookup"]
    assert before.fingerprint != after.fingerprint


def test_capability_search_explains_provenance_matches() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="ticket_lookup",
            display_name="Ticket Lookup",
            source_kind=ToolSourceKind.EXTENSION,
            source_id="jira",
            capability_group="support",
            provenance={"plugin_id": "support-pack", "origin": "plugin_config"},
            deferred=True,
        )
    )

    search_result = registry.search(CapabilitySearchRequest(query="support-pack"))

    assert [entry.name for entry in search_result.matches] == ["ticket_lookup"]
    assert "provenance" in search_result.match_traces["ticket_lookup"].matched_fields
    assert set(search_result.match_traces["ticket_lookup"].query_terms) == {"support", "pack"}


def test_tool_registry_defers_visible_tools_when_schema_budget_is_exceeded() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="capability_search",
            display_name="Capability Search",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="core",
            input_schema={"properties": {"query": {"type": "string"}}},
        )
    )
    registry.register(
        ToolRegistryEntry(
            name="large_extension_tool",
            display_name="Large Extension Tool",
            source_kind=ToolSourceKind.EXTENSION,
            source_id="big",
            capability_group="external",
            input_schema={"properties": {f"field_{index}": {"type": "string"} for index in range(200)}},
        )
    )

    bundle = registry.build_bundle(
        effective_config_fingerprint="cfg-1",
        visible_schema_token_budget=20,
        always_visible_names={"capability_search"},
    )

    assert [entry.name for entry in bundle.visible_tools] == ["capability_search"]
    assert [entry.name for entry in bundle.deferred_tools] == ["large_extension_tool"]
    assert bundle.deferred_tools[0].provenance["schema_budget"]["status"] == "deferred_due_budget"
    assert bundle.assembly_diagnostics.visible_tool_count == 1
    assert bundle.assembly_diagnostics.deferred_tool_count == 1
    assert bundle.assembly_diagnostics.visible_schema_token_budget == 20
    assert bundle.assembly_diagnostics.visible_schema_tokens >= 1
    assert bundle.assembly_diagnostics.schema_deferred_tool_count == 1
    assert bundle.assembly_diagnostics.deferred_by_source_kind == {"extension": 1}


def test_tool_registry_task_prefilter_keeps_relevant_large_catalog_tools_visible() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="capability_search",
            display_name="Capability Search",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="capability_discovery",
            summary="Search capabilities.",
        )
    )
    for index in range(30):
        registry.register(
            ToolRegistryEntry(
                name=f"calendar_noise_{index}",
                display_name=f"Calendar Noise {index}",
                source_kind=ToolSourceKind.MCP,
                source_id="large-suite",
                capability_group="google_workspace",
                summary="List and update calendar events.",
            )
        )
    for index in range(6):
        registry.register(
            ToolRegistryEntry(
                name=f"github_search_{index}",
                display_name=f"GitHub Search {index}",
                source_kind=ToolSourceKind.MCP,
                source_id="large-suite",
                capability_group="code",
                summary="Search GitHub repositories, pull requests, and code references.",
            )
        )

    bundle = registry.build_bundle(
        effective_config_fingerprint="cfg-1",
        request_context="Search GitHub code references for the Anvil repo",
        action_prefilter={"enabled": True, "min_tools": 8, "max_visible": 8, "min_score": 0.25},
        always_visible_names={"capability_search"},
    )
    visible_names = {entry.name for entry in bundle.visible_tools}
    deferred = {entry.name: entry for entry in bundle.deferred_tools}

    assert "capability_search" in visible_names
    assert {f"github_search_{index}" for index in range(6)}.issubset(visible_names)
    assert "calendar_noise_0" in deferred
    assert deferred["calendar_noise_0"].provenance["action_prefilter"]["status"] == "deferred_due_low_task_relevance"
    assert bundle.assembly_diagnostics.action_prefilter_deferred_tool_count >= 1
    assert bundle.assembly_diagnostics.visible_by_group["code"] == 6
    assert bundle.assembly_diagnostics.deferred_by_group["google_workspace"] >= 1


def test_tool_registry_task_prefilter_preserves_promoted_tools() -> None:
    registry = ToolRegistry()
    for index in range(12):
        registry.register(
            ToolRegistryEntry(
                name=f"browser_noise_{index}",
                display_name=f"Browser Noise {index}",
                source_kind=ToolSourceKind.MCP,
                source_id="browser-suite",
                capability_group="browser",
                summary="Click and inspect browser pages.",
            )
        )
    registry.register(
        ToolRegistryEntry(
            name="calendar_create_event",
            display_name="Calendar Create Event",
            source_kind=ToolSourceKind.MCP,
            source_id="calendar-suite",
            capability_group="google_workspace",
            summary="Create a calendar event.",
        )
    )

    bundle = registry.build_bundle(
        effective_config_fingerprint="cfg-1",
        request_context="Browse the website and take a screenshot",
        promoted_names=DeferredCapabilityPromotion(promoted_names=("calendar_create_event",)),
        action_prefilter={"enabled": True, "min_tools": 4, "max_visible": 4, "min_score": 0.25},
    )
    visible_names = {entry.name for entry in bundle.visible_tools}

    assert "calendar_create_event" in visible_names
    assert len(visible_names) == 4


def test_tool_registry_uses_sanitized_schema_for_model_visible_external_handlers() -> None:
    class RawExternalTool:
        name = "external_lookup"
        description = "External lookup"
        calls: list[dict[str, object]] = []

        def __init__(self) -> None:
            self.args_schema = {
                "type": "array",
                "properties": {
                    "query": {"type": "string", "description": object()},
                    "callback": object(),
                },
                "required": ["query", "missing"],
            }

        def invoke(self, payload: dict[str, object]) -> str:
            type(self).calls.append(payload)
            return json.dumps({"payload": payload}, ensure_ascii=False)

    RawExternalTool.calls = []
    raw_tool = RawExternalTool()
    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="external_lookup",
            display_name="External Lookup",
            source_kind=ToolSourceKind.MCP,
            source_id="remote",
            capability_group="external",
            handler=raw_tool,
            input_schema=raw_tool.args_schema,
        )
    )

    bundle = registry.build_bundle(effective_config_fingerprint="cfg-1")
    visible_handler = bundle.visible_tools[0].handler

    assert bundle.visible_tools[0].input_schema == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "callback": {},
        },
        "required": ["query"],
    }
    assert visible_handler is not raw_tool
    assert visible_handler.args_schema == bundle.visible_tools[0].input_schema
    assert json.loads(visible_handler.invoke({"query": "docs"})) == {"payload": {"query": "docs"}}
    assert RawExternalTool.calls == [{"query": "docs"}]


def test_tool_registry_compacts_external_schema_before_deferring_for_budget() -> None:
    class RawExternalTool:
        def invoke(self, payload: dict[str, object]) -> str:
            return json.dumps(payload, ensure_ascii=False)

    registry = ToolRegistry()
    registry.register(
        ToolRegistryEntry(
            name="capability_search",
            display_name="Capability Search",
            source_kind=ToolSourceKind.BUILTIN,
            source_id="core",
            capability_group="core",
            input_schema={"properties": {"query": {"type": "string"}}},
        )
    )
    registry.register(
        ToolRegistryEntry(
            name="verbose_external_tool",
            display_name="Verbose External Tool",
            source_kind=ToolSourceKind.MCP,
            source_id="remote",
            capability_group="external",
            handler=RawExternalTool(),
            input_schema={
                "type": "object",
                "description": "Top level docs " * 80,
                "properties": {
                    "query": {
                        "type": "string",
                        "title": "Query",
                        "description": "Detailed query guidance " * 80,
                        "examples": ["large example " * 40],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Detailed limit guidance " * 80,
                        "default": 5,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["query"],
            },
        )
    )

    bundle = registry.build_bundle(
        effective_config_fingerprint="cfg-1",
        visible_schema_token_budget=120,
        always_visible_names={"capability_search"},
    )
    external = next(entry for entry in bundle.visible_tools if entry.name == "verbose_external_tool")

    assert [entry.name for entry in bundle.visible_tools] == ["capability_search", "verbose_external_tool"]
    assert bundle.deferred_tools == ()
    assert external.input_schema == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    }
    assert external.handler.args_schema == external.input_schema
    assert external.provenance["schema_budget"]["status"] == "compacted"
    assert external.provenance["schema_budget"]["tokens_before"] > external.provenance["schema_budget"]["tokens_after"]
    assert bundle.assembly_diagnostics.schema_compacted_tool_count == 1
    assert bundle.assembly_diagnostics.schema_deferred_tool_count == 0
    assert bundle.assembly_diagnostics.visible_schema_tokens <= 120


def test_tool_registry_caps_pathological_external_schema_shape() -> None:
    root: dict[str, object] = {
        "type": "object",
        "properties": {},
        "required": [f"field_{index}" for index in range(80)],
        "$defs": {f"Def{index}": {"type": "string"} for index in range(80)},
        "oneOf": [{"type": "string", "description": "x" * 300} for _ in range(40)],
        "enum": [f"value-{index}" * 30 for index in range(70)],
    }
    properties = root["properties"]
    assert isinstance(properties, dict)
    for index in range(80):
        properties[f"field_{index}"] = {
            "type": "object",
            "description": "long property description " * 80,
            "properties": {
                f"nested_{nested}": {
                    "type": "string",
                    "description": "deep docs " * 80,
                }
                for nested in range(12)
            },
        }
    cyclic: dict[str, object] = {"type": "object", "properties": {}}
    cyclic["properties"] = {"self": cyclic}
    properties["field_0"] = cyclic

    registry = ToolRegistry()
    stored = registry.register(
        ToolRegistryEntry(
            name="pathological_external",
            display_name="Pathological External",
            source_kind=ToolSourceKind.MCP,
            source_id="remote",
            capability_group="external",
            input_schema=root,
        )
    )

    schema = stored.input_schema
    diagnostics = stored.provenance["schema_sanitizer"]

    assert schema["type"] == "object"
    assert len(schema["properties"]) <= 32
    assert schema["required"] == [f"field_{index}" for index in range(32)]
    assert len(schema["$defs"]) <= 32
    assert len(schema["oneOf"]) <= 16
    assert len(schema["enum"]) <= 32
    assert all(len(value) <= 160 for value in schema["enum"])
    assert schema["properties"]["field_0"] == {"type": "object", "properties": {"self": {}}}
    assert diagnostics["truncated"] is True
    assert diagnostics["dropped_properties"] > 0
    assert diagnostics["dropped_map_entries"] > 0
    assert diagnostics["dropped_list_items"] > 0
    assert diagnostics["truncated_strings"] > 0
    assert diagnostics["cycles"] > 0


def test_tool_registry_sanitizes_external_schema_metadata_before_catalog_use() -> None:
    non_json_default = object()
    registry = ToolRegistry()
    stored = registry.register(
        ToolRegistryEntry(
            name="external_lookup",
            display_name="External Lookup",
            source_kind=ToolSourceKind.MCP,
            source_id="remote",
            capability_group="external",
            input_schema={
                "properties": {
                    "limit": {"type": "integer", "default": non_json_default},
                    "note": {"type": "string"},
                    "callback": non_json_default,
                },
                "required": ["limit", "missing", 42, "limit", "note"],
            },
        )
    )

    bundle = registry.build_bundle(effective_config_fingerprint="cfg-1")
    catalog_item = registry.catalog_entries(bundle)[0]

    assert stored.input_schema == {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "note": {"type": "string"},
            "callback": {},
        },
        "required": ["limit", "note"],
    }
    assert bundle.visible_tools[0].input_schema == stored.input_schema
    assert catalog_item.name == "external_lookup"


def test_tool_registry_simplifies_nullable_type_unions() -> None:
    registry = ToolRegistry()
    stored = registry.register(
        ToolRegistryEntry(
            name="nullable_tool",
            display_name="Nullable Tool",
            source_kind=ToolSourceKind.MCP,
            source_id="remote",
            capability_group="external",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": ["string", "null"]},
                    "limit": {"type": ["integer", "null"]},
                },
                "required": ["query"],
            },
        )
    )

    assert stored.input_schema["properties"]["query"]["type"] == "string"
    assert stored.input_schema["properties"]["limit"]["type"] == "integer"
    assert stored.provenance["schema_sanitizer"]["simplified_nullable_unions"] >= 2


def test_tool_registry_sanitizes_external_schema_composition_and_definitions() -> None:
    non_json_default = object()
    registry = ToolRegistry()
    stored = registry.register(
        ToolRegistryEntry(
            name="complex_external",
            display_name="Complex External",
            source_kind=ToolSourceKind.MCP,
            source_id="remote",
            capability_group="external",
            input_schema={
                "type": "array",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": non_json_default,
                        "enum": ["docs", non_json_default, 7],
                    },
                    "mode": {
                        "oneOf": [
                            {"type": "string", "enum": ["fast", "deep"]},
                            non_json_default,
                            {"type": "null"},
                        ],
                    },
                    "filters": {
                        "type": "object",
                        "properties": "not-a-dict",
                        "additionalProperties": non_json_default,
                    },
                },
                "required": ["query", "missing", "query"],
                "$defs": {
                    "Filter": {"type": "object", "properties": {"name": {"type": "string"}, "bad": non_json_default}},
                    "Broken": non_json_default,
                },
                "definitions": {
                    "Legacy": {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
                },
                "allOf": [{"$ref": "#/$defs/Filter"}, non_json_default],
                "anyOf": "not-a-list",
            },
        )
    )

    assert stored.input_schema == {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "enum": ["docs", 7],
            },
            "mode": {
                "oneOf": [
                    {"type": "string", "enum": ["fast", "deep"]},
                    {"type": "null"},
                ],
            },
            "filters": {
                "type": "object",
                "properties": {},
            },
        },
        "required": ["query"],
        "$defs": {
            "Filter": {"type": "object", "properties": {"name": {"type": "string"}, "bad": {}}},
        },
        "definitions": {
            "Legacy": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]},
        },
        "allOf": [{"$ref": "#/$defs/Filter"}],
    }


def test_capability_search_keeps_default_result_limit_without_authoring_contract() -> None:
    registry = ToolRegistry()
    for name, summary in (
        ("PPT-document", "Generate PowerPoint documents from outlines"),
        ("slide-image", "Generate slide deck image assets"),
        ("document-export", "Export office documents"),
        ("web-search", "Search the public web"),
        ("report-summary", "Summarize generated documents"),
        ("archive-document", "Archive documents"),
    ):
        registry.register(
            ToolRegistryEntry(
                name=name,
                display_name=name,
                source_kind=ToolSourceKind.PLUGIN,
                source_id="catalog",
                capability_group="horizontal_tools",
                summary=summary,
                deferred=True,
            )
        )

    search_result = registry.search(CapabilitySearchRequest(query="document"))

    assert len(search_result.matches) == 4
    assert search_result.total_matches == 4
    assert all(not hasattr(entry, "tool_kind") for entry in search_result.matches)
    assert all(not hasattr(entry, "tool_contract") for entry in search_result.matches)
