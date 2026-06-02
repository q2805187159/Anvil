"""
Performance Timing Middleware

Tracks detailed timing for all request processing stages:
- Request parsing
- Middleware chain execution
- LLM calls
- Tool execution
- Response generation

Logs timing data for performance analysis and optimization.
"""

import time
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from anvil.agents.middleware import Middleware, MiddlewareContext


logger = logging.getLogger(__name__)


class TimingMiddleware(Middleware):
    """Middleware that tracks detailed timing for performance analysis."""

    def __init__(self):
        super().__init__()
        self.timings: Dict[str, Dict[str, Any]] = {}

    async def before_request(self, context: MiddlewareContext) -> None:
        """Start timing the request."""
        request_id = id(context)
        self.timings[request_id] = {
            "request_start": time.time(),
            "stages": {},
            "thread_id": getattr(context, "thread_id", None),
        }
        logger.info(f"[TIMING] Request started: {request_id}")

    async def before_model(self, context: MiddlewareContext) -> None:
        """Track time before LLM call."""
        request_id = id(context)
        if request_id in self.timings:
            self.timings[request_id]["stages"]["before_model"] = time.time()

            # Calculate middleware overhead
            request_start = self.timings[request_id]["request_start"]
            middleware_time = time.time() - request_start
            logger.info(f"[TIMING] Middleware overhead: {middleware_time:.3f}s")

    async def after_model(self, context: MiddlewareContext) -> None:
        """Track time after LLM call."""
        request_id = id(context)
        if request_id in self.timings:
            before_model_time = self.timings[request_id]["stages"].get("before_model")
            if before_model_time:
                llm_time = time.time() - before_model_time
                self.timings[request_id]["stages"]["llm_call"] = llm_time
                logger.info(f"[TIMING] LLM call: {llm_time:.3f}s")

    async def before_tool(self, context: MiddlewareContext, tool_name: str) -> None:
        """Track time before tool execution."""
        request_id = id(context)
        if request_id in self.timings:
            if "tools" not in self.timings[request_id]:
                self.timings[request_id]["tools"] = []

            self.timings[request_id]["tools"].append({
                "name": tool_name,
                "start": time.time(),
            })
            logger.info(f"[TIMING] Tool {tool_name} starting")

    async def after_tool(self, context: MiddlewareContext, tool_name: str, result: Any) -> None:
        """Track time after tool execution."""
        request_id = id(context)
        if request_id in self.timings and "tools" in self.timings[request_id]:
            tools = self.timings[request_id]["tools"]
            if tools:
                last_tool = tools[-1]
                if last_tool["name"] == tool_name and "duration" not in last_tool:
                    duration = time.time() - last_tool["start"]
                    last_tool["duration"] = duration
                    logger.info(f"[TIMING] Tool {tool_name}: {duration:.3f}s")

    async def after_response(self, context: MiddlewareContext) -> None:
        """Log final timing summary."""
        request_id = id(context)
        if request_id in self.timings:
            timing_data = self.timings[request_id]
            total_time = time.time() - timing_data["request_start"]

            # Calculate component times
            middleware_time = timing_data["stages"].get("before_model", timing_data["request_start"]) - timing_data["request_start"]
            llm_time = timing_data["stages"].get("llm_call", 0)
            tool_time = sum(t.get("duration", 0) for t in timing_data.get("tools", []))
            other_time = total_time - middleware_time - llm_time - tool_time

            logger.info(
                f"[TIMING] Request {request_id} complete: "
                f"total={total_time:.3f}s, "
                f"middleware={middleware_time:.3f}s ({middleware_time/total_time*100:.1f}%), "
                f"llm={llm_time:.3f}s ({llm_time/total_time*100:.1f}%), "
                f"tools={tool_time:.3f}s ({tool_time/total_time*100:.1f}%), "
                f"other={other_time:.3f}s ({other_time/total_time*100:.1f}%)"
            )

            # Log tool breakdown
            if timing_data.get("tools"):
                logger.info(f"[TIMING] Tool breakdown:")
                for tool in timing_data["tools"]:
                    duration = tool.get("duration", 0)
                    logger.info(f"  - {tool['name']}: {duration:.3f}s")

            # Clean up
            del self.timings[request_id]

    async def on_error(self, context: MiddlewareContext, error: Exception) -> None:
        """Log timing even on error."""
        request_id = id(context)
        if request_id in self.timings:
            total_time = time.time() - self.timings[request_id]["request_start"]
            logger.error(f"[TIMING] Request {request_id} failed after {total_time:.3f}s: {error}")
            del self.timings[request_id]
