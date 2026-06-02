from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from anvil.agents import ThreadState
from anvil.config import EffectiveConfig
from anvil.skills import ProcedureLearningService, SkillsService

from .contracts import (
    TrajectoryBatchExportRequest,
    TrajectoryBatchExportResult,
    TrajectoryBatchManifest,
    TrajectoryExportEntry,
    TrajectoryExportOptions,
    TrajectoryQualityStatus,
)
from .exporter import ThreadTrajectoryExporter


class TrajectoryBatchExporter:
    """Batch exporter for durable checkpointer state."""

    def __init__(
        self,
        *,
        checkpointer,
        export_root: str | Path,
        exporter: ThreadTrajectoryExporter | None = None,
        config: EffectiveConfig | None = None,
        skills_service: SkillsService | None = None,
        procedure_learning_service: ProcedureLearningService | None = None,
    ) -> None:
        self.checkpointer = checkpointer
        self.export_root = Path(export_root).expanduser().resolve()
        self.exporter = exporter or ThreadTrajectoryExporter()
        self.config = config
        self.skills_service = skills_service
        self.procedure_learning_service = procedure_learning_service or ProcedureLearningService()

    def export(
        self,
        request: TrajectoryBatchExportRequest | None = None,
    ) -> tuple[TrajectoryBatchExportResult, TrajectoryBatchManifest]:
        request = request or TrajectoryBatchExportRequest()
        states, missing = self._load_states(request.thread_ids)
        dataset_id = f"trajectory-{uuid4().hex[:12]}"
        output_path = self._resolve_output_path(request.output_path, dataset_id=dataset_id, suffix="jsonl")

        raw_result = self.exporter.export_threads(
            states,
            path=None,
            options=request.options,
        )
        kept_entries, quality_diagnostics = self._filter_entries_by_quality(
            raw_result.entries,
            min_quality_status=request.min_quality_status,
        )
        learning_summary, learning_diagnostics = self._learn_procedures_from_entries(
            request=request,
            entries=kept_entries,
            states_by_thread_id={state.identity.thread_id: state for state in states},
        )
        if request.write_jsonl:
            self.exporter.write_jsonl(kept_entries, path=output_path, options=request.options)
        diagnostics = [*missing, *raw_result.diagnostics, *quality_diagnostics, *learning_diagnostics]
        missing_count = len(missing)
        quality_filtered_count = len(raw_result.entries) - len(kept_entries)
        entries_for_manifest = list(kept_entries)
        entries_for_response = entries_for_manifest if request.include_entries else []
        result = raw_result.model_copy(
            update={
                "exported_count": len(kept_entries),
                "entries": entries_for_response,
                "diagnostics": diagnostics,
                "skipped_count": raw_result.skipped_count + missing_count + quality_filtered_count,
                "path": str(output_path) if request.write_jsonl else None,
            }
        )

        manifest = self._build_manifest(
            dataset_id=dataset_id,
            request=request,
            result=result,
            entries=entries_for_manifest,
            output_path=output_path if request.write_jsonl else None,
            diagnostics=diagnostics,
            learning_summary=learning_summary,
        )
        manifest_path = self._resolve_manifest_path(output_path, dataset_id=dataset_id)
        manifest = manifest.model_copy(update={"manifest_path": str(manifest_path)})
        self._write_manifest(manifest, manifest_path)
        return result, manifest

    def _filter_entries_by_quality(
        self,
        entries: list[TrajectoryExportEntry],
        *,
        min_quality_status: TrajectoryQualityStatus,
    ) -> tuple[list[TrajectoryExportEntry], list[str]]:
        kept: list[TrajectoryExportEntry] = []
        diagnostics: list[str] = []
        minimum_rank = _quality_rank(min_quality_status.value)
        for entry in entries:
            quality_status = str(entry.quality.status or "failed")
            if _quality_rank(quality_status) >= minimum_rank:
                kept.append(entry)
                continue
            diagnostics.append(
                f"{entry.thread_id}: filtered by quality gate "
                f"{quality_status} < {min_quality_status.value}"
            )
        return kept, diagnostics

    def _load_states(self, thread_ids: list[str]) -> tuple[list[ThreadState], list[str]]:
        ids = list(dict.fromkeys(thread_ids or self.checkpointer.list_thread_ids()))
        states: list[ThreadState] = []
        diagnostics: list[str] = []
        for thread_id in ids:
            state = self.checkpointer.get_thread_state(thread_id)
            if state is None:
                diagnostics.append(f"{thread_id}: thread not found")
                continue
            states.append(state)
        return states, diagnostics

    def _resolve_output_path(self, output_path: str | None, *, dataset_id: str, suffix: str) -> Path:
        if output_path:
            candidate = Path(output_path).expanduser()
            if not candidate.is_absolute():
                candidate = self.export_root / candidate
            return candidate.resolve()
        return (self.export_root / f"{dataset_id}.{suffix}").resolve()

    def _resolve_manifest_path(self, output_path: Path, *, dataset_id: str) -> Path:
        if output_path.name:
            return output_path.with_suffix(".manifest.json")
        return (self.export_root / f"{dataset_id}.manifest.json").resolve()

    def _build_manifest(
        self,
        *,
        dataset_id: str,
        request: TrajectoryBatchExportRequest,
        result: TrajectoryBatchExportResult,
        entries: list[TrajectoryExportEntry],
        output_path: Path | None,
        diagnostics: list[str],
        learning_summary: dict[str, object] | None = None,
    ) -> TrajectoryBatchManifest:
        stats = self._aggregate_stats(entries)
        if learning_summary is not None:
            stats["procedure_learning"] = learning_summary
        return TrajectoryBatchManifest(
            dataset_id=dataset_id,
            format=request.options.format,
            jsonl_path=str(output_path) if output_path is not None else None,
            exported_count=result.exported_count,
            skipped_count=result.skipped_count,
            thread_ids=[entry.thread_id for entry in entries],
            diagnostics=diagnostics,
            stats=stats,
        )

    def _learn_procedures_from_entries(
        self,
        *,
        request: TrajectoryBatchExportRequest,
        entries: list[TrajectoryExportEntry],
        states_by_thread_id: dict[str, ThreadState],
    ) -> tuple[dict[str, object] | None, list[str]]:
        if not request.learn_procedures:
            return None, []
        if self.config is None or self.skills_service is None:
            return (
                {
                    "enabled": False,
                    "accepted_count": 0,
                    "skipped_count": len(entries),
                    "reasons": {"missing_runtime_dependencies": len(entries)},
                    "procedure_ids": [],
                },
                [f"{entry.thread_id}: procedure learning skipped; config or skills service unavailable" for entry in entries],
            )
        accepted_count = 0
        skipped_count = 0
        reasons: dict[str, int] = {}
        procedure_ids: list[str] = []
        diagnostics: list[str] = []
        for entry in entries:
            state = states_by_thread_id.get(entry.thread_id)
            if state is None:
                skipped_count += 1
                reasons["thread_not_loaded"] = reasons.get("thread_not_loaded", 0) + 1
                diagnostics.append(f"{entry.thread_id}: procedure learning skipped; thread state not loaded")
                continue
            result = self.procedure_learning_service.learn_from_thread(
                state=state,
                config=self.config,
                skills_service=self.skills_service,
                source="trajectory_batch",
                run_id=entry.run_id,
                skill_ids=(),
            )
            if result.accepted:
                accepted_count += 1
                if result.procedure_id:
                    procedure_ids.append(result.procedure_id)
                continue
            skipped_count += 1
            reason = result.reason or "unknown"
            reasons[reason] = reasons.get(reason, 0) + 1
            diagnostics.append(f"{entry.thread_id}: procedure learning skipped; {reason}")
        return (
            {
                "enabled": True,
                "accepted_count": accepted_count,
                "skipped_count": skipped_count,
                "reasons": dict(sorted(reasons.items())),
                "procedure_ids": procedure_ids[:50],
            },
            diagnostics,
        )

    def _aggregate_stats(self, entries: list[TrajectoryExportEntry]) -> dict[str, object]:
        totals = {
            "message_count": 0,
            "exported_turn_count": 0,
            "original_turn_count": 0,
            "omitted_turn_count": 0,
            "quality_failed_count": 0,
            "quality_warning_count": 0,
            "quality_passed_count": 0,
            "quality_error_issue_count": 0,
            "quality_warning_issue_count": 0,
            "quality_info_issue_count": 0,
            "tool_call_count": 0,
            "tool_success_count": 0,
            "tool_error_count": 0,
            "approval_count": 0,
            "artifact_count": 0,
            "completed_count": 0,
            "interrupted_count": 0,
        }
        tool_counts: dict[str, int] = {}
        models: dict[str, int] = {}
        for entry in entries:
            stats = entry.stats
            totals["message_count"] += stats.message_count
            totals["exported_turn_count"] += stats.exported_turn_count
            totals["original_turn_count"] += stats.original_turn_count
            totals["omitted_turn_count"] += stats.omitted_turn_count
            if entry.quality.status == "failed":
                totals["quality_failed_count"] += 1
            elif entry.quality.status == "warning":
                totals["quality_warning_count"] += 1
            else:
                totals["quality_passed_count"] += 1
            totals["quality_error_issue_count"] += sum(1 for issue in entry.quality.issues if issue.severity == "error")
            totals["quality_warning_issue_count"] += sum(1 for issue in entry.quality.issues if issue.severity == "warning")
            totals["quality_info_issue_count"] += sum(1 for issue in entry.quality.issues if issue.severity == "info")
            totals["tool_call_count"] += stats.tool_call_count
            totals["tool_success_count"] += stats.tool_success_count
            totals["tool_error_count"] += stats.tool_error_count
            totals["approval_count"] += stats.approval_count
            totals["artifact_count"] += stats.artifact_count
            totals["completed_count"] += 1 if stats.completed else 0
            totals["interrupted_count"] += 1 if stats.interrupted else 0
            if entry.model:
                models[entry.model] = models.get(entry.model, 0) + 1
            for tool_name, tool_stats in stats.tool_stats.items():
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + tool_stats.count
        return {
            **totals,
            "models": dict(sorted(models.items())),
            "tools": dict(sorted(tool_counts.items())),
        }

    def _write_manifest(self, manifest: TrajectoryBatchManifest, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _quality_rank(status: str) -> int:
    return {
        TrajectoryQualityStatus.FAILED.value: 0,
        TrajectoryQualityStatus.WARNING.value: 1,
        TrajectoryQualityStatus.PASSED.value: 2,
    }.get(status, 0)
