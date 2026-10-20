"""Concrete retrieval strategies.

Compared strategies (identical interface, identical corpus):

=============  ==================================================================
``static``     Baseline: exact identifier / path / kind matching with no ranking
               model at all. This is the "naive grep" reference point.
``bm25``       Lexical ranking over symbol documents (Okapi BM25).
``semantic``   Cosine search over stored embeddings.
``hybrid``     BM25 + semantic fused with weighted Reciprocal Rank Fusion.
``hybrid_rerank`` Hybrid, then the heuristic reranker rescores the top candidates.
=============  ==================================================================

Multi-query behaviour is uniform: a review issue usually generates several
queries (one per changed symbol plus a diff-level summary) describing the *same*
change, so per-symbol raw scores are combined with ``max`` and then ranked; only
*across retrievers* is RRF used, where rank fusion is the right tool.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from patchlens.core.config import Settings, get_settings
from patchlens.core.enums import RetrievalStrategy
from patchlens.core.logging import get_logger
from patchlens.services.embedding.base import EmbeddingProvider
from patchlens.services.retrieval.base import RetrievalResult, RetrievedSymbol, Retriever
from patchlens.services.retrieval.bm25 import BM25Index
from patchlens.services.retrieval.corpus import Corpus, CorpusDocument
from patchlens.services.retrieval.fusion import cap_per_file, reciprocal_rank_fusion
from patchlens.services.retrieval.rerank import NoOpReranker, Reranker
from patchlens.services.retrieval.vector_index import PORTABLE_BACKEND, SemanticVectorIndex

logger = get_logger(__name__)

SOURCE_EXCERPT_CHARS = 1_600
IDENTIFIER_RE_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"


def _query_identifiers(query: str) -> set[str]:
    """Extract identifier-shaped tokens (the ones that matter for code search)."""
    tokens: set[str] = set()
    current: list[str] = []
    for char in query:
        if char in IDENTIFIER_RE_CHARS:
            current.append(char)
        else:
            if len(current) > 2:
                tokens.add("".join(current))
            current = []
    if len(current) > 2:
        tokens.add("".join(current))
    return {token for token in tokens if not token.isdigit()}


@dataclass(slots=True)
class StrategyConfig:
    """Everything a strategy needs, resolved once per review."""

    settings: Settings
    top_k: int
    candidate_k: int
    rrf_k: int
    max_per_file: int
    exclude_symbol_ids: frozenset[str] = frozenset()


class BaseRetriever(Retriever):
    """Shared plumbing: corpus lookup, ranking merge, result construction."""

    def __init__(
        self,
        *,
        session: Session,
        corpus: Corpus,
        config: StrategyConfig,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.session = session
        self.corpus = corpus
        self.config = config
        self.embedding_provider = embedding_provider
        self.reranker = reranker or NoOpReranker()
        self._documents = corpus.by_id()

    # --------------------------------------------------------------- utilities
    def _eligible(self) -> list[CorpusDocument]:
        exclude = self.config.exclude_symbol_ids
        return [document for document in self.corpus.documents if document.symbol_id not in exclude]

    def _merge_query_scores(self, per_query: list[dict[str, float]]) -> dict[str, float]:
        """Combine the same change's queries with ``max`` (documented choice)."""
        merged: dict[str, float] = {}
        for scores in per_query:
            for symbol_id, score in scores.items():
                current = merged.get(symbol_id)
                if current is None or score > current:
                    merged[symbol_id] = score
        return merged

    def _rank(self, scores: dict[str, float]) -> list[tuple[str, float]]:
        """Rank by score, then by stable document identity.

        ``symbol_id`` is generated at index time, so breaking ties with it makes
        the same corpus rank differently after a re-index (and lets a random id
        decide an evaluation score). Ties are broken by path, line and qualified
        name instead, which are properties of the code under test.
        """
        return sorted(scores.items(), key=self._rank_key)

    def _rank_key(self, item: tuple[str, float]) -> tuple[float, str, int, str]:
        symbol_id, score = item
        document = self._documents.get(symbol_id)
        if document is None:
            return (-score, symbol_id, 0, "")
        return (-score, document.path, document.start_line, document.qualified_name)

    def _make_result(
        self,
        *,
        ranked_keys: list[tuple[str, float]],
        retriever: str,
        queries: list[str],
        started: float,
        backend: str,
        candidate_count: int,
        components: dict[str, dict[str, float]] | None = None,
        notes: list[str] | None = None,
        limit: int | None = None,
    ) -> RetrievalResult:
        limit = limit or self.config.top_k
        results: list[RetrievedSymbol] = []
        seen: set[tuple[str, str]] = set()
        deduplicated = 0
        for symbol_id, score in ranked_keys:
            document = self._documents.get(symbol_id)
            if document is None:
                continue
            key = (document.path, document.qualified_name)
            if key in seen:
                deduplicated += 1
                continue
            seen.add(key)
            results.append(
                RetrievedSymbol(
                    symbol_id=document.symbol_id,
                    path=document.path,
                    qualified_name=document.qualified_name,
                    kind=document.kind,
                    start_line=document.start_line,
                    end_line=document.end_line,
                    score=score,
                    retriever=retriever,
                    is_test=document.is_test,
                    docstring=document.docstring,
                    signature=document.signature,
                    source_excerpt=document.source[:SOURCE_EXCERPT_CHARS],
                    components=dict((components or {}).get(symbol_id, {})),
                )
            )
        return RetrievalResult(
            strategy=self.strategy,
            query_count=len(queries),
            results=results[:limit],
            latency_ms=(time.perf_counter() - started) * 1000,
            backend=backend,
            candidate_count=candidate_count,
            deduplicated=deduplicated,
            notes=notes or [],
            reranker=self.reranker.name if self.reranker.name != "none" else None,
            semantic_is_neural=self.corpus.semantic_is_neural if self.corpus.vectors else None,
        )


