"""Repository, code-intelligence and embedding tables."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from patchlens.core.enums import IndexStatus, SymbolKind
from patchlens.db.base import Base, TimestampMixin, UTCDateTime, id_default
from patchlens.db.types import VectorColumn

EMBEDDING_DIM_DEFAULT = 512
"""Default ``EMBEDDING_DIM``.

The pgvector column is unconstrained (``vector`` without a dimension) so the
configured embedding model can be swapped without a schema migration; the
dimension actually used is validated in Python and recorded per row. See
``docs/ARCHITECTURE.md`` -> "Vector storage" for the indexable-column migration.
"""


class Repository(Base, TimestampMixin):
    """A source repository PatchLens can index and review."""

    __tablename__ = "repositories"

    id_prefix = "repo"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    #: ``github`` for GitHub App installations, ``local`` for fixtures/demos.
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    installation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: Local checkout used for indexing in local/demo mode. Never exposed via API.
    local_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    clone_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    language_primary: Mapped[str] = mapped_column(String(32), nullable=False, default="python")
    indexing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Denormalised pointers used by the dashboard to avoid N+1 queries.
    latest_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_status: Mapped[str] = mapped_column(String(32), nullable=False, default=IndexStatus.QUEUED.value)
    indexed_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_indexed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_index_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    versions: Mapped[list[RepositoryVersion]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
        order_by="RepositoryVersion.created_at.desc()",
    )

    @classmethod
    def make_full_name(cls, owner: str, name: str) -> str:
        return f"{owner}/{name}".lower()


class RepositoryVersion(Base, TimestampMixin):
    """An immutable index snapshot of a repository at one commit."""

    __tablename__ = "repository_versions"
    __table_args__ = (
        UniqueConstraint("repository_id", "commit_sha", name="uq_repository_versions_repo_commit"),
        Index("ix_repository_versions_repo_status", "repository_id", "status"),
    )

    id_prefix = "ver"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=IndexStatus.QUEUED.value)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    test_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    index_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    repository: Mapped[Repository] = relationship(back_populates="versions")
    symbols: Mapped[list[CodeSymbol]] = relationship(
        back_populates="version", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_usable(self) -> bool:
        return self.status == IndexStatus.INDEXED.value


class CodeSymbol(Base, TimestampMixin):
    """A module, class, function, method or test extracted from source."""

    __tablename__ = "code_symbols"
    __table_args__ = (
        UniqueConstraint(
            "repository_version_id", "path", "qualified_name", "start_line", name="uq_code_symbols_identity"
        ),
        Index("ix_code_symbols_version_kind", "repository_version_id", "kind"),
        Index("ix_code_symbols_version_path", "repository_version_id", "path"),
        Index("ix_code_symbols_name", "name"),
    )

    id_prefix = "sym"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    repository_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    repository_version_id: Mapped[str] = mapped_column(
        ForeignKey("repository_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="python")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default=SymbolKind.FUNCTION.value)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    qualified_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    parent_qualified_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    docstring: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    decorators: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    bases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    calls: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    imports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: Source text of the symbol (bounded by ``INDEX_MAX_FILE_BYTES``).
    source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Text actually embedded (signature + docstring + normalised source).
    embedding_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    version: Mapped[RepositoryVersion] = relationship(back_populates="symbols")

    @property
    def display_name(self) -> str:
        return f"{self.qualified_name} ({self.path}:{self.start_line})"


class SymbolEdge(Base):
    """An approximate relationship between symbols (calls/imports/inheritance).

    Edges are resolved best-effort: unresolved edges keep ``resolved=False`` and
    a ``dst_name`` so the review prompt can still mention a dependency without
    inventing a location.
    """

    __tablename__ = "symbol_edges"
    __table_args__ = (
        Index("ix_symbol_edges_version_kind", "repository_version_id", "kind"),
        Index("ix_symbol_edges_dst_name", "repository_version_id", "dst_name"),
    )

    id_prefix = "edge"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    repository_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    repository_version_id: Mapped[str] = mapped_column(
        ForeignKey("repository_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    src_symbol_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    src_qualified_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    dst_name: Mapped[str] = mapped_column(String(512), nullable=False)
    dst_symbol_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="call")
    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CodeEmbedding(Base):
    """Embedding vector for a symbol (or a standalone module/doc chunk)."""

    __tablename__ = "code_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "repository_version_id", "content_hash", "provider", "model", name="uq_code_embeddings_identity"
        ),
        Index("ix_code_embeddings_version", "repository_version_id"),
    )

    id_prefix = "emb"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    repository_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    repository_version_id: Mapped[str] = mapped_column(
        ForeignKey("repository_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default=SymbolKind.FUNCTION.value)
    #: Hash of the embedded text; makes re-indexing idempotent.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False, default=EMBEDDING_DIM_DEFAULT)
    #: True when the vector came from a learned model (vs. deterministic hashing).
    is_neural: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    embedding: Mapped[list[float]] = mapped_column(VectorColumn(), nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
