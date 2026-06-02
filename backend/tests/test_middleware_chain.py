from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from types import SimpleNamespace
import pytest

from anvil.agents.factory import build_middleware_chain
from anvil.agents.features import Next, Prev, RuntimeFeatureSet, resolve_feature_set
from anvil.agents.lead_agent.types import LeadAgentState
from anvil.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware
from anvil.agents.middlewares.view_image_middleware import ViewImageMiddleware
from anvil.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from anvil.agents.middlewares.llm_error_handling_middleware import LLMExecutionError
from anvil.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from anvil.agents.middlewares.memory_prefetch_middleware import MemoryPrefetchMiddleware
from anvil.config import ConfigLayer, ConfigLayerKind, EffectiveConfig, MemoryConfig, MemoryPlatformConfig
from anvil.config.models import CompactionConfig, LoopDetectionConfig, SummarizationConfig
from anvil.memory_platform.contracts import ArchiveSearchHit, CuratedEntry, RecallEvidence, RecallResult
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
        "SummarizationMiddleware",
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


def test_memory_platform_feature_does_not_reenable_legacy_capture_by_default() -> None:
    chain = build_middleware_chain(
        RuntimeFeatureSet(
            memory=True,
            memory_prefetch=True,
        )
    )

    assert "MemoryPrefetchMiddleware" in [middleware.name for middleware in chain]
    assert "MemoryCaptureMiddleware" not in [middleware.name for middleware in chain]


def test_memory_platform_config_disables_legacy_capture_even_when_legacy_memory_enabled() -> None:
    config = EffectiveConfig(
        memory=MemoryConfig(enabled=True),
        memory_platform=MemoryPlatformConfig(enabled=True),
    )
    features = resolve_feature_set(RuntimeFeatureSet(), config)
    chain = build_middleware_chain(features)

    assert features.memory is True
    assert features.memory_prefetch is True
    assert features.memory_capture is False
    assert "MemoryPrefetchMiddleware" in [middleware.name for middleware in chain]
    assert "MemoryCaptureMiddleware" not in [middleware.name for middleware in chain]


def test_default_context_compaction_uses_summarization_middleware_not_legacy_compaction() -> None:
    config = EffectiveConfig()
    features = resolve_feature_set(RuntimeFeatureSet(), config)
    chain = build_middleware_chain(features, effective_config=config)

    names = [middleware.name for middleware in chain]

    assert features.summarization is False
    assert features.compaction is False
    assert "SummarizationMiddleware" not in names
    assert "CompactionMiddleware" not in names


def test_legacy_compaction_config_does_not_enable_context_compaction_without_summarization() -> None:
    config = EffectiveConfig(compaction=CompactionConfig(enabled=True))
    features = resolve_feature_set(RuntimeFeatureSet(), config)
    chain = build_middleware_chain(features, effective_config=config)

    names = [middleware.name for middleware in chain]

    assert features.summarization is False
    assert features.compaction is False
    assert "SummarizationMiddleware" not in names
    assert "CompactionMiddleware" not in names


def test_summarization_config_enables_single_context_compaction_surface() -> None:
    config = EffectiveConfig(summarization=SummarizationConfig(enabled=True))
    features = resolve_feature_set(RuntimeFeatureSet(), config)
    chain = build_middleware_chain(features, effective_config=config)

    names = [middleware.name for middleware in chain]

    assert features.summarization is True
    assert features.compaction is False
    assert "SummarizationMiddleware" in names
    assert "CompactionMiddleware" not in names


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


def test_memory_prefetch_records_bounded_injection_diagnostics_without_memory_payload() -> None:
    class FakeMemoryManager:
        config = SimpleNamespace(recall=SimpleNamespace(turn_recall_token_budget=80))

        def prefetch_recall(self, *, thread_id: str, query: str) -> RecallResult:
            return RecallResult(
                thread_id=thread_id,
                query=query,
                snapshot_fingerprint="snapshot-diag",
                summary="summary " * 80,
                curated_matches=(
                    CuratedEntry(
                        entry_id="entry-1",
                        store_id="project",
                        content="project memory " * 80,
                    ),
                    CuratedEntry(
                        entry_id="entry-2",
                        store_id="user",
                        content="user memory",
                    ),
                ),
                archive_hits=(
                    ArchiveSearchHit(
                        archive_id="archive-1",
                        thread_id="source-thread",
                        score=0.9,
                        excerpt="archive memory " * 40,
                        created_at=datetime.now(timezone.utc),
                    ),
                ),
                provider_notes=("provider note",),
                evidence=(
                    RecallEvidence(
                        evidence_id="ev-1",
                        source_kind="curated",
                        source_id="entry-1",
                        score=0.8,
                        reason="matched project preference",
                    ),
                    RecallEvidence(
                        evidence_id="ev-2",
                        source_kind="archive",
                        source_id="archive-1",
                        score=0.7,
                        reason="matched older thread",
                    ),
                ),
            )

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            thread_id="thread-memory-diag",
            memory_manager=FakeMemoryManager(),
            memory_service=None,
            memory_context=None,
            memory_injection_diagnostics={},
        )
    )

    update = MemoryPrefetchMiddleware().before_model(
        LeadAgentState(messages=[HumanMessage(content="Use my stored project preference")]),
        runtime,
    )

    assert update is not None
    assert update["memory_snapshot_id"] == "snapshot-diag"
    assert "memory_context" in update
    assert runtime.context.memory_injection_diagnostics["source"] == "memory_manager"
    assert runtime.context.memory_injection_diagnostics["status"] == "injected"
    assert runtime.context.memory_injection_diagnostics["curated_match_count"] == 2
    assert runtime.context.memory_injection_diagnostics["archive_hit_count"] == 1
    assert runtime.context.memory_injection_diagnostics["evidence_count"] == 2
    assert runtime.context.memory_injection_diagnostics["provider_note_count"] == 1
    assert runtime.context.memory_injection_diagnostics["token_budget"] == 80
    assert runtime.context.memory_injection_diagnostics["truncated"] is True
    assert runtime.context.memory_injection_diagnostics["store_counts"] == {"project": 1, "user": 1}
    assert runtime.context.memory_injection_diagnostics["source_kind_counts"] == {"archive": 1, "curated": 1}
    assert "project memory project memory" not in repr(runtime.context.memory_injection_diagnostics)


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
        SummarizationMiddleware,
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
    wrap_method(SummarizationMiddleware, "before_model", "summarization")
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
                "memory": {"enabled": True, "prefetch_once_per_turn": True, "store_path": str(contract_tmp_path / "memory")},
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
        "summarization",
        "todo",
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

    first_summarization = trace.index("summarization")
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

    assert first_summarization < first_todo < first_prefetch < first_view_image < first_visibility < first_filter
    assert first_guardrail < first_filter
    assert first_audit < tool_error_index
    assert tool_error_index < tool_output_budget_index
    assert title_index < last_clarification
    assert token_usage_index < last_clarification
    assert trace[-1] == "memory_capture"
