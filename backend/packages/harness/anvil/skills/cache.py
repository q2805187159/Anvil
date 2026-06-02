from __future__ import annotations

from .contracts import SkillDiscoveryResult


class SkillsCache:
    def __init__(self) -> None:
        self._entries: dict[str, SkillDiscoveryResult] = {}

    def get(self, fingerprint: str) -> SkillDiscoveryResult | None:
        entry = self._entries.get(fingerprint)
        return entry.model_copy(deep=True) if entry is not None else None

    def get_shared(self, fingerprint: str) -> SkillDiscoveryResult | None:
        return self._entries.get(fingerprint)

    def put(self, fingerprint: str, result: SkillDiscoveryResult) -> None:
        self._entries[fingerprint] = result.model_copy(deep=True)

    def invalidate(self, fingerprint: str | None = None) -> None:
        if fingerprint is None:
            self._entries.clear()
        else:
            self._entries.pop(fingerprint, None)
