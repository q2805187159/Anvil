from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from .archive import SqliteSessionArchive
from .contracts import CuratedEntry, ReflectionJob, ReflectionRunResult, ReflectionScheduleKind, utc_now
from .curated import CuratedStoreManager
from .extraction_policy import durable_preference_sentences, is_stable_workspace_memory


class ReflectionScheduler:
    def __init__(
        self,
        *,
        jobs_path: str | Path,
        curated_store_manager: CuratedStoreManager,
        archive: SqliteSessionArchive,
        tick_seconds: int = 60,
        enabled: bool = False,
    ) -> None:
        self.jobs_path = Path(jobs_path).expanduser().resolve()
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
        self.curated_store_manager = curated_store_manager
        self.archive = archive
        self.tick_seconds = tick_seconds
        self.enabled = enabled
        self._lock = Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._jobs = self._load_jobs()
        self._executor = None

    def list_jobs(self) -> tuple[ReflectionJob, ...]:
        return tuple(self._jobs.values())

    def ensure_default_jobs(self) -> None:
        defaults = (
            ReflectionJob(
                job_id="system-nightly-consolidation",
                name="Nightly Consolidation",
                schedule_kind=ReflectionScheduleKind.CRON,
                cron="0 2 * * *",
                target_store_id="runtime_memory",
                template="nightly_consolidation",
                enabled=True,
                system_managed=True,
            ),
            ReflectionJob(
                job_id="system-preference-extraction",
                name="Preference Extraction",
                schedule_kind=ReflectionScheduleKind.INTERVAL,
                interval_seconds=6 * 60 * 60,
                target_store_id="user_profile",
                template="preference_extraction",
                enabled=True,
                system_managed=True,
            ),
            ReflectionJob(
                job_id="system-project-recap",
                name="Project Recap",
                schedule_kind=ReflectionScheduleKind.INTERVAL,
                interval_seconds=12 * 60 * 60,
                target_store_id="runtime_memory",
                template="project_recap",
                enabled=True,
                system_managed=True,
            ),
            ReflectionJob(
                job_id="system-pattern-extraction",
                name="Pattern Extraction",
                schedule_kind=ReflectionScheduleKind.INTERVAL,
                interval_seconds=12 * 60 * 60,
                target_store_id="runtime_memory",
                template="pattern_extraction",
                enabled=True,
                system_managed=True,
            ),
        )
        changed = False
        for job in defaults:
            if job.job_id not in self._jobs:
                self._jobs[job.job_id] = self._schedule_next(job)
                changed = True
        if changed:
            self._save_jobs()

    def create_job(self, job: ReflectionJob) -> ReflectionJob:
        scheduled = self._schedule_next(job)
        self._jobs[job.job_id] = scheduled
        self._save_jobs()
        return scheduled

    def pause_job(self, job_id: str) -> ReflectionJob:
        job = self._jobs[job_id]
        job.enabled = False
        self._save_jobs()
        return job

    def resume_job(self, job_id: str) -> ReflectionJob:
        job = self._jobs[job_id]
        job.enabled = True
        self._jobs[job_id] = self._schedule_next(job)
        self._save_jobs()
        return self._jobs[job_id]

    def remove_job(self, job_id: str) -> ReflectionJob:
        job = self._jobs.pop(job_id)
        self._save_jobs()
        return job

    def run_job(self, job_id: str) -> ReflectionRunResult:
        job = self._jobs[job_id]
        result = self._execute_job(job)
        job.last_run_at = utc_now()
        job.last_status = result.status
        self._jobs[job_id] = self._schedule_next(job)
        self._save_jobs()
        return result

    def register_executor(self, executor) -> None:
        self._executor = executor

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            self.tick()

    def tick(self) -> None:
        now = utc_now()
        for job in list(self._jobs.values()):
            if not job.enabled or job.next_run_at is None or job.next_run_at > now:
                continue
            self.run_job(job.job_id)

    def _execute_job(self, job: ReflectionJob) -> ReflectionRunResult:
        if self._executor is not None:
            return self._executor(job)
        archive = self.archive.search(job.source_query or "", limit=8) if job.source_query else self.archive.search("*", limit=8)
        written: list[CuratedEntry] = []

        if job.template == "preference_extraction":
            for hit in archive.hits:
                for sentence in _extract_preference_sentences(hit.excerpt):
                    written.append(
                        self.curated_store_manager.create_entry(
                            job.target_store_id,
                            content=sentence,
                            category="preference",
                            source_kind="reflection",
                            priority=0.9,
                        )
                    )
            if not written:
                existing = self.curated_store_manager.search_entries("prefer", limit=3)
                for entry in existing:
                    if entry.store_id != "user_profile":
                        continue
                    written.append(
                        self.curated_store_manager.create_entry(
                            job.target_store_id,
                            content=f"Preference ledger: {entry.content}",
                            category="preference",
                            source_kind="reflection",
                            priority=0.8,
                        )
                    )
        elif job.template == "project_recap":
            if archive.hits:
                summary = " ".join(hit.excerpt for hit in archive.hits[:3])[:500]
                written.append(
                    self.curated_store_manager.create_entry(
                        job.target_store_id,
                        content=f"Project recap: {summary}",
                        category="recap",
                        source_kind="reflection",
                        priority=0.7,
                    )
                )
        elif job.template == "nightly_consolidation":
            snapshot = self.curated_store_manager.render_stable_snapshot()
            if snapshot:
                written.append(
                    self.curated_store_manager.create_entry(
                        job.target_store_id,
                        content=f"Nightly consolidation: {snapshot[:500]}",
                        category="consolidation",
                        source_kind="reflection",
                        priority=0.6,
                    )
                )
        elif job.template == "pattern_extraction":
            if archive.hits:
                text = " ".join(hit.excerpt for hit in archive.hits)
                tokens = _extract_recurring_terms(text) if is_stable_workspace_memory(text) else []
                if tokens:
                    written.append(
                        self.curated_store_manager.create_entry(
                            job.target_store_id,
                            content=f"Recurring pattern: {', '.join(tokens)}",
                            category="pattern",
                            source_kind="reflection",
                            priority=0.6,
                        )
                    )
        else:
            instructions = job.instructions or job.name
            written.append(
                self.curated_store_manager.create_entry(
                    job.target_store_id,
                    content=f"Reflection note: {instructions}",
                    category="reflection",
                    source_kind="reflection",
                    priority=0.5,
                )
            )

        status = "completed" if written else "noop"
        return ReflectionRunResult(
            job_id=job.job_id,
            status=status,
            entries_written=len(written),
            archive_hits=len(archive.hits),
            summary=f"{job.name} processed {len(archive.hits)} archive hits and wrote {len(written)} entries.",
            written_entries=tuple(written),
        )

    def _load_jobs(self) -> dict[str, ReflectionJob]:
        if not self.jobs_path.exists():
            return {}
        payload = json.loads(self.jobs_path.read_text(encoding="utf-8"))
        return {
            item["job_id"]: ReflectionJob.model_validate(item)
            for item in payload.get("jobs", [])
        }

    def _save_jobs(self) -> None:
        with self._lock:
            payload = {"jobs": [job.model_dump(mode="json") for job in self._jobs.values()]}
            self.jobs_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _schedule_next(self, job: ReflectionJob) -> ReflectionJob:
        if not job.enabled:
            return job
        now = utc_now()
        if job.schedule_kind is ReflectionScheduleKind.ONCE:
            job.next_run_at = job.next_run_at or now
        elif job.schedule_kind is ReflectionScheduleKind.INTERVAL:
            interval = job.interval_seconds or 3600
            job.next_run_at = now + timedelta(seconds=interval)
        else:
            job.next_run_at = _next_cron(job.cron or "0 0 * * *", now)
        return job


