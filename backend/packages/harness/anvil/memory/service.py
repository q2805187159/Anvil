from __future__ import annotations

import difflib
from collections.abc import Mapping
from threading import Lock
from typing import Any, Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from .compiler import KnowledgeCompiler, compile_manual_memory_content
from .contracts import (
    CausalPath,
    CausalNode,
    CounterfactualImpact,
    CounterfactualResult,
    Evidence,
    EvidenceType,
    Memory,
    MemoryCaptureEnvelope,
    MemoryCategory,
    MemoryInjectionView,
    MemoryLifecycleState,
    MemorySummary,
    MemoryVersionRecord,
    MemoryQueue,
    MemoryState,
    MemoryStore,
    MemoryUpdater,
    RetrievalConfig,
    RetrievalResult,
    SourceType,
    sanitize_memory_context_text,
    stable_id,
    tokenize,
    utc_now,
    record_memory_diagnostic,
)
from .lifecycle import MemoryLifecycleManager
from .retrieval import FourStreamRetriever
from .signals import detect_capture_signals


class MemoryService:
    """HCMS facade used by agent middleware and app adapters."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        queue: MemoryQueue,
        updater: MemoryUpdater,
        max_facts: int = 12,
        injection_token_budget: int = 1200,
        max_evidence: int = 6,
        min_relevance_score: float = 0.0,
        retriever: FourStreamRetriever | None = None,
        lifecycle: MemoryLifecycleManager | None = None,
    ) -> None:
        self.store = store
        self.queue = queue
        self.updater = updater
        self.max_facts = max_facts
        self.injection_token_budget = injection_token_budget
        self.max_evidence = max(1, int(max_evidence))
        self.min_relevance_score = max(0.0, min(float(min_relevance_score), 1.0))
        self.compiler = KnowledgeCompiler()
        self.retriever = retriever or FourStreamRetriever(RetrievalConfig(default_limit=max_facts))
        self.lifecycle = lifecycle or MemoryLifecycleManager()
        self._mutation_lock = Lock()

    def build_capture_envelope(
        self,
        *,
        thread_id: str,
        namespace: str,
        messages: Iterable[BaseMessage],
        trace_id: str | None = None,
        blocked: bool = False,
        failed: bool = False,
    ) -> MemoryCaptureEnvelope:
        user_messages: list[str] = []
        final_assistant_messages: list[str] = []
        corrections: list[str] = []
        positive: list[str] = []

        for message in messages:
            if isinstance(message, ToolMessage):
                continue

            content = self._extract_text(message.content)
            if not content:
                continue

            if isinstance(message, HumanMessage):
                user_messages.append(content)
                signal = detect_capture_signals(content)
                if signal.correction:
                    corrections.append(content)
                if signal.reinforcement:
                    positive.append(content)
            elif isinstance(message, AIMessage):
                if blocked or failed:
                    continue
                if getattr(message, "tool_calls", None):
                    continue
                final_assistant_messages.append(content)

        runtime_event_refs = _runtime_event_refs_for_capture(
            thread_id=thread_id,
            namespace=namespace,
            trace_id=trace_id,
            user_messages=user_messages,
            assistant_messages=final_assistant_messages,
        )
        return MemoryCaptureEnvelope(
            thread_id=thread_id,
            memory_namespace=namespace,
            user_messages=user_messages,
            final_assistant_messages=final_assistant_messages,
            explicit_corrections=corrections,
            positive_reinforcement=positive,
            trace_id=trace_id,
            metadata={"runtime_event_refs": runtime_event_refs} if runtime_event_refs else {},
        )

    def enqueue_capture(self, envelope: MemoryCaptureEnvelope) -> None:
        self.queue.enqueue(envelope)

    def has_capture_signal(self, envelope: MemoryCaptureEnvelope) -> bool:
        return bool(
            envelope.user_messages
            or envelope.final_assistant_messages
            or envelope.explicit_corrections
            or envelope.positive_reinforcement
        )

    def should_process_capture_immediately(self, envelope: MemoryCaptureEnvelope) -> bool:
        should_flush = getattr(self.queue, "should_flush_immediately", None)
        if callable(should_flush):
            return bool(should_flush(envelope))
        text = " ".join(
            [
                *envelope.user_messages,
                *envelope.final_assistant_messages,
                *envelope.explicit_corrections,
                *envelope.positive_reinforcement,
            ]
        ).lower()
        signal = detect_capture_signals(
            text,
            correction=bool(envelope.explicit_corrections),
            reinforcement=bool(envelope.positive_reinforcement),
        )
        return signal.correction or signal.remember

    def process_pending(self, namespace: str | None = None, *, force: bool = True) -> int:
        with self._mutation_lock:
            processed = 0
            while True:
                pop_next = getattr(self.queue, "pop_next")
                try:
                    envelope = pop_next(namespace, force=force)
                except TypeError:
                    envelope = pop_next(namespace)
                if envelope is None:
                    break
                current_state: MemoryState | None = None
                try:
                    current_state = self.store.load(envelope.memory_namespace)
                    next_state = self.updater.update(current_state, envelope)
                    self.lifecycle.apply_forgetting(next_state)
                    self.store.save(envelope.memory_namespace, next_state)
                    processed += 1
                except Exception as exc:
                    self._record_capture_failure(envelope, current_state, exc)
                    self._requeue_failed_envelope(envelope, exc)
                    raise
            return processed

    def prefetch(self, namespace: str) -> MemoryState:
        return self.store.load(namespace)

    def search(self, namespace: str, query: str, *, limit: int | None = None) -> list[RetrievalResult]:
        state = self.store.load(namespace)
        results = self.retriever.retrieve(state, query, limit=limit or self.max_facts)
        results = self._filter_relevance(results)
        self.store.save(namespace, state)
        return results

    def why(self, namespace: str, query: str, *, limit: int = 3) -> list[CausalPath]:
        state = self.store.load(namespace)
        causal_direction = _causal_query_direction(query)
        results = self.retriever.retrieve(state, query, limit=max(limit, 1))
        paths: list[CausalPath] = []
        for result in results:
            paths.extend(self.retriever.causal_paths(state, result.memory_id, max_hops=3, direction=causal_direction))
        if not paths:
            for result in results[:limit]:
                if result.memory is None:
                    continue
                paths.append(
                    self._degraded_why_path(result)
                )
        return paths[:limit]

    def counterfactual(self, namespace: str, query: str, *, avoid: str = "", limit: int = 5) -> CounterfactualResult:
        state = self.store.load(namespace)
        assumption_text = (avoid or query or "").strip()
        anchor = self._counterfactual_anchor(state, assumption_text or query)
        if anchor is None:
            return CounterfactualResult(
                query=query,
                assumption=f"Assume the event did not occur: {assumption_text or query}",
                impacts=[],
                evidence=[],
                confidence=0.0,
                engine_notes=["HCMS counterfactual reasoning active"],
            )

        impacts: list[CounterfactualImpact] = []
        seen: set[str] = set()
        for edge, target, depth in self._downstream_causal_impacts(state, anchor.memory_id, max_depth=3, limit=max(limit, 1)):
            if target.memory_id in seen:
                continue
            seen.add(target.memory_id)
            evidence_ids = [*edge.evidence, *[item.evidence_id for item in anchor.evidence[:2]], *[item.evidence_id for item in target.evidence[:2]]]
            impacts.append(
                CounterfactualImpact(
                    memory_id=target.memory_id,
                    summary=target.summary or target.content[:120],
                    projected_change=self._project_counterfactual_change(anchor, target, edge),
                    confidence=min(anchor.confidence, target.confidence, edge.strength),
                    evidence=list(dict.fromkeys(evidence_ids))[:8],
                    causal_depth=depth,
                    relation_type=str(edge.metadata.get("relation_type") or edge.causal_type.value),
                )
            )

        evidence = [item.evidence_id for item in anchor.evidence[:4]]
        for impact in impacts:
            evidence.extend(impact.evidence[:4])
        confidence = sum(item.confidence for item in impacts) / len(impacts) if impacts else anchor.confidence
        return CounterfactualResult(
            query=query,
            assumption=f"Assume the event did not occur: {assumption_text or anchor.summary}",
            removed_memory_id=anchor.memory_id,
            impacts=impacts[:limit],
            evidence=list(dict.fromkeys(evidence))[:12],
            confidence=confidence,
            engine_notes=["HCMS counterfactual reasoning active"],
        )

    def build_injection_view(self, namespace: str, *, query: str = "") -> MemoryInjectionView:
        state = self.prefetch(namespace)
        if query:
            retrieval = self.retriever.retrieve(state, query, limit=self.max_facts)
            retrieval = self._filter_relevance(retrieval)
            ranked = [result.memory for result in retrieval if result.memory is not None]
        else:
            ranked = sorted(
                state.active_memories(),
                key=lambda memory: (
                    0 if memory.category == MemoryCategory.PREFERENCE else 1,
                    -memory.confidence,
                    -memory.salience,
                ),
            )
            retrieval = []

        facts: list[str] = []
        evidence: list[str] = []
        char_budget = self.injection_token_budget * 4
        used = len(state.summary.summary)
        confidence_values: list[float] = []
        for memory in ranked[: self.max_facts]:
            line = f"{memory.category.value}: {memory.summary or memory.content}"
            if used + len(line) > char_budget:
                break
            facts.append(line)
            confidence_values.append(memory.confidence)
            evidence.extend(f"{item.type.value}:{item.content}" for item in memory.evidence[:2])
            used += len(line)
        causal_chains = self._render_causal_chains(state, [memory.memory_id for memory in ranked[:3]])
        confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
        return MemoryInjectionView(
            namespace=namespace,
            summary=state.summary.summary,
            facts=tuple(facts),
            causal_chains=tuple(causal_chains[:3]),
            evidence=tuple(evidence[: self.max_evidence]),
            confidence=confidence,
        )

    def capture_runtime_event_v2(self, event: object, *, namespace: str = "global/default"):
        from .hcms_v2.service import HCMSV2CaptureResult, HCMSV2RuntimeBridge

        bridge = HCMSV2RuntimeBridge()
        capture = bridge.capture_runtime_event(event, namespace=namespace)
        memory = self._persist_runtime_event_observation(
            capture,
            event_metadata=_runtime_event_metadata(event),
        )
        schedule = bridge.schedule_capture_consolidation(
            capture,
            persisted_memory_id=memory.memory_id,
        )
        memory = self._register_hcms_v2_consolidation_schedule(
            namespace=namespace,
            persisted_memory_id=memory.memory_id,
            schedule=schedule,
        )
        replay = bridge.replay_slow_consolidation(capture, schedule=schedule)
        memory, slow_memory_ids = self._persist_hcms_v2_slow_consolidation_replay(
            namespace=namespace,
            persisted_memory_id=memory.memory_id,
            replay=replay,
            schedule=schedule,
        )
        slow_claim_ids = [str(claim.claim_id) for claim in replay.claims if str(getattr(claim, "claim_id", "") or "")]
        envelope = capture.envelope.model_copy(
            update={
                "metadata": {
                    **capture.envelope.metadata,
                    "persisted_memory_id": memory.memory_id,
                    "hcms_v2_consolidation_schedule_id": schedule.schedule_id,
                    "hcms_v2_fast_consolidation_task_id": schedule.fast_task.task_id,
                    "hcms_v2_slow_consolidation_task_id": schedule.slow_task.task_id,
                    "hcms_v2_slow_consolidation_status": replay.status,
                    "hcms_v2_slow_consolidated_memory_ids": list(slow_memory_ids),
                    "hcms_v2_slow_consolidation_claim_ids": slow_claim_ids,
                }
            }
        )
        return HCMSV2CaptureResult(
            envelope=envelope,
            observation=capture.observation,
            guard_decision=capture.guard_decision,
        )

    def capture_runtime_events_v2(self, events: Iterable[object], *, namespace: str = "global/default"):
        from .hcms_v2.service import HCMSV2CaptureResult, HCMSV2RuntimeBridge

        event_list = list(events or [])
        if not event_list:
            return []

        bridge = HCMSV2RuntimeBridge()
        prepared: list[tuple[Any, Memory, Any, Any, list[Memory], list[str], tuple[str, ...]]] = []
        for event in event_list:
            capture = bridge.capture_runtime_event(event, namespace=namespace)
            source_memory = self._runtime_event_observation_memory(
                capture,
                event_metadata=_runtime_event_metadata(event),
            )
            schedule = bridge.schedule_capture_consolidation(
                capture,
                persisted_memory_id=source_memory.memory_id,
            )
            replay = bridge.replay_slow_consolidation(capture, schedule=schedule)
            claim_ids = [
                str(claim.claim_id)
                for claim in getattr(replay, "claims", []) or []
                if str(getattr(claim, "claim_id", "") or "")
            ]
            slow_memories = [
                _memory_from_hcms_v2_consolidated_memory(
                    _hcms_v2_slow_replay_candidate_with_metadata(
                        candidate,
                        replay=replay,
                        schedule=schedule,
                        claim_ids=claim_ids,
                    ),
                    namespace=namespace,
                )
                for candidate in getattr(replay, "consolidated_memories", []) or []
            ]
            prepared.append(
                (
                    capture,
                    source_memory,
                    schedule,
                    replay,
                    slow_memories,
                    claim_ids,
                    tuple(memory.memory_id for memory in slow_memories),
                )
            )

        results = []
        now = utc_now()
        with self._mutation_lock:
            state = self.store.load(namespace)
            for capture, source_memory, schedule, replay, slow_memories, claim_ids, slow_memory_ids in prepared:
                state = self.compiler.upsert_memory(
                    state,
                    source_memory,
                    reason="hcms_v2_runtime_event_capture",
                )
                persisted_source = _require_memory(state, source_memory.memory_id)
                self._apply_hcms_v2_consolidation_schedule(
                    state,
                    persisted_source,
                    schedule=schedule,
                    now=now,
                )
                for slow_memory in slow_memories:
                    state = self.compiler.upsert_memory(
                        state,
                        slow_memory,
                        reason="hcms_v2_slow_consolidation_replay",
                    )
                persisted_source = _require_memory(state, source_memory.memory_id)
                self._apply_hcms_v2_slow_consolidation_replay(
                    state,
                    persisted_source,
                    replay=replay,
                    schedule=schedule,
                    slow_memory_ids=slow_memory_ids,
                    claim_ids=claim_ids,
                    now=now,
                )
                envelope = capture.envelope.model_copy(
                    update={
                        "metadata": {
                            **capture.envelope.metadata,
                            "persisted_memory_id": persisted_source.memory_id,
                            "hcms_v2_consolidation_schedule_id": schedule.schedule_id,
                            "hcms_v2_fast_consolidation_task_id": schedule.fast_task.task_id,
                            "hcms_v2_slow_consolidation_task_id": schedule.slow_task.task_id,
                            "hcms_v2_slow_consolidation_status": replay.status,
                            "hcms_v2_slow_consolidated_memory_ids": list(slow_memory_ids),
                            "hcms_v2_slow_consolidation_claim_ids": claim_ids,
                        }
                    }
                )
                results.append(
                    HCMSV2CaptureResult(
                        envelope=envelope,
                        observation=capture.observation,
                        guard_decision=capture.guard_decision,
                    )
                )
            self.compiler.weave_relations(state)
            self.compiler._rebuild_entities(state)  # noqa: SLF001 - runtime capture batch keeps graph indexes current.
            self.compiler._rebuild_causal_edges(state)  # noqa: SLF001 - runtime capture batch keeps graph indexes current.
            state.summary = MemorySummary(summary=self.compiler._summary(state), updated_at=now)  # noqa: SLF001
            state.updated_at = now
            self.store.save(namespace, state)
        return results

    def sync_workspace_state_v2(self, workspace_state: object, *, namespace: str = "global/default") -> Memory | None:
        if not _workspace_state_has_signal(workspace_state):
            return None

        from .hcms_v2 import workspace_state_to_working_memory

        consolidated = workspace_state_to_working_memory(workspace_state, namespace=namespace)
        memory = _memory_from_workspace_state(consolidated, namespace=namespace)
        with self._mutation_lock:
            state = self.store.load(namespace)
            state = _upsert_workspace_snapshot_memory(state, memory, reason="hcms_v2_workspace_state_sync")
            self.compiler.weave_relations(state)
            self.compiler._rebuild_entities(state)  # noqa: SLF001 - workspace snapshot keeps graph indexes current.
            self.compiler._rebuild_causal_edges(state)  # noqa: SLF001 - workspace snapshot keeps graph indexes current.
            state.summary = MemorySummary(summary=self.compiler._summary(state), updated_at=utc_now())  # noqa: SLF001
            self.store.save(namespace, state)
            return next(item for item in state.memories if item.memory_id == memory.memory_id)

    def mine_capability_usage_events_v2(
        self,
        events: Iterable[object],
        *,
        namespace: str = "global/default",
    ):
        from .hcms_v2 import capability_usage_event_from_runtime_event
        from .hcms_v2.service import HCMSV2RuntimeBridge

        bridge = HCMSV2RuntimeBridge()
        event_list = list(events)
        usage_events = []
        for event in event_list:
            usage = capability_usage_event_from_runtime_event(event)
            if usage is not None:
                usage_events.append(usage)
        batch = bridge.mine_capability_usage_events(usage_events, namespace=namespace)
        if not usage_events:
            return batch.model_copy(
                update={
                    "diagnostics": {
                        **batch.diagnostics,
                        "runtime_event_count": len(event_list),
                        "capability_usage_event_count": 0,
                    }
                }
            )
        candidates = [*batch.procedural_memories, *batch.wisdom_memories]
        if not candidates:
            return batch.model_copy(
                update={
                    "diagnostics": {
                        **batch.diagnostics,
                        "runtime_event_count": len(event_list),
                        "capability_usage_event_count": len(usage_events),
                    }
                }
            )

        memories = [_memory_from_hcms_v2_consolidated_memory(candidate, namespace=namespace) for candidate in candidates]
        with self._mutation_lock:
            state = self.store.load(namespace)
            for memory in memories:
                state = self.compiler.upsert_memory(state, memory, reason="hcms_v2_capability_usage_mining")
            self.compiler.weave_relations(state)
            self.compiler._rebuild_entities(state)  # noqa: SLF001 - capability mining keeps graph indexes current.
            self.compiler._rebuild_causal_edges(state)  # noqa: SLF001 - capability mining keeps graph indexes current.
            state.summary = MemorySummary(summary=self.compiler._summary(state), updated_at=utc_now())  # noqa: SLF001
            self.store.save(namespace, state)

        persisted_ids = [memory.memory_id for memory in memories]
        return batch.model_copy(
            update={
                "persisted_memory_ids": persisted_ids,
                "diagnostics": {
                    **batch.diagnostics,
                    "runtime_event_count": len(event_list),
                    "capability_usage_event_count": len(usage_events),
                    "persisted_memory_count": len(persisted_ids),
                    "persisted_memory_ids": persisted_ids,
                },
            }
        )

    def _persist_runtime_event_observation(self, capture: Any, *, event_metadata: dict[str, Any]) -> Memory:
        memory = self._runtime_event_observation_memory(capture, event_metadata=event_metadata)
        namespace = str(capture.envelope.namespace or capture.observation.namespace or "global/default")
        with self._mutation_lock:
            state = self.store.load(namespace)
            state = self.compiler.upsert_memory(state, memory, reason="hcms_v2_runtime_event_capture")
            self.compiler.weave_relations(state)
            self.compiler._rebuild_entities(state)  # noqa: SLF001 - runtime capture keeps graph indexes current.
            self.compiler._rebuild_causal_edges(state)  # noqa: SLF001 - runtime capture keeps graph indexes current.
            state.summary.summary = self.compiler._summary(state)  # noqa: SLF001 - facade centralizes summary refresh.
            self.store.save(namespace, state)
            return next(item for item in state.memories if item.memory_id == memory.memory_id)

    def _runtime_event_observation_memory(self, capture: Any, *, event_metadata: dict[str, Any]) -> Memory:
        envelope = capture.envelope
        observation = capture.observation
        namespace = str(envelope.namespace or observation.namespace or "global/default")
        content = sanitize_memory_context_text(observation.content or "")
        if not content:
            runtime_summary = envelope.runtime_events[0].payload_summary if envelope.runtime_events else ""
            content = sanitize_memory_context_text(runtime_summary or observation.observation_type or "runtime event")
        summary = content[:240]
        now = observation.timestamp or utc_now()
        confidence = max(0.0, min(float(capture.guard_decision.trust_score), 1.0))
        salience = max(0.0, min(float(envelope.salience_seed), 1.0))
        source_id = observation.observation_id
        memory_id = stable_id(
            "mem",
            "runtime_event",
            namespace,
            observation.event_id or observation.observation_id,
            *envelope.tool_result_refs,
            size=12,
        )
        evidence = Evidence(
            evidence_id=stable_id("ev", memory_id, source_id, summary, size=12),
            type=EvidenceType.OBSERVATION,
            content=summary[:180],
            weight=confidence,
            timestamp=now,
            source_id=source_id,
            metadata={
                "event_id": observation.event_id,
                "event_type": observation.observation_type,
                "content_ref": observation.content_ref,
                "tool_result_refs": list(envelope.tool_result_refs),
                "workspace_refs": list(observation.workspace_refs),
                "capture_envelope_id": envelope.envelope_id,
            },
        )
        metadata = _runtime_event_memory_metadata(
            capture,
            event_metadata=event_metadata,
            persisted_memory_id=memory_id,
        )
        memory = Memory(
            memory_id=memory_id,
            content=content,
            summary=summary,
            category=MemoryCategory.CONTEXT,
            confidence=confidence,
            salience=salience,
            evidence=[evidence],
            tags=_runtime_event_memory_tags(observation.observation_type, event_metadata),
            concepts=list(tokenize(f"{summary} {' '.join(str(value) for value in event_metadata.values())}"))[:12],
            created_at=now,
            updated_at=now,
            accessed_at=now,
            source_thread_id=observation.thread_id,
            source_type=SourceType.TOOL if envelope.tool_result_refs else SourceType.OBSERVATION,
            metadata=metadata,
        )
        return memory

    def _register_hcms_v2_consolidation_schedule(
        self,
        *,
        namespace: str,
        persisted_memory_id: str,
        schedule: Any,
    ) -> Memory:
        schedule_payload = schedule.model_dump(mode="json")
        slow_task_id = str(schedule.slow_task.task_id or "")
        fast_task_id = str(schedule.fast_task.task_id or "")
        now = utc_now()
        with self._mutation_lock:
            state = self.store.load(namespace)
            memory = _require_memory(state, persisted_memory_id)
            if memory.metadata.get("hcms_v2_consolidation_schedule_id") == schedule.schedule_id:
                return memory

            parent_version_id = f"{memory.memory_id}@v{memory.version}"
            memory.version += 1
            memory.parent_id = parent_version_id
            memory.supersedes = [*memory.supersedes, parent_version_id]
            memory.updated_at = now
            memory.metadata = {
                **memory.metadata,
                "hcms_v2_fast_consolidated": True,
                "hcms_v2_consolidation_schedule_id": schedule.schedule_id,
                "hcms_v2_fast_consolidation_task_id": fast_task_id,
                "hcms_v2_slow_consolidation_task_id": slow_task_id,
                "hcms_v2_consolidation_schedule": schedule_payload,
            }
            state.versions.append(
                MemoryVersionRecord(
                    memory_id=memory.memory_id,
                    version=memory.version,
                    parent_id=memory.parent_id,
                    content=memory.content,
                    summary=memory.summary,
                    reason="hcms_v2_consolidation_scheduled",
                    metadata={
                        **memory.version_metadata(),
                        "hcms_v2_consolidation_schedule_id": schedule.schedule_id,
                        "hcms_v2_fast_consolidation_task_id": fast_task_id,
                        "hcms_v2_slow_consolidation_task_id": slow_task_id,
                    },
                )
            )
            record_memory_diagnostic(
                state,
                component="hcms_v2_consolidation",
                reason="slow_consolidation_scheduled",
                stream_name="slow",
                message="HCMS V2 slow consolidation task scheduled from runtime event capture.",
                metadata={
                    "task_id": slow_task_id,
                    "schedule_id": str(schedule.schedule_id or ""),
                    "capture_envelope_id": str(schedule.capture_envelope_id),
                    "observation_id": str(schedule.observation_id),
                    "persisted_memory_id": persisted_memory_id,
                    "target_layer": str(schedule.slow_task.target_layer),
                },
            )
            state.updated_at = now
            self.store.save(namespace, state)
            return memory

    def _apply_hcms_v2_consolidation_schedule(
        self,
        state: MemoryState,
        memory: Memory,
        *,
        schedule: Any,
        now,
    ) -> None:
        if memory.metadata.get("hcms_v2_consolidation_schedule_id") == schedule.schedule_id:
            return

        schedule_payload = schedule.model_dump(mode="json")
        slow_task_id = str(schedule.slow_task.task_id or "")
        fast_task_id = str(schedule.fast_task.task_id or "")
        parent_version_id = f"{memory.memory_id}@v{memory.version}"
        memory.version += 1
        memory.parent_id = parent_version_id
        memory.supersedes = [*memory.supersedes, parent_version_id]
        memory.updated_at = now
        memory.metadata = {
            **memory.metadata,
            "hcms_v2_fast_consolidated": True,
            "hcms_v2_consolidation_schedule_id": schedule.schedule_id,
            "hcms_v2_fast_consolidation_task_id": fast_task_id,
            "hcms_v2_slow_consolidation_task_id": slow_task_id,
            "hcms_v2_consolidation_schedule": schedule_payload,
        }
        state.versions.append(
            MemoryVersionRecord(
                memory_id=memory.memory_id,
                version=memory.version,
                parent_id=memory.parent_id,
                content=memory.content,
                summary=memory.summary,
                reason="hcms_v2_consolidation_scheduled",
                metadata={
                    **memory.version_metadata(),
                    "hcms_v2_consolidation_schedule_id": schedule.schedule_id,
                    "hcms_v2_fast_consolidation_task_id": fast_task_id,
                    "hcms_v2_slow_consolidation_task_id": slow_task_id,
                },
            )
        )
        record_memory_diagnostic(
            state,
            component="hcms_v2_consolidation",
            reason="slow_consolidation_scheduled",
            stream_name="slow",
            message="HCMS V2 slow consolidation task scheduled from runtime event capture.",
            metadata={
                "task_id": slow_task_id,
                "schedule_id": str(schedule.schedule_id or ""),
                "capture_envelope_id": str(schedule.capture_envelope_id),
                "observation_id": str(schedule.observation_id),
                "persisted_memory_id": memory.memory_id,
                "target_layer": str(schedule.slow_task.target_layer),
            },
        )
        state.updated_at = now

    def _persist_hcms_v2_slow_consolidation_replay(
        self,
        *,
        namespace: str,
        persisted_memory_id: str,
        replay: Any,
        schedule: Any,
    ) -> tuple[Memory, tuple[str, ...]]:
        candidates = list(getattr(replay, "consolidated_memories", []) or [])
        claim_ids = [
            str(claim.claim_id)
            for claim in getattr(replay, "claims", []) or []
            if str(getattr(claim, "claim_id", "") or "")
        ]
        memories = [
            _memory_from_hcms_v2_consolidated_memory(
                _hcms_v2_slow_replay_candidate_with_metadata(
                    candidate,
                    replay=replay,
                    schedule=schedule,
                    claim_ids=claim_ids,
                ),
                namespace=namespace,
            )
            for candidate in candidates
        ]
        slow_memory_ids = tuple(memory.memory_id for memory in memories)
        now = utc_now()
        with self._mutation_lock:
            state = self.store.load(namespace)
            source_memory = _require_memory(state, persisted_memory_id)
            for memory in memories:
                state = self.compiler.upsert_memory(
                    state,
                    memory,
                    reason="hcms_v2_slow_consolidation_replay",
                )

            parent_version_id = f"{source_memory.memory_id}@v{source_memory.version}"
            source_memory.version += 1
            source_memory.parent_id = parent_version_id
            source_memory.supersedes = [*source_memory.supersedes, parent_version_id]
            source_memory.updated_at = now
            source_memory.metadata = {
                **source_memory.metadata,
                "hcms_v2_slow_consolidated": True,
                "hcms_v2_slow_consolidation_status": str(getattr(replay, "status", "") or ""),
                "hcms_v2_slow_consolidation_replay_id": getattr(replay, "replay_id", None),
                "hcms_v2_slow_consolidated_memory_ids": list(slow_memory_ids),
                "hcms_v2_slow_consolidation_claim_ids": claim_ids,
                "hcms_v2_slow_consolidation_replay": replay.model_dump(mode="json")
                if hasattr(replay, "model_dump")
                else {},
            }
            state.versions.append(
                MemoryVersionRecord(
                    memory_id=source_memory.memory_id,
                    version=source_memory.version,
                    parent_id=source_memory.parent_id,
                    content=source_memory.content,
                    summary=source_memory.summary,
                    reason="hcms_v2_slow_consolidation_replayed",
                    metadata={
                        **source_memory.version_metadata(),
                        "hcms_v2_slow_consolidation_schedule_id": str(getattr(replay, "schedule_id", "") or ""),
                        "hcms_v2_slow_consolidation_task_id": str(getattr(replay, "task_id", "") or ""),
                        "hcms_v2_slow_consolidated_memory_ids": list(slow_memory_ids),
                        "hcms_v2_slow_consolidation_claim_ids": claim_ids,
                    },
                )
            )
            record_memory_diagnostic(
                state,
                component="hcms_v2_consolidation",
                reason="slow_consolidation_replayed",
                stream_name="slow",
                message="HCMS V2 slow consolidation replay persisted from runtime event capture.",
                metadata={
                    "task_id": str(getattr(replay, "task_id", "") or ""),
                    "schedule_id": str(getattr(replay, "schedule_id", "") or ""),
                    "capture_envelope_id": str(getattr(replay, "capture_envelope_id", "") or ""),
                    "observation_id": str(getattr(replay, "observation_id", "") or ""),
                    "persisted_memory_id": persisted_memory_id,
                    "persisted_consolidated_memory_ids": list(slow_memory_ids),
                    "claim_ids": claim_ids,
                    "target_layer": str(getattr(replay, "target_layer", "") or ""),
                    "status": str(getattr(replay, "status", "") or ""),
                },
            )
            self.compiler.weave_relations(state)
            self.compiler._rebuild_entities(state)  # noqa: SLF001 - slow replay keeps graph indexes current.
            self.compiler._rebuild_causal_edges(state)  # noqa: SLF001 - slow replay keeps graph indexes current.
            state.summary = MemorySummary(summary=self.compiler._summary(state), updated_at=now)  # noqa: SLF001
            state.updated_at = now
            self.store.save(namespace, state)
            return next(item for item in state.memories if item.memory_id == persisted_memory_id), slow_memory_ids

    def _apply_hcms_v2_slow_consolidation_replay(
        self,
        state: MemoryState,
        source_memory: Memory,
        *,
        replay: Any,
        schedule: Any,
        slow_memory_ids: tuple[str, ...],
        claim_ids: list[str],
        now,
    ) -> None:
        parent_version_id = f"{source_memory.memory_id}@v{source_memory.version}"
        source_memory.version += 1
        source_memory.parent_id = parent_version_id
        source_memory.supersedes = [*source_memory.supersedes, parent_version_id]
        source_memory.updated_at = now
        source_memory.metadata = {
            **source_memory.metadata,
            "hcms_v2_slow_consolidated": True,
            "hcms_v2_slow_consolidation_status": str(getattr(replay, "status", "") or ""),
            "hcms_v2_slow_consolidation_replay_id": getattr(replay, "replay_id", None),
            "hcms_v2_slow_consolidated_memory_ids": list(slow_memory_ids),
            "hcms_v2_slow_consolidation_claim_ids": claim_ids,
            "hcms_v2_slow_consolidation_replay": replay.model_dump(mode="json")
            if hasattr(replay, "model_dump")
            else {},
        }
        state.versions.append(
            MemoryVersionRecord(
                memory_id=source_memory.memory_id,
                version=source_memory.version,
                parent_id=source_memory.parent_id,
                content=source_memory.content,
                summary=source_memory.summary,
                reason="hcms_v2_slow_consolidation_replayed",
                metadata={
                    **source_memory.version_metadata(),
                    "hcms_v2_slow_consolidation_schedule_id": str(getattr(replay, "schedule_id", "") or ""),
                    "hcms_v2_slow_consolidation_task_id": str(getattr(replay, "task_id", "") or ""),
                    "hcms_v2_slow_consolidated_memory_ids": list(slow_memory_ids),
                    "hcms_v2_slow_consolidation_claim_ids": claim_ids,
                },
            )
        )
        record_memory_diagnostic(
            state,
            component="hcms_v2_consolidation",
            reason="slow_consolidation_replayed",
            stream_name="slow",
            message="HCMS V2 slow consolidation replay persisted from runtime event capture.",
            metadata={
                "task_id": str(getattr(replay, "task_id", "") or ""),
                "schedule_id": str(getattr(replay, "schedule_id", "") or ""),
                "capture_envelope_id": str(getattr(replay, "capture_envelope_id", "") or ""),
                "observation_id": str(getattr(replay, "observation_id", "") or ""),
                "persisted_memory_id": source_memory.memory_id,
                "persisted_consolidated_memory_ids": list(slow_memory_ids),
                "claim_ids": claim_ids,
                "target_layer": str(getattr(replay, "target_layer", "") or ""),
                "status": str(getattr(replay, "status", "") or ""),
            },
        )
        state.updated_at = now

    def _filter_relevance(self, results: list[RetrievalResult]) -> list[RetrievalResult]:
        if self.min_relevance_score <= 0.0:
            return results
        return [result for result in results if result.score >= self.min_relevance_score]

    def create_memory(
        self,
        namespace: str,
        *,
        content: str,
        category: str = "note",
        confidence: float = 0.5,
        salience: float = 0.5,
        source_thread_id: str | None = None,
        evidence_text: str | None = None,
        metadata: dict | None = None,
    ) -> Memory:
        with self._mutation_lock:
            state = self.store.load(namespace)
            normalized_category = _category(category)
            now = utc_now()
            memory_id = stable_id("mem", normalized_category.value, content, size=12)
            source_id = source_thread_id or "manual"
            observation_id = stable_id("obs", source_id, namespace, content, size=16)
            evidence = Evidence(
                evidence_id=stable_id("ev", memory_id, source_id, evidence_text or content[:180], size=12),
                type=EvidenceType.USER_STATED,
                content=evidence_text or content[:180],
                weight=confidence,
                timestamp=now,
                source_id=source_id,
            )
            compiled_content = compile_manual_memory_content(
                content,
                memory_id=memory_id,
                category=normalized_category,
                confidence=confidence,
                created_at=now,
                source_thread_id=source_id,
                observation_id=observation_id,
                evidence=(evidence,),
            )
            memory = Memory(
                memory_id=memory_id,
                content=compiled_content,
                summary=content[:120],
                category=normalized_category,
                confidence=confidence,
                salience=salience,
                created_at=now,
                updated_at=now,
                accessed_at=now,
                source_thread_id=source_id,
                source_type=SourceType.MANUAL,
                evidence=[evidence],
                concepts=list(tokenize(content))[:12],
                metadata={**dict(metadata or {}), "observation_id": observation_id, "raw_content": content},
            )
            state = self.compiler.upsert_memory(state, memory, reason="manual_create")
            self.compiler.weave_relations(state)
            self.compiler._rebuild_entities(state)  # noqa: SLF001 - facade keeps manual writes graph-complete.
            self.compiler._rebuild_causal_edges(state)  # noqa: SLF001 - facade keeps manual writes graph-complete.
            state.summary.summary = self.compiler._summary(state)  # noqa: SLF001 - facade centralizes summary refresh.
            self.store.save(namespace, state)
            return next(item for item in state.memories if item.memory_id == memory.memory_id)

    def update_memory(
        self,
        namespace: str,
        memory_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
        salience: float | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> Memory:
        with self._mutation_lock:
            state = self.store.load(namespace)
            memory = _require_memory(state, memory_id)
            previous_content = memory.content
            previous_evidence_ids = {item.evidence_id for item in memory.evidence}
            next_category = _category(category) if category is not None else memory.category
            next_confidence = max(0.0, min(float(confidence), 1.0)) if confidence is not None else memory.confidence
            next_salience = max(0.0, min(float(salience), 1.0)) if salience is not None else memory.salience
            memory.category = next_category
            memory.confidence = next_confidence
            memory.salience = next_salience
            if content is not None:
                source_id = memory.source_thread_id or "manual"
                observation_id = str(memory.metadata.get("observation_id") or stable_id("obs", source_id, namespace, content, size=16))
                memory.content = compile_manual_memory_content(
                    content,
                    memory_id=memory.memory_id,
                    category=next_category,
                    confidence=next_confidence,
                    created_at=memory.created_at,
                    source_thread_id=source_id,
                    observation_id=observation_id,
                    evidence=memory.evidence,
                )
                memory.summary = content[:120]
                memory.concepts = list(tokenize(content))[:12]
                memory.metadata["observation_id"] = observation_id
                memory.metadata["raw_content"] = content
            for evidence_ref in _normalize_evidence_refs(evidence_refs):
                evidence_id = stable_id("ev", memory.memory_id, evidence_ref, size=12)
                if evidence_id in previous_evidence_ids:
                    continue
                memory.evidence.append(
                    Evidence(
                        evidence_id=evidence_id,
                        type=EvidenceType.REINFORCEMENT,
                        content=evidence_ref,
                        weight=memory.confidence,
                        source_id=evidence_ref,
                    )
                )
                previous_evidence_ids.add(evidence_id)
            previous_version = memory.version
            parent_version_id = f"{memory.memory_id}@v{previous_version}"
            memory.version += 1
            memory.parent_id = parent_version_id
            memory.supersedes = [*memory.supersedes, parent_version_id]
            memory.updated_at = utc_now()
            diff = "\n".join(
                difflib.unified_diff(
                    previous_content.splitlines(),
                    memory.content.splitlines(),
                    fromfile=parent_version_id,
                    tofile=f"{memory.memory_id}@v{memory.version}",
                    lineterm="",
                )
            )
            state.versions.append(
                MemoryVersionRecord(
                    memory_id=memory.memory_id,
                    version=memory.version,
                    parent_id=memory.parent_id,
                    content=memory.content,
                    summary=memory.summary,
                    diff=diff,
                    reason="manual_update",
                    metadata=memory.version_metadata(),
                )
            )
            self.store.save(namespace, state)
            return memory

    def archive_memory(self, namespace: str, memory_id: str) -> Memory:
        with self._mutation_lock:
            state = self.store.load(namespace)
            memory = self.lifecycle.archive(state, memory_id)
            self.store.save(namespace, state)
            return memory

    def restore_memory(self, namespace: str, memory_id: str) -> Memory:
        with self._mutation_lock:
            state = self.store.load(namespace)
            memory = self.lifecycle.restore(state, memory_id)
            self.store.save(namespace, state)
            return memory

    def forget_memory(self, namespace: str, memory_id: str) -> Memory:
        with self._mutation_lock:
            state = self.store.load(namespace)
            memory = self.lifecycle.forget(state, memory_id)
            self.store.save(namespace, state)
            return memory

    def delete_memory(self, namespace: str, memory_id: str) -> None:
        with self._mutation_lock:
            state = self.store.load(namespace)
            before = len(state.memories)
            state.memories = [memory for memory in state.memories if memory.memory_id != memory_id]
            state.relations = [relation for relation in state.relations if memory_id not in {relation.source_memory_id, relation.target_memory_id}]
            state.causal_edges = [edge for edge in state.causal_edges if memory_id not in {edge.source_event, edge.target_event}]
            if len(state.memories) == before:
                raise KeyError(memory_id)
            self.store.save(namespace, state)

    def history(self, namespace: str, memory_id: str):
        state = self.store.load(namespace)
        return tuple(version for version in state.versions if version.memory_id == memory_id)

    def diff(self, namespace: str, memory_id: str) -> str:
        versions = self.history(namespace, memory_id)
        return versions[-1].diff if versions else ""

    def diff_details(self, namespace: str, memory_id: str) -> dict[str, object]:
        versions = self.history(namespace, memory_id)
        if not versions:
            return {
                "memory_id": memory_id,
                "from_version": None,
                "to_version": None,
                "diff": "",
                "confidence_delta": 0.0,
                "evidence_added": [],
                "evidence_removed": [],
            }
        target = versions[-1]
        source = versions[-2] if len(versions) >= 2 else target
        source_evidence = _version_evidence_ids(source)
        target_evidence = _version_evidence_ids(target)
        return {
            "memory_id": memory_id,
            "from_version": source.version,
            "to_version": target.version,
            "diff": target.diff,
            "confidence_delta": round(_version_confidence(target) - _version_confidence(source), 4),
            "evidence_added": sorted(target_evidence - source_evidence),
            "evidence_removed": sorted(source_evidence - target_evidence),
        }

    def _requeue_failed_envelope(self, envelope: MemoryCaptureEnvelope, exc: Exception) -> None:
        attempts = int(envelope.metadata.get("processing_attempts", 0) or 0) + 1
        retry_envelope = envelope.model_copy(
            deep=True,
            update={
                "metadata": {
                    **envelope.metadata,
                    "processing_attempts": attempts,
                    "last_processing_error": type(exc).__name__,
                    "last_processing_error_message": str(exc)[:240],
                    "last_processing_failed_at": utc_now().isoformat(),
                }
            },
        )
        self.queue.enqueue(retry_envelope)

    def _record_capture_failure(
        self,
        envelope: MemoryCaptureEnvelope,
        state: MemoryState | None,
        exc: Exception,
    ) -> None:
        try:
            failed_state = state or self.store.load(envelope.memory_namespace)
            record_memory_diagnostic(
                failed_state,
                component="capture",
                reason="queue_processing_failed",
                error_type=exc.__class__.__name__,
                message="Capture queue processing failed open and envelope was requeued.",
                metadata={"namespace": envelope.memory_namespace},
            )
            self.store.save(envelope.memory_namespace, failed_state)
        except Exception:
            return

    def _degraded_why_path(self, result: RetrievalResult) -> CausalPath:
        memory = result.memory
        if memory is None:
            return CausalPath(
                nodes=[],
                edges=[],
                total_strength=0.0,
                confidence=0.0,
                explanation_kind="degraded",
                degradation_reason="memory_not_found",
                evidence_summary=[],
            )
        degradation_reason = _why_degradation_reason(memory)
        explanation_kind = "correlation" if degradation_reason == "no_causal_path_found" else "degraded"
        return CausalPath(
            nodes=[
                CausalNode(
                    memory_id=memory.memory_id,
                    event_type=memory.category.value,
                    timestamp=memory.created_at,
                    confidence=memory.confidence,
                )
            ],
            edges=[],
            total_strength=result.score,
            confidence=min(memory.confidence, result.score),
            explanation_kind=explanation_kind,
            degradation_reason=degradation_reason,
            evidence_summary=_why_evidence_summary(memory),
        )

    def _extract_text(self, content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(part for part in parts if part)
        return ""

    def _render_causal_chains(self, state: MemoryState, memory_ids: list[str]) -> list[str]:
        memories = {memory.memory_id: memory for memory in state.memories}
        chains: list[str] = []
        for memory_id in memory_ids:
            for path in self.retriever.causal_paths(state, memory_id, max_hops=3):
                labels = [memories[node.memory_id].summary for node in path.nodes if node.memory_id in memories]
                if len(labels) >= 2:
                    chains.append(" -> ".join(labels))
        return chains

    def _counterfactual_anchor(self, state: MemoryState, query: str) -> Memory | None:
        terms = set(tokenize(query))
        if not terms:
            return None
        scored: list[tuple[float, Memory]] = []
        causal_sources = {edge.source_event for edge in state.causal_edges}
        for memory in state.active_memories():
            haystack = " ".join([memory.summary, memory.content, *memory.tags, *memory.entities, *memory.concepts])
            memory_terms = set(tokenize(haystack))
            overlap = len(terms & memory_terms)
            if overlap <= 0:
                continue
            causal_boost = 0.25 if memory.memory_id in causal_sources else 0.0
            score = overlap + causal_boost + memory.confidence * 0.2 + memory.salience * 0.1
            scored.append((score, memory))
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], item[1].confidence, item[1].salience), reverse=True)
        return scored[0][1]

    def _downstream_causal_impacts(self, state: MemoryState, memory_id: str, *, max_depth: int, limit: int):
        memories = {memory.memory_id: memory for memory in state.active_memories()}
        queue: list[tuple[str, int]] = [(memory_id, 0)]
        visited: set[str] = {memory_id}
        impacts = []
        while queue and len(impacts) < limit:
            current, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            outgoing = sorted(
                [edge for edge in state.causal_edges if edge.source_event == current],
                key=lambda edge: edge.strength,
                reverse=True,
            )
            for edge in outgoing:
                if edge.target_event in visited or edge.target_event not in memories:
                    continue
                next_depth = depth + 1
                visited.add(edge.target_event)
                impacts.append((edge, memories[edge.target_event], next_depth))
                queue.append((edge.target_event, next_depth))
                if len(impacts) >= limit:
                    break
        return impacts

    def _project_counterfactual_change(self, anchor: Memory, target: Memory, edge) -> str:
        relation_type = str(edge.metadata.get("relation_type") or "").lower()
        anchor_summary = anchor.summary or anchor.content[:120]
        target_summary = target.summary or target.content[:120]
        if relation_type == "prevents":
            return f"Without '{anchor_summary}', risk around '{target_summary}' would increase because the preventing cause is removed."
        if relation_type == "enables":
            return f"Without '{anchor_summary}', '{target_summary}' would be less enabled and would require different supporting evidence."
        return f"Without '{anchor_summary}', downstream outcome '{target_summary}' would be less likely; related release failures and follow-up decisions would weaken."


def _runtime_event_refs_for_capture(
    *,
    thread_id: str,
    namespace: str,
    trace_id: str | None,
    user_messages: list[str],
    assistant_messages: list[str],
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for event_type, actor, messages in (
        ("user_message", "user", user_messages),
        ("assistant_message", "assistant", assistant_messages),
    ):
        for index, content in enumerate(messages):
            refs.append(
                {
                    "event_id": stable_id("event", thread_id, namespace, trace_id, event_type, index, content, size=16),
                    "event_type": event_type,
                    "source_ref": stable_id("msg", thread_id, event_type, index, content, size=12),
                    "payload_summary": sanitize_memory_context_text(content)[:240],
                    "actor": actor,
                    "privacy_level": "project",
                    "trust_level": "local_runtime",
                }
            )
    return refs


def _runtime_event_metadata(event: object) -> dict[str, Any]:
    raw_metadata = _event_get(event, "metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    for key in ("tool_name", "tool_call_id", "status", "compacted", "raw_size_chars", "summary_size_chars"):
        if key not in metadata:
            value = _event_get(event, key)
            if value is not None:
                metadata[key] = value
    return metadata


def _runtime_event_memory_metadata(
    capture: Any,
    *,
    event_metadata: dict[str, Any],
    persisted_memory_id: str,
) -> dict[str, Any]:
    envelope = capture.envelope
    observation = capture.observation
    runtime_event = envelope.runtime_events[0] if envelope.runtime_events else None
    metadata = {
        "layer": "episodic",
        "hcms_layer": "episodic",
        "layer_id": "episodic",
        "store_id": "hcms_episodic",
        "hcms_v2": True,
        "persisted_memory_id": persisted_memory_id,
        "capture_envelope_id": envelope.envelope_id,
        "hcms_v2_observation_id": observation.observation_id,
        "observation_id": observation.observation_id,
        "event_id": observation.event_id,
        "event_type": observation.observation_type,
        "source_event_id": envelope.metadata.get("source_event_id") or observation.event_id,
        "source_event_type": envelope.metadata.get("source_event_type") or observation.observation_type,
        "tool_result_refs": list(envelope.tool_result_refs),
        "workspace_refs": list(observation.workspace_refs),
        "content_ref": observation.content_ref,
        "payload_ref": runtime_event.payload_ref if runtime_event is not None else observation.content_ref,
        "source_ref": runtime_event.source_ref if runtime_event is not None else observation.source_id,
        "run_id": observation.run_id,
        "turn_id": envelope.turn_id,
        "thread_id": observation.thread_id or envelope.thread_id,
        "privacy_level": observation.privacy_level,
        "trust_level": observation.trust_level,
        "redaction_state": observation.redaction_state,
        "guard_decision_id": capture.guard_decision.decision_id,
        "guard_action": capture.guard_decision.action,
        "guard_reasons": list(capture.guard_decision.reasons),
        "guard_trust_score": capture.guard_decision.trust_score,
    }
    for key in ("tool_name", "tool_call_id", "status", "compacted", "raw_size_chars", "summary_size_chars"):
        if key in event_metadata:
            metadata[key] = event_metadata[key]
    return metadata


def _runtime_event_memory_tags(event_type: str, event_metadata: dict[str, Any]) -> list[str]:
    tags = ["hcms_v2", "episodic", str(event_type or "runtime_event")]
    tool_name = str(event_metadata.get("tool_name") or "").strip()
    if tool_name:
        tags.append(tool_name)
    if event_metadata.get("compacted") is True:
        tags.append("compacted")
    return list(dict.fromkeys(tags))[:12]


def _workspace_state_has_signal(workspace_state: object) -> bool:
    return bool(
        _event_get(workspace_state, "active_files")
        or _event_get(workspace_state, "variables")
        or _event_get(workspace_state, "intermediate_results")
    )


def _memory_from_workspace_state(consolidated: Any, *, namespace: str) -> Memory:
    metadata = dict(getattr(consolidated, "metadata", {}) or {})
    workspace_ref = str(metadata.get("workspace_state_ref") or getattr(consolidated, "memory_id", "") or "workspace")
    memory_id = stable_id("mem", "workspace_state", namespace, workspace_ref, size=12)
    now = getattr(consolidated, "updated_at", None) or utc_now()
    evidence_span = consolidated.evidence[0] if consolidated.evidence else None
    evidence_id_seed = getattr(evidence_span, "evidence_id", None) or workspace_ref
    evidence = Evidence(
        evidence_id=stable_id("ev", memory_id, evidence_id_seed, size=12),
        type=EvidenceType.OBSERVATION,
        content=(getattr(evidence_span, "excerpt", None) or consolidated.summary or consolidated.canonical_content)[:180],
        weight=float(getattr(consolidated, "confidence", 0.75) or 0.75),
        timestamp=getattr(evidence_span, "timestamp", None) or now,
        source_id=getattr(evidence_span, "observation_id", None) or workspace_ref,
        metadata={
            "hcms_v2_evidence_id": getattr(evidence_span, "evidence_id", None),
            "hcms_v2_memory_id": consolidated.memory_id,
            "source_uri": getattr(evidence_span, "source_uri", None),
            "source_label": getattr(evidence_span, "source_label", None),
            "workspace_state_ref": workspace_ref,
        },
    )
    working_metadata = {
        **metadata,
        "layer": "working",
        "hcms_layer": "working",
        "layer_id": "working",
        "store_id": "hcms_working",
        "hcms_v2": True,
        "hcms_v2_memory_id": consolidated.memory_id,
        "persisted_memory_id": memory_id,
        "workspace_state_ref": workspace_ref,
        "source": "workspace_state",
        "source_kind": "workspace_state",
    }
    thread_id = str(metadata.get("thread_id") or "").strip() or None
    content = sanitize_memory_context_text(consolidated.canonical_content or consolidated.summary or workspace_ref)
    summary = sanitize_memory_context_text(consolidated.summary or content[:240])[:240]
    return Memory(
        memory_id=memory_id,
        content=content,
        summary=summary,
        category=MemoryCategory.CONTEXT,
        confidence=float(getattr(consolidated, "confidence", 0.75) or 0.75),
        salience=float(getattr(consolidated, "salience", 0.55) or 0.55),
        evidence=[evidence],
        tags=["hcms_v2", "working", "workspace_state"],
        concepts=list(tokenize(f"{summary} {content}"))[:12],
        created_at=getattr(consolidated, "created_at", None) or now,
        updated_at=now,
        accessed_at=now,
        source_thread_id=thread_id,
        source_type=SourceType.OBSERVATION,
        metadata=working_metadata,
    )


def _memory_from_hcms_v2_consolidated_memory(consolidated: Any, *, namespace: str) -> Memory:
    metadata = dict(getattr(consolidated, "metadata", {}) or {})
    layer = str(getattr(consolidated, "layer", "") or "semantic").strip().lower()
    hcms_memory_id = str(getattr(consolidated, "memory_id", "") or stable_id("hcms_v2", namespace, layer, size=12))
    memory_id = stable_id("mem", layer, namespace, hcms_memory_id, size=12)
    now = getattr(consolidated, "updated_at", None) or utc_now()
    content = sanitize_memory_context_text(
        getattr(consolidated, "canonical_content", None)
        or getattr(consolidated, "summary", None)
        or hcms_memory_id
    )
    summary = sanitize_memory_context_text(getattr(consolidated, "summary", None) or content[:240])[:240]
    confidence = max(0.0, min(float(getattr(consolidated, "confidence", 0.5) or 0.5), 1.0))
    salience = max(0.0, min(float(getattr(consolidated, "salience", 0.5) or 0.5), 1.0))
    category = _hcms_v2_memory_category(layer, str(getattr(consolidated, "category", "") or ""), metadata)
    evidence_span = consolidated.evidence[0] if getattr(consolidated, "evidence", None) else None
    source_id = getattr(evidence_span, "observation_id", None) or metadata.get("hcms_v2_capability_usage_id") or hcms_memory_id
    evidence = Evidence(
        evidence_id=stable_id("ev", memory_id, getattr(evidence_span, "evidence_id", None) or source_id, size=12),
        type=EvidenceType.PATTERN,
        content=sanitize_memory_context_text(
            getattr(evidence_span, "excerpt", None) or summary or content[:180]
        )[:180],
        weight=float(getattr(evidence_span, "trust_score", None) or confidence),
        timestamp=getattr(evidence_span, "timestamp", None) or now,
        source_id=str(source_id),
        metadata={
            "hcms_v2_evidence_id": getattr(evidence_span, "evidence_id", None),
            "hcms_v2_memory_id": hcms_memory_id,
            "source_uri": getattr(evidence_span, "source_uri", None),
            "source_label": getattr(evidence_span, "source_label", None),
            "collector": getattr(evidence_span, "collector", None),
            "capability_usage_id": metadata.get("hcms_v2_capability_usage_id"),
            "context_block_refs": list(metadata.get("context_block_refs") or []),
        },
    )
    memory_metadata = {
        **metadata,
        "layer": layer,
        "hcms_layer": layer,
        "layer_id": layer,
        "store_id": f"hcms_{layer}",
        "hcms_v2": True,
        "hcms_v2_memory_id": hcms_memory_id,
        "persisted_memory_id": memory_id,
        "source": metadata.get("source") or "procedure_wisdom_miner",
        "source_kind": metadata.get("source_kind") or "capability_usage",
    }
    concepts_seed = " ".join(
        str(value or "")
        for value in (
            summary,
            content,
            metadata.get("capability_id"),
            metadata.get("capability_kind"),
            metadata.get("tool_name"),
            metadata.get("mcp_server_id"),
            " ".join(str(item) for item in metadata.get("skill_ids") or []),
        )
    )
    return Memory(
        memory_id=memory_id,
        content=content,
        summary=summary,
        category=category,
        confidence=confidence,
        salience=salience,
        evidence=[evidence],
        tags=_hcms_v2_consolidated_memory_tags(layer, category, memory_metadata),
        concepts=list(tokenize(concepts_seed))[:12],
        created_at=getattr(consolidated, "created_at", None) or now,
        updated_at=now,
        accessed_at=now,
        source_thread_id=str(metadata.get("turn_id") or "").strip() or None,
        source_type=SourceType.INFERENCE,
        metadata=memory_metadata,
    )


def _hcms_v2_memory_category(layer: str, category: str, metadata: Mapping[str, Any]) -> MemoryCategory:
    if layer in {"procedural", "procedure"}:
        return MemoryCategory.PROCEDURE
    if layer == "wisdom":
        insight_type = str(metadata.get("insight_type") or "").lower()
        if "failure" in insight_type or metadata.get("error_type"):
            return MemoryCategory.ERROR_PATTERN
        return MemoryCategory.DECISION
    return _category(category or "context")


def _hcms_v2_consolidated_memory_tags(
    layer: str,
    category: MemoryCategory,
    metadata: Mapping[str, Any],
) -> list[str]:
    source_kind = str(metadata.get("source_kind") or "").strip()
    tags = ["hcms_v2", layer, category.value]
    if source_kind:
        tags.append(source_kind)
    for key in ("source", "tool_name", "mcp_server_id", "capability_kind"):
        value = str(metadata.get(key) or "").strip()
        if value:
            tags.append(value)
    for skill_id in metadata.get("skill_ids") or []:
        text = str(skill_id or "").strip()
        if text:
            tags.append(text)
    return list(dict.fromkeys(tags))[:12]


def _hcms_v2_slow_replay_candidate_with_metadata(
    candidate: Any,
    *,
    replay: Any,
    schedule: Any,
    claim_ids: list[str],
) -> Any:
    metadata = {
        **dict(getattr(candidate, "metadata", {}) or {}),
        "source": "hcms_v2_slow_consolidation_replay",
        "source_kind": "runtime_event_slow_consolidation",
        "claim_ids": list(claim_ids),
        "hcms_v2_claim_ids": list(claim_ids),
        "hcms_v2_slow_consolidation_replay_id": getattr(replay, "replay_id", None),
        "hcms_v2_slow_consolidation_status": str(getattr(replay, "status", "") or ""),
        "hcms_v2_consolidation_schedule_id": str(getattr(schedule, "schedule_id", "") or ""),
        "hcms_v2_fast_consolidation_task_id": str(getattr(getattr(schedule, "fast_task", None), "task_id", "") or ""),
        "hcms_v2_slow_consolidation_task_id": str(getattr(replay, "task_id", "") or ""),
    }
    if hasattr(candidate, "model_copy"):
        return candidate.model_copy(update={"metadata": metadata})
    return candidate


def _upsert_workspace_snapshot_memory(state: MemoryState, memory: Memory, *, reason: str) -> MemoryState:
    now = utc_now()
    for index, previous in enumerate(state.memories):
        if previous.memory_id != memory.memory_id:
            continue
        updated = memory.model_copy(deep=True)
        parent_version_id = f"{previous.memory_id}@v{previous.version}"
        updated.version = previous.version + 1
        updated.parent_id = parent_version_id
        updated.supersedes = [*previous.supersedes, parent_version_id]
        updated.created_at = previous.created_at
        updated.access_count = previous.access_count
        updated.accessed_at = now
        updated.updated_at = now
        state.memories[index] = updated
        state.versions.append(
            MemoryVersionRecord(
                memory_id=updated.memory_id,
                version=updated.version,
                parent_id=updated.parent_id,
                content=updated.content,
                summary=updated.summary,
                diff="\n".join(
                    difflib.unified_diff(
                        previous.content.splitlines(),
                        updated.content.splitlines(),
                        fromfile=f"{previous.memory_id}@v{previous.version}",
                        tofile=f"{updated.memory_id}@v{updated.version}",
                        lineterm="",
                    )
                ),
                reason=reason,
                metadata=updated.version_metadata(),
            )
        )
        state.updated_at = now
        return state

    memory.updated_at = now
    state.memories.append(memory)
    state.versions.append(
        MemoryVersionRecord(
            memory_id=memory.memory_id,
            version=memory.version,
            parent_id=memory.parent_id,
            content=memory.content,
            summary=memory.summary,
            diff=memory.content,
            reason=reason,
            metadata=memory.version_metadata(),
        )
    )
    state.updated_at = now
    return state


def _event_get(value: object, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _category(value: str) -> MemoryCategory:
    try:
        return MemoryCategory(str(value or "note").strip().lower())
    except ValueError:
        return MemoryCategory.NOTE


def _require_memory(state: MemoryState, memory_id: str) -> Memory:
    for memory in state.memories:
        if memory.memory_id == memory_id:
            return memory
    raise KeyError(memory_id)


def _normalize_evidence_refs(values: Iterable[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in result:
            result.append(value[:240])
    return tuple(result)


def _causal_query_direction(query: str) -> str:
    lowered = str(query or "").lower()
    effect_markers = (
        "effect",
        "impact",
        "consequence",
        "what happened after",
        "what followed",
        "影响",
        "后果",
        "结果",
    )
    return "effects" if any(marker in lowered for marker in effect_markers) else "causes"


def _why_degradation_reason(memory: Memory) -> str:
    if memory.confidence < 0.5:
        return "low_confidence_evidence"
    text = " ".join(
        str(part or "")
        for part in [memory.summary, memory.content, memory.reasoning, *(item.content for item in memory.evidence[:4])]
    ).lower()
    conflict_markers = (
        "conflict",
        "conflicts",
        "conflicting",
        "contradict",
        "contradicts",
        "contradiction",
        "wrong",
        "must not",
        "do not",
        "矛盾",
        "冲突",
        "错误",
        "不要",
        "不能",
    )
    if any(marker in text for marker in conflict_markers):
        return "conflicting_evidence"
    return "no_causal_path_found"


def _why_evidence_summary(memory: Memory) -> list[str]:
    summaries: list[str] = []
    for evidence in memory.evidence[:3]:
        value = sanitize_memory_context_text(evidence.content).strip()
        if value:
            summaries.append(value[:180])
    if summaries:
        return summaries
    fallback = sanitize_memory_context_text(memory.summary or memory.content).strip()
    return [fallback[:180]] if fallback else []


def _version_confidence(record: MemoryVersionRecord) -> float:
    try:
        return float(record.metadata.get("confidence", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _version_evidence_ids(record: MemoryVersionRecord) -> set[str]:
    raw = record.metadata.get("evidence_ids", ())
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(item) for item in raw if str(item or "").strip()}
