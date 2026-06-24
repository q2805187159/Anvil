from __future__ import annotations

import os
import shutil
import json
import re
from pathlib import Path
from typing import Any

import yaml

from anvil.mcp.config import (
    default_mcp_config_payload,
    normalize_mcp_server_config,
    normalize_mcp_server_mapping,
    read_mcp_config_file,
)

from .env_refs import is_env_ref, resolve_env_name_ref
from .models import ConfigLayer, ConfigLayerKind


CONFIG_ENV_VAR = "ANVIL_CONFIG_PATH"
ANVIL_HOME_ENV_VAR = "ANVIL_HOME"
ANVIL_PROFILE_ENV_VAR = "ANVIL_PROFILE"
CONFIG_FILE_NAME = "config.yaml"
CONFIG_EXAMPLE_FILE_NAME = "config.example.yaml"
DOTENV_FILE_NAME = ".env"
ANVIL_CONFIG_DIR_NAME = ".anvil"
PLUGIN_CONFIG_FILE_NAME = "plugins.json"
DEFAULT_THREAD_WORKSPACE_DIR_NAME = "workspace"
DEFAULT_PROFILE_NAME = "default"
ACTIVE_PROFILE_FILE_NAME = "active_profile"
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REPO_ROOT_CACHE: Path | None = None
_REPO_ROOT_MARKER_MISS_CACHE = False


LLM_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "display_name": "Anthropic Claude",
        "use": "anvil.agents.provider_adapters:AnvilAnthropicChatModel",
        "provider": "anthropic",
        "provider_kind": "anthropic_compatible",
        "api_key": "$ANTHROPIC_API_KEY",
        "context_window_tokens": 200000,
        "auto_compact_threshold_tokens": 150000,
        "supports_thinking": True,
        "supports_vision": True,
        "when_thinking_enabled": {"thinking": {"type": "enabled"}},
    },
    "claude": {
        "preset": "anthropic",
    },
    "doubao": {
        "display_name": "Doubao",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": "$VOLCENGINE_API_KEY",
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_vision": True,
        "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
    },
    "minimax": {
        "display_name": "MiniMax",
        "use": "anvil.agents.provider_adapters:AnvilAnthropicChatModel",
        "provider": "anthropic",
        "provider_kind": "anthropic_compatible",
        "base_url": "https://api.minimaxi.com/anthropic",
        "api_key": "$MINIMAX_API_KEY",
        "context_window_tokens": 1048576,
        "auto_compact_threshold_tokens": 786432,
    },
    "minimax_cn": {
        "display_name": "MiniMax CN",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "$MINIMAX_API_KEY",
        "context_window_tokens": 1048576,
        "auto_compact_threshold_tokens": 786432,
        "temperature": 1.0,
        "supports_thinking": True,
        "supports_vision": True,
    },
    "minimax_global": {
        "display_name": "MiniMax Global",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://api.minimax.io/v1",
        "api_key": "$MINIMAX_API_KEY",
        "context_window_tokens": 1048576,
        "auto_compact_threshold_tokens": 786432,
        "temperature": 1.0,
        "supports_thinking": True,
        "supports_vision": True,
    },
    "openai": {
        "display_name": "OpenAI",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "api_key": "$OPENAI_API_KEY",
        "context_window_tokens": 1047576,
        "auto_compact_threshold_tokens": 785682,
    },
    "openai_responses": {
        "display_name": "OpenAI Responses",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "api_key": "$OPENAI_API_KEY",
        "context_window_tokens": 400000,
        "auto_compact_threshold_tokens": 300000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_vision": True,
        "use_responses_api": True,
        "output_version": "responses/v1",
    },
    "deepseek": {
        "display_name": "DeepSeek",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "$DEEPSEEK_API_KEY",
        "context_window_tokens": 128000,
        "auto_compact_threshold_tokens": 96000,
        "supports_thinking": True,
        "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
    },
    "gemini": {
        "display_name": "Gemini",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key": "$GEMINI_API_KEY",
        "context_window_tokens": 1048576,
        "auto_compact_threshold_tokens": 786432,
        "supports_vision": True,
    },
    "gemini_native": {
        "display_name": "Gemini Native",
        "use": "langchain_google_genai:ChatGoogleGenerativeAI",
        "provider": "google",
        "provider_settings": {"gemini_api_key": "$GEMINI_API_KEY"},
        "context_window_tokens": 1048576,
        "auto_compact_threshold_tokens": 786432,
        "supports_vision": True,
    },
    "kimi": {
        "display_name": "Kimi",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key": "$MOONSHOT_API_KEY",
        "context_window_tokens": 262144,
        "auto_compact_threshold_tokens": 196608,
        "supports_thinking": True,
        "supports_vision": True,
        "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
    },
    "moonshot": {
        "preset": "kimi",
    },
    "novita": {
        "display_name": "Novita",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://api.novita.ai/openai",
        "api_key": "$NOVITA_API_KEY",
        "context_window_tokens": 65536,
        "auto_compact_threshold_tokens": 49152,
        "supports_thinking": True,
        "supports_vision": True,
        "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
    },
    "openrouter": {
        "display_name": "OpenRouter",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "$OPENROUTER_API_KEY",
        "context_window_tokens": 1047576,
        "auto_compact_threshold_tokens": 785682,
    },
    "vllm": {
        "display_name": "vLLM",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "vllm",
        "provider_kind": "vllm_openai_compatible",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "$VLLM_API_KEY",
        "context_window_tokens": 131072,
        "auto_compact_threshold_tokens": 98304,
        "supports_thinking": True,
        "when_thinking_enabled": {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}},
    },
    "custom_openai": {
        "display_name": "Custom OpenAI-Compatible",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "api_key": "$CUSTOM_OPENAI_API_KEY",
        "context_window_tokens": 128000,
        "auto_compact_threshold_tokens": 96000,
    },
    "mimo": {
        "display_name": "MiMo",
        "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
        "provider": "openai",
        "provider_kind": "openai_compatible",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "api_key": "$MIMO_API_KEY",
        "context_window_tokens": 1048576,
        "auto_compact_threshold_tokens": 786432,
        "supports_vision": False,
    },
}


