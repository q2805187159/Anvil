from __future__ import annotations

from html import escape
from typing import Any

from .budget import AttentionBudgetController
from .contracts import (
    AssembledContext,
    AttentionBudget,
    ContextAssemblyTrace,
    ContextBlock,
    stable_prompt_hash,
)


class ContextAssemblerV2:
    """XML-like assembler for Runtime Context V2 diagnostic mode."""

    def __init__(self, *, budget_controller: AttentionBudgetController | None = None) -> None:
        self.budget_controller = budget_controller or AttentionBudgetController()

    def assemble(
        self,
        blocks: list[ContextBlock] | tuple[ContextBlock, ...],
        *,
        budget: AttentionBudget | None = None,
        salience_route: object | None = None,
        trace_metadata: dict[str, Any] | None = None,
    ) -> AssembledContext:
        active_budget = budget or AttentionBudget()
        candidates = tuple(blocks)
        selection = self.budget_controller.select(
            candidates,
            budget=active_budget,
            salience_route=salience_route,
        )
        rendered = self._render(selection.selected)
        metadata = dict(trace_metadata or {})
        for key in ("salience_route_id", "goal_stack_ref", "active_goal_id"):
            value = selection.diagnostics.get(key)
            if value:
                metadata.setdefault(key, value)
        trace = ContextAssemblyTrace(
            prompt_hash=stable_prompt_hash(rendered),
            candidate_block_ids=tuple(block.block_id for block in candidates),
            selected_block_ids=tuple(block.block_id for block in selection.selected),
            compressed_block_ids=tuple(
                trace.block_id for trace in selection.traces if trace.selected and trace.compressed
            ),
            deferred_block_ids=tuple(
                decision.block_id
                for decision in selection.dropped
                if decision.reason == "reference_only"
            ),
            dropped_block_ids=tuple(decision.block_id for decision in selection.dropped),
            layer_token_usage=selection.layer_token_usage,
            selected_capabilities=tuple(
                _capability_trace_name(block)
                for block in selection.selected
                if block.block_type == "capability"
            ),
            selected_tools=tuple(
                _capability_trace_name(block)
                for block in selection.selected
                if block.block_type == "capability"
            ),
            selected_mcp_tools=tuple(
                _capability_trace_name(block)
                for block in selection.selected
                if block.block_type == "capability" and block.metadata.get("source_kind") == "mcp"
            ),
            selected_skills=tuple(
                _capability_trace_name(block)
                for block in selection.selected
                if block.source.kind.value == "skill" or block.metadata.get("source_kind") == "skill"
            ),
            selected_memory=tuple(
                str(block.metadata.get("memory_id") or block.metadata.get("claim_id") or block.block_id)
                for block in selection.selected
                if _is_memory_trace_block(block)
            ),
            selected_workspace=tuple(
                str(block.metadata.get("workspace_id") or block.source.ref or block.block_id)
                for block in selection.selected
                if block.source.kind.value == "workspace"
            ),
            selected_events=tuple(
                str(block.metadata.get("event_id") or block.source.ref or block.block_id)
                for block in selection.selected
                if block.source.kind.value == "event" or block.block_type == "recent_event"
            ),
            selected_tool_results=tuple(
                str(block.metadata.get("tool_call_id") or block.source.ref or block.block_id)
                for block in selection.selected
                if block.source.kind.value == "tool_result" or block.block_type == "previous_tool_result"
            ),
            selected_tool_result_refs=tuple(
                str(block.metadata.get("raw_ref") or block.source.metadata.get("raw_ref") or "")
                for block in selection.selected
                if (
                    block.source.kind.value == "tool_result"
                    or block.block_type == "previous_tool_result"
                )
                and (block.metadata.get("raw_ref") or block.source.metadata.get("raw_ref"))
            ),
            retrieval_scores=selection.retrieval_scores or _base_retrieval_scores(candidates),
            block_traces=selection.traces,
            drop_decisions=selection.dropped,
            total_tokens=selection.total_tokens,
            budget=active_budget,
            metadata=metadata,
        )
        return AssembledContext(
            rendered_context=rendered,
            blocks=selection.selected,
            trace=trace,
            fallback_used=selection.fallback_used,
            diagnostics=dict(selection.diagnostics),
        )

    def _render(self, blocks: tuple[ContextBlock, ...]) -> str:
        lines = ['<runtime_context_v2 version="p0">']
        current_layer: str | None = None
        for block in blocks:
            if block.block_type != current_layer:
                if current_layer is not None:
                    lines.append(f"</context_layer>")
                current_layer = block.block_type
                lines.append(f'<context_layer name="{escape(block.block_type, quote=True)}">')
            lines.extend(_render_block(block))
        if current_layer is not None:
            lines.append("</context_layer>")
        lines.append("</runtime_context_v2>")
        return "\n".join(lines)


def _is_memory_trace_block(block: ContextBlock) -> bool:
    if block.source.kind.value != "memory":
        return False
    return block.block_type in {
        "memory",
        "semantic_fact",
        "episodic_summary",
        "procedural_hint",
        "wisdom_warning",
        "retrieved_memory",
    }


def _capability_trace_name(block: ContextBlock) -> str:
    return str(block.metadata.get("tool_name") or block.metadata.get("capability_id") or block.block_id)


def _base_retrieval_scores(blocks: tuple[ContextBlock, ...]) -> dict[str, dict[str, float]]:
    return {
        block.block_id: {
            "priority": block.priority,
            "salience": block.salience,
            "confidence": block.confidence,
        }
        for block in blocks
    }


def _render_block(block: ContextBlock) -> list[str]:
    attrs = {
        "block_id": block.block_id,
        "source": block.source.kind.value,
        "title": block.title,
        "tokens": str(block.token_cost),
        "confidence": f"{block.confidence:.3f}",
        "privacy": block.privacy_level,
        "conflict": block.conflict_state,
    }
    attr_text = " ".join(f'{key}="{escape(value, quote=True)}"' for key, value in attrs.items())
    return [
        f"<context_block {attr_text}>",
        escape(block.content, quote=False),
        "</context_block>",
    ]


__all__ = ["ContextAssemblerV2"]
