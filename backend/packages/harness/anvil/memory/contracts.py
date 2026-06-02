from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    content: str
    confidence: float
    source_timestamp: datetime = Field(default_factory=utc_now)


class MemorySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    updated_at: datetime = Field(default_factory=utc_now)


class MemoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    summary: MemorySummary = Field(default_factory=MemorySummary)
    facts: list[MemoryFact] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class MemoryCaptureEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    memory_namespace: str
    user_messages: list[str] = Field(default_factory=list)
    final_assistant_messages: list[str] = Field(default_factory=list)
    explicit_corrections: list[str] = Field(default_factory=list)
    positive_reinforcement: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)
    trace_id: str | None = None


class MemoryInjectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    summary: str
    facts: tuple[str, ...] = ()

    def render_fenced(self) -> str:
        lines = [f"namespace={self.namespace}", f"summary={self.summary}"]
        lines.extend(f"- {fact}" for fact in self.facts)
        return "<memory_context>\n" + "\n".join(lines) + "\n</memory_context>"


class MemoryStore(Protocol):
    def load(self, namespace: str) -> MemoryState: ...

    def save(self, namespace: str, memory_state: MemoryState) -> None: ...

    def invalidate(self, namespace: str) -> None: ...

    def list_namespaces(self) -> list[str]: ...


class MemoryQueue(Protocol):
    def enqueue(self, envelope: MemoryCaptureEnvelope) -> None: ...

    def get_pending(self, namespace: str | None = None) -> list[MemoryCaptureEnvelope]: ...

    def pop_next(self, namespace: str | None = None) -> MemoryCaptureEnvelope | None: ...


class MemoryUpdater(Protocol):
    def update(self, current_state: MemoryState, envelope: MemoryCaptureEnvelope) -> MemoryState: ...