def get_repo_root() -> Path:
    global _REPO_ROOT_CACHE, _REPO_ROOT_MARKER_MISS_CACHE
    if _REPO_ROOT_CACHE is not None:
        return _REPO_ROOT_CACHE
    if _REPO_ROOT_MARKER_MISS_CACHE:
        return Path.cwd().resolve()
    start = Path(__file__).resolve()
    for candidate in [start.parent, *start.parents]:
        if (candidate / CONFIG_EXAMPLE_FILE_NAME).exists():
            _REPO_ROOT_CACHE = candidate
            return candidate
        if (candidate / "backend").exists() and (candidate / "README.md").exists():
            _REPO_ROOT_CACHE = candidate
            return candidate
    _REPO_ROOT_MARKER_MISS_CACHE = True
    return Path.cwd().resolve()


def get_backend_root() -> Path:
    root = get_repo_root()
    backend = root / "backend"
    return backend if backend.exists() else root


def get_anvil_home(anvil_home: str | Path | None = None) -> Path:
    if anvil_home is not None:
        return Path(os.path.expandvars(str(anvil_home))).expanduser().resolve()
    env_home = os.getenv(ANVIL_HOME_ENV_VAR)
    if env_home:
        return Path(os.path.expandvars(env_home)).expanduser().resolve()
    return (Path.home() / ANVIL_CONFIG_DIR_NAME).resolve()


def resolve_anvil_profile_name(profile_name: str | None = None, *, anvil_home: str | Path | None = None) -> str:
    if profile_name is not None and profile_name.strip():
        resolved = profile_name.strip()
    else:
        env_profile = os.getenv(ANVIL_PROFILE_ENV_VAR)
        if env_profile and env_profile.strip():
            resolved = env_profile.strip()
        else:
            active_profile_path = get_anvil_home(anvil_home) / ACTIVE_PROFILE_FILE_NAME
            if active_profile_path.exists():
                try:
                    resolved = active_profile_path.read_text(encoding="utf-8").strip() or DEFAULT_PROFILE_NAME
                except OSError:
                    resolved = DEFAULT_PROFILE_NAME
            else:
                resolved = DEFAULT_PROFILE_NAME
    validate_anvil_profile_name(resolved)
    return resolved


