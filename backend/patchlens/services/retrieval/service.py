"""Retrieval entry point used by the review pipeline.

Responsibilities:

* resolve the indexed version to search (and say so clearly when there is none),
* build a corpus snapshot (cached),
* run exactly one strategy through the shared interface,
* split results into *code context* and *related tests* for the test-intelligence
  stage, without hiding either from the recorded evidence.

The strategy is an argument, so the evaluation harness can sweep every strategy
over the same corpus and the same queries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from patchlens.core.config import Settings, get_settings
from patchlens.core.enums import IndexStatus, RetrievalStrategy
from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.db.models.repository import Repository, RepositoryVersion
from patchlens.services.embedding.base import EmbeddingProvider
from patchlens.services.retrieval.base import RetrievalResult, RetrievedSymbol
from patchlens.services.retrieval.corpus import Corpus, CorpusCache
from patchlens.services.retrieval.rerank import Reranker, build_reranker
from patchlens.services.retrieval.strategies import StrategyConfig, build_retriever

logger = get_logger(__name__)


@dataclass(slots=True)
class RetrievalBundle:
    """Everything retrieval contributed to a review, with provenance."""

    result: RetrievalResult
    context: list[RetrievedSymbol] = field(default_factory=list)
    related_tests: list[RetrievedSymbol] = field(default_factory=list)
    corpus: dict[str, Any] = field(default_factory=dict)
    version_id: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def findings_context_count(self) -> int:
        return len(self.context) + len(self.related_tests)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "corpus": self.corpus,
            "warnings": self.warnings,
            "context": [item.to_dict() for item in self.context],
            "related_tests": [item.to_dict() for item in self.related_tests],
            "retrieval": self.result.to_dict(),
        }


def resolve_version(session: Session, repository: Repository) -> RepositoryVersion | None:
    """Return the newest usable index snapshot for a repository, if any."""
    if repository.latest_version_id:
        version = session.get(RepositoryVersion, repository.latest_version_id)
        if version is not None and version.status == IndexStatus.INDEXED.value:
            return version
    statement = (
        select(RepositoryVersion)
        .where(
            RepositoryVersion.repository_id == repository.id,
            RepositoryVersion.status == IndexStatus.INDEXED.value,
        )
        .order_by(RepositoryVersion.indexed_at.desc().nullslast())
        .limit(1)
    )
    return session.scalar(statement)


class CodeRetriever:
    """Repository-aware retrieval facade."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        cache: CorpusCache | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.embedding_provider = embedding_provider
        self.cache = cache or CorpusCache(settings=self.settings)
        self.reranker = reranker

    def retrieve(
        self,
        *,
        repository: Repository,
        queries: list[str],
        strategy: RetrievalStrategy | None = None,
        top_k: int | None = None,
        exclude_symbol_ids: frozenset[str] = frozenset(),
        max_related_tests: int = 5,
    ) -> RetrievalBundle:
        started = time.perf_counter()
        strategy = strategy or RetrievalStrategy(self.settings.retrieval_strategy)
        top_k = top_k or self.settings.retrieval_top_k
        warnings: list[str] = []

        version = resolve_version(self.session, repository)
        if version is None:
            empty = RetrievalResult(
                strategy=strategy,
                query_count=len(queries),
                results=[],
                latency_ms=0.0,
                backend="none",
                notes=["repository has no successfully indexed version"],
            )
            return RetrievalBundle(
                result=empty,
                version_id=None,
                warnings=[
                    "repository has no successfully indexed version: review ran without repository context"
                ],
            )

        try:
            corpus = self.cache.get(self.session, version)
        except Exception as exc:  # surface, never silently degrade
            logger.error(
                "corpus build failed", extra=log_extra(version_id=version.id, **describe_exception(exc))
            )
            raise

        if not corpus.documents:
            warnings.append("indexed version contains no symbols: review ran without repository context")
            empty = RetrievalResult(
                strategy=strategy,
                query_count=len(queries),
                results=[],
                latency_ms=(time.perf_counter() - started) * 1000,
                backend="empty_corpus",
                notes=[corpus.skip_reason or "empty corpus"],
            )
            return RetrievalBundle(
                result=empty, version_id=version.id, corpus=corpus.metadata(), warnings=warnings
            )

        config = StrategyConfig(
            settings=self.settings,
            top_k=top_k,
            candidate_k=max(self.settings.retrieval_candidate_k, top_k),
            rrf_k=self.settings.retrieval_rrf_k,
            max_per_file=self.settings.retrieval_max_results_per_file,
            exclude_symbol_ids=exclude_symbol_ids,
        )
        reranker = self.reranker or build_reranker(self.settings.reranker)
        retriever = build_retriever(
            strategy,
            session=self.session,
            corpus=corpus,
            config=config,
            embedding_provider=self.embedding_provider,
            reranker=reranker,
        )
        result = retriever.retrieve(queries, top_k=top_k)

        context = [item for item in result.results if not item.is_test][:top_k]
        related_tests = [item for item in result.results if item.is_test][:max_related_tests]
        if not related_tests:
            warnings.append(
                "no related tests were retrieved for this change (either none exist in the index "
                "or they did not rank into the candidate set)"
            )

        logger.info(
            "retrieval complete",
            extra=log_extra(
                repository=repository.full_name,
                strategy=strategy.value,
                backend=result.backend,
                context=len(context),
                tests=len(related_tests),
                latency_ms=round(result.latency_ms, 2),
            ),
        )
        return RetrievalBundle(
            result=result,
            context=context,
            related_tests=related_tests,
            corpus=corpus.metadata(),
            version_id=version.id,
            warnings=warnings,
        )

    def corpus_for(self, version: RepositoryVersion) -> Corpus:
        """Expose the corpus snapshot (used by the evaluation harness)."""
        return self.cache.get(self.session, version)


__all__ = ["CodeRetriever", "RetrievalBundle", "resolve_version"]
