from __future__ import annotations

from anvil.agents.features import RuntimeFeatureSet
from anvil.agents.lead_agent.prompt import (
    build_prompt_snapshot,
    build_turn_injection_view,
    compose_system_prompt,
    prompt_snapshot_cache_stats,
    reset_prompt_snapshot_cache,
)
from anvil.runtime.tool_registry import CapabilityBundle


def make_bundle(
    *,
    fingerprint: str = "bundle-a",
    prompt_safe_summaries: tuple[str, ...] = ("read_file: Read File [visible]",),
) -> CapabilityBundle:
    return CapabilityBundle(
        fingerprint=fingerprint,
        visible_tools=(),
        deferred_tools=(),
        enabled_skill_ids=("skill-a",),
        prompt_safe_summaries=prompt_safe_summaries,
    )


def test_turn_local_injections_do_not_change_stable_snapshot_id() -> None:
    baseline = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        policy_version="v1",
        memory_namespace="global/default",
    )
    unchanged = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        policy_version="v1",
        memory_namespace="global/default",
    )

    prompt = compose_system_prompt(
        unchanged,
        build_turn_injection_view(
            request_context="request-local hint",
            upload_context="uploaded file is available",
            approval_context="approval shown in shell",
            promoted_capabilities=("ext_search",),
        ),
    )

    assert baseline.snapshot_id == unchanged.snapshot_id
    assert "request_context" in prompt
    assert "upload_context" in prompt
    assert "approval_context" in prompt
    assert "promoted_capabilities" in prompt


def test_prompt_snapshot_cache_reuses_stable_snapshot_and_records_hit() -> None:
    reset_prompt_snapshot_cache(max_entries=8)

    baseline = build_prompt_snapshot(
        config_fingerprint="cfg-cache-hit",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    reused = build_prompt_snapshot(
        config_fingerprint="cfg-cache-hit",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    stats = prompt_snapshot_cache_stats()

    assert reused is baseline
    assert stats.size == 1
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.evictions == 0


def test_prompt_snapshot_cache_is_bounded_lru() -> None:
    reset_prompt_snapshot_cache(max_entries=2)

    first = build_prompt_snapshot(
        config_fingerprint="cfg-lru-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    second = build_prompt_snapshot(
        config_fingerprint="cfg-lru-2",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    assert build_prompt_snapshot(
        config_fingerprint="cfg-lru-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    ) is first

    third = build_prompt_snapshot(
        config_fingerprint="cfg-lru-3",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    rebuilt_second = build_prompt_snapshot(
        config_fingerprint="cfg-lru-2",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    stats = prompt_snapshot_cache_stats()

    assert third is not first
    assert rebuilt_second is not second
    assert stats.size == 2
    assert stats.evictions >= 2


def test_prompt_snapshot_cache_can_be_bypassed_per_feature_set() -> None:
    reset_prompt_snapshot_cache(max_entries=8)

    first = build_prompt_snapshot(
        config_fingerprint="cfg-no-cache",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(stable_prompt_cache=False),
    )
    second = build_prompt_snapshot(
        config_fingerprint="cfg-no-cache",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(stable_prompt_cache=False),
    )
    stats = prompt_snapshot_cache_stats()

    assert first is not second
    assert stats.size == 0
    assert stats.bypasses == 2


def test_prompt_snapshot_derives_fingerprint_for_stable_content_without_explicit_fingerprint() -> None:
    reset_prompt_snapshot_cache(max_entries=8)

    baseline = build_prompt_snapshot(
        config_fingerprint="cfg-derived-fingerprint",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        memory_snapshot="Remember stable preference A.",
    )
    changed = build_prompt_snapshot(
        config_fingerprint="cfg-derived-fingerprint",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
        memory_snapshot="Remember stable preference B.",
    )
    memory_sections = {section.name: section.content for section in changed.stable_sections}

    assert baseline.snapshot_id != changed.snapshot_id
    assert "Remember stable preference B." in memory_sections["memory_snapshot"]
    assert "Remember stable preference A." not in memory_sections["memory_snapshot"]


def test_snapshot_invalidates_when_capability_bundle_fingerprint_changes() -> None:
    baseline = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(fingerprint="bundle-a"),
        feature_set=RuntimeFeatureSet(),
    )
    changed = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(fingerprint="bundle-b"),
        feature_set=RuntimeFeatureSet(),
    )

    assert baseline.snapshot_id != changed.snapshot_id


def test_snapshot_ignores_local_display_state_not_in_runtime_inputs() -> None:
    baseline = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )
    # Shell/frontend local display state is intentionally outside the snapshot inputs.
    shell_display_compact = False
    frontend_sidebar_open = True

    unchanged = build_prompt_snapshot(
        config_fingerprint="cfg-1",
        capability_bundle=make_bundle(),
        feature_set=RuntimeFeatureSet(),
    )

    assert baseline.snapshot_id == unchanged.snapshot_id
    assert shell_display_compact is False
    assert frontend_sidebar_open is True
