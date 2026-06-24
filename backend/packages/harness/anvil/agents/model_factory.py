from __future__ import annotations

import importlib
import inspect
import math
import os
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from anvil.config import ModelConfig, ProviderKind
from anvil.config.env_refs import env_ref_name, is_env_ref
from anvil.memory.scrubber import MemorySecretScrubber

from .provider_adapters import anthropic_compatible_overrides


def create_chat_model(
    model_config: ModelConfig,
    *,
    thinking_enabled: bool = False,
    reasoning_effort_override: str | None = None,
    model_override: BaseChatModel | None = None,
    tracing_service: Any | None = None,
) -> BaseChatModel:
    if model_override is not None:
        return model_override

    model_class = _resolve_chat_model_class(model_config)
    provider_kind = model_config.normalized_provider_kind()
    model_name = model_config.effective_model_name()
    api_key = _resolve_api_key(model_config)
    reasoning_effort = reasoning_effort_override or model_config.default_reasoning_effort
    kwargs = _build_constructor_kwargs(
        model_class=model_class,
        model_config=model_config,
        provider_kind=provider_kind,
        model_name=model_name,
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        thinking_enabled=thinking_enabled,
    )
    callbacks = _resolve_model_callbacks(tracing_service)
    if callbacks:
        _attach_callbacks(kwargs, callbacks)
    try:
        return model_class(**kwargs)
    except TypeError as exc:
        retried = _retry_constructor_after_type_error(
            model_class=model_class,
            model_config=model_config,
            kwargs=kwargs,
            exc=exc,
        )
        if retried is not None:
            return retried
        raise _wrap_model_constructor_error(
            model_config=model_config,
            model_class=model_class,
            kwargs=kwargs,
            exc=exc,
        ) from exc
    except Exception as exc:
        raise _wrap_model_constructor_error(
            model_config=model_config,
            model_class=model_class,
            kwargs=kwargs,
            exc=exc,
        ) from exc


def _resolve_chat_model_class(model_config: ModelConfig) -> type[BaseChatModel]:
    use_path = model_config.resolved_use_path()
    module_path, attr_name = _split_class_path(use_path)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ValueError(f"could not import model provider module '{module_path}' for model '{model_config.name}'") from exc
    try:
        resolved = getattr(module, attr_name)
    except AttributeError as exc:
        raise ValueError(f"model provider '{use_path}' could not be resolved for model '{model_config.name}'") from exc
    if not inspect.isclass(resolved) or not issubclass(resolved, BaseChatModel):
        raise ValueError(f"model provider '{use_path}' is not a LangChain-compatible chat model")
    return resolved


def _split_class_path(value: str) -> tuple[str, str]:
    if ":" in value:
        return tuple(value.split(":", 1))  # type: ignore[return-value]
    if "." not in value:
        raise ValueError(f"invalid model provider path: {value}")
    module_path, _, attr_name = value.rpartition(".")
    return module_path, attr_name


def _build_constructor_kwargs(
    *,
    model_class: type[BaseChatModel],
    model_config: ModelConfig,
    provider_kind: ProviderKind | None,
    model_name: str,
    api_key: str | None,
    reasoning_effort: str | None,
    thinking_enabled: bool,
) -> dict[str, Any]:
    kwargs = _resolve_provider_setting_env_refs(dict(model_config.effective_provider_settings()))
    if model_config.extra_body:
        kwargs["extra_body"] = _deep_merge_dicts(kwargs.get("extra_body") or {}, model_config.extra_body)
    effective_when_thinking_enabled = model_config.effective_when_thinking_enabled()
    has_explicit_thinking_settings = model_config.has_explicit_thinking_settings()

    if thinking_enabled:
        if has_explicit_thinking_settings and not model_config.supports_thinking:
            raise ValueError(f"model '{model_config.name}' does not support thinking")
        if effective_when_thinking_enabled:
            kwargs = _deep_merge_dicts(kwargs, effective_when_thinking_enabled)
    elif has_explicit_thinking_settings:
        kwargs = _apply_disabled_thinking_overlays(
            kwargs=kwargs,
            model_config=model_config,
            effective_when_thinking_enabled=effective_when_thinking_enabled,
        )

    _set_constructor_arg(
        kwargs,
        model_class,
        model_name,
        preferred_names=_preferred_model_name_args(provider_kind),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        api_key,
        preferred_names=("api_key", "openai_api_key", "anthropic_api_key"),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        model_config.base_url,
        preferred_names=("base_url", "openai_api_base"),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        model_config.temperature,
        preferred_names=("temperature",),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        model_config.top_p,
        preferred_names=("top_p",),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        model_config.max_tokens,
        preferred_names=_preferred_max_tokens_args(provider_kind),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        model_config.use_responses_api,
        preferred_names=("use_responses_api",),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        model_config.output_version,
        preferred_names=("output_version",),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        _effective_timeout(model_config),
        preferred_names=("timeout", "request_timeout", "default_request_timeout"),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        model_config.max_retries,
        preferred_names=("max_retries",),
    )
    _set_constructor_arg(
        kwargs,
        model_class,
        model_config.default_headers,
        preferred_names=("default_headers", "headers"),
    )

    if not model_config.supports_reasoning_effort:
        kwargs.pop("reasoning_effort", None)
    elif reasoning_effort is not None:
        kwargs.setdefault("reasoning_effort", reasoning_effort)

    if _is_special_reasoning_provider(model_config, model_class):
        kwargs = _apply_special_reasoning_provider_overlays(
            kwargs=kwargs,
            model_config=model_config,
            reasoning_effort=reasoning_effort,
            thinking_enabled=thinking_enabled,
        )
    kwargs = _apply_provider_compatibility_overlays(
        kwargs=kwargs,
        model_config=model_config,
        provider_kind=provider_kind,
        api_key=api_key,
    )
    return _sanitize_constructor_kwargs(kwargs=kwargs, model_config=model_config, model_class=model_class)


