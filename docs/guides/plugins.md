# Plugin Registry and Installation

Anvil does not pretend to own a central public plugin marketplace. The Ops Console renders a configurable registry layer that can read local catalogs, local plugin folders, and remote JSON registry files. The built-in source is a small curated catalog: list views read summary metadata, while install/detail flows resolve the plugin manifest on demand.

## Registry Sources

The backend merges these sources:

- `.anvil/plugin-registries.json`: user-managed registry list created from Ops Console.
- `.anvil/plugin-catalog.json`: workspace-local catalog when present.
- `plugin-catalog.json`: project-local catalog when present.
- `plugins/catalog.json`: read-only Anvil curated catalog when present.
- optional local desktop plugin caches when present
- installed plugins from `.anvil/plugins.json`.

The local cache sources are read-only discovery inputs. They may be absent in Docker or CI, and absence is not an error. `examples/plugins/` remains test documentation material only and is not registered as a default source.

## Adding a Source

Open `Ops Console -> Plugins -> Sources -> Add source`.

Supported source values:

- Local catalog JSON path: `C:\path\plugin-catalog.json` or `/opt/plugins/catalog.json`
- Local plugin directory: `C:\path\plugins` or `/opt/plugins`
- Remote registry JSON URL: `https://example.com/anvil-plugin-catalog.json`

Local directories can either contain `catalog.json` / `plugin-catalog.json`, or plugin subdirectories with `plugin.yaml`, `plugin.yml`, `plugin.json`, `anvil.plugin.json`, or supported legacy manifest paths. Compatibility manifest names are supported so users can migrate existing local plugin packs without rewriting them first.

List views only require summary metadata. Install and detail flows resolve the concrete manifest on demand, so local cache directories can stay lightweight until the user asks for a plugin detail or install action.

## Catalog Format

```json
{
  "plugins": [
    {
      "plugin_id": "example-plugin",
      "name": "Example Plugin",
      "description": "Short plugin summary.",
      "source": "https://github.com/example/example-plugin.git",
      "version": "0.1.0",
      "author": "Example Team",
      "tags": ["example", "tools"],
      "trust_level": "third-party",
      "permissions": ["network access when the plugin tool is used"]
    }
  ]
}
```

Relative `source` values are resolved relative to the catalog file. Remote catalogs resolve relative plugin sources with normal URL joining.

## Installation Behavior

The `Install` button calls the same guarded backend installer as advanced manual install:

- local directories are copied into the active Anvil Home `plugins/{plugin_id}`
- zip or `.skill` archives are extracted into the active Anvil Home `plugins/{plugin_id}`
- Git URLs and `owner/repo` shorthand are cloned with depth 1
- bundled `.mcp.json` / `mcp.json` entries are merged into Home `config.yaml` under `mcp_servers`
- `memory_providers` entries are merged into Home `plugins.json` and appear in Ops Console / Memory Workspace
- Home `plugins.json` is updated and runtime capabilities are reloaded

Registry deletion only removes the registry source from the Home plugin registry. It does not remove already installed plugins.

## Memory Provider Plugins

Plugins can register memory providers without adding Python dependencies. A provider is declarative and handled by the Anvil memory platform:

```yaml
plugin_id: memory-http-provider-plugin
name: Memory HTTP Provider
version: 0.1.0
memory_providers:
  - provider_id: http_memory
    display_name: HTTP Memory Provider
    kind: http
    enabled: true
    roles:
      - prefetch
      - sync_turn
      - session_end
      - pre_compact
      - delegation
      - shutdown
    settings:
      endpoint: http://127.0.0.1:8787/memory
      timeout_seconds: 2
```

Supported provider kinds:

- `local_curated`: built-in Anvil curated/archive provider, always available.
- `http`: JSON lifecycle provider using stdlib HTTP.

HTTP providers receive JSON payloads for supported lifecycle actions. Failures, timeouts, and invalid JSON are recorded as diagnostics and fail open; they do not block local memory writes or the active agent turn. External active recall providers are exclusive, but passive providers can still receive declared lifecycle hooks.

The curated catalog includes `Memory HTTP Provider` in `plugins/memory-http-provider-plugin` so the Ops Console plugin market starts with a valid provider package instead of test/demo content.
