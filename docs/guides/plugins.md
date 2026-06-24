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
- plugin metadata is recorded in Home `plugins.json` and runtime capabilities are reloaded
- Home `plugins.json` is updated and runtime capabilities are reloaded

Registry deletion only removes the registry source from the Home plugin registry. It does not remove already installed plugins.

## Memory Plugins

HCMS is the active memory engine for this release. Plugins may add tools,
prompts, resources, MCP servers, or documentation that integrate with external
memory services, but they do not replace the active HCMS engine or define
memory lifecycle truth.

The curated HTTP memory integration example is a documentation/resource plugin:

```yaml
plugin_id: memory-http-integration-notes
name: Memory HTTP Integration Notes
version: 0.1.0
resources:
  - resource_id: memory-http-integration-notes-readme
    title: HTTP Memory Integration Notes
    path: README.md
```

Memory engine administration is exposed through `/memory/engines` and
`/memory/admin/engines`; plugins do not define memory engine metadata.
