# Memory HTTP Integration Notes

HCMS is the active memory engine in this Anvil release. This curated plugin is
documentation-only: it gives operators a place to keep HTTP memory integration
notes without registering or activating an external engine.

External services should integrate through normal tools, MCP servers, or
gateway clients. They must not replace HCMS as the source of memory truth.

Memory administration uses the built-in `hcms` engine through `/memory/engines`
and `/memory/admin/engines`.
