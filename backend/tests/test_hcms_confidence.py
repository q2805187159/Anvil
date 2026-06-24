from __future__ import annotations

import math

from anvil.memory import bayesian_update


def test_hcms_confidence_strong_supporting_evidence_increases_prior() -> None:
    updated = bayesian_update(0.55, 0.9, evidence_weight=0.8)

    assert updated > 0.55
    assert 0.0 <= updated <= 1.0


def test_hcms_confidence_weak_supporting_evidence_has_limited_uplift() -> None:
    updated = bayesian_update(0.55, 0.56, evidence_weight=0.2)

    assert updated >= 0.55
    assert updated - 0.55 < 0.01


def test_hcms_confidence_contradictory_evidence_decreases_prior() -> None:
    updated = bayesian_update(0.75, 0.2, evidence_weight=0.7)

    assert updated < 0.75
    assert 0.0 <= updated <= 1.0


def test_hcms_confidence_bounds_invalid_inputs_without_nan() -> None:
    for value in (
        bayesian_update(float("nan"), 0.9, evidence_weight=0.8),
        bayesian_update(0.7, float("nan"), evidence_weight=0.8),
        bayesian_update(2.0, -1.0, evidence_weight=3.0),
    ):
        assert not math.isnan(value)
        assert 0.0 <= value <= 1.0
