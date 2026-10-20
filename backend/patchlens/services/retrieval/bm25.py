"""BM25 (Okapi) lexical ranking.

Implemented directly rather than pulled from a library so the exact scoring
formula is inspectable and testable:

``score(q, d) = sum over terms t in q of  idf(t) * tf_bonus(t, d)``

``idf(t) = ln(1 + (N - n_t + 0.5) / (n_t + 0.5))``
``tf_bonus(t, d) = f(t, d) * (k1 + 1) / (f(t, d) + k1 * (1 - b + b * |d| / avgdl))``

The Robertson/Sparck-Jones idf variant above (``+1`` inside the log) never goes
negative, which matters for tiny corpora. ``k1`` and ``b`` are configurable and
recorded with each retrieval result.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScoredDocument:
    index: int
    score: float


class BM25Index:
    """Pre-computed term statistics for a fixed document collection."""

    def __init__(self, documents: Sequence[Sequence[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 <= 0:
            raise ValueError("k1 must be positive")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b must be in [0, 1]")
        self.k1 = k1
        self.b = b
        self.document_count = len(documents)
        self.frequencies: list[Counter[str]] = [Counter(tokens) for tokens in documents]
        self.lengths: list[int] = [sum(counter.values()) for counter in self.frequencies]
        self.average_length = (sum(self.lengths) / self.document_count) if self.document_count else 0.0
        self.document_frequency: Counter[str] = Counter()
        for counter in self.frequencies:
            self.document_frequency.update(counter.keys())

    def idf(self, term: str) -> float:
        n_t = self.document_frequency.get(term, 0)
        return math.log(1.0 + (self.document_count - n_t + 0.5) / (n_t + 0.5))

    def score(self, query_tokens: Sequence[str]) -> list[ScoredDocument]:
        """Score every document against the query, best first."""
        if not self.document_count or not query_tokens:
            return []
        scores = [0.0] * self.document_count
        for term, query_frequency in Counter(query_tokens).items():
            if term not in self.document_frequency:
                continue
            idf = self.idf(term)
            for index, counter in enumerate(self.frequencies):
                frequency = counter.get(term, 0)
                if not frequency:
                    continue
                length = self.lengths[index]
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * (length / self.average_length if self.average_length else 1.0)
                )
                scores[index] += idf * (frequency * (self.k1 + 1.0) / denominator) * query_frequency

        ranked = [
            ScoredDocument(index=index, score=score) for index, score in enumerate(scores) if score > 0.0
        ]
        ranked.sort(key=lambda item: (-item.score, item.index))
        return ranked

    def statistics(self) -> dict[str, float]:
        return {
            "documents": float(self.document_count),
            "average_length": round(self.average_length, 3),
            "vocabulary": float(len(self.document_frequency)),
            "k1": self.k1,
            "b": self.b,
        }


__all__ = ["BM25Index", "ScoredDocument"]
