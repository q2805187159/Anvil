from __future__ import annotations

import re
from uuid import uuid4

from .archive import SqliteSessionArchive
from .contracts import ReflectionArtifact, ReflectionJob, ReflectionRunResult
from .curated import CuratedStoreManager
from .extraction_policy import durable_preference_sentences, is_stable_workspace_memory
from .recall import SessionSearchService
from .write_service import MemoryWriteService


class ReflectionService:
    def __init__(
        self,
        *,
        archive: SqliteSessionArchive,
        curated_store_manager: CuratedStoreManager,
        session_search_service: SessionSearchService,
        write_service: MemoryWriteService,
    ) -> None:
        self.archive = archive
        self.curated_store_manager = curated_store_manager
        self.session_search_service = session_search_service
        self.write_service = write_service

    def run_job(self, job: ReflectionJob) -> ReflectionRunResult:
        archive = self.archive.search(job.source_query or "*", limit=8)
        artifacts = self._build_artifacts(job=job, archive_hits=archive.hits)
        written_entries = []
        for artifact in artifacts:
            try:
                entry = self.write_service.create_entry(
                    job.target_store_id,
                    content=artifact.content,
                    category=artifact.category,
                    source_kind="reflection",
                    priority=artifact.priority,
                    source_ref=artifact.artifact_id,
                    evidence_refs=artifact.evidence_refs,
                    write_policy="reflection",
                    write_reason=artifact.write_reason,
                )
                written_entries.append(entry)
            except ValueError:
                continue
        return ReflectionRunResult(
            job_id=job.job_id,
            status="completed" if written_entries else "noop",
            entries_written=len(written_entries),
            archive_hits=len(archive.hits),
            summary=f"{job.name} evaluated {len(archive.hits)} archive hits and produced {len(artifacts)} artifacts.",
            written_entries=tuple(written_entries),
            artifacts=tuple(artifacts),
        )

    def list_conflicts(self):
        return self.write_service.list_conflicts()

    def list_staleness(self):
        return self.write_service.list_staleness()

    def _build_artifacts(self, *, job: ReflectionJob, archive_hits) -> list[ReflectionArtifact]:
        if job.template == "preference_extraction":
            return self._build_preference_artifacts(job=job, archive_hits=archive_hits)
        if job.template == "project_recap":
            return self._build_recap_artifacts(job=job, archive_hits=archive_hits)
        if job.template == "nightly_consolidation":
            snapshot = self.curated_store_manager.render_stable_snapshot()
            if not snapshot:
                return []
            return [
                ReflectionArtifact(
                    artifact_id=f"artifact-{uuid4().hex[:16]}",
                    job_id=job.job_id,
                    target_store_id=job.target_store_id,
                    layer_id="workspace",
                    content=f"Consolidated workspace summary: {snapshot[:600]}",
                    category="consolidation",
                    priority=0.65,
                    write_reason="nightly consolidation snapshot",
                )
            ]
        if job.template == "pattern_extraction":
            text = " ".join(hit.excerpt for hit in archive_hits)
            if not is_stable_workspace_memory(text):
                return []
            tokens = _recurring_terms(text)
            if not tokens:
                return []
            return [
                ReflectionArtifact(
                    artifact_id=f"artifact-{uuid4().hex[:16]}",
                    job_id=job.job_id,
                    target_store_id=job.target_store_id,
                    layer_id="workspace",
                    content=f"Recurring pattern detected: {', '.join(tokens)}",
                    category="pattern",
                    priority=0.6,
                    evidence_refs=tuple(hit.archive_id for hit in archive_hits[:3]),
                    write_reason="pattern extraction from repeated archive terms",
                )
            ]
        instructions = job.instructions or job.name
        return [
            ReflectionArtifact(
                artifact_id=f"artifact-{uuid4().hex[:16]}",
                job_id=job.job_id,
                target_store_id=job.target_store_id,
                layer_id="workspace" if job.target_store_id == "runtime_memory" else "user",
                content=f"Reflection note: {instructions}",
                category="reflection",
                priority=0.5,
                write_reason="custom reflection instructions",
            )
        ]

    def _build_preference_artifacts(self, *, job: ReflectionJob, archive_hits) -> list[ReflectionArtifact]:
        artifacts: list[ReflectionArtifact] = []
        for hit in archive_hits:
            for sentence in durable_preference_sentences(hit.excerpt):
                artifacts.append(
                    ReflectionArtifact(
                        artifact_id=f"artifact-{uuid4().hex[:16]}",
                        job_id=job.job_id,
                        target_store_id=job.target_store_id,
                        layer_id="user",
                        content=sentence,
                        category="preference",
                        priority=0.9,
                        evidence_refs=(hit.archive_id,),
                        write_reason="preference extracted from session history",
                    )
                )
        if artifacts:
            return artifacts
        for entry in self.curated_store_manager.search_entries("prefer", limit=3):
            if entry.store_id != "user_profile":
                continue
            artifacts.append(
                ReflectionArtifact(
                    artifact_id=f"artifact-{uuid4().hex[:16]}",
                    job_id=job.job_id,
                    target_store_id=job.target_store_id,
                    layer_id="user",
                    content=f"Preference ledger: {entry.content}",
                    category="preference",
                    priority=0.8,
                    evidence_refs=entry.evidence_refs,
                    write_reason="preference reflection fallback from curated user memory",
                )
            )
        return artifacts

    def _build_recap_artifacts(self, *, job: ReflectionJob, archive_hits) -> list[ReflectionArtifact]:
        summaries = self.session_search_service.search(
            query=job.source_query or "*",
            current_thread_id=None,
            scope="all",
            limit=3,
        )
        if not summaries:
            return []
        recap = " ".join(summary.summary for summary in summaries)[:800]
        evidence_refs = tuple(evidence.archive_id for summary in summaries for evidence in summary.evidence if evidence.archive_id)[:6]
        return [
            ReflectionArtifact(
                artifact_id=f"artifact-{uuid4().hex[:16]}",
                job_id=job.job_id,
                target_store_id=job.target_store_id,
                layer_id="workspace",
                content=f"Workspace recap: {recap}",
                category="recap",
                priority=0.75,
                evidence_refs=evidence_refs,
                write_reason="session-search recap synthesis",
            )
        ]


def _preference_sentences(text: str) -> list[str]:
    return list(durable_preference_sentences(text))


def _recurring_terms(text: str) -> list[str]:
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
    return [word for word, count in sorted(counts.items(), key=lambda item: item[1], reverse=True) if count > 1][:5]
