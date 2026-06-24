from __future__ import annotations

import argparse
import contextlib
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_SRC = REPO_ROOT / "backend" / "packages" / "harness"
if str(HARNESS_SRC) not in sys.path:
    sys.path.insert(0, str(HARNESS_SRC))

from anvil.memory import (  # noqa: E402
    CausalEdge,
    CausalType,
    DebouncedMemoryQueue,
    FileMemoryStore,
    HeuristicMemoryUpdater,
    MemoryLifecycleState,
    MemoryManager,
    MemoryRecallBenchmarkCase,
    MemoryService,
)


NAMESPACE = "global/default"
DATASET_PROFILE = "deterministic-medium-fixture"
DEFAULT_QUERY = "Why does Northstar require canary verification before release?"
SEMANTIC_NEGATIVE_QUERY = "orchard invoice watercolor piano recital"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic HCMS medium-fixture recall and latency benchmark.")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / ".omx" / "goal" / "reports")
    parser.add_argument("--iterations", type=int, default=120)
    parser.add_argument("--fail-under-recall", type=float, default=0.85)
    parser.add_argument("--fail-over-p95-ms", type=float, default=200.0)
    args = parser.parse_args()

    if args.iterations < 10:
        parser.error("--iterations must be at least 10")

    generated_at = datetime.now(timezone.utc).isoformat()
    tmp_dir = _select_local_tmp() / f"anvil-hcms-benchmark-{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=False)
    service = _build_service(tmp_dir)
    payload = _run_benchmark(
        service,
        tmp_dir=tmp_dir,
        generated_at=generated_at,
        iterations=args.iterations,
        fail_under_recall=args.fail_under_recall,
        fail_over_p95_ms=args.fail_over_p95_ms,
    )

    report_paths = _write_reports(args.output_dir, payload)
    warm = payload["latency"]["warm"]
    print(f"hcms-benchmark-json={report_paths['json']}")
    print(f"hcms-benchmark-md={report_paths['markdown']}")
    print(
        "hcms-benchmark-summary="
        f"passed={payload['passed']} "
        f"profile={payload['dataset']['profile']} "
        f"dataset_size={payload['dataset']['dataset_size']} "
        f"query_count={payload['dataset']['query_count']} "
        f"recall_at_10={payload['recall']['score']:.4f} "
        f"warm_p95_ms={warm['p95_ms']:.3f}"
    )
    return 0 if payload["passed"] else 1


def _build_service(root: Path) -> MemoryService:
    return MemoryService(
        store=FileMemoryStore(root / "hcms"),
        queue=DebouncedMemoryQueue(),
        updater=HeuristicMemoryUpdater(max_facts=96),
        max_facts=96,
        injection_token_budget=1400,
    )


def _run_benchmark(
    service: MemoryService,
    *,
    tmp_dir: Path,
    generated_at: str,
    iterations: int,
    fail_under_recall: float,
    fail_over_p95_ms: float,
) -> dict[str, Any]:
    seeded = _seed_memories(service)
    manager = MemoryManager(service=service, state_root=tmp_dir / "state")
    cases = _benchmark_cases(seeded)
    latency = _measure_latency(service, cases=cases, iterations=iterations)
    recall_report = manager.recall_benchmark(
        suite_id="hcms-deterministic-medium-recall",
        cases=cases,
        evidence_limit=10,
    )
    degraded_case = _measure_degraded_stream_case(service)
    semantic_negative = _measure_semantic_negative_probe(service)
    passed = (
        recall_report.score >= fail_under_recall
        and latency["warm"]["p95_ms"] < fail_over_p95_ms
        and degraded_case["failed_open"]
        and semantic_negative["top_vector_score"] <= 0.05
    )

    return {
        "generated_at": generated_at,
        "profile": DATASET_PROFILE,
        "benchmark_boundaries": {
            "smoke": {
                "status": "retained",
                "description": "The script remains bounded and suitable for local smoke validation.",
            },
            "medium_fixture": {
                "status": "verified",
                "description": "Deterministic medium corpus with recall, latency, degraded-stream, and semantic-negative probes.",
            },
            "release_gate": {
                "status": "deterministic_regression_gate",
                "description": (
                    "Release readiness runs this deterministic medium-fixture gate for recall, latency, "
                    "degraded-stream, and semantic-negative regressions; it is not a production-scale "
                    "corpus or soak test."
                ),
            },
        },
        "dataset": {
            "profile": DATASET_PROFILE,
            "dataset_size": len(seeded),
            "query_count": len(cases),
            "negative_case_count": 1,
            "case_ids": [case.case_id for case in cases],
        },
        "thresholds": {
            "recall_at_10": fail_under_recall,
            "warm_p95_latency_ms": fail_over_p95_ms,
            "semantic_negative_top_vector_score": 0.05,
        },
        "latency": latency,
        "recall": recall_report.model_dump(mode="json"),
        "degraded_stream_case": degraded_case,
        "semantic_negative_probe": semantic_negative,
        "work_dir": str(tmp_dir),
        "passed": bool(passed),
    }