def _apply_disabled_thinking_overlays(
    *,
    kwargs: dict[str, Any],
    model_config: ModelConfig,
    effective_when_thinking_enabled: dict[str, Any],
) -> dict[str, Any]:
    explicit_disabled = model_config.effective_when_thinking_disabled()
    if explicit_disabled:
        return _deep_merge_dicts(kwargs, explicit_disabled)

    extra_body = effective_when_thinking_enabled.get("extra_body") or {}
    extra_body_thinking = extra_body.get("thinking") or {}
    if extra_body_thinking.get("type"):
        kwargs["extra_body"] = _deep_merge_dicts(
            kwargs.get("extra_body") or {},
            {"thinking": {"type": "disabled"}},
        )
        if model_config.supports_reasoning_effort:
            kwargs["reasoning_effort"] = "minimal"
        return kwargs

    chat_template_kwargs = extra_body.get("chat_template_kwargs") or {}
    disable_chat_template_kwargs = {
        key: False for key in ("thinking", "enable_thinking") if key in chat_template_kwargs
    }
    if disable_chat_template_kwargs:
        kwargs["extra_body"] = _deep_merge_dicts(
            kwargs.get("extra_body") or {},
            {"chat_template_kwargs": disable_chat_template_kwargs},
        )
        return kwargs

    direct_thinking = effective_when_thinking_enabled.get("thinking") or {}
    if direct_thinking.get("type"):
        kwargs["thinking"] = {"type": "disabled"}
    return kwargs


def _apply_special_reasoning_provider_overlays(
    *,
    kwargs: dict[str, Any],
    model_config: ModelConfig,
    reasoning_effort: str | None,
    thinking_enabled: bool,
) -> dict[str, Any]:
    for key in ("max_tokens", "max_completion_tokens", "max_tokens_to_sample", "max_output_tokens"):
        kwargs.pop(key, None)

    if not model_config.supports_reasoning_effort:
        kwargs.pop("reasoning_effort", None)
        return kwargs

    if not thinking_enabled:
        kwargs["reasoning_effort"] = "none"
        return kwargs

    if reasoning_effort in {"low", "medium", "high", "xhigh"}:
        kwargs["reasoning_effort"] = reasoning_effort
    else:
        kwargs.setdefault("reasoning_effort", "medium")
    return kwargs


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _set_constructor_arg(
    kwargs: dict[str, Any],
    model_class: type[BaseChatModel],
    value: Any,
    *,
    preferred_names: tuple[str, ...],
) -> None:
    if value is None:
        return
    if any(name in kwargs for name in preferred_names):
        return

    for name in preferred_names:
        if _supports_constructor_arg(model_class, name):
            kwargs[name] = value
            return

    if _supports_var_keyword(model_class):
        kwargs[preferred_names[0]] = value


def _apply_provider_compatibility_overlays(
    *,
    kwargs: dict[str, Any],
    model_config: ModelConfig,
    provider_kind: ProviderKind | None,
    api_key: str | None,
) -> dict[str, Any]:
    merged = dict(kwargs)
    if provider_kind is ProviderKind.ANTHROPIC_COMPATIBLE:
        merged = _deep_merge_dicts(
            merged,
            anthropic_compatible_overrides(
                base_url=model_config.base_url,
                api_key=api_key,
                headers=merged.get("default_headers") if isinstance(merged.get("default_headers"), dict) else None,
            ),
        )
        merged = _apply_anthropic_request_compatibility(
            merged,
            model_name=model_config.effective_model_name(),
        )
    return merged


