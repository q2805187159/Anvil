# Usage Guide

This guide shows the main ways to operate Anvil after configuration is ready.

## Run Modes

| Mode | Best for | Start command |
| --- | --- | --- |
| Docker full stack | First run, release-like validation, isolated services | `make docker-start` |
| Backend gateway | API development and frontend integration | `make backend` |
| Frontend workbench | Browser-based operator workflow | `make frontend` |
| Shell/TUI | Keyboard-first local operation | `make shell` |
| CLI step | One-shot prompts and automation scripts | `anvil step "..."` |
| Embedded SDK | Python application integration | `EmbeddedClient(...)` |

## Browser Workbench

Start the backend, then the frontend. Open `http://127.0.0.1:13200`.

Common routes:

- `/` opens the operator workspace.
- `/threads/<threadId>` opens a specific thread.
- `?ops=1&surface=tools` opens the Tools surface.
- `?ops=1&surface=skills` opens Skills governance.
- `?ops=1&surface=mcp` opens the MCP console.
- `?ops=1&surface=plugins` opens plugin registry views.

The workbench is a runtime view. It renders backend contracts and does not
invent tool availability, approval semantics, memory state, or path rules.

## CLI One-Shot Runs

After installing the backend package:

```bash
cd backend
python -m pip install -e ".[observability,test,docs]"
```

Run one prompt:

```bash
anvil step "Reply with OK only."
```

Use a persistent thread:

```bash
anvil step --thread demo-thread "Remember this thread name."
anvil step --thread demo-thread "What thread did I ask you to remember?"
```

Stream structured output:

```bash
anvil step --stream "List available runtime tools."
```

## Embedded Python

```python
from app.sdk import EmbeddedClient, EmbeddedRunRequest

with EmbeddedClient() as client:
    thread = client.create_thread()
    result = client.run(
        EmbeddedRunRequest(
            thread_id=thread.thread_id,
            message="Reply with OK only.",
        )
    )
    print(result.status, result.assistant_message)
```

## Memory Workflow

Anvil separates memory into three surfaces:

- session archive: searchable transcript and summary history
- user profile: durable user preferences and corrections
- runtime memory: project, workflow, environment, and workspace facts

Useful commands:

```bash
anvil memory overview
anvil memory stores
anvil memory search "deployment config"
```

The frontend Memory Workspace exposes the same underlying stores and review
queues.

## Skills and Extensions

Public releases do not bundle unreviewed local skill packs in `skills/`.
Install local or shared skills into Anvil Home instead:

```text
~/.anvil/skills
~/.anvil/profiles/<profile>/skills
```

Additional read-only skill roots can be configured with
`skills_config.external_dirs` in `config.yaml`.

MCP servers are configured under `mcp_servers` in `config.yaml`.

## Filesystem Model

Agent-visible paths are normalized into virtual roots:

```text
/mnt/user-data/
|-- uploads/
|-- workspace/
`-- outputs/
```

Use host path bridges only when they are explicitly mounted into the backend
process or Docker container.
