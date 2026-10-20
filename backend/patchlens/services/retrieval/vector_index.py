"""Semantic search over stored embeddings.

Two backends behind one call, because PatchLens supports both a PostgreSQL +
pgvector deployment and a zero-dependency local deployment:

* ``pgvector`` - cosine distance (``<=>``) evaluated inside PostgreSQL with a
  parameterised query;
* ``portable_scan`` - in-process cosine similarity over the version's vectors
  (SQLite development/CI). This is an *exact* scan, but it is bounded: above
  ``SEMANTIC_SCAN_LIMIT`` candidates the retriever fails loudly instead of
  silently returning partial results.

The active backend is reported with every result so a "semantic" hit is never
confused with approximate-vector-index behaviour it did not use.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from patchlens.core.config import Settings, get_settings
from patchlens.core.errors import ProviderNotConfiguredError
from patchlens.core.logging import get_logger, log_extra
from patchlens.db.types import cosine_similarity
from patchlens.services.retrieval.corpus import Corpus

logger = get_logger(__name__)

PGVECTOR_BACKEND = "pgvector"
PORTABLE_BACKEND = "portable_scan"

#: Parameterised: no string interpolation of values into SQL anywhere in PatchLens.
_PGVECTOR_SQL = text(
    """
    SELECT symbol_id, 1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
    FROM code_embeddings
    WHERE repository_version_id = :version_id
      AND provider = :provider
      AND model = :model
      AND embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:query_vector AS vector), symbol_id
    LIMIT :limit
    """
)


@dataclass(slots=True)
class VectorHit:
    symbol_id: str
    score: float
    backend: str


class SemanticVectorIndex:
    """Cosine-similarity search over one indexed version."""

    def __init__(
        self,
        session: Session,
        corpus: Corpus,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session = session
        self.corpus = corpus

    @property
    def backend(self) -> str:
        return PGVECTOR_BACKEND if self._uses_pgvector else PORTABLE_BACKEND

    @property
    def _uses_pgvector(self) -> bool:
        from patchlens.db.types import VectorColumn  # noqa: F401  (documentation anchor)

        return self.session.get_bind().dialect.name == "postgresql"

    def search(self, query_vector: list[float], *, limit: int) -> list[VectorHit]:
        if not query_vector:
            return []
        if self._uses_pgvector:
            return self._search_pgvector(query_vector, limit=limit)
        return self._search_portable(query_vector, limit=limit)

    # ----------------------------------------------------------------- internals
    def _search_pgvector(self, query_vector: list[float], *, limit: int) -> list[VectorHit]:
        payload = "[" + ",".join(f"{value:.8f}" for value in query_vector) + "]"
        rows = self.session.execute(
            _PGVECTOR_SQL,
            {
                "query_vector": payload,
                "version_id": self.corpus.version_id,
                "provider": self.corpus.embedding_provider or "",
                "model": self.corpus.embedding_model or "",
                "limit": limit,
            },
        ).all()
        hits: list[VectorHit] = []
        for symbol_id, similarity in rows:
            if not symbol_id:
                continue
            hits.append(
                VectorHit(symbol_id=str(symbol_id), score=float(similarity or 0.0), backend=PGVECTOR_BACKEND)
            )
        return hits

    def _search_portable(self, query_vector: list[float], *, limit: int) -> list[VectorHit]:
        vectors = [record for record in self.corpus.vectors if record.symbol_id]
        if not vectors:
            return []
        if len(vectors) > self.settings.semantic_scan_limit:
            raise ProviderNotConfiguredError(
                "portable semantic scan refused: candidate set exceeds SEMANTIC_SCAN_LIMIT",
                details={
                    "candidates": len(vectors),
                    "limit": self.settings.semantic_scan_limit,
                    "remedy": "deploy PostgreSQL + pgvector, or raise SEMANTIC_SCAN_LIMIT knowingly",
                },
            )
        if self.corpus.embedding_dim and len(query_vector) != self.corpus.embedding_dim:
            raise ProviderNotConfiguredError(
                "query embedding dimension does not match the indexed version",
                details={"query_dim": len(query_vector), "index_dim": self.corpus.embedding_dim},
            )

        started = time.perf_counter()
        scored: list[VectorHit] = []
        for record in vectors:
            if len(record.vector) != len(query_vector):
                continue
            similarity = cosine_similarity(query_vector, record.vector)
            if similarity <= 0.0:
                continue
            scored.append(VectorHit(symbol_id=record.symbol_id, score=similarity, backend=PORTABLE_BACKEND))
        # Ties must not be resolved by the random index-time symbol id: the same
        # code re-indexed would otherwise return a different ranking.
        documents = self.corpus.by_id()

        def stable_key(hit: VectorHit) -> tuple[float, str, int, str]:
            document = documents.get(hit.symbol_id)
            if document is None:
                return (-hit.score, "", 0, hit.symbol_id)
            return (-hit.score, document.path, document.start_line, document.qualified_name)

        scored.sort(key=stable_key)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.debug(
            "portable semantic scan",
            extra=log_extra(candidates=len(vectors), latency_ms=round(elapsed_ms, 2)),
        )
        return scored[:limit]


__all__ = ["PGVECTOR_BACKEND", "PORTABLE_BACKEND", "SemanticVectorIndex", "VectorHit"]
