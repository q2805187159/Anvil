# HCMS Implementation Audit

Date: 2026-06-07

This is the current Anvil-local remediation audit. It supersedes earlier HCMS
implementation audit language that converted targeted tests and a small
benchmark into broader release claims.

Current position:

- P1 and P2-01 through P2-07 are closed by targeted executable evidence.
- P2-08 is delivery hygiene: the worktree is still dirty and should be
  committed with the lore commit protocol after review.
- Production-scale recall, soak testing, remote deployment behavior, and final
  release decision are not verified by this audit.

## Closed Items

| ID | Status | Evidence |
| --- | --- | --- |
| P1-01 causal reasoning quality | Closed | `pytest tests/test_hcms_causal_quality.py tests/test_memory_runtime_tools.py::test_memory_tool_surfaces_hcms_recall_why_history_and_diff -q` -> `5 passed in 7.01s`. |
| P1-02 degradation and observability | Closed | `pytest tests/test_hcms_memory.py tests/test_gateway_hcms_memory_surfaces.py -q` -> `53 passed in 13.07s`; gateway health exposes degraded diagnostics. |
| P1-03 benchmark credibility | Closed | `run-hcms-benchmark-report.py --iterations 120` passed with deterministic medium fixture, dataset size `53`, query count `10`, Recall@10 `1.0`, warm P95 `9.484ms`; benchmark tests -> `3 passed in 25.12s`; report marks release gate as not verified. |
| P2-01 Bayesian confidence | Closed | `pytest tests/test_hcms_confidence.py -q` -> `4 passed in 0.21s`. |
| P2-02 frontend API and hook adapter | Closed | `npm test -- --run src/core/memory/api.test.ts memory-governance-panel` -> `11 passed`; API shapes include recall/search/why/list/get/delete/history/versions/relations/diff/health/benchmark. |
| P2-03 import and heavy-runtime boundary | Closed | `.\.venv\Scripts\python.exe -c "import anvil.memory; print('ok')"` -> `ok`; storage tests -> `16 passed in 1.07s`. |
| P2-04 migration dry-run and rollback | Closed | `pytest tests/test_hcms_migration.py -q` -> `9 passed in 0.34s`; tests preserve source data and cover migrated recall. |
| P2-05 semantic vector fixture | Closed | `pytest tests/test_hcms_benchmark.py tests/test_hcms_memory.py -q` -> `51 passed in 27.56s`; fixture includes acronym, paraphrase, low-overlap, and negative query checks. |
| P2-06 gateway route snapshot | Closed | `pytest tests/test_gateway_hcms_memory_surfaces.py -q` -> `5 passed in 11.32s`; frontend API route test -> `1 passed`. |
| P2-07 documentation synchronization | Closed | Anvil-local `docs/memory` reports added; `docs/guides/hcms-memory-api.md` updated to bounded benchmark language; legacy symbols documented as legacy-only context. |
| P2-08 delivery hygiene | Delivery note | Dirty worktree remains; final response must include `git status --short` and `git diff --stat` summary. |

## Interface Consistency

Gateway and frontend API paths are aligned by tests around these HCMS surfaces:

- `POST /memory/hcms/recall`
- `POST /memory/hcms/search`
- `POST /memory/search`
- `POST /memory/hcms/why`
- `GET /memory/list`
- `GET /memory/{memory_id}`
- `DELETE /memory/{memory_id}`
- `GET /memory/{memory_id}/history`
- `GET /memory/{memory_id}/versions`
- `GET /memory/{memory_id}/relations`
- `GET /memory/{memory_id}/diff`
- `GET /memory/admin/health`
- `POST /memory/admin/benchmark`

Runtime memory tools expose the same recall, why, history, diff, flush, CRUD,
and lifecycle operations without importing gateway or frontend code into
`backend/packages/harness`.

## Legacy Scope

`CuratedEntry`, `ProfileFacet`, `CuratedStoreManager`, `memory_platform`,
`summarization_middleware`, and `compaction_middleware` are legacy-only symbols
or historical strings when they appear in migration, compatibility tests, config
cleanup tests, or historical documentation. They must not re-enter the active
HCMS runtime path.

## Not Verified

The following claims are intentionally not made here:

- production-scale recall quality
- long-running load or soak stability
- remote deployment readiness
- clean release commit state
- source-control review outcome
