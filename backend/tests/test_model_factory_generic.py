from __future__ import annotations

import json
import pytest
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage

from anvil.agents.model_factory import create_chat_model
from anvil.agents.provider_adapters import AnvilOpenAIChatModel
from anvil.config import ModelConfig, ProviderKind
from fake_chat_provider import (
    CapturingChatModel,
    CapturingReasoningCliChatModel,
    FailingSecretChatModel,
    StrictChatModel,
    TypeErrorRetryChatModel,
)


def test_factory_resolves_class_path_and_passes_common_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="openai-main",
        display_name="OpenAI Main",
        use="fake_chat_provider:CapturingChatModel",
        model="gpt-5.4",
        api_key="$OPENAI_API_KEY",
        base_url="https://example.test/v1",
        temperature=0.3,
        max_tokens=2048,
        provider="openai",
        provider_kind="openai_compatible",
    )

    create_chat_model(model)
    assert CapturingChatModel.captured_kwargs["model"] == "gpt-5.4"
    assert CapturingChatModel.captured_kwargs["api_key"] == "secret-key"
    assert CapturingChatModel.captured_kwargs["base_url"] == "https://example.test/v1"
    assert CapturingChatModel.captured_kwargs["temperature"] == 0.3
    assert CapturingChatModel.captured_kwargs["max_completion_tokens"] == 2048


def test_factory_infers_openai_compatible_provider_from_v1_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="inferred-openai",
        model="gpt-5.4",
        api_key="$OPENAI_API_KEY",
        base_url="https://gateway.example/v1",
        max_tokens=2048,
    )

    assert model.provider_kind == ProviderKind.OPENAI_COMPATIBLE
    assert model.provider == "openai"
    assert model.resolved_use_path() == "anvil.agents.provider_adapters:AnvilOpenAIChatModel"


def test_openai_adapter_replays_reasoning_content_on_assistant_history() -> None:
    model = AnvilOpenAIChatModel(model="mimo-test", api_key="test-key", base_url="https://example.test/v1")

    payload = model._get_request_payload(
        [
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "private reasoning"},
                tool_calls=[
                    {
                        "name": "list_dir",
                        "args": {"path": "/mnt/user-data/workspace"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content="[]", tool_call_id="call_1"),
        ]
    )

    assert payload["messages"][0]["role"] == "assistant"
    assert payload["messages"][0]["reasoning_content"] == "private reasoning"
    assert payload["messages"][0]["tool_calls"]


def test_openai_adapter_repairs_invalid_tool_call_argument_json_for_replay() -> None:
    model = AnvilOpenAIChatModel(model="mimo-test", api_key="test-key", base_url="https://example.test/v1")
    invalid_windows_path_json = '{"path":"E:\临时下载"}'

    payload = model._get_request_payload(
        [
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "file_info",
                                "arguments": invalid_windows_path_json,
                            },
                        }
                    ]
                },
            ),
            ToolMessage(content="{}", tool_call_id="call_1"),
        ]
    )

    arguments = payload["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"path": "E:\临时下载"}


def test_factory_infers_anthropic_compatible_provider_from_anthropic_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="inferred-anthropic",
        model="MiniMax-M2.7",
        api_key="$MINIMAX_API_KEY",
        base_url="https://api.minimaxi.com/anthropic",
        max_tokens=2048,
    )

    assert model.provider_kind == ProviderKind.ANTHROPIC_COMPATIBLE
    assert model.provider == "anthropic"
    assert model.resolved_use_path() == "anvil.agents.provider_adapters:AnvilAnthropicChatModel"


def test_factory_uses_default_model_from_provider_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIMO_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="MiMo",
        use="fake_chat_provider:CapturingChatModel",
        model=[
            "mimo-v2.5-pro",
            "mimo-v2.5",
            "mimo-v2-pro",
            "mimo-v2-omni",
            "mimo-v2-flash",
        ],
        default_model="mimo-v2-flash",
        api_key="$MIMO_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
    )

    create_chat_model(model)
    assert model.model_catalog == [
        "mimo-v2.5-pro",
        "mimo-v2.5",
        "mimo-v2-pro",
        "mimo-v2-omni",
        "mimo-v2-flash",
    ]
    assert CapturingChatModel.captured_kwargs["model"] == "mimo-v2-flash"


