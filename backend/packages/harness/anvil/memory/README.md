# HCMS Memory

`anvil.memory` is Anvil's only forward memory implementation. It owns capture,
storage, lifecycle, recall, causal reasoning, version history, runtime tools,
and middleware-facing facades below the reusable harness boundary.

The old `anvil.memory_platform` package and provider/review governance model
are not compatibility paths. App, shell, and frontend layers consume HCMS
through typed manager/service APIs and generated gateway contracts. Legacy
`memory_platform` config keys are dropped during config normalization rather
than treated as a compatibility surface.

## Architecture Map

| HCMS layer | Harness implementation |
| --- | --- |
| Observation | `MemoryCaptureEnvelope`, `Observation`, `DebouncedMemoryQueue`, capture middleware |
| Compilation | `KnowledgeCompiler`, structured Markdown frontmatter, schema validation, deterministic self-correction |
| Structured memory | `Memory`, `Evidence`, categories, confidence, salience, lifecycle states |
| Relation weaving | `Entity`, `Relation`, relation classifier, graph and causal edge construction |
| Semantic index | BM25, deterministic vector, graph, temporal-causal retrievers, hybrid storage indexes |
| Active recall | `MemoryService.search()`, `build_injection_view()`, prefetch middleware, runtime tools |
| Causal reasoning | `CausalEdge`, `CausalPath`, incoming `why()` causes, downstream effect/impact paths, counterfactual impact projection |

## Main Entry Points

Use `MemoryManager.from_config(...)` for runtime-owned HCMS:

```python
from anvil.config import HCMSRuntimeConfig
from anvil.memory import MemoryManager

manager = MemoryManager.from_config(
    config=HCMSRuntimeConfig(enabled=True, storage_backend="hybrid"),
    base_path=".anvil/runtime",
)
```

Use `MemoryService` directly for focused harness tests or embedded local flows:

```python
from anvil.memory import (
    DebouncedMemoryQueue,
    FileMemoryStore,
    HeuristicMemoryUpdater,
    MemoryService,
)

service = MemoryService(
    store=FileMemoryStore(".anvil/hcms-test"),
    queue=DebouncedMemoryQueue(),
    updater=HeuristicMemoryUpdater(),
)
```

Runtime tools are built with `build_memory_tools(manager)`. The gateway exposes
thin projections under `/memory`; see `docs/guides/hcms-memory-api.md`.

HCMS Git-like version metadata requires the top-level `git` base config. The
default token environment variable is `GITHUB_TOKEN`, and operators can edit
or test it from the browser Configuration Center's Basic Configuration surface.

## Data Flow

1. Capture middleware builds a `MemoryCaptureEnvelope` from completed runtime
   messages and queues it by thread/namespace. `detect_capture_signals()`
   provides the shared multilingual correction/reinforcement/remember detector
   used by capture, compilation, and queue window selection; negated phrases
   such as `not correct` do not produce reinforcement.
2. `DebouncedMemoryQueue` coalesces repeated captures without dropping
   messages, computes a `CaptureSignalProfile`, flushes once the configured max
   batch turn count is reached, then reports avoided update work through
   `cost_reduction_ratio()`. Signal strength is explicit and additive:
   correction `0.5`, reinforcement `0.3`, and remember/preference `0.2`.
   Corrections and explicit remember/preference requests use
   `min_window_seconds`, strength `>=0.3` or longer text uses
   `default_window_seconds`, and very low-signal captures use
   `max_window_seconds`. The queue keeps synchronous middleware compatibility
   through `enqueue`/`pop_next` and exposes asyncio-safe equivalents
   `enqueue_async`, `get_pending_async`, `pop_next_async`, and
   `pending_count_async` for async capture pipelines.
   `hcms.update_queue.enabled=false` keeps capture enabled but disables waiting,
   so captures process immediately. `debounce_seconds` acts as a fixed-window
   compatibility alias when adaptive window fields are omitted.
