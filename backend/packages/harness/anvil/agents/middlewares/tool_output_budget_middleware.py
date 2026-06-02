"""Behavior enhancement layer.

Reads: tool results, runtime config, path service
Writes: budgeted ToolMessage content and optional output artifacts
Side effects: writes oversized tool results to thread output artifacts
Failure behavior: fail-open with inline truncation when artifact storage is unavailable
"""

from __future__ import annotations

import json
import re
from typing import Any
from pathlib import Path
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState
from anvil.runtime.token_budget import TokenBudgetService


class ToolOutputBudgetMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def wrap_tool_call(self, request, handler):
        response = handler(request)
        config_result = getattr(request.runtime.context, "config_result", None)
        effective_config = getattr(config_result, "effective_config", None)
        config = getattr(effective_config, "tool_output_budget", None)
        if config is None:
            return response
        if not config.enabled:
            return response
        content = getattr(response, "content", response)
        if isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") in {"image_url", "image"}
            for item in content
        ):
            return response
        if not isinstance(content, str):
            content = str(content)

        token_budget = TokenBudgetService()
        output_budget = self._entry_output_budget(request)
        soft_tokens = output_budget or config.default_token_budget
        soft_chars = config.default_char_budget
        hard_tokens = max(config.hard_token_budget, soft_tokens)
        hard_chars = max(config.hard_char_budget, soft_chars)
        token_count = token_budget.count_text(content)
        if token_count <= soft_tokens and len(content) <= soft_chars:
            return response

        artifact_url = None
        if token_count > hard_tokens or len(content) > hard_chars:
            artifact_url = self._persist_artifact(
                runtime=request.runtime,
                tool_name=request.tool_call["name"],
                content=content,
                artifact_directory=config.artifact_directory,
            )

        clipped = token_budget.truncate_text(
            content,
            max_tokens=soft_tokens,
            max_chars=soft_chars,
            marker="\n... [tool output truncated]",
        )
        line_count = content.count("\n") + 1 if content else 0
        notice = {
            "truncated": True,
            "original_tokens_approx": token_count,
            "original_chars": len(content),
            "original_lines": line_count,
        }
        if artifact_url:
            notice["artifact_url"] = artifact_url
        json_content = self._budget_json_content(
            content,
            runtime=request.runtime,
            tool_name=request.tool_call["name"],
            config=config,
            token_budget=token_budget,
            soft_tokens=soft_tokens,
            soft_chars=soft_chars,
            artifact_directory=config.artifact_directory,
            notice=notice,
        )
        if json_content is not None:
            new_content = json_content
        else:
            text_notice = (
                f"\n\n[tool_output_budget] original_tokens~{token_count}, "
                f"original_chars={len(content)}, original_lines={line_count}."
            )
            if artifact_url:
                text_notice += f" Full output stored at {artifact_url}."
            new_content = clipped + text_notice
        if isinstance(response, ToolMessage):
            return response.model_copy(update={"content": new_content})
        return new_content

    def _entry_output_budget(self, request) -> int | None:
        bundle = getattr(request.runtime.context, "capability_bundle", None)
        if bundle is None:
            return None
        tool_name = request.tool_call["name"]
        for entry in bundle.visible_tools:
            if entry.name == tool_name:
                token_budget = getattr(entry, "output_token_budget", None)
                if token_budget is not None:
                    return int(token_budget)
                if entry.output_budget is not None:
                    return max(int(entry.output_budget) // 4, 1)
        return None

    def _persist_artifact(self, *, runtime, tool_name: str, content: str, artifact_directory: str) -> str | None:
        try:
            thread_id = runtime.context.thread_id
            output_root = runtime.context.path_service.thread_outputs_dir(thread_id)
            relative_path = Path(artifact_directory) / f"{_safe_tool_output_name(tool_name)}-{uuid4().hex[:12]}.txt"
            target = output_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            descriptor = runtime.context.path_service.to_artifact_descriptor(thread_id, "outputs", relative_path.as_posix())
            return descriptor.artifact_url
        except Exception:
            return None

    def _budget_json_content(
        self,
        content: str,
        *,
        runtime,
        tool_name: str,
        config,
        token_budget: TokenBudgetService,
        soft_tokens: int,
        soft_chars: int,
        artifact_directory: str,
        notice: dict[str, Any],
    ) -> str | None:
        try:
            value = json.loads(content)
        except (TypeError, ValueError):
            return None
        compacted = self._compact_command_payload(
            value,
            runtime=runtime,
            tool_name=tool_name,
            config=config,
            artifact_directory=artifact_directory,
        )
        if compacted is not None:
            value, compaction = compacted
            notice = {**notice, "compaction": compaction}
            value["_tool_output_budget"] = notice
            return json.dumps(value, ensure_ascii=False, default=str)

        catalog_payload = self._budget_catalog_payload(
            value,
            token_budget=token_budget,
            soft_tokens=soft_tokens,
            soft_chars=soft_chars,
            notice=notice,
        )
        if catalog_payload is not None:
            return catalog_payload

        max_string_tokens = max(soft_tokens // 8, 24)
        max_string_chars = max(soft_chars // 8, 96)
        budgeted = self._truncate_json_value(
            value,
            token_budget=token_budget,
            max_string_tokens=max_string_tokens,
            max_string_chars=max_string_chars,
            max_sequence_items=8,
        )
        if isinstance(budgeted, dict):
            budgeted["_tool_output_budget"] = notice
        else:
            budgeted = {"items": budgeted, "_tool_output_budget": notice}

        rendered = json.dumps(budgeted, ensure_ascii=False, default=str)
        if token_budget.count_text(rendered) <= soft_tokens and len(rendered) <= soft_chars:
            return rendered

        budgeted = self._truncate_json_value(
            value,
            token_budget=token_budget,
            max_string_tokens=max(soft_tokens // 16, 12),
            max_string_chars=max(soft_chars // 16, 48),
            max_sequence_items=3,
        )
        if isinstance(budgeted, dict):
            budgeted["_tool_output_budget"] = notice
        else:
            budgeted = {"items": budgeted, "_tool_output_budget": notice}
        rendered = json.dumps(budgeted, ensure_ascii=False, default=str)
        if token_budget.count_text(rendered) <= soft_tokens and len(rendered) <= soft_chars:
            return rendered
        return rendered

    def _budget_catalog_payload(
        self,
        value: Any,
        *,
        token_budget: TokenBudgetService,
        soft_tokens: int,
        soft_chars: int,
        notice: dict[str, Any],
    ) -> str | None:
        if not _is_catalog_payload(value):
            return None
        item_count = len(value.get("items") or [])
        for include_descriptions in (True, False):
            payload = _compact_catalog_payload(
                value,
                max_string_tokens=max(soft_tokens // (16 if include_descriptions else 24), 12),
                max_string_chars=max(soft_chars // (16 if include_descriptions else 24), 48),
                include_descriptions=include_descriptions,
                token_budget=token_budget,
            )
            catalog_notice = {
                **notice,
                "compaction": {
                    "profile": "catalog",
                    "items_preserved": item_count,
                    "descriptions_preserved": include_descriptions,
                    "reason": "preserve catalog identifiers and read handles; compact verbose item fields",
                },
            }
            payload["_tool_output_budget"] = catalog_notice
            rendered = json.dumps(payload, ensure_ascii=False, default=str)
            if include_descriptions and (
                token_budget.count_text(rendered) > soft_tokens or len(rendered) > soft_chars
            ):
                continue
            return rendered
        return None

    def _truncate_json_value(
        self,
        value: Any,
        *,
        token_budget: TokenBudgetService,
        max_string_tokens: int,
        max_string_chars: int,
        max_sequence_items: int,
    ) -> Any:
        if isinstance(value, str):
            return token_budget.truncate_text(
                value,
                max_tokens=max_string_tokens,
                max_chars=max_string_chars,
                marker="... [truncated]",
            )
        if isinstance(value, list):
            return [
                self._truncate_json_value(
                    item,
                    token_budget=token_budget,
                    max_string_tokens=max_string_tokens,
                    max_string_chars=max_string_chars,
                    max_sequence_items=max_sequence_items,
                )
                for item in value[:max_sequence_items]
            ]
        if isinstance(value, dict):
            return {
                str(key): self._truncate_json_value(
                    item,
                    token_budget=token_budget,
                    max_string_tokens=max_string_tokens,
                    max_string_chars=max_string_chars,
                    max_sequence_items=max_sequence_items,
                )
                for key, item in value.items()
            }
        return value

    def _compact_command_payload(
        self,
        value: Any,
        *,
        runtime,
        tool_name: str,
        config,
        artifact_directory: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if not bool(getattr(config, "command_compaction_enabled", True)):
            return None
        if not isinstance(value, dict):
            return None
        command = value.get("command")
        raw_output = value.get("output")
        exit_code = value.get("exit_code")
        status = str(value.get("status") or "").lower()
        if not isinstance(command, str) or not isinstance(raw_output, str):
            return None
        if len(raw_output) < int(getattr(config, "command_compaction_min_chars", 1200)):
            return None
        profile = _command_compaction_profile(command)
        if profile not in set(getattr(config, "command_profiles", ()) or ()):
            return None
        compacted_output, normalization = _compact_command_output(
            raw_output,
            profile=profile,
            exit_code=exit_code if isinstance(exit_code, int) else None,
            status=status,
            max_chars=int(getattr(config, "command_compaction_max_chars", 4800)),
        )
        if compacted_output == raw_output:
            return None
        raw_artifact_url = None
        failed_command = status not in {"completed", "success"} or (isinstance(exit_code, int) and exit_code != 0)
        if failed_command:
            should_store_raw = bool(getattr(config, "raw_failure_artifacts", True))
            raw_artifact_reason = "failure"
        else:
            should_store_raw = bool(getattr(config, "raw_compaction_artifacts", True))
            raw_artifact_reason = "compaction"
        if should_store_raw:
            raw_artifact_url = self._persist_artifact(
                runtime=runtime,
                tool_name=f"{tool_name}-raw",
                content=raw_output,
                artifact_directory=artifact_directory,
            )
        chars_saved = max(len(raw_output) - len(compacted_output), 0)
        compacted = dict(value)
        compacted["output"] = compacted_output
        compacted["output_compacted"] = True
        compacted["output_compaction_profile"] = profile
        if raw_artifact_url:
            compacted["raw_output_artifact_url"] = raw_artifact_url
        compaction: dict[str, Any] = {
            "profile": profile,
            "original_chars": len(raw_output),
            "compacted_chars": len(compacted_output),
            "original_lines": raw_output.count("\n") + 1 if raw_output else 0,
            "compacted_lines": compacted_output.count("\n") + 1 if compacted_output else 0,
            "savings": {
                "chars_saved": chars_saved,
                "ratio": round(chars_saved / max(len(raw_output), 1), 4),
            },
            "normalization": normalization,
        }
        if raw_artifact_url:
            compaction["raw_artifact_url"] = raw_artifact_url
            compaction["raw_artifact_reason"] = raw_artifact_reason
        return compacted, compaction


def _is_catalog_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    items = value.get("items")
    if not isinstance(items, list) or not items:
        return False
    if not all(isinstance(item, dict) for item in items):
        return False
    top_level_markers = {"total", "returned", "truncated", "read_hint", "progressive_disclosure"}
    item_markers = {"skill_id", "tool_name", "toolset", "id", "name", "title", "read_tool", "source_root"}
    return bool(top_level_markers.intersection(value)) and any(
        bool(item_markers.intersection(item))
        for item in items
        if isinstance(item, dict)
    )


def _compact_catalog_payload(
    value: dict[str, Any],
    *,
    max_string_tokens: int,
    max_string_chars: int,
    include_descriptions: bool,
    token_budget: TokenBudgetService,
) -> dict[str, Any]:
    payload = {
        str(key): item
        for key, item in value.items()
        if key != "items" and key != "_tool_output_budget"
    }
    payload["items"] = [
        _compact_catalog_item(
            item,
            max_string_tokens=max_string_tokens,
            max_string_chars=max_string_chars,
            include_description=include_descriptions,
            token_budget=token_budget,
        )
        for item in value.get("items", [])
        if isinstance(item, dict)
    ]
    return payload


def _compact_catalog_item(
    item: dict[str, Any],
    *,
    max_string_tokens: int,
    max_string_chars: int,
    include_description: bool,
    token_budget: TokenBudgetService,
) -> dict[str, Any]:
    priority_fields = (
        "skill_id",
        "tool_name",
        "toolset",
        "id",
        "name",
        "title",
        "summary",
        "enabled",
        "valid",
        "trust",
        "tags",
        "read_tool",
        "read_hint",
        "path",
        "source_root",
        "readiness",
        "curator",
        "kind",
        "type",
    )
    compacted = {
        key: _compact_catalog_value(
            item[key],
            max_string_tokens=max_string_tokens,
            max_string_chars=max_string_chars,
            token_budget=token_budget,
        )
        for key in priority_fields
        if key in item
    }
    if include_description and "description" in item:
        compacted["description"] = _compact_catalog_value(
            item["description"],
            max_string_tokens=max_string_tokens,
            max_string_chars=max_string_chars,
            token_budget=token_budget,
        )
    elif "description" in item:
        compacted["description_omitted"] = True
    return compacted


def _compact_catalog_value(
    value: Any,
    *,
    max_string_tokens: int,
    max_string_chars: int,
    token_budget: TokenBudgetService,
) -> Any:
    if isinstance(value, str):
        return token_budget.truncate_text(
            value,
            max_tokens=max_string_tokens,
            max_chars=max_string_chars,
            marker="... [truncated]",
        )
    if isinstance(value, list):
        return [
            _compact_catalog_value(
                item,
                max_string_tokens=max_string_tokens,
                max_string_chars=max_string_chars,
                token_budget=token_budget,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            str(key): _compact_catalog_value(
                item,
                max_string_tokens=max_string_tokens,
                max_string_chars=max_string_chars,
                token_budget=token_budget,
            )
            for key, item in value.items()
        }
    return value


def _safe_tool_output_name(tool_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(tool_name or "tool")).strip(".-")
    return normalized or "tool"


def _command_compaction_profile(command: str) -> str:
    lowered = command.lower()
    if "pytest" in lowered or "vitest" in lowered or "jest" in lowered or "npm test" in lowered:
        return "test"
    if "tsc" in lowered or "mypy" in lowered:
        return "typecheck"
    if "ruff" in lowered or "eslint" in lowered or "flake8" in lowered:
        return "lint"
    if "npm " in lowered or "pnpm " in lowered or "yarn " in lowered or "pip " in lowered:
        return "package"
    if "git " in f" {lowered}":
        return "git"
    if "docker " in lowered or "compose " in lowered:
        return "container"
    if "rg " in f" {lowered}" or "grep " in f" {lowered}" or "findstr" in lowered:
        return "search"
    return "generic"


def _compact_command_output(raw_output: str, *, profile: str, exit_code: int | None, status: str, max_chars: int) -> tuple[str, dict[str, int]]:
    raw_lines = raw_output.splitlines()
    lines, normalization = _normalize_command_output_lines(raw_lines)
    normalized_output = "\n".join(lines)
    if len(raw_lines) <= 16 and len(raw_output) <= 1200 and normalization["ansi_sequences_removed"] == 0:
        return raw_output, normalization
    failure = status not in {"completed", "success"} or (exit_code is not None and exit_code != 0)
    structured = _structured_command_profile_output(lines, profile=profile, command_failed=failure)
    if structured is not None:
        rendered = "\n".join(
            [
                (
                    f"[command_output_compaction] profile={profile} mode=structured original_lines={len(raw_lines)} "
                    f"normalized_lines={len(lines)} retained_lines={structured.count(chr(10)) + 1 if structured else 0} "
                    f"ansi_removed={normalization['ansi_sequences_removed']} "
                    f"progress_updates_collapsed={normalization['progress_updates_collapsed']}"
                ),
                structured,
            ]
        )
        if len(rendered) <= max_chars:
            return rendered, normalization
        return rendered[: max(0, max_chars - 25)].rstrip() + "\n... [compacted output clipped]", normalization
    important = _select_important_output_lines(lines, profile=profile, failure=failure)
    head = lines[:6]
    tail = [] if failure and profile == "test" else lines[-8:] if failure else lines[-4:]
    selected = _dedupe_preserve_order([*important, *head, *tail])
    if not selected:
        selected = _dedupe_preserve_order([*head, *tail])
    omitted = max(len(lines) - len(selected), 0)
    rendered = "\n".join(
        [
            (
                f"[command_output_compaction] profile={profile} original_lines={len(raw_lines)} "
                f"normalized_lines={len(lines)} retained_lines={len(selected)} omitted_lines={omitted} "
                f"ansi_removed={normalization['ansi_sequences_removed']} "
                f"progress_updates_collapsed={normalization['progress_updates_collapsed']}"
            ),
            *selected,
        ]
    )
    if rendered == normalized_output and normalization["ansi_sequences_removed"] == 0:
        return raw_output, normalization
    if len(rendered) <= max_chars:
        return rendered, normalization
    return rendered[: max(0, max_chars - 25)].rstrip() + "\n... [compacted output clipped]", normalization


def _select_important_output_lines(lines: list[str], *, profile: str, failure: bool) -> list[str]:
    common_patterns = [
        r"\b(error|failed|failure|exception|traceback|assertionerror|fatal|warning)\b",
        r"\b(exit code|exit status|returncode)\b",
        r"\b\d+\s+(failed|passed|error|skipped|warnings?)\b",
    ]
    profile_patterns = {
        "test": [
            r"\bFAILED\b",
            r"\bERROR\b",
            r"^E\s+",
            r"^>\s+",
            r"short test summary",
        ],
        "typecheck": [r"\bTS\d+\b", r"\[[-a-z0-9_]+\]", r"\bFound \d+ errors?\b"],
        "lint": [r"\b[A-Z]\d{3,4}\b", r"\bfixable\b", r"\bproblems?\b"],
        "package": [r"\bERR!\b", r"\bELIFECYCLE\b", r"\bdeprecated\b", r"\bfailed\b"],
        "git": [r"\bfatal:\b", r"\berror:\b", r"\bCONFLICT\b", r"\bmodified:\b"],
        "container": [r"\bfailed\b", r"\berror\b", r"\bexited\b", r"\bunhealthy\b"],
        "search": [r"\bNo such file\b", r"\bPermission denied\b"],
    }.get(profile, [])
    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in [*common_patterns, *profile_patterns]]
    selected: list[str] = []
    for index, line in enumerate(lines):
        if any(pattern.search(line) for pattern in patterns):
            if failure and index > 0:
                selected.append(lines[index - 1])
            selected.append(line)
            if failure and index + 1 < len(lines):
                selected.append(lines[index + 1])
    return selected[:80]


def _structured_command_profile_output(lines: list[str], *, profile: str, command_failed: bool) -> str | None:
    if profile == "test":
        return _compact_test_output(lines, command_failed=command_failed)
    if profile == "typecheck":
        return _compact_typecheck_output(lines)
    if profile == "lint":
        return _compact_lint_output(lines)
    if profile == "package":
        return _compact_package_output(lines)
    return None


def _compact_test_output(lines: list[str], *, command_failed: bool) -> str | None:
    pytest = _compact_pytest_output(lines)
    if pytest is not None:
        return pytest
    vitest = _compact_vitest_output(lines)
    if vitest is not None:
        return vitest
    if not command_failed:
        summary = _last_matching_line(lines, [r"\b\d+\s+passed\b", r"\bTests?\s+.*passed\b"])
        if summary is not None:
            return f"Tests: {summary}"
    return None


def _compact_pytest_output(lines: list[str]) -> str | None:
    failures: list[str] = []
    xfail_lines: list[str] = []
    current: list[str] = []
    in_failures = False
    in_summary = False
    summary_line: str | None = None
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if stripped.startswith("=") and "failures" in lowered:
            in_failures = True
            in_summary = False
            continue
        if stripped.startswith("=") and "short test summary" in lowered:
            if current:
                failures.append("\n".join(current))
                current = []
            in_failures = False
            in_summary = True
            continue
        if _looks_like_pytest_summary(stripped):
            summary_line = stripped.strip("= ").strip()
            continue
        if in_failures:
            if stripped.startswith("___"):
                if current:
                    failures.append("\n".join(current))
                current = [stripped]
            elif stripped:
                current.append(stripped)
            continue
        if in_summary:
            if stripped.startswith(("FAILED", "ERROR")):
                failures.append(stripped)
            elif stripped.startswith(("XFAIL", "XPASS")):
                xfail_lines.append(stripped)
    if current:
        failures.append("\n".join(current))
    if summary_line is None and not failures and not xfail_lines:
        return None
    counts = _parse_count_words(summary_line or "")
    if not failures and counts.get("failed", 0) == 0 and counts.get("error", 0) == 0:
        return _join_nonempty(["Pytest: " + (summary_line or "completed"), *_bounded_prefixed_lines("Expected outcomes", xfail_lines, limit=8)])
    parts = [f"Pytest: {summary_line or 'failures detected'}"]
    if xfail_lines:
        parts.extend(_bounded_prefixed_lines("Expected outcomes", xfail_lines, limit=8))
    if failures:
        parts.append("Failures:")
        for index, failure in enumerate(failures[:12], start=1):
            parts.extend(_format_failure_block(index, failure, language_hint=".py"))
        if len(failures) > 12:
            parts.append(f"... +{len(failures) - 12} more failures")
    return _join_nonempty(parts)


def _compact_vitest_output(lines: list[str]) -> str | None:
    summary_lines = [
        line.strip()
        for line in lines
        if re.search(r"\b(Test Files|Tests|Snapshots|Duration)\b", line)
    ]
    failure_lines: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.search(r"\b(FAIL|failed)\b", stripped, re.IGNORECASE) or stripped.startswith(("×", "✕", "✖")):
            context = [stripped]
            for next_line in lines[index + 1 : index + 4]:
                if next_line.startswith((" ", "\t")) or re.search(r"\b(error|expected|received)\b", next_line, re.IGNORECASE):
                    context.append(next_line.strip())
            failure_lines.append("\n".join(context))
    if not summary_lines and not failure_lines:
        return None
    parts = ["Vitest/Jest:"]
    parts.extend(_dedupe_preserve_order(summary_lines[-6:]))
    if failure_lines:
        parts.append("Failures:")
        for index, failure in enumerate(_dedupe_preserve_order(failure_lines)[:12], start=1):
            parts.extend(_format_failure_block(index, failure, language_hint="."))
        if len(failure_lines) > 12:
            parts.append(f"... +{len(failure_lines) - 12} more failures")
    return _join_nonempty(parts)


def _compact_typecheck_output(lines: list[str]) -> str | None:
    tsc_entries = _parse_tsc_entries(lines)
    if tsc_entries:
        return _render_diagnostics("TypeScript", tsc_entries)
    mypy_entries, fileless = _parse_mypy_entries(lines)
    if mypy_entries or fileless:
        parts: list[str] = []
        if fileless:
            parts.extend(fileless[:12])
        if mypy_entries:
            parts.append(_render_diagnostics("mypy", mypy_entries))
        return _join_nonempty(parts)
    success = _last_matching_line(lines, [r"\bFound 0 errors\b", r"\bSuccess: no issues found\b"])
    if success is not None:
        return f"Typecheck: {success}"
    return None


def _compact_lint_output(lines: list[str]) -> str | None:
    ruff = _parse_ruff_json(lines)
    if ruff is not None:
        return ruff
    diagnostics: list[dict[str, str | int]] = []
    pattern = re.compile(r"^(.+?):(\d+):(?:(\d+):)?\s*([A-Z]\d{3,4}|[A-Za-z0-9_/-]+)\s+(.+)$")
    for line in lines:
        match = pattern.match(line.strip())
        if match:
            diagnostics.append(
                {
                    "file": match.group(1),
                    "line": int(match.group(2)),
                    "code": match.group(4),
                    "message": match.group(5),
                }
            )
    if diagnostics:
        return _render_diagnostics("Lint", diagnostics)
    summary = _last_matching_line(lines, [r"\bAll checks passed\b", r"\bNo issues found\b", r"\b0 problems?\b"])
    if summary is not None:
        return f"Lint: {summary}"
    return None


def _compact_package_output(lines: list[str]) -> str | None:
    kept: list[str] = []
    latest_progress: str | None = None
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped:
            continue
        if _progress_line_prefix(stripped):
            latest_progress = stripped
            continue
        if stripped.startswith(">") and "@" in stripped:
            continue
        if lowered.startswith(("npm notice", "npm warn")) and "deprecated" not in lowered:
            continue
        if re.search(r"\b(err!|error|failed|deprecated|added \d+|removed \d+|changed \d+|audited \d+|vulnerabilit)", lowered):
            kept.append(stripped)
    if latest_progress:
        kept = [f"Latest progress: {latest_progress}", *kept[-39:]]
    if not kept:
        return None
    return _join_nonempty(["Package command:", *_dedupe_preserve_order(kept[-40:])])


def _parse_tsc_entries(lines: list[str]) -> list[dict[str, str | int]]:
    pattern = re.compile(r"^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.+)$")
    entries: list[dict[str, str | int]] = []
    index = 0
    while index < len(lines):
        match = pattern.match(lines[index].strip())
        if not match:
            index += 1
            continue
        context: list[str] = []
        index += 1
        while index < len(lines) and lines[index].startswith((" ", "\t")) and not pattern.match(lines[index].strip()):
            context.append(lines[index].strip())
            index += 1
        entries.append(
            {
                "file": match.group(1),
                "line": int(match.group(2)),
                "code": match.group(5),
                "message": _truncate_line(match.group(6), 160),
                "context": "\n".join(_truncate_line(item, 160) for item in context[:3]),
            }
        )
    return entries


def _parse_mypy_entries(lines: list[str]) -> tuple[list[dict[str, str | int]], list[str]]:
    pattern = re.compile(r"^(.+?):(\d+)(?::\d+)?:\s+(error|warning|note):\s+(.+?)(?:\s+\[([-a-zA-Z0-9_]+)\])?$")
    entries: list[dict[str, str | int]] = []
    fileless: list[str] = []
    for line in lines:
        stripped = line.strip()
        match = pattern.match(stripped)
        if not match:
            if "error:" in stripped and not stripped.startswith("Found "):
                fileless.append(stripped)
            continue
        severity = match.group(3)
        if severity == "note" and entries and entries[-1]["file"] == match.group(1):
            existing = str(entries[-1].get("context") or "")
            note = _truncate_line(match.group(4), 160)
            entries[-1]["context"] = "\n".join(item for item in [existing, note] if item)
            continue
        if severity == "note":
            fileless.append(stripped)
            continue
        entries.append(
            {
                "file": match.group(1),
                "line": int(match.group(2)),
                "code": match.group(5) or severity,
                "message": _truncate_line(match.group(4), 160),
            }
        )
    return entries, fileless


def _parse_ruff_json(lines: list[str]) -> str | None:
    text = "\n".join(lines).strip()
    if not text.startswith("["):
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, list):
        return None
    if not payload:
        return "Ruff: No issues found"
    diagnostics: list[dict[str, str | int]] = []
    fixable = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        location = item.get("location")
        line = location.get("row") if isinstance(location, dict) else 0
        if item.get("fix") is not None:
            fixable += 1
        diagnostics.append(
            {
                "file": str(item.get("filename") or "unknown"),
                "line": int(line or 0),
                "code": str(item.get("code") or "ruff"),
                "message": _truncate_line(str(item.get("message") or ""), 160),
            }
        )
    rendered = _render_diagnostics("Ruff", diagnostics)
    if fixable:
        rendered = rendered.replace("\n", f" ({fixable} fixable)\n", 1)
    return rendered


def _render_diagnostics(label: str, diagnostics: list[dict[str, str | int]]) -> str:
    by_file: dict[str, list[dict[str, str | int]]] = {}
    by_code: dict[str, int] = {}
    for item in diagnostics:
        file = str(item.get("file") or "unknown")
        code = str(item.get("code") or "")
        by_file.setdefault(file, []).append(item)
        if code:
            by_code[code] = by_code.get(code, 0) + 1
    parts = [f"{label}: {len(diagnostics)} issues in {len(by_file)} files"]
    if len(by_code) > 1:
        top_codes = sorted(by_code.items(), key=lambda item: item[1], reverse=True)[:6]
        parts.append("Top codes: " + ", ".join(f"{code} ({count}x)" for code, count in top_codes))
    for file, items in sorted(by_file.items(), key=lambda item: len(item[1]), reverse=True)[:20]:
        parts.append(f"{file} ({len(items)} issues)")
        for item in items[:12]:
            line = int(item.get("line") or 0)
            code = str(item.get("code") or "")
            message = str(item.get("message") or "")
            prefix = f"  L{line}: " if line else "  "
            parts.append(f"{prefix}{code} {message}".rstrip())
            context = str(item.get("context") or "")
            for context_line in context.splitlines()[:3]:
                parts.append(f"    {_truncate_line(context_line, 160)}")
        if len(items) > 12:
            parts.append(f"  ... +{len(items) - 12} more in this file")
    if len(by_file) > 20:
        parts.append(f"... +{len(by_file) - 20} more files")
    return _join_nonempty(parts)


def _format_failure_block(index: int, failure: str, *, language_hint: str) -> list[str]:
    lines = [line.strip() for line in failure.splitlines() if line.strip()]
    if not lines:
        return []
    title = lines[0].strip("_ ").strip() or "failure"
    result = [f"{index}. [FAIL] {_truncate_line(title, 160)}"]
    relevant = 0
    for line in lines[1:]:
        lowered = line.lower()
        if line.startswith((">", "E", "AssertionError", "Error:")) or "assert" in lowered or "error" in lowered or language_hint in line:
            result.append(f"   {_truncate_line(line, 160)}")
            relevant += 1
        if relevant >= 4:
            break
    return result


def _looks_like_pytest_summary(line: str) -> bool:
    lowered = line.lower().strip("= ")
    return " in " in lowered and bool(re.search(r"\b\d+\s+(passed|failed|error|errors|skipped|xfailed|xpassed)\b", lowered))


def _parse_count_words(line: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for count, word in re.findall(r"\b(\d+)\s+([A-Za-z]+)", line):
        key = word.lower()
        if key.endswith("s"):
            key = key[:-1]
        counts[key] = int(count)
    return counts


def _last_matching_line(lines: list[str], patterns: list[str]) -> str | None:
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for line in reversed(lines):
        stripped = line.strip()
        if any(pattern.search(stripped) for pattern in compiled):
            return stripped
    return None


def _bounded_prefixed_lines(title: str, lines: list[str], *, limit: int) -> list[str]:
    if not lines:
        return []
    result = [f"{title}:"]
    result.extend(f"  {_truncate_line(item, 160)}" for item in lines[:limit])
    if len(lines) > limit:
        result.append(f"  ... +{len(lines) - limit} more")
    return result


def _truncate_line(line: str, max_chars: int) -> str:
    if len(line) <= max_chars:
        return line
    return line[: max(0, max_chars - 3)].rstrip() + "..."


def _join_nonempty(lines: list[str]) -> str:
    return "\n".join(line for line in lines if line)


def _dedupe_preserve_order(lines: list[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(line)
    return selected


def _normalize_command_output_lines(raw_lines: list[str]) -> tuple[list[str], dict[str, int]]:
    normalized: list[str] = []
    ansi_sequences_removed = 0
    progress_updates_collapsed = 0
    last_progress_prefix: str | None = None
    for raw_line in raw_lines:
        line, removed = _strip_ansi_and_control(raw_line)
        ansi_sequences_removed += removed
        line = line.rstrip()
        if not line.strip():
            continue
        progress_prefix = _progress_line_prefix(line)
        if progress_prefix and progress_prefix == last_progress_prefix and normalized:
            normalized[-1] = line
            progress_updates_collapsed += 1
            continue
        normalized.append(line)
        last_progress_prefix = progress_prefix
    return normalized, {
        "ansi_sequences_removed": ansi_sequences_removed,
        "progress_updates_collapsed": progress_updates_collapsed,
    }


def _strip_ansi_and_control(text: str) -> tuple[str, int]:
    ansi_pattern = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-Z\\-_])")
    stripped, ansi_count = ansi_pattern.subn("", text.replace("\r", "\n"))
    cleaned = "".join(ch for ch in stripped if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return cleaned, ansi_count


def _progress_line_prefix(line: str) -> str | None:
    stripped = line.strip()
    lowered = stripped.lower()
    has_progress_marker = bool(re.search(r"\b\d{1,3}(?:\.\d+)?%\b", stripped))
    has_size_ratio = bool(
        re.search(r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|kib|mib|gib)\s*/\s*\d+(?:\.\d+)?\s*(?:kb|mb|gb|kib|mib|gib)\b", lowered)
    )
    has_count_ratio = bool(re.search(r"\b\d+/\d+\b", stripped)) and bool(
        re.search(r"\b(download|fetch|pull|extract|install|build|test|done|complete)", lowered)
    )
    if has_progress_marker or has_size_ratio or has_count_ratio:
        prefix = re.sub(r"\b\d{1,3}(?:\.\d+)?%\b", "<percent>", lowered)
        prefix = re.sub(r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|kib|mib|gib)\b", "<size>", prefix)
        prefix = re.sub(r"\b\d+/\d+\b", "<count>", prefix)
        return re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", prefix)
    return None
