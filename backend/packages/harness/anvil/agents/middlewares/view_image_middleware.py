"""Behavior enhancement layer.

Reads: messages, viewed_images
Writes: viewed_images
Side effects: injects model-visible multimodal image blocks after view_image tools complete
Failure behavior: fail-open; image injection is skipped on read/parse errors
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class ViewImageMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def before_model(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        update = self._view_image_tool_message_update(state_obj)
        if update is not None:
            image_count = _count_image_blocks(update.get("messages"))
            runtime.context.view_image_context = (
                f"{image_count} image(s) returned by view_image were attached as multimodal content."
            )
        return update

    def _view_image_tool_message_update(self, state_obj: LeadAgentState) -> dict[str, object] | None:
        messages = list(state_obj.messages or [])
        assistant = _last_assistant_with_view_image_call(messages)
        if assistant is None:
            return None
        assistant_index = messages.index(assistant)
        tool_call_ids = {
            str(tool_call.get("id"))
            for tool_call in (getattr(assistant, "tool_calls", None) or [])
            if tool_call.get("name") == "view_image" and tool_call.get("id")
        }
        if not tool_call_ids:
            return None
        tool_messages = [
            message
            for message in messages[assistant_index + 1 :]
            if isinstance(message, ToolMessage) and str(message.tool_call_id or "") in tool_call_ids
        ]
        if len(tool_messages) < len(tool_call_ids):
            return None
        if _has_injected_message_after(messages, assistant_index):
            return None

        seen = set(state_obj.viewed_images)
        content_blocks: list[dict[str, object]] = []
        viewed: list[str] = []
        for tool_message in tool_messages:
            extracted = _extract_view_image_blocks(tool_message.content)
            if extracted is None or extracted.reference in seen:
                continue
            content_blocks.extend(extracted.blocks)
            viewed.append(extracted.reference)
            seen.add(extracted.reference)
        if not content_blocks:
            return None
        injected = HumanMessage(
            content=content_blocks,
            additional_kwargs={
                "anvil_view_image_injection": True,
                "anvil_model_only": True,
                "visibility": "model_only",
            },
        )
        return {"messages": [injected], "viewed_images": sorted(seen)}


class _ExtractedImageBlocks:
    def __init__(self, *, reference: str, blocks: list[dict[str, object]]) -> None:
        self.reference = reference
        self.blocks = blocks


def _last_assistant_with_view_image_call(messages: list[object]) -> AIMessage | None:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if any(
            isinstance(tool_call, dict) and tool_call.get("name") == "view_image"
            for tool_call in (getattr(message, "tool_calls", None) or [])
        ):
            return message
    return None


def _has_injected_message_after(messages: list[object], assistant_index: int) -> bool:
    for message in messages[assistant_index + 1 :]:
        if isinstance(message, HumanMessage) and getattr(message, "additional_kwargs", {}).get("anvil_view_image_injection"):
            return True
    return False


def _extract_view_image_blocks(content: object) -> _ExtractedImageBlocks | None:
    if not isinstance(content, list):
        return None
    image_url: str | None = None
    reference: str | None = None
    mime_type: str | None = None
    size_bytes: str | None = None
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "image_url":
            raw_image = block.get("image_url")
            if isinstance(raw_image, dict):
                candidate = raw_image.get("url")
            else:
                candidate = raw_image
            if isinstance(candidate, str) and candidate.strip():
                image_url = candidate.strip()
            continue
        if block_type != "text":
            continue
        text = str(block.get("text") or "")
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            normalized_key = key.strip()
            normalized_value = value.strip()
            if normalized_key == "path" and normalized_value:
                reference = normalized_value
            elif normalized_key == "mime_type" and normalized_value:
                mime_type = normalized_value
            elif normalized_key == "size_bytes" and normalized_value:
                size_bytes = normalized_value
    if not image_url:
        return None
    reference = reference or image_url
    return _ExtractedImageBlocks(
        reference=reference,
        blocks=[
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    )


def _count_image_blocks(messages: object) -> int:
    if not isinstance(messages, list):
        return 0
    count = 0
    for message in messages:
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        count += sum(1 for block in content if isinstance(block, dict) and block.get("type") == "image_url")
    return count
