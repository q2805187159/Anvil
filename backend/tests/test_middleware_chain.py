from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pathlib import Path
from types import SimpleNamespace
import pytest

import anvil.agents.middlewares.memory_prefetch_middleware as memory_prefetch_module
from anvil.agents.factory import build_middleware_chain
from anvil.agents.features import Next, Prev, RuntimeFeatureSet, resolve_feature_set
from anvil.agents.lead_agent.prompt import PromptSection, PromptSnapshot, PromptSnapshotKey
from anvil.agents.lead_agent.types import LeadAgentState
from anvil.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware
from anvil.agents.middlewares.view_image_middleware import ViewImageMiddleware
from anvil.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from anvil.agents.middlewares.llm_error_handling_middleware import LLMExecutionError
from anvil.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from anvil.agents.middlewares.memory_capture_middleware import MemoryCaptureMiddleware
from anvil.agents.middlewares.memory_prefetch_middleware import MemoryPrefetchMiddleware
from anvil.config import ConfigLayer, ConfigLayerKind, EffectiveConfig, HCMSRuntimeConfig
from anvil.config.models import LoopDetectionConfig, SummarizationConfig
from anvil.memory import DebouncedMemoryQueue, FileMemoryStore, HeuristicMemoryUpdater, MemoryService
from anvil.memory.contracts import MemoryInjectionView
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.context_envelope import ContextAssembler
from anvil.runtime.runs import RunEngine, RunRequest
from anvil.runtime.store import StoreBackend, create_store
from anvil.sandbox import PathService
from fake_models import BindableFakeMessagesListChatModel


@pytest.mark.parametrize(
    "message",
    [
        "Concurrency limit exceeded for account, please retry later",
        "Too many requests; retry later",
        "Provider is overloaded, try again later",
        "server busy",
    ],
)
def test_llm_error_handling_treats_provider_capacity_errors_as_transient(message: str) -> None:
    middleware = LLMErrorHandlingMiddleware()

    assert middleware._categorize_error(RuntimeError(message)) == "transient"


@pytest.mark.parametrize(
    "message",
    [
        "unauthorized api key",
        "model not found",
        "permission denied for this model",
    ],
)
def test_llm_error_handling_keeps_auth_and_model_errors_fatal(message: str) -> None:
    middleware = LLMErrorHandlingMiddleware()

    assert middleware._categorize_error(RuntimeError(message)) == "fatal"


def _llm_request(max_attempts: int = 3) -> SimpleNamespace:
    retry = SimpleNamespace(
        max_attempts=max_attempts,
        initial_delay=0.0,
        backoff_multiplier=1.0,
        max_delay=0.0,
    )
    return SimpleNamespace(
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                config_result=SimpleNamespace(
                    effective_config=SimpleNamespace(llm=SimpleNamespace(retry=retry, fallback_models=["backup"]))
                ),
                interrupted_stream=False,
                emergency_summarize_triggered=False,
            )
        )
    )


def test_llm_error_handling_retries_transient_errors_on_same_model() -> None:
    middleware = LLMErrorHandlingMiddleware()
    request = _llm_request(max_attempts=2)
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("primary provider returned 503 temporarily unavailable")
        return "ok"

    assert middleware.wrap_model_call(request, handler) == "ok"
    assert calls == 2


def test_llm_error_handling_does_not_retry_fatal_key_errors_or_fallback() -> None:
    middleware = LLMErrorHandlingMiddleware()
    request = _llm_request(max_attempts=3)
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        raise RuntimeError("Error code: 401 - {'code': 'INVALID_API_KEY', 'message': 'Invalid API key'}")

    with pytest.raises(LLMExecutionError) as exc_info:
        middleware.wrap_model_call(request, handler)

    assert calls == 1
    assert exc_info.value.category == "fatal"
    assert "fallback" not in str(exc_info.value).lower()


def test_middleware_chain_uses_canonical_order() -> None:
    chain = build_middleware_chain(RuntimeFeatureSet())
    assert [middleware.name for middleware in chain] == [
        "ThreadDataMiddleware",
        "UploadsMiddleware",
        "SandboxMiddleware",
        "DanglingToolCallMiddleware",
        "LLMErrorHandlingMiddleware",
        "GuardrailMiddleware",
        "SandboxAuditMiddleware",
        "ToolErrorHandlingMiddleware",
        "ToolOutputBudgetMiddleware",
        "ToolVisibilityMiddleware",
        "DeferredToolFilterMiddleware",
        "LoopDetectionMiddleware",
        "ClarificationMiddleware",
    ]


def test_clarification_middleware_is_last() -> None:
    chain = build_middleware_chain(RuntimeFeatureSet())
    assert chain[-1].name == "ClarificationMiddleware"


def test_feature_gating_can_remove_middlewares_without_reordering_remaining() -> None:
    chain = build_middleware_chain(
        RuntimeFeatureSet(
            uploads=False,
            dangling_tool_calls=False,
            llm_error_handling=False,
            sandbox_audit=False,
            tool_output_budget=False,
            deferred_tool_filter=False,
            loop_detection=False,
            clarification=False,
        )
    )
    assert [middleware.name for middleware in chain] == [
        "ThreadDataMiddleware",
        "SandboxMiddleware",
        "GuardrailMiddleware",
        "ToolErrorHandlingMiddleware",
        "ToolVisibilityMiddleware",
    ]


def test_behavior_middlewares_slot_between_safety_and_clarification() -> None:
    chain = build_middleware_chain(
        RuntimeFeatureSet(
            memory=True,
            memory_prefetch=True,
            summarization=True,
            plan_mode=True,
            title=True,
            token_usage=True,
            view_image=True,
            memory_capture=True,
            subagents=True,
        )
    )
    assert [middleware.name for middleware in chain] == [
        "ThreadDataMiddleware",
        "UploadsMiddleware",
        "SandboxMiddleware",
        "DanglingToolCallMiddleware",
        "LLMErrorHandlingMiddleware",
        "GuardrailMiddleware",
        "SandboxAuditMiddleware",
        "ToolErrorHandlingMiddleware",
        "ToolOutputBudgetMiddleware",
        "TodoMiddleware",
        "TokenUsageMiddleware",
        "TitleMiddleware",
        "MemoryPrefetchMiddleware",
        "MemoryCaptureMiddleware",
        "ViewImageMiddleware",
        "ToolVisibilityMiddleware",
        "DeferredToolFilterMiddleware",
        "SubagentLimitMiddleware",
        "LoopDetectionMiddleware",
        "ClarificationMiddleware",
    ]


