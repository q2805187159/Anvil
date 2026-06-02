from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from anvil.agents import ThreadLifecycleStatus, ThreadState
from anvil.memory_platform.scrubber import MemorySecretScrubber

from .contracts import (
    EvaluationBatchReport,
    EvaluationReportEvaluatorResult,
    EvaluationReportCapabilitySection,
    EvaluationReportIssue,
    EvaluationReportMemorySection,
    EvaluationReportOptions,
    EvaluationReportRuntimeSection,
    EvaluationReportStep,
    EvaluationReportStepChainSection,
    EvaluationReportToolCall,
    EvaluationThreadReport,
    TrajectoryExportOptions,
    utc_now,
)
from .exporter import ThreadTrajectoryExporter


class ThreadEvaluationReportBuilder:
    """Build read-only benchmark/debug reports from durable thread state."""

    def __init__(
        self,
        *,
        trajectory_exporter: ThreadTrajectoryExporter | None = None,
        scrubber: MemorySecretScrubber | None = None,
    ) -> None:
        self.scrubber = scrubber or MemorySecretScrubber()
        self.trajectory_exporter = trajectory_exporter or ThreadTrajectoryExporter(scrubber=self.scrubber)

    def build_thread_report(
        self,
        state: ThreadState,
        *,
        options: EvaluationReportOptions | None = None,
        evaluator_result: EvaluationReportEvaluatorResult | None = None,
    ) -> EvaluationThreadReport:
        options = options or EvaluationReportOptions()
        trajectory = self.trajectory_exporter.export_thread(
            state,
            options=TrajectoryExportOptions(
                include_metadata=True,
                include_reasoning=False,
                include_hidden_steps=False,
                include_token_usage=True,
                scrub_secrets=options.scrub_secrets,
            ),
        )
        step_chain = self._step_chain(state, options=options)
        risks = self._hidden_bug_risks(state, trajectory.quality.issues, step_chain=step_chain)
        recommendations = self._recommendations(state, risks)
        report = EvaluationThreadReport(
            report_id=f"thread-eval-{uuid4().hex[:12]}",
            thread_id=state.identity.thread_id,
            run_id=state.identity.run_id,
            generated_at=utc_now(),
            title=self._safe_text(state.conversation.title, max_chars=240, options=options),
            task_preview=self._task_preview(state, options=options),
            final_answer_preview=self._final_answer_preview(state, options=options),
            outcome=self._outcome(state, trajectory.quality.status),
            score=self._score(state, trajectory.quality.score, risks, evaluator_result=evaluator_result),
            evaluator=self._safe_evaluator(evaluator_result, options=options),
            runtime=EvaluationReportRuntimeSection(
                status=state.lifecycle.status.value,
                model=state.execution.active_model or state.execution.selected_model,
                execution_mode=state.execution.execution_mode.value,
                reasoning_effort=state.execution.reasoning_effort or state.execution.selected_reasoning_effort,
                runtime_phase_timings=self._safe_mapping(state.execution.runtime_phase_timings, options=options),
                runtime_phase_diagnostics=self._safe_mapping(
                    _runtime_phase_diagnostics(state.execution.runtime_phase_timings),
                    options=options,
                ),
                runtime_assembly_snapshot=self._safe_mapping(state.execution.runtime_assembly_snapshot, options=options),
                runtime_assembly_diff=self._safe_mapping(state.execution.runtime_assembly_diff, options=options),
                context_window_usage=self._safe_mapping(state.execution.context_window_usage, options=options),
                token_usage=self._safe_mapping(state.execution.token_usage, options=options),
                model_fallback_history=[
                    self._safe_mapping(item, options=options)
                    for item in state.execution.model_fallback_history[:20]
                    if isinstance(item, dict)
                ],
            ),
            trajectory_quality=trajectory.quality,
            stats=trajectory.stats,
            tool_calls=self._tool_calls(state, options=options),
            step_chain=step_chain,
            memory=EvaluationReportMemorySection(
                namespace=state.memory.memory_namespace,
                injected_memory_snapshot_id=state.memory.injected_memory_snapshot_id,
                procedure_learning_runs=list(state.memory.procedure_learning_runs),
                procedure_learning_signatures=list(state.memory.procedure_learning_signatures),
            ),
            capabilities=EvaluationReportCapabilitySection(
                visible_tool_names=list(state.capabilities.visible_tool_names),
                deferred_tool_names=list(state.capabilities.deferred_tool_names),
                enabled_skill_ids=list(state.capabilities.enabled_skill_ids),
                capability_bundle_fingerprint=state.capabilities.capability_bundle_fingerprint,
            ),
            approvals=[self._safe_mapping(item.model_dump(mode="json"), options=options) for item in state.approvals.recent_approval_events[:20]],
            artifacts={
                "output_artifacts": list(state.artifacts.output_artifacts[:50]),
                "uploaded_file_count": len(state.artifacts.uploaded_files),
                "presented_artifacts": list(state.artifacts.presented_artifacts[:50]),
            },
            hidden_bug_risks=risks,
            recommendations=recommendations,
            notes=self._notes(state, trajectory.quality.status),
        )
        if options.include_markdown:
            report = report.model_copy(update={"markdown": self.render_thread_markdown(report)})
        return report

    def build_batch_report(
        self,
        states: Iterable[ThreadState],
        *,
        requested_thread_ids: list[str] | None = None,
        options: EvaluationReportOptions | None = None,
        evaluator_results: dict[str, EvaluationReportEvaluatorResult] | None = None,
        markdown_path: str | Path | None = None,
    ) -> EvaluationBatchReport:
        state_list = list(states)
        evaluator_results = evaluator_results or {}
        reports = [
            self.build_thread_report(
                state,
                options=options,
                evaluator_result=evaluator_results.get(state.identity.thread_id),
            )
            for state in state_list
        ]
        found_ids = {state.identity.thread_id for state in state_list}
        missing = [thread_id for thread_id in dict.fromkeys(requested_thread_ids or []) if thread_id not in found_ids]
        status_counts = Counter(report.outcome for report in reports)
        risk_counts = Counter(issue.code for report in reports for issue in report.hidden_bug_risks)
        average_score = round(sum(report.score for report in reports) / len(reports), 4) if reports else 0.0
        evaluator_scores = [
            result.score
            for result in evaluator_results.values()
            if result.score is not None
        ]
        report = EvaluationBatchReport(
            report_id=f"batch-eval-{uuid4().hex[:12]}",
            generated_at=utc_now(),
            thread_reports=reports,
            missing_thread_ids=missing,
            score=average_score,
            summary={
                "thread_count": len(reports),
                "missing_thread_count": len(missing),
                "outcomes": dict(sorted(status_counts.items())),
                "risk_codes": dict(sorted(risk_counts.items())),
                "average_score": average_score,
                "completed_count": sum(1 for report in reports if report.runtime.status in {"completed", "ready", "archived"}),
                "tool_call_count": sum(report.stats.tool_call_count for report in reports),
                "enabled_skill_count": sum(len(report.capabilities.enabled_skill_ids) for report in reports),
                "external_evaluator_count": len(evaluator_results),
                "external_evaluator_average_score": round(sum(evaluator_scores) / len(evaluator_scores), 4) if evaluator_scores else None,
                "external_evaluator_passed_count": sum(1 for result in evaluator_results.values() if result.passed is True),
                "external_evaluator_failed_count": sum(1 for result in evaluator_results.values() if result.passed is False),
            },
        )
        markdown = self.render_batch_markdown(report)
        if markdown_path is not None:
            path = Path(markdown_path).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8", newline="\n")
            report = report.model_copy(update={"markdown_path": str(path)})
        if options and options.include_markdown:
            report = report.model_copy(update={"markdown": markdown})
        return report

    def render_thread_markdown(self, report: EvaluationThreadReport) -> str:
        lines = [
            f"## Thread Report: {report.thread_id}",
            "",
            f"- Report ID: `{report.report_id}`",
            f"- Run ID: `{report.run_id or '-'}`",
            f"- Outcome: `{report.outcome}`",
            f"- Report score: `{report.score:.4f}`",
            f"- Runtime status: `{report.runtime.status}`",
            f"- Model: `{report.runtime.model or '-'}`",
        ]
        if report.evaluator is not None:
            lines.extend(
                [
                    f"- Evaluator: `{report.evaluator.evaluator}`",
                    f"- Evaluator score: `{_format_optional_number(report.evaluator.score)}`",
                    f"- Evaluator passed: `{report.evaluator.passed}`",
                ]
            )
        lines.extend(["", "### Task", "", _markdown_block(report.task_preview or "-")])
        lines.extend(["", "### Final Answer", "", _markdown_block(report.final_answer_preview or "-")])
        lines.extend(["", "### Step Chain", ""])
        lines.append(
            "- Summary: "
            f"`total={report.step_chain.total} returned={report.step_chain.returned} "
            f"visible={report.step_chain.visible_step_count} hidden={report.step_chain.hidden_step_count} "
            f"open={report.step_chain.open_step_count} errors={report.step_chain.error_step_count}`"
        )
        lines.append(f"- Types: `{report.step_chain.type_counts or {}}`")
        lines.append(f"- Statuses: `{report.step_chain.status_counts or {}}`")
        if report.step_chain.items:
            for step in report.step_chain.items[:30]:
                label = step.tool_name or step.title or step.type
                lines.append(
                    f"- #{step.order if step.order is not None else '-'} `{step.type}` `{label}` "
                    f"status=`{step.status or '-'}` visibility=`{step.visibility or '-'}` "
                    f"duration_ms=`{step.duration_ms if step.duration_ms is not None else '-'}`"
                )
                if step.payload_preview:
                    lines.append(f"  - payload: {_markdown_inline_preview(step.payload_preview)}")
                if step.error_preview:
                    lines.append(f"  - error: {_markdown_inline_preview(step.error_preview)}")
            if report.step_chain.truncated:
                lines.append("- Step chain truncated to report budget.")
        else:
            lines.append("- None")
        lines.extend(
            [
                "",
                "### Runtime",
                "",
                f"- Total elapsed: `{report.runtime.runtime_phase_timings.get('total_elapsed_ms', '-')}` ms",
                f"- First model event: `{report.runtime.runtime_phase_timings.get('first_model_event_elapsed_ms', '-')}` ms",
                f"- First content delta: `{report.runtime.runtime_phase_timings.get('first_content_delta_elapsed_ms', '-')}` ms",
                f"- Context tokens: `{report.runtime.context_window_usage.get('context_tokens', '-')}`",
                f"- Token usage keys: `{', '.join(sorted(report.runtime.token_usage.keys())) or '-'}`",
            ]
        )
        if report.runtime.runtime_phase_diagnostics:
            lines.append(f"- Runtime phase diagnostics: `{_format_runtime_phase_diagnostics(report.runtime.runtime_phase_diagnostics)}`")
        prompt_cache_delta = _prompt_cache_delta(report.runtime.runtime_assembly_snapshot)
        if prompt_cache_delta:
            lines.append(f"- Prompt cache delta: `{_format_prompt_cache_delta(prompt_cache_delta)}`")
        prompt_cache = _prompt_cache_cumulative(report.runtime.runtime_assembly_snapshot)
        if prompt_cache:
            lines.append(f"- Prompt cache cumulative: `{_format_prompt_cache_cumulative(prompt_cache)}`")
        project_context_cache_status = _project_context_cache_status(report.runtime.runtime_assembly_snapshot)
        if project_context_cache_status:
            lines.append(f"- Project context cache: `{project_context_cache_status}`")
        runtime_path_cache_status = _runtime_path_cache_status(report.runtime.runtime_assembly_snapshot)
        if runtime_path_cache_status:
            lines.append(f"- Runtime path cache: `{runtime_path_cache_status}`")
        context_cache_diagnostics = _context_cache_diagnostics(report.runtime.runtime_assembly_snapshot)
        if context_cache_diagnostics:
            lines.append(f"- Context cache diagnostics: `{_format_context_cache_diagnostics(context_cache_diagnostics)}`")
        prompt_section_tokens = _prompt_section_tokens(report.runtime.runtime_assembly_snapshot)
        if prompt_section_tokens:
            lines.append(f"- Prompt section tokens: `{_format_prompt_section_tokens(prompt_section_tokens)}`")
        capability_diagnostics = _capability_assembly_diagnostics(report.runtime.runtime_assembly_snapshot)
        if capability_diagnostics:
            lines.append(f"- Capability diagnostics: `{_format_capability_diagnostics(capability_diagnostics)}`")
        memory_diagnostics = _memory_injection_diagnostics(report.runtime.runtime_assembly_snapshot)
        if memory_diagnostics:
            lines.append(f"- Memory injection diagnostics: `{_format_memory_injection_diagnostics(memory_diagnostics)}`")
        compaction_diagnostics = _compaction_diagnostics(
            report.runtime.runtime_assembly_snapshot,
            report.runtime.context_window_usage,
        )
        if compaction_diagnostics:
            lines.append(f"- Compaction diagnostics: `{_format_compaction_diagnostics(compaction_diagnostics)}`")
        runtime_diff_paths = _runtime_assembly_changed_paths(report.runtime.runtime_assembly_diff)
        if runtime_diff_paths:
            lines.append(f"- Runtime assembly diff: `{', '.join(runtime_diff_paths[:12])}`")
        lines.extend(["", "### Tool Calls", ""])
        if report.tool_calls:
            for tool in report.tool_calls[:20]:
                lines.append(
                    f"- `{tool.name or tool.display_name or 'tool'}` status=`{tool.status or '-'}` duration_ms=`{tool.duration_ms if tool.duration_ms is not None else '-'}`"
                )
        else:
            lines.append("- None")
        lines.extend(["", "### Skills / Memory", ""])
        lines.append(f"- Enabled skills: `{', '.join(report.capabilities.enabled_skill_ids) or '-'}`")
        lines.append(f"- Visible tools: `{len(report.capabilities.visible_tool_names)}`")
        lines.append(f"- Deferred tools: `{len(report.capabilities.deferred_tool_names)}`")
        lines.append(f"- Memory snapshot: `{report.memory.injected_memory_snapshot_id or '-'}`")
        lines.extend(["", "### Risks", ""])
        if report.hidden_bug_risks:
            for risk in report.hidden_bug_risks:
                lines.append(f"- `{risk.severity}` `{risk.code}`: {risk.message}")
        else:
            lines.append("- None")
        lines.extend(["", "### Recommendations", ""])
        lines.extend(f"- {item}" for item in report.recommendations)
        return "\n".join(lines).strip() + "\n"

    def render_batch_markdown(self, report: EvaluationBatchReport) -> str:
        lines = [
            f"# Anvil Evaluation Report: {report.report_id}",
            "",
            f"- Generated at: `{report.generated_at.isoformat()}`",
            f"- Thread count: `{report.summary.get('thread_count', len(report.thread_reports))}`",
            f"- Missing threads: `{len(report.missing_thread_ids)}`",
            f"- Average report score: `{report.score:.4f}`",
            f"- External evaluator count: `{report.summary.get('external_evaluator_count', 0)}`",
            f"- External evaluator average score: `{_format_optional_number(report.summary.get('external_evaluator_average_score'))}`",
            "",
            "## Summary",
            "",
            f"- Outcomes: `{report.summary.get('outcomes', {})}`",
            f"- Risk codes: `{report.summary.get('risk_codes', {})}`",
            f"- Tool calls: `{report.summary.get('tool_call_count', 0)}`",
            f"- Enabled skill count: `{report.summary.get('enabled_skill_count', 0)}`",
        ]
        if report.missing_thread_ids:
            lines.extend(["", "## Missing Threads", ""])
            lines.extend(f"- `{thread_id}`" for thread_id in report.missing_thread_ids)
        lines.extend(["", "## Thread Reports", ""])
        for thread_report in report.thread_reports:
            lines.append(self.render_thread_markdown(thread_report).strip())
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _task_preview(self, state: ThreadState, *, options: EvaluationReportOptions) -> str | None:
        if not options.include_conversation_preview:
            return None
        for message in state.conversation.messages:
            if str(message.get("role") or "") in {"human", "user"}:
                text = self._message_text(message, options=options)
                if text:
                    return text
        return None

    def _final_answer_preview(self, state: ThreadState, *, options: EvaluationReportOptions) -> str | None:
        if not options.include_conversation_preview:
            return None
        for message in reversed(state.conversation.messages):
            if str(message.get("role") or "") in {"ai", "assistant"}:
                text = self._message_text(message, options=options)
                if text:
                    return text
        return None

    def _message_text(self, message: dict[str, object], *, options: EvaluationReportOptions) -> str:
        blocks = message.get("content_blocks")
        if isinstance(blocks, list):
            parts: list[str] = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if str(block.get("type") or "text") == "thinking":
                    continue
                text = self._safe_text(block.get("text") or "", max_chars=options.max_preview_chars, options=options)
                if text:
                    parts.append(text)
            if parts:
                return self._safe_text("\n\n".join(parts), max_chars=options.max_preview_chars, options=options)
        return self._safe_text(message.get("content"), max_chars=options.max_preview_chars, options=options)

    def _tool_calls(self, state: ThreadState, *, options: EvaluationReportOptions) -> list[EvaluationReportToolCall]:
        calls: list[EvaluationReportToolCall] = []
        for activity in state.execution.recent_tool_activity[:100]:
            calls.append(
                EvaluationReportToolCall(
                    tool_call_id=activity.tool_call_id,
                    message_id=activity.message_id,
                    name=activity.name,
                    display_name=activity.display_name,
                    capability_group=activity.capability_group,
                    status=activity.status,
                    duration_ms=activity.duration_ms,
                    result_text=self._safe_text(activity.result_text, max_chars=options.max_tool_result_chars, options=options),
                )
            )
        return calls

    def _step_chain(self, state: ThreadState, *, options: EvaluationReportOptions) -> EvaluationReportStepChainSection:
        raw_steps = [item for item in state.conversation.steps if isinstance(item, dict)]
        ordered_steps = sorted(
            raw_steps,
            key=lambda item: (
                _int_or_none(item.get("order")) if _int_or_none(item.get("order")) is not None else 1_000_000,
                str(item.get("step_id") or ""),
            ),
        )
        type_counts = Counter(_normalized_step_type(item.get("type")) for item in ordered_steps)
        status_counts = Counter(_normalized_step_status(item.get("status")) for item in ordered_steps)
        visibility_counts = Counter(_normalized_step_visibility(item.get("visibility")) for item in ordered_steps)
        open_count = sum(1 for item in ordered_steps if _normalized_step_status(item.get("status")) in {"pending", "running"})
        error_count = sum(1 for item in ordered_steps if _normalized_step_status(item.get("status")) == "error")
        items = [self._step_chain_item(item, options=options) for item in ordered_steps[:120]]
        return EvaluationReportStepChainSection(
            total=len(ordered_steps),
            returned=len(items),
            truncated=len(ordered_steps) > len(items),
            visible_step_count=sum(count for visibility, count in visibility_counts.items() if visibility != "hidden"),
            hidden_step_count=visibility_counts.get("hidden", 0),
            open_step_count=open_count,
            error_step_count=error_count,
            type_counts=dict(sorted(type_counts.items())),
            status_counts=dict(sorted(status_counts.items())),
            visibility_counts=dict(sorted(visibility_counts.items())),
            items=items,
        )

    def _step_chain_item(self, step: dict[str, object], *, options: EvaluationReportOptions) -> EvaluationReportStep:
        visibility = _normalized_step_visibility(step.get("visibility"))
        include_payload = visibility != "hidden"
        payload = step.get("payload") if include_payload else None
        action = step.get("action") if include_payload else None
        error = step.get("error") if include_payload else None
        return EvaluationReportStep(
            step_id=str(step.get("step_id") or ""),
            message_id=str(step.get("message_id")) if step.get("message_id") is not None else None,
            type=_normalized_step_type(step.get("type")),
            title=self._safe_text(step.get("title"), max_chars=240, options=options) or None,
            status=_normalized_step_status(step.get("status")),
            visibility=visibility,
            tool_name=str(step.get("tool_name")) if step.get("tool_name") is not None else None,
            tool_call_id=str(step.get("tool_call_id")) if step.get("tool_call_id") is not None else None,
            duration_ms=_int_or_none(step.get("duration_ms")),
            order=_int_or_none(step.get("order")),
            started_at=str(step.get("started_at")) if step.get("started_at") is not None else None,
            completed_at=str(step.get("completed_at")) if step.get("completed_at") is not None else None,
            payload_preview=self._safe_text(payload, max_chars=options.max_tool_result_chars, options=options) if payload else None,
            action_preview=self._safe_text(action, max_chars=options.max_tool_result_chars, options=options) if action else None,
            error_preview=self._safe_text(error, max_chars=options.max_tool_result_chars, options=options) if error else None,
        )

    def _hidden_bug_risks(
        self,
        state: ThreadState,
        quality_issues,
        *,
        step_chain: EvaluationReportStepChainSection,
    ) -> list[EvaluationReportIssue]:
        risks: list[EvaluationReportIssue] = []
        if state.lifecycle.status not in {ThreadLifecycleStatus.COMPLETED, ThreadLifecycleStatus.READY, ThreadLifecycleStatus.ARCHIVED}:
            risks.append(EvaluationReportIssue(severity="warning", code="run_not_completed", message=f"thread ended with status {state.lifecycle.status.value}"))
        if state.lifecycle.last_error:
            risks.append(EvaluationReportIssue(severity="error", code="last_error_present", message=str(state.lifecycle.last_error)))
        if state.execution.last_message_interrupted:
            interrupt_reason = state.execution.last_message_interrupted_reason or "last assistant message was interrupted"
            risks.append(
                EvaluationReportIssue(
                    severity="warning",
                    code="last_message_interrupted",
                    message=interrupt_reason,
                )
            )
            interruption_kind = _interruption_kind(interrupt_reason, state.lifecycle.last_error)
            if interruption_kind is not None:
                risks.append(
                    EvaluationReportIssue(
                        severity="error",
                        code=f"interruption:{interruption_kind}",
                        message=_interruption_kind_message(interruption_kind, interrupt_reason),
                    )
                )
        if step_chain.open_step_count and state.lifecycle.status in {ThreadLifecycleStatus.COMPLETED, ThreadLifecycleStatus.READY, ThreadLifecycleStatus.ARCHIVED}:
            risks.append(
                EvaluationReportIssue(
                    severity="error",
                    code="step_chain:open_steps_after_terminal",
                    message=f"{step_chain.open_step_count} durable step(s) remained pending or running after terminal lifecycle status {state.lifecycle.status.value}",
                )
            )
        if step_chain.error_step_count and state.lifecycle.status in {ThreadLifecycleStatus.COMPLETED, ThreadLifecycleStatus.READY, ThreadLifecycleStatus.ARCHIVED}:
            risks.append(
                EvaluationReportIssue(
                    severity="warning",
                    code="step_chain:error_steps_with_completed_run",
                    message=f"{step_chain.error_step_count} durable step(s) ended with error while the run status is {state.lifecycle.status.value}",
                )
            )
        if step_chain.hidden_step_count and not step_chain.visible_step_count and state.execution.recent_tool_activity:
            risks.append(
                EvaluationReportIssue(
                    severity="warning",
                    code="step_chain:all_work_hidden",
                    message="durable tool or reasoning steps exist but every recorded step is hidden from the chat timeline",
                )
            )
        if state.execution.context_window_usage:
            percent = _float_or_none(state.execution.context_window_usage.get("percent_used"))
            threshold = _float_or_none(state.execution.context_window_usage.get("auto_compact_threshold_percent"))
            if percent is not None and threshold is not None and percent >= threshold:
                risks.append(EvaluationReportIssue(severity="warning", code="context_near_compaction", message="context window is at or above auto-compaction threshold"))
        if state.execution.runtime_phase_timings:
            marks = state.execution.runtime_phase_timings.get("marks")
            if isinstance(marks, list):
                slow_marks = [
                    mark for mark in marks
                    if isinstance(mark, dict) and _int_or_none(mark.get("duration_since_previous_ms")) is not None and int(mark["duration_since_previous_ms"]) >= 30_000
                ]
                for mark in slow_marks[:3]:
                    risks.append(
                        EvaluationReportIssue(
                            severity="warning",
                            code="slow_runtime_phase",
                            message=f"{mark.get('label') or mark.get('phase') or 'runtime phase'} took {mark.get('duration_since_previous_ms')}ms",
                        )
                    )
        for issue in quality_issues:
            severity = "error" if issue.severity == "error" else "warning" if issue.severity == "warning" else "info"
            if severity == "info":
                continue
            risks.append(EvaluationReportIssue(severity=severity, code=f"trajectory:{issue.code}", message=issue.message))
        return risks

    def _recommendations(self, state: ThreadState, risks: list[EvaluationReportIssue]) -> list[str]:
        codes = {risk.code for risk in risks}
        recommendations: list[str] = []
        if "slow_runtime_phase" in codes:
            phase_diagnostics = _runtime_phase_diagnostics(state.execution.runtime_phase_timings)
            category = str(phase_diagnostics.get("slowest_phase_category") or "")
            if category == "provider_first_content_wait":
                recommendations.append("Inspect provider/model streaming because first visible content lagged after the first graph/model event.")
            elif category == "runtime_assembly":
                recommendations.append("Inspect runtime assembly marks, capability/schema budget, context cache, memory recall, and model client construction.")
            elif category == "finalization":
                recommendations.append("Inspect final persistence, artifact/report projection, and post-run maintenance after the model response.")
            else:
                recommendations.append("Inspect runtime_phase_timings to profile runtime assembly, provider latency, persistence, or post-run maintenance.")
        if "context_near_compaction" in codes:
            recommendations.append("Review context_window_usage and compression settings before running longer multi-turn benchmark tasks.")
        if "interruption:empty_final_after_tools" in codes:
            recommendations.append("Inspect the final model turn and tool results: the provider stopped after tool execution without a final answer, so the run must be continued or retried rather than scored as completed.")
        if "interruption:tool_loop_hard_stop" in codes:
            recommendations.append("Inspect repeated tool-call signatures and tool results; the loop guard interrupted a repeated internal tool loop before normal completion.")
        if "step_chain:open_steps_after_terminal" in codes:
            recommendations.append("Inspect durable conversation.steps finalization because pending/running steps remained after a terminal run status.")
        if "step_chain:error_steps_with_completed_run" in codes:
            recommendations.append("Inspect completed runs with error steps before scoring the task; a tool or content step failed even though lifecycle is terminal success.")
        if "step_chain:all_work_hidden" in codes:
            recommendations.append("Inspect step visibility rules if user-visible progress disappeared from the chat timeline while tools were running.")
        prompt_cache_delta = _prompt_cache_delta(state.execution.runtime_assembly_snapshot)
        if prompt_cache_delta and _int_or_none(prompt_cache_delta.get("misses")):
            recommendations.append("Review prompt assembly cache stability if repeated tasks keep missing the stable prompt cache.")
        context_cache_diagnostics = _context_cache_diagnostics(state.execution.runtime_assembly_snapshot)
        if context_cache_diagnostics and str(context_cache_diagnostics.get("project_status") or "") == "miss":
            recommendations.append("Review project context cache invalidation if repeated same-workspace turns keep rebuilding context files.")
        if context_cache_diagnostics and str(context_cache_diagnostics.get("runtime_status") or "") == "miss":
            recommendations.append("Review runtime path roots and workspace settings if repeated same-thread turns keep rebuilding path context.")
        if context_cache_diagnostics and _int_or_none(context_cache_diagnostics.get("project_truncated_files")):
            recommendations.append("Review context_files budgets because at least one project context file was truncated before prompt assembly.")
        capability_diagnostics = _capability_assembly_diagnostics(state.execution.runtime_assembly_snapshot)
        if capability_diagnostics and _int_or_none(capability_diagnostics.get("schema_deferred_tool_count")):
            recommendations.append("Review visible tool schema budget and deferred capability search if required tools were deferred for schema cost.")
        memory_diagnostics = _memory_injection_diagnostics(state.execution.runtime_assembly_snapshot)
        if memory_diagnostics and _bool_or_false(memory_diagnostics.get("truncated")):
            recommendations.append("Review memory recall ranking and turn_recall_token_budget because injected memory was truncated before the model call.")
        if memory_diagnostics and str(memory_diagnostics.get("status") or "") == "error":
            recommendations.append("Inspect memory prefetch diagnostics; the live turn failed open without dynamic recall.")
        compaction_diagnostics = _compaction_diagnostics(
            state.execution.runtime_assembly_snapshot,
            state.execution.context_window_usage,
        )
        if compaction_diagnostics and str(compaction_diagnostics.get("summary_source") or "") in {"fallback", "empty_fallback"}:
            recommendations.append("Inspect summarization model routing and fallback quality because context compaction did not use a model-generated summary.")
        if compaction_diagnostics and (
            _int_or_none(compaction_diagnostics.get("pruned_tool_result_count"))
            or _int_or_none(compaction_diagnostics.get("truncated_message_count"))
        ):
            recommendations.append("Review compacted tool/message evidence if the next turn depends on details from archived context.")
        runtime_diff_paths = _runtime_assembly_changed_paths(state.execution.runtime_assembly_diff)
        if runtime_diff_paths:
            recommendations.append("Inspect runtime_assembly_diff before comparing this run with previous benchmark samples.")
        if any(code.startswith("trajectory:") for code in codes):
            recommendations.append("Inspect trajectory quality issues before using this run for evaluation, training, or procedure learning.")
        if state.capabilities.deferred_tool_names:
            recommendations.append("Check deferred capability search behavior if the task required tools that were not visible up front.")
        if not recommendations:
            recommendations.append("No immediate report-level remediation was detected; compare with benchmark-specific assertions.")
        return recommendations

    def _notes(self, state: ThreadState, quality_status: str) -> list[str]:
        notes = [
            "Report is generated from durable ThreadState and trajectory quality; it does not include provider-private chain-of-thought.",
            f"Trajectory quality status: {quality_status}.",
        ]
        if state.memory.injected_memory_snapshot_id:
            notes.append("Memory injection was present; inspect injected_memory_snapshot_id for replay context.")
        memory_diagnostics = _memory_injection_diagnostics(state.execution.runtime_assembly_snapshot)
        if memory_diagnostics:
            notes.append("Memory injection diagnostics include recall counts and token budget only; recalled memory text is not copied into this report.")
        compaction_diagnostics = _compaction_diagnostics(
            state.execution.runtime_assembly_snapshot,
            state.execution.context_window_usage,
        )
        if compaction_diagnostics:
            notes.append("Compaction diagnostics include summary/source counters only; archived transcript, summary prompts, and image data are not copied into this report.")
        if state.capabilities.enabled_skill_ids:
            notes.append("Enabled skills are listed as ids only; skill content is not copied into this report.")
        if state.conversation.steps:
            notes.append("Step chain includes durable step metadata and scrubbed visible previews only; hidden/private step payloads are omitted.")
        return notes

    def _outcome(self, state: ThreadState, quality_status: str) -> str:
        if state.lifecycle.status in {ThreadLifecycleStatus.FAILED, ThreadLifecycleStatus.TIMED_OUT}:
            return "failed"
        if state.lifecycle.status in {ThreadLifecycleStatus.CANCELLED, ThreadLifecycleStatus.INTERRUPTED}:
            return "interrupted"
        if quality_status == "failed":
            return "needs_review"
        if quality_status == "warning":
            return "completed_with_warnings"
        return "completed"

    def _score(
        self,
        state: ThreadState,
        quality_score: float,
        risks: list[EvaluationReportIssue],
        *,
        evaluator_result: EvaluationReportEvaluatorResult | None = None,
    ) -> float:
        score = float(quality_score)
        if state.lifecycle.status not in {ThreadLifecycleStatus.COMPLETED, ThreadLifecycleStatus.READY, ThreadLifecycleStatus.ARCHIVED}:
            score -= 0.25
        score -= 0.1 * sum(1 for risk in risks if risk.severity == "error")
        score -= 0.04 * sum(1 for risk in risks if risk.severity == "warning")
        if evaluator_result is not None:
            if evaluator_result.passed is False:
                score -= 0.3
            if evaluator_result.score is not None:
                max_score = evaluator_result.max_score or 1.0
                if max_score > 0:
                    normalized = max(0.0, min(1.0, float(evaluator_result.score) / float(max_score)))
                    score = (score * 0.6) + (normalized * 0.4)
        return round(max(0.0, min(1.0, score)), 4)

    def _safe_evaluator(
        self,
        evaluator_result: EvaluationReportEvaluatorResult | None,
        *,
        options: EvaluationReportOptions,
    ) -> EvaluationReportEvaluatorResult | None:
        if evaluator_result is None:
            return None
        return evaluator_result.model_copy(
            update={
                "summary": self._safe_text(evaluator_result.summary, max_chars=options.max_preview_chars, options=options),
                "details": self._safe_value(evaluator_result.details, options=options),
            }
        )

    def _safe_mapping(self, value: dict[str, object], *, options: EvaluationReportOptions) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        return self._safe_value(value, options=options)

    def _safe_value(self, value, *, options: EvaluationReportOptions):
        if isinstance(value, str):
            return self._safe_text(value, max_chars=options.max_preview_chars, options=options)
        if isinstance(value, dict):
            return {str(key): self._safe_value(item, options=options) for key, item in value.items()}
        if isinstance(value, list):
            return [self._safe_value(item, options=options) for item in value]
        return value

    def _safe_text(self, value, *, max_chars: int, options: EvaluationReportOptions) -> str:
        text = "" if value is None else str(value)
        if options.scrub_secrets:
            text = self.scrubber.scrub(text).text
        if len(text) > max_chars:
            return f"{text[:max_chars]}... [truncated {len(text) - max_chars} chars]"
        return text


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_false(value) -> bool:
    return bool(value)