def validate_anvil_profile_name(profile_name: str) -> None:
    if profile_name == DEFAULT_PROFILE_NAME:
        return
    if not PROFILE_NAME_RE.fullmatch(profile_name):
        raise ValueError(f"invalid Anvil profile name: {profile_name!r}")


def resolve_anvil_profile_home(
    profile_name: str | None = None,
    *,
    anvil_home: str | Path | None = None,
) -> Path:
    root = get_anvil_home(anvil_home)
    resolved_profile = resolve_anvil_profile_name(profile_name, anvil_home=root)
    if resolved_profile == DEFAULT_PROFILE_NAME:
        return root
    return (root / "profiles" / resolved_profile).resolve()


def resolve_anvil_config_path(
    *,
    profile_name: str | None = None,
    anvil_home: str | Path | None = None,
) -> Path:
    return resolve_anvil_profile_home(profile_name, anvil_home=anvil_home) / CONFIG_FILE_NAME


def bootstrap_anvil_profile_home(
    profile_name: str | None = None,
    *,
    anvil_home: str | Path | None = None,
) -> Path:
    resolved_profile = resolve_anvil_profile_name(profile_name, anvil_home=anvil_home)
    profile_home = resolve_anvil_profile_home(resolved_profile, anvil_home=anvil_home)
    profile_home.mkdir(parents=True, exist_ok=True)
    profile_dirs = ["logs", "sessions", "memories", "skills", "cron", "cache"]
    if resolved_profile == DEFAULT_PROFILE_NAME:
        profile_dirs.append("profiles")
    for relative in profile_dirs:
        (profile_home / relative).mkdir(parents=True, exist_ok=True)

    config_path = profile_home / CONFIG_FILE_NAME
    if not config_path.exists():
        config_path.write_text(
            yaml.safe_dump(_default_profile_config_payload(resolved_profile), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    dotenv_path = profile_home / DOTENV_FILE_NAME
    if not dotenv_path.exists():
        dotenv_path.write_text("# Anvil provider keys and local secrets.\n", encoding="utf-8")

    auth_path = profile_home / "auth.json"
    if not auth_path.exists():
        auth_path.write_text("{}\n", encoding="utf-8")

    soul_path = profile_home / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text("# Anvil\n\n", encoding="utf-8")

    active_profile_path = get_anvil_home(anvil_home) / ACTIVE_PROFILE_FILE_NAME
    if not active_profile_path.exists():
        active_profile_path.write_text(resolved_profile, encoding="utf-8")

    return profile_home


def default_anvil_config_dir(repo_root: Path | None = None) -> Path:
    env_home = os.getenv(ANVIL_HOME_ENV_VAR)
    if env_home:
        return get_anvil_home()
    if repo_root is not None:
        return (Path(repo_root).expanduser().resolve() / ANVIL_CONFIG_DIR_NAME).resolve()
    return resolve_anvil_profile_home()


def resolve_config_path(
    config_path: str | Path | None = None,
    *,
    repo_root: Path | None = None,
) -> Path | None:
    root = (repo_root or get_repo_root()).resolve()
    profile_home = default_anvil_config_dir(root)
    load_dotenv_file(profile_home / DOTENV_FILE_NAME)

    if config_path is not None:
        candidate = Path(config_path).expanduser().resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"config file not found: {candidate}")
        return candidate

    env_path = os.getenv(CONFIG_ENV_VAR)
    if env_path:
        candidate = Path(env_path).expanduser().resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"config file from {CONFIG_ENV_VAR} not found: {candidate}")
        return candidate

    candidate = profile_home / CONFIG_FILE_NAME
    if candidate.exists():
        return candidate
    repo_candidate = root / CONFIG_FILE_NAME
    return repo_candidate if repo_candidate.exists() else None