def test_dangling_tool_call_middleware_patches_missing_results_in_place() -> None:
    middleware = DanglingToolCallMiddleware()
    first = AIMessage(
        content="",
        tool_calls=[
            {"name": "read_file", "args": {"path": "a.txt"}, "id": "call-read", "type": "tool_call"}
        ],
    )
    second = AIMessage(
        content="done",
        tool_calls=[
            {"name": "write_file", "args": {"path": "b.txt"}, "id": "call-write", "type": "tool_call"}
        ],
    )
    existing = ToolMessage(content="ok", tool_call_id="call-write")
    request = SimpleNamespace(
        messages=[HumanMessage(content="start"), first, second, existing],
        override=lambda **updates: SimpleNamespace(
            messages=updates.get("messages"),
            override=request.override,
        ),
    )

    captured = {}

    def handler(patched_request):
        captured["messages"] = list(patched_request.messages)
        return "ok"

    assert middleware.wrap_model_call(request, handler) == "ok"
    patched = captured["messages"]
    assert patched[1] is first
    assert isinstance(patched[2], ToolMessage)
    assert patched[2].tool_call_id == "call-read"
    assert patched[2].status == "error"
    assert patched[3] is second
    assert patched[4] is existing


def test_dangling_tool_call_middleware_removes_orphan_tool_results() -> None:
    middleware = DanglingToolCallMiddleware()
    orphan = ToolMessage(content="orphan", tool_call_id="call-orphan")
    valid_assistant = AIMessage(
        content="",
        tool_calls=[
            {"name": "read_file", "args": {"path": "a.txt"}, "id": "call-read", "type": "tool_call"}
        ],
    )
    valid_result = ToolMessage(content="ok", tool_call_id="call-read")
    request = SimpleNamespace(
        messages=[HumanMessage(content="start"), orphan, valid_assistant, valid_result],
        override=lambda **updates: SimpleNamespace(
            messages=updates.get("messages"),
            override=request.override,
        ),
    )
    captured = {}

    def handler(patched_request):
        captured["messages"] = list(patched_request.messages)
        return "ok"

    assert middleware.wrap_model_call(request, handler) == "ok"
    patched = captured["messages"]
    assert orphan not in patched
    assert patched == [request.messages[0], valid_assistant, valid_result]


def test_view_image_middleware_injects_multimodal_human_message_after_tool_result() -> None:
    image_data_url = "data:image/png;base64,iVBORw0KGgo="
    state = LeadAgentState(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "view_image",
                        "args": {"path": "/mnt/user-data/uploads/diagram.png"},
                        "id": "call_view_image",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content=[
                    {"type": "text", "text": "<view_image>\npath: /mnt/user-data/uploads/diagram.png"},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": "</view_image>"},
                ],
                tool_call_id="call_view_image",
            ),
        ]
    )

    update = ViewImageMiddleware().before_model(state, runtime=SimpleNamespace(context=SimpleNamespace()))

    assert update is not None
    injected_messages = update["messages"]
    assert len(injected_messages) == 1
    injected = injected_messages[0]
    assert isinstance(injected, HumanMessage)
    assert injected.additional_kwargs["anvil_model_only"] is True
    assert injected.additional_kwargs["visibility"] == "model_only"
    assert isinstance(injected.content, list)
    assert any(
        isinstance(block, dict)
        and block.get("type") == "image_url"
        and block.get("image_url", {}).get("url") == image_data_url
        for block in injected.content
    )
    assert "view_image_attachment" not in repr(injected.content)
    assert "Images returned by view_image" not in repr(injected.content)
    assert update["viewed_images"] == ["/mnt/user-data/uploads/diagram.png"]


def test_run_engine_storage_drops_view_image_model_only_bridge(contract_tmp_path) -> None:
    message = HumanMessage(
        content=[
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        ],
        additional_kwargs={
            "anvil_view_image_injection": True,
            "anvil_model_only": True,
            "visibility": "model_only",
        },
    )

    stored = ContextAssembler(
        path_service=PathService(contract_tmp_path / "threads"),
        thread_id="thread-view-image-storage",
    ).persistent_transcript(
        [HumanMessage(content="visible"), message],
    )

    assert len(stored) == 1
    assert stored[0].content == "visible"


def test_hcms_feature_uses_prefetch_without_capture_when_capture_not_requested() -> None:
    chain = build_middleware_chain(
        RuntimeFeatureSet(
            memory=True,
            memory_prefetch=True,
        )
    )

    assert "MemoryPrefetchMiddleware" in [middleware.name for middleware in chain]
    assert "MemoryCaptureMiddleware" not in [middleware.name for middleware in chain]


def test_hcms_config_enables_native_capture() -> None:
    config = EffectiveConfig(hcms=HCMSRuntimeConfig(enabled=True))
    features = resolve_feature_set(RuntimeFeatureSet(), config)
    chain = build_middleware_chain(features)

    assert features.memory is True
    assert features.memory_prefetch is True
    assert features.memory_capture is True
    assert "MemoryPrefetchMiddleware" in [middleware.name for middleware in chain]
    assert "MemoryCaptureMiddleware" in [middleware.name for middleware in chain]


def test_context_v2_hcms_inserts_prefetch_into_explicit_middleware_chain() -> None:
    config = EffectiveConfig(hcms=HCMSRuntimeConfig(enabled=True))
    features = resolve_feature_set(RuntimeFeatureSet(), config)
    chain = build_middleware_chain(
        features,
        middleware=[LLMErrorHandlingMiddleware()],
        effective_config=config,
    )

    assert [middleware.name for middleware in chain] == [
        "LLMErrorHandlingMiddleware",
        "MemoryPrefetchMiddleware",
    ]