def _normalized_step_type(value: object) -> str:
    step_type = str(value or "content")
    if step_type in {"thinking", "call", "content"}:
        return step_type
    return "content"


def _normalized_step_status(value: object) -> str:
    status = str(value or "success")
    if status in {"pending", "running", "success", "error"}:
        return status
    if status in {"completed", "complete"}:
        return "success"
    if status in {"failed", "cancelled", "timed_out", "interrupted"}:
        return "error"
    return "running"


def _normalized_step_visibility(value: object) -> str:
    visibility = str(value or "chat")
    return "hidden" if visibility == "hidden" else "chat"


def _markdown_inline_preview(value: str) -> str:
    preview = " ".join(str(value or "").split())
    if len(preview) > 240:
        preview = f"{preview[:237]}..."
    preview = preview.replace("`", "'")
    return f"`{preview}`"


def _interruption_kind(reason: object, last_error: object) -> str | None:
    text = f"{reason or ''}\n{last_error or ''}".lower()
    if "without producing a final answer" in text or "stopped after tool execution" in text:
        return "empty_final_after_tools"
    if "repeated internal tool loop" in text or "identical tool-call rounds" in text:
        return "tool_loop_hard_stop"
    return None


def _interruption_kind_message(kind: str, reason: object) -> str:
    detail = str(reason or "").strip()
    if kind == "empty_final_after_tools":
        return f"model stopped after tool execution without a final answer: {detail}"
    if kind == "tool_loop_hard_stop":
        return f"loop guard interrupted repeated tool calls before normal completion: {detail}"
    return detail or kind


