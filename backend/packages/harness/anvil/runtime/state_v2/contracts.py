from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ConfigDict, Field

from anvil.runtime.context_v2 import (
    AssembledContext,
    CompressionPolicy,
    ContextBlock,
    ContextAssemblerV2,
    ContextSource,
    ContextSourceKind,
    AttentionBudget,
    InjectionPolicy,
    bounded_score,
    stable_context_id,
    tool_result_to_block,
    workspace_text_to_block,
)
from anvil.runtime.token_budget import TokenBudgetService


class GoalFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: str
    title: str
    status: str = "active"
    summary: str = ""
    blockers: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    priority: float = 0.5
    metadata: dict[str, Any] = Field(default_factory=dict)


class SalienceRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str
    goal_stack_ref: str
    active_goal_id: str | None = None
    memory_query: str
    boost_terms: dict[str, float] = Field(default_factory=dict)
    blocker_terms: list[str] = Field(default_factory=list)
    next_action_terms: list[str] = Field(default_factory=list)
    suppressed_goal_refs: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class SalienceRouter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router_id: str
    thread_id: str
    route_limit: int = 3
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    def route_goal_stack(
        self,
        goal_stack: "GoalStack",
        *,
        query: str | None = None,
        token_budget: TokenBudgetService | None = None,
    ) -> SalienceRoute:
        counter = token_budget or TokenBudgetService()
        base_route = goal_stack.to_salience_route(token_budget=counter)
        raw_query = str(query or "").strip()
        bounded_query = counter.truncate_text(raw_query, max_tokens=80, max_chars=560) if raw_query else ""
        memory_query = base_route.memory_query
        if bounded_query:
            memory_query = counter.truncate_text(
                f"current_query={bounded_query}\n{base_route.memory_query}",
                max_tokens=260,
                max_chars=1800,
            )
        diagnostics = dict(base_route.diagnostics)
        diagnostics.update(
            {
                "router_id": self.router_id,
                "thread_id": self.thread_id,
                "route_limit": self.route_limit,
                "query_tokens": counter.count_text(bounded_query),
                "query_bounded": bool(raw_query and bounded_query != raw_query),
            }
        )
        diagnostics.update(self.diagnostics)
        return base_route.model_copy(
            update={
                "route_id": stable_context_id(
                    "salience-route",
                    self.router_id,
                    goal_stack.stack_id,
                    base_route.active_goal_id,
                    memory_query,
                ),
                "memory_query": memory_query,
                "diagnostics": diagnostics,
            }
        )


class GoalStack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stack_id: str
    thread_id: str
    active_goal_id: str | None = None
    goals: list[GoalFrame] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    def to_salience_route(self, *, token_budget: TokenBudgetService | None = None) -> SalienceRoute:
        counter = token_budget or TokenBudgetService()
        active_goals = [goal for goal in self.goals if _goal_is_active(goal)]
        active_goal = self._active_goal(active_goals)
        completed_goals = [goal for goal in self.goals if _goal_is_suppressed(goal)]
        route_goals = [active_goal] if active_goal is not None else active_goals[:3]
        lines = [
            f"goal_stack_id={self.stack_id}",
            f"thread_id={self.thread_id}",
        ]
        boost_terms: dict[str, float] = {}
        blocker_terms: list[str] = []
        next_action_terms: list[str] = []
        for goal in route_goals:
            if goal is None:
                continue
            priority = bounded_score(goal.priority, default=0.5)
            lines.append(f"goal={goal.title}")
            lines.append(f"status={goal.status}")
            if goal.summary:
                lines.append(f"summary={counter.truncate_text(goal.summary, max_tokens=60, max_chars=360)}")
            if goal.blockers:
                lines.append("blockers:")
                for blocker in goal.blockers[:6]:
                    text = _bounded_line(blocker, counter)
                    blocker_terms.append(text)
                    lines.append(f"- {text}")
            if goal.next_actions:
                lines.append("next_actions:")
                for action in goal.next_actions[:6]:
                    text = _bounded_line(action, counter)
                    next_action_terms.append(text)
                    lines.append(f"- {text}")
            for keyword in goal.keywords[:12]:
                term = str(keyword or "").strip()
                if term:
                    boost_terms[term] = max(boost_terms.get(term, 0.0), priority)
        memory_query = counter.truncate_text("\n".join(lines), max_tokens=220, max_chars=1600)
        return SalienceRoute(
            route_id=stable_context_id("salience-route", self.stack_id, self.active_goal_id, memory_query),
            goal_stack_ref=self.stack_id,
            active_goal_id=active_goal.goal_id if active_goal is not None else self.active_goal_id,
            memory_query=memory_query,
            boost_terms=boost_terms,
            blocker_terms=blocker_terms,
            next_action_terms=next_action_terms,
            suppressed_goal_refs=[goal.goal_id for goal in completed_goals],
            diagnostics={
                "goal_count": len(self.goals),
                "active_goal_count": len(active_goals),
                "suppressed_completed_goals": len(completed_goals),
            },
        )

    def to_context_blocks(self, *, token_budget: TokenBudgetService | None = None) -> list[ContextBlock]:
        route = self.to_salience_route(token_budget=token_budget)
        if not route.memory_query:
            return []
        block = workspace_text_to_block(
            route.memory_query,
            name="goal_stack",
            token_budget=token_budget,
        )
        return [
            block.model_copy(
                update={
                    "block_type": "goal_stack",
                    "title": "GoalStack",
                    "priority": 0.82,
                    "salience": 0.88,
                    "position_hint": "workspace:goal_stack",
                    "tags": ("goal_stack", "salience_route"),
                    "metadata": {
                        "goal_stack_ref": self.stack_id,
                        "active_goal_id": route.active_goal_id,
                        "route_id": route.route_id,
                        "boost_terms": route.boost_terms,
                        "suppressed_goal_refs": route.suppressed_goal_refs,
                    },
                }
            )
        ]

    def _active_goal(self, active_goals: list[GoalFrame]) -> GoalFrame | None:
        if self.active_goal_id:
            for goal in active_goals:
                if goal.goal_id == self.active_goal_id:
                    return goal
        return active_goals[0] if active_goals else None


class WorkspaceIntermediateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_ref: str
    tool_result_id: str
    tool_name: str
    summary: str
    raw_ref: str | None = None
    status: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    thread_id: str
    project_root: str | None = None
    active_files: list[str] = Field(default_factory=list)
    variables: dict[str, str] = Field(default_factory=dict)
    intermediate_results: list[WorkspaceIntermediateResult] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    def add_tool_result(self, record: "ToolResultRecord") -> WorkspaceIntermediateResult:
        for existing in self.intermediate_results:
            if existing.tool_result_id == record.result_id:
                return existing
        result = WorkspaceIntermediateResult(
            result_ref=stable_context_id("workspace-result", self.thread_id, record.result_id),
            tool_result_id=record.result_id,
            tool_name=record.tool_name,
            summary=record.summary,
            raw_ref=record.raw_ref,
            status=record.status,
            metadata={
                "tool_call_id": record.tool_call_id,
                "run_id": record.run_id,
                "turn_id": record.turn_id,
            },
        )
        self.intermediate_results.append(result)
        self.diagnostics["intermediate_result_count"] = len(self.intermediate_results)
        return result

    def to_context_blocks(self, *, token_budget: TokenBudgetService | None = None) -> list[ContextBlock]:
        if not self.intermediate_results and not self.active_files and not self.variables:
            return []
        lines = [
            f"workspace_id={self.workspace_id}",
            f"thread_id={self.thread_id}",
        ]
        if self.project_root:
            lines.append(f"project_root={self.project_root}")
        if self.active_files:
            lines.append("active_files:")
            lines.extend(f"- {path}" for path in self.active_files[:12])
        if self.variables:
            lines.append("variables:")
            lines.extend(f"- {key}={value}" for key, value in sorted(self.variables.items())[:12])
        if self.intermediate_results:
            lines.append("intermediate_results:")
            for item in self.intermediate_results[-8:]:
                raw_ref = f" raw_ref={item.raw_ref}" if item.raw_ref else ""
                lines.append(
                    f"- result_id={item.tool_result_id} tool={item.tool_name} "
                    f"status={item.status}{raw_ref} summary={item.summary}"
                )
        return [
            workspace_text_to_block(
                "\n".join(lines),
                name="workspace_state",
                token_budget=token_budget,
            )
        ]


class ScratchpadEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    kind: str
    summary: str
    status: str = "active"
    priority: float = 0.5
    source_refs: list[str] = Field(default_factory=list)
    raw_ref: str | None = None
    raw_size_chars: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class Scratchpad(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scratchpad_id: str
    thread_id: str
    entries: list[ScratchpadEntry] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    def add_entry(
        self,
        *,
        kind: str,
        summary: str,
        status: str = "active",
        priority: float = 0.5,
        source_refs: list[str] | tuple[str, ...] = (),
        raw_ref: str | None = None,
        raw_detail: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        token_budget: TokenBudgetService | None = None,
    ) -> ScratchpadEntry:
        counter = token_budget or TokenBudgetService()
        bounded_summary = counter.truncate_text(str(summary or "").strip(), max_tokens=80, max_chars=480)
        raw_size = len(str(raw_detail or ""))
        entry = ScratchpadEntry(
            entry_id=stable_context_id("scratchpad-entry", self.thread_id, kind, bounded_summary, raw_ref),
            kind=str(kind or "note").strip() or "note",
            summary=bounded_summary,
            status=str(status or "active").strip() or "active",
            priority=bounded_score(priority, default=0.5),
            source_refs=_string_list(tuple(source_refs), limit=16),
            raw_ref=raw_ref,
            raw_size_chars=raw_size,
            metadata=dict(metadata or {}),
        )
        for index, existing in enumerate(self.entries):
            if existing.entry_id == entry.entry_id:
                self.entries[index] = entry
                break
        else:
            self.entries.append(entry)
        self._refresh_diagnostics()
        return entry

    def to_context_blocks(self, *, token_budget: TokenBudgetService | None = None) -> list[ContextBlock]:
        active_entries = [entry for entry in self.entries if _scratchpad_entry_active(entry)]
        if not active_entries:
            return []
        counter = token_budget or TokenBudgetService()
        selected = sorted(active_entries, key=lambda entry: entry.priority, reverse=True)[:12]
        lines = [
            f"scratchpad_id={self.scratchpad_id}",
            f"thread_id={self.thread_id}",
        ]
        raw_refs: list[str] = []
        source_refs: list[str] = []
        for entry in selected:
            raw_fragment = f" raw_ref={entry.raw_ref}" if entry.raw_ref else ""
            lines.append(
                f"- entry_id={entry.entry_id} kind={entry.kind} status={entry.status} "
                f"priority={entry.priority:.2f}{raw_fragment} summary={entry.summary}"
            )
            if entry.raw_ref:
                raw_refs.append(entry.raw_ref)
            source_refs.extend(entry.source_refs)
        content = counter.truncate_text("\n".join(lines), max_tokens=260, max_chars=1800)
        block = workspace_text_to_block(
            content,
            name="scratchpad",
            token_budget=token_budget,
        )
        return [
            block.model_copy(
                update={
                    "block_type": "scratchpad",
                    "source": ContextSource(
                        kind=ContextSourceKind.WORKSPACE,
                        name="scratchpad",
                        ref=self.scratchpad_id,
                        trust_level="runtime",
                        metadata={"thread_id": self.thread_id},
                    ),
                    "title": "Scratchpad",
                    "priority": max((entry.priority for entry in selected), default=0.5),
                    "salience": 0.78,
                    "position_hint": "workspace:scratchpad",
                    "compression_policy": CompressionPolicy(
                        allow_compression=True,
                        allow_reference=bool(raw_refs),
                        ref=raw_refs[0] if raw_refs else None,
                    ),
                    "tags": ("scratchpad", "workspace_state"),
                    "metadata": {
                        "scratchpad_id": self.scratchpad_id,
                        "thread_id": self.thread_id,
                        "entry_refs": [entry.entry_id for entry in selected],
                        "raw_refs": raw_refs,
                        "source_refs": _string_list(tuple(source_refs), limit=24),
                    },
                }
            )
        ]

    def _refresh_diagnostics(self) -> None:
        active_entries = [entry for entry in self.entries if _scratchpad_entry_active(entry)]
        self.diagnostics["entry_count"] = len(self.entries)
        self.diagnostics["active_entry_count"] = len(active_entries)
        self.diagnostics["raw_ref_count"] = len([entry for entry in self.entries if entry.raw_ref])


class ToolResultRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    tool_name: str
    tool_call_id: str | None = None
    capability_id: str | None = None
    run_id: str | None = None
    turn_id: str
    status: str = "unknown"
    summary: str
    raw_ref: str | None = None
    raw_size_chars: int = 0
    summary_size_chars: int = 0
    compacted: bool = False
    workspace_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    sequence: int = 0
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str = "runtime"
    thread_id: str
    run_id: str | None = None
    turn_id: str | None = None
    source_kind: str = "runtime"
    source_ref: str | None = None
    payload_ref: str | None = None
    payload_summary: str
    privacy_level: str = "project"
    trust_level: str = "local_runtime"
    trace_id: str | None = None
    tool_result_refs: list[str] = Field(default_factory=list)
    workspace_refs: list[str] = Field(default_factory=list)
    goal_stack_ref: str | None = None
    capability_usage_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def type(self) -> str:
        return self.event_type


class TurnPipelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    thread_id: str
    run_id: str | None = None
    turn_id: str
    user_text: str
    goal_stack: GoalStack | None = None
    salience_route: SalienceRoute | None = None
    workspace_state: WorkspaceState | None = None
    scratchpad: Scratchpad | None = None
    tool_result_store: "ToolResultStore | None" = None
    review_inbox: "ReviewInbox | None" = None
    extra_blocks: list[ContextBlock] = Field(default_factory=list)
    budget: AttentionBudget = Field(default_factory=AttentionBudget)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    thread_id: str
    run_id: str | None = None
    user_message_ref: str
    user_text_summary: str
    context_trace_id: str | None = None
    phase_statuses: dict[str, str] = Field(default_factory=dict)
    candidate_block_ids: list[str] = Field(default_factory=list)
    selected_block_ids: list[str] = Field(default_factory=list)
    tool_result_refs: list[str] = Field(default_factory=list)
    workspace_refs: list[str] = Field(default_factory=list)
    scratchpad_entry_refs: list[str] = Field(default_factory=list)
    goal_stack_ref: str | None = None
    review_inbox_refs: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TurnPipelineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_state: TurnState
    candidate_blocks: tuple[ContextBlock, ...]
    assembled_context: AssembledContext
    events: tuple[RuntimeEvent, ...] = ()


class TurnPipeline:
    def __init__(
        self,
        *,
        event_bus: "RuntimeEventBus | None" = None,
        assembler: ContextAssemblerV2 | None = None,
        token_budget: TokenBudgetService | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.assembler = assembler or ContextAssemblerV2()
        self.token_budget = token_budget or TokenBudgetService()

    def prepare_llm_context(self, request: TurnPipelineInput) -> TurnPipelineResult:
        turn_state = self._intake(request)
        emitted: list[RuntimeEvent] = []
        intake_event = self._publish(
            RuntimeEvent(
                event_id=stable_context_id("event", request.thread_id, request.turn_id, "user_message_received"),
                event_type="user_message_received",
                actor="user",
                thread_id=request.thread_id,
                run_id=request.run_id,
                turn_id=request.turn_id,
                source_kind="user_message",
                source_ref=turn_state.user_message_ref,
                payload_summary=turn_state.user_text_summary,
                metadata={"phase": "intake"},
            )
        )
        emitted.append(intake_event)

        local_salience_route = request.salience_route
        if local_salience_route is None and request.goal_stack is not None:
            local_salience_route = SalienceRouter(
                router_id=f"salience-router:{request.thread_id}",
                thread_id=request.thread_id,
            ).route_goal_stack(
                request.goal_stack,
                query=request.user_text,
                token_budget=self.token_budget,
            )
        candidate_blocks = tuple(self._collect_candidate_blocks(request, turn_state))
        assembled = self.assembler.assemble(
            candidate_blocks,
            budget=request.budget,
            salience_route=local_salience_route,
            trace_metadata={
                "thread_id": request.thread_id,
                "run_id": request.run_id,
                "turn_id": request.turn_id,
                "pipeline": "runtime_context_v2_turn_pipeline",
                "phase": "context_assembly",
                **dict(request.metadata),
            },
        )
        turn_state.context_trace_id = assembled.trace.trace_id
        turn_state.phase_statuses["context_assembly"] = "completed"
        turn_state.candidate_block_ids = list(assembled.trace.candidate_block_ids)
        turn_state.selected_block_ids = list(assembled.trace.selected_block_ids)
        turn_state.diagnostics.update(
            {
                "candidate_block_count": len(candidate_blocks),
                "selected_block_count": len(assembled.blocks),
                "dropped_block_count": len(assembled.trace.dropped_block_ids),
                "assembled_context_tokens": assembled.trace.total_tokens,
                "fallback_used": assembled.fallback_used,
                "tool_result_count": len(request.tool_result_store.records)
                if request.tool_result_store is not None
                else 0,
                "workspace_block_count": len(assembled.trace.selected_workspace),
                "event_block_count": len(assembled.trace.selected_events),
            }
        )
        context_event = self._publish(
            RuntimeEvent(
                event_id=stable_context_id("event", request.thread_id, request.turn_id, "context_assembled"),
                event_type="context_assembled",
                actor="runtime",
                thread_id=request.thread_id,
                run_id=request.run_id,
                turn_id=request.turn_id,
                source_kind="runtime_context_v2",
                source_ref=assembled.trace.trace_id,
                payload_summary=(
                    f"assembled {len(assembled.blocks)} of {len(candidate_blocks)} context blocks "
                    f"using {assembled.trace.total_tokens} tokens"
                ),
                trace_id=assembled.trace.trace_id,
                tool_result_refs=turn_state.tool_result_refs,
                workspace_refs=turn_state.workspace_refs,
                goal_stack_ref=turn_state.goal_stack_ref,
                metadata={
                    "phase": "context_assembly",
                    "candidate_block_count": len(candidate_blocks),
                    "selected_block_count": len(assembled.blocks),
                    "selected_block_ids": list(assembled.trace.selected_block_ids),
                    "layer_token_usage": assembled.trace.layer_token_usage,
                },
            )
        )
        emitted.append(context_event)
        return TurnPipelineResult(
            turn_state=turn_state,
            candidate_blocks=candidate_blocks,
            assembled_context=assembled,
            events=tuple(emitted),
        )

    def _intake(self, request: TurnPipelineInput) -> TurnState:
        summary = self.token_budget.truncate_text(request.user_text, max_tokens=120, max_chars=800)
        phase_statuses = {
            "intake": "completed",
            "intent_profiling": "pending",
            "query_planning": "pending",
            "parallel_resource_retrieval": "pending",
            "attention_budgeting": "completed",
            "context_assembly": "pending",
            "llm_call": "pending",
            "action_dispatch": "pending",
            "observation_handling": "pending",
            "state_update": "pending",
            "memory_capture": "pending",
            "maintenance_scheduling": "pending",
        }
        return TurnState(
            turn_id=request.turn_id,
            thread_id=request.thread_id,
            run_id=request.run_id,
            user_message_ref=stable_context_id(
                "user-message",
                request.thread_id,
                request.turn_id,
                summary,
            ),
            user_text_summary=summary,
            phase_statuses=phase_statuses,
            diagnostics={
                "user_text_chars": len(request.user_text),
                "user_text_tokens": self.token_budget.count_text(request.user_text),
            },
        )

    def _collect_candidate_blocks(
        self,
        request: TurnPipelineInput,
        turn_state: TurnState,
    ) -> list[ContextBlock]:
        blocks = [_user_message_to_block(request, turn_state, self.token_budget)]
        if request.goal_stack is not None:
            blocks.extend(request.goal_stack.to_context_blocks(token_budget=self.token_budget))
            turn_state.goal_stack_ref = request.goal_stack.stack_id
        if request.workspace_state is not None:
            blocks.extend(request.workspace_state.to_context_blocks(token_budget=self.token_budget))
            turn_state.workspace_refs.append(request.workspace_state.workspace_id)
            turn_state.workspace_refs.extend(
                item.result_ref for item in request.workspace_state.intermediate_results if item.result_ref
            )
        if request.scratchpad is not None:
            blocks.extend(request.scratchpad.to_context_blocks(token_budget=self.token_budget))
            turn_state.scratchpad_entry_refs = [
                entry.entry_id for entry in request.scratchpad.entries if _scratchpad_entry_active(entry)
            ]
            if request.scratchpad.scratchpad_id not in turn_state.workspace_refs:
                turn_state.workspace_refs.append(request.scratchpad.scratchpad_id)
        if request.tool_result_store is not None:
            blocks.extend(request.tool_result_store.to_context_blocks(token_budget=self.token_budget))
            turn_state.tool_result_refs = [record.result_id for record in request.tool_result_store.records]
        if request.review_inbox is not None:
            blocks.extend(request.review_inbox.to_context_blocks(token_budget=self.token_budget))
            turn_state.review_inbox_refs = [
                item.review_inbox_id for item in request.review_inbox.items if item.is_unresolved()
            ]
        blocks.extend(request.extra_blocks)
        return blocks

    def _publish(self, event: RuntimeEvent) -> RuntimeEvent:
        if self.event_bus is None:
            return event
        return self.event_bus.publish(event)


class ConflictAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str
    conflict_id: str
    severity: str = "medium"
    affected_claims: list[str] = Field(default_factory=list)
    affected_memories: list[str] = Field(default_factory=list)
    preferred_claim_id: str | None = None
    unresolved_reason: str | None = None
    injection_policy: str = "inject_warning"
    review_inbox_id: str | None = None
    status: str = "needs_review"
    conflict_type: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewInboxItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_inbox_id: str
    alert_id: str
    conflict_id: str
    severity: str = "medium"
    status: str = "needs_review"
    affected_claims: list[str] = Field(default_factory=list)
    affected_memories: list[str] = Field(default_factory=list)
    preferred_claim_id: str | None = None
    unresolved_reason: str | None = None
    injection_policy: str = "inject_warning"
    conflict_type: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_alert(cls, alert: ConflictAlert, *, thread_id: str) -> "ReviewInboxItem":
        review_inbox_id = alert.review_inbox_id or stable_context_id(
            "review-inbox",
            thread_id,
            alert.conflict_id,
            alert.alert_id,
        )
        return cls(
            review_inbox_id=review_inbox_id,
            alert_id=alert.alert_id,
            conflict_id=alert.conflict_id,
            severity=alert.severity,
            status=alert.status or "needs_review",
            affected_claims=_string_list(alert.affected_claims, limit=24),
            affected_memories=_string_list(alert.affected_memories, limit=24),
            preferred_claim_id=alert.preferred_claim_id,
            unresolved_reason=alert.unresolved_reason,
            injection_policy=alert.injection_policy or "inject_warning",
            conflict_type=alert.conflict_type,
            created_at=alert.created_at,
            metadata=dict(alert.metadata),
        )

    def is_unresolved(self) -> bool:
        return self.status.strip().lower() in {"open", "needs_review", "unresolved"}

    def to_context_block(
        self,
        *,
        inbox_id: str,
        thread_id: str,
        token_budget: TokenBudgetService | None = None,
    ) -> ContextBlock:
        counter = token_budget or TokenBudgetService()
        content = "\n".join(self._warning_lines(counter))
        severity = self.severity.strip().lower()
        conflict_state = "unresolved" if self.is_unresolved() else self.status
        metadata = {
            "inbox_id": inbox_id,
            "thread_id": thread_id,
            "review_inbox_id": self.review_inbox_id,
            "alert_id": self.alert_id,
            "conflict_id": self.conflict_id,
            "conflict_type": self.conflict_type,
            "severity": self.severity,
            "affected_claims": self.affected_claims,
            "affected_memories": self.affected_memories,
            "preferred_claim_id": self.preferred_claim_id,
            "injection_policy": self.injection_policy,
        }
        metadata.update(_bounded_metadata(self.metadata, counter))
        return ContextBlock(
            block_id=stable_context_id("runtime-warning", thread_id, self.review_inbox_id, self.alert_id),
            block_type="runtime_warning",
            source=ContextSource(
                kind=ContextSourceKind.EVENT,
                name="review_inbox",
                ref=self.review_inbox_id,
                trust_level="runtime",
                metadata={"inbox_id": inbox_id, "conflict_id": self.conflict_id},
            ),
            title="Runtime Conflict Warning",
            content=content,
            token_cost=counter.count_text(content),
            priority=0.96 if severity in {"high", "critical"} else 0.74,
            salience=0.92,
            confidence=0.9,
            position_hint="runtime:warning",
            conflict_state=conflict_state,
            privacy_level="project",
            injection_policy=InjectionPolicy(
                allow=True,
                protected=True,
                requires_warning=True,
                reason=self.injection_policy,
            ),
            compression_policy=CompressionPolicy(
                allow_compression=False,
                allow_reference=True,
                ref=self.review_inbox_id,
            ),
            tags=("runtime_warning", "conflict", severity),
            metadata=metadata,
        )

    def _warning_lines(self, counter: TokenBudgetService) -> list[str]:
        lines = [
            f"review_inbox_id={self.review_inbox_id}",
            f"alert_id={self.alert_id}",
            f"conflict_id={self.conflict_id}",
            f"severity={self.severity}",
            f"status={self.status}",
            f"policy={self.injection_policy}",
        ]
        if self.conflict_type:
            lines.append(f"conflict_type={self.conflict_type}")
        if self.affected_claims:
            lines.append(f"claims={', '.join(self.affected_claims)}")
        if self.affected_memories:
            lines.append(f"memories={', '.join(self.affected_memories)}")
        if self.preferred_claim_id:
            lines.append(f"preferred_claim_id={self.preferred_claim_id}")
        if self.unresolved_reason:
            lines.append(f"unresolved_reason={counter.truncate_text(self.unresolved_reason, max_tokens=80, max_chars=480)}")
        return lines


class ReviewInbox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbox_id: str
    thread_id: str
    items: list[ReviewInboxItem] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    def add_alert(self, alert: ConflictAlert) -> ReviewInboxItem:
        item = ReviewInboxItem.from_alert(alert, thread_id=self.thread_id)
        for index, existing in enumerate(self.items):
            if existing.review_inbox_id == item.review_inbox_id:
                self.items[index] = item
                break
        else:
            self.items.append(item)
        self._refresh_diagnostics()
        return item

    def to_context_blocks(self, *, token_budget: TokenBudgetService | None = None) -> list[ContextBlock]:
        return [
            item.to_context_block(inbox_id=self.inbox_id, thread_id=self.thread_id, token_budget=token_budget)
            for item in self.items[-12:]
            if item.is_unresolved()
        ]

    def _refresh_diagnostics(self) -> None:
        open_items = [item for item in self.items if item.is_unresolved()]
        self.diagnostics["item_count"] = len(self.items)
        self.diagnostics["open_item_count"] = len(open_items)


def conflict_alert_from_runtime_event(event: RuntimeEvent) -> ConflictAlert | None:
    metadata = event.metadata if isinstance(event.metadata, Mapping) else {}
    event_type = str(event.event_type or "").strip().lower()
    source_kind = str(event.source_kind or "").strip().lower()
    conflict_id = _metadata_text(metadata, "conflict_id", "conflict_ref") or (
        event.source_ref if "conflict" in source_kind else None
    )
    is_conflict_event = (
        event_type in {"conflict_alert", "conflict_detected", "memory_conflict", "hcms_conflict_alert"}
        or "conflict" in event_type
        or "conflict" in source_kind
        or bool(conflict_id)
    )
    if not is_conflict_event or not conflict_id:
        return None

    counter = TokenBudgetService()
    alert_id = _metadata_text(metadata, "alert_id") or stable_context_id(
        "conflict-alert",
        event.thread_id,
        conflict_id,
        event.event_id,
    )
    alert_metadata = _bounded_metadata(metadata, counter)
    alert_metadata.update(
        {
            "runtime_event_id": event.event_id,
            "runtime_event_type": event.event_type,
            "source_kind": event.source_kind,
            "source_ref": event.source_ref,
            "payload_ref": event.payload_ref,
            "trace_id": event.trace_id,
        }
    )
    return ConflictAlert(
        alert_id=alert_id,
        conflict_id=conflict_id,
        severity=_metadata_text(metadata, "severity") or "medium",
        affected_claims=_metadata_string_list(metadata, "affected_claims", "claim_ids", "claims", limit=24),
        affected_memories=_metadata_string_list(
            metadata,
            "affected_memories",
            "memory_ids",
            "memories",
            limit=24,
        ),
        preferred_claim_id=_metadata_text(metadata, "preferred_claim_id", "preferred_claim"),
        unresolved_reason=_metadata_text(metadata, "unresolved_reason", "reason") or event.payload_summary,
        injection_policy=_metadata_text(metadata, "injection_policy") or "inject_warning",
        review_inbox_id=_metadata_text(metadata, "review_inbox_id"),
        status=_metadata_text(metadata, "status") or "needs_review",
        conflict_type=_metadata_text(metadata, "conflict_type"),
        created_at=event.timestamp,
        metadata=alert_metadata,
    )


class ConflictAlertSubscriber:
    def __init__(
        self,
        *,
        review_inbox: ReviewInbox,
        token_budget: TokenBudgetService | None = None,
    ) -> None:
        self.review_inbox = review_inbox
        self.token_budget = token_budget or TokenBudgetService()
        self._processed_event_ids: set[str] = set()
        self.diagnostics: dict[str, Any] = {
            "routed_alert_count": 0,
            "duplicate_event_count": 0,
            "ignored_event_count": 0,
            "last_review_inbox_id": None,
            "last_runtime_warning_block_id": None,
        }

    def __call__(self, event: RuntimeEvent) -> ContextBlock | None:
        alert = conflict_alert_from_runtime_event(event)
        if alert is None:
            self.diagnostics["ignored_event_count"] += 1
            return None

        if event.event_id in self._processed_event_ids:
            event.metadata["runtime_warning_duplicate"] = True
            self.diagnostics["duplicate_event_count"] += 1
            return None

        item = self.review_inbox.add_alert(alert)
        warning = item.to_context_block(
            inbox_id=self.review_inbox.inbox_id,
            thread_id=self.review_inbox.thread_id,
            token_budget=self.token_budget,
        )
        self._processed_event_ids.add(event.event_id)
        _record_conflict_alert_route(event, alert, item, warning)
        self.diagnostics["routed_alert_count"] += 1
        self.diagnostics["last_review_inbox_id"] = item.review_inbox_id
        self.diagnostics["last_runtime_warning_block_id"] = warning.block_id
        return warning

    def on_duplicate(self, existing_event: RuntimeEvent, incoming_event: RuntimeEvent) -> RuntimeEvent | None:
        alert = conflict_alert_from_runtime_event(existing_event)
        if alert is None:
            return None
        if existing_event.event_id not in self._processed_event_ids and not any(
            item.alert_id == alert.alert_id for item in self.review_inbox.items
        ):
            self(existing_event)
            return existing_event
        duplicate = existing_event.model_copy(deep=True)
        duplicate.metadata["runtime_warning_duplicate"] = True
        self.diagnostics["duplicate_event_count"] += 1
        return duplicate


class EventLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    events: list[RuntimeEvent] = Field(default_factory=list)
    latest_sequence: int = 0

    def find_event(self, event_id: str) -> RuntimeEvent | None:
        normalized = str(event_id or "")
        if not normalized:
            return None
        for event in self.events:
            if event.event_id == normalized:
                return event
        return None

    def append(self, event: RuntimeEvent) -> RuntimeEvent:
        existing = self.find_event(event.event_id)
        if existing is not None:
            return existing
        sequence = self.latest_sequence + 1
        appended = event.model_copy(update={"sequence": sequence})
        self.events.append(appended)
        self.latest_sequence = sequence
        return appended


class RuntimeEventBus:
    def __init__(self, *, event_log: EventLog) -> None:
        self.event_log = event_log
        self._subscribers: list[Any] = []

    def subscribe(self, handler: Any) -> None:
        self._subscribers.append(handler)

    def publish(self, event: RuntimeEvent) -> RuntimeEvent:
        existing = self.event_log.find_event(event.event_id)
        if existing is not None:
            duplicate = existing
            for handler in tuple(self._subscribers):
                on_duplicate = getattr(handler, "on_duplicate", None)
                if not callable(on_duplicate):
                    continue
                handled = on_duplicate(existing, event)
                if isinstance(handled, RuntimeEvent):
                    duplicate = handled
            return duplicate
        appended = self.event_log.append(event)
        for handler in tuple(self._subscribers):
            handler(appended)
        return appended


class ToolResultStore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    records: list[ToolResultRecord] = Field(default_factory=list)

    def ingest_tool_message(
        self,
        tool_message: ToolMessage,
        *,
        tool_name: str | None = None,
        run_id: str | None = None,
        turn_id: str,
        workspace_state: WorkspaceState | None = None,
        token_budget: TokenBudgetService | None = None,
    ) -> ToolResultRecord:
        counter = token_budget or TokenBudgetService()
        payload = _coerce_tool_payload(tool_message.content)
        payload_mapping = payload if isinstance(payload, Mapping) else {}
        name = tool_name or str(getattr(tool_message, "name", "") or payload_mapping.get("tool_name") or "tool")
        tool_call_id = str(getattr(tool_message, "tool_call_id", "") or payload_mapping.get("tool_call_id") or "")
        raw_ref = _raw_ref(payload_mapping)
        status = str(payload_mapping.get("status") or getattr(tool_message, "status", "") or "unknown")
        summary = counter.truncate_text(_summary(payload), max_tokens=160)
        budget_notice = payload_mapping.get("_tool_output_budget")
        budget_notice = budget_notice if isinstance(budget_notice, Mapping) else {}
        compaction = budget_notice.get("compaction")
        compaction = compaction if isinstance(compaction, Mapping) else {}
        compacted = bool(payload_mapping.get("output_compacted") or budget_notice.get("truncated") or compaction or raw_ref)
        result_id = stable_context_id("tool-result", self.thread_id, turn_id, name, tool_call_id, raw_ref or summary)
        record = ToolResultRecord(
            result_id=result_id,
            tool_name=name,
            tool_call_id=tool_call_id or None,
            capability_id=str(payload_mapping.get("capability_id") or f"tool:{name}").strip() or f"tool:{name}",
            run_id=run_id,
            turn_id=turn_id,
            status=status,
            summary=summary,
            raw_ref=raw_ref,
            raw_size_chars=len(str(tool_message.content or "")),
            summary_size_chars=len(summary),
            compacted=compacted,
            metadata={
                "original_chars": budget_notice.get("original_chars"),
                "original_tokens_approx": budget_notice.get("original_tokens_approx"),
                "output_compaction_profile": compaction.get("profile"),
            },
        )
        for existing in self.records:
            same_call = bool(tool_call_id) and existing.tool_call_id == tool_call_id and (
                existing.turn_id == turn_id
                or (bool(run_id) and existing.run_id == run_id)
            )
            if existing.result_id == result_id or same_call:
                if workspace_state is not None and existing.workspace_ref is None:
                    workspace_ref = workspace_state.add_tool_result(existing)
                    existing.workspace_ref = workspace_ref.result_ref
                return existing
        if workspace_state is not None:
            workspace_ref = workspace_state.add_tool_result(record)
            record.workspace_ref = workspace_ref.result_ref
        self.records.append(record)
        return record

    def to_context_blocks(self, *, token_budget: TokenBudgetService | None = None) -> list[ContextBlock]:
        blocks: list[ContextBlock] = []
        for record in self.records[-8:]:
            payload = {
                "status": record.status,
                "tool_name": record.tool_name,
                "tool_call_id": record.tool_call_id,
                "summary": record.summary,
                "raw_ref": record.raw_ref,
                "output_compacted": record.compacted,
            }
            blocks.append(
                tool_result_to_block(
                    ToolMessage(
                        content=json.dumps(payload, ensure_ascii=False, default=str),
                        name=record.tool_name,
                        tool_call_id=record.tool_call_id or record.result_id,
                    ),
                    tool_name=record.tool_name,
                    token_budget=token_budget,
                )
            )
        return blocks


def tool_result_record_to_event(
    record: ToolResultRecord,
    *,
    thread_id: str,
    workspace_refs: list[str | None] | tuple[str | None, ...] = (),
    trace_id: str | None = None,
) -> RuntimeEvent:
    refs = [ref for ref in workspace_refs if ref]
    payload_summary = TokenBudgetService().truncate_text(
        record.summary,
        max_tokens=160,
        max_chars=1200,
    )
    return RuntimeEvent(
        event_id=stable_context_id("event", thread_id, record.turn_id, record.result_id),
        event_type="tool_result",
        actor="tool",
        thread_id=thread_id,
        run_id=record.run_id,
        turn_id=record.turn_id,
        source_kind="tool",
        source_ref=record.tool_call_id or record.result_id,
        payload_ref=record.raw_ref,
        payload_summary=payload_summary,
        trace_id=trace_id,
        tool_result_refs=[record.result_id],
        workspace_refs=refs,
        metadata={
            "capability_id": record.capability_id or f"tool:{record.tool_name}",
            "capability_kind": "tool",
            "tool_name": record.tool_name,
            "tool_call_id": record.tool_call_id,
            "status": record.status,
            "compacted": record.compacted,
            "raw_size_chars": record.raw_size_chars,
            "summary_size_chars": record.summary_size_chars,
        },
    )


def _user_message_to_block(
    request: TurnPipelineInput,
    turn_state: TurnState,
    token_budget: TokenBudgetService,
) -> ContextBlock:
    content = "\n".join(
        [
            f"user_message_ref={turn_state.user_message_ref}",
            f"thread_id={request.thread_id}",
            f"turn_id={request.turn_id}",
            "message:",
            turn_state.user_text_summary,
        ]
    )
    return ContextBlock(
        block_id=stable_context_id("turn-user-message", request.thread_id, request.turn_id, turn_state.user_text_summary),
        block_type="task",
        source=ContextSource(
            kind=ContextSourceKind.PROMPT,
            name="user_message",
            ref=turn_state.user_message_ref,
            trust_level="user",
            metadata={"thread_id": request.thread_id, "turn_id": request.turn_id},
        ),
        title="User Message",
        content=content,
        token_cost=token_budget.count_text(content),
        priority=1.0,
        salience=1.0,
        confidence=1.0,
        position_hint="stable:task",
        privacy_level="project",
        injection_policy=InjectionPolicy(
            allow=True,
            protected=True,
            reason="current_user_turn",
        ),
        compression_policy=CompressionPolicy(
            allow_compression=False,
            allow_reference=False,
        ),
        tags=("turn_state", "user_message", "intake"),
        metadata={
            "thread_id": request.thread_id,
            "run_id": request.run_id,
            "turn_id": request.turn_id,
            "user_message_ref": turn_state.user_message_ref,
        },
    )


def _goal_is_active(goal: GoalFrame) -> bool:
    return goal.status.strip().lower() not in {"completed", "done", "cancelled", "canceled", "archived"}


def _goal_is_suppressed(goal: GoalFrame) -> bool:
    return not _goal_is_active(goal)


def _scratchpad_entry_active(entry: ScratchpadEntry) -> bool:
    return entry.status.strip().lower() not in {"closed", "resolved", "archived", "discarded"}


def _bounded_line(value: str, counter: TokenBudgetService) -> str:
    return counter.truncate_text(str(value or "").strip(), max_tokens=40, max_chars=240)


def _coerce_tool_payload(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _raw_ref(payload: Mapping[str, Any]) -> str | None:
    direct = payload.get("raw_output_artifact_url") or payload.get("artifact_url") or payload.get("raw_ref")
    if direct:
        return str(direct)
    notice = payload.get("_tool_output_budget")
    notice = notice if isinstance(notice, Mapping) else {}
    notice_ref = notice.get("artifact_url") or notice.get("raw_artifact_url")
    if notice_ref:
        return str(notice_ref)
    compaction = notice.get("compaction")
    compaction = compaction if isinstance(compaction, Mapping) else {}
    compaction_ref = compaction.get("raw_artifact_url") or compaction.get("artifact_url")
    return str(compaction_ref) if compaction_ref else None


def _summary(payload: Any) -> str:
    if isinstance(payload, Mapping):
        for key in ("summary", "output", "message", "content"):
            value = payload.get(key)
            if value is not None:
                return _stringify(value)
        visible = {
            key: value
            for key, value in payload.items()
            if key not in {"_tool_output_budget", "raw_output_artifact_url", "artifact_url", "raw_ref"}
        }
        return _stringify(visible)
    return _stringify(payload)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except TypeError:
        return str(value)


def _bounded_metadata(metadata: Mapping[str, Any], counter: TokenBudgetService) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in list(metadata.items())[:24]:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        if isinstance(value, str):
            payload[normalized_key] = counter.truncate_text(value, max_tokens=80, max_chars=480)
        elif isinstance(value, (int, float, bool)) or value is None:
            payload[normalized_key] = value
        elif isinstance(value, (list, tuple)):
            payload[normalized_key] = [
                counter.truncate_text(item, max_tokens=40, max_chars=240)
                if isinstance(item, str)
                else item
                for item in value[:24]
            ]
        else:
            payload[normalized_key] = counter.truncate_text(
                _stringify(value),
                max_tokens=80,
                max_chars=480,
            )
    return payload


def _metadata_text(metadata: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        text = str(value or "").strip()
        if text:
            return text[:480]
    return None


def _metadata_string_list(metadata: Mapping[str, Any], *keys: str, limit: int) -> list[str]:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            return _string_list(value, limit=limit)
        text = str(value or "").strip()
        if text:
            return _string_list([text], limit=limit)
    return []


def _record_conflict_alert_route(
    event: RuntimeEvent,
    alert: ConflictAlert,
    item: ReviewInboxItem,
    warning: ContextBlock,
) -> None:
    event.metadata["review_inbox_id"] = item.review_inbox_id
    event.metadata["runtime_warning_block_id"] = warning.block_id
    event.metadata["runtime_warning_injected"] = True
    event.metadata["hcms_v2_conflict_alert_routed"] = {
        "alert_id": alert.alert_id,
        "conflict_id": alert.conflict_id,
        "review_inbox_id": item.review_inbox_id,
        "runtime_warning_block_id": warning.block_id,
        "severity": alert.severity,
        "status": item.status,
    }


def _string_list(values: list[str] | tuple[str, ...], *, limit: int) -> list[str]:
    items: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        items.append(text[:240])
        if len(items) >= limit:
            break
    return items


TurnPipelineInput.model_rebuild()


__all__ = [
    "ConflictAlert",
    "ConflictAlertSubscriber",
    "EventLog",
    "GoalFrame",
    "GoalStack",
    "ReviewInbox",
    "ReviewInboxItem",
    "RuntimeEvent",
    "RuntimeEventBus",
    "SalienceRoute",
    "SalienceRouter",
    "Scratchpad",
    "ScratchpadEntry",
    "ToolResultRecord",
    "ToolResultStore",
    "TurnPipeline",
    "TurnPipelineInput",
    "TurnPipelineResult",
    "TurnState",
    "WorkspaceIntermediateResult",
    "WorkspaceState",
    "conflict_alert_from_runtime_event",
    "tool_result_record_to_event",
]
