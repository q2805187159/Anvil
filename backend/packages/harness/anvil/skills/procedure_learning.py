from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anvil.agents import RecentToolActivity, ThreadState
    from anvil.config import EffectiveConfig
else:  # keep runtime annotations resolvable for dataclasses under postponed annotations
    RecentToolActivity = object
    ThreadState = object
    EffectiveConfig = object


CODING_DISCOVERY_TOOL_NAMES = frozenset(
    {
        "code_map",
        "code_focus",
        "code_symbols",
        "code_symbol_search",
        "code_references",
        "code_definition",
        "code_semantic_index",
        "code_file_summary",
        "code_impact",
    }
)
CODING_AUDIT_TOOL_NAMES = frozenset(
    {
        "code_health",
        "code_security_scan",
        "code_pattern_scan",
        "code_doc_graph",
    }
)
PROCEDURE_NOISE_TOOL_NAMES = frozenset(
    {
        "capability_search",
        "tool_catalog",
        "tool_view",
        "toolset_catalog",
        "toolset_view",
        "write_todos",
    }
)


@dataclass(frozen=True)
class ProcedureLearningResult:
    accepted: bool
    reason: str | None = None
    procedure_id: str | None = None
    payload: dict[str, object] = field(default_factory=dict)
    curator_result: dict[str, object] | None = None


@dataclass(frozen=True)
class ProcedureLearningEvidence:
    accepted: bool
    reason: str
    tool_steps: tuple[dict[str, object], ...] = ()
    tool_activities: tuple[RecentToolActivity, ...] = ()
    evidence_steps: tuple[dict[str, object], ...] = ()
    tool_names: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    signature: str | None = None
    high_value: bool = False
    explicit_learning_request: bool = False
    completion_signal: bool = False
    verification_signal: bool = False
    failure_recovery_signal: bool = False
    loaded_skill_ids: tuple[str, ...] = ()