def _runtime_phase_diagnostics(runtime_phase_timings: dict[str, object]) -> dict[str, object]:
    if not isinstance(runtime_phase_timings, dict):
        return {}
    marks = runtime_phase_timings.get("marks")
    mark_payloads = [item for item in marks if isinstance(item, dict)] if isinstance(marks, list) else []
    if not runtime_phase_timings and not mark_payloads:
        return {}
    slowest_mark = _slowest_runtime_phase_mark(mark_payloads)
    first_model_ms = _int_or_none(runtime_phase_timings.get("first_model_event_elapsed_ms"))
    first_content_ms = _int_or_none(runtime_phase_timings.get("first_content_delta_elapsed_ms"))
    runtime_assembly_ms = _int_or_none(runtime_phase_timings.get("runtime_assembly_elapsed_ms"))
    if runtime_assembly_ms is None:
        runtime_assembly_ms = _phase_elapsed_ms(
            mark_payloads,
            "agent_stream_entered",
            "run_started_emitted",
            "input_payload_built",
            "runtime_assembled",
        )
    completed_ms = _phase_elapsed_ms(
        mark_payloads,
        "run_completed_emitted",
        "final_state_persisted",
    ) or _int_or_none(runtime_phase_timings.get("completed_elapsed_ms"))
    diagnostics: dict[str, object] = {
        "phase_count": len(mark_payloads),
        "runtime_assembly_elapsed_ms": runtime_assembly_ms,
        "model_start_wait_ms": _int_or_none(runtime_phase_timings.get("model_start_wait_ms")),
        "first_model_event_elapsed_ms": first_model_ms,
        "first_content_delta_elapsed_ms": first_content_ms,
        "first_content_wait_ms": _int_or_none(runtime_phase_timings.get("first_content_wait_ms")),
        "post_content_elapsed_ms": _int_or_none(runtime_phase_timings.get("post_content_elapsed_ms")),
        "completed_elapsed_ms": completed_ms,
        "total_elapsed_ms": _int_or_none(runtime_phase_timings.get("total_elapsed_ms")),
    }
    if (
        diagnostics.get("model_start_wait_ms") is None
        and runtime_assembly_ms is not None
        and first_model_ms is not None
        and first_model_ms >= runtime_assembly_ms
    ):
        diagnostics["model_start_wait_ms"] = first_model_ms - runtime_assembly_ms
    if first_model_ms is not None and first_content_ms is not None and first_content_ms >= first_model_ms:
        if diagnostics.get("first_content_wait_ms") is None:
            diagnostics["first_content_wait_ms"] = first_content_ms - first_model_ms
    if completed_ms is not None and first_content_ms is not None and completed_ms >= first_content_ms:
        if diagnostics.get("post_content_elapsed_ms") is None:
            diagnostics["post_content_elapsed_ms"] = completed_ms - first_content_ms
    if slowest_mark is not None:
        phase = str(slowest_mark.get("phase") or "")
        slowest_duration = _int_or_none(slowest_mark.get("duration_since_previous_ms"))
        if (
            diagnostics.get("first_content_wait_ms") is None
            and _runtime_phase_category(phase) == "provider_first_content_wait"
            and slowest_duration is not None
        ):
            diagnostics["first_content_wait_ms"] = slowest_duration
        diagnostics.update(
            {
                "slowest_phase": phase or None,
                "slowest_phase_label": str(slowest_mark.get("label") or phase or ""),
                "slowest_phase_duration_ms": slowest_duration,
                "slowest_phase_elapsed_ms": _int_or_none(slowest_mark.get("elapsed_ms")),
                "slowest_phase_category": _runtime_phase_category(phase),
            }
        )
    return {key: value for key, value in diagnostics.items() if value is not None and value != ""}


