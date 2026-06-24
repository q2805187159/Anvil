from __future__ import annotations

import json
import shutil
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from anvil.config import EffectiveConfig, SkillCuratorConfig, SkillsConfig
from anvil.skills import SkillsService


def write_skill(root: Path, slug: str, title: str, body: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


def write_manifest_skill(root: Path, slug: str, frontmatter: str, body: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n{frontmatter.strip()}\n---\n# {slug}\n\n{body}\n",
        encoding="utf-8",
    )


def test_repo_skill_discovery_and_enable_disable_resolution(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")
    write_skill(skills_root, "beta", "Beta Skill", "Beta summary")

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            external_dirs=[str(skills_root)],
            disabled_ids=["beta"],
        )
    )

    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    result = service.discover(config=config, fingerprint="cfg-1")

    assert [summary.skill_id for summary in result.all_summaries] == ["alpha", "beta"]
    assert [summary.skill_id for summary in result.enabled_summaries] == ["alpha"]


def test_skill_lookup_accepts_prompt_style_prefixed_skill_ids(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "ppt-generation", "PPT Generation", "Build visually rich decks.")

    config = EffectiveConfig(skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)]))
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]

    content = service.get_skill_content(config=config, fingerprint="cfg-prefixed", skill_id="$ppt-generation")
    files = service.list_skill_files(config=config, fingerprint="cfg-prefixed", skill_id="@ppt-generation")
    manifest = service.get_skill(config=config, fingerprint="cfg-prefixed", skill_id="`$ppt-generation`")
    path_manifest = service.get_skill(
        config=config,
        fingerprint="cfg-prefixed",
        skill_id="/app/.anvil/skills/ppt-generation/SKILL.md",
    )
    uri_manifest = service.get_skill(config=config, fingerprint="cfg-prefixed", skill_id="skill://ppt-generation")

    assert content.skill_id == "ppt-generation"
    assert files.skill_id == "ppt-generation"
    assert manifest is not None
    assert manifest.skill_id == "ppt-generation"
    assert path_manifest is not None
    assert path_manifest.skill_id == "ppt-generation"
    assert uri_manifest is not None
    assert uri_manifest.skill_id == "ppt-generation"


def test_skill_retrieval_l0_l3_selects_top_k_without_loading_full_content(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = contract_tmp_path / "skills"
    write_manifest_skill(
        skills_root,
        "code-review",
        """
title: Code Review
summary: Review code regressions and missing tests.
tags: [review, regression, tests]
domain: engineering
task_type: review
allowed_tools: [shell_command, rg]
related_skills: [test-driven-development]
risk_level: low
""",
        "Review code for regressions. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    write_manifest_skill(
        skills_root,
        "test-driven-development",
        """
title: Test Driven Development
summary: Write failing tests before implementation.
tags: [tests, regression]
domain: engineering
task_type: implementation
allowed_tools: [shell_command]
risk_level: low
""",
        "Use red green refactor loops. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    write_manifest_skill(
        skills_root,
        "ppt-generation",
        """
title: Presentation Generation
summary: Create presentation decks and slide layouts.
tags: [slides]
domain: presentation
task_type: generation
risk_level: normal
""",
        "Make slides. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )

    config = EffectiveConfig(skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)]))
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]

    def fail_content_read(*_args, **_kwargs):
        raise AssertionError("skill retrieval must not load full skill content")

    monkeypatch.setattr(service, "get_skill_content", fail_content_read)
    monkeypatch.setattr(service, "mentioned_skill_content_summaries", fail_content_read)

    plan = service.retrieve(
        config=config,
        fingerprint="cfg-retrieval",
        query="review code regression tests",
        top_k=2,
        feedback_by_skill_id={
            "code-review": {
                "usage_count": 5,
                "success_count": 4,
                "failure_count": 1,
                "utility_score": 0.82,
                "average_latency_ms": 110,
            }
        },
        graph_neighbors_by_skill_id={
            "test-driven-development": ("code-review", "regression-tests"),
        },
    )

    assert plan.query == "review code regression tests"
    assert plan.top_k == 2
    assert plan.l0_summary["enabled_count"] == 3
    assert plan.l0_summary["domain_counts"]["engineering"] == 2
    assert plan.l0_summary["tag_counts"]["tests"] == 2
    assert plan.tiers_used == ("L0", "L1", "L2", "L3")
    assert plan.diagnostics["loaded_full_skill_content"] is False
    assert plan.diagnostics["embedding_mode"] == "lexical_fallback"
    assert plan.selected_skill_ids == ("code-review", "test-driven-development")

    candidates = {candidate.skill_id: candidate for candidate in plan.candidates}
    assert candidates["code-review"].selected is True
    assert candidates["code-review"].selection_rank == 1
    assert candidates["code-review"].tier_scores["history"] > 0
    assert "summary" in candidates["code-review"].matched_fields
    assert "tests" in candidates["code-review"].matched_terms
    assert candidates["test-driven-development"].tier_scores["graph"] > 0
    assert "code-review" in candidates["test-driven-development"].graph_neighbors
    assert candidates["ppt-generation"].selected is False
    assert "FULL BODY SENTINEL" not in json.dumps(plan.model_dump(mode="json"))


def test_skill_retrieval_l4_l6_reranks_expands_and_prefetches_without_loading_full_content(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = contract_tmp_path / "skills"
    write_manifest_skill(
        skills_root,
        "runtime-context",
        """
title: Runtime Context Assembly
summary: Assemble ContextBlock budgets, salience routes, and runtime trace diagnostics.
tags: [runtime-context, contextblock, salience]
domain: runtime
task_type: implementation
allowed_tools: [shell_command, rg]
risk_level: low
""",
        "Runtime context implementation body. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    write_manifest_skill(
        skills_root,
        "context-cleanup",
        """
title: Context Cleanup
summary: Remove duplicate prompt injection paths and migrate legacy memory appenders.
tags: [cleanup, prompt-injection]
domain: runtime
task_type: refactor
allowed_tools: [shell_command, rg]
risk_level: normal
""",
        "Cleanup body. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    write_manifest_skill(
        skills_root,
        "dangerous-deploy",
        """
title: Dangerous Deploy
summary: Deploy production infrastructure with broad release permissions.
tags: [deploy]
domain: release
task_type: deployment
allowed_tools: [shell_command]
risk_level: high
""",
        "Deploy body. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )
    write_manifest_skill(
        skills_root,
        "evaluation-suite",
        """
title: Evaluation Suite
summary: Build trace replay, ablation reports, and release quality metrics.
tags: [evaluation, trace-replay, ablation]
domain: evaluation
task_type: verification
risk_level: low
""",
        "Evaluation body. FULL BODY SENTINEL SHOULD NOT LOAD.",
    )

    config = EffectiveConfig(skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)]))
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]

    def fail_content_read(*_args, **_kwargs):
        raise AssertionError("skill retrieval must not load full skill content")

    monkeypatch.setattr(service, "get_skill_content", fail_content_read)
    monkeypatch.setattr(service, "mentioned_skill_content_summaries", fail_content_read)

    plan = service.retrieve(
        config=config,
        fingerprint="cfg-retrieval-l4-l6",
        query="fix it",
        top_k=2,
        salience_boost_terms={
            "contextblock": 1.0,
            "salience": 0.8,
            "runtime": 0.7,
            "legacy memory": 0.45,
            "prompt injection": 0.4,
            "trace": 0.5,
        },
        prefetch_terms=("ablation", "release metrics", "legacy memory appenders"),
    )

    assert plan.tiers_used == ("L0", "L1", "L2", "L3", "L4", "L5", "L6")
    assert set(plan.selected_skill_ids) == {"runtime-context", "context-cleanup"}
    assert plan.diagnostics["loaded_full_skill_content"] is False
    assert plan.diagnostics["l4_rerank_triggered"] is True
    assert plan.diagnostics["l4_trigger_reasons"] == ("high_candidate_count", "high_risk_candidate")
    assert plan.diagnostics["l5_hyde_triggered"] is True
    assert "contextblock" in plan.diagnostics["expanded_query_terms"]
    assert plan.diagnostics["prefetch_skill_ids"] == ("evaluation-suite",)

    candidates = {candidate.skill_id: candidate for candidate in plan.candidates}
    assert candidates["runtime-context"].selection_rank is not None
    assert candidates["context-cleanup"].selection_rank is not None
    assert candidates["runtime-context"].tier_scores["rerank"] > candidates["dangerous-deploy"].tier_scores["rerank"]
    assert candidates["runtime-context"].tier_scores["hyde"] > 0
    assert candidates["evaluation-suite"].selected is False
    assert candidates["evaluation-suite"].metadata["prefetch_candidate"] is True
    assert candidates["evaluation-suite"].metadata["prefetch_reason"] == "L6_goal_prefetch"
    assert "FULL BODY SENTINEL" not in json.dumps(plan.model_dump(mode="json"))


def test_skill_discovery_defers_supporting_file_index_until_detail(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")
    references_dir = skills_root / "alpha" / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    (references_dir / "guide.md").write_text("reference guide", encoding="utf-8")

    config = EffectiveConfig(skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)]))
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            "anvil.skills.loader._collect_manifest_file_index",
            lambda _skill_root: (_ for _ in ()).throw(
                AssertionError("skill list discovery must not scan supporting files")
            ),
        )
        result = service.discover(config=config, fingerprint="cfg-light-skills")

    manifest = result.all_manifests[0]
    assert manifest.reference_paths == ()

    files = service.list_skill_files(config=config, fingerprint="cfg-light-skills", skill_id="alpha")
    assert [item.path for item in files.files] == ["SKILL.md", "references/guide.md"]


def test_skill_supporting_file_index_stops_at_scan_budget(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")
    references_dir = skills_root / "alpha" / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    for index in range(12):
        (references_dir / f"guide-{index:02}.md").write_text("reference guide", encoding="utf-8")

    config = EffectiveConfig(skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)]))
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    monkeypatch.setattr("anvil.skills.service.DEFAULT_SKILL_SUPPORT_FILE_SCAN_LIMIT", 5, raising=False)

    files = service.list_skill_files(config=config, fingerprint="cfg-file-budget", skill_id="alpha")

    assert files.scan_truncated is True
    assert files.scanned_path_count == 5
    assert files.max_scanned_paths == 5
    assert len(files.files) <= 6
    assert files.files[0].path == "SKILL.md"


def test_skill_loader_file_index_stops_at_scan_budget_without_rglob(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anvil.skills.loader import SkillLoader

    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")
    references_dir = skills_root / "alpha" / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    for index in range(12):
        (references_dir / f"guide-{index:02}.md").write_text("reference guide", encoding="utf-8")

    monkeypatch.setattr("anvil.skills.loader.DEFAULT_SKILL_MANIFEST_FILE_INDEX_SCAN_LIMIT", 5, raising=False)

    def fail_rglob(_self: Path, pattern: str):
        raise AssertionError(f"skill manifest file index should not use rglob({pattern!r})")

    monkeypatch.setattr(Path, "rglob", fail_rglob)

    result = SkillLoader().discover([skills_root], include_file_index=True, include_body_preview=False)
    manifest = result.manifests[0]

    assert manifest.file_index_scan_truncated is True
    assert manifest.file_index_scanned_path_count == 5
    assert manifest.file_index_max_scanned_paths == 5
    assert manifest.reference_paths
    assert all(path.startswith("references/") for path in manifest.reference_paths)


def test_skill_discovery_defers_body_preview_until_detail(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")

    config = EffectiveConfig(skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)]))
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]

    result = service.discover(config=config, fingerprint="cfg-body-preview")

    assert result.all_manifests[0].body_preview == ""

    content = service.get_skill_content(config=config, fingerprint="cfg-body-preview", skill_id="alpha")
    assert content.body_preview == "# Alpha Skill Alpha summary"


