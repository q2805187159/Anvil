from __future__ import annotations

from anvil.agents.lead_agent.types import LeadAgentContext
from anvil.agents.middlewares.jit_context.cache import ContextCache
from anvil.agents.middlewares.jit_context.contracts import ContextRequest, ContextType, JITContextConfig
from anvil.agents.middlewares.jit_context.loader import ContextLoader
from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService, ContextFilesConfig
from anvil.runtime.tool_registry.contracts import CapabilityBundle, ToolRegistryEntry, ToolSourceKind
from anvil.sandbox import PathService
from anvil.skills import SkillsService


def _config_result(contract_tmp_path, *, include_readme: bool = False):
    skills_root = contract_tmp_path / "skills"
    skill_dir = skills_root / "ppt-generation"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# PPT Generation\n\nCreate polished decks.\n", encoding="utf-8")
    return ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "skills_config": {
                        "enabled": True,
                        "external_dirs": [str(skills_root)],
                        "enabled_ids": ["ppt-generation"],
                    },
                    "context_files": ContextFilesConfig(include_readme=include_readme, max_chars=4000).model_dump(),
                },
            )
        ]
    )


def _tool_entry() -> ToolRegistryEntry:
    return ToolRegistryEntry(
        name="code_map",
        display_name="Code Map",
        source_kind=ToolSourceKind.BUILTIN,
        source_id="builtin",
        capability_group="coding",
        summary="Build a bounded project code map.",
        schema={
            "type": "object",
            "properties": {
                "project_path": {"type": "string"},
                "max_files": {"type": "integer"},
            },
        },
    )


def _context(contract_tmp_path, *, memory_manager=None, request_context: str | None = None, include_readme: bool = False):
    thread_id = "thread-jit"
    path_service = PathService(contract_tmp_path / "threads")
    path_service.bootstrap_thread_paths(thread_id)
    config_result = _config_result(contract_tmp_path, include_readme=include_readme)
    bundle = CapabilityBundle(
        fingerprint="bundle",
        visible_tools=(_tool_entry(),),
        deferred_tools=(),
    )
    return LeadAgentContext(
        thread_id=thread_id,
        path_service=path_service,
        sandbox_provider=None,
        capability_bundle=bundle,
        config_result=config_result,
        skills_service=SkillsService(),
        memory_manager=memory_manager,
        request_context=request_context,
        summary_context="Thread summary from runtime.",
    )


def test_jit_context_loader_reads_real_file_project_skill_and_tool_context(contract_tmp_path) -> None:
    context = _context(contract_tmp_path, include_readme=True)
    workspace = context.path_service.thread_workspace_dir(context.thread_id)
    (workspace / "README.md").write_text("Project overview for JIT loader.\n", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("print('hello from jit')\n", encoding="utf-8")

    loader = ContextLoader()

    file_response = loader.load(
        ContextRequest(context_type=ContextType.FILE, identifier="src/main.py", metadata={"max_chars": 1000}),
        context,
    )
    project_response = loader.load(
        ContextRequest(context_type=ContextType.PROJECT, identifier="README.md", metadata={"max_chars": 1000}),
        context,
    )
    skill_response = loader.load(
        ContextRequest(context_type=ContextType.SKILL, identifier="/app/.anvil/skills/ppt-generation/SKILL.md"),
        context,
    )
    tool_response = loader.load(ContextRequest(context_type=ContextType.TOOL, identifier="code_map"), context)

    assert file_response is not None
    assert "hello from jit" in file_response.content
    assert project_response is not None
    assert "Project overview for JIT loader." in project_response.content
    assert skill_response is not None
    assert "Create polished decks." in skill_response.content
    assert tool_response is not None
    assert '"project_path"' in tool_response.content
    for response in (file_response, project_response, skill_response, tool_response):
        assert "not yet implemented" not in response.content


def test_jit_context_loader_uses_memory_recall_and_runtime_conversation_context(contract_tmp_path) -> None:
    class Recall:
        def render_turn_block(self) -> str:
            return "<memory_recall>\nReusable finding.\n</memory_recall>"

    class MemoryManager:
        def __init__(self) -> None:
            self.search_calls = []

        def prefetch_recall(self, *, thread_id: str, query: str):
            return Recall()

        def search_sessions(self, **kwargs):
            self.search_calls.append(kwargs)
            return {"matches": [{"thread_id": kwargs["current_thread_id"], "title": "Prior turn"}]}

    memory_manager = MemoryManager()
    context = _context(contract_tmp_path, memory_manager=memory_manager, request_context="Generate a deck.")
    loader = ContextLoader()

    memory_response = loader.load(ContextRequest(context_type=ContextType.MEMORY, identifier="deck"), context)
    conversation_response = loader.load(ContextRequest(context_type=ContextType.CONVERSATION, identifier="request"), context)

    assert memory_response is not None
    assert "Reusable finding." in memory_response.content
    assert "[memory_recall]" in memory_response.content
    assert conversation_response is not None
    assert "Generate a deck." in conversation_response.content
    assert memory_manager.search_calls == []


def test_jit_context_loader_fail_opens_missing_sources(contract_tmp_path) -> None:
    context = _context(contract_tmp_path)
    loader = ContextLoader()

    missing_file = loader.load(
        ContextRequest(context_type=ContextType.FILE, identifier="missing.py", required=False),
        context,
    )
    missing_tool = loader.load(
        ContextRequest(context_type=ContextType.TOOL, identifier="unknown_tool", required=False),
        context,
    )
    missing_skill = loader.load(
        ContextRequest(context_type=ContextType.SKILL, identifier="$unknown-skill", required=False),
        context,
    )

    assert missing_file is None
    assert missing_tool is None
    assert missing_skill is None


def test_jit_context_loader_cache_key_includes_metadata_and_thread(contract_tmp_path) -> None:
    context = _context(contract_tmp_path)
    workspace = context.path_service.thread_workspace_dir(context.thread_id)
    (workspace / "notes.txt").write_text("A" * 900 + "\nfull tail marker\n", encoding="utf-8")
    cache = ContextCache(JITContextConfig(cache_enabled=True))
    loader = ContextLoader(cache=cache)

    short_response = loader.load(
        ContextRequest(context_type=ContextType.FILE, identifier="notes.txt", metadata={"max_chars": 220}),
        context,
    )
    long_response = loader.load(
        ContextRequest(context_type=ContextType.FILE, identifier="notes.txt", metadata={"max_chars": 1200}),
        context,
    )
    repeated_short_response = loader.load(
        ContextRequest(context_type=ContextType.FILE, identifier="notes.txt", metadata={"max_chars": 220}),
        context,
    )

    assert short_response is not None
    assert long_response is not None
    assert repeated_short_response is not None
    assert "full tail marker" not in short_response.content
    assert "full tail marker" in long_response.content
    assert repeated_short_response.cached is True
    assert len(cache.cache) == 2