class StaticRetriever(BaseRetriever):
    """Exact identifier/path matcher: the un-ranked baseline."""

    strategy = RetrievalStrategy.STATIC

    def retrieve(self, queries: list[str], *, top_k: int) -> RetrievalResult:
        started = time.perf_counter()
        documents = self._eligible()
        per_query: list[dict[str, float]] = []
        for query in queries:
            identifiers = {token.lower() for token in _query_identifiers(query)}
            scores: dict[str, float] = {}
            for document in documents:
                haystack_qualified = document.qualified_name.lower()
                haystack_path = document.path.lower()
                score = 0.0
                for identifier in identifiers:
                    if identifier == document.name.lower():
                        score += 3.0
                    elif identifier in haystack_qualified:
                        score += 2.0
                    elif identifier in haystack_path:
                        score += 1.0
                if score > 0.0:
                    scores[document.symbol_id] = score
            per_query.append(scores)

        merged = self._merge_query_scores(per_query)
        ranked = self._rank(merged)
        return self._make_result(
            ranked_keys=ranked[: self.config.candidate_k],
            retriever=self.name,
            queries=queries,
            started=started,
            backend="lexical_identifier_match",
            candidate_count=len(merged),
            notes=["baseline: exact identifier and path matching, no ranking model"],
            limit=top_k,
        )


class BM25Retriever(BaseRetriever):
    """Okapi BM25 over symbol documents."""

    strategy = RetrievalStrategy.BM25

    def retrieve(self, queries: list[str], *, top_k: int) -> RetrievalResult:
        from patchlens.services.embedding.hashing import tokenise

        started = time.perf_counter()
        documents = self._eligible()
        index = BM25Index(
            [document.tokens for document in documents],
            k1=self.config.settings.retrieval_bm25_k1,
            b=self.config.settings.retrieval_bm25_b,
        )
        per_query: list[dict[str, float]] = []
        for query in queries:
            scored = index.score(tokenise(query))
            per_query.append({documents[item.index].symbol_id: item.score for item in scored})

        merged = self._merge_query_scores(per_query)
        ranked = self._rank(merged)
        statistics = index.statistics()
        return self._make_result(
            ranked_keys=ranked[: self.config.candidate_k],
            retriever=self.name,
            queries=queries,
            started=started,
            backend="bm25_okapi",
            candidate_count=len(merged),
            notes=[
                f"BM25 k1={statistics['k1']} b={statistics['b']} corpus_documents={int(statistics['documents'])}"
            ],
            limit=top_k,
        )


