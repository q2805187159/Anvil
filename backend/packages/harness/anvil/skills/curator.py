from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anvil.config import EffectiveConfig
from anvil.memory.scrubber import MemorySecretScrubber

from .contracts import SkillGovernanceRecord, SkillManifest
from .governance import SkillGovernanceService, utc_now_iso
from .loader import SkillLoader, default_installed_skill_root


SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MERGE_PROPOSAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MAX_BODY_CHARS = 32_000
MAX_SUMMARY_CHARS = 500
MAX_RATIONALE_CHARS = 2_000
MAX_SUPPORT_FILE_CHARS = 64_000
MAX_MERGE_PATCH_CHARS = 4_000
MAX_MERGE_DISTILLED_LINES = 12
MAX_PROCEDURE_STEPS = 12
MAX_PROCEDURE_STEP_CHARS = 500
MAX_PROCEDURE_TRIGGER_CHARS = 500
MAX_PROCEDURE_OUTCOME_CHARS = 800
DEFAULT_CURATOR_BACKUP_SCAN_LIMIT = 5_000
MAX_CURATOR_BACKUP_SCAN_LIMIT = 50_000
MIN_PROCEDURE_QUALITY_FOR_PROMOTION = 0.58
DEFAULT_STALE_DAYS = 30
DEFAULT_ARCHIVE_DAYS = 90
PROCEDURE_VERIFICATION_KEYWORDS_RE = re.compile(
    r"\b(assert|benchmark|build|check(?:ed|ing)?|diff|lint|passed|preflight|pytest|regression|screenshot|test(?:ed|ing|s)?|tsc|typecheck|validat(?:e|ed|es|ing|ion)|verif(?:y|ied|ies|ication))\b",
    re.IGNORECASE,
)
PROCEDURE_VERIFICATION_TOOL_NAMES = frozenset(
    {
        "bash",
        "browser_console",
        "browser_network",
        "browser_screenshot",
        "browser_snapshot",
        "code_health",
        "code_pattern_scan",
        "code_security_scan",
        "js_repl",
        "process",
        "run_command",
    }
)
PROCEDURE_DISCOVERY_TOOL_NAMES = frozenset(
    {
        "code_definition",
        "code_doc_graph",
        "code_file_summary",
        "code_focus",
        "code_impact",
        "code_map",
        "code_references",
        "code_semantic_index",
        "code_symbol_search",
        "code_symbols",
        "file_info",
        "glob_files",
        "grep_files",
        "list_dir",
        "read_file",
        "search_files",
        "web_crawl",
        "web_extract",
        "web_fetch",
        "web_search",
    }
)
PROCEDURE_MUTATION_TOOL_NAMES = frozenset(
    {
        "delete_path",
        "export_document",
        "make_dir",
        "move_path",
        "patch_file",
        "write_file",
    }
)
GENERIC_PROCEDURE_STEP_RE = re.compile(
    r"^(do the thing|summarize it|finish the task|complete the task|handle the request|do it|make changes|fix it)\.?$",
    re.IGNORECASE,
)
ONE_OFF_PROCEDURE_RE = re.compile(
    r"\b(current session|current thread|this task|one[- ]off|temporary|scratch|screenshot|thread-[a-z0-9_-]+|run-[a-z0-9_-]+)\b"
    r"|当前会话|当前线程|本轮|这个任务|一次性|临时|草稿|截图",
    re.IGNORECASE,
)
SUPPORTED_ACTIONS = (
    "report",
    "curate",
    "create",
    "update",
    "patch",
    "write_file",
    "remove_file",
    "archive",
    "restore",
    "backup",
    "rollback",
    "merge_plan",
    "merge_apply",
    "quality_plan",
    "review_apply",
    "feedback",
    "learn_procedure",
    "procedures",
    "promote_procedure",
    "reject_procedure",
    "restore_procedure",
    "maintenance",
    "pin",
    "unpin",
)
SUPPORT_FILE_PREFIXES = {"assets", "templates", "scripts", "references"}
REUSABLE_TEMPLATE_PATH = "templates/reusable-template.md"


@dataclass(frozen=True)
class SkillCuratorAutomationResult:
    ran: bool
    reason: str
    report: dict[str, object] | None = None
    next_run_at: str | None = None


@dataclass(frozen=True, slots=True)
class _CuratorBackupResult:
    path: Path
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False

    def metadata(self) -> dict[str, object]:
        return {
            "backup_path": str(self.path),
            "backup_scanned_path_count": self.scanned_path_count,
            "backup_max_scanned_paths": self.max_scanned_paths,
            "backup_scan_truncated": self.scan_truncated,
        }


@dataclass(frozen=True, slots=True)
class _CuratorTreeFileScan:
    files: tuple[tuple[str, str], ...]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False


@dataclass(frozen=True, slots=True)
class _CuratorBackupArchiveEntry:
    info: zipfile.ZipInfo
    filename: str


@dataclass(frozen=True, slots=True)
class _CuratorBackupArchiveScan:
    entries: tuple[_CuratorBackupArchiveEntry, ...]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False


@dataclass(frozen=True, slots=True)
class _CuratorCandidateScan:
    paths: tuple[Path, ...]
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False


@dataclass(frozen=True, slots=True)
class _CuratorProposalLookup:
    proposal: dict[str, object] | None
    scanned_path_count: int
    max_scanned_paths: int
    scan_truncated: bool = False

    def metadata(self, prefix: str) -> dict[str, object]:
        return {
            f"{prefix}_scanned_path_count": self.scanned_path_count,
            f"{prefix}_max_scanned_paths": self.max_scanned_paths,
            f"{prefix}_scan_truncated": self.scan_truncated,
        }


