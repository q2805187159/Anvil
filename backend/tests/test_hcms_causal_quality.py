from __future__ import annotations

from anvil.memory import (
    CausalEdge,
    CausalType,
    DebouncedMemoryQueue,
    FileMemoryStore,
    HeuristicMemoryUpdater,
    MemoryService,
    stable_id,
    utc_now,
)


def make_hcms(contract_tmp_path):
    return MemoryService(
        store=FileMemoryStore(contract_tmp_path / "hcms-store"),
        queue=DebouncedMemoryQueue(min_window_seconds=5, default_window_seconds=30, max_window_seconds=60),
        updater=HeuristicMemoryUpdater(max_facts=20),
        max_facts=20,
        injection_token_budget=400,
    )


def test_hcms_why_prefers_multihop_causal_path_when_edges_exist(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    cause = service.create_memory(
        "global/default",
        content="Northstar release failures happened after direct rollout.",
        category="project_context",
        confidence=0.91,
        salience=0.82,
    )
    intermediate = service.create_memory(
        "global/default",
        content="Northstar canary verification was introduced to reduce direct rollout risk.",
        category="decision",
        confidence=0.93,
        salience=0.88,
    )
    effect = service.create_memory(
        "global/default",
        content="Northstar smoke validation now blocks repeat release failures.",
        category="project_context",
        confidence=0.94,
        salience=0.9,
    )
    state = service.prefetch("global/default")
    state.causal_edges = [
        CausalEdge(
            edge_id=stable_id("edge", cause.memory_id, intermediate.memory_id),
            source_event=cause.memory_id,
            target_event=intermediate.memory_id,
            causal_type=CausalType.DIRECT_CAUSE,
            strength=0.82,
            evidence=["northstar-direct-rollout"],
            timestamp=utc_now(),
        ),
        CausalEdge(
            edge_id=stable_id("edge", intermediate.memory_id, effect.memory_id),
            source_event=intermediate.memory_id,
            target_event=effect.memory_id,
            causal_type=CausalType.CONTRIBUTORY,
            strength=0.8,
            evidence=["northstar-canary-smoke"],
            timestamp=utc_now(),
        ),
    ]
    service.store.save("global/default", state)

    paths = service.why("global/default", "why did Northstar smoke validation block repeat release failures", limit=3)

    assert paths
    multihop = next((path for path in paths if len(path.edges) >= 2), None)
    assert multihop is not None
    assert multihop.explanation_kind == "causal"
    assert multihop.degradation_reason is None
    assert [node.memory_id for node in multihop.nodes] == [cause.memory_id, intermediate.memory_id, effect.memory_id]


def test_hcms_why_degrades_to_correlation_without_causal_edges(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    service.create_memory(
        "global/default",
        content="Northstar canary verification is related to release safety checks.",
        category="project_context",
        confidence=0.88,
        salience=0.84,
        evidence_text="Canary verification was discussed as related release-safety evidence.",
    )

    paths = service.why("global/default", "why is Northstar related to release safety", limit=1)

    assert paths
    assert paths[0].edges == []
    assert paths[0].explanation_kind == "correlation"
    assert paths[0].degradation_reason == "no_causal_path_found"
    assert paths[0].evidence_summary


def test_hcms_why_marks_low_confidence_degradation(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    service.create_memory(
        "global/default",
        content="Northstar rollback ownership might belong to the release desk.",
        category="project_context",
        confidence=0.32,
        salience=0.75,
        evidence_text="Tentative note without corroborating evidence.",
    )

    paths = service.why("global/default", "why does Northstar rollback ownership belong to release desk", limit=1)

    assert paths
    assert paths[0].edges == []
    assert paths[0].explanation_kind == "degraded"
    assert paths[0].degradation_reason == "low_confidence_evidence"
    assert paths[0].confidence <= 0.5
    assert paths[0].evidence_summary


def test_hcms_why_marks_conflicting_evidence_degradation(contract_tmp_path) -> None:
    service = make_hcms(contract_tmp_path)
    service.create_memory(
        "global/default",
        content="Northstar release note conflicts with the earlier claim and must not be treated as causal truth.",
        category="project_context",
        confidence=0.79,
        salience=0.77,
        evidence_text="Conflicting release note contradicts the previous rollout account.",
    )

    paths = service.why("global/default", "why should Northstar release note be treated as causal truth", limit=1)

    assert paths
    assert paths[0].edges == []
    assert paths[0].explanation_kind == "degraded"
    assert paths[0].degradation_reason == "conflicting_evidence"
    assert paths[0].evidence_summary
