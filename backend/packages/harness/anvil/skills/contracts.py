from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SkillValidationSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class SkillDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str
    required: bool = True
    details: dict[str, object] = Field(default_factory=dict)


class SkillReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ready"
    requirements: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class SkillValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: SkillValidationSeverity
    code: str
    message: str
    skill_id: str | None = None
    source_root: str | None = None
    path: str | None = None
    field: str | None = None


class SkillCollisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    winner_source_root: str
    loser_source_root: str
    resolution: str = "last_root_wins"


class SkillFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str
    size_bytes: int
    is_binary: bool = False


class SkillSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    title: str
    summary: str
    name: str | None = None
    description: str | None = None
    version: str = "0.1.0"
    trust: str = "local"
    allowed_tools: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    domain: str | None = None
    task_type: str | None = None
    input_requirements: tuple[str, ...] = ()
    risk_level: str | None = None
    enabled: bool = True
    valid: bool = True
    readiness: dict[str, object] = Field(default_factory=dict)
    path: str
    source_root: str
    issue_counts: dict[str, int] = Field(default_factory=dict)
    curator: dict[str, object] = Field(default_factory=dict)


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    path: str
    source_root: str
    title: str
    summary: str
    name: str | None = None
    description: str | None = None
    version: str = "0.1.0"
    trust: str = "local"
    allowed_tools: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    domain: str | None = None
    task_type: str | None = None
    input_requirements: tuple[str, ...] = ()
    risk_level: str | None = None
    dependencies: tuple[SkillDependency, ...] = ()
    readiness: SkillReadiness = Field(default_factory=SkillReadiness)
    config: dict[str, object] = Field(default_factory=dict)
    platforms: tuple[str, ...] = ()
    related_skills: tuple[str, ...] = ()
    asset_paths: tuple[str, ...] = ()
    template_paths: tuple[str, ...] = ()
    script_paths: tuple[str, ...] = ()
    reference_paths: tuple[str, ...] = ()
    file_index_scanned_path_count: int = 0
    file_index_max_scanned_paths: int = 0
    file_index_scan_truncated: bool = False
    body_preview: str = ""
    valid: bool = True
    issues: tuple[SkillValidationIssue, ...] = ()
    content_hash: str | None = None
    enabled: bool = True

    def to_summary(self) -> SkillSummary:
        issue_counts = {
            severity.value: sum(1 for issue in self.issues if issue.severity is severity)
            for severity in SkillValidationSeverity
            if any(issue.severity is severity for issue in self.issues)
        }
        return SkillSummary(
            skill_id=self.skill_id,
            title=self.title,
            summary=self.summary,
            name=self.name,
            description=self.description,
            version=self.version,
            trust=self.trust,
            allowed_tools=self.allowed_tools,
            tags=self.tags,
            domain=self.domain,
            task_type=self.task_type,
            input_requirements=self.input_requirements,
            risk_level=self.risk_level,
            enabled=self.enabled,
            valid=self.valid,
            readiness=self.readiness.model_dump(mode="json"),
            path=self.path,
            source_root=self.source_root,
            issue_counts=issue_counts,
        )


class SkillContentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    title: str
    path: str
    source_root: str
    body: str
    body_preview: str = ""
    file_count: int = 0


class SkillFileIndexView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    path: str
    source_root: str
    files: tuple[SkillFileEntry, ...] = ()
    scanned_path_count: int = 0
    max_scanned_paths: int = 0
    scan_truncated: bool = False


class SkillFileReadView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    relative_path: str
    path: str
    source_root: str
    kind: str
    is_binary: bool = False
    encoding: str = "utf-8"
    content: str = ""
    truncated: bool = False
    size_bytes: int = 0


class SkillPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: SkillManifest
    package_path: str | None = None
    installed_path: str | None = None
    quarantine_path: str | None = None
    checksum: str | None = None
    source: str | None = None
    status: str = "discovered"
    audit_findings: tuple[str, ...] = ()
    audit_warnings: tuple[str, ...] = ()
    security_scan: dict[str, object] = Field(default_factory=dict)
    package_scanned_path_count: int = 0
    package_max_scanned_paths: int = 0
    package_scan_truncated: bool = False
    package_uncompressed_bytes: int = 0
    package_max_uncompressed_bytes: int = 0


class SkillGovernanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    action: str
    created_at: str
    actor: str = "runtime"
    detail: dict[str, object] = Field(default_factory=dict)


class SkillsCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    manifests: tuple[SkillManifest, ...] = ()
    summaries: tuple[SkillSummary, ...] = ()


class SkillDiscoveryDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_hit: bool = False
    watch_enabled: bool = False
    root_count: int = 0
    manifest_count: int = 0
    enabled_count: int = 0
    package_count: int = 0
    stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    slowest_stage: str | None = None
    slowest_stage_duration_ms: int | None = None


class SkillDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_manifests: tuple[SkillManifest, ...] = ()
    all_summaries: tuple[SkillSummary, ...] = ()
    enabled_manifests: tuple[SkillManifest, ...] = ()
    enabled_summaries: tuple[SkillSummary, ...] = ()
    enabled_ids: tuple[str, ...] = ()
    packages: tuple[SkillPackage, ...] = ()
    issues: tuple[SkillValidationIssue, ...] = ()
    collisions: tuple[SkillCollisionRecord, ...] = ()
    discovery_diagnostics: SkillDiscoveryDiagnostics = Field(default_factory=SkillDiscoveryDiagnostics)
