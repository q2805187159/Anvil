from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil.agents.features import RuntimeFeatureSet
from anvil.config import (
    ConfigLayer,
    ConfigLayerKind,
    ConfigService,
    ModelRouteRequest,
    RequiredModelCapabilities,
    resolve_model_route,
)
from anvil.extensions import ExtensionsService
from anvil.runtime.state_v2 import GoalFrame, GoalStack, SalienceRouter
from anvil.runtime.tool_registry import CapabilityAssemblyService
from anvil.runtime.tool_registry import service as tool_registry_service
from anvil.sandbox import PathService, create_sandbox_provider
from anvil.skills import SkillsService
from anvil.subagents import SubagentService


def write_skill(root: Path, slug: str, title: str, body: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


def write_manifest_skill(root: Path, slug: str, frontmatter: str, body: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n{frontmatter.strip()}\n---\n# {slug}\n\n{body}\n",
        encoding="utf-8",
    )


def make_config(contract_tmp_path) -> object:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "demo-skill", "Demo Skill", "Use the demo workflow")
    return ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "skills_config": {
                        "enabled": True,
                        "external_dirs": [str(skills_root)],
                        "enabled_ids": ["demo-skill"],
                    },
                    "subagents": {"enabled": True},
                    "extensions": {
                        "mcp_servers": {
                            "github": {
                                "enabled": True,
                                "transport_kind": "stdio",
                                "connection_config": {
                                    "inline_resources": [
                                        {
                                            "resource_id": "research-index",
                                            "title": "Research Index",
                                            "description": "External research source catalog",
                                        }
                                    ],
                                    "inline_prompts": [
                                        {
                                            "prompt_id": "research-template",
                                            "title": "Research Template",
                                            "description": "Prompt for external research workflows",
                                        }
                                    ],
                                    "inline_tools": [
                                        {
                                            "name": "ext_search",
                                            "display_name": "External Search",
                                            "capability_group": "research",
                                            "deferred": True,
                                            "summary": "Search external research indexes",
                                            "metadata": {"plugin_id": "github-pack"},
                                        }
                                    ]
                                },
                            }
                        }
                    },
                },
            )
        ]
    )


def make_vision_config(contract_tmp_path) -> object:
    result = make_config(contract_tmp_path)
    model = result.effective_config.models["openai"]
    model.supports_vision = True
    model.capabilities.vision = True
    return result


def make_image_generation_config(contract_tmp_path) -> object:
    result = make_config(contract_tmp_path)
    model = result.effective_config.models["openai"]
    model.supports_image_generation = True
    model.capabilities.image_generation = True
    model.image_generation = {
        "providers": ["mock"],
        "mock_image_bytes": "image-bytes",
        "model": "mock-image",
        "endpoint": "/images/generations",
    }
    return result


def make_auxiliary_image_generation_config(contract_tmp_path) -> object:
    result = make_config(contract_tmp_path)
    image_model = result.effective_config.models["openai"].model_copy(
        deep=True,
        update={
            "name": "image_gen",
            "supports_tool_calling": False,
            "supports_image_generation": True,
            "image_generation": {
                "providers": ["mock"],
                "mock_image_bytes": "image-bytes",
                "model": "mock-image",
                "endpoint": "/images/generations",
            },
        }
    )
    image_model.capabilities.tool_calling = False
    image_model.capabilities.image_generation = True
    result.effective_config.models["image_gen"] = image_model
    return result


def test_capability_bundle_service_merges_base_skills_extensions_and_subagents(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-1", path_service=path_service)

    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )
    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(
            skills=True,
            extensions=True,
            subagents=True,
            capability_mentions=True,
        ),
        request_context="$demo-skill @ext_search",
    )

    visible = [entry.name for entry in result.bundle.visible_tools]
    assert "delegated_task" in visible
    assert "subagent" in visible
    assert "ext_search" in visible
    assert result.bundle.enabled_skill_ids == ("demo-skill",)
    assert result.bundle.mentioned_skill_ids == ("demo-skill",)
    assert any(summary.startswith("$demo-skill:") for summary in result.bundle.prompt_safe_summaries)
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools if entry.handler is not None}
    delegated_payload = json.loads(handlers["delegated_task"].invoke({"prompt": "write hello.md"}))
    assert "write_file" in delegated_payload["allowed_tool_names"]


def test_view_image_tool_is_visible_only_for_vision_routes(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_handle = create_sandbox_provider(config_result.effective_config).acquire(
        thread_id="thread-no-vision-route",
        path_service=path_service,
    )
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )
    text_route = resolve_model_route(config_result.effective_config, ModelRouteRequest(subsystem="lead_agent"))
    text_result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(),
        resolved_route=text_route,
    )

    vision_config = make_vision_config(contract_tmp_path / "vision")
    vision_path_service = PathService(contract_tmp_path / "vision-threads")
    vision_sandbox_handle = create_sandbox_provider(vision_config.effective_config).acquire(
        thread_id="thread-vision-route",
        path_service=vision_path_service,
    )
    vision_route = resolve_model_route(
        vision_config.effective_config,
        ModelRouteRequest(
            subsystem="lead_agent",
            required_capabilities=RequiredModelCapabilities(vision=True),
        ),
    )
    vision_result = service.assemble(
        sandbox_handle=vision_sandbox_handle,
        config_result=vision_config,
        feature_set=RuntimeFeatureSet(),
        resolved_route=vision_route,
    )

    assert "view_image" not in {entry.name for entry in text_result.bundle.visible_tools}
    assert "view_image" in {entry.name for entry in vision_result.bundle.visible_tools}