class SemanticRetriever(BaseRetriever):
    """Cosine-similarity search over stored embeddings."""

    strategy = RetrievalStrategy.SEMANTIC

    def retrieve(self, queries: list[str], *, top_k: int) -> RetrievalResult:
        started = time.perf_counter()
        if self.embedding_provider is None:
            raise ValueError("SemanticRetriever requires an embedding provider")
        index = SemanticVectorIndex(self.session, self.corpus, settings=self.config.settings)

        batch = self.embedding_provider.embed_documents(queries)
        per_query: list[dict[str, float]] = []
        for vector in batch.vectors:
            hits = index.search(vector, limit=self.config.candidate_k)
            per_query.append({hit.symbol_id: hit.score for hit in hits})

        merged = self._merge_query_scores(per_query)
        ranked = self._rank(merged)
        notes = [f"embedding provider={batch.provider} model={batch.model} dim={batch.dim}"]
        if not batch.is_neural:
            notes.append(
                "embedding provider is the deterministic hashing encoder, so this is a "
                "bag-of-words cosine baseline rather than neural semantic search"
            )
        return self._make_result(
            ranked_keys=ranked[: self.config.candidate_k],
            retriever=self.name,
            queries=queries,
            started=started,
            backend=index.backend,
            candidate_count=len(merged),
            notes=notes,
            limit=top_k,
        )


class HybridRetriever(BaseRetriever):
    """BM25 + semantic fused with weighted RRF."""

    strategy = RetrievalStrategy.HYBRID

    def retrieve(self, queries: list[str], *, top_k: int) -> RetrievalResult:
        started = time.perf_counter()
        bm25_result = BM25Retriever(
            session=self.session,
            corpus=self.corpus,
            config=self.config,
            embedding_provider=self.embedding_provider,
            reranker=self.reranker,
        ).retrieve(queries, top_k=self.config.candidate_k)

        backend = bm25_result.backend
        component_scores: dict[str, dict[str, float]] = {}
        ranked_lists: dict[str, list[tuple[str, str]]] = {"bm25": []}
        bm25_scores: dict[str, float] = {}
        for item in bm25_result.results:
            ranked_lists["bm25"].append((item.path, item.qualified_name))
            bm25_scores[item.symbol_id] = item.components.get("bm25", item.score)
            component_scores.setdefault(item.symbol_id, {})["bm25"] = item.score

        semantic_scores: dict[str, float] = {}
        semantic_notes: list[str] = []
        if self.embedding_provider is not None and self.corpus.vectors:
            semantic_result = SemanticRetriever(
                session=self.session,
                corpus=self.corpus,
                config=self.config,
                embedding_provider=self.embedding_provider,
                reranker=self.reranker,
            ).retrieve(queries, top_k=self.config.candidate_k)
            backend = f"{bm25_result.backend}+{semantic_result.backend}"
            semantic_notes = [note for note in semantic_result.notes if "baseline" in note]
            ranked_lists["semantic"] = []
            for semantic_item in semantic_result.results:
                ranked_lists["semantic"].append((semantic_item.path, semantic_item.qualified_name))
                semantic_scores[semantic_item.symbol_id] = semantic_item.score
                component_scores.setdefault(semantic_item.symbol_id, {})["semantic"] = semantic_item.score
        else:
            semantic_notes.append("no embeddings available for this version: hybrid degraded to BM25 only")

        by_key = {document.key(): document for document in self.corpus.documents}
        fused = reciprocal_rank_fusion(
            ranked_lists,
            weights={
                "bm25": self.config.settings.retrieval_hybrid_weight_bm25,
                "semantic": self.config.settings.retrieval_hybrid_weight_semantic,
            },
            k=self.config.rrf_k,
        )
        capped = cap_per_file(fused, max_per_file=self.config.max_per_file, limit=self.config.candidate_k)

        ranked_keys: list[tuple[str, float]] = []
        for fused_item in capped:
            document = by_key.get((fused_item.key[0], fused_item.key[1]))
            if document is None:
                continue
            components = component_scores.get(document.symbol_id, {})
            components["rrf"] = fused_item.fused_score
            component_scores[document.symbol_id] = components
            ranked_keys.append((document.symbol_id, fused_item.fused_score))

        notes = [
            f"RRF k={self.config.rrf_k} weights bm25={self.config.settings.retrieval_hybrid_weight_bm25}"
            f" semantic={self.config.settings.retrieval_hybrid_weight_semantic}",
            f"BM25 candidates={len(bm25_scores)} semantic candidates={len(semantic_scores)}",
            *semantic_notes,
        ]
        if not semantic_scores:
            notes.append("hybrid result is lexical-only for this query")

        return self._make_result(
            ranked_keys=ranked_keys,
            retriever=self.name,
            queries=queries,
            started=started,
            backend=backend,
            candidate_count=len(fused),
            components=component_scores,
            notes=notes,
            limit=top_k,
        )