3. `MemoryService.process_pending()` compiles observations into structured
   Markdown memories, evidence, entities, relations, causal edges, versions,
   metrics, and summaries. Callers that need a provider-planned update can use
   `build_structured_update_prompt()` and
   `parse_structured_update_response()` to convert the documented
   `newFacts`/`updates`/`removals` JSON contract into a `MemoryUpdatePlan`.
   `StructuredMemoryUpdater` applies that plan with Bayesian confidence
   updates, typed evidence accumulation, source-thread version metadata, and a
   rule-based zero-LLM fallback when the provider is absent or returns invalid
   JSON. The rule-based fallback covers the documented preference forms
   `prefer ... instead of/over ...`, `I like using ...`, and
   `I don't like ...`; explicit corrections are recorded as
   `MemoryCategory.CORRECTION` with correction evidence/source-error metadata.
   New facts from both provider JSON and rule-based plans are compiled
   through `KnowledgeCompiler.self_correct_markdown_schema()`, so
   `Memory.content` keeps the same frontmatter, source thread, observation id,
   evidence, relation, and metadata sections as observation-compiled memories.
   Provider-planned new facts with confidence below `0.8` are persisted as
   `provisional` memories; higher-confidence facts are `active`. Provisional
   memories remain durable and versioned, but active recall and relation
   weaving only use active memories until a later update promotes them.
   Runtime config selects this path with `hcms.updater.mode=structured`; the
   default `heuristic` mode keeps the compiler-backed deterministic capture
   behavior, and `rule_based` applies direct structured plans without a provider.
   If compilation, lifecycle maintenance, or persistence fails after an envelope
   is popped from the queue, `process_pending()` requeues that envelope with
   `processing_attempts`, `last_processing_error`, and
   `last_processing_failed_at` metadata before re-raising the original error.
   Agent middleware can still fail open, while the capture itself remains
   recoverable on the next flush or maintenance run.
4. The selected store persists the state. `hybrid` writes human-readable
   Markdown, SQLite search metadata, JSONL versions, and a full-state sidecar.
   Markdown frontmatter exposes the documented audit fields directly:
   concepts, source thread, source agent, source type, access count, and
   reasoning, while the full typed payload remains embedded for lossless
   round-trips.
   Version records include metadata snapshots for confidence, salience,
   lifecycle state, and evidence ids so Git-like diffs can report evidence and
   confidence changes as well as content changes. `MemoryVersionControl`
   preserves `parent_id` and `supersedes` chains even when used with a minimal
   storage backend that does not provide its own `update_memory` helper.
5. Prefetch middleware and tools call four-stream recall, receive ranked
   evidence, and render bounded prompt context with diagnostics.
6. Lifecycle APIs archive, restore, forget, delete, import, export, benchmark,
   and run bounded maintenance.

Manual, gateway, runtime-tool, and direct hybrid-backend writes use the same
compiled memory schema as captured observations. `MemoryService.create_memory`,
`MemoryService.update_memory`, `FileSystemMemoryBackend.save_memory`,
`FileMemoryStore.save`, `HybridMemoryBackend.save_memory`,
`HybridMemoryBackend.save`, `HybridMemoryBackend.create_memory`, hybrid
`update_memory`, hybrid full-state sidecars, and Git-like three-way merge
outputs compile the supplied human fact body through
`KnowledgeCompiler.self_correct_markdown_schema()` before persistence when the
incoming `Memory.content` is not already schema-valid. HCMS recall strips
compiled frontmatter and required Evidence/Relations/Metadata boilerplate for
lexical, vector, and MMR similarity scoring, so the durable Markdown schema does
not pollute ranking behavior.
When a manual/API update changes `content` together with `category` or
`confidence`, HCMS recompiles the Markdown frontmatter with the updated
metadata before writing the new Git-like version record.
HCMS accepts and preserves the seven design-document categories
`preference`, `knowledge`, `context`, `behavior`, `goal`, `correction`, and
`pattern`, while retaining runtime extension categories such as
`project_context`, `procedure`, `decision`, `error_pattern`,
`preference_profile`, `relationship`, and `note` for existing operational
memories.
Lifecycle state accepts the documented `active`, `provisional`, `archived`,
`forgotten`, and `deleted` values plus internal `superseded`/`review` states;
hard delete still uses the delete API so connected relation and causal edges are
removed through one explicit outlet.

## Migration Helpers

`anvil.memory.migration` provides pure harness converters for the source shapes
described in `docs/memory/migration-guide.md`:

- `migrate_agentmemory_payloads(...)`
- `migrate_deerflow_payload(...)`
- `migrate_legacy_anvil_payloads(...)`
- `validate_migration_result(...)`
- `run_memory_migration(...)`
- `DualWriteMemoryService`

The helpers do not import app/gateway code, do not write storage, and do not
keep removed legacy runtimes alive. Results include converted memories plus
per-item errors so migration batches can continue after malformed rows.
Converted records use `SourceType.IMPORT`, retain legacy ids in metadata,
compile content through the same HCMS Markdown schema as manual writes, and can
be saved through `FileMemoryStore`, `HybridMemoryBackend`, or `MemoryService`.
`DualWriteMemoryService` is a migration-period bridge for adapter-owned legacy
clients: saves are attempted against both old and HCMS clients with typed
partial-failure reporting, while reads prefer HCMS and fall back to the legacy
client if HCMS retrieval fails.

`run_memory_migration(...)` is the source-file runner for the documented
migration CLI shape. It reads AgentMemory JSON files/directories, DeerFlow
`memory.json` roots, and legacy Anvil JSON or SQLite `curated.db` sources,
validates converted Markdown when requested, and writes into an HCMS
`FileMemoryStore` namespace. It reports seen, migrated, written, validation,
and per-item error counts so operators can dry-run or publish a migration
without reviving legacy memory runtimes.

