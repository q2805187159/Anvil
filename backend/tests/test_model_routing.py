from __future__ import annotations

import pytest

from anvil.config import (
    EffectiveConfig,
    ModelCapabilities,
    ModelConfig,
    ModelRouteError,
    ModelRouteRequest,
    ProviderKind,
    ProfileConfig,
    RequiredModelCapabilities,
    resolve_model_route,
)


def make_config() -> EffectiveConfig:
    return EffectiveConfig(
        default_model="default-model",
        models={
            "default-model": ModelConfig(name="default-model", provider="openai"),
            "profile-model": ModelConfig(name="profile-model", provider="openai"),
            "profile-memory-model": ModelConfig(name="profile-memory-model", provider="openai"),
            "request-model": ModelConfig(
                name="request-model",
                provider="anthropic",
                provider_kind=ProviderKind.ANTHROPIC_COMPATIBLE,
                capabilities=ModelCapabilities(thinking=True, reasoning_effort=True),
            ),
            "vision-model": ModelConfig(
                name="vision-model",
                provider="openai",
                provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                capabilities=ModelCapabilities(vision=True),
            ),
            "image-model": ModelConfig(
                name="image-model",
                provider="openai",
                provider_kind=ProviderKind.OPENAI_COMPATIBLE,
                capabilities=ModelCapabilities(image_generation=True),
            ),
        },
        subsystem_models={"memory": "vision-model"},
        profiles={
            "coder": ProfileConfig(
                name="coder",
                default_model="profile-model",
                subsystem_models={"memory": "profile-memory-model"},
            )
        },
    )


def test_request_override_has_highest_precedence() -> None:
    route = resolve_model_route(
        make_config(),
        ModelRouteRequest(subsystem="memory", request_override_model="request-model", profile="coder"),
    )

    assert route.model_name == "request-model"
    assert route.source.value == "request"
    assert route.provider_kind == ProviderKind.ANTHROPIC_COMPATIBLE


def test_profile_override_beats_subsystem_and_default() -> None:
    route = resolve_model_route(
        make_config(),
        ModelRouteRequest(subsystem="memory", profile="coder"),
    )

    assert route.model_name == "profile-memory-model"
    assert route.source.value == "profile"


def test_global_subsystem_override_beats_default() -> None:
    route = resolve_model_route(
        make_config(),
        ModelRouteRequest(subsystem="memory"),
    )

    assert route.model_name == "vision-model"
    assert route.source.value == "subsystem"


def test_internal_task_subsystem_falls_back_to_shared_internal_binding() -> None:
    config = make_config().model_copy(
        update={
            "subsystem_models": {
                "title": "profile-model",
                "summarization": "profile-model",
                "session_search": "profile-model",
                "memory_updater": "profile-model",
                "memory_rerank": "profile-model",
            }
        }
    )

    route = resolve_model_route(
        config,
        ModelRouteRequest(subsystem="skill_curator"),
    )

    assert route.model_name == "profile-model"
    assert route.source.value == "subsystem"


def test_route_fails_when_model_lacks_required_capability() -> None:
    with pytest.raises(ModelRouteError, match="lacks required capabilities"):
        resolve_model_route(
            make_config(),
            ModelRouteRequest(
                subsystem="chat",
                required_capabilities=RequiredModelCapabilities(vision=True),
            ),
        )


def test_route_can_require_image_generation_capability() -> None:
    route = resolve_model_route(
        make_config(),
        ModelRouteRequest(
            subsystem="chat",
            request_override_model="image-model",
            required_capabilities=RequiredModelCapabilities(image_generation=True),
        ),
    )

    assert route.model_name == "image-model"
    assert route.capabilities.image_generation is True


def test_route_fails_when_model_lacks_required_image_generation() -> None:
    with pytest.raises(ModelRouteError, match="image_generation"):
        resolve_model_route(
            make_config(),
            ModelRouteRequest(
                subsystem="chat",
                required_capabilities=RequiredModelCapabilities(image_generation=True),
            ),
        )


def test_route_exposes_reasoning_effort_from_model_defaults() -> None:
    config = make_config()
    config.models["request-model"].default_reasoning_effort = "xhigh"

    route = resolve_model_route(
        config,
        ModelRouteRequest(subsystem="chat", request_override_model="request-model"),
    )

    assert route.reasoning_effort == "xhigh"
