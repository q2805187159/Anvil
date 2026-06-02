# Model and Provider Configuration

Anvil's primary configuration path is:

- `config.yaml` for model definitions
- `.env` for secrets

Legacy `ANVIL_*` bootstrap variables are still supported, but they are a fallback path when `config.yaml` is absent.

## Core Model Fields

The main user-facing `ModelConfig` fields are:

- `name`
- `display_name`
- `description`
- `model`
- `default_model`
- `selected_model`
- `model_catalog`
- `api_key`
- `base_url`
- `temperature`
- `max_tokens`
- `context_window_tokens`
- `auto_compact_threshold_tokens`
- `model_context_windows`
- `model_auto_compact_thresholds`
- `provider_settings`
- `supports_thinking`
- `supports_reasoning_effort`
- `supports_vision`
- `supports_image_generation`
- `thinking`
- `when_thinking_enabled`
- `when_thinking_disabled`
- `use_responses_api`
- `output_version`
- `provider_settings.compatibility`

Compatibility aliases are still accepted for current migration safety:

- `model_name -> model`
- provider-kind hints such as `use`, `provider`, and `provider_kind`

`api_key_env` is also accepted as a legacy/advanced compatibility field, but
new project config should prefer explicit `api_key: ${PROVIDER_API_KEY}`
placeholders.

## Compact Provider Catalog

Prefer `llm.default + llm.providers` for product config. Each provider entry can
use a built-in preset and override only the fields that differ:

```yaml
llm:
  default: openai
  providers:
    openai:
      model: gpt-5.4
      base_url: ${OPENAI_BASE_URL}
      api_key: ${OPENAI_API_KEY}
      # Optional: enable only for providers that actually expose Responses API
      # and reasoning_effort-compatible routes.
      use_responses_api: false
      supports_reasoning_effort: false
    MiMo:
      model:
        - mimo-v2.5-pro
        - mimo-v2.5
        - mimo-v2-pro
        - mimo-v2-omni
        - mimo-v2-flash
      default_model: mimo-v2.5-pro
      base_url: https://token-plan-cn.xiaomimimo.com/v1
      api_key: ${MIMO_API_KEY}
      context_window_tokens: 1048576
      auto_compact_threshold_tokens: 786432
      model_context_windows:
        mimo-v2.5-flash: 32768
      model_auto_compact_thresholds:
        mimo-v2.5-flash: 24576
    kimi:
      model: kimi-k2.5
      base_url: https://api.moonshot.cn/v1
      api_key: ${MOONSHOT_API_KEY}
    gateway:
      model: vendor/model
      base_url: ${CUSTOM_OPENAI_BASE_URL}
      api_key: ${CUSTOM_OPENAI_API_KEY}
```

Built-in presets include:

- `openai`, `openai_responses`
- `anthropic`
- `doubao`
- `deepseek`
- `gemini`, `gemini_native`
- `kimi`, `moonshot`
- `minimax`, `minimax_cn`, `minimax_global`
- `novita`
- `openrouter`
- `vllm`
- `custom_openai`
- `mimo`

The preset catalog stores common provider URLs, adapters, timeout, context, and
capability defaults in the harness config loader. It does not select a concrete
model for the user; every enabled provider should keep `model` explicit in
`config.yaml`. Override `base_url` for private gateways, regional endpoints,
proxies, or local servers.

`mimo` uses Xiaomi's OpenAI-compatible endpoint
`https://token-plan-cn.xiaomimimo.com/v1` and expects `MIMO_API_KEY`.

## Provider Model Catalogs

Provider entries may define either one model ID or a curated model catalog:

```yaml
llm:
  providers:
    MiMo:
      model:
        - mimo-v2.5-pro
        - mimo-v2.5
        - mimo-v2-pro
        - mimo-v2-omni
        - mimo-v2-flash
      default_model: mimo-v2.5-pro
      base_url: https://token-plan-cn.xiaomimimo.com/v1
      api_key: ${MIMO_API_KEY}
```

When `model` is a string, that value is the concrete model ID sent to the
provider. When `model` is a list, the list is exposed as the provider's
`model_catalog`, and `default_model` selects the concrete active model. If
`default_model` is omitted, Anvil uses the first catalog entry. `model_name`
and `selected_model` remain compatibility aliases for the concrete active
model.

## Context Window and Auto-Compaction