def _extract_preference_sentences(text: str) -> list[str]:
    return list(durable_preference_sentences(text))


def _extract_recurring_terms(text: str) -> list[str]:
    noise = {
        "assistant",
        "current",
        "created",
        "create",
        "edited",
        "error",
        "file",
        "fixed",
        "please",
        "reply",
        "session",
        "task",
        "thread",
        "tools",
        "user",
        "want",
        "needs",
    }
    words = re.findall(r"[A-Za-z][A-Za-z_-]{3,}", text.lower())
    counts: dict[str, int] = {}
    for word in words:
        if word in noise:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = [word for word, count in sorted(counts.items(), key=lambda item: item[1], reverse=True) if count > 1]
    return ranked[:5]


def _next_cron(expr: str, after: datetime) -> datetime:
    minute_spec, hour_spec, day_spec, month_spec, weekday_spec = expr.split()
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(60 * 24 * 370):
        if (
            _matches_field(candidate.minute, minute_spec)
            and _matches_field(candidate.hour, hour_spec)
            and _matches_field(candidate.day, day_spec)
            and _matches_field(candidate.month, month_spec)
            and _matches_field(candidate.weekday(), weekday_spec)
        ):
            return candidate
        candidate += timedelta(minutes=1)
    return after + timedelta(days=1)


def _matches_field(value: int, spec: str) -> bool:
    if spec == "*":
        return True
    allowed = {int(part) for part in spec.split(",")}
    return value in allowed
