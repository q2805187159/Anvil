# HCMS Benchmark Report

Date: 2026-06-07

This report is the current Anvil-local HCMS benchmark evidence. It supersedes
earlier smoke-only notes and any implementation audit language that treated a
small benchmark as final release proof.

## Scope

This report covers:

- `scripts/run-hcms-benchmark-report.py`
- `backend/tests/test_hcms_benchmark.py`
- `backend/packages/harness/anvil/memory/`

The benchmark is a deterministic local medium fixture. It is not a production
corpus, soak test, or final release proof.

## Latest Result

Run from `E:\python\python学习\harness\Anvil\backend`:

```powershell
.\.venv\Scripts\python.exe ..\scripts\run-hcms-benchmark-report.py --iterations 120
.\.venv\Scripts\python.exe -m pytest tests\test_hcms_benchmark.py -q
```

Observed result:

- Benchmark script: passed
- Benchmark tests: `3 passed in 25.12s`
- JSON: `.omx/goal/reports/hcms-benchmark-20260606T181248Z.json`
- Markdown: `.omx/goal/reports/hcms-benchmark-20260606T181248Z.md`

## Verified Metrics

Source: `.omx/goal/reports/hcms-benchmark-20260606T181248Z.json`

- Profile: `deterministic-medium-fixture`
- Dataset size: `53`
- Query count: `10`
- Negative semantic probe count: `1`
- Iterations: `120`
- Recall@10 threshold: `0.85`
- Latest Recall@10: `1.0`
- Warm P95 threshold: `200.0ms`
- Latest warm P95: `9.484ms`
- Warm uncached P95: `64.304ms`
- Cold start latency: `272.959ms`
- Degraded stream case: passed for injected `bm25` `RuntimeError`
- Semantic negative top vector score: `0.0`

## Boundary

- Smoke: retained. The script remains bounded and suitable for local smoke
  validation.
- Medium fixture: verified. The script seeds a deterministic 53-memory corpus
  and reports recall, dataset size, query count, thresholds, cold/warm latency,
  per-stream latency, degraded-stream behavior, and semantic-negative behavior.
- Release gate: not verified. This result must not be used as proof of
  production-scale recall quality or final release status.

## Per-Stream Latency

Source: `.omx/goal/reports/hcms-benchmark-20260606T181248Z.md`

| Stream | P50 ms | P95 ms | P99 ms | Samples | Results |
| --- | ---: | ---: | ---: | ---: | ---: |
| `bm25` | `1.158` | `1.279` | `1.279` | `10` | `158` |
| `vector` | `1.005` | `1.023` | `1.023` | `10` | `158` |
| `graph` | `1.663` | `1.824` | `1.824` | `10` | `509` |
| `temporal` | `1.096` | `3.23` | `3.23` | `10` | `530` |

## Covered Cases

The medium fixture covers:

- causal why recall for Northstar canary policy
- retrieval diagnostics and fail-open stream behavior
- version history, `parent_id`, `supersedes`, and diff evidence
- zero-LLM deterministic correction extraction
- archive/restore low-retention behavior
- migration dry-run and rollback recall terms
- gateway health degraded diagnostics terms
- acronym, paraphrase, and low-overlap semantic cases
- benchmark boundary wording for smoke, medium fixture, and release gate
- unrelated semantic negative query without high-confidence vector match

## Not Verified Here

These checks require separate gates:

- production-scale corpus recall quality
- long-running soak or load test
- remote deployment gateway behavior
- final release decision
