"""Reciprocal Rank Fusion and deduplication helpers.

RRF is used to merge ranked lists because it needs no score calibration: lexical
scores (unbounded) and cosine similarities (bounded) are not comparable, but their
*ranks* are. The formula, with configurable weights:

``fused(d) = sum over retrievers r of w_r / (k + rank_r(d))``   with ``k = 60``

``k`` damps the influence of top ranks, which is the value recommended in the
original RRF work (Cormack et al., 2009). Weights default to 1.0 for BM25 and
semantic; the weights actually used are recorded with every retrieval result so a
comparison stays interpretable after the fact.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

DEFAULT_RRF_K = 60


@dataclass(slots=True)
class FusedItem:
    key: tuple[str, str]
    fused_score: float
    components: dict[str, float] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)


def reciprocal_rank_fusion(
    ranked_lists: Mapping[str, list[tuple[str, str]]],
    *,
    weights: Mapping[str, float] | None = None,
    k: int = DEFAULT_RRF_K,
) -> list[FusedItem]:
    """Fuse ranked lists.

    ``ranked_lists`` maps a retriever name to its ranked keys, best first. Returns
    items sorted by descending fused score, keeping each retriever's reciprocal
    rank contribution for explainability.
    """
    if k < 1:
        raise ValueError("RRF k must be >= 1")
    weights = weights or {}
    fused: dict[tuple[str, str], FusedItem] = {}

    for retriever, ranking in ranked_lists.items():
        weight = weights.get(retriever, 1.0)
        if weight <= 0:
            continue
        for position, key in enumerate(ranking, start=1):
            contribution = weight / (k + position)
            item = fused.get(key)
            if item is None:
                item = FusedItem(key=key, fused_score=0.0)
                fused[key] = item
            item.fused_score += contribution
            item.components[retriever] = contribution
            item.ranks[retriever] = position

    return sorted(fused.values(), key=lambda item: (-item.fused_score, item.key))


def deduplicate_keys(keys: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Stable de-duplication while preserving order."""
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def cap_per_file(
    items: list[FusedItem],
    *,
    max_per_file: int,
    limit: int | None = None,
) -> list[FusedItem]:
    """Keep at most ``max_per_file`` hits per path.

    Stops one widely-referenced module from crowding out every other piece of
    evidence in a prompt.
    """
    counts: defaultdict[str, int] = defaultdict(int)
    kept: list[FusedItem] = []
    for item in items:
        path = item.key[0]
        if counts[path] >= max_per_file:
            continue
        counts[path] += 1
        kept.append(item)
        if limit is not None and len(kept) >= limit:
            break
    return kept


__all__ = ["DEFAULT_RRF_K", "FusedItem", "cap_per_file", "deduplicate_keys", "reciprocal_rank_fusion"]