def test_factory_passes_provider_settings_and_responses_api_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="responses",
        use="fake_chat_provider:CapturingChatModel",
        model="gpt-5.4",
        api_key="$OPENAI_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
        use_responses_api=True,
        output_version="responses/v1",
        provider_settings={"timeout": 30},
    )

    create_chat_model(model)
    assert CapturingChatModel.captured_kwargs["timeout"] == 30
    assert CapturingChatModel.captured_kwargs["use_responses_api"] is True
    assert CapturingChatModel.captured_kwargs["output_version"] == "responses/v1"


def test_factory_resolves_provider_setting_secret_refs_at_instantiation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="gemini-native",
        use="fake_chat_provider:CapturingChatModel",
        model="gemini-2.5-pro",
        provider="google",
        provider_settings={"gemini_api_key": "$GEMINI_API_KEY"},
    )

    create_chat_model(model)
    assert CapturingChatModel.captured_kwargs["gemini_api_key"] == "gemini-secret"


def test_factory_requires_provider_setting_secret_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_PROVIDER_SETTING_KEY", raising=False)

    model = ModelConfig(
        name="secret-provider-setting",
        use="fake_chat_provider:CapturingChatModel",
        model="provider-model",
        provider="google",
        provider_settings={"gemini_api_key": "$MISSING_PROVIDER_SETTING_KEY"},
    )

    with pytest.raises(ValueError, match="MISSING_PROVIDER_SETTING_KEY"):
        create_chat_model(model)


def test_factory_passes_generic_model_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="extended",
        use="fake_chat_provider:CapturingChatModel",
        model="gpt-5.4",
        api_key="$OPENAI_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
        timeout=600.0,
        max_retries=2,
        top_p=0.9,
        default_headers={"X-Test": "1"},
        extra_body={"reasoning": {"summary": "auto"}},
        provider_settings={"extra_body": {"metadata": {"source": "test"}}},
    )

    create_chat_model(model)
    assert CapturingChatModel.captured_kwargs["timeout"] == 600.0
    assert CapturingChatModel.captured_kwargs["max_retries"] == 2
    assert CapturingChatModel.captured_kwargs["top_p"] == 0.9
    assert CapturingChatModel.captured_kwargs["default_headers"] == {"X-Test": "1"}
    assert CapturingChatModel.captured_kwargs["extra_body"] == {
        "metadata": {"source": "test"},
        "reasoning": {"summary": "auto"},
    }


def test_factory_applies_thinking_overlay_only_when_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="thinking-model",
        use="fake_chat_provider:CapturingChatModel",
        model="gpt-5.4",
        api_key="$OPENAI_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
        supports_thinking=True,
        when_thinking_enabled={"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 2000}}},
    )

    create_chat_model(model, thinking_enabled=True)
    assert CapturingChatModel.captured_kwargs["extra_body"] == {"thinking": {"type": "enabled", "budget_tokens": 2000}}

    unsupported = model.model_copy(update={"supports_thinking": False})
    with pytest.raises(ValueError, match="does not support thinking"):
        create_chat_model(unsupported, thinking_enabled=True)


def test_factory_merges_thinking_shortcut_with_when_thinking_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="thinking-shortcut",
        use="fake_chat_provider:CapturingChatModel",
        model="claude-test",
        api_key="$ANTHROPIC_API_KEY",
        provider="anthropic",
        provider_kind="anthropic_compatible",
        supports_thinking=True,
        when_thinking_enabled={"max_tokens_to_sample": 2048},
        thinking={"type": "enabled", "budget_tokens": 1024},
    )

    create_chat_model(model, thinking_enabled=True)
    assert CapturingChatModel.captured_kwargs["model_name"] == "claude-test"
    assert CapturingChatModel.captured_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert CapturingChatModel.captured_kwargs["max_tokens_to_sample"] == 2048


def test_factory_disables_openai_compatible_thinking_with_provider_specific_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="openai-thinking",
        use="fake_chat_provider:CapturingChatModel",
        model="gpt-5.4",
        api_key="$OPENAI_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
        supports_thinking=True,
        supports_reasoning_effort=True,
        when_thinking_enabled={"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 2000}}},
    )

    create_chat_model(model, thinking_enabled=False)
    assert CapturingChatModel.captured_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert CapturingChatModel.captured_kwargs["reasoning_effort"] == "minimal"


def test_factory_disables_vllm_thinking_with_chat_template_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="vllm-thinking",
        use="fake_chat_provider:CapturingChatModel",
        model="Qwen/Qwen3-32B",
        api_key="$VLLM_API_KEY",
        provider="vllm",
        provider_kind="vllm_openai_compatible",
        supports_thinking=True,
        when_thinking_enabled={"extra_body": {"chat_template_kwargs": {"thinking": True}}},
        provider_settings={"extra_body": {"top_k": 20}},
    )

    create_chat_model(model, thinking_enabled=False)
    assert CapturingChatModel.captured_kwargs["extra_body"] == {
        "top_k": 20,
        "chat_template_kwargs": {"thinking": False},
    }


