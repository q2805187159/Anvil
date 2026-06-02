# Backend Test Notes

Canonical release verification guidance now lives in:

- `docs/guides/release-verification.md`
- release-facing guides under `docs/guides/`

## Automated Tests

Run the backend test suite from `backend/`:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q
```

Phase 7 also adds:

- embedded SDK parity tests
- shell thin-wrapper tests
- gateway POST streaming coverage for frontend-friendly SSE

Phase 8 adds:

- HTTP vs embedded conformance tests
- stream lifecycle parity tests
- runtime middleware-order verification
- capability visibility and prompt-cache boundary tests
- approval/sandbox control-plane conformance tests
- SDK packaging smoke coverage

## Manual Release Smoke

Use the packaged smoke entrypoint or module:

- `anvil-smoke local`
- `python -m app.smoke local`
- `anvil-smoke provider --config ./config.yaml --model <model-key> --message "Reply with OK only."`
- `python -m app.smoke provider --config ./config.yaml --model <model-key> --message "Reply with OK only."`

Supported providers:

- OpenAI-compatible
- MiniMax Anthropic-compatible
- optional local vLLM

Required environment variables depend on your config path:

If you use `config.yaml` copied from `config.example.yaml`:

### `config.yaml` primary path

- OpenAI-compatible entry: `OPENAI_API_KEY`
- MiniMax entry: `MINIMAX_API_KEY`
- local vLLM entry: `VLLM_API_KEY`

If you intentionally use the legacy env-bootstrap fallback with no `config.yaml`, use:

### legacy fallback

#### OpenAI-compatible

- `ANVIL_OPENAI_COMPAT_BASE_URL`
- `ANVIL_OPENAI_COMPAT_API_KEY`
- `ANVIL_OPENAI_COMPAT_MODEL`
- `ANVIL_OPENAI_REASONING_EFFORT`

#### MiniMax

- `ANVIL_MINIMAX_BASE_URL`
- `ANVIL_MINIMAX_API_KEY`
- `ANVIL_MINIMAX_MODEL`

#### vLLM

- `ANVIL_VLLM_BASE_URL`
- `ANVIL_VLLM_API_KEY`
- `ANVIL_VLLM_MODEL`

Example:

```powershell
python -m app.smoke local
python -m app.doctor --config ./config.yaml
python -m app.smoke provider --config ./config.yaml --model <model-key> --message "Reply with OK only."
```

`<model-key>` is the configured model key reported by doctor under `available_models`.

## Frontend Verification

Run the frontend checks from `frontend/`:

```powershell
npm test
npm run typecheck
npm run build
```
