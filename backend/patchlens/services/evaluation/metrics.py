"""Metric functions for the evaluation harness.

Pure functions with hand-computable definitions. Nothing here reads the database or
invents a value: callers pass predictions and labels, and get numbers back. A metric
that cannot be computed returns ``None`` (reported as ``UNKNOWN``) rather than 0.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

Key = tuple[str, str]


def _top(predictions: Sequence[Key], k: int) -> list[Key]:
    return list(predictions[:k])


def recall_at_k(predictions: Sequence[Key], relevant: set[Key], k: int) -> float | None:
    """Share of relevant items present in the first ``k`` predictions."""
    if not relevant:
        return None
    return len(set(_top(predictions, k)) & relevant) / len(relevant)


def mrr_at_k(predictions: Sequence[Key], relevant: set[Key], k: int) -> float | None:
    """Reciprocal rank of the first relevant prediction, or 0 when none is found."""
    if not relevant:
        return None
    for index, key in enumerate(_top(predictions, k), start=1):
        if key in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(predictions: Sequence[Key], grades: dict[Key, int], k: int) -> float | None:
    """Normalised discounted cumulative gain with exponential gain."""
    if not grades:
        return None
    dcg = 0.0
    for index, key in enumerate(_top(predictions, k), start=1):
        grade = grades.get(key, 0)
        if grade:
            dcg += (2**grade - 1) / math.log2(index + 1)
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2**grade - 1) / math.log2(index + 1) for index, grade in enumerate(ideal, start=1))
    return dcg / idcg if idcg else None


def percentile(values: Iterable[float], fraction: float) -> float | None:
    """Linear-interpolated percentile (the common ``numpy`` definition).

    Returns ``None`` for an empty sample instead of a fabricated 0.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def mean(values: Iterable[float]) -> float | None:
    materialised = list(values)
    return sum(materialised) / len(materialised) if materialised else None


def precision_recall_f1(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    """Counts to precision/recall/F1, with ``None`` for undefined ratios."""
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def aggregate_retrieval(
    case_metrics: Sequence[dict[str, Any]],
    *,
    k_recall: int,
    k_mrr: int,
    k_ndcg: int,
) -> dict[str, Any]:
    """Aggregate per-case metrics for one strategy.

    Metrics are macro-averaged over cases where they are defined. Latency percentiles
    are computed over the same cases, and failures are counted, never dropped.
    """
    latencies = [float(case["latency_ms"]) for case in case_metrics if case.get("latency_ms") is not None]
    failures = [case for case in case_metrics if case.get("error")]

    def average(field: str) -> float | None:
        values = [
            float(case[field])
            for case in case_metrics
            if not case.get("error") and case.get(field) is not None
        ]
        return mean(values)

    return {
        "cases": len(case_metrics),
        "failures": len(failures),
        "failure_rate": (len(failures) / len(case_metrics)) if case_metrics else None,
        f"recall@{k_recall}": average(f"recall@{k_recall}"),
        f"mrr@{k_mrr}": average(f"mrr@{k_mrr}"),
        f"ndcg@{k_ndcg}": average(f"ndcg@{k_ndcg}"),
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
        # No provider-reported usage is available for local retrieval, and PatchLens
        # does not estimate prices. The API renders this as UNKNOWN.
        "cost_usd": None,
    }


def score_category(expected: Sequence[dict[str, Any]], predicted: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Match findings to expected labels and return category + overall counts.

    A pair matches when the category is equal, the path is equal, and the predicted
    line range covers the expected line. ``rule_id`` is reported separately because
    "found the right problem with a different rule" is still a find.
    """
    unused = list(predicted)
    per_category: dict[str, dict[str, int]] = {}
    matched: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []

    def bucket(category: str) -> dict[str, int]:
        return per_category.setdefault(category, {"tp": 0, "fp": 0, "fn": 0})

    for label in expected:
        category = str(label["category"])
        bucket(category)
        hit = None
        for candidate in unused:
            if str(candidate["category"]) != category:
                continue
            if candidate["path"] != label["path"]:
                continue
            if int(candidate["start_line"]) <= int(label["line"]) <= int(candidate["end_line"]):
                hit = candidate
                break
        if hit is None:
            per_category[category]["fn"] += 1
            missed.append(label)
            continue
        unused.remove(hit)
        per_category[category]["tp"] += 1
        matched.append(
            {
                "expected": label,
                "predicted": hit,
                "rule_match": label.get("rule_id") in (None, hit.get("rule_id")),
            }
        )

    for candidate in unused:
        bucket(str(candidate["category"]))["fp"] += 1

    category_metrics: dict[str, dict[str, Any]] = {}
    totals = {"tp": 0, "fp": 0, "fn": 0}
    for category, counts in sorted(per_category.items()):
        metrics = precision_recall_f1(counts["tp"], counts["fp"], counts["fn"])
        category_metrics[category] = {**counts, **metrics}
        for key in totals:
            totals[key] += counts[key]

    return {
        "categories": category_metrics,
        "overall": {**totals, **precision_recall_f1(totals["tp"], totals["fp"], totals["fn"])},
        "matched": matched,
        "missed": missed,
        "unexpected": list(unused),
        "rule_match_rate": (
            sum(1 for pair in matched if pair["rule_match"]) / len(matched) if matched else None
        ),
    }


__all__ = [
    "aggregate_retrieval",
    "mean",
    "mrr_at_k",
    "ndcg_at_k",
    "percentile",
    "precision_recall_f1",
    "recall_at_k",
    "score_category",
]
