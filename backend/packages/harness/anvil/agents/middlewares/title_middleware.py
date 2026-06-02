"""Behavior enhancement layer.

Reads: messages, existing/current title, title config, model routing config
Writes: title
Side effects: optional small-model title generation call when explicitly allowed
Failure behavior: fail-open with deterministic truncation fallback
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState
from anvil.agents.model_factory import create_chat_model
from anvil.config import ModelRouteRequest, RequiredModelCapabilities, resolve_model_route
from anvil.config.service import resolve_internal_task_model_config, resolve_internal_task_model_name
from anvil.runtime.serialization import strip_inline_thinking_tags

THREAD_RAIL_TITLE_MAX_LENGTH = 32
CJK_THREAD_RAIL_TITLE_MAX_LENGTH = 18


def _contains_cjk(text: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in text
    )


def _compact_title_for_thread_rail(title: str, configured_max_length: int) -> str:
    normalized = " ".join(title.strip().split())
    if not normalized:
        return ""
    rail_limit = CJK_THREAD_RAIL_TITLE_MAX_LENGTH if _contains_cjk(normalized) else THREAD_RAIL_TITLE_MAX_LENGTH
    max_length = max(1, min(configured_max_length, rail_limit))
    if len(normalized) <= max_length:
        return normalized
    if max_length <= 3:
        return normalized[:max_length].rstrip()
    return f"{normalized[: max_length - 3].rstrip()}..."


class TitleMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def _normalize_content(self, content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(part for part in (self._normalize_content(item) for item in content) if part)
        if isinstance(content, dict):
            if isinstance(content.get("text"), str):
                return str(content["text"])
            if content.get("content") is not None:
                return self._normalize_content(content["content"])
        return ""

    def _should_generate_title(
        self,
        state: LeadAgentState,
        runtime,
        *,
        require_assistant: bool = True,
        ignore_existing: bool = False,
    ) -> bool:
        config = runtime.context.config_result.effective_config.title
        if not config.enabled:
            return False
        if not ignore_existing and (runtime.context.current_title or runtime.context.existing_thread_title or state.title):
            return False

        messages = state.messages
        if not messages:
            return False

        user_messages = [message for message in messages if getattr(message, "type", None) == "human"]
        assistant_messages = [message for message in messages if getattr(message, "type", None) == "ai"]
        if len(user_messages) != 1:
            return False
        if require_assistant and not assistant_messages:
            return False
        return True

    def _build_title_prompt(self, state: LeadAgentState, runtime) -> tuple[str, str]:
        config = runtime.context.config_result.effective_config.title
        user_message = next((message for message in state.messages if getattr(message, "type", None) == "human"), None)
        assistant_message = next((message for message in state.messages if getattr(message, "type", None) == "ai"), None)

        user_text = self._normalize_content(getattr(user_message, "content", ""))[:500]
        assistant_text = self._normalize_content(getattr(assistant_message, "content", ""))[:500]
        rail_limit = min(config.max_length, THREAD_RAIL_TITLE_MAX_LENGTH)
        cjk_rail_limit = min(config.max_length, CJK_THREAD_RAIL_TITLE_MAX_LENGTH)
        prompt = (
            "Generate a concise conversation title.\n"
            f"- Maximum {config.max_length} characters\n"
            f"- Prefer {rail_limit} English characters or {cjk_rail_limit} CJK characters so it fits the sidebar row\n"
            "- Maximum 8 words\n"
            "- Return title only, no quotes, no punctuation suffix unless necessary\n\n"
            "- Use the same language as the user's input (if user writes in Chinese, respond in Chinese; if Japanese, respond in Japanese, etc.)\n\n"
            f"User message:\n{user_text}\n\n"
            f"Assistant response:\n{assistant_text}"
        )
        return prompt, user_text

    def _fallback_title(self, user_text: str, runtime) -> str:
        config = runtime.context.config_result.effective_config.title
        normalized = " ".join(user_text.strip().split())
        if not normalized:
            return "New Conversation"
        return _compact_title_for_thread_rail(normalized, config.max_length)

    def _parse_title(self, content: object, runtime) -> str:
        config = runtime.context.config_result.effective_config.title
        title = strip_inline_thinking_tags(self._normalize_content(content)).strip().strip('"').strip("'")
        if title.lower().startswith("title:"):
            title = title[6:].strip()
        return _compact_title_for_thread_rail(title, config.max_length)

    def _explicit_title_model_name(self, runtime) -> str | None:
        effective_config = runtime.context.config_result.effective_config
        if effective_config.title.model_name:
            return effective_config.title.model_name
        subsystem_model = effective_config.subsystem_models.get("title")
        if subsystem_model:
            return subsystem_model
        llm_subsystem_model = effective_config.llm.subsystems.get("title")
        if llm_subsystem_model:
            return llm_subsystem_model
        internal_model = resolve_internal_task_model_name(effective_config) if effective_config.llm.internal_task_model else None
        if internal_model and effective_config.llm.internal_task_model:
            return internal_model
        return None

    def _resolve_title_model_name(self, runtime) -> str | None:
        config_result = runtime.context.config_result
        effective_config = config_result.effective_config
        try:
            route = resolve_model_route(
                effective_config,
                ModelRouteRequest(
                    subsystem="title",
                    required_capabilities=RequiredModelCapabilities(tool_calling=False),
                ),
            )
            return route.model_name
        except Exception:
            pass
        if effective_config.title.model_name:
            return effective_config.title.model_name
        if effective_config.default_model:
            return effective_config.default_model
        return None

    def wants_llm_title(self, runtime) -> bool:
        config = runtime.context.config_result.effective_config.title
        if not config.enabled:
            return False
        return config.generation_strategy == "llm" or self._explicit_title_model_name(runtime) is not None

    def _generate_title(self, state: LeadAgentState, runtime, *, allow_llm: bool = True) -> str | None:
        prompt, user_text = self._build_title_prompt(state, runtime)
        config = runtime.context.config_result.effective_config.title
        explicit_model_name = self._explicit_title_model_name(runtime)
        if not allow_llm:
            return self._fallback_title(user_text, runtime)
        if config.generation_strategy != "llm" and not explicit_model_name:
            return self._fallback_title(user_text, runtime)

        model_name = explicit_model_name or self._resolve_title_model_name(runtime)
        if not model_name:
            return self._fallback_title(user_text, runtime)

        try:
            model_config = resolve_internal_task_model_config(runtime.context.config_result.effective_config, model_name)
            if model_config is None:
                model_config = runtime.context.config_result.effective_config.models[model_name]
            model = create_chat_model(model_config, thinking_enabled=False)
            try:
                response = model.invoke(
                    prompt,
                    config={
                        "callbacks": [],
                        "tags": ["anvil_internal_title"],
                        "metadata": {"anvil_internal": True, "anvil_internal_kind": "title"},
                    },
                )
            except TypeError:
                response = model.invoke(prompt)
            title = self._parse_title(getattr(response, "content", ""), runtime)
            if title:
                return title
        except Exception:
            pass
        return self._fallback_title(user_text, runtime)

    def generate_title_for_state(
        self,
        state: LeadAgentState,
        runtime,
        *,
        require_assistant: bool = True,
        allow_llm: bool = True,
        ignore_existing: bool = False,
        update_runtime: bool = True,
    ) -> str | None:
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        if not self._should_generate_title(
            state_obj,
            runtime,
            require_assistant=require_assistant,
            ignore_existing=ignore_existing,
        ):
            return None
        title = self._generate_title(state_obj, runtime, allow_llm=allow_llm)
        if not title:
            return None
        if update_runtime:
            runtime.context.current_title = title
        return title

    def after_model(self, state: LeadAgentState, runtime):
        return self._maybe_generate_title(state, runtime)

    def after_agent(self, state: LeadAgentState, runtime):
        return self._maybe_generate_title(state, runtime)

    def _maybe_generate_title(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        title = self.generate_title_for_state(state_obj, runtime, require_assistant=True, allow_llm=False)
        if not title:
            return None
        return {"title": title}
