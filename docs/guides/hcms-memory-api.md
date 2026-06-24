# HCMS Memory API

This guide documents the release-facing HCMS memory API exposed by the FastAPI
gateway. The harness implementation remains the source of truth; gateway routes
only validate requests and project typed views.

Base path: `/memory`

## Core Surfaces

| Route | Method | Purpose |
| --- | --- | --- |
| `/memory/overview` | `GET` | Return active HCMS engine, stores, summary budgets, and memory counts. |
| `/memory/layers` | `GET` | List the session, user, and workspace memory layers. |
| `/memory/stores` | `GET` | List HCMS stores and their budgets. |
| `/memory/layers/{layer}/entries` | `GET` | Read user or workspace entries. |
| `/memory/layers/{layer}/entries` | `POST` | Create a user or workspace memory. |
| `/memory/layers/{layer}/entries/{entry_id}` | `PATCH` | Update content, category, confidence, or salience. |
| `/memory/layers/{layer}/entries/{entry_id}` | `DELETE` | Delete a layer memory. |
| `/memory/stores/{store_id}/entries` | `GET` | Read entries by HCMS store id. |
| `/memory/stores/{store_id}/entries` | `POST` | Create a memory in a concrete store. |
| `/memory/stores/{store_id}/entries/{entry_id}` | `PATCH` | Update a store memory. |
| `/memory/stores/{store_id}/entries/{entry_id}` | `DELETE` | Delete a store memory. |
| `/memory/search` | `POST` | Search active HCMS memory through the four-stream recall engine. |
| `/memory/list` | `GET` | Public checklist alias for structured HCMS memory listing. |
| `/memory/{memory_id}` | `GET` | Public checklist alias for reading one structured HCMS memory. |
| `/memory/{memory_id}` | `DELETE` | Public checklist alias for hard-deleting one HCMS memory. |
| `/memory/{memory_id}/versions` | `GET` | Public checklist alias for Git-like version history. |
| `/memory/{memory_id}/relations` | `GET` | Public checklist alias for connected relation graph edges. |
| `/memory/hcms/search` | `POST` | HCMS-native search alias for the same four-stream recall engine. |
| `/memory/hcms/memories` | `GET` | List structured HCMS memories with query, state, category, layer, limit, and offset filters. |
| `/memory/hcms/memories/{memory_id}` | `GET` | Read one structured HCMS memory with evidence, entities, concepts, and metadata. |
| `/memory/hcms/memories/{memory_id}` | `DELETE` | Hard-delete one HCMS memory and remove connected relation/causal edges from storage. |
| `/memory/hcms/memories/{memory_id}/versions` | `GET` | Read Git-like version history for one memory. |
| `/memory/hcms/memories/{memory_id}/relations` | `GET` | Read relation graph edges connected to one memory, including source/target memory projections. |

Supported writable layer ids are `user` and `workspace`. Session memory is a
derived archive/search view and is not directly writable.

The top-level checklist routes (`/memory/list`, `/memory/{memory_id}`,
`/memory/{memory_id}/versions`, `/memory/{memory_id}/relations`, and
`DELETE /memory/{memory_id}`) are thin aliases for the HCMS-native
`/memory/hcms/memories...` routes. They exist to satisfy stable release-facing
API names without duplicating HCMS ranking, history, relation, or lifecycle
logic.

Version history entries include content, summary, unified diff text, reason,
created timestamp, and a metadata snapshot containing confidence, salience,
lifecycle state, source info, and evidence ids. Harness `MemoryVersionControl`
uses those snapshots to report confidence deltas and evidence added/removed in
Git-like diffs.

## HCMS Recall And Explanation

### `POST /memory/hcms/recall`

`POST /memory/hcms/search` and `POST /memory/search` are thin public aliases for
the same HCMS recall pipeline. They exist so clients can depend on a literal
search route while the harness keeps one ranking implementation.

