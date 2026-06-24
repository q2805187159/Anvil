from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CapabilityVisibility(str, Enum):
    DISCOVERED = "discovered"
    ENABLED = "enabled"
    MATERIALIZED = "materialized"
    VISIBLE = "visible"


class ToolSourceKind(str, Enum):
    BUILTIN = "builtin"
    SKILL = "skill"
    MCP = "mcp"
    EXTENSION = "extension"
    PLUGIN = "plugin"
    FUTURE_APP = "future_app"


class ToolExecutionMode(str, Enum):
    SYNC = "sync"
    ASYNC = "async"


class CapabilityStability(str, Enum):
    STABLE = "stable"
    BETA = "beta"
    EXPERIMENTAL = "experimental"


class CapabilityHealthStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class CapabilityDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str
    required: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilitySuccessHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    user_correction_count: int = 0
    recent_success_rate: float = 0.0
    average_latency_ms: int | None = None

    @field_validator("usage_count", "success_count", "failure_count", "user_correction_count")
    @classmethod
    def _bound_count(cls, value: int) -> int:
        return max(int(value or 0), 0)

    @field_validator("recent_success_rate")
    @classmethod
    def _bound_success_rate(cls, value: float) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.0
        return round(min(max(numeric, 0.0), 1.0), 4)

    @field_validator("average_latency_ms")
    @classmethod
    def _bound_average_latency(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return max(int(value), 0)


class CapabilityResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    title: str
    description: str = ""
    server_id: str | None = None
    path: str | None = None
    name: str = ""
    kind: str = "resource"
    usage_scenarios: tuple[str, ...] = ()
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    examples: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    risk_level: str = "normal"
    latency_cost: int = 0
    token_cost: int = 0
    success_history: CapabilitySuccessHistory = Field(default_factory=CapabilitySuccessHistory)
    last_used_at: str | None = None
    related_memories: tuple[str, ...] = ()
    related_skills: tuple[str, ...] = ()
    graph_neighbors: tuple[str, ...] = ()
    source_ref: str = ""
    visibility_state: str = "discovered"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.resource_id

    def model_post_init(self, __context: Any) -> None:
        if not self.name:
            self.name = self.title
        if not self.source_ref:
            self.source_ref = self.resource_id

    @field_validator("latency_cost", "token_cost")
    @classmethod
    def _bound_cost(cls, value: int) -> int:
        return max(int(value or 0), 0)


class CapabilityPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str
    title: str
    description: str = ""
    server_id: str | None = None
    arguments: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class TypedApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "runtime"
    risk_category: str | None = None
    requires_network: bool = False
    scope: str | None = None


class CapabilityHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CapabilityHealthStatus = CapabilityHealthStatus.UNKNOWN
    message: str | None = None
    checked_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass
class SchemaSanitizerDiagnostics:
    dropped_properties: int = 0
    dropped_map_entries: int = 0
    dropped_list_items: int = 0
    truncated_strings: int = 0
    truncated_required: int = 0
    simplified_nullable_unions: int = 0
    cycles: int = 0
    max_depth_hits: int = 0
    _seen_ids: set[int] = field(default_factory=set)

    def to_payload(self) -> dict[str, int | bool]:
        return {
            "truncated": any(
                (
                    self.dropped_properties,
                    self.dropped_map_entries,
                    self.dropped_list_items,
                    self.truncated_strings,
                    self.truncated_required,
                    self.simplified_nullable_unions,
                    self.cycles,
                    self.max_depth_hits,
                )
            ),
            "dropped_properties": self.dropped_properties,
            "dropped_map_entries": self.dropped_map_entries,
            "dropped_list_items": self.dropped_list_items,
            "truncated_strings": self.truncated_strings,
            "truncated_required": self.truncated_required,
            "simplified_nullable_unions": self.simplified_nullable_unions,
            "cycles": self.cycles,
            "max_depth_hits": self.max_depth_hits,
        }


MAX_SCHEMA_DEPTH = 12
MAX_SCHEMA_PROPERTIES = 32
MAX_SCHEMA_MAP_ENTRIES = 32
MAX_SCHEMA_LIST_ITEMS = 16
MAX_SCHEMA_ENUM_ITEMS = 32
MAX_SCHEMA_STRING_CHARS = 160


class ToolRegistryEntry(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
        populate_by_name=True,
    )

    capability_id: str | None = None
    name: str
    display_name: str
    source_kind: ToolSourceKind
    source_id: str
    capability_group: str
    summary: str | None = None
    risk_category: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    stability: CapabilityStability = CapabilityStability.STABLE
    dependencies: tuple[CapabilityDependency, ...] = ()
    resources: tuple[CapabilityResource, ...] = ()
    prompts: tuple[CapabilityPrompt, ...] = ()
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        alias="schema",
        serialization_alias="schema",
    )
    handler: Any = None
    availability_check: Callable[[], bool] | None = None
    execution_mode: ToolExecutionMode = ToolExecutionMode.SYNC
    output_budget: int | None = None
    output_token_budget: int | None = None
    approval_profile: str | None = None
    typed_approval: TypedApprovalPolicy | None = None
    health: CapabilityHealth = Field(default_factory=CapabilityHealth)
    deferred: bool = False

    def model_post_init(self, __context: Any) -> None:
        diagnostics = SchemaSanitizerDiagnostics()
        self.input_schema = sanitize_tool_input_schema(self.input_schema, diagnostics=diagnostics)
        diagnostics_payload = diagnostics.to_payload()
        if diagnostics_payload["truncated"]:
            self.provenance = {
                **self.provenance,
                "schema_sanitizer": diagnostics_payload,
            }
        self.handler = sanitize_tool_handler_for_schema(
            handler=self.handler,
            name=self.name,
            description=self.display_name,
            input_schema=self.input_schema,
            source_kind=self.source_kind,
        )
        if self.capability_id is None:
            self.capability_id = f"{self.source_kind.value}:{self.source_id}:{self.name}"
        if self.summary is None:
            self.summary = self.display_name
        if self.typed_approval is None:
            self.typed_approval = TypedApprovalPolicy(
                mode=self.approval_profile or "runtime",
                risk_category=self.risk_category,
                requires_network=(self.risk_category or "") in {"network_request", "web", "image_search"},
            )

    def is_available(self) -> bool:
        if self.availability_check is None:
            return True
        return bool(self.availability_check())

    def with_input_schema(
        self,
        input_schema: dict[str, Any],
        *,
        schema_budget: dict[str, Any] | None = None,
    ) -> "ToolRegistryEntry":
        diagnostics = SchemaSanitizerDiagnostics()
        clean_schema = sanitize_tool_input_schema(input_schema, diagnostics=diagnostics)
        provenance = dict(self.provenance)
        if schema_budget is not None:
            provenance["schema_budget"] = schema_budget
        diagnostics_payload = diagnostics.to_payload()
        if diagnostics_payload["truncated"]:
            provenance["schema_sanitizer"] = diagnostics_payload
        return self.model_copy(
            update={
                "input_schema": clean_schema,
                "handler": sanitize_tool_handler_for_schema(
                    handler=self.handler,
                    name=self.name,
                    description=self.display_name,
                    input_schema=clean_schema,
                    source_kind=self.source_kind,
                ),
                "provenance": provenance,
            }
        )