def build_default_config_layers(
    config_path: str | Path | None = None,
    *,
    repo_root: Path | None = None,
) -> list[ConfigLayer]:
    root = (repo_root or get_repo_root()).resolve()
    profile_home = bootstrap_anvil_profile_home(anvil_home=default_anvil_config_dir(root))
    load_dotenv_file(profile_home / DOTENV_FILE_NAME)
    config_file = resolve_config_path(config_path, repo_root=root)
    layers: list[ConfigLayer] = [
        ConfigLayer(
            name="runtime_defaults",
            kind=ConfigLayerKind.DEFAULT,
            data=_runtime_default_config_payload(),
            source="runtime-defaults",
        )
    ]
    if config_file is not None:
        layers.extend(build_config_layers_from_file(config_file))
    else:
        layers.extend(build_env_bootstrap_config_layers_from_env())
    for mcp_config_file in resolve_mcp_config_paths(repo_root=root):
        if config_file is not None and mcp_config_file == config_file.resolve():
            continue
        layers.append(build_mcp_config_layer_from_file(mcp_config_file))
    for plugin_config_file in resolve_plugin_config_paths(repo_root=root):
        layers.append(build_plugin_config_layer_from_file(plugin_config_file))
    return layers


def build_config_layers_from_file(
    config_path: str | Path,
    *,
    layer_kind: ConfigLayerKind = ConfigLayerKind.PROJECT,
) -> list[ConfigLayer]:
    resolved_path = Path(config_path).expanduser().resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"config file not found: {resolved_path}")

    load_dotenv_file(resolved_path.parent / DOTENV_FILE_NAME)
    payload = read_config_file(resolved_path)
    normalized = normalize_loaded_config(payload)
    return [
        ConfigLayer(
            name="config_file",
            kind=layer_kind,
            data=normalized,
            source=str(resolved_path),
        )
    ]


def build_env_bootstrap_config_layers_from_env() -> list[ConfigLayer]:
    models: dict[str, dict[str, Any]] = {}

    if _env("ANVIL_OPENAI_COMPAT_MODEL"):
        models["openai_compatible"] = {
            "name": "openai_compatible",
            "display_name": "OpenAI-Compatible",
            "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
            "provider": "openai",
            "provider_kind": "openai_compatible",
            "model": _env("ANVIL_OPENAI_COMPAT_MODEL"),
            "base_url": _env("ANVIL_OPENAI_COMPAT_BASE_URL"),
            "api_key": "$ANVIL_OPENAI_COMPAT_API_KEY",
            "default_reasoning_effort": _env("ANVIL_OPENAI_REASONING_EFFORT"),
            "supports_reasoning_effort": True,
        }

    if _env("ANVIL_MINIMAX_MODEL"):
        models["minimax"] = {
            "name": "minimax",
            "display_name": "MiniMax",
            "use": "anvil.agents.provider_adapters:AnvilAnthropicChatModel",
            "provider": "anthropic",
            "provider_kind": "anthropic_compatible",
            "model": _env("ANVIL_MINIMAX_MODEL"),
            "base_url": _env("ANVIL_MINIMAX_BASE_URL"),
            "api_key": "$ANVIL_MINIMAX_API_KEY",
        }

    if _env("ANVIL_VLLM_MODEL"):
        models["vllm"] = {
            "name": "vllm",
            "display_name": "vLLM",
            "use": "anvil.agents.provider_adapters:AnvilOpenAIChatModel",
            "provider": "vllm",
            "provider_kind": "vllm_openai_compatible",
            "model": _env("ANVIL_VLLM_MODEL"),
            "base_url": _env("ANVIL_VLLM_BASE_URL"),
            "api_key": "$ANVIL_VLLM_API_KEY",
        }

    default_model = _env("ANVIL_DEFAULT_MODEL") or next(iter(models), None)
    return [
        ConfigLayer(
            name="env_bootstrap",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": default_model,
                "models": models,
            },
            source="environment",
        )
    ]