class SkillCuratorService:
    def __init__(
        self,
        *,
        loader: SkillLoader | None = None,
        governance: SkillGovernanceService | None = None,
    ) -> None:
        self.loader = loader or SkillLoader()
        self.governance = governance or SkillGovernanceService(loader=self.loader)

    def manage(
        self,
        *,
        config: EffectiveConfig,
        action: str,
        skill_id: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        body: str | None = None,
        rationale: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        allowed_tools: list[str] | tuple[str, ...] | None = None,
        file_path: str | None = None,
        content: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        absorbed_into: str | None = None,
        revision: str | None = None,
        outcome: str | None = None,
        feedback_source: str | None = None,
        confidence: float | int | None = None,
        trigger: str | None = None,
        steps: list[str] | tuple[str, ...] | None = None,
        expected_outcome: str | None = None,
        evidence_refs: list[str] | tuple[str, ...] | None = None,
        source_ref: str | None = None,
        procedure_id: str | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        normalized = action.strip().lower()
        if normalized in {"status", "report"}:
            return self.report(config=config)
        if normalized == "curate":
            return self.curate(config=config, dry_run=dry_run, force=force)
        if normalized in {"merge_plan", "plan_merge"}:
            return self.plan_duplicate_merge(
                config=config,
                skill_id=skill_id,
                absorbed_into=absorbed_into,
                dry_run=dry_run,
                force=force,
            )
        if normalized in {"merge_apply", "apply_merge"}:
            return self.apply_duplicate_merge(
                config=config,
                revision=revision,
                dry_run=dry_run,
                force=force,
            )
        if normalized in {"quality_plan", "plan_review"}:
            return self.plan_skill_review(
                config=config,
                skill_id=skill_id,
                rationale=rationale,
                dry_run=dry_run,
            )
        if normalized in {"review_apply", "apply_review"}:
            return self.apply_skill_review(
                config=config,
                revision=revision,
                dry_run=dry_run,
                force=force,
            )
        if normalized in {"backup", "rollback"}:
            return self.backup_or_rollback(
                config=config,
                action=normalized,
                skill_id=skill_id,
                revision=revision,
                dry_run=dry_run,
                force=force,
            )
        if normalized == "feedback":
            return self.record_feedback(
                config=config,
                skill_id=skill_id,
                outcome=outcome,
                rationale=rationale,
                feedback_source=feedback_source,
                confidence=confidence,
            )
        if normalized in {"learn_procedure", "procedure_learn"}:
            return self.learn_procedure(
                config=config,
                title=title,
                trigger=trigger,
                steps=steps,
                expected_outcome=expected_outcome,
                rationale=rationale,
                tags=tags,
                allowed_tools=allowed_tools,
                evidence_refs=evidence_refs,
                source_ref=source_ref,
                outcome=outcome,
                feedback_source=feedback_source,
                confidence=confidence,
                dry_run=dry_run,
            )
        if normalized in {"procedures", "procedure_report"}:
            return self.procedure_report(config=config, status=outcome, limit=25)
        if normalized in {"promote_procedure", "procedure_promote"}:
            return self.promote_procedure(
                config=config,
                procedure_id=procedure_id or revision,
                skill_id=skill_id,
                dry_run=dry_run,
                force=force,
            )
        if normalized in {"reject_procedure", "procedure_reject"}:
            return self.reject_procedure(
                config=config,
                procedure_id=procedure_id or revision,
                rationale=rationale,
                dry_run=dry_run,
                force=force,
            )
        if normalized in {"restore_procedure", "procedure_restore"}:
            return self.restore_procedure(
                config=config,
                procedure_id=procedure_id or revision,
                rationale=rationale,
                dry_run=dry_run,
                force=force,
            )
        if normalized in {"maintenance", "maintain", "run_maintenance"}:
            return self.run_maintenance(config=config, dry_run=dry_run, force=force, source="tool")
        if normalized in {"create", "update", "patch", "write_file", "remove_file", "archive", "delete", "restore"}:
            return self.apply(
                config=config,
                action="archive" if normalized == "delete" else normalized,
                skill_id=skill_id,
                title=title,
                summary=summary,
                body=body,
                rationale=rationale,
                tags=tags,
                allowed_tools=allowed_tools,
                file_path=file_path,
                content=content,
                old_text=old_text,
                new_text=new_text,
                absorbed_into=absorbed_into,
                dry_run=dry_run,
                force=force,
            )
        if normalized == "pin":
            if not skill_id:
                return self._refusal("pin requires skill_id")
            return self.set_pinned(config=config, skill_id=skill_id, pinned=True)
        if normalized == "unpin":
            if not skill_id:
                return self._refusal("unpin requires skill_id")
            return self.set_pinned(config=config, skill_id=skill_id, pinned=False)
        return self._refusal(
            f"unsupported agent-managed skill action: {action}",
            supported_actions=SUPPORTED_ACTIONS,
            note="Use curator lifecycle actions for agent-created workspace skills. Direct enable/disable/uninstall stays in Gateway/Ops.",
        )

    def report(self, *, config: EffectiveConfig) -> dict[str, object]:
        usage = self._load_usage(config)
        recommendations = self._governance_recommendations(config=config, usage=usage)
        active_items = []
        archived_items = []
        core_items = []
        observe_items = []
        pinned = []
        stale_items = []
        for skill_id, item in sorted(usage.items()):
            state = str(item.get("state") or "active")
            tier = str(item.get("tier") or "active")
            last_feedback = item.get("last_feedback") if isinstance(item.get("last_feedback"), dict) else {}
            summary = {
                "skill_id": skill_id,
                "state": state,
                "tier": tier,
                "pinned": bool(item.get("pinned")),
                "view_count": int(item.get("view_count") or 0),
                "use_count": int(item.get("use_count") or 0),
                "patch_count": int(item.get("patch_count") or 0),
                "context_count": int(item.get("context_count") or 0),
                "utility_score": int(item.get("utility_score") or 0),
                "feedback_health": self._feedback_health(item),
                "last_feedback_source": last_feedback.get("source"),
                "last_feedback_confidence": last_feedback.get("confidence"),
                "template_path": item.get("template_path"),
                "last_activity_at": item.get("last_activity_at"),
            }
            if state == "archived":
                archived_items.append(summary)
            elif state == "stale":
                stale_items.append(summary)
                active_items.append(summary)
            else:
                active_items.append(summary)
            if state != "archived" and tier == "core":
                core_items.append(summary)
            if state != "archived" and tier == "observe":
                observe_items.append(summary)
            if item.get("pinned"):
                pinned.append(skill_id)
        lru = sorted(
            active_items,
            key=lambda item: str(item.get("last_activity_at") or item.get("skill_id") or ""),
        )[:5]
        return {
            "accepted": True,
            "mode": "curator",
            "counts": {
                "tracked": len(usage),
                "active": len(active_items),
                "stale": len(stale_items),
                "archived": len(archived_items),
                "core": len(core_items),
                "observe": len(observe_items),
                "pinned": len(pinned),
            },
            "pinned": pinned,
            "core": sorted(core_items, key=lambda item: (-int(item.get("utility_score") or 0), str(item.get("skill_id"))))[:10],
            "observe": sorted(observe_items, key=lambda item: (int(item.get("utility_score") or 0), str(item.get("skill_id"))))[:10],
            "least_recently_active": lru,
            "stale": stale_items[:10],
            "archived": archived_items[:10],
            "recommendations": recommendations,
        }

    def usage_snapshot(self, *, config: EffectiveConfig) -> dict[str, dict[str, object]]:
        return self._load_usage(config)

    def curate(self, *, config: EffectiveConfig, dry_run: bool = False, force: bool = False) -> dict[str, object]:
        usage = self._load_usage(config)
        now = datetime.now(timezone.utc)
        actions: list[dict[str, object]] = []
        for skill_id, item in sorted(usage.items()):
            pinned = bool(item.get("pinned"))
            if str(item.get("state") or "active") == "archived":
                continue
            if not self._is_workspace_skill_id(skill_id):
                continue
            utility_score = self._utility_score(item)
            item["utility_score"] = utility_score
            item["context_count"] = len([value for value in item.get("contexts") or [] if str(value).strip()])
            last_activity = self._parse_time(item.get("last_activity_at") or item.get("created_at"))
            if last_activity is None:
                continue
            age_days = max((now - last_activity).days, 0)
            if pinned and not force:
                age_days = 0
            if age_days >= DEFAULT_ARCHIVE_DAYS:
                actions.append(
                    {
                        "action": "archive",
                        "skill_id": skill_id,
                        "reason": f"inactive for {age_days} days",
                        "age_days": age_days,
                    }
                )
                continue
            elif age_days >= DEFAULT_STALE_DAYS and str(item.get("state") or "active") != "stale":
                actions.append(
                    {
                        "action": "mark_stale",
                        "skill_id": skill_id,
                        "reason": f"inactive for {age_days} days",
                        "age_days": age_days,
                    }
                )
                continue
            if utility_score >= int(config.skills_config.curator.core_score_threshold or 0) and str(item.get("tier") or "active") != "core":
                actions.append(
                    {
                        "action": "mark_core",
                        "skill_id": skill_id,
                        "reason": f"utility score {utility_score} reached core threshold",
                        "utility_score": utility_score,
                    }
                )
            elif (
                age_days >= int(config.skills_config.curator.observe_min_age_days or 0)
                and utility_score <= int(config.skills_config.curator.observe_score_threshold or 0)
                and str(item.get("tier") or "active") != "observe"
            ):
                actions.append(
                    {
                        "action": "mark_observe",
                        "skill_id": skill_id,
                        "reason": f"low utility score {utility_score} after {age_days} days",
                        "utility_score": utility_score,
                        "age_days": age_days,
                    }
                )
            if self._should_promote_template(config=config, skill_id=skill_id, item=item):
                actions.append(
                    {
                        "action": "promote_template",
                        "skill_id": skill_id,
                        "reason": "reused across contexts",
                        "template_path": REUSABLE_TEMPLATE_PATH,
                        "use_count": int(item.get("use_count") or 0),
                        "context_count": int(item.get("context_count") or 0),
                    }
                )
            review_lookup = (
                self._find_existing_review_proposal(config=config, skill_id=skill_id)
                if config.skills_config.curator.auto_review
                else None
            )
            if review_lookup is not None and review_lookup.scan_truncated:
                actions.append(
                    {
                        "action": "quality_plan",
                        "accepted": False,
                        "skill_id": skill_id,
                        "reason": "curator review proposal scan truncated before quality_plan",
                        **review_lookup.metadata("review_proposal"),
                    }
                )
                continue
            if (
                config.skills_config.curator.auto_review
                and self._should_plan_skill_review(
                    config=config,
                    skill_id=skill_id,
                    item=item,
                    existing_lookup=review_lookup,
                )
            ):
                if dry_run:
                    actions.append(
                        {
                            "action": "quality_plan",
                            "skill_id": skill_id,
                            "reason": "quality review signal",
                            "dry_run": True,
                        }
                    )
                    continue
                existing = review_lookup.proposal if review_lookup is not None else None
                planned = (
                    {"accepted": True, "proposal": existing, "reused": True}
                    if existing is not None
                    else self._build_skill_review_proposal(
                        config=config,
                        skill_id=skill_id,
                        rationale="Automatic curator quality review.",
                    )
                )
                if planned.get("accepted"):
                    proposal = planned["proposal"]
                    paths = {"proposal_path": str(self._review_proposals_root(config) / str(proposal["proposal_id"]) / "proposal.json")}
                    if not planned.get("reused"):
                        paths = self._write_review_proposal(config=config, proposal=proposal)
                        self._record_quality_plan_history(config=config, proposal=proposal)
                    item["last_review_proposal_id"] = proposal["proposal_id"]
                    item["last_quality_planned_at"] = utc_now_iso()
                    item["last_review_signal"] = self._review_signal_fingerprint(item)
                    actions.append(
                        {
                            "action": "quality_plan",
                            "skill_id": skill_id,
                            "proposal_id": proposal["proposal_id"],
                            "proposal_path": paths["proposal_path"],
                            "reused": bool(planned.get("reused")),
                            "reason": "quality review signal",
                        }
                    )
                else:
                    review_lookup = self._find_existing_review_proposal(config=config, skill_id=skill_id)
                    existing = None if review_lookup.scan_truncated else review_lookup.proposal
                    if existing is not None:
                        item["last_review_proposal_id"] = existing["proposal_id"]
                        item["last_quality_planned_at"] = utc_now_iso()
                        item["last_review_signal"] = self._review_signal_fingerprint(item)
                        actions.append(
                            {
                                "action": "quality_plan",
                                "skill_id": skill_id,
                                "proposal_id": existing["proposal_id"],
                                "proposal_path": str(self._review_proposals_root(config) / str(existing["proposal_id"]) / "proposal.json"),
                                "reused": True,
                                "reason": "quality review signal",
                            }
                        )
        duplicate_groups = self._duplicate_groups_with_fingerprints()
        for group in duplicate_groups:
            if not config.skills_config.curator.auto_merge:
                actions.append(
                    {
                        "action": "review_duplicates",
                        "skill_ids": group["skill_ids"],
                        "reason": "auto_merge disabled",
                    }
                )
                continue
            if dry_run:
                actions.append(
                    {
                        "action": "merge_plan",
                        "skill_ids": group["skill_ids"],
                        "fingerprint": group["fingerprint"],
                        "reason": "similar title/summary fingerprint",
                        "dry_run": True,
                    }
                )
                continue
            planned = self._build_duplicate_merge_proposal(config=config, group=group, force=force)
            if planned.get("accepted"):
                proposal = planned["proposal"]
                paths = self._write_merge_proposal(config=config, proposal=proposal)
                if not planned.get("reused"):
                    self._record_merge_plan_history(config=config, proposal=proposal)
                actions.append(
                    {
                        "action": "merge_plan",
                        "skill_ids": group["skill_ids"],
                        "primary_skill_id": proposal["primary_skill_id"],
                        "proposal_id": proposal["proposal_id"],
                        "proposal_path": paths["proposal_path"],
                        "reused": bool(planned.get("reused")),
                        "reason": "similar title/summary fingerprint",
                    }
                )
            else:
                actions.append(
                    {
                        "action": "review_duplicates",
                        "skill_ids": group["skill_ids"],
                        "reason": planned.get("error") or "similar title/summary fingerprint",
                        **_prefixed_items(planned, "merge_proposal_"),
                    }
                )
        recommendations = self._governance_recommendations(config=config, usage=usage, actions=actions)
        report = {
            "accepted": True,
            "dry_run": dry_run,
            "started_at": utc_now_iso(),
            "actions": actions,
            "recommendations": recommendations,
            "counts": {
                "actions": len(actions),
                "duplicates": len(duplicate_groups),
                "recommendations": len(recommendations),
            },
        }
        if not dry_run:
            for action in actions:
                if action["action"] == "mark_stale":
                    item = self._usage_item(usage, str(action["skill_id"]))
                    item["state"] = "stale"
                    item["stale_at"] = utc_now_iso()
                elif action["action"] == "mark_core":
                    item = self._usage_item(usage, str(action["skill_id"]))
                    item["tier"] = "core"
                    item["core_at"] = utc_now_iso()
                elif action["action"] == "mark_observe":
                    item = self._usage_item(usage, str(action["skill_id"]))
                    item["tier"] = "observe"
                    item["observed_at"] = utc_now_iso()
                elif action["action"] == "promote_template":
                    self._save_usage(config, usage)
                    self._promote_reusable_template(config=config, skill_id=str(action["skill_id"]))
                    usage = self._load_usage(config)
                elif action["action"] == "archive":
                    self._save_usage(config, usage)
                    self._apply_archive(
                        config=config,
                        change={"skill_id": action["skill_id"], "force": force},
                        dry_run=False,
                    )
                    usage = self._load_usage(config)
            self._save_usage(config, usage)
        run_paths = self._write_run_report(config=config, report=report)
        return report | run_paths

    def run_maintenance(
        self,
        *,
        config: EffectiveConfig,
        dry_run: bool = True,
        force: bool = False,
        source: str = "ops",
    ) -> dict[str, object]:
        started_at = utc_now_iso()
        if not config.skills_config.enabled:
            return {
                "accepted": True,
                "mode": "curator_maintenance",
                "status": "disabled",
                "dry_run": dry_run,
                "source": source,
                "reason": "skills_disabled",
                "actions": [],
                "actions_executed": {},
                "skipped_actions": {},
                "errors": [],
                "started_at": started_at,
                "finished_at": utc_now_iso(),
            }
        if not bool(config.skills_config.curator.maintenance_enabled):
            return {
                "accepted": True,
                "mode": "curator_maintenance",
                "status": "disabled",
                "dry_run": dry_run,
                "source": source,
                "reason": "maintenance_disabled",
                "actions": [],
                "actions_executed": {},
                "skipped_actions": {},
                "errors": [],
                "started_at": started_at,
                "finished_at": utc_now_iso(),
            }

        plan = self.curate(config=config, dry_run=True, force=force)
        candidate_actions = [
            dict(item)
            for item in plan.get("actions", [])
            if isinstance(item, dict)
        ]
        for recommendation in plan.get("recommendations", []):
            if not isinstance(recommendation, dict):
                continue
            candidate = self._maintenance_action_from_recommendation(recommendation)
            if candidate is not None:
                candidate_actions.append(candidate)
        candidate_actions = self._dedupe_maintenance_actions(candidate_actions)
        actions, skipped_actions = self._bound_maintenance_actions(config=config, actions=candidate_actions)
        results: list[dict[str, object]] = []
        errors: list[str] = []
        executed_counts: dict[str, int] = {}
        if not dry_run:
            for action in actions:
                result = self._execute_maintenance_action(config=config, action=action, force=force)
                results.append(result)
                action_name = str(action.get("action") or "unknown")
                if result.get("accepted") and (result.get("applied") is not False):
                    executed_counts[action_name] = executed_counts.get(action_name, 0) + 1
                else:
                    skipped_actions[action_name] = skipped_actions.get(action_name, 0) + 1
                    reason = result.get("error") or result.get("reason")
                    if reason:
                        errors.append(str(reason))
        report_after = self.report(config=config)
        finished_at = utc_now_iso()
        maintenance = {
            "accepted": True,
            "mode": "curator_maintenance",
            "status": "planned" if dry_run else ("completed" if not errors else "completed_with_errors"),
            "dry_run": dry_run,
            "source": source,
            "force": force,
            "started_at": started_at,
            "finished_at": finished_at,
            "plan_run_id": plan.get("run_id"),
            "plan_json_path": plan.get("run_json_path"),
            "plan_report_path": plan.get("report_path"),
            "actions": actions,
            "candidate_count": len(candidate_actions),
            "selected_count": len(actions),
            "skipped_count": sum(skipped_actions.values()),
            "results": results,
            "actions_executed": executed_counts,
            "skipped_actions": skipped_actions,
            "errors": errors,
            "recommendations": plan.get("recommendations") if isinstance(plan.get("recommendations"), list) else [],
            "counts": {
                "candidate_actions": len(candidate_actions),
                "selected_actions": len(actions),
                "results": len(results),
                "errors": len(errors),
            },
            "summary": report_after.get("counts") if isinstance(report_after.get("counts"), dict) else {},
        }
        run_paths = self._write_run_report(config=config, report=maintenance)
        maintenance.update(
            {
                "run_id": run_paths.get("run_id"),
                "run_json_path": run_paths.get("run_json_path"),
                "report_path": run_paths.get("report_path"),
            }
        )
        return maintenance

    def plan_duplicate_merge(
        self,
        *,
        config: EffectiveConfig,
        skill_id: str | None,
        absorbed_into: str | None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        planned = self._build_duplicate_merge_proposal(
            config=config,
            skill_id=skill_id,
            absorbed_into=absorbed_into,
            force=force,
        )
        if not planned.get("accepted"):
            return planned
        proposal = planned["proposal"]
        plan_metadata = _prefixed_items(planned, "merge_proposal_")
        if dry_run:
            return {
                "accepted": True,
                "mode": "curator_merge_plan",
                "dry_run": True,
                "proposal": proposal,
                "would_write_proposal": str(self._merge_proposals_root(config) / str(proposal["proposal_id"]) / "proposal.json"),
                "reused": bool(planned.get("reused")),
                **plan_metadata,
            }
        paths = self._write_merge_proposal(config=config, proposal=proposal)
        if not planned.get("reused"):
            self._record_merge_plan_history(config=config, proposal=proposal)
        return {
            "accepted": True,
            "mode": "curator_merge_plan",
            "proposal_id": proposal["proposal_id"],
            "proposal": proposal,
            "reused": bool(planned.get("reused")),
            **plan_metadata,
        } | paths

    def apply_duplicate_merge(
        self,
        *,
        config: EffectiveConfig,
        revision: str | None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        loaded = self._load_merge_proposal(config=config, revision=revision)
        if not loaded.get("accepted"):
            return loaded
        proposal = loaded["proposal"]
        primary_skill_id = str(proposal.get("primary_skill_id") or "")
        source_skill_ids = [str(item) for item in proposal.get("source_skill_ids") or []]
        if self._normalize_skill_id(primary_skill_id) is None or not self._is_workspace_skill_id(primary_skill_id):
            return self._refusal(f"primary skill '{primary_skill_id}' is not in the curator workspace root")
        if not source_skill_ids:
            return self._refusal("merge proposal has no source_skill_ids")
        for source_skill_id in source_skill_ids:
            if self._normalize_skill_id(source_skill_id) is None:
                return self._refusal(f"merge proposal contains unsafe skill_id '{source_skill_id}'")
            if not self._is_workspace_skill_id(source_skill_id):
                return self._refusal(f"source skill '{source_skill_id}' is not in the curator workspace root")
            if self._is_pinned(config=config, skill_id=source_skill_id) and not force:
                return self._refusal(f"source skill '{source_skill_id}' is pinned; pass force=true before merge_apply")
        primary_patch = proposal.get("primary_patch")
        primary_patch_result = self._apply_merge_primary_patch(
            config=config,
            primary_skill_id=primary_skill_id,
            primary_patch=primary_patch if isinstance(primary_patch, dict) else {},
            dry_run=dry_run,
        )
        if dry_run:
            return {
                "accepted": True,
                "mode": "curator_merge_apply",
                "dry_run": True,
                "proposal_id": proposal["proposal_id"],
                "primary_skill_id": primary_skill_id,
                "primary_patch_result": primary_patch_result,
                "would_archive": source_skill_ids,
            }

        archive_results = []
        if not primary_patch_result.get("accepted"):
            return primary_patch_result | {
                "mode": "curator_merge_apply",
                "proposal_id": proposal["proposal_id"],
            }
        for source_skill_id in source_skill_ids:
            archived = self.apply(
                config=config,
                action="archive",
                skill_id=source_skill_id,
                title=None,
                summary=None,
                body=None,
                rationale=f"Duplicate skill merged into {primary_skill_id}.",
                tags=None,
                allowed_tools=None,
                file_path=None,
                content=None,
                old_text=None,
                new_text=None,
                absorbed_into=primary_skill_id,
                dry_run=False,
                force=force,
            )
            if not archived.get("accepted"):
                return archived | {
                    "mode": "curator_merge_apply",
                    "proposal_id": proposal["proposal_id"],
                    "partial_archive_results": archive_results,
                }
            archive_results.append(archived)

        usage = self._load_usage(config)
        primary_item = self._usage_item(usage, primary_skill_id)
        merged_from = [str(item) for item in primary_item.get("merged_from") or []]
        primary_item["merged_from"] = sorted(set(merged_from + source_skill_ids))
        primary_item["last_activity_at"] = utc_now_iso()
        self._save_usage(config, usage)

        proposal["status"] = "applied"
        proposal["applied_at"] = utc_now_iso()
        proposal["force"] = force
        proposal["archive_results"] = archive_results
        proposal["primary_patch_result"] = primary_patch_result
        paths = self._write_merge_proposal(config=config, proposal=proposal)
        self._append_curator_change(
            config=config,
            change={
                "change_id": self._change_id("merge_apply", primary_skill_id),
                "created_at": utc_now_iso(),
                "change_action": "merge_apply",
                "skill_id": primary_skill_id,
                "proposal_id": proposal["proposal_id"],
                "source_skill_ids": source_skill_ids,
                "force": force,
                "result": {
                    "archived_skill_ids": source_skill_ids,
                    "primary_patch_result": primary_patch_result,
                },
            },
        )
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=primary_skill_id,
                action="curator_merge_apply",
                created_at=utc_now_iso(),
                detail={
                    "proposal_id": proposal["proposal_id"],
                    "source_skill_ids": source_skill_ids,
                    "archive_results": archive_results,
                    "primary_patch_result": primary_patch_result,
                    "force": force,
                },
            ),
        )
        return {
            "accepted": True,
            "applied": True,
            "mode": "curator_merge_apply",
            "proposal_id": proposal["proposal_id"],
            "primary_skill_id": primary_skill_id,
            "primary_patch_result": primary_patch_result,
            "archived_skill_ids": source_skill_ids,
            "archive_results": archive_results,
        } | paths

    def plan_skill_review(
        self,
        *,
        config: EffectiveConfig,
        skill_id: str | None,
        rationale: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, object]:
        proposal_result = self._build_skill_review_proposal(
            config=config,
            skill_id=skill_id,
            rationale=rationale,
        )
        if not proposal_result.get("accepted"):
            return proposal_result
        proposal = proposal_result["proposal"]
        if dry_run:
            return {
                "accepted": True,
                "mode": "curator_quality_plan",
                "dry_run": True,
                "proposal": proposal,
                "would_write_proposal": str(self._review_proposals_root(config) / str(proposal["proposal_id"]) / "proposal.json"),
            }
        paths = self._write_review_proposal(config=config, proposal=proposal)
        self._record_quality_plan_history(config=config, proposal=proposal)
        return {
            "accepted": True,
            "mode": "curator_quality_plan",
            "proposal_id": proposal["proposal_id"],
            "proposal": proposal,
        } | paths

    def apply_skill_review(
        self,
        *,
        config: EffectiveConfig,
        revision: str | None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        loaded = self._load_review_proposal(config=config, revision=revision)
        if not loaded.get("accepted"):
            return loaded
        proposal = loaded["proposal"]
        skill_id = str(proposal.get("skill_id") or "")
        if self._normalize_skill_id(skill_id) is None or not self._is_workspace_skill_id(skill_id):
            return self._refusal(f"skill '{skill_id}' is not in the curator workspace root")
        if self._is_pinned(config=config, skill_id=skill_id) and not force:
            return self._refusal(f"skill '{skill_id}' is pinned; pass force=true before review_apply")
        patch = proposal.get("patch") if isinstance(proposal.get("patch"), dict) else {}
        patch_result = self._apply_review_patch(
            config=config,
            skill_id=skill_id,
            patch=patch,
            dry_run=dry_run,
        )
        if dry_run:
            return {
                "accepted": True,
                "mode": "curator_review_apply",
                "dry_run": True,
                "proposal_id": proposal["proposal_id"],
                "skill_id": skill_id,
                "patch_result": patch_result,
            }
        if not patch_result.get("accepted"):
            return patch_result | {
                "mode": "curator_review_apply",
                "proposal_id": proposal["proposal_id"],
            }
        proposal["status"] = "applied"
        proposal["applied_at"] = utc_now_iso()
        proposal["force"] = force
        proposal["patch_result"] = patch_result
        paths = self._write_review_proposal(config=config, proposal=proposal)
        self._append_curator_change(
            config=config,
            change={
                "change_id": self._change_id("review_apply", skill_id),
                "created_at": utc_now_iso(),
                "change_action": "review_apply",
                "skill_id": skill_id,
                "proposal_id": proposal["proposal_id"],
                "force": force,
                "result": {
                    "patch_result": patch_result,
                },
            },
        )
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=skill_id,
                action="curator_review_apply",
                created_at=utc_now_iso(),
                detail={
                    "proposal_id": proposal["proposal_id"],
                    "patch_result": patch_result,
                    "force": force,
                },
            ),
        )
        return {
            "accepted": True,
            "applied": bool(patch_result.get("applied")),
            "mode": "curator_review_apply",
            "proposal_id": proposal["proposal_id"],
            "skill_id": skill_id,
            "patch_result": patch_result,
        } | paths

    def automation_status(self, *, config: EffectiveConfig, now: datetime | None = None) -> dict[str, object]:
        state = self._load_automation_state(config)
        now = self._utc_datetime(now or datetime.now(timezone.utc))
        next_run = self._next_automation_run_at(config=config, now=now, state=state)
        return {
            "enabled": bool(config.skills_config.enabled and config.skills_config.curator.automation_enabled),
            "last_run_at": state.get("last_run_at"),
            "last_status": state.get("last_status"),
            "last_reason": state.get("last_reason"),
            "last_run_id": state.get("last_run_id"),
            "last_recommendation_count": int(state.get("last_recommendation_count") or 0),
            "last_recommendations": state.get("last_recommendations") if isinstance(state.get("last_recommendations"), list) else [],
            "next_run_at": next_run.isoformat(),
            "schedule": config.skills_config.curator.schedule,
            "auto_merge": bool(config.skills_config.curator.auto_merge),
            "pin_protection": bool(config.skills_config.curator.pin_protection),
            "interval_seconds": self._curator_interval_seconds(config),
            "tick_seconds": max(int(config.skills_config.curator.tick_seconds or 0), 10),
            "min_idle_hours": float(config.skills_config.curator.min_idle_hours or 0),
            "dry_run": bool(config.skills_config.curator.dry_run),
            "force": bool(config.skills_config.curator.force),
        }

    def run_automation_if_due(
        self,
        *,
        config: EffectiveConfig,
        now: datetime | None = None,
        force_run: bool = False,
    ) -> SkillCuratorAutomationResult:
        if not config.skills_config.enabled:
            return SkillCuratorAutomationResult(ran=False, reason="skills_disabled")
        if not config.skills_config.curator.automation_enabled and not force_run:
            return SkillCuratorAutomationResult(ran=False, reason="automation_disabled")
        now = self._utc_datetime(now or datetime.now(timezone.utc))
        state = self._load_automation_state(config)
        next_run = self._next_automation_run_at(config=config, now=now, state=state)
        if not force_run and next_run > now:
            return SkillCuratorAutomationResult(ran=False, reason="not_due", next_run_at=next_run.isoformat())
        if not force_run and self._last_skill_activity_within_idle_window(config=config, now=now):
            return SkillCuratorAutomationResult(ran=False, reason="not_idle", next_run_at=next_run.isoformat())

        report = self.run_maintenance(
            config=config,
            dry_run=bool(config.skills_config.curator.dry_run),
            force=bool(config.skills_config.curator.force),
            source="automation",
        )
        recommendations = report.get("recommendations") if isinstance(report.get("recommendations"), list) else []
        self._save_automation_state(
            config,
            {
                "last_run_at": now.isoformat(),
                "last_status": "completed" if report.get("accepted") else "refused",
                "last_reason": "forced" if force_run else "due",
                "last_run_id": report.get("run_id"),
                "last_counts": report.get("counts"),
                "last_recommendation_count": len(recommendations),
                "last_recommendations": recommendations[:5],
            },
        )
        next_after_run = self._next_automation_run_after(config=config, completed_at=now)
        return SkillCuratorAutomationResult(
            ran=True,
            reason="forced" if force_run else "due",
            report=report,
            next_run_at=next_after_run.isoformat(),
        )

    def apply(
        self,
        *,
        config: EffectiveConfig,
        action: str,
        skill_id: str | None,
        title: str | None,
        summary: str | None,
        body: str | None,
        rationale: str | None,
        tags: list[str] | tuple[str, ...] | None,
        allowed_tools: list[str] | tuple[str, ...] | None,
        file_path: str | None,
        content: str | None,
        old_text: str | None,
        new_text: str | None,
        absorbed_into: str | None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        normalized_skill_id = self._normalize_skill_id(skill_id)
        if normalized_skill_id is None:
            return self._refusal("skill_id is required and must be a safe slug")
        if self._is_pinned(config=config, skill_id=normalized_skill_id) and action in {"update", "patch", "write_file", "remove_file", "archive"} and not force:
            return self._refusal(f"skill '{normalized_skill_id}' is pinned; unpin or pass force=true before {action}")
        change = {
            "change_id": self._change_id(action, normalized_skill_id),
            "created_at": utc_now_iso(),
            "change_action": action,
            "skill_id": normalized_skill_id,
            "title": (title or normalized_skill_id).strip(),
            "summary": (summary or "").strip(),
            "body": (body or "").strip(),
            "rationale": (rationale or "").strip()[:MAX_RATIONALE_CHARS],
            "tags": self._safe_string_list(tags),
            "allowed_tools": self._safe_string_list(allowed_tools),
            "file_path": (file_path or "").strip(),
            "content": content or "",
            "old_text": old_text or "",
            "new_text": new_text or "",
            "absorbed_into": (absorbed_into or "").strip(),
            "actor": "agent",
            "mode": "curator_direct",
            "dry_run": dry_run,
            "force": force,
        }
        if action in {"create", "update"}:
            if not str(change["title"]).strip():
                return self._refusal("title is required for create/update")
            if not str(change["summary"]).strip():
                return self._refusal("summary is required for create/update")
            if not str(change["body"]).strip():
                return self._refusal("body is required for create/update")
            if len(str(change["body"])) > MAX_BODY_CHARS:
                return self._refusal(f"body exceeds {MAX_BODY_CHARS} chars")
            if len(str(change["summary"])) > MAX_SUMMARY_CHARS:
                return self._refusal(f"summary exceeds {MAX_SUMMARY_CHARS} chars")
        self._validate_change_shape(change)
        if action in {"create", "update"}:
            result = self._apply_write(config=config, change=change, dry_run=dry_run)
        elif action == "patch":
            result = self._apply_patch(config=config, change=change, dry_run=dry_run)
        elif action == "write_file":
            result = self._apply_support_file_write(config=config, change=change, dry_run=dry_run)
        elif action == "remove_file":
            result = self._apply_support_file_remove(config=config, change=change, dry_run=dry_run)
        elif action == "archive":
            result = self._apply_archive(config=config, change=change, dry_run=dry_run)
        elif action == "restore":
            result = self._apply_restore(config=config, change=change, dry_run=dry_run)
        else:
            return self._refusal(f"unsupported curator action '{action}'")
        if dry_run or not result.get("accepted"):
            return result
        self._append_curator_change(config=config, change=change | {"result": result})
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=normalized_skill_id,
                action=f"curator_{action}",
                created_at=utc_now_iso(),
                detail={
                    "change_action": action,
                    "change_id": change["change_id"],
                    "force": force,
                    "result": result,
                },
            ),
        )
        return result | {"change": change}

    def set_pinned(self, *, config: EffectiveConfig, skill_id: str, pinned: bool) -> dict[str, object]:
        normalized = self._normalize_skill_id(skill_id)
        if normalized is None:
            return self._refusal("skill_id is required and must be a safe slug")
        usage = self._load_usage(config)
        item = self._usage_item(usage, normalized)
        item["pinned"] = pinned
        item["last_activity_at"] = utc_now_iso()
        self._save_usage(config, usage)
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=normalized,
                action="curator_pin" if pinned else "curator_unpin",
                created_at=utc_now_iso(),
                detail={"pinned": pinned},
            ),
        )
        return {"accepted": True, "skill_id": normalized, "pinned": pinned}

    def record_feedback(
        self,
        *,
        config: EffectiveConfig,
        skill_id: str | None,
        outcome: str | None,
        rationale: str | None = None,
        feedback_source: str | None = None,
        confidence: float | int | None = None,
    ) -> dict[str, object]:
        normalized = self._normalize_skill_id(skill_id)
        if normalized is None:
            return self._refusal("feedback requires a safe skill_id")
        if not self._is_workspace_skill_id(normalized):
            return self._refusal(f"skill '{normalized}' is not in the curator workspace root")
        normalized_outcome = str(outcome or "").strip().lower()
        if normalized_outcome not in {"success", "failure", "neutral"}:
            return self._refusal("feedback outcome must be success, failure, or neutral")
        normalized_source = self._normalize_feedback_source(feedback_source)
        normalized_confidence = self._normalize_feedback_confidence(confidence)
        if normalized_confidence is None:
            return self._refusal("feedback confidence must be a number between 0 and 1")
        usage = self._load_usage(config)
        item = self._usage_item(usage, normalized)
        now = utc_now_iso()
        item["feedback_count"] = int(item.get("feedback_count") or 0) + 1
        by_source = item.get("feedback_by_source") if isinstance(item.get("feedback_by_source"), dict) else {}
        by_source[normalized_source] = int(by_source.get(normalized_source) or 0) + 1
        item["feedback_by_source"] = by_source
        confidence_totals = item.get("confidence_totals") if isinstance(item.get("confidence_totals"), dict) else {}
        confidence_totals[normalized_outcome] = round(float(confidence_totals.get(normalized_outcome) or 0.0) + normalized_confidence, 4)
        item["confidence_totals"] = confidence_totals
        if normalized_outcome == "success":
            item["success_count"] = int(item.get("success_count") or 0) + 1
        elif normalized_outcome == "failure":
            item["failure_count"] = int(item.get("failure_count") or 0) + 1
        else:
            item["neutral_count"] = int(item.get("neutral_count") or 0) + 1
        item["last_feedback"] = {
            "outcome": normalized_outcome,
            "source": normalized_source,
            "confidence": normalized_confidence,
            "rationale": (rationale or "").strip()[:MAX_RATIONALE_CHARS],
            "created_at": now,
        }
        item["last_activity_at"] = now
        item["utility_score"] = self._utility_score(item)
        self._save_usage(config, usage)
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=normalized,
                action="curator_feedback",
                created_at=now,
                detail={
                    "outcome": normalized_outcome,
                    "source": normalized_source,
                    "confidence": normalized_confidence,
                    "rationale": (rationale or "").strip()[:MAX_RATIONALE_CHARS],
                    "utility_score": item["utility_score"],
                },
            ),
        )
        return {
            "accepted": True,
            "skill_id": normalized,
            "outcome": normalized_outcome,
            "feedback_source": normalized_source,
            "confidence": normalized_confidence,
            "utility_score": item["utility_score"],
            "feedback_count": item["feedback_count"],
        }

    def learn_procedure(
        self,
        *,
        config: EffectiveConfig,
        title: str | None,
        trigger: str | None,
        steps: list[str] | tuple[str, ...] | None,
        expected_outcome: str | None,
        rationale: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        allowed_tools: list[str] | tuple[str, ...] | None = None,
        evidence_refs: list[str] | tuple[str, ...] | None = None,
        source_ref: str | None = None,
        outcome: str | None = None,
        feedback_source: str | None = None,
        confidence: float | int | None = None,
        dry_run: bool = False,
    ) -> dict[str, object]:
        normalized_title = (title or "Reusable Procedure").strip()[:120]
        normalized_trigger = (trigger or "").strip()[:MAX_PROCEDURE_TRIGGER_CHARS]
        normalized_steps = self._normalize_procedure_steps(steps)
        if not normalized_trigger:
            return self._refusal("learn_procedure requires trigger")
        if not normalized_steps:
            return self._refusal("learn_procedure requires at least one step")
        normalized_confidence = self._normalize_feedback_confidence(confidence)
        if normalized_confidence is None:
            return self._refusal("confidence must be a number between 0 and 1")
        normalized_outcome = self._normalize_procedure_outcome(outcome)
        if normalized_outcome is None:
            return self._refusal("procedure outcome must be success, failure, or neutral")
        normalized_source = self._normalize_feedback_source(feedback_source)
        normalized_evidence = self._safe_string_list(evidence_refs)[:20]
        normalized_tags = self._safe_string_list(tags)[:12]
        normalized_tools = self._safe_string_list(allowed_tools)[:20]
        fingerprint = self._procedure_fingerprint(
            title=normalized_title,
            trigger=normalized_trigger,
            steps=normalized_steps,
            allowed_tools=normalized_tools,
        )
        legacy_fingerprint = self._legacy_procedure_fingerprint(
            title=normalized_title,
            trigger=normalized_trigger,
            steps=normalized_steps,
        )
        procedure_id = f"proc-{fingerprint[:16]}"
        legacy_procedure_id = f"proc-{legacy_fingerprint[:16]}"
        now = utc_now_iso()
        procedures = self._load_procedures(config)
        existing = procedures.get(procedure_id)
        if existing is None and legacy_procedure_id != procedure_id:
            existing = procedures.get(legacy_procedure_id)
            if existing is not None:
                procedure_id = legacy_procedure_id
                fingerprint = str(existing.get("fingerprint") or legacy_fingerprint)
        if existing is None:
            similar_id, similar_candidate = self._find_similar_procedure(
                procedures,
                trigger=normalized_trigger,
                steps=normalized_steps,
                allowed_tools=normalized_tools,
            )
            if similar_id and similar_candidate is not None:
                existing = similar_candidate
                procedure_id = similar_id
                fingerprint = str(existing.get("fingerprint") or fingerprint)
        if existing is not None and str(existing.get("status") or "candidate") == "rejected":
            candidate = dict(existing)
            candidate["last_seen_at"] = now
            candidate["updated_at"] = now
            candidate["rejected_reinforcement_count"] = int(candidate.get("rejected_reinforcement_count") or 0) + 1
            self._record_procedure_outcome(
                candidate,
                outcome=normalized_outcome,
                source=normalized_source,
                confidence=normalized_confidence,
                rationale=rationale,
                now=now,
            )
            source_refs = self._safe_string_list(candidate.get("source_refs") if isinstance(candidate.get("source_refs"), list) else [])
            if source_ref:
                source_refs.append(source_ref)
            candidate["source_refs"] = sorted(set(source_refs))[:40]
            candidate = self._finalize_procedure_candidate(candidate)
            if dry_run:
                return {
                    "accepted": True,
                    "dry_run": True,
                    "mode": "curator_procedure_learn",
                    "procedure_id": procedure_id,
                    "reinforced": False,
                    "skipped": True,
                    "reason": "procedure candidate was previously rejected",
                    "candidate": candidate,
                    "would_write": str(self._procedures_path(config)),
                }
            procedures[procedure_id] = candidate
            self._save_procedures(config, procedures)
            self._append_curator_change(
                config=config,
                change={
                    "change_id": self._change_id("learn_procedure_skipped", procedure_id),
                    "created_at": now,
                    "change_action": "learn_procedure_skipped",
                    "procedure_id": procedure_id,
                    "reason": "procedure candidate was previously rejected",
                    "result": {
                        "rejected_reinforcement_count": candidate["rejected_reinforcement_count"],
                        "source_ref": source_ref,
                    },
                },
            )
            self.governance.record_history(
                config,
                SkillGovernanceRecord(
                    skill_id=procedure_id,
                    action="curator_rejected_procedure_seen",
                    created_at=now,
                    detail={
                        "procedure_id": procedure_id,
                        "source_ref": source_ref,
                        "rejected_reinforcement_count": candidate["rejected_reinforcement_count"],
                    },
                ),
            )
            return {
                "accepted": True,
                "mode": "curator_procedure_learn",
                "procedure_id": procedure_id,
                "reinforced": False,
                "skipped": True,
                "reason": "procedure candidate was previously rejected",
                "candidate": candidate,
                "procedures_path": str(self._procedures_path(config)),
            }
        if existing is None:
            candidate = {
                "procedure_id": procedure_id,
                "fingerprint": fingerprint,
                "status": "candidate",
                "title": normalized_title,
                "trigger": normalized_trigger,
                "steps": normalized_steps,
                "expected_outcome": (expected_outcome or "").strip()[:MAX_PROCEDURE_OUTCOME_CHARS],
                "rationale": (rationale or "").strip()[:MAX_RATIONALE_CHARS],
                "tags": normalized_tags,
                "allowed_tools": normalized_tools,
                "evidence_refs": normalized_evidence,
                "source_refs": self._safe_string_list([source_ref]) if source_ref else [],
                "confidence": normalized_confidence,
                "frequency": 1,
                "outcome_counts": {},
                "confidence_totals": {},
                "source_counts": {},
                "created_at": now,
                "last_seen_at": now,
                "updated_at": now,
                "promoted_skill_id": None,
                "promoted_at": None,
            }
            self._record_procedure_outcome(
                candidate,
                outcome=normalized_outcome,
                source=normalized_source,
                confidence=normalized_confidence,
                rationale=rationale,
                now=now,
            )
            reinforced = False
        else:
            candidate = dict(existing)
            candidate["title"] = str(candidate.get("title") or normalized_title)[:120]
            candidate["trigger"] = str(candidate.get("trigger") or normalized_trigger)[:MAX_PROCEDURE_TRIGGER_CHARS]
            candidate["steps"] = self._normalize_procedure_steps(candidate.get("steps") if isinstance(candidate.get("steps"), list) else normalized_steps)
            if expected_outcome and not str(candidate.get("expected_outcome") or "").strip():
                candidate["expected_outcome"] = expected_outcome.strip()[:MAX_PROCEDURE_OUTCOME_CHARS]
            candidate["frequency"] = int(candidate.get("frequency") or 0) + 1
            if normalized_outcome == "success":
                candidate["confidence"] = round(max(float(candidate.get("confidence") or 0.0), normalized_confidence), 4)
            candidate["tags"] = sorted(set(self._safe_string_list(candidate.get("tags") if isinstance(candidate.get("tags"), list) else []) + normalized_tags))[:12]
            candidate["allowed_tools"] = sorted(set(self._safe_string_list(candidate.get("allowed_tools") if isinstance(candidate.get("allowed_tools"), list) else []) + normalized_tools))[:20]
            candidate["evidence_refs"] = sorted(set(self._safe_string_list(candidate.get("evidence_refs") if isinstance(candidate.get("evidence_refs"), list) else []) + normalized_evidence))[:40]
            source_refs = self._safe_string_list(candidate.get("source_refs") if isinstance(candidate.get("source_refs"), list) else [])
            if source_ref:
                source_refs.append(source_ref)
            candidate["source_refs"] = sorted(set(source_refs))[:40]
            self._record_procedure_outcome(
                candidate,
                outcome=normalized_outcome,
                source=normalized_source,
                confidence=normalized_confidence,
                rationale=rationale,
                now=now,
            )
            candidate["last_seen_at"] = now
            candidate["updated_at"] = now
            reinforced = True
        candidate = self._finalize_procedure_candidate(candidate)
        if dry_run:
            return {
                "accepted": True,
                "dry_run": True,
                "mode": "curator_procedure_learn",
                "procedure_id": procedure_id,
                "reinforced": reinforced,
                "candidate": candidate,
                "would_write": str(self._procedures_path(config)),
            }
        procedures[procedure_id] = candidate
        self._save_procedures(config, procedures)
        self._append_curator_change(
            config=config,
            change={
                "change_id": self._change_id("learn_procedure", procedure_id),
                "created_at": now,
                "change_action": "learn_procedure",
                "procedure_id": procedure_id,
                "reinforced": reinforced,
                "result": {
                    "frequency": candidate["frequency"],
                    "strength": candidate["strength"],
                    "confidence": candidate["confidence"],
                },
            },
        )
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=procedure_id,
                action="curator_learn_procedure",
                created_at=now,
                detail={
                    "procedure_id": procedure_id,
                    "reinforced": reinforced,
                    "frequency": candidate["frequency"],
                    "strength": candidate["strength"],
                    "source_ref": source_ref,
                },
            ),
        )
        return {
            "accepted": True,
            "mode": "curator_procedure_learn",
            "procedure_id": procedure_id,
            "reinforced": reinforced,
            "candidate": candidate,
            "procedures_path": str(self._procedures_path(config)),
        }

    def procedure_report(self, *, config: EffectiveConfig, status: str | None = None, limit: int = 25) -> dict[str, object]:
        procedures = self._load_procedures(config)
        normalized_status = (status or "").strip().lower()
        items = list(procedures.values())
        items = [self._finalize_procedure_candidate(dict(item)) for item in items]
        if normalized_status and normalized_status != "all":
            items = [item for item in items if str(item.get("status") or "candidate") == normalized_status]
        elif not normalized_status:
            items = [item for item in items if str(item.get("status") or "candidate") != "rejected"]
        items.sort(
            key=lambda item: (
                -float(item.get("strength") or 0.0),
                -int(item.get("frequency") or 0),
                str(item.get("title") or ""),
            )
        )
        returned = items[: max(1, min(limit, 100))]
        return {
            "accepted": True,
            "mode": "curator_procedures",
            "counts": {
                "total": len(procedures),
                "returned": len(returned),
                "promotable": len([item for item in items if self._procedure_promotable(item)]),
                "promoted": len([item for item in procedures.values() if str(item.get("status") or "") == "promoted"]),
                "rejected": len([item for item in procedures.values() if str(item.get("status") or "") == "rejected"]),
            },
            "items": returned,
            "truncated": len(items) > len(returned),
        }

    def promote_procedure(
        self,
        *,
        config: EffectiveConfig,
        procedure_id: str | None,
        skill_id: str | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        normalized_procedure_id = (procedure_id or "").strip()
        if not normalized_procedure_id:
            return self._refusal("promote_procedure requires procedure_id")
        procedures = self._load_procedures(config)
        candidate = procedures.get(normalized_procedure_id)
        if candidate is None:
            return self._refusal(f"unknown procedure_id '{normalized_procedure_id}'")
        candidate = self._finalize_procedure_candidate(dict(candidate))
        if str(candidate.get("status") or "candidate") == "rejected" and not force:
            return self._refusal(f"procedure_id '{normalized_procedure_id}' was rejected; pass force=true to override")
        normalized_skill_id = self._normalize_skill_id(skill_id) if skill_id else self._slug_skill_id(str(candidate.get("title") or normalized_procedure_id))
        if normalized_skill_id is None:
            return self._refusal("skill_id must be a safe slug")
        exists = self._is_workspace_skill_id(normalized_skill_id)
        if exists and not force:
            return self._refusal(f"skill '{normalized_skill_id}' already exists; pass force=true before promoting into it")
        body = self._render_procedure_skill_body(candidate)
        result = self.apply(
            config=config,
            action="update" if exists else "create",
            skill_id=normalized_skill_id,
            title=str(candidate.get("title") or normalized_skill_id),
            summary=str(candidate.get("trigger") or "Reusable learned procedure")[:MAX_SUMMARY_CHARS],
            body=body,
            rationale=f"Promoted procedural candidate {normalized_procedure_id}.",
            tags=self._safe_string_list(candidate.get("tags") if isinstance(candidate.get("tags"), list) else []) or ["learned-procedure"],
            allowed_tools=self._safe_string_list(candidate.get("allowed_tools") if isinstance(candidate.get("allowed_tools"), list) else []),
            file_path=None,
            content=None,
            old_text=None,
            new_text=None,
            absorbed_into=None,
            dry_run=dry_run,
            force=force,
        )
        if dry_run or not result.get("accepted"):
            return {
                "mode": "curator_procedure_promote",
                "procedure_id": normalized_procedure_id,
                "skill_id": normalized_skill_id,
            } | result
        now = utc_now_iso()
        candidate["status"] = "promoted"
        candidate["promoted_skill_id"] = normalized_skill_id
        candidate["promoted_at"] = now
        candidate["updated_at"] = now
        procedures[normalized_procedure_id] = candidate
        self._save_procedures(config, procedures)
        self._append_curator_change(
            config=config,
            change={
                "change_id": self._change_id("promote_procedure", normalized_skill_id),
                "created_at": now,
                "change_action": "promote_procedure",
                "procedure_id": normalized_procedure_id,
                "skill_id": normalized_skill_id,
                "result": {"skill_path": result.get("path"), "updated": exists},
            },
        )
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=normalized_skill_id,
                action="curator_promote_procedure",
                created_at=now,
                detail={
                    "procedure_id": normalized_procedure_id,
                    "strength": candidate.get("strength"),
                    "frequency": candidate.get("frequency"),
                },
            ),
        )
        return {
            "accepted": True,
            "mode": "curator_procedure_promote",
            "procedure_id": normalized_procedure_id,
            "skill_id": normalized_skill_id,
            "candidate": candidate,
            "result": result,
        }

    def reject_procedure(
        self,
        *,
        config: EffectiveConfig,
        procedure_id: str | None,
        rationale: str | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        normalized_procedure_id = (procedure_id or "").strip()
        if not normalized_procedure_id:
            return self._refusal("reject_procedure requires procedure_id")
        procedures = self._load_procedures(config)
        candidate = procedures.get(normalized_procedure_id)
        if candidate is None:
            return self._refusal(f"unknown procedure_id '{normalized_procedure_id}'")
        current_status = str(candidate.get("status") or "candidate")
        if current_status == "promoted" and not force:
            return self._refusal(f"procedure_id '{normalized_procedure_id}' was already promoted; pass force=true to mark it rejected")
        now = utc_now_iso()
        next_candidate = dict(candidate)
        next_candidate["status"] = "rejected"
        next_candidate["rejected_at"] = now
        next_candidate["rejection_reason"] = (rationale or "").strip()[:MAX_RATIONALE_CHARS]
        next_candidate["updated_at"] = now
        if dry_run:
            return {
                "accepted": True,
                "dry_run": True,
                "mode": "curator_procedure_reject",
                "procedure_id": normalized_procedure_id,
                "candidate": next_candidate,
                "would_write": str(self._procedures_path(config)),
            }
        procedures[normalized_procedure_id] = next_candidate
        self._save_procedures(config, procedures)
        self._append_curator_change(
            config=config,
            change={
                "change_id": self._change_id("reject_procedure", normalized_procedure_id),
                "created_at": now,
                "change_action": "reject_procedure",
                "procedure_id": normalized_procedure_id,
                "previous_status": current_status,
                "result": {
                    "status": "rejected",
                    "reason": next_candidate["rejection_reason"],
                },
            },
        )
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=normalized_procedure_id,
                action="curator_reject_procedure",
                created_at=now,
                detail={
                    "procedure_id": normalized_procedure_id,
                    "previous_status": current_status,
                    "reason": next_candidate["rejection_reason"],
                },
            ),
        )
        return {
            "accepted": True,
            "mode": "curator_procedure_reject",
            "procedure_id": normalized_procedure_id,
            "candidate": next_candidate,
            "procedures_path": str(self._procedures_path(config)),
        }

    def restore_procedure(
        self,
        *,
        config: EffectiveConfig,
        procedure_id: str | None,
        rationale: str | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        normalized_procedure_id = (procedure_id or "").strip()
        if not normalized_procedure_id:
            return self._refusal("restore_procedure requires procedure_id")
        procedures = self._load_procedures(config)
        candidate = procedures.get(normalized_procedure_id)
        if candidate is None:
            return self._refusal(f"unknown procedure_id '{normalized_procedure_id}'")
        current_status = str(candidate.get("status") or "candidate")
        if current_status == "promoted" and not force:
            return self._refusal(f"procedure_id '{normalized_procedure_id}' was already promoted; pass force=true to restore it")
        if current_status != "rejected" and not force:
            return self._refusal(f"procedure_id '{normalized_procedure_id}' is not rejected")
        now = utc_now_iso()
        next_candidate = dict(candidate)
        next_candidate["status"] = "candidate"
        next_candidate["restored_at"] = now
        next_candidate["restore_reason"] = (rationale or "").strip()[:MAX_RATIONALE_CHARS]
        next_candidate["updated_at"] = now
        next_candidate.pop("rejected_at", None)
        next_candidate.pop("rejection_reason", None)
        finalized = self._finalize_procedure_candidate(dict(next_candidate))
        if dry_run:
            return {
                "accepted": True,
                "dry_run": True,
                "mode": "curator_procedure_restore",
                "procedure_id": normalized_procedure_id,
                "candidate": finalized,
                "would_write": str(self._procedures_path(config)),
            }
        procedures[normalized_procedure_id] = next_candidate
        self._save_procedures(config, procedures)
        self._append_curator_change(
            config=config,
            change={
                "change_id": self._change_id("restore_procedure", normalized_procedure_id),
                "created_at": now,
                "change_action": "restore_procedure",
                "procedure_id": normalized_procedure_id,
                "previous_status": current_status,
                "result": {
                    "status": "candidate",
                    "reason": next_candidate["restore_reason"],
                },
            },
        )
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=normalized_procedure_id,
                action="curator_restore_procedure",
                created_at=now,
                detail={
                    "procedure_id": normalized_procedure_id,
                    "previous_status": current_status,
                    "reason": next_candidate["restore_reason"],
                },
            ),
        )
        return {
            "accepted": True,
            "mode": "curator_procedure_restore",
            "procedure_id": normalized_procedure_id,
            "candidate": finalized,
            "procedures_path": str(self._procedures_path(config)),
        }

    def record_view(self, *, config: EffectiveConfig, manifest: SkillManifest) -> None:
        if not self._is_curatable_manifest(manifest):
            return
        usage = self._load_usage(config)
        item = self._usage_item(usage, manifest.skill_id)
        now = utc_now_iso()
        item["view_count"] = int(item.get("view_count") or 0) + 1
        item["last_viewed_at"] = now
        item["last_activity_at"] = now
        self._save_usage(config, usage)

    def record_use(self, *, config: EffectiveConfig, manifest: SkillManifest, fingerprint: str | None = None) -> None:
        if not self._is_curatable_manifest(manifest):
            return
        usage = self._load_usage(config)
        item = self._usage_item(usage, manifest.skill_id)
        now = utc_now_iso()
        item["use_count"] = int(item.get("use_count") or 0) + 1
        contexts = [str(value) for value in item.get("contexts") or [] if str(value).strip()]
        normalized_fingerprint = str(fingerprint or "").strip()
        if normalized_fingerprint and normalized_fingerprint not in contexts:
            contexts.append(normalized_fingerprint)
        item["contexts"] = sorted(contexts)
        item["context_count"] = len(contexts)
        item["last_used_at"] = now
        item["last_activity_at"] = now
        self._save_usage(config, usage)

    def _apply_write(self, *, config: EffectiveConfig, change: dict[str, object], dry_run: bool) -> dict[str, object]:
        skill_id = str(change["skill_id"])
        target_dir = self._workspace_root() / skill_id
        skill_text = self._render_skill_text(change)
        if dry_run:
            return {
                "accepted": True,
                "dry_run": True,
                "would_write": str(target_dir / "SKILL.md"),
            }
        backup = self._backup_existing(config=config, skill_id=skill_id, target_dir=target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
        try:
            manifest = self._validate_written_skill(skill_id=skill_id, skill_dir=target_dir)
        except ValueError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            if backup is not None:
                self._restore_backup(backup_path=backup.path, target_dir=target_dir)
            return self._refusal(str(exc))
        usage = self._load_usage(config)
        item = self._usage_item(usage, skill_id)
        now = utc_now_iso()
        item["state"] = "active"
        item["created_by"] = "agent"
        item["patch_count"] = int(item.get("patch_count") or 0) + 1
        item["last_patched_at"] = now
        item["last_activity_at"] = now
        if not item.get("created_at"):
            item["created_at"] = now
        item["archived_at"] = None
        self._save_usage(config, usage)
        return {
            "accepted": True,
            "applied": True,
            "change_action": change["change_action"],
            "skill_id": skill_id,
            "path": str(target_dir / "SKILL.md"),
            **_backup_result_metadata(backup),
            "manifest": manifest.model_dump(mode="json"),
        }

    def _apply_patch(self, *, config: EffectiveConfig, change: dict[str, object], dry_run: bool) -> dict[str, object]:
        skill_id = str(change["skill_id"])
        target = self._workspace_root() / skill_id / "SKILL.md"
        if not target.exists():
            return self._refusal(f"skill '{skill_id}' is not in the curator workspace root")
        old_text = str(change.get("old_text") or "")
        new_text = str(change.get("new_text") or "")
        if not old_text:
            return self._refusal("old_text is required for patch")
        if old_text == new_text:
            return self._refusal("new_text must differ from old_text")
        current = target.read_text(encoding="utf-8")
        if old_text not in current:
            return self._refusal("old_text was not found in SKILL.md")
        updated = current.replace(old_text, new_text, 1)
        if dry_run:
            return {"accepted": True, "dry_run": True, "would_patch": str(target)}
        backup = self._backup_existing(config=config, skill_id=skill_id, target_dir=target.parent)
        target.write_text(updated, encoding="utf-8")
        try:
            manifest = self._validate_written_skill(skill_id=skill_id, skill_dir=target.parent)
        except ValueError as exc:
            if backup is not None:
                self._restore_backup(backup_path=backup.path, target_dir=target.parent)
            return self._refusal(str(exc))
        self._record_patch(config=config, skill_id=skill_id)
        return {
            "accepted": True,
            "applied": True,
            "change_action": "patch",
            "skill_id": skill_id,
            "path": str(target),
            **_backup_result_metadata(backup),
            "manifest": manifest.model_dump(mode="json"),
        }

    def _apply_support_file_write(self, *, config: EffectiveConfig, change: dict[str, object], dry_run: bool) -> dict[str, object]:
        skill_id = str(change["skill_id"])
        target = self._resolve_support_file(skill_id=skill_id, relative_path=str(change.get("file_path") or ""))
        if target is None:
            return self._refusal("file_path must stay under assets/, templates/, scripts/, or references/")
        content = str(change.get("content") or "")
        if len(content) > MAX_SUPPORT_FILE_CHARS:
            return self._refusal(f"content exceeds {MAX_SUPPORT_FILE_CHARS} chars")
        if not (self._workspace_root() / skill_id / "SKILL.md").exists():
            return self._refusal(f"skill '{skill_id}' is not in the curator workspace root")
        if dry_run:
            return {"accepted": True, "dry_run": True, "would_write": str(target)}
        backup = self._backup_existing(config=config, skill_id=skill_id, target_dir=self._workspace_root() / skill_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._record_patch(config=config, skill_id=skill_id)
        return {
            "accepted": True,
            "applied": True,
            "change_action": "write_file",
            "skill_id": skill_id,
            "path": str(target),
            **_backup_result_metadata(backup),
        }

    def _apply_support_file_remove(self, *, config: EffectiveConfig, change: dict[str, object], dry_run: bool) -> dict[str, object]:
        skill_id = str(change["skill_id"])
        target = self._resolve_support_file(skill_id=skill_id, relative_path=str(change.get("file_path") or ""))
        if target is None:
            return self._refusal("file_path must stay under assets/, templates/, scripts/, or references/")
        if not target.exists() or not target.is_file():
            return self._refusal(f"support file '{change.get('file_path')}' was not found")
        if dry_run:
            return {"accepted": True, "dry_run": True, "would_remove": str(target)}
        backup = self._backup_existing(config=config, skill_id=skill_id, target_dir=self._workspace_root() / skill_id)
        target.unlink()
        self._record_patch(config=config, skill_id=skill_id)
        return {
            "accepted": True,
            "applied": True,
            "change_action": "remove_file",
            "skill_id": skill_id,
            "path": str(target),
            **_backup_result_metadata(backup),
        }

    def _apply_archive(self, *, config: EffectiveConfig, change: dict[str, object], dry_run: bool) -> dict[str, object]:
        skill_id = str(change["skill_id"])
        target_dir = self._workspace_root() / skill_id
        if not target_dir.exists():
            return self._refusal(f"skill '{skill_id}' is not in the curator workspace root")
        if self._is_pinned(config=config, skill_id=skill_id) and not bool(change.get("force")):
            return self._refusal(f"skill '{skill_id}' is pinned; unpin or pass force=true before archive")
        archive_dir = self._archive_root(config) / f"{skill_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        if dry_run:
            return {"accepted": True, "dry_run": True, "would_archive": str(target_dir), "archive_path": str(archive_dir)}
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_dir), str(archive_dir))
        usage = self._load_usage(config)
        item = self._usage_item(usage, skill_id)
        now = utc_now_iso()
        item["state"] = "archived"
        item["archived_at"] = now
        item["last_activity_at"] = now
        item["archive_path"] = str(archive_dir)
        absorbed_into = str(change.get("absorbed_into") or "").strip()
        if absorbed_into:
            item["absorbed_into"] = absorbed_into
        self._save_usage(config, usage)
        return {"accepted": True, "applied": True, "change_action": "archive", "skill_id": skill_id, "archive_path": str(archive_dir)}

    def _apply_restore(self, *, config: EffectiveConfig, change: dict[str, object], dry_run: bool) -> dict[str, object]:
        skill_id = str(change["skill_id"])
        target_dir = self._workspace_root() / skill_id
        if target_dir.exists():
            return self._refusal(f"skill '{skill_id}' already exists in the curator workspace root")
        candidate_scan = _scan_curator_direct_candidates(
            self._archive_root(config),
            prefix=f"{skill_id}-",
            directories_only=True,
        )
        archive_metadata = _candidate_scan_metadata("archive_candidate", candidate_scan)
        if candidate_scan.scan_truncated:
            return self._refusal("curator archive candidate scan truncated before restore", **archive_metadata)
        if not candidate_scan.paths:
            return self._refusal(f"no archived copy found for skill '{skill_id}'", **archive_metadata)
        selected = candidate_scan.paths[-1]
        if dry_run:
            return {
                "accepted": True,
                "dry_run": True,
                "would_restore": str(selected),
                "target_path": str(target_dir),
                **archive_metadata,
            }
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(selected), str(target_dir))
        usage = self._load_usage(config)
        item = self._usage_item(usage, skill_id)
        now = utc_now_iso()
        item["state"] = "active"
        item["archived_at"] = None
        item["last_activity_at"] = now
        self._save_usage(config, usage)
        return {
            "accepted": True,
            "applied": True,
            "change_action": "restore",
            "skill_id": skill_id,
            "path": str(target_dir),
            **archive_metadata,
        }

    def backup_or_rollback(
        self,
        *,
        config: EffectiveConfig,
        action: str,
        skill_id: str | None,
        revision: str | None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        normalized = self._normalize_skill_id(skill_id)
        if normalized is None:
            return self._refusal("skill_id is required and must be a safe slug")
        target_dir = self._workspace_root() / normalized
        if action == "backup":
            if not target_dir.exists():
                return self._refusal(f"skill '{normalized}' is not in the curator workspace root")
            if dry_run:
                return {"accepted": True, "dry_run": True, "would_backup": str(target_dir)}
            backup = self._backup_existing(config=config, skill_id=normalized, target_dir=target_dir)
            return {
                "accepted": True,
                "backed_up": backup is not None,
                "skill_id": normalized,
                **_backup_result_metadata(backup),
            }
        backup_dir = self._backup_root(config) / normalized
        candidate_metadata: dict[str, object]
        if revision:
            selected = _resolve_curator_backup_revision(backup_dir=backup_dir, revision=revision)
            candidate_metadata = {
                "backup_candidate_scanned_path_count": 1 if selected is not None else 0,
                "backup_candidate_max_scanned_paths": _bounded_curator_backup_scan_limit(),
                "backup_candidate_scan_truncated": False,
            }
            if selected is None:
                return self._refusal(f"unknown backup revision '{revision}'", **candidate_metadata)
        else:
            candidate_scan = _scan_curator_direct_candidates(backup_dir, suffix=".skill", files_only=True)
            candidate_metadata = _candidate_scan_metadata("backup_candidate", candidate_scan)
            if candidate_scan.scan_truncated:
                return self._refusal("curator backup candidate scan truncated before rollback", **candidate_metadata)
            if not candidate_scan.paths:
                return self._refusal(f"no curator backup found for skill '{normalized}'", **candidate_metadata)
            selected = candidate_scan.paths[-1]
        if self._is_pinned(config=config, skill_id=normalized) and not force:
            return self._refusal(
                f"skill '{normalized}' is pinned; unpin or pass force=true before rollback",
                **candidate_metadata,
            )
        if dry_run:
            return {"accepted": True, "dry_run": True, "would_rollback": str(selected), **candidate_metadata}
        pre_rollback = self._backup_existing(config=config, skill_id=normalized, target_dir=target_dir) if target_dir.exists() else None
        try:
            self._restore_backup(backup_path=selected, target_dir=target_dir)
        except ValueError as exc:
            return self._refusal(str(exc), **candidate_metadata)
        self._record_patch(config=config, skill_id=normalized)
        return {
            "accepted": True,
            "rolled_back": True,
            "skill_id": normalized,
            "revision": selected.name,
            **candidate_metadata,
            "pre_rollback_backup_path": str(pre_rollback.path) if pre_rollback is not None else None,
            "pre_rollback_backup_scanned_path_count": pre_rollback.scanned_path_count if pre_rollback is not None else 0,
            "pre_rollback_backup_max_scanned_paths": pre_rollback.max_scanned_paths if pre_rollback is not None else 0,
            "pre_rollback_backup_scan_truncated": pre_rollback.scan_truncated if pre_rollback is not None else False,
        }

    def _render_skill_text(self, change: dict[str, object]) -> str:
        title = str(change["title"]).strip()
        summary = str(change["summary"]).strip()
        body = str(change["body"]).strip()
        tags = change.get("tags") or []
        allowed_tools = change.get("allowed_tools") or []
        metadata = [
            "---",
            f"name: {json.dumps(str(change['skill_id']), ensure_ascii=False)}",
            f"description: {json.dumps(summary, ensure_ascii=False)}",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"summary: {json.dumps(summary, ensure_ascii=False)}",
            "version: \"0.1.0\"",
            "trust: \"local\"",
            f"tags: {json.dumps(tags, ensure_ascii=False)}",
            f"allowed_tools: {json.dumps(allowed_tools, ensure_ascii=False)}",
            "config:",
            "  anvil:",
            "    created_by: \"agent\"",
            f"    change_id: {json.dumps(str(change['change_id']), ensure_ascii=False)}",
            "---",
            "",
        ]
        if not body.startswith("# "):
            body = f"# {title}\n\n{body}"
        return "\n".join(metadata) + body.rstrip() + "\n"

    def _validate_written_skill(self, *, skill_id: str, skill_dir: Path) -> SkillManifest:
        load_result = self.loader.discover([skill_dir.parent])
        manifest = next((item for item in load_result.manifests if item.skill_id == skill_id), None)
        if manifest is None:
            raise ValueError("curator change did not produce a discoverable skill")
        if not manifest.valid:
            messages = "; ".join(issue.message for issue in manifest.issues)
            raise ValueError(f"curator change produced invalid SKILL.md: {messages}")
        return manifest

    def _validate_change_shape(self, change: dict[str, object]) -> None:
        json.dumps(change, ensure_ascii=False)

    def _backup_existing(self, *, config: EffectiveConfig, skill_id: str, target_dir: Path) -> _CuratorBackupResult | None:
        if not target_dir.exists():
            return None
        backup_path = self._backup_root(config) / skill_id / f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-pre-curator.skill"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        tree_scan = _scan_curator_tree_files(target_dir)
        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for _relative, path_text in tree_scan.files:
                path = Path(path_text)
                if path.is_file():
                    archive.write(path, arcname=str(path.relative_to(target_dir.parent)).replace("\\", "/"))
        return _CuratorBackupResult(
            path=backup_path,
            scanned_path_count=tree_scan.scanned_path_count,
            max_scanned_paths=tree_scan.max_scanned_paths,
            scan_truncated=tree_scan.scan_truncated,
        )

    def _record_patch(self, *, config: EffectiveConfig, skill_id: str) -> None:
        usage = self._load_usage(config)
        item = self._usage_item(usage, skill_id)
        now = utc_now_iso()
        item["patch_count"] = int(item.get("patch_count") or 0) + 1
        item["last_patched_at"] = now
        item["last_activity_at"] = now
        self._save_usage(config, usage)

    def _restore_backup(self, *, backup_path: Path, target_dir: Path) -> None:
        if not backup_path.exists():
            return
        with zipfile.ZipFile(backup_path) as archive:
            archive_scan = _scan_curator_backup_archive(archive)
            if archive_scan.scan_truncated:
                raise ValueError("curator backup scan truncated before restore")
            target_parent = target_dir.parent.resolve()
            for item in archive_scan.entries:
                if _is_zip_symlink(item.info):
                    raise ValueError("curator backup contains symlink entries")
                target = (target_parent / item.filename).resolve()
                if target != target_parent and target_parent not in target.parents:
                    raise ValueError("curator backup contains path traversal")
        shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(backup_path) as archive:
            for item in archive_scan.entries:
                archive.extract(item.info, target_dir.parent)

    def _resolve_support_file(self, *, skill_id: str, relative_path: str) -> Path | None:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        if not normalized or normalized == "SKILL.md":
            return None
        prefix = normalized.split("/", 1)[0]
        if prefix not in SUPPORT_FILE_PREFIXES:
            return None
        skill_root = (self._workspace_root() / skill_id).resolve()
        target = (skill_root / normalized).resolve()
        if skill_root not in target.parents:
            return None
        return target

    def _is_pinned(self, *, config: EffectiveConfig, skill_id: str) -> bool:
        usage = self._load_usage(config)
        return bool(usage.get(skill_id, {}).get("pinned"))

    def _is_workspace_skill_id(self, skill_id: str) -> bool:
        return (self._workspace_root() / skill_id / "SKILL.md").exists()

    def _usage_item(self, usage: dict[str, dict[str, object]], skill_id: str) -> dict[str, object]:
        now = utc_now_iso()
        item = usage.setdefault(
            skill_id,
            {
                "created_by": "agent",
                "state": "active",
                "tier": "active",
                "pinned": False,
                "view_count": 0,
                "use_count": 0,
                "patch_count": 0,
                "context_count": 0,
                "contexts": [],
                "utility_score": 0,
                "feedback_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "neutral_count": 0,
                "feedback_by_source": {},
                "confidence_totals": {},
                "created_at": now,
                "last_activity_at": now,
                "archived_at": None,
            },
        )
        return item

    def _normalize_feedback_source(self, value: str | None) -> str:
        normalized = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "agent").strip().lower()).strip("_.-")
        if not normalized:
            return "agent"
        return normalized[:64]

    def _normalize_feedback_confidence(self, value: float | int | None) -> float | None:
        if value is None:
            return 1.0
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        if confidence < 0 or confidence > 1:
            return None
        return round(confidence, 4)

    def _normalize_procedure_outcome(self, value: str | None) -> str | None:
        normalized = str(value or "success").strip().lower()
        if normalized in {"ok", "passed", "pass", "complete", "completed"}:
            return "success"
        if normalized in {"failed", "error", "errored"}:
            return "failure"
        if normalized in {"success", "failure", "neutral"}:
            return normalized
        return None

    def _is_curatable_manifest(self, manifest: SkillManifest) -> bool:
        return Path(manifest.source_root).resolve() == self._workspace_root()

    def _workspace_root(self) -> Path:
        return default_installed_skill_root().resolve()

    def _curator_root(self, config: EffectiveConfig) -> Path:
        root = config.skills_config.governance_root
        if root:
            return Path(root).expanduser().resolve() / "curator"
        return self._workspace_root() / ".hub"

    def _usage_path(self, config: EffectiveConfig) -> Path:
        root = config.skills_config.governance_root
        if root:
            return self._curator_root(config) / "usage.json"
        return self._workspace_root() / ".usage.json"

    def _archive_root(self, config: EffectiveConfig) -> Path:
        return self._curator_root(config) / "archive"

    def _backup_root(self, config: EffectiveConfig) -> Path:
        return self._curator_root(config) / "backups"

    def _change_log_path(self, config: EffectiveConfig) -> Path:
        return self._curator_root(config) / "changes.jsonl"

    def _runs_root(self, config: EffectiveConfig) -> Path:
        return self._curator_root(config) / "runs"

    def _automation_state_path(self, config: EffectiveConfig) -> Path:
        return self._curator_root(config) / "automation.json"

    def _merge_proposals_root(self, config: EffectiveConfig) -> Path:
        return self._curator_root(config) / "merge-proposals"

    def _review_proposals_root(self, config: EffectiveConfig) -> Path:
        return self._curator_root(config) / "review-proposals"

    def _procedures_path(self, config: EffectiveConfig) -> Path:
        return self._curator_root(config) / "procedures.json"

    def _load_procedures(self, config: EffectiveConfig) -> dict[str, dict[str, object]]:
        path = self._procedures_path(config)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        raw_items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(raw_items, dict):
            return {}
        procedures: dict[str, dict[str, object]] = {}
        for key, value in raw_items.items():
            if not isinstance(value, dict):
                continue
            procedure_id = str(value.get("procedure_id") or key).strip()
            if not re.match(r"^proc-[a-f0-9]{8,64}$", procedure_id):
                continue
            procedures[procedure_id] = value | {"procedure_id": procedure_id}
        return procedures

    def _save_procedures(self, config: EffectiveConfig, procedures: dict[str, dict[str, object]]) -> None:
        path = self._procedures_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": utc_now_iso(),
            "items": procedures,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _normalize_procedure_steps(self, steps: object) -> list[str]:
        if not isinstance(steps, (list, tuple)):
            return []
        normalized: list[str] = []
        for raw_step in steps[:MAX_PROCEDURE_STEPS]:
            step = re.sub(r"\s+", " ", str(raw_step or "").strip())
            step = step[:MAX_PROCEDURE_STEP_CHARS].strip()
            if step and step not in normalized:
                normalized.append(step)
        return normalized

    def _procedure_fingerprint(self, *, title: str, trigger: str, steps: list[str], allowed_tools: list[str]) -> str:
        normalized_tools = sorted(re.sub(r"\W+", " ", tool.lower()).strip() for tool in allowed_tools[:12] if tool.strip())
        if not normalized_tools:
            return self._legacy_procedure_fingerprint(title=title, trigger=trigger, steps=steps)
        workflow_parts = [
            "tools",
            " ".join(normalized_tools),
            "steps",
            " ".join(re.sub(r"\W+", " ", step.lower()).strip() for step in steps[:6]),
        ]
        return self._fingerprint_digest(" ".join(part for part in workflow_parts if part.strip()))

    def _legacy_procedure_fingerprint(self, *, title: str, trigger: str, steps: list[str]) -> str:
        normalized = " ".join(
            [
                re.sub(r"\W+", " ", title.lower()).strip(),
                re.sub(r"\W+", " ", trigger.lower()).strip(),
                " ".join(re.sub(r"\W+", " ", step.lower()).strip() for step in steps[:6]),
            ]
        )
        return self._fingerprint_digest(normalized)

    def _find_similar_procedure(
        self,
        procedures: dict[str, dict[str, object]],
        *,
        trigger: str,
        steps: list[str],
        allowed_tools: list[str],
    ) -> tuple[str | None, dict[str, object] | None]:
        incoming_steps = self._fingerprint_text(" ".join(steps[:8]))
        incoming_tools = self._fingerprint_text(" ".join(sorted(allowed_tools)))
        incoming_trigger = self._fingerprint_text(trigger)
        if not incoming_steps:
            return None, None
        for procedure_id, candidate in procedures.items():
            if str(candidate.get("status") or "candidate") == "rejected":
                continue
            candidate_steps = self._normalize_procedure_steps(candidate.get("steps") if isinstance(candidate.get("steps"), list) else [])
            candidate_step_key = self._fingerprint_text(" ".join(candidate_steps[:8]))
            if candidate_step_key and candidate_step_key == incoming_steps:
                return procedure_id, candidate
            if not incoming_tools:
                continue
            candidate_tools = self._safe_string_list(candidate.get("allowed_tools") if isinstance(candidate.get("allowed_tools"), list) else [])
            candidate_tool_key = self._fingerprint_text(" ".join(sorted(candidate_tools)))
            if candidate_tool_key != incoming_tools:
                continue
            candidate_trigger_key = self._fingerprint_text(str(candidate.get("trigger") or ""))
            if self._fingerprint_overlap(incoming_trigger, candidate_trigger_key) >= 0.55:
                return procedure_id, candidate
        return None, None

    def _procedure_strength(self, *, frequency: int, confidence: float, evidence_count: int) -> float:
        frequency_score = min(max(frequency, 1) * 0.22, 0.88)
        confidence_score = max(min(confidence, 1.0), 0.0) * 0.45
        evidence_score = min(max(evidence_count, 0) * 0.04, 0.2)
        return round(min(1.0, frequency_score + confidence_score + evidence_score), 4)

    def _procedure_quality(self, item: dict[str, object]) -> dict[str, object]:
        steps = self._normalize_procedure_steps(item.get("steps") if isinstance(item.get("steps"), list) else [])
        evidence_refs = self._safe_string_list(item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else [])
        source_refs = self._safe_string_list(item.get("source_refs") if isinstance(item.get("source_refs"), list) else [])
        allowed_tools = self._safe_string_list(item.get("allowed_tools") if isinstance(item.get("allowed_tools"), list) else [])
        source_counts = item.get("source_counts") if isinstance(item.get("source_counts"), dict) else {}
        expected_outcome_present = bool(str(item.get("expected_outcome") or "").strip())
        source_count = max(len(source_refs), len([key for key, value in source_counts.items() if key and self._safe_float(value) > 0]))
        step_text = "\n".join(steps)
        trigger_text = str(item.get("trigger") or "")
        expected_text = str(item.get("expected_outcome") or "")
        evidence_text = "\n".join(evidence_refs)
        tool_set = set(allowed_tools)
        verification_tools = sorted(
            tool
            for tool in tool_set
            if tool in PROCEDURE_VERIFICATION_TOOL_NAMES
            or tool.startswith("browser_")
            and tool in {"browser_console", "browser_network", "browser_screenshot", "browser_snapshot"}
        )
        discovery_tools = sorted(tool for tool in tool_set if tool in PROCEDURE_DISCOVERY_TOOL_NAMES)
        mutation_tools = sorted(tool for tool in tool_set if tool in PROCEDURE_MUTATION_TOOL_NAMES)
        verification_signal = bool(
            verification_tools
            or PROCEDURE_VERIFICATION_KEYWORDS_RE.search(step_text)
            or PROCEDURE_VERIFICATION_KEYWORDS_RE.search(evidence_text)
        )
        blockers: list[str] = []
        if len(steps) < 2:
            blockers.append("needs_steps")
        if not expected_outcome_present:
            blockers.append("needs_expected_outcome")
        if len(evidence_refs) < 2:
            blockers.append("needs_more_evidence")
        if source_count < 2:
            blockers.append("needs_source_diversity")
        if not verification_signal:
            blockers.append("needs_verification_signal")
        generic_steps = sum(1 for step in steps if GENERIC_PROCEDURE_STEP_RE.match(step.strip()))
        one_off_scope = bool(ONE_OFF_PROCEDURE_RE.search("\n".join((trigger_text, step_text, expected_text))))
        if generic_steps:
            blockers.append("generic_steps")
        if one_off_scope:
            blockers.append("one_off_scope")
        evidence_score = min(len(evidence_refs), 6) / 6
        source_score = min(source_count, 3) / 3
        step_score = min(len(steps), 4) / 4
        tool_score = min(len(tool_set), 4) / 4
        verification_score = 1.0 if verification_signal else 0.0
        expected_score = 1.0 if expected_outcome_present else 0.0
        quality_score = min(
            1.0,
            evidence_score * 0.24
            + source_score * 0.18
            + step_score * 0.16
            + tool_score * 0.12
            + verification_score * 0.2
            + expected_score * 0.1,
        )
        return {
            "quality_score": round(quality_score, 4),
            "blockers": blockers,
            "evidence_count": len(evidence_refs),
            "source_count": source_count,
            "step_count": len(steps),
            "tool_count": len(tool_set),
            "expected_outcome_present": expected_outcome_present,
            "verification_signal": verification_signal,
            "verification_tools": verification_tools,
            "discovery_tools": discovery_tools,
            "mutation_tools": mutation_tools,
            "generic_step_count": generic_steps,
            "one_off_scope": one_off_scope,
        }

    def _procedure_promotable(self, item: dict[str, object]) -> bool:
        if str(item.get("status") or "candidate") in {"promoted", "rejected"}:
            return False
        finalized = self._finalize_procedure_candidate(dict(item))
        readiness = finalized.get("promotion_readiness") if isinstance(finalized.get("promotion_readiness"), dict) else {}
        return bool(readiness.get("promotable"))

    def _record_procedure_outcome(
        self,
        candidate: dict[str, object],
        *,
        outcome: str,
        source: str,
        confidence: float,
        rationale: str | None,
        now: str,
    ) -> None:
        outcome_counts = candidate.get("outcome_counts") if isinstance(candidate.get("outcome_counts"), dict) else {}
        outcome_counts[outcome] = int(outcome_counts.get(outcome) or 0) + 1
        candidate["outcome_counts"] = outcome_counts
        confidence_totals = candidate.get("confidence_totals") if isinstance(candidate.get("confidence_totals"), dict) else {}
        confidence_totals[outcome] = round(self._safe_float(confidence_totals.get(outcome)) + confidence, 4)
        candidate["confidence_totals"] = confidence_totals
        source_counts = candidate.get("source_counts") if isinstance(candidate.get("source_counts"), dict) else {}
        source_counts[source] = int(source_counts.get(source) or 0) + 1
        candidate["source_counts"] = source_counts
        candidate["last_outcome"] = {
            "outcome": outcome,
            "source": source,
            "confidence": confidence,
            "rationale": (rationale or "").strip()[:MAX_RATIONALE_CHARS],
            "created_at": now,
        }
        if outcome == "success":
            candidate["last_success_at"] = now
        elif outcome == "failure":
            candidate["last_failure_at"] = now

    def _procedure_outcome_health(self, item: dict[str, object]) -> dict[str, object]:
        totals = item.get("confidence_totals") if isinstance(item.get("confidence_totals"), dict) else {}
        counts = item.get("outcome_counts") if isinstance(item.get("outcome_counts"), dict) else {}
        success_confidence = self._safe_float(totals.get("success"))
        failure_confidence = self._safe_float(totals.get("failure"))
        neutral_confidence = self._safe_float(totals.get("neutral"))
        success_count = int(counts.get("success") or 0)
        failure_count = int(counts.get("failure") or 0)
        neutral_count = int(counts.get("neutral") or 0)
        samples = success_count + failure_count + neutral_count
        confidence_samples = success_confidence + failure_confidence + neutral_confidence
        success_rate = success_count / samples if samples else (1.0 if int(item.get("frequency") or 0) > 0 else 0.0)
        confidence_success_rate = success_confidence / confidence_samples if confidence_samples else success_rate
        return {
            "success_count": success_count,
            "failure_count": failure_count,
            "neutral_count": neutral_count,
            "success_confidence": round(success_confidence, 4),
            "failure_confidence": round(failure_confidence, 4),
            "neutral_confidence": round(neutral_confidence, 4),
            "net_confidence": round(success_confidence - failure_confidence, 4),
            "confidence_samples": round(confidence_samples, 4),
            "success_rate": round(success_rate, 4),
            "confidence_success_rate": round(confidence_success_rate, 4),
            "last_outcome": item.get("last_outcome") if isinstance(item.get("last_outcome"), dict) else None,
        }

    def _procedure_promotion_readiness(self, item: dict[str, object]) -> dict[str, object]:
        health = self._procedure_outcome_health(item)
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else self._procedure_quality(item)
        frequency = int(item.get("frequency") or 0)
        evidence_count = len(self._safe_string_list(item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else []))
        source_count = len(self._safe_string_list(item.get("source_refs") if isinstance(item.get("source_refs"), list) else []))
        strength = self._safe_float(item.get("strength"))
        quality_score = self._safe_float(quality.get("quality_score"))
        success_rate = self._safe_float(health.get("confidence_success_rate"))
        failure_confidence = self._safe_float(health.get("failure_confidence"))
        blockers: list[str] = []
        if frequency < 2:
            blockers.append("needs_repetition")
        if evidence_count < 1:
            blockers.append("needs_evidence")
        if failure_confidence >= 0.75 and success_rate < 0.75:
            blockers.append("failure_signal")
        if strength < 0.72 and frequency < 3:
            blockers.append("weak_strength")
        if quality_score < MIN_PROCEDURE_QUALITY_FOR_PROMOTION:
            blockers.append("weak_quality")
        for blocker in quality.get("blockers") if isinstance(quality.get("blockers"), list) else []:
            if blocker in {"needs_steps", "needs_expected_outcome", "needs_verification_signal"} and blocker not in blockers:
                blockers.append(str(blocker))
        promotable = not blockers
        readiness_score = min(
            1.0,
            strength * 0.34
            + min(frequency, 5) / 5 * 0.16
            + min(evidence_count, 5) / 5 * 0.1
            + min(source_count, 4) / 4 * 0.08
            + success_rate * 0.12
            + quality_score * 0.2,
        )
        if "failure_signal" in blockers:
            readiness_score = min(readiness_score, 0.45)
        if "weak_quality" in blockers:
            readiness_score = min(readiness_score, 0.62)
        if any(blocker in blockers for blocker in ("generic_steps", "one_off_scope")):
            readiness_score = min(readiness_score, 0.5)
        return {
            "promotable": promotable,
            "readiness_score": round(readiness_score, 4),
            "blockers": blockers,
            "recommendation": "promote" if promotable else "observe",
            "quality_score": round(quality_score, 4),
        }

    def _finalize_procedure_candidate(self, item: dict[str, object]) -> dict[str, object]:
        frequency = int(item.get("frequency") or 0)
        confidence = self._safe_float(item.get("confidence"))
        evidence = self._safe_string_list(item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else [])
        outcome_health = self._procedure_outcome_health(item)
        if not item.get("outcome_counts"):
            item["outcome_counts"] = {
                "success": frequency,
                "failure": 0,
                "neutral": 0,
            }
            item["confidence_totals"] = {
                "success": round(confidence * max(frequency, 1), 4),
                "failure": 0.0,
                "neutral": 0.0,
            }
            outcome_health = self._procedure_outcome_health(item)
        effective_confidence = max(confidence, self._safe_float(outcome_health.get("confidence_success_rate")))
        item["confidence"] = round(min(max(effective_confidence, 0.0), 1.0), 4)
        item["quality"] = self._procedure_quality(item)
        item["strength"] = self._procedure_strength(
            frequency=frequency,
            confidence=float(item["confidence"]),
            evidence_count=len(evidence),
        )
        item["outcome_health"] = outcome_health
        item["promotion_readiness"] = self._procedure_promotion_readiness(item)
        return item

    def _slug_skill_id(self, value: str) -> str | None:
        slug = re.sub(r"[^a-z0-9_.-]+", "-", value.strip().lower()).strip("-.")
        if not slug:
            slug = "learned-procedure"
        if not slug.startswith("learned-"):
            slug = f"learned-{slug}"
        return self._normalize_skill_id(slug[:64])

    def _render_procedure_skill_body(self, candidate: dict[str, object]) -> str:
        title = str(candidate.get("title") or "Learned Procedure").strip()
        trigger = str(candidate.get("trigger") or "").strip()
        outcome = str(candidate.get("expected_outcome") or "").strip()
        steps = self._normalize_procedure_steps(candidate.get("steps") if isinstance(candidate.get("steps"), list) else [])
        evidence = self._safe_string_list(candidate.get("evidence_refs") if isinstance(candidate.get("evidence_refs"), list) else [])
        source_refs = self._safe_string_list(candidate.get("source_refs") if isinstance(candidate.get("source_refs"), list) else [])
        outcome_health = candidate.get("outcome_health") if isinstance(candidate.get("outcome_health"), dict) else self._procedure_outcome_health(candidate)
        readiness = candidate.get("promotion_readiness") if isinstance(candidate.get("promotion_readiness"), dict) else self._procedure_promotion_readiness(candidate)
        quality = candidate.get("quality") if isinstance(candidate.get("quality"), dict) else self._procedure_quality(candidate)
        step_lines = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))
        evidence_lines = "\n".join(f"- {item}" for item in evidence[:10]) or "- Record task outputs, changed files, commands, or user-visible proof."
        source_lines = "\n".join(f"- {item}" for item in source_refs[:10])
        sections = [
            f"# {title}",
            "",
            "## Trigger",
            "",
            trigger or "Use this when the same workflow pattern appears again.",
            "",
            "## Procedure",
            "",
            step_lines or "1. Identify the repeatable workflow and collect enough evidence before acting.",
        ]
        if outcome:
            sections.extend(["", "## Expected Outcome", "", outcome])
        sections.extend(["", "## Verification Evidence", "", evidence_lines])
        if source_lines:
            sections.extend(["", "## Source Signals", "", source_lines])
        sections.extend(
            [
                "",
                "## Promotion Evidence",
                "",
                f"- Frequency: {int(candidate.get('frequency') or 0)}",
                f"- Strength: {float(candidate.get('strength') or 0.0):.2f}",
                f"- Quality: {float(quality.get('quality_score') or 0.0):.2f}",
                f"- Verification signal: {'yes' if quality.get('verification_signal') else 'no'}",
                f"- Success confidence: {float(outcome_health.get('success_confidence') or 0.0):.2f}",
                f"- Failure confidence: {float(outcome_health.get('failure_confidence') or 0.0):.2f}",
                f"- Readiness: {readiness.get('recommendation') or 'observe'} ({float(readiness.get('readiness_score') or 0.0):.2f})",
            ]
        )
        sections.extend(
            [
                "",
                "## Maintenance",
                "",
                "- Update this skill when repeated usage reveals a better trigger, safer steps, or stronger verification evidence.",
            ]
        )
        return "\n".join(sections).rstrip() + "\n"

    def _review_signal_fingerprint(self, item: dict[str, object]) -> str:
        last_feedback = item.get("last_feedback") if isinstance(item.get("last_feedback"), dict) else {}
        parts = [
            str(item.get("tier") or "active"),
            str(int(item.get("utility_score") or 0)),
            str(int(item.get("feedback_count") or 0)),
            str(int(item.get("failure_count") or 0)),
            str(last_feedback.get("outcome") or ""),
            str(last_feedback.get("source") or ""),
            str(last_feedback.get("confidence") or ""),
            str(last_feedback.get("rationale") or ""),
        ]
        return self._fingerprint_text(" ".join(parts))

    def _should_plan_skill_review(
        self,
        *,
        config: EffectiveConfig,
        skill_id: str,
        item: dict[str, object],
        existing_lookup: _CuratorProposalLookup | None = None,
    ) -> bool:
        if not self._is_workspace_skill_id(skill_id):
            return False
        if existing_lookup is None:
            existing_lookup = self._find_existing_review_proposal(config=config, skill_id=skill_id)
        if existing_lookup.scan_truncated:
            return False
        existing = existing_lookup.proposal
        signal = self._review_signal_fingerprint(item)
        if not signal:
            return False
        if str(item.get("last_review_signal") or "") == signal and str(item.get("last_review_proposal_id") or ""):
            if existing is None:
                return False
            return True
        if existing is not None:
            return True
        if str(item.get("last_review_signal") or "") == signal:
            return False
        failure_count = int(item.get("failure_count") or 0)
        feedback_count = int(item.get("feedback_count") or 0)
        utility_score = int(item.get("utility_score") or 0)
        tier = str(item.get("tier") or "active")
        if self._has_strong_failure_signal(item):
            return True
        if feedback_count > 0 and utility_score <= 20 and self._has_actionable_feedback_signal(item):
            return True
        if tier == "observe":
            return True
        return False

    def _feedback_health(self, item: dict[str, object]) -> dict[str, object]:
        confidence_totals = item.get("confidence_totals") if isinstance(item.get("confidence_totals"), dict) else {}
        by_source = item.get("feedback_by_source") if isinstance(item.get("feedback_by_source"), dict) else {}
        last_feedback = item.get("last_feedback") if isinstance(item.get("last_feedback"), dict) else {}
        success = self._safe_float(confidence_totals.get("success"))
        failure = self._safe_float(confidence_totals.get("failure"))
        neutral = self._safe_float(confidence_totals.get("neutral"))
        dominant_source = self._dominant_feedback_source(by_source=by_source, last_feedback=last_feedback)
        return {
            "success_confidence": round(success, 4),
            "failure_confidence": round(failure, 4),
            "neutral_confidence": round(neutral, 4),
            "net_confidence": round(success - failure, 4),
            "dominant_source": dominant_source,
            "confidence_samples": round(success + failure + neutral, 4),
        }

    def _has_strong_failure_signal(self, item: dict[str, object]) -> bool:
        if int(item.get("failure_count") or 0) <= 0:
            return False
        health = self._feedback_health(item)
        failure_confidence = self._safe_float(health.get("failure_confidence"))
        if failure_confidence >= 1.0:
            return True
        last_feedback = item.get("last_feedback") if isinstance(item.get("last_feedback"), dict) else {}
        return str(last_feedback.get("outcome") or "") == "failure" and str(last_feedback.get("source") or "") != "runtime_failure"

    def _has_actionable_feedback_signal(self, item: dict[str, object]) -> bool:
        last_feedback = item.get("last_feedback") if isinstance(item.get("last_feedback"), dict) else {}
        if str(last_feedback.get("source") or "") != "runtime_failure":
            return True
        return self._safe_float(self._feedback_health(item).get("confidence_samples")) >= 1.0

    def _dominant_feedback_source(self, *, by_source: dict[object, object], last_feedback: dict[str, object]) -> str | None:
        if not by_source:
            return None
        counts: dict[str, int] = {}
        for key, value in by_source.items():
            source = self._normalize_feedback_source(str(key))
            counts[source] = counts.get(source, 0) + int(value or 0)
        if not counts:
            return None
        highest = max(counts.values())
        candidates = {source for source, count in counts.items() if count == highest}
        last_source = str(last_feedback.get("source") or "").strip()
        if last_source in candidates:
            return last_source
        return sorted(candidates)[0]

    def _safe_float(self, value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _load_usage(self, config: EffectiveConfig) -> dict[str, dict[str, object]]:
        path = self._usage_path(config)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        usage: dict[str, dict[str, object]] = {}
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            normalized = self._normalize_skill_id(str(key))
            if normalized:
                usage[normalized] = value
        return usage

    def _save_usage(self, config: EffectiveConfig, usage: dict[str, dict[str, object]]) -> None:
        path = self._usage_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(usage, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _change_id(self, action: str, skill_id: str) -> str:
        return f"{skill_id}-{action}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    def _load_automation_state(self, config: EffectiveConfig) -> dict[str, object]:
        path = self._automation_state_path(config)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_automation_state(self, config: EffectiveConfig, state: dict[str, object]) -> None:
        path = self._automation_state_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_curator_change(self, *, config: EffectiveConfig, change: dict[str, object]) -> None:
        change_log_path = self._change_log_path(config)
        change_log_path.parent.mkdir(parents=True, exist_ok=True)
        change_log_path.open("a", encoding="utf-8").write(
            json.dumps(change, ensure_ascii=False) + "\n"
        )

    def _next_automation_run_at(
        self,
        *,
        config: EffectiveConfig,
        now: datetime,
        state: dict[str, object],
    ) -> datetime:
        now = self._utc_datetime(now)
        last_run_at = self._parse_time(state.get("last_run_at"))
        if last_run_at is None:
            return self._next_calendar_boundary(config=config, after=now, include_current=True)
        return self._next_automation_run_after(config=config, completed_at=last_run_at)

    def _next_automation_run_after(self, *, config: EffectiveConfig, completed_at: datetime) -> datetime:
        completed_at = self._utc_datetime(completed_at)
        if str(config.skills_config.curator.schedule or "interval") == "interval":
            return completed_at + timedelta(seconds=self._curator_interval_seconds(config))
        return self._next_calendar_boundary(config=config, after=completed_at, include_current=False)

    def _next_calendar_boundary(self, *, config: EffectiveConfig, after: datetime, include_current: bool) -> datetime:
        current = self._utc_datetime(after)
        schedule = str(config.skills_config.curator.schedule or "interval")
        if schedule == "hourly":
            boundary = current.replace(minute=0, second=0, microsecond=0)
            if not include_current or boundary < current:
                boundary += timedelta(hours=1)
            return boundary
        if schedule == "daily":
            boundary = current.replace(hour=0, minute=0, second=0, microsecond=0)
            if not include_current or boundary < current:
                boundary += timedelta(days=1)
            return boundary
        if schedule == "weekly":
            boundary = current.replace(hour=0, minute=0, second=0, microsecond=0)
            days_until_sunday = (6 - boundary.weekday()) % 7
            boundary += timedelta(days=days_until_sunday)
            if not include_current or boundary < current:
                boundary += timedelta(days=7)
            return boundary
        return current

    def _curator_interval_seconds(self, config: EffectiveConfig) -> int:
        return max(int(config.skills_config.curator.interval_seconds or 0), 60)

    def _utc_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _last_skill_activity_within_idle_window(self, *, config: EffectiveConfig, now: datetime) -> bool:
        min_idle_hours = float(config.skills_config.curator.min_idle_hours or 0)
        if min_idle_hours <= 0:
            return False
        idle_cutoff = now - timedelta(hours=min_idle_hours)
        for item in self._load_usage(config).values():
            if str(item.get("state") or "active") == "archived":
                continue
            last_activity = self._parse_time(item.get("last_activity_at") or item.get("created_at"))
            if last_activity is not None and last_activity > idle_cutoff:
                return True
        return False

    def _utility_score(self, item: dict[str, object]) -> int:
        contexts = [value for value in item.get("contexts") or [] if str(value).strip()]
        score = int(item.get("use_count") or 0) * 100
        score += int(item.get("view_count") or 0) * 10
        score += int(item.get("patch_count") or 0) * 5
        score += len(contexts) * 25
        outcome_score = self._feedback_utility_score(item)
        score += outcome_score
        if bool(item.get("pinned")):
            score += 10_000
        return max(score, 0)

    def _feedback_utility_score(self, item: dict[str, object]) -> int:
        confidence_totals = item.get("confidence_totals") if isinstance(item.get("confidence_totals"), dict) else {}
        if confidence_totals:
            success = self._safe_float(confidence_totals.get("success"))
            failure = self._safe_float(confidence_totals.get("failure"))
            return round(success * 80) - round(failure * 70)
        return int(item.get("success_count") or 0) * 80 - int(item.get("failure_count") or 0) * 70

    def _governance_recommendations(
        self,
        *,
        config: EffectiveConfig,
        usage: dict[str, dict[str, object]],
        actions: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        action_items = actions or []
        recommendations: list[dict[str, object]] = []
        for skill_id, item in sorted(usage.items()):
            if str(item.get("state") or "active") == "archived":
                continue
            if not self._is_workspace_skill_id(skill_id):
                continue
            if self._should_plan_skill_review(config=config, skill_id=skill_id, item=item):
                recommendations.append(
                    self._recommendation(
                        action="quality_plan",
                        skill_id=skill_id,
                        reason="quality review signal",
                        priority=900 + self._review_priority(item),
                        next_tool_call={"action": "quality_plan", "skill_id": skill_id},
                        item=item,
                    )
                )
            if self._should_promote_template(config=config, skill_id=skill_id, item=item):
                recommendations.append(
                    self._recommendation(
                        action="curate",
                        skill_id=skill_id,
                        reason="reused across contexts",
                        priority=650 + min(int(item.get("context_count") or 0) * 20, 100),
                        next_tool_call={"action": "curate"},
                        item=item,
                    )
                )
            utility_score = self._utility_score(item)
            if (
                utility_score >= int(config.skills_config.curator.core_score_threshold or 0)
                and str(item.get("tier") or "active") != "core"
            ):
                recommendations.append(
                    self._recommendation(
                        action="curate",
                        skill_id=skill_id,
                        reason="core promotion threshold reached",
                        priority=600 + min(utility_score // 10, 100),
                        next_tool_call={"action": "curate"},
                        item=item,
                    )
                )
            if any(
                action.get("action") == "archive" and action.get("skill_id") == skill_id
                for action in action_items
            ):
                recommendations.append(
                    self._recommendation(
                        action="curate",
                        skill_id=skill_id,
                        reason="archive candidate after inactivity",
                        priority=500,
                        next_tool_call={"action": "curate"},
                        item=item,
                    )
                )
        for group in self._duplicate_groups_with_fingerprints():
            skill_ids = [str(item) for item in group.get("skill_ids") or []]
            if len(skill_ids) < 2:
                continue
            recommendations.append(
                {
                    "action": "merge_plan" if config.skills_config.curator.auto_merge else "review_duplicates",
                    "skill_ids": skill_ids,
                    "fingerprint": group.get("fingerprint"),
                    "reason": "similar title/summary fingerprint",
                    "priority": 800 if config.skills_config.curator.auto_merge else 400,
                    "next_tool_call": {"action": "merge_plan", "skill_id": skill_ids[0]}
                    if config.skills_config.curator.auto_merge
                    else {"action": "report"},
                }
            )
        for procedure in self._load_procedures(config).values():
            procedure = self._finalize_procedure_candidate(dict(procedure))
            if not self._procedure_promotable(procedure):
                continue
            procedure_id = str(procedure.get("procedure_id") or "")
            if not procedure_id:
                continue
            recommendations.append(
                {
                    "action": "promote_procedure",
                    "procedure_id": procedure_id,
                    "title": procedure.get("title"),
                    "reason": "repeated successful procedure candidate reached promotion threshold",
                    "priority": 760 + min(int(float(procedure.get("strength") or 0.0) * 100), 100),
                    "strength": procedure.get("strength"),
                    "quality": procedure.get("quality"),
                    "frequency": procedure.get("frequency"),
                    "promotion_readiness": procedure.get("promotion_readiness"),
                    "outcome_health": procedure.get("outcome_health"),
                    "next_tool_call": {"action": "promote_procedure", "procedure_id": procedure_id},
                }
            )
        return sorted(
            recommendations,
            key=lambda recommendation: (
                -int(recommendation.get("priority") or 0),
                str(
                    recommendation.get("skill_id")
                    or recommendation.get("procedure_id")
                    or ",".join(str(item) for item in recommendation.get("skill_ids") or [])
                ),
                str(recommendation.get("action") or ""),
            ),
        )[:10]

    def _bound_maintenance_actions(
        self,
        *,
        config: EffectiveConfig,
        actions: list[dict[str, object]],
    ) -> tuple[list[dict[str, object]], dict[str, int]]:
        limits = {
            "archive": max(int(config.skills_config.curator.max_archive_per_run or 0), 0),
            "quality_plan": max(int(config.skills_config.curator.max_quality_plan_per_run or 0), 0),
            "merge_plan": max(int(config.skills_config.curator.max_merge_plan_per_run or 0), 0),
            "promote_procedure": max(int(config.skills_config.curator.max_procedure_promotions_per_run or 0), 0),
            "promote_template": max(int(config.skills_config.curator.max_template_promotions_per_run or 0), 0),
        }
        max_actions = max(int(config.skills_config.curator.max_actions_per_run or 0), 0)
        selected: list[dict[str, object]] = []
        skipped: dict[str, int] = {}
        seen_merge_fingerprints: set[str] = set()
        counts: dict[str, int] = {}
        for action in sorted(actions, key=self._maintenance_action_sort_key):
            action_name = str(action.get("action") or "unknown")
            if action_name == "review_duplicates":
                skipped[action_name] = skipped.get(action_name, 0) + 1
                continue
            if action_name == "promote_procedure" and not bool(config.skills_config.curator.auto_promote_procedures):
                skipped[action_name] = skipped.get(action_name, 0) + 1
                continue
            if action_name == "merge_plan":
                fingerprint = str(action.get("fingerprint") or "")
                if fingerprint and fingerprint in seen_merge_fingerprints:
                    skipped[action_name] = skipped.get(action_name, 0) + 1
                    continue
                if fingerprint:
                    seen_merge_fingerprints.add(fingerprint)
            limit = limits.get(action_name)
            if limit is not None and counts.get(action_name, 0) >= limit:
                skipped[action_name] = skipped.get(action_name, 0) + 1
                continue
            if max_actions and len(selected) >= max_actions:
                skipped[action_name] = skipped.get(action_name, 0) + 1
                continue
            selected.append(action)
            counts[action_name] = counts.get(action_name, 0) + 1
        return selected, skipped

    def _maintenance_action_from_recommendation(self, recommendation: dict[str, object]) -> dict[str, object] | None:
        action_name = str(recommendation.get("action") or "")
        if action_name == "promote_procedure":
            procedure_id = str(recommendation.get("procedure_id") or "").strip()
            if not procedure_id:
                return None
            return {
                "action": "promote_procedure",
                "procedure_id": procedure_id,
                "title": recommendation.get("title"),
                "reason": recommendation.get("reason") or "procedure candidate reached promotion threshold",
                "priority": recommendation.get("priority"),
            }
        if action_name == "merge_plan":
            skill_ids = [str(item) for item in recommendation.get("skill_ids") or [] if str(item).strip()]
            if len(skill_ids) < 2:
                return None
            return {
                "action": "merge_plan",
                "skill_ids": skill_ids,
                "fingerprint": recommendation.get("fingerprint"),
                "reason": recommendation.get("reason") or "duplicate skill candidate",
                "priority": recommendation.get("priority"),
            }
        if action_name == "quality_plan":
            skill_id = str(recommendation.get("skill_id") or "").strip()
            if not skill_id:
                return None
            return {
                "action": "quality_plan",
                "skill_id": skill_id,
                "reason": recommendation.get("reason") or "quality review signal",
                "priority": recommendation.get("priority"),
            }
        return None

    def _dedupe_maintenance_actions(self, actions: list[dict[str, object]]) -> list[dict[str, object]]:
        deduped: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for action in actions:
            action_name = str(action.get("action") or "")
            if action_name == "promote_procedure":
                key = (action_name, str(action.get("procedure_id") or ""))
            elif action_name == "merge_plan":
                key = (action_name, ",".join(str(item) for item in action.get("skill_ids") or []))
            else:
                key = (action_name, str(action.get("skill_id") or ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(action)
        return deduped

    def _maintenance_action_sort_key(self, action: dict[str, object]) -> tuple[int, str, str]:
        order = {
            "quality_plan": 10,
            "merge_plan": 20,
            "promote_procedure": 30,
            "promote_template": 40,
            "mark_core": 50,
            "mark_observe": 60,
            "mark_stale": 70,
            "archive": 80,
        }
        return (
            order.get(str(action.get("action") or ""), 100),
            str(action.get("skill_id") or action.get("procedure_id") or ""),
            ",".join(str(item) for item in action.get("skill_ids") or []),
        )

    def _execute_maintenance_action(
        self,
        *,
        config: EffectiveConfig,
        action: dict[str, object],
        force: bool,
    ) -> dict[str, object]:
        action_name = str(action.get("action") or "")
        skill_id = str(action.get("skill_id") or "").strip() or None
        try:
            if action_name == "quality_plan":
                planned = self.plan_skill_review(
                    config=config,
                    skill_id=skill_id,
                    rationale=str(action.get("reason") or "Automatic skill maintenance review."),
                    dry_run=False,
                )
                if not planned.get("accepted"):
                    return planned
                applied = self.apply_skill_review(
                    config=config,
                    revision=str(planned.get("proposal_id") or ""),
                    dry_run=False,
                    force=force,
                )
                return {
                    "accepted": bool(applied.get("accepted")),
                    "applied": bool(applied.get("accepted") and applied.get("applied") is not False),
                    "mode": "curator_maintenance_action",
                    "action": action_name,
                    "skill_id": skill_id,
                    "proposal_id": planned.get("proposal_id"),
                    "plan_result": planned,
                    "apply_result": applied,
                    "error": applied.get("error"),
                    "reason": applied.get("reason"),
                }
            if action_name == "merge_plan":
                skill_ids = [str(item) for item in action.get("skill_ids") or [] if str(item).strip()]
                planned = self.plan_duplicate_merge(
                    config=config,
                    skill_id=skill_ids[0] if skill_ids else skill_id,
                    absorbed_into=None,
                    dry_run=False,
                    force=force,
                )
                if not planned.get("accepted"):
                    return planned
                applied = self.apply_duplicate_merge(
                    config=config,
                    revision=str(planned.get("proposal_id") or ""),
                    dry_run=False,
                    force=force,
                )
                return {
                    "accepted": bool(applied.get("accepted")),
                    "applied": bool(applied.get("accepted") and applied.get("applied") is not False),
                    "mode": "curator_maintenance_action",
                    "action": action_name,
                    "skill_ids": skill_ids,
                    "proposal_id": planned.get("proposal_id"),
                    "plan_result": planned,
                    "apply_result": applied,
                    "error": applied.get("error"),
                    "reason": applied.get("reason"),
                }
            if action_name == "promote_procedure":
                return self.promote_procedure(
                    config=config,
                    procedure_id=str(action.get("procedure_id") or ""),
                    skill_id=None,
                    dry_run=False,
                    force=force,
                )
            if action_name in {"mark_stale", "mark_core", "mark_observe"}:
                if not skill_id:
                    return self._refusal(f"{action_name} requires skill_id")
                usage = self._load_usage(config)
                item = self._usage_item(usage, skill_id)
                now = utc_now_iso()
                if action_name == "mark_stale":
                    item["state"] = "stale"
                    item["stale_at"] = now
                elif action_name == "mark_core":
                    item["tier"] = "core"
                    item["core_at"] = now
                else:
                    item["tier"] = "observe"
                    item["observed_at"] = now
                item["last_activity_at"] = now
                self._save_usage(config, usage)
                self.governance.record_history(
                    config,
                    SkillGovernanceRecord(
                        skill_id=skill_id,
                        action=f"curator_{action_name}",
                        created_at=now,
                        detail={"source": "maintenance", "reason": action.get("reason")},
                    ),
                )
                return {
                    "accepted": True,
                    "applied": True,
                    "mode": "curator_maintenance_action",
                    "action": action_name,
                    "skill_id": skill_id,
                }
            if action_name == "promote_template":
                if not skill_id:
                    return self._refusal("promote_template requires skill_id")
                return self._promote_reusable_template(config=config, skill_id=skill_id) | {
                    "mode": "curator_maintenance_action",
                    "action": action_name,
                    "skill_id": skill_id,
                }
            if action_name == "archive":
                if not skill_id:
                    return self._refusal("archive requires skill_id")
                result = self._apply_archive(
                    config=config,
                    change={"skill_id": skill_id, "force": force},
                    dry_run=False,
                ) | {"mode": "curator_maintenance_action"}
                if result.get("accepted"):
                    self.governance.record_history(
                        config,
                        SkillGovernanceRecord(
                            skill_id=skill_id,
                            action="curator_maintenance_archive",
                            created_at=utc_now_iso(),
                            detail={"source": "maintenance", "reason": action.get("reason"), "result": result},
                        ),
                    )
                return result
        except Exception as exc:  # pragma: no cover - defensive safety boundary
            return {
                "accepted": False,
                "mode": "curator_maintenance_action",
                "action": action_name,
                "skill_id": skill_id,
                "error": str(exc),
            }
        return {
            "accepted": False,
            "mode": "curator_maintenance_action",
            "action": action_name,
            "skill_id": skill_id,
            "reason": f"unsupported maintenance action '{action_name}'",
        }

    def _recommendation(
        self,
        *,
        action: str,
        skill_id: str,
        reason: str,
        priority: int,
        next_tool_call: dict[str, object],
        item: dict[str, object],
    ) -> dict[str, object]:
        return {
            "action": action,
            "skill_id": skill_id,
            "reason": reason,
            "priority": priority,
            "next_tool_call": next_tool_call,
            "tier": str(item.get("tier") or "active"),
            "utility_score": self._utility_score(item),
            "feedback_health": self._feedback_health(item),
        }

    def _review_priority(self, item: dict[str, object]) -> int:
        health = self._feedback_health(item)
        failure = self._safe_float(health.get("failure_confidence"))
        feedback_count = int(item.get("feedback_count") or 0)
        utility_penalty = max(20 - int(item.get("utility_score") or 0), 0)
        return min(round(failure * 100) + feedback_count * 10 + utility_penalty, 250)

    def _should_promote_template(self, *, config: EffectiveConfig, skill_id: str, item: dict[str, object]) -> bool:
        if not bool(config.skills_config.curator.template_promotion_enabled):
            return False
        if str(item.get("template_path") or "") == REUSABLE_TEMPLATE_PATH:
            return False
        if (self._workspace_root() / skill_id / REUSABLE_TEMPLATE_PATH).exists():
            return False
        use_count = int(item.get("use_count") or 0)
        context_count = int(item.get("context_count") or 0)
        return (
            use_count >= int(config.skills_config.curator.template_use_threshold or 0)
            and context_count >= int(config.skills_config.curator.template_context_threshold or 0)
        )

    def _promote_reusable_template(self, *, config: EffectiveConfig, skill_id: str) -> dict[str, object]:
        target = self._workspace_root() / skill_id / REUSABLE_TEMPLATE_PATH
        skill_path = self._workspace_root() / skill_id / "SKILL.md"
        if not skill_path.exists():
            return self._refusal(f"skill '{skill_id}' is not in the curator workspace root")
        if target.exists():
            self._record_template_usage(config=config, skill_id=skill_id)
            return {"accepted": True, "applied": False, "reason": "template already exists", "path": str(target)}
        text = self._build_reusable_template(skill_id=skill_id, skill_text=skill_path.read_text(encoding="utf-8"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self._record_template_usage(config=config, skill_id=skill_id)
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=skill_id,
                action="curator_template_promote",
                created_at=utc_now_iso(),
                detail={"template_path": REUSABLE_TEMPLATE_PATH, "path": str(target)},
            ),
        )
        return {"accepted": True, "applied": True, "path": str(target)}

    def _record_template_usage(self, *, config: EffectiveConfig, skill_id: str) -> None:
        usage = self._load_usage(config)
        item = self._usage_item(usage, skill_id)
        item["template_path"] = REUSABLE_TEMPLATE_PATH
        item["template_promoted_at"] = item.get("template_promoted_at") or utc_now_iso()
        item["last_activity_at"] = utc_now_iso()
        self._save_usage(config, usage)

    def _build_reusable_template(self, *, skill_id: str, skill_text: str) -> str:
        body = self._skill_body_text(skill_text)
        title = skill_id
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip() or title
                break
        scrubber = MemorySecretScrubber()
        bullets: list[str] = []
        for line in self._distill_merge_lines(body):
            scrubbed = scrubber.scrub(line).text.strip()
            if scrubbed and scrubbed not in bullets:
                bullets.append(scrubbed)
            if len(bullets) >= 8:
                break
        if not bullets:
            bullets = ["Capture the repeatable trigger, required inputs, execution steps, and verification evidence."]
        bullet_text = "\n".join(f"- {line}" for line in bullets)
        return (
            f"# Reusable Skill Template: {title}\n\n"
            "Use this template when converting repeated task-specific behavior into a reusable Anvil skill.\n\n"
            "## Trigger\n\n"
            "- Replace with the smallest reliable signal that should activate this skill.\n\n"
            "## Procedure\n\n"
            f"{bullet_text}\n\n"
            "## Evidence\n\n"
            "- Record commands, files, outputs, or user-visible results that prove the skill worked.\n"
        )

    def _write_run_report(self, *, config: EffectiveConfig, report: dict[str, object]) -> dict[str, object]:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        run_dir = self._runs_root(config) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "run.json"
        markdown_path = run_dir / "REPORT.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_lines = [
            "# Skill Curator Run",
            "",
            f"- started_at: {report.get('started_at')}",
            f"- dry_run: {report.get('dry_run')}",
            f"- actions: {len(report.get('actions', [])) if isinstance(report.get('actions'), list) else 0}",
            "",
        ]
        for item in report.get("actions", []):
            if isinstance(item, dict):
                markdown_lines.append(f"- {item.get('action')}: {item.get('skill_id') or item.get('skill_ids')} - {item.get('reason')}")
        recommendations = report.get("recommendations")
        if isinstance(recommendations, list) and recommendations:
            markdown_lines.extend(["", "## Recommendations", ""])
            for item in recommendations:
                if isinstance(item, dict):
                    target = item.get("skill_id") or item.get("skill_ids")
                    markdown_lines.append(f"- {item.get('action')}: {target} - {item.get('reason')} (priority {item.get('priority')})")
        markdown_path.write_text("\n".join(markdown_lines).rstrip() + "\n", encoding="utf-8")
        return {"run_id": run_id, "run_json_path": str(json_path), "report_path": str(markdown_path)}

    def _build_duplicate_merge_proposal(
        self,
        *,
        config: EffectiveConfig,
        group: dict[str, object] | None = None,
        skill_id: str | None = None,
        absorbed_into: str | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        selected_skill_id = self._normalize_skill_id(skill_id)
        if skill_id and selected_skill_id is None:
            return self._refusal("skill_id is required to be a safe slug")
        preferred_primary = self._normalize_skill_id(absorbed_into)
        if absorbed_into and preferred_primary is None:
            return self._refusal("absorbed_into must be a safe skill_id")

        groups = [group] if group is not None else self._duplicate_groups_with_fingerprints()
        selected_group = None
        for candidate in groups:
            ids = [str(item) for item in candidate.get("skill_ids") or []]
            if selected_skill_id and selected_skill_id not in ids:
                continue
            if preferred_primary and preferred_primary not in ids:
                continue
            selected_group = candidate
            break
        if selected_group is None:
            return self._refusal("no duplicate curator skill group matched the request")

        skill_ids = sorted({str(item) for item in selected_group.get("skill_ids") or []})
        safe_ids = [item for item in skill_ids if self._normalize_skill_id(item) is not None]
        if len(safe_ids) != len(skill_ids) or len(skill_ids) < 2:
            return self._refusal("duplicate group must contain at least two safe skill_ids")
        missing = [item for item in skill_ids if not self._is_workspace_skill_id(item)]
        if missing:
            return self._refusal(f"duplicate group contains non-workspace skills: {missing}")

        usage = self._load_usage(config)
        manifests = {
            manifest.skill_id: manifest
            for manifest in self.loader.discover([self._workspace_root()]).manifests
            if manifest.skill_id in skill_ids
        }
        if set(manifests) != set(skill_ids):
            missing_manifest_ids = sorted(set(skill_ids) - set(manifests))
            return self._refusal(f"duplicate group contains undiscoverable skills: {missing_manifest_ids}")

        primary_skill_id = preferred_primary or self._select_merge_primary(
            config=config,
            skill_ids=skill_ids,
            usage=usage,
            manifests=manifests,
        )
        source_skill_ids = [item for item in skill_ids if item != primary_skill_id]
        pinned_sources = [item for item in source_skill_ids if bool(usage.get(item, {}).get("pinned"))]
        fingerprint = str(selected_group.get("fingerprint") or self._fingerprint_text(" ".join(skill_ids)))
        existing = self._find_existing_merge_proposal(
            config=config,
            fingerprint=fingerprint,
            skill_ids=skill_ids,
            primary_skill_id=primary_skill_id,
        )
        merge_metadata = existing.metadata("merge_proposal")
        if existing.scan_truncated:
            return self._refusal("curator merge proposal scan truncated before merge_plan", **merge_metadata)
        if existing.proposal is not None:
            proposal = dict(existing.proposal)
            proposal["requires_force"] = bool(pinned_sources) and not force
            proposal["pinned_source_skill_ids"] = pinned_sources
            return {"accepted": True, "proposal": proposal, "reused": True, **merge_metadata}
        fingerprint_slug = re.sub(r"[^a-z0-9]+", "-", fingerprint.lower()).strip("-")[:40] or "duplicates"
        proposal_id = f"merge-{fingerprint_slug}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        proposal = {
            "proposal_id": proposal_id,
            "created_at": utc_now_iso(),
            "status": "proposed",
            "reason": "Duplicate skills share a normalized title/summary fingerprint.",
            "fingerprint": fingerprint,
            "skill_ids": skill_ids,
            "primary_skill_id": primary_skill_id,
            "source_skill_ids": source_skill_ids,
            "merge_strategy": "archive_duplicates_preserve_primary",
            "requires_force": bool(pinned_sources) and not force,
            "pinned_source_skill_ids": pinned_sources,
            "safety": {
                "hard_delete": False,
                "primary_skill_rewrite": False,
                "primary_skill_patch": True,
                "source_skills_archived": True,
                "source_support_files_preserved_in_archive": True,
            },
            "primary_patch": self._build_merge_primary_patch(
                primary_skill_id=primary_skill_id,
                source_skill_ids=source_skill_ids,
            ),
            "primary_selection": {
                "reason": "preferred absorbed_into" if preferred_primary else "highest curator usage score",
                "score": self._merge_score(config=config, skill_id=primary_skill_id, usage=usage, manifest=manifests[primary_skill_id]),
            },
            "skills": [
                {
                    "skill_id": current_id,
                    "title": manifests[current_id].title,
                    "summary": manifests[current_id].summary,
                    "path": manifests[current_id].path,
                    "usage": {
                        "view_count": int(usage.get(current_id, {}).get("view_count") or 0),
                        "use_count": int(usage.get(current_id, {}).get("use_count") or 0),
                        "patch_count": int(usage.get(current_id, {}).get("patch_count") or 0),
                        "pinned": bool(usage.get(current_id, {}).get("pinned")),
                    },
                }
                for current_id in skill_ids
            ],
        }
        return {"accepted": True, "proposal": proposal, **merge_metadata}

    def _build_skill_review_proposal(
        self,
        *,
        config: EffectiveConfig,
        skill_id: str | None,
        rationale: str | None = None,
    ) -> dict[str, object]:
        normalized = self._normalize_skill_id(skill_id)
        if normalized is None:
            return self._refusal("quality_plan requires a safe skill_id")
        if not self._is_workspace_skill_id(normalized):
            return self._refusal(f"skill '{normalized}' is not in the curator workspace root")
        manifest = next(
            (item for item in self.loader.discover([self._workspace_root()]).manifests if item.skill_id == normalized),
            None,
        )
        if manifest is None:
            return self._refusal(f"skill '{normalized}' is not discoverable")
        usage = self._load_usage(config)
        usage_item = self._usage_item(usage, normalized)
        skill_path = self._workspace_root() / normalized / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")
        patch = self._build_review_patch(skill_text=skill_text, usage_item=usage_item, rationale=rationale)
        recommendations = self._review_recommendations(usage_item=usage_item, manifest=manifest, patch=patch)
        proposal_id = f"review-{normalized}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        proposal = {
            "proposal_id": proposal_id,
            "created_at": utc_now_iso(),
            "status": "proposed",
            "skill_id": normalized,
            "reason": "Curator quality review based on usage, feedback, tier, and skill text signals.",
            "rationale": (rationale or "").strip()[:MAX_RATIONALE_CHARS],
            "recommendations": recommendations,
            "safety": {
                "hard_delete": False,
                "direct_skill_mutation": False,
                "append_only_patch_candidate": bool(patch.get("proposed")),
                "requires_explicit_apply_action": True,
            },
            "usage": {
                "tier": str(usage_item.get("tier") or "active"),
                "utility_score": int(usage_item.get("utility_score") or 0),
                "view_count": int(usage_item.get("view_count") or 0),
                "use_count": int(usage_item.get("use_count") or 0),
                "patch_count": int(usage_item.get("patch_count") or 0),
                "feedback_count": int(usage_item.get("feedback_count") or 0),
                "success_count": int(usage_item.get("success_count") or 0),
                "failure_count": int(usage_item.get("failure_count") or 0),
                "neutral_count": int(usage_item.get("neutral_count") or 0),
                "feedback_by_source": usage_item.get("feedback_by_source") if isinstance(usage_item.get("feedback_by_source"), dict) else {},
                "confidence_totals": usage_item.get("confidence_totals") if isinstance(usage_item.get("confidence_totals"), dict) else {},
                "feedback_health": self._feedback_health(usage_item),
                "context_count": int(usage_item.get("context_count") or 0),
                "last_activity_at": usage_item.get("last_activity_at"),
                "last_feedback": usage_item.get("last_feedback"),
                "pinned": bool(usage_item.get("pinned")),
            },
            "skill": {
                "title": manifest.title,
                "summary": manifest.summary,
                "path": manifest.path,
                "valid": bool(manifest.valid),
            },
            "patch": patch,
        }
        return {"accepted": True, "proposal": proposal}

    def _build_review_patch(
        self,
        *,
        skill_text: str,
        usage_item: dict[str, object],
        rationale: str | None,
    ) -> dict[str, object]:
        body = self._skill_body_text(skill_text)
        existing_fingerprints = self._line_fingerprints(body)
        candidates: list[str] = []
        failure_count = int(usage_item.get("failure_count") or 0)
        success_count = int(usage_item.get("success_count") or 0)
        feedback_count = int(usage_item.get("feedback_count") or 0)
        if failure_count:
            candidates.append("Add explicit failure checks and verification evidence before relying on this skill.")
        if feedback_count and failure_count >= success_count:
            candidates.append("Clarify the trigger and expected output so the skill is not used in weak-fit situations.")
        if int(usage_item.get("context_count") or 0) >= 2:
            candidates.append("Preserve reusable steps as template material for future related skills.")
        body_scrubbed = MemorySecretScrubber().scrub(body)
        if body_scrubbed.redacted:
            candidates.append(f"Remove or generalize secret-like skill text: {body_scrubbed.text[:240]}")
        last_feedback = usage_item.get("last_feedback") if isinstance(usage_item.get("last_feedback"), dict) else {}
        feedback_rationale = str(last_feedback.get("rationale") or "").strip()
        if feedback_rationale:
            candidates.append(f"Address latest feedback: {feedback_rationale}")
        if rationale:
            candidates.append(f"Review rationale: {rationale.strip()}")
        if not candidates:
            candidates.append("Review trigger, procedure, and verification sections for clarity and measurable evidence.")

        scrubber = MemorySecretScrubber()
        notes: list[str] = []
        redacted_rules: list[str] = []
        for candidate in candidates:
            fingerprint = self._fingerprint_text(candidate)
            if not fingerprint or fingerprint in existing_fingerprints:
                continue
            scrubbed = scrubber.scrub(candidate)
            note = scrubbed.text.strip()
            if not note:
                continue
            if scrubbed.redacted:
                redacted_rules.extend(scrubbed.rule_ids)
            bullet = f"- {note}"
            if bullet not in notes:
                notes.append(bullet)
            if len(notes) >= MAX_MERGE_DISTILLED_LINES:
                break
        if not notes:
            return {
                "proposed": False,
                "reason": "no unique review notes found",
                "redacted_rules": tuple(dict.fromkeys(redacted_rules)),
            }
        marker = "## Curator Review Notes"
        append_text = "\n\n" + marker + "\n\n" + "\n".join(notes) + "\n"
        if len(append_text) > MAX_MERGE_PATCH_CHARS:
            append_text = append_text[: MAX_MERGE_PATCH_CHARS - 1].rstrip() + "\n"
        return {
            "proposed": True,
            "strategy": "append_redacted_review_notes",
            "section_heading": marker,
            "append_text": append_text,
            "redacted_rules": tuple(dict.fromkeys(redacted_rules)),
            "line_count": len(notes),
        }

    def _review_recommendations(
        self,
        *,
        usage_item: dict[str, object],
        manifest: SkillManifest,
        patch: dict[str, object],
    ) -> list[str]:
        recommendations: list[str] = []
        failure_count = int(usage_item.get("failure_count") or 0)
        success_count = int(usage_item.get("success_count") or 0)
        use_count = int(usage_item.get("use_count") or 0)
        if failure_count:
            recommendations.append(f"Address {failure_count} failure feedback item(s) before promoting this skill.")
        if success_count and failure_count == 0:
            recommendations.append("Preserve successful behavior; prefer small append-only clarifications.")
        if use_count == 0:
            recommendations.append("Keep this skill in observation until it has real prompt-time use.")
        if not manifest.valid:
            recommendations.append("Fix manifest validity before any promotion or merge.")
        if patch.get("proposed"):
            recommendations.append("Review the bounded patch candidate before applying it.")
        if not recommendations:
            recommendations.append("No urgent changes; keep collecting usage and feedback signals.")
        return recommendations

    def _find_existing_review_proposal(self, *, config: EffectiveConfig, skill_id: str) -> _CuratorProposalLookup:
        root = self._review_proposals_root(config)
        if not root.exists():
            return _CuratorProposalLookup(
                proposal=None,
                scanned_path_count=0,
                max_scanned_paths=_bounded_curator_backup_scan_limit(),
            )
        proposal_scan = _scan_curator_proposal_files(root)
        for path in reversed(proposal_scan.paths):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "proposed") != "proposed":
                continue
            if str(payload.get("skill_id") or "") != skill_id:
                continue
            return _CuratorProposalLookup(
                proposal=payload,
                scanned_path_count=proposal_scan.scanned_path_count,
                max_scanned_paths=proposal_scan.max_scanned_paths,
                scan_truncated=proposal_scan.scan_truncated,
            )
        return _CuratorProposalLookup(
            proposal=None,
            scanned_path_count=proposal_scan.scanned_path_count,
            max_scanned_paths=proposal_scan.max_scanned_paths,
            scan_truncated=proposal_scan.scan_truncated,
        )

    def _record_quality_plan_history(self, *, config: EffectiveConfig, proposal: dict[str, object]) -> None:
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=str(proposal["skill_id"]),
                action="curator_quality_plan",
                created_at=utc_now_iso(),
                detail={
                    "proposal_id": proposal["proposal_id"],
                    "status": proposal.get("status"),
                    "recommendation_count": len(proposal.get("recommendations") or []),
                },
            ),
        )

    def _find_existing_merge_proposal(
        self,
        *,
        config: EffectiveConfig,
        fingerprint: str,
        skill_ids: list[str],
        primary_skill_id: str,
    ) -> _CuratorProposalLookup:
        root = self._merge_proposals_root(config)
        if not root.exists():
            return _CuratorProposalLookup(
                proposal=None,
                scanned_path_count=0,
                max_scanned_paths=_bounded_curator_backup_scan_limit(),
            )
        expected_ids = sorted(skill_ids)
        proposal_scan = _scan_curator_proposal_files(root)
        for path in reversed(proposal_scan.paths):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status") or "proposed") != "proposed":
                continue
            if str(payload.get("fingerprint") or "") != fingerprint:
                continue
            if sorted(str(item) for item in payload.get("skill_ids") or []) != expected_ids:
                continue
            if str(payload.get("primary_skill_id") or "") != primary_skill_id:
                continue
            return _CuratorProposalLookup(
                proposal=payload,
                scanned_path_count=proposal_scan.scanned_path_count,
                max_scanned_paths=proposal_scan.max_scanned_paths,
                scan_truncated=proposal_scan.scan_truncated,
            )
        return _CuratorProposalLookup(
            proposal=None,
            scanned_path_count=proposal_scan.scanned_path_count,
            max_scanned_paths=proposal_scan.max_scanned_paths,
            scan_truncated=proposal_scan.scan_truncated,
        )

    def _select_merge_primary(
        self,
        *,
        config: EffectiveConfig,
        skill_ids: list[str],
        usage: dict[str, dict[str, object]],
        manifests: dict[str, SkillManifest],
    ) -> str:
        return sorted(
            skill_ids,
            key=lambda current_id: (
                -self._merge_score(config=config, skill_id=current_id, usage=usage, manifest=manifests[current_id]),
                current_id,
            ),
        )[0]

    def _merge_score(
        self,
        *,
        config: EffectiveConfig,
        skill_id: str,
        usage: dict[str, dict[str, object]],
        manifest: SkillManifest,
    ) -> int:
        item = usage.get(skill_id, {})
        score = int(item.get("use_count") or 0) * 100
        score += int(item.get("view_count") or 0) * 10
        score += int(item.get("patch_count") or 0) * 2
        if manifest.valid:
            score += 5
        if self._is_pinned(config=config, skill_id=skill_id):
            score += 10_000
        return score

    def _record_merge_plan_history(self, *, config: EffectiveConfig, proposal: dict[str, object]) -> None:
        self.governance.record_history(
            config,
            SkillGovernanceRecord(
                skill_id=str(proposal["primary_skill_id"]),
                action="curator_merge_plan",
                created_at=utc_now_iso(),
                detail={
                    "proposal_id": proposal["proposal_id"],
                    "source_skill_ids": proposal["source_skill_ids"],
                    "requires_force": proposal["requires_force"],
                    "status": proposal.get("status"),
                },
            ),
        )

    def _build_merge_primary_patch(self, *, primary_skill_id: str, source_skill_ids: list[str]) -> dict[str, object]:
        target = self._workspace_root() / primary_skill_id / "SKILL.md"
        if not target.exists():
            return {"proposed": False, "reason": "primary SKILL.md missing"}
        primary_text = target.read_text(encoding="utf-8")
        primary_body = self._skill_body_text(primary_text)
        primary_fingerprints = self._line_fingerprints(primary_body)
        source_notes: list[str] = []
        redacted_rules: list[str] = []
        scrubber = MemorySecretScrubber()
        for source_skill_id in source_skill_ids:
            source_path = self._workspace_root() / source_skill_id / "SKILL.md"
            if not source_path.exists():
                continue
            source_body = self._skill_body_text(source_path.read_text(encoding="utf-8"))
            for line in self._distill_merge_lines(source_body):
                line_fingerprint = self._fingerprint_text(line)
                if not line_fingerprint or line_fingerprint in primary_fingerprints:
                    continue
                scrubbed = scrubber.scrub(line)
                normalized = scrubbed.text.strip()
                if not normalized:
                    continue
                if scrubbed.redacted:
                    redacted_rules.extend(scrubbed.rule_ids)
                bullet = f"- From {source_skill_id}: {normalized}"
                if bullet not in source_notes:
                    source_notes.append(bullet)
                if len(source_notes) >= MAX_MERGE_DISTILLED_LINES:
                    break
            if len(source_notes) >= MAX_MERGE_DISTILLED_LINES:
                break
        if not source_notes:
            return {
                "proposed": False,
                "reason": "no unique source details found",
                "redacted_rules": tuple(dict.fromkeys(redacted_rules)),
            }
        marker = "## Curated Merge Notes"
        section = "\n\n" + marker + "\n\n" + "\n".join(source_notes) + "\n"
        if len(section) > MAX_MERGE_PATCH_CHARS:
            section = section[: MAX_MERGE_PATCH_CHARS - 1].rstrip() + "\n"
        return {
            "proposed": True,
            "strategy": "append_distilled_redacted_notes",
            "section_heading": marker,
            "append_text": section,
            "redacted_rules": tuple(dict.fromkeys(redacted_rules)),
            "line_count": len(source_notes),
        }

    def _apply_merge_primary_patch(
        self,
        *,
        config: EffectiveConfig,
        primary_skill_id: str,
        primary_patch: dict[str, object],
        dry_run: bool,
    ) -> dict[str, object]:
        if not primary_patch.get("proposed"):
            return {
                "accepted": True,
                "applied": False,
                "reason": primary_patch.get("reason") or "no primary patch proposed",
            }
        append_text = str(primary_patch.get("append_text") or "")
        if not append_text.strip():
            return self._refusal("merge primary patch is empty")
        if len(append_text) > MAX_MERGE_PATCH_CHARS:
            return self._refusal(f"merge primary patch exceeds {MAX_MERGE_PATCH_CHARS} chars")
        target_dir = self._workspace_root() / primary_skill_id
        target = target_dir / "SKILL.md"
        if not target.exists():
            return self._refusal(f"primary skill '{primary_skill_id}' is not in the curator workspace root")
        current = target.read_text(encoding="utf-8")
        if append_text.strip() in current:
            return {"accepted": True, "applied": False, "reason": "primary patch already present"}
        if dry_run:
            return {
                "accepted": True,
                "dry_run": True,
                "would_patch": str(target),
                "redacted_rules": list(primary_patch.get("redacted_rules") or []),
            }
        backup = self._backup_existing(config=config, skill_id=primary_skill_id, target_dir=target_dir)
        target.write_text(current.rstrip() + append_text, encoding="utf-8")
        try:
            manifest = self._validate_written_skill(skill_id=primary_skill_id, skill_dir=target_dir)
        except ValueError as exc:
            if backup is not None:
                self._restore_backup(backup_path=backup.path, target_dir=target_dir)
            return self._refusal(str(exc))
        self._record_patch(config=config, skill_id=primary_skill_id)
        return {
            "accepted": True,
            "applied": True,
            "change_action": "merge_primary_patch",
            "skill_id": primary_skill_id,
            "path": str(target),
            **_backup_result_metadata(backup),
            "redacted_rules": list(primary_patch.get("redacted_rules") or []),
            "manifest": manifest.model_dump(mode="json"),
        }

    def _apply_review_patch(
        self,
        *,
        config: EffectiveConfig,
        skill_id: str,
        patch: dict[str, object],
        dry_run: bool,
    ) -> dict[str, object]:
        if not patch.get("proposed"):
            return {
                "accepted": True,
                "applied": False,
                "reason": patch.get("reason") or "no review patch proposed",
            }
        if str(patch.get("strategy") or "") != "append_redacted_review_notes":
            return self._refusal("review patch strategy must be append_redacted_review_notes")
        append_text = str(patch.get("append_text") or "")
        if not append_text.strip():
            return self._refusal("review patch is empty")
        if len(append_text) > MAX_MERGE_PATCH_CHARS:
            return self._refusal(f"review patch exceeds {MAX_MERGE_PATCH_CHARS} chars")
        target_dir = self._workspace_root() / skill_id
        target = target_dir / "SKILL.md"
        if not target.exists():
            return self._refusal(f"skill '{skill_id}' is not in the curator workspace root")
        current = target.read_text(encoding="utf-8")
        if append_text.strip() in current:
            return {"accepted": True, "applied": False, "reason": "review patch already present"}
        if dry_run:
            return {
                "accepted": True,
                "dry_run": True,
                "would_patch": str(target),
                "redacted_rules": list(patch.get("redacted_rules") or []),
            }
        backup = self._backup_existing(config=config, skill_id=skill_id, target_dir=target_dir)
        target.write_text(current.rstrip() + append_text, encoding="utf-8")
        try:
            manifest = self._validate_written_skill(skill_id=skill_id, skill_dir=target_dir)
        except ValueError as exc:
            if backup is not None:
                self._restore_backup(backup_path=backup.path, target_dir=target_dir)
            return self._refusal(str(exc))
        self._record_patch(config=config, skill_id=skill_id)
        return {
            "accepted": True,
            "applied": True,
            "change_action": "review_patch",
            "skill_id": skill_id,
            "path": str(target),
            **_backup_result_metadata(backup),
            "redacted_rules": list(patch.get("redacted_rules") or []),
            "manifest": manifest.model_dump(mode="json"),
        }

    def _write_merge_proposal(self, *, config: EffectiveConfig, proposal: dict[str, object]) -> dict[str, object]:
        proposal_id = self._normalize_merge_proposal_id(str(proposal.get("proposal_id") or ""))
        if proposal_id is None:
            raise ValueError("merge proposal_id must be a safe slug")
        proposal_dir = self._merge_proposals_root(config) / proposal_id
        proposal_dir.mkdir(parents=True, exist_ok=True)
        json_path = proposal_dir / "proposal.json"
        markdown_path = proposal_dir / "PROPOSAL.md"
        json_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        markdown_lines = [
            "# Skill Merge Proposal",
            "",
            f"- proposal_id: {proposal_id}",
            f"- status: {proposal.get('status')}",
            f"- primary_skill_id: {proposal.get('primary_skill_id')}",
            f"- source_skill_ids: {', '.join(str(item) for item in proposal.get('source_skill_ids') or [])}",
            f"- requires_force: {proposal.get('requires_force')}",
            f"- strategy: {proposal.get('merge_strategy')}",
            "",
            "## Safety",
            "",
            "- Source skills are archived, not deleted.",
            "- Primary SKILL.md is patched with a bounded, redacted distilled note when source details are unique.",
            "- Source support files remain in the archive for rollback.",
        ]
        primary_patch = proposal.get("primary_patch")
        if isinstance(primary_patch, dict):
            markdown_lines.extend(
                [
                    "",
                    "## Primary Patch",
                    "",
                    f"- proposed: {primary_patch.get('proposed')}",
                    f"- redacted_rules: {', '.join(str(item) for item in primary_patch.get('redacted_rules') or [])}",
                ]
            )
        markdown_path.write_text("\n".join(markdown_lines).rstrip() + "\n", encoding="utf-8")
        return {"proposal_path": str(json_path), "proposal_report_path": str(markdown_path)}

    def _write_review_proposal(self, *, config: EffectiveConfig, proposal: dict[str, object]) -> dict[str, object]:
        proposal_id = self._normalize_merge_proposal_id(str(proposal.get("proposal_id") or ""))
        if proposal_id is None:
            raise ValueError("review proposal_id must be a safe slug")
        proposal_dir = self._review_proposals_root(config) / proposal_id
        proposal_dir.mkdir(parents=True, exist_ok=True)
        json_path = proposal_dir / "proposal.json"
        markdown_path = proposal_dir / "PROPOSAL.md"
        json_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        patch = proposal.get("patch") if isinstance(proposal.get("patch"), dict) else {}
        markdown_lines = [
            "# Skill Review Proposal",
            "",
            f"- proposal_id: {proposal_id}",
            f"- status: {proposal.get('status')}",
            f"- skill_id: {proposal.get('skill_id')}",
            f"- patch_proposed: {patch.get('proposed')}",
            "",
            "## Recommendations",
            "",
        ]
        for recommendation in proposal.get("recommendations") or []:
            markdown_lines.append(f"- {recommendation}")
        markdown_lines.extend(
            [
                "",
                "## Safety",
                "",
                "- This proposal does not mutate SKILL.md.",
                "- Any patch candidate is append-only, bounded, and secret-scrubbed.",
                "- A separate explicit apply action is required before changing the skill.",
            ]
        )
        if patch:
            markdown_lines.extend(
                [
                    "",
                    "## Patch Candidate",
                    "",
                    f"- redacted_rules: {', '.join(str(item) for item in patch.get('redacted_rules') or [])}",
                    f"- line_count: {patch.get('line_count')}",
                ]
            )
        markdown_path.write_text("\n".join(markdown_lines).rstrip() + "\n", encoding="utf-8")
        return {"proposal_path": str(json_path), "proposal_report_path": str(markdown_path)}

    def _load_merge_proposal(self, *, config: EffectiveConfig, revision: str | None) -> dict[str, object]:
        proposal_id = self._normalize_merge_proposal_id(revision)
        if proposal_id is None:
            return self._refusal("merge_apply requires revision=<proposal_id>")
        proposal_path = self._merge_proposals_root(config) / proposal_id / "proposal.json"
        if not proposal_path.exists():
            return self._refusal(f"unknown merge proposal revision '{revision}'")
        try:
            payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return self._refusal(f"merge proposal '{revision}' could not be read: {exc}")
        if not isinstance(payload, dict):
            return self._refusal(f"merge proposal '{revision}' is not a JSON object")
        if str(payload.get("status") or "proposed") == "applied":
            return self._refusal(f"merge proposal '{revision}' is already applied")
        return {"accepted": True, "proposal": payload}

    def _load_review_proposal(self, *, config: EffectiveConfig, revision: str | None) -> dict[str, object]:
        proposal_id = self._normalize_merge_proposal_id(revision)
        if proposal_id is None:
            return self._refusal("review_apply requires revision=<proposal_id>")
        proposal_path = self._review_proposals_root(config) / proposal_id / "proposal.json"
        if not proposal_path.exists():
            return self._refusal(f"unknown review proposal revision '{revision}'")
        try:
            payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return self._refusal(f"review proposal '{revision}' could not be read: {exc}")
        if not isinstance(payload, dict):
            return self._refusal(f"review proposal '{revision}' is not a JSON object")
        if str(payload.get("status") or "proposed") == "applied":
            return self._refusal(f"review proposal '{revision}' is already applied")
        return {"accepted": True, "proposal": payload}

    def _normalize_merge_proposal_id(self, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped or not MERGE_PROPOSAL_RE.match(stripped):
            return None
        return stripped

    def _duplicate_groups(self) -> list[list[str]]:
        return [list(item["skill_ids"]) for item in self._duplicate_groups_with_fingerprints()]

    def _duplicate_groups_with_fingerprints(self) -> list[dict[str, object]]:
        manifests = self.loader.discover([self._workspace_root()]).manifests
        buckets: dict[str, list[str]] = {}
        for manifest in manifests:
            if not manifest.valid:
                continue
            key = self._fingerprint_text(f"{manifest.title} {manifest.summary}")
            if not key:
                continue
            buckets.setdefault(key, []).append(manifest.skill_id)
        return [
            {"fingerprint": key, "skill_ids": sorted(ids)}
            for key, ids in sorted(buckets.items())
            if len(ids) > 1
        ]

    def _fingerprint_text(self, value: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", value.lower())
        return " ".join(sorted(set(tokens)))

    def _fingerprint_digest(self, value: str) -> str:
        import hashlib

        normalized = self._fingerprint_text(value)
        return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()

    def _fingerprint_overlap(self, left: str, right: str) -> float:
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)

    def _skill_body_text(self, text: str) -> str:
        stripped = text.lstrip()
        if not stripped.startswith("---\n"):
            return text
        _, _, remainder = stripped.partition("---\n")
        _, separator, body = remainder.partition("\n---\n")
        return body if separator else text

    def _line_fingerprints(self, text: str) -> set[str]:
        return {
            fingerprint
            for fingerprint in (self._fingerprint_text(line) for line in self._distill_merge_lines(text))
            if fingerprint
        }

    def _distill_merge_lines(self, text: str) -> list[str]:
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            normalized = re.sub(r"^[-*]\s+", "", line).strip()
            normalized = re.sub(r"^\d+[.)]\s+", "", normalized).strip()
            if not normalized or len(normalized) < 16:
                continue
            lines.append(normalized[:500])
        return lines

    def _parse_time(self, value: object) -> datetime | None:
        if not value:
            return None
        try:
            text = str(value)
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None

    def _normalize_skill_id(self, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped or not SKILL_ID_RE.match(stripped):
            return None
        return stripped

    def _safe_string_list(self, value: list[str] | tuple[str, ...] | None) -> list[str]:
        if not value:
            return []
        items = []
        for item in value:
            normalized = str(item).strip()
            if normalized and len(normalized) <= 80:
                items.append(normalized)
        return items[:20]

    def _refusal(self, reason: str, **extra: object) -> dict[str, object]:
        return {"accepted": False, "error": reason} | extra


def _bounded_curator_backup_scan_limit() -> int:
    configured = DEFAULT_CURATOR_BACKUP_SCAN_LIMIT
    if configured < 1:
        return 1
    return min(configured, MAX_CURATOR_BACKUP_SCAN_LIMIT)


def _scan_curator_tree_files(root: Path) -> _CuratorTreeFileScan:
    max_scanned_paths = _bounded_curator_backup_scan_limit()
    files: list[tuple[str, str]] = []
    stack: list[tuple[str, str]] = [("", os.fspath(root))]
    scanned_path_count = 0
    scan_truncated = False
    while stack:
        relative_dir, absolute_dir = stack.pop()
        try:
            iterator = os.scandir(absolute_dir)
        except OSError:
            continue
        with iterator as entries:
            for entry in entries:
                if scanned_path_count >= max_scanned_paths:
                    scan_truncated = True
                    stack.clear()
                    break
                scanned_path_count += 1
                relative_path = f"{relative_dir}/{entry.name}" if relative_dir else entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((relative_path, entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        files.append((relative_path.replace("\\", "/"), entry.path))
                except OSError:
                    files.append((relative_path.replace("\\", "/"), entry.path))
        if scan_truncated:
            break
    return _CuratorTreeFileScan(
        files=tuple(sorted(files, key=lambda item: item[0])),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _scan_curator_backup_archive(archive: zipfile.ZipFile) -> _CuratorBackupArchiveScan:
    max_scanned_paths = _bounded_curator_backup_scan_limit()
    entries: list[_CuratorBackupArchiveEntry] = []
    scanned_path_count = 0
    scan_truncated = False
    for info in archive.filelist:
        if scanned_path_count >= max_scanned_paths:
            scan_truncated = True
            break
        scanned_path_count += 1
        entries.append(_CuratorBackupArchiveEntry(info=info, filename=str(info.filename).replace("\\", "/")))
    return _CuratorBackupArchiveScan(
        entries=tuple(entries),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _scan_curator_direct_candidates(
    root: Path,
    *,
    prefix: str = "",
    suffix: str = "",
    files_only: bool = False,
    directories_only: bool = False,
) -> _CuratorCandidateScan:
    max_scanned_paths = _bounded_curator_backup_scan_limit()
    paths: list[Path] = []
    scanned_path_count = 0
    scan_truncated = False
    try:
        iterator = os.scandir(root)
    except OSError:
        return _CuratorCandidateScan(
            paths=(),
            scanned_path_count=0,
            max_scanned_paths=max_scanned_paths,
            scan_truncated=False,
        )
    with iterator as entries:
        for entry in entries:
            if scanned_path_count >= max_scanned_paths:
                scan_truncated = True
                break
            scanned_path_count += 1
            if prefix and not entry.name.startswith(prefix):
                continue
            if suffix and not entry.name.endswith(suffix):
                continue
            try:
                if files_only and not entry.is_file(follow_symlinks=False):
                    continue
                if directories_only and not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            paths.append(Path(entry.path))
    return _CuratorCandidateScan(
        paths=tuple(sorted(paths, key=lambda path: path.name)),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _resolve_curator_backup_revision(*, backup_dir: Path, revision: str) -> Path | None:
    revision_name = str(revision).strip()
    if not revision_name:
        return None
    if "/" in revision_name or "\\" in revision_name:
        return None
    names = [revision_name]
    if not revision_name.endswith(".skill"):
        names.append(f"{revision_name}.skill")
    backup_root = backup_dir.resolve()
    for name in names:
        candidate = (backup_dir / name).resolve()
        if candidate != backup_root and backup_root in candidate.parents and candidate.is_file():
            return candidate
    return None


def _scan_curator_proposal_files(root: Path) -> _CuratorCandidateScan:
    max_scanned_paths = _bounded_curator_backup_scan_limit()
    paths: list[Path] = []
    scanned_path_count = 0
    scan_truncated = False
    try:
        iterator = os.scandir(root)
    except OSError:
        return _CuratorCandidateScan(
            paths=(),
            scanned_path_count=0,
            max_scanned_paths=max_scanned_paths,
            scan_truncated=False,
        )
    with iterator as entries:
        for entry in entries:
            if scanned_path_count >= max_scanned_paths:
                scan_truncated = True
                break
            scanned_path_count += 1
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            proposal_path = Path(entry.path) / "proposal.json"
            if proposal_path.is_file():
                paths.append(proposal_path)
    return _CuratorCandidateScan(
        paths=tuple(sorted(paths, key=lambda path: path.parent.name)),
        scanned_path_count=scanned_path_count,
        max_scanned_paths=max_scanned_paths,
        scan_truncated=scan_truncated,
    )


def _candidate_scan_metadata(prefix: str, scan: _CuratorCandidateScan) -> dict[str, object]:
    return {
        f"{prefix}_scanned_path_count": scan.scanned_path_count,
        f"{prefix}_max_scanned_paths": scan.max_scanned_paths,
        f"{prefix}_scan_truncated": scan.scan_truncated,
    }


def _prefixed_items(payload: dict[str, object], prefix: str) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key.startswith(prefix)}


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _backup_result_metadata(backup: _CuratorBackupResult | None) -> dict[str, object]:
    if backup is None:
        return {
            "backup_path": None,
            "backup_scanned_path_count": 0,
            "backup_max_scanned_paths": 0,
            "backup_scan_truncated": False,
        }
    return backup.metadata()
