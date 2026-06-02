from __future__ import annotations

from collections.abc import Callable, Sequence

from .contracts import (
    MemoryRecallBenchmarkCase,
    MemoryRecallBenchmarkCaseResult,
    MemoryRecallBenchmarkReport,
    RecallResult,
    utc_now,
)


def run_recall_benchmark(
    *,
    suite_id: str = "ad_hoc",
    cases: Sequence[MemoryRecallBenchmarkCase],
    recall: Callable[[str, str], RecallResult],
    evidence_limit: int = 5,
) -> MemoryRecallBenchmarkReport:
    normalized_cases = tuple(cases)
    results = tuple(
        _score_case(case, recall=recall, evidence_limit=evidence_limit)
        for case in normalized_cases
    )
    case_count = len(results)
    passed_count = sum(1 for item in results if item.passed)
    total_expected = sum(item.expected_count for item in results)
    total_hits = sum(item.recall_hits for item in results)
    total_false_positives = sum(item.false_positive_count for item in results)
    total_evidence = sum(item.evidence_count for item in results)
    recall_hit_rate = _ratio(total_hits, total_expected, default=1.0)
    false_positive_rate = _ratio(total_false_positives, total_evidence, default=0.0)
    score = _round_score((recall_hit_rate * 0.8) + ((1.0 - false_positive_rate) * 0.2))
    recommendations = _recommendations(
        results=results,
        recall_hit_rate=recall_hit_rate,
        false_positive_rate=false_positive_rate,
    )
    return MemoryRecallBenchmarkReport(
        suite_id=suite_id.strip()[:120] or "ad_hoc",
        passed=case_count == 0 or passed_count == case_count,
        score=score,
        case_count=case_count,
        passed_count=passed_count,
        failed_count=case_count - passed_count,
        recall_hit_rate=_round_score(recall_hit_rate),
        false_positive_rate=_round_score(false_positive_rate),
        average_evidence_count=_round_score(_ratio(total_evidence, case_count, default=0.0)),
        cases=results,
        recommendations=recommendations,
        generated_at=utc_now(),
    )


def _score_case(
    case: MemoryRecallBenchmarkCase,
    *,
    recall: Callable[[str, str], RecallResult],
    evidence_limit: int,
) -> MemoryRecallBenchmarkCaseResult:
    result = recall(case.thread_id, case.query)
    evidence = tuple(result.evidence)
    top_evidence = evidence[: max(1, min(evidence_limit, 20))]
    evidence_texts = tuple(
        " ".join(
            str(part or "")
            for part in (
                item.evidence_id,
                item.source_kind,
                item.source_id,
                item.memory_id,
                item.archive_id,
                item.thread_id,
                item.reason,
                item.excerpt,
            )
        )
        for item in evidence
    )

    expected_checks: list[tuple[str, bool]] = []
    expected_checks.extend(
        (f"term:{term}", _contains_any(evidence_texts, term))
        for term in _safe_strings(case.expected_terms)
    )
    expected_checks.extend(
        (f"memory:{memory_id}", any(item.memory_id == memory_id for item in evidence))
        for memory_id in _safe_strings(case.expected_memory_ids)
    )
    expected_checks.extend(
        (f"thread:{thread_id}", any(item.thread_id == thread_id for item in evidence))
        for thread_id in _safe_strings(case.expected_archive_thread_ids)
    )
    forbidden_checks: list[tuple[str, bool]] = []
    forbidden_checks.extend(
        (f"term:{term}", _contains_any(evidence_texts, term))
        for term in _safe_strings(case.forbidden_terms)
    )
    forbidden_checks.extend(
        (f"memory:{memory_id}", any(item.memory_id == memory_id for item in evidence))
        for memory_id in _safe_strings(case.forbidden_memory_ids)
    )

    expected_count = len(expected_checks)
    recall_hits = sum(1 for _, passed in expected_checks if passed)
    false_positives = tuple(label for label, found in forbidden_checks if found)
    missing = tuple(label for label, passed in expected_checks if not passed)
    expected_score = _ratio(recall_hits, expected_count, default=1.0)
    false_positive_penalty = _ratio(len(false_positives), max(len(forbidden_checks), 1), default=0.0)
    score = _round_score(max(0.0, expected_score - false_positive_penalty))
    passed = score >= min(max(case.min_score, 0.0), 1.0) and not false_positives and not missing
    return MemoryRecallBenchmarkCaseResult(
        case_id=case.case_id.strip()[:120] or "case",
        query=case.query,
        passed=passed,
        score=score,
        recall_hits=recall_hits,
        expected_count=expected_count,
        false_positive_count=len(false_positives),
        evidence_count=len(evidence),
        top_evidence=top_evidence,
        missing_expectations=missing,
        false_positives=false_positives,
        summary=result.summary,
    )


def _contains_any(values: Sequence[str], needle: str) -> bool:
    normalized = needle.strip().lower()
    if not normalized:
        return False
    return any(normalized in value.lower() for value in values)


def _safe_strings(values: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value).strip() for value in values if str(value or "").strip())


def _ratio(numerator: int | float, denominator: int | float, *, default: float) -> float:
    if denominator <= 0:
        return default
    return float(numerator) / float(denominator)


def _round_score(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 4)


def _recommendations(
    *,
    results: Sequence[MemoryRecallBenchmarkCaseResult],
    recall_hit_rate: float,
    false_positive_rate: float,
) -> tuple[str, ...]:
    recommendations: list[str] = []
    failed = [item for item in results if not item.passed]
    if failed:
        recommendations.append("Review failed benchmark cases and add stronger evidence-backed memories or archive indexes.")
    if recall_hit_rate < 0.8:
        recommendations.append("Recall hit rate is low; inspect query wording, memory salience, and retrieval index freshness.")
    if false_positive_rate > 0.1:
        recommendations.append("False positives are elevated; archive stale or conflicting entries and tighten memory categories.")
    if any(item.evidence_count == 0 for item in results):
        recommendations.append("Some cases returned no evidence; verify the memory write path and archive indexing.")
    return tuple(dict.fromkeys(recommendations))