def test_skill_discovery_recurses_nested_groups_with_depth_limit(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")
    grouped = skills_root / "groups" / "productivity" / "beta"
    grouped.mkdir(parents=True)
    (grouped / "SKILL.md").write_text("# Beta Skill\n\nBeta summary\n", encoding="utf-8")
    too_deep = skills_root / "d1" / "d2" / "d3" / "d4" / "d5" / "d6" / "d7" / "gamma"
    too_deep.mkdir(parents=True)
    (too_deep / "SKILL.md").write_text("# Gamma Skill\n\nGamma summary\n", encoding="utf-8")
    hidden = skills_root / ".hidden" / "delta"
    hidden.mkdir(parents=True)
    (hidden / "SKILL.md").write_text("# Delta Skill\n\nDelta summary\n", encoding="utf-8")

    config = EffectiveConfig(skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)]))
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]

    result = service.discover(config=config, fingerprint="cfg-bounded-recursive-scan")

    assert result.enabled_ids == ("alpha", "beta")


def test_skill_discovery_prefers_shallow_manifest_over_nested_plugin_duplicate(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "frontend-slides", "Frontend Slides", "Create browser presentations.")
    nested = skills_root / "frontend-slides" / "plugins" / "frontend-slides" / "skills" / "frontend-slides"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("../../../../SKILL.md\n", encoding="utf-8")

    config = EffectiveConfig(skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)]))
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]

    content = service.get_skill_content(config=config, fingerprint="cfg-nested-plugin-skill", skill_id="frontend-slides")

    assert content.body.startswith("# Frontend Slides")
    assert content.path == str((skills_root / "frontend-slides" / "SKILL.md").resolve())


