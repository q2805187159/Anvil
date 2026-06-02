from __future__ import annotations

from typing import Any

from .contracts import ArchiveSearchResult, ArchiveTurnRecord, CuratedEntry, MemoryProviderManifest, MemoryProviderTestResult, MemoryWriteEvent, RecallEvidence
from .providers import ProviderRegistry


class ProviderRuntime:
    def __init__(self, *, registry: ProviderRegistry) -> None:
        self.registry = registry

    @property
    def active_provider_id(self) -> str | None:
        return self.registry.active_provider_id

    def list_providers(self) -> tuple[MemoryProviderManifest, ...]:
        return self.registry.list_providers()

    def activate(self, provider_id: str) -> MemoryProviderManifest:
        return self.registry.activate(provider_id)

    def system_prompt_block(self) -> str:
        blocks: list[str] = []
        active = self.registry.active_provider()
        if active is not None:
            try:
                block = active.system_prompt_block()
                if block:
                    blocks.append(block)
            except Exception:
                pass
        for provider in self.registry.providers_for_role("system_prompt"):
            if active is not None and provider.manifest().provider_id == active.manifest().provider_id:
                continue
            try:
                block = provider.system_prompt_block()
                if block:
                    blocks.append(block)
            except Exception:
                continue
        return "\n".join(blocks)

    def queue_prefetch(self, *, query: str, thread_id: str) -> None:
        provider = self.registry.active_provider()
        if provider is None:
            return
        try:
            provider.queue_prefetch(query, thread_id=thread_id)
        except Exception:
            return

    def prefetch(
        self,
        *,
        query: str,
        thread_id: str,
        archive: ArchiveSearchResult,
        curated_matches: tuple[CuratedEntry, ...],
    ) -> tuple[str, ...]:
        provider = self.registry.active_provider()
        if provider is None:
            return ()
        try:
            return provider.prefetch(query, thread_id=thread_id, archive=archive, curated_matches=curated_matches)
        except Exception:
            return ()

    def sync_turn(self, record: ArchiveTurnRecord) -> None:
        for provider in self.registry.providers_for_role("sync"):
            try:
                provider.sync_turn(record)
            except Exception:
                continue

    def on_session_end(self, *, thread_id: str, messages: list[dict[str, Any]], reason: str, allow_network: bool) -> tuple[str, ...]:
        notes: list[str] = []
        for provider in self.registry.providers_for_role("session_end"):
            try:
                notes.extend(provider.on_session_end(thread_id=thread_id, messages=messages, reason=reason, allow_network=allow_network))
            except Exception:
                continue
        return tuple(note for note in notes if note)

    def on_pre_compact(self, messages: list[dict[str, Any]]) -> tuple[str, ...]:
        notes: list[str] = []
        for provider in self.registry.providers_for_role("pre_compact"):
            try:
                note = provider.on_pre_compact(messages)
                if note:
                    notes.append(note)
            except Exception:
                continue
        return tuple(notes)

    def on_delegation(self, *, parent_thread_id: str, task: dict[str, Any], result: dict[str, Any], status: str) -> tuple[str, ...]:
        notes: list[str] = []
        for provider in self.registry.providers_for_role("delegation"):
            try:
                notes.extend(provider.on_delegation(parent_thread_id=parent_thread_id, task=task, result=result, status=status))
            except Exception:
                continue
        return tuple(note for note in notes if note)

    def test_provider(self, provider_id: str) -> MemoryProviderTestResult:
        for provider in self.registry.providers():
            manifest = provider.manifest()
            if manifest.provider_id == provider_id:
                try:
                    return provider.test()
                except Exception as exc:
                    return MemoryProviderTestResult(
                        provider_id=provider_id,
                        ok=False,
                        health="failed",
                        diagnostics=(str(exc),),
                    )
        raise KeyError(provider_id)

    def index_write(self, *, entry: CuratedEntry | None = None, record: ArchiveTurnRecord | None = None) -> tuple[str, ...]:
        notes: list[str] = []
        for provider in self.registry.providers_for_role("index"):
            try:
                notes.extend(provider.index_write(entry=entry, record=record))
            except Exception:
                continue
        return tuple(note for note in notes if note)

    def explain(self, *, query: str, evidence: tuple[RecallEvidence, ...]) -> tuple[str, ...]:
        notes: list[str] = []
        for provider in self.registry.providers_for_role("explain"):
            try:
                notes.extend(provider.explain(query=query, evidence=evidence))
            except Exception:
                continue
        return tuple(note for note in notes if note)

    def on_memory_write(self, event: MemoryWriteEvent) -> None:
        for provider in self.registry.providers_for_role("memory_write"):
            try:
                provider.on_memory_write(event)
            except Exception:
                continue

    def shutdown(self, *, allow_network: bool = False) -> None:
        for provider in self.registry.providers():
            try:
                provider.shutdown(allow_network=allow_network)
            except Exception:
                continue
