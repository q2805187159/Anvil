from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .models import EffectiveConfig, ModelCapabilities, ModelConfig, ProviderKind


class RouteSource(str, Enum):
    REQUEST = "request"
    PROFILE = "profile"
    SUBSYSTEM = "subsystem"
    DEFAULT = "default"


class RequiredModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thinking: bool = False
    reasoning_effort: bool = False
    vision: bool = False
    tool_calling: bool = False
    image_generation: bool = False


class ModelRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subsystem: str
    request_override_model: str | None = None
    profile: str | None = None
    required_capabilities: RequiredModelCapabilities = Field(default_factory=RequiredModelCapabilities)


class ResolvedModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subsystem: str
    model_name: str
    source: RouteSource
    provider: str
    provider_kind: ProviderKind
    capabilities: ModelCapabilities
    reasoning_effort: str | None = None


class ModelRouteError(ValueError):
    pass


def resolve_model_route(config: EffectiveConfig, request: ModelRouteRequest) -> ResolvedModelRoute:
    selected_name, source = _select_model_name(config, request)
    model = config.models.get(selected_name)
    if model is None:
        raise ModelRouteError(f"resolved model '{selected_name}' is not defined")

    _validate_capabilities(model, request.required_capabilities)

    return ResolvedModelRoute(
        subsystem=request.subsystem,
        model_name=model.name,
        source=source,
        provider=model.provider,
        provider_kind=_resolve_provider_kind(model),
        capabilities=model.capabilities,
        reasoning_effort=model.default_reasoning_effort,
    )


def _select_model_name(config: EffectiveConfig, request: ModelRouteRequest) -> tuple[str, RouteSource]:
    if request.request_override_model:
        return request.request_override_model, RouteSource.REQUEST

    if request.profile:
        profile = config.profiles.get(request.profile)
        if profile:
            if request.subsystem in profile.subsystem_models:
                return profile.subsystem_models[request.subsystem], RouteSource.PROFILE
            if profile.default_model:
                return profile.default_model, RouteSource.PROFILE

    if request.subsystem in config.subsystem_models:
        return config.subsystem_models[request.subsystem], RouteSource.SUBSYSTEM
    if request.subsystem in config.llm.subsystems:
        return config.llm.subsystems[request.subsystem], RouteSource.SUBSYSTEM

    internal_model = _internal_task_model_for_subsystem(config, request.subsystem)
    if internal_model:
        return internal_model, RouteSource.SUBSYSTEM

    if config.default_model:
        return config.default_model, RouteSource.DEFAULT

    raise ModelRouteError(
        f"no model route found for subsystem '{request.subsystem}' and no default model is configured"
    )


def _validate_capabilities(model: ModelConfig, required: RequiredModelCapabilities) -> None:
    missing: list[str] = []

    if required.thinking and not model.capabilities.thinking:
        missing.append("thinking")
    if required.reasoning_effort and not model.capabilities.reasoning_effort:
        missing.append("reasoning_effort")
    if required.vision and not model.capabilities.vision:
        missing.append("vision")
    if required.tool_calling and not model.capabilities.tool_calling:
        missing.append("tool_calling")
    if required.image_generation and not model.capabilities.image_generation:
        missing.append("image_generation")

    if missing:
        raise ModelRouteError(
            f"model '{model.name}' lacks required capabilities: {', '.join(missing)}"
        )


def _resolve_provider_kind(model: ModelConfig) -> ProviderKind:
    provider_kind = model.normalized_provider_kind()
    if provider_kind is None:
        raise ModelRouteError(f"unsupported provider kind for model '{model.name}': {model.provider}")
    return provider_kind


def _internal_task_model_for_subsystem(config: EffectiveConfig, subsystem: str) -> str | None:
    try:
        from .service import INTERNAL_TASK_MODEL_SUBSYSTEMS, resolve_internal_task_model_name
    except Exception:
        return None
    if subsystem not in INTERNAL_TASK_MODEL_SUBSYSTEMS:
        return None
    return resolve_internal_task_model_name(config)