def sanitize_tool_input_schema(
    schema: Any,
    *,
    diagnostics: SchemaSanitizerDiagnostics | None = None,
) -> dict[str, Any]:
    diagnostics = diagnostics or SchemaSanitizerDiagnostics()
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    sanitized = _sanitize_json_schema_value(schema, diagnostics=diagnostics, depth=0)
    if not isinstance(sanitized, dict):
        sanitized = {}
    if sanitized.get("type") != "object":
        sanitized["type"] = "object"
    if not isinstance(sanitized.get("properties"), dict):
        sanitized["properties"] = _sanitize_properties(
            schema.get("properties"),
            diagnostics=diagnostics,
            depth=1,
        )
    _sanitize_object_schema_in_place(sanitized, diagnostics=diagnostics, depth=0)
    return sanitized


def sanitize_tool_handler_for_schema(
    *,
    handler: Any,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    source_kind: ToolSourceKind,
) -> Any:
    if handler is None:
        return None
    if source_kind is ToolSourceKind.BUILTIN:
        return handler
    try:
        from langchain_core.tools import StructuredTool
    except Exception:
        return handler
    handler_args_schema = getattr(handler, "args_schema", None)
    if handler_args_schema == input_schema:
        return handler

    def _dispatch(**kwargs: Any) -> Any:
        return _invoke_tool_handler(handler, kwargs)

    return StructuredTool(
        name=name,
        description=getattr(handler, "description", None) or description,
        args_schema=input_schema,
        func=_dispatch,
    )


