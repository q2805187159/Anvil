from __future__ import annotations

from anvil.config import EffectiveConfig, SandboxMode

from .container_provider import IsolatedSandboxProvider
from .local_provider import HostIsolatedSandboxProvider, LocalSandboxProvider


class ConfigurationError(ValueError):
    pass


class NotImplementedSandboxProvider:
    def __init__(self, provider_mode: str) -> None:
        self.provider_mode = provider_mode

    def acquire(self, *, thread_id: str, path_service):
        raise NotImplementedError(f"{self.provider_mode} sandbox provider is not implemented in Phase 4")

    def get(self, thread_id: str):
        return None

    def release(self, thread_id: str) -> None:
        return None


def create_sandbox_provider(config: EffectiveConfig):
    if config.sandbox_mode is SandboxMode.LOCAL:
        return HostIsolatedSandboxProvider()
    if config.sandbox_mode is SandboxMode.HOST_ISOLATED:
        return HostIsolatedSandboxProvider()
    if config.sandbox_mode is SandboxMode.ISOLATED:
        isolated = config.sandbox.isolated
        return IsolatedSandboxProvider(
            image=isolated.image,
            network_access=isolated.network_access,
            max_execution_time=isolated.max_execution_time,
        )
    if config.sandbox_mode is SandboxMode.EXTERNAL:
        raise ConfigurationError("external sandbox mode is unsupported in this release")
    raise ValueError(f"unsupported sandbox mode: {config.sandbox_mode}")