def _slowest_runtime_phase_mark(marks: list[dict[str, object]]) -> dict[str, object] | None:
    candidates: list[tuple[int, dict[str, object]]] = []
    for mark in marks:
        duration = _int_or_none(mark.get("duration_since_previous_ms"))
        if duration is None:
            continue
        candidates.append((duration, mark))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _phase_elapsed_ms(marks: list[dict[str, object]], *phases: str) -> int | None:
    wanted = set(phases)
    for mark in reversed(marks):
        if str(mark.get("phase") or "") not in wanted:
            continue
        value = _int_or_none(mark.get("elapsed_ms"))
        if value is not None:
            return value
    return None


def _runtime_phase_category(phase: str) -> str:
    if phase in {
        "config_resolved",
        "config_reused",
        "thread_state_loaded",
        "model_route_resolved",
        "sandbox_provider_created",
        "factory_started",
        "factory_feature_set_resolved",
        "factory_memory_services_ready",
        "factory_approval_service_ready",
        "capability_assembly_started",
        "capability_assembly_completed",
        "memory_snapshot_loaded",
        "project_context_loaded",
        "runtime_path_context_built",
        "prompt_snapshot_built",
        "turn_injection_built",
        "system_prompt_composed",
        "lead_context_built",
        "middleware_chain_built",
        "chat_model_created",
        "langgraph_agent_created",
        "assembly_snapshot_built",
        "runtime_assembled",
        "tracing_started",
        "input_payload_built",
        "running_state_persisted",
        "run_started_emitted",
        "agent_stream_entered",
    }:
        return "runtime_assembly"
    if phase in {
        "first_model_event",
        "first_message_event",
        "first_update_event",
        "first_values_event",
    }:
        return "model_stream_start"
    if phase in {
        "first_reasoning_delta",
        "first_content_step_started",
        "first_content_delta",
    }:
        return "provider_first_content_wait"
    if phase in {
        "agent_stream_completed",
        "agent_state_merged",
        "terminal_events_finalized",
        "final_state_persisted",
        "run_completed_emitted",
    }:
        return "finalization"
    if phase == "run_failed":
        return "failure"
    return "other"


