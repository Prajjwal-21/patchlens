"""In-memory corpus snapshot for a repository version.

BM25 needs the whole version's term statistics, and the portable vector backend
needs candidate rows. Both read through :class:`CorpusCache`, which:

* materialises the version's symbols and embeddings once,
* reuses the snapshot while the version's ``stats.tree_hash`` is unchanged,
* optionally mirrors the snapshot metadata into Redis so multiple workers can
  skip a warm-up (Redis is a cache only; correctness never depends on it).

Snapshots are bounded by configuration so a huge repository degrades predictably.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from patchlens.core.config import Settings, get_settings
from patchlens.core.enums import RetrievalStrategy, SymbolKind
from patchlens.core.logging import get_logger, log_extra
from patchlens.db.models.repository import CodeEmbedding, CodeSymbol, RepositoryVersion

logger = get_logger(__name__)


@dataclass(slots=True)
class CorpusDocument:
    """A symbol as the retrievers see it."""

    symbol_id: str
    path: str
    qualified_name: str
    kind: str
    name: str
    start_line: int
    end_line: int
    is_test: bool
    signature: str | None
    docstring: str | None
    source: str
    text: str
    tokens: list[str]
    token_counts: dict[str, int] = field(default_factory=dict)
    length: int = 0

    def key(self) -> tuple[str, str]:
        return (self.path, self.qualified_name)


@dataclass(slots=True)
class VectorRecord:
    symbol_id: str
    path: str | None
    vector: list[float]


@dataclass(slots=True)
class Corpus:
    """Immutable snapshot of one repository version's index."""

    version_id: str
    repository_id: str
    tree_hash: str
    documents: list[CorpusDocument]
    vectors: list[VectorRecord]
    embedding_provider: str | None
    embedding_model: str | None
    embedding_dim: int | None
    semantic_is_neural: bool
    skip_reason: str | None = None

    @property
    def size(self) -> int:
        return len(self.documents)

    def by_id(self) -> dict[str, CorpusDocument]:
        return {document.symbol_id: document for document in self.documents}

    def tests(self) -> list[CorpusDocument]:
        return [document for document in self.documents if document.is_test]

    def metadata(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "documents": len(self.documents),
            "vectors": len(self.vectors),
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "semantic_is_neural": self.semantic_is_neural,
            "corpus_unavailable_reason": self.skip_reason,
        }


class CorpusCache:
    """Thread-safe, size-bounded cache of corpus snapshots."""

    def __init__(self, *, max_entries: int = 4, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.max_entries = max(1, max_entries)
        self._entries: dict[str, tuple[float, Corpus]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get(self, session: Session, version: RepositoryVersion) -> Corpus:
        tree_hash = str((version.stats or {}).get("tree_hash") or version.commit_sha)
        key = f"{version.id}:{tree_hash}"
        now = time.monotonic()
        with self._lock:
            cached = self._entries.get(key)
            if cached and now - cached[0] < self.settings.retrieval_cache_ttl_seconds:
                return cached[1]

        corpus = self._build(session, version, tree_hash)
        with self._lock:
            if len(self._entries) >= self.max_entries:
                oldest = min(self._entries.items(), key=lambda item: item[1][0])[0]
                self._entries.pop(oldest, None)
            self._entries[key] = (now, corpus)
        return corpus

    # ------------------------------------------------------------------ builder
    def _build(self, session: Session, version: RepositoryVersion, tree_hash: str) -> Corpus:
        documents: list[CorpusDocument] = []
        statement = select(CodeSymbol).where(CodeSymbol.repository_version_id == version.id)
        for symbol in session.scalars(statement):
            text = _document_text(symbol)
            tokens = _tokenise(text)
            documents.append(
                CorpusDocument(
                    symbol_id=symbol.id,
                    path=symbol.path,
                    qualified_name=symbol.qualified_name,
                    kind=symbol.kind,
                    name=symbol.name,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    is_test=symbol.is_test,
                    signature=symbol.signature,
                    docstring=symbol.docstring,
                    source=symbol.source,
                    text=text,
                    tokens=tokens,
                    length=len(tokens),
                )
            )

        vectors: list[VectorRecord] = []
        provider = version.embedding_provider
        model = version.embedding_model
        semantic_is_neural = False
        vectors_statement = select(CodeEmbedding).where(CodeEmbedding.repository_version_id == version.id)
        vector_rows = list(session.scalars(vectors_statement))
        if vector_rows:
            semantic_is_neural = any(row.is_neural for row in vector_rows)
        for row in vector_rows:
            vectors.append(
                VectorRecord(symbol_id=row.symbol_id or "", path=row.path, vector=list(row.embedding))
            )

        skip_reason: str | None = None
        if not documents:
            skip_reason = "the indexed version contains no symbols"
        logger.debug(
            "built corpus snapshot",
            extra=log_extra(
                version_id=version.id,
                documents=len(documents),
                vectors=len(vectors),
                tree_hash=tree_hash[:12],
            ),
        )
        return Corpus(
            version_id=version.id,
            repository_id=version.repository_id,
            tree_hash=tree_hash,
            documents=documents,
            vectors=vectors,
            embedding_provider=provider,
            embedding_model=model,
            embedding_dim=version.embedding_dim,
            semantic_is_neural=semantic_is_neural,
            skip_reason=skip_reason,
        )


def _document_text(symbol: CodeSymbol) -> str:
    """Build the searchable text for a symbol (also the BM25 document)."""
    parts = [symbol.qualified_name, symbol.path, symbol.kind]
    if symbol.name:
        parts.append(symbol.name)
    if symbol.signature:
        parts.append(symbol.signature)
    if symbol.docstring:
        parts.append(symbol.docstring)
    if symbol.bases:
        parts.append(" ".join(symbol.bases))
    if symbol.calls:
        parts.append(" ".join(symbol.calls))
    parts.append(symbol.source)
    return "\n".join(part for part in parts if part)


def _tokenise(text: str) -> list[str]:
    from patchlens.services.embedding.hashing import tokenise

    return tokenise(text)


__all__ = ["Corpus", "CorpusCache", "CorpusDocument", "RetrievalStrategy", "SymbolKind", "VectorRecord"]
