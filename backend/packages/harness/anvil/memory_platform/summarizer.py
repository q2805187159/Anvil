from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass

from anvil.agents.model_factory import create_chat_model
from anvil.config import EffectiveConfig, ModelRouteRequest, RequiredModelCapabilities, resolve_model_route
from anvil.config.service import resolve_internal_task_model_config

from .contracts import ArchiveSearchHit, ArchiveTurnRecord


@dataclass(frozen=True)
class FocusedSummaryRequest:
    query: str
    thread_id: str
    turns: tuple[ArchiveTurnRecord, ...]
    hits: tuple[ArchiveSearchHit, ...]


class FocusedSessionSummaryService:
    def __init__(self, *, effective_config: EffectiveConfig) -> None:
        self.effective_config = effective_config

    def summarize(self, request: FocusedSummaryRequest) -> str | None:
        model_name = self._resolve_model_name()
        if not model_name:
            return None
        model_config = resolve_internal_task_model_config(self.effective_config, model_name)
        if model_config is None:
            model_config = self.effective_config.models.get(model_name)
        if model_config is None:
            return None

        session_config = self.effective_config.memory_platform.session_search
        prompt = self._build_prompt(request)[: max(session_config.max_summary_input_chars, 1000)]
        if not prompt.strip():
            return None

        model_config = model_config.model_copy(update={"max_tokens": session_config.max_summary_output_chars})
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="anvil-session-summary")
        future = executor.submit(self._invoke_model, model_config, prompt)
        try:
            content = future.result(timeout=max(float(session_config.summary_timeout_seconds), 1.0))
        except TimeoutError:
            future.cancel()
            return None
        except Exception:
            return None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if not content:
            return None
        return content[: max(session_config.max_summary_output_chars, 256)].strip()

    def _resolve_model_name(self) -> str | None:
        try:
            route = resolve_model_route(
                self.effective_config,
                ModelRouteRequest(
                    subsystem="session_search",
                    required_capabilities=RequiredModelCapabilities(tool_calling=False),
                ),
            )
            return route.model_name
        except Exception:
            pass
        if self.effective_config.memory_platform.session_search.model_name:
            return self.effective_config.memory_platform.session_search.model_name
        return self.effective_config.default_model

    def _build_prompt(self, request: FocusedSummaryRequest) -> str:
        transcript = "\n\n".join(_turn_to_text(turn) for turn in request.turns)
        evidence = "\n".join(f"- {hit.thread_id}/{hit.archive_id}: {hit.excerpt}" for hit in request.hits[:5])
        return (
            "You are reviewing an archived agent conversation for memory recall.\n"
            "Write a focused, factual summary for the current agent. Include only information useful for the search topic.\n"
            "Preserve concrete files, commands, decisions, constraints, outcomes, and unresolved items when present.\n"
            "Use the same language as the archived conversation when obvious. Do not invent details.\n\n"
            f"Search topic: {request.query or 'recent session'}\n"
            f"Thread id: {request.thread_id}\n\n"
            f"Matching evidence:\n{evidence or '(none)'}\n\n"
            f"Transcript excerpt:\n{transcript or '(none)'}\n\n"
            "Focused summary:"
        )

    def _invoke_model(self, model_config, prompt: str) -> str:
        model = create_chat_model(model_config, thinking_enabled=False)
        response = model.invoke(prompt)
        return _extract_text(getattr(response, "content", "")).strip()


def _turn_to_text(turn: ArchiveTurnRecord) -> str:
    return (
        f"[{turn.created_at.isoformat()}]\n"
        f"User: {turn.user_content[:4000]}\n"
        f"Assistant: {turn.assistant_content[:4000]}"
    )


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(str(item["text"]))
        return "\n".join(pieces)
    return str(content)