def test_factory_disables_anthropic_thinking_with_direct_thinking_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="anthropic-thinking",
        use="fake_chat_provider:CapturingChatModel",
        model="claude-test",
        api_key="$ANTHROPIC_API_KEY",
        provider="anthropic",
        provider_kind="anthropic_compatible",
        supports_thinking=True,
        when_thinking_enabled={"thinking": {"type": "enabled", "budget_tokens": 2000}},
    )

    create_chat_model(model, thinking_enabled=False)
    assert CapturingChatModel.captured_kwargs["thinking"] == {"type": "disabled"}


def test_factory_prefers_explicit_when_thinking_disabled_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="explicit-thinking-toggle",
        use="fake_chat_provider:CapturingChatModel",
        model="gpt-5.4",
        api_key="${OPENAI_API_KEY}",
        provider="openai",
        provider_kind="openai_compatible",
        supports_thinking=True,
        supports_reasoning_effort=True,
        when_thinking_enabled={"extra_body": {"thinking": {"type": "enabled"}}},
        when_thinking_disabled={
            "extra_body": {"thinking": {"type": "disabled"}, "chat_template_kwargs": {"enable_thinking": False}},
            "reasoning_effort": "none",
        },
    )

    create_chat_model(model, thinking_enabled=False)
    assert CapturingChatModel.captured_kwargs["api_key"] == "secret-key"
    assert CapturingChatModel.captured_kwargs["extra_body"] == {
        "thinking": {"type": "disabled"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert CapturingChatModel.captured_kwargs["reasoning_effort"] == "none"


def test_factory_preserves_reasoning_effort_only_when_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    supported = ModelConfig(
        name="reasoning-model",
        use="fake_chat_provider:CapturingChatModel",
        model="gpt-5.4",
        api_key="$OPENAI_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
        supports_reasoning_effort=True,
        default_reasoning_effort="xhigh",
    )
    create_chat_model(supported)
    assert CapturingChatModel.captured_kwargs["reasoning_effort"] == "xhigh"

    CapturingChatModel.captured_kwargs = {}
    unsupported = supported.model_copy(update={"supports_reasoning_effort": False})
    create_chat_model(unsupported)
    assert "reasoning_effort" not in CapturingChatModel.captured_kwargs


def test_factory_applies_special_reasoning_provider_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingReasoningCliChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="reasoning_cli",
        use="fake_chat_provider:CapturingReasoningCliChatModel",
        model="gpt-5.4",
        api_key="$OPENAI_API_KEY",
        provider="reasoning_cli",
        provider_kind="openai_compatible",
        supports_thinking=True,
        supports_reasoning_effort=True,
        max_tokens=4096,
    )

    create_chat_model(model, thinking_enabled=False)
    assert CapturingReasoningCliChatModel.captured_kwargs["reasoning_effort"] == "none"
    assert "max_completion_tokens" not in CapturingReasoningCliChatModel.captured_kwargs

    CapturingReasoningCliChatModel.captured_kwargs = {}
    create_chat_model(model, thinking_enabled=True)
    assert CapturingReasoningCliChatModel.captured_kwargs["reasoning_effort"] == "medium"
    assert "max_completion_tokens" not in CapturingReasoningCliChatModel.captured_kwargs


def test_factory_uses_anvil_anthropic_adapter_for_minimax_bearer_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "secret-key")

    model = ModelConfig(
        name="minimax-anthropic",
        use="anvil.agents.provider_adapters:AnvilAnthropicChatModel",
        model="MiniMax-M2.7",
        api_key="$MINIMAX_API_KEY",
        base_url="https://api.minimaxi.com/anthropic",
        provider="anthropic",
        provider_kind="anthropic_compatible",
        max_tokens=2048,
    )

    chat_model = create_chat_model(model)
    assert chat_model.bearer_auth is True
    assert chat_model._client_params["auth_token"] == "secret-key"
    assert "api_key" not in chat_model._client_params
    assert "fine-grained-tool-streaming" not in chat_model._client_params["default_headers"]["anthropic-beta"]
    assert chat_model.max_tokens == 2048