def _sanitize_constructor_kwargs(
    *,
    kwargs: dict[str, Any],
    model_config: ModelConfig,
    model_class: type[BaseChatModel],
) -> dict[str, Any]:
    sanitized = _drop_factory_control_args(dict(kwargs))
    sanitized = _apply_constructor_arg_aliases(sanitized, model_config)
    sanitized = _drop_configured_constructor_args(sanitized, model_config)
    sanitized = _drop_unsupported_constructor_args(sanitized, model_class)
    sanitized = _compact_model_kwargs(sanitized)
    return sanitized


def _drop_factory_control_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    for key in ("compatibility", "drop_constructor_args", "disabled_params", "constructor_arg_aliases"):
        kwargs.pop(key, None)
    return kwargs


def _apply_constructor_arg_aliases(kwargs: dict[str, Any], model_config: ModelConfig) -> dict[str, Any]:
    aliases = _string_mapping(_compatibility_setting(model_config, "constructor_arg_aliases"))
    for source, target in aliases.items():
        if source in kwargs and target not in kwargs:
            kwargs[target] = kwargs.pop(source)
    return kwargs


def _drop_configured_constructor_args(kwargs: dict[str, Any], model_config: ModelConfig) -> dict[str, Any]:
    for key in _string_list(_compatibility_setting(model_config, "drop_constructor_args")):
        kwargs.pop(key, None)
    for key in _string_list(_compatibility_setting(model_config, "disabled_params")):
        kwargs.pop(key, None)
    return kwargs


def _drop_unsupported_constructor_args(kwargs: dict[str, Any], model_class: type[BaseChatModel]) -> dict[str, Any]:
    if _supports_var_keyword(model_class):
        return kwargs
    supported = _accepted_constructor_keys(model_class)
    return {key: value for key, value in kwargs.items() if key in supported}


def _compact_model_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    model_kwargs = kwargs.get("model_kwargs")
    if isinstance(model_kwargs, dict) and not model_kwargs:
        kwargs.pop("model_kwargs", None)
    return kwargs


def _apply_anthropic_request_compatibility(kwargs: dict[str, Any], *, model_name: str) -> dict[str, Any]:
    if _anthropic_forbids_sampling_params(model_name):
        for key in ("temperature", "top_p", "top_k"):
            kwargs.pop(key, None)

    for key in ("max_tokens", "max_tokens_to_sample", "max_completion_tokens"):
        if key in kwargs and _positive_int(kwargs[key]) is None:
            kwargs.pop(key, None)

    if "reasoning_effort" in kwargs and "effort" not in kwargs:
        effort = _anthropic_effort(kwargs.pop("reasoning_effort"), model_name=model_name)
        if effort:
            kwargs["effort"] = effort
    return kwargs


def _anthropic_forbids_sampling_params(model_name: str) -> bool:
    normalized = model_name.lower().replace(".", "-")
    return any(marker in normalized for marker in ("4-7",))


