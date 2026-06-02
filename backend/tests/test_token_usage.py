from __future__ import annotations

from langchain_core.messages import AIMessage

from anvil.config import ModelConfig, TokenUsageConfig
from anvil.runtime.token_usage import aggregate_token_usage_from_messages


def test_token_usage_aggregates_multiple_provider_payloads() -> None:
    model_config = ModelConfig(
        name="minimax",
        provider="minimax",
        model="MiniMax-M1",
    )
    usage = aggregate_token_usage_from_messages(
        [
            AIMessage(
                content="call tools",
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "input_token_details": {"cached_tokens": 25},
                },
            ),
            AIMessage(
                content="final",
                response_metadata={
                    "usage": {
                        "prompt_tokens": 140,
                        "completion_tokens": 20,
                        "completion_tokens_details": {"reasoning_tokens": 7},
                    },
                    "model_name": "MiniMax-M1",
                },
            ),
        ],
        model_config=model_config,
        route_model_name="minimax",
    )

    assert usage["provider"] == "minimax"
    assert usage["model"] == "minimax"
    assert usage["concrete_model"] == "MiniMax-M1"
    assert usage["request_count"] == 2
    assert usage["input_tokens"] == 240
    assert usage["output_tokens"] == 30
    assert usage["total_tokens"] == 270
    assert usage["cache_read_tokens"] == 25
    assert usage["reasoning_tokens"] == 7
    assert usage["total"]["input_tokens"] == 240
    assert usage["total"]["output_tokens"] == 30
    assert usage["total"]["total_tokens"] == 270
    assert usage["last"]["input_tokens"] == 140
    assert usage["last"]["output_tokens"] == 20
    assert usage["last"]["total_tokens"] == 160
    assert usage["last"]["reasoning_tokens"] == 7
    assert usage["entries"][1]["provider_model"] == "MiniMax-M1"


def test_token_usage_estimates_cost_from_configured_provider_pricing() -> None:
    model_config = ModelConfig(
        name="openrouter",
        provider="openrouter",
        model="openai/gpt-5.4",
    )
    token_usage_config = TokenUsageConfig(
        enabled=True,
        cost_precision=10,
        pricing={
            "openrouter/openai/gpt-5.4": {
                "input_cost_per_million": 1.25,
                "output_cost_per_million": 10.0,
                "cache_read_cost_per_million": 0.125,
                "reasoning_cost_per_million": 10.0,
                "source": "test-pricing",
            }
        },
    )

    usage = aggregate_token_usage_from_messages(
        [
            AIMessage(
                content="done",
                usage_metadata={
                    "input_tokens": 1_000,
                    "output_tokens": 200,
                    "total_tokens": 1_200,
                    "cache_read_tokens": 400,
                    "reasoning_tokens": 50,
                },
            )
        ],
        model_config=model_config,
        route_model_name="openrouter",
        token_usage_config=token_usage_config,
    )

    assert usage["cost_status"] == "estimated"
    assert usage["pricing_source"] == "test-pricing"
    assert usage["cost"]["pricing_key"] == "openrouter/openai/gpt-5.4"
    assert usage["estimated_cost_usd"] == 0.0038


def test_token_usage_can_hide_per_request_entries() -> None:
    usage = aggregate_token_usage_from_messages(
        [AIMessage(content="done", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})],
        token_usage_config=TokenUsageConfig(enabled=True, include_entries=False),
    )

    assert usage["request_count"] == 1
    assert usage["entries"] == []
    assert usage["total"]["input_tokens"] == 10
    assert usage["total"]["output_tokens"] == 5
    assert usage["last"]["input_tokens"] == 10
    assert usage["last"]["output_tokens"] == 5


def test_token_usage_enriches_previous_flat_summary_without_new_provider_payloads() -> None:
    usage = aggregate_token_usage_from_messages(
        [AIMessage(content="no usage metadata")],
        previous={"input_tokens": 8, "output_tokens": 3, "total_tokens": 11, "request_count": 1},
    )

    assert usage["input_tokens"] == 8
    assert usage["output_tokens"] == 3
    assert usage["total"]["total_tokens"] == 11
    assert usage["last"]["input_tokens"] == 8
    assert usage["last"]["output_tokens"] == 3
