from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from langchain_core.messages import BaseMessage


class TokenBudgetService:
    """Small no-dependency token budget helper with model hooks and a stable fallback."""

    def __init__(self, model: Any | None = None) -> None:
        self.model = model

    def count_text(self, text: str | None) -> int:
        value = text or ""
        if not value:
            return 0
        if self.model is not None:
            counter = getattr(self.model, "get_num_tokens", None)
            if callable(counter):
                try:
                    return int(counter(value))
                except Exception:
                    pass
        return max(len(value) // 4, 1)

    def count_object(self, value: Any) -> int:
        if isinstance(value, str):
            return self.count_text(value)
        try:
            payload = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        except TypeError:
            payload = str(value)
        return self.count_text(payload)

    def count_messages(self, messages: Iterable[BaseMessage]) -> int:
        message_list = list(messages)
        if self.model is not None:
            counter = getattr(self.model, "get_num_tokens_from_messages", None)
            if callable(counter):
                try:
                    return int(counter(message_list))
                except Exception:
                    pass

        total = 0
        for message in message_list:
            total += 16
            total += self.count_object(getattr(message, "content", ""))
            for tool_call in getattr(message, "tool_calls", None) or []:
                total += self.count_object(tool_call.get("args", {}))
        return total

    def truncate_text(
        self,
        text: str,
        *,
        max_tokens: int | None = None,
        max_chars: int | None = None,
        marker: str = "...",
    ) -> str:
        if max_chars is not None and max_chars > 0 and len(text) > max_chars:
            text = text[: max(max_chars - len(marker), 0)].rstrip() + marker
        if max_tokens is None or max_tokens <= 0:
            return text
        if self.count_text(text) <= max_tokens:
            return text

        char_limit = max(max_tokens * 4, len(marker))
        clipped = text[: max(char_limit - len(marker), 0)].rstrip() + marker
        while clipped and self.count_text(clipped) > max_tokens:
            char_limit = max(int(char_limit * 0.8), len(marker))
            clipped = text[: max(char_limit - len(marker), 0)].rstrip() + marker
            if char_limit <= len(marker) + 4:
                break
        return clipped