Runs four-stream HCMS recall over independently runnable BM25, deterministic
vector, graph, and temporal-causal streams. HCMS fuses stream scores with
adaptive RRF and applies MMR diversity ranking before returning bounded results.
The harness retriever also maintains a bounded L1 LRU cache. Cache keys include
the namespace, query, limit, and memory-state fingerprint; `cache_stats()`
reports max entries, TTL, hits, misses, writes, evictions, expirations,
bypasses, and hit rate.

Runtime recall behavior is configured under `hcms.recall`. `max_candidates`
sets the default and maximum retrieval limit used by the runtime manager,
`max_evidence` caps evidence entries included in prompt injection,
`min_relevance_score` filters low-scoring recall results before runtime
injection,
`bm25_weight`, `vector_weight`, `graph_weight`, and `temporal_weight` control
the four stream contributions, `rrf_k` controls reciprocal-rank fusion,
`enable_adaptive_weights` toggles query-intent weight adjustment. The harness
query analyzer extracts entities and common English/Chinese temporal ranges
such as `yesterday`, `today`, `last week`, `最近`, and `上周`; temporal-causal
queries also recognize documented causal intent terms such as `why`, `cause`,
`effect`, `impact`, `consequence`, `enabled`, `prevented`, `原因`, `为什么`,
`导致`, `影响`, `使得`, and `阻止`, then boost memories inside the parsed time
range before RRF fusion.
`enable_cache`, `cache_ttl`, and `cache_max_entries` control the bounded L1
cache, and `enable_mmr` plus `mmr_lambda` control diversity reranking after
RRF fusion.

By default `hcms.recall.injection_mode=context_v2`: runtime recall is projected
to Runtime Context V2 `ContextBlock` candidates and competes under the same
token budget and assembly trace as tools, skills, workspace state, and prompt
sections. `legacy_prompt_append` remains available only as an explicit
compatibility fallback.

Request:

```json
{
  "query": "why did the deployment fail",
  "limit": 10
}
```

Response shape:

- `query`: original query
- `items[]`: ranked recall items with `score`, `raw_scores`, stream `ranks`,
  optional `highlight`, `explanation`, and full `memory`
- `metrics`: HCMS latency, recall counters, deterministic update counters, and
  LLM calls avoided

## Capture, Flush, And Cost Metrics

HCMS capture uses `DebouncedMemoryQueue` before compilation. Repeated captures
for the same `thread_id` and namespace are merged into one pending batch while
preserving all user, assistant, correction, and reinforcement messages. The
queue reports `cost_reduction_ratio()` as avoided baseline update calls divided
by raw capture calls. The queue remains compatible with synchronous middleware
through `enqueue`, `get_pending`, `pop_next`, and `pending_count`, and also
provides asyncio-safe `enqueue_async`, `get_pending_async`, `pop_next_async`,
and `pending_count_async` for async capture pipelines sharing the same queue
state.

Capture signal detection is centralized in `detect_capture_signals()`. It
recognizes multilingual correction, reinforcement, and remember/preference
signals for capture, compilation, and queue scheduling. Negated reinforcement
phrases such as `not correct` and `not good` are treated as correction pressure
instead of positive confirmation.

Current regression evidence covers 10 low-signal captures coalescing into one
zero-LLM compile batch: 10 observations are retained, `deterministic_updates`
increments once, `llm_calls_avoided` is 10, and `cost_reduction_ratio()` remains
`>= 0.85` after flush.

Runtime integration evidence also covers the default manager path: when HCMS is
enabled by config and no adapter-provided memory manager is passed, RunEngine
uses the runtime-created HCMS manager to record completed turns, immediately
flush high-signal correction/remember captures, keep low-signal captures pending
until their debounce window or an explicit admin flush, and inject recalled
memory on following turns.