def test_factory_drops_configured_provider_constructor_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="no-temperature",
        use="fake_chat_provider:CapturingChatModel",
        model="gateway-model",
        api_key="$OPENAI_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
        temperature=0.7,
        max_tokens=512,
        provider_settings={
            "compatibility": {
                "drop_constructor_args": ["temperature", "max_completion_tokens"],
            }
        },
    )

    create_chat_model(model)
    assert "temperature" not in CapturingChatModel.captured_kwargs
    assert "max_completion_tokens" not in CapturingChatModel.captured_kwargs
    assert "compatibility" not in CapturingChatModel.captured_kwargs
    assert CapturingChatModel.captured_kwargs["model"] == "gateway-model"


def test_factory_filters_kwargs_for_strict_provider_constructor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    StrictChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="strict",
        use="fake_chat_provider:StrictChatModel",
        model="strict-model",
        api_key="$OPENAI_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
        temperature=0.3,
        max_tokens=1024,
        provider_settings={"custom_payload": "ignored"},
    )

    create_chat_model(model)
    assert StrictChatModel.captured_kwargs == {"model": "strict-model", "api_key": "secret-key", "timeout": None}


def test_factory_retries_once_after_unexpected_optional_constructor_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    TypeErrorRetryChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="retry",
        use="fake_chat_provider:TypeErrorRetryChatModel",
        model="retry-model",
        api_key="$OPENAI_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
        temperature=0.4,
    )

    create_chat_model(model)
    assert TypeErrorRetryChatModel.captured_kwargs["model"] == "retry-model"
    assert "temperature" not in TypeErrorRetryChatModel.captured_kwargs


def test_factory_applies_anthropic_model_family_request_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    model = ModelConfig(
        name="claude-new",
        use="fake_chat_provider:CapturingChatModel",
        model="claude-opus-4.7",
        api_key="$ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com",
        provider="anthropic",
        provider_kind="anthropic_compatible",
        temperature=0.4,
        top_p=0.9,
        max_tokens=0,
        supports_reasoning_effort=True,
        default_reasoning_effort="minimal",
    )

    create_chat_model(model)
    assert CapturingChatModel.captured_kwargs["model_name"] == "claude-opus-4.7"
    assert "temperature" not in CapturingChatModel.captured_kwargs
    assert "top_p" not in CapturingChatModel.captured_kwargs
    assert "max_tokens_to_sample" not in CapturingChatModel.captured_kwargs
    assert "reasoning_effort" not in CapturingChatModel.captured_kwargs
    assert CapturingChatModel.captured_kwargs["effort"] == "low"

    CapturingChatModel.captured_kwargs = {}
    create_chat_model(model, reasoning_effort_override="xhigh")
    assert CapturingChatModel.captured_kwargs["effort"] == "max"


def test_factory_wraps_constructor_errors_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-testsecretvalue1234567890")

    model = ModelConfig(
        name="secret-failure",
        use="fake_chat_provider:FailingSecretChatModel",
        model="failing-model",
        api_key="$OPENAI_API_KEY",
        provider="openai",
        provider_kind="openai_compatible",
    )

    with pytest.raises(ValueError) as exc_info:
        create_chat_model(model)
    message = str(exc_info.value)
    assert "secret-failure" in message
    assert "constructor kwargs: api_key, model" in message
    assert "sk-proj-testsecretvalue1234567890" not in message
    assert "[REDACTED:openai_project_token]" in message


def test_factory_attaches_model_callbacks_from_tracing_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    CapturingChatModel.captured_kwargs = {}

    class DummyCallbackHandler(BaseCallbackHandler):
        pass

    class FakeTracingService:
        def build_model_callbacks(self) -> list[object]:
            return [DummyCallbackHandler(), DummyCallbackHandler()]

    model = ModelConfig(
        name="openai-main",
        display_name="OpenAI Main",
        use="fake_chat_provider:CapturingChatModel",
        model="gpt-5.4",
        api_key="$OPENAI_API_KEY",
        base_url="https://example.test/v1",
        provider="openai",
        provider_kind="openai_compatible",
    )

    create_chat_model(model, tracing_service=FakeTracingService())
    assert len(CapturingChatModel.captured_kwargs["callbacks"]) == 2


def test_factory_fails_cleanly_when_class_path_is_invalid() -> None:
    model = ModelConfig(
        name="broken",
        use="missing.module:MissingModel",
        model="broken",
        provider="openai",
    )
    with pytest.raises(ValueError, match="could not import"):
        create_chat_model(model)


def test_factory_fails_when_resolved_class_is_not_chat_model() -> None:
    model = ModelConfig(
        name="broken",
        use="builtins:str",
        model="broken",
        provider="openai",
    )
    with pytest.raises(ValueError, match="not a LangChain-compatible chat model"):
        create_chat_model(model)