def init_config_from_example(
    destination: str | Path | None = None,
    *,
    repo_root: Path | None = None,
    force: bool = False,
) -> Path:
    root = (repo_root or get_repo_root()).resolve()
    src = root / CONFIG_EXAMPLE_FILE_NAME
    if not src.exists():
        raise FileNotFoundError(f"config example not found: {src}")

    destination_path = Path(destination).expanduser().resolve() if destination else resolve_anvil_config_path()
    if destination_path.exists() and not force:
        return destination_path
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, destination_path)
    return destination_path


def resolve_configured_path(value: str, *, repo_root: Path | None = None, default_name: str | None = None) -> Path:
    root = (repo_root or get_repo_root()).resolve()
    expanded = Path(os.path.expandvars(value)).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    if default_name and str(expanded) in {".", ""}:
        return (root / default_name).resolve()
    return (root / expanded).resolve()


def resolve_workspace_root(repo_root: Path | None = None) -> Path | None:
    root = (repo_root or get_repo_root()).resolve()
    config_path = resolve_config_path(repo_root=root)
    if config_path is None or not config_path.exists():
        return None
    try:
        payload = read_config_file(config_path)
    except Exception:
        return None
    workspace = payload.get("workspace")
    if not isinstance(workspace, dict):
        return None
    workspace_root = workspace.get("root")
    if not isinstance(workspace_root, str) or not workspace_root.strip():
        return None
    return resolve_configured_path(workspace_root.strip(), repo_root=root)


def resolve_mcp_config_path(*, repo_root: Path | None = None) -> Path | None:
    paths = resolve_mcp_config_paths(repo_root=repo_root)
    return paths[0] if paths else None


def resolve_mcp_config_paths(*, repo_root: Path | None = None) -> list[Path]:
    root = (repo_root or get_repo_root()).resolve()
    candidates = [
        root / ".anvil" / "mcp.json",
    ]
    return _existing_unique_paths(candidates)


def resolve_plugin_config_path(*, repo_root: Path | None = None) -> Path | None:
    paths = resolve_plugin_config_paths(repo_root=repo_root)
    return paths[0] if paths else None


def resolve_plugin_config_paths(*, repo_root: Path | None = None) -> list[Path]:
    _ = repo_root
    profile_home = resolve_anvil_profile_home()
    candidates = [
        profile_home / "plugins" / "marketplace.json",
        profile_home / PLUGIN_CONFIG_FILE_NAME,
    ]
    return _existing_unique_paths(candidates)


def build_mcp_config_layer_from_file(
    config_path: str | Path,
    *,
    layer_kind: ConfigLayerKind = ConfigLayerKind.USER,
) -> ConfigLayer:
    resolved_path = Path(config_path).expanduser().resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"mcp config file not found: {resolved_path}")
    payload = read_mcp_config_file(resolved_path)
    normalized = normalize_loaded_config(payload)
    return ConfigLayer(
        name="mcp_config_file",
        kind=layer_kind,
        data=normalized,
        source=str(resolved_path),
    )


def build_plugin_config_layer_from_file(
    config_path: str | Path,
    *,
    layer_kind: ConfigLayerKind = ConfigLayerKind.USER,
) -> ConfigLayer:
    resolved_path = Path(config_path).expanduser().resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"plugin config file not found: {resolved_path}")
    payload = read_plugin_config_file(resolved_path)
    normalized = normalize_loaded_config(payload)
    return ConfigLayer(
        name="plugin_config_file",
        kind=layer_kind,
        data=normalized,
        source=str(resolved_path),
    )


def _default_profile_config_payload(profile_name: str) -> dict[str, object]:
    return {
        "anvil": {
            "profile": profile_name,
        },
        "llm": {
            "default": None,
            "providers": {},
        },
        "workspace": {
            "mode": "thread",
            "auto_host_drives": True,
            "path_bridges": [],
        },
        **_runtime_default_config_payload(),
        "skills_config": {
            "enabled": True,
            "watch_enabled": True,
            "external_dirs": [],
            "curator": {
                "enabled": True,
                "schedule": "weekly",
                "auto_merge": True,
                "pin_protection": True,
            },
        },
        **default_mcp_config_payload(),
        "terminal": {
            "active_backend": "local",
            "backends": {
                "local": {
                    "kind": "local",
                    "label": "Local shell",
                    "enabled": True,
                },
            },
        },
    }