def test_image_generate_tool_is_visible_only_for_image_generation_routes(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_handle = create_sandbox_provider(config_result.effective_config).acquire(
        thread_id="thread-no-image-route",
        path_service=path_service,
    )
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )
    text_route = resolve_model_route(config_result.effective_config, ModelRouteRequest(subsystem="lead_agent"))
    text_result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(),
        resolved_route=text_route,
    )

    image_config = make_image_generation_config(contract_tmp_path / "image")
    image_path_service = PathService(contract_tmp_path / "image-threads")
    image_sandbox_handle = create_sandbox_provider(image_config.effective_config).acquire(
        thread_id="thread-image-route",
        path_service=image_path_service,
    )
    image_route = resolve_model_route(
        image_config.effective_config,
        ModelRouteRequest(
            subsystem="lead_agent",
            required_capabilities=RequiredModelCapabilities(image_generation=True),
        ),
    )
    image_result = service.assemble(
        sandbox_handle=image_sandbox_handle,
        config_result=image_config,
        feature_set=RuntimeFeatureSet(),
        resolved_route=image_route,
    )

    assert "image_generate" not in {entry.name for entry in text_result.bundle.visible_tools}
    assert "image_generate" in {entry.name for entry in image_result.bundle.visible_tools}


def test_image_generate_tool_is_visible_for_tool_calling_route_when_auxiliary_image_model_exists(contract_tmp_path) -> None:
    config_result = make_auxiliary_image_generation_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_handle = create_sandbox_provider(config_result.effective_config).acquire(
        thread_id="thread-aux-image-route",
        path_service=path_service,
    )
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )
    lead_route = resolve_model_route(config_result.effective_config, ModelRouteRequest(subsystem="lead_agent"))

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(),
        resolved_route=lead_route,
    )

    assert lead_route.capabilities.tool_calling is True
    assert lead_route.capabilities.image_generation is False
    assert "image_generate" in {entry.name for entry in result.bundle.visible_tools}


def test_capability_bundle_service_prefilters_large_task_irrelevant_external_catalog(contract_tmp_path) -> None:
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "extensions": {
                        "plugins": {
                            "large-suite": {
                                "enabled": True,
                                "inline_tools": [
                                    {
                                        "name": f"calendar_noise_{index}",
                                        "display_name": f"Calendar Noise {index}",
                                        "capability_group": "google_workspace",
                                        "summary": "List and update calendar events.",
                                    }
                                    for index in range(55)
                                ]
                                + [
                                    {
                                        "name": f"github_code_search_{index}",
                                        "display_name": f"GitHub Code Search {index}",
                                        "capability_group": "code",
                                        "summary": "Search GitHub repositories, pull requests, and code references.",
                                    }
                                    for index in range(4)
                                ],
                            }
                        }
                    },
                },
            )
        ]
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-prefilter", path_service=path_service)

    result = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    ).assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=False),
        request_context="Search GitHub code references for Anvil",
    )
    visible = {entry.name for entry in result.bundle.visible_tools}
    deferred = {entry.name: entry for entry in result.bundle.deferred_tools}

    assert {"capability_search", "tool_catalog", "tool_view"}.issubset(visible)
    assert {f"github_code_search_{index}" for index in range(4)}.issubset(visible)
    assert "calendar_noise_0" in deferred
    assert deferred["calendar_noise_0"].provenance["action_prefilter"]["status"] == "deferred_due_low_task_relevance"


def test_missing_mcp_key_is_hidden_but_skills_remain_visible(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    monkeypatch.setattr("anvil.skills.service.default_repo_skill_root", lambda: contract_tmp_path / "empty-default-skills")
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: contract_tmp_path / "empty-repo-agents-skills")
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: contract_tmp_path / "empty-user-agents-skills")
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: contract_tmp_path / "empty-workspace-skills")
    skills_root = contract_tmp_path / "skills"
    skill_dir = skills_root / "linear"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: linear\n"
        "description: Manage Linear issues.\n"
        "prerequisites:\n"
        "  env_vars: [LINEAR_API_KEY]\n"
        "---\n\n"
        "# Linear\n\n"
        "Use when Linear API access is configured.\n",
        encoding="utf-8",
    )
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "skills_config": {
                        "enabled": True,
                        "external_dirs": [str(skills_root)],
                    },
                    "extensions": {
                        "mcp_servers": {
                            "secure": {
                                "enabled": True,
                                "transport_kind": "stdio",
                                "connection_config": {
                                    "command": "npx",
                                    "env": {"MCP_TOKEN": "$MCP_TOKEN"},
                                    "inline_tools": [
                                        {
                                            "name": "secure_tool",
                                            "display_name": "Secure Tool",
                                            "summary": "Requires an MCP token.",
                                        }
                                    ],
                                },
                            }
                        }
                    },
                },
            )
        ]
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-missing-env", path_service=path_service)

    result = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    ).assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True),
        request_context="$linear @secure_tool",
    )

    assert "linear" in result.bundle.enabled_skill_ids
    assert result.bundle.mentioned_skill_ids == ("linear",)
    assert any(summary.startswith("$linear:") for summary in result.bundle.prompt_safe_summaries)
    assert "secure_tool" not in [entry.name for entry in result.bundle.visible_tools]
    assert "secure" not in result.bundle.effective_mcp_servers


