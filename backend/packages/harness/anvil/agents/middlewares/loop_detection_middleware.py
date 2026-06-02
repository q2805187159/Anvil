from __future__ import annotations

import hashlib
import json
from collections import OrderedDict, defaultdict

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


def _hash_tool_calls(tool_calls: list[dict[str, object]]) -> str:
    normalized = [
        {
            "name": tool_call.get("name", ""),
            "args": tool_call.get("args", {}),
        }
        for tool_call in tool_calls
    ]
    normalized.sort(
        key=lambda item: (
            item["name"],
            json.dumps(item["args"], sort_keys=True, default=str),
        )
    )
    payload = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]


class LoopDetectionMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def __init__(
        self,
        warn_threshold: int = 3,
        hard_limit: int = 5,
        window_size: int = 20,
        max_tracked_runs: int = 100,
    ) -> None:
        super().__init__()
        self.warn_threshold = warn_threshold
        self.hard_limit = hard_limit
        self.window_size = window_size
        self.max_tracked_runs = max_tracked_runs
        self._history: OrderedDict[str, list[str]] = OrderedDict()
        self._warned: dict[str, set[str]] = defaultdict(set)

    def after_model(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        if not state_obj.messages:
            return None

        last_message = state_obj.messages[-1]
        tool_calls = getattr(last_message, "tool_calls", None) or []
        if getattr(last_message, "type", None) != "ai" or not tool_calls:
            return None

        run_id = getattr(runtime.context, "run_id", None) or runtime.context.thread_id
        call_hash = _hash_tool_calls(tool_calls)
        self._touch_run(run_id)
        history = self._history.setdefault(run_id, [])
        history.append(call_hash)
        if len(history) > self.window_size:
            self._history[run_id] = history[-self.window_size :]
        count = self._history[run_id].count(call_hash)

        if count >= self.hard_limit:
            updated_message = last_message.model_copy(
                update={
                    "tool_calls": [],
                    "content": self._append_text(
                        last_message.content,
                        "I stopped a repeated internal tool loop. I will answer from the available results.",
                    ),
                }
            )
            reason = f"Repeated internal tool loop stopped after {count} identical tool-call rounds."
            state_obj.stream_interrupted = True
            state_obj.interrupted_stream = True
            state_obj.interrupted_stream_reason = reason
            return {
                "messages": [updated_message],
                "stream_interrupted": True,
                "interrupted_stream": True,
                "interrupted_stream_reason": reason,
            }

        if count >= self.warn_threshold and call_hash not in self._warned[run_id]:
            self._warned[run_id].add(call_hash)
            return None
        return None

    def _touch_run(self, run_id: str) -> None:
        if run_id in self._history:
            self._history.move_to_end(run_id)
        else:
            self._history[run_id] = []
        while len(self._history) > self.max_tracked_runs:
            evicted_run_id, _ = self._history.popitem(last=False)
            self._warned.pop(evicted_run_id, None)

    def _append_text(self, content: object, text: str) -> str | list[dict[str, str]]:
        if content is None:
            return text
        if isinstance(content, list):
            return [*content, {"type": "text", "text": f"\n\n{text}"}]
        return f"{content}\n\n{text}"
