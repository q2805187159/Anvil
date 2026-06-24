from __future__ import annotations

import re
from dataclasses import dataclass, field

from .contracts import AttentionBudget, ContextBlock, ContextBlockTrace, DropDecision, bounded_score


@dataclass(frozen=True)
class BudgetSelection:
    selected: tuple[ContextBlock, ...]
    dropped: tuple[DropDecision, ...]
    traces: tuple[ContextBlockTrace, ...]
    layer_token_usage: dict[str, int]
    total_tokens: int
    fallback_used: bool = False
    diagnostics: dict[str, object] = field(default_factory=dict)
    retrieval_scores: dict[str, dict[str, float]] = field(default_factory=dict)


class AttentionBudgetController:
    """Deterministic P0 selector for ContextBlock budget competition."""

    def select(
        self,
        blocks: list[ContextBlock] | tuple[ContextBlock, ...],
        *,
        budget: AttentionBudget,
        salience_route: object | None = None,
    ) -> BudgetSelection:
        candidates, route_scores, route_diagnostics = _apply_salience_route(list(blocks), salience_route)
        candidate_order = {block.block_id: index for index, block in enumerate(candidates)}
        indexed = list(enumerate(candidates))
        protected = [block for _, block in indexed if block.injection_policy.protected]
        ordinary = [item for item in indexed if not item[1].injection_policy.protected]
        ordered = protected + [
            block
            for _, block in sorted(
                ordinary,
                key=lambda item: (
                    -_selection_score(item[1]),
                    _position_weight(item[1].position_hint),
                    item[0],
                    item[1].block_id,
                ),
            )
        ]

        selected: list[ContextBlock] = []
        dropped: list[DropDecision] = []
        traces: list[ContextBlockTrace] = []
        layer_usage: dict[str, int] = {}
        total = 0

        protected_tokens = sum(block.token_cost for block in protected)
        if protected_tokens > budget.hard_context_tokens:
            for block in protected:
                selected.append(block)
                total += block.token_cost
                layer_usage[block.block_type] = layer_usage.get(block.block_type, 0) + block.token_cost
                traces.append(_trace(block, selected=True, reason="emergency_fallback", score=_selection_score(block)))
            for _, block in ordinary:
                score = _selection_score(block)
                dropped.append(
                    DropDecision(
                        block_id=block.block_id,
                        reason="emergency_fallback",
                        token_cost=block.token_cost,
                        score=score,
                    )
                )
                traces.append(
                    _trace(block, selected=False, dropped=True, reason="emergency_fallback", score=score)
                )
            return BudgetSelection(
                selected=tuple(selected),
                dropped=tuple(dropped),
                traces=tuple(traces),
                layer_token_usage=dict(sorted(layer_usage.items())),
                total_tokens=total,
                fallback_used=True,
                diagnostics={
                    "fallback_reason": "protected_blocks_exceed_budget",
                    "protected_tokens": protected_tokens,
                    "hard_context_tokens": budget.hard_context_tokens,
                    **route_diagnostics,
                },
                retrieval_scores=route_scores,
            )

        for block in ordered:
            score = _selection_score(block)
            if not block.injection_policy.allow:
                reason = block.injection_policy.reason or "injection_disallowed"
                dropped.append(
                    DropDecision(
                        block_id=block.block_id,
                        reason=reason,
                        token_cost=block.token_cost,
                        score=score,
                    )
                )
                traces.append(_trace(block, selected=False, dropped=True, reason=reason, score=score))
                continue

            layer_budget = budget.per_layer_token_budget.get(block.block_type)
            current_layer_tokens = layer_usage.get(block.block_type, 0)
            exceeds_layer = layer_budget is not None and current_layer_tokens + block.token_cost > layer_budget
            exceeds_total = total + block.token_cost > budget.hard_context_tokens
            if block.injection_policy.protected:
                exceeds_layer = False
                exceeds_total = False

            if exceeds_layer or exceeds_total:
                reference_block = _reference_block(block)
                if reference_block is not None:
                    reference_layer_tokens = layer_usage.get(reference_block.block_type, 0)
                    reference_layer_budget = budget.per_layer_token_budget.get(reference_block.block_type)
                    reference_exceeds_layer = (
                        reference_layer_budget is not None
                        and reference_layer_tokens + reference_block.token_cost > reference_layer_budget
                    )
                    reference_exceeds_total = total + reference_block.token_cost > budget.hard_context_tokens
                    if not reference_exceeds_layer and not reference_exceeds_total:
                        dropped.append(
                            DropDecision(
                                block_id=block.block_id,
                                reason="reference_only",
                                token_cost=block.token_cost,
                                score=score,
                                metadata={
                                    "reference_block_id": reference_block.block_id,
                                    "recoverable_ref": block.compression_policy.ref,
                                },
                            )
                        )
                        traces.append(
                            _trace(
                                block,
                                selected=False,
                                dropped=True,
                                deferred=True,
                                reason="reference_only",
                                score=score,
                            )
                        )
                        selected.append(reference_block)
                        total += reference_block.token_cost
                        layer_usage[reference_block.block_type] = (
                            reference_layer_tokens + reference_block.token_cost
                        )
                        traces.append(
                            _trace(
                                reference_block,
                                selected=True,
                                compressed=True,
                                deferred=True,
                                reason="reference_only",
                                score=score,
                            )
                        )
                        continue
                reason = "layer_budget_exceeded" if exceeds_layer else "budget_exceeded"
                dropped.append(
                    DropDecision(
                        block_id=block.block_id,
                        reason=reason,
                        token_cost=block.token_cost,
                        score=score,
                    )
                )
                traces.append(_trace(block, selected=False, dropped=True, reason=reason, score=score))
                continue

            selected.append(block)
            total += block.token_cost
            layer_usage[block.block_type] = current_layer_tokens + block.token_cost
            traces.append(_trace(block, selected=True, score=score))

        selected = sorted(
            selected,
            key=lambda block: (
                0 if block.injection_policy.protected else _position_weight(block.position_hint),
                candidate_order.get(block.block_id, 0),
                block.block_id,
            ),
        )
        layer_usage = dict(sorted(layer_usage.items()))
        return BudgetSelection(
            selected=tuple(selected),
            dropped=tuple(dropped),
            traces=tuple(traces),
            layer_token_usage=layer_usage,
            total_tokens=total,
            diagnostics=route_diagnostics,
            retrieval_scores=route_scores,
        )