def test_context_v2_hcms_overrides_disabled_memory_prefetch_feature() -> None:
    config = EffectiveConfig(hcms=HCMSRuntimeConfig(enabled=True))
    features = resolve_feature_set(RuntimeFeatureSet(memory_prefetch=False), config)
    chain = build_middleware_chain(features, effective_config=config)

    assert features.memory is True
    assert features.memory_prefetch is False
    assert "MemoryPrefetchMiddleware" in [middleware.name for middleware in chain]


def test_deleted_context_compaction_middleware_is_not_in_default_chain() -> None:
    config = EffectiveConfig()
    features = resolve_feature_set(RuntimeFeatureSet(), config)
    chain = build_middleware_chain(features, effective_config=config)

    names = [middleware.name for middleware in chain]

    assert features.summarization is False
    assert "SummarizationMiddleware" not in names
    assert "CompactionMiddleware" not in names


def test_summarization_config_no_longer_enables_deleted_context_compaction_surface() -> None:
    config = EffectiveConfig(summarization=SummarizationConfig(enabled=True))
    features = resolve_feature_set(RuntimeFeatureSet(), config)
    chain = build_middleware_chain(features, effective_config=config)

    names = [middleware.name for middleware in chain]

    assert features.summarization is False
    assert "SummarizationMiddleware" not in names
    assert "CompactionMiddleware" not in names


def test_legacy_context_compaction_package_is_deleted() -> None:
    import anvil.agents.middlewares as middlewares
    import anvil.agents.middlewares.compaction as compaction_package

    package_path = Path(compaction_package.__path__[0])
    assert not any(path.suffix == ".py" for path in package_path.glob("*.py"))
    assert not hasattr(middlewares, "CompactionMiddleware")
    assert not hasattr(compaction_package, "CompactionService")
    assert not hasattr(compaction_package, "CompactionConfig")


def test_extra_middlewares_can_anchor_before_clarification_without_breaking_tail() -> None:
    from anvil.agents.middlewares import ClarificationMiddleware

    @Prev(ClarificationMiddleware)
    class BeforeClarification:
        @property
        def name(self) -> str:
            return self.__class__.__name__

    chain = build_middleware_chain(
        RuntimeFeatureSet(),
        extra_middlewares=[BeforeClarification()],
    )
    assert [middleware.name for middleware in chain][-2:] == [
        "BeforeClarification",
        "ClarificationMiddleware",
    ]


def test_extra_middlewares_can_anchor_after_named_middleware() -> None:
    from anvil.agents.middlewares import ThreadDataMiddleware

    @Next(ThreadDataMiddleware)
    class AfterThreadData:
        @property
        def name(self) -> str:
            return self.__class__.__name__

    chain = build_middleware_chain(
        RuntimeFeatureSet(),
        extra_middlewares=[AfterThreadData()],
    )
    assert [middleware.name for middleware in chain][:3] == [
        "ThreadDataMiddleware",
        "AfterThreadData",
        "UploadsMiddleware",
    ]


def test_loop_detection_warning_does_not_inject_visible_human_message() -> None:
    middleware = LoopDetectionMiddleware(warn_threshold=2, hard_limit=5)
    runtime = SimpleNamespace(context=SimpleNamespace(thread_id="thread-loop"))
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"path": "/mnt/user-data/workspace/a.txt"},
                "id": "call-read",
                "type": "tool_call",
            }
        ],
    )

    assert middleware.after_model(LeadAgentState(messages=[message]), runtime) is None
    assert middleware.after_model(LeadAgentState(messages=[message]), runtime) is None


def test_loop_detection_hard_limit_marks_run_interrupted_without_user_message() -> None:
    middleware = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3)
    runtime = SimpleNamespace(context=SimpleNamespace(thread_id="thread-loop", run_id="run-loop-a"))
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"path": "/mnt/user-data/workspace/a.txt"},
                "id": "call-read",
                "type": "tool_call",
            }
        ],
    )

    assert middleware.after_model(LeadAgentState(messages=[message]), runtime) is None
    assert middleware.after_model(LeadAgentState(messages=[message]), runtime) is None
    update = middleware.after_model(LeadAgentState(messages=[message]), runtime)

    assert update is not None
    assert update["stream_interrupted"] is True
    assert update["interrupted_stream"] is True
    assert "3 identical tool-call rounds" in update["interrupted_stream_reason"]
    assert len(update["messages"]) == 1
    updated_message = update["messages"][0]
    assert isinstance(updated_message, AIMessage)
    assert updated_message.tool_calls == []
    assert "I stopped a repeated internal tool loop" in str(updated_message.content)


def test_loop_detection_history_is_scoped_per_run() -> None:
    middleware = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3)
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"path": "/mnt/user-data/workspace/a.txt"},
                "id": "call-read",
                "type": "tool_call",
            }
        ],
    )

    first_runtime = SimpleNamespace(context=SimpleNamespace(thread_id="thread-loop", run_id="run-loop-a"))
    second_runtime = SimpleNamespace(context=SimpleNamespace(thread_id="thread-loop", run_id="run-loop-b"))
    assert middleware.after_model(LeadAgentState(messages=[message]), first_runtime) is None
    assert middleware.after_model(LeadAgentState(messages=[message]), first_runtime) is None

    assert middleware.after_model(LeadAgentState(messages=[message]), second_runtime) is None


def test_loop_detection_config_controls_thresholds_and_disablement() -> None:
    config = EffectiveConfig(
        loop_detection=LoopDetectionConfig(
            warn_threshold=7,
            hard_limit=11,
            window_size=13,
            max_tracked_runs=17,
        )
    )
    chain = build_middleware_chain(resolve_feature_set(RuntimeFeatureSet(), config), effective_config=config)
    middleware = next(item for item in chain if isinstance(item, LoopDetectionMiddleware))

    assert middleware.warn_threshold == 7
    assert middleware.hard_limit == 11
    assert middleware.window_size == 13
    assert middleware.max_tracked_runs == 17

    disabled_config = EffectiveConfig(loop_detection=LoopDetectionConfig(enabled=False))
    disabled_chain = build_middleware_chain(
        resolve_feature_set(RuntimeFeatureSet(), disabled_config),
        effective_config=disabled_config,
    )
    assert not any(isinstance(item, LoopDetectionMiddleware) for item in disabled_chain)