def test_capability_assembly_keeps_lazy_live_mcp_from_blocking_runtime(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_probe_count = 0

    def fail_live_probe(*args, **kwargs):
        nonlocal live_probe_count
        live_probe_count += 1
        raise AssertionError("lazy MCP servers must not be live-probed during run capability assembly")

    monkeypatch.setattr(
        "anvil.extensions.materializer.ExtensionsMaterializer._discover_live_capabilities",
        fail_live_probe,
    )
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "extensions": {
                        "mcp_servers": {
                            "remote_docs": {
                                "enabled": True,
                                "transport_kind": "http",
                                "startup_policy": "lazy",
                                "connection_config": {"url": "https://example.invalid/mcp"},
                            }
                        }
                    },
                },
            )
        ]
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-lazy-mcp", path_service=path_service)

    result = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    ).assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=False, extensions=True, subagents=False),
        live_extensions=True,
    )

    assert live_probe_count == 0
    assert result.bundle.effective_mcp_servers == ()
    assert all(entry.source_id != "remote_docs" for entry in result.bundle.visible_tools)


def test_lazy_main_run_mcp_defer_does_not_poison_config_discovery_cache(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_probe_count = 0

    def fail_live_probe(*args, **kwargs):
        nonlocal live_probe_count
        live_probe_count += 1
        raise AssertionError("lazy MCP servers must not be live-probed during run capability assembly")

    monkeypatch.setattr(
        "anvil.extensions.materializer.ExtensionsMaterializer._discover_live_capabilities",
        fail_live_probe,
    )
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "extensions": {
                        "mcp_servers": {
                            "remote_docs": {
                                "enabled": True,
                                "transport_kind": "http",
                                "startup_policy": "lazy",
                                "connection_config": {
                                    "url": "https://example.invalid/mcp",
                                    "inline_tools": [
                                        {
                                            "name": "docs_inline",
                                            "display_name": "Docs Inline",
                                            "summary": "Inline docs fallback.",
                                        }
                                    ],
                                },
                            }
                        }
                    },
                },
            )
        ]
    )
    extensions_service = ExtensionsService()
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-lazy-mcp-cache", path_service=path_service)

    run_result = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=extensions_service,
        subagent_service=SubagentService(),
    ).assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=False, extensions=True, subagents=False),
        live_extensions=True,
    )
    config_result_after_run = extensions_service.discover(
        config=config_result.effective_config,
        fingerprint=config_result.fingerprint,
        live=False,
    )

    assert live_probe_count == 0
    assert run_result.bundle.effective_mcp_servers == ()
    assert config_result_after_run.effective_mcp_servers == ("remote_docs",)
    assert config_result_after_run.materializations[0].tools[0].name == "docs_inline"


def test_capability_assembly_materializes_eager_mcp_during_runtime(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_probe_count = 0

    def fake_live_probe(self, server_id, server):
        nonlocal live_probe_count
        live_probe_count += 1
        return self._build_inline_tools(server_id, server, resources=(), prompts=()), (), ()

    monkeypatch.setattr(
        "anvil.extensions.materializer.ExtensionsMaterializer._discover_live_capabilities",
        fake_live_probe,
    )
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "extensions": {
                        "mcp_servers": {
                            "eager_docs": {
                                "enabled": True,
                                "transport_kind": "http",
                                "startup_policy": "eager",
                                "connection_config": {
                                    "url": "https://example.invalid/mcp",
                                    "inline_tools": [
                                        {
                                            "name": "docs_search",
                                            "display_name": "Docs Search",
                                            "summary": "Search docs.",
                                        }
                                    ],
                                },
                            }
                        }
                    },
                },
            )
        ]
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-eager-mcp", path_service=path_service)

    result = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    ).assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=False, extensions=True, subagents=False),
        live_extensions=True,
    )

    visible_names = {entry.name for entry in result.bundle.visible_tools}

    assert live_probe_count == 1
    assert result.bundle.effective_mcp_servers == ("eager_docs",)
    assert "docs_search" in visible_names


