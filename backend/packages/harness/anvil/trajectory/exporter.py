from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
import re
from typing import Any

from anvil.agents import ThreadLifecycleStatus, ThreadState
from anvil.memory.scrubber import MemorySecretScrubber

from .contracts import (
    ToolUsageStats,
    TrajectoryBatchExportResult,
    TrajectoryCompressionConfig,
    TrajectoryExportEntry,
    TrajectoryExportFormat,
    TrajectoryExportOptions,
    TrajectoryQualityIssue,
    TrajectoryQualityReport,
    TrajectoryStats,
    TrajectoryTurn,
    utc_now,
)
from .tool_calls import ToolCallParser


class ThreadTrajectoryExporter:
    """Convert durable thread state into scrubbed SFT/RL-ready trajectory payloads."""

    def __init__(self, *, scrubber: MemorySecretScrubber | None = None) -> None:
        self.scrubber = scrubber or MemorySecretScrubber()
        self.tool_call_parser = ToolCallParser(scrubber=self.scrubber)

    def export_thread(
        self,
        state: ThreadState,
        *,
        options: TrajectoryExportOptions | None = None,
    ) -> TrajectoryExportEntry:
        options = options or TrajectoryExportOptions()
        raw_turns = [
            turn
            for index, message in enumerate(state.conversation.messages)
            for turn in self._turns_from_message(message, index=index, options=options)
        ]
        compressed_turns, omitted_turn_count = self._compress_turns(raw_turns, options.compression)
        stats = self._build_stats(
            state,
            raw_turns=raw_turns,
            exported_turns=compressed_turns,
            omitted_turn_count=omitted_turn_count,
            options=options,
        )
        quality = self._build_quality_report(
            compressed_turns,
            stats=stats,
            raw_turn_count=len(raw_turns),
            omitted_turn_count=omitted_turn_count,
            options=options,
        )
        metadata = self._build_metadata(state, options=options) if options.include_metadata else {}
        completed = state.lifecycle.status in {
            ThreadLifecycleStatus.COMPLETED,
            ThreadLifecycleStatus.READY,
            ThreadLifecycleStatus.ARCHIVED,
        }
        return TrajectoryExportEntry(
            id=self._entry_id(state),
            thread_id=state.identity.thread_id,
            run_id=state.identity.run_id,
            timestamp=state.lifecycle.updated_at or utc_now(),
            model=state.execution.active_model or state.execution.selected_model,
            completed=completed,
            conversations=compressed_turns,
            stats=stats,
            quality=quality,
            metadata=metadata,
        )

    def export_threads(
        self,
        states: Iterable[ThreadState],
        *,
        path: str | Path | None = None,
        options: TrajectoryExportOptions | None = None,
    ) -> TrajectoryBatchExportResult:
        options = options or TrajectoryExportOptions()
        entries: list[TrajectoryExportEntry] = []
        diagnostics: list[str] = []
        skipped_count = 0
        for state in states:
            try:
                entry = self.export_thread(state, options=options)
            except Exception as exc:  # pragma: no cover - defensive batch isolation
                skipped_count += 1
                diagnostics.append(f"{state.identity.thread_id}: {type(exc).__name__}: {exc}")
                continue
            if not entry.conversations:
                skipped_count += 1
                diagnostics.append(f"{state.identity.thread_id}: no exportable conversations")
                continue
            entries.append(entry)

        if path is not None:
            self.write_jsonl(entries, path=path, options=options)

        return TrajectoryBatchExportResult(
            exported_count=len(entries),
            skipped_count=skipped_count,
            path=str(Path(path).expanduser().resolve()) if path is not None else None,
            format=options.format,
            entries=entries,
            diagnostics=diagnostics,
        )

    def write_jsonl(
        self,
        entries: Iterable[TrajectoryExportEntry],
        *,
        path: str | Path,
        options: TrajectoryExportOptions | None = None,
    ) -> Path:
        options = options or TrajectoryExportOptions()
        resolved = Path(path).expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with resolved.open("w", encoding="utf-8", newline="\n") as handle:
            for entry in entries:
                if options.format is TrajectoryExportFormat.SHAREGPT:
                    payload = entry.to_sharegpt_payload(include_metadata=options.include_metadata)
                else:
                    payload = entry.model_dump(mode="json", by_alias=True)
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        return resolved

    def _turns_from_message(
        self,
        message: dict[str, Any],
        *,
        index: int,
        options: TrajectoryExportOptions,
    ) -> list[TrajectoryTurn]:
        role = str(message.get("role") or "")
        if role == "user":
            role = "human"
        if role == "assistant":
            role = "ai"
        if role == "system" and not options.include_system:
            return []
        if role == "tool" and not options.include_tools:
            return []

        text = self._message_text(message, options=options)
        if not text.strip():
            return []

        from_role = self._sharegpt_role(role)
        if from_role is None:
            return []

        message_id = str(message.get("id")) if message.get("id") is not None else f"message-{index}"
        metadata = self._message_metadata(message, role=role, options=options)
        max_chars = (
            options.compression.max_tool_result_chars
            if role == "tool"
            else options.compression.max_message_chars
        )
        return [
            TrajectoryTurn(
                **{
                    "from": from_role,
                    "value": self._truncate(text, max_chars),
                    "message_id": message_id,
                    "role": role,
                    "metadata": metadata,
                }
            )
        ]

    def _message_text(self, message: dict[str, Any], *, options: TrajectoryExportOptions) -> str:
        blocks = message.get("content_blocks")
        if isinstance(blocks, list):
            parts: list[str] = []
            reasoning_parts: list[str] = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "text")
                text = self._safe_text(block.get("text") or block.get("thinking") or "", options=options)
                if not text:
                    continue
                if block_type == "thinking":
                    if options.include_reasoning:
                        reasoning_parts.append(text)
                    continue
                parts.append(text)
            if reasoning_parts and options.include_reasoning:
                parts = [f"<think>\n{'\n\n'.join(reasoning_parts)}\n</think>", *parts]
            if parts:
                return "\n\n".join(parts)

        content = message.get("content")
        text = self._safe_text(content, options=options)
        if not options.include_reasoning and "<think" in text.lower():
            text = self._strip_inline_think_blocks(text)
        if options.include_reasoning and isinstance(message.get("reasoning"), str):
            reasoning = self._safe_text(message.get("reasoning"), options=options)
            if reasoning:
                return f"<think>\n{reasoning}\n</think>\n\n{text}" if text else f"<think>\n{reasoning}\n</think>"
        return text

    def _message_metadata(
        self,
        message: dict[str, Any],
        *,
        role: str,
        options: TrajectoryExportOptions,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if role == "tool":
            for key in ("tool_call_id", "name", "status"):
                if message.get(key) is not None:
                    metadata[key] = self._safe_value(message.get(key), options=options)
        raw_tool_calls = message.get("tool_calls")
        if isinstance(raw_tool_calls, list) and raw_tool_calls:
            calls: list[dict[str, Any]] = []
            for item in raw_tool_calls:
                if not isinstance(item, dict):
                    continue
                normalized = {
                    "id": item.get("id"),
                    "name": item.get("name"),
                }
                if options.include_tool_args and isinstance(item.get("args"), dict):
                    normalized["args"] = self._safe_value(item.get("args"), options=options)
                calls.append({key: value for key, value in normalized.items() if value is not None})
            if calls:
                metadata["tool_calls"] = calls
        elif role == "ai" and options.include_parsed_tool_calls:
            parsed = self.tool_call_parser.parse(self._message_text(message, options=options))
            parsed_tool_calls = [
                self._parsed_tool_call_metadata(call.model_dump(mode="json", exclude={"raw"}), options=options)
                for call in parsed.calls
            ]
            if parsed_tool_calls:
                metadata["parsed_tool_calls"] = parsed_tool_calls
            if parsed.diagnostics:
                metadata["tool_call_parse_diagnostics"] = parsed.diagnostics[:5]
        if "status" in message and role != "tool":
            metadata["status"] = self._safe_value(message.get("status"), options=options)
        if not metadata:
            return {}
        return self._safe_value(metadata, options=options)

    def _parsed_tool_call_metadata(self, call: dict[str, Any], *, options: TrajectoryExportOptions) -> dict[str, Any]:
        if not options.include_tool_args:
            call = {key: value for key, value in call.items() if key != "args"}
        return call

    def _compress_turns(
        self,
        turns: list[TrajectoryTurn],
        config: TrajectoryCompressionConfig,
    ) -> tuple[list[TrajectoryTurn], int]:
        if not config.enabled or config.max_turns is None or len(turns) <= config.max_turns:
            return turns, 0

        first_count = min(config.keep_first_turns, len(turns))
        remaining_budget = max(config.max_turns - first_count - 1, 0)
        last_count = min(config.keep_last_turns, remaining_budget, max(len(turns) - first_count, 0))
        head = turns[:first_count]
        tail = turns[len(turns) - last_count :] if last_count else []
        omitted = len(turns) - len(head) - len(tail)
        if omitted <= 0:
            return turns[: config.max_turns], max(len(turns) - config.max_turns, 0)

        marker = TrajectoryTurn(
            **{
                "from": "system",
                "value": config.marker_template.format(omitted_turns=omitted),
                "message_id": "trajectory-compression-marker",
                "role": "system",
                "metadata": {"compression_marker": True, "omitted_turns": omitted},
            }
        )
        return [*head, marker, *tail], omitted

    def _build_stats(
        self,
        state: ThreadState,
        *,
        raw_turns: list[TrajectoryTurn],
        exported_turns: list[TrajectoryTurn],
        omitted_turn_count: int,
        options: TrajectoryExportOptions,
    ) -> TrajectoryStats:
        role_counts: dict[str, int] = {}
        for turn in raw_turns:
            role_counts[turn.from_] = role_counts.get(turn.from_, 0) + 1
        tool_stats: dict[str, ToolUsageStats] = {}
        tool_call_count = 0
        tool_success_count = 0
        tool_error_count = 0
        for activity in state.execution.recent_tool_activity:
            name = activity.name or activity.display_name or "unknown"
            stats = tool_stats.setdefault(name, ToolUsageStats())
            stats.count += 1
            tool_call_count += 1
            normalized_status = str(activity.status or "").lower()
            if normalized_status in {"success", "completed", "complete"} or activity.completed_at is not None:
                stats.success_count += 1
                tool_success_count += 1
            elif normalized_status in {"error", "failed", "failure"}:
                stats.error_count += 1
                tool_error_count += 1
            else:
                stats.running_count += 1
            if activity.duration_ms is not None:
                stats.total_duration_ms += max(int(activity.duration_ms), 0)

        completed = state.lifecycle.status in {
            ThreadLifecycleStatus.COMPLETED,
            ThreadLifecycleStatus.READY,
            ThreadLifecycleStatus.ARCHIVED,
        }
        artifact_count = (
            len(state.artifacts.output_artifacts)
            + len(state.artifacts.uploaded_files)
            + len(state.artifacts.presented_artifacts)
        )
        token_usage = self._safe_value(state.execution.token_usage, options=options) if options.include_token_usage else {}
        return TrajectoryStats(
            message_count=len(state.conversation.messages),
            exported_turn_count=len(exported_turns),
            original_turn_count=len(raw_turns),
            omitted_turn_count=omitted_turn_count,
            user_turns=role_counts.get("human", 0),
            assistant_turns=role_counts.get("gpt", 0),
            system_turns=role_counts.get("system", 0),
            tool_turns=role_counts.get("tool", 0),
            tool_call_count=tool_call_count,
            tool_success_count=tool_success_count,
            tool_error_count=tool_error_count,
            approval_count=len(state.approvals.recent_approval_events),
            artifact_count=artifact_count,
            completed=completed,
            interrupted=state.execution.last_message_interrupted or state.lifecycle.status in {
                ThreadLifecycleStatus.INTERRUPTED,
                ThreadLifecycleStatus.CANCELLED,
                ThreadLifecycleStatus.TIMED_OUT,
            },
            token_usage=token_usage if isinstance(token_usage, dict) else {},
            tool_stats=tool_stats,
        )

    def _build_quality_report(
        self,
        turns: list[TrajectoryTurn],
        *,
        stats: TrajectoryStats,
        raw_turn_count: int,
        omitted_turn_count: int,
        options: TrajectoryExportOptions,
    ) -> TrajectoryQualityReport:
        issues: list[TrajectoryQualityIssue] = []
        if not turns:
            issues.append(TrajectoryQualityIssue(severity="error", code="empty_export", message="trajectory has no exportable turns"))
        if stats.user_turns == 0:
            issues.append(TrajectoryQualityIssue(severity="error", code="missing_user_turn", message="trajectory has no user turn"))
        if stats.assistant_turns == 0:
            issues.append(TrajectoryQualityIssue(severity="error", code="missing_assistant_turn", message="trajectory has no assistant turn"))
        if omitted_turn_count:
            issues.append(
                TrajectoryQualityIssue(
                    severity="warning",
                    code="compressed_middle_turns",
                    message=f"trajectory omitted {omitted_turn_count} middle turn(s)",
                )
            )
        if stats.interrupted:
            issues.append(TrajectoryQualityIssue(severity="warning", code="interrupted_thread", message="thread was interrupted, cancelled, or timed out"))

        tool_call_ids: set[str] = set()
        tool_result_ids: set[str] = set()
        for index, turn in enumerate(turns):
            lowered = turn.value.lower()
            if not options.include_reasoning and "<think" in lowered:
                issues.append(
                    TrajectoryQualityIssue(
                        severity="error",
                        code="reasoning_leak",
                        message="assistant turn still contains inline reasoning tags",
                        turn_index=index,
                        message_id=turn.message_id,
                    )
                )
            if "[redacted:" in lowered:
                issues.append(
                    TrajectoryQualityIssue(
                        severity="info",
                        code="secret_redacted",
                        message="secret-like text was redacted from the trajectory",
                        turn_index=index,
                        message_id=turn.message_id,
                    )
                )
            metadata = turn.metadata or {}
            if isinstance(metadata.get("tool_calls"), list):
                for call in metadata["tool_calls"]:
                    if isinstance(call, dict) and call.get("id") is not None:
                        tool_call_ids.add(str(call["id"]))
            if isinstance(metadata.get("parsed_tool_calls"), list):
                for call in metadata["parsed_tool_calls"]:
                    if isinstance(call, dict) and call.get("id") is not None:
                        tool_call_ids.add(str(call["id"]))
            if turn.from_ == "tool" and metadata.get("tool_call_id") is not None:
                tool_result_ids.add(str(metadata["tool_call_id"]))
        for missing_id in sorted(tool_call_ids - tool_result_ids):
            issues.append(
                TrajectoryQualityIssue(
                    severity="warning",
                    code="tool_call_without_result",
                    message=f"tool call {missing_id} has no exported tool result",
                    message_id=missing_id,
                )
            )

        severity_rank = {"error": 3, "warning": 2, "info": 1}
        worst = max((severity_rank.get(issue.severity, 0) for issue in issues), default=0)
        status = "failed" if worst >= 3 else "warning" if worst >= 2 else "passed"
        penalty = sum(0.2 if issue.severity == "error" else 0.08 if issue.severity == "warning" else 0.02 for issue in issues)
        score = max(0.0, round(1.0 - penalty, 4))
        return TrajectoryQualityReport(
            status=status,
            score=score,
            issues=issues,
            summary={
                "raw_turn_count": raw_turn_count,
                "exported_turn_count": len(turns),
                "omitted_turn_count": omitted_turn_count,
                "issue_count": len(issues),
                "error_count": sum(1 for issue in issues if issue.severity == "error"),
                "warning_count": sum(1 for issue in issues if issue.severity == "warning"),
                "info_count": sum(1 for issue in issues if issue.severity == "info"),
            },
        )

    def _build_metadata(self, state: ThreadState, *, options: TrajectoryExportOptions) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "title": state.conversation.title,
            "summary": self._truncate(self._safe_text(state.conversation.summary, options=options), options.compression.max_metadata_chars),
            "status": state.lifecycle.status.value,
            "created_at": state.lifecycle.created_at.isoformat(),
            "updated_at": state.lifecycle.updated_at.isoformat(),
            "completed_at": state.lifecycle.completed_at.isoformat() if state.lifecycle.completed_at else None,
            "execution_mode": state.execution.execution_mode.value,
            "selected_model": state.execution.selected_model,
            "active_model": state.execution.active_model,
            "reasoning_effort": state.execution.reasoning_effort or state.execution.selected_reasoning_effort,
            "workspace_mode": state.thread_data.workspace_mode,
            "capabilities": {
                "visible_tool_count": len(state.capabilities.visible_tool_names),
                "deferred_tool_count": len(state.capabilities.deferred_tool_names),
                "enabled_skill_count": len(state.capabilities.enabled_skill_ids),
            },
            "prompt_snapshot": {
                "snapshot_id": state.prompt_snapshot.snapshot_id,
                "snapshot_hash": state.prompt_snapshot.snapshot_hash,
            },
            "context_window_usage": self._safe_value(state.execution.context_window_usage, options=options),
        }
        if options.include_artifacts:
            metadata["artifacts"] = {
                "outputs": self._safe_value(state.artifacts.output_artifacts, options=options),
                "uploads": self._safe_value(state.artifacts.uploaded_files, options=options),
                "presented": self._safe_value(state.artifacts.presented_artifacts, options=options),
            }
        if options.include_approvals:
            metadata["approvals"] = [
                self._safe_value(event.model_dump(mode="json"), options=options)
                for event in state.approvals.recent_approval_events
            ]
        return self._drop_none(metadata)

    def _entry_id(self, state: ThreadState) -> str:
        if state.identity.run_id:
            return f"{state.identity.thread_id}:{state.identity.run_id}"
        return state.identity.thread_id

    def _safe_text(self, value: Any, *, options: TrajectoryExportOptions) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            text = value
        elif isinstance(value, (dict, list, tuple)):
            text = json.dumps(self._json_safe(value), ensure_ascii=False, sort_keys=True)
        else:
            text = str(value)
        return self.scrubber.scrub(text).text if options.scrub_secrets else text

    def _safe_value(self, value: Any, *, options: TrajectoryExportOptions) -> Any:
        value = self._json_safe(value)
        if isinstance(value, str):
            return self._safe_text(value, options=options)
        if isinstance(value, list):
            return [self._safe_value(item, options=options) for item in value]
        if isinstance(value, dict):
            return {str(key): self._safe_value(item, options=options) for key, item in value.items()}
        return value

    def _json_safe(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(item) for item in value]
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def _truncate(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        omitted = len(text) - max_chars
        return f"{text[:max_chars]}...[truncated {omitted} chars]"

    def _strip_inline_think_blocks(self, text: str) -> str:
        if "<think" not in text.lower():
            return text
        return re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).lstrip()

    def _sharegpt_role(self, role: str) -> str | None:
        if role in {"human", "user"}:
            return "human"
        if role in {"ai", "assistant"}:
            return "gpt"
        if role == "system":
            return "system"
        if role == "tool":
            return "tool"
        return None

    def _drop_none(self, value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item
            for key, item in value.items()
            if item is not None
        }