def _selection_score(block: ContextBlock) -> float:
    protected_boost = 1.0 if block.injection_policy.protected else 0.0
    conflict_penalty = 0.4 if block.conflict_state not in {"", "none", "resolved"} else 0.0
    privacy_penalty = 0.2 if block.privacy_level in {"secret", "restricted"} else 0.0
    return round(
        protected_boost
        + block.priority * 0.4
        + block.salience * 0.4
        + block.confidence * 0.2
        - conflict_penalty
        - privacy_penalty,
        4,
    )


def _position_weight(position_hint: str | None) -> int:
    value = str(position_hint or "")
    if value.startswith("stable:"):
        return 10
    if value.startswith("workspace:"):
        return 20
    if value.startswith("memory:"):
        return 30
    if value.startswith("capability:"):
        return 40
    if value.startswith("event:"):
        return 50
    if value.startswith("volatile:"):
        return 60
    return 70


def _trace(
    block: ContextBlock,
    *,
    selected: bool,
    compressed: bool = False,
    deferred: bool = False,
    dropped: bool = False,
    reason: str | None = None,
    score: float = 0.0,
) -> ContextBlockTrace:
    return ContextBlockTrace(
        block_id=block.block_id,
        block_type=block.block_type,
        source_kind=block.source.kind.value,
        token_cost=block.token_cost,
        selected=selected,
        compressed=compressed,
        deferred=deferred,
        dropped=dropped,
        reason=reason,
        score=score,
    )


def _reference_block(block: ContextBlock) -> ContextBlock | None:
    if not block.compression_policy.allow_reference or not block.compression_policy.ref:
        return None
    summary = block.compression_policy.summary or f"{block.title} is available by reference."
    ref = block.compression_policy.ref
    token_cost = max(1, min(int(block.compression_policy.min_tokens), int(block.token_cost)))
    return block.model_copy(
        update={
            "block_id": f"{block.block_id}:reference",
            "title": f"{block.title} Reference",
            "content": "\n".join(
                [
                    f"summary={summary}",
                    f"ref={ref}",
                    f"original_block_id={block.block_id}",
                ]
            ),
            "token_cost": token_cost,
            "compression_policy": block.compression_policy.model_copy(
                update={"allow_compression": False, "allow_reference": False}
            ),
            "metadata": {
                **block.metadata,
                "original_block_id": block.block_id,
                "raw_ref": ref,
                "compression_strategy": "reference_only",
            },
        }
    )