def test_loop_detection_config_keeps_legacy_max_identical_turns_compatible() -> None:
    config = LoopDetectionConfig.model_validate({"max_identical_turns": 4})

    assert config.warn_threshold == 2
    assert config.hard_limit == 4
    assert config.window_size >= 4


def test_legacy_memory_prefetch_mode_migrates_recall_to_context_v2_blocks() -> None:
    class FakeMemoryManager:
        config = SimpleNamespace(recall=SimpleNamespace(turn_recall_token_budget=80))

        def prefetch_recall(self, *, thread_id: str, query: str):
            recall = SimpleNamespace(
                thread_id=thread_id,
                query=query,
                snapshot_fingerprint="snapshot-diag",
                summary="summary " * 80,
                memory_matches=(
                    SimpleNamespace(
                        entry_id="entry-1",
                        store_id="project",
                        content="project memory " * 80,
                    ),
                    SimpleNamespace(
                        entry_id="entry-2",
                        store_id="user",
                        content="user memory",
                    ),
                ),
                archive_hits=(
                    SimpleNamespace(
                        archive_id="archive-1",
                        thread_id="source-thread",
                        score=0.9,
                        excerpt="archive memory " * 40,
                    ),
                ),
                engine_notes=("engine note",),
                evidence=(
                    SimpleNamespace(
                        evidence_id="ev-1",
                        source_kind="memory",
                        source_id="entry-1",
                        score=0.8,
                        reason="matched project preference",
                    ),
                    SimpleNamespace(
                        evidence_id="ev-2",
                        source_kind="archive",
                        source_id="archive-1",
                        score=0.7,
                        reason="matched older thread",
                    ),
                ),
            )
            recall.render_turn_block = lambda: f"<memory_recall>\n{recall.summary}\n</memory_recall>"
            return recall

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-memory-diag",
            memory_manager=FakeMemoryManager(),
            memory_service=None,
            memory_context=None,
            memory_context_mode="legacy_prompt_append",
            context_v2_memory_blocks=[],
            memory_injection_diagnostics={},
        )
    )

    update = MemoryPrefetchMiddleware().before_model(
        LeadAgentState(messages=[HumanMessage(content="Use my stored project preference")]),
        runtime,
    )

    assert update is not None
    assert update["memory_snapshot_id"] == "snapshot-diag"
    assert "memory_context" not in update
    blocks = update["context_v2_memory_blocks"]
    assert len(blocks) == 1
    assert blocks[0]["source"]["kind"] == "memory"
    assert blocks[0]["block_type"] == "retrieved_memory"
    assert blocks[0]["content"].startswith("Dynamic memory recall is bounded fact lookup")
    assert "<memory_recall>" not in blocks[0]["content"]
    assert runtime.context.context_v2_memory_blocks == blocks
    assert runtime.context.memory_context is None
    assert runtime.context.memory_injection_diagnostics["source"] == "memory_manager"
    assert runtime.context.memory_injection_diagnostics["status"] == "injected"
    assert runtime.context.memory_injection_diagnostics["memory_match_count"] == 2
    assert runtime.context.memory_injection_diagnostics["archive_hit_count"] == 1
    assert runtime.context.memory_injection_diagnostics["evidence_count"] == 2
    assert runtime.context.memory_injection_diagnostics["engine_note_count"] == 1
    assert runtime.context.memory_injection_diagnostics["token_budget"] == 80
    assert runtime.context.memory_injection_diagnostics["truncated"] is True
    assert runtime.context.memory_injection_diagnostics["injection_mode"] == "context_v2"
    assert runtime.context.memory_injection_diagnostics["requested_injection_mode"] == "legacy_prompt_append"
    assert runtime.context.memory_injection_diagnostics["legacy_prompt_append_migrated"] is True
    assert runtime.context.memory_injection_diagnostics["context_v2_block_count"] == 1
    assert runtime.context.memory_injection_diagnostics["store_counts"] == {"project": 1, "user": 1}
    assert runtime.context.memory_injection_diagnostics["source_kind_counts"] == {"archive": 1, "memory": 1}
    assert "project memory project memory" not in repr(runtime.context.memory_injection_diagnostics)

    captured: dict[str, str] = {}

    def override_request(**updates):
        system_message = updates.get("system_message")
        return SimpleNamespace(
            runtime=runtime,
            system_prompt=system_message.content if system_message is not None else request.system_prompt,
            override=override_request,
            messages=request.messages,
        )

    request = SimpleNamespace(
        runtime=runtime,
        system_prompt="base runtime prompt",
        messages=[HumanMessage(content="Use my stored project preference")],
        override=override_request,
    )

    def handler(patched_request):
        captured["system_prompt"] = patched_request.system_prompt
        return "ok"

    assert MemoryPrefetchMiddleware().wrap_model_call(request, handler) == "ok"
    assert captured["system_prompt"].startswith('<runtime_context_v2 version="p0">')
    assert "base runtime prompt\n\nDynamic memory recall" not in captured["system_prompt"]
    assert "<memory_context>" not in captured["system_prompt"]
    assert "<memory_recall>" not in captured["system_prompt"]
    assert "Dynamic memory recall is bounded fact lookup" in captured["system_prompt"]
    assert runtime.context.context_v2["actual_prompt_mode"] == "runtime_context_v2"
    assert runtime.context.context_v2["trace"]["selected_memory"] == [blocks[0]["block_id"]]