def test_capability_bundle_discovers_skill_summaries_without_injecting_all_into_prompt(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_result = make_config(contract_tmp_path)
    skills_root = contract_tmp_path / "skills"
    empty_default_skills = contract_tmp_path / "empty-default-skills"
    empty_repo_agents = contract_tmp_path / "empty-repo-agents-skills"
    empty_user_agents = contract_tmp_path / "empty-user-agents-skills"
    monkeypatch.setattr("anvil.skills.service.default_repo_skill_root", lambda: empty_default_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: empty_repo_agents)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: empty_user_agents)
    write_skill(skills_root, "agent-core", "Agent Core", "Core workflow summary")
    write_skill(skills_root, "agent-observe", "Agent Observe", "Observe workflow summary")
    usage_root = contract_tmp_path / "governance" / "curator"
    usage_root.mkdir(parents=True)
    (usage_root / "usage.json").write_text(
        json.dumps(
            {
                "agent-core": {
                    "tier": "core",
                    "utility_score": 500,
                    "template_path": "templates/reusable-template.md",
                },
                "agent-observe": {"tier": "observe", "utility_score": 1},
                "demo-skill": {"utility_score": 100},
            }
        ),
        encoding="utf-8",
    )
    config_result = config_result.model_copy(
        update={
            "effective_config": config_result.effective_config.model_copy(
                update={
                    "skills_config": config_result.effective_config.skills_config.model_copy(
                        update={
                            "enabled_ids": [],
                            "governance_root": str(contract_tmp_path / "governance"),
                        }
                    )
                }
            ),
            "fingerprint": "tiered-skills",
        }
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-tier", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True),
    )

    skill_summaries = [summary for summary in result.bundle.prompt_safe_summaries if summary.startswith("$")]
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools if entry.handler is not None}
    skills_payload = json.loads(handlers["skills_list"].invoke({"enabled_only": True}))

    assert skill_summaries == []
    assert skills_payload["total"] == 3
    assert skills_payload["returned"] == 3
    assert [item["skill_id"] for item in skills_payload["items"]] == [
        "agent-core",
        "demo-skill",
        "agent-observe",
    ]
    assert skills_payload["items"][0]["summary"] == "[core] [template] Core workflow summary"
    assert all("description" not in item for item in skills_payload["items"])


def test_capability_bundle_injects_only_mentioned_skill_summaries(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-mentioned-skill", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, capability_mentions=True),
        request_context="$demo-skill",
    )

    skill_summaries = [summary for summary in result.bundle.prompt_safe_summaries if summary.startswith("$")]

    assert result.bundle.mentioned_skill_ids == ("demo-skill",)
    assert result.bundle.enabled_skill_ids == ("demo-skill",)
    assert "$demo-skill: Use the demo workflow" in skill_summaries
    assert all(summary.startswith("$demo-skill") for summary in skill_summaries)


def test_capability_assembly_uses_skill_retrieval_top_k_without_broad_enabled_ids(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_manifest_skill(
        skills_root,
        "code-review",
        """
title: Code Review
summary: Review code regressions and missing tests.
tags: [review, regression, tests]
domain: engineering
task_type: review
allowed_tools: [shell_command, rg]
related_skills: [test-driven-development]
risk_level: low
""",
        "Review code for regressions. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    write_manifest_skill(
        skills_root,
        "test-driven-development",
        """
title: Test Driven Development
summary: Write failing tests before implementation.
tags: [tests, regression]
domain: engineering
task_type: implementation
allowed_tools: [shell_command]
risk_level: low
""",
        "Use red green refactor loops. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    write_manifest_skill(
        skills_root,
        "ppt-generation",
        """
title: Presentation Generation
summary: Create presentation decks and slide layouts.
tags: [slides]
domain: presentation
task_type: generation
risk_level: normal
""",
        "Make slides. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "skills_config": {
                        "enabled": True,
                        "external_dirs": [str(skills_root)],
                        "enabled_ids": ["code-review", "test-driven-development", "ppt-generation"],
                    },
                },
            )
        ]
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-skill-retrieval-top-k", path_service=path_service)
    skills_service = SkillsService()
    skills_service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    service = CapabilityAssemblyService(
        skills_service=skills_service,
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, capability_mentions=True),
        request_context="review code regression tests",
    )

    skill_summaries = [summary for summary in result.bundle.prompt_safe_summaries if summary.startswith("$")]
    diagnostics = result.bundle.assembly_diagnostics

    assert result.bundle.mentioned_skill_ids == ()
    assert result.bundle.enabled_skill_ids == ("code-review", "test-driven-development")
    assert "$code-review: Review code regressions and missing tests." in skill_summaries
    assert "$test-driven-development: Write failing tests before implementation." in skill_summaries
    assert all(not summary.startswith("$ppt-generation") for summary in skill_summaries)
    assert diagnostics.skill_retrieval_selected_ids == ("code-review", "test-driven-development")
    assert diagnostics.skill_retrieval_tiers_used == ("L0", "L1", "L2", "L3")
    assert diagnostics.skill_retrieval_candidate_count == 3
    assert diagnostics.skill_retrieval_loaded_full_content is False
    assert "FULL BODY SENTINEL" not in "\n".join(result.bundle.prompt_safe_summaries)