def _apply_salience_route(
    blocks: list[ContextBlock],
    salience_route: object | None,
) -> tuple[list[ContextBlock], dict[str, dict[str, float]], dict[str, object]]:
    if salience_route is None:
        return blocks, {}, {}

    route_id = str(getattr(salience_route, "route_id", "") or "")
    goal_stack_ref = str(getattr(salience_route, "goal_stack_ref", "") or "")
    active_goal_id = str(getattr(salience_route, "active_goal_id", "") or "")
    route_terms = _route_terms(salience_route)
    adjusted_blocks: list[ContextBlock] = []
    retrieval_scores: dict[str, dict[str, float]] = {}
    boosted_count = 0

    for block in blocks:
        alignment, matched_terms = _goal_alignment(block, route_terms)
        adjusted = block
        adjusted_priority = block.priority
        adjusted_salience = block.salience
        if alignment > 0 and _route_can_boost(block):
            boosted_count += 1
            adjusted_priority = bounded_score(max(block.priority, min(1.0, block.priority + alignment * 0.6)))
            adjusted_salience = bounded_score(max(block.salience, min(1.0, block.salience + alignment * 0.75)))
            adjusted = block.model_copy(
                update={
                    "priority": adjusted_priority,
                    "salience": adjusted_salience,
                    "metadata": {
                        **block.metadata,
                        "salience_route_id": route_id,
                        "goal_stack_ref": goal_stack_ref,
                        "active_goal_id": active_goal_id or None,
                        "goal_alignment": alignment,
                        "goal_matched_terms": matched_terms[:12],
                    },
                }
            )
        adjusted_blocks.append(adjusted)
        retrieval_scores[block.block_id] = {
            "priority": block.priority,
            "salience": block.salience,
            "confidence": block.confidence,
            "adjusted_priority": adjusted_priority,
            "adjusted_salience": adjusted_salience,
            "goal_alignment": alignment,
            "matched_term_count": float(len(matched_terms)),
            "selection_score": _selection_score(adjusted),
        }

    return (
        adjusted_blocks,
        retrieval_scores,
        {
            "salience_route_id": route_id,
            "goal_stack_ref": goal_stack_ref,
            "active_goal_id": active_goal_id,
            "salience_routed_block_count": len(blocks),
            "salience_boosted_block_count": boosted_count,
        },
    )


def _route_terms(salience_route: object) -> list[tuple[str, float]]:
    terms: list[tuple[str, float]] = []
    boost_terms = getattr(salience_route, "boost_terms", {}) or {}
    if isinstance(boost_terms, dict):
        for term, weight in boost_terms.items():
            text = str(term or "").strip()
            if text:
                terms.append((text, max(0.3, bounded_score(weight, default=0.5))))

    for phrase in list(getattr(salience_route, "blocker_terms", ()) or ()):
        terms.extend((term, 0.45) for term in _meaningful_terms(phrase))
    for phrase in list(getattr(salience_route, "next_action_terms", ()) or ()):
        terms.extend((term, 0.5) for term in _meaningful_terms(phrase))

    memory_query = str(getattr(salience_route, "memory_query", "") or "")
    query_line = ""
    for line in memory_query.splitlines():
        if line.startswith("current_query="):
            query_line = line.removeprefix("current_query=")
            break
    terms.extend((term, 0.35) for term in _meaningful_terms(query_line)[:12])

    deduped: dict[str, float] = {}
    for term, weight in terms:
        normalized = _normalize_text(term)
        if normalized:
            deduped[normalized] = max(deduped.get(normalized, 0.0), weight)
    return sorted(deduped.items(), key=lambda item: (-item[1], item[0]))[:48]


def _goal_alignment(block: ContextBlock, route_terms: list[tuple[str, float]]) -> tuple[float, list[str]]:
    if not route_terms:
        return 0.0, []
    haystack = _block_haystack(block)
    if not haystack:
        return 0.0, []

    matched_terms: list[str] = []
    weighted = 0.0
    for term, weight in route_terms:
        if _term_matches(term, haystack):
            matched_terms.append(term)
            weighted += weight

    if not matched_terms:
        return 0.0, []

    return bounded_score(min(1.0, weighted / 1.4), default=0.0), matched_terms


def _route_can_boost(block: ContextBlock) -> bool:
    if not block.injection_policy.allow:
        return False
    if block.privacy_level in {"secret", "restricted"}:
        return False
    if block.conflict_state not in {"", "none", "resolved"}:
        return False
    return block.source.kind.value in {
        "memory",
        "workspace",
        "capability",
        "tool_result",
        "mcp",
        "skill",
    }


def _block_haystack(block: ContextBlock) -> str:
    parts: list[str] = [
        block.block_id,
        block.source.ref or "",
        block.title,
        block.content[:2400],
        " ".join(block.tags),
    ]
    for key, value in block.metadata.items():
        if key in {
            "capability_id",
            "tool_name",
            "memory_id",
            "claim_id",
            "skill_id",
            "source_kind",
            "workspace_id",
        } or key.endswith("_id"):
            parts.append(str(value)[:320])
    return _normalize_text(" ".join(part for part in parts if part))


def _meaningful_terms(value: object) -> list[str]:
    normalized = _normalize_text(str(value or ""))
    if not normalized:
        return []
    stop_words = {
        "into",
        "with",
        "from",
        "that",
        "this",
        "should",
        "current",
        "active",
        "goal",
        "route",
        "memory",
    }
    terms = []
    for term in normalized.split():
        if len(term) < 4 or term in stop_words:
            continue
        terms.append(term)
    return terms


def _term_matches(term: str, haystack: str) -> bool:
    normalized = _normalize_text(term)
    if not normalized:
        return False
    if normalized in haystack:
        return True
    tokens = normalized.split()
    return bool(tokens) and all(token in haystack for token in tokens)


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


__all__ = ["AttentionBudgetController", "BudgetSelection"]
