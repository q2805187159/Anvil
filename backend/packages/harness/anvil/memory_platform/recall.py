from __future__ import annotations

from collections import defaultdict
from uuid import uuid4
from datetime import datetime
import json
import re

from anvil.config import EffectiveConfig, MemoryPlatformRecallConfig
from anvil.config.model_routing import ModelRouteRequest, RequiredModelCapabilities, resolve_model_route
from anvil.config.service import resolve_internal_task_model_config
from anvil.runtime.token_budget import TokenBudgetService
from anvil.agents.model_factory import create_chat_model

from .resolution import MemoryResolutionService
from .contracts import (
    ArchiveSearchHit,
    ArchiveSearchResult,
    CuratedEntry,
    MemoryTrace,
    RecallEvidence,
    RecallPlan,
    SessionSearchSummary,
    utc_now,
)
from .curated import CuratedStoreManager
from .prompt_snapshots import PromptSnapshotStore
from .provider_runtime import ProviderRuntime
from .retrieval_index import RetrievalIndexStore
from .summarizer import FocusedSessionSummaryService, FocusedSummaryRequest
from .trace import MemoryTraceStore
from .archive import SqliteSessionArchive


INTERNAL_MEMORY_RERANK_CONFIG = {
    "metadata": {
        "anvil_internal": True,
        "anvil_internal_kind": "memory_rerank",
    },
    "tags": ["anvil_internal_memory", "anvil_internal_memory_rerank"],
}


def _truncate(text: str, limit: int = 180) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _category_recall_boost(category: str) -> float:
    return {
        "resolved_outcome": 0.35,
        "correction": 0.30,
        "project_constraint": 0.25,
        "workflow": 0.20,
    }.get(category, 0.0)


