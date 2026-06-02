"""Legacy opt-in compaction middleware for experimental context trimming.

Production conversation compaction is owned by SummarizationMiddleware because
it writes durable summaries and compaction-level telemetry. This middleware is
kept for explicit experiments only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState
from anvil.runtime.token_budget import TokenBudgetService

from .compaction.service import CompactionService

if TYPE_CHECKING:
    from .contracts import CompactionConfig

logger = logging.getLogger(__name__)


class CompactionMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    """Monitors context size and triggers legacy message-list compaction.

    Execution: before_model hook
    Trigger: When context tokens > threshold (default 70% of max)

    Design principles:
    - Harness-first: Integrates cleanly with existing middleware chain
    - Priority-based: Preserves critical information, discards redundant content
    - LLM-powered: Uses model intelligence for semantic compression
    - Metrics-driven: Tracks compression ratio, fact preservation, performance

    This middleware does not write `summary_context` or compaction-level
    telemetry. It must therefore stay opt-in and must not replace the
    production SummarizationMiddleware path.
    """

    state_schema = LeadAgentState

    def __init__(self, config: CompactionConfig):
        """Initialize compaction middleware.

        Args:
            config: Compaction configuration
        """
        self.config = config
        self.service: CompactionService | None = None
        self.token_budget = TokenBudgetService()

    def before_model(self, state: LeadAgentState, runtime):
        """Check context size and compact if needed.

        Args:
            state: Current agent state
            runtime: Runtime context

        Returns:
            State update dict if compaction triggered, None otherwise
        """
        if not self.config.enabled:
            return None

        # Lazy initialize service (needs effective_config from runtime)
        if self.service is None:
            self._initialize_service(runtime)

        # Get messages from state
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        messages = state_obj.messages

        if not messages:
            return None

        # Get max context tokens from model
        max_tokens = self._get_max_context_tokens(runtime)

        # Check if compaction should be triggered
        if not self.service.should_compact(messages, max_tokens):
            return None

        # Trigger compaction
        logger.info("Context compaction triggered")

        try:
            compacted_messages = self.service.compact(
                messages=messages,
                max_tokens=max_tokens,
                context=runtime.context
            )

            # Return state update
            return {"messages": compacted_messages}

        except Exception as e:
            logger.error(f"Compaction failed: {e}", exc_info=True)
            # Don't fail the request, just skip compaction
            return None

    def _initialize_service(self, runtime):
        """Lazy initialize compaction service with effective config."""
        config_result = getattr(runtime.context, "config_result", None)
        effective_config = getattr(config_result, "effective_config", None)

        self.service = CompactionService(
            config=self.config,
            effective_config=effective_config
        )

    def _get_max_context_tokens(self, runtime) -> int:
        """Get maximum context tokens from model configuration.

        Args:
            runtime: Runtime context

        Returns:
            Maximum context tokens (default: 100000 if not specified)
        """
        # Try to get from model config
        config_result = getattr(runtime.context, "config_result", None)
        if config_result:
            effective_config = getattr(config_result, "effective_config", None)
            if effective_config:
                # Get current model name
                model_name = getattr(runtime.context, "model_name", None)
                if not model_name:
                    model_name = effective_config.default_model

                # Get model config
                model_config = effective_config.models.get(model_name)
                if model_config:
                    # Try to get max_input_tokens or max_tokens
                    max_input = getattr(model_config, "max_input_tokens", None)
                    if max_input:
                        return int(max_input)

                    max_tokens = getattr(model_config, "max_tokens", None)
                    if max_tokens:
                        # max_tokens is usually output, assume 4x for input
                        return int(max_tokens) * 4

        # Default: 100k tokens (conservative estimate)
        return 100000