def test_skills_cache_is_keyed_by_fingerprint(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    first = service.discover(config=config, fingerprint="cfg-1")
    (skills_root / "alpha" / "SKILL.md").write_text("# Alpha Skill\n\nChanged summary\n", encoding="utf-8")
    still_cached = service.discover(config=config, fingerprint="cfg-1")
    refreshed = service.discover(config=config, fingerprint="cfg-2")

    assert first.enabled_summaries[0].summary == "Alpha summary"
    assert still_cached.enabled_summaries[0].summary == "Changed summary"
    assert refreshed.enabled_summaries[0].summary == "Changed summary"


def test_skill_discovery_hot_loads_new_skill_files_without_explicit_reload(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, watch_enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]

    first = service.discover(config=config, fingerprint="cfg-hot")
    write_skill(skills_root, "beta", "Beta Skill", "Beta summary")
    second = service.discover(config=config, fingerprint="cfg-hot")

    assert first.enabled_ids == ("alpha",)
    assert second.enabled_ids == ("alpha", "beta")
    assert all(summary.enabled for summary in second.all_summaries)


def test_skill_discovery_scans_skill_roots_once_on_cold_watch_miss(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, watch_enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    original_rglob = Path.rglob
    scan_count = 0

    def count_rglob(self: Path, pattern: str):
        nonlocal scan_count
        if pattern == "SKILL.md":
            scan_count += 1
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", count_rglob)
    result = service.discover(config=config, fingerprint="cfg-cold-scan")

    assert result.enabled_ids == ("alpha",)
    assert scan_count == 0


def test_skill_discovery_reuses_watch_fingerprint_when_roots_are_unchanged(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, watch_enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    first = service.discover(config=config, fingerprint="cfg-hot-cache")

    def fail_rglob(_self: Path, pattern: str):
        raise AssertionError(f"unchanged skill roots should not rescan {pattern}")

    monkeypatch.setattr(Path, "rglob", fail_rglob)
    second = service.discover(config=config, fingerprint="cfg-hot-cache")

    assert first.enabled_ids == ("alpha",)
    assert second.enabled_ids == ("alpha",)
    assert first.discovery_diagnostics.cache_hit is False
    assert second.discovery_diagnostics.cache_hit is True
    assert second.discovery_diagnostics.manifest_count == 1
    assert second.discovery_diagnostics.enabled_count == 1
    assert second.discovery_diagnostics.stage_durations_ms["resolve_roots"] >= 0
    assert second.discovery_diagnostics.stage_durations_ms["cache_read"] >= 0
    assert "loader_discover" not in second.discovery_diagnostics.stage_durations_ms


def test_skill_discovery_rechecks_manifest_stat_before_cache_hit(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, watch_enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    service.discover(config=config, fingerprint="cfg-hot-recheck")

    original_stat = Path.stat
    manifest_stats = 0

    def count_manifest_stat(self: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal manifest_stats
        if self.name == "SKILL.md":
            manifest_stats += 1
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", count_manifest_stat)
    result = service.discover(config=config, fingerprint="cfg-hot-recheck")

    assert result.discovery_diagnostics.cache_hit is True
    assert manifest_stats >= 1


def test_skill_discovery_reports_cold_stage_diagnostics(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    write_skill(skills_root, "alpha", "Alpha Skill", "Alpha summary")

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, watch_enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]

    result = service.discover(config=config, fingerprint="cfg-stage-diagnostics")
    diagnostics = result.discovery_diagnostics

    assert diagnostics.cache_hit is False
    assert diagnostics.watch_enabled is True
    assert diagnostics.root_count == 1
    assert diagnostics.manifest_count == 1
    assert diagnostics.enabled_count == 1
    assert diagnostics.stage_durations_ms["loader_discover"] >= 0
    assert diagnostics.stage_durations_ms["filter_enabled"] >= 0
    assert diagnostics.stage_durations_ms["rank_manifests"] >= 0
    assert diagnostics.stage_durations_ms["total"] >= 0
    assert diagnostics.slowest_stage in diagnostics.stage_durations_ms
    assert diagnostics.slowest_stage != "total"


def test_skill_loader_prefers_valid_frontmatter_metadata(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    skill_dir = skills_root / "alpha"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "title: Alpha Frontmatter\n"
        "summary: Prompt-safe alpha summary\n"
        "allowed_tools:\n"
        "  - read_file\n"
        "  - write_file\n"
        "tags:\n"
        "  - ops\n"
        "  - safe\n"
        "---\n\n"
        "# Fallback Title\n\n"
        "Fallback summary\n",
        encoding="utf-8",
    )

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    result = service.discover(config=config, fingerprint="cfg-frontmatter")

    summary = result.enabled_summaries[0]
    assert summary.title == "Alpha Frontmatter"
    assert summary.summary == "Prompt-safe alpha summary"
    assert summary.allowed_tools == ("read_file", "write_file")
    assert summary.tags == ("ops", "safe")


def test_skill_loader_keeps_common_external_frontmatter_as_valid_metadata(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    skill_dir = skills_root / "external"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: external\n"
        "description: External skill summary\n"
        "author: External Agent\n"
        "license: MIT\n"
        "metadata:\n"
        "  anvil:\n"
        "    tags: [Research, API]\n"
        "    related_skills: [deep-research]\n"
        "triggers:\n"
        "  - research\n"
        "argument-hint: topic\n"
        "allowed-tools:\n"
        "  - web_search\n"
        "dependency:\n"
        "  - curl\n"
        "---\n\n"
        "# External Skill\n\n"
        "Use when external metadata should not disable an otherwise valid skill.\n",
        encoding="utf-8",
    )

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    result = service.discover(config=config, fingerprint="cfg-external-frontmatter")

    assert result.enabled_ids == ("external",)
    summary = result.enabled_summaries[0]
    assert summary.summary == "External skill summary"
    assert summary.tags == ("Research", "API")
    assert summary.allowed_tools == ("web_search",)
    assert summary.valid is True


def test_skill_loader_validates_explicit_name_description_boundaries(contract_tmp_path) -> None:
    skills_root = contract_tmp_path / "skills"
    invalid_name_dir = skills_root / "invalid-name"
    invalid_name_dir.mkdir(parents=True, exist_ok=True)
    (invalid_name_dir / "SKILL.md").write_text(
        "---\n"
        "name: Bad_Skill\n"
        "description: Valid description\n"
        "---\n\n"
        "# Invalid Name\n\n"
        "Body\n",
        encoding="utf-8",
    )
    reserved_name_dir = skills_root / "reserved-name"
    reserved_name_dir.mkdir(parents=True, exist_ok=True)
    (reserved_name_dir / "SKILL.md").write_text(
        "---\n"
        "name: claude-helper\n"
        "description: Valid description\n"
        "---\n\n"
        "# Reserved Name\n\n"
        "Body\n",
        encoding="utf-8",
    )
    xml_description_dir = skills_root / "xml-description"
    xml_description_dir.mkdir(parents=True, exist_ok=True)
    (xml_description_dir / "SKILL.md").write_text(
        "---\n"
        "name: xml-description\n"
        "description: Use <tag>xml</tag> here\n"
        "---\n\n"
        "# XML Description\n\n"
        "Body\n",
        encoding="utf-8",
    )
    long_description_dir = skills_root / "long-description"
    long_description_dir.mkdir(parents=True, exist_ok=True)
    (long_description_dir / "SKILL.md").write_text(
        "---\n"
        "name: long-description\n"
        f"description: {'x' * 1025}\n"
        "---\n\n"
        "# Long Description\n\n"
        "Body\n",
        encoding="utf-8",
    )

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    result = service.discover(config=config, fingerprint="cfg-skill-identity-boundaries")

    issues_by_skill = {
        manifest.skill_id: {issue.code for issue in manifest.issues}
        for manifest in result.all_manifests
    }
    assert issues_by_skill["invalid-name"] == {"skill_name_invalid"}
    assert issues_by_skill["reserved-name"] == {"skill_name_reserved_term"}
    assert issues_by_skill["xml-description"] == {"skill_description_xml_tag"}
    assert issues_by_skill["long-description"] == {"skill_description_too_long"}
    assert result.enabled_ids == ("invalid-name", "long-description", "reserved-name", "xml-description")


def test_skill_required_env_metadata_does_not_hide_skill_from_llm(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    skills_root = contract_tmp_path / "skills"
    skill_dir = skills_root / "linear"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: linear\n"
        "description: Manage Linear issues.\n"
        "prerequisites:\n"
        "  env_vars: [LINEAR_API_KEY]\n"
        "---\n\n"
        "# Linear\n\n"
        "Use when Linear API access is configured.\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, external_dirs=[str(skills_root)])
    )
    service = SkillsService()
    service.resolve_roots = lambda _config: [skills_root.resolve()]  # type: ignore[method-assign]
    result = service.discover(config=config, fingerprint="cfg-missing-env")

    assert [summary.skill_id for summary in result.all_summaries] == ["linear"]
    assert result.all_summaries[0].enabled is True
    assert result.all_summaries[0].readiness["status"] == "ready"
    assert result.all_summaries[0].readiness["requirements"] == []
    assert result.enabled_ids == ("linear",)
    assert [summary.skill_id for summary in result.enabled_summaries] == ["linear"]


def test_skills_service_syncs_bundled_skills_into_home(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = contract_tmp_path / "repo"
    repo_skills = repo_root / "skills"
    installed_skills = contract_tmp_path / "home" / "skills"
    write_skill(repo_skills, "repo-skill", "Repo Skill", "Repo summary")

    monkeypatch.setattr("anvil.skills.service.default_repo_skill_root", lambda: repo_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: installed_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True)
    )
    service = SkillsService()
    result = service.discover(config=config, fingerprint="cfg-default-roots")

    assert [summary.skill_id for summary in result.enabled_summaries] == ["repo-skill"]
    assert result.enabled_summaries[0].source_root == str(installed_skills.resolve())
    assert (installed_skills / "repo-skill" / "SKILL.md").exists()


def test_skill_root_resolution_reuses_default_installed_root_within_call(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_skills = contract_tmp_path / "repo" / "skills"
    installed_skills = contract_tmp_path / "home" / "skills"
    external_skills = contract_tmp_path / "external" / "skills"
    installed_calls = 0
    repo_calls = 0
    sync_calls: list[tuple[Path, Path]] = []

    def installed_root() -> Path:
        nonlocal installed_calls
        installed_calls += 1
        return installed_skills

    def repo_root() -> Path:
        nonlocal repo_calls
        repo_calls += 1
        return repo_skills

    def sync(installed_root: Path, bundled_root: Path) -> None:
        sync_calls.append((installed_root, bundled_root))

    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", installed_root)
    monkeypatch.setattr("anvil.skills.service.default_repo_skill_root", repo_root)
    monkeypatch.setattr("anvil.skills.service.sync_bundled_skills_to_home", sync)

    config = EffectiveConfig(
        skills_config=SkillsConfig(enabled=True, external_dirs=[str(external_skills)])
    )
    roots = SkillsService().resolve_roots(config)

    assert roots[:2] == [installed_skills.resolve(), external_skills.resolve()]
    assert sync_calls == [(installed_skills, repo_skills)]
    assert installed_calls == 1
    assert repo_calls == 1


def test_default_installed_skill_root_does_not_probe_repo_root(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anvil.skills import loader as skills_loader_module

    config_dir = contract_tmp_path / "home"

    def fail_repo_root() -> Path:
        raise AssertionError("installed skill root should not resolve repo root")

    def default_config_dir(repo_root: Path | None = None) -> Path:
        assert repo_root is None
        return config_dir

    monkeypatch.setattr(skills_loader_module, "get_repo_root", fail_repo_root)
    monkeypatch.setattr(skills_loader_module, "default_anvil_config_dir", default_config_dir)

    assert skills_loader_module.default_installed_skill_root() == config_dir / "skills"


def test_bundled_skill_sync_skips_deep_hash_when_source_stamp_matches(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_skills = contract_tmp_path / "repo" / "skills"
    installed_skills = contract_tmp_path / "home" / "skills"
    write_skill(repo_skills, "repo-skill", "Repo Skill", "Repo summary")
    asset_dir = repo_skills / "repo-skill" / "assets"
    asset_dir.mkdir()
    (asset_dir / "sample.bin").write_bytes(b"x" * 1024)

    from anvil.skills import service as skills_service_module

    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)

    def fail_deep_hash(_: Path) -> str:
        raise AssertionError("unchanged bundled sync should not deep-hash skill trees")

    monkeypatch.setattr(skills_service_module, "_skill_tree_hash", fail_deep_hash)
    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)

    assert (installed_skills / "repo-skill" / "SKILL.md").exists()


def test_skill_tree_hash_stops_at_scan_budget_without_rglob(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_dir = contract_tmp_path / "repo" / "skills" / "oversized"
    write_skill(skill_dir.parent, "oversized", "Oversized Skill", "Oversized summary")
    references_dir = skill_dir / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    for index in range(16):
        (references_dir / f"guide-{index:02}.md").write_text("reference guide", encoding="utf-8")

    from anvil.skills import service as skills_service_module

    def fail_rglob(_self: Path, pattern: str):
        raise AssertionError(f"skill tree hash should not use rglob({pattern!r})")

    monkeypatch.setattr(Path, "rglob", fail_rglob)
    monkeypatch.setattr(skills_service_module, "DEFAULT_SKILL_TREE_SCAN_LIMIT", 5, raising=False)

    tree_hash = skills_service_module._skill_tree_hash(skill_dir)

    assert tree_hash.startswith("sha256-truncated:")


def test_bundled_source_skill_stamps_collect_metadata_concurrently(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_skills = contract_tmp_path / "repo" / "skills"
    for skill_id in ("alpha", "beta", "delta", "gamma"):
        write_skill(repo_skills, skill_id, skill_id.title(), f"{skill_id} summary")

    from anvil.skills import service as skills_service_module

    active = 0
    started = 0
    max_active = 0
    lock = threading.Lock()
    concurrent_call_started = threading.Event()

    def slow_metadata_stamp(path: Path) -> str:
        nonlocal active, started, max_active
        with lock:
            active += 1
            started += 1
            max_active = max(max_active, active)
            if started >= 2:
                concurrent_call_started.set()
        concurrent_call_started.wait(timeout=0.05)
        with lock:
            active -= 1
        return f"stamp:{path.name}"

    monkeypatch.setattr(skills_service_module, "_skill_tree_metadata_stamp", slow_metadata_stamp)

    stamps = skills_service_module._bundled_source_skill_stamps(repo_skills)

    assert stamps == (
        ("alpha", "stamp:alpha"),
        ("beta", "stamp:beta"),
        ("delta", "stamp:delta"),
        ("gamma", "stamp:gamma"),
    )
    assert max_active > 1


def test_bundled_skill_sync_reuses_in_memory_root_stamp_without_rechecking_source(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_skills = contract_tmp_path / "repo" / "skills"
    installed_skills = contract_tmp_path / "home" / "skills"
    write_skill(repo_skills, "repo-skill", "Repo Skill", "Repo summary")

    from anvil.skills import service as skills_service_module

    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)

    def fail_source_stamps(_: Path) -> tuple[tuple[str, str], ...]:
        raise AssertionError("fresh in-memory bundled sync cache should avoid source stamp collection")

    monkeypatch.setattr(skills_service_module, "_bundled_source_skill_stamps", fail_source_stamps)
    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)

    assert (installed_skills / "repo-skill" / "SKILL.md").exists()


def test_bundled_skill_sync_uses_persisted_root_stamp_to_skip_deep_hash_across_processes(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_skills = contract_tmp_path / "repo" / "skills"
    installed_skills = contract_tmp_path / "home" / "skills"
    write_skill(repo_skills, "repo-skill", "Repo Skill", "Repo summary")
    asset_dir = repo_skills / "repo-skill" / "assets"
    asset_dir.mkdir()
    (asset_dir / "sample.bin").write_bytes(b"x" * 1024)

    from anvil.skills import service as skills_service_module

    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)
    skills_service_module._BUNDLED_SYNC_CACHE.clear()

    def fail_deep_hash(_: Path) -> str:
        raise AssertionError("persisted bundled root stamp should avoid deep content hashing")

    monkeypatch.setattr(skills_service_module, "_skill_tree_hash", fail_deep_hash)
    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)

    assert (installed_skills / "repo-skill" / "SKILL.md").exists()


def test_bundled_skill_sync_recopies_missing_target_despite_persisted_root_stamp(
    contract_tmp_path,
) -> None:
    repo_skills = contract_tmp_path / "repo" / "skills"
    installed_skills = contract_tmp_path / "home" / "skills"
    write_skill(repo_skills, "repo-skill", "Repo Skill", "Repo summary")

    from anvil.skills import service as skills_service_module

    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)
    shutil.rmtree(installed_skills / "repo-skill")
    skills_service_module._BUNDLED_SYNC_CACHE.clear()
    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)

    assert (installed_skills / "repo-skill" / "SKILL.md").exists()


def test_bundled_skill_sync_recopies_missing_target_despite_fresh_in_memory_cache(
    contract_tmp_path,
) -> None:
    repo_skills = contract_tmp_path / "repo" / "skills"
    installed_skills = contract_tmp_path / "home" / "skills"
    write_skill(repo_skills, "repo-skill", "Repo Skill", "Repo summary")

    from anvil.skills import service as skills_service_module

    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)
    shutil.rmtree(installed_skills / "repo-skill")
    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)

    assert (installed_skills / "repo-skill" / "SKILL.md").exists()


def test_bundled_skill_sync_updates_support_files_when_source_metadata_changes(
    contract_tmp_path,
) -> None:
    repo_skills = contract_tmp_path / "repo" / "skills"
    installed_skills = contract_tmp_path / "home" / "skills"
    write_skill(repo_skills, "repo-skill", "Repo Skill", "Repo summary")
    asset_dir = repo_skills / "repo-skill" / "assets"
    asset_dir.mkdir()
    source_asset = asset_dir / "sample.txt"
    source_asset.write_text("one", encoding="utf-8")

    from anvil.skills import service as skills_service_module

    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)
    skills_service_module._BUNDLED_SYNC_CACHE.clear()
    source_asset.write_text("two", encoding="utf-8")
    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)

    assert (installed_skills / "repo-skill" / "assets" / "sample.txt").read_text(encoding="utf-8") == "two"


def test_bundled_skill_sync_does_not_fail_request_when_manifest_is_unwritable(
    contract_tmp_path,
) -> None:
    repo_skills = contract_tmp_path / "repo" / "skills"
    installed_skills = contract_tmp_path / "home" / "skills"
    write_skill(repo_skills, "repo-skill", "Repo Skill", "Repo summary")
    installed_skills.mkdir(parents=True)
    (installed_skills / ".bundled_manifest").mkdir()

    from anvil.skills import service as skills_service_module

    skills_service_module.sync_bundled_skills_to_home(installed_skills, repo_skills)

    assert (installed_skills / "repo-skill" / "SKILL.md").exists()


def test_skill_discovery_orders_core_before_active_and_observe(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    empty_repo = contract_tmp_path / "empty-repo-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_repo_skill_root", lambda: empty_repo)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    for skill_id in ("agent-active", "agent-core", "agent-observe"):
        service.manage_curator(
            config=config,
            action="create",
            skill_id=skill_id,
            title=skill_id,
            summary=f"{skill_id} summary",
            body=f"Use when {skill_id} should be discovered.",
        )
    usage_path = contract_tmp_path / "governance" / "curator" / "usage.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    usage["agent-core"]["tier"] = "core"
    usage["agent-core"]["utility_score"] = 500
    usage["agent-core"]["template_path"] = "templates/reusable-template.md"
    usage["agent-active"]["utility_score"] = 100
    usage["agent-observe"]["tier"] = "observe"
    usage["agent-observe"]["utility_score"] = 1
    usage_path.write_text(json.dumps(usage), encoding="utf-8")

    result = service.discover(config=config, fingerprint="tier-order")

    assert result.enabled_ids == ("agent-core", "agent-active", "agent-observe")
    summaries = {summary.skill_id: summary.summary for summary in result.enabled_summaries}
    assert summaries["agent-core"].startswith("[core]")
    assert "[template]" in summaries["agent-core"]
    assert summaries["agent-observe"].startswith("[observe]")
    core_summary = next(summary for summary in result.enabled_summaries if summary.skill_id == "agent-core")
    assert core_summary.curator["tier"] == "core"
    assert core_summary.curator["utility_score"] == 500
    assert core_summary.curator["template_path"] == "templates/reusable-template.md"
    assert core_summary.curator["rank"] == 0


