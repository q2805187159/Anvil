from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from anvil.config import EffectiveConfig

MiddlewareT = TypeVar("MiddlewareT")


def Next(anchor: type[Any]):
    def decorator(cls):
        cls._next_anchor = anchor
        return cls

    return decorator


def Prev(anchor: type[Any]):
    def decorator(cls):
        cls._prev_anchor = anchor
        return cls

    return decorator


class RuntimeFeatureSet(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    middleware: Sequence[Any] | None = None
    extra_middlewares: list[Any] = Field(default_factory=list)

    thread_data: bool | Any = True
    uploads: bool | Any = True
    sandboxing: bool | Any = True

    dangling_tool_calls: bool | Any = True
    llm_error_handling: bool | Any = True
    guardrails: bool | Any = True
    sandbox_audit: bool | Any = True
    tool_error_shaping: bool | Any = True
    tool_output_budget: bool | Any = True

    tool_visibility: bool | Any = True
    deferred_tool_filter: bool | Any = True
    plan_mode: bool | Any = False
    memory_prefetch: bool | Any = False
    memory_capture: bool | Any = False
    jit_context: bool | Any = False
    compaction: bool | Any = False
    title: bool | Any = False
    token_usage: bool | Any = False
    summarization: bool | Any = False
    view_image: bool | Any = False
    subagent_limit: bool | Any = False
    loop_detection: bool | Any = True

    clarification: bool | Any = True
    stable_prompt_cache: bool = True
    manual_provider_smoke: bool = False

    memory: bool = False
    skills: bool = False
    capability_mentions: bool = False
    extensions: bool = False
    dynamic_mcp_refresh: bool = False
    subagents: bool = False
    network_approval_service: bool = False


def _apply_config_enablement(
    feature_set: RuntimeFeatureSet,
    field_name: str,
    enabled: bool,
) -> None:
    if field_name in feature_set.model_fields_set:
        return

    current = getattr(feature_set, field_name)
    if isinstance(current, bool):
        setattr(feature_set, field_name, current or enabled)


def resolve_feature_set(
    base: RuntimeFeatureSet | None,
    config: EffectiveConfig,
) -> RuntimeFeatureSet:
    feature_set = base.model_copy(deep=True) if base is not None else RuntimeFeatureSet()

    _apply_config_enablement(
        feature_set,
        "plan_mode",
        config.plan_mode.enabled,
    )
    _apply_config_enablement(feature_set, "title", config.title.enabled)
    _apply_config_enablement(feature_set, "token_usage", config.token_usage.enabled)
    _apply_config_enablement(feature_set, "summarization", config.summarization.enabled)
    _apply_config_enablement(feature_set, "jit_context", config.jit_context.enabled)
    # Conversation context compaction has one production truth surface:
    # SummarizationMiddleware. The older priority CompactionMiddleware mutates
    # message lists without durable summary/level telemetry, so keep it as an
    # explicit feature_set override only.
    _apply_config_enablement(
        feature_set,
        "view_image",
        any(model.capabilities.vision for model in config.models.values()) or config.documents.page_image_derivatives.enabled,
    )
    _apply_config_enablement(
        feature_set,
        "memory",
        config.memory.enabled or config.memory_platform.enabled,
    )
    _apply_config_enablement(
        feature_set,
        "memory_prefetch",
        (config.memory.enabled and config.memory.prefetch_once_per_turn)
        or config.memory_platform.enabled,
    )
    _apply_config_enablement(
        feature_set,
        "memory_capture",
        config.memory.enabled and not config.memory_platform.enabled,
    )
    _apply_config_enablement(feature_set, "skills", config.skills_config.enabled)
    _apply_config_enablement(feature_set, "capability_mentions", config.skills_config.enabled)
    _apply_config_enablement(feature_set, "extensions", bool(config.extensions.mcp_servers))
    _apply_config_enablement(
        feature_set,
        "dynamic_mcp_refresh",
        any(server.refresh_policy == "dynamic" for server in config.extensions.mcp_servers.values()),
    )
    _apply_config_enablement(feature_set, "subagents", config.subagents.enabled)
    _apply_config_enablement(feature_set, "subagent_limit", config.subagents.enabled)
    _apply_config_enablement(feature_set, "guardrails", config.guardrails.enabled)
    _apply_config_enablement(
        feature_set,
        "network_approval_service",
        config.guardrails.enabled and config.guardrails.require_network_approval,
    )
    return feature_set