def test_capability_assembly_routes_skill_retrieval_through_goal_salience(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = contract_tmp_path / "skills"

    monkeypatch.setattr(tool_registry_service, "DEFAULT_SKILL_RETRIEVAL_TOP_K", 1)
    write_manifest_skill(
        skills_root,
        "runtime-context",
        """
title: Runtime Context
summary: Assemble ContextBlock budgets and trace runtime context diagnostics.
tags: [runtime-context, contextblock, salience]
domain: runtime
task_type: implementation
risk_level: low
""",
        "Runtime context body. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    write_manifest_skill(
        skills_root,
        "skill-retrieval-prefetch",
        """
title: Skill Retrieval Prefetch
summary: Continue wiring capability assembly skill retrieval prefetch diagnostics.
tags: [capability, assembly, skill, retrieval, prefetch]
domain: runtime
task_type: implementation
risk_level: low
""",
        "Skill retrieval body. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    write_manifest_skill(
        skills_root,
        "presentation-generation",
        """
title: Presentation Generation
summary: Create slide decks and presentation layouts.
tags: [slides, deck]
domain: presentation
task_type: generation
risk_level: normal
""",
        "Presentation body. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-5.4",
                        }
                    },
                    "skills_config": {
                        "enabled": True,
                        "external_dirs": [str(skills_root)],
                        "enabled_ids": [
                            "runtime-context",
                            "skill-retrieval-prefetch",
                            "presentation-generation",
                        ],
                    },
                },
            )
        ]
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-skill-goal-salience", path_service=path_service)
    skills_service = SkillsService()
    skills_service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    service = CapabilityAssemblyService(
        skills_service=skills_service,
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )
    goal_stack = GoalStack(
        stack_id="goals:thread-skill-goal-salience",
        thread_id="thread-skill-goal-salience",
        active_goal_id="goal-runtime-context",
        goals=[
            GoalFrame(
                goal_id="goal-runtime-context",
                title="Continue Runtime Context V2 salience wiring",
                summary="GoalStack should route skill retrieval toward runtime-context ContextBlock work.",
                next_actions=["wire salience route into capability assembly skill retrieval"],
                keywords=["runtime-context", "contextblock", "salience"],
                priority=0.95,
            )
        ],
    )
    salience_route = SalienceRouter(
        router_id="salience-router:thread-skill-goal-salience",
        thread_id="thread-skill-goal-salience",
    ).route_goal_stack(goal_stack, query="continue implementation")

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, capability_mentions=True),
        request_context="continue implementation",
        salience_route=salience_route,
    )

    diagnostics = result.bundle.assembly_diagnostics

    assert result.bundle.enabled_skill_ids == ("runtime-context",)
    assert "$runtime-context: Assemble ContextBlock budgets and trace runtime context diagnostics." in (
        result.bundle.prompt_safe_summaries
    )
    assert all(not summary.startswith("$presentation-generation") for summary in result.bundle.prompt_safe_summaries)
    assert all(not summary.startswith("$skill-retrieval-prefetch") for summary in result.bundle.prompt_safe_summaries)
    assert diagnostics.skill_retrieval_selected_ids == ("runtime-context",)
    assert diagnostics.skill_retrieval_tiers_used == ("L0", "L1", "L2", "L3", "L5", "L6")
    assert diagnostics.skill_retrieval_l4_rerank_triggered is False
    assert diagnostics.skill_retrieval_l5_hyde_triggered is True
    assert diagnostics.skill_retrieval_l6_prefetch_triggered is True
    assert "contextblock" in diagnostics.skill_retrieval_expanded_query_terms
    assert diagnostics.skill_retrieval_prefetch_ids == ("skill-retrieval-prefetch",)
    assert diagnostics.skill_retrieval_salience_route_id == salience_route.route_id
    assert diagnostics.skill_retrieval_goal_stack_ref == "goals:thread-skill-goal-salience"
    assert diagnostics.skill_retrieval_active_goal_id == "goal-runtime-context"
    assert "goal=Continue Runtime Context V2 salience wiring" in diagnostics.skill_retrieval_query
    assert "FULL BODY SENTINEL" not in "\n".join(result.bundle.prompt_safe_summaries)


def test_skills_list_exposes_curator_metadata(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_result = make_config(contract_tmp_path)
    skills_root = contract_tmp_path / "skills"
    empty_default_skills = contract_tmp_path / "empty-default-skills"
    empty_repo_agents = contract_tmp_path / "empty-repo-agents-skills"
    empty_user_agents = contract_tmp_path / "empty-user-agents-skills"
    monkeypatch.setattr("anvil.skills.service.default_repo_skill_root", lambda: empty_default_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: empty_repo_agents)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: empty_user_agents)
    write_skill(skills_root, "agent-core", "Agent Core", "Core workflow summary")
    usage_root = contract_tmp_path / "governance" / "curator"
    usage_root.mkdir(parents=True)
    (usage_root / "usage.json").write_text(
        json.dumps(
            {
                "agent-core": {
                    "tier": "core",
                    "utility_score": 500,
                    "context_count": 3,
                    "template_path": "templates/reusable-template.md",
                },
                "demo-skill": {"utility_score": 100},
            }
        ),
        encoding="utf-8",
    )
    config_result = config_result.model_copy(
        update={
            "effective_config": config_result.effective_config.model_copy(
                update={
                    "skills_config": config_result.effective_config.skills_config.model_copy(
                        update={
                            "enabled_ids": [],
                            "governance_root": str(contract_tmp_path / "governance"),
                        }
                    )
                }
            ),
            "fingerprint": "skills-list-curator",
        }
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-skills-list-curator", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True),
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools if entry.handler is not None}
    payload = json.loads(handlers["skills_list"].invoke({"enabled_only": True}))

    core = next(item for item in payload["items"] if item["skill_id"] == "agent-core")
    assert core["summary"] == "[core] [template] Core workflow summary"
    assert core["curator"] == {
        "tier": "core",
        "utility_score": 500,
        "context_count": 3,
        "template_path": "templates/reusable-template.md",
        "rank": 0,
    }


def test_skills_list_uses_progressive_disclosure_metadata_payload(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    skills_root = contract_tmp_path / "skills"
    for index in range(12):
        write_skill(
            skills_root,
            f"agent-skill-{index}",
            f"Agent Skill {index}",
            "Long detailed description. " * 80,
        )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-skills-list-compact", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True),
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools if entry.handler is not None}
    payload = json.loads(handlers["skills_list"].invoke({"query": "agent-skill-", "enabled_only": True, "limit": 5}))

    assert payload["total"] == 12
    assert payload["returned"] == 5
    assert payload["truncated"] is True
    assert len(payload["items"]) == 5
    assert all(set(item).issuperset({"skill_id", "title", "summary", "enabled", "valid", "read_tool"}) for item in payload["items"])
    assert all("description" not in item for item in payload["items"])
    assert payload["progressive_disclosure"]["level_1"] == "skills_list metadata"


