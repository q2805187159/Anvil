"""JIT context service coordinating loading, caching, and prefetching."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .cache import ContextCache
from .contracts import ContextRequest, ContextResponse, JITContextMetrics
from .loader import ContextLoader
from .predictor import ContextPredictor

if TYPE_CHECKING:
    from anvil.agents.lead_agent.types import LeadAgentContext
    from anvil.config import EffectiveConfig
    from .contracts import JITContextConfig

logger = logging.getLogger(__name__)


class JITContextService:
    """Core JIT context service.

    Coordinates:
    - Lazy loading via ContextLoader
    - Caching via ContextCache
    - Prefetching via ContextPredictor
    - Metrics collection
    """

    def __init__(
        self,
        config: JITContextConfig,
        effective_config: EffectiveConfig | None = None
    ):
        """Initialize JIT context service.

        Args:
            config: JIT context configuration
            effective_config: Application configuration
        """
        self.config = config
        self.effective_config = effective_config

        # Initialize components
        self.cache = ContextCache(config)
        self.loader = ContextLoader(
            effective_config=effective_config,
            cache=self.cache,
            max_load_time_ms=config.max_load_time_ms,
            parallel_loading=config.parallel_loading,
            max_parallel_loads=config.max_parallel_loads
        )
        self.predictor = ContextPredictor(config)

        # Metrics
        self.metrics = JITContextMetrics()

    def load_context(
        self,
        request: ContextRequest,
        context: LeadAgentContext | None = None
    ) -> ContextResponse | None:
        """Load context on-demand.

        Args:
            request: Context load request
            context: Runtime context

        Returns:
            Context response if successful
        """
        if not self.config.enabled:
            return None

        # Update metrics
        self.metrics.total_requests += 1

        # Load context
        response = self.loader.load(request, context)

        if response:
            # Update metrics
            if response.cached:
                self.metrics.cache_hits += 1
            else:
                self.metrics.cache_misses += 1

            self.metrics.total_load_time_ms += response.load_time_ms
            self.metrics.total_tokens_loaded += response.tokens

            logger.info(
                f"Loaded {response.context_type}:{response.identifier} "
                f"({response.tokens} tokens, {response.load_time_ms:.1f}ms, "
                f"cached: {response.cached})"
            )

        return response

    def prefetch_context(
        self,
        messages: list,
        current_message: str | None = None,
        context: LeadAgentContext | None = None
    ) -> int:
        """Prefetch likely-needed context in background.

        Args:
            messages: Recent message history
            current_message: Current user message
            context: Runtime context

        Returns:
            Number of items prefetched
        """
        if not self.config.prefetch_enabled:
            return 0

        # Get predictions
        predictions = self.predictor.predict(messages, current_message)

        if not predictions:
            return 0

        # Prefetch predicted context
        prefetched = 0
        for pred in predictions:
            request = ContextRequest(
                context_type=pred.context_type,
                identifier=pred.identifier,
                priority=pred.priority,
                required=False  # Prefetch is optional
            )

            response = self.load_context(request, context)
            if response:
                prefetched += 1
                self.metrics.prefetch_hits += 1
            else:
                self.metrics.prefetch_misses += 1

        logger.info(f"Prefetched {prefetched}/{len(predictions)} predicted contexts")
        return prefetched

    def cleanup(self) -> None:
        """Cleanup expired cache entries."""
        if self.cache:
            self.cache.cleanup_expired()

    def get_metrics(self) -> dict:
        """Get service metrics.

        Returns:
            Dictionary with metrics
        """
        cache_stats = self.cache.get_stats() if self.cache else {}

        return {
            "enabled": self.config.enabled,
            "requests": {
                "total": self.metrics.total_requests,
                "cache_hits": self.metrics.cache_hits,
                "cache_misses": self.metrics.cache_misses,
                "cache_hit_rate": self.metrics.cache_hit_rate,
            },
            "prefetch": {
                "hits": self.metrics.prefetch_hits,
                "misses": self.metrics.prefetch_misses,
                "accuracy": self.metrics.prefetch_accuracy,
            },
            "performance": {
                "total_load_time_ms": self.metrics.total_load_time_ms,
                "avg_load_time_ms": self.metrics.avg_load_time_ms,
            },
            "tokens": {
                "loaded": self.metrics.total_tokens_loaded,
                "saved": self.metrics.total_tokens_saved,
                "reduction_rate": self.metrics.token_reduction_rate,
            },
            "cache": cache_stats,
        }
