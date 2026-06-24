from __future__ import annotations

from anvil.agents.features import RuntimeFeatureSet
from anvil.agents.lead_agent.prompt import (
    build_prompt_snapshot,
    build_runtime_path_context,
    build_turn_injection_view,
    compose_system_prompt,
    reset_runtime_path_context_cache,
    runtime_path_context_cache_stats,
)
from anvil.runtime.tool_registry import CapabilityBundle, ToolRegistryEntry, ToolSourceKind
from anvil.sandbox import PathBridge, PathService


def make_bundle(
    *,
    prompt_safe_summaries: tuple[str, ...] = ("read_file: Read File [visible]",),
    deferred_tools: tuple[ToolRegistryEntry, ...] = (),
) -> CapabilityBundle:
    return CapabilityBundle(
        fingerprint="bundle-1",
        visible_tools=(),
        deferred_tools=deferred_tools,
        enabled_skill_ids=("skill-a",),
        prompt_safe_summaries=prompt_safe_summaries,
    )


def test_prompt_snapshot_has_stable_section_order() -> None:
    snapshot = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )

    assert [section.name for section in snapshot.stable_sections] == [
        "role_and_intent",
        "operating_principles",
        "workflow_rules",
        "environment_contract",
        "path_contract",
        "capability_summary",
        "deferred_capabilities",
        "delegation_rules",
        "response_contract",
    ]


def test_turn_injection_view_exposes_sections_without_parsing_rendered_prompt() -> None:
    view = build_turn_injection_view(
        request_context="turn-local request",
        upload_context="upload summary",
        approval_context="approval summary",
        promoted_capabilities=("browser_open",),
    )

    sections = view.sections()

    assert [section.name for section in sections] == [
        "request_context",
        "upload_context",
        "approval_context",
        "promoted_capabilities",
    ]
    assert "turn-local request" in view.render()


def test_prompt_injection_memory_context_is_compatibility_field_not_rendered_directly() -> None:
    snapshot = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    view = build_turn_injection_view(
        request_context="turn-local request",
        memory_context="<memory_context>\nLEGACY_DIRECT_MEMORY_SENTINEL\n</memory_context>",
        promoted_capabilities=("grep_files",),
    )

    section_names = [section.name for section in view.sections()]
    rendered = view.render()
    prompt = compose_system_prompt(snapshot, view)

    assert view.memory_context == "<memory_context>\nLEGACY_DIRECT_MEMORY_SENTINEL\n</memory_context>"
    assert section_names == ["request_context", "promoted_capabilities"]
    assert "LEGACY_DIRECT_MEMORY_SENTINEL" not in rendered
    assert "LEGACY_DIRECT_MEMORY_SENTINEL" not in prompt
    assert "<memory_context>" not in prompt
    assert "turn-local request" in prompt
    assert "grep_files" in prompt


def test_prompt_snapshot_places_project_context_before_memory_when_present() -> None:
    snapshot = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        project_context="Project rule: run focused tests.",
        project_context_fingerprint="ctx-1",
        memory_snapshot="Remember stable preference.",
        memory_snapshot_fingerprint="mem-1",
    )

    assert [section.name for section in snapshot.stable_sections] == [
        "role_and_intent",
        "operating_principles",
        "workflow_rules",
        "environment_contract",
        "path_contract",
        "project_context_files",
        "memory_snapshot",
        "capability_summary",
        "deferred_capabilities",
        "delegation_rules",
        "response_contract",
    ]


def test_turn_local_injections_do_not_change_stable_snapshot() -> None:
    snapshot_one = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    snapshot_two = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )

    injected = build_turn_injection_view(
        request_context="user asked for a summary",
        upload_context="an upload is available",
        approval_context="approval is pending",
        promoted_capabilities=("write_file",),
    )
    prompt = compose_system_prompt(snapshot_two, injected)

    assert snapshot_one.snapshot_id == snapshot_two.snapshot_id
    assert "request_context" in prompt
    assert "upload_context" in prompt
    assert "approval_context" in prompt
    assert "promoted_capabilities" in prompt