def _query_terms(value: str) -> tuple[str, ...]:
    normalized = value.lower().strip()
    if not normalized:
        return ()
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", normalized)
    terms: list[str] = [token for token in tokens if token]
    chinese_chars = [token for token in tokens if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    terms.extend("".join(chinese_chars[index : index + 2]) for index in range(max(len(chinese_chars) - 1, 0)))
    return tuple(dict.fromkeys(terms))


class RecallPlanner:
    def __init__(
        self,
        *,
        curated_store_manager: CuratedStoreManager,
        archive: SqliteSessionArchive,
        retrieval_index: RetrievalIndexStore,
        provider_runtime: ProviderRuntime,
        trace_store: MemoryTraceStore,
        token_budget: TokenBudgetService | None = None,
        resolution_service: MemoryResolutionService | None = None,
        config: MemoryPlatformRecallConfig | None = None,
        effective_config: EffectiveConfig | None = None,
    ) -> None:
        self.curated_store_manager = curated_store_manager
        self.archive = archive
        self.retrieval_index = retrieval_index
        self.provider_runtime = provider_runtime
        self.trace_store = trace_store
        self.token_budget = token_budget or TokenBudgetService()
        self.resolution_service = resolution_service or MemoryResolutionService()
        self.config = config or MemoryPlatformRecallConfig()
        self.effective_config = effective_config

    def build(self, *, query: str, thread_id: str, stable_snapshot: str = "") -> RecallPlan:
        lexical_curated = self.curated_store_manager.search_entries(query, limit=8)
        lexical_archive = self.archive.search(query, limit=8)
        indexed_curated = self.retrieval_index.search_memory(query, limit=8)
        indexed_archive = self.retrieval_index.search_archive(query, limit=8)

        evidence: list[RecallEvidence] = []
        matched_entry_ids = {entry.memory_id or entry.entry_id for entry in lexical_curated}
        for entry in lexical_curated:
            category_boost = _category_recall_boost(entry.category)
            final_score = 1.0 + self.resolution_service.effective_score(entry) + category_boost
            evidence.append(
                RecallEvidence(
                    evidence_id=f"lexical-memory-{entry.memory_id or entry.entry_id}",
                    source_kind="memory",
                    source_id=entry.store_id,
                    layer_id=entry.layer_id,
                    memory_id=entry.memory_id or entry.entry_id,
                    thread_id=entry.thread_id,
                    score=final_score,
                    match_score=1.0,
                    recency_score=self._recency_score(entry.updated_at),
                    final_score=final_score,
                    reason="lexical memory match",
                    excerpt=_truncate(entry.content),
                )
            )

        for item in indexed_curated:
            if item["memory_id"] in matched_entry_ids:
                continue
            entry = self.curated_store_manager.find_entry(str(item["memory_id"]), include_inactive=False)
            if entry is None or not self.curated_store_manager.entry_is_recall_visible(entry):
                continue
            evidence.append(
                RecallEvidence(
                    evidence_id=f"fts-memory-{item['memory_id']}",
                    source_kind="fts_memory",
                    source_id=str(item["store_id"]),
                    layer_id=item.get("layer_id"),
                    memory_id=str(item["memory_id"]),
                    thread_id=item.get("thread_id"),
                    score=float(item["score"]),
                    match_score=float(item["score"]),
                    final_score=float(item["score"]),
                    reason="FTS indexed memory match",
                    excerpt=_truncate(str(item["content"])),
                )
            )

        matched_archive_ids = {hit.archive_id for hit in lexical_archive.hits}
        for hit in lexical_archive.hits:
            evidence.append(
                RecallEvidence(
                    evidence_id=f"lexical-archive-{hit.archive_id}",
                    source_kind="archive",
                    source_id="archive",
                    archive_id=hit.archive_id,
                    thread_id=hit.thread_id,
                    score=abs(float(hit.score)) + 1.0,
                    match_score=abs(float(hit.score)),
                    recency_score=self._recency_score(hit.created_at),
                    final_score=abs(float(hit.score)) + 1.0 + self._recency_score(hit.created_at) * 0.2,
                    reason="lexical archive match",
                    excerpt=_truncate(hit.excerpt),
                )
            )
        for item in indexed_archive:
            if item["archive_id"] in matched_archive_ids:
                continue
            evidence.append(
                RecallEvidence(
                    evidence_id=f"fts-archive-{item['archive_id']}",
                    source_kind="fts_archive",
                    source_id="archive",
                    archive_id=str(item["archive_id"]),
                    thread_id=str(item["thread_id"]),
                    score=float(item["score"]),
                    match_score=float(item["score"]),
                    final_score=float(item["score"]),
                    reason="FTS indexed archive match",
                    excerpt=_truncate(str(item["content"])),
                )
            )

        evidence = self._rerank_evidence(query=query, evidence=evidence)
        evidence = [item for item in evidence if (item.final_score if item.final_score is not None else item.score) >= self.config.min_relevance_score]
        evidence = evidence[: self.config.max_candidates]
        evidence_by_memory = {item.memory_id for item in evidence if item.memory_id}
        evidence_by_archive = {item.archive_id for item in evidence if item.archive_id}
        lexical_curated = tuple(
            entry
            for entry in lexical_curated
            if (entry.memory_id or entry.entry_id) in evidence_by_memory
        )[:4]
        archive_hits = tuple(hit for hit in lexical_archive.hits if hit.archive_id in evidence_by_archive)[:4]
        provider_notes = self.provider_runtime.prefetch(
            query=query,
            thread_id=thread_id,
            archive=lexical_archive.model_copy(update={"hits": archive_hits}),
            curated_matches=lexical_curated,
        )
        provider_explanations = self.provider_runtime.explain(query=query, evidence=tuple(evidence[: self.config.max_evidence]))
        combined_provider_notes = self._budget_notes(tuple([*provider_notes, *provider_explanations]))
        summary = self._build_summary(
            query=query,
            curated_matches=lexical_curated,
            archive_hits=lexical_archive.model_copy(update={"hits": archive_hits}),
        )
        plan = RecallPlan(
            query=query,
            thread_id=thread_id,
            summary=summary,
            stable_snapshot=stable_snapshot,
            evidence=tuple(evidence[: self.config.max_evidence]),
            curated_matches=lexical_curated,
            archive_hits=archive_hits,
            provider_notes=combined_provider_notes,
        )
        self.trace_store.record(
            MemoryTrace(
                trace_id=f"trace-{uuid4().hex[:16]}",
                thread_id=thread_id,
                query=query,
                trace_kind="recall",
                provider_notes=plan.provider_notes,
                evidence=plan.evidence,
            )
        )
        return plan

    def _rerank_evidence(self, *, query: str, evidence: list[RecallEvidence]) -> list[RecallEvidence]:
        ranked = []
        query_tokens = set(_query_terms(query))
        for item in evidence:
            overlap = sum(1 for token in query_tokens if token in item.excerpt.lower()) / max(len(query_tokens), 1)
            final_score = (item.final_score if item.final_score is not None else item.score) + overlap * 0.5
            ranked.append(
                item.model_copy(
                    update={
                        "rerank_score": overlap,
                        "final_score": final_score,
                    }
                )
            )
        if self.config.enable_model_rerank and self.effective_config is not None:
            ranked = self._model_rerank(query=query, evidence=ranked)
        ranked.sort(key=lambda item: item.final_score if item.final_score is not None else item.score, reverse=True)
        return ranked

    def _model_rerank(self, *, query: str, evidence: list[RecallEvidence]) -> list[RecallEvidence]:
        model_name = self._resolve_rerank_model_name()
        if not model_name:
            return evidence
        try:
            model_config = resolve_internal_task_model_config(self.effective_config, model_name)
            if model_config is None:
                model_config = self.effective_config.models[model_name]
            model = create_chat_model(model_config, thinking_enabled=False)
            payload = "\n".join(
                f"{index}. id={item.evidence_id} excerpt={_truncate(item.excerpt, 160)}"
                for index, item in enumerate(evidence[: self.config.max_candidates], start=1)
            )
            response = model.invoke(
                "Rerank the memory evidence for the query. "
                "Return only JSON object mapping evidence id to score 0..1.\n"
                f"Query: {query}\nEvidence:\n{payload}",
                config=INTERNAL_MEMORY_RERANK_CONFIG,
            )
            content = getattr(response, "content", "")
            scores = _parse_rerank_scores(content)
            updated = []
            for item in evidence:
                rerank = max(0.0, min(1.0, float(scores.get(item.evidence_id, item.rerank_score or 0.0))))
                base = item.final_score if item.final_score is not None else item.score
                updated.append(item.model_copy(update={"rerank_score": rerank, "final_score": base + rerank}))
            return updated
        except Exception:
            return evidence

    def _resolve_rerank_model_name(self) -> str | None:
        if self.config.rerank_model_name:
            return self.config.rerank_model_name
        if self.effective_config is None:
            return None
        try:
            route = resolve_model_route(
                self.effective_config,
                ModelRouteRequest(
                    subsystem="memory_rerank",
                    required_capabilities=RequiredModelCapabilities(tool_calling=False),
                ),
            )
            return route.model_name
        except Exception:
            return self.effective_config.memory_platform.session_search.model_name or self.effective_config.default_model

    def _budget_notes(self, notes: tuple[str, ...]) -> tuple[str, ...]:
        rendered: list[str] = []
        remaining = max(self.config.turn_recall_token_budget // 4, 1)
        for note in notes:
            cost = self.token_budget.count_text(note)
            if cost > remaining:
                clipped = self.token_budget.truncate_text(note, max_tokens=remaining)
                if clipped:
                    rendered.append(clipped)
                break
            rendered.append(note)
            remaining -= cost
            if remaining <= 0:
                break
        return tuple(rendered)

    def _recency_score(self, value: datetime) -> float:
        age_days = max((datetime.now(value.tzinfo) - value).days, 0)
        return max(0.0, 1.0 - min(age_days / 180, 1.0))

    def _build_summary(
        self,
        *,
        query: str,
        curated_matches: tuple[CuratedEntry, ...],
        archive_hits: ArchiveSearchResult,
    ) -> str:
        parts: list[str] = [f"Recall focus: {query}."]
        if curated_matches:
            parts.append(
                "Durable memory: " + "; ".join(_truncate(entry.content, 96) for entry in curated_matches[:2]) + "."
            )
        if archive_hits.hits:
            parts.append(
                "Related sessions: "
                + "; ".join(f"{hit.thread_id}: {_truncate(hit.excerpt, 96)}" for hit in archive_hits.hits[:2])
                + "."
            )
        return " ".join(parts)


def _parse_rerank_scores(content) -> dict[str, float]:
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        text = "\n".join(parts)
    else:
        text = str(content)
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        return {}
    return {str(key): float(value) for key, value in payload.items()}


class SessionSearchService:
    def __init__(
        self,
        *,
        archive: SqliteSessionArchive,
        retrieval_index: RetrievalIndexStore,
        prompt_snapshot_store: PromptSnapshotStore,
        trace_store: MemoryTraceStore,
        summary_service: FocusedSessionSummaryService | None = None,
    ) -> None:
        self.archive = archive
        self.retrieval_index = retrieval_index
        self.prompt_snapshot_store = prompt_snapshot_store
        self.trace_store = trace_store
        self.summary_service = summary_service

    def search(
        self,
        *,
        query: str,
        current_thread_id: str | None = None,
        scope: str = "exclude_current",
        limit: int = 5,
        mode: str = "summarize",
    ) -> tuple[SessionSearchSummary, ...]:
        normalized_scope = scope.strip().lower()
        normalized_mode = self._normalize_mode(mode=mode, query=query)
        search_query = "*" if normalized_mode == "recent" else query.strip()
        lexical = self.archive.search(search_query, limit=max(limit * 4, limit))
        indexed = (
            ()
            if normalized_mode == "recent"
            else self.retrieval_index.search_archive(
                search_query,
                limit=max(limit * 4, limit),
                exclude_thread_id=current_thread_id if normalized_scope == "exclude_current" else None,
            )
        )

        merged: dict[str, ArchiveSearchHit] = {hit.archive_id: hit for hit in lexical.hits}
        for item in indexed:
            archive_id = str(item["archive_id"])
            if archive_id in merged:
                continue
            if current_thread_id is not None and normalized_scope == "exclude_current" and item["thread_id"] == current_thread_id:
                continue
            if current_thread_id is not None and normalized_scope == "current" and item["thread_id"] != current_thread_id:
                continue
            merged[archive_id] = ArchiveSearchHit(
                archive_id=archive_id,
                thread_id=str(item["thread_id"]),
                score=float(item["score"]),
                excerpt=_truncate(str(item["content"]), 240),
                created_at=lexical.hits[0].created_at if lexical.hits else utc_now(),
            )

        grouped: dict[str, list[ArchiveSearchHit]] = defaultdict(list)
        for hit in merged.values():
            if current_thread_id is not None and normalized_scope == "exclude_current" and hit.thread_id == current_thread_id:
                continue
            if current_thread_id is not None and normalized_scope == "current" and hit.thread_id != current_thread_id:
                continue
            grouped[hit.thread_id].append(hit)

        summaries: list[SessionSearchSummary] = []
        for thread_key, hits in grouped.items():
            hits.sort(key=lambda item: item.score, reverse=True)
            turns = self.archive.list_thread_turns(thread_key, limit=5)
            evidence = tuple(
                RecallEvidence(
                    evidence_id=f"session-{hit.archive_id}",
                    source_kind="session_archive",
                    source_id="archive",
                    archive_id=hit.archive_id,
                    thread_id=hit.thread_id,
                    score=hit.score,
                    match_score=abs(float(hit.score)),
                    recency_score=self._recency_score(hit.created_at),
                    final_score=abs(float(hit.score)) + self._recency_score(hit.created_at) * 0.2,
                    reason=f"session_search {normalized_mode} evidence",
                    excerpt=_truncate(hit.excerpt),
                )
                for hit in hits[:3]
            )
            snapshot = self.prompt_snapshot_store.latest_for_thread(thread_key)
            summaries.append(
                SessionSearchSummary(
                    thread_id=thread_key,
                    summary=self._focused_summary(
                        query=query,
                        turns=turns,
                        hits=hits,
                        mode=normalized_mode,
                    ),
                    evidence=evidence,
                    archive_hits=tuple(hits[:5]),
                    latest_prompt_snapshot_id=str(snapshot.get("snapshot_id")) if isinstance(snapshot, dict) else None,
                )
            )
            if len(summaries) >= max(limit, 1):
                break

        self.trace_store.record(
            MemoryTrace(
                trace_id=f"trace-{uuid4().hex[:16]}",
                thread_id=current_thread_id,
                query=search_query,
                trace_kind="session_search",
                provider_notes=(),
                evidence=tuple(item for summary in summaries for item in summary.evidence),
            )
        )
        return tuple(summaries)

    def _recency_score(self, value: datetime) -> float:
        age_days = max((datetime.now(value.tzinfo) - value).days, 0)
        return max(0.0, 1.0 - min(age_days / 180, 1.0))

    def _normalize_mode(self, *, mode: str, query: str) -> str:
        normalized = (mode or "summarize").strip().lower()
        if not query.strip():
            return "recent"
        if normalized not in {"recent", "search", "summarize"}:
            raise ValueError("session_search mode must be one of recent, search, summarize")
        return normalized

    def _focused_summary(
        self,
        *,
        query: str,
        turns: tuple[ArchiveTurnRecord, ...],
        hits: list[ArchiveSearchHit],
        mode: str,
    ) -> str:
        if mode == "summarize" and self.summary_service is not None:
            summary = self.summary_service.summarize(
                FocusedSummaryRequest(
                    query=query,
                    thread_id=hits[0].thread_id if hits else "",
                    turns=turns,
                    hits=tuple(hits[:5]),
                )
            )
            if summary:
                return summary

        recent_user = "; ".join(_truncate(turn.user_content, 96) for turn in turns[:2] if turn.user_content)
        recent_outcomes = "; ".join(_truncate(turn.assistant_content, 96) for turn in turns[:2] if turn.assistant_content)
        matched = "; ".join(_truncate(hit.excerpt, 96) for hit in hits[:2])
        focus = "recent session" if mode == "recent" else query
        parts = [f"Session focus: {focus}."]
        if recent_user:
            parts.append(f"User intent: {recent_user}.")
        if recent_outcomes:
            parts.append(f"Observed outcome: {recent_outcomes}.")
        if matched:
            parts.append(f"Evidence: {matched}.")
        return " ".join(parts)
