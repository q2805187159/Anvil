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
- `?ops=1&surface=basics` opens Basic Configuration for required Git setup.
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

Anvil uses HCMS as the memory control plane:

- observation capture and adaptive debounce queue for new evidence
- workspace and user layers for structured Memory records
- hybrid recall across BM25, vector, graph, and temporal-causal streams
- version history, confidence, evidence, and causal explanation surfaces
- required Git token configuration for HCMS Git-like version metadata

Useful commands:

```bash
anvil memory overview
anvil memory stores
anvil memory search "deployment config"
```

The frontend Memory Workspace exposes the same underlying HCMS stores,
quality signals, lifecycle controls, version history, evidence, and causal
surfaces.

The HCMS Console includes a Memory Atlas for operator inspection. It clusters
visible memories by category, renders a relation-weighted graph, exposes
category and lifecycle distribution filters, and adds an evidence spectrum plus
an entity lens for cross-memory browsing. The selected memory side panel keeps
version history, diff, evidence, confidence, salience, and relation
neighborhood visible without leaving the Atlas. The frontend only visualizes
gateway data; HCMS ranking, lifecycle policy, and relation truth stay in the
harness and gateway contracts.

For gateway route shapes and runtime tool surfaces, see
[HCMS Memory API](./hcms-memory-api.md).

## Skills and Extensions

The repository bundles starter skills in the root `skills/` directory. Install
user-local or shared team skills into Anvil Home instead:

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