def _invoke_tool_handler(handler: Any, kwargs: dict[str, Any]) -> Any:
    invoke = getattr(handler, "invoke", None)
    if callable(invoke):
        return invoke(kwargs)
    if callable(handler):
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError):
            return handler(**kwargs)
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
            return handler(**kwargs)
        accepted_kwargs = {
            name: value
            for name, value in kwargs.items()
            if name in signature.parameters
            and signature.parameters[name].kind
            in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        }
        return handler(**accepted_kwargs)
    raise TypeError(f"tool handler for {getattr(handler, 'name', type(handler).__name__)} is not callable")


def _sanitize_json_schema_value(
    value: Any,
    *,
    diagnostics: SchemaSanitizerDiagnostics,
    depth: int,
) -> Any:
    if depth > MAX_SCHEMA_DEPTH:
        diagnostics.max_depth_hits += 1
        return None
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in diagnostics._seen_ids:
            diagnostics.cycles += 1
            return None
        diagnostics._seen_ids.add(value_id)
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            clean_item = _sanitize_schema_keyword(str(key), item, diagnostics=diagnostics, depth=depth + 1)
            if clean_item is not None:
                sanitized[str(key)] = clean_item
        diagnostics._seen_ids.remove(value_id)
        _sanitize_object_schema_in_place(sanitized, diagnostics=diagnostics, depth=depth)
        return sanitized
    if isinstance(value, (list, tuple)):
        sanitized_list = []
        for item in list(value)[:MAX_SCHEMA_LIST_ITEMS]:
            clean_item = _sanitize_json_schema_value(item, diagnostics=diagnostics, depth=depth + 1)
            if clean_item is not None:
                sanitized_list.append(clean_item)
        if len(value) > MAX_SCHEMA_LIST_ITEMS:
            diagnostics.dropped_list_items += len(value) - MAX_SCHEMA_LIST_ITEMS
        return sanitized_list
    if isinstance(value, str | int | float | bool) or value is None:
        return _sanitize_scalar(value, diagnostics=diagnostics)
    return None


