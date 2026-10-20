"""Reranking.

PatchLens ships one reranker: :class:`HeuristicReranker`. It is a **feature-based
scorer, not a trained cross-encoder** - it computes interpretable overlap features
between the query and each candidate and blends them with fixed, documented
weights. It is labelled as such in the UI and in the evaluation report, and no
result of it is ever described as "cross-encoder reranked".

A real cross-encoder can be added behind the same :class:`Reranker` interface
without touching the review pipeline; the evaluation harness will then report it
as its own row.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from patchlens.services.retrieval.base import RetrievedSymbol
from patchlens.services.retrieval.corpus import CorpusDocument

#: Fixed, hand-set weights. Kept as module constants so the evaluation report can
#: print exactly what was used.
WEIGHT_TERM_COVERAGE = 0.42
WEIGHT_BIGRAM_OVERLAP = 0.18
WEIGHT_SYMBOL_NAME_MATCH = 0.22
WEIGHT_PATH_AFFINITY = 0.10
WEIGHT_KIND_PRIOR = 0.08
LENGTH_PENALTY_THRESHOLD = 6_000
LENGTH_PENALTY = 0.05

_TEST_MARKERS = ("test", "tests", "spec")


@dataclass(frozen=True, slots=True)
class RerankFeatures:
    term_coverage: float
    bigram_overlap: float
    symbol_name_match: float
    path_affinity: float
    kind_prior: float
    length_penalty: float

    @property
    def score(self) -> float:
        raw = (
            WEIGHT_TERM_COVERAGE * self.term_coverage
            + WEIGHT_BIGRAM_OVERLAP * self.bigram_overlap
            + WEIGHT_SYMBOL_NAME_MATCH * self.symbol_name_match
            + WEIGHT_PATH_AFFINITY * self.path_affinity
            + WEIGHT_KIND_PRIOR * self.kind_prior
            - self.length_penalty
        )
        return max(0.0, min(1.0, raw))

    def to_dict(self) -> dict[str, float]:
        return {
            "term_coverage": round(self.term_coverage, 4),
            "bigram_overlap": round(self.bigram_overlap, 4),
            "symbol_name_match": round(self.symbol_name_match, 4),
            "path_affinity": round(self.path_affinity, 4),
            "kind_prior": round(self.kind_prior, 4),
            "length_penalty": round(self.length_penalty, 4),
        }


class Reranker(ABC):
    name: str = "none"

    @abstractmethod
    def rerank(
        self,
        *,
        query: str,
        candidates: list[RetrievedSymbol],
        documents: dict[str, CorpusDocument],
        limit: int,
    ) -> list[RetrievedSymbol]:
        """Return candidates reordered by the reranker's own score."""

    def describe(self) -> dict[str, Any]:
        return {"reranker": self.name, "is_trained_cross_encoder": False}


class NoOpReranker(Reranker):
    """Pass-through; keeps the pipeline shape identical when reranking is off."""

    name = "none"

    def rerank(
        self,
        *,
        query: str,
        candidates: list[RetrievedSymbol],
        documents: dict[str, CorpusDocument],
        limit: int,
    ) -> list[RetrievedSymbol]:
        _ = query, documents
        return candidates[:limit]


class HeuristicReranker(Reranker):
    """Feature-based reranker (not a trained model)."""

    name = "heuristic"

    def rerank(
        self,
        *,
        query: str,
        candidates: list[RetrievedSymbol],
        documents: dict[str, CorpusDocument],
        limit: int,
    ) -> list[RetrievedSymbol]:
        from patchlens.services.embedding.hashing import tokenise

        query_terms = set(tokenise(query))
        query_bigrams = set(zip(tokenise(query), tokenise(query)[1:], strict=False))
        mentions_test = any(marker in query.lower() for marker in _TEST_MARKERS)

        rescored: list[RetrievedSymbol] = []
        for candidate in candidates:
            document = documents.get(candidate.symbol_id)
            if document is None:
                rescored.append(candidate)
                continue
            features = self.features(
                query_terms=query_terms,
                query_bigrams=query_bigrams,
                document=document,
                mentions_test=mentions_test,
            )
            candidate.components["rerank"] = features.score
            candidate.reasons.append("heuristic reranker features " + str(features.to_dict()))
            # Blend the fusion score with the reranker score: fusion keeps recall,
            # the features correct for lexical/vector blind spots. Equal weighting
            # is deliberate and recorded here rather than hidden.
            candidate.score = 0.5 * candidate.score + 0.5 * features.score
            rescored.append(candidate)

        rescored.sort(key=lambda item: (-item.score, item.path, item.start_line))
        return rescored[:limit]

    def features(
        self,
        *,
        query_terms: set[str],
        query_bigrams: set[tuple[str, str]],
        document: CorpusDocument,
        mentions_test: bool,
    ) -> RerankFeatures:
        doc_terms = set(document.tokens)
        coverage = (len(query_terms & doc_terms) / len(query_terms)) if query_terms else 0.0

        doc_bigrams = set(zip(document.tokens, document.tokens[1:], strict=False))
        bigram_overlap = (len(query_bigrams & doc_bigrams) / len(query_bigrams)) if query_bigrams else 0.0

        name_terms = set(document.qualified_name.lower().replace(".", "_").split("_"))
        name_match = (
            1.0 if query_terms & {document.name.lower()} else (0.5 if query_terms & name_terms else 0.0)
        )

        path_terms = set(document.path.lower().replace("/", "_").removesuffix(".py").split("_"))
        path_affinity = (len(query_terms & path_terms) / len(query_terms)) if query_terms else 0.0

        if document.is_test and not mentions_test:
            kind_prior = 0.0
        elif document.kind in {"function", "method", "test_function"}:
            kind_prior = 1.0
        elif document.kind in {"class", "test_class"}:
            kind_prior = 0.7
        else:
            kind_prior = 0.4

        penalty = LENGTH_PENALTY if document.length > LENGTH_PENALTY_THRESHOLD else 0.0
        return RerankFeatures(
            term_coverage=coverage,
            bigram_overlap=bigram_overlap,
            symbol_name_match=name_match,
            path_affinity=path_affinity,
            kind_prior=kind_prior,
            length_penalty=penalty,
        )


def build_reranker(name: str) -> Reranker:
    if name == "heuristic":
        return HeuristicReranker()
    if name == "none":
        return NoOpReranker()
    raise ValueError(f"unknown reranker {name!r}")


__all__ = ["HeuristicReranker", "NoOpReranker", "RerankFeatures", "Reranker", "build_reranker"]
