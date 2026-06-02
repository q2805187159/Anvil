from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


AI_PROVIDER_REPLAY_KEYS = frozenset(
    {
        "reasoning_content",
        "raw_content",
        "provider_content",
    }
)


def serialize_message(message: BaseMessage) -> dict[str, Any]:
    content_source = message.content
    if isinstance(message, HumanMessage):
        display_content = message.additional_kwargs.get("display_content")
        if isinstance(display_content, (str, list)):
            content_source = display_content
    elif isinstance(message, AIMessage):
        content_source = _ai_content_source(message)

    content_text, content_blocks = normalize_message_content(
        content_source
    )
    payload: dict[str, Any] = {
        "role": message.type,
        "content": content_text,
    }
    if getattr(message, "id", None) is not None:
        payload["id"] = message.id
    if content_blocks:
        payload["content_blocks"] = content_blocks

    if isinstance(message, AIMessage) and message.tool_calls:
        payload["tool_calls"] = message.tool_calls
    if isinstance(message, AIMessage):
        additional_kwargs = _ai_additional_kwargs_for_storage(message)
        if additional_kwargs:
            payload["additional_kwargs"] = additional_kwargs
    if isinstance(message, HumanMessage):
        additional_kwargs = _human_additional_kwargs_for_storage(message.additional_kwargs)
        if additional_kwargs:
            payload["additional_kwargs"] = additional_kwargs
    if isinstance(message, ToolMessage):
        payload["tool_call_id"] = message.tool_call_id
        payload["name"] = message.name
        payload["status"] = message.status
    return payload


def serialize_messages(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    return [serialize_message(message) for message in messages]


def deserialize_message(payload: dict[str, Any]) -> BaseMessage:
    role = payload.get("role", "human")
    content = payload.get("content", "")
    content_blocks = payload.get("content_blocks")

    if role == "human":
        additional_kwargs = payload.get("additional_kwargs")
        return HumanMessage(
            content=content,
            id=payload.get("id"),
            additional_kwargs=additional_kwargs if isinstance(additional_kwargs, dict) else {},
        )
    if role == "system":
        return SystemMessage(content=content, id=payload.get("id"))
    if role == "ai":
        additional_kwargs = _ai_additional_kwargs_from_payload(payload)
        if content_blocks:
            additional_kwargs["content_blocks"] = content_blocks
        return AIMessage(
            content=content,
            tool_calls=payload.get("tool_calls", []),
            additional_kwargs=additional_kwargs,
            id=payload.get("id"),
        )
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=payload["tool_call_id"],
            name=payload.get("name"),
            status=payload.get("status"),
            id=payload.get("id"),
        )
    return HumanMessage(content=content, id=payload.get("id"))


def deserialize_messages(payloads: list[dict[str, Any]]) -> list[BaseMessage]:
    return [deserialize_message(payload) for payload in payloads]


THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
THINK_START_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
THINK_END_RE = re.compile(r"</think>", re.IGNORECASE)


def strip_inline_thinking_tags(text: str) -> str:
    lowered = text.lower()
    if "<think" not in lowered and "</think>" not in lowered:
        return text
    cleaned = THINK_BLOCK_RE.sub("", text)
    cleaned = THINK_END_RE.sub("", cleaned)
    start = THINK_START_RE.search(cleaned)
    if start is not None:
        cleaned = cleaned[: start.start()]
    return cleaned.lstrip()


def normalize_message_content(content: Any) -> tuple[str, list[dict[str, str]]]:
    if isinstance(content, str):
        return strip_inline_thinking_tags(content), []

    blocks: list[dict[str, str]] = []
    text_parts: list[str] = []
    fallback_parts: list[str] = []

    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                text = strip_inline_thinking_tags(item)
                if text:
                    blocks.append({"type": "text", "text": text})
                    text_parts.append(text)
                continue

            if not isinstance(item, dict):
                continue

            block_type = str(item.get("type", "text"))
            if block_type == "text":
                text = strip_inline_thinking_tags(str(item.get("text", "")))
                if text:
                    blocks.append({"type": "text", "text": text})
                    text_parts.append(text)
                continue

            if block_type == "thinking":
                thinking = str(item.get("thinking", item.get("text", "")))
                if thinking:
                    blocks.append({"type": "thinking", "text": thinking})
                    fallback_parts.append(thinking)
                continue

            text = str(item.get("text", item.get("thinking", "")))
            if text:
                blocks.append({"type": block_type, "text": text})
                fallback_parts.append(text)

    normalized_text = "\n".join(part for part in text_parts if part)
    if not normalized_text:
        normalized_text = "\n".join(part for part in fallback_parts if part)
    return normalized_text, blocks


def _ai_content_source(message: AIMessage) -> Any:
    if "content_blocks" in message.additional_kwargs:
        return message.additional_kwargs["content_blocks"]
    return message.content


def _ai_additional_kwargs_for_storage(message: AIMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in AI_PROVIDER_REPLAY_KEYS:
        value = message.additional_kwargs.get(key)
        if value is not None:
            payload[key] = _json_compatible(value)
    return payload


def _ai_additional_kwargs_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    additional_kwargs = payload.get("additional_kwargs")
    if not isinstance(additional_kwargs, dict):
        return {}
    return {
        str(key): value
        for key, value in additional_kwargs.items()
        if str(key) in AI_PROVIDER_REPLAY_KEYS
    }


def _human_additional_kwargs_for_storage(additional_kwargs: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    client_message_id = additional_kwargs.get("client_message_id")
    if isinstance(client_message_id, str) and client_message_id:
        payload["client_message_id"] = client_message_id
    files = additional_kwargs.get("files")
    if isinstance(files, list):
        normalized_files = [_json_compatible(item) for item in files]
        payload["files"] = [item for item in normalized_files if isinstance(item, dict)]
    uploaded_filenames = additional_kwargs.get("uploaded_filenames")
    if isinstance(uploaded_filenames, (list, tuple)):
        payload["uploaded_filenames"] = [str(item) for item in uploaded_filenames if item is not None]
    return payload


def _json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return str(value)


def translate_message_for_runtime(message: BaseMessage, *, path_service, thread_id: str) -> BaseMessage:
    translated_content = path_service.translate_user_data_to_runtime(message.content, thread_id=thread_id)
    if isinstance(message, HumanMessage):
        return message.model_copy(update={"content": translated_content})
    if isinstance(message, SystemMessage):
        return message.model_copy(update={"content": translated_content})
    if isinstance(message, ToolMessage):
        return message.model_copy(update={"content": translated_content})
    if isinstance(message, AIMessage):
        return message.model_copy(
            update={
                "content": translated_content,
                "tool_calls": path_service.translate_user_data_to_runtime(message.tool_calls, thread_id=thread_id),
                "additional_kwargs": path_service.translate_user_data_to_runtime(message.additional_kwargs, thread_id=thread_id),
            }
        )
    return message
