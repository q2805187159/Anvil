from __future__ import annotations

from anvil.agents.lead_agent.types import LeadAgentContext
from anvil.agents.middlewares.jit_context.cache import ContextCache
from anvil.agents.middlewares.jit_context.contracts import ContextRequest, ContextType, JITContextConfig
from anvil.agents.middlewares.jit_context.loader import ContextLoader
from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService, ContextFilesConfig
from anvil.memory.contracts import MemoryInjectionView
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
    injection = MemoryInjectionView(
        namespace="global/default",
        summary="Reusable deck finding.",
        facts=("Reusable finding.",),
        evidence=("Captured from a previous deck turn.",),
        confidence=0.82,
    )

    class Recall:
        snapshot_fingerprint = "snapshot-jit-memory"

        def __init__(self) -> None:
            self.injection = injection

        def render_turn_block(self) -> str:
            return injection.render_fenced()

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
    assert "<memory_context>" not in memory_response.content
    assert "<memory_recall>" not in memory_response.content
    assert '"block_type": "semantic_fact"' in memory_response.content
    assert memory_response.metadata["injection_mode"] == "context_v2"
    assert memory_response.metadata["context_v2_block_count"] == 1
    assert memory_response.metadata["snapshot_id"] == "snapshot-jit-memory"
    assert context.context_v2_memory_blocks
    assert context.context_v2_memory_blocks[0]["source"]["kind"] == "memory"
    assert context.memory_injection_diagnostics["source"] == "jit_context_loader"
    assert context.memory_injection_diagnostics["injection_mode"] == "context_v2"
    assert conversation_response is not None
    assert "Generate a deck." in conversation_response.content
    assert memory_manager.search_calls == []


def test_jit_context_loader_conversation_ignores_legacy_memory_context(contract_tmp_path) -> None:
    context = _context(contract_tmp_path, request_context="Generate a deck.")
    context.memory_context = "<memory_context>\nLEGACY_JIT_MEMORY_SENTINEL\n</memory_context>"
    context.todo_context = "- [PENDING] Verify JIT memory projection convergence."

    loader = ContextLoader()

    current_response = loader.load(ContextRequest(context_type=ContextType.CONVERSATION, identifier="current"), context)
    memory_alias_response = loader.load(ContextRequest(context_type=ContextType.CONVERSATION, identifier="memory"), context)

    assert current_response is not None
    assert "Generate a deck." in current_response.content
    assert "Verify JIT memory projection convergence." in current_response.content
    assert "LEGACY_JIT_MEMORY_SENTINEL" not in current_response.content
    assert "<memory_context>" not in current_response.content
    assert "[memory_context]" not in current_response.content
    assert memory_alias_response is None


def test_jit_context_loader_does_not_cache_memory_recall_side_effects(contract_tmp_path) -> None:
    injection = MemoryInjectionView(
        namespace="global/default",
        summary="Cache-sensitive finding.",
        facts=("Memory recall must hydrate each runtime context.",),
        evidence=("Captured from JIT cache regression.",),
        confidence=0.84,
    )

    class MemoryManager:
        def __init__(self) -> None:
            self.prefetch_calls = 0

        def prefetch_recall(self, *, thread_id: str, query: str):
            self.prefetch_calls += 1
            return type(
                "Recall",
                (),
                {
                    "snapshot_fingerprint": f"snapshot-jit-memory-{self.prefetch_calls}",
                    "injection": injection,
                    "render_turn_block": injection.render_fenced,
                },
            )()

    memory_manager = MemoryManager()
    cache = ContextCache(JITContextConfig(cache_enabled=True))
    loader = ContextLoader(cache=cache)
    context = _context(contract_tmp_path, memory_manager=memory_manager)

    first = loader.load(ContextRequest(context_type=ContextType.MEMORY, identifier="deck"), context)
    context.context_v2_memory_blocks = []
    context.memory_injection_diagnostics = {}
    second = loader.load(ContextRequest(context_type=ContextType.MEMORY, identifier="deck"), context)

    assert first is not None
    assert second is not None
    assert first.cached is False
    assert second.cached is False
    assert memory_manager.prefetch_calls == 2
    assert context.context_v2_memory_blocks
    assert context.memory_injection_diagnostics["snapshot_id"] == "snapshot-jit-memory-2"
    assert cache.cache == {}


def test_jit_context_loader_wraps_unstructured_memory_recall_as_context_v2_block(contract_tmp_path) -> None:
    class Recall:
        snapshot_fingerprint = "snapshot-unstructured"

        def render_turn_block(self) -> str:
            return "<memory_recall>\nUnstructured recall still avoids prompt sections.\n</memory_recall>"

    class MemoryManager:
        def prefetch_recall(self, *, thread_id: str, query: str):
            return Recall()

    context = _context(contract_tmp_path, memory_manager=MemoryManager())
    loader = ContextLoader()

    response = loader.load(ContextRequest(context_type=ContextType.MEMORY, identifier="deck"), context)

    assert response is not None
    assert "<memory_recall>" not in response.content
    assert "[memory_recall]" not in response.content
    assert '"block_type": "retrieved_memory"' in response.content
    assert "Unstructured recall still avoids prompt sections." in response.content
    assert response.metadata["injection_mode"] == "context_v2_unstructured"
    assert context.context_v2_memory_blocks[0]["block_type"] == "retrieved_memory"
    assert context.memory_injection_diagnostics["source"] == "jit_context_loader"
    assert context.memory_injection_diagnostics["injection_mode"] == "context_v2_unstructured"


def test_jit_context_loader_strips_attributed_unstructured_memory_recall_fences(contract_tmp_path) -> None:
    class Recall:
        snapshot_fingerprint = "snapshot-attributed-unstructured"

        def render_turn_block(self) -> str:
            return (
                '<memory_recall source="legacy" priority="high">\n'
                "Attributed JIT recall should become a clean block.\n"
                "</memory_recall>"
            )

    class MemoryManager:
        def prefetch_recall(self, *, thread_id: str, query: str):
            return Recall()

    context = _context(contract_tmp_path, memory_manager=MemoryManager())
    loader = ContextLoader()

    response = loader.load(ContextRequest(context_type=ContextType.MEMORY, identifier="deck"), context)

    assert response is not None
    assert "Attributed JIT recall should become a clean block." in response.content
    assert "<memory_recall" not in response.content
    assert "[memory_recall" not in response.content
    assert "source=\"legacy\"" not in response.content
    assert context.context_v2_memory_blocks
    assert "<memory_recall" not in context.context_v2_memory_blocks[0]["content"]
    assert "[memory_recall" not in context.context_v2_memory_blocks[0]["content"]
    assert "source=\"legacy\"" not in context.context_v2_memory_blocks[0]["content"]


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