def _sanitize_schema_keyword(
    key: str,
    value: Any,
    *,
    diagnostics: SchemaSanitizerDiagnostics,
    depth: int,
) -> Any:
    if key == "type":
        return _sanitize_schema_type(value, diagnostics=diagnostics)
    if key == "properties":
        return _sanitize_properties(value, diagnostics=diagnostics, depth=depth)
    if key in {"$defs", "definitions", "dependentSchemas", "patternProperties"}:
        return _sanitize_schema_map(value, diagnostics=diagnostics, depth=depth)
    if key in {"allOf", "anyOf", "oneOf", "prefixItems"}:
        return _sanitize_schema_list(value, diagnostics=diagnostics, depth=depth)
    if key in {"items", "additionalProperties", "contains", "not", "if", "then", "else"}:
        if isinstance(value, bool):
            return value
        clean = _sanitize_json_schema_value(value, diagnostics=diagnostics, depth=depth)
        return clean if isinstance(clean, dict) else None
    if key == "required":
        if not isinstance(value, (list, tuple)):
            return None
        if len(value) > MAX_SCHEMA_PROPERTIES:
            diagnostics.truncated_required += len(value) - MAX_SCHEMA_PROPERTIES
        return [
            item
            for item in value[:MAX_SCHEMA_PROPERTIES]
            if isinstance(item, str)
        ] or None
    if key == "enum":
        if not isinstance(value, (list, tuple)):
            return None
        if len(value) > MAX_SCHEMA_ENUM_ITEMS:
            diagnostics.dropped_list_items += len(value) - MAX_SCHEMA_ENUM_ITEMS
        values = [
            clean
            for item in list(value)[:MAX_SCHEMA_ENUM_ITEMS]
            if _is_json_scalar(clean := _sanitize_json_schema_value(item, diagnostics=diagnostics, depth=depth))
            and (clean is not None or item is None)
        ]
        return values or None
    return _sanitize_json_schema_value(value, diagnostics=diagnostics, depth=depth)


def _sanitize_schema_type(value: Any, *, diagnostics: SchemaSanitizerDiagnostics) -> str | None:
    valid = {"object", "array", "string", "number", "integer", "boolean", "null"}
    if isinstance(value, str):
        return value if value in valid else None
    if not isinstance(value, (list, tuple)):
        return None
    normalized = [str(item) for item in value if isinstance(item, str) and item in valid]
    if not normalized:
        return None
    non_null = [item for item in normalized if item != "null"]
    if non_null:
        if "null" in normalized or len(normalized) > 1:
            diagnostics.simplified_nullable_unions += 1
        return non_null[0]
    return "null"


