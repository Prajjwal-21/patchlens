"""Retrieval interfaces and result types.

Everything the review pipeline needs from search goes through :class:`Retriever`,
so strategies (static, BM25, semantic, hybrid, hybrid+rerank) can be compared in
the evaluation harness without touching pipeline code.

Retrieval results always carry the evidence needed to justify them: the symbol
identity, its file and line range, the score, which retriever produced it, and
which storage backend served the search (``pgvector`` or the portable in-process
scan).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from patchlens.core.enums import RetrievalStrategy


@dataclass(slots=True)
class RetrievedSymbol:
    """One search hit."""

    symbol_id: str
    path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    score: float
    retriever: str
    is_test: bool = False
    docstring: str | None = None
    signature: str | None = None
    source_excerpt: str | None = None
    #: Per-retriever scores before fusion (e.g. ``{"bm25": 12.3, "semantic": 0.61}``).
    components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def key(self) -> tuple[str, str]:
        return (self.path, self.qualified_name)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol_id": self.symbol_id,
            "path": self.path,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "score": round(self.score, 6),
            "retriever": self.retriever,
            "is_test": self.is_test,
        }
        if self.components:
            payload["score_components"] = {key: round(value, 6) for key, value in self.components.items()}
        if self.reasons:
            payload["reasons"] = self.reasons
        return payload


@dataclass(slots=True)
class RetrievalResult:
    """Result of one retrieval call, with provenance for the dashboard/evals."""

    strategy: RetrievalStrategy
    query_count: int
    results: list[RetrievedSymbol]
    latency_ms: float
    backend: str
    candidate_count: int = 0
    deduplicated: int = 0
    notes: list[str] = field(default_factory=list)
    reranker: str | None = None
    #: True when the semantic half of the search used a non-neural embedder.
    semantic_is_neural: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "query_count": self.query_count,
            "result_count": len(self.results),
            "candidate_count": self.candidate_count,
            "deduplicated": self.deduplicated,
            "latency_ms": round(self.latency_ms, 3),
            "backend": self.backend,
            "reranker": self.reranker,
            "semantic_is_neural": self.semantic_is_neural,
            "notes": self.notes,
            "results": [item.to_dict() for item in self.results],
        }


class Retriever(ABC):
    """A retrieval strategy over one indexed repository version."""

    strategy: RetrievalStrategy

    @property
    def name(self) -> str:
        return self.strategy.value

    @abstractmethod
    def retrieve(self, queries: list[str], *, top_k: int) -> RetrievalResult:
        """Return the ``top_k`` most relevant symbols for the query set."""

    def describe(self) -> dict[str, Any]:
        return {"strategy": self.strategy.value}


__all__ = ["RetrievalResult", "RetrievedSymbol", "Retriever"]