The capture debounce windows are configured under `hcms.update_queue`.
`enabled=false` disables waiting and processes queued captures immediately.
`debounce_seconds` is a fixed-window compatibility alias when adaptive
`min_window_seconds`/`default_window_seconds`/`max_window_seconds` are omitted.
Each queued capture exposes a `CaptureSignalProfile` so callers and tests can
inspect the adaptive choice: correction contributes `0.5`, reinforcement
contributes `0.3`, and remember/preference language contributes `0.2`.
Corrections and explicit remember/preference requests map to
`min_window_seconds`, strength `>=0.3` or long text maps to
`default_window_seconds`, and very low-signal captures map to
`max_window_seconds`. `min_batch_turns` and `max_batch_turns` additionally cap
how many completed turns can coalesce before the queue is flushed; reaching
`max_batch_turns` makes the pending low-signal batch immediately processable
even when its time window has not elapsed.

Long observations are compressed by harness-owned `MultiLevelCompressor` before
they become structured memories. Compression metadata is preserved on each
`Observation`: `compression_method`, `compression_level`, `compression_ratio`,
`information_retention_score`, and `preserved_terms`. Current regression
evidence proves deterministic level 3 compression exceeds `8:1`.

Compiled memories store a human-readable structured Markdown body in
`Memory.content`. The compiler writes frontmatter with `memory_id`, `category`,
`confidence`, `created_at`, `source_thread_id`, `observation_id`, and
`evidence_count`, then requires `## Evidence`, `## Relations`, and
`## Metadata` sections. `KnowledgeCompiler.validate_markdown_schema()` reports
schema errors, and `self_correct_markdown_schema()` deterministically patches
missing frontmatter or required sections without requiring an LLM.
This contract applies to observation capture, structured updater/provider new
facts, manual/gateway/runtime-tool CRUD writes, direct hybrid-backend
`save_memory`/`create_memory`/`update_memory`, direct
`FileSystemMemoryBackend.save_memory`, direct JSON-state
`FileMemoryStore.save`, hybrid full-state `save` sidecars, and Git-like
three-way merge outputs. Direct filesystem, JSON state, or hybrid storage calls
normalize bare `Memory.content` into compiled Markdown unless the content
already validates against the compiled schema. Clients that only need the human
fact should use the memory `summary` or strip compiled sections; the harness
recall engine strips frontmatter and Evidence/Relations/Metadata boilerplate
before lexical, vector, graph, temporal, and MMR scoring.
Filesystem and hybrid Markdown files also expose audit frontmatter for
concepts, source thread, source agent, source type, access count, and reasoning;
the embedded HCMS JSON payload remains the lossless storage contract.
Manual/API updates that change `content` and metadata in the same request
recompile the Markdown frontmatter with the new category and confidence before
the version record is appended, so object fields, durable content, and diff
metadata stay aligned.
Category input preserves the seven HCMS design-document values:
`preference`, `knowledge`, `context`, `behavior`, `goal`, `correction`, and
`pattern`. Runtime extension values such as `project_context`, `procedure`,
`decision`, `error_pattern`, `preference_profile`, `relationship`, and `note`
remain valid for existing operational memories and migration compatibility.

The relation-weaving layer uses `KnowledgeCompiler.classify_relation()` as the
single harness-owned classifier for the 15 documented relation types:
`similar_to`, `contradicts`, `refines`, `generalizes`, `happens_before`,
`happens_after`, `concurrent_with`, `causes`, `caused_by`, `enables`,
`prevents`, `part_of`, `has_part`, `instance_of`, and `related_to`.

