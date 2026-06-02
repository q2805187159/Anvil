from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState, MemoryInjectionDiagnostics
from anvil.runtime.token_budget import TokenBudgetService


class MemoryPrefetchMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def before_model(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        memory_manager = runtime.context.memory_manager
        if memory_manager is not None:
            query = ""
            for message in reversed(state_obj.messages):
                if getattr(message, "type", "") == "human":
                    content = getattr(message, "content", "")
                    query = content if isinstance(content, str) else str(content)
                    break
            try:
                recall = memory_manager.prefetch_recall(
                    thread_id=runtime.context.thread_id,
                    query=query,
                )
            except Exception as exc:
                recall = None
                runtime.context.memory_injection_diagnostics = MemoryInjectionDiagnostics(
                    source="memory_manager",
                    status="error",
                    query_tokens=TokenBudgetService().count_text(query),
                    token_budget=getattr(memory_manager.config.recall, "turn_recall_token_budget", 900),
                    error_type=exc.__class__.__name__,
                ).model_dump(mode="json")
            if recall is not None:
                memory_payload = recall.render_turn_block()
                if memory_payload:
                    memory_context = (
                        "Dynamic memory recall is bounded fact lookup for this turn. "
                        "Use it as evidence hints and prefer current user instructions when they conflict.\n"
                        f"{memory_payload}"
                    )
                else:
                    memory_context = ""
                token_budget = TokenBudgetService()
                max_tokens = getattr(memory_manager.config.recall, "turn_recall_token_budget", 900)
                original_memory_context = memory_context
                rendered_tokens_before = token_budget.count_text(original_memory_context)
                memory_context = token_budget.truncate_text(
                    original_memory_context,
                    max_tokens=max_tokens,
                )
                diagnostics = _recall_diagnostics(
                    recall,
                    query=query,
                    token_budget=max_tokens,
                    rendered_tokens_before_truncation=rendered_tokens_before,
                    rendered_tokens=token_budget.count_text(memory_context),
                    truncated=memory_context != original_memory_context,
                )
                runtime.context.memory_injection_diagnostics = diagnostics.model_dump(mode="json")
                if memory_context:
                    runtime.context.memory_context = memory_context
                    return {
                        "memory_snapshot_id": recall.snapshot_fingerprint,
                        "memory_context": memory_context,
                        "memory_injection_diagnostics": runtime.context.memory_injection_diagnostics,
                    }
                return {"memory_injection_diagnostics": runtime.context.memory_injection_diagnostics}

        memory_service = runtime.context.memory_service
        if memory_service is None:
            return None

        namespace = runtime.context.memory_namespace or "global/default"
        try:
            injection = memory_service.build_injection_view(namespace)
        except Exception:
            return None

        memory_context = injection.render_fenced()
        token_budget = TokenBudgetService()
        runtime.context.memory_context = memory_context
        runtime.context.memory_injection_diagnostics = MemoryInjectionDiagnostics(
            source="legacy_memory_service",
            status="injected" if memory_context else "empty",
            snapshot_id=namespace,
            rendered_tokens_before_truncation=token_budget.count_text(memory_context),
            rendered_tokens=token_budget.count_text(memory_context),
            token_budget=None,
            truncated=False,
        ).model_dump(mode="json")
        return {
            "memory_snapshot_id": namespace,
            "memory_context": memory_context,
            "memory_injection_diagnostics": runtime.context.memory_injection_diagnostics,
        }

    def wrap_model_call(self, request, handler):
        memory_context = request.runtime.context.memory_context
        if memory_context:
            system_prompt = request.system_prompt or ""
            if "<memory_context>" not in system_prompt and "<memory_recall>" not in system_prompt:
                system_prompt = f"{system_prompt}\n\n{memory_context}" if system_prompt else memory_context
                request = request.override(system_message=SystemMessage(content=system_prompt))
        return handler(request)


def _recall_diagnostics(
    recall,
    *,
    query: str,
    token_budget: int | None,
    rendered_tokens_before_truncation: int,
    rendered_tokens: int,
    truncated: bool,
) -> MemoryInjectionDiagnostics:
    store_counts: dict[str, int] = {}
    for entry in getattr(recall, "curated_matches", ()) or ():
        store_id = str(getattr(entry, "store_id", "") or "unknown")
        store_counts[store_id] = store_counts.get(store_id, 0) + 1
    source_kind_counts: dict[str, int] = {}
    for item in getattr(recall, "evidence", ()) or ():
        source_kind = str(getattr(item, "source_kind", "") or "unknown")
        source_kind_counts[source_kind] = source_kind_counts.get(source_kind, 0) + 1
    has_payload = any(
        (
            getattr(recall, "summary", None),
            getattr(recall, "curated_matches", ()),
            getattr(recall, "archive_hits", ()),
            getattr(recall, "provider_notes", ()),
            getattr(recall, "evidence", ()),
        )
    )
    return MemoryInjectionDiagnostics(
        source="memory_manager",
        status="injected" if rendered_tokens > 0 and has_payload else "empty",
        snapshot_id=getattr(recall, "snapshot_fingerprint", None),
        query_tokens=TokenBudgetService().count_text(query),
        curated_match_count=len(getattr(recall, "curated_matches", ()) or ()),
        archive_hit_count=len(getattr(recall, "archive_hits", ()) or ()),
        evidence_count=len(getattr(recall, "evidence", ()) or ()),
        provider_note_count=len(getattr(recall, "provider_notes", ()) or ()),
        summary_present=bool(getattr(recall, "summary", None)),
        rendered_tokens_before_truncation=rendered_tokens_before_truncation,
        rendered_tokens=rendered_tokens,
        token_budget=token_budget,
        truncated=truncated,
        store_counts=dict(sorted(store_counts.items())),
        source_kind_counts=dict(sorted(source_kind_counts.items())),
    )