def test_legacy_memory_prefetch_strips_attributed_memory_fences_from_fallback_block() -> None:
    class FakeMemoryManager:
        config = SimpleNamespace(recall=SimpleNamespace(turn_recall_token_budget=120))

        def prefetch_recall(self, *, thread_id: str, query: str):
            recall = SimpleNamespace(
                thread_id=thread_id,
                query=query,
                snapshot_fingerprint="snapshot-attributed-fence",
                summary="",
                memory_matches=(),
                archive_hits=(),
                engine_notes=(),
                evidence=(),
            )
            recall.render_turn_block = (
                lambda: '<memory_recall source="legacy" priority="high">\n'
                "Attributed memory fence still migrates to a ContextBlock.\n"
                "</memory_recall>"
            )
            return recall

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-attributed-memory-fence",
            memory_manager=FakeMemoryManager(),
            memory_service=None,
            memory_context=None,
            memory_context_mode="legacy_prompt_append",
            context_v2_memory_blocks=[],
            memory_injection_diagnostics={},
        )
    )

    update = MemoryPrefetchMiddleware().before_model(
        LeadAgentState(messages=[HumanMessage(content="Use stored recall")]),
        runtime,
    )

    assert update is not None
    block_content = update["context_v2_memory_blocks"][0]["content"]
    assert "Attributed memory fence still migrates to a ContextBlock." in block_content
    assert "<memory_recall" not in block_content
    assert "[memory_recall" not in block_content
    assert "source=\"legacy\"" not in block_content

    captured: dict[str, str] = {}

    def override_request(**updates):
        system_message = updates.get("system_message")
        return SimpleNamespace(
            runtime=runtime,
            system_prompt=system_message.content if system_message is not None else request.system_prompt,
            override=override_request,
            messages=request.messages,
        )

    request = SimpleNamespace(
        runtime=runtime,
        system_prompt="base runtime prompt",
        messages=[HumanMessage(content="Use stored recall")],
        override=override_request,
    )

    def handler(patched_request):
        captured["system_prompt"] = patched_request.system_prompt
        return "ok"

    assert MemoryPrefetchMiddleware().wrap_model_call(request, handler) == "ok"
    assert "Attributed memory fence still migrates to a ContextBlock." in captured["system_prompt"]
    assert "<memory_recall" not in captured["system_prompt"]
    assert "[memory_recall" not in captured["system_prompt"]
    assert "source=\"legacy\"" not in captured["system_prompt"]


def test_legacy_memory_context_is_suppressed_without_context_v2_blocks() -> None:
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-legacy-direct-suppressed",
            memory_context_mode="legacy_prompt_append",
            memory_context="LEGACY_DIRECT_APPEND_SENTINEL",
            context_v2_memory_blocks=[],
            memory_injection_diagnostics={},
        )
    )
    captured: dict[str, str] = {}

    def override_request(**updates):
        system_message = updates.get("system_message")
        return SimpleNamespace(
            runtime=runtime,
            system_prompt=system_message.content if system_message is not None else request.system_prompt,
            override=override_request,
        )

    request = SimpleNamespace(
        runtime=runtime,
        system_prompt="base runtime prompt",
        override=override_request,
    )

    def handler(patched_request):
        captured["system_prompt"] = patched_request.system_prompt
        return "ok"

    assert MemoryPrefetchMiddleware().wrap_model_call(request, handler) == "ok"
    assert captured["system_prompt"] == "base runtime prompt"
    assert "LEGACY_DIRECT_APPEND_SENTINEL" not in captured["system_prompt"]


def test_memory_prefetch_can_emit_context_v2_blocks_without_direct_prompt_append() -> None:
    injection = MemoryInjectionView(
        namespace="global/default",
        summary="Project recall",
        facts=("User prefers pytest through the repo venv.",),
        evidence=("Memory captured from thread-memory-v2.",),
        confidence=0.8,
    )

    class FakeMemoryManager:
        config = SimpleNamespace(recall=SimpleNamespace(turn_recall_token_budget=80))

        def prefetch_recall(self, *, thread_id: str, query: str):
            recall = SimpleNamespace(
                thread_id=thread_id,
                query=query,
                snapshot_fingerprint="snapshot-v2",
                injection=injection,
                summary=injection.summary,
                memory_matches=(),
                archive_hits=(),
                engine_notes=("HCMS recall active",),
                evidence=(),
            )
            recall.render_turn_block = injection.render_fenced
            return recall

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-memory-v2",
            memory_manager=FakeMemoryManager(),
            memory_service=None,
            memory_context=None,
            memory_context_mode="context_v2",
            context_v2_memory_blocks=[],
            memory_injection_diagnostics={},
        )
    )

    update = MemoryPrefetchMiddleware().before_model(
        LeadAgentState(messages=[HumanMessage(content="Use my stored project preference")]),
        runtime,
    )

    assert update is not None
    assert update["memory_snapshot_id"] == "snapshot-v2"
    assert "memory_context" not in update
    blocks = update["context_v2_memory_blocks"]
    assert len(blocks) == 1
    assert blocks[0]["source"]["kind"] == "memory"
    assert blocks[0]["block_type"] == "semantic_fact"
    assert blocks[0]["content"] == "User prefers pytest through the repo venv."
    assert blocks[0]["evidence_refs"][0]["source_kind"] == "memory_evidence"
    assert runtime.context.context_v2_memory_blocks == blocks
    assert runtime.context.memory_context is None
    assert runtime.context.memory_injection_diagnostics["injection_mode"] == "context_v2"
    assert runtime.context.memory_injection_diagnostics["context_v2_block_count"] == 1

    captured: dict[str, str] = {}

    def override_request(**updates):
        system_message = updates.get("system_message")
        return SimpleNamespace(
            runtime=runtime,
            system_prompt=system_message.content if system_message is not None else request.system_prompt,
            override=override_request,
        )

    request = SimpleNamespace(
        runtime=runtime,
        system_prompt="base runtime prompt",
        override=override_request,
    )

    def handler(patched_request):
        captured["system_prompt"] = patched_request.system_prompt
        return "ok"

    assert MemoryPrefetchMiddleware().wrap_model_call(request, handler) == "ok"
    assert captured["system_prompt"].startswith('<runtime_context_v2 version="p0">')
    assert "base runtime prompt\n\n<runtime_context_v2" not in captured["system_prompt"]
    assert "<memory_context>" not in captured["system_prompt"]
    assert "<memory_recall>" not in captured["system_prompt"]
    assert "User prefers pytest through the repo venv." in captured["system_prompt"]
    memory_trace = runtime.context.context_v2["memory_prefetch_trace"]
    assert memory_trace["selected_memory"] == [blocks[0]["block_id"]]
    assert memory_trace["layer_token_usage"]["semantic_fact"] > 0
    assert runtime.context.context_v2["actual_prompt_mode"] == "runtime_context_v2"
    assert runtime.context.context_v2["candidate_block_count"] > runtime.context.memory_injection_diagnostics[
        "context_v2_block_count"
    ]
    assert runtime.context.context_v2["trace"]["selected_memory"] == [blocks[0]["block_id"]]


