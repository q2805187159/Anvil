from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import timedelta

from .contracts import (
    CausalPath,
    Memory,
    MemoryLifecycleState,
    MemoryState,
    QueryAnalysis,
    QueryIntent,
    RetrievalConfig,
    RetrievalResult,
    bounded_float,
    record_memory_diagnostic,
    tokenize,
    utc_now,
)


@dataclass(frozen=True)
class RetrievalCacheStats:
    max_entries: int
    ttl_seconds: int
    size: int
    hits: int
    misses: int
    writes: int
    evictions: int
    expirations: int
    bypasses: int

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0


class BM25Retriever:
    """Standalone lexical stream for exact terms and short technical queries."""

    def search(self, memories: list[Memory], query: str) -> dict[str, float]:
        query_terms = tokenize(query)
        if not query_terms:
            return {}
        docs = {memory.memory_id: tokenize(_memory_search_text(memory)) for memory in memories}
        doc_count = max(len(docs), 1)
        df: dict[str, int] = defaultdict(int)
        for terms in docs.values():
            for term in set(terms):
                df[term] += 1
        avg_len = sum(len(terms) for terms in docs.values()) / max(len(docs), 1)
        scores: dict[str, float] = {}
        for memory in memories:
            terms = docs[memory.memory_id]
            if not terms:
                continue
            score = 0.0
            for term in query_terms:
                tf = terms.count(term)
                if tf <= 0:
                    continue
                idf = math.log(1 + (doc_count - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
                denom = tf + 1.2 * (1 - 0.75 + 0.75 * (len(terms) / max(avg_len, 1)))
                score += idf * (tf * 2.2 / denom)
            if score:
                scores[memory.memory_id] = bounded_float(score / (score + 1.0))
        return scores


class DeterministicVectorRetriever:
    """Standalone zero-dependency semantic stream using token-set similarity."""

    def search(self, memories: list[Memory], query: str) -> dict[str, float]:
        query_terms = set(tokenize(query))
        if not query_terms:
            return {}
        scores: dict[str, float] = {}
        for memory in memories:
            terms = set(tokenize(_memory_search_text(memory)))
            if not terms:
                continue
            intersection = len(query_terms & terms)
            union = len(query_terms | terms)
            if intersection:
                scores[memory.memory_id] = bounded_float(intersection / union)
        return scores


class GraphRetriever:
    """Standalone relation propagation stream over HCMS memory relations."""

    def __init__(self, vector_retriever: DeterministicVectorRetriever | None = None) -> None:
        self.vector_retriever = vector_retriever or DeterministicVectorRetriever()

    def search(self, state: MemoryState, memories: list[Memory], query: str) -> dict[str, float]:
        base = self.vector_retriever.search(memories, query)
        scores = dict(base)
        relation_by_source: dict[str, list[object]] = defaultdict(list)
        for relation in state.relations:
            relation_by_source[relation.source_memory_id].append(relation)
            if relation.bidirectional:
                relation_by_source[relation.target_memory_id].append(relation)
        for memory_id, score in base.items():
            for relation in relation_by_source.get(memory_id, []):
                target = getattr(relation, "target_memory_id", None)
                if target == memory_id:
                    target = getattr(relation, "source_memory_id", None)
                if not target:
                    continue
                propagated = score * float(getattr(relation, "weight", 0.5)) * 0.65
                scores[target] = max(scores.get(target, 0.0), bounded_float(propagated))
        return scores


class TemporalCausalRetriever:
    """Standalone recency and causal-edge stream for why/temporal queries."""

    def __init__(self, vector_retriever: DeterministicVectorRetriever | None = None) -> None:
        self.vector_retriever = vector_retriever or DeterministicVectorRetriever()

    def search(
        self,
        state: MemoryState,
        memories: list[Memory],
        query: str,
        *,
        analysis: QueryAnalysis,
    ) -> dict[str, float]:
        base = self.vector_retriever.search(memories, query)
        scores: dict[str, float] = {}
        if not memories:
            return scores
        newest = max(memory.created_at for memory in memories)
        for memory in memories:
            age_days = max((newest - memory.created_at).days, 0)
            recency = math.exp(-0.03 * age_days)
            temporal_match = self._temporal_match_score(memory, analysis)
            causal_boost = 0.0
            if analysis.intent == QueryIntent.TEMPORAL_CAUSAL:
                causal_boost = sum(edge.strength for edge in state.causal_edges if memory.memory_id in {edge.source_event, edge.target_event})
            scores[memory.memory_id] = bounded_float(
                (base.get(memory.memory_id, 0.0) * 0.48)
                + (recency * 0.17)
                + (temporal_match * 0.2)
                + (min(causal_boost, 1.0) * 0.15)
            )
        return scores

    def _temporal_match_score(self, memory: Memory, analysis: QueryAnalysis) -> float:
        if analysis.time_range is None:
            return 0.0
        start, end = analysis.time_range
        memory_times = (memory.created_at, memory.updated_at, memory.accessed_at)
        if any(start <= timestamp <= end for timestamp in memory_times):
            return 1.0
        distance_seconds = min(
            abs((timestamp - start).total_seconds()) if timestamp < start else abs((timestamp - end).total_seconds())
            for timestamp in memory_times
        )
        distance_days = max(distance_seconds / 86400, 0.0)
        return math.exp(-0.45 * distance_days)


class FourStreamRetriever:
    """BM25 + deterministic vector + graph + temporal/causal retrieval."""

    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig()
        self.bm25_retriever = BM25Retriever()
        self.vector_retriever = DeterministicVectorRetriever()
        self.graph_retriever = GraphRetriever(self.vector_retriever)
        self.temporal_retriever = TemporalCausalRetriever(self.vector_retriever)
        self._cache: OrderedDict[tuple[str, str, int, str], tuple[float, list[RetrievalResult]]] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_writes = 0
        self._cache_evictions = 0
        self._cache_expirations = 0
        self._cache_bypasses = 0

    def analyze(self, query: str) -> QueryAnalysis:
        lowered = query.lower()
        time_range, temporal_marker = _parse_time_range(lowered)
        intent = QueryIntent.SEMANTIC
        if query.strip().startswith("mem_") or "id:" in lowered:
            intent = QueryIntent.EXACT_MATCH
        elif time_range is not None or any(
            marker in lowered
            for marker in (
                "why",
                "because",
                "cause",
                "caused",
                "causing",
                "led to",
                "resulted in",
                "effect",
                "impact",
                "consequence",
                "enabled",
                "allowed",
                "made possible",
                "prevented",
                "blocked",
                "stopped",
                "原因",
                "为什么",
                "导致",
                "影响",
                "使得",
                "阻止",
                "recent",
                "recently",
                "latest",
                "last week",
                "上周",
                "最近",
                "昨天",
            )
        ):
            intent = QueryIntent.TEMPORAL_CAUSAL
        elif any(marker in lowered for marker in ("related", "depends", "connect", "关联", "关系")):
            intent = QueryIntent.RELATIONAL
        entities = _extract_entities(query)
        filters = {"temporal_marker": temporal_marker} if temporal_marker else {}
        return QueryAnalysis(intent=intent, entities=entities, time_range=time_range, filters=filters)

    def retrieve(self, state: MemoryState, query: str, *, limit: int | None = None) -> list[RetrievalResult]:
        started = time.perf_counter()
        normalized_limit = max(1, min(int(limit or self.config.default_limit), self.config.max_limit))
        cache_key = (state.namespace, query.strip().lower(), normalized_limit, self._state_fingerprint(state))
        cached_results = self._cache_get(cache_key)
        if cached_results is not None:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._record_recall_metrics(state, cached_results, elapsed_ms=elapsed_ms)
            return cached_results

        memories = [memory for memory in state.memories if memory.state == MemoryLifecycleState.ACTIVE]
        analysis = self.analyze(query)
        streams = {
            "bm25": self._run_stream(state, "bm25", lambda: self._bm25(memories, query)),
            "vector": self._run_stream(state, "vector", lambda: self._vector(memories, query)),
            "graph": self._run_stream(state, "graph", lambda: self._graph(state, memories, query)),
            "temporal": self._run_stream(
                state,
                "temporal",
                lambda: self._temporal_causal(state, memories, query, analysis=analysis),
            ),
        }
        weights = self._weights(analysis)
        results = self._fuse(memories, streams, weights=weights, limit=normalized_limit)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._record_recall_metrics(state, results, elapsed_ms=elapsed_ms)
        self._cache_put(cache_key, results)
        return results

    def _record_recall_metrics(self, state: MemoryState, results: list[RetrievalResult], *, elapsed_ms: float) -> None:
        state.metrics.recall_count += 1
        state.metrics.last_latency_ms = round(elapsed_ms, 3)
        state.metrics.recall_hit_rate = 1.0 if results else 0.0
        memories_by_id = {memory.memory_id: memory for memory in state.memories}
        for result in results:
            memory = memories_by_id.get(result.memory_id)
            if memory is not None:
                memory.access_count += 1
                memory.accessed_at = utc_now()

    def cache_stats(self) -> RetrievalCacheStats:
        return RetrievalCacheStats(
            max_entries=int(self.config.cache_max_entries),
            ttl_seconds=int(self.config.cache_ttl),
            size=len(self._cache),
            hits=self._cache_hits,
            misses=self._cache_misses,
            writes=self._cache_writes,
            evictions=self._cache_evictions,
            expirations=self._cache_expirations,
            bypasses=self._cache_bypasses,
        )

    def clear_cache(self) -> None:
        self._cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_writes = 0
        self._cache_evictions = 0
        self._cache_expirations = 0
        self._cache_bypasses = 0

    def _cache_get(self, cache_key: tuple[str, str, int, str]) -> list[RetrievalResult] | None:
        if not self.config.enable_cache or self.config.cache_max_entries <= 0 or self.config.cache_ttl <= 0:
            self._cache_bypasses += 1
            return None
        cached = self._cache.get(cache_key)
        if cached is None:
            self._cache_misses += 1
            return None
        timestamp, results = cached
        if time.time() - timestamp > self.config.cache_ttl:
            self._cache.pop(cache_key, None)
            self._cache_misses += 1
            self._cache_expirations += 1
            return None
        self._cache_hits += 1
        self._cache.move_to_end(cache_key)
        return [item.model_copy(deep=True) for item in results]

    def _cache_put(self, cache_key: tuple[str, str, int, str], results: list[RetrievalResult]) -> None:
        if not self.config.enable_cache or self.config.cache_max_entries <= 0 or self.config.cache_ttl <= 0:
            return
        self._cache_writes += 1
        self._cache[cache_key] = (time.time(), [item.model_copy(deep=True) for item in results])
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self.config.cache_max_entries:
            self._cache.popitem(last=False)
            self._cache_evictions += 1

    def _state_fingerprint(self, state: MemoryState) -> str:
        parts: list[str] = [state.namespace, state.updated_at.isoformat()]
        parts.extend(
            f"{memory.memory_id}:{memory.version}:{memory.state.value}:{memory.updated_at.isoformat()}"
            for memory in state.memories
        )
        parts.extend(f"{relation.relation_id}:{relation.updated_at.isoformat()}" for relation in state.relations)
        parts.extend(f"{edge.edge_id}:{edge.timestamp.isoformat()}" for edge in state.causal_edges)
        return str(hash(tuple(parts)))

    def causal_paths(
        self,
        state: MemoryState,
        target_id: str,
        *,
        max_hops: int = 3,
        direction: str = "causes",
    ) -> list[CausalPath]:
        memories = {memory.memory_id: memory for memory in state.memories}
        paths: list[CausalPath] = []
        queue: list[tuple[str, list[str], list[object]]] = [(target_id, [target_id], [])]
        downstream = direction in {"effects", "downstream"}
        while queue:
            current, nodes, edges = queue.pop(0)
            if len(nodes) > max_hops + 1:
                continue
            candidates = [
                edge
                for edge in state.causal_edges
                if (edge.source_event == current if downstream else edge.target_event == current)
            ]
            for edge in candidates:
                next_event = edge.target_event if downstream else edge.source_event
                if next_event in nodes:
                    continue
                next_nodes = [*nodes, next_event] if downstream else [next_event, *nodes]
                next_edges = [*edges, edge] if downstream else [edge, *edges]
                if next_event in memories:
                    path_nodes = [
                        {
                            "memory_id": memory_id,
                            "event_type": memories[memory_id].category.value,
                            "timestamp": memories[memory_id].created_at,
                            "confidence": memories[memory_id].confidence,
                        }
                        for memory_id in next_nodes
                        if memory_id in memories
                    ]
                    path_strength = math.prod(float(item.strength) for item in next_edges) if next_edges else 0.0
                    paths.append(
                        CausalPath.model_validate(
                            {
                                "nodes": path_nodes,
                                "edges": next_edges,
                                "total_strength": bounded_float(path_strength),
                                "confidence": bounded_float(path_strength),
                            }
                        )
                    )
                    queue.append((next_event, next_nodes, next_edges))
        paths.sort(key=lambda item: item.confidence, reverse=True)
        return paths[:8]

    def _weights(self, analysis: QueryAnalysis) -> dict[str, float]:
        weights = {
            "bm25": self.config.bm25_weight,
            "vector": self.config.vector_weight,
            "graph": self.config.graph_weight,
            "temporal": self.config.temporal_weight,
        }
        if not self.config.enable_adaptive_weights:
            return weights
        if analysis.intent == QueryIntent.TEMPORAL_CAUSAL:
            return {"bm25": 0.2, "vector": 0.2, "graph": 0.25, "temporal": 0.35}
        if analysis.intent == QueryIntent.RELATIONAL:
            return {"bm25": 0.2, "vector": 0.25, "graph": 0.4, "temporal": 0.15}
        if analysis.intent == QueryIntent.EXACT_MATCH:
            return {"bm25": 0.55, "vector": 0.15, "graph": 0.15, "temporal": 0.15}
        return weights

    def _bm25(self, memories: list[Memory], query: str) -> dict[str, float]:
        return self.bm25_retriever.search(memories, query)

    def _run_stream(self, state: MemoryState, stream_name: str, search: Callable[[], dict[str, float]]) -> dict[str, float]:
        try:
            return search()
        except Exception as exc:
            record_memory_diagnostic(
                state,
                component="retrieval",
                reason="stream_failed",
                stream_name=stream_name,
                error_type=exc.__class__.__name__,
                message=f"{stream_name} stream failed open.",
            )
            return {}

    def _vector(self, memories: list[Memory], query: str) -> dict[str, float]:
        return self.vector_retriever.search(memories, query)

    def _graph(self, state: MemoryState, memories: list[Memory], query: str) -> dict[str, float]:
        return self.graph_retriever.search(state, memories, query)

    def _temporal_causal(
        self,
        state: MemoryState,
        memories: list[Memory],
        query: str,
        *,
        analysis: QueryAnalysis,
    ) -> dict[str, float]:
        return self.temporal_retriever.search(state, memories, query, analysis=analysis)

    def _fuse(
        self,
        memories: list[Memory],
        streams: dict[str, dict[str, float]],
        *,
        weights: dict[str, float],
        limit: int,
    ) -> list[RetrievalResult]:
        by_id = {memory.memory_id: memory for memory in memories}
        ranks: dict[str, dict[str, int]] = {}
        raw: dict[str, dict[str, float]] = defaultdict(dict)
        fused: dict[str, float] = defaultdict(float)
        for stream_name, scores in streams.items():
            ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            for rank, (memory_id, score) in enumerate(ranked, start=1):
                ranks.setdefault(memory_id, {})[stream_name] = rank
                raw[memory_id][stream_name] = score
                fused[memory_id] += weights.get(stream_name, 0.0) * (1.0 / (self.config.rrf_k + rank)) * max(score, 0.001)
        if not fused and memories:
            for memory in sorted(memories, key=lambda item: (item.confidence, item.salience), reverse=True)[:limit]:
                fused[memory.memory_id] = memory.confidence * memory.salience * 0.001
                raw[memory.memory_id]["fallback"] = fused[memory.memory_id]
                ranks[memory.memory_id]["fallback"] = 1
        ranked_ids = [memory_id for memory_id, _ in sorted(fused.items(), key=lambda item: item[1], reverse=True)]
        if self.config.enable_mmr:
            ranked_ids = self._apply_mmr(ranked_ids, by_id, fused, limit=limit)
        else:
            ranked_ids = ranked_ids[:limit]

        max_score = max(fused.values(), default=1.0)
        results: list[RetrievalResult] = []
        for memory_id in ranked_ids:
            score = fused[memory_id]
            memory = by_id.get(memory_id)
            if memory is None:
                continue
            results.append(
                RetrievalResult(
                    memory_id=memory_id,
                    score=bounded_float(score / max_score if max_score else score),
                    raw_scores={key: bounded_float(value) for key, value in raw[memory_id].items()},
                    ranks=ranks.get(memory_id, {}),
                    memory=memory,
                    highlight=memory.summary,
                    explanation=", ".join(sorted(raw[memory_id])),
                )
            )
        return results

    def _apply_mmr(
        self,
        ranked_ids: list[str],
        memories: dict[str, Memory],
        fused_scores: dict[str, float],
        *,
        limit: int,
    ) -> list[str]:
        if limit <= 0 or len(ranked_ids) <= 1:
            return ranked_ids[:limit]

        selected: list[str] = []
        remaining = [memory_id for memory_id in ranked_ids if memory_id in memories]
        max_score = max(fused_scores.values(), default=1.0) or 1.0
        lambda_value = self.config.mmr_lambda

        while remaining and len(selected) < limit:
            if not selected:
                selected.append(remaining.pop(0))
                continue

            best_id = max(
                remaining,
                key=lambda memory_id: self._mmr_score(
                    memory_id,
                    selected,
                    memories,
                    fused_scores,
                    max_score=max_score,
                    lambda_value=lambda_value,
                ),
            )
            remaining.remove(best_id)
            selected.append(best_id)
        return selected

    def _mmr_score(
        self,
        memory_id: str,
        selected: list[str],
        memories: dict[str, Memory],
        fused_scores: dict[str, float],
        *,
        max_score: float,
        lambda_value: float,
    ) -> float:
        relevance = fused_scores.get(memory_id, 0.0) / max_score
        candidate_terms = self._memory_terms(memories[memory_id])
        similarity = max(
            (self._jaccard(candidate_terms, self._memory_terms(memories[selected_id])) for selected_id in selected if selected_id in memories),
            default=0.0,
        )
        return (lambda_value * relevance) - ((1.0 - lambda_value) * similarity)

    def _memory_terms(self, memory: Memory) -> set[str]:
        return set(tokenize(_memory_search_text(memory)))

    def _jaccard(self, left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)


def _extract_entities(query: str) -> list[str]:
    entities: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9_.-]{1,}\b", str(query or "")):
        value = match.group(0).strip(".,:;!?()[]{}")
        if value.lower() in {"Why", "What", "When", "Where", "How"}:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        entities.append(value)
    return entities


def _memory_search_text(memory: Memory) -> str:
    return " ".join(
        part
        for part in (
            memory.summary,
            _strip_compiled_memory_boilerplate(memory.content),
            " ".join(memory.tags),
            " ".join(memory.entities),
            " ".join(memory.concepts),
        )
        if str(part or "").strip()
    )


def _strip_compiled_memory_boilerplate(content: str) -> str:
    text = str(content or "")
    if text.startswith("---"):
        lines = text.splitlines()
        end_index: int | None = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_index = index
                break
        if end_index is not None:
            text = "\n".join(lines[end_index + 1 :]).strip()
    cut = len(text)
    for section in ("## Evidence", "## Relations", "## Metadata", "## Reinforcement"):
        position = text.find(section)
        if position >= 0:
            cut = min(cut, position)
    text = text[:cut].strip()
    if text.startswith("#"):
        lines = text.splitlines()
        title = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        return " ".join(part for part in (title, body) if part).strip()
    return text


def _parse_time_range(lowered_query: str):
    now = utc_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"\b(yesterday)\b|昨天", lowered_query):
        start = today_start - timedelta(days=1)
        return (start, today_start), "yesterday"
    if re.search(r"\b(today)\b|今天", lowered_query):
        return (today_start, now), "today"
    if re.search(r"\b(last\s+week)\b|上周", lowered_query):
        start = today_start - timedelta(days=7)
        return (start, now), "last_week"
    match = re.search(r"\blast\s+(\d+)\s+(day|days|week|weeks|month|months)\b", lowered_query)
    if match:
        count = max(1, int(match.group(1)))
        unit = match.group(2)
        days = count * (30 if unit.startswith("month") else 7 if unit.startswith("week") else 1)
        return (now - timedelta(days=days), now), f"last_{count}_{unit}"
    if re.search(r"\b(recent|recently|latest)\b|最近", lowered_query):
        return (now - timedelta(days=7), now), "recent"
    return None, None
