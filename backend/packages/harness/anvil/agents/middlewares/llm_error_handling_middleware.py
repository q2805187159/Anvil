"""Safety and stability layer.

Reads: retry config, request/system prompt
Writes: interrupted_stream flags for partial failures
Side effects: bounded retry/backoff
Failure behavior: retries transient failures, otherwise raises typed runtime errors
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class LLMExecutionError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class LLMErrorHandlingMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def wrap_model_call(self, request, handler):
        return self._call_with_retry(request, handler)

    def _call_with_retry(self, request, handler: Callable[[Any], Any]) -> Any:
        retry = request.runtime.context.config_result.effective_config.llm.retry
        attempts = max(1, retry.max_attempts)
        delay = max(retry.initial_delay, 0.0)
        for attempt in range(1, attempts + 1):
            try:
                return handler(request)
            except Exception as exc:  # noqa: BLE001
                category = self._categorize_error(exc)
                if category == "transient" and attempt < attempts:
                    time.sleep(min(delay, retry.max_delay))
                    delay = min(delay * max(retry.backoff_multiplier, 1.0), retry.max_delay)
                    continue
                if category == "overload" and attempt < attempts:
                    request.runtime.context.emergency_summarize_triggered = True
                    request.runtime.context.emergency_summarize_reason = str(exc)
                    continue
                if category == "partial_stream":
                    request.runtime.context.interrupted_stream = True
                    request.runtime.context.interrupted_stream_reason = str(exc)
                    raise LLMExecutionError(
                        "partial_stream",
                        f"Model stream interrupted after partial delivery: {exc}",
                    ) from exc
                raise LLMExecutionError(
                    category,
                    self._format_failure(category, exc, attempt=attempt, attempts=attempts),
                ) from exc

    def _categorize_error(self, exc: Exception) -> str:
        detail = str(exc).lower()
        if any(
            token in detail
            for token in (
                "invalid_api_key",
                "invalid api key",
                "api key invalid",
                "401",
                "auth",
                "unauthorized",
                "model not found",
                "permission denied",
            )
        ):
            return "fatal"
        if any(
            token in detail
            for token in (
                "rate limit",
                "429",
                "timeout",
                "temporarily unavailable",
                "503",
                "concurrency limit",
                "too many requests",
                "retry later",
                "try again later",
                "overloaded",
                "server busy",
            )
        ):
            return "transient"
        if any(token in detail for token in ("context length", "token limit", "too many tokens", "context window")):
            return "overload"
        if any(token in detail for token in ("stream interrupted", "chunkedencodingerror", "incomplete read")):
            return "partial_stream"
        return "fatal"

    def _format_failure(self, category: str, exc: Exception, *, attempt: int, attempts: int) -> str:
        if category in {"transient", "overload"} and attempts > 1:
            return f"Model execution failed ({category}) after {attempts} attempts on the selected model: {exc}"
        return f"Model execution failed ({category}): {exc}"
