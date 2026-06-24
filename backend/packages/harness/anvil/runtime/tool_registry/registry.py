from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .contracts import (
    CapabilityAssemblyDiagnostics,
    CapabilityBundle,
    CapabilityCatalogEntry,
    CapabilityContext,
    CapabilityFeedbackDecision,
    CapabilityHealthStatus,
    CapabilityResource,
    CapabilitySearchRequest,
    CapabilitySearchResult,
    CapabilitySearchTrace,
    CapabilitySuccessHistory,
    CapabilityVisibility,
    DeferredCapabilityPromotion,
    HiddenCapabilitySummary,
    SkillSelectionFeedback,
    ToolRegistryEntry,
    ToolSourceKind,
)
from anvil.runtime.token_budget import TokenBudgetService


SEPARATOR_RE = re.compile(r"[-_/]+")
SCHEMA_COMPACT_DROP_KEYS = {
    "$comment",
    "default",
    "deprecated",
    "description",
    "examples",
    "readOnly",
    "title",
    "writeOnly",
}

ACTION_VERB_TERMS = {
    "read": {"read", "open", "view", "inspect", "show", "cat", "display", "查看", "读取", "打开", "看"},
    "write": {"write", "create", "add", "generate", "save", "export", "make", "生成", "创建", "写入", "保存", "导出"},
    "edit": {"edit", "update", "modify", "patch", "replace", "fix", "change", "修改", "编辑", "更新", "修复"},
    "delete": {"delete", "remove", "rm", "archive", "清理", "删除", "移除"},
    "search": {"search", "find", "lookup", "query", "grep", "检索", "搜索", "查找", "查询"},
    "browse": {"browser", "browse", "click", "navigate", "screenshot", "scroll", "网页", "浏览器", "点击", "截图"},
    "code": {"code", "symbol", "reference", "refactor", "test", "pytest", "coding", "代码", "函数", "引用", "测试"},
    "memory": {"memory", "remember", "recall", "profile", "记忆", "记住", "回忆"},
    "mail": {"mail", "email", "gmail", "calendar", "邮件", "日历"},
    "web": {"web", "url", "http", "crawl", "extract", "网页", "网址", "联网"},
    "media": {"image", "audio", "speech", "tts", "stt", "图片", "音频", "语音"},
    "process": {"terminal", "shell", "command", "process", "run", "命令", "终端", "运行"},
}


class ToolRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, ToolRegistryEntry] = {}
        self._built_in_names: set[str] = set()

    def register(self, entry: ToolRegistryEntry) -> ToolRegistryEntry:
        stored_entry = entry.model_copy(deep=True)

        if stored_entry.source_kind is ToolSourceKind.BUILTIN:
            if stored_entry.name in self._entries:
                raise ValueError(f"tool '{stored_entry.name}' is already registered")
            self._entries[stored_entry.name] = stored_entry
            self._built_in_names.add(stored_entry.name)
            return stored_entry

        if stored_entry.name in self._built_in_names:
            stored_entry.name = self._namespaced_name(stored_entry.source_id, stored_entry.name)
            stored_entry.capability_id = f"{stored_entry.source_kind.value}:{stored_entry.source_id}:{stored_entry.name}"

        if stored_entry.name in self._entries:
            raise ValueError(f"tool '{stored_entry.name}' is already registered")

        self._entries[stored_entry.name] = stored_entry
        return stored_entry

    def entries(self) -> tuple[ToolRegistryEntry, ...]:
        return tuple(self._entries[name].model_copy(deep=True) for name in sorted(self._entries))

    def record_skill_selection_feedback(self, feedback: SkillSelectionFeedback) -> CapabilityFeedbackDecision:
        matches = [
            entry
            for entry in self._entries.values()
            if entry.source_kind is ToolSourceKind.SKILL
            and feedback.skill_id in {entry.source_id, entry.name, str(entry.capability_id or "")}
        ]
        if not matches:
            return CapabilityFeedbackDecision(
                skill_id=feedback.skill_id,
                updated=False,
                last_outcome=feedback.outcome,
                diagnostics={"reason": "skill_capability_not_registered"},
            )

        updated_entries: list[ToolRegistryEntry] = []
        for entry in matches:
            stats = _updated_skill_feedback_stats(
                dict(entry.provenance.get("skill_selection_feedback") or {}),
                feedback,
            )
            updated_entry = entry.model_copy(
                update={
                    "provenance": {
                        **entry.provenance,
                        "skill_selection_feedback": stats,
                    }
                },
                deep=True,
            )
            self._entries[updated_entry.name] = updated_entry
            updated_entries.append(updated_entry)

        first_stats = updated_entries[0].provenance["skill_selection_feedback"]
        return CapabilityFeedbackDecision(
            skill_id=feedback.skill_id,
            capability_ids=tuple(str(entry.capability_id) for entry in updated_entries if entry.capability_id),
            updated=True,
            feedback_count=int(first_stats["feedback_count"]),
            success_count=int(first_stats["success_count"]),
            correction_count=int(first_stats["correction_count"]),
            utility_score=float(first_stats["utility_score"]),
            last_outcome=str(first_stats["last_outcome"]),
            diagnostics={"matched_entry_count": len(updated_entries)},
        )

    def build_bundle(
        self,
        *,
        effective_config_fingerprint: str,
        request_context: str | None = None,
        promoted_names: DeferredCapabilityPromotion | None = None,
        enabled_source_ids: set[str] | None = None,
        allowed_capability_groups: set[str] | None = None,
        allowed_tool_names: set[str] | None = None,
        enabled_skill_ids: tuple[str, ...] = (),
        effective_mcp_servers: tuple[str, ...] = (),
        effective_extension_sources: tuple[str, ...] = (),
        effective_plugin_ids: tuple[str, ...] = (),
        effective_app_ids: tuple[str, ...] = (),
        visible_schema_token_budget: int | None = None,
        action_prefilter: dict[str, Any] | None = None,
        always_visible_names: set[str] | None = None,
    ) -> CapabilityBundle:
        promoted = set(promoted_names.promoted_names if promoted_names is not None else ())
        discovered: list[ToolRegistryEntry] = []
        enabled: list[ToolRegistryEntry] = []
        materialized: list[ToolRegistryEntry] = []
        visible: list[ToolRegistryEntry] = []
        deferred: list[ToolRegistryEntry] = []

        for entry in self.entries():
            discovered.append(entry)
            if enabled_source_ids is not None and entry.source_id not in enabled_source_ids:
                continue
            if allowed_capability_groups is not None and entry.capability_group not in allowed_capability_groups:
                continue
            if allowed_tool_names is not None and entry.name not in allowed_tool_names:
                continue
            enabled.append(entry)
            if not entry.is_available():
                continue
            materialized.append(entry)

            if entry.deferred and entry.name not in promoted:
                deferred.append(entry)
            else:
                visible.append(entry)

        if action_prefilter and action_prefilter.get("enabled", True):
            visible, deferred = self._apply_action_prefilter(
                visible=visible,
                deferred=deferred,
                request_context=request_context,
                promoted=promoted,
                always_visible_names=always_visible_names or set(),
                min_tools=int(action_prefilter.get("min_tools") or 0),
                max_visible=int(action_prefilter.get("max_visible") or 0),
                min_score=float(action_prefilter.get("min_score") or 0.0),
            )

        if visible_schema_token_budget is not None and visible_schema_token_budget > 0:
            visible, deferred = self._apply_visible_schema_budget(
                visible=visible,
                deferred=deferred,
                promoted=promoted,
                budget=visible_schema_token_budget,
                always_visible_names=always_visible_names or set(),
            )

        prompt_safe_summaries = tuple(
            f"{entry.name}: {entry.summary or entry.display_name} [{CapabilityVisibility.VISIBLE.value}]"
            for entry in visible
        ) + tuple(
            f"{entry.name}: {entry.summary or entry.display_name} [{CapabilityVisibility.MATERIALIZED.value}]"
            for entry in deferred
        )

        fingerprint_payload = {
            "effective_config_fingerprint": effective_config_fingerprint,
            "discovered_names": [entry.name for entry in discovered],
            "enabled_names": [entry.name for entry in enabled],
            "materialized_names": [entry.name for entry in materialized],
            "visible_names": [entry.name for entry in visible],
            "deferred_names": [entry.name for entry in deferred],
            "allowed_tool_names": sorted(allowed_tool_names) if allowed_tool_names is not None else None,
            "enabled_skill_ids": list(enabled_skill_ids),
            "effective_mcp_servers": list(effective_mcp_servers),
            "effective_extension_sources": list(effective_extension_sources),
            "effective_plugin_ids": list(effective_plugin_ids),
            "effective_app_ids": list(effective_app_ids),
            "promoted_names": sorted(promoted),
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        catalog_fingerprint = hashlib.sha256(
            json.dumps(
                [
                    {
                        "capability_id": entry.capability_id,
                        "name": entry.name,
                        "source_kind": entry.source_kind.value,
                        "source_id": entry.source_id,
                        "deferred": entry.deferred,
                    }
                    for entry in materialized
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        context = CapabilityContext(
            fingerprint=fingerprint,
            visible_tool_names=tuple(entry.name for entry in visible),
            deferred_tool_names=tuple(entry.name for entry in deferred),
            enabled_skill_ids=enabled_skill_ids,
            effective_mcp_servers=effective_mcp_servers,
            effective_extension_sources=effective_extension_sources,
            effective_plugin_ids=effective_plugin_ids,
            effective_app_ids=effective_app_ids,
            active_promotions=tuple(sorted(promoted)),
            prompt_safe_summaries=prompt_safe_summaries,
        )
        assembly_diagnostics = _build_capability_assembly_diagnostics(
            discovered=discovered,
            enabled=enabled,
            materialized=materialized,
            visible=visible,
            deferred=deferred,
            promoted=promoted,
            visible_schema_token_budget=visible_schema_token_budget,
        )

        return CapabilityBundle(
            fingerprint=fingerprint,
            catalog_fingerprint=catalog_fingerprint,
            discovered_tools=tuple(discovered),
            enabled_tools=tuple(enabled),
            materialized_tools=tuple(materialized),
            visible_tools=tuple(visible),
            deferred_tools=tuple(deferred),
            enabled_skill_ids=enabled_skill_ids,
            effective_mcp_servers=effective_mcp_servers,
            effective_extension_sources=effective_extension_sources,
            effective_plugin_ids=effective_plugin_ids,
            effective_app_ids=effective_app_ids,
            prompt_safe_summaries=prompt_safe_summaries,
            assembly_diagnostics=assembly_diagnostics,
            capability_context=context,
        )

    def search(self, request: CapabilitySearchRequest) -> CapabilitySearchResult:
        query = request.query.lower().strip()
        normalized_query = self._search_text(query)
        query_terms = [term for term in normalized_query.replace(":", " ").split() if term]
        scored: list[tuple[float, ToolRegistryEntry, CapabilitySearchTrace]] = []

        for entry in self.entries():
            if not request.include_visible and not entry.deferred:
                continue
            if request.source_id is not None and entry.source_id != request.source_id:
                continue
            schema_text = json.dumps(entry.input_schema, ensure_ascii=False, sort_keys=True).lower()
            fields = {
                "name": (self._search_text(entry.name), 4.0),
                "display_name": (self._search_text(entry.display_name), 3.0),
                "capability_group": (self._search_text(entry.capability_group), 4.0),
                "source_id": (self._search_text(entry.source_id), 2.0),
                "summary": (self._search_text(entry.summary or ""), 2.5),
                "provenance": (self._search_text(json.dumps(entry.provenance, ensure_ascii=False)), 1.0),
                "schema": (self._search_text(schema_text), 1.2),
            }
            if query.startswith("select:"):
                selected = query.split(":", 1)[1].strip()
                if entry.name.lower() == selected:
                    scored.append(
                        (
                            100.0,
                            entry,
                            CapabilitySearchTrace(
                                score=100.0,
                                matched_fields=("name",),
                                query_terms=(selected,),
                            ),
                        )
                    )
                continue
            score = 0.0
            matched_fields: set[str] = set()
            matched_terms: set[str] = set()
            for field_name, (haystack, weight) in fields.items():
                if normalized_query and normalized_query in haystack:
                    score += weight * 2
                    matched_fields.add(field_name)
                for term in query_terms:
                    if term in haystack:
                        score += weight
                        matched_fields.add(field_name)
                        matched_terms.add(term)
            if score > 0:
                scored.append(
                    (
                        score,
                        entry,
                        CapabilitySearchTrace(
                            score=score,
                            matched_fields=tuple(sorted(matched_fields)),
                            query_terms=tuple(sorted(matched_terms or set(query_terms))),
                        ),
                    )
                )

        scored.sort(key=lambda item: item[0], reverse=True)
        selected = scored[: request.max_results]
        matches = [entry for _, entry, _ in selected]
        return CapabilitySearchResult(
            matches=tuple(matches),
            promotion=DeferredCapabilityPromotion(
                promoted_names=tuple(entry.name for entry in matches) if request.promote else (),
                query=request.query,
            ),
            total_matches=len(scored),
            match_traces={entry.name: trace for _, entry, trace in selected},
        )

    def _search_text(self, value: str) -> str:
        normalized = SEPARATOR_RE.sub(" ", value.lower())
        for source, target in {
            "generation": "generate",
            "generator": "generate",
            "generated": "generate",
            "generating": "generate",
        }.items():
            normalized = normalized.replace(source, target)
        return " ".join(normalized.split())

    def catalog_entries(self, bundle: CapabilityBundle | None = None) -> tuple[CapabilityCatalogEntry, ...]:
        visible_names = {entry.name for entry in bundle.visible_tools} if bundle is not None else set()
        deferred_names = {entry.name for entry in bundle.deferred_tools} if bundle is not None else set()
        enabled_names = {entry.name for entry in bundle.enabled_tools} if bundle is not None else set()
        materialized_names = {entry.name for entry in bundle.materialized_tools} if bundle is not None else set()

        items: list[CapabilityCatalogEntry] = []
        for entry in self.entries():
            if entry.name in visible_names:
                visibility = CapabilityVisibility.VISIBLE
            elif entry.name in deferred_names:
                visibility = CapabilityVisibility.MATERIALIZED
            elif entry.name in materialized_names:
                visibility = CapabilityVisibility.MATERIALIZED
            elif entry.name in enabled_names:
                visibility = CapabilityVisibility.ENABLED
            else:
                visibility = CapabilityVisibility.DISCOVERED
            items.append(
                CapabilityCatalogEntry(
                    capability_id=str(entry.capability_id),
                    name=entry.name,
                    display_name=entry.display_name,
                    summary=entry.summary or entry.display_name,
                    source_kind=entry.source_kind,
                    source_id=entry.source_id,
                    capability_group=entry.capability_group,
                    visibility=visibility,
                    deferred=entry.deferred,
                    stability=entry.stability,
                    risk_category=entry.risk_category,
                    approval=entry.typed_approval,
                    resources=entry.resources,
                    prompts=entry.prompts,
                    dependencies=entry.dependencies,
                    provenance=entry.provenance,
                    health=entry.health,
                )
            )
        return tuple(items)

    def capability_resources(self, bundle: CapabilityBundle | None = None) -> tuple[CapabilityResource, ...]:
        visible_names = {entry.name for entry in bundle.visible_tools} if bundle is not None else set()
        deferred_names = {entry.name for entry in bundle.deferred_tools} if bundle is not None else set()
        enabled_names = {entry.name for entry in bundle.enabled_tools} if bundle is not None else set()
        materialized_names = {entry.name for entry in bundle.materialized_tools} if bundle is not None else set()
        token_budget = TokenBudgetService()

        resources: list[CapabilityResource] = []
        for entry in self.entries():
            visibility_state, visibility_reason = _capability_visibility_state(
                entry=entry,
                visible_names=visible_names,
                deferred_names=deferred_names,
                materialized_names=materialized_names,
                enabled_names=enabled_names,
            )
            resources.append(
                CapabilityResource(
                    resource_id=str(entry.capability_id),
                    title=entry.display_name,
                    description=entry.summary or entry.display_name,
                    server_id=entry.source_id if entry.source_kind is ToolSourceKind.MCP else None,
                    name=entry.name,
                    kind=_capability_resource_kind(entry),
                    usage_scenarios=_text_tuple(entry.provenance.get("usage_scenarios")),
                    input_schema=entry.input_schema,
                    output_schema=_optional_mapping(entry.provenance.get("output_schema")),
                    examples=_text_tuple(entry.provenance.get("examples")),
                    preconditions=_text_tuple(entry.provenance.get("preconditions")),
                    side_effects=_text_tuple(entry.provenance.get("side_effects")),
                    risk_level=_capability_risk_level(entry),
                    latency_cost=_int_from_provenance(entry.provenance, "latency_cost"),
                    token_cost=_capability_token_cost(entry, token_budget=token_budget),
                    success_history=_capability_success_history(entry.provenance),
                    last_used_at=_optional_text(entry.provenance.get("last_used_at")),
                    related_memories=_text_tuple(entry.provenance.get("related_memories")),
                    related_skills=_text_tuple(entry.provenance.get("related_skills")),
                    graph_neighbors=_text_tuple(entry.provenance.get("graph_neighbors")),
                    source_ref=str(entry.capability_id),
                    visibility_state=visibility_state,
                    metadata={
                        "visibility_reason": visibility_reason,
                        "source_kind": entry.source_kind.value,
                        "source_id": entry.source_id,
                        "capability_group": entry.capability_group,
                        "deferred": entry.deferred,
                        "health_status": entry.health.status.value,
                        "risk_category": entry.risk_category,
                    },
                )
            )
        return tuple(resources)

    def hidden_capability_summary(
        self,
        bundle: CapabilityBundle | None = None,
        *,
        resources: tuple[CapabilityResource, ...] | None = None,
        max_examples: int = 6,
    ) -> HiddenCapabilitySummary:
        resource_items = resources if resources is not None else self.capability_resources(bundle)
        hidden = tuple(
            sorted(
                (resource for resource in resource_items if resource.visibility_state != "visible"),
                key=lambda resource: (_hidden_summary_priority(resource.visibility_state), resource.name),
            )
        )
        categories = tuple(
            sorted({f"{resource.kind}:{str(resource.metadata.get('capability_group') or 'general')}" for resource in hidden})
        )
        example_names = tuple(resource.name for resource in hidden[: max(max_examples, 0)])
        payload = {
            "categories": categories,
            "example_names": example_names,
            "omitted_count": len(hidden),
        }
        token_budget = TokenBudgetService()
        return HiddenCapabilitySummary(
            categories=categories,
            example_names=example_names,
            omitted_count=len(hidden),
            token_cost=token_budget.count_object(payload),
            metadata={
                "visible_count": len(tuple(resource for resource in resource_items if resource.visibility_state == "visible")),
                "deferred_count": len(tuple(resource for resource in hidden if resource.visibility_state == "deferred")),
                "hidden_count": len(tuple(resource for resource in hidden if resource.visibility_state in {"hidden", "disabled", "unhealthy"})),
            },
        )

    def _namespaced_name(self, source_id: str, name: str) -> str:
        return f"{source_id}__{name}"

    def _apply_visible_schema_budget(
        self,
        *,
        visible: list[ToolRegistryEntry],
        deferred: list[ToolRegistryEntry],
        promoted: set[str],
        budget: int,
        always_visible_names: set[str],
    ) -> tuple[list[ToolRegistryEntry], list[ToolRegistryEntry]]:
        token_budget = TokenBudgetService()
        visible = [
            self._compact_entry_schema_for_budget(entry=entry, token_budget=token_budget)
            for entry in visible
        ]
        total = sum(self._entry_schema_tokens(entry, token_budget) for entry in visible)
        if total <= budget:
            return visible, deferred
        kept: list[ToolRegistryEntry] = []
        moved: list[ToolRegistryEntry] = list(deferred)
        for entry in visible:
            if entry.name in promoted or entry.name in always_visible_names:
                kept.append(entry)
            else:
                before_tokens = self._entry_schema_tokens(entry, token_budget)
                moved.append(
                    entry.model_copy(
                        update={
                            "deferred": True,
                            "provenance": {
                                **entry.provenance,
                                "schema_budget": {
                                    **dict(entry.provenance.get("schema_budget") or {}),
                                    "status": "deferred_due_budget",
                                    "tokens_before": before_tokens,
                                    "tokens_after": before_tokens,
                                    "budget": budget,
                                },
                            },
                        }
                    )
                )
        return kept, moved

    def _apply_action_prefilter(
        self,
        *,
        visible: list[ToolRegistryEntry],
        deferred: list[ToolRegistryEntry],
        request_context: str | None,
        promoted: set[str],
        always_visible_names: set[str],
        min_tools: int,
        max_visible: int,
        min_score: float,
    ) -> tuple[list[ToolRegistryEntry], list[ToolRegistryEntry]]:
        if max_visible <= 0 or len(visible) <= max(min_tools, max_visible):
            return visible, deferred
        filterable = [
            entry
            for entry in visible
            if entry.name not in promoted
            and entry.name not in always_visible_names
            and entry.source_kind is not ToolSourceKind.BUILTIN
        ]
        if len(filterable) <= max_visible:
            return visible, deferred
        context = self._search_text(request_context or "")
        if not context:
            return visible, deferred
        query_terms = tuple(term for term in context.replace(":", " ").split() if term)
        if not query_terms:
            return visible, deferred
        active_actions = _active_action_terms(query_terms)
        fixed: list[ToolRegistryEntry] = []
        scored: list[tuple[float, int, ToolRegistryEntry]] = []
        moved: list[ToolRegistryEntry] = list(deferred)
        for index, entry in enumerate(visible):
            if entry.name in promoted or entry.name in always_visible_names:
                fixed.append(entry)
                continue
            score = self._action_prefilter_score(entry=entry, query_terms=query_terms, active_actions=active_actions)
            if score >= min_score:
                scored.append((score, index, entry))
            else:
                moved.append(_defer_for_action_filter(entry, score=score, max_visible=max_visible))
        remaining_slots = max(max_visible - len(fixed), 0)
        scored.sort(key=lambda item: (-item[0], item[1]))
        kept_scored = scored[:remaining_slots]
        dropped_scored = scored[remaining_slots:]
        kept = fixed + [entry for _, _, entry in sorted(kept_scored, key=lambda item: item[1])]
        moved.extend(
            _defer_for_action_filter(entry, score=score, max_visible=max_visible)
            for score, _, entry in dropped_scored
        )
        kept_order = {entry.name for entry in kept}
        return [entry for entry in visible if entry.name in kept_order], moved

    def _action_prefilter_score(
        self,
        *,
        entry: ToolRegistryEntry,
        query_terms: tuple[str, ...],
        active_actions: set[str],
    ) -> float:
        fields = {
            "name": self._search_text(entry.name),
            "display_name": self._search_text(entry.display_name),
            "capability_group": self._search_text(entry.capability_group),
            "summary": self._search_text(entry.summary or ""),
            "source_id": self._search_text(entry.source_id),
            "provenance": self._search_text(json.dumps(entry.provenance, ensure_ascii=False)),
            "schema": self._search_text(json.dumps(entry.input_schema, ensure_ascii=False, sort_keys=True)),
        }
        score = 0.0
        weights = {
            "name": 3.8,
            "display_name": 3.2,
            "capability_group": 2.8,
            "summary": 2.2,
            "source_id": 1.4,
            "provenance": 1.0,
            "schema": 0.8,
        }
        for field_name, haystack in fields.items():
            weight = weights[field_name]
            for term in query_terms:
                if term and term in haystack:
                    score += weight
            if active_actions and _entry_action_matches(entry, active_actions, haystack):
                score += weight * 1.5
        return score

    def _compact_entry_schema_for_budget(
        self,
        *,
        entry: ToolRegistryEntry,
        token_budget: TokenBudgetService,
    ) -> ToolRegistryEntry:
        if entry.source_kind is ToolSourceKind.BUILTIN:
            return entry
        before_tokens = self._entry_schema_tokens(entry, token_budget)
        compact_schema = self._compact_schema_value(entry.input_schema)
        if compact_schema == entry.input_schema:
            return entry
        after_tokens = token_budget.count_object(compact_schema)
        return entry.with_input_schema(
            compact_schema,
            schema_budget={
                "status": "compacted",
                "tokens_before": before_tokens,
                "tokens_after": after_tokens,
                "dropped_keys": sorted(SCHEMA_COMPACT_DROP_KEYS),
            },
        )

    def _compact_schema_value(self, value):
        if isinstance(value, dict):
            return {
                key: self._compact_schema_value(item)
                for key, item in value.items()
                if key not in SCHEMA_COMPACT_DROP_KEYS
            }
        if isinstance(value, list):
            return [self._compact_schema_value(item) for item in value]
        return value

    def _entry_schema_tokens(self, entry: ToolRegistryEntry, token_budget: TokenBudgetService) -> int:
        return token_budget.count_object(entry.input_schema)


class SkillSelectionFeedbackSubscriber:
    """RuntimeEventBus subscriber that feeds skill selection feedback into the registry."""

    def __init__(self, *, registry: ToolRegistry) -> None:
        self.registry = registry
        self.decisions: list[CapabilityFeedbackDecision] = []
        self._processed_feedback_ids: set[str] = set()
        self.diagnostics: dict[str, int] = {
            "seen_event_count": 0,
            "updated_event_count": 0,
            "skipped_duplicate_count": 0,
            "skipped_non_feedback_count": 0,
        }

    def __call__(self, event: object) -> CapabilityFeedbackDecision | None:
        self.diagnostics["seen_event_count"] += 1
        feedback = skill_selection_feedback_from_runtime_event(event)
        if feedback is None:
            self.diagnostics["skipped_non_feedback_count"] += 1
            return None
        feedback_id = _skill_feedback_id(event, feedback)
        if feedback_id in self._processed_feedback_ids:
            self.diagnostics["skipped_duplicate_count"] += 1
            return None

        decision = self.registry.record_skill_selection_feedback(feedback)
        self._processed_feedback_ids.add(feedback_id)
        self.decisions.append(decision)
        self.diagnostics["updated_event_count"] += int(decision.updated)
        _record_skill_feedback_decision(event, decision=decision, feedback_id=feedback_id)
        return decision


def skill_retrieval_plan_to_capability_resources(plan: object) -> tuple[CapabilityResource, ...]:
    token_budget = TokenBudgetService()
    resources: list[CapabilityResource] = []
    for candidate in tuple(getattr(plan, "candidates", ()) or ()):
        candidate_id = str(getattr(candidate, "skill_id", "") or "").strip()
        if not candidate_id:
            continue
        selected = bool(getattr(candidate, "selected", False))
        metadata = dict(getattr(candidate, "metadata", {}) or {})
        feedback = _optional_mapping(metadata.get("feedback")) or {}
        tier_scores = dict(getattr(candidate, "tier_scores", {}) or {})
        matched_terms = _text_tuple(getattr(candidate, "matched_terms", ()))
        matched_fields = _text_tuple(getattr(candidate, "matched_fields", ()))
        graph_neighbors = _text_tuple(getattr(candidate, "graph_neighbors", ()))
        source_ref = str(getattr(candidate, "source_ref", "") or f"skill://{candidate_id}")
        visibility_state = "visible" if selected else "hidden"
        visibility_reason = "skill_retrieval_top_k" if selected else "skill_retrieval_not_selected"
        payload = {
            "skill_id": candidate_id,
            "title": getattr(candidate, "title", ""),
            "summary": getattr(candidate, "summary", ""),
            "tier_scores": tier_scores,
            "matched_terms": matched_terms,
        }
        resources.append(
            CapabilityResource(
                resource_id=f"skill:{candidate_id}",
                title=str(getattr(candidate, "title", "") or candidate_id),
                description=str(getattr(candidate, "summary", "") or candidate_id),
                name=candidate_id,
                kind="skill",
                usage_scenarios=tuple(
                    item
                    for item in (
                        f"query:{getattr(plan, 'query', '')}".strip(),
                        f"matched_terms:{', '.join(matched_terms)}" if matched_terms else "",
                    )
                    if item
                ),
                preconditions=_text_tuple(metadata.get("input_requirements")),
                risk_level=str(metadata.get("risk_level") or "normal"),
                token_cost=max(int(getattr(candidate, "token_cost", 0) or 0), token_budget.count_object(payload)),
                success_history=_capability_success_history({"skill_selection_feedback": feedback}),
                related_skills=tuple(item for item in graph_neighbors if item != candidate_id),
                graph_neighbors=graph_neighbors,
                source_ref=source_ref,
                visibility_state=visibility_state,
                metadata={
                    "visibility_reason": visibility_reason,
                    "source_kind": "skill",
                    "source_id": candidate_id,
                    "capability_group": "skills",
                    "risk_category": metadata.get("risk_level") or "normal",
                    "selection_rank": getattr(candidate, "selection_rank", None),
                    "selected": selected,
                    "matched_terms": matched_terms,
                    "matched_fields": matched_fields,
                    "loaded_full_skill_content": bool(metadata.get("loaded_full_skill_content", False)),
                    "skill_retrieval": {
                        "query": str(getattr(plan, "query", "") or ""),
                        "top_k": int(getattr(plan, "top_k", 0) or 0),
                        "tiers_used": _text_tuple(getattr(plan, "tiers_used", ())),
                        "tier_scores": tier_scores,
                        "fusion_score": float(getattr(candidate, "fusion_score", 0.0) or 0.0),
                        "selection_rank": getattr(candidate, "selection_rank", None),
                        "matched_terms": matched_terms,
                        "matched_fields": matched_fields,
                        "embedding_mode": _optional_mapping(getattr(plan, "diagnostics", {})).get("embedding_mode")
                        if _optional_mapping(getattr(plan, "diagnostics", {}))
                        else None,
                    },
                    "skill_metadata": {
                        "domain": metadata.get("domain"),
                        "task_type": metadata.get("task_type"),
                        "tags": _text_tuple(metadata.get("tags")),
                        "allowed_tools": _text_tuple(metadata.get("allowed_tools")),
                        "trust": metadata.get("trust"),
                        "version": metadata.get("version"),
                        "readiness": metadata.get("readiness"),
                    },
                },
            )
        )
    return tuple(resources)


def skill_selection_feedback_from_runtime_event(event: object) -> SkillSelectionFeedback | None:
    metadata_value = _get_event_value(event, "metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    event_type = _optional_text(_get_event_value(event, "event_type") or _get_event_value(event, "type") or metadata.get("event_type"))
    source_kind = _optional_text(_get_event_value(event, "source_kind") or metadata.get("source_kind"))
    event_type_lower = str(event_type or "").lower()
    source_kind_lower = str(source_kind or "").lower()
    if event_type_lower not in {
        "skill_selection_feedback",
        "skill_feedback",
        "capability_feedback",
        "capability_selection_feedback",
    }:
        return None
    if source_kind_lower and source_kind_lower not in {"skill", "capability", "runtime"}:
        return None

    skill_id = _optional_text(
        metadata.get("skill_id")
        or metadata.get("skill_name")
        or metadata.get("capability_id")
        or _get_event_value(event, "skill_id")
        or _get_event_value(event, "source_ref")
    )
    turn_id = _optional_text(metadata.get("turn_id") or _get_event_value(event, "turn_id"))
    if not skill_id or not turn_id:
        return None

    return SkillSelectionFeedback(
        skill_id=skill_id,
        turn_id=turn_id,
        selected=_truthy(metadata.get("selected") if "selected" in metadata else _get_event_value(event, "selected")),
        injected=_truthy(metadata.get("injected") if "injected" in metadata else _get_event_value(event, "injected")),
        used_by_llm=_truthy(metadata.get("used_by_llm") if "used_by_llm" in metadata else _get_event_value(event, "used_by_llm")),
        outcome=_optional_text(metadata.get("outcome") or _get_event_value(event, "outcome")) or "unknown",
        user_correction=_truthy(
            metadata.get("user_correction") if "user_correction" in metadata else _get_event_value(event, "user_correction")
        ),
        latency_ms=_optional_int(metadata.get("latency_ms") or _get_event_value(event, "latency_ms")),
        context_block_refs=tuple(_string_list(metadata.get("context_block_refs") or _get_event_value(event, "context_block_refs"))),
    )


def _capability_visibility_state(
    *,
    entry: ToolRegistryEntry,
    visible_names: set[str],
    deferred_names: set[str],
    materialized_names: set[str],
    enabled_names: set[str],
) -> tuple[str, str]:
    if entry.name in visible_names:
        return "visible", "selected_top_k_or_promoted"
    if entry.health.status in {CapabilityHealthStatus.DEGRADED, CapabilityHealthStatus.FAILED} or not entry.is_available():
        return "unhealthy", "unavailable_or_unhealthy"
    if entry.name in deferred_names:
        return "deferred", "deferred_by_policy_or_budget"
    if entry.name in materialized_names:
        return "hidden", "materialized_but_not_visible"
    if entry.name in enabled_names:
        return "hidden", "enabled_but_not_materialized"
    return "hidden", "not_enabled_or_filtered"


def _capability_resource_kind(entry: ToolRegistryEntry) -> str:
    if entry.source_kind is ToolSourceKind.SKILL:
        return "skill"
    if entry.source_kind is ToolSourceKind.MCP:
        return "mcp"
    if entry.source_kind is ToolSourceKind.BUILTIN:
        return "tool"
    if entry.source_kind in {ToolSourceKind.EXTENSION, ToolSourceKind.PLUGIN, ToolSourceKind.FUTURE_APP}:
        return "service"
    return "tool"


def _capability_risk_level(entry: ToolRegistryEntry) -> str:
    return entry.risk_category or (entry.typed_approval.risk_category if entry.typed_approval else None) or "normal"


def _capability_token_cost(entry: ToolRegistryEntry, *, token_budget: TokenBudgetService) -> int:
    payload = {
        "name": entry.name,
        "summary": entry.summary or entry.display_name,
        "schema": entry.input_schema,
    }
    return token_budget.count_object(payload)


def _capability_success_history(provenance: dict[str, Any]) -> CapabilitySuccessHistory:
    stats = provenance.get("skill_selection_feedback") or provenance.get("capability_success_history") or {}
    if not isinstance(stats, dict):
        stats = {}
    usage_count = _int_from_mapping(stats, "feedback_count", fallback_key="usage_count")
    success_count = _int_from_mapping(stats, "success_count")
    failure_count = _int_from_mapping(stats, "failure_count")
    correction_count = _int_from_mapping(stats, "correction_count", fallback_key="user_correction_count")
    recent_success_rate = _float_from_mapping(stats, "recent_success_rate")
    if recent_success_rate is None:
        recent_success_rate = round(success_count / usage_count, 4) if usage_count else _float_from_mapping(stats, "utility_score") or 0.0
    average_latency_ms = _optional_int(stats.get("average_latency_ms"))
    return CapabilitySuccessHistory(
        usage_count=usage_count,
        success_count=success_count,
        failure_count=failure_count,
        user_correction_count=correction_count,
        recent_success_rate=recent_success_rate,
        average_latency_ms=average_latency_ms,
    )


def _hidden_summary_priority(visibility_state: str) -> int:
    return {
        "deferred": 0,
        "hidden": 1,
        "unhealthy": 2,
        "disabled": 3,
    }.get(visibility_state, 4)


def _optional_mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _text_tuple(value: Any) -> tuple[str, ...]:
    return tuple(item[:240] for item in _string_list(value)[:24])


def _int_from_provenance(provenance: dict[str, Any], key: str) -> int:
    return _int_from_mapping(provenance, key)


def _int_from_mapping(mapping: dict[str, Any], key: str, *, fallback_key: str | None = None) -> int:
    for candidate_key in (key, fallback_key):
        if candidate_key is None:
            continue
        value = mapping.get(candidate_key)
        if value is None:
            continue
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            continue
    return 0


def _float_from_mapping(mapping: dict[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _active_action_terms(query_terms: tuple[str, ...]) -> set[str]:
    active: set[str] = set()
    for action, terms in ACTION_VERB_TERMS.items():
        if any(term in terms for term in query_terms):
            active.add(action)
    return active


def _entry_action_matches(entry: ToolRegistryEntry, active_actions: set[str], haystack: str) -> bool:
    if entry.capability_group in active_actions:
        return True
    group_aliases = {
        "filesystem": {"read", "write", "edit", "delete", "search"},
        "coding": {"code", "search", "read", "edit"},
        "browser": {"browse", "web"},
        "web": {"web", "search", "browse"},
        "google_workspace": {"mail"},
        "memory": {"memory", "search"},
        "media": {"media"},
        "execution": {"process", "write", "edit"},
        "process": {"process"},
        "document": {"read", "write"},
        "document_generation": {"write"},
    }
    if active_actions & group_aliases.get(entry.capability_group, set()):
        return True
    for action in active_actions:
        if any(term in haystack for term in ACTION_VERB_TERMS[action]):
            return True
    return False


def _defer_for_action_filter(entry: ToolRegistryEntry, *, score: float, max_visible: int) -> ToolRegistryEntry:
    return entry.model_copy(
        update={
            "deferred": True,
            "provenance": {
                **entry.provenance,
                "action_prefilter": {
                    "status": "deferred_due_low_task_relevance",
                    "score": round(score, 4),
                    "max_visible": max_visible,
                },
            },
        }
    )


def _updated_skill_feedback_stats(
    existing: dict[str, Any],
    feedback: SkillSelectionFeedback,
) -> dict[str, Any]:
    feedback_count = _int_stat(existing, "feedback_count") + 1
    selected_count = _int_stat(existing, "selected_count") + int(feedback.selected)
    injected_count = _int_stat(existing, "injected_count") + int(feedback.injected)
    used_by_llm_count = _int_stat(existing, "used_by_llm_count") + int(feedback.used_by_llm)
    correction_count = _int_stat(existing, "correction_count") + int(feedback.user_correction)
    success_count = _int_stat(existing, "success_count") + int(_feedback_success(feedback.outcome))
    failure_count = _int_stat(existing, "failure_count") + int(_feedback_failure(feedback.outcome))
    total_latency_ms = _int_stat(existing, "total_latency_ms") + int(feedback.latency_ms or 0)
    utility_score = _feedback_utility_score(
        feedback_count=feedback_count,
        selected_count=selected_count,
        injected_count=injected_count,
        used_by_llm_count=used_by_llm_count,
        success_count=success_count,
        failure_count=failure_count,
        correction_count=correction_count,
    )
    return {
        "feedback_count": feedback_count,
        "selected_count": selected_count,
        "injected_count": injected_count,
        "used_by_llm_count": used_by_llm_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "correction_count": correction_count,
        "total_latency_ms": total_latency_ms,
        "average_latency_ms": round(total_latency_ms / feedback_count, 2) if feedback_count else 0.0,
        "last_turn_id": feedback.turn_id,
        "last_outcome": feedback.outcome,
        "last_context_block_refs": list(feedback.context_block_refs[:8]),
        "utility_score": utility_score,
    }


def _feedback_utility_score(
    *,
    feedback_count: int,
    selected_count: int,
    injected_count: int,
    used_by_llm_count: int,
    success_count: int,
    failure_count: int,
    correction_count: int,
) -> float:
    denominator = max(feedback_count, 1)
    score = (
        (success_count / denominator) * 0.55
        + (used_by_llm_count / denominator) * 0.25
        + (injected_count / denominator) * 0.1
        + (selected_count / denominator) * 0.1
        - (failure_count / denominator) * 0.25
        - (correction_count / denominator) * 0.35
    )
    return round(min(max(score, 0.0), 1.0), 4)


def _feedback_success(outcome: str) -> bool:
    return str(outcome or "").strip().lower() in {"success", "succeeded", "passed", "useful", "accepted"}


def _feedback_failure(outcome: str) -> bool:
    return str(outcome or "").strip().lower() in {"failure", "failed", "error", "rejected", "regression"}


def _get_event_value(event: object, key: str) -> Any:
    if isinstance(event, dict):
        return event.get(key)
    return getattr(event, key, None)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "selected", "injected", "used", "success"}


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _skill_feedback_id(event: object, feedback: SkillSelectionFeedback) -> str:
    event_id = _optional_text(_get_event_value(event, "event_id"))
    if event_id:
        return event_id
    return f"{feedback.turn_id}:{feedback.skill_id}:{feedback.outcome}"


def _record_skill_feedback_decision(
    event: object,
    *,
    decision: CapabilityFeedbackDecision,
    feedback_id: str,
) -> None:
    metadata = _get_event_value(event, "metadata")
    if not isinstance(metadata, dict):
        return
    metadata["capability_feedback_decision"] = {
        "feedback_id": feedback_id,
        "skill_id": decision.skill_id,
        "capability_ids": list(decision.capability_ids),
        "updated": decision.updated,
        "feedback_count": decision.feedback_count,
        "success_count": decision.success_count,
        "correction_count": decision.correction_count,
        "utility_score": decision.utility_score,
        "last_outcome": decision.last_outcome,
        "diagnostics": dict(decision.diagnostics),
    }


def _int_stat(stats: dict[str, Any], key: str) -> int:
    try:
        return max(int(stats.get(key) or 0), 0)
    except (TypeError, ValueError):
        return 0


def _build_capability_assembly_diagnostics(
    *,
    discovered: list[ToolRegistryEntry],
    enabled: list[ToolRegistryEntry],
    materialized: list[ToolRegistryEntry],
    visible: list[ToolRegistryEntry],
    deferred: list[ToolRegistryEntry],
    promoted: set[str],
    visible_schema_token_budget: int | None,
) -> CapabilityAssemblyDiagnostics:
    token_budget = TokenBudgetService()
    visible_schema_tokens = sum(token_budget.count_object(entry.input_schema) for entry in visible)
    deferred_schema_tokens = sum(token_budget.count_object(entry.input_schema) for entry in deferred)
    budget_remaining = (
        max(int(visible_schema_token_budget) - visible_schema_tokens, 0)
        if visible_schema_token_budget is not None and visible_schema_token_budget > 0
        else None
    )
    all_entries = [*visible, *deferred]
    return CapabilityAssemblyDiagnostics(
        discovered_tool_count=len(discovered),
        enabled_tool_count=len(enabled),
        materialized_tool_count=len(materialized),
        visible_tool_count=len(visible),
        deferred_tool_count=len(deferred),
        active_promotion_count=len(promoted),
        visible_schema_token_budget=visible_schema_token_budget,
        visible_schema_tokens=visible_schema_tokens,
        deferred_schema_tokens=deferred_schema_tokens,
        total_schema_tokens=visible_schema_tokens + deferred_schema_tokens,
        visible_schema_budget_remaining_tokens=budget_remaining,
        schema_compacted_tool_count=sum(
            1
            for entry in all_entries
            if isinstance(entry.provenance.get("schema_budget"), dict)
            and entry.provenance["schema_budget"].get("status") == "compacted"
        ),
        schema_deferred_tool_count=sum(
            1
            for entry in deferred
            if isinstance(entry.provenance.get("schema_budget"), dict)
            and entry.provenance["schema_budget"].get("status") == "deferred_due_budget"
        ),
        action_prefilter_deferred_tool_count=sum(
            1
            for entry in deferred
            if isinstance(entry.provenance.get("action_prefilter"), dict)
            and entry.provenance["action_prefilter"].get("status") == "deferred_due_low_task_relevance"
        ),
        sanitizer_truncated_tool_count=sum(
            1
            for entry in all_entries
            if isinstance(entry.provenance.get("schema_sanitizer"), dict)
            and entry.provenance["schema_sanitizer"].get("truncated") is True
        ),
        visible_by_source_kind=_count_by_source_kind(visible),
        deferred_by_source_kind=_count_by_source_kind(deferred),
        visible_by_group=_count_by_group(visible),
        deferred_by_group=_count_by_group(deferred),
    )


def _count_by_source_kind(entries: list[ToolRegistryEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        key = entry.source_kind.value
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _count_by_group(entries: list[ToolRegistryEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        key = str(entry.capability_group)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
