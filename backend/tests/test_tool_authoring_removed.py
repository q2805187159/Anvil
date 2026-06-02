from __future__ import annotations

import json

from fastapi.testclient import TestClient

from anvil.agents.features import RuntimeFeatureSet
from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService
from anvil.extensions import ExtensionsService
from anvil.runtime.tool_registry import CapabilityAssemblyService
from anvil.sandbox import PathService, create_sandbox_provider
from anvil.skills import SkillsService
from anvil.subagents import SubagentService


def _config_with_deferred_extension() -> object:
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
                    "extensions": {
                        "mcp_servers": {
                            "github": {
                                "enabled": True,
                                "transport_kind": "stdio",
                                "connection_config": {
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


def test_runtime_restores_pre_split_tool_catalog_without_authoring_surface(contract_tmp_path) -> None:
    config_result = _config_with_deferred_extension()
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-tool-restore", path_service=path_service)
    service = CapabilityAssemblyService(
        skills_service=SkillsService(),
        extensions_service=ExtensionsService(),
        subagent_service=SubagentService(),
    )

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=RuntimeFeatureSet(extensions=True),
        request_context=None,
    )

    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}
    assert "tool_authoring" not in handlers
    assert {"web_search", "web_fetch", "web_extract", "web_crawl", "image_search", "js_repl"}.issubset(handlers)

    search_payload = json.loads(handlers["capability_search"].invoke({"query": "ext", "max_results": 10}))
    assert "rules" not in search_payload
    assert search_payload["matches"][0]["name"] == "ext_search"
    assert "tool_kind" not in search_payload["matches"][0]
    assert "tool_contract" not in search_payload["matches"][0]

    catalog_payload = json.loads(handlers["tool_catalog"].invoke({"query": "ext"}))
    ext_item = next(item for item in catalog_payload if item["name"] == "ext_search")
    assert "tool_kind" not in ext_item
    assert "tool_contract" not in ext_item


def test_gateway_removes_tool_authoring_api_and_split_catalog_fields(gateway_app_factory) -> None:
    app = gateway_app_factory(config_layers=[_config_with_deferred_extension().layers[0]])
    with TestClient(app) as client:
        assert client.get("/tools/authoring/rules").status_code == 404
        assert client.post("/tools/authoring/validate", json={"proposal": {}}).status_code == 404
        assert client.get("/threads/thread-tool-restore/tool-authoring/drafts").status_code == 404

        catalog = client.get("/tools/catalog")
        assert catalog.status_code == 200
        item = next(entry for entry in catalog.json() if entry["name"] == "tool_catalog")
        assert "tool_kind" not in item
        assert "tool_contract" not in item