def test_memory_prefetch_defaults_missing_mode_to_context_v2_without_direct_prompt_append() -> None:
    injection = MemoryInjectionView(
        namespace="global/default",
        summary="Implicit V2 recall",
        facts=("Default memory prefetch competes as a ContextBlock.",),
        evidence=("Memory captured from thread-memory-default.",),
        confidence=0.82,
    )

    class FakeMemoryManager:
        config = SimpleNamespace(recall=SimpleNamespace(turn_recall_token_budget=80))

        def prefetch_recall(self, *, thread_id: str, query: str):
            recall = SimpleNamespace(
                thread_id=thread_id,
                query=query,
                snapshot_fingerprint="snapshot-default-v2",
                injection=injection,
                summary=injection.summary,
                memory_matches=(),
                archive_hits=(),
                engine_notes=("HCMS recall active",),
                evidence=(),
            )
            recall.render_turn_block = injection.render_fenced
            return recall

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-memory-default-v2",
            memory_manager=FakeMemoryManager(),
            memory_service=None,
            memory_context=None,
            memory_injection_diagnostics={},
        )
    )

    update = MemoryPrefetchMiddleware().before_model(
        LeadAgentState(messages=[HumanMessage(content="Use default memory injection")]),
        runtime,
    )

    assert update is not None
    assert "memory_context" not in update
    blocks = update["context_v2_memory_blocks"]
    assert len(blocks) == 1
    assert blocks[0]["block_type"] == "semantic_fact"
    assert blocks[0]["content"] == "Default memory prefetch competes as a ContextBlock."
    assert runtime.context.memory_context is None
    assert runtime.context.context_v2_memory_blocks == blocks
    assert runtime.context.memory_injection_diagnostics["injection_mode"] == "context_v2"
    assert runtime.context.memory_injection_diagnostics["context_v2_block_count"] == 1

    captured: dict[str, str] = {}

    def override_request(**updates):
        system_message = updates.get("system_message")
        return SimpleNamespace(
            runtime=runtime,
            system_prompt=system_message.content if system_message is not None else request.system_prompt,
            override=override_request,
        )

    request = SimpleNamespace(
        runtime=runtime,
        system_prompt="base runtime prompt\n\n<memory_recall>stale legacy recall</memory_recall>",
        override=override_request,
    )

    def handler(patched_request):
        captured["system_prompt"] = patched_request.system_prompt
        return "ok"

    assert MemoryPrefetchMiddleware().wrap_model_call(request, handler) == "ok"
    assert captured["system_prompt"].startswith('<runtime_context_v2 version="p0">')
    assert "<memory_context>" not in captured["system_prompt"]
    assert "<memory_recall>" not in captured["system_prompt"]
    assert "stale legacy recall" not in captured["system_prompt"]
    assert "Default memory prefetch competes as a ContextBlock." in captured["system_prompt"]
    assert runtime.context.context_v2["actual_prompt_mode"] == "runtime_context_v2"
    assert runtime.context.context_v2["trace"]["selected_memory"] == [blocks[0]["block_id"]]


def test_memory_prefetch_assembles_context_v2_from_stable_memory_snapshot_without_recall_blocks() -> None:
    sentinel_memory = "ROUND41_STABLE_MEMORY_ONLY should be assembled as ContextBlock"
    prompt_snapshot = PromptSnapshot(
        snapshot_id="snap-stable-memory-only",
        snapshot_key=PromptSnapshotKey(
            config_fingerprint="cfg",
            capability_bundle_fingerprint="cap",
            enabled_skill_summary_fingerprint="skills",
            policy_version="v1",
            memory_namespace="global/default",
            memory_snapshot_fingerprint="stable-memory-only-v1",
        ),
        stable_sections=[
            PromptSection(name="role_and_intent", content="Act as the lead runtime."),
            PromptSection(name="memory_snapshot", content=sentinel_memory),
        ],
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-stable-memory-only",
            run_id="run-stable-memory-only",
            memory_context_mode="context_v2",
            context_v2_memory_blocks=[],
            memory_context=None,
            memory_injection_diagnostics={},
            prompt_snapshot=prompt_snapshot,
            request_context="Use stored project preferences.",
            promoted_capabilities=(),
            memory_namespace="global/default",
        )
    )
    captured: dict[str, str] = {}

    def override_request(**updates):
        system_message = updates.get("system_message")
        return SimpleNamespace(
            runtime=runtime,
            system_prompt=system_message.content if system_message is not None else request.system_prompt,
            override=override_request,
            messages=request.messages,
        )

    request = SimpleNamespace(
        runtime=runtime,
        system_prompt="base runtime prompt",
        messages=[HumanMessage(content="Use stored project preferences.")],
        override=override_request,
    )

    def handler(patched_request):
        captured["system_prompt"] = patched_request.system_prompt
        return "ok"

    assert MemoryPrefetchMiddleware().wrap_model_call(request, handler) == "ok"
    assert captured["system_prompt"].startswith('<runtime_context_v2 version="p0">')
    assert "base runtime prompt" not in captured["system_prompt"]
    assert "<memory_snapshot>" not in captured["system_prompt"]
    assert sentinel_memory in captured["system_prompt"]
    context_v2 = runtime.context.context_v2
    assert context_v2["actual_prompt_mode"] == "runtime_context_v2"
    assert context_v2["hcms_v2_memory_candidate_count"] == 0
    assert context_v2["stable_memory_block_ids"] == context_v2["trace"]["selected_memory"]
    assert context_v2["total_memory_candidate_count"] == 1
    assert context_v2["trace"]["layer_token_usage"]["memory"] > 0
    assert context_v2["trace"]["selected_memory"]