def _format_runtime_phase_diagnostics(diagnostics: dict[str, object]) -> str:
    fields = (
        "phase_count",
        "slowest_phase",
        "slowest_phase_duration_ms",
        "slowest_phase_category",
        "runtime_assembly_elapsed_ms",
        "model_start_wait_ms",
        "first_content_wait_ms",
        "post_content_elapsed_ms",
    )
    labels = {
        "phase_count": "phases",
        "slowest_phase": "slowest",
        "slowest_phase_duration_ms": "slowest_ms",
        "slowest_phase_category": "category",
        "runtime_assembly_elapsed_ms": "assembly_ms",
        "model_start_wait_ms": "model_start_wait_ms",
        "first_content_wait_ms": "first_content_wait_ms",
        "post_content_elapsed_ms": "post_content_ms",
    }
    return " ".join(f"{labels[field]}={diagnostics.get(field, '-')}" for field in fields if field in diagnostics)


def _prompt_cache_delta(runtime_assembly_snapshot: dict[str, object]) -> dict[str, object]:
    prompt = runtime_assembly_snapshot.get("prompt") if isinstance(runtime_assembly_snapshot, dict) else None
    if not isinstance(prompt, dict):
        return {}
    cache_delta = prompt.get("cache_delta")
    return cache_delta if isinstance(cache_delta, dict) else {}


