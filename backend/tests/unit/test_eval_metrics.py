"""Metric definitions, tested against hand-computed values.

The point of these tests is that a wrong metric cannot hide behind a plausible
dashboard: each function is checked with numbers that can be verified by hand.
"""

from __future__ import annotations

import pytest

from patchlens.services.evaluation.metrics import (
    aggregate_retrieval,
    mrr_at_k,
    ndcg_at_k,
    percentile,
    precision_recall_f1,
    recall_at_k,
    score_category,
)

A = ("pkg/mod.py", "fn_a")
B = ("pkg/mod.py", "fn_b")
C = ("pkg/mod.py", "fn_c")
D = ("pkg/mod.py", "fn_d")
E = ("pkg/mod.py", "fn_e")
F = ("pkg/mod.py", "fn_f")


def test_recall_at_five_counts_only_the_top_five() -> None:
    predictions = [A, B, C, D, E, F]
    assert recall_at_k(predictions, {A, F}, 5) == pytest.approx(0.5)
    assert recall_at_k(predictions, {E, F}, 5) == pytest.approx(0.5)
    assert recall_at_k(predictions, {F}, 6) == 1.0


def test_recall_is_unknown_without_labels() -> None:
    assert recall_at_k([A], set(), 5) is None


def test_mrr_uses_the_first_relevant_rank() -> None:
    assert mrr_at_k([A, B, C], {A}, 10) == 1.0
    assert mrr_at_k([A, B, C], {C}, 10) == pytest.approx(1 / 3)
    assert mrr_at_k([A, B], {C}, 10) == 0.0, "a miss inside the cut-off is zero, not unknown"


def test_ndcg_matches_a_hand_computed_example() -> None:
    grades = {A: 3, B: 2}
    # Predictions put the grade-2 item first.
    dcg = (2**2 - 1) / 1.0 + (2**3 - 1) / 1.5849625007211563
    idcg = (2**3 - 1) / 1.0 + (2**2 - 1) / 1.5849625007211563
    assert ndcg_at_k([B, A], grades, 10) == pytest.approx(dcg / idcg)
    assert ndcg_at_k([A, B], grades, 10) == pytest.approx(1.0)
    assert ndcg_at_k([], grades, 10) == 0.0


def test_ndcg_is_unknown_without_grades() -> None:
    assert ndcg_at_k([A], {}, 10) is None


def test_percentiles_use_linear_interpolation_and_reject_empty_samples() -> None:
    assert percentile([], 0.5) is None
    assert percentile([7.0], 0.95) == 7.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)
    with pytest.raises(ValueError):
        percentile([1.0], 1.5)


def test_precision_recall_f1_reports_undefined_ratios_as_none() -> None:
    metrics = precision_recall_f1(tp=2, fp=1, fn=2)
    assert metrics["precision"] == pytest.approx(2 / 3)
    assert metrics["recall"] == pytest.approx(0.5)
    assert metrics["f1"] == pytest.approx(2 * (2 / 3) * 0.5 / ((2 / 3) + 0.5))
    assert precision_recall_f1(0, 0, 0) == {"precision": None, "recall": None, "f1": None}
    # No predictions: precision is undefined rather than 1.0, recall is genuinely 0.
    empty = precision_recall_f1(0, 0, 3)
    assert empty["precision"] is None
    assert empty["recall"] == 0.0


def test_aggregate_retrieval_keeps_failures_in_the_denominator() -> None:
    cases = [
        {"recall@5": 1.0, "mrr@10": 1.0, "ndcg@10": 1.0, "latency_ms": 1.0, "error": None},
        {"recall@5": 0.0, "mrr@10": 0.0, "ndcg@10": 0.0, "latency_ms": 3.0, "error": None},
        {"recall@5": None, "mrr@10": None, "ndcg@10": None, "latency_ms": 2.0, "error": "boom"},
    ]
    metrics = aggregate_retrieval(cases, k_recall=5, k_mrr=10, k_ndcg=10)
    assert metrics["cases"] == 3
    assert metrics["failures"] == 1
    assert metrics["failure_rate"] == pytest.approx(1 / 3)
    # Averages exclude the failed case rather than silently counting it as zero.
    assert metrics["recall@5"] == pytest.approx(0.5)
    assert metrics["mrr@10"] == pytest.approx(0.5)
    assert metrics["latency_ms"]["p50"] == pytest.approx(2.0)
    assert metrics["cost_usd"] is None, "cost is unknown, never an estimate"


def test_aggregate_retrieval_with_no_cases_does_not_invent_numbers() -> None:
    metrics = aggregate_retrieval([], k_recall=5, k_mrr=10, k_ndcg=10)
    assert metrics["cases"] == 0
    assert metrics["failure_rate"] is None
    assert metrics["recall@5"] is None
    assert metrics["latency_ms"] == {"p50": None, "p95": None}


def test_score_category_matches_on_path_and_line_range() -> None:
    expected = [
        {"category": "correctness", "path": "pkg/mod.py", "line": 10, "rule_id": "bare-except"},
        {"category": "security", "path": "pkg/mod.py", "line": 20, "rule_id": "eval-exec"},
    ]
    predicted = [
        # A range is allowed: reviewers may anchor a block at its first line.
        {
            "category": "correctness",
            "path": "pkg/mod.py",
            "start_line": 8,
            "end_line": 12,
            "rule_id": "bare-except",
        },
        # Same category and line, but the wrong path: not a match.
        {
            "category": "security",
            "path": "pkg/other.py",
            "start_line": 20,
            "end_line": 20,
            "rule_id": "eval-exec",
        },
        # Unlabelled finding: a false positive in the maintainability bucket.
        {
            "category": "maintainability",
            "path": "pkg/mod.py",
            "start_line": 30,
            "end_line": 30,
            "rule_id": "unused",
        },
        # Right problem, different rule: a match, but rule_match is false.
        {
            "category": "security",
            "path": "pkg/mod.py",
            "start_line": 20,
            "end_line": 21,
            "rule_id": "sql-injection",
        },
    ]
    scored = score_category(expected, predicted)

    assert scored["categories"]["correctness"] == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    security = scored["categories"]["security"]
    assert security["tp"] == 1
    assert security["fn"] == 0
    assert security["fp"] == 1, "the wrong-path finding is a false positive, not a match"
    maintainability = scored["categories"]["maintainability"]
    assert maintainability["fp"] == 1
    assert maintainability["tp"] == 0
    assert scored["overall"]["tp"] == 2
    assert scored["overall"]["fp"] == 2
    assert scored["rule_match_rate"] == pytest.approx(0.5)
    assert len(scored["unexpected"]) == 2


def test_score_category_counts_a_missed_label_as_a_false_negative() -> None:
    expected = [{"category": "correctness", "path": "pkg/mod.py", "line": 5, "rule_id": None}]
    scored = score_category(expected, [])
    assert scored["categories"]["correctness"]["fn"] == 1
    assert scored["missed"] == expected
    assert scored["overall"]["recall"] == 0.0
    assert scored["rule_match_rate"] is None, "no matches means the rate is undefined, not 0"