def test_skill_curator_creates_updates_archives_and_restores_workspace_skills(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()

    created = service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-note",
        title="Agent Note",
        summary="Capture repeatable note-taking preferences.",
        body="Use this when the user asks to preserve repeated project preferences.",
        rationale="Repeated request pattern.",
        tags=["memory"],
        allowed_tools=["read_file"],
    )
    assert created["accepted"] is True
    assert created["applied"] is True
    skill_text = (workspace_skills / "agent-note" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: \"agent-note\"" in skill_text
    assert "description: \"Capture repeatable note-taking preferences.\"" in skill_text

    result = service.discover(config=config, fingerprint="curator-created")
    assert "agent-note" in [summary.skill_id for summary in result.enabled_summaries]

    service.mentioned_skill_content_summaries(
        config=config,
        fingerprint="curator-created",
        skill_ids=("agent-note",),
    )
    service.get_skill_content(config=config, fingerprint="curator-created", skill_id="agent-note")
    report = service.manage_curator(config=config, action="report")
    tracked = report["least_recently_active"][0]
    assert tracked["skill_id"] == "agent-note"
    assert tracked["use_count"] == 1
    assert tracked["view_count"] == 1

    pinned = service.manage_curator(config=config, action="pin", skill_id="agent-note")
    assert pinned["pinned"] is True
    blocked_archive = service.manage_curator(config=config, action="archive", skill_id="agent-note")
    assert blocked_archive["accepted"] is False
    archive = service.manage_curator(config=config, action="archive", skill_id="agent-note", force=True)
    assert archive["accepted"] is True
    assert not (workspace_skills / "agent-note").exists()

    restore = service.manage_curator(config=config, action="restore", skill_id="agent-note")
    assert restore["accepted"] is True
    assert (workspace_skills / "agent-note" / "SKILL.md").exists()


def test_skill_curator_backup_stops_at_scan_budget_without_rglob(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-large",
        title="Agent Large",
        summary="Large support tree.",
        body="Use this when a large support tree must be backed up safely.",
    )
    references_dir = workspace_skills / "agent-large" / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    for index in range(16):
        (references_dir / f"guide-{index:02}.md").write_text("reference guide", encoding="utf-8")

    from anvil.skills import curator as skills_curator_module

    def fail_rglob(_self: Path, pattern: str):
        raise AssertionError(f"curator backup should not use rglob({pattern!r})")

    monkeypatch.setattr(Path, "rglob", fail_rglob)
    monkeypatch.setattr(skills_curator_module, "DEFAULT_CURATOR_BACKUP_SCAN_LIMIT", 5, raising=False)

    backup = service.manage_curator(config=config, action="backup", skill_id="agent-large")

    assert backup["accepted"] is True
    assert backup["backed_up"] is True
    assert backup["backup_scan_truncated"] is True
    assert backup["backup_scanned_path_count"] == 5


def test_skill_curator_rollback_blocks_path_traversal_backup(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-restore",
        title="Agent Restore",
        summary="Restore backups safely.",
        body="Use this when backup restoration must stay inside the workspace.",
    )
    backup_dir = contract_tmp_path / "governance" / "curator" / "backups" / "agent-restore"
    backup_dir.mkdir(parents=True, exist_ok=True)
    malicious_backup = backup_dir / "malicious.skill"
    with zipfile.ZipFile(malicious_backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt", "escape")
        archive.writestr("agent-restore/SKILL.md", "# Agent Restore\n\nSafe body\n")

    rollback = service.manage_curator(
        config=config,
        action="rollback",
        skill_id="agent-restore",
        revision="malicious.skill",
    )

    assert rollback["accepted"] is False
    assert "path traversal" in str(rollback["error"])
    assert not (workspace_skills / "escape.txt").exists()
    assert not (contract_tmp_path / "governance" / "curator" / "backups" / "escape.txt").exists()


def test_skill_curator_rollback_stops_at_backup_scan_budget(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.curator.DEFAULT_CURATOR_BACKUP_SCAN_LIMIT", 5, raising=False)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-budget",
        title="Agent Budget",
        summary="Restore backups within budget.",
        body="Original body.",
    )
    backup_dir = contract_tmp_path / "governance" / "curator" / "backups" / "agent-budget"
    backup_dir.mkdir(parents=True, exist_ok=True)
    large_backup = backup_dir / "large.skill"
    with zipfile.ZipFile(large_backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("agent-budget/SKILL.md", "# Agent Budget\n\nRestored body\n")
        for index in range(16):
            archive.writestr(f"agent-budget/references/ref-{index:02}.md", "reference")

    rollback = service.manage_curator(
        config=config,
        action="rollback",
        skill_id="agent-budget",
        revision="large.skill",
    )

    assert rollback["accepted"] is False
    assert "scan truncated" in str(rollback["error"])
    assert "Original body" in (workspace_skills / "agent-budget" / "SKILL.md").read_text(encoding="utf-8")


def test_skill_curator_rollback_stops_at_backup_candidate_scan_budget(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.curator.DEFAULT_CURATOR_BACKUP_SCAN_LIMIT", 5, raising=False)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-candidates",
        title="Agent Candidates",
        summary="Restore from bounded candidate lists.",
        body="Original body.",
    )
    backup_dir = contract_tmp_path / "governance" / "curator" / "backups" / "agent-candidates"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for index in range(16):
        backup_path = backup_dir / f"202605300000{index:02}-pre-curator.skill"
        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("agent-candidates/SKILL.md", f"# Agent Candidates\n\nRestored body {index}\n")

    rollback = service.manage_curator(
        config=config,
        action="rollback",
        skill_id="agent-candidates",
    )

    assert rollback["accepted"] is False
    assert "candidate scan truncated" in str(rollback["error"])
    assert rollback["backup_candidate_scan_truncated"] is True
    assert rollback["backup_candidate_scanned_path_count"] == 5
    assert "Original body" in (workspace_skills / "agent-candidates" / "SKILL.md").read_text(encoding="utf-8")


def test_skill_curator_restore_stops_at_archive_candidate_scan_budget(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.curator.DEFAULT_CURATOR_BACKUP_SCAN_LIMIT", 5, raising=False)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    archive_root = contract_tmp_path / "governance" / "curator" / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    for index in range(16):
        archive_dir = archive_root / f"agent-archived-202605300000{index:02}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "SKILL.md").write_text(f"# Agent Archived\n\nRestored body {index}\n", encoding="utf-8")

    service = SkillsService()
    restore = service.manage_curator(config=config, action="restore", skill_id="agent-archived")

    assert restore["accepted"] is False
    assert "archive candidate scan truncated" in str(restore["error"])
    assert restore["archive_candidate_scan_truncated"] is True
    assert restore["archive_candidate_scanned_path_count"] == 5
    assert not (workspace_skills / "agent-archived").exists()


def test_skill_curator_learns_reinforces_and_promotes_procedures(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()

    first = service.manage_curator(
        config=config,
        action="learn_procedure",
        title="Patch Focused Python Bug",
        trigger="A Python bugfix needs a focused edit with regression evidence.",
        steps=[
            "Inspect the failing function and nearest tests.",
            "Patch only the smallest affected block.",
            "Run the focused test and record the result.",
        ],
        expected_outcome="Bug is fixed with a focused test result.",
        evidence_refs=["thread:alpha"],
        source_ref="thread:alpha",
        confidence=0.8,
    )
    second = service.manage_curator(
        config=config,
        action="learn_procedure",
        title="Patch Focused Python Bug",
        trigger="A Python bugfix needs a focused edit with regression evidence.",
        steps=[
            "Inspect the failing function and nearest tests.",
            "Patch only the smallest affected block.",
            "Run the focused test and record the result.",
        ],
        evidence_refs=["thread:beta"],
        source_ref="thread:beta",
        confidence=0.9,
    )
    report = service.manage_curator(config=config, action="procedures")
    recommendations = service.manage_curator(config=config, action="report")["recommendations"]
    promoted = service.manage_curator(
        config=config,
        action="promote_procedure",
        procedure_id=first["procedure_id"],
        skill_id="agent-focused-python-bugfix",
    )

    assert first["accepted"] is True
    assert first["reinforced"] is False
    assert second["accepted"] is True
    assert second["reinforced"] is True
    assert second["candidate"]["frequency"] == 2
    assert set(second["candidate"]["evidence_refs"]) == {"thread:alpha", "thread:beta"}
    assert second["candidate"]["outcome_health"]["success_count"] == 2
    assert second["candidate"]["quality"]["quality_score"] >= 0.58
    assert second["candidate"]["quality"]["verification_signal"] is True
    assert second["candidate"]["promotion_readiness"]["promotable"] is True
    assert second["candidate"]["promotion_readiness"]["quality_score"] == second["candidate"]["quality"]["quality_score"]
    assert report["counts"]["total"] == 1
    assert report["counts"]["promotable"] == 1
    assert report["items"][0]["procedure_id"] == first["procedure_id"]
    assert report["items"][0]["promotion_readiness"]["recommendation"] == "promote"
    procedure_recommendation = next(item for item in recommendations if item["action"] == "promote_procedure")
    assert procedure_recommendation["procedure_id"] == first["procedure_id"]
    assert procedure_recommendation["promotion_readiness"]["promotable"] is True
    assert promoted["accepted"] is True
    skill_path = workspace_skills / "agent-focused-python-bugfix" / "SKILL.md"
    assert skill_path.exists()
    skill_text = skill_path.read_text(encoding="utf-8")
    assert "Patch Focused Python Bug" in skill_text
    assert "## Procedure" in skill_text
    assert "## Promotion Evidence" in skill_text
    assert "- Quality:" in skill_text
    assert "thread:alpha" in skill_text


def test_skill_curator_reinforces_similar_procedure_signatures_across_runs(
    contract_tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    steps = [
        "Inspect the failing function and nearest tests.",
        "Patch only the smallest affected block.",
        "Run the focused test and record the result.",
    ]

    first = service.manage_curator(
        config=config,
        action="learn_procedure",
        title="Focused Python Fix",
        trigger="A backend defect needs a contained Python patch.",
        steps=steps,
        expected_outcome="Bug is fixed with a focused regression test.",
        allowed_tools=["read_file", "patch_file", "run_command"],
        evidence_refs=["thread:alpha/run:run-a", "pytest:alpha"],
        source_ref="thread:alpha/run:run-a",
        confidence=0.86,
    )
    second = service.manage_curator(
        config=config,
        action="learn_procedure",
        title="Targeted Regression Repair",
        trigger="A similar issue needs the same inspect-patch-test workflow.",
        steps=steps,
        expected_outcome="Regression is resolved and verified.",
        allowed_tools=["read_file", "patch_file", "run_command"],
        evidence_refs=["thread:beta/run:run-b", "pytest:beta"],
        source_ref="thread:beta/run:run-b",
        confidence=0.9,
    )
    report = service.manage_curator(config=config, action="procedures")
    item = report["items"][0]

    assert first["accepted"] is True
    assert second["accepted"] is True
    assert second["reinforced"] is True
    assert second["procedure_id"] == first["procedure_id"]
    assert report["counts"]["total"] == 1
    assert item["frequency"] == 2
    assert set(item["source_refs"]) == {"thread:alpha/run:run-a", "thread:beta/run:run-b"}
    assert item["quality"]["source_count"] == 2
    assert item["promotion_readiness"]["promotable"] is True


def test_skill_curator_merges_semantically_duplicate_procedure_without_allowed_tools(
    contract_tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    steps = [
        "Inspect the failing function and nearest tests.",
        "Patch only the smallest affected block.",
        "Run the focused test and record the result.",
    ]

    first = service.manage_curator(
        config=config,
        action="learn_procedure",
        title="Focused Python Fix",
        trigger="A backend defect needs a contained Python patch.",
        steps=steps,
        expected_outcome="Bug is fixed with a focused regression test.",
        evidence_refs=["thread:alpha/run:run-a", "pytest:alpha"],
        source_ref="thread:alpha/run:run-a",
        confidence=0.86,
    )
    second = service.manage_curator(
        config=config,
        action="learn_procedure",
        title="Targeted Regression Repair",
        trigger="A similar issue needs the same inspect-patch-test workflow.",
        steps=steps,
        expected_outcome="Regression is resolved and verified.",
        evidence_refs=["thread:beta/run:run-b", "pytest:beta"],
        source_ref="thread:beta/run:run-b",
        confidence=0.9,
    )
    report = service.manage_curator(config=config, action="procedures")

    assert first["accepted"] is True
    assert second["accepted"] is True
    assert second["reinforced"] is True
    assert second["procedure_id"] == first["procedure_id"]
    assert report["counts"]["total"] == 1
    assert report["items"][0]["frequency"] == 2


def test_skill_curator_blocks_low_quality_procedure_auto_promotion(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    common = {
        "title": "Vague One Off Workflow",
        "trigger": "A vague task might be repeated.",
        "steps": ["Do the thing.", "Summarize it."],
        "expected_outcome": "",
        "evidence_refs": ["thread:vague"],
        "source_ref": "thread:vague",
        "outcome": "success",
        "feedback_source": "runtime_success",
        "confidence": 0.95,
    }

    service.manage_curator(config=config, action="learn_procedure", **common)
    second = service.manage_curator(config=config, action="learn_procedure", **common)
    report = service.manage_curator(config=config, action="procedures")
    recommendations = service.manage_curator(config=config, action="report")["recommendations"]
    item = report["items"][0]

    assert second["accepted"] is True
    assert item["frequency"] == 2
    assert item["quality"]["quality_score"] < 0.58
    assert item["promotion_readiness"]["promotable"] is False
    assert "weak_quality" in item["promotion_readiness"]["blockers"]
    assert "needs_expected_outcome" in item["promotion_readiness"]["blockers"]
    assert "generic_steps" in item["quality"]["blockers"]
    assert report["counts"]["promotable"] == 0
    assert not [candidate for candidate in recommendations if candidate.get("action") == "promote_procedure"]


def test_skill_curator_procedure_failure_signal_blocks_promotion(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    common = {
        "title": "Unstable Browser Workflow",
        "trigger": "A browser automation workflow appears to be reusable.",
        "steps": ["Open the target page.", "Click the action.", "Validate the page state."],
        "expected_outcome": "The browser state changes predictably.",
        "evidence_refs": ["thread:unstable"],
        "source_ref": "thread:unstable",
    }

    service.manage_curator(config=config, action="learn_procedure", confidence=0.9, outcome="success", **common)
    service.manage_curator(config=config, action="learn_procedure", confidence=0.95, outcome="failure", feedback_source="runtime_failure", rationale="Selector failed.", **common)
    service.manage_curator(config=config, action="learn_procedure", confidence=0.9, outcome="failure", feedback_source="agent", rationale="The workflow was not reusable.", **common)
    report = service.manage_curator(config=config, action="procedures")
    item = report["items"][0]

    assert item["frequency"] == 3
    assert item["outcome_health"]["failure_count"] == 2
    assert item["promotion_readiness"]["promotable"] is False
    assert "failure_signal" in item["promotion_readiness"]["blockers"]
    assert report["counts"]["promotable"] == 0
    recommendations = service.manage_curator(config=config, action="report")["recommendations"]
    assert not [candidate for candidate in recommendations if candidate.get("action") == "promote_procedure"]


def test_skill_curator_rejects_and_restores_procedure_candidates(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()

    learned = service.manage_curator(
        config=config,
        action="learn_procedure",
        title="Rejectable Procedure",
        trigger="A workflow should not become a reusable skill.",
        steps=["Inspect the candidate.", "Decline promotion."],
        expected_outcome="The candidate remains out of the default promotion queue.",
        evidence_refs=["thread:reject"],
        source_ref="thread:reject",
        confidence=0.9,
    )
    rejected = service.manage_curator(
        config=config,
        action="reject_procedure",
        procedure_id=learned["procedure_id"],
        rationale="Too task-specific.",
    )
    default_report = service.manage_curator(config=config, action="procedures")
    audit_report = service.manage_curator(config=config, action="procedures", outcome="all")
    promoted = service.manage_curator(
        config=config,
        action="promote_procedure",
        procedure_id=learned["procedure_id"],
        skill_id="learned-rejected-procedure",
    )
    restored = service.manage_curator(
        config=config,
        action="restore_procedure",
        procedure_id=learned["procedure_id"],
        rationale="The rejection was incorrect.",
    )
    restored_report = service.manage_curator(config=config, action="procedures")
    promoted_after_restore = service.manage_curator(
        config=config,
        action="promote_procedure",
        procedure_id=learned["procedure_id"],
        skill_id="learned-restored-procedure",
    )

    assert rejected["accepted"] is True
    assert rejected["candidate"]["status"] == "rejected"
    assert default_report["items"] == []
    assert default_report["counts"]["rejected"] == 1
    assert audit_report["items"][0]["status"] == "rejected"
    assert promoted["accepted"] is False
    assert "was rejected" in promoted["error"]
    assert restored["accepted"] is True
    assert restored["candidate"]["status"] == "candidate"
    assert restored_report["items"][0]["procedure_id"] == learned["procedure_id"]
    assert promoted_after_restore["accepted"] is True
    assert promoted_after_restore["candidate"]["status"] == "promoted"


def test_skill_curator_patches_support_files_reports_and_rolls_back(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-cleanup",
        title="Agent Cleanup",
        summary="Clean repeated temporary files.",
        body="Use this when cleanup is requested.",
    )

    patch = service.manage_curator(
        config=config,
        action="patch",
        skill_id="agent-cleanup",
        old_text="cleanup is requested",
        new_text="cleanup or artifact pruning is requested",
    )
    assert patch["accepted"] is True
    assert "artifact pruning" in (workspace_skills / "agent-cleanup" / "SKILL.md").read_text(encoding="utf-8")

    support_file = service.manage_curator(
        config=config,
        action="write_file",
        skill_id="agent-cleanup",
        file_path="references/rules.md",
        content="Prefer dry-run before deleting generated artifacts.",
    )
    assert support_file["accepted"] is True
    assert (workspace_skills / "agent-cleanup" / "references" / "rules.md").exists()

    backup = service.manage_curator(config=config, action="backup", skill_id="agent-cleanup")
    assert backup["backed_up"] is True

    service.manage_curator(
        config=config,
        action="patch",
        skill_id="agent-cleanup",
        old_text="artifact pruning",
        new_text="artifact pruning after review",
    )
    rollback = service.manage_curator(
        config=config,
        action="rollback",
        skill_id="agent-cleanup",
        revision=Path(str(backup["backup_path"])).name,
    )
    assert rollback["rolled_back"] is True
    assert "after review" not in (workspace_skills / "agent-cleanup" / "SKILL.md").read_text(encoding="utf-8")

    remove_file = service.manage_curator(
        config=config,
        action="remove_file",
        skill_id="agent-cleanup",
        file_path="references/rules.md",
    )
    assert remove_file["accepted"] is True
    assert not (workspace_skills / "agent-cleanup" / "references" / "rules.md").exists()

    report = service.manage_curator(config=config, action="curate", dry_run=True)
    assert report["accepted"] is True
    assert Path(str(report["run_json_path"])).exists()
    assert Path(str(report["report_path"])).exists()


def test_skill_curator_marks_stale_and_archives_by_usage_age(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    for skill_id in ("agent-stale", "agent-archive"):
        service.manage_curator(
            config=config,
            action="create",
            skill_id=skill_id,
            title=skill_id,
            summary=f"{skill_id} summary",
            body=f"# {skill_id}\n\n{skill_id} body",
        )

    usage_path = contract_tmp_path / "governance" / "curator" / "usage.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    usage["agent-stale"]["last_activity_at"] = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    usage["agent-archive"]["last_activity_at"] = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    usage_path.write_text(json.dumps(usage), encoding="utf-8")

    result = service.manage_curator(config=config, action="curate")
    actions = {item["skill_id"]: item["action"] for item in result["actions"] if "skill_id" in item}

    assert actions["agent-stale"] == "mark_stale"
    assert actions["agent-archive"] == "archive"
    assert (workspace_skills / "agent-stale" / "SKILL.md").exists()
    assert not (workspace_skills / "agent-archive").exists()
    assert any((contract_tmp_path / "governance" / "curator" / "archive").glob("agent-archive-*"))
    usage_after = json.loads(usage_path.read_text(encoding="utf-8"))
    assert usage_after["agent-stale"]["state"] == "stale"
    assert usage_after["agent-archive"]["state"] == "archived"


def test_skill_curator_plans_and_applies_duplicate_merges(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    bodies = {
        "agent-merge-primary": "Use when repeated procedural memory should be consolidated.",
        "agent-merge-copy": (
            "Use when repeated procedural memory should be consolidated.\n"
            "- Review duplicate candidates before merging them."
        ),
    }
    for skill_id, body in bodies.items():
        service.manage_curator(
            config=config,
            action="create",
            skill_id=skill_id,
            title="Agent Merge",
            summary="Capture duplicate procedural memory.",
            body=body,
        )

    service.get_skill_content(config=config, fingerprint="merge-score", skill_id="agent-merge-primary")
    plan = service.manage_curator(config=config, action="merge_plan", skill_id="agent-merge-copy")

    assert plan["accepted"] is True
    assert plan["mode"] == "curator_merge_plan"
    assert Path(str(plan["proposal_path"])).exists()
    assert Path(str(plan["proposal_report_path"])).exists()
    assert plan["proposal"]["primary_skill_id"] == "agent-merge-primary"
    assert plan["proposal"]["source_skill_ids"] == ["agent-merge-copy"]

    dry_apply = service.manage_curator(
        config=config,
        action="merge_apply",
        revision=str(plan["proposal_id"]),
        dry_run=True,
    )
    assert dry_apply["accepted"] is True
    assert dry_apply["would_archive"] == ["agent-merge-copy"]
    assert (workspace_skills / "agent-merge-copy" / "SKILL.md").exists()

    applied = service.manage_curator(
        config=config,
        action="merge_apply",
        revision=str(plan["proposal_id"]),
    )
    assert applied["accepted"] is True
    assert applied["archived_skill_ids"] == ["agent-merge-copy"]
    assert (workspace_skills / "agent-merge-primary" / "SKILL.md").exists()
    assert not (workspace_skills / "agent-merge-copy").exists()

    usage = json.loads((contract_tmp_path / "governance" / "curator" / "usage.json").read_text(encoding="utf-8"))
    assert usage["agent-merge-copy"]["state"] == "archived"
    assert usage["agent-merge-copy"]["absorbed_into"] == "agent-merge-primary"
    assert usage["agent-merge-primary"]["merged_from"] == ["agent-merge-copy"]
    primary_text = (workspace_skills / "agent-merge-primary" / "SKILL.md").read_text(encoding="utf-8")
    assert "Curated Merge Notes" in primary_text
    assert "From agent-merge-copy" in primary_text
    assert "Review duplicate candidates before merging them" in primary_text
    proposal_payload = json.loads(Path(str(applied["proposal_path"])).read_text(encoding="utf-8"))
    assert proposal_payload["status"] == "applied"
    assert proposal_payload["primary_patch_result"]["applied"] is True


def test_skill_curator_duplicate_merge_distills_source_details_and_scrubs_secrets(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-distill-primary",
        title="Agent Distill",
        summary="Capture duplicate distillation practices.",
        body="Use when duplicate skill details should be merged safely.",
    )
    env_name = "OPENAI" "_API_KEY"
    fake_key = "sk" "-proj-" "testabcdefghijklmnopqrstuvwx"
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-distill-copy",
        title="Agent Distill",
        summary="Capture duplicate distillation practices.",
        body=(
            "Use when duplicate skill details should be merged safely.\n"
            "- Preserve exact user terminology when creating migration notes.\n"
            f"- Store {env_name}={fake_key} only in environment variables."
        ),
    )

    plan = service.manage_curator(
        config=config,
        action="merge_plan",
        skill_id="agent-distill-copy",
        absorbed_into="agent-distill-primary",
    )

    assert plan["accepted"] is True
    primary_patch = plan["proposal"]["primary_patch"]
    assert primary_patch["proposed"] is True
    assert "Preserve exact user terminology" in primary_patch["append_text"]
    assert fake_key not in primary_patch["append_text"]
    assert "[REDACTED:openai_project_token]" in primary_patch["append_text"]
    assert "openai_project_token" in primary_patch["redacted_rules"]

    applied = service.manage_curator(
        config=config,
        action="merge_apply",
        revision=str(plan["proposal_id"]),
    )

    assert applied["accepted"] is True
    assert applied["primary_patch_result"]["applied"] is True
    primary_text = (workspace_skills / "agent-distill-primary" / "SKILL.md").read_text(encoding="utf-8")
    assert "Preserve exact user terminology" in primary_text
    assert fake_key not in primary_text
    assert "[REDACTED:openai_project_token]" in primary_text
    assert not (workspace_skills / "agent-distill-copy").exists()


def test_skill_curator_duplicate_merge_respects_pinned_sources(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    for skill_id in ("agent-pin-primary", "agent-pin-copy"):
        service.manage_curator(
            config=config,
            action="create",
            skill_id=skill_id,
            title="Agent Pin Merge",
            summary="Capture pinned duplicate procedural memory.",
            body="Use when pinned repeated procedural memory should be consolidated.",
        )
    service.manage_curator(config=config, action="pin", skill_id="agent-pin-copy")

    plan = service.manage_curator(
        config=config,
        action="merge_plan",
        skill_id="agent-pin-primary",
        absorbed_into="agent-pin-primary",
    )
    assert plan["accepted"] is True
    assert plan["proposal"]["requires_force"] is True

    blocked = service.manage_curator(config=config, action="merge_apply", revision=str(plan["proposal_id"]))
    assert blocked["accepted"] is False
    assert "pinned" in blocked["error"]
    assert (workspace_skills / "agent-pin-copy" / "SKILL.md").exists()

    forced = service.manage_curator(
        config=config,
        action="merge_apply",
        revision=str(plan["proposal_id"]),
        force=True,
    )
    assert forced["accepted"] is True
    assert not (workspace_skills / "agent-pin-copy").exists()


def test_skill_curator_merge_plan_stops_at_existing_proposal_scan_budget(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.curator.DEFAULT_CURATOR_BACKUP_SCAN_LIMIT", 5, raising=False)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    for skill_id in ("agent-merge-scan-primary", "agent-merge-scan-copy"):
        service.manage_curator(
            config=config,
            action="create",
            skill_id=skill_id,
            title="Agent Merge Scan",
            summary="Capture bounded merge proposal scanning.",
            body="Use when duplicate proposal reuse must avoid unbounded scans.",
        )
    proposal_root = contract_tmp_path / "governance" / "curator" / "merge-proposals"
    proposal_root.mkdir(parents=True, exist_ok=True)
    for index in range(16):
        proposal_dir = proposal_root / f"merge-existing-{index:02}"
        proposal_dir.mkdir(parents=True, exist_ok=True)
        (proposal_dir / "proposal.json").write_text(
            json.dumps(
                {
                    "proposal_id": f"merge-existing-{index:02}",
                    "status": "proposed",
                    "fingerprint": "unrelated",
                    "skill_ids": ["unrelated-a", "unrelated-b"],
                    "primary_skill_id": "unrelated-a",
                }
            ),
            encoding="utf-8",
        )

    plan = service.manage_curator(config=config, action="merge_plan", skill_id="agent-merge-scan-copy")

    assert plan["accepted"] is False
    assert "merge proposal scan truncated" in str(plan["error"])
    assert plan["merge_proposal_scan_truncated"] is True
    assert plan["merge_proposal_scanned_path_count"] == 5
    assert (workspace_skills / "agent-merge-scan-primary" / "SKILL.md").exists()
    assert (workspace_skills / "agent-merge-scan-copy" / "SKILL.md").exists()


def test_skill_curator_auto_merge_can_be_disabled(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(auto_merge=False),
        )
    )
    service = SkillsService()
    for skill_id in ("agent-review-a", "agent-review-b"):
        service.manage_curator(
            config=config,
            action="create",
            skill_id=skill_id,
            title="Agent Review",
            summary="Capture duplicate review practices.",
            body="Use when duplicate skills should be reviewed before merge.",
        )

    result = service.manage_curator(config=config, action="curate")

    assert result["accepted"] is True
    assert any(item["action"] == "review_duplicates" and item["reason"] == "auto_merge disabled" for item in result["actions"])
    assert not (contract_tmp_path / "governance" / "curator" / "merge-proposals").exists()


def test_skill_curator_feedback_tracks_outcomes_and_adjusts_utility(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-feedback",
        title="Agent Feedback",
        summary="Capture reusable skill result feedback.",
        body="Use when skill result quality should influence future selection.",
    )

    ok = service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-feedback",
        outcome="success",
        rationale="Skill produced the expected migration checklist.",
        feedback_source="user",
        confidence=0.9,
    )
    failed = service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-feedback",
        outcome="failure",
        rationale="Skill missed a required verification command.",
    )

    assert ok["accepted"] is True
    assert ok["outcome"] == "success"
    assert failed["accepted"] is True
    usage = json.loads((contract_tmp_path / "governance" / "curator" / "usage.json").read_text(encoding="utf-8"))
    item = usage["agent-feedback"]
    assert item["success_count"] == 1
    assert item["failure_count"] == 1
    assert item["feedback_count"] == 2
    assert item["last_feedback"]["outcome"] == "failure"
    assert item["last_feedback"]["source"] == "agent"
    assert item["last_feedback"]["confidence"] == 1.0
    assert item["feedback_by_source"]["user"] == 1
    assert item["feedback_by_source"]["agent"] == 1
    assert item["confidence_totals"]["success"] == 0.9
    assert item["confidence_totals"]["failure"] == 1.0
    assert ok["feedback_source"] == "user"
    assert ok["confidence"] == 0.9

    result = service.manage_curator(config=config, action="curate", dry_run=True)
    assert result["accepted"] is True
    refreshed = json.loads((contract_tmp_path / "governance" / "curator" / "usage.json").read_text(encoding="utf-8"))
    assert refreshed["agent-feedback"]["utility_score"] == 7


def test_skill_curator_utility_weights_runtime_feedback_by_confidence(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-runtime-weight",
        title="Agent Runtime Weight",
        summary="Exercise confidence-weighted runtime utility.",
        body="Use when runtime feedback should be weaker than explicit feedback.",
    )

    success = service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-runtime-weight",
        outcome="success",
        feedback_source="runtime_success",
        confidence=0.4,
    )
    failure = service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-runtime-weight",
        outcome="failure",
        feedback_source="runtime_failure",
        confidence=0.7,
    )

    assert success["utility_score"] == 37
    assert failure["utility_score"] == 0
    usage = json.loads((contract_tmp_path / "governance" / "curator" / "usage.json").read_text(encoding="utf-8"))
    item = usage["agent-runtime-weight"]
    assert item["confidence_totals"]["success"] == 0.4
    assert item["confidence_totals"]["failure"] == 0.7
    assert item["utility_score"] == 0


def test_skill_curator_explicit_feedback_keeps_strong_utility_weight(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-explicit-weight",
        title="Agent Explicit Weight",
        summary="Exercise explicit feedback utility weight.",
        body="Use when explicit feedback should stay a strong signal.",
    )

    success = service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-explicit-weight",
        outcome="success",
        feedback_source="user",
        confidence=0.9,
    )

    assert success["utility_score"] == 77


def test_skill_curator_report_exposes_weighted_feedback_health(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-health",
        title="Agent Health",
        summary="Exercise weighted curator feedback health.",
        body="Use when feedback health needs to be surfaced.",
    )
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-health",
        outcome="success",
        feedback_source="user",
        confidence=0.9,
    )
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-health",
        outcome="failure",
        feedback_source="runtime_failure",
        confidence=0.7,
    )

    report = service.manage_curator(config=config, action="report")

    item = next(entry for entry in report["least_recently_active"] if entry["skill_id"] == "agent-health")
    assert item["feedback_health"] == {
        "success_confidence": 0.9,
        "failure_confidence": 0.7,
        "neutral_confidence": 0.0,
        "net_confidence": 0.2,
        "dominant_source": "runtime_failure",
        "confidence_samples": 1.6,
    }
    assert item["last_feedback_source"] == "runtime_failure"
    assert item["last_feedback_confidence"] == 0.7


def test_skill_curator_report_ranks_governance_recommendations(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(core_score_threshold=200, template_use_threshold=2, template_context_threshold=2),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-review-rec",
        title="Agent Review Recommendation",
        summary="Capture review recommendation behavior.",
        body="Use when a skill needs explicit review.",
    )
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-review-rec",
        outcome="failure",
        rationale="The skill skipped verification evidence.",
        feedback_source="user",
        confidence=1.0,
    )
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-template-rec",
        title="Agent Template Recommendation",
        summary="Capture template recommendation behavior.",
        body="Use when repeated behavior should become reusable.",
    )
    service.mentioned_skill_content_summaries(config=config, fingerprint="repo-a", skill_ids=("agent-template-rec",))
    service.mentioned_skill_content_summaries(config=config, fingerprint="repo-b", skill_ids=("agent-template-rec",))

    report = service.manage_curator(config=config, action="report")

    recommendations = report["recommendations"]
    assert recommendations[0]["action"] == "quality_plan"
    assert recommendations[0]["skill_id"] == "agent-review-rec"
    assert recommendations[0]["next_tool_call"] == {
        "action": "quality_plan",
        "skill_id": "agent-review-rec",
    }
    template_rec = next(item for item in recommendations if item.get("skill_id") == "agent-template-rec")
    assert template_rec["action"] == "curate"
    assert template_rec["next_tool_call"] == {"action": "curate"}
    assert template_rec["reason"] == "reused across contexts"


def test_skill_curator_curate_report_persists_recommendations(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-run-rec",
        title="Agent Run Recommendation",
        summary="Capture run recommendation behavior.",
        body="Use when curate run reports should guide the next action.",
    )
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-run-rec",
        outcome="failure",
        rationale="The skill returned stale steps.",
        feedback_source="user",
        confidence=1.0,
    )

    run = service.manage_curator(config=config, action="curate", dry_run=True)

    assert run["recommendations"][0]["action"] == "quality_plan"
    run_payload = json.loads(Path(str(run["run_json_path"])).read_text(encoding="utf-8"))
    assert run_payload["recommendations"][0]["skill_id"] == "agent-run-rec"
    report_text = Path(str(run["report_path"])).read_text(encoding="utf-8")
    assert "## Recommendations" in report_text
    assert "quality_plan: agent-run-rec" in report_text


def test_skill_curator_auto_review_ignores_single_low_confidence_runtime_failure(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-runtime-noise",
        title="Agent Runtime Noise",
        summary="Exercise low confidence runtime feedback filtering.",
        body="Use when runtime failure signals should not over-trigger review.",
    )
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-runtime-noise",
        outcome="failure",
        rationale="Provider transport failed after the skill was loaded.",
        feedback_source="runtime_failure",
        confidence=0.7,
    )

    report = service.manage_curator(config=config, action="curate")

    assert [item for item in report["actions"] if item["action"] == "quality_plan"] == []
    usage = json.loads((contract_tmp_path / "governance" / "curator" / "usage.json").read_text(encoding="utf-8"))
    assert usage["agent-runtime-noise"]["failure_count"] == 1
    assert usage["agent-runtime-noise"]["feedback_by_source"]["runtime_failure"] == 1


def test_skill_curator_quality_plan_generates_bounded_proposal_without_mutating_skill(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-review-plan",
        title="Agent Review Plan",
        summary="Capture review plan behavior.",
        body=(
            "Use when skills need quality review.\n"
            "Always record verification commands.\n"
            "Never persist SERVICE_TOKEN=local-dev-placeholder-12345 in skill text."
        ),
    )
    original_text = (workspace_skills / "agent-review-plan" / "SKILL.md").read_text(encoding="utf-8")
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-review-plan",
        outcome="failure",
        rationale="Missed required verification evidence and leaked a provider placeholder.",
    )

    planned = service.manage_curator(
        config=config,
        action="quality_plan",
        skill_id="agent-review-plan",
        rationale="Prepare a safe quality-improvement proposal.",
    )

    assert planned["accepted"] is True
    assert planned["mode"] == "curator_quality_plan"
    assert Path(str(planned["proposal_path"])).exists()
    assert Path(str(planned["proposal_report_path"])).exists()
    assert planned["proposal"]["skill_id"] == "agent-review-plan"
    assert planned["proposal"]["status"] == "proposed"
    assert planned["proposal"]["patch"]["proposed"] is True
    assert "local-dev-placeholder-12345" not in planned["proposal"]["patch"]["append_text"]
    assert "[REDACTED:" in planned["proposal"]["patch"]["append_text"]
    assert "failure feedback" in " ".join(planned["proposal"]["recommendations"])
    assert (workspace_skills / "agent-review-plan" / "SKILL.md").read_text(encoding="utf-8") == original_text