`anvil.memory.updater.RuleBasedMemoryUpdater` is the explicit zero-LLM update
surface for callers that need structured update plans before mutating state. It
produces `MemoryUpdatePlan` values with `new_facts`, `updates`, and `removals`,
then applies them with Bayesian confidence updates, typed evidence, lifecycle
transitions, and version records. The deterministic extraction rules cover the
documented preference patterns `prefer ... instead of/over ...`,
`I like using ...`, and `I don't like ...`; explicit corrections are emitted as
`correction` facts with correction evidence metadata instead of generic
decision records. New facts are deterministically compiled into the same
schema-valid Markdown frontmatter/body format used by observation compilation,
including source thread, observation id, evidence, relation, and metadata
sections; provider-planned updates do not store bare text in `Memory.content`.
`build_structured_update_prompt()` and
`parse_structured_update_response()` define the provider contract for the
documented JSON keys `newFacts`, `updates`, and `removals`; fenced JSON is
accepted, low-confidence facts are filtered, invalid categories fall back to
`note`, and `confidenceDelta` is bounded before application.
Provider-planned new facts with confidence below `0.8` are stored as
`provisional`; facts at `0.8` or above are `active`. Provisional facts remain
durable/versioned but are not included in active recall until promoted by a
later update.
`StructuredMemoryUpdater` can call a provider with that prompt and falls back to
the rule-based path if no valid response is available. The runtime capture path
still uses `HeuristicMemoryUpdater` through the compiler for full observation
batches.

### `POST /memory/hcms/why`

Returns causal paths for a query. If explicit causal edges are unavailable, HCMS
returns bounded fallback paths from the recalled memories so the UI and tools can
still explain what evidence was surfaced. `why`/cause queries traverse incoming
causal edges to explain reasons; `effect`/`impact`/`consequence` queries
traverse downstream edges to show outcomes.

Request:

```json
{
  "query": "why did the deployment fail",
  "limit": 4
}
```

Response shape:

- `query`
- `paths[]`
- `paths[].nodes[]`: memory id, category/event type, timestamp, confidence
- `paths[].edges[]`: causal edge source, target, type, strength, evidence ids
- `paths[].confidence` and `total_strength`
- `paths[].explanation_kind`: `causal`, `correlation`, or `degraded`
- `paths[].degradation_reason`: present when HCMS falls back from a verified
  causal path because of low confidence, conflicting evidence, no causal path,
  or missing recalled memory
- `paths[].evidence_summary`: bounded sanitized evidence text for degraded or
  correlation fallback paths

### Harness Counterfactual Reasoning

The harness service and runtime `memory` tool also expose counterfactual
reasoning for L7 causal analysis. This is currently a harness/runtime surface,
not a separate gateway route. It is read-only: HCMS selects a causal anchor from
the assumed removed event, traverses downstream causal edges, and returns
projected impacts without mutating memory state.

Runtime tool call:

```json
{
  "action": "counterfactual",
  "layer": "workspace",
  "content": "What if direct full rollout had not happened?",
  "old_text": "direct full rollout",
  "limit": 3
}
```

Response shape:

- `query`
- `assumption`
- `removed_memory_id`
- `impacts[]`
- `impacts[].memory_id`, `summary`, `projected_change`, `confidence`,
  `evidence`, `causal_depth`, and `relation_type`
- `evidence`
- `confidence`
- `engine_notes`

## Version History And Diff

### `GET /memory/hcms/memories`

`GET /memory/list` is the public checklist alias for the same payload.

Lists structured HCMS memories for browsing. Supported query parameters:

- `query`: optional text filter matched across id, content, summary, tags,
  entities, and concepts
- `state`: optional lifecycle filter such as `active`, `archived`, or
  `forgotten`; defaults to `all`
- `category`: optional exact category filter
- `layer_id`: optional `user`, `workspace`, `session`, or `all`; defaults to
  `all`
- `limit`: page size, bounded by the harness to `1..100`
- `offset`: zero-based page offset

Response shape:

- `items[]`: `HCMSMemoryView` entries with the same shape returned by single
  memory reads
- `total`
- `limit`
- `offset`
- `query`, `state`, `category`, `layer_id`
- `engine_notes`

Invalid layer filters return `400` with `error="invalid_hcms_memory_filter"`.

### `GET /memory/hcms/memories/{memory_id}`

`GET /memory/{memory_id}` is the public checklist alias for the same payload.

Returns one structured HCMS memory:

- `memory`: content, summary, category, confidence, salience, lifecycle state,
  entities, concepts, evidence, metadata, and timestamps
- `engine_notes`

Missing memory ids return `404` with `error="memory_not_found"`.