`max_tokens` is the response output cap passed to the provider. It is not the
model's total context window. Configure the total window separately:

```yaml
llm:
  providers:
    openai:
      model:
        - gpt-5.4
        - gpt-5.4-mini
      default_model: gpt-5.4
      base_url: ${OPENAI_BASE_URL}
      api_key: ${OPENAI_API_KEY}
      context_window_tokens: 1047576
      auto_compact_threshold_tokens: 785682
```

When one provider entry exposes several concrete models, use per-model maps for
exceptions:

```yaml
llm:
  providers:
    MiMo:
      model:
        - mimo-v2.5-pro
        - mimo-v2.5-flash
      default_model: mimo-v2.5-flash
      context_window_tokens: 1048576
      auto_compact_threshold_tokens: 786432
      model_context_windows:
        mimo-v2.5-flash: 32768
      model_auto_compact_thresholds:
        mimo-v2.5-flash: 24576
```

The runtime projects the effective values for the active concrete model through
thread state as `context_window_usage`. The frontend meter shows progress toward
`auto_compact_threshold_tokens`; 100% means automatic summarization should fire.
`context_window_usage` is backend-owned: RunEngine estimates or normalizes
context pressure and provider token usage, the gateway maps the view model, and
the frontend renders the returned fields without deriving durable context from
the visible transcript.

## Provider Inference

For normal project config, `use`, `provider`, and `provider_kind` are optional.
Anvil infers the LangChain adapter from `base_url`:

- URLs ending in `/anthropic` use `anvil.agents.provider_adapters:AnvilAnthropicChatModel`.
- URLs ending in `/v1`, URLs containing `/openai`, and Volcengine-style
  `/api/v3` URLs use `langchain_openai:ChatOpenAI`.

Keep explicit `use` only for native SDK providers that do not have an OpenAI- or
Anthropic-compatible URL to inspect, such as `langchain_google_genai`.

## Environment Placeholders

Recommended secret and URL references:

- `api_key: $OPENAI_API_KEY`
- `api_key: ${OPENAI_API_KEY}`
- `base_url: ${CUSTOM_OPENAI_BASE_URL}`

`api_key` references are kept as references until the model factory creates the
model. Other string fields resolve during config service merge when the env var
is set; if not set, the original placeholder remains visible for diagnostics.

`api_key_env` remains supported for migration and indirection. For example,
`api_key_env: ${ACTIVE_PROVIDER_KEY_ENV}` means the env var
`ACTIVE_PROVIDER_KEY_ENV` contains the name of the env var that stores the real
secret, such as `MOONSHOT_API_KEY`. Do not use it for normal static provider
entries.

## Recommended Flow

1. Copy `config.example.yaml` to `config.yaml`
2. Choose one or more model entries
3. Put the referenced secrets in `.env`
4. Run `anvil-doctor` or `python -m app.doctor`
5. Run `anvil-smoke local`
6. Run provider smoke only after doctor passes

## Thinking and Reasoning

Anvil now supports both:

- `thinking`
  - the concise shortcut field
- `when_thinking_enabled`
  - the explicit overlay field
- `when_thinking_disabled`
  - the explicit overlay for providers that require a disable payload

If both are present, the `thinking` shortcut merges into `when_thinking_enabled`.

Provider-specific disable behavior is handled in the harness model factory so users do not need separate shell/app logic for:

- OpenAI-compatible gateways
- Anthropic-compatible models
- vLLM/Qwen-style chat template toggles
- special reasoning-provider token and reasoning rules

Anthropic-compatible third-party endpoints use Anvil's thin LangChain adapter by default. This keeps MiniMax-style `/anthropic` endpoints on Bearer auth, applies compatible Anthropic beta headers, and removes provider-specific header hazards without changing the lead-agent loop.

The same layer also normalizes Anthropic-family request quirks before model construction: invalid non-positive output caps are dropped, `reasoning_effort` is translated to Anthropic `effort`, and strict provider families can suppress unsupported sampling params.

## Vision and Image Capabilities

`supports_vision: true` is now a runtime contract, not only UI metadata. When a run includes a current-turn PNG/JPEG/WEBP/GIF upload, Anvil routes the lead model with required `vision` capability and sends the image as a data-URL `image_url` content block alongside the user text. When the model calls `view_image`, the tool result is reattached before the next model call as a multimodal human message with the returned image blocks, not as base64 preview text. If the selected model lacks vision, the run fails during model routing instead of silently passing only the file path to a text model.