class HybridRerankRetriever(HybridRetriever):
    """Hybrid retrieval followed by the heuristic reranker."""

    strategy = RetrievalStrategy.HYBRID_RERANK

    def retrieve_hybrid(self, queries: list[str], *, top_k: int) -> RetrievalResult:
        return super().retrieve(queries, top_k=top_k)

    def retrieve(self, queries: list[str], *, top_k: int) -> RetrievalResult:
        started = time.perf_counter()
        hybrid = super().retrieve(queries, top_k=self.config.candidate_k)
        merged_query = "\n".join(queries)
        reranked = self.reranker.rerank(
            query=merged_query,
            candidates=list(hybrid.results),
            documents=self._documents,
            limit=top_k,
        )
        return RetrievalResult(
            strategy=self.strategy,
            query_count=len(queries),
            results=reranked,
            latency_ms=(time.perf_counter() - started) * 1000,
            backend=hybrid.backend,
            candidate_count=hybrid.candidate_count,
            deduplicated=hybrid.deduplicated,
            notes=[
                *hybrid.notes,
                f"reranker={self.reranker.name} (feature-based, not a trained cross-encoder)",
            ],
            reranker=self.reranker.name,
            semantic_is_neural=hybrid.semantic_is_neural,
        )


def build_retriever(
    strategy: RetrievalStrategy,
    *,
    session: Session,
    corpus: Corpus,
    config: StrategyConfig,
    embedding_provider: EmbeddingProvider | None = None,
    reranker: Reranker | None = None,
) -> Retriever:
    """Factory used by the review pipeline and the evaluation harness."""
    kwargs: dict[str, Any] = {
        "session": session,
        "corpus": corpus,
        "config": config,
        "embedding_provider": embedding_provider,
        "reranker": reranker,
    }
    if strategy == RetrievalStrategy.STATIC:
        return StaticRetriever(**kwargs)
    if strategy == RetrievalStrategy.BM25:
        return BM25Retriever(**kwargs)
    if strategy == RetrievalStrategy.SEMANTIC:
        return SemanticRetriever(**kwargs)
    if strategy == RetrievalStrategy.HYBRID:
        return HybridRetriever(**kwargs)
    if strategy == RetrievalStrategy.HYBRID_RERANK:
        return HybridRerankRetriever(**kwargs)
    raise ValueError(f"unknown retrieval strategy {strategy!r}")


__all__ = [
    "PORTABLE_BACKEND",
    "BM25Retriever",
    "BaseRetriever",
    "HybridRerankRetriever",
    "HybridRetriever",
    "SemanticRetriever",
    "StaticRetriever",
    "StrategyConfig",
    "build_retriever",
    "get_settings",
]
