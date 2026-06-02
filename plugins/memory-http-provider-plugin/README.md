# Memory HTTP Provider Plugin

Registers a generic HTTP memory provider without adding Python dependencies.

The provider endpoint receives JSON lifecycle payloads such as `prefetch`, `sync_turn`, `session_end`, `pre_compact`, `delegation`, `explain`, `memory_write`, `test`, and `shutdown`.

Recall-style responses may return `notes`, which Anvil can surface through the memory recall diagnostics.
