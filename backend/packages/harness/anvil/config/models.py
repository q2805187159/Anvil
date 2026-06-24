from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigLayerKind(str, Enum):
    DEFAULT = "default"
    USER = "user"
    PROJECT = "project"
    PROFILE = "profile"
    REQUEST = "request"
    REQUIREMENTS = "requirements"


class SandboxMode(str, Enum):
    LOCAL = "local"
    HOST_ISOLATED = "host_isolated"
    ISOLATED = "isolated"
    EXTERNAL = "external"


class ProviderKind(str, Enum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC_COMPATIBLE = "anthropic_compatible"
    VLLM_OPENAI_COMPATIBLE = "vllm_openai_compatible"


class McpTransportKind(str, Enum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thinking: bool = False
    reasoning_effort: bool = False
    vision: bool = False
    tool_calling: bool = True
    image_generation: bool = False


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    display_name: str | None = None
    description: str | None = None
    use: str | None = None
    model: str | list[str] | None = None
    default_model: str | None = None
    selected_model: str | None = None
    model_catalog: list[str] = Field(default_factory=list)
    provider: str = "unknown"
    provider_kind: ProviderKind | None = None
    model_name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None
    default_reasoning_effort: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    model_context_windows: dict[str, int] = Field(default_factory=dict)
    model_auto_compact_thresholds: dict[str, int] = Field(default_factory=dict)
    timeout: float | None = None
    request_timeout: float | None = None
    default_request_timeout: float | None = None
    max_retries: int | None = None
    default_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    provider_settings: dict[str, Any] = Field(default_factory=dict)
    supports_tool_calling: bool = True
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    supports_vision: bool = False
    supports_image_generation: bool = False
    when_thinking_enabled: dict[str, Any] | None = None
    when_thinking_disabled: dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None
    use_responses_api: bool | None = None
    output_version: str | None = None
    image_generation: dict[str, Any] | None = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)

    @model_validator(mode="after")
    def normalize_aliases_and_capabilities(self) -> "ModelConfig":
        catalog = self._normalized_model_catalog()
        if catalog:
            self.model_catalog = catalog

        selected_model = self.model_name or self.selected_model or self.default_model
        if selected_model is None and isinstance(self.model, str):
            selected_model = self.model
        if selected_model is None and catalog:
            selected_model = catalog[0]
        if isinstance(self.model, list) and selected_model is not None and selected_model not in catalog:
            raise ValueError(
                f"model '{self.name}' default model '{selected_model}' is not present in its model catalog"
            )

        if selected_model is not None:
            self.model_name = selected_model
        if self.model is None and self.model_name is not None:
            self.model = self.model_name
        if self.base_url is None and self.api_base is not None:
            self.base_url = self.api_base
        if self.api_base is None and self.base_url is not None:
            self.api_base = self.base_url
        inferred_provider_kind = self.normalized_provider_kind()
        if self.provider_kind is None and inferred_provider_kind is not None:
            self.provider_kind = inferred_provider_kind
        if self.provider == "unknown" and inferred_provider_kind is not None:
            self.provider = _provider_name_for_kind(inferred_provider_kind)

        self.capabilities.tool_calling = bool(self.supports_tool_calling)

        if self.supports_thinking:
            self.capabilities.thinking = True
        else:
            self.supports_thinking = self.capabilities.thinking

        if self.supports_reasoning_effort:
            self.capabilities.reasoning_effort = True
        else:
            self.supports_reasoning_effort = self.capabilities.reasoning_effort

        if self.supports_vision:
            self.capabilities.vision = True
        else:
            self.supports_vision = self.capabilities.vision

        if self.supports_image_generation:
            self.capabilities.image_generation = True
            if not _image_generation_endpoint_configured(self.image_generation):
                raise ValueError(
                    f"model '{self.name}' enables supports_image_generation but image_generation.endpoint is not configured"
                )
        else:
            self.supports_image_generation = self.capabilities.image_generation

        return self

    def _normalized_model_catalog(self) -> list[str]:
        values: list[str] = []
        if isinstance(self.model, list):
            values.extend(str(item).strip() for item in self.model if str(item).strip())
        elif isinstance(self.model, str) and self.model.strip():
            values.append(self.model.strip())
        values.extend(str(item).strip() for item in self.model_catalog if str(item).strip())
        return list(dict.fromkeys(values))

    def normalized_provider_kind(self) -> ProviderKind | None:
        if self.provider_kind is not None:
            return self.provider_kind
        provider_value = self.provider.lower()
        if provider_value in {"openai", "openai_compatible", "openai-compatible"}:
            return ProviderKind.OPENAI_COMPATIBLE
        if provider_value in {"anthropic", "anthropic_compatible", "anthropic-compatible"}:
            return ProviderKind.ANTHROPIC_COMPATIBLE
        if provider_value in {"vllm", "vllm_openai_compatible", "vllm-openai-compatible"}:
            return ProviderKind.VLLM_OPENAI_COMPATIBLE
        base_url = (self.base_url or self.api_base or "").rstrip("/").lower()
        if base_url.endswith("/anthropic") or "api.anthropic.com" in base_url:
            return ProviderKind.ANTHROPIC_COMPATIBLE
        if base_url.endswith("/v1") or "/openai" in base_url or "/api/v3" in base_url:
            return ProviderKind.OPENAI_COMPATIBLE
        if self.use:
            if "langchain_openai" in self.use:
                return ProviderKind.OPENAI_COMPATIBLE
            if "langchain_anthropic" in self.use:
                return ProviderKind.ANTHROPIC_COMPATIBLE
        return None

    def resolved_use_path(self) -> str:
        if self.use:
            return self.use
        provider_kind = self.normalized_provider_kind()
        if provider_kind in {ProviderKind.OPENAI_COMPATIBLE, ProviderKind.VLLM_OPENAI_COMPATIBLE}:
            return "anvil.agents.provider_adapters:AnvilOpenAIChatModel"
        if provider_kind is ProviderKind.ANTHROPIC_COMPATIBLE:
            return "anvil.agents.provider_adapters:AnvilAnthropicChatModel"
        raise ValueError(f"model '{self.name}' does not define 'use' and provider kind could not be inferred")

    def effective_model_name(self) -> str:
        if self.model_name:
            return self.model_name
        if self.selected_model:
            return self.selected_model
        if self.default_model:
            return self.default_model
        if isinstance(self.model, str):
            return self.model
        if isinstance(self.model, list) and self.model:
            return str(self.model[0])
        return self.name

    def effective_context_window_tokens(self) -> int | None:
        model_name = self.effective_model_name()
        if model_name in self.model_context_windows:
            return self.model_context_windows[model_name]
        return self.context_window_tokens

    def effective_auto_compact_threshold_tokens(self) -> int | None:
        model_name = self.effective_model_name()
        if model_name in self.model_auto_compact_thresholds:
            return self.model_auto_compact_thresholds[model_name]
        if self.auto_compact_threshold_tokens is not None:
            return self.auto_compact_threshold_tokens
        context_window = self.effective_context_window_tokens()
        if context_window is None or context_window <= 0:
            return None
        return int(context_window * 0.75)

    def effective_provider_settings(self) -> dict[str, Any]:
        extras = dict(self.model_extra or {})
        return {**extras, **self.provider_settings}

    def has_explicit_thinking_settings(self) -> bool:
        return self.when_thinking_enabled is not None or self.when_thinking_disabled is not None or self.thinking is not None

    def effective_when_thinking_enabled(self) -> dict[str, Any]:
        effective = dict(self.when_thinking_enabled or {})
        if self.thinking is not None:
            merged_thinking = {**(effective.get("thinking") or {}), **self.thinking}
            effective["thinking"] = merged_thinking
        return effective

    def effective_when_thinking_disabled(self) -> dict[str, Any]:
        return dict(self.when_thinking_disabled or {})


def _provider_name_for_kind(provider_kind: ProviderKind) -> str:
    if provider_kind is ProviderKind.ANTHROPIC_COMPATIBLE:
        return "anthropic"
    if provider_kind is ProviderKind.VLLM_OPENAI_COMPATIBLE:
        return "vllm"
    return "openai"


def _image_generation_endpoint_configured(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    endpoint = value.get("endpoint") or value.get("path") or value.get("image_generation_path")
    return isinstance(endpoint, str) and bool(endpoint.strip())


class ProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    default_model: str | None = None
    subsystem_models: dict[str, str] = Field(default_factory=dict)


class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    source_path: str | None = None
    skill_roots: list[str] = Field(default_factory=list)
    inline_tools: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    prompts: list[dict[str, Any]] = Field(default_factory=list)
    catalog_metadata: dict[str, Any] = Field(default_factory=dict)


class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    description: str = ""
    transport_kind: McpTransportKind = McpTransportKind.STDIO
    connection_config: dict[str, Any] = Field(default_factory=dict)
    startup_policy: str = "lazy"
    refresh_policy: str = "fingerprint"
    approval_policy: str = "runtime"
    tool_prefix: str | None = None
    collision_policy: str = "preserve_builtin"
    tool_allowlist: list[str] = Field(default_factory=list)
    tool_allowlist_active: bool = False
    tool_denylist: list[str] = Field(default_factory=list)
    oauth: dict[str, Any] = Field(default_factory=dict)
    env_resolution: dict[str, str] = Field(default_factory=dict)
    header_templates: dict[str, str] = Field(default_factory=dict)
    resource_policy: dict[str, Any] = Field(default_factory=dict)
    prompt_policy: dict[str, Any] = Field(default_factory=dict)
    reconnect_policy: dict[str, Any] = Field(default_factory=dict)
    healthcheck: dict[str, Any] = Field(default_factory=dict)


class PromptSnapshotRetentionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    store_path: str | None = None
    ttl_days: int = 7
    max_snapshots_per_thread: int = 10


class MemorySessionSnapshotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    store_path: str | None = None


class MemoryUpdateQueueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    debounce_seconds: float = 1.5
    min_window_seconds: float = 5.0
    default_window_seconds: float = 30.0
    max_window_seconds: float = 60.0
    min_batch_turns: int = 4
    max_batch_turns: int = 8

    @model_validator(mode="after")
    def normalize_window_bounds(self) -> "MemoryUpdateQueueConfig":
        self.debounce_seconds = max(0.0, float(self.debounce_seconds))
        self.min_window_seconds = max(0.0, float(self.min_window_seconds))
        self.default_window_seconds = max(self.min_window_seconds, float(self.default_window_seconds))
        self.max_window_seconds = max(self.default_window_seconds, float(self.max_window_seconds))
        self.min_batch_turns = max(1, int(self.min_batch_turns))
        self.max_batch_turns = max(self.min_batch_turns, int(self.max_batch_turns))
        return self


class TranscriptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    sqlite_path: str | None = None
    fts_enabled: bool = True
    transcript_context_tokens: int = 4000


class HCMSStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    display_name: str | None = None
    max_chars: int = 2200
    injection_chars: int = 1200
    max_tokens: int | None = None
    injection_tokens: int | None = None
    category_bias: str = "general"


class HCMSArchiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    sqlite_path: str | None = None
    fts_enabled: bool = True
    max_hits: int = 8


class HCMSEngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    configured: bool = False
    roles: tuple[str, ...] = ("sync", "index", "reflection", "explain", "memory_write")
    settings: dict[str, Any] = Field(default_factory=dict)


class HCMSEnginesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_engine_id: str | None = None
    catalog: dict[str, HCMSEngineConfig] = Field(default_factory=dict)


class HCMSReflectionJobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    schedule_kind: str = "interval"
    cron: str | None = None
    interval_seconds: int | None = None
    template: str = "custom"
    target_store_id: str = "hcms_workspace"
    instructions: str | None = None
    source_query: str | None = None


class HCMSReflectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    tick_seconds: int = 60
    auto_register_defaults: bool = True
    jobs: dict[str, HCMSReflectionJobConfig] = Field(default_factory=dict)


class HCMSCompactionHooksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    include_archive: bool = True
    include_engine_notes: bool = True


class HCMSSessionSearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    default_limit: int = 5
    model_name: str | None = None
    max_summary_input_chars: int = 100_000
    max_summary_output_chars: int = 10_000
    summary_timeout_seconds: float = 60.0


class HCMSRecallConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    injection_mode: str = "context_v2"
    max_candidates: int = 16
    max_evidence: int = 8
    min_relevance_score: float = 0.05
    turn_recall_token_budget: int = 900
    enable_model_rerank: bool = False
    rerank_model_name: str | None = None
    bm25_weight: float = 0.3
    vector_weight: float = 0.4
    graph_weight: float = 0.2
    temporal_weight: float = 0.1
    rrf_k: int = 60
    enable_adaptive_weights: bool = True
    enable_cache: bool = True
    cache_ttl: int = 300
    cache_max_entries: int = 100
    enable_mmr: bool = True
    mmr_lambda: float = 0.72

    @model_validator(mode="after")
    def normalize_recall_bounds(self) -> "HCMSRecallConfig":
        normalized_injection_mode = str(self.injection_mode or "context_v2").strip().lower().replace("-", "_")
        injection_mode_aliases = {
            "legacy": "legacy_prompt_append",
            "legacy_append": "legacy_prompt_append",
            "prompt_append": "legacy_prompt_append",
            "memory_context": "legacy_prompt_append",
            "memory_prompt": "legacy_prompt_append",
            "v1": "legacy_prompt_append",
            "context_v2": "context_v2",
            "runtime_context_v2": "context_v2",
            "block_assembly": "context_v2",
            "v2": "context_v2",
        }
        self.injection_mode = injection_mode_aliases.get(normalized_injection_mode, normalized_injection_mode)
        if self.injection_mode not in {"legacy_prompt_append", "context_v2"}:
            raise ValueError("hcms.recall.injection_mode must be one of: legacy_prompt_append, context_v2")
        self.max_candidates = max(1, int(self.max_candidates))
        self.max_evidence = max(1, int(self.max_evidence))
        self.min_relevance_score = max(0.0, min(float(self.min_relevance_score), 1.0))
        self.turn_recall_token_budget = max(1, int(self.turn_recall_token_budget))
        self.bm25_weight = max(0.0, min(float(self.bm25_weight), 1.0))
        self.vector_weight = max(0.0, min(float(self.vector_weight), 1.0))
        self.graph_weight = max(0.0, min(float(self.graph_weight), 1.0))
        self.temporal_weight = max(0.0, min(float(self.temporal_weight), 1.0))
        self.rrf_k = max(1, int(self.rrf_k))
        self.cache_ttl = max(0, int(self.cache_ttl))
        self.cache_max_entries = max(0, int(self.cache_max_entries))
        self.mmr_lambda = max(0.0, min(float(self.mmr_lambda), 1.0))
        return self


class HCMSQualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_accept_confidence: float = 0.82
    auto_supersede_confidence: float = 0.90
    max_direct_content_chars: int = 360


class HCMSUpdaterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    mode: str = "heuristic"
    model_name: str | None = None
    max_input_tokens: int = 6000
    max_output_tokens: int = 1800
    fact_confidence_threshold: float = 0.82
    outcome_confidence_threshold: float = 0.86
    timeout_seconds: float = 60.0
    fail_open: bool = True

    @model_validator(mode="after")
    def normalize_updater(self) -> "HCMSUpdaterConfig":
        normalized = str(self.mode or "heuristic").strip().lower().replace("-", "_")
        aliases = {
            "default": "heuristic",
            "compiler": "heuristic",
            "zero_llm": "heuristic",
            "zero_llm_compiler": "heuristic",
            "rule": "rule_based",
            "rules": "rule_based",
            "rulebased": "rule_based",
            "rule_based": "rule_based",
            "structured_json": "structured",
            "json": "structured",
            "json_plan": "structured",
            "llm": "structured",
            "llm_json": "structured",
            "structured": "structured",
        }
        self.mode = aliases.get(normalized, normalized)
        if self.mode not in {"heuristic", "rule_based", "structured"}:
            raise ValueError("hcms.updater.mode must be one of: heuristic, rule_based, structured")
        self.max_input_tokens = max(256, min(int(self.max_input_tokens), 128_000))
        self.max_output_tokens = max(128, min(int(self.max_output_tokens), 32_000))
        self.fact_confidence_threshold = max(0.0, min(float(self.fact_confidence_threshold), 1.0))
        self.outcome_confidence_threshold = max(0.0, min(float(self.outcome_confidence_threshold), 1.0))
        self.timeout_seconds = max(0.1, min(float(self.timeout_seconds), 600.0))
        return self


class HCMSMaintenanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    automation_enabled: bool = True
    policy: str = "balanced"
    layer_id: str | None = None
    limit: int = 12
    execute: bool = True
    tick_seconds: int = 300
    interval_seconds: int = 6 * 60 * 60
    interval_hours: float | None = None
    min_idle_seconds: int = 0
    max_archive_per_run: int = 2
    max_quality_inspections_per_run: int = 8
    max_reinforce_per_run: int = 6
    min_quality_score_for_execute: float = 0.55
    max_quality_issues_for_execute: int = 30
    run_reflection_due_jobs: bool = True
    include_health: bool = True

    @model_validator(mode="after")
    def normalize_schedule_aliases(self) -> "HCMSMaintenanceConfig":
        if self.interval_hours is not None:
            self.interval_seconds = max(int(float(self.interval_hours) * 60 * 60), 60)
        self.tick_seconds = max(int(self.tick_seconds), 10)
        self.interval_seconds = max(int(self.interval_seconds), 60)
        self.min_idle_seconds = max(int(self.min_idle_seconds), 0)
        return self


class HCMSOnboardingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    trigger_when_project_memory_empty: bool = True
    quality_first: bool = True
    target_store_id: str = "hcms_workspace"
    target_layer_id: str = "workspace"
    category: str = "project_context"
    max_files: int = 8
    max_total_chars: int = 12_000
    max_file_chars: int = 2_500
    priority: float = 0.62
    confidence: float = 0.68
    salience: float = 0.72
    include_patterns: tuple[str, ...] = Field(
        default_factory=lambda: (
            "AGENTS.md",
            "README.md",
            "README_zh.md",
            "pyproject.toml",
            "package.json",
            "pnpm-lock.yaml",
            "package-lock.json",
            "yarn.lock",
            "Makefile",
            "makefile",
            "justfile",
            "Taskfile.yml",
            "tox.ini",
            "pytest.ini",
            "setup.cfg",
            "requirements*.txt",
            "docs/guides/*.md",
            "docs/adr/*.md",
            "docs/memory/*.md",
        )
    )
    exclude_patterns: tuple[str, ...] = Field(
        default_factory=lambda: (
            ".env",
            ".env.*",
            "**/.env",
            "**/.env.*",
            "**/*secret*",
            "**/*token*",
            "**/*credential*",
            "**/.git/**",
            "**/.hg/**",
            "**/.svn/**",
            "**/.venv/**",
            "**/venv/**",
            "**/node_modules/**",
            "**/__pycache__/**",
            "**/dist/**",
            "**/build/**",
            "**/.mypy_cache/**",
            "**/.pytest_cache/**",
        )
    )

    @model_validator(mode="after")
    def normalize_bounds(self) -> "HCMSOnboardingConfig":
        self.max_files = max(1, min(int(self.max_files), 32))
        self.max_total_chars = max(400, min(int(self.max_total_chars), 80_000))
        self.max_file_chars = max(200, min(int(self.max_file_chars), self.max_total_chars))
        self.priority = min(max(float(self.priority), 0.0), 1.0)
        self.confidence = min(max(float(self.confidence), 0.0), 1.0)
        self.salience = min(max(float(self.salience), 0.0), 1.0)
        self.target_store_id = (self.target_store_id or "hcms_workspace").strip() or "hcms_workspace"
        self.target_layer_id = (self.target_layer_id or "workspace").strip() or "workspace"
        self.category = (self.category or "project_context").strip() or "project_context"
        return self


class JITContextConfig(BaseModel):
    """Configuration for JIT (Just-In-Time) context loading.

    JIT context loading implements on-demand context retrieval to minimize
    upfront token usage while maintaining task effectiveness.

    Design principles:
    - Lazy loading: Load only when referenced or needed
    - Progressive disclosure: Start minimal, expand as needed
    - Smart prefetching: Predict and preload likely needs
    - Harness-first: Clean integration with existing services
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # Lazy loading toggles
    lazy_memory: bool = True
    lazy_files: bool = True
    lazy_skills: bool = True
    lazy_tools: bool = True
    lazy_conversation: bool = False  # Keep recent conversation loaded

    # Prefetching
    prefetch_enabled: bool = True
    prefetch_threshold: float = 0.7  # Confidence threshold
    prefetch_max_items: int = 5

    # Caching
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300  # 5 minutes
    max_cache_size_mb: int = 50
    cache_strategy: str = "lru"  # lru, lfu, fifo

    # Performance
    max_load_time_ms: float = 500.0
    parallel_loading: bool = True
    max_parallel_loads: int = 3

    # Metrics
    collect_metrics: bool = True


class ToolOutputBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    default_token_budget: int = 1600
    hard_token_budget: int = 4000
    default_char_budget: int = 6400
    hard_char_budget: int = 16000
    artifact_directory: str = "tool-results"
    command_compaction_enabled: bool = True
    command_compaction_min_chars: int = 1200
    command_compaction_max_chars: int = 4800
    raw_failure_artifacts: bool = True
    raw_compaction_artifacts: bool = True
    command_profiles: tuple[str, ...] = ("test", "typecheck", "lint", "git", "package", "container", "search")

    @model_validator(mode="after")
    def normalize_bounds(self) -> "ToolOutputBudgetConfig":
        self.default_token_budget = max(32, min(int(self.default_token_budget), 64_000))
        self.hard_token_budget = max(self.default_token_budget, min(int(self.hard_token_budget), 128_000))
        self.default_char_budget = max(128, min(int(self.default_char_budget), 256_000))
        self.hard_char_budget = max(self.default_char_budget, min(int(self.hard_char_budget), 512_000))
        self.command_compaction_min_chars = max(200, min(int(self.command_compaction_min_chars), 100_000))
        self.command_compaction_max_chars = max(400, min(int(self.command_compaction_max_chars), self.default_char_budget))
        allowed = {"test", "typecheck", "lint", "git", "package", "container", "search"}
        profiles = tuple(
            profile
            for profile in dict.fromkeys(str(item or "").strip().lower() for item in self.command_profiles)
            if profile in allowed
        )
        self.command_profiles = profiles or ("test", "typecheck", "lint", "git", "package", "container", "search")
        self.artifact_directory = str(self.artifact_directory or "tool-results").strip().strip("/\\") or "tool-results"
        return self


class ToolVisibilityBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    visible_schema_token_budget: int = 12000
    action_prefilter_enabled: bool = True
    action_prefilter_min_tools: int = 48
    action_prefilter_max_visible: int = 56
    action_prefilter_min_score: float = 0.25


class HCMSRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    storage_backend: str = "hybrid"
    stores: dict[str, HCMSStoreConfig] = Field(default_factory=dict)
    archive: HCMSArchiveConfig = Field(default_factory=HCMSArchiveConfig)
    engines: HCMSEnginesConfig = Field(default_factory=HCMSEnginesConfig)
    reflection: HCMSReflectionConfig = Field(default_factory=HCMSReflectionConfig)
    compaction_hooks: HCMSCompactionHooksConfig = Field(default_factory=HCMSCompactionHooksConfig)
    session_search: HCMSSessionSearchConfig = Field(default_factory=HCMSSessionSearchConfig)
    recall: HCMSRecallConfig = Field(default_factory=HCMSRecallConfig)
    quality: HCMSQualityConfig = Field(default_factory=HCMSQualityConfig)
    updater: HCMSUpdaterConfig = Field(default_factory=HCMSUpdaterConfig)
    maintenance: HCMSMaintenanceConfig = Field(default_factory=HCMSMaintenanceConfig)
    onboarding: HCMSOnboardingConfig = Field(default_factory=HCMSOnboardingConfig)
    transcript: TranscriptConfig = Field(default_factory=TranscriptConfig)
    prompt_snapshot: PromptSnapshotRetentionConfig = Field(default_factory=PromptSnapshotRetentionConfig)
    session_snapshot: MemorySessionSnapshotConfig = Field(default_factory=MemorySessionSnapshotConfig)
    update_queue: MemoryUpdateQueueConfig = Field(default_factory=MemoryUpdateQueueConfig)

    @model_validator(mode="after")
    def ensure_default_stores(self) -> "HCMSRuntimeConfig":
        default_stores = {
            "hcms_workspace": HCMSStoreConfig(
                display_name="HCMS Workspace Layer",
                max_chars=2800,
                injection_chars=1400,
                max_tokens=700,
                injection_tokens=350,
                category_bias="workspace",
            ),
            "hcms_user": HCMSStoreConfig(
                display_name="HCMS User Layer",
                max_chars=1800,
                injection_chars=1000,
                max_tokens=450,
                injection_tokens=250,
                category_bias="preference",
            ),
        }
        merged = dict(self.stores)
        for store_id, config in default_stores.items():
            if store_id not in merged:
                merged[store_id] = config
            else:
                updates: dict[str, int | str] = {}
                if merged[store_id].display_name is None:
                    updates["display_name"] = config.display_name or store_id
                if merged[store_id].max_tokens is None:
                    updates["max_tokens"] = config.max_tokens or max(merged[store_id].max_chars // 4, 1)
                if merged[store_id].injection_tokens is None:
                    updates["injection_tokens"] = config.injection_tokens or max(merged[store_id].injection_chars // 4, 1)
                if updates:
                    merged[store_id] = merged[store_id].model_copy(update=updates)
        self.stores = merged
        return self

    @model_validator(mode="after")
    def normalize_storage_backend(self) -> "HCMSRuntimeConfig":
        backend = str(self.storage_backend or "hybrid").strip().lower().replace("-", "_")
        aliases = {
            "file": "filesystem",
            "files": "filesystem",
            "json": "filesystem",
            "local": "filesystem",
            "markdown": "hybrid",
        }
        backend = aliases.get(backend, backend)
        if backend not in {"filesystem", "hybrid"}:
            raise ValueError("hcms.storage_backend must be one of: filesystem, hybrid")
        self.storage_backend = backend
        return self


class GitRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    required: bool = True
    provider: str = "github"
    token_env: str = "GITHUB_TOKEN"
    user_name: str | None = None
    user_email: str | None = None
    remote_url: str | None = None

    @model_validator(mode="after")
    def normalize_git_config(self) -> "GitRuntimeConfig":
        self.provider = str(self.provider or "github").strip().lower().replace("-", "_") or "github"
        token_env = str(self.token_env or "GITHUB_TOKEN").strip()
        if token_env.startswith("${") and token_env.endswith("}"):
            token_env = token_env[2:-1].strip()
        self.token_env = token_env or "GITHUB_TOKEN"
        self.user_name = _clean_optional_string(self.user_name)
        self.user_email = _clean_optional_string(self.user_email)
        self.remote_url = _clean_optional_string(self.remote_url)
        return self


def _clean_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


class SkillCuratorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    automation_enabled: bool = True
    schedule: str = "interval"
    auto_merge: bool = True
    auto_review: bool = True
    pin_protection: bool = True
    tick_seconds: int = 60
    interval_seconds: int = 6 * 60 * 60
    interval_hours: float | None = None
    min_idle_hours: float = 0.0
    core_score_threshold: int = 500
    observe_score_threshold: int = 20
    observe_min_age_days: int = 14
    template_promotion_enabled: bool = True
    template_use_threshold: int = 3
    template_context_threshold: int = 2
    maintenance_enabled: bool = True
    max_actions_per_run: int = 25
    max_archive_per_run: int = 2
    max_quality_plan_per_run: int = 6
    max_merge_plan_per_run: int = 3
    max_procedure_promotions_per_run: int = 3
    max_template_promotions_per_run: int = 3
    auto_promote_procedures: bool = True
    dry_run: bool = False
    force: bool = False

    @model_validator(mode="after")
    def normalize_curator_alias_fields(self) -> "SkillCuratorConfig":
        if self.enabled is not None:
            self.automation_enabled = bool(self.enabled)
        if self.interval_hours is not None:
            self.interval_seconds = max(int(float(self.interval_hours) * 60 * 60), 60)
        normalized_schedule = str(self.schedule or "interval").strip().lower()
        if normalized_schedule in {"weekly", "week"}:
            self.schedule = "weekly"
            self.interval_seconds = 7 * 24 * 60 * 60
        elif normalized_schedule in {"daily", "day"}:
            self.schedule = "daily"
            self.interval_seconds = 24 * 60 * 60
        elif normalized_schedule in {"hourly", "hour"}:
            self.schedule = "hourly"
            self.interval_seconds = 60 * 60
        else:
            self.schedule = "interval"
        if not self.pin_protection:
            self.force = True
        return self


class SkillsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    watch_enabled: bool = True
    external_dirs: list[str] = Field(default_factory=list)
    enabled_ids: list[str] = Field(default_factory=list)
    disabled_ids: list[str] = Field(default_factory=list)
    governance_root: str | None = None
    quarantine_root: str | None = None
    history_root: str | None = None
    quarantine_on_install: bool = True
    allow_remote_install: bool = True
    curator: SkillCuratorConfig = Field(default_factory=SkillCuratorConfig)


class AnvilPathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home: str | None = None
    profile: str | None = None


class WorkspacePathBridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str
    display_root: str
    actual_root: str | None = None
    enabled: bool = True


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str | None = None
    mode: str = "thread"
    auto_host_drives: bool = True
    auto_host_drive_letters: list[str] = Field(default_factory=list)
    path_bridges: list[WorkspacePathBridgeConfig] = Field(default_factory=list)


class SubagentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_concurrency: int = 3
    max_depth: int = 1
    timeout_seconds: int = 900
    batch_join_timeout_seconds: int = 900
    show_child_reasoning: bool = False
    progress_event_budget: int = 20
    allow_recursive_delegation: bool = False
    allow_memory_write: bool = False
    allow_clarification: bool = False


class RiskThresholdsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_allow_below: str = "low"
    require_approval_above: str = "medium"
    always_block: str = "critical"


class ToolPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_mode: str = "suggest"
    sandbox_policy: str | None = None


class GuardrailToolPoliciesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shell_execution: ToolPolicyConfig = Field(default_factory=lambda: ToolPolicyConfig(approval_mode="require"))
    filesystem_write: ToolPolicyConfig = Field(default_factory=lambda: ToolPolicyConfig(approval_mode="suggest"))
    network_request: ToolPolicyConfig = Field(default_factory=lambda: ToolPolicyConfig(approval_mode="suggest"))
    delegation: ToolPolicyConfig = Field(default_factory=lambda: ToolPolicyConfig(approval_mode="auto"))


class GuardrailsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "builtin"
    require_network_approval: bool = False
    fail_closed: bool = True
    default_approval_mode: str = "suggest"
    risk_thresholds: RiskThresholdsConfig = Field(default_factory=RiskThresholdsConfig)
    tool_policies: GuardrailToolPoliciesConfig = Field(default_factory=GuardrailToolPoliciesConfig)


class SandboxAuditConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    log_path: str | None = None
    async_write: bool = True


class HostIsolatedSandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_paths: list[str] = Field(default_factory=list)
    network_access: bool = False
    max_execution_time: int = 30


class ContainerIsolatedSandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = "python:3.12-slim"
    allowed_paths: list[str] = Field(default_factory=list)
    network_access: bool = False
    max_execution_time: int = 30


class SandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit: SandboxAuditConfig = Field(default_factory=SandboxAuditConfig)
    host_isolated: HostIsolatedSandboxConfig = Field(default_factory=HostIsolatedSandboxConfig)
    isolated: ContainerIsolatedSandboxConfig = Field(default_factory=ContainerIsolatedSandboxConfig)


class SummarizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    token_threshold: int = 80000
    keep_recent_turns: int = 10
    emergency_threshold: int = 110000
    model_name: str | None = None


class TitleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_length: int = 60
    generation_strategy: str = "truncate"
    model_name: str | None = None


class PlanModeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    default: bool = False


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = 3
    initial_delay: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay: float = 30.0


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    retry: RetryConfig = Field(default_factory=RetryConfig)
    fallback_models: list[str] = Field(default_factory=list)
    default: str | None = None
    internal_task_model: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    subsystems: dict[str, str] = Field(default_factory=dict)
    vision: dict[str, Any] = Field(default_factory=dict)


class ConfigFreshnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mtime_watch_enabled: bool = True
    watch_interval_seconds: int = 5


class TokenUsagePricingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    cache_read_cost_per_million: float | None = None
    cache_write_cost_per_million: float | None = None
    reasoning_cost_per_million: float | None = None
    request_cost: float | None = None
    source: str = "config"


class TokenUsageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    log_per_turn: bool = False
    include_entries: bool = True
    currency: str = "USD"
    cost_precision: int = 8
    pricing: dict[str, TokenUsagePricingConfig] = Field(default_factory=dict)


class TrajectoryCompressionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_turns: int | None = 80
    keep_first_turns: int = 2
    keep_last_turns: int = 40
    max_message_chars: int = 12000
    max_tool_result_chars: int = 6000
    max_metadata_chars: int = 4000


class TrajectoryExportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    export_root: str = ".anvil/trajectories"
    default_format: str = "anvil"
    batch_include_entries_default: bool = False
    batch_write_jsonl_default: bool = True
    batch_min_quality_status_default: str = "warning"
    include_system: bool = False
    include_tools: bool = True
    include_tool_args: bool = True
    include_metadata: bool = True
    include_reasoning: bool = False
    include_parsed_tool_calls: bool = True
    include_hidden_steps: bool = False
    include_artifacts: bool = True
    include_approvals: bool = True
    include_token_usage: bool = True
    scrub_secrets: bool = True
    compression: TrajectoryCompressionConfig = Field(default_factory=TrajectoryCompressionConfig)

    @model_validator(mode="after")
    def normalize_batch_quality_gate(self) -> "TrajectoryExportConfig":
        value = str(self.batch_min_quality_status_default).strip().lower()
        allowed = {"failed", "warning", "passed"}
        if value not in allowed:
            raise ValueError(
                "trajectory_export.batch_min_quality_status_default must be one of: "
                "failed, warning, passed"
            )
        self.batch_min_quality_status_default = value
        return self


class ScheduledTasksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    tick_seconds: int = 60
    state_path: str | None = None
    default_execution_mode: str = "agent"
    default_profile: str | None = None
    default_model: str | None = None
    max_due_per_tick: int = 3
    output_root: str = ".anvil/scheduled-task-output"
    prompt_safety_scan_enabled: bool = True


class LoopDetectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_identical_turns: int | None = None
    warn_threshold: int = 12
    hard_limit: int = 24
    window_size: int = 80
    max_tracked_runs: int = 200

    @model_validator(mode="after")
    def normalize_limits(self) -> "LoopDetectionConfig":
        if self.max_identical_turns is not None:
            legacy_limit = max(2, int(self.max_identical_turns))
            if "warn_threshold" not in self.model_fields_set:
                self.warn_threshold = max(2, legacy_limit - 2)
            if "hard_limit" not in self.model_fields_set:
                self.hard_limit = legacy_limit
        self.warn_threshold = max(2, int(self.warn_threshold or 12))
        self.hard_limit = max(self.warn_threshold + 1, int(self.hard_limit or 24))
        self.window_size = max(self.hard_limit, int(self.window_size or 80))
        self.max_tracked_runs = max(1, int(self.max_tracked_runs or 200))
        return self


class UploadsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    convert_documents: bool = True
    pdf_converter: str = "auto"
    ocr_strategy: str = "local"
    ocr_enabled: bool = True
    max_outline_entries: int = 50
    preview_line_count: int = 5
    max_ocr_pages: int = 20
    ocr_languages: str = "eng+chi_sim"


class DocumentProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_text: list[str] = Field(default_factory=lambda: ["pymupdf4llm", "markitdown", "pypdf2"])
    pdf_ocr: list[str] = Field(default_factory=lambda: ["marker-pdf", "tesseract"])
    office: list[str] = Field(default_factory=lambda: ["python-docx", "python-pptx", "openpyxl", "markitdown"])


class DocumentExportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_mode: str = "editable"


class DocumentScratchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cleanup_on_success: bool = True


class DocumentPageImageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class DocumentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    providers: DocumentProvidersConfig = Field(default_factory=DocumentProvidersConfig)
    export: DocumentExportConfig = Field(default_factory=DocumentExportConfig)
    scratch: DocumentScratchConfig = Field(default_factory=DocumentScratchConfig)
    page_image_derivatives: DocumentPageImageConfig = Field(default_factory=DocumentPageImageConfig)


class CodeSemanticsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = "static"
    external_index_path: str | None = None
    lsp_command: list[str] = Field(default_factory=list)
    lsp_cwd: str | None = None
    lsp_env: dict[str, str] = Field(default_factory=dict)
    lsp_timeout_seconds: float = 8.0
    lsp_session_idle_ttl_seconds: float = 300.0
    lsp_stderr_max_chars: int = 2000
    lsp_initialization_options: dict[str, Any] = Field(default_factory=dict)
    fallback_to_static: bool = True
    validate_freshness: bool = True
    watch_default_auto_recover: bool = True
    watch_state_ttl_seconds: float = 3600.0
    watch_max_entries: int = 128
    watch_drift_path_limit: int = 20

    @model_validator(mode="after")
    def normalize_backend(self) -> "CodeSemanticsConfig":
        backend = str(self.backend or "static").strip().lower()
        allowed = {"static", "external_index", "lsp_jsonrpc"}
        if backend not in allowed:
            raise ValueError("code_semantics.backend must be one of: static, external_index, lsp_jsonrpc")
        self.backend = backend
        self.lsp_command = [str(part) for part in self.lsp_command if str(part)]
        self.lsp_timeout_seconds = max(0.5, float(self.lsp_timeout_seconds or 8.0))
        self.lsp_session_idle_ttl_seconds = max(0.0, min(float(self.lsp_session_idle_ttl_seconds), 3600.0))
        self.lsp_stderr_max_chars = max(0, min(int(self.lsp_stderr_max_chars), 10000))
        self.watch_state_ttl_seconds = max(0.0, min(float(self.watch_state_ttl_seconds), 24 * 60 * 60))
        self.watch_max_entries = max(1, min(int(self.watch_max_entries), 1024))
        self.watch_drift_path_limit = max(1, min(int(self.watch_drift_path_limit), 200))
        return self


class TerminalBackendMountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_path: str
    container_path: str
    read_only: bool = False


class TerminalBackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "local"
    label: str | None = None
    enabled: bool = True
    command_prefix: list[str] = Field(default_factory=list)
    default_cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    env_passthrough: list[str] = Field(default_factory=list)
    env_prefix_passthrough: list[str] = Field(default_factory=list)
    timeout_seconds: int | None = None
    lifetime_seconds: int | None = None
    image: str | None = None
    host: str | None = None
    username: str | None = None
    sandbox_id: str | None = None
    app: str | None = None
    runtime: str | None = None
    working_dir: str | None = None
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    sync: dict[str, Any] = Field(default_factory=dict)
    mounts: list[TerminalBackendMountConfig] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TerminalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_backend: str = "local"
    backends: dict[str, TerminalBackendConfig] = Field(default_factory=dict)
    logs_dir: str | None = None

    @model_validator(mode="after")
    def ensure_local_backend(self) -> "TerminalConfig":
        backends = dict(self.backends)
        if "local" not in backends:
            backends["local"] = TerminalBackendConfig(kind="local", label="Local shell")
        self.backends = backends
        if self.active_backend not in self.backends:
            self.backends[self.active_backend] = TerminalBackendConfig(kind=self.active_backend)
        return self


class ContextFilesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    filenames: list[str] = Field(
        default_factory=lambda: [
            "AGENTS.md",
            "PROJECT_RULES.md",
            ".cursorrules",
            ".windsurfrules",
        ]
    )
    rule_globs: list[str] = Field(default_factory=lambda: [".cursor/rules/*.md", ".github/copilot-instructions.md"])
    include_readme: bool = False
    recursive_agents: bool = False
    recursive_names: list[str] = Field(default_factory=lambda: ["AGENTS.md", "PROJECT_RULES.md"])
    max_files: int = 12
    max_chars: int = 12000
    max_chars_per_file: int = 4000
    max_discovery_paths: int = 5000

    @model_validator(mode="after")
    def normalize_budgets(self) -> "ContextFilesConfig":
        self.max_files = max(int(self.max_files), 0)
        self.max_chars = max(int(self.max_chars), 0)
        self.max_chars_per_file = max(int(self.max_chars_per_file), 0)
        self.max_discovery_paths = max(int(self.max_discovery_paths), 0)
        return self


class ExtensionsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: dict[str, bool] = Field(default_factory=dict)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    plugins: dict[str, PluginConfig] = Field(default_factory=dict)


class EffectiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model: str | None = None
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    subsystem_models: dict[str, str] = Field(default_factory=dict)
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig)
    hcms: HCMSRuntimeConfig = Field(default_factory=HCMSRuntimeConfig)
    git: GitRuntimeConfig = Field(default_factory=GitRuntimeConfig)
    skills_config: SkillsConfig = Field(default_factory=SkillsConfig)
    subagents: SubagentsConfig = Field(default_factory=SubagentsConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig)
    jit_context: JITContextConfig = Field(default_factory=JITContextConfig)
    tool_output_budget: ToolOutputBudgetConfig = Field(default_factory=ToolOutputBudgetConfig)
    tool_visibility_budget: ToolVisibilityBudgetConfig = Field(default_factory=ToolVisibilityBudgetConfig)
    title: TitleConfig = Field(default_factory=TitleConfig)
    plan_mode: PlanModeConfig = Field(default_factory=PlanModeConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    config_freshness: ConfigFreshnessConfig = Field(default_factory=ConfigFreshnessConfig)
    token_usage: TokenUsageConfig = Field(default_factory=TokenUsageConfig)
    trajectory_export: TrajectoryExportConfig = Field(default_factory=TrajectoryExportConfig)
    scheduled_tasks: ScheduledTasksConfig = Field(default_factory=ScheduledTasksConfig)
    loop_detection: LoopDetectionConfig = Field(default_factory=LoopDetectionConfig)
    uploads: UploadsConfig = Field(default_factory=UploadsConfig)
    documents: DocumentsConfig = Field(default_factory=DocumentsConfig)
    code_semantics: CodeSemanticsConfig = Field(default_factory=CodeSemanticsConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    context_files: ContextFilesConfig = Field(default_factory=ContextFilesConfig)
    anvil: AnvilPathsConfig = Field(default_factory=AnvilPathsConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    sandbox_mode: SandboxMode = SandboxMode.LOCAL
    requirements: dict[str, Any] = Field(default_factory=dict)
    additional_settings: dict[str, Any] = Field(default_factory=dict)


class ConfigLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: ConfigLayerKind
    data: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    enabled: bool = True


class ConfigOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_path: str
    layer_name: str
    layer_kind: ConfigLayerKind
    source: str | None = None


class ConfigResolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_config: EffectiveConfig
    origins: dict[str, ConfigOrigin]
    fingerprint: str
    layers: list[ConfigLayer]