def _prompt_cache_cumulative(runtime_assembly_snapshot: dict[str, object]) -> dict[str, object]:
    prompt = runtime_assembly_snapshot.get("prompt") if isinstance(runtime_assembly_snapshot, dict) else None
    if not isinstance(prompt, dict):
        return {}
    cache = prompt.get("cache")
    return cache if isinstance(cache, dict) else {}


def _format_prompt_cache_delta(cache_delta: dict[str, object]) -> str:
    fields = ("hits", "misses", "writes", "bypasses", "evictions", "size_before", "size_after")
    return " ".join(f"{field}={cache_delta.get(field, '-')}" for field in fields)


def _format_prompt_cache_cumulative(cache: dict[str, object]) -> str:
    fields = ("hits", "misses", "writes", "bypasses", "evictions", "size", "max_entries")
    return " ".join(f"{field}={cache.get(field, '-')}" for field in fields)


def _project_context_cache_status(runtime_assembly_snapshot: dict[str, object]) -> str | None:
    prompt = runtime_assembly_snapshot.get("prompt") if isinstance(runtime_assembly_snapshot, dict) else None
    if not isinstance(prompt, dict):
        return None
    status = prompt.get("project_context_cache_status")
    return str(status) if status is not None and str(status).strip() else None


def _runtime_path_cache_status(runtime_assembly_snapshot: dict[str, object]) -> str | None:
    prompt = runtime_assembly_snapshot.get("prompt") if isinstance(runtime_assembly_snapshot, dict) else None
    if not isinstance(prompt, dict):
        return None
    status = prompt.get("runtime_path_cache_status")
    return str(status) if status is not None and str(status).strip() else None