### `DELETE /memory/hcms/memories/{memory_id}`

`DELETE /memory/{memory_id}` is the public checklist alias for the same
operation.

Hard-deletes one HCMS memory through the harness memory manager. The delete
operation removes the memory and connected relation/causal edges from the
durable HCMS state; use lifecycle governance actions when archive, forget, or
restore semantics are desired instead of hard deletion.
The lifecycle enum still accepts `deleted` for imported or serialized HCMS data,
but the public delete route is the explicit outlet that removes connected graph
state.
Automatic forgetting is two-stage: active low-retention memories are archived
first so restore remains possible, while already archived or forgotten cold
memories older than `hcms.forgetting.low_importance_ttl_days` are removed from
hot state on a later maintenance pass. That cold cleanup removes connected
relation/causal edges and appends a final `auto_delete_expired_cold` version
record for auditability.

Response shape:

- `memory_id`
- `status`: `deleted`
- `deleted`: `true`
- `engine_notes`

Missing memory ids return `404` with `error="memory_not_found"`.

### `GET /memory/hcms/memories/{memory_id}/relations`

`GET /memory/{memory_id}/relations` is the public checklist alias for the same
payload.

Returns relation graph edges connected to one memory:

- `memory_id`
- `relations[]`
- `relations[].source_memory_id`
- `relations[].target_memory_id`
- `relations[].relation_type`
- `relations[].weight`
- `relations[].confidence`
- `relations[].source_memory`
- `relations[].target_memory`
- `engine_notes`

### `GET /memory/hcms/memories/{memory_id}/versions`

`GET /memory/{memory_id}/versions` is the public checklist alias for the same
payload.

`GET /memory/hcms/memories/{memory_id}/history` is kept as a compatibility
alias for the same version-history payload.

Returns Git-like version records for one memory:

- `version_id`
- `memory_id`
- `version`
- `parent_id`
- `content`
- `summary`
- `diff`
- `reason`
- `created_at`

### `GET /memory/hcms/memories/{memory_id}/diff`

Returns the latest Git-like diff metadata for one memory:

- `memory_id`
- `from_version`
- `to_version`
- `diff`
- `confidence_delta`
- `evidence_added`
- `evidence_removed`
- `engine_notes`

The response is computed by the harness from version-record metadata snapshots,
so runtime tools and gateway clients see the same content diff, confidence
delta, and evidence-id additions/removals.

### Harness Three-Way Merge

The release-facing gateway exposes history and diff views. Harness storage also
provides `MemoryVersionControl.merge_versions()` for Git-like three-way merge
workflows. It accepts a base version and two derived versions, writes a new
merge version when line-level edits do not conflict, and returns conflict
records without mutating storage when both sides edit the same base line
differently.

### Configured Storage Backends

`hcms.storage_backend` selects the harness-owned storage implementation used by
`MemoryManager.from_config()` and the runtime memory control plane. The core
`anvil.memory.HCMSConfig.storage_backend` field uses the same default,
aliases, and rejection rules so direct harness construction cannot silently
select a different backend.

- `hybrid` is the default production path. It stores human-readable Markdown
  memories, a SQLite search index, JSONL version records, and a namespace state
  sidecar so observations, entities, relations, causal edges, metrics, and
  summaries survive manager reloads.
- `filesystem` keeps the compact JSON `MemoryState` store for lightweight local
  runs and compatibility with focused service tests.
- Alias `markdown` normalizes to `hybrid`; invalid values fail config
  validation.

### Harness Namespace Backup And Restore