def test_prompt_snapshot_invalidates_when_config_fingerprint_changes() -> None:
    baseline = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    changed = build_prompt_snapshot(
        config_fingerprint="cfg-2",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )

    assert baseline.snapshot_id != changed.snapshot_id


def test_prompt_snapshot_invalidates_when_policy_or_memory_namespace_changes() -> None:
    baseline = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        policy_version="v1",
        memory_namespace="memory-a",
    )
    policy_changed = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        policy_version="v2",
        memory_namespace="memory-a",
    )
    memory_changed = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        policy_version="v1",
        memory_namespace="memory-b",
    )

    assert baseline.snapshot_id != policy_changed.snapshot_id
    assert baseline.snapshot_id != memory_changed.snapshot_id


def test_prompt_snapshot_includes_project_context_files_when_available() -> None:
    snapshot = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        project_context="Project rule: run focused tests.",
        project_context_fingerprint="ctx-1",
    )
    sections = {section.name: section.content for section in snapshot.stable_sections}

    assert "project_context_files" in sections
    assert sections["project_context_files"] == "Project rule: run focused tests."


def test_prompt_snapshot_invalidates_when_project_context_changes() -> None:
    baseline = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        project_context="Project rule: run focused tests.",
        project_context_fingerprint="ctx-1",
    )
    changed = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        project_context="Project rule: run full tests.",
        project_context_fingerprint="ctx-2",
    )

    assert baseline.snapshot_id != changed.snapshot_id


def test_prompt_snapshot_invalidates_when_capability_summary_inputs_change() -> None:
    baseline = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(
            prompt_safe_summaries=("read_file: Read File [visible]",),
        ),
        feature_set=RuntimeFeatureSet(),
    )
    changed = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(
            prompt_safe_summaries=("list_dir: List Directory [visible]",),
        ),
        feature_set=RuntimeFeatureSet(),
    )

    assert baseline.snapshot_id != changed.snapshot_id


def test_prompt_snapshot_uses_prompt_safe_summaries_and_deferred_tool_names() -> None:
    snapshot = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(
            prompt_safe_summaries=("registry-owned summary",),
            deferred_tools=(
                ToolRegistryEntry(
                    name="search_skill",
                    display_name="Search Skill",
                    source_kind=ToolSourceKind.SKILL,
                    source_id="skills",
                    capability_group="research",
                    deferred=True,
                ),
            ),
        ),
        feature_set=RuntimeFeatureSet(),
    )
    sections = {section.name: section.content for section in snapshot.stable_sections}

    assert sections["capability_summary"] == "registry-owned summary"
    assert sections["deferred_capabilities"].startswith("- search_skill")
    assert "capability_search" in sections["deferred_capabilities"]


def test_prompt_snapshot_includes_operational_rules_for_clarification_and_discovery() -> None:
    snapshot = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(
            deferred_tools=(
                ToolRegistryEntry(
                    name="ext_search",
                    display_name="External Search",
                    source_kind=ToolSourceKind.MCP,
                    source_id="github",
                    capability_group="research",
                    deferred=True,
                ),
            )
        ),
        feature_set=RuntimeFeatureSet(),
    )
    sections = {section.name: section.content for section in snapshot.stable_sections}

    assert "Clarify before irreversible work" in sections["workflow_rules"]
    assert "search names/content with search_files or the thin glob_files/grep_files aliases" in sections["workflow_rules"]
    assert "code_symbols for one-file outlines" in sections["workflow_rules"]
    assert "code_definition for implementations" in sections["workflow_rules"]
    assert "code_references for bounded usages" in sections["workflow_rules"]
    assert "code_impact before editing shared/public code" in sections["workflow_rules"]
    assert "code_map only when a compact project index is needed" in sections["workflow_rules"]
    assert "do not request all coding-analysis surfaces when one will do" in sections["workflow_rules"]
    assert "prefer patch_file for focused edits" in sections["workflow_rules"]
    assert "toolset_catalog/toolset_view" in sections["workflow_rules"]
    assert "Do not call legacy external skill-download tools that target third-party skill directories" in sections["workflow_rules"]
    assert "Large external tool catalogs may be task-filtered" in sections["workflow_rules"]
    assert "capability_search" in sections["deferred_capabilities"]
    assert "Do not infer host paths" in sections["path_contract"]
    assert "/mnt/user-data/workspace" in sections["path_contract"]
    assert "list_dir may start at /mnt/user-data" in sections["path_contract"]
    assert "search_files, glob_files, and grep_files must target /mnt/user-data" in sections["path_contract"]
    assert "Do not use '.', '/', or unlisted host paths" in sections["path_contract"]
    assert "patch_file only edits existing UTF-8 text files" in sections["path_contract"]