`supports_image_generation` and `image_generation` describe provider-side image generation capability. They are capability-gated but not required to be the lead chat route: a tool-calling lead model can expose `image_generate` when the config contains a governed auxiliary image model. The tool uses the current route if it supports `image_generation`, otherwise `subsystem_models.image_generation`, otherwise the first configured model with `supports_image_generation: true`. Generated images are persisted as `/mnt/user-data/outputs/images/...` artifacts and returned as artifact refs; base64 payloads are not written into the transcript.

Minimal image generation model:

```yaml
models:
  image_gen:
    provider: openai
    provider_kind: openai_compatible
    model: gpt-image-1
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    supports_tool_calling: false
    supports_image_generation: true
    image_generation:
      providers: [openai]
      endpoint: /images/generations
      model: gpt-image-1
      output_format: png
      size: 1024x1024
      quality: high
      timeout_seconds: 120

subsystem_models:
  image_generation: image_gen
```

For offline tests, use `image_generation.providers: [mock]` and `mock_image_bytes`; the runtime still exposes the tool only when the resolved model capability includes `image_generation`.

Image generation endpoints are model configuration, not backend constants. `image_generation.endpoint` is required for real providers and is appended to the provider `base_url`; for example OpenAI uses `base_url: https://api.openai.com/v1` plus `endpoint: /images/generations`, while MiniMax uses `base_url: https://api.minimaxi.com/v1` plus `endpoint: /image_generation`. A MiniMax model can still infer provider `minimax` and model `image-01` from the MiniMax route, but it must carry the endpoint suffix in `image_generation`:

```yaml
models:
  minimax_cn:
    provider: openai
    provider_kind: openai_compatible
    model: MiniMax-M2.7
    base_url: https://api.minimaxi.com/v1
    api_key: ${MINIMAX_API_KEY}
    supports_tool_calling: true
    supports_vision: true
    supports_image_generation: true
    image_generation:
      providers: [minimax]
      endpoint: /image_generation
      model: image-01
      aspect_ratio: "16:9"
      response_format: url
```

## Responses API and Provider Settings

Use `provider_settings` for provider-specific kwargs that should pass through without turning the main schema into a grab bag.

`use_responses_api` and `supports_reasoning_effort` default to false. Enable them only when the concrete provider route documents support for `/v1/responses` and `reasoning_effort`; OpenAI-compatible providers such as MiniMax/MiMo often use `/v1/chat/completions`-style routes and can return 404 when these are enabled incorrectly.

Use `provider_settings.compatibility` for provider quirks that must be handled before LangChain model construction:

```yaml
llm:
  providers:
    strict_gateway:
      provider: custom_openai
      model: vendor/model
      base_url: ${CUSTOM_OPENAI_BASE_URL}
      api_key: ${CUSTOM_OPENAI_API_KEY}
      provider_settings:
        compatibility:
          drop_constructor_args:
            - temperature
            - max_completion_tokens
          constructor_arg_aliases:
            max_completion_tokens: max_tokens
```

The factory also drops unsupported kwargs for strict constructors and wraps provider instantiation failures with the model name, provider, adapter class, and safe kwarg keys. It does not log secret values.

## Supported Example Shapes

See:

- [`examples/config/openai-compatible.config.yaml`](https://github.com/q2805187159/Anvil/blob/main/examples/config/openai-compatible.config.yaml)
- [`examples/config/minimax-anthropic.config.yaml`](https://github.com/q2805187159/Anvil/blob/main/examples/config/minimax-anthropic.config.yaml)
- [`examples/config/vllm-local.config.yaml`](https://github.com/q2805187159/Anvil/blob/main/examples/config/vllm-local.config.yaml)

## Secret Handling

Secrets must stay out of tracked files.

Use:

- `.env`
- shell environment variables
- CI/CD secret injection

Do not commit real keys into:

- `config.yaml`
- `examples/`
- `README.md`

## Troubleshooting

- Wrong or missing secret:
  - run `anvil-doctor --config ./config.yaml`
- Need to verify provider reachability:
  - run `anvil-smoke provider --config ./config.yaml --model <name> --message "Reply with OK only."`
  - provider smoke sends a real model request; use it only with approved keys and budget
- Need to verify tracing too:
  - add `--expect-trace` and enable LangSmith env vars
