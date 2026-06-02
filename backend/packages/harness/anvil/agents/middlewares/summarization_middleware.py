"""Behavior enhancement layer.

Reads: messages, summary, config_result.effective_config.summarization
Writes: summary, summarization_triggered
Side effects: optional lightweight model call for summary generation
Failure behavior: fail-open with deterministic fallback summary
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState
from anvil.agents.model_factory import create_chat_model
from anvil.config.service import resolve_internal_task_model_config
from anvil.config.model_routing import ModelRouteRequest, RequiredModelCapabilities, resolve_model_route
from anvil.runtime.context_envelope import ContextAssembler
from anvil.runtime.token_budget import TokenBudgetService


COMPACTION_LEVEL_LABELS = {
    0: "none",
    1: "summary",
    2: "recursive_summary",
    3: "emergency",
}

_MESSAGE_CONTENT_MAX_CHARS = 6_000
_MESSAGE_CONTENT_HEAD_CHARS = 4_000
_MESSAGE_CONTENT_TAIL_CHARS = 1_500
_TOOL_ARGS_MAX_CHARS = 1_500
_TOOL_RESULT_PRUNE_THRESHOLD_CHARS = 1_200
_TOOL_RESULT_PRUNED_CHARS = 600


class SummarizationMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def before_model(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        config = runtime.context.config_result.effective_config.summarization
        if not config.enabled:
            runtime.context.summary_context = state_obj.summary
            return None

        token_estimate = self._estimate_tokens(state_obj.messages)
        token_threshold = self._effective_token_threshold(runtime)
        current_summary = state_obj.summary or runtime.context.summary_context
        if current_summary:
            runtime.context.summary_context = current_summary
            if getattr(runtime.context, "compaction_level", 0) <= 0:
                self._record_compaction_metadata(
                    runtime,
                    level=1,
                    reason=getattr(runtime.context, "compaction_reason", None),
                    input_tokens=getattr(runtime.context, "compaction_input_tokens", None),
                    summary_tokens=self._estimate_summary_tokens(current_summary),
                    keep_recent_turns=config.keep_recent_turns,
                )

        force_emergency = bool(runtime.context.emergency_summarize_triggered)
        if not force_emergency and token_estimate < token_threshold:
            return None
        compaction_reason = (
            runtime.context.emergency_summarize_reason
            if force_emergency
            else "token_threshold_exceeded"
        )
        compaction_level = self._compaction_level(
            existing_summary=current_summary,
            force_emergency=force_emergency,
        )

        generated = self._generate_summary(
            runtime=runtime,
            messages=state_obj.messages,
            keep_recent_turns=config.keep_recent_turns,
            existing_summary=current_summary,
            compaction_level=compaction_level,
        )
        summary_tokens = self._estimate_summary_tokens(generated)
        runtime.context.summary_context = generated
        runtime.context.summarization_triggered = True
        self._record_compaction_metadata(
            runtime,
            level=compaction_level,
            reason=compaction_reason,
            input_tokens=token_estimate,
            summary_tokens=summary_tokens,
            keep_recent_turns=config.keep_recent_turns,
        )
        runtime.context.emergency_summarize_triggered = False
        runtime.context.emergency_summarize_reason = None
        return {
            "summary": generated,
            "summarization_triggered": True,
            "compaction_diagnostics": dict(runtime.context.compaction_diagnostics),
            "compaction_level": compaction_level,
            "compaction_level_label": COMPACTION_LEVEL_LABELS[compaction_level],
            "compaction_reason": compaction_reason,
            "compaction_input_tokens": token_estimate,
            "compaction_summary_tokens": summary_tokens,
            "compaction_keep_recent_turns": config.keep_recent_turns,
        }

    def wrap_model_call(self, request, handler):
        if request.runtime.context.emergency_summarize_triggered:
            messages = list(request.messages)
            input_tokens = self._estimate_tokens(messages)
            compaction_level = self._compaction_level(
                existing_summary=request.runtime.context.summary_context,
                force_emergency=True,
            )
            compaction_reason = request.runtime.context.emergency_summarize_reason or "provider_context_overload"
            request.runtime.context.summary_context = self._generate_summary(
                runtime=request.runtime,
                messages=messages,
                keep_recent_turns=request.runtime.context.config_result.effective_config.summarization.keep_recent_turns,
                existing_summary=request.runtime.context.summary_context,
                compaction_level=compaction_level,
            )
            summary_tokens = self._estimate_summary_tokens(request.runtime.context.summary_context)
            request.runtime.context.summarization_triggered = True
            self._record_compaction_metadata(
                request.runtime,
                level=compaction_level,
                reason=compaction_reason,
                input_tokens=input_tokens,
                summary_tokens=summary_tokens,
                keep_recent_turns=request.runtime.context.config_result.effective_config.summarization.keep_recent_turns,
            )
            request.runtime.context.emergency_summarize_triggered = False
            request.runtime.context.emergency_summarize_reason = None

        summary_context = request.runtime.context.summary_context
        if not summary_context:
            return handler(request)

        config = request.runtime.context.config_result.effective_config.summarization
        assembler = ContextAssembler(
            path_service=request.runtime.context.path_service,
            thread_id=request.runtime.context.thread_id,
        )
        compacted_messages, system_message = assembler.compacted_summary_model_call(
            messages=list(request.messages),
            system_prompt=request.system_prompt,
            summary_context=summary_context,
            keep_recent_turns=config.keep_recent_turns,
        )
        return handler(
            request.override(
                messages=compacted_messages,
                system_message=system_message,
            )
        )

    def _estimate_tokens(self, messages: list[BaseMessage]) -> int:
        return TokenBudgetService().count_messages(messages)

    def _effective_token_threshold(self, runtime) -> int:
        config = runtime.context.config_result.effective_config.summarization
        active_model_name = getattr(runtime.context, "active_model_name", None)
        model_config = (
            runtime.context.config_result.effective_config.models.get(active_model_name)
            if active_model_name
            else None
        )
        if model_config is None:
            return config.token_threshold
        return model_config.effective_auto_compact_threshold_tokens() or config.token_threshold

    def _generate_summary(
        self,
        *,
        runtime,
        messages: list[BaseMessage],
        keep_recent_turns: int,
        existing_summary: str | None,
        compaction_level: int,
    ) -> str:
        preserved = self._preserved_messages(messages, keep_recent_turns=keep_recent_turns)
        memory_manager = runtime.context.memory_manager
        if memory_manager is not None:
            try:
                memory_manager.flush_memory(
                    thread_id=runtime.context.thread_id,
                    messages=[
                        {"content": getattr(message, "content", "") if isinstance(getattr(message, "content", ""), str) else str(getattr(message, "content", ""))}
                        for message in preserved
                    ],
                )
            except Exception:
                pass
        transcript, diagnostics = self._serialize_for_summary(preserved, keep_recent_turns=keep_recent_turns)
        diagnostics.update(
            {
                "compaction_level": compaction_level,
                "compaction_level_label": COMPACTION_LEVEL_LABELS.get(compaction_level, "summary"),
                "has_existing_summary": bool(existing_summary),
            }
        )
        self._record_compaction_diagnostics(runtime, diagnostics)
        if not transcript.strip():
            self._record_compaction_diagnostics(runtime, {**diagnostics, "summary_source": "empty_fallback"})
            return existing_summary or "No prior conversation summary is available."

        model_name = self._resolve_summary_model_name(runtime)
        if model_name:
            try:
                effective_config = runtime.context.config_result.effective_config
                model_config = resolve_internal_task_model_config(effective_config, model_name)
                if model_config is None:
                    model_config = effective_config.models[model_name]
                model = create_chat_model(model_config)
                compression_profile = self._compression_profile(compaction_level)
                prompt = (
                    "Summarize the prior conversation for continuation. "
                    "Return a structured markdown summary with these headings when the information exists: "
                    "Goal, Progress, Decisions, Files, Tool Evidence, Open Questions, Next Steps. "
                    "Preserve concrete file paths, user constraints, completed work, failed attempts, tool outputs, and follow-up actions. "
                    f"Compression level: {compression_profile['label']}. "
                    f"Target length: {compression_profile['target']}.\n\n"
                    f"Existing summary:\n{existing_summary or '(none)'}\n\n"
                    f"Conversation to archive:\n{transcript}"
                )
                response = model.invoke(prompt)
                content = getattr(response, "content", "")
                if isinstance(content, str) and content.strip():
                    self._record_compaction_diagnostics(
                        runtime,
                        {
                            **diagnostics,
                            "summary_source": "model",
                            "summary_model": model_name,
                            "summary_prompt_tokens": TokenBudgetService().count_text(prompt),
                        },
                    )
                    return content.strip()
            except Exception:
                self._record_compaction_diagnostics(
                    runtime,
                    {
                        **diagnostics,
                        "summary_source": "fallback",
                        "summary_model": model_name,
                        "summary_error_type": "model_invoke_failed",
                    },
                )
        self._record_compaction_diagnostics(
            runtime,
            {
                **diagnostics,
                "summary_source": "fallback",
                "summary_model": model_name,
            },
        )
        return self._fallback_summary(transcript, existing_summary=existing_summary, compaction_level=compaction_level)

    def _resolve_summary_model_name(self, runtime) -> str | None:
        effective_config = runtime.context.config_result.effective_config
        config = effective_config.summarization
        if config.model_name:
            return config.model_name
        try:
            route = resolve_model_route(
                effective_config,
                ModelRouteRequest(
                    subsystem="summarization",
                    required_capabilities=RequiredModelCapabilities(tool_calling=False),
                ),
            )
            return route.model_name
        except Exception:
            return effective_config.default_model

    def _fallback_summary(self, transcript: str, *, existing_summary: str | None, compaction_level: int) -> str:
        lines = [line.strip() for line in transcript.splitlines() if line.strip()]
        profile = self._compression_profile(compaction_level)
        recent = lines[-profile["fallback_lines"] :]
        chunks = []
        if existing_summary:
            chunks.append("## Existing Summary")
            chunks.append(existing_summary.strip())
        if recent:
            chunks.append("## Recent Archived Context")
            chunks.extend(f"- {line[: profile['line_chars']]}" for line in recent)
        return "\n".join(chunks)[: profile["max_chars"]]

    def _preserved_messages(self, messages: list[BaseMessage], *, keep_recent_turns: int) -> list[BaseMessage]:
        if len(messages) > keep_recent_turns:
            return messages[:-keep_recent_turns]
        return messages[:-1]

    def _serialize_for_summary(
        self,
        messages: list[BaseMessage],
        *,
        keep_recent_turns: int,
    ) -> tuple[str, dict[str, Any]]:
        parts: list[str] = []
        diagnostics: dict[str, Any] = {
            "archived_message_count": len(messages),
            "keep_recent_turns": keep_recent_turns,
            "tool_call_count": 0,
            "tool_result_count": 0,
            "image_block_count": 0,
            "truncated_message_count": 0,
            "pruned_tool_result_count": 0,
            "serialized_chars": 0,
            "serialized_tokens": 0,
        }
        for index, message in enumerate(messages, start=1):
            rendered, metadata = self._serialize_message_for_summary(index, message)
            if not rendered:
                continue
            parts.append(rendered)
            for key in ("tool_call_count", "tool_result_count", "image_block_count", "truncated_message_count", "pruned_tool_result_count"):
                diagnostics[key] += int(metadata.get(key, 0) or 0)
        transcript = "\n\n".join(parts)
        diagnostics["serialized_chars"] = len(transcript)
        diagnostics["serialized_tokens"] = TokenBudgetService().count_text(transcript) if transcript else 0
        return transcript, diagnostics

    def _serialize_message_for_summary(self, index: int, message: BaseMessage) -> tuple[str, dict[str, int]]:
        role = str(getattr(message, "type", "message") or "message")
        metadata = {
            "tool_call_count": 0,
            "tool_result_count": 1 if role == "tool" else 0,
            "image_block_count": 0,
            "truncated_message_count": 0,
            "pruned_tool_result_count": 0,
        }
        content, content_metadata = self._render_message_content(getattr(message, "content", ""))
        metadata["image_block_count"] += content_metadata["image_block_count"]
        metadata["truncated_message_count"] += content_metadata["truncated_message_count"]
        if role == "tool" and len(content) > _TOOL_RESULT_PRUNE_THRESHOLD_CHARS:
            content, pruned = self._head_tail_truncate(
                content,
                max_chars=_TOOL_RESULT_PRUNED_CHARS,
                head_chars=360,
                tail_chars=180,
            )
            if pruned:
                metadata["pruned_tool_result_count"] += 1
                metadata["truncated_message_count"] += 1
        lines = [f"[{index}] {role}"]
        tool_name = getattr(message, "name", None)
        if tool_name:
            lines.append(f"tool_name: {tool_name}")
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            lines.append(f"tool_call_id: {tool_call_id}")
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        invalid_tool_calls = list(getattr(message, "invalid_tool_calls", None) or [])
        if tool_calls:
            metadata["tool_call_count"] += len(tool_calls)
            lines.append("tool_calls:")
            for call in tool_calls:
                lines.append(f"- {self._render_tool_call(call)}")
        if invalid_tool_calls:
            metadata["tool_call_count"] += len(invalid_tool_calls)
            lines.append("invalid_tool_calls:")
            for call in invalid_tool_calls:
                lines.append(f"- {self._render_tool_call(call)}")
        if content:
            lines.append("content:")
            lines.append(content)
        return "\n".join(lines), metadata

    def _render_message_content(self, content: object) -> tuple[str, dict[str, int]]:
        metadata = {"image_block_count": 0, "truncated_message_count": 0}
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    block_type = str(block.get("type") or "")
                    if block_type in {"image_url", "input_image", "image"}:
                        metadata["image_block_count"] += 1
                        image_url = block.get("image_url")
                        if isinstance(image_url, dict):
                            image_url = image_url.get("url")
                        parts.append(f"[image block: {self._image_source_label(image_url)}]")
                        continue
                    if "text" in block:
                        parts.append(str(block.get("text") or ""))
                        continue
                parts.append(str(block))
            text = "\n".join(part for part in parts if part.strip())
        else:
            text = str(content or "")
        rendered, truncated = self._head_tail_truncate(
            text,
            max_chars=_MESSAGE_CONTENT_MAX_CHARS,
            head_chars=_MESSAGE_CONTENT_HEAD_CHARS,
            tail_chars=_MESSAGE_CONTENT_TAIL_CHARS,
        )
        if truncated:
            metadata["truncated_message_count"] += 1
        return rendered.strip(), metadata

    def _render_tool_call(self, call: object) -> str:
        if isinstance(call, dict):
            name = call.get("name") or call.get("function", {}).get("name")
            args = call.get("args")
            if args is None and isinstance(call.get("function"), dict):
                args = call["function"].get("arguments")
            call_id = call.get("id") or call.get("tool_call_id")
            args_text = self._bounded_json(args)
            label = str(name or "tool")
            if call_id:
                label = f"{label}#{call_id}"
            return f"{label} args={args_text}"
        return self._bounded_json(call)

    def _bounded_json(self, value: object) -> str:
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            rendered = str(value)
        bounded, _ = self._head_tail_truncate(
            rendered,
            max_chars=_TOOL_ARGS_MAX_CHARS,
            head_chars=1_200,
            tail_chars=200,
        )
        return bounded

    def _head_tail_truncate(
        self,
        value: str,
        *,
        max_chars: int,
        head_chars: int,
        tail_chars: int,
    ) -> tuple[str, bool]:
        text = str(value or "")
        if len(text) <= max_chars:
            return text, False
        head = text[:head_chars].rstrip()
        tail = text[-tail_chars:].lstrip() if tail_chars > 0 else ""
        omitted = max(len(text) - len(head) - len(tail), 0)
        if tail:
            return f"{head}\n...[truncated {omitted} chars]...\n{tail}", True
        return f"{head}\n...[truncated {omitted} chars]...", True

    def _image_source_label(self, source: object) -> str:
        text = str(source or "").strip()
        if not text:
            return "attached image"
        if text.startswith("data:"):
            media_type = text.split(";", 1)[0].removeprefix("data:")
            return f"{media_type or 'inline image'} data omitted"
        return text[:200]

    def _compaction_level(self, *, existing_summary: str | None, force_emergency: bool) -> int:
        if force_emergency:
            return 3
        if existing_summary:
            return 2
        return 1

    def _compression_profile(self, compaction_level: int) -> dict[str, int | str]:
        if compaction_level >= 3:
            return {
                "label": "emergency",
                "target": "120-220 tokens",
                "fallback_lines": 4,
                "line_chars": 160,
                "max_chars": 900,
            }
        if compaction_level == 2:
            return {
                "label": "recursive_summary",
                "target": "250-450 tokens",
                "fallback_lines": 6,
                "line_chars": 200,
                "max_chars": 1600,
            }
        return {
            "label": "summary",
            "target": "500-900 tokens",
            "fallback_lines": 8,
            "line_chars": 220,
            "max_chars": 2400,
        }

    def _estimate_summary_tokens(self, summary: str | None) -> int | None:
        if not summary:
            return None
        return TokenBudgetService().count_text(summary)

    def _record_compaction_metadata(
        self,
        runtime,
        *,
        level: int,
        reason: str | None,
        input_tokens: int | None,
        summary_tokens: int | None,
        keep_recent_turns: int,
    ) -> None:
        runtime.context.compaction_level = level
        runtime.context.compaction_level_label = COMPACTION_LEVEL_LABELS.get(level, "summary")
        runtime.context.compaction_reason = reason
        runtime.context.compaction_input_tokens = input_tokens
        runtime.context.compaction_summary_tokens = summary_tokens
        runtime.context.compaction_keep_recent_turns = keep_recent_turns
        diagnostics = dict(getattr(runtime.context, "compaction_diagnostics", {}) or {})
        diagnostics.update(
            {
                "compaction_level": level,
                "compaction_level_label": COMPACTION_LEVEL_LABELS.get(level, "summary"),
                "compaction_reason": reason,
                "compaction_input_tokens": input_tokens,
                "compaction_summary_tokens": summary_tokens,
                "keep_recent_turns": keep_recent_turns,
            }
        )
        runtime.context.compaction_diagnostics = diagnostics

    def _record_compaction_diagnostics(self, runtime, diagnostics: dict[str, Any]) -> None:
        runtime.context.compaction_diagnostics = {
            **dict(getattr(runtime.context, "compaction_diagnostics", {}) or {}),
            **{key: value for key, value in diagnostics.items() if value is not None},
        }

    def _message_line(self, message: BaseMessage) -> str:
        role = getattr(message, "type", "message")
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = " ".join(str(item) for item in content)
        text = str(content).strip()
        if not text:
            return ""
        return f"{role}: {text[:800]}"