def _runtime_default_config_payload() -> dict[str, object]:
    return {
        "git": {
            "enabled": True,
            "required": True,
            "provider": "github",
            "token_env": "GITHUB_TOKEN",
        },
        "hcms": {
            "enabled": True,
            "recall": {
                "bm25_weight": 0.3,
                "vector_weight": 0.4,
                "graph_weight": 0.2,
                "temporal_weight": 0.1,
                "rrf_k": 60,
                "enable_adaptive_weights": True,
                "enable_cache": True,
                "cache_ttl": 300,
                "cache_max_entries": 100,
                "enable_mmr": True,
                "mmr_lambda": 0.72,
            },
            "update_queue": {
                "enabled": True,
                "debounce_seconds": 1.5,
                "min_window_seconds": 5.0,
                "default_window_seconds": 30.0,
                "max_window_seconds": 60.0,
                "min_batch_turns": 4,
                "max_batch_turns": 8,
            },
            "updater": {
                "enabled": True,
                "mode": "heuristic",
                "max_input_tokens": 6000,
                "max_output_tokens": 1800,
                "fact_confidence_threshold": 0.82,
                "timeout_seconds": 60,
                "fail_open": True,
            },
            "maintenance": {
                "enabled": True,
                "automation_enabled": True,
                "execute": True,
                "interval_seconds": 21600,
            },
        },
    }


def read_config_file(path: str | Path) -> dict[str, Any]:
    resolved_path = Path(path).expanduser().resolve()
    try:
        payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in config file '{resolved_path}': {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"config file '{resolved_path}' must contain a mapping at the root")
    return payload


def read_plugin_config_file(path: str | Path) -> dict[str, Any]:
    resolved_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in plugin config file '{resolved_path}': {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"plugin config file '{resolved_path}' must contain a mapping at the root")
    if "extensions" in payload:
        return payload
    if "plugins" in payload:
        return {"extensions": {"plugins": payload["plugins"]}}
    return {"extensions": {"plugins": {}}}