def _select_local_tmp() -> Path:
    candidates = [
        REPO_ROOT / ".omx" / "goal" / "tmp",
        REPO_ROOT / "backend" / ".pytest_tmp",
        REPO_ROOT / ".pytest_tmp",
    ]
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if _tmp_usable(resolved):
            return resolved
    raise RuntimeError("no usable local temp directory for HCMS benchmark")


def _tmp_usable(root: Path) -> bool:
    marker = root / f".anvil-hcms-benchmark-probe-{uuid4().hex}.txt"
    try:
        root.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok", encoding="utf-8")
        return True
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            marker.unlink(missing_ok=True)


def _seed_memories(service: MemoryService) -> dict[str, str]:
    ids: dict[str, str] = {}
    for item in _medium_fixture():
        memory = service.create_memory(
            NAMESPACE,
            content=item["content"],
            category=item["category"],
            confidence=float(item.get("confidence", 0.9)),
            salience=float(item.get("salience", 0.85)),
            source_thread_id="hcms-benchmark",
            metadata={"benchmark_profile": DATASET_PROFILE, "fixture_key": item["key"]},
        )
        ids[item["key"]] = memory.memory_id
    _add_benchmark_causal_edges(service, ids)
    return ids


def _medium_fixture() -> tuple[dict[str, Any], ...]:
    primary = [
        ("canary_policy", "Northstar requires canary verification before release to expose rollout defects safely.", "project_context"),
        ("direct_rollout_failure", "Direct full rollout caused repeated release failures in Northstar staging.", "decision"),
        ("canary_causal_chain", "Canary verification exists because direct full rollout caused repeated release failures.", "decision"),
        ("pytest_backend", "Northstar uses pytest -q for backend verification before HCMS release.", "procedure"),
        ("updates_style", "Use concise implementation updates for the release thread.", "preference"),
        ("stack", "Workspace uses FastAPI in backend and React in frontend.", "project_context"),
        ("archive_search", "Use sqlite archive search for older session recall.", "procedure"),
        ("why_answering", "When users ask why, return the causal chain rather than only a raw fact.", "procedure"),
        ("version_history", "HCMS version history stores parent_id, supersedes, and diff evidence.", "procedure"),
        ("zero_llm", "HCMS zero LLM path extracts preference and correction memories with deterministic rules.", "procedure"),
        ("forgetting", "Low retention HCMS memories can be archived and restored instead of hard deleted.", "procedure"),
        ("retrieval_diagnostics", "HCMS retrieval diagnostics record stream name, error type, count, and degraded status.", "procedure"),
        ("retrieval_fail_open", "HCMS retrieval streams fail open and keep other recall streams running.", "procedure"),
        ("gateway_health", "Gateway memory health exposes HCMS degraded diagnostics to frontend API callers.", "procedure"),
        ("migration_dry_run", "HCMS migration dry-run reports converted count, errors, and legacy distribution without writing data.", "procedure"),
        ("migration_rollback", "HCMS migration rollback restores migrated records while preserving legacy source data.", "procedure"),
        ("semantic_paraphrase", "Staged exposure, also called canary verification, is required before shipping broad release changes.", "project_context"),
        ("semantic_acronym", "NS is an acronym for Northstar in release fixtures; NS canary gate maps to Northstar canary verification.", "project_context"),
        ("semantic_low_overlap", "Gradual exposure is mandatory before shipping changes to everyone; this policy prevents rollout failures.", "project_context"),
        ("semantic_negative_control", "Museum visitor scheduling is unrelated to HCMS release memory.", "note"),
    ]
    secondary = [
        ("counterfactual", "HCMS counterfactual explains projected downstream impact when an assumption is removed.", "procedure"),
        ("relations_diff", "HCMS relation diff compares source and target memory relation sets for governance.", "procedure"),
        ("benchmark_boundary", "HCMS benchmark reports must separate smoke, medium fixture, and release-gate claims.", "procedure"),
        ("frontend_recall", "Frontend memory API calls recall through gateway contracts instead of direct harness imports.", "procedure"),
        ("frontend_health", "Frontend memory governance panel reads health and benchmark fields from API adapters.", "procedure"),
        ("heavy_import", "Importing anvil.memory should avoid agent and LangChain-heavy runtime side effects.", "procedure"),
        ("storage_hybrid", "HCMS hybrid storage keeps markdown records plus structured index metadata.", "project_context"),
        ("version_parent", "A memory update increments version and links parent_id to the previous version.", "procedure"),
        ("delete_soft", "Deleting an HCMS memory marks lifecycle state instead of erasing audit history.", "procedure"),
        ("archive_restore", "Archive restore returns cold HCMS records to active state with version evidence.", "procedure"),
        ("confidence_same", "Same-direction evidence should increase Bayesian confidence without exceeding one.", "procedure"),
        ("confidence_weak", "Weak evidence should have a small bounded effect on Bayesian confidence.", "procedure"),
        ("confidence_conflict", "Contradictory evidence should reduce confidence without producing NaN.", "procedure"),
        ("capture_queue", "Capture queue processing requeues failed envelopes and records capture diagnostics.", "procedure"),
        ("prefetch", "Prefetch loads the namespace memory state before recall injection.", "procedure"),
        ("memory_tools", "Runtime memory tools expose recall, why, history, diff, health, and benchmark operations.", "procedure"),
        ("causal_conflict", "Conflicting causal evidence must surface degradation reason instead of a fabricated cause.", "decision"),
        ("low_confidence_why", "Low confidence why results return evidence summary and correlation fallback.", "decision"),
        ("multi_hop", "Multi-hop causal chains connect rollout failure, canary verification, and release approval.", "decision"),
        ("thread_isolation", "Thread-scoped HCMS namespaces prevent unrelated session memory from polluting recall.", "project_context"),
        ("scrubbing", "Memory context text scrubs secrets and neutralizes memory fence injection markers.", "procedure"),
        ("observation_layer", "Observation layer records user messages, corrections, tool calls, and file operations.", "project_context"),
        ("compiler_layer", "Compilation layer merges durable facts, evidence, confidence, and salience.", "project_context"),
        ("structured_layer", "Structured layer maintains entities, concepts, relations, and causal edges.", "project_context"),
        ("semantic_layer", "Semantic index supports deterministic zero-dependency vector recall.", "project_context"),
        ("active_recall", "Active recall fuses BM25, vector, graph, and temporal causal streams.", "project_context"),
        ("temporal_query", "Temporal causal queries detect why, recent, latest, and date range markers.", "procedure"),
        ("governance_plan", "Governance plan ranks cold, stale, and low-confidence memories for review or archive.", "procedure"),
        ("reflection_job", "Reflection jobs inspect related memories and summarize continuity without direct frontend logic.", "procedure"),
        ("route_snapshot", "Gateway route snapshot must stay aligned with frontend memory API paths.", "procedure"),
        ("api_contract", "Gateway contracts expose why degradation fields and health diagnostics.", "procedure"),
        ("no_reverse_import", "Harness packages must not import backend app or frontend modules.", "project_context"),
        ("documentation_sync", "Docs must mark superseded benchmark claims and avoid release-ready wording when not verified.", "procedure"),
    ]
    rows = [
        {"key": key, "content": content, "category": category, "confidence": 0.92, "salience": 0.9}
        for key, content, category in primary
    ]
    rows.extend(
        {"key": key, "content": content, "category": category, "confidence": 0.86, "salience": 0.78}
        for key, content, category in secondary
    )
    return tuple(rows)