`AgentMemoryCompatLayer` provides the AgentMemory-style async
`observe(...)`, `consolidate(...)`, and `search(...)` facade described by the
migration guide. It stages observations by session, persists them through the
HCMS service API during consolidation, and projects HCMS retrieval results back
to legacy-shaped dictionaries without importing or reviving the removed legacy
runtime.

## Runtime Recall Configuration

`MemoryManager.from_config(...)` maps `hcms.recall` into the runtime
`FourStreamRetriever`.

- `max_candidates` controls the default and maximum candidate limit.
- `max_evidence` caps evidence entries included in prompt injection.
- `min_relevance_score` filters low-scoring recall results before runtime
  injection.
- `turn_recall_token_budget` controls prompt-injection budget.
- `bm25_weight`, `vector_weight`, `graph_weight`, and `temporal_weight`
  control the four stream contributions.
- `rrf_k` controls reciprocal-rank fusion sensitivity.
- `enable_adaptive_weights` toggles query-intent weight adjustment.
  The query analyzer extracts capitalized entities plus common English and
  Chinese time ranges such as `yesterday`, `today`, `last week`, `最近`, and
  `上周`; it also recognizes documented causal intent terms including
  `why`, `cause`, `effect`, `impact`, `consequence`, `enabled`, `prevented`,
  `原因`, `为什么`, `导致`, `影响`, `使得`, and `阻止`. Temporal-causal queries
  then boost memories whose timestamps fall in that range.
- `enable_cache`, `cache_ttl`, and `cache_max_entries` control the bounded L1
  recall cache and `cache_stats()` diagnostics.
- `enable_mmr` and `mmr_lambda` control post-RRF diversity reranking.

## Runtime Maintenance Configuration

`MemoryManager.run_maintenance(...)` uses `hcms.maintenance` as its runtime
default when a caller does not pass explicit overrides.

- `enabled=false` skips maintenance work and leaves pending capture plus memory
  lifecycle state untouched.
- `policy`, `layer_id`, and `limit` choose the governance plan scope.
- `execute=false` makes maintenance dry-run by default; explicit tool calls can
  still override `dry_run`.
- `max_archive_per_run`, `max_quality_inspections_per_run`, and
  `max_reinforce_per_run` cap per-action execution so automatic maintenance is
  bounded.
- `include_health` controls pre/post health snapshots in maintenance output.
- `automation_enabled`, `tick_seconds`, `interval_seconds`,
  `min_idle_seconds`, and `run_reflection_due_jobs` are reflected by
  `maintenance_automation_status()`.

## Storage

`hcms.storage_backend` accepts:

- `hybrid`: default production path with Markdown, SQLite, version history, and
  full-state plus `since`-bounded incremental namespace backup/restore.
- `filesystem`: compact JSON state storage for lightweight local harness runs.

Aliases such as `markdown` normalize to `hybrid`; unsupported values fail config
validation before manager construction.

## Error And Fallback Policy

- LLM unavailable: deterministic compiler/updater path still captures and
  updates memory.
- Invalid compiled Markdown: schema validation reports errors and deterministic
  self-correction patches required sections.
- Individual recall stream failure: BM25, vector, graph, and temporal-causal
  streams are isolated; a failed stream contributes no scores while the
  remaining streams still produce fused recall results.
- Recall empty or weak: HCMS fails open with no injection rather than blocking
  the agent run.
- Prompt-time recall exception: the agent continues and records injection
  diagnostics with the error type.
- Capture/update processing exception: popped envelopes are requeued with typed
  retry metadata before the exception propagates to the caller.
- Storage conflict or malformed backup: callers receive typed storage errors.
- Low-value or expired memory: lifecycle maintenance archives before hard
  deletion so restore remains available. Archived or forgotten cold memories
  whose retention score remains below threshold after
  `hcms.forgetting.low_importance_ttl_days` are removed from hot state on a
  later maintenance pass, with relation and causal edges cleaned and a final
  version record retained.

## Verification

Run focused HCMS gates from the repository root:

```powershell
backend\.venv\Scripts\python.exe scripts\generate-contracts.py --check
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_storage.py backend\tests\test_hcms_memory.py backend\tests\test_hcms_benchmark.py backend\tests\test_memory_runtime_tools.py backend\tests\test_gateway_hcms_memory_surfaces.py backend\tests\test_self_upgrade_health.py backend\tests\test_phase5_integration.py -k "hcms or memory"
backend\.venv\Scripts\python.exe scripts\run-hcms-benchmark-report.py --iterations 120
```

Expected release evidence is recorded in `docs/memory/hcms-benchmark-report.md`,
`docs/memory/hcms-implementation-audit.md`, and
`.omx/task-updates/hcms-memory-release-gate-final-20260606.md`.