def normalize_loaded_config(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    _normalize_llm_compact_config(normalized)
    if "skills" in normalized and "skills_config" not in normalized:
        normalized["skills_config"] = normalized.pop("skills")
    if "mcpServers" in normalized:
        mcp_servers = normalized.pop("mcpServers")
        if isinstance(mcp_servers, dict):
            normalized["extensions"] = {
                **normalized.get("extensions", {}),
                "mcp_servers": normalize_mcp_server_mapping(mcp_servers),
            }
    if "mcp_servers" in normalized:
        mcp_servers = normalized.pop("mcp_servers")
        if isinstance(mcp_servers, dict):
            normalized["extensions"] = {
                **normalized.get("extensions", {}),
                "mcp_servers": normalize_mcp_server_mapping(mcp_servers),
            }
    if "mcp" in normalized and "extensions" not in normalized:
        mcp_value = normalized.pop("mcp")
        if isinstance(mcp_value, dict):
            mcp_servers = mcp_value.get("mcpServers")
            if isinstance(mcp_servers, dict):
                normalized["extensions"] = {
                    **normalized.get("extensions", {}),
                    "mcp_servers": normalize_mcp_server_mapping(mcp_servers),
                }
            else:
                servers = mcp_value.get("servers", [])
                if isinstance(servers, dict):
                    normalized["extensions"] = {
                        **normalized.get("extensions", {}),
                        "mcp_servers": normalize_mcp_server_mapping(servers),
                    }
                else:
                    normalized["extensions"] = {
                        **normalized.get("extensions", {}),
                        "mcp_servers": {
                            item["id"]: normalize_mcp_server_config(item)
                            for item in servers
                            if isinstance(item, dict) and item.get("id")
                        },
                    }
    if "plugins" in normalized and "extensions" not in normalized:
        plugins = normalized.pop("plugins")
        if isinstance(plugins, dict):
            normalized["extensions"] = {
                **normalized.get("extensions", {}),
                "plugins": plugins,
            }
    extensions = normalized.get("extensions")
    if isinstance(extensions, dict):
        mcp_servers = extensions.get("mcp_servers")
        if isinstance(mcp_servers, dict):
            extensions["mcp_servers"] = normalize_mcp_server_mapping(mcp_servers)
        normalized["extensions"] = extensions
    sandbox = normalized.get("sandbox")
    if isinstance(sandbox, dict):
        mode = sandbox.get("mode")
        if mode and "sandbox_mode" not in normalized:
            normalized["sandbox_mode"] = mode
        if "host_isolated" not in sandbox and "host" in sandbox:
            sandbox["host_isolated"] = sandbox.pop("host")
        normalized["sandbox"] = sandbox
    if isinstance(normalized.get("models"), list):
        models_list = normalized["models"]
        model_entries: dict[str, dict[str, Any]] = {}
        for entry in models_list:
            if not isinstance(entry, dict) or not entry.get("name"):
                raise ValueError("each model entry in config.yaml must be a mapping with a non-empty 'name'")
            model_entries[entry["name"]] = dict(entry)
        normalized["models"] = model_entries
        if normalized.get("default_model") is None and models_list:
            first = next((entry for entry in models_list if isinstance(entry, dict) and entry.get("name")), None)
            if first is not None:
                normalized["default_model"] = first["name"]

    if isinstance(normalized.get("profiles"), list):
        profiles: dict[str, dict[str, Any]] = {}
        for entry in normalized["profiles"]:
            if not isinstance(entry, dict) or not entry.get("name"):
                raise ValueError("each profile entry in config.yaml must be a mapping with a non-empty 'name'")
            profiles[entry["name"]] = dict(entry)
        normalized["profiles"] = profiles

    return normalized


def _existing_unique_paths(candidates: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _normalize_llm_compact_config(normalized: dict[str, Any]) -> None:
    llm = normalized.get("llm")
    if not isinstance(llm, dict):
        return

    providers = llm.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    if not providers and isinstance(llm.get("default"), str) and llm["default"].strip():
        providers = {llm["default"].strip(): {}}
    defaults = dict(_default_llm_provider_values())
    if isinstance(llm.get("defaults"), dict):
        defaults = _deep_merge_dicts(defaults, llm["defaults"])

    compact_models: dict[str, dict[str, Any]] = {}
    for provider_name, raw_provider in providers.items():
        if raw_provider is None:
            raw_provider = {}
        if not isinstance(raw_provider, dict):
            continue
        model_entry = _normalize_llm_provider_entry(str(provider_name), raw_provider, defaults)
        if model_entry is not None:
            compact_models[model_entry["name"]] = model_entry

    if compact_models:
        existing_models = normalized.get("models")
        if isinstance(existing_models, list):
            existing_map = {
                str(entry.get("name")): dict(entry)
                for entry in existing_models
                if isinstance(entry, dict) and entry.get("name")
            }
            normalized["models"] = {**compact_models, **existing_map}
        elif isinstance(existing_models, dict):
            normalized["models"] = {**compact_models, **existing_models}
        else:
            normalized["models"] = compact_models

    if normalized.get("default_model") is None and isinstance(llm.get("default"), str):
        normalized["default_model"] = llm["default"]
    if isinstance(llm.get("subsystems"), dict):
        normalized["subsystem_models"] = {
            **llm["subsystems"],
            **dict(normalized.get("subsystem_models") or {}),
        }


def _normalize_llm_provider_entry(
    provider_name: str,
    raw_provider: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any] | None:
    preset_name = str(raw_provider.get("provider") or provider_name).lower().replace("-", "_")
    preset = _llm_provider_preset(preset_name)
    raw_api_key_env = raw_provider.get("api_key_env")
    merged = _deep_merge_dicts(defaults, preset)
    merged = _deep_merge_dicts(merged, raw_provider)
    merged.setdefault("name", provider_name)
    merged.setdefault("display_name", provider_name)
    merged.setdefault("provider", preset.get("provider", preset_name))
    _normalize_provider_model_selection(merged)
    if merged.get("api_base") and not merged.get("base_url"):
        merged["base_url"] = merged["api_base"]
    if merged.get("base_url") and not merged.get("api_base"):
        merged["api_base"] = merged["base_url"]
    if raw_api_key_env is not None and not raw_provider.get("api_key") and "api_key" in preset:
        merged.pop("api_key", None)
    if isinstance(merged.get("api_key_env"), str) and is_env_ref(merged["api_key_env"]):
        merged["api_key_env"] = resolve_env_name_ref(merged["api_key_env"])
    if merged.get("api_key_env") and not merged.get("api_key") and not _preset_uses_nonstandard_api_key_field(merged):
        merged["api_key"] = f"${merged['api_key_env']}"
    if not merged.get("model"):
        return None
    return merged


def _normalize_provider_model_selection(payload: dict[str, Any]) -> None:
    raw_models = payload.get("model")
    raw_catalog = payload.get("model_catalog")
    has_catalog = isinstance(raw_models, list) or isinstance(raw_catalog, list)
    model_catalog = _normalized_model_catalog(raw_models, raw_catalog)
    selected = payload.get("model_name") or payload.get("selected_model") or payload.get("default_model")
    if selected is None and isinstance(raw_models, str) and raw_models.strip():
        selected = raw_models.strip()
    if selected is None and model_catalog:
        selected = model_catalog[0]
    if has_catalog and selected and model_catalog and str(selected).strip() not in model_catalog:
        raise ValueError(
            f"provider '{payload.get('name') or payload.get('display_name') or 'unknown'}' "
            f"default model '{selected}' is not present in its model catalog"
        )

    if has_catalog and model_catalog:
        payload["model"] = model_catalog
        payload["model_catalog"] = model_catalog
    if selected:
        if not payload.get("model"):
            payload["model"] = str(selected).strip()
        payload["model_name"] = str(selected).strip()
        payload.setdefault("default_model", str(selected).strip())
        payload.setdefault("selected_model", str(selected).strip())


def _normalized_model_catalog(*values: Any) -> list[str]:
    catalog: list[str] = []
    for value in values:
        if isinstance(value, list):
            catalog.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            catalog.append(value.strip())
    return list(dict.fromkeys(catalog))


def _default_llm_provider_values() -> dict[str, Any]:
    return {
        "timeout": 600.0,
        "max_retries": 2,
        "supports_tool_calling": True,
        "supports_thinking": False,
        "supports_reasoning_effort": False,
        "supports_vision": False,
        "supports_image_generation": False,
    }


def _llm_provider_preset(name: str) -> dict[str, Any]:
    preset = LLM_PROVIDER_PRESETS.get(name, LLM_PROVIDER_PRESETS["custom_openai"])
    if isinstance(preset.get("preset"), str):
        preset = _deep_merge_dicts(
            _llm_provider_preset(str(preset["preset"])),
            {key: value for key, value in preset.items() if key != "preset"},
        )
    return dict(preset)


def llm_provider_preset(name: str) -> dict[str, Any]:
    return _llm_provider_preset(name.lower().replace("-", "_"))


def llm_provider_presets() -> dict[str, dict[str, Any]]:
    return {
        name: _llm_provider_preset(name)
        for name in sorted(LLM_PROVIDER_PRESETS)
        if name not in {"claude", "moonshot"}
    }


def _preset_uses_nonstandard_api_key_field(payload: dict[str, Any]) -> bool:
    provider_settings = payload.get("provider_settings")
    return isinstance(provider_settings, dict) and "gemini_api_key" in provider_settings


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_dotenv_file(dotenv_path: str | Path | None = None, *, override: bool = False) -> None:
    path = Path(dotenv_path).expanduser().resolve() if dotenv_path is not None else (get_repo_root() / DOTENV_FILE_NAME)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if not key:
            continue
        if key in os.environ and not override:
            continue
        os.environ[key] = value


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None