def test_skill_curator_review_apply_applies_bounded_patch_and_respects_pin(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-review-apply",
        title="Agent Review Apply",
        summary="Apply bounded review proposals.",
        body="Use when a skill needs safe review application.\nAlways keep existing behavior.",
    )
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-review-apply",
        outcome="failure",
        rationale="The skill missed verification evidence.",
    )
    planned = service.manage_curator(config=config, action="quality_plan", skill_id="agent-review-apply")
    original_text = (workspace_skills / "agent-review-apply" / "SKILL.md").read_text(encoding="utf-8")

    service.manage_curator(config=config, action="pin", skill_id="agent-review-apply")
    blocked = service.manage_curator(config=config, action="review_apply", revision=str(planned["proposal_id"]))

    assert blocked["accepted"] is False
    assert "pinned" in blocked["error"]
    assert (workspace_skills / "agent-review-apply" / "SKILL.md").read_text(encoding="utf-8") == original_text

    dry_apply = service.manage_curator(
        config=config,
        action="review_apply",
        revision=str(planned["proposal_id"]),
        dry_run=True,
        force=True,
    )
    assert dry_apply["accepted"] is True
    assert dry_apply["dry_run"] is True
    assert dry_apply["patch_result"]["dry_run"] is True

    applied = service.manage_curator(
        config=config,
        action="review_apply",
        revision=str(planned["proposal_id"]),
        force=True,
    )

    assert applied["accepted"] is True
    assert applied["mode"] == "curator_review_apply"
    assert applied["patch_result"]["applied"] is True
    updated_text = (workspace_skills / "agent-review-apply" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Curator Review Notes" in updated_text
    assert "verification evidence" in updated_text
    stored_proposal = json.loads(Path(str(applied["proposal_path"])).read_text(encoding="utf-8"))
    assert stored_proposal["status"] == "applied"
    assert stored_proposal["patch_result"]["applied"] is True
    usage = json.loads((contract_tmp_path / "governance" / "curator" / "usage.json").read_text(encoding="utf-8"))
    assert usage["agent-review-apply"]["patch_count"] >= 1


def test_skill_curator_auto_quality_plans_failure_feedback_without_mutating_skill(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-auto-review",
        title="Agent Auto Review",
        summary="Exercise automatic review planning.",
        body="Use when automatic curator review planning needs verification.",
    )
    original_text = (workspace_skills / "agent-auto-review" / "SKILL.md").read_text(encoding="utf-8")
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-auto-review",
        outcome="failure",
        rationale="The skill produced weak verification evidence.",
    )

    report = service.manage_curator(config=config, action="curate")

    review_actions = [item for item in report["actions"] if item["action"] == "quality_plan"]
    assert review_actions
    assert review_actions[0]["skill_id"] == "agent-auto-review"
    assert Path(str(review_actions[0]["proposal_path"])).exists()
    assert (workspace_skills / "agent-auto-review" / "SKILL.md").read_text(encoding="utf-8") == original_text

    second = service.manage_curator(config=config, action="curate")
    second_review_actions = [item for item in second["actions"] if item["action"] == "quality_plan"]
    assert second_review_actions
    assert second_review_actions[0]["reused"] is True


