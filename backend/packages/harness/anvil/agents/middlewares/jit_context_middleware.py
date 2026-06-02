"""JIT context middleware for on-demand context loading."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState

from .jit_context.service import JITContextService

if TYPE_CHECKING:
    from .contracts import JITContextConfig

logger = logging.getLogger(__name__)


class JITContextMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    """Middleware for Just-In-Time context loading.

    Execution: before_model and after_model hooks

    before_model:
    - Analyze message for context needs
    - Load required context lazily
    - Prefetch predicted context

    after_model:
    - Cache used context
    - Cleanup expired entries
    - Collect metrics

    Design principles:
    - Lazy loading: Load only when needed
    - Progressive disclosure: Start minimal, expand as needed
    - Smart prefetching: Predict and preload
    - Harness-first: Clean integration
    """

    state_schema = LeadAgentState

    def __init__(self, config: JITContextConfig):
        """Initialize JIT context middleware.

        Args:
            config: JIT context configuration
        """
        self.config = config
        self.service: JITContextService | None = None

    def before_model(self, state: LeadAgentState, runtime):
        """Load context before model call.

        Args:
            state: Current agent state
            runtime: Runtime context

        Returns:
            State update dict if context loaded, None otherwise
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

        # Get current message
        current_message = None
        if messages:
            last_msg = messages[-1]
            content = getattr(last_msg, "content", "")
            if isinstance(content, str):
                current_message = content

        # Prefetch predicted context
        try:
            prefetched = self.service.prefetch_context(
                messages=messages,
                current_message=current_message,
                context=runtime.context
            )

            if prefetched > 0:
                logger.info(f"JIT context: Prefetched {prefetched} items")

        except Exception as e:
            logger.error(f"JIT context prefetch failed: {e}", exc_info=True)

        # No state update needed (context loaded into cache)
        return None

    def after_model(self, state: LeadAgentState, runtime):
        """Cleanup after model call.

        Args:
            state: Current agent state
            runtime: Runtime context

        Returns:
            None (no state update)
        """
        if not self.config.enabled or self.service is None:
            return None

        try:
            # Cleanup expired cache entries
            self.service.cleanup()

            # Log metrics periodically
            if self.config.collect_metrics:
                metrics = self.service.get_metrics()
                if metrics["requests"]["total"] % 10 == 0:  # Every 10 requests
                    logger.info(
                        f"JIT context metrics: "
                        f"cache_hit_rate={metrics['requests']['cache_hit_rate']:.1%}, "
                        f"prefetch_accuracy={metrics['prefetch']['accuracy']:.1%}, "
                        f"avg_load_time={metrics['performance']['avg_load_time_ms']:.1f}ms"
                    )

        except Exception as e:
            logger.error(f"JIT context cleanup failed: {e}", exc_info=True)

        return None

    def _initialize_service(self, runtime):
        """Lazy initialize JIT context service.

        Args:
            runtime: Runtime context
        """
        config_result = getattr(runtime.context, "config_result", None)
        effective_config = getattr(config_result, "effective_config", None)

        self.service = JITContextService(
            config=self.config,
            effective_config=effective_config
        )

        logger.info("JIT context service initialized")