`HybridMemoryBackend.export_namespace_backup(namespace, destination)` writes a
portable JSON backup containing a manifest, all memories in the namespace,
their version records, and the full `MemoryState` sidecar. Passing
`since=<datetime-or-iso-string>` creates an incremental backup: only memories
created or updated after that timestamp, related version records, and graph
edges whose endpoints are included are exported, and the manifest records
`incremental=true` plus the `since` timestamp. Restored hybrid backends keep
observations, entities, relations, causal edges, summaries, metrics, version
records, Markdown memories, and the SQLite search index in sync.
`restore_namespace_backup(path)` restores those records into a backend and
rebuilds the SQLite search index through normal `save_memory()` indexing, so
restored memories remain searchable immediately. Restore validates the backup
object, manifest, memories/version payload lists, and optional full-state
payload before mutating storage. Malformed JSON, missing manifests, invalid
lists or state payloads, and validation failures raise typed `StorageError`
values.

This is a harness storage API, not a gateway route. It is intended for local
backup, migration, and disaster-recovery workflows.

## Trace, Health, And Governance

| Route | Method | Purpose |
| --- | --- | --- |
| `/memory/trace` | `POST` | Inspect recent HCMS recall/capture traces for a target memory or thread. |
| `/memory/admin/health` | `GET` | Return store health, quality issues, missing evidence, stale memories, and recommendations. |
| `/memory/admin/flush` | `POST` | Drain pending HCMS observations through the zero-LLM compiler. |
| `/memory/admin/memories/{memory_id}/govern` | `POST` | Archive, forget, restore, reinforce, or mark a memory for inspection. |
| `/memory/admin/governance` | `POST` | Plan or execute bounded batch governance. |
| `/memory/admin/maintenance` | `POST` | Run bounded HCMS maintenance. |
| `/memory/admin/benchmark` | `POST` | Run an ad hoc recall benchmark. |
| `/memory/admin/benchmark/suites` | `GET`/`POST` | List or create benchmark suites. |
| `/memory/admin/benchmark/suites/{suite_id}/run` | `POST` | Run a stored benchmark suite. |

Maintenance uses `hcms.maintenance` as the default runtime policy when the
request omits a field. `policy`, `layer_id`, and `limit` define the governance
scope; `execute=false` makes the run a dry-run by default; and
`max_archive_per_run`, `max_quality_inspections_per_run`, and
`max_reinforce_per_run` cap per-action execution. `include_health` controls
pre/post health snapshots in the response, while `maintenance_automation_status`
reflects `automation_enabled`, tick/interval/idleness fields, execution mode,
and reflection-job settings.

Health responses include `diagnostics[]`. Retrieval stream failures are exposed
as degraded HCMS diagnostics with component, reason, stream name, error type,
and count; gateway clients should treat a non-empty diagnostics list as a
degraded health signal instead of assuming a silent fail-open path.

## Runtime Tools

Agent runtime tools expose the same HCMS capabilities:

- `memory` actions: `recall`, `why`, `counterfactual`, `history`, `diff`,
  `flush`, CRUD and lifecycle operations
- `session_search`: prior-session search backed by HCMS/session archive
- `memory_trace`: HCMS recall/capture trace inspection

Tool output is scrubbed before model-visible injection and bounded by runtime
tool-output budgets.

`MemoryVersionControl` maintains Git-like `parent_id` and `supersedes` chains
for both hybrid storage updates and generic storage fallback updates, so
version history remains traversable even outside the production hybrid backend.

## Cursor-Compatible Context Export

`MemoryManager.render_cursor_memory_rule()` renders active HCMS memories as a
Cursor-compatible Markdown rule document. `export_cursor_memory_rule()` writes
that bounded document under the caller's workspace, by default:

```text
.cursor/rules/hcms-memory.md
```

The project context loader already discovers `.cursor/rules/*.md`, so exported
HCMS memories become readable through the same scoped context-file path used for
normal Cursor rules. The export validates that the target path remains inside
the workspace root and scrubs memory-context tags/secrets before writing.
Each exported memory includes the HCMS id, layer, category, version, lifecycle
state, confidence, salience, source thread/type, evidence ids, evidence text,
and summary so Cursor receives structured hints instead of only a flattened
sentence.

## Error And Fallback Contract

- HCMS is harness-owned. Gateway and frontend never compute recall ranking,
  lifecycle policy, or path truth.