def test_memory_prefetch_context_v2_assembly_failure_uses_structured_emergency_fallback(monkeypatch) -> None:
    injection = MemoryInjectionView(
        namespace="global/default",
        summary="Project recall",
        facts=("Emergency fallback must not reopen legacy prompt append.",),
        evidence=("Memory captured from thread-emergency-fallback.",),
        confidence=0.8,
    )

    class FakeMemoryManager:
        config = SimpleNamespace(recall=SimpleNamespace(turn_recall_token_budget=80))

        def prefetch_recall(self, *, thread_id: str, query: str):
            recall = SimpleNamespace(
                thread_id=thread_id,
                query=query,
                snapshot_fingerprint="snapshot-emergency-v2",
                injection=injection,
                summary=injection.summary,
                memory_matches=(),
                archive_hits=(),
                engine_notes=("HCMS recall active",),
                evidence=(),
            )
            recall.render_turn_block = injection.render_fenced
            return recall

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-emergency-fallback",
            run_id="run-emergency-fallback",
            memory_manager=FakeMemoryManager(),
            memory_service=None,
            memory_context=None,
            memory_context_mode="context_v2",
            context_v2_memory_blocks=[],
            memory_injection_diagnostics={},
        ),
        assembly_snapshot=SimpleNamespace(context_v2={}, memory_injection_diagnostics={}),
    )
    update = MemoryPrefetchMiddleware().before_model(
        LeadAgentState(messages=[HumanMessage(content="Use memory through Runtime Context V2")]),
        runtime,
    )
    assert update is not None

    def broken_prepare_llm_context(self, pipeline_input):
        raise RuntimeError("forced assembler failure")

    monkeypatch.setattr(
        memory_prefetch_module.TurnPipeline,
        "prepare_llm_context",
        broken_prepare_llm_context,
    )

    captured: dict[str, str] = {}

    def override_request(**updates):
        system_message = updates.get("system_message")
        return SimpleNamespace(
            runtime=runtime,
            system_prompt=system_message.content if system_message is not None else request.system_prompt,
            override=override_request,
            messages=request.messages,
        )

    request = SimpleNamespace(
        runtime=runtime,
        system_prompt=(
            "base runtime prompt\n\n"
            "<memory_recall>STALE_LEGACY_MEMORY_SHOULD_NOT_SURVIVE</memory_recall>"
        ),
        messages=[HumanMessage(content="Use memory through Runtime Context V2")],
        override=override_request,
    )

    def handler(patched_request):
        captured["system_prompt"] = patched_request.system_prompt
        return "ok"

    assert MemoryPrefetchMiddleware().wrap_model_call(request, handler) == "ok"
    assert captured["system_prompt"].startswith('<runtime_context_v2 version="p0">')
    assert 'mode="emergency_fallback"' in captured["system_prompt"]
    assert "base runtime prompt" in captured["system_prompt"]
    assert "STALE_LEGACY_MEMORY_SHOULD_NOT_SURVIVE" not in captured["system_prompt"]
    assert "<memory_recall>" not in captured["system_prompt"]
    assert runtime.context.memory_injection_diagnostics["context_v2_emergency_fallback"] is True
    assert runtime.context.context_v2["actual_prompt_mode"] == "runtime_context_v2_emergency_fallback"
    assert runtime.context.context_v2["trace"]["selected_block_ids"]
    assert runtime.assembly_snapshot.context_v2["emergency_fallback_used"] is True


def test_memory_capture_middleware_processes_hcms_queue_without_engine_fallback(contract_tmp_path) -> None:
    memory_service = MemoryService(
        store=FileMemoryStore(contract_tmp_path / "hcms-store"),
        queue=DebouncedMemoryQueue(),
        updater=HeuristicMemoryUpdater(max_facts=5),
        max_facts=5,
        injection_token_budget=200,
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-capture-direct",
            memory_namespace="global/default",
            memory_service=memory_service,
            memory_capture_processed=False,
            memory_capture_processed_count=0,
        )
    )

    update = MemoryCaptureMiddleware().after_agent(
        LeadAgentState(
            messages=[
                HumanMessage(content="Remember: User prefers concise updates instead of verbose status reports."),
                AIMessage(content="I will keep future updates concise."),
            ]
        ),
        runtime,
    )

    assert update is not None
    assert update["memory_snapshot_id"] == "global/default"
    assert update["memory_capture_diagnostics"] == {
        "source": "memory_service",
        "status": "processed",
        "phase": "after_agent",
        "processed_count": 1,
    }
    assert runtime.context.memory_capture_processed is True
    assert runtime.context.memory_capture_processed_count == 1
    assert runtime.context.memory_capture_diagnostics == update["memory_capture_diagnostics"]
    assert memory_service.queue.pending_count() == 0
    stored = memory_service.store.load("global/default")
    assert any("concise updates" in memory.content for memory in stored.memories)


def test_memory_capture_middleware_keeps_low_signal_capture_pending_until_debounce_window(contract_tmp_path) -> None:
    memory_service = MemoryService(
        store=FileMemoryStore(contract_tmp_path / "hcms-store"),
        queue=DebouncedMemoryQueue(),
        updater=HeuristicMemoryUpdater(max_facts=5),
        max_facts=5,
        injection_token_budget=200,
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-capture-low-signal",
            memory_namespace="global/default",
            memory_service=memory_service,
            memory_capture_processed=False,
            memory_capture_processed_count=0,
        )
    )

    update = MemoryCaptureMiddleware().after_agent(
        LeadAgentState(
            messages=[
                HumanMessage(content="Low signal continuity detail about the build output."),
                AIMessage(content="Acknowledged."),
            ]
        ),
        runtime,
    )

    assert update is not None
    assert update["memory_snapshot_id"] == "global/default"
    assert update["memory_capture_diagnostics"] == {
        "source": "memory_service",
        "status": "queued",
        "phase": "after_agent",
        "processed_count": 0,
    }
    assert runtime.context.memory_capture_processed is False
    assert runtime.context.memory_capture_processed_count == 0
    assert runtime.context.memory_capture_diagnostics == update["memory_capture_diagnostics"]
    assert memory_service.queue.pending_count() == 1
    assert memory_service.store.load("global/default").memories == []

    assert memory_service.process_pending("global/default", force=True) == 1
    stored = memory_service.store.load("global/default")
    assert any("Low signal continuity detail" in memory.content for memory in stored.memories)


