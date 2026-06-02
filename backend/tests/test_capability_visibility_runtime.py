from __future__ import annotations

from anvil.agents import make_lead_agent
from anvil.agents.features import RuntimeFeatureSet
from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.store import StoreBackend, create_store
from anvil.sandbox import PathService
from fake_models import BindableFakeMessagesListChatModel
from langchain_core.messages import AIMessage


def build_layers(contract_tmp_path):
    return [
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
                    "external_dirs": [str(contract_tmp_path / "skills")],
                },
                "extensions": {
                    "mcp_servers": {
                        "github": {
                            "enabled": True,
                            "transport_kind": "stdio",
                            "refresh_policy": "dynamic",
                            "connection_config": {
                                "inline_tools": [
                                    {
                                        "name": "ext_search",
                                        "display_name": "External Search",
                                        "capability_group": "research",
                                        "deferred": True,
                                    }
                                ]
                            },
                        }
                    }
                },
                "subagents": {"enabled": True},
            },
        )
    ]


def write_skill(root, slug: str, title: str, body: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


def test_promoted_capabilities_are_request_local_and_do_not_leak_across_turns(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "demo-skill", "Demo Skill", "Use the demo workflow")
    config_result = ConfigService().resolve(build_layers(contract_tmp_path))
    path_service = PathService(contract_tmp_path / "threads")
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)

    with_mention = make_lead_agent(
        config_result=config_result,
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        thread_id="thread-a",
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=True, capability_mentions=True),
        request_context="@ext_search please inspect remote capability",
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="noop")]),
    )
    same_thread_next_turn = make_lead_agent(
        config_result=config_result,
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        thread_id="thread-a",
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=True, capability_mentions=True),
        request_context=None,
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="noop")]),
    )
    other_thread = make_lead_agent(
        config_result=config_result,
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        thread_id="thread-b",
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=True, capability_mentions=True),
        request_context=None,
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="noop")]),
    )

    with_mention_visible = {entry.name for entry in with_mention.context.capability_bundle.visible_tools}
    next_turn_visible = {entry.name for entry in same_thread_next_turn.context.capability_bundle.visible_tools}
    other_thread_visible = {entry.name for entry in other_thread.context.capability_bundle.visible_tools}

    assert "ext_search" in with_mention_visible
    assert "ext_search" not in next_turn_visible
    assert "ext_search" not in other_thread_visible
    assert with_mention.context.capability_bundle.fingerprint != same_thread_next_turn.context.capability_bundle.fingerprint


def test_capability_bundle_fingerprint_changes_when_deferred_tool_is_promoted(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "demo-skill", "Demo Skill", "Use the demo workflow")
    config_result = ConfigService().resolve(build_layers(contract_tmp_path))
    path_service = PathService(contract_tmp_path / "threads")
    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)

    deferred_runtime = make_lead_agent(
        config_result=config_result,
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        thread_id="thread-c",
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=True, capability_mentions=True),
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="noop")]),
    )
    promoted_runtime = make_lead_agent(
        config_result=config_result,
        path_service=path_service,
        checkpointer=checkpointer,
        store=store,
        thread_id="thread-c",
        feature_set=RuntimeFeatureSet(skills=True, extensions=True, subagents=True, capability_mentions=True),
        promoted_capabilities=("ext_search",),
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="noop")]),
    )

    assert deferred_runtime.context.capability_bundle.fingerprint != promoted_runtime.context.capability_bundle.fingerprint
    assert "ext_search" not in {entry.name for entry in deferred_runtime.context.capability_bundle.visible_tools}
    assert "ext_search" in {entry.name for entry in promoted_runtime.context.capability_bundle.visible_tools}
    assert promoted_runtime.assembly_snapshot.capabilities.assembly_diagnostics["visible_tool_count"] >= 1
    assert promoted_runtime.assembly_snapshot.capabilities.assembly_diagnostics["active_promotion_count"] == 1
    diff = deferred_runtime.assembly_snapshot.diff(promoted_runtime.assembly_snapshot)
    assert "capabilities.visible_tool_names" in diff.changed_paths
    assert "capabilities.deferred_tool_names" in diff.changed_paths
    assert diff.added["capabilities.visible_tool_names"] == ("ext_search",)
    assert diff.removed["capabilities.deferred_tool_names"] == ("ext_search",)
