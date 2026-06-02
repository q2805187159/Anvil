# Anvil Frontend

This frontend is the operator workspace shell for Anvil.

It is intentionally thin:

- typed HTTP wrapper layer
- domain hooks over the wrapper layer
- workspace components that consume hooks
- plain HTTP plus streaming against the gateway

It does not import harness or app runtime internals.

Current surface highlights:

- deep-linkable thread routes at `/threads/<threadId>`
- deep-linkable `Ops Console` via `?ops=1&surface=tools|skills|mcp|plugins&item=<id>&action=<name>&server=<id>`
- durable transcript rendering backed by `GET /threads/{thread_id}/detail`
- structured live-tail streaming for messages, reasoning, tools, and approvals
- global system-event subscription for config/skills/capability refresh signals
- streamed approval controls with editable approval notes plus cancel support
- specialized tool blocks for filesystem and artifact-heavy runs
- automatic locale detection with manual `en-US` / `zh-CN` switching
- Y2K-inspired tri-pane operator workspace with collapsible side rails
- right-drawer ops summary for runtime health, visible capabilities, skill/plugin snapshots, and recent operator actions
- full-width `Ops Console` for `/tools`, `/catalog`, `/skills/manage`, `/mcp/*`, and `/plugins`

## Scripts

- `npm install`
- `npm run dev`
- `npm run test`
- `npm run typecheck`
- `npm run build`
- `npm run start`

## Environment

- `NEXT_PUBLIC_ANVIL_GATEWAY_URL`

If unset, the frontend defaults to `http://127.0.0.1:18000`.

## Quick Start

```bash
npm install
npm run dev
```

For a full stack local run, start the backend gateway first, then start the frontend.
For the Docker-based operator flow, see `docs/guides/local-docker-workspace.md`.

Related docs:

- `docs/index.md`
- `docs/guides/quickstart-and-startup-modes.md`
- `docs/architecture/source-of-truth.md`