def test_runtime_call_order_matches_protocol_phases(monkeypatch, contract_tmp_path) -> None:
    trace: list[str] = []

    from anvil.agents.middlewares import (
        GuardrailMiddleware,
        ClarificationMiddleware,
        DanglingToolCallMiddleware,
        DeferredToolFilterMiddleware,
        LLMErrorHandlingMiddleware,
        MemoryCaptureMiddleware,
        MemoryPrefetchMiddleware,
        SandboxAuditMiddleware,
        SandboxMiddleware,
        ThreadDataMiddleware,
        TodoMiddleware,
        ToolErrorHandlingMiddleware,
        ToolOutputBudgetMiddleware,
        ToolVisibilityMiddleware,
        UploadsMiddleware,
        ViewImageMiddleware,
        TokenUsageMiddleware,
        TitleMiddleware,
    )

    def wrap_method(cls, method_name: str, label: str):
        original = getattr(cls, method_name)

        if method_name in {"before_agent", "before_model", "after_model", "after_agent"}:
            def wrapped(self, state, runtime):
                trace.append(label)
                return original(self, state, runtime)
        elif method_name in {"wrap_model_call", "wrap_tool_call"}:
            def wrapped(self, request, handler):
                trace.append(label)
                return original(self, request, handler)
        else:
            raise AssertionError(f"unsupported method for trace wrapping: {method_name}")

        monkeypatch.setattr(cls, method_name, wrapped)

    wrap_method(ThreadDataMiddleware, "before_agent", "thread_data")
    wrap_method(UploadsMiddleware, "before_agent", "uploads")
    wrap_method(SandboxMiddleware, "before_agent", "sandbox")
    wrap_method(DanglingToolCallMiddleware, "wrap_model_call", "dangling_tool_calls")
    wrap_method(LLMErrorHandlingMiddleware, "wrap_model_call", "llm_error")
    wrap_method(SandboxAuditMiddleware, "wrap_tool_call", "sandbox_audit")
    wrap_method(TodoMiddleware, "before_model", "todo")
    wrap_method(TokenUsageMiddleware, "after_model", "token_usage")
    wrap_method(TitleMiddleware, "after_model", "title")
    wrap_method(MemoryPrefetchMiddleware, "before_model", "memory_prefetch")
    wrap_method(ViewImageMiddleware, "before_model", "view_image")
    wrap_method(ToolVisibilityMiddleware, "before_model", "capability_visibility")
    wrap_method(DeferredToolFilterMiddleware, "wrap_model_call", "deferred_filter")
    wrap_method(GuardrailMiddleware, "wrap_model_call", "guardrail")
    wrap_method(ToolErrorHandlingMiddleware, "wrap_tool_call", "tool_error")
    wrap_method(ToolOutputBudgetMiddleware, "wrap_tool_call", "tool_output_budget")
    wrap_method(ClarificationMiddleware, "after_model", "clarification")
    wrap_method(MemoryCaptureMiddleware, "after_agent", "memory_capture")

    engine = RunEngine()
    layers = [
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
                "hcms": {"enabled": True},
                "guardrails": {"enabled": True},
                "summarization": {"enabled": True},
                "plan_mode": {"enabled": True, "default": True},
                "view_image": {"enabled": True},
                "token_usage": {"enabled": True},
                "title": {"enabled": True},
            },
        )
    ]

    result = engine.run(
        RunRequest(
            thread_id="thread-order",
            user_message="list files",
            config_layers=layers,
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            feature_set=RuntimeFeatureSet(
                memory=True,
                memory_prefetch=True,
                guardrails=True,
                summarization=True,
                plan_mode=True,
                token_usage=True,
                title=True,
                view_image=True,
            ),
            approval_context="approved for this turn",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_dir",
                                "args": {"path": "/mnt/user-data/workspace"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status.value == "completed"
    assert trace[:5] == [
        "thread_data",
        "uploads",
        "sandbox",
        "todo",
        "memory_prefetch",
    ]
    assert "memory_prefetch" in trace
    assert "view_image" in trace
    assert "capability_visibility" in trace
    assert "guardrail" in trace
    assert "deferred_filter" in trace
    assert "sandbox_audit" in trace
    assert "tool_error" in trace
    assert "tool_output_budget" in trace
    assert "title" in trace
    assert "token_usage" in trace
    assert "clarification" in trace
    assert trace[-1] == "memory_capture"

    first_todo = trace.index("todo")
    first_prefetch = trace.index("memory_prefetch")
    first_view_image = trace.index("view_image")
    first_visibility = trace.index("capability_visibility")
    first_guardrail = trace.index("guardrail")
    first_filter = trace.index("deferred_filter")
    first_audit = trace.index("sandbox_audit")
    tool_error_index = trace.index("tool_error")
    tool_output_budget_index = trace.index("tool_output_budget")
    title_index = trace.index("title")
    token_usage_index = trace.index("token_usage")
    last_clarification = len(trace) - 1 - trace[::-1].index("clarification")

    assert first_todo < first_prefetch < first_view_image < first_visibility < first_filter
    assert first_guardrail < first_filter
    assert first_audit < tool_error_index
    assert tool_error_index < tool_output_budget_index
    assert title_index < last_clarification
    assert token_usage_index < last_clarification
    assert trace[-1] == "memory_capture"
