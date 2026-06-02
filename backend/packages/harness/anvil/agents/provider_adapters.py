from __future__ import annotations

from functools import cached_property
import json
from typing import Any, Mapping

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI


_MINIMAX_ANTHROPIC_PREFIXES = (
    "https://api.minimax.io/anthropic",
    "https://api.minimaxi.com/anthropic",
)
_ANTHROPIC_TOOL_STREAMING_BETA = "fine-grained-tool-streaming-2025-05-14"


def anthropic_compatible_headers(base_url: str | None) -> dict[str, str]:
    normalized = _normalized_url(base_url)
    if not normalized:
        return {}
    betas = [
        "interleaved-thinking-2025-05-14",
        "fine-grained-tool-streaming-2025-05-14",
    ]
    if _requires_bearer_auth(normalized):
        betas = [beta for beta in betas if beta != _ANTHROPIC_TOOL_STREAMING_BETA]
    return {"anthropic-beta": ",".join(betas)} if betas else {}


class AnvilAnthropicChatModel(ChatAnthropic):
    """LangChain Anthropic adapter with third-party endpoint compatibility."""

    bearer_auth: bool = False

    @cached_property
    def _client_params(self) -> dict[str, Any]:
        params = dict(super()._client_params)
        if self.bearer_auth:
            api_key = params.pop("api_key", None)
            if api_key:
                params["auth_token"] = api_key
        return params


class AnvilOpenAIChatModel(ChatOpenAI):
    """OpenAI-compatible adapter that preserves provider reasoning replay fields."""

    def _get_request_payload(self, input_, *, stop=None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = self._convert_input(input_).to_messages()
        request_messages = payload.get("messages")
        if isinstance(request_messages, list):
            for source, target in zip(messages, request_messages, strict=False):
                if isinstance(source, AIMessage) and isinstance(target, dict):
                    _inject_provider_reasoning_replay(source, target)
                    _repair_openai_tool_call_arguments(target)
        return payload

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info=generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        choices = response_dict.get("choices") if isinstance(response_dict, dict) else None
        if not isinstance(choices, list):
            return result
        for generation, choice in zip(result.generations, choices, strict=False):
            raw_message = choice.get("message") if isinstance(choice, dict) else None
            reasoning_content = raw_message.get("reasoning_content") if isinstance(raw_message, dict) else None
            if reasoning_content and isinstance(generation.message, AIMessage):
                generation.message.additional_kwargs["reasoning_content"] = reasoning_content
        return result

    def _convert_chunk_to_generation_chunk(self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None):
        generation_chunk = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if generation_chunk is None:
            return None
        choice = _first_choice(chunk)
        delta = choice.get("delta") if isinstance(choice, dict) else None
        reasoning_content = delta.get("reasoning_content") if isinstance(delta, dict) else None
        if reasoning_content and isinstance(generation_chunk.message, AIMessageChunk):
            generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning_content
        return generation_chunk


def _inject_provider_reasoning_replay(message: AIMessage, target: dict[str, Any]) -> None:
    reasoning_content = message.additional_kwargs.get("reasoning_content")
    if reasoning_content is not None:
        target["reasoning_content"] = reasoning_content


def _repair_openai_tool_call_arguments(target: dict[str, Any]) -> None:
    tool_calls = target.get("tool_calls")
    if not isinstance(tool_calls, list):
        return
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict) or "arguments" not in function:
            continue
        function["arguments"] = _valid_json_argument_string(function.get("arguments"))


def _valid_json_argument_string(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if not isinstance(value, str):
        return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        parsed = _repair_json_object_with_unescaped_backslashes(value)
    if isinstance(parsed, dict):
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return json.dumps({}, ensure_ascii=False, separators=(",", ":"))


def _repair_json_object_with_unescaped_backslashes(value: str) -> dict[str, Any]:
    repaired = value.replace("\\", "\\\\")
    try:
        parsed = json.loads(repaired)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_choice(chunk: dict[str, Any]) -> dict[str, Any] | None:
    choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0]
    return None


def anthropic_compatible_overrides(
    *,
    base_url: str | None,
    api_key: str | None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    normalized = _normalized_url(base_url)
    overrides: dict[str, Any] = {}
    if _requires_bearer_auth(normalized):
        overrides["bearer_auth"] = True
    merged_headers = dict(headers or {})
    for key, value in anthropic_compatible_headers(normalized).items():
        merged_headers.setdefault(key, value)
    if merged_headers:
        overrides["default_headers"] = merged_headers
    if api_key and _is_third_party_anthropic_endpoint(normalized):
        overrides["api_key"] = api_key
    return overrides


def _requires_bearer_auth(base_url: str | None) -> bool:
    normalized = _normalized_url(base_url)
    return any(normalized.startswith(prefix) for prefix in _MINIMAX_ANTHROPIC_PREFIXES)


def _is_third_party_anthropic_endpoint(base_url: str | None) -> bool:
    normalized = _normalized_url(base_url)
    if not normalized:
        return False
    return "anthropic.com" not in normalized


def _normalized_url(base_url: str | None) -> str:
    return str(base_url or "").strip().rstrip("/").lower()
