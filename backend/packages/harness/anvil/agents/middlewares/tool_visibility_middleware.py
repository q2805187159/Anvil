"""Behavior enhancement layer.

Reads: capability bundle, promotion state, parent-visible tool constraints
Writes: visible_tool_names, deferred_tool_names, capability bundle fingerprint
Side effects: rebuilds the runtime-visible capability bundle for the current turn
Failure behavior: fail-open by preserving the current bundle if rebuild inputs are missing
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class ToolVisibilityMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def before_model(self, state: LeadAgentState, runtime):
        bundle = runtime.context.capability_bundle
        capability_service = runtime.context.capability_service
        config_result = runtime.context.config_result
        tool_registry = runtime.context.tool_registry
        if capability_service is not None and config_result is not None and tool_registry is not None:
            # Tool visibility is runtime-aware: it decides what the model may see this turn.
            # The downstream deferred filter only applies this already-computed visibility to
            # the actual schema list sent to the model.
            promoted = tuple(sorted(runtime.context.promotion_state | set(runtime.context.promoted_capabilities)))
            bundle = capability_service.rebuild_bundle(
                registry=tool_registry,
                config_result=config_result,
                request_context=runtime.context.request_context,
                promoted_capabilities=promoted,
                parent_visible_tool_names=runtime.context.parent_visible_tool_names,
                enabled_skill_ids=bundle.enabled_skill_ids,
                effective_mcp_servers=bundle.effective_mcp_servers,
                effective_extension_sources=bundle.effective_extension_sources,
                effective_plugin_ids=bundle.effective_plugin_ids,
            )
            bundle = bundle.model_copy(
                update={
                    "enabled_skill_ids": runtime.context.capability_bundle.enabled_skill_ids,
                    "mentioned_skill_ids": runtime.context.capability_bundle.mentioned_skill_ids,
                    "prompt_safe_summaries": runtime.context.capability_bundle.prompt_safe_summaries,
                }
            )
            runtime.context.capability_bundle = bundle
        return {
            "visible_tool_names": [entry.name for entry in bundle.visible_tools],
            "deferred_tool_names": [entry.name for entry in bundle.deferred_tools],
            "capability_bundle_fingerprint": bundle.fingerprint,
            "enabled_skill_ids": list(bundle.enabled_skill_ids),
        }
