"""Hybrid code retrieval: BM25, semantic search, RRF fusion and reranking."""

from patchlens.services.retrieval.base import RetrievalResult, RetrievedSymbol, Retriever
from patchlens.services.retrieval.bm25 import BM25Index
from patchlens.services.retrieval.corpus import Corpus, CorpusCache, CorpusDocument
from patchlens.services.retrieval.fusion import reciprocal_rank_fusion
from patchlens.services.retrieval.rerank import HeuristicReranker, NoOpReranker, Reranker, build_reranker
from patchlens.services.retrieval.service import CodeRetriever, RetrievalBundle, resolve_version
from patchlens.services.retrieval.strategies import StrategyConfig, build_retriever
from patchlens.services.retrieval.vector_index import SemanticVectorIndex

__all__ = [
    "BM25Index",
    "CodeRetriever",
    "Corpus",
    "CorpusCache",
    "CorpusDocument",
    "HeuristicReranker",
    "NoOpReranker",
    "Reranker",
    "RetrievalBundle",
    "RetrievalResult",
    "RetrievedSymbol",
    "Retriever",
    "SemanticVectorIndex",
    "StrategyConfig",
    "build_reranker",
    "build_retriever",
    "reciprocal_rank_fusion",
    "resolve_version",
]