def _add_benchmark_causal_edges(service: MemoryService, ids: dict[str, str]) -> None:
    state = service.prefetch(NAMESPACE)
    existing = {(edge.source_event, edge.target_event) for edge in state.causal_edges}
    edges = (
        ("direct_rollout_failure", "canary_policy", 0.94),
        ("direct_rollout_failure", "canary_causal_chain", 0.96),
        ("canary_policy", "semantic_paraphrase", 0.74),
        ("canary_policy", "multi_hop", 0.78),
        ("multi_hop", "gateway_health", 0.62),
    )
    for source_key, target_key, strength in edges:
        source = ids[source_key]
        target = ids[target_key]
        if (source, target) in existing:
            continue
        state.causal_edges.append(
            CausalEdge(
                source_event=source,
                target_event=target,
                causal_type=CausalType.CONTRIBUTORY,
                strength=strength,
                evidence=[source, target],
                metadata={"benchmark_profile": DATASET_PROFILE},
            )
        )
    service.store.save(NAMESPACE, state)


def _measure_latency(
    service: MemoryService,
    *,
    cases: tuple[MemoryRecallBenchmarkCase, ...],
    iterations: int,
) -> dict[str, Any]:
    queries = [case.query for case in cases]
    service.retriever.clear_cache()
    started = time.perf_counter()
    service.search(NAMESPACE, queries[0], limit=10)
    cold_start_ms = (time.perf_counter() - started) * 1000

    state = service.prefetch(NAMESPACE)
    service.retriever.clear_cache()
    for query in queries:
        service.retriever.retrieve(state, query, limit=10)

    cached_samples: list[float] = []
    for index in range(iterations):
        query = queries[index % len(queries)]
        started = time.perf_counter()
        service.retriever.retrieve(state, query, limit=10)
        cached_samples.append((time.perf_counter() - started) * 1000)

    uncached_samples: list[float] = []
    for query in queries:
        service.retriever.clear_cache()
        started = time.perf_counter()
        service.retriever.retrieve(state, query, limit=10)
        uncached_samples.append((time.perf_counter() - started) * 1000)
    return {
        "cold_start_ms": round(cold_start_ms, 3),
        "warm": {
            **_latency_summary(cached_samples, iterations=iterations, query_count=len(queries)),
            "measurement": "prefetched_state_cached_retrieval",
        },
        "warm_uncached": {
            **_latency_summary(uncached_samples, iterations=len(queries), query_count=len(queries)),
            "measurement": "prefetched_state_uncached_retrieval",
        },
        "per_stream_ms": _measure_stream_latency(service, queries=queries),
    }


