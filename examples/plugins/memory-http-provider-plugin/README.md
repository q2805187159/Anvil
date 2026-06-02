# Memory HTTP Provider Plugin

This example registers a generic HTTP memory provider without adding Python dependencies.

The provider endpoint receives JSON payloads with an `action` field, for example:

```json
{
  "action": "sync_turn",
  "turn": {
    "thread_id": "thread-123",
    "user_content": "...",
    "assistant_content": "...",
    "status": "completed"
  }
}
```

Supported actions include `system_prompt_block`, `prefetch`, `queue_prefetch`, `sync_turn`, `session_end`, `pre_compact`, `delegation`, `explain`, `memory_write`, `test`, and `shutdown`.

Responses should be JSON objects. For recall-style hooks, return `notes`:

```json
{
  "notes": ["Provider note visible in memory recall."]
}
```