def _context_cache_diagnostics(runtime_assembly_snapshot: dict[str, object]) -> dict[str, object]:
    prompt = runtime_assembly_snapshot.get("prompt") if isinstance(runtime_assembly_snapshot, dict) else None
    if not isinstance(prompt, dict):
        return {}
    files = prompt.get("project_context_files")
    file_payloads = [item for item in files if isinstance(item, dict)] if isinstance(files, list) else []
    project_file_count = _int_or_none(prompt.get("project_context_file_count"))
    if project_file_count is None:
        project_file_count = len(file_payloads)
    project_truncated_files = _int_or_none(prompt.get("project_context_truncated_file_count"))
    if project_truncated_files is None:
        project_truncated_files = sum(1 for item in file_payloads if bool(item.get("truncated")))
    diagnostics = {
        "project_status": _project_context_cache_status(runtime_assembly_snapshot),
        "project_files": project_file_count,
        "project_truncated_files": project_truncated_files,
        "project_total_chars": _int_or_none(prompt.get("project_context_total_chars")),
        "runtime_status": _runtime_path_cache_status(runtime_assembly_snapshot),
        "runtime_roots": _int_or_none(prompt.get("runtime_path_root_count")),
        "runtime_host_bridges": _int_or_none(prompt.get("runtime_path_host_bridge_count")),
    }
    return {key: value for key, value in diagnostics.items() if value is not None}


def _format_context_cache_diagnostics(diagnostics: dict[str, object]) -> str:
    fields = (
        "project_status",
        "project_files",
        "project_truncated_files",
        "project_total_chars",
        "runtime_status",
        "runtime_roots",
        "runtime_host_bridges",
    )
    labels = {
        "project_status": "project",
        "project_files": "files",
        "project_truncated_files": "truncated",
        "project_total_chars": "chars",
        "runtime_status": "runtime_paths",
        "runtime_roots": "roots",
        "runtime_host_bridges": "host_bridges",
    }
    return " ".join(f"{labels[field]}={diagnostics.get(field, '-')}" for field in fields if field in diagnostics)


def _prompt_section_tokens(runtime_assembly_snapshot: dict[str, object]) -> dict[str, object]:
    prompt = runtime_assembly_snapshot.get("prompt") if isinstance(runtime_assembly_snapshot, dict) else None
    if not isinstance(prompt, dict):
        return {}
    payload = {
        "stable_total": _int_or_none(prompt.get("stable_prompt_tokens")),
        "volatile_total": _int_or_none(prompt.get("volatile_prompt_tokens")),
        "stable_sections": prompt.get("stable_section_tokens") if isinstance(prompt.get("stable_section_tokens"), dict) else {},
        "volatile_sections": prompt.get("volatile_section_tokens") if isinstance(prompt.get("volatile_section_tokens"), dict) else {},
    }
    if not payload["stable_total"] and not payload["volatile_total"]:
        return {}
    return payload


def _format_prompt_section_tokens(tokens: dict[str, object]) -> str:
    stable_total = tokens.get("stable_total")
    volatile_total = tokens.get("volatile_total")
    stable_sections = tokens.get("stable_sections") if isinstance(tokens.get("stable_sections"), dict) else {}
    volatile_sections = tokens.get("volatile_sections") if isinstance(tokens.get("volatile_sections"), dict) else {}
    parts = [
        f"stable={stable_total if stable_total is not None else '-'}",
        f"volatile={volatile_total if volatile_total is not None else '-'}",
    ]
    stable_top = _top_token_sections(stable_sections)
    volatile_top = _top_token_sections(volatile_sections)
    if stable_top:
        parts.append(f"stable_top={stable_top}")
    if volatile_top:
        parts.append(f"volatile_top={volatile_top}")
    return " ".join(parts)


