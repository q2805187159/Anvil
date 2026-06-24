from __future__ import annotations

import importlib.util
from pathlib import Path


def load_benchmark_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run-hcms-benchmark-report.py"
    spec = importlib.util.spec_from_file_location("hcms_benchmark_report", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_hcms_benchmark_medium_fixture_reports_required_fields(contract_tmp_path) -> None:
    module = load_benchmark_module()
    service = module._build_service(contract_tmp_path / "benchmark")

    payload = module._run_benchmark(
        service,
        tmp_dir=contract_tmp_path / "benchmark-state",
        generated_at="2026-06-07T00:00:00+00:00",
        iterations=12,
        fail_under_recall=0.85,
        fail_over_p95_ms=200.0,
    )

    assert payload["passed"] is True
    assert payload["dataset"]["profile"] == "deterministic-medium-fixture"
    assert payload["dataset"]["dataset_size"] >= 50
    assert payload["dataset"]["query_count"] >= 10
    assert payload["dataset"]["negative_case_count"] >= 1
    assert payload["recall"]["score"] >= 0.85
    assert payload["latency"]["cold_start_ms"] >= 0.0
    assert payload["latency"]["warm"]["iterations"] == 12
    assert payload["latency"]["warm"]["measurement"] == "prefetched_state_cached_retrieval"
    assert payload["latency"]["warm_uncached"]["measurement"] == "prefetched_state_uncached_retrieval"
    assert payload["latency"]["warm"]["p95_ms"] < 200.0
    assert set(payload["latency"]["per_stream_ms"]) == {"bm25", "vector", "graph", "temporal"}
    for stream_latency in payload["latency"]["per_stream_ms"].values():
        assert stream_latency["query_count"] == payload["dataset"]["query_count"]
        assert stream_latency["p95_ms"] >= 0.0
        assert stream_latency["result_count_total"] > 0
    assert payload["degraded_stream_case"]["failed_open"] is True
    assert payload["degraded_stream_case"]["stream"] == "bm25"
    assert any("retrieval:stream_failed:bm25:RuntimeError" in item for item in payload["degraded_stream_case"]["diagnostics"])
    assert payload["semantic_negative_probe"]["top_vector_score"] <= 0.05
    assert payload["benchmark_boundaries"]["release_gate"]["status"] == "deterministic_regression_gate"


def test_hcms_benchmark_cases_cover_semantic_and_operational_gates(contract_tmp_path) -> None:
    module = load_benchmark_module()
    service = module._build_service(contract_tmp_path / "benchmark-cases")
    seeded = module._seed_memories(service)
    cases = module._benchmark_cases(seeded)

    case_ids = {case.case_id for case in cases}

    assert len(seeded) >= 50
    assert len(cases) >= 10
    assert {
        "why-canary",
        "diagnostics-fail-open",
        "migration-dry-run-rollback",
        "gateway-health",
        "semantic-acronym",
        "semantic-low-overlap",
        "benchmark-boundary",
    } <= case_ids


def test_hcms_benchmark_markdown_marks_release_gate_as_deterministic_regression_gate(contract_tmp_path) -> None:
    module = load_benchmark_module()
    service = module._build_service(contract_tmp_path / "benchmark-md")
    payload = module._run_benchmark(
        service,
        tmp_dir=contract_tmp_path / "benchmark-md-state",
        generated_at="2026-06-07T00:00:00+00:00",
        iterations=10,
        fail_under_recall=0.85,
        fail_over_p95_ms=200.0,
    )

    markdown = module._render_markdown(payload)

    assert "deterministic-medium-fixture" in markdown
    assert "Dataset size" in markdown
    assert "Query count" in markdown
    assert "Per-Stream Latency" in markdown
    assert "Degraded stream case" in markdown
    assert "Release gate" in markdown
    assert "deterministic_regression_gate" in markdown
    assert "not a production-scale corpus or soak test" in markdown
