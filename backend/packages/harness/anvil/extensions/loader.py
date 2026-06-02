from __future__ import annotations

from anvil.config import EffectiveConfig


class ExtensionsLoader:
    def configured_server_ids(self, config: EffectiveConfig) -> tuple[str, ...]:
        return tuple(sorted(config.extensions.mcp_servers))

    def configured_plugin_ids(self, config: EffectiveConfig) -> tuple[str, ...]:
        return tuple(sorted(config.extensions.plugins))
