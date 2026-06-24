from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import yaml

from .env_refs import is_env_ref, is_secret_ref_key, resolve_env_name_ref, resolve_env_ref
from .models import (
    ConfigLayer,
    ConfigLayerKind,
    ConfigOrigin,
    ConfigResolutionResult,
    EffectiveConfig,
    ModelConfig,
)


_LAYER_PRECEDENCE = {
    ConfigLayerKind.DEFAULT: 0,
    ConfigLayerKind.USER: 10,
    ConfigLayerKind.PROJECT: 20,
    ConfigLayerKind.PROFILE: 30,
    ConfigLayerKind.REQUEST: 40,
    ConfigLayerKind.REQUIREMENTS: 50,
}

_KNOWN_EFFECTIVE_KEYS = {
    "default_model",
    "models",
    "profiles",
    "subsystem_models",
    "extensions",
    "memory",
    "hcms",
    "git",
    "skills_config",
    "subagents",
    "guardrails",
    "sandbox",
    "summarization",
    "tool_output_budget",
    "tool_visibility_budget",
    "title",
    "plan_mode",
    "llm",
    "config_freshness",
    "token_usage",
    "trajectory_export",
    "scheduled_tasks",
    "loop_detection",
    "uploads",
    "documents",
    "code_semantics",
    "terminal",
    "context_files",
    "anvil",
    "workspace",
    "sandbox_mode",
    "requirements",
}

INTERNAL_TASK_MODEL_SUBSYSTEMS = (
    "summarization",
    "title",
    "session_search",
    "memory_updater",
    "memory_rerank",
    "memory_reflection",
    "memory_governance",
    "memory_maintenance",
    "skill_curator",
    "skill_extraction",
    "procedure_learning",
    "scheduled_automation",
    "trajectory_compression",
)


class ConfigMutationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ConfigFileMutation:
    config_path: Path
    original_text: str
    provider_name: str
    selected_model: str
    default_reasoning_effort: str | None = None
    internal_task_default: bool = False
    provider_selection_changed: bool = True

    def rollback(self) -> None:
        self.config_path.write_text(self.original_text, encoding="utf-8")