def test_skill_curator_auto_review_stops_at_existing_proposal_scan_budget(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.curator.DEFAULT_CURATOR_BACKUP_SCAN_LIMIT", 5, raising=False)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-review-scan",
        title="Agent Review Scan",
        summary="Capture bounded review proposal scanning.",
        body="Use when review proposal reuse must avoid unbounded scans.",
    )
    original_text = (workspace_skills / "agent-review-scan" / "SKILL.md").read_text(encoding="utf-8")
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-review-scan",
        outcome="failure",
        rationale="Needs review.",
    )
    proposal_root = contract_tmp_path / "governance" / "curator" / "review-proposals"
    proposal_root.mkdir(parents=True, exist_ok=True)
    for index in range(16):
        proposal_dir = proposal_root / f"review-existing-{index:02}"
        proposal_dir.mkdir(parents=True, exist_ok=True)
        (proposal_dir / "proposal.json").write_text(
            json.dumps(
                {
                    "proposal_id": f"review-existing-{index:02}",
                    "status": "proposed",
                    "skill_id": "unrelated-skill",
                }
            ),
            encoding="utf-8",
        )

    report = service.manage_curator(config=config, action="curate")
    review_actions = [item for item in report["actions"] if item["action"] == "quality_plan"]

    assert review_actions
    assert review_actions[0]["accepted"] is False
    assert "review proposal scan truncated" in str(review_actions[0]["reason"])
    assert review_actions[0]["review_proposal_scan_truncated"] is True
    assert review_actions[0]["review_proposal_scanned_path_count"] == 5
    assert (workspace_skills / "agent-review-scan" / "SKILL.md").read_text(encoding="utf-8") == original_text
    assert len(list(proposal_root.iterdir())) == 16