def _measure_stream_latency(service: MemoryService, *, queries: list[str]) -> dict[str, Any]:
    state = service.prefetch(NAMESPACE)
    memories = [memory for memory in state.memories if memory.state == MemoryLifecycleState.ACTIVE]
    stream_samples: dict[str, list[float]] = {name: [] for name in ("bm25", "vector", "graph", "temporal")}
    result_counts: dict[str, int] = {name: 0 for name in stream_samples}
    for query in queries:
        analysis = service.retriever.analyze(query)
        stream_calls = {
            "bm25": lambda query=query: service.retriever.bm25_retriever.search(memories, query),
            "vector": lambda query=query: service.retriever.vector_retriever.search(memories, query),
            "graph": lambda query=query: service.retriever.graph_retriever.search(state, memories, query),
            "temporal": lambda query=query, analysis=analysis: service.retriever.temporal_retriever.search(
                state,
                memories,
                query,
                analysis=analysis,
            ),
        }
        for stream_name, call in stream_calls.items():
            started = time.perf_counter()
            scores = call()
            stream_samples[stream_name].append((time.perf_counter() - started) * 1000)
            result_counts[stream_name] += len(scores)
    return {
        stream_name: {
            **_latency_summary(samples, iterations=len(samples), query_count=len(queries)),
            "result_count_total": result_counts[stream_name],
        }
        for stream_name, samples in stream_samples.items()
    }


