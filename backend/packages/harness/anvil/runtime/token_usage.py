from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping

from anvil.config.models import ModelConfig, TokenUsageConfig, TokenUsagePricingConfig


_INPUT_KEYS = ("input_tokens", "prompt_tokens", "input_token_count", "prompt_token_count")
_OUTPUT_KEYS = (
    "output_tokens",
    "completion_tokens",
    "output_token_count",
    "completion_token_count",
    "generated_tokens",
)
_TOTAL_KEYS = ("total_tokens", "total_token_count")
_CACHE_READ_KEYS = ("cache_read_tokens", "prompt_cache_hit_tokens", "cached_tokens", "cache_read_input_tokens")
_CACHE_WRITE_KEYS = ("cache_write_tokens", "cache_creation_input_tokens", "prompt_cache_miss_tokens")
_REASONING_KEYS = ("reasoning_tokens",)


def aggregate_token_usage_from_messages(
    messages: Iterable[Any],
    *,
    previous: Mapping[str, Any] | None = None,
    model_config: ModelConfig | None = None,
    route_model_name: str | None = None,
    token_usage_config: TokenUsageConfig | None = None,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        payload = _usage_payload_from_message(message)
        if not payload:
            continue
        normalized = _normalize_usage_payload(payload)
        if not _has_token_counts(normalized):
            continue
        entry = _build_entry(index=index, message=message, payload=payload, normalized=normalized)
        entries.append(entry)

    if not entries:
        if not previous:
            return {}
        return enrich_token_usage_summary(
            previous,
            model_config=model_config,
            route_model_name=route_model_name,
            token_usage_config=token_usage_config,
        )

    summary = _summarize_entries(entries)
    return enrich_token_usage_summary(
        summary,
        model_config=model_config,
        route_model_name=route_model_name,
        token_usage_config=token_usage_config,
    )


def enrich_token_usage_summary(
    usage: Mapping[str, Any],
    *,
    model_config: ModelConfig | None = None,
    route_model_name: str | None = None,
    token_usage_config: TokenUsageConfig | None = None,
) -> dict[str, Any]:
    summary = dict(usage)
    concrete_model = model_config.effective_model_name() if model_config is not None else None
    provider = model_config.provider if model_config is not None else None
    if route_model_name is not None:
        summary.setdefault("model", route_model_name)
    if concrete_model is not None:
        summary.setdefault("concrete_model", concrete_model)
    if provider is not None:
        summary.setdefault("provider", provider)

    _ensure_usage_breakdowns(summary)

    config = token_usage_config or TokenUsageConfig()
    if not config.include_entries and isinstance(summary.get("entries"), list):
        summary["entries"] = []

    pricing_key, pricing = _select_pricing(
        config.pricing,
        provider=provider,
        route_model_name=route_model_name,
        concrete_model=concrete_model,
    )
    cost = _estimate_cost(summary, pricing=pricing, pricing_key=pricing_key, config=config)
    summary["cost"] = cost
    summary["estimated_cost_usd"] = cost.get("estimated_cost_usd")
    summary["cost_status"] = cost.get("status")
    summary["currency"] = cost.get("currency")
    summary["pricing_source"] = cost.get("source")
    return summary


def _usage_payload_from_message(message: Any) -> dict[str, Any] | None:
    usage_metadata = getattr(message, "usage_metadata", None)
    if isinstance(usage_metadata, Mapping) and usage_metadata:
        return {str(key): value for key, value in usage_metadata.items()}

    response_metadata = getattr(message, "response_metadata", None)
    if not isinstance(response_metadata, Mapping):
        return None
    token_usage = response_metadata.get("token_usage") or response_metadata.get("usage")
    if isinstance(token_usage, Mapping) and token_usage:
        payload = {str(key): value for key, value in token_usage.items()}
        for key in ("model", "model_name", "id"):
            if key in response_metadata and key not in payload:
                payload[key] = response_metadata[key]
        return payload
    return None


def _normalize_usage_payload(payload: Mapping[str, Any]) -> dict[str, int | None]:
    input_tokens = _first_int(payload, *_INPUT_KEYS)
    output_tokens = _first_int(payload, *_OUTPUT_KEYS)
    total_tokens = _first_int(payload, *_TOTAL_KEYS)
    cache_read_tokens = _first_int(payload, *_CACHE_READ_KEYS, "input_token_details.cached_tokens")
    cache_write_tokens = _first_int(payload, *_CACHE_WRITE_KEYS)
    reasoning_tokens = _first_int(
        payload,
        *_REASONING_KEYS,
        "output_token_details.reasoning_tokens",
        "completion_tokens_details.reasoning_tokens",
    )

    if total_tokens is None:
        if input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        elif input_tokens is not None:
            total_tokens = input_tokens
        elif output_tokens is not None:
            total_tokens = output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _build_entry(
    *,
    index: int,
    message: Any,
    payload: Mapping[str, Any],
    normalized: Mapping[str, int | None],
) -> dict[str, Any]:
    entry = {
        "message_index": index,
        "message_type": type(message).__name__,
        "input_tokens": normalized.get("input_tokens"),
        "output_tokens": normalized.get("output_tokens"),
        "total_tokens": normalized.get("total_tokens"),
        "cache_read_tokens": normalized.get("cache_read_tokens"),
        "cache_write_tokens": normalized.get("cache_write_tokens"),
        "reasoning_tokens": normalized.get("reasoning_tokens"),
        "raw_usage": dict(payload),
    }
    model_name = payload.get("model_name") or payload.get("model")
    if isinstance(model_name, str) and model_name.strip():
        entry["provider_model"] = model_name.strip()
    return entry


def _summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    total = _breakdown_from_values(
        input_tokens=_sum_entries(entries, "input_tokens"),
        output_tokens=_sum_entries(entries, "output_tokens"),
        total_tokens=_sum_entries(entries, "total_tokens"),
        cache_read_tokens=_sum_entries(entries, "cache_read_tokens"),
        cache_write_tokens=_sum_entries(entries, "cache_write_tokens"),
        reasoning_tokens=_sum_entries(entries, "reasoning_tokens"),
    )
    last = _breakdown_from_entry(entries[-1])
    summary: dict[str, Any] = {
        "input_tokens": total["input_tokens"],
        "output_tokens": total["output_tokens"],
        "total_tokens": total["total_tokens"],
        "cache_read_tokens": total["cache_read_tokens"],
        "cache_write_tokens": total["cache_write_tokens"],
        "reasoning_tokens": total["reasoning_tokens"],
        "request_count": len(entries),
        "total": total,
        "last": last,
        "entries": entries,
    }
    provider_models = [
        str(entry["provider_model"])
        for entry in entries
        if isinstance(entry.get("provider_model"), str) and str(entry["provider_model"]).strip()
    ]
    if provider_models:
        summary["provider_models"] = list(dict.fromkeys(provider_models))
    return summary


def _ensure_usage_breakdowns(summary: dict[str, Any]) -> None:
    total = _breakdown_from_mapping(
        summary.get("total") if isinstance(summary.get("total"), Mapping) else None,
        fallback=summary,
    )
    summary["total"] = total

    last_payload: Mapping[str, Any] | None = summary.get("last") if isinstance(summary.get("last"), Mapping) else None
    if last_payload is None:
        entries = summary.get("entries")
        if isinstance(entries, list) and entries and isinstance(entries[-1], Mapping):
            last_payload = entries[-1]
        elif _int_or_none(summary.get("request_count")) == 1:
            last_payload = total
    summary["last"] = _breakdown_from_mapping(last_payload)


def _breakdown_from_mapping(
    payload: Mapping[str, Any] | None,
    *,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, int | None]:
    payload = payload or {}
    fallback = fallback or {}
    return _breakdown_from_values(
        input_tokens=_breakdown_value(payload, fallback, "input_tokens"),
        output_tokens=_breakdown_value(payload, fallback, "output_tokens"),
        total_tokens=_breakdown_value(payload, fallback, "total_tokens"),
        cache_read_tokens=_breakdown_value(payload, fallback, "cache_read_tokens"),
        cache_write_tokens=_breakdown_value(payload, fallback, "cache_write_tokens"),
        reasoning_tokens=_breakdown_value(payload, fallback, "reasoning_tokens"),
    )


def _breakdown_value(payload: Mapping[str, Any], fallback: Mapping[str, Any], key: str) -> int | None:
    parsed = _int_or_none(payload.get(key))
    if parsed is not None:
        return parsed
    return _int_or_none(fallback.get(key))


def _breakdown_from_entry(entry: Mapping[str, Any]) -> dict[str, int | None]:
    return _breakdown_from_values(
        input_tokens=_int_or_none(entry.get("input_tokens")),
        output_tokens=_int_or_none(entry.get("output_tokens")),
        total_tokens=_int_or_none(entry.get("total_tokens")),
        cache_read_tokens=_int_or_none(entry.get("cache_read_tokens")),
        cache_write_tokens=_int_or_none(entry.get("cache_write_tokens")),
        reasoning_tokens=_int_or_none(entry.get("reasoning_tokens")),
    )


def _breakdown_from_values(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    reasoning_tokens: int | None,
) -> dict[str, int | None]:
    if total_tokens is None:
        if input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        elif input_tokens is not None:
            total_tokens = input_tokens
        elif output_tokens is not None:
            total_tokens = output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _estimate_cost(
    usage: Mapping[str, Any],
    *,
    pricing: TokenUsagePricingConfig | None,
    pricing_key: str | None,
    config: TokenUsageConfig,
) -> dict[str, Any]:
    if pricing is None:
        return {
            "estimated_cost_usd": None,
            "status": "unknown",
            "currency": config.currency,
            "source": "none",
            "pricing_key": None,
        }

    amount = Decimal("0")
    amount += _per_million_cost(_int_or_zero(usage.get("input_tokens")), pricing.input_cost_per_million)
    amount += _per_million_cost(_int_or_zero(usage.get("output_tokens")), pricing.output_cost_per_million)
    amount += _per_million_cost(_int_or_zero(usage.get("cache_read_tokens")), pricing.cache_read_cost_per_million)
    amount += _per_million_cost(_int_or_zero(usage.get("cache_write_tokens")), pricing.cache_write_cost_per_million)
    amount += _per_million_cost(_int_or_zero(usage.get("reasoning_tokens")), pricing.reasoning_cost_per_million)
    if pricing.request_cost is not None:
        amount += Decimal(str(pricing.request_cost)) * Decimal(_int_or_zero(usage.get("request_count")))

    precision = max(int(config.cost_precision), 0)
    quantizer = Decimal("1") if precision == 0 else Decimal("1").scaleb(-precision)
    return {
        "estimated_cost_usd": float(amount.quantize(quantizer, rounding=ROUND_HALF_UP)),
        "status": "estimated",
        "currency": config.currency,
        "source": pricing.source,
        "pricing_key": pricing_key,
    }


def _select_pricing(
    pricing: Mapping[str, TokenUsagePricingConfig],
    *,
    provider: str | None,
    route_model_name: str | None,
    concrete_model: str | None,
) -> tuple[str | None, TokenUsagePricingConfig | None]:
    normalized_pricing = {str(key).lower(): (str(key), value) for key, value in pricing.items()}
    candidates = []
    if provider and concrete_model:
        candidates.extend([f"{provider}/{concrete_model}", f"{provider}:{concrete_model}"])
    if provider and route_model_name:
        candidates.extend([f"{provider}/{route_model_name}", f"{provider}:{route_model_name}"])
    for item in (concrete_model, route_model_name):
        if item:
            candidates.append(item)
    if provider:
        candidates.append(provider)
    candidates.append("default")

    for candidate in candidates:
        match = normalized_pricing.get(str(candidate).lower())
        if match is not None:
            return match
    return None, None


def _per_million_cost(tokens: int, rate: float | None) -> Decimal:
    if rate is None or tokens <= 0:
        return Decimal("0")
    return Decimal(tokens) * Decimal(str(rate)) / Decimal(1_000_000)


def _sum_entries(entries: list[dict[str, Any]], key: str) -> int | None:
    values = [_int_or_none(entry.get(key)) for entry in entries]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present)


def _has_token_counts(normalized: Mapping[str, int | None]) -> bool:
    return any(value is not None for value in normalized.values())


def _first_int(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _nested_get(mapping, key)
        parsed = _int_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _nested_get(mapping: Mapping[str, Any], key: str) -> Any:
    current: Any = mapping
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _int_or_zero(value: Any) -> int:
    parsed = _int_or_none(value)
    return parsed if parsed is not None else 0