def test_skill_curator_scores_core_and_promotes_reusable_template(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(
                core_score_threshold=200,
                template_use_threshold=2,
                template_context_threshold=2,
            ),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-template",
        title="Agent Template",
        summary="Capture reusable cross-context project handoff practices.",
        body=(
            "Use when repeated handoff practices appear across projects.\n"
            "- Preserve stable file paths and verification evidence.\n"
            "- Convert repeated procedural steps into reusable checklist slots."
        ),
    )

    service.mentioned_skill_content_summaries(config=config, fingerprint="repo-a", skill_ids=("agent-template",))
    service.mentioned_skill_content_summaries(config=config, fingerprint="repo-b", skill_ids=("agent-template",))

    result = service.manage_curator(config=config, action="curate")
    actions = {item["action"]: item for item in result["actions"] if "skill_id" in item}

    assert actions["mark_core"]["skill_id"] == "agent-template"
    assert actions["promote_template"]["skill_id"] == "agent-template"
    template_path = workspace_skills / "agent-template" / "templates" / "reusable-template.md"
    assert template_path.exists()
    template_text = template_path.read_text(encoding="utf-8")
    assert "Reusable Skill Template" in template_text
    assert "Convert repeated procedural steps" in template_text

    usage = json.loads((contract_tmp_path / "governance" / "curator" / "usage.json").read_text(encoding="utf-8"))
    item = usage["agent-template"]
    assert item["tier"] == "core"
    assert item["context_count"] == 2
    assert item["utility_score"] >= 200
    assert item["template_path"] == "templates/reusable-template.md"

    report = service.manage_curator(config=config, action="report")
    assert report["counts"]["core"] == 1
    assert report["core"][0]["skill_id"] == "agent-template"


def test_skill_curator_pinned_skills_can_still_become_core(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(core_score_threshold=100),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-pinned-core",
        title="Agent Pinned Core",
        summary="Capture protected high-value behavior.",
        body="Use when a manually protected skill keeps proving useful.",
    )
    service.manage_curator(config=config, action="pin", skill_id="agent-pinned-core")

    result = service.manage_curator(config=config, action="curate")

    assert any(item["action"] == "mark_core" and item["skill_id"] == "agent-pinned-core" for item in result["actions"])
    usage = json.loads((contract_tmp_path / "governance" / "curator" / "usage.json").read_text(encoding="utf-8"))
    assert usage["agent-pinned-core"]["pinned"] is True
    assert usage["agent-pinned-core"]["tier"] == "core"