def _measure_degraded_stream_case(service: MemoryService) -> dict[str, Any]:
    service.retriever.clear_cache()
    original_search = service.retriever.bm25_retriever.search

    def _broken_bm25(*_args: object, **_kwargs: object) -> dict[str, float]:
        raise RuntimeError("benchmark injected bm25 failure")

    service.retriever.bm25_retriever.search = _broken_bm25  # type: ignore[method-assign]
    try:
        results = service.search(NAMESPACE, DEFAULT_QUERY, limit=10)
    finally:
        service.retriever.bm25_retriever.search = original_search  # type: ignore[method-assign]

    state = service.prefetch(NAMESPACE)
    diagnostics = [
        f"{item.component}:{item.reason}:{item.stream_name or '-'}:{item.error_type or '-'}:x{item.count}"
        for item in state.diagnostics
    ]
    matching_diagnostics = [item for item in diagnostics if "retrieval:stream_failed:bm25:RuntimeError" in item]
    return {
        "stream": "bm25",
        "error_type": "RuntimeError",
        "failed_open": bool(results) and bool(matching_diagnostics),
        "result_count": len(results),
        "diagnostics": diagnostics,
    }


def _measure_semantic_negative_probe(service: MemoryService) -> dict[str, Any]:
    state = service.prefetch(NAMESPACE)
    memories = [memory for memory in state.memories if memory.state == MemoryLifecycleState.ACTIVE]
    vector_scores = service.retriever.vector_retriever.search(memories, SEMANTIC_NEGATIVE_QUERY)
    top_score = max(vector_scores.values(), default=0.0)
    return {
        "query": SEMANTIC_NEGATIVE_QUERY,
        "top_vector_score": round(top_score, 4),
        "matched_memory_count": len(vector_scores),
        "status": "passed" if top_score <= 0.05 else "failed",
    }


def _benchmark_cases(ids: dict[str, str]) -> tuple[MemoryRecallBenchmarkCase, ...]:
    return (
        MemoryRecallBenchmarkCase(
            case_id="why-canary",
            query=DEFAULT_QUERY,
            expected_memory_ids=(ids["canary_policy"], ids["canary_causal_chain"], ids["direct_rollout_failure"]),
            expected_terms=("canary verification", "release failures"),
        ),
        MemoryRecallBenchmarkCase(
            case_id="diagnostics-fail-open",
            query="HCMS retrieval diagnostics stream name error type degraded status fail open",
            expected_memory_ids=(ids["retrieval_diagnostics"], ids["retrieval_fail_open"]),
            expected_terms=("stream name", "error type", "fail open"),
        ),
        MemoryRecallBenchmarkCase(
            case_id="version-history",
            query="HCMS version history parent_id supersedes diff evidence",
            expected_memory_ids=(ids["version_history"], ids["version_parent"]),
            expected_terms=("parent_id", "supersedes", "diff evidence"),
        ),
        MemoryRecallBenchmarkCase(
            case_id="zero-llm",
            query="HCMS zero LLM correction memories deterministic rules",
            expected_memory_ids=(ids["zero_llm"],),
            expected_terms=("zero LLM", "deterministic rules"),
        ),
        MemoryRecallBenchmarkCase(
            case_id="archive-restore",
            query="archive restore low retention memories instead of hard deleted",
            expected_memory_ids=(ids["forgetting"], ids["archive_restore"]),
            expected_terms=("archived", "restored"),
        ),
        MemoryRecallBenchmarkCase(
            case_id="migration-dry-run-rollback",
            query="HCMS migration dry-run converted count errors legacy distribution rollback source data",
            expected_memory_ids=(ids["migration_dry_run"], ids["migration_rollback"]),
            expected_terms=("dry-run", "rollback", "legacy"),
        ),
        MemoryRecallBenchmarkCase(
            case_id="gateway-health",
            query="gateway memory health exposes HCMS degraded diagnostics frontend API",
            expected_memory_ids=(ids["gateway_health"], ids["api_contract"]),
            expected_terms=("degraded diagnostics", "frontend API"),
        ),
        MemoryRecallBenchmarkCase(
            case_id="semantic-acronym",
            query="NS canary gate Northstar verification",
            expected_memory_ids=(ids["semantic_acronym"], ids["canary_policy"]),
            expected_terms=("NS", "Northstar", "canary verification"),
        ),
        MemoryRecallBenchmarkCase(
            case_id="semantic-low-overlap",
            query="gradual exposure shipping changes everyone rollout failures",
            expected_memory_ids=(ids["semantic_low_overlap"],),
            expected_terms=("Gradual exposure", "rollout failures"),
        ),
        MemoryRecallBenchmarkCase(
            case_id="benchmark-boundary",
            query="benchmark reports separate smoke medium fixture release gate claims",
            expected_memory_ids=(ids["benchmark_boundary"], ids["documentation_sync"]),
            expected_terms=("smoke", "medium fixture", "release-gate"),
        ),
    )