def _anthropic_effort(value: Any, *, model_name: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized == "none":
        return None
    if normalized == "minimal":
        return "low"
    if normalized == "xhigh":
        return "max" if _anthropic_supports_max_effort(model_name) else "high"
    if normalized in {"max", "high", "medium", "low"}:
        return normalized
    return None


def _anthropic_supports_max_effort(model_name: str) -> bool:
    normalized = model_name.lower().replace(".", "-")
    return "opus" in normalized and any(marker in normalized for marker in ("4-6", "4-7"))


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(value):
            return None
    except Exception:
        return None
    coerced = int(value)
    return coerced if coerced > 0 else None


def _retry_constructor_after_type_error(
    *,
    model_class: type[BaseChatModel],
    model_config: ModelConfig,
    kwargs: dict[str, Any],
    exc: TypeError,
) -> BaseChatModel | None:
    unsupported = _unsupported_arg_from_type_error(exc)
    if unsupported is None or unsupported not in kwargs:
        return None
    if unsupported in _protected_constructor_keys(model_config):
        return None
    retry_kwargs = dict(kwargs)
    retry_kwargs.pop(unsupported, None)
    try:
        return model_class(**retry_kwargs)
    except Exception:
        return None


def _unsupported_arg_from_type_error(exc: TypeError) -> str | None:
    message = str(exc)
    patterns = (
        r"unexpected keyword argument '([^']+)'",
        r"unexpected keyword argument \"([^\"]+)\"",
        r"extra inputs are not permitted.*?([A-Za-z_][A-Za-z0-9_]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    return None


def _wrap_model_constructor_error(
    *,
    model_config: ModelConfig,
    model_class: type[BaseChatModel],
    kwargs: dict[str, Any],
    exc: Exception,
) -> ValueError:
    safe_keys = ", ".join(sorted(kwargs)) or "none"
    message = (
        f"could not instantiate model '{model_config.name}' with provider "
        f"'{model_config.provider}' using {model_class.__module__}:{model_class.__name__}; "
        f"constructor kwargs: {safe_keys}; error: {_scrub_error_text(str(exc))}"
    )
    return ValueError(message)


def _accepted_constructor_keys(model_class: type[BaseChatModel]) -> set[str]:
    keys = {
        name
        for name, parameter in inspect.signature(model_class.__init__).parameters.items()
        if name != "self" and parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }
    for field_name, field in getattr(model_class, "model_fields", {}).items():
        keys.add(str(field_name))
        alias = getattr(field, "alias", None)
        if alias:
            keys.add(str(alias))
        validation_alias = getattr(field, "validation_alias", None)
        if validation_alias:
            keys.add(str(validation_alias))
    return keys


def _compatibility_setting(model_config: ModelConfig, key: str) -> Any:
    settings = model_config.effective_provider_settings()
    compatibility = settings.get("compatibility")
    if isinstance(compatibility, dict) and key in compatibility:
        return compatibility.get(key)
    return settings.get(key)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if str(key) and str(item)}


def _protected_constructor_keys(model_config: ModelConfig) -> set[str]:
    return {
        "anthropic_api_key",
        "anthropic_api_url",
        "api_key",
        "base_url",
        "model",
        "model_name",
        "openai_api_base",
        "openai_api_key",
    }


def _scrub_error_text(value: str) -> str:
    return MemorySecretScrubber().scrub(value).text


def _preferred_model_name_args(provider_kind: ProviderKind | None) -> tuple[str, ...]:
    if provider_kind is ProviderKind.ANTHROPIC_COMPATIBLE:
        return ("model_name", "model")
    return ("model", "model_name")


def _preferred_max_tokens_args(provider_kind: ProviderKind | None) -> tuple[str, ...]:
    if provider_kind in {ProviderKind.OPENAI_COMPATIBLE, ProviderKind.VLLM_OPENAI_COMPATIBLE}:
        return ("max_completion_tokens", "max_tokens")
    if provider_kind is ProviderKind.ANTHROPIC_COMPATIBLE:
        return ("max_tokens_to_sample", "max_tokens")
    return ("max_tokens", "max_completion_tokens", "max_tokens_to_sample")


def _effective_timeout(model_config: ModelConfig) -> float | None:
    return model_config.request_timeout or model_config.default_request_timeout or model_config.timeout


def _supports_constructor_arg(model_class: type[BaseChatModel], arg_name: str) -> bool:
    signature = inspect.signature(model_class.__init__)
    return arg_name in signature.parameters


def _supports_var_keyword(model_class: type[BaseChatModel]) -> bool:
    signature = inspect.signature(model_class.__init__)
    return any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())


def _is_special_reasoning_provider(model_config: ModelConfig, model_class: type[BaseChatModel]) -> bool:
    provider = (model_config.provider or "").lower()
    use_path = model_config.resolved_use_path().lower()
    class_name = model_class.__name__.lower()
    markers = ("reasoning_cli", "reasoningcli")
    return any(marker in provider or marker in use_path or marker in class_name for marker in markers)


def _resolve_model_callbacks(tracing_service: Any | None) -> list[Any]:
    if tracing_service is None:
        return []
    builder = getattr(tracing_service, "build_model_callbacks", None)
    if not callable(builder):
        return []
    callbacks = builder()
    return list(callbacks or [])


def _attach_callbacks(kwargs: dict[str, Any], callbacks: list[Any]) -> None:
    existing_callbacks = list(kwargs.get("callbacks") or [])
    kwargs["callbacks"] = [*existing_callbacks, *callbacks]


def _resolve_provider_setting_env_refs(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _resolve_provider_setting_env_ref(value)
        for key, value in payload.items()
    }


def _resolve_provider_setting_env_ref(value: Any) -> Any:
    if isinstance(value, dict):
        return _resolve_provider_setting_env_refs(value)
    if isinstance(value, list):
        return [_resolve_provider_setting_env_ref(item) for item in value]
    if isinstance(value, str) and is_env_ref(value):
        env_name = env_ref_name(value)
        resolved = os.getenv(env_name)
        if not resolved:
            raise ValueError(f"missing required environment variable: {env_name}")
        return resolved
    return value


def _resolve_api_key(model_config: ModelConfig) -> str | None:
    if model_config.api_key:
        if is_env_ref(model_config.api_key):
            env_name = env_ref_name(model_config.api_key)
            api_key = os.getenv(env_name)
            if not api_key:
                raise ValueError(f"missing required environment variable: {env_name}")
            return api_key
        return model_config.api_key
    if model_config.api_key_env:
        api_key = os.getenv(model_config.api_key_env)
        if not api_key:
            raise ValueError(f"missing required environment variable: {model_config.api_key_env}")
        return api_key
    return None
