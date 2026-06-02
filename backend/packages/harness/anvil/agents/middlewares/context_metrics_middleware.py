"""Context metrics middleware for tracking and monitoring."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState

from .service import ContextMetricsService

if TYPE_CHECKING:
    from .contracts import ContextMetricsConfig

logger = logging.getLogger(__name__)


class ContextMetricsMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    """Middleware for context metrics tracking.

    Execution: before_model and after_model hooks

    before_model:
    - Start turn tracking
    - Record initial context state

    after_model:
    - End turn tracking
    - Record token usage
    - Aggregate metrics
    - Store results

    Design principles:
    - Low overhead (<5ms target)
    - Comprehensive tracking
    - Actionable insights
    - Harness-first integration
    """

    state_schema = LeadAgentState

    def __init__(self, config: ContextMetricsConfig):
        """Initialize context metrics middleware.

        Args:
            config: Metrics configuration
        """
        self.config = config
        self.service: ContextMetricsService | None = None

    def before_model(self, state: LeadAgentState, runtime):
        """Start tracking before model call.

        Args:
            state: Current agent state
            runtime: Runtime context

        Returns:
            None (no state update)
        """
        if not self.config.enabled:
            return None

        # Lazy initialize service
        if self.service is None:
            self._initialize_service(runtime)

        # Get messages from state
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        messages = state_obj.messages

        if not messages:
            return None

        try:
            # Start turn tracking
            self.service.start_turn(messages)

        except Exception as e:
            logger.error(f"Context metrics before_model failed: {e}", exc_info=True)

        return None

    def after_model(self, state: LeadAgentState, runtime):
        """End tracking after model call.

        Args:
            state: Current agent state
            runtime: Runtime context

        Returns:
            None (no state update)
        """
        if not self.config.enabled or self.service is None:
            return None

        try:
            # Get token usage from runtime
            # (This is a placeholder - actual implementation depends on runtime API)
            input_tokens = getattr(runtime, 'input_tokens', 0)
            output_tokens = getattr(runtime, 'output_tokens', 0)

            # End turn tracking
            turn_metrics = self.service.end_turn(input_tokens, output_tokens)

            # Log summary
            if self.config.track_tokens:
                logger.info(
                    f"Turn complete: {turn_metrics.total_tokens} tokens "
                    f"(saved: {turn_metrics.tokens_saved_compaction + turn_metrics.tokens_saved_jit}), "
                    f"overhead: {turn_metrics.total_overhead_ms:.2f}ms"
                )

        except Exception as e:
            logger.error(f"Context metrics after_model failed: {e}", exc_info=True)

        return None

    def _initialize_service(self, runtime):
        """Lazy initialize metrics service.

        Args:
            runtime: Runtime context
        """
        # Get session ID from context
        session_id = getattr(runtime.context, 'thread_id', 'unknown')

        self.service = ContextMetricsService(
            config=self.config,
            session_id=session_id
        )

        logger.info("Context metrics service initialized")