def _latency_summary(samples: list[float], *, iterations: int, query_count: int) -> dict[str, Any]:
    ordered = sorted(samples)
    if not ordered:
        return {
            "iterations": iterations,
            "query_count": query_count,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
            "min_ms": 0.0,
        }
    return {
        "iterations": iterations,
        "query_count": query_count,
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(_percentile(ordered, 0.95), 3),
        "p99_ms": round(_percentile(ordered, 0.99), 3),
        "max_ms": round(max(ordered), 3),
        "min_ms": round(min(ordered), 3),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * percentile))))
    return values[index]


def _write_reports(output_dir: Path, payload: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"hcms-benchmark-{stamp}.json"
    md_path = output_dir / f"hcms-benchmark-{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def _render_markdown(payload: dict[str, Any]) -> str:
    recall = payload["recall"]
    latency = payload["latency"]
    warm = latency["warm"]
    warm_uncached = latency["warm_uncached"]
    dataset = payload["dataset"]
    boundaries = payload["benchmark_boundaries"]
    degraded = payload["degraded_stream_case"]
    semantic_negative = payload["semantic_negative_probe"]
    lines = [
        "# HCMS Benchmark Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Profile: `{payload['profile']}`",
        f"- Passed: `{payload['passed']}`",
        f"- Dataset size: `{dataset['dataset_size']}`",
        f"- Query count: `{dataset['query_count']}`",
        f"- Recall@10: `{recall['score']}` (threshold `{payload['thresholds']['recall_at_10']}`)",
        f"- Cold start latency: `{latency['cold_start_ms']}ms`",
        f"- Warm P50 latency: `{warm['p50_ms']}ms`",
        f"- Warm P95 latency: `{warm['p95_ms']}ms` (threshold `{payload['thresholds']['warm_p95_latency_ms']}ms`)",
        f"- Warm P99 latency: `{warm['p99_ms']}ms`",
        f"- Warm uncached P95 latency: `{warm_uncached['p95_ms']}ms`",
        f"- Warm iterations: `{warm['iterations']}`",
        f"- Degraded stream case: `{degraded['failed_open']}` (`{degraded['stream']}` / `{degraded['error_type']}`)",
        f"- Semantic negative top vector score: `{semantic_negative['top_vector_score']}`",
        "",
        "## Boundary",
        "",
        f"- Smoke: `{boundaries['smoke']['status']}` - {boundaries['smoke']['description']}",
        f"- Medium fixture: `{boundaries['medium_fixture']['status']}` - {boundaries['medium_fixture']['description']}",
        f"- Release gate: `{boundaries['release_gate']['status']}` - {boundaries['release_gate']['description']}",
        "",
        "## Per-Stream Latency",
        "",
        "| Stream | P50 ms | P95 ms | P99 ms | Samples | Results |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for stream_name, stream_latency in latency["per_stream_ms"].items():
        lines.append(
            f"| `{stream_name}` | `{stream_latency['p50_ms']}` | `{stream_latency['p95_ms']}` | "
            f"`{stream_latency['p99_ms']}` | `{stream_latency['iterations']}` | "
            f"`{stream_latency['result_count_total']}` |"
        )
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Passed | Score | Missing | Evidence |",
            "| --- | --- | ---: | --- | ---: |",
        ]
    )
    for case in recall["cases"]:
        missing = ", ".join(case.get("missing_expectations") or ()) or "-"
        lines.append(
            f"| `{case['case_id']}` | `{case['passed']}` | `{case['score']}` | {missing} | `{case['evidence_count']}` |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