def test_prompt_snapshot_includes_runtime_path_roots_and_fingerprint() -> None:
    snapshot = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        runtime_path_context="- /mnt/user-data/workspace/_host/e_drive: configured host path bridge 'e_drive' -> E:/work; writable=True",
        runtime_path_fingerprint="paths-1",
    )
    sections = {section.name: section.content for section in snapshot.stable_sections}

    assert "runtime_path_roots" in sections
    assert "/mnt/user-data/workspace/_host/e_drive" in sections["runtime_path_roots"]
    assert snapshot.snapshot_key.runtime_path_fingerprint == "paths-1"


def test_runtime_path_context_cache_reuses_unchanged_roots(contract_tmp_path) -> None:
    reset_runtime_path_context_cache(max_entries=8)
    path_service = PathService(
        contract_tmp_path / "threads",
        path_bridges=[
            PathBridge.create(
                alias="e_drive",
                display_root="E:/work",
                actual_root=str(contract_tmp_path / "external"),
            )
        ],
    )

    first = build_runtime_path_context(path_service=path_service, thread_id="thread-path-cache")
    second = build_runtime_path_context(path_service=path_service, thread_id="thread-path-cache")
    stats = runtime_path_context_cache_stats()

    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert second.fingerprint == first.fingerprint
    assert "/mnt/user-data/workspace/_host/e_drive" in second.rendered
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.writes == 1


def test_runtime_path_context_cache_invalidates_when_roots_change(contract_tmp_path) -> None:
    reset_runtime_path_context_cache(max_entries=8)
    first_service = PathService(contract_tmp_path / "threads")
    bridge_root = contract_tmp_path / "bridge"
    second_service = PathService(
        contract_tmp_path / "threads",
        path_bridges=[
            PathBridge.create(
                alias="project",
                display_root="E:/project",
                actual_root=str(bridge_root),
            )
        ],
    )

    first = build_runtime_path_context(path_service=first_service, thread_id="thread-path-change")
    second = build_runtime_path_context(path_service=second_service, thread_id="thread-path-change")
    stats = runtime_path_context_cache_stats()

    assert first.cache_status == "miss"
    assert second.cache_status == "miss"
    assert first.fingerprint != second.fingerprint
    assert second.host_bridge_count == 1
    assert stats.hits == 0
    assert stats.misses == 2
    assert stats.writes == 2


def test_prompt_snapshot_does_not_expose_tool_authoring_rules() -> None:
    snapshot = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(
            deferred_tools=(
                ToolRegistryEntry(
                    name="PPT-document",
                    display_name="PPT Document",
                    source_kind=ToolSourceKind.PLUGIN,
                    source_id="office-pack",
                    capability_group="document_generation",
                    deferred=True,
                ),
            )
        ),
        feature_set=RuntimeFeatureSet(),
    )
    sections = {section.name: section.content for section in snapshot.stable_sections}

    assert "tool_authoring" not in sections["deferred_capabilities"]
    assert "cross-cutting" not in sections["deferred_capabilities"]
    assert "include_rules" not in sections["deferred_capabilities"]
    assert "Use tool_catalog" in sections["deferred_capabilities"]
    assert "Do not create new tools or MCP servers" in sections["deferred_capabilities"]