- Storage conflicts are reported as typed errors by the storage backend.
- Malformed or incomplete namespace backup files are reported as typed
  `StorageError` values before restore mutates storage.
- Namespace backup restore preserves the full hybrid `MemoryState`, including
  causal edges used by why/counterfactual reasoning.
- Individual recall stream failure is fail-open: BM25, vector, graph, and
  temporal-causal streams are isolated, so one unavailable stream contributes no
  scores while remaining streams still fuse and return bounded results. HCMS
  records diagnostics with stream name, error type, and count, and gateway
  health exposes the degraded state.
- Prompt-time recall failure is fail-open: the agent run continues without HCMS
  injection and records diagnostics.
- Capture/update processing failure is recoverable: if compilation, lifecycle
  maintenance, or persistence fails after an envelope is popped from the queue,
  HCMS requeues that envelope with `processing_attempts`,
  `last_processing_error`, and `last_processing_failed_at` metadata before the
  exception reaches the caller. Middleware may continue the run, and a later
  flush or maintenance run can retry the same capture.
- LLM unavailability does not block capture or update because the active compiler
  and updater are deterministic zero-LLM paths.
- Weak recall falls back to the remaining streams and bounded confidence/salience
  candidates; empty recall returns an empty result, not a fabricated memory.

## Verification

Current release evidence:

```powershell
backend\.venv\Scripts\python.exe scripts\generate-contracts.py --check
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_memory.py::test_hcms_retrieval_cache_is_lru_ttl_bounded_and_tracks_stats backend\tests\test_hcms_memory.py::test_hcms_exports_cursor_rule_loaded_by_project_context_snapshot
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_phase5_integration.py::test_phase5_hcms_default_manager_records_and_prefetches_between_turns
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_memory.py::test_hcms_compiled_memory_content_has_frontmatter_schema_and_self_correction
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_memory.py::test_hcms_rule_based_updater_extracts_updates_and_removals
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_memory.py::test_hcms_relation_classifier_covers_all_documented_relation_types
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_memory.py::test_hcms_counterfactual_projects_downstream_causal_impact backend\tests\test_memory_runtime_tools.py::test_memory_tool_surfaces_hcms_recall_why_history_and_diff
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_memory.py::test_hcms_manager_uses_configured_hybrid_storage_backend backend\tests\test_hcms_memory.py::test_hcms_hybrid_storage_preserves_full_state_for_causal_reasoning
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_config_loader.py::test_hcms_storage_backend_config_is_typed_and_bounded
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_storage.py::test_hcms_core_config_storage_backend_default_aliases_and_rejection
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_storage.py::test_hcms_hybrid_backend_exports_and_restores_namespace_backup
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_storage.py::test_hcms_hybrid_backup_restores_full_state_graph
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_storage.py::test_hcms_hybrid_backend_restore_reports_typed_backup_errors
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_hcms_storage.py backend\tests\test_hcms_memory.py backend\tests\test_hcms_benchmark.py backend\tests\test_memory_runtime_tools.py backend\tests\test_gateway_hcms_memory_surfaces.py backend\tests\test_self_upgrade_health.py backend\tests\test_phase5_integration.py -k "hcms or memory"
backend\.venv\Scripts\python.exe scripts\run-hcms-benchmark-report.py --iterations 120
cd frontend
npm test
npm run typecheck
```

Latest recorded medium-fixture benchmark:
`.omx/goal/reports/hcms-benchmark-20260606T181248Z.json` with dataset size
`53`, query count `10`, Recall@10 `1.0`, warm P95 `9.484ms`, warm uncached P95
`64.304ms`, cold start `272.959ms`, degraded `bm25` stream case passed, and
semantic negative top vector score `0.0`. The report explicitly marks release
gate status as `not verified`; it must not be used as production-scale or final
release proof. Current HCMS package coverage evidence is
`.omx/goal/reports/hcms-memory-coverage-20260606-current.json` with scoped
`anvil.memory` coverage at `90%`.
