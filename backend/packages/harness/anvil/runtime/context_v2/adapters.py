from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

from anvil.runtime.token_budget import TokenBudgetService

from .contracts import (
    CompressionPolicy,
    ContextBlock,
    ContextSource,
    ContextSourceKind,
    EvidenceRef,
    InjectionPolicy,
    stable_context_id,
)


def prompt_snapshot_to_blocks(prompt_snapshot: Any, *, token_budget: TokenBudgetService | None = None) -> list[ContextBlock]:
    counter = token_budget or TokenBudgetService()
    snapshot_key = getattr(prompt_snapshot, "snapshot_key", None)
    memory_namespace = getattr(snapshot_key, "memory_namespace", None)
    memory_fingerprint = getattr(snapshot_key, "memory_snapshot_fingerprint", None)
    blocks: list[ContextBlock] = []
    for section in getattr(prompt_snapshot, "stable_sections", ()) or ():
        if getattr(section, "name", None) == "memory_snapshot":
            blocks.append(
                stable_memory_snapshot_section_to_block(
                    section,
                    namespace=memory_namespace,
                    fingerprint=memory_fingerprint,
                    token_budget=counter,
                )
            )
            continue
        blocks.append(prompt_section_to_block(section, stable=True, token_budget=counter))
    return blocks


def prompt_injection_view_to_blocks(
    prompt_injection_view: Any,
    *,
    namespace: str | None = None,
    token_budget: TokenBudgetService | None = None,
) -> list[ContextBlock]:
    counter = token_budget or TokenBudgetService()
    blocks: list[ContextBlock] = []
    for section in prompt_injection_view.sections():
        if section.name == "promoted_capabilities":
            source = ContextSource(kind=ContextSourceKind.CAPABILITY, name="promoted_capabilities")
            block_type = "capability"
            position_hint = "capability:promoted"
        else:
            source = ContextSource(kind=ContextSourceKind.PROMPT, name=section.name)
            block_type = "prompt"
            position_hint = f"volatile:{section.name}"
        blocks.append(
            prompt_section_to_block(
                section,
                stable=False,
                token_budget=counter,
                block_type=block_type,
                source=source,
                position_hint=position_hint,
            )
        )
    return blocks


