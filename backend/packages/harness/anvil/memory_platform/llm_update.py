from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass, replace
import re
from typing import Any

from anvil.agents.model_factory import create_chat_model
from anvil.config import EffectiveConfig, MemoryPlatformUpdaterConfig
from anvil.config.model_routing import ModelRouteRequest, RequiredModelCapabilities, resolve_model_route
from anvil.config.service import resolve_internal_task_model_config
from anvil.runtime.token_budget import TokenBudgetService

from .candidates import MemoryCandidate, MemoryCandidateExtractor
from .extraction_policy import has_durable_outcome_signal, memory_extraction_decision, semantic_memory_key
from .scrubber import MemorySecretScrubber


@dataclass(frozen=True)
class StructuredMemoryUpdate:
    candidates: tuple[MemoryCandidate, ...] = ()
    user_summary: str | None = None
    history_summary: str | None = None
    user_summary_sections: dict[str, dict[str, str]] | None = None
    history_summary_sections: dict[str, dict[str, str]] | None = None
    facts_to_remove: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    error: str | None = None


class LLMMemoryUpdateService:
    def __init__(
        self,
        *,
        config: MemoryPlatformUpdaterConfig,
        fallback_extractor: MemoryCandidateExtractor,
        effective_config: EffectiveConfig | None = None,
        token_budget: TokenBudgetService | None = None,
    ) -> None:
        self.config = config
        self.fallback_extractor = fallback_extractor
        self.effective_config = effective_config
        self.token_budget = token_budget or TokenBudgetService()
        self.scrubber = MemorySecretScrubber()

    def extract_turn(
        self,
        *,
        user_content: str,
        assistant_content: str,
        status: str = "completed",
        evidence_ref: str | None = None,
        existing_memory_context: str | None = None,
        signals: dict[str, Any] | None = None,
    ) -> StructuredMemoryUpdate:
        scrubbed_user = self.scrubber.scrub(user_content)
        scrubbed_assistant = self.scrubber.scrub(assistant_content)
        redacted_rules = tuple(dict.fromkeys((*scrubbed_user.rule_ids, *scrubbed_assistant.rule_ids)))
        safe_user_content = scrubbed_user.text
        safe_assistant_content = scrubbed_assistant.text
        safe_existing_context = self.scrubber.scrub(existing_memory_context or "").text
        fallback = self._fallback_update(
            user_content=safe_user_content,
            assistant_content=safe_assistant_content,
            evidence_ref=evidence_ref,
            redacted_rules=redacted_rules,
            signals=signals,
        )
        if not self.config.enabled:
            return fallback
        model_name = self._resolve_model_name()
        if self.effective_config is None or not model_name:
            return fallback_with_skip(fallback, "llm_updater_unavailable")

        model_config = resolve_internal_task_model_config(self.effective_config, model_name)
        if model_config is None:
            model_config = self.effective_config.models.get(model_name)
        if model_config is None:
            return fallback_with_skip(fallback, f"llm_model_not_configured:{model_name}")

        prompt = self._build_prompt(
            user_content=safe_user_content,
            assistant_content=safe_assistant_content,
            status=status,
            evidence_ref=evidence_ref,
            existing_memory_context=safe_existing_context,
            signals=signals,
        )
        try:
            model = create_chat_model(model_config.model_copy(update={"max_tokens": self.config.max_output_tokens}))
            response = _invoke_model_with_timeout(model, prompt, timeout_seconds=self.config.timeout_seconds)
            content = getattr(response, "content", "")
            payload = _parse_json_content(content)
            structured = self._structured_update_from_payload(
                payload,
                evidence_ref=evidence_ref,
                redacted_rules=redacted_rules,
                signals=signals,
            )
            if not structured.candidates and not structured.user_summary and not structured.history_summary and not structured.facts_to_remove:
                return fallback_with_skip(fallback, "llm_updater_empty")
            return structured
        except Exception as exc:
            if self.config.fail_open:
                return fallback_with_skip(fallback, f"llm_updater_failed:{exc.__class__.__name__}")
            raise

    def _resolve_model_name(self) -> str | None:
        if self.config.model_name:
            return self.config.model_name
        if self.effective_config is None:
            return None
        try:
            route = resolve_model_route(
                self.effective_config,
                ModelRouteRequest(
                    subsystem="memory_updater",
                    required_capabilities=RequiredModelCapabilities(tool_calling=False),
                ),
            )
            return route.model_name
        except Exception:
            return self.effective_config.default_model

    def _fallback_update(
        self,
        *,
        user_content: str,
        assistant_content: str,
        evidence_ref: str | None,
        redacted_rules: tuple[str, ...] = (),
        signals: dict[str, Any] | None = None,
    ) -> StructuredMemoryUpdate:
        candidates = self._filter_and_dedupe_candidates(
            self.fallback_extractor.extract_turn(
                user_content=user_content,
                assistant_content=assistant_content,
                evidence_ref=evidence_ref,
            ),
            signals=signals,
        )
        return StructuredMemoryUpdate(
            candidates=tuple(
                replace(candidate, redacted_rules=redacted_rules)
                for candidate in candidates
            )
        )

    def _build_prompt(
        self,
        *,
        user_content: str,
        assistant_content: str,
        status: str,
        evidence_ref: str | None,
        existing_memory_context: str | None,
        signals: dict[str, Any] | None,
    ) -> str:
        transcript = (
            f"Status: {status}\n"
            f"Evidence ref: {evidence_ref or '(none)'}\n\n"
            f"User:\n{user_content.strip()}\n\n"
            f"Assistant:\n{assistant_content.strip()}"
        )
        memory_context = self.token_budget.truncate_text(
            existing_memory_context or "(none)",
            max_tokens=max(self.config.max_input_tokens // 3, 1),
        )
        transcript = self.token_budget.truncate_text(
            transcript,
            max_tokens=max(self.config.max_input_tokens - self.token_budget.count_text(memory_context), 1),
        )
        signal_payload = json.dumps(signals or {}, ensure_ascii=False, sort_keys=True)
        return (
            "You are Anvil's memory updater. Analyze one completed assistant turn and return ONLY valid JSON.\n"
            "Persist only durable, future-useful memory. Return empty arrays when the turn only contains ordinary task progress.\n"
            "Do not save title generation, thinking, tool chatter, upload bookkeeping, commands run, file creation reports, temporary syntax errors, screenshots, or one-off implementation narration.\n"
            "Do not save one-turn user instructions such as exact-output requests, 'do not use tools', file/count/search commands, screenshots, or current-thread coordination as preferences.\n"
            "Save user-layer memory only for explicit durable user preferences, corrections, recurring personal/work context, or stable interaction rules.\n"
            "A durable preference normally contains signals like 'always', 'never', 'default', 'from now on', 'remember that', '以后', '默认', or a clear recurring style preference.\n"
            "Save workspace-layer memory only for stable project constraints, environment facts, reusable workflows, durable root causes, and verified resolutions that will matter in later sessions.\n"
            "Resolved outcomes must be reusable and evidenced by tests, deployment, root-cause analysis, or a durable project change; do not store generic 'I fixed/created/ran X' summaries.\n"
            "Before adding a fact, compare against existing_memory and prefer factsToRemove/supersedes over creating near-duplicates under a different category.\n"
            "Memory is not skills. Reusable procedures may be described as workspace workflow only when verified and recurring; skill promotion is handled later by Curator.\n"
            "When existing_memory already contains the same fact, return nothing unless replacing or removing a stale fact by id.\n"
            "Explicitly analyze errors, retries, user corrections, project constraints, and solved outcomes, but keep only those with future value.\n"
            "Use original language for project names and user-facing facts.\n\n"
            "Output schema:\n"
            "{\n"
            '  "user": {"workContext": {"summary": "...", "shouldUpdate": true}, "personalContext": {...}, "topOfMind": {...}},\n'
            '  "history": {"recentMonths": {"summary": "...", "shouldUpdate": true}, "earlierContext": {...}, "longTermBackground": {...}},\n'
            '  "newFacts": [{"layer": "user|workspace", "content": "...", "category": "preference|project_context|workflow|environment|goal|correction", "confidence": 0.0, "priority": 0.0, "salience": 0.0, "supersedes": []}],\n'
            '  "factsToRemove": ["memory_id_or_entry_id"],\n'
            '  "outcomes": [{"content": "...", "status": "resolved|unresolved|failed", "confidence": 0.0, "supersedes": []}],\n'
            '  "constraints": [{"content": "...", "confidence": 0.0}],\n'
            '  "corrections": [{"content": "...", "sourceError": "...", "confidence": 0.0, "layer": "user|workspace"}]\n'
            "}\n\n"
            "Rules:\n"
            f"- Only facts with confidence >= {self.config.fact_confidence_threshold:.2f} may become direct facts.\n"
            f"- Only outcomes with confidence >= {self.config.outcome_confidence_threshold:.2f} should be direct resolved outcomes.\n"
            "- Lower-confidence outcomes should be returned only when they still contain durable evidence such as tests, deployment, root cause, configuration, permission, schema, security, or migration details; otherwise omit them instead of sending them to review.\n"
            "- Prefer workspace layer for project/environment/workflow/resolution facts.\n"
            "- Prefer user layer for preferences, communication style, personal context, and explicit user corrections.\n"
            "- If a new fact replaces an old memory, include the old id in supersedes or factsToRemove.\n\n"
            f"<existing_memory>\n{memory_context}\n</existing_memory>\n\n"
            f"<signals>\n{signal_payload}\n</signals>\n\n"
            f"<conversation>\n{transcript}\n</conversation>"
        )

    def _structured_update_from_payload(
        self,
        payload: dict[str, Any],
        *,
        evidence_ref: str | None,
        redacted_rules: tuple[str, ...] = (),
        signals: dict[str, Any] | None = None,
    ) -> StructuredMemoryUpdate:
        evidence_refs = (evidence_ref,) if evidence_ref else ()
        candidates: list[MemoryCandidate] = []
        candidates.extend(self._facts_to_candidates(payload.get("newFacts"), evidence_refs=evidence_refs))
        candidates.extend(self._outcomes_to_candidates(payload.get("outcomes"), evidence_refs=evidence_refs))
        candidates.extend(self._constraints_to_candidates(payload.get("constraints"), evidence_refs=evidence_refs))
        candidates.extend(self._corrections_to_candidates(payload.get("corrections"), evidence_refs=evidence_refs))
        candidates = self._filter_and_dedupe_candidates(candidates, signals=signals)
        return StructuredMemoryUpdate(
            candidates=tuple(
                replace(candidate, redacted_rules=redacted_rules)
                for candidate in candidates
            ),
            user_summary=_extract_summary(payload.get("user")),
            history_summary=_extract_summary(payload.get("history")),
            user_summary_sections=_extract_summary_sections(
                payload.get("user"),
                allowed_keys=("workContext", "personalContext", "topOfMind"),
            ),
            history_summary_sections=_extract_summary_sections(
                payload.get("history"),
                allowed_keys=("recentMonths", "earlierContext", "longTermBackground"),
            ),
            facts_to_remove=_extract_string_tuple(payload.get("factsToRemove")),
        )

    def _filter_and_dedupe_candidates(
        self,
        candidates: tuple[MemoryCandidate, ...] | list[MemoryCandidate],
        *,
        signals: dict[str, Any] | None,
    ) -> list[MemoryCandidate]:
        durable_candidates: list[MemoryCandidate] = []
        for candidate in candidates:
            if self._is_low_value_candidate(candidate, signals=signals):
                continue
            durable_candidates.append(candidate)

        deduped: dict[str, MemoryCandidate] = {}
        for candidate in durable_candidates:
            key = _semantic_memory_key(candidate.content)
            existing = deduped.get(key)
            if existing is None or _candidate_rank(candidate) > _candidate_rank(existing):
                deduped[key] = candidate
        return sorted(
            deduped.values(),
            key=lambda item: (item.priority, item.confidence, item.salience),
            reverse=True,
        )

    def _is_low_value_candidate(self, candidate: MemoryCandidate, *, signals: dict[str, Any] | None) -> bool:
        text = candidate.content.strip()
        normalized = _semantic_memory_key(text)
        if not normalized:
            return True
        if len(normalized) < 24 and candidate.category not in {"preference", "correction"}:
            return True
        decision = memory_extraction_decision(
            content=text,
            category=candidate.category,
            layer_id=candidate.layer_id,
            confidence=candidate.confidence,
            evidence_refs=candidate.evidence_refs,
            supersedes=candidate.supersedes,
            signals=signals,
        )
        if not decision.accepted:
            if candidate.category in {"resolved_outcome", "outcome"} and decision.reason in {
                "missing_durable_outcome_signal",
                "task_or_session_noise",
            }:
                if not re.search(
                    r"\b(current session|current turn|created file|file created|edited file|ran command|calculator\.py)\b"
                    r"|当前会话|当前线程|本轮|已创建文件|文件创建成功|已编辑文件|已运行命令",
                    text,
                    re.IGNORECASE,
                ):
                    return False
            return True
        if candidate.category in {"project_context", "environment", "workflow"} and candidate.confidence < self.config.fact_confidence_threshold:
            return True
        return False

    def _facts_to_candidates(self, values: Any, *, evidence_refs: tuple[str, ...]) -> list[MemoryCandidate]:
        candidates = []
        for item in _iter_dicts(values):
            content = _clean_string(item.get("content"))
            if not content:
                continue
            category = _clean_string(item.get("category")) or "context"
            confidence = _bounded_float(item.get("confidence"), default=0.5)
            layer_id = _normalize_layer(item.get("layer"), category=category)
            candidates.append(
                MemoryCandidate(
                    layer_id=layer_id,
                    content=_prefix_for_category(content, category),
                    category=category,
                    priority=_bounded_float(item.get("priority"), default=0.74),
                    confidence=confidence,
                    salience=_bounded_float(item.get("salience"), default=0.72),
                    rationale="llm structured fact extraction",
                    evidence_refs=evidence_refs,
                    review_required=confidence < self.config.fact_confidence_threshold,
                    supersedes=_extract_string_tuple(item.get("supersedes")),
                )
            )
        return candidates

    def _outcomes_to_candidates(self, values: Any, *, evidence_refs: tuple[str, ...]) -> list[MemoryCandidate]:
        candidates = []
        for item in _iter_dicts(values):
            content = _clean_string(item.get("content"))
            if not content:
                continue
            confidence = _bounded_float(item.get("confidence"), default=0.5)
            status = (_clean_string(item.get("status")) or "resolved").lower()
            category = "resolved_outcome" if status in {"resolved", "completed", "success", "succeeded"} else "outcome"
            candidates.append(
                MemoryCandidate(
                    layer_id="workspace",
                    content=_prefix_for_category(content, category),
                    category=category,
                    priority=0.86,
                    confidence=confidence,
                    salience=0.84,
                    rationale=f"llm structured outcome extraction ({status})",
                    evidence_refs=evidence_refs,
                    review_required=confidence < self.config.outcome_confidence_threshold or category != "resolved_outcome",
                    supersedes=_extract_string_tuple(item.get("supersedes")),
                )
            )
        return candidates

    def _constraints_to_candidates(self, values: Any, *, evidence_refs: tuple[str, ...]) -> list[MemoryCandidate]:
        candidates = []
        for item in _iter_dicts(values):
            content = _clean_string(item.get("content"))
            if not content:
                continue
            confidence = _bounded_float(item.get("confidence"), default=0.5)
            candidates.append(
                MemoryCandidate(
                    layer_id="workspace",
                    content=_prefix_for_category(content, "project_constraint"),
                    category="project_constraint",
                    priority=0.82,
                    confidence=confidence,
                    salience=0.80,
                    rationale="llm structured project constraint extraction",
                    evidence_refs=evidence_refs,
                    review_required=confidence < self.config.fact_confidence_threshold,
                    supersedes=_extract_string_tuple(item.get("supersedes")),
                )
            )
        return candidates

    def _corrections_to_candidates(self, values: Any, *, evidence_refs: tuple[str, ...]) -> list[MemoryCandidate]:
        candidates = []
        for item in _iter_dicts(values):
            content = _clean_string(item.get("content"))
            if not content:
                continue
            source_error = _clean_string(item.get("sourceError"))
            if source_error:
                content = f"{content} (avoid: {source_error})"
            confidence = _bounded_float(item.get("confidence"), default=0.5)
            candidates.append(
                MemoryCandidate(
                    layer_id=_normalize_layer(item.get("layer"), category="correction"),
                    content=_prefix_for_category(content, "correction"),
                    category="correction",
                    priority=0.90,
                    confidence=confidence,
                    salience=0.86,
                    rationale="llm structured correction extraction",
                    evidence_refs=evidence_refs,
                    review_required=confidence < self.config.fact_confidence_threshold,
                    supersedes=_extract_string_tuple(item.get("supersedes")),
                )
            )
        return candidates


def fallback_with_skip(update: StructuredMemoryUpdate, reason: str) -> StructuredMemoryUpdate:
    return StructuredMemoryUpdate(
        candidates=update.candidates,
        user_summary=update.user_summary,
        history_summary=update.history_summary,
        user_summary_sections=update.user_summary_sections,
        history_summary_sections=update.history_summary_sections,
        facts_to_remove=update.facts_to_remove,
        skipped=(*update.skipped, reason),
        error=update.error,
    )


def _invoke_model_with_timeout(model: Any, prompt: str, *, timeout_seconds: float) -> Any:
    if timeout_seconds <= 0:
        return model.invoke(prompt)
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            result_queue.put(("ok", model.invoke(prompt)))
        except Exception as exc:
            result_queue.put(("error", exc))

    worker = threading.Thread(target=_worker, name="anvil-memory-updater", daemon=True)
    worker.start()
    try:
        status, value = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise TimeoutError(f"memory updater exceeded {timeout_seconds:.3f}s") from exc
    if status == "error":
        raise value
    return value


def _parse_json_content(content: Any) -> dict[str, Any]:
    text = _extract_text(content)
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("memory updater response must be a JSON object")
    return parsed


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        pending: list[str] = []
        for item in content:
            if isinstance(item, str):
                pending.append(item)
            elif isinstance(item, dict):
                if pending:
                    parts.append("".join(pending))
                    pending.clear()
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if pending:
            parts.append("".join(pending))
        return "\n".join(parts)
    return str(content)


def _extract_summary(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    parts: list[str] = []
    for key in ("workContext", "personalContext", "topOfMind", "recentMonths", "earlierContext", "longTermBackground", "summary"):
        item = value.get(key)
        if isinstance(item, str):
            summary = item.strip()
            if summary:
                parts.append(summary)
        elif isinstance(item, dict) and item.get("shouldUpdate", False):
            summary = _clean_string(item.get("summary"))
            if summary:
                parts.append(summary)
    return " ".join(parts).strip() or None


def _extract_summary_sections(value: Any, *, allowed_keys: tuple[str, ...]) -> dict[str, dict[str, str]] | None:
    if not isinstance(value, dict):
        return None
    sections: dict[str, dict[str, str]] = {}
    for key in allowed_keys:
        item = value.get(key)
        summary = ""
        should_update = False
        if isinstance(item, str):
            summary = item.strip()
            should_update = bool(summary)
        elif isinstance(item, dict):
            should_update = bool(item.get("shouldUpdate", False))
            summary = _clean_string(item.get("summary"))
        if should_update and summary:
            sections[key] = {"summary": summary}
    return sections or None


def _iter_dicts(value: Any):
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, dict):
            yield item


def _extract_string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result = []
    for item in value:
        text = _clean_string(item)
        if text:
            result.append(text)
    return tuple(dict.fromkeys(result))


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def _normalize_layer(value: Any, *, category: str) -> str:
    layer = _clean_string(value).lower()
    if layer in {"user", "profile", "user_profile"}:
        return "user"
    if layer in {"workspace", "runtime", "runtime_memory", "project"}:
        return "workspace"
    if category in {"preference", "goal", "behavior"}:
        return "user"
    return "workspace"


def _prefix_for_category(content: str, category: str) -> str:
    prefixes = {
        "resolved_outcome": "Resolved outcome",
        "outcome": "Outcome",
        "project_constraint": "Project constraint",
        "workflow": "Workflow",
        "environment": "Environment fact",
        "project_context": "Workspace fact",
        "preference": "User preference",
        "correction": "Correction",
    }
    prefix = prefixes.get(category)
    if not prefix or content.lower().startswith(prefix.lower()):
        return content
    return f"{prefix}: {content}"


def _semantic_memory_key(content: str) -> str:
    return semantic_memory_key(content)


def _candidate_rank(candidate: MemoryCandidate) -> tuple[float, float, float, int]:
    return (
        candidate.priority,
        candidate.confidence,
        candidate.salience,
        1 if candidate.category in {"correction", "project_constraint", "preference"} else 0,
    )


def memory_candidate_quality(candidate: MemoryCandidate) -> dict[str, Any]:
    content = candidate.content.strip()
    blockers: list[str] = []
    durable_signal = _has_durable_outcome_signal(content, signals=None)
    extraction_decision = memory_extraction_decision(
        content=content,
        category=candidate.category,
        layer_id=candidate.layer_id,
        confidence=candidate.confidence,
        evidence_refs=candidate.evidence_refs,
        supersedes=candidate.supersedes,
    )
    blockers.extend(blocker for blocker in extraction_decision.blockers if blocker not in blockers)
    has_evidence = bool(candidate.evidence_refs)
    if not _semantic_memory_key(content):
        blockers.append("empty_content")
    if len(_semantic_memory_key(content)) < 24 and candidate.category not in {"preference", "correction"}:
        blockers.append("too_short")
    if candidate.category in {"resolved_outcome", "outcome"} and not durable_signal and not candidate.supersedes:
        blockers.append("missing_durable_outcome_signal")
    if not has_evidence:
        blockers.append("missing_evidence")
    if candidate.confidence < 0.5:
        blockers.append("very_low_confidence")
    evidence_score = min(len(candidate.evidence_refs), 3) / 3
    confidence_score = candidate.confidence
    salience_score = candidate.salience
    durable_score = 1.0 if durable_signal or candidate.category in {"preference", "correction", "project_constraint"} else 0.45
    explicit_supersede = bool(candidate.supersedes)
    review_signal = 1.0 if candidate.review_required or explicit_supersede else 0.0
    quality_score = min(
        1.0,
        confidence_score * 0.34
        + salience_score * 0.18
        + evidence_score * 0.18
        + durable_score * 0.22
        + review_signal * 0.08,
    )
    if not extraction_decision.accepted or "missing_durable_outcome_signal" in blockers or "very_low_confidence" in blockers:
        decision = "skip"
    elif explicit_supersede and candidate.confidence >= 0.9 and has_evidence and "empty_content" not in blockers:
        decision = "write"
    elif candidate.review_required or explicit_supersede or candidate.confidence < 0.82:
        decision = "review" if quality_score >= 0.55 and "missing_evidence" not in blockers else "skip"
    else:
        decision = "write"
    return {
        "quality_score": round(quality_score, 4),
        "decision": decision,
        "blockers": blockers,
        "durable_signal": durable_signal,
        "evidence_count": len(candidate.evidence_refs),
        "extraction_policy": extraction_decision.reason,
    }


def _has_durable_outcome_signal(content: str, *, signals: dict[str, Any] | None) -> bool:
    return has_durable_outcome_signal(content, signals=signals)
