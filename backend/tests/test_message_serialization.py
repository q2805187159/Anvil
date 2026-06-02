from __future__ import annotations

from langchain_core.messages import AIMessage

from anvil.runtime.serialization import deserialize_message, serialize_message


def test_serialize_plain_text_ai_message_keeps_text_only() -> None:
    payload = serialize_message(AIMessage(content="hello"))
    assert payload["content"] == "hello"
    assert "content_blocks" not in payload


def test_serialize_block_content_normalizes_provider_payload() -> None:
    payload = serialize_message(
        AIMessage(
            content=[
                {"type": "thinking", "thinking": "reasoning"},
                {"type": "text", "text": "final answer"},
            ]
        )
    )

    assert payload["content"] == "final answer"
    assert payload["content_blocks"] == [
        {"type": "thinking", "text": "reasoning"},
        {"type": "text", "text": "final answer"},
    ]


def test_serialize_ai_message_strips_inline_think_tags() -> None:
    payload = serialize_message(
        AIMessage(content="<think>private reasoning</think>\n\nfinal answer")
    )

    assert payload["content"] == "final answer"
    assert "<think>" not in payload["content"]


def test_serialize_ai_message_preserves_reasoning_content_for_provider_replay() -> None:
    payload = serialize_message(
        AIMessage(
            content="<think>private reasoning</think>\n\nfinal answer",
            additional_kwargs={"reasoning_content": "private reasoning"},
        )
    )

    assert payload["content"] == "final answer"
    assert payload["additional_kwargs"] == {"reasoning_content": "private reasoning"}

    restored = deserialize_message(payload)
    assert restored.content == "final answer"
    assert restored.additional_kwargs["reasoning_content"] == "private reasoning"


def test_deserialize_preserves_normalized_blocks_in_metadata() -> None:
    message = deserialize_message(
        {
            "role": "ai",
            "content": "final answer",
            "content_blocks": [
                {"type": "thinking", "text": "reasoning"},
                {"type": "text", "text": "final answer"},
            ],
        }
    )

    assert message.content == "final answer"
    assert message.additional_kwargs["content_blocks"][0]["type"] == "thinking"