def test_skills_list_and_view_expose_routing_metadata_without_loading_body(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    skills_root = contract_tmp_path / "skills"
    skill_dir = skills_root / "csv-cleaner"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: csv-cleaner
description: Clean CSV files with schema-aware transformations.
domain: data
task_type: transformation
input_requirements:
  - CSV file
  - column schema
risk_level: low
allowed_tools:
  - read_file
  - write_file
---

# CSV Cleaner

Use this only after the user provides a CSV file and expected output schema.
""",
        encoding="utf-8",
    )
    config_result = config_result.model_copy(
        update={
            "effective_config": config_result.effective_config.model_copy(
                update={
                    "skills_config": config_result.effective_config.skills_config.model_copy(
                        update={"enabled_ids": ["demo-skill", "csv-cleaner"]}
                    )
                }
            ),
            "fingerprint": "skills-routing-metadata",
        }
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-skills-routing-metadata", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True),
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools if entry.handler is not None}
    list_payload = json.loads(handlers["skills_list"].invoke({"query": "schema", "enabled_only": True}))
    view_payload = json.loads(handlers["skill_view"].invoke({"skill_id": "csv-cleaner"}))

    assert list_payload["total"] == 1
    item = list_payload["items"][0]
    assert item["skill_id"] == "csv-cleaner"
    assert item["routing"] == {
        "domain": "data",
        "task_type": "transformation",
        "risk_level": "low",
        "input_requirements": ["CSV file", "column schema"],
    }
    assert item["allowed_tools"] == ["read_file", "write_file"]
    assert "Use this only after" not in json.dumps(item)
    assert item["source_root"] == "skill://csv-cleaner"
    assert item["path"] == "SKILL.md"
    assert view_payload["domain"] == "data"
    assert view_payload["task_type"] == "transformation"
    assert view_payload["input_requirements"] == ["CSV file", "column schema"]
    assert view_payload["risk_level"] == "low"


def test_capability_mentions_are_request_local(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-1", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    without_mentions = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=True),
        request_context=None,
    )
    with_mentions = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=True, capability_mentions=True),
        request_context="@ext_search",
    )

    assert "ext_search" in [entry.name for entry in without_mentions.bundle.deferred_tools]
    assert "ext_search" in [entry.name for entry in with_mentions.bundle.visible_tools]


def test_capability_search_promotes_deferred_tools_without_mentions(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-2", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=False),
        request_context=None,
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}
    payload = handlers["capability_search"].invoke({"query": "ext"})
    rebuilt = service.rebuild_bundle(
        registry=result.registry,
        config_result=config_result,
        promoted_capabilities=tuple(sorted(result.promotion_state)),
        enabled_skill_ids=result.bundle.enabled_skill_ids,
        effective_mcp_servers=result.bundle.effective_mcp_servers,
        effective_extension_sources=result.bundle.effective_extension_sources,
    )

    search_payload = json.loads(payload)
    assert search_payload["matches"][0]["name"] == "ext_search"
    assert "summary" in search_payload["match_traces"]["ext_search"]["matched_fields"]
    assert "name" in search_payload["match_traces"]["ext_search"]["matched_fields"]
    assert "ext_search" in [entry.name for entry in rebuilt.visible_tools]


def test_capability_search_payload_returns_deferred_catalog_matches(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    config_result.effective_config.extensions.mcp_servers["github"].connection_config["inline_tools"].extend(
        [
            {
                "name": "PPT-document",
                "display_name": "PPT Document Generator",
                "capability_group": "document_generation",
                "deferred": True,
                "summary": "Generate reusable PowerPoint documents from outlines",
                "metadata": {"semantic_tags": ["deck-artifact"]},
            },
            {
                "name": "deck-outline",
                "display_name": "Deck Outline",
                "capability_group": "document_generation",
                "deferred": True,
                "summary": "Prepare document outlines for slide decks",
            },
            {
                "name": "slide-assets",
                "display_name": "Slide Assets",
                "capability_group": "document_generation",
                "deferred": True,
                "summary": "Generate image assets for presentation documents",
            },
            {
                "name": "web-document",
                "display_name": "Web Document",
                "capability_group": "research",
                "deferred": True,
                "summary": "Search web documents",
            },
        ]
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-cross-cutting", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=False),
        request_context=None,
    )

    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}
    search_payload = json.loads(handlers["capability_search"].invoke({"query": "PPT document generation"}))
    bounded_payload = json.loads(handlers["capability_search"].invoke({"query": "document", "max_results": 10}))
    catalog_payload = json.loads(handlers["tool_catalog"].invoke({"query": "PPT document"}))
    provenance_catalog_payload = json.loads(handlers["tool_catalog"].invoke({"query": "deck-artifact"}))
    traced_catalog_payload = json.loads(
        handlers["tool_catalog"].invoke(
            {
                "query": "deck-artifact",
                "include_match_traces": True,
            }
        )
    )
    detail_payload = json.loads(handlers["tool_view"].invoke({"name_or_capability_id": "PPT-document"}))

    matched_names = [item["name"] for item in search_payload["matches"]]
    assert matched_names[0] == "PPT-document"
    assert set(matched_names) == {"PPT-document", "deck-outline", "slide-assets", "web-document"}
    assert len(bounded_payload["matches"]) == 4
    assert bounded_payload["returned_count"] == 4
    assert bounded_payload["total_matches"] == 4
    assert "rules" not in search_payload
    assert "tool_kind" not in search_payload["matches"][0]
    assert "tool_contract" not in search_payload["matches"][0]
    assert "tool_kind" not in catalog_payload[0]
    assert [item["name"] for item in provenance_catalog_payload] == ["PPT-document"]
    assert [item["name"] for item in traced_catalog_payload["items"]] == ["PPT-document"]
    assert traced_catalog_payload["match_traces"]["PPT-document"]["matched_fields"] == ["provenance"]
    assert detail_payload["name"] == "PPT-document"
    assert "tool_contract" not in detail_payload


def test_capability_search_payload_explains_provenance_match(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-2", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=False),
        request_context=None,
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}
    payload = handlers["capability_search"].invoke({"query": "github-pack"})

    search_payload = json.loads(payload)
    assert search_payload["matches"][0]["name"] == "ext_search"
    assert "provenance" in search_payload["match_traces"]["ext_search"]["matched_fields"]
    assert set(search_payload["match_traces"]["ext_search"]["query_terms"]) == {"github", "pack"}


def test_tool_catalog_and_tool_view_expose_runtime_capability_metadata(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-3", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=True),
        request_context=None,
    )

    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}
    catalog_payload = json.loads(handlers["tool_catalog"].invoke({}))
    ext_search = next(item for item in catalog_payload if item["name"] == "ext_search")
    detail_payload = json.loads(handlers["tool_view"].invoke({"name_or_capability_id": "ext_search"}))

    assert ext_search["visibility"] == "materialized"
    assert ext_search["deferred"] is True
    assert ext_search["source_kind"] == "mcp"
    assert detail_payload["capability_id"] == "mcp:github:ext_search"
    assert detail_payload["capability_group"] == "research"


def test_toolset_catalog_exposes_registry_derived_logical_groups(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-toolsets", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=True),
    )

    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}
    catalog_payload = json.loads(handlers["toolset_catalog"].invoke({"query": "coding"}))
    coding_payload = json.loads(handlers["toolset_view"].invoke({"name": "coding"}))
    file_payload = json.loads(handlers["toolset_view"].invoke({"name": "file"}))
    browser_payload = json.loads(handlers["toolset_view"].invoke({"name": "browser"}))
    workspace_payload = json.loads(handlers["toolset_view"].invoke({"name": "google-workspace"}))
    media_payload = json.loads(handlers["toolset_view"].invoke({"name": "media"}))
    default_payload = json.loads(handlers["toolset_view"].invoke({"name": "anvil-default"}))

    assert [item["name"] for item in catalog_payload] == ["coding"]
    assert "code_map" in coding_payload["visible_tools"]
    assert "code_focus" in coding_payload["visible_tools"]
    assert "code_symbols" in coding_payload["visible_tools"]
    assert "code_symbol_search" in coding_payload["visible_tools"]
    assert "code_references" in coding_payload["visible_tools"]
    assert "code_definition" in coding_payload["visible_tools"]
    assert "code_file_summary" in coding_payload["visible_tools"]
    assert "code_security_scan" in coding_payload["visible_tools"]
    assert "code_doc_graph" in coding_payload["visible_tools"]
    assert "search_files" in file_payload["visible_tools"]
    assert "glob_files" in file_payload["visible_tools"]
    assert "grep_files" in file_payload["visible_tools"]
    assert "read_file" in file_payload["visible_tools"]
    assert "browser_navigate" in browser_payload["visible_tools"]
    assert "browser_screenshot" in browser_payload["visible_tools"]
    assert "presentation_browser_snapshot_report" not in browser_payload["visible_tools"]
    assert "presentation_browser_diff_report" not in browser_payload["visible_tools"]
    assert "gmail_search" in workspace_payload["visible_tools"]
    assert "calendar_create_event" in workspace_payload["visible_tools"]
    assert "text_to_speech" in media_payload["visible_tools"]
    assert "speech_to_text" in media_payload["visible_tools"]
    assert "image_generate" not in media_payload["visible_tools"]
    assert "browser_navigate" in default_payload["visible_tools"]
    assert "presentation_browser_snapshot_report" not in default_payload["visible_tools"]
    assert "presentation_browser_diff_report" not in default_payload["visible_tools"]
    assert "gmail_search" in default_payload["visible_tools"]
    assert "web_search" in default_payload["visible_tools"]
    assert "web_crawl" in default_payload["visible_tools"]
    assert "text_to_speech" in default_payload["visible_tools"]
    assert "ext_search" in default_payload["deferred_tools"]


def test_tool_catalog_query_matches_provenance_resources_and_prompts(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-4", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=False),
        request_context=None,
    )

    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}
    provenance_payload = json.loads(handlers["tool_catalog"].invoke({"query": "github-pack"}))
    traced_payload = json.loads(handlers["tool_catalog"].invoke({"query": "github-pack", "include_match_traces": True}))
    resource_payload = json.loads(handlers["tool_catalog"].invoke({"query": "research-index"}))
    prompt_payload = json.loads(handlers["tool_catalog"].invoke({"query": "research-template"}))

    assert [item["name"] for item in provenance_payload] == ["ext_search"]
    assert [item["name"] for item in traced_payload["items"]] == ["ext_search"]
    assert traced_payload["match_traces"]["ext_search"]["matched_fields"] == ["provenance"]
    assert [item["name"] for item in resource_payload] == ["ext_search"]
    assert [item["name"] for item in prompt_payload] == ["ext_search"]


def test_tool_catalog_names_only_returns_compact_searchable_index(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-catalog-index", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=False),
        request_context=None,
    )

    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}
    payload = json.loads(
        handlers["tool_catalog"].invoke(
            {
                "query": "research github",
                "names_only": True,
                "include_match_traces": True,
            }
        )
    )

    assert [item["name"] for item in payload["items"]] == ["ext_search"]
    assert payload["total"] == 1
    assert payload["match_traces"]["ext_search"]["matched_fields"] == ["source"]
    assert set(payload["items"][0]) == {
        "capability_id",
        "name",
        "display_name",
        "summary",
        "source_kind",
        "source_id",
        "capability_group",
        "visibility",
        "deferred",
    }
    assert "tool_contract" not in payload["items"][0]
    assert "resources" not in payload["items"][0]
    assert "prompts" not in payload["items"][0]


def test_tool_catalog_symbol_only_query_does_not_match_everything(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-catalog-symbols", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=False),
        request_context=None,
    )

    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}
    payload = json.loads(handlers["tool_catalog"].invoke({"query": ":", "names_only": True}))

    assert payload["items"] == []
    assert payload["total"] == 0


def test_skill_management_is_model_visible_curator_tool(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-skills-governance", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=False),
        request_context=None,
    )

    visible_names = {entry.name for entry in result.bundle.visible_tools}
    catalog_names = {entry.name for entry in result.registry.catalog_entries(result.bundle)}

    assert {
        "skills_list",
        "skill_view",
        "skill_content",
        "skill_files",
        "skill_read_file",
    }.issubset(visible_names)
    assert "skill_manage" in visible_names
    assert "skill_manage" in catalog_names
    visible_by_name = {entry.name: entry for entry in result.bundle.visible_tools}
    catalog_by_name = {entry.name: entry for entry in result.registry.catalog_entries(result.bundle)}
    assert catalog_by_name["skill_manage"].risk_category == "skill_curator"
    assert catalog_by_name["skill_manage"].approval is not None
    assert catalog_by_name["skill_manage"].approval.mode == "runtime"
    actions = visible_by_name["skill_manage"].input_schema["properties"]["action"]["enum"]
    assert "quality_plan" in actions
    assert "review_apply" in actions
    assert "merge_plan" in actions
    assert "merge_apply" in actions
    assert "feedback" in actions
    assert "maintenance" in actions
    assert "learn_procedure" in actions
    assert "procedures" in actions
    assert "promote_procedure" in actions
    assert "reject_procedure" in actions
    assert "restore_procedure" in actions
    assert "outcome" in visible_by_name["skill_manage"].input_schema["properties"]
    assert "feedback_source" in visible_by_name["skill_manage"].input_schema["properties"]
    assert "confidence" in visible_by_name["skill_manage"].input_schema["properties"]
    assert "trigger" in visible_by_name["skill_manage"].input_schema["properties"]
    assert "steps" in visible_by_name["skill_manage"].input_schema["properties"]
    assert "procedure_id" in visible_by_name["skill_manage"].input_schema["properties"]


def test_skill_operator_tools_accept_prompt_style_prefixed_skill_ids(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-skills-prefixed", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=False, subagents=False),
        request_context=None,
    )

    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    content = json.loads(handlers["skill_content"].invoke({"skill_id": "$demo-skill"}))
    files = json.loads(handlers["skill_files"].invoke({"skill_id": "@demo-skill"}))
    view = json.loads(handlers["skill_view"].invoke({"skill_id": "`$demo-skill`"}))

    assert content["skill_id"] == "demo-skill"
    assert files["skill_id"] == "demo-skill"
    assert view["skill_id"] == "demo-skill"


def test_skill_operator_tools_return_model_safe_relative_skill_paths(contract_tmp_path) -> None:
    config_result = make_config(contract_tmp_path)
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-skills-safe-paths", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(skills=True, extensions=False, subagents=False),
        request_context=None,
    )

    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    content = json.loads(handlers["skill_content"].invoke({"skill_id": "demo-skill"}))
    files = json.loads(handlers["skill_files"].invoke({"skill_id": "demo-skill"}))
    read = json.loads(handlers["skill_read_file"].invoke({"skill_id": "demo-skill", "relative_path": "SKILL.md"}))

    assert content["path"] == "SKILL.md"
    assert content["source_root"] == "skill://demo-skill"
    assert content["read_tool"] == "skill_read_file"
    assert files["path"] == "."
    assert files["source_root"] == "skill://demo-skill"
    assert files["read_tool"] == "skill_read_file"
    assert read["path"] == "SKILL.md"
    assert read["source_root"] == "skill://demo-skill"
    assert all("/app/.anvil" not in json.dumps(payload) for payload in (content, files, read))