class ConfigService:
    """Compute one effective config plus provenance metadata from layered inputs."""

    def resolve(self, layers: list[ConfigLayer]) -> ConfigResolutionResult:
        enabled_layers = [layer for layer in layers if layer.enabled]
        ordered_layers = [
            layer
            for _, layer in sorted(
                enumerate(enabled_layers),
                key=lambda item: (_LAYER_PRECEDENCE[item[1].kind], item[0]),
            )
        ]

        merged: dict[str, Any] = {}
        origins: dict[str, ConfigOrigin] = {}

        for layer in ordered_layers:
            self._merge_into(merged, layer.data, layer, origins)

        self._normalize_legacy_effective_config(merged)
        known = {key: merged.get(key) for key in _KNOWN_EFFECTIVE_KEYS if key in merged}
        additional_settings = {
            key: value for key, value in merged.items() if key not in _KNOWN_EFFECTIVE_KEYS
        }

        try:
            effective_config = EffectiveConfig(
                **known,
                additional_settings=additional_settings,
            )
        except ValidationError as exc:
            raise ValueError(f"invalid effective config: {exc}") from exc

        fingerprint = hashlib.sha256(
            json.dumps(merged, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()

        return ConfigResolutionResult(
            effective_config=effective_config,
            origins=origins,
            fingerprint=fingerprint,
            layers=ordered_layers,
        )

    def _normalize_legacy_effective_config(self, payload: dict[str, Any]) -> None:
        legacy_memory = payload.pop("memory", None)
        if isinstance(legacy_memory, dict):
            hcms = payload.setdefault("hcms", {})
            if isinstance(hcms, dict):
                if "enabled" in legacy_memory and "enabled" not in hcms:
                    hcms["enabled"] = legacy_memory["enabled"]
                recall = hcms.setdefault("recall", {})
                if isinstance(recall, dict):
                    if "max_facts" in legacy_memory and "max_candidates" not in recall:
                        recall["max_candidates"] = legacy_memory["max_facts"]
                    if "injection_token_budget" in legacy_memory and "turn_recall_token_budget" not in recall:
                        recall["turn_recall_token_budget"] = legacy_memory["injection_token_budget"]
                transcript = hcms.setdefault("transcript", {})
                if isinstance(transcript, dict) and "transcript_context_tokens" in legacy_memory:
                    transcript.setdefault("transcript_context_tokens", legacy_memory["transcript_context_tokens"])
        payload.pop("memory_platform", None)
        scheduled_tasks = payload.get("scheduled_tasks")
        if isinstance(scheduled_tasks, dict):
            scheduled_tasks.pop("generation_strategy", None)

    def write_model_selection(
        self,
        *,
        config_path: Path,
        effective_config: EffectiveConfig,
        provider_name: str,
        selected_model: str,
        default_reasoning_effort: str | None = None,
        default_reasoning_effort_provided: bool = False,
        internal_task_default: bool = False,
    ) -> ConfigFileMutation:
        provider_name = provider_name.strip()
        selected_model = selected_model.strip()
        if not provider_name:
            raise ConfigMutationError("invalid_model_provider", "model provider name is required")
        if not selected_model:
            raise ConfigMutationError("invalid_model_selection", "model_name is required")

        effective_model = effective_config.models.get(provider_name)
        if effective_model is None:
            raise ConfigMutationError("model_not_found", f"model provider '{provider_name}' was not found")
        available_models = model_selection_options(effective_model)
        if available_models and selected_model not in available_models:
            raise ConfigMutationError(
                "invalid_model_selection",
                f"model '{selected_model}' is not configured for provider '{provider_name}'",
            )

        original_text = config_path.read_text(encoding="utf-8")
        payload = yaml.safe_load(original_text) or {}
        if not isinstance(payload, dict):
            raise ConfigMutationError("config_file_invalid", "config file must contain a mapping")

        provider_payload = find_writable_model_provider(payload, provider_name)
        if provider_payload is None:
            raise ConfigMutationError(
                "model_not_writable",
                f"model provider '{provider_name}' is not backed by the active config file",
            )

        apply_model_selection_payload(
            provider_payload,
            selected_model,
            default_reasoning_effort=default_reasoning_effort,
            default_reasoning_effort_provided=default_reasoning_effort_provided,
        )
        if internal_task_default:
            apply_internal_task_model_payload(payload, provider_name)
        config_path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return ConfigFileMutation(
            config_path=config_path,
            original_text=original_text,
            provider_name=provider_name,
            selected_model=selected_model,
            default_reasoning_effort=default_reasoning_effort,
            internal_task_default=internal_task_default,
            provider_selection_changed=True,
        )

    def write_internal_task_model_selection(
        self,
        *,
        config_path: Path,
        effective_config: EffectiveConfig,
        provider_name: str,
        selected_model: str,
    ) -> ConfigFileMutation:
        provider_name = provider_name.strip()
        selected_model = selected_model.strip()
        if not provider_name:
            raise ConfigMutationError("invalid_model_provider", "model provider name is required")
        if not selected_model:
            raise ConfigMutationError("invalid_model_selection", "model_name is required")

        effective_model = effective_config.models.get(provider_name)
        if effective_model is None:
            raise ConfigMutationError("model_not_found", f"model provider '{provider_name}' was not found")
        available_models = model_selection_options(effective_model)
        if available_models and selected_model not in available_models:
            raise ConfigMutationError(
                "invalid_model_selection",
                f"model '{selected_model}' is not configured for provider '{provider_name}'",
            )

        original_text = config_path.read_text(encoding="utf-8")
        payload = yaml.safe_load(original_text) or {}
        if not isinstance(payload, dict):
            raise ConfigMutationError("config_file_invalid", "config file must contain a mapping")

        apply_internal_task_model_payload(payload, provider_name, selected_model=selected_model)
        config_path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return ConfigFileMutation(
            config_path=config_path,
            original_text=original_text,
            provider_name=provider_name,
            selected_model=selected_model,
            internal_task_default=True,
            provider_selection_changed=False,
        )

    def _merge_into(
        self,
        merged: dict[str, Any],
        incoming: dict[str, Any],
        layer: ConfigLayer,
        origins: dict[str, ConfigOrigin],
        prefix: str = "",
    ) -> None:
        for key, value in incoming.items():
            value = self._resolve_env_refs(value, key)
            key_path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                self._merge_into(merged[key], value, layer, origins, key_path)
                continue

            if isinstance(value, dict):
                merged[key] = json.loads(json.dumps(value))
                self._record_leaf_origins(value, layer, origins, key_path)
                continue

            merged[key] = value
            origins[key_path] = ConfigOrigin(
                key_path=key_path,
                layer_name=layer.name,
                layer_kind=layer.kind,
                source=layer.source,
            )

    def _resolve_env_refs(self, value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return {nested_key: self._resolve_env_refs(nested_value, nested_key) for nested_key, nested_value in value.items()}
        if isinstance(value, list):
            return [self._resolve_env_refs(item, key) for item in value]
        if isinstance(value, str) and is_secret_ref_key(key) and is_env_ref(value):
            return value
        if isinstance(value, str) and is_env_ref(value) and not key.endswith("_env"):
            return resolve_env_ref(value)
        if isinstance(value, str) and key.endswith("_env") and is_env_ref(value):
            return resolve_env_name_ref(value)
        return value

    def _record_leaf_origins(
        self,
        payload: dict[str, Any],
        layer: ConfigLayer,
        origins: dict[str, ConfigOrigin],
        prefix: str,
    ) -> None:
        for key, value in payload.items():
            key_path = f"{prefix}.{key}"
            if isinstance(value, dict):
                self._record_leaf_origins(value, layer, origins, key_path)
                continue
            origins[key_path] = ConfigOrigin(
                key_path=key_path,
                layer_name=layer.name,
                layer_kind=layer.kind,
                source=layer.source,
            )


def model_selection_options(model: ModelConfig) -> list[str]:
    catalog = [str(item) for item in model.model_catalog if str(item)]
    if catalog:
        return catalog
    for candidate in (
        model.selected_model,
        model.model_name,
        model.default_model,
        model.model,
    ):
        if isinstance(candidate, str) and candidate:
            return [candidate]
    return []


def find_writable_model_provider(payload: dict[str, Any], provider_name: str) -> dict[str, Any] | None:
    llm = payload.get("llm")
    if isinstance(llm, dict):
        providers = llm.get("providers")
        if isinstance(providers, dict):
            provider_payload = providers.get(provider_name)
            if isinstance(provider_payload, dict):
                return provider_payload
        profiles = llm.get("profiles")
        if isinstance(profiles, dict):
            provider_payload = profiles.get(provider_name)
            if isinstance(provider_payload, dict):
                return provider_payload

    models = payload.get("models")
    if isinstance(models, dict):
        provider_payload = models.get(provider_name)
        if isinstance(provider_payload, dict):
            return provider_payload
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict) and item.get("name") == provider_name:
                return item
    return None


def apply_model_selection_payload(
    provider_payload: dict[str, Any],
    selected_model: str,
    *,
    default_reasoning_effort: str | None = None,
    default_reasoning_effort_provided: bool = False,
) -> None:
    raw_model = provider_payload.get("model")
    if isinstance(raw_model, list):
        provider_payload["model"] = [str(item) for item in raw_model]
    elif isinstance(raw_model, str):
        provider_payload["model"] = selected_model
    elif "model_catalog" not in provider_payload:
        provider_payload["model"] = selected_model
    provider_payload["selected_model"] = selected_model
    provider_payload["model_name"] = selected_model
    provider_payload["default_model"] = selected_model
    if not default_reasoning_effort_provided:
        return
    if default_reasoning_effort is None:
        provider_payload.pop("default_reasoning_effort", None)
    else:
        provider_payload["default_reasoning_effort"] = default_reasoning_effort


def apply_internal_task_model_payload(payload: dict[str, Any], provider_name: str, *, selected_model: str | None = None) -> None:
    provider_name = provider_name.strip()
    if not provider_name:
        raise ConfigMutationError("invalid_model_provider", "model provider name is required")
    selected_model = (selected_model or "").strip() or None

    llm = payload.setdefault("llm", {})
    if not isinstance(llm, dict):
        raise ConfigMutationError("config_file_invalid", "config key 'llm' must be a mapping")
    subsystems = llm.setdefault("subsystems", {})
    if not isinstance(subsystems, dict):
        raise ConfigMutationError("config_file_invalid", "config key 'llm.subsystems' must be a mapping")
    for subsystem in INTERNAL_TASK_MODEL_SUBSYSTEMS:
        subsystems[subsystem] = provider_name
    if selected_model:
        llm["internal_task_model"] = selected_model

    legacy_subsystems = payload.setdefault("subsystem_models", {})
    if isinstance(legacy_subsystems, dict):
        for subsystem in INTERNAL_TASK_MODEL_SUBSYSTEMS:
            legacy_subsystems[subsystem] = provider_name

    summarization = payload.setdefault("summarization", {})
    if isinstance(summarization, dict):
        summarization["model_name"] = provider_name

    title = payload.setdefault("title", {})
    if isinstance(title, dict):
        title["model_name"] = provider_name

    hcms = payload.setdefault("hcms", {})
    if isinstance(hcms, dict):
        session_search = hcms.setdefault("session_search", {})
        if isinstance(session_search, dict):
            session_search["model_name"] = provider_name
        recall = hcms.setdefault("recall", {})
        if isinstance(recall, dict):
            recall["rerank_model_name"] = provider_name
        updater = hcms.setdefault("updater", {})
        if isinstance(updater, dict):
            updater["model_name"] = provider_name

    scheduled_tasks = payload.setdefault("scheduled_tasks", {})
    if isinstance(scheduled_tasks, dict):
        scheduled_tasks["default_model"] = provider_name


def resolve_internal_task_model_name(effective_config: EffectiveConfig) -> str | None:
    model_names = tuple(effective_config.models)
    if len(model_names) == 1:
        return model_names[0]

    configured: list[str] = []
    for subsystem in INTERNAL_TASK_MODEL_SUBSYSTEMS:
        value = effective_config.llm.subsystems.get(subsystem)
        if value in effective_config.models:
            configured.append(value)

    for subsystem in INTERNAL_TASK_MODEL_SUBSYSTEMS:
        value = effective_config.subsystem_models.get(subsystem)
        if value in effective_config.models:
            configured.append(value)

    for value in _legacy_internal_task_model_names(effective_config):
        if value in effective_config.models:
            configured.append(value)

    if configured:
        return max(
            dict.fromkeys(configured),
            key=lambda item: configured.count(item),
        )
    if effective_config.default_model in effective_config.models:
        return effective_config.default_model
    return model_names[0] if model_names else None


def resolve_internal_task_concrete_model_name(effective_config: EffectiveConfig) -> str | None:
    provider_name = resolve_internal_task_model_name(effective_config)
    if not provider_name:
        return None
    provider = effective_config.models.get(provider_name)
    if provider is None:
        return None
    selected = (effective_config.llm.internal_task_model or "").strip()
    if selected and selected in model_selection_options(provider):
        return selected
    return provider.effective_model_name()


def resolve_internal_task_model_config(effective_config: EffectiveConfig, provider_name: str) -> ModelConfig | None:
    provider = effective_config.models.get(provider_name)
    if provider is None:
        return None
    if provider_name != resolve_internal_task_model_name(effective_config):
        return provider
    selected = (effective_config.llm.internal_task_model or "").strip()
    if selected and selected in model_selection_options(provider):
        return provider.model_copy(update={"model_name": selected, "selected_model": selected})
    return provider


def _legacy_internal_task_model_names(effective_config: EffectiveConfig) -> tuple[str | None, ...]:
    return (
        effective_config.summarization.model_name,
        effective_config.title.model_name,
        effective_config.hcms.session_search.model_name,
        effective_config.hcms.recall.rerank_model_name,
        effective_config.hcms.updater.model_name,
        effective_config.scheduled_tasks.default_model,
    )