class ProcedureLearningService:
    """Learn reusable procedure candidates from high-value workflow evidence."""

    MIN_PROCEDURE_TOOL_EVIDENCE = 5
    MIN_REUSABLE_TOOL_EVIDENCE = 2

    HIDDEN_TOOL_NAMES = frozenset(
        {
            "delegate_batch",
            "delegate_cancel",
            "delegate_status",
            "delegated_task",
            "memory",
            "memory_trace",
            "session_search",
            "subagent",
        }
    )
    HIDDEN_TOOL_CAPABILITY_GROUPS = frozenset({"memory"})
    EXPLICIT_LEARNING_RE = re.compile(
        r"(沉淀|整理|保存|记录).{0,12}(技能|skill|流程|procedure|workflow)|"
        r"(以后|下次).{0,12}(这样|这么做|按这个流程)|"
        r"(make|save|turn).{0,16}(skill|procedure|workflow)",
        re.IGNORECASE,
    )
    COMPLETION_RE = re.compile(
        r"(完成|已完成|搞定|解决|修复|验证通过|测试通过|部署成功|done|fixed|resolved|verified|passed)",
        re.IGNORECASE,
    )
    FAILURE_RECOVERY_RE = re.compile(
        r"(失败|报错|错误|重试|retry|failed|error).{0,80}(完成|修复|解决|通过|fixed|resolved|passed|verified)|"
        r"(完成|修复|解决|通过|fixed|resolved|passed|verified).{0,80}(失败|报错|错误|重试|retry|failed|error)",
        re.IGNORECASE,
    )

    def learn_from_thread(
        self,
        *,
        state: ThreadState,
        config: EffectiveConfig,
        skills_service,
        source: str = "runtime_success",
        run_id: str | None = None,
        skill_ids: tuple[str, ...] = (),
    ) -> ProcedureLearningResult:
        if str(getattr(state.lifecycle.status, "value", state.lifecycle.status)) != "completed":
            return ProcedureLearningResult(accepted=False, reason="thread_not_completed")
        if skills_service is None:
            return ProcedureLearningResult(accepted=False, reason="skills_service_unavailable")
        if not config.skills_config.enabled or not config.skills_config.curator.automation_enabled:
            return ProcedureLearningResult(accepted=False, reason="skills_curator_disabled")

        normalized_skill_ids = tuple(dict.fromkeys(str(item).strip() for item in skill_ids if str(item).strip()))
        evidence = self.evaluate_thread(
            state=state,
            run_id=run_id,
            skill_ids=normalized_skill_ids,
        )
        if not evidence.accepted:
            return ProcedureLearningResult(accepted=False, reason=evidence.reason)

        source_ref = f"thread:{state.identity.thread_id}/run:{run_id or state.identity.run_id or 'unknown'}"
        tags = [
            "runtime-learned",
            *(f"skill:{skill_id}" for skill_id in normalized_skill_ids[:6]),
        ]
        if evidence.explicit_learning_request:
            tags.append("explicit-request")
        if evidence.failure_recovery_signal:
            tags.append("failure-recovery")
        payload: dict[str, object] = {
            "action": "learn_procedure",
            "title": self.procedure_title(tool_names=list(evidence.tool_names), skill_ids=normalized_skill_ids),
            "trigger": self.procedure_trigger(tool_names=list(evidence.tool_names), skill_ids=normalized_skill_ids),
            "steps": list(evidence.steps),
            "expected_outcome": self.latest_assistant_content(state) or "A completed task with evidence from the executed workflow.",
            "rationale": self._procedure_rationale(evidence),
            "tags": tags,
            "allowed_tools": list(evidence.tool_names),
            "evidence_refs": [
                source_ref,
                *(str(step.get("step_id")) for step in evidence.evidence_steps[:8] if step.get("step_id") is not None),
                *(f"signature:{evidence.signature}" if evidence.signature else ()),
            ],
            "source_ref": source_ref,
            "outcome": "success",
            "feedback_source": source,
            "confidence": self._procedure_confidence(evidence),
        }
        try:
            result = skills_service.manage_curator(config=config, **payload)
        except Exception as exc:  # pragma: no cover - learning must stay fail-open
            return ProcedureLearningResult(accepted=False, reason=f"{type(exc).__name__}: {exc}", payload=payload)
        return ProcedureLearningResult(
            accepted=bool(result.get("accepted")),
            reason=str(result.get("error") or result.get("reason") or "") or None,
            procedure_id=str(result.get("procedure_id") or "") or None,
            payload=payload,
            curator_result=result,
        )

    def evaluate_thread(
        self,
        *,
        state: ThreadState,
        run_id: str | None = None,
        skill_ids: tuple[str, ...] = (),
    ) -> ProcedureLearningEvidence:
        normalized_skill_ids = tuple(dict.fromkeys(str(item).strip() for item in skill_ids if str(item).strip()))
        tool_steps = tuple(self.procedure_tool_steps(state, run_id=run_id))
        tool_activities = tuple(self.procedure_tool_activities(state, run_id=run_id))
        evidence_steps = tuple(tool_steps if tool_steps else self.procedure_steps_from_activities(list(tool_activities)))
        tool_names = tuple(
            name
            for name in dict.fromkeys(
                [
                    *(str(step.get("tool_name") or "").strip() for step in tool_steps),
                    *(str(activity.name or "").strip() for activity in tool_activities),
                ]
            )
            if name
        )
        if not tool_names:
            return ProcedureLearningEvidence(accepted=False, reason="missing_tools", loaded_skill_ids=normalized_skill_ids)
        steps = tuple(self.procedure_steps_from_tool_steps(list(evidence_steps)))
        if len(steps) < 2:
            return ProcedureLearningEvidence(
                accepted=False,
                reason="insufficient_steps",
                tool_steps=tool_steps,
                tool_activities=tool_activities,
                evidence_steps=evidence_steps,
                tool_names=tool_names,
                loaded_skill_ids=normalized_skill_ids,
            )
        latest_assistant = self.latest_assistant_content(state) or ""
        latest_user = self.latest_user_content(state) or ""
        combined_text = f"{latest_user}\n{latest_assistant}"
        explicit_learning_request = bool(self.EXPLICIT_LEARNING_RE.search(combined_text))
        completion_signal = bool(self.COMPLETION_RE.search(latest_assistant))
        verification_signal = self._has_verification_signal(tool_names=tool_names, steps=steps, text=combined_text)
        failure_recovery_signal = bool(self.FAILURE_RECOVERY_RE.search(combined_text)) or self._has_failed_then_successful_tool_activity(state, run_id=run_id)
        enough_tool_evidence = len(evidence_steps) >= self.MIN_PROCEDURE_TOOL_EVIDENCE
        reusable_workflow = len(tool_names) >= self.MIN_REUSABLE_TOOL_EVIDENCE and len(evidence_steps) >= self.MIN_REUSABLE_TOOL_EVIDENCE
        high_value = (
            explicit_learning_request
            or enough_tool_evidence
            or failure_recovery_signal
            or (completion_signal and verification_signal and reusable_workflow)
            or (normalized_skill_ids and completion_signal and verification_signal and reusable_workflow)
        )
        if not high_value:
            return ProcedureLearningEvidence(
                accepted=False,
                reason="low_value_episode",
                tool_steps=tool_steps,
                tool_activities=tool_activities,
                evidence_steps=evidence_steps,
                tool_names=tool_names,
                steps=steps,
                signature=self.procedure_signature(tool_names=tool_names, steps=steps),
                high_value=False,
                explicit_learning_request=explicit_learning_request,
                completion_signal=completion_signal,
                verification_signal=verification_signal,
                failure_recovery_signal=failure_recovery_signal,
                loaded_skill_ids=normalized_skill_ids,
            )
        return ProcedureLearningEvidence(
            accepted=True,
            reason="accepted",
            tool_steps=tool_steps,
            tool_activities=tool_activities,
            evidence_steps=evidence_steps,
            tool_names=tool_names,
            steps=steps,
            signature=self.procedure_signature(tool_names=tool_names, steps=steps),
            high_value=True,
            explicit_learning_request=explicit_learning_request,
            completion_signal=completion_signal,
            verification_signal=verification_signal,
            failure_recovery_signal=failure_recovery_signal,
            loaded_skill_ids=normalized_skill_ids,
        )

    def procedure_tool_steps(self, state: ThreadState, *, run_id: str | None = None) -> list[dict[str, object]]:
        steps: list[dict[str, object]] = []
        for step in state.conversation.steps:
            if not isinstance(step, dict):
                continue
            if run_id is not None and not self._step_belongs_to_run(step, run_id):
                continue
            if step.get("type") != "call":
                continue
            if str(step.get("visibility") or "chat") != "chat":
                continue
            if str(step.get("status") or "") not in {"success", "completed", "complete"}:
                continue
            name = str(step.get("tool_name") or "").strip()
            if not name or name in PROCEDURE_NOISE_TOOL_NAMES:
                continue
            if name in self.HIDDEN_TOOL_NAMES:
                continue
            steps.append(step)
        return steps

    def procedure_tool_activities(self, state: ThreadState, *, run_id: str | None = None) -> list[RecentToolActivity]:
        activities: list[RecentToolActivity] = []
        for activity in state.execution.recent_tool_activity:
            if run_id is not None and not self._activity_belongs_to_run(activity, run_id):
                continue
            if (activity.status or "").strip() not in {"completed", "success"}:
                continue
            name = (activity.name or "").strip()
            if not name or name in PROCEDURE_NOISE_TOOL_NAMES:
                continue
            capability_group = (activity.capability_group or "").strip()
            if name in self.HIDDEN_TOOL_NAMES:
                continue
            if capability_group in self.HIDDEN_TOOL_CAPABILITY_GROUPS or capability_group == "delegation_internal":
                continue
            activities.append(activity)
        return activities

    def procedure_steps_from_activities(self, activities: list[RecentToolActivity]) -> list[dict[str, object]]:
        steps: list[dict[str, object]] = []
        for index, activity in enumerate(reversed(activities)):
            name = (activity.name or "").strip()
            if not name:
                continue
            steps.append(
                {
                    "step_id": activity.tool_call_id or f"activity:{index}:{name}",
                    "type": "call",
                    "tool_name": name,
                    "status": "success",
                    "visibility": "chat",
                }
            )
        return steps

    def procedure_steps_from_tool_steps(self, tool_steps: list[dict[str, object]]) -> list[str]:
        steps: list[str] = []
        for step in tool_steps[:8]:
            tool_name = str(step.get("tool_name") or "").strip()
            procedure_step = self.procedure_step_for_tool(tool_name)
            if procedure_step and procedure_step not in steps:
                steps.append(procedure_step)
        if steps:
            final_step = "Summarize the completed work with the evidence, outputs, and any remaining risk."
            if final_step not in steps:
                steps.append(final_step)
        return steps

    def procedure_step_for_tool(self, tool_name: str) -> str:
        if tool_name in {"list_dir", "search_files", "glob_files", "grep_files", "file_info", "read_file"}:
            return "Narrow the target files first, then read only the relevant bounded context."
        if tool_name in CODING_DISCOVERY_TOOL_NAMES:
            return "Use focused code analysis before broad file reads or edits."
        if tool_name in CODING_AUDIT_TOOL_NAMES:
            return "Inspect the specialized analysis result and keep follow-up reads scoped to the findings."
        if tool_name in {"patch_file", "write_file", "move_path", "delete_path", "make_dir"}:
            return "Apply the smallest bounded filesystem change that satisfies the task."
        if tool_name in {"run_command", "process", "bash", "js_repl"}:
            return "Run a targeted verification command and use its result to decide the next step."
        if tool_name in {"web_search", "web_fetch", "web_extract", "web_crawl", "image_search"}:
            return "Collect bounded external evidence and preserve the useful source details."
        if tool_name.startswith("browser_"):
            return "Use browser state or screenshots to verify the interactive result."
        if tool_name in {"extract_document", "export_document"}:
            return "Separate content planning from document export and verify the generated artifact."
        if tool_name:
            return f"Use {tool_name} with scoped inputs and inspect the result before proceeding."
        return ""

    def procedure_title(self, *, tool_names: list[str], skill_ids: tuple[str, ...]) -> str:
        if skill_ids:
            return f"Runtime workflow for {', '.join(skill_ids[:2])}"
        return f"Runtime workflow: {' -> '.join(tool_names[:4])}"

    def procedure_trigger(self, *, tool_names: list[str], skill_ids: tuple[str, ...]) -> str:
        tool_sequence = " -> ".join(tool_names[:6])
        if skill_ids:
            return f"When a similar task uses {', '.join(skill_ids[:3])} and follows the {tool_sequence} workflow."
        return f"When a task needs the {tool_sequence} workflow with verifiable outputs."

    def procedure_signature(self, *, tool_names: tuple[str, ...], steps: tuple[str, ...]) -> str:
        normalized = "|".join(
            [
                ",".join(tool_names[:8]),
                *steps[:8],
            ]
        ).lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def latest_user_content(self, state: ThreadState) -> str | None:
        for message in reversed(state.conversation.messages):
            if message.get("role") in {"user", "human"}:
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
        return None

    def latest_assistant_content(self, state: ThreadState) -> str | None:
        for step in reversed(state.conversation.steps):
            if step.get("type") == "content":
                payload = step.get("payload")
                if isinstance(payload, str) and payload.strip():
                    return payload
        for message in reversed(state.conversation.messages):
            if message.get("role") in {"assistant", "ai"}:
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
        return None

    def _step_belongs_to_run(self, step: dict[str, object], run_id: str) -> bool:
        metadata = step.get("metadata")
        if isinstance(metadata, dict):
            metadata_run_id = metadata.get("run_id")
            if metadata_run_id is not None:
                return str(metadata_run_id) == run_id
        return True

    def _activity_belongs_to_run(self, activity: RecentToolActivity, run_id: str) -> bool:
        activity_run_id = getattr(activity, "run_id", None)
        if activity_run_id is not None:
            return str(activity_run_id) == run_id
        return True

    def _has_verification_signal(self, *, tool_names: tuple[str, ...], steps: tuple[str, ...], text: str) -> bool:
        verification_tools = {
            "run_command",
            "process",
            "bash",
            "js_repl",
            "code_health",
            "code_security_scan",
            "code_pattern_scan",
            "browser_snapshot",
            "browser_screenshot",
            "browser_console",
        }
        if any(name in verification_tools or name.startswith("browser_") for name in tool_names):
            return True
        combined_steps = "\n".join(steps)
        return bool(self.COMPLETION_RE.search(text) or self.COMPLETION_RE.search(combined_steps))

    def _has_failed_then_successful_tool_activity(self, state: ThreadState, *, run_id: str | None) -> bool:
        seen_error = False
        for step in state.conversation.steps:
            if not isinstance(step, dict):
                continue
            if run_id is not None and not self._step_belongs_to_run(step, run_id):
                continue
            if step.get("type") != "call":
                continue
            status = str(step.get("status") or "").strip().lower()
            if status in {"error", "failed", "failure"}:
                seen_error = True
            elif seen_error and status in {"success", "completed", "complete"}:
                return True
        return False

    def _procedure_confidence(self, evidence: ProcedureLearningEvidence) -> float:
        score = 0.58
        if evidence.explicit_learning_request:
            score += 0.12
        if evidence.failure_recovery_signal:
            score += 0.1
        if evidence.verification_signal:
            score += 0.08
        if len(evidence.evidence_steps) >= self.MIN_PROCEDURE_TOOL_EVIDENCE:
            score += 0.08
        if evidence.loaded_skill_ids:
            score += 0.04
        return round(min(score, 0.9), 4)

    def _procedure_rationale(self, evidence: ProcedureLearningEvidence) -> str:
        reasons: list[str] = []
        if evidence.explicit_learning_request:
            reasons.append("explicit user request")
        if evidence.failure_recovery_signal:
            reasons.append("failure recovery")
        if evidence.verification_signal:
            reasons.append("verification evidence")
        if len(evidence.evidence_steps) >= self.MIN_PROCEDURE_TOOL_EVIDENCE:
            reasons.append("substantial visible tool workflow")
        if evidence.loaded_skill_ids:
            reasons.append("loaded skill workflow")
        suffix = ", ".join(reasons) if reasons else "workflow evidence"
        return f"Reusable procedure candidate captured from {suffix}."
