from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

SECRET_ENV_REF_KEYS = frozenset({"api_key", "gemini_api_key", "anthropic_api_key", "openai_api_key"})
SECRET_ENV_REF_SUFFIXES = ("_api_key", "_secret", "_token", "_password")


def is_env_ref(value: str) -> bool:
    return (value.startswith("${") and value.endswith("}")) or value.startswith("$")


def env_ref_name(value: str) -> str:
    if value.startswith("${") and value.endswith("}"):
        return value[2:-1]
    return value[1:]


def resolve_env_ref(value: str) -> str:
    return os.getenv(env_ref_name(value), value)


def resolve_env_name_ref(value: str) -> str:
    env_name = env_ref_name(value)
    if value.startswith("${"):
        return os.getenv(env_name, env_name)
    return env_name


def is_secret_ref_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in SECRET_ENV_REF_KEYS or normalized.endswith(SECRET_ENV_REF_SUFFIXES)


def iter_env_ref_names(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        if is_env_ref(value):
            yield env_ref_name(value)
        return
    if isinstance(value, dict):
        for nested in value.values():
            yield from iter_env_ref_names(nested)
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_env_ref_names(item)