def prompt_section_to_block(
    section: Any,
    *,
    stable: bool,
    token_budget: TokenBudgetService | None = None,
    block_type: str = "prompt",
    source: ContextSource | None = None,
    priority: float | None = None,
    salience: float | None = None,
    confidence: float = 0.9,
    position_hint: str | None = None,
    evidence_refs: tuple[EvidenceRef, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    name = str(getattr(section, "name", "") or "section")
    content = str(getattr(section, "content", "") or "")
    rendered = section.render() if hasattr(section, "render") else content
    return ContextBlock(
        block_id=stable_context_id("prompt", "stable" if stable else "volatile", name, content),
        block_type=block_type,
        source=source or ContextSource(kind=ContextSourceKind.PROMPT, name=name),
        title=name,
        content=content,
        token_cost=counter.count_text(rendered),
        priority=priority if priority is not None else (1.0 if stable else 0.7),
        salience=salience if salience is not None else (0.9 if stable else 0.65),
        confidence=confidence,
        position_hint=position_hint or f"{'stable' if stable else 'volatile'}:{name}",
        evidence_refs=evidence_refs,
        privacy_level="internal",
        injection_policy=InjectionPolicy(protected=stable),
        compression_policy=CompressionPolicy(allow_compression=not stable, allow_reference=not stable),
        metadata={"section_name": name, **dict(metadata or {})},
    )


def stable_memory_snapshot_section_to_block(
    section: Any,
    *,
    namespace: str | None = None,
    fingerprint: str | None = None,
    token_budget: TokenBudgetService | None = None,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    content = str(getattr(section, "content", "") or "")
    rendered = section.render() if hasattr(section, "render") else content
    memory_ref = fingerprint or stable_context_id("memory-snapshot-content", namespace or "default", content)
    block_id = stable_context_id("memory-snapshot", namespace or "default", memory_ref)
    evidence = (
        EvidenceRef(
            ref_id=block_id,
            source_kind="memory_snapshot",
            source_id=memory_ref,
            confidence=0.65,
            metadata={"namespace": namespace or "default"},
        ),
    )
    return ContextBlock(
        block_id=block_id,
        block_type="memory",
        source=ContextSource(
            kind=ContextSourceKind.MEMORY,
            name=namespace or "stable_memory_snapshot",
            ref=memory_ref,
        ),
        title="memory_snapshot",
        content=content,
        token_cost=counter.count_text(rendered),
        priority=0.74,
        salience=0.7,
        confidence=0.65,
        position_hint="memory:stable_snapshot",
        evidence_refs=evidence,
        privacy_level="internal",
        injection_policy=InjectionPolicy(protected=False),
        compression_policy=CompressionPolicy(
            allow_compression=True,
            allow_reference=True,
            ref=f"memory_snapshot:{memory_ref}",
            summary="Stable memory snapshot is available by reference.",
        ),
        metadata={
            "section_name": "memory_snapshot",
            "stable_section": True,
            "legacy_section": "memory_snapshot",
            "namespace": namespace or "default",
            "memory_snapshot_fingerprint": fingerprint,
            "memory_id": block_id,
        },
    )


def memory_injection_view_to_blocks(
    injection_view: Any,
    *,
    token_budget: TokenBudgetService | None = None,
    query: str = "",
) -> list[ContextBlock]:
    from anvil.memory.hcms_v2 import memory_injection_view_v2_from_legacy, memory_injection_view_v2_to_blocks

    counter = token_budget or TokenBudgetService()
    hcms_v2_view = memory_injection_view_v2_from_legacy(
        injection_view,
        query=query,
        token_budget=counter,
    )
    return memory_injection_view_v2_to_blocks(hcms_v2_view, token_budget=counter)


def tool_registry_entry_to_block(
    entry: Any,
    *,
    visible: bool = True,
    token_budget: TokenBudgetService | None = None,
    selection_metadata: Mapping[str, Any] | None = None,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    name = str(getattr(entry, "name", "") or "tool")
    source_kind = _entry_source_kind(entry)
    source_id = str(getattr(entry, "source_id", "") or "runtime")
    source = ContextSource(
        kind=_capability_context_source_kind(source_kind),
        name=name,
        ref=str(getattr(entry, "capability_id", "") or ""),
        trust_level="trusted" if visible else "deferred",
        metadata={"source_kind": source_kind, "source_id": source_id},
    )
    summary = str(getattr(entry, "summary", None) or getattr(entry, "display_name", None) or name)
    content = f"{name}: {summary}"
    risk_category = getattr(entry, "risk_category", None)
    if risk_category:
        content += f"\nrisk={risk_category}"
    selection = dict(selection_metadata or {})
    relevance_score = float(selection.get("capability_relevance_score") or 0.0)
    relevance_boost = min(max(relevance_score, 0.0) * 0.025, 0.16)
    return ContextBlock(
        block_id=stable_context_id("capability", source_kind, source_id, name),
        block_type="capability",
        source=source,
        title=name,
        content=content,
        token_cost=counter.count_text(content),
        priority=min((0.72 if visible else 0.35) + relevance_boost, 0.92),
        salience=min((0.7 if visible else 0.35) + relevance_boost, 0.9),
        confidence=0.85 if visible else 0.6,
        position_hint="capability:visible" if visible else "capability:hidden",
        tags=tuple(filter(None, (source_kind, str(getattr(entry, "capability_group", "") or "")))),
        metadata={
            "tool_name": name,
            "capability_id": str(getattr(entry, "capability_id", "") or ""),
            "source_kind": source_kind,
            "source_id": source_id,
            "risk_category": risk_category,
            "deferred": bool(getattr(entry, "deferred", False)),
            **selection,
        },
    )


def capability_resource_to_block(
    resource: Any,
    *,
    token_budget: TokenBudgetService | None = None,
    max_description_tokens: int = 56,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    name = str(getattr(resource, "name", "") or getattr(resource, "title", "") or "capability")
    title = str(getattr(resource, "title", "") or name)
    kind = str(getattr(resource, "kind", "") or "tool")
    source_ref = str(
        getattr(resource, "source_ref", "")
        or getattr(resource, "resource_id", "")
        or getattr(resource, "id", "")
        or name
    )
    metadata = dict(getattr(resource, "metadata", {}) or {})
    source_kind = str(metadata.get("source_kind") or kind or "tool")
    source_id = str(metadata.get("source_id") or getattr(resource, "server_id", "") or "runtime")
    visibility_state = str(getattr(resource, "visibility_state", "") or "discovered")
    visibility_reason = str(metadata.get("visibility_reason") or "")
    risk_level = str(getattr(resource, "risk_level", "") or metadata.get("risk_category") or "normal")
    success_history = _capability_success_history_payload(getattr(resource, "success_history", None))
    description = counter.truncate_text(
        str(getattr(resource, "description", "") or title),
        max_tokens=max_description_tokens,
    )

    lines = [
        f"capability={name}",
        f"title={title}",
        f"kind={kind}",
        f"visibility={visibility_state}",
        f"source={source_kind}:{source_id}",
        f"risk={risk_level}",
    ]
    if description:
        lines.extend(("summary:", description))
    usage_scenarios = _string_tuple(getattr(resource, "usage_scenarios", ()), limit=3)
    examples = _string_tuple(getattr(resource, "examples", ()), limit=2)
    side_effects = _string_tuple(getattr(resource, "side_effects", ()), limit=2)
    preconditions = _string_tuple(getattr(resource, "preconditions", ()), limit=2)
    if usage_scenarios:
        lines.append("usage_scenarios=" + ", ".join(usage_scenarios))
    if examples:
        lines.append("examples=" + ", ".join(examples))
    if preconditions:
        lines.append("preconditions=" + ", ".join(preconditions))
    if side_effects:
        lines.append("side_effects=" + ", ".join(side_effects))
    if success_history.get("usage_count"):
        lines.append(
            "success_history="
            f"usage:{success_history['usage_count']},"
            f"success_rate:{success_history['recent_success_rate']}"
        )
    if visibility_reason:
        lines.append(f"visibility_reason={visibility_reason}")
    content = "\n".join(lines)

    priority, salience, confidence = _capability_resource_scores(visibility_state)
    injection_allowed = visibility_state not in {"hidden", "unhealthy", "disabled"}
    declared_token_cost = int(getattr(resource, "token_cost", 0) or 0)
    context_token_cost = max(counter.count_text(content), 1)
    evidence = (
        EvidenceRef(
            ref_id=stable_context_id("capability-resource-evidence", source_ref, name),
            source_kind="capability_resource",
            source_id=name,
            confidence=confidence,
            metadata={
                "source_ref": source_ref,
                "visibility_state": visibility_state,
                "source_kind": source_kind,
            },
        ),
    )
    return ContextBlock(
        block_id=stable_context_id("capability-resource", source_kind, source_ref, name, visibility_state),
        block_type="capability",
        source=ContextSource(
            kind=_capability_resource_context_source_kind(kind=kind, source_kind=source_kind),
            name=name,
            ref=source_ref,
            trust_level=_capability_resource_trust_level(visibility_state),
            metadata={
                "source_kind": source_kind,
                "source_id": source_id,
                "visibility_state": visibility_state,
            },
        ),
        title=title,
        content=content,
        token_cost=context_token_cost,
        priority=priority,
        salience=salience,
        confidence=confidence,
        position_hint="capability:visible" if visibility_state == "visible" else "capability:hidden",
        evidence_refs=evidence,
        privacy_level="internal",
        injection_policy=InjectionPolicy(
            allow=injection_allowed,
            reason=None if injection_allowed else "capability_hidden_or_unavailable",
        ),
        compression_policy=CompressionPolicy(
            allow_compression=True,
            allow_reference=True,
            min_tokens=18,
            summary=f"{name} capability metadata is available by reference.",
            ref=f"capability:{kind}:{name}",
        ),
        tags=tuple(filter(None, ("capability_resource", kind, source_kind, visibility_state))),
        metadata={
            "tool_name": name,
            "capability_name": name,
            "capability_id": source_ref,
            "capability_resource_id": str(getattr(resource, "resource_id", "") or source_ref),
            "capability_kind": kind,
            "resource_kind": kind,
            "source_kind": source_kind,
            "source_id": source_id,
            "capability_group": metadata.get("capability_group"),
            "visibility_state": visibility_state,
            "visibility_reason": visibility_reason or None,
            "risk_level": risk_level,
            "risk_category": metadata.get("risk_category") or risk_level,
            "latency_cost": int(getattr(resource, "latency_cost", 0) or 0),
            "declared_token_cost": declared_token_cost,
            "success_history": success_history,
            "last_used_at": getattr(resource, "last_used_at", None),
            "related_memories": _string_tuple(getattr(resource, "related_memories", ()), limit=20),
            "related_skills": _string_tuple(getattr(resource, "related_skills", ()), limit=20),
            "graph_neighbors": _string_tuple(getattr(resource, "graph_neighbors", ()), limit=20),
            "example_count": len(tuple(getattr(resource, "examples", ()) or ())),
            "has_input_schema": bool(getattr(resource, "input_schema", None)),
            "has_output_schema": bool(getattr(resource, "output_schema", None)),
            "input_schema_property_count": _schema_property_count(getattr(resource, "input_schema", None)),
            "skill_retrieval": metadata.get("skill_retrieval"),
            "skill_metadata": metadata.get("skill_metadata"),
            "matched_terms": metadata.get("matched_terms"),
            "matched_fields": metadata.get("matched_fields"),
            "selection_rank": metadata.get("selection_rank"),
        },
    )


def hidden_capability_summary_to_block(
    summary: Any,
    *,
    token_budget: TokenBudgetService | None = None,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    categories = _string_tuple(getattr(summary, "categories", ()), limit=20)
    example_names = _string_tuple(getattr(summary, "example_names", ()), limit=12)
    omitted_count = int(getattr(summary, "omitted_count", 0) or 0)
    request_hint = str(
        getattr(summary, "request_hint", "")
        or "Use capability_search to request a hidden or deferred capability by name or task."
    )
    lines = [f"hidden_capability_count={omitted_count}"]
    if categories:
        lines.append("categories:")
        lines.extend(f"- {category}" for category in categories)
    if example_names:
        lines.append("examples:")
        lines.extend(f"- {name}" for name in example_names)
    lines.append(f"request_hint={request_hint}")
    content = "\n".join(lines)
    metadata = dict(getattr(summary, "metadata", {}) or {})
    return ContextBlock(
        block_id=stable_context_id("capability-hidden-summary-v2", categories, example_names, omitted_count),
        block_type="capability",
        source=ContextSource(
            kind=ContextSourceKind.CAPABILITY,
            name="hidden_capability_summary",
            ref="capability:hidden_summary",
            trust_level="deferred",
            metadata={"source_kind": "capability_summary"},
        ),
        title="HiddenCapabilitySummary",
        content=content,
        token_cost=max(counter.count_text(content), int(getattr(summary, "token_cost", 0) or 0), 1),
        priority=0.46,
        salience=0.48,
        confidence=0.75,
        position_hint="capability:hidden_summary",
        evidence_refs=(
            EvidenceRef(
                ref_id=stable_context_id("hidden-capability-summary-evidence", content),
                source_kind="hidden_capability_summary",
                source_id="capability_registry",
                confidence=0.75,
                metadata={"omitted_count": omitted_count},
            ),
        ),
        privacy_level="internal",
        compression_policy=CompressionPolicy(
            allow_compression=True,
            allow_reference=True,
            min_tokens=18,
            summary="Deferred and hidden capability details are available by registry lookup.",
            ref="capability:hidden_summary",
        ),
        tags=("capability_summary", "hidden_capabilities"),
        metadata={
            **metadata,
            "tool_name": "hidden_capability_summary",
            "capability_name": "hidden_capability_summary",
            "source_kind": "capability_summary",
            "visibility_state": "summary",
            "categories": categories,
            "example_names": example_names,
            "omitted_count": omitted_count,
            "request_hint": request_hint,
        },
    )


def capability_resources_to_blocks(
    resources: tuple[Any, ...] | list[Any],
    *,
    hidden_summary: Any | None = None,
    token_budget: TokenBudgetService | None = None,
) -> list[ContextBlock]:
    counter = token_budget or TokenBudgetService()
    blocks = [capability_resource_to_block(resource, token_budget=counter) for resource in resources]
    if hidden_summary is not None and int(getattr(hidden_summary, "omitted_count", 0) or 0) > 0:
        blocks.append(hidden_capability_summary_to_block(hidden_summary, token_budget=counter))
    return blocks


def capability_bundle_to_blocks(
    capability_bundle: Any,
    *,
    top_k: int = 12,
    query: str | None = None,
    token_budget: TokenBudgetService | None = None,
) -> list[ContextBlock]:
    counter = token_budget or TokenBudgetService()
    visible_tools = tuple(getattr(capability_bundle, "visible_tools", ()) or ())
    deferred_tools = tuple(getattr(capability_bundle, "deferred_tools", ()) or ())
    selected, hidden_visible, query_terms = _select_visible_capabilities(
        visible_tools,
        top_k=top_k,
        query=query,
    )
    hidden = hidden_visible + deferred_tools
    blocks = []
    for rank, (entry, score, matched_terms, matched_fields) in enumerate(selected, start=1):
        blocks.append(
            tool_registry_entry_to_block(
                entry,
                visible=True,
                token_budget=counter,
                selection_metadata={
                    "selection_rank": rank,
                    "capability_relevance_score": score,
                    "matched_query_terms": matched_terms,
                    "matched_capability_fields": matched_fields,
                    "query_aware_selection": bool(query_terms),
                },
            )
        )
    if hidden:
        blocks.append(
            _hidden_capability_summary_block(
                hidden,
                token_budget=counter,
                selected_tools=tuple(entry for entry, *_ in selected),
                query_terms=query_terms,
            )
        )
    return blocks


def _select_visible_capabilities(
    visible_tools: tuple[Any, ...],
    *,
    top_k: int,
    query: str | None,
) -> tuple[tuple[tuple[Any, float, tuple[str, ...], tuple[str, ...]], ...], tuple[Any, ...], tuple[str, ...]]:
    limit = max(int(top_k or 0), 0)
    query_terms = _capability_query_terms(query)
    if limit <= 0:
        return (), visible_tools, query_terms

    scored = tuple(
        (*_capability_relevance(entry, query_terms), index, entry)
        for index, entry in enumerate(visible_tools)
    )
    has_relevance = bool(query_terms) and any(score > 0.0 for score, *_ in scored)
    if has_relevance:
        ranked = sorted(scored, key=lambda item: (-item[0], item[3]))
        selected_rows = tuple(ranked[:limit])
        selected_indices = {index for *_score_terms_fields, index, _entry in selected_rows}
        hidden_visible = tuple(
            entry for index, entry in enumerate(visible_tools) if index not in selected_indices
        )
    else:
        selected_rows = scored[:limit]
        hidden_visible = visible_tools[len(selected_rows) :]

    selected = tuple(
        (entry, score, matched_terms, matched_fields)
        for score, matched_terms, matched_fields, _index, entry in selected_rows
    )
    return selected, hidden_visible, query_terms


def _capability_query_terms(query: str | None) -> tuple[str, ...]:
    if not query:
        return ()
    terms: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[\w]+", str(query).lower(), flags=re.UNICODE):
        for token in raw.split("_"):
            term = token.strip()
            if len(term) < 2 or term in _CAPABILITY_QUERY_STOPWORDS or term in seen:
                continue
            terms.append(term)
            seen.add(term)
    return tuple(terms)


def _capability_relevance(entry: Any, query_terms: tuple[str, ...]) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    if not query_terms:
        return 0.0, (), ()
    fields = (
        ("name", str(getattr(entry, "name", "") or ""), 2.4),
        ("display_name", str(getattr(entry, "display_name", "") or ""), 2.0),
        ("summary", str(getattr(entry, "summary", "") or ""), 1.6),
        ("capability_group", str(getattr(entry, "capability_group", "") or ""), 1.2),
        ("source_id", str(getattr(entry, "source_id", "") or ""), 0.8),
        ("source_kind", _entry_source_kind(entry), 0.4),
    )
    score = 0.0
    matched_terms: list[str] = []
    matched_fields: list[str] = []
    for term in query_terms:
        term_matched = False
        for field_name, value, weight in fields:
            if term not in value.lower():
                continue
            score += weight
            term_matched = True
            if field_name not in matched_fields:
                matched_fields.append(field_name)
        if term_matched:
            matched_terms.append(term)
    return round(score, 4), tuple(matched_terms), tuple(matched_fields)


def _hidden_capability_summary_block(
    hidden_tools: tuple[Any, ...],
    *,
    token_budget: TokenBudgetService,
    selected_tools: tuple[Any, ...] = (),
    query_terms: tuple[str, ...] = (),
) -> ContextBlock:
    by_source = Counter(
        _entry_source_kind(entry)
        for entry in hidden_tools
    )
    by_group = Counter(str(getattr(entry, "capability_group", "") or "ungrouped") for entry in hidden_tools)
    selected_names = _capability_names(selected_tools)
    omitted_names = _capability_names(hidden_tools)
    lines = [f"hidden_count={len(hidden_tools)}", "source_kinds:"]
    lines.extend(f"- {key}: {count}" for key, count in sorted(by_source.items()))
    lines.append("groups:")
    lines.extend(f"- {key}: {count}" for key, count in sorted(by_group.items()))
    if selected_names:
        lines.append("selected:")
        lines.extend(f"- {name}" for name in selected_names[:8])
    if omitted_names:
        lines.append("omitted_examples:")
        lines.extend(f"- {name}" for name in omitted_names[:8])
    lines.append("Use capability_search or tool_catalog for deferred details when task-relevant.")
    content = "\n".join(lines)
    return ContextBlock(
        block_id=stable_context_id("capability-hidden-summary", content),
        block_type="hidden_capability_summary",
        source=ContextSource(kind=ContextSourceKind.CAPABILITY, name="hidden_capability_summary"),
        title="HiddenCapabilitySummary",
        content=content,
        token_cost=token_budget.count_text(content),
        priority=0.6,
        salience=0.55,
        confidence=0.85,
        position_hint="capability:hidden_summary",
        metadata={
            "hidden_count": len(hidden_tools),
            "by_source_kind": dict(sorted(by_source.items())),
            "by_group": dict(sorted(by_group.items())),
            "selected_capability_names": selected_names,
            "omitted_capability_names": omitted_names,
            "query_terms": query_terms,
        },
    )


def _entry_source_kind(entry: Any) -> str:
    source_kind = getattr(entry, "source_kind", "tool")
    return str(getattr(source_kind, "value", source_kind) or "tool")


def _capability_context_source_kind(source_kind: str) -> ContextSourceKind:
    if source_kind == "skill":
        return ContextSourceKind.SKILL
    if source_kind == "mcp":
        return ContextSourceKind.MCP
    return ContextSourceKind.CAPABILITY


def _capability_resource_context_source_kind(*, kind: str, source_kind: str) -> ContextSourceKind:
    if kind == "skill" or source_kind == "skill":
        return ContextSourceKind.SKILL
    if kind == "mcp" or source_kind == "mcp":
        return ContextSourceKind.MCP
    return ContextSourceKind.CAPABILITY


def _capability_resource_trust_level(visibility_state: str) -> str:
    if visibility_state == "visible":
        return "trusted"
    if visibility_state == "deferred":
        return "deferred"
    if visibility_state in {"hidden", "unhealthy", "disabled"}:
        return "limited"
    return "discovered"


def _capability_resource_scores(visibility_state: str) -> tuple[float, float, float]:
    if visibility_state == "visible":
        return 0.76, 0.74, 0.86
    if visibility_state == "deferred":
        return 0.62, 0.6, 0.78
    if visibility_state == "hidden":
        return 0.42, 0.4, 0.65
    if visibility_state == "unhealthy":
        return 0.34, 0.32, 0.6
    if visibility_state == "disabled":
        return 0.26, 0.24, 0.55
    return 0.4, 0.38, 0.62


def _capability_names(entries: tuple[Any, ...], *, limit: int = 50) -> tuple[str, ...]:
    return tuple(str(getattr(entry, "name", "") or "tool") for entry in entries[:limit])


def _string_tuple(value: Any, *, limit: int = 50) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, Mapping):
        items = tuple(str(key) for key in value.keys())
    else:
        try:
            items = tuple(value)
        except TypeError:
            items = (value,)
    return tuple(str(item) for item in items[: max(limit, 0)] if str(item))


def _capability_success_history_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {
            "usage_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "user_correction_count": 0,
            "recent_success_rate": 0.0,
            "average_latency_ms": None,
        }
    if isinstance(value, Mapping):
        payload = dict(value)
    else:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            payload = dict(model_dump())
        else:
            payload = {
                "usage_count": getattr(value, "usage_count", 0),
                "success_count": getattr(value, "success_count", 0),
                "failure_count": getattr(value, "failure_count", 0),
                "user_correction_count": getattr(value, "user_correction_count", 0),
                "recent_success_rate": getattr(value, "recent_success_rate", 0.0),
                "average_latency_ms": getattr(value, "average_latency_ms", None),
            }
    return {
        "usage_count": int(payload.get("usage_count") or 0),
        "success_count": int(payload.get("success_count") or 0),
        "failure_count": int(payload.get("failure_count") or 0),
        "user_correction_count": int(payload.get("user_correction_count") or 0),
        "recent_success_rate": float(payload.get("recent_success_rate") or 0.0),
        "average_latency_ms": payload.get("average_latency_ms"),
    }


def _schema_property_count(schema: Any) -> int:
    if not isinstance(schema, Mapping):
        return 0
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        return len(properties)
    return 0


_CAPABILITY_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "please",
    "run",
    "the",
    "to",
    "use",
    "with",
}


def workspace_text_to_block(
    content: str,
    *,
    name: str = "workspace",
    token_budget: TokenBudgetService | None = None,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    return ContextBlock(
        block_id=stable_context_id("workspace", name, content),
        block_type="workspace",
        source=ContextSource(kind=ContextSourceKind.WORKSPACE, name=name),
        title=name,
        content=content,
        token_cost=counter.count_text(content),
        priority=0.7,
        salience=0.7,
        confidence=0.8,
        position_hint="workspace:summary",
    )


def recent_event_to_block(
    event: Any,
    *,
    token_budget: TokenBudgetService | None = None,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    event_id = str(getattr(event, "event_id", None) or getattr(event, "id", None) or stable_context_id("event", event))
    event_type = str(getattr(event, "event_type", None) or getattr(event, "type", None) or "event")
    content = str(getattr(event, "summary", None) or getattr(event, "message", None) or event)
    return ContextBlock(
        block_id=stable_context_id("event", event_id, event_type),
        block_type="recent_event",
        source=ContextSource(kind=ContextSourceKind.EVENT, name=event_type, ref=event_id),
        title=event_type,
        content=content,
        token_cost=counter.count_text(content),
        priority=0.55,
        salience=0.6,
        confidence=0.75,
        position_hint="event:recent",
        metadata={"event_id": event_id, "event_type": event_type},
    )


def tool_result_to_block(
    tool_result: Any,
    *,
    tool_name: str | None = None,
    token_budget: TokenBudgetService | None = None,
    summary_token_budget: int = 160,
) -> ContextBlock:
    counter = token_budget or TokenBudgetService()
    payload = _coerce_tool_result_payload(tool_result)
    payload_mapping = payload if isinstance(payload, Mapping) else {}
    name = tool_name or str(getattr(tool_result, "name", "") or payload_mapping.get("tool_name") or "tool")
    tool_call_id = str(
        getattr(tool_result, "tool_call_id", "")
        or getattr(tool_result, "id", "")
        or payload_mapping.get("tool_call_id")
        or ""
    )
    raw_ref = _tool_result_raw_ref(payload_mapping)
    status = str(payload_mapping.get("status") or getattr(tool_result, "status", "") or "unknown")
    output = _tool_result_output_summary(payload)
    output = counter.truncate_text(str(output), max_tokens=summary_token_budget)
    budget_notice = payload_mapping.get("_tool_output_budget")
    budget_notice = budget_notice if isinstance(budget_notice, Mapping) else {}
    compaction = budget_notice.get("compaction")
    compaction = compaction if isinstance(compaction, Mapping) else {}
    compacted = bool(
        payload_mapping.get("output_compacted")
        or budget_notice.get("truncated")
        or compaction
        or raw_ref
    )

    lines = [f"tool={name}", f"status={status}"]
    if tool_call_id:
        lines.append(f"tool_call_id={tool_call_id}")
    if "exit_code" in payload_mapping:
        lines.append(f"exit_code={payload_mapping.get('exit_code')}")
    if payload_mapping.get("command"):
        lines.append(f"command={payload_mapping.get('command')}")
    if payload_mapping.get("cwd"):
        lines.append(f"cwd={payload_mapping.get('cwd')}")
    if raw_ref:
        lines.append(f"raw_ref={raw_ref}")
    if "original_chars" in budget_notice:
        lines.append(f"original_chars={budget_notice.get('original_chars')}")
    if "original_tokens_approx" in budget_notice:
        lines.append(f"original_tokens_approx={budget_notice.get('original_tokens_approx')}")
    if compaction.get("profile"):
        lines.append(f"compaction_profile={compaction.get('profile')}")
    if compacted:
        lines.append("compacted=true")
    if output:
        lines.append("summary:")
        lines.append(output)
    content = "\n".join(lines)

    evidence_refs = ()
    if raw_ref:
        evidence_refs = (
            EvidenceRef(
                ref_id=stable_context_id("tool-result-raw", raw_ref),
                source_kind="tool_result_artifact",
                source_id=name,
                confidence=0.8,
                metadata={"artifact_url": raw_ref},
            ),
        )
    return ContextBlock(
        block_id=stable_context_id("tool-result", name, tool_call_id, raw_ref or content),
        block_type="previous_tool_result",
        source=ContextSource(
            kind=ContextSourceKind.TOOL_RESULT,
            name=name,
            ref=tool_call_id or None,
            trust_level="runtime",
            metadata={"raw_ref": raw_ref} if raw_ref else {},
        ),
        title=f"ToolResult {name}",
        content=content,
        token_cost=counter.count_text(content),
        priority=0.65,
        salience=0.7,
        confidence=0.8,
        position_hint="recent:tool_result",
        evidence_refs=evidence_refs,
        privacy_level="internal",
        compression_policy=CompressionPolicy(
            allow_compression=True,
            allow_reference=True,
            summary=output or None,
            ref=raw_ref,
        ),
        tags=tuple(filter(None, ("tool_result", "compacted" if compacted else None))),
        metadata={
            "tool_name": name,
            "tool_call_id": tool_call_id or None,
            "status": status,
            "raw_ref": raw_ref,
            "compacted": compacted,
            "artifact_url": raw_ref,
            "output_compaction_profile": compaction.get("profile"),
        },
    )


def _coerce_tool_result_payload(tool_result: Any) -> Any:
    content = getattr(tool_result, "content", tool_result)
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _tool_result_raw_ref(payload: Mapping[str, Any]) -> str | None:
    direct = payload.get("raw_output_artifact_url") or payload.get("artifact_url") or payload.get("raw_ref")
    if direct:
        return str(direct)
    notice = payload.get("_tool_output_budget")
    notice = notice if isinstance(notice, Mapping) else {}
    notice_ref = notice.get("artifact_url") or notice.get("raw_artifact_url")
    if notice_ref:
        return str(notice_ref)
    compaction = notice.get("compaction")
    compaction = compaction if isinstance(compaction, Mapping) else {}
    compaction_ref = compaction.get("raw_artifact_url") or compaction.get("artifact_url")
    return str(compaction_ref) if compaction_ref else None


def _tool_result_output_summary(payload: Any) -> str:
    if isinstance(payload, Mapping):
        for key in ("summary", "output", "message", "content"):
            value = payload.get(key)
            if value is not None:
                return _stringify_tool_value(value)
        visible_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"_tool_output_budget", "raw_output_artifact_url", "artifact_url"}
        }
        return _stringify_tool_value(visible_payload)
    return _stringify_tool_value(payload)


def _stringify_tool_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except TypeError:
        return str(value)


__all__ = [
    "capability_bundle_to_blocks",
    "capability_resource_to_block",
    "capability_resources_to_blocks",
    "hidden_capability_summary_to_block",
    "memory_injection_view_to_blocks",
    "prompt_injection_view_to_blocks",
    "prompt_section_to_block",
    "prompt_snapshot_to_blocks",
    "recent_event_to_block",
    "tool_result_to_block",
    "tool_registry_entry_to_block",
    "workspace_text_to_block",
]