def test_skill_curator_observes_low_utility_skills_before_stale(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(
                observe_min_age_days=7,
                observe_score_threshold=10,
            ),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-observe",
        title="Agent Observe",
        summary="Capture a rarely reused local workflow.",
        body="Use when a narrow workflow appears once.",
    )

    usage_path = contract_tmp_path / "governance" / "curator" / "usage.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    old_activity = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    usage["agent-observe"]["created_at"] = old_activity
    usage["agent-observe"]["last_activity_at"] = old_activity
    usage_path.write_text(json.dumps(usage), encoding="utf-8")

    result = service.manage_curator(config=config, action="curate")
    actions = {item["skill_id"]: item["action"] for item in result["actions"] if "skill_id" in item}

    assert actions["agent-observe"] == "mark_observe"
    usage_after = json.loads(usage_path.read_text(encoding="utf-8"))
    assert usage_after["agent-observe"]["state"] == "active"
    assert usage_after["agent-observe"]["tier"] == "observe"
    assert usage_after["agent-observe"]["utility_score"] <= 10

    report = service.manage_curator(config=config, action="report")
    assert report["counts"]["observe"] == 1
    assert report["observe"][0]["skill_id"] == "agent-observe"


def test_skill_curator_automation_runs_when_due_and_tracks_state(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(
                automation_enabled=True,
                interval_seconds=60,
                dry_run=True,
            ),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-automation",
        title="Agent Automation",
        summary="Exercise automatic curator runs.",
        body="Use when curator automation needs verification.",
    )
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-automation",
        outcome="failure",
        rationale="Automation should surface the next review step.",
        feedback_source="user",
        confidence=1.0,
    )

    first = service.run_curator_automation_if_due(config=config)
    assert first.ran is True
    assert first.reason == "due"
    assert first.report is not None
    assert first.report["accepted"] is True
    assert first.report["recommendations"][0]["next_tool_call"] == {
        "action": "quality_plan",
        "skill_id": "agent-automation",
    }

    second = service.run_curator_automation_if_due(config=config)
    assert second.ran is False
    assert second.reason == "not_due"
    assert second.next_run_at is not None

    forced = service.run_curator_automation_if_due(config=config, force_run=True)
    assert forced.ran is True
    assert forced.reason == "forced"

    status = service.curator_automation_status(config=config)
    assert status["enabled"] is True
    assert status["schedule"] == "interval"
    assert status["auto_merge"] is True
    assert status["last_status"] == "completed"
    assert status["last_reason"] == "forced"
    assert status["last_run_id"] == forced.report["run_id"]
    assert status["last_recommendation_count"] >= 1
    assert status["last_recommendations"][0]["next_tool_call"] == {
        "action": "quality_plan",
        "skill_id": "agent-automation",
    }
    assert (contract_tmp_path / "governance" / "curator" / "automation.json").exists()


def test_skill_curator_maintenance_plans_and_executes_bounded_actions(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(
                max_quality_plan_per_run=1,
                max_procedure_promotions_per_run=1,
                max_actions_per_run=2,
            ),
        )
    )
    service = SkillsService()
    for skill_id in ("agent-review-a", "agent-review-b"):
        service.manage_curator(
            config=config,
            action="create",
            skill_id=skill_id,
            title=skill_id,
            summary=f"{skill_id} summary",
            body=f"Use when {skill_id} should be reviewed.",
        )
        service.manage_curator(
            config=config,
            action="feedback",
            skill_id=skill_id,
            outcome="failure",
            rationale="Needs review.",
            feedback_source="user",
            confidence=1.0,
        )
    for title in ("Focused Verification", "Focused Verification Extended"):
        service.manage_curator(
            config=config,
            action="learn_procedure",
            title=title,
            trigger=f"{title} trigger",
            steps=["Inspect boundary", "Run focused test"],
            expected_outcome="Verified result",
            evidence_refs=[f"thread:{title}"],
            source_ref=f"thread:{title}",
            outcome="success",
            feedback_source="user",
            confidence=0.95,
        )
        service.manage_curator(
            config=config,
            action="learn_procedure",
            title=title,
            trigger=f"{title} trigger",
            steps=["Inspect boundary", "Run focused test"],
            expected_outcome="Verified result",
            evidence_refs=[f"thread:{title}:2"],
            source_ref=f"thread:{title}:2",
            outcome="success",
            feedback_source="user",
            confidence=0.95,
        )

    dry_run = service.run_curator_maintenance(config=config, dry_run=True, source="test")
    assert dry_run["accepted"] is True
    assert dry_run["dry_run"] is True
    assert dry_run["status"] == "planned"
    assert dry_run["selected_count"] == 2
    assert dry_run["candidate_count"] >= 3
    procedures = service.manage_curator(config=config, action="procedures")
    assert procedures["counts"]["total"] == 1
    assert procedures["items"][0]["frequency"] == 4
    assert dry_run["skipped_actions"]["quality_plan"] >= 1
    assert dry_run["skipped_actions"].get("promote_procedure", 0) == 0
    assert not (workspace_skills / "learned-focused-verification" / "SKILL.md").exists()

    executed = service.run_curator_maintenance(config=config, dry_run=False, source="test")
    assert executed["status"] == "completed"
    assert executed["actions_executed"]["quality_plan"] == 1
    assert executed["actions_executed"]["promote_procedure"] == 1
    assert (workspace_skills / "learned-focused-verification" / "SKILL.md").exists()
    assert Path(str(executed["run_json_path"])).exists()


def test_skill_curator_maintenance_auto_applies_safe_review_and_merge(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(
                max_actions_per_run=4,
                max_quality_plan_per_run=1,
                max_merge_plan_per_run=1,
            ),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-auto-apply-review",
        title="Agent Auto Apply Review",
        summary="Exercise automatic review apply.",
        body="Use when automatic maintenance should close review feedback.",
    )
    service.manage_curator(
        config=config,
        action="feedback",
        skill_id="agent-auto-apply-review",
        outcome="failure",
        rationale="The skill produced weak verification evidence.",
        feedback_source="user",
        confidence=1.0,
    )
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-auto-merge-primary",
        title="Agent Auto Merge",
        summary="Consolidate duplicate automatic maintenance skills.",
        body="Use when duplicate skill details should be merged safely.",
    )
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-auto-merge-copy",
        title="Agent Auto Merge",
        summary="Consolidate duplicate automatic maintenance skills.",
        body=(
            "Use when duplicate skill details should be merged safely.\n"
            "- Preserve merged verification evidence."
        ),
    )
    service.get_skill_content(config=config, fingerprint="merge-primary", skill_id="agent-auto-merge-primary")

    dry_run = service.run_curator_maintenance(config=config, dry_run=True, source="test")
    assert dry_run["status"] == "planned"
    assert (workspace_skills / "agent-auto-merge-copy" / "SKILL.md").exists()
    assert "Curator Review Notes" not in (
        workspace_skills / "agent-auto-apply-review" / "SKILL.md"
    ).read_text(encoding="utf-8")

    executed = service.run_curator_maintenance(config=config, dry_run=False, source="test")

    assert executed["status"] == "completed"
    assert executed["actions_executed"]["quality_plan"] == 1
    assert executed["actions_executed"]["merge_plan"] == 1
    review_result = next(item for item in executed["results"] if item["action"] == "quality_plan")
    merge_result = next(item for item in executed["results"] if item["action"] == "merge_plan")
    assert review_result["apply_result"]["mode"] == "curator_review_apply"
    assert review_result["apply_result"]["patch_result"]["applied"] is True
    assert merge_result["apply_result"]["mode"] == "curator_merge_apply"
    assert merge_result["apply_result"]["archived_skill_ids"] == ["agent-auto-merge-copy"]
    reviewed_text = (workspace_skills / "agent-auto-apply-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Curator Review Notes" in reviewed_text
    assert "verification evidence" in reviewed_text
    primary_text = (workspace_skills / "agent-auto-merge-primary" / "SKILL.md").read_text(encoding="utf-8")
    assert "Curated Merge Notes" in primary_text
    assert "Preserve merged verification evidence" in primary_text
    assert not (workspace_skills / "agent-auto-merge-copy").exists()


def test_skill_curator_calendar_schedules_use_wall_clock_boundaries(
    contract_tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    service = SkillsService()
    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(
                automation_enabled=True,
                schedule="weekly",
                dry_run=True,
            ),
        )
    )
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-calendar",
        title="Agent Calendar",
        summary="Exercise wall-clock curator scheduling.",
        body="Use when curator automation should run on calendar boundaries.",
    )

    saturday = datetime(2026, 5, 9, 9, 30, tzinfo=timezone.utc)
    weekly_status = service.curator.automation_status(config=config, now=saturday)
    assert weekly_status["next_run_at"] == "2026-05-10T00:00:00+00:00"

    not_due = service.curator.run_automation_if_due(config=config, now=saturday)
    assert not_due.ran is False
    assert not_due.reason == "not_due"
    assert not_due.next_run_at == "2026-05-10T00:00:00+00:00"

    sunday = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
    first = service.curator.run_automation_if_due(config=config, now=sunday)
    assert first.ran is True
    assert first.reason == "due"
    assert first.next_run_at == "2026-05-17T00:00:00+00:00"

    daily_config = config.model_copy(
        update={
            "skills_config": SkillsConfig(
                enabled=True,
                governance_root=str(contract_tmp_path / "daily-governance"),
                curator=SkillCuratorConfig(schedule="daily"),
            )
        }
    )
    daily_status = service.curator.automation_status(
        config=daily_config,
        now=datetime(2026, 5, 9, 9, 30, tzinfo=timezone.utc),
    )
    assert daily_status["next_run_at"] == "2026-05-10T00:00:00+00:00"

    hourly_config = config.model_copy(
        update={
            "skills_config": SkillsConfig(
                enabled=True,
                governance_root=str(contract_tmp_path / "hourly-governance"),
                curator=SkillCuratorConfig(schedule="hourly"),
            )
        }
    )
    hourly_status = service.curator.automation_status(
        config=hourly_config,
        now=datetime(2026, 5, 9, 9, 30, tzinfo=timezone.utc),
    )
    assert hourly_status["next_run_at"] == "2026-05-09T10:00:00+00:00"


def test_skill_curator_automation_respects_min_idle_window(contract_tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_skills = contract_tmp_path / "workspace-skills"
    monkeypatch.setattr("anvil.skills.curator.default_installed_skill_root", lambda: workspace_skills)
    monkeypatch.setattr("anvil.skills.service.default_installed_skill_root", lambda: workspace_skills)

    config = EffectiveConfig(
        skills_config=SkillsConfig(
            enabled=True,
            governance_root=str(contract_tmp_path / "governance"),
            curator=SkillCuratorConfig(
                automation_enabled=True,
                interval_seconds=60,
                min_idle_hours=2,
            ),
        )
    )
    service = SkillsService()
    service.manage_curator(
        config=config,
        action="create",
        skill_id="agent-idle",
        title="Agent Idle",
        summary="Exercise idle-aware curator runs.",
        body="Use when curator automation should wait for an idle window.",
    )

    blocked = service.run_curator_automation_if_due(config=config)
    assert blocked.ran is False
    assert blocked.reason == "not_idle"

    usage_path = contract_tmp_path / "governance" / "curator" / "usage.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    usage["agent-idle"]["last_activity_at"] = "2026-01-01T00:00:00+00:00"
    usage_path.write_text(json.dumps(usage), encoding="utf-8")

    due = service.run_curator_automation_if_due(config=config)
    assert due.ran is True
    assert due.reason == "due"