def _sanitize_properties(
    value: Any,
    *,
    diagnostics: SchemaSanitizerDiagnostics,
    depth: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    properties: dict[str, Any] = {}
    items = list(value.items())
    for name, raw_schema in items[:MAX_SCHEMA_PROPERTIES]:
        clean = _sanitize_json_schema_value(raw_schema, diagnostics=diagnostics, depth=depth + 1)
        properties[str(name)] = clean if isinstance(clean, dict) else {}
    if len(items) > MAX_SCHEMA_PROPERTIES:
        diagnostics.dropped_properties += len(items) - MAX_SCHEMA_PROPERTIES
    return properties


def _sanitize_schema_map(
    value: Any,
    *,
    diagnostics: SchemaSanitizerDiagnostics,
    depth: int,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    schemas: dict[str, Any] = {}
    items = list(value.items())
    for name, raw_schema in items[:MAX_SCHEMA_MAP_ENTRIES]:
        clean = _sanitize_json_schema_value(raw_schema, diagnostics=diagnostics, depth=depth + 1)
        if isinstance(clean, dict):
            schemas[str(name)] = clean
    if len(items) > MAX_SCHEMA_MAP_ENTRIES:
        diagnostics.dropped_map_entries += len(items) - MAX_SCHEMA_MAP_ENTRIES
    return schemas or None


def _sanitize_schema_list(
    value: Any,
    *,
    diagnostics: SchemaSanitizerDiagnostics,
    depth: int,
) -> list[Any] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) > MAX_SCHEMA_LIST_ITEMS:
        diagnostics.dropped_list_items += len(value) - MAX_SCHEMA_LIST_ITEMS
    schemas = [
        clean
        for item in list(value)[:MAX_SCHEMA_LIST_ITEMS]
        if isinstance(clean := _sanitize_json_schema_value(item, diagnostics=diagnostics, depth=depth + 1), dict)
    ]
    return schemas or None


def _sanitize_object_schema_in_place(
    schema: dict[str, Any],
    *,
    diagnostics: SchemaSanitizerDiagnostics,
    depth: int,
) -> None:
    if schema.get("type") == "object":
        schema["properties"] = _sanitize_properties(
            schema.get("properties"),
            diagnostics=diagnostics,
            depth=depth + 1,
        )
        required = schema.get("required")
        if isinstance(required, list):
            property_names = set(schema["properties"])
            deduped: list[str] = []
            invalid_count = 0
            for item in required:
                if isinstance(item, str) and item in property_names and item not in deduped:
                    deduped.append(item)
                else:
                    invalid_count += 1
            if deduped:
                schema["required"] = deduped
            else:
                schema.pop("required", None)
            if invalid_count:
                diagnostics.truncated_required += invalid_count
        else:
            schema.pop("required", None)


def _sanitize_scalar(value: Any, *, diagnostics: SchemaSanitizerDiagnostics) -> Any:
    if isinstance(value, str) and len(value) > MAX_SCHEMA_STRING_CHARS:
        diagnostics.truncated_strings += 1
        return value[:MAX_SCHEMA_STRING_CHARS]
    return value


def _is_json_scalar(value: Any) -> bool:
    return isinstance(value, str | int | float | bool) or value is None


class DeferredCapabilityPromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promoted_names: tuple[str, ...] = ()
    query: str | None = None


class SkillSelectionFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    turn_id: str
    selected: bool = False
    injected: bool = False
    used_by_llm: bool = False
    outcome: str = "unknown"
    user_correction: bool = False
    latency_ms: int | None = None
    context_block_refs: tuple[str, ...] = ()

    @field_validator("skill_id", "turn_id", "outcome")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("value must not be empty")
        return text[:160]

    @field_validator("latency_ms")
    @classmethod
    def _bound_latency(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return max(int(value), 0)

    @field_validator("context_block_refs", mode="before")
    @classmethod
    def _bound_context_block_refs(cls, value: Any) -> tuple[str, ...]:
        refs = _string_tuple(value)
        return tuple(ref[:240] for ref in refs[:12])


class CapabilityFeedbackDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    capability_ids: tuple[str, ...] = ()
    updated: bool = False
    feedback_count: int = 0
    success_count: int = 0
    correction_count: int = 0
    utility_score: float = 0.0
    last_outcome: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("utility_score")
    @classmethod
    def _bound_utility_score(cls, value: float) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.0
        return round(min(max(numeric, 0.0), 1.0), 4)


class CapabilitySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    max_results: int = 5
    source_id: str | None = None
    include_visible: bool = False
    promote: bool = True


class CapabilitySearchTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = 0.0
    matched_fields: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()


class CapabilitySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: tuple[ToolRegistryEntry, ...]
    promotion: DeferredCapabilityPromotion
    total_matches: int = 0
    match_traces: dict[str, CapabilitySearchTrace] = Field(default_factory=dict)


class HiddenCapabilitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: tuple[str, ...] = ()
    example_names: tuple[str, ...] = ()
    request_hint: str = "Use capability_search to request a hidden or deferred capability by name or task."
    omitted_count: int = 0
    token_cost: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("omitted_count", "token_cost")
    @classmethod
    def _bound_count(cls, value: int) -> int:
        return max(int(value or 0), 0)


class CapabilityCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    name: str
    display_name: str
    summary: str
    source_kind: ToolSourceKind
    source_id: str
    capability_group: str
    visibility: CapabilityVisibility
    deferred: bool = False
    stability: CapabilityStability = CapabilityStability.STABLE
    risk_category: str | None = None
    approval: TypedApprovalPolicy | None = None
    resources: tuple[CapabilityResource, ...] = ()
    prompts: tuple[CapabilityPrompt, ...] = ()
    dependencies: tuple[CapabilityDependency, ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)
    health: CapabilityHealth = Field(default_factory=CapabilityHealth)


class CapabilityContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    visible_tool_names: tuple[str, ...] = ()
    deferred_tool_names: tuple[str, ...] = ()
    enabled_skill_ids: tuple[str, ...] = ()
    effective_mcp_servers: tuple[str, ...] = ()
    effective_extension_sources: tuple[str, ...] = ()
    effective_plugin_ids: tuple[str, ...] = ()
    effective_app_ids: tuple[str, ...] = ()
    active_promotions: tuple[str, ...] = ()
    prompt_safe_summaries: tuple[str, ...] = ()


class CapabilityAssemblyDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovered_tool_count: int = 0
    enabled_tool_count: int = 0
    materialized_tool_count: int = 0
    visible_tool_count: int = 0
    deferred_tool_count: int = 0
    active_promotion_count: int = 0
    visible_schema_token_budget: int | None = None
    visible_schema_tokens: int = 0
    deferred_schema_tokens: int = 0
    total_schema_tokens: int = 0
    visible_schema_budget_remaining_tokens: int | None = None
    schema_compacted_tool_count: int = 0
    schema_deferred_tool_count: int = 0
    action_prefilter_deferred_tool_count: int = 0
    sanitizer_truncated_tool_count: int = 0
    assembly_stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    slowest_assembly_stage: str | None = None
    slowest_assembly_stage_duration_ms: int | None = None
    skills_discovery_cache_hit: bool | None = None
    skills_discovery_watch_enabled: bool | None = None
    skills_discovery_root_count: int = 0
    skills_discovery_manifest_count: int = 0
    skills_discovery_enabled_count: int = 0
    skills_discovery_package_count: int = 0
    skills_discovery_stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    slowest_skills_discovery_stage: str | None = None
    slowest_skills_discovery_stage_duration_ms: int | None = None
    skill_retrieval_query: str = ""
    skill_retrieval_top_k: int = 0
    skill_retrieval_selected_ids: tuple[str, ...] = ()
    skill_retrieval_tiers_used: tuple[str, ...] = ()
    skill_retrieval_candidate_count: int = 0
    skill_retrieval_loaded_full_content: bool | None = None
    skill_retrieval_embedding_mode: str | None = None
    skill_retrieval_expanded_query_terms: tuple[str, ...] = ()
    skill_retrieval_prefetch_ids: tuple[str, ...] = ()
    skill_retrieval_l4_rerank_triggered: bool = False
    skill_retrieval_l5_hyde_triggered: bool = False
    skill_retrieval_l6_prefetch_triggered: bool = False
    skill_retrieval_salience_route_id: str | None = None
    skill_retrieval_goal_stack_ref: str | None = None
    skill_retrieval_active_goal_id: str | None = None
    visible_by_source_kind: dict[str, int] = Field(default_factory=dict)
    deferred_by_source_kind: dict[str, int] = Field(default_factory=dict)
    visible_by_group: dict[str, int] = Field(default_factory=dict)
    deferred_by_group: dict[str, int] = Field(default_factory=dict)


class CapabilityBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    catalog_fingerprint: str = ""
    discovered_tools: tuple[ToolRegistryEntry, ...] = ()
    enabled_tools: tuple[ToolRegistryEntry, ...] = ()
    materialized_tools: tuple[ToolRegistryEntry, ...] = ()
    visible_tools: tuple[ToolRegistryEntry, ...]
    deferred_tools: tuple[ToolRegistryEntry, ...]
    enabled_skill_ids: tuple[str, ...] = ()
    mentioned_skill_ids: tuple[str, ...] = ()
    effective_mcp_servers: tuple[str, ...] = ()
    effective_extension_sources: tuple[str, ...] = ()
    effective_plugin_ids: tuple[str, ...] = ()
    effective_app_ids: tuple[str, ...] = ()
    prompt_safe_summaries: tuple[str, ...] = ()
    assembly_diagnostics: CapabilityAssemblyDiagnostics = Field(default_factory=CapabilityAssemblyDiagnostics)
    capability_context: CapabilityContext | None = None


ToolRegistryEntry.model_rebuild()
CapabilitySearchResult.model_rebuild()
CapabilityBundle.model_rebuild()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        text = str(value).strip()
        return (text,) if text else ()
    if isinstance(value, tuple | list | set):
        return tuple(str(item).strip() for item in value if str(item or "").strip())
    text = str(value).strip()
    return (text,) if text else ()
