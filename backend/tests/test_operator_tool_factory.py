from __future__ import annotations

from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService
from anvil.extensions import ExtensionsService
from anvil.sandbox import PathService
from anvil.runtime.tool_registry.operator_factory import OperatorToolFactory
from anvil.skills import SkillsService


def _config_result():
    return ConfigService().resolve(
        [
            ConfigLayer(
                name="test",
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
                },
            )
        ]
    )


def test_static_schema_operator_tools_do_not_rebuild_schema_with_tool_decorator(monkeypatch, contract_tmp_path) -> None:
    import langchain_core.tools as langchain_tools

    def fail_tool_decorator(*args, **kwargs):  # pragma: no cover - failure path proves accidental use
        raise AssertionError("static-schema operator tools should use explicit StructuredTool handlers")

    monkeypatch.setattr(langchain_tools, "tool", fail_tool_decorator)
    factory = OperatorToolFactory(skills_service=SkillsService(), extensions_service=ExtensionsService())
    config_result = _config_result()
    path_service = PathService(contract_tmp_path / "threads")

    entries = factory.build_tools(
        registry=None,
        bundle_ref={"bundle": None},
        promotion_state=set(),
        config_result=config_result,
        skills_result=None,
        thread_id="thread-static-operator-tools",
        path_service=path_service,
        resolved_route=None,
    )

    names = {entry.name for entry in entries}
    assert {
        "capability_search",
        "tool_catalog",
        "tool_view",
        "toolset_catalog",
        "toolset_view",
        "skills_list",
        "skill_view",
        "skill_content",
        "skill_files",
        "skill_read_file",
        "skill_manage",
        "mcp_manage",
        "mcp_list_resources",
        "mcp_read_resource",
        "mcp_list_prompts",
        "mcp_get_prompt",
        "web_search",
        "browser_navigate",
        "gmail_search",
    }.issubset(names)
    for entry in entries:
        assert entry.handler.name == entry.name
        assert entry.handler.description
        assert entry.handler.args_schema == entry.input_schema