def _top_token_sections(sections: dict[str, object], *, limit: int = 3) -> str:
    pairs: list[tuple[str, int]] = []
    for name, value in sections.items():
        count = _int_or_none(value)
        if count is None:
            continue
        pairs.append((str(name), count))
    pairs.sort(key=lambda item: item[1], reverse=True)
    return ",".join(f"{name}:{count}" for name, count in pairs[:limit])


def _capability_assembly_diagnostics(runtime_assembly_snapshot: dict[str, object]) -> dict[str, object]:
    capabilities = runtime_assembly_snapshot.get("capabilities") if isinstance(runtime_assembly_snapshot, dict) else None
    if not isinstance(capabilities, dict):
        return {}
    diagnostics = capabilities.get("assembly_diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else {}


def _format_capability_diagnostics(diagnostics: dict[str, object]) -> str:
    fields = (
        "visible_tool_count",
        "deferred_tool_count",
        "visible_schema_tokens",
        "visible_schema_token_budget",
        "schema_compacted_tool_count",
        "schema_deferred_tool_count",
        "action_prefilter_deferred_tool_count",
        "slowest_assembly_stage",
        "slowest_assembly_stage_duration_ms",
        "skills_discovery_cache_hit",
        "skills_discovery_manifest_count",
        "skills_discovery_enabled_count",
        "slowest_skills_discovery_stage",
        "slowest_skills_discovery_stage_duration_ms",
    )
    labels = {
        "visible_tool_count": "visible",
        "deferred_tool_count": "deferred",
        "visible_schema_tokens": "schema_visible",
        "visible_schema_token_budget": "schema_budget",
        "schema_compacted_tool_count": "schema_compacted",
        "schema_deferred_tool_count": "schema_deferred",
        "action_prefilter_deferred_tool_count": "action_deferred",
        "slowest_assembly_stage": "slowest_stage",
        "slowest_assembly_stage_duration_ms": "slowest_stage_ms",
        "skills_discovery_cache_hit": "skills_cache_hit",
        "skills_discovery_manifest_count": "skills_manifests",
        "skills_discovery_enabled_count": "skills_enabled",
        "slowest_skills_discovery_stage": "skills_slowest",
        "slowest_skills_discovery_stage_duration_ms": "skills_slowest_ms",
    }
    parts = [f"{labels[field]}={diagnostics.get(field, '-')}" for field in fields]
    skills_stages = diagnostics.get("skills_discovery_stage_durations_ms")
    if isinstance(skills_stages, dict) and skills_stages:
        visible_stage_durations = {
            stage: duration
            for stage, duration in skills_stages.items()
            if stage != "total"
        }
        if visible_stage_durations:
            parts.append(f"skills_stages={_format_count_map(visible_stage_durations)}")
    return " ".join(parts)


def _memory_injection_diagnostics(runtime_assembly_snapshot: dict[str, object]) -> dict[str, object]:
    if not isinstance(runtime_assembly_snapshot, dict):
        return {}
    diagnostics = runtime_assembly_snapshot.get("memory_injection_diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else {}


def _format_memory_injection_diagnostics(diagnostics: dict[str, object]) -> str:
    fields = (
        "source",
        "status",
        "curated_match_count",
        "archive_hit_count",
        "evidence_count",
        "provider_note_count",
        "rendered_tokens",
        "token_budget",
        "truncated",
    )
    labels = {
        "curated_match_count": "curated",
        "archive_hit_count": "archive",
        "evidence_count": "evidence",
        "provider_note_count": "provider_notes",
        "rendered_tokens": "tokens",
        "token_budget": "budget",
    }
    parts = [f"{labels.get(field, field)}={diagnostics.get(field, '-')}" for field in fields]
    store_counts = diagnostics.get("store_counts")
    if isinstance(store_counts, dict) and store_counts:
        parts.append(f"stores={_format_count_map(store_counts)}")
    source_kind_counts = diagnostics.get("source_kind_counts")
    if isinstance(source_kind_counts, dict) and source_kind_counts:
        parts.append(f"sources={_format_count_map(source_kind_counts)}")
    return " ".join(parts)


def _compaction_diagnostics(
    runtime_assembly_snapshot: dict[str, object],
    context_window_usage: dict[str, object],
) -> dict[str, object]:
    if isinstance(runtime_assembly_snapshot, dict):
        diagnostics = runtime_assembly_snapshot.get("compaction_diagnostics")
        if isinstance(diagnostics, dict) and diagnostics:
            return diagnostics
    if isinstance(context_window_usage, dict):
        diagnostics = context_window_usage.get("compaction_diagnostics")
        if isinstance(diagnostics, dict) and diagnostics:
            return diagnostics
    return {}


def _format_compaction_diagnostics(diagnostics: dict[str, object]) -> str:
    fields = (
        "compaction_level",
        "compaction_level_label",
        "compaction_reason",
        "summary_source",
        "summary_model",
        "archived_message_count",
        "tool_call_count",
        "tool_result_count",
        "image_block_count",
        "truncated_message_count",
        "pruned_tool_result_count",
        "serialized_tokens",
        "summary_prompt_tokens",
        "compaction_input_tokens",
        "compaction_summary_tokens",
        "keep_recent_turns",
    )
    labels = {
        "compaction_level": "level",
        "compaction_level_label": "label",
        "compaction_reason": "reason",
        "summary_source": "source",
        "summary_model": "model",
        "archived_message_count": "archived",
        "tool_call_count": "tool_calls",
        "tool_result_count": "tool_results",
        "image_block_count": "images",
        "truncated_message_count": "truncated",
        "pruned_tool_result_count": "pruned_tools",
        "serialized_tokens": "serialized_tokens",
        "summary_prompt_tokens": "summary_prompt_tokens",
        "compaction_input_tokens": "input_tokens",
        "compaction_summary_tokens": "summary_tokens",
        "keep_recent_turns": "keep_recent",
    }
    parts = [f"{labels.get(field, field)}={diagnostics.get(field, '-')}" for field in fields if field in diagnostics]
    return " ".join(parts)


def _format_count_map(values: dict[str, object], *, limit: int = 4) -> str:
    pairs: list[tuple[str, int]] = []
    for key, value in values.items():
        count = _int_or_none(value)
        if count is None:
            continue
        pairs.append((str(key), count))
    pairs.sort(key=lambda item: item[1], reverse=True)
    return ",".join(f"{key}:{count}" for key, count in pairs[:limit])


def _runtime_assembly_changed_paths(runtime_assembly_diff: dict[str, object]) -> list[str]:
    if not isinstance(runtime_assembly_diff, dict) or runtime_assembly_diff.get("changed") is not True:
        return []
    paths = runtime_assembly_diff.get("changed_paths")
    if not isinstance(paths, list):
        return []
    return [str(item) for item in paths if str(item).strip()]


def _markdown_block(value: str) -> str:
    return "```text\n" + value.replace("```", "'''") + "\n```"


def _format_optional_number(value) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)
