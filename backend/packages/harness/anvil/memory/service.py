from __future__ import annotations

from typing import Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from .contracts import MemoryCaptureEnvelope, MemoryInjectionView, MemoryQueue, MemoryState, MemoryStore, MemoryUpdater


class MemoryService:
    def __init__(
        self,
        *,
        store: MemoryStore,
        queue: MemoryQueue,
        updater: MemoryUpdater,
        max_facts: int = 12,
        injection_token_budget: int = 1200,
    ) -> None:
        self.store = store
        self.queue = queue
        self.updater = updater
        self.max_facts = max_facts
        self.injection_token_budget = injection_token_budget

    def build_capture_envelope(
        self,
        *,
        thread_id: str,
        namespace: str,
        messages: Iterable[BaseMessage],
        trace_id: str | None = None,
        blocked: bool = False,
        failed: bool = False,
    ) -> MemoryCaptureEnvelope:
        user_messages: list[str] = []
        final_assistant_messages: list[str] = []
        corrections: list[str] = []
        positive: list[str] = []

        for message in messages:
            if isinstance(message, ToolMessage):
                continue

            content = self._extract_text(message.content)
            if not content:
                continue

            if isinstance(message, HumanMessage):
                user_messages.append(content)
                lowered = content.lower()
                if "actually" in lowered or "that's wrong" in lowered or "you are wrong" in lowered:
                    corrections.append(content)
                if "correct" in lowered or "exactly" in lowered or "good" in lowered:
                    positive.append(content)
            elif isinstance(message, AIMessage):
                if blocked or failed:
                    continue
                if getattr(message, "tool_calls", None):
                    continue
                final_assistant_messages.append(content)

        return MemoryCaptureEnvelope(
            thread_id=thread_id,
            memory_namespace=namespace,
            user_messages=user_messages,
            final_assistant_messages=final_assistant_messages,
            explicit_corrections=corrections,
            positive_reinforcement=positive,
            trace_id=trace_id,
        )

    def enqueue_capture(self, envelope: MemoryCaptureEnvelope) -> None:
        self.queue.enqueue(envelope)

    def has_capture_signal(self, envelope: MemoryCaptureEnvelope) -> bool:
        return bool(
            envelope.user_messages
            or envelope.final_assistant_messages
            or envelope.explicit_corrections
            or envelope.positive_reinforcement
        )

    def process_pending(self, namespace: str | None = None) -> int:
        processed = 0
        while True:
            envelope = self.queue.pop_next(namespace)
            if envelope is None:
                break
            current_state = self.store.load(envelope.memory_namespace)
            next_state = self.updater.update(current_state, envelope)
            self.store.save(envelope.memory_namespace, next_state)
            processed += 1
        return processed

    def prefetch(self, namespace: str) -> MemoryState:
        return self.store.load(namespace)

    def build_injection_view(self, namespace: str) -> MemoryInjectionView:
        state = self.prefetch(namespace)
        ranked = sorted(
            state.facts,
            key=lambda fact: (
                0 if fact.category == "correction" else 1,
                -fact.confidence,
            ),
        )

        facts: list[str] = []
        char_budget = self.injection_token_budget * 4
        used = len(state.summary.summary)
        for fact in ranked[: self.max_facts]:
            line = f"{fact.category}: {fact.content}"
            if used + len(line) > char_budget:
                break
            facts.append(line)
            used += len(line)

        return MemoryInjectionView(
            namespace=namespace,
            summary=state.summary.summary,
            facts=tuple(facts),
        )

    def _extract_text(self, content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(part for part in parts if part)
        return ""
