"""Repository indexing.

Turns a checkout into a queryable, versioned index snapshot:

``discover -> parse (AST) -> persist symbols -> resolve edges -> embed -> commit``

Design guarantees:

* **Versioned** - one snapshot per ``(repository, commit_sha)``; a snapshot is
  either fully ``indexed`` or marked ``failed`` with a reason, never half-written
  behind a success flag.
* **Idempotent** - re-indexing the same tree hash is a no-op; forcing a reindex
  clears the version's rows first, so no duplicates can accumulate.
* **Incremental** - embeddings are reused across versions when the provider,
  model and embedded content hash match, which is what makes re-indexing a large
  repository affordable.
* **Bounded** - file, symbol and byte budgets come from configuration.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from patchlens.core.config import Settings, get_settings
from patchlens.core.enums import IndexStatus, SymbolKind
from patchlens.core.errors import ProviderNotConfiguredError
from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.core.security import content_fingerprint, new_id, utcnow
from patchlens.db.models.repository import (
    CodeEmbedding,
    CodeSymbol,
    Repository,
    RepositoryVersion,
    SymbolEdge,
)
from patchlens.services.embedding.base import EmbeddingProvider
from patchlens.services.ingestion.discovery import (
    DiscoveredFile,
    DiscoveryReport,
    RepositoryWalker,
    compute_tree_hash,
)
from patchlens.services.ingestion.python_parser import (
    FileParseResult,
    ParsedSymbol,
    PythonParser,
    build_symbol_document,
)

logger = get_logger(__name__)

ProgressCallback = Callable[[int, str, dict[str, Any]], None]

SYMBOL_CHUNK = 500
EDGE_CHUNK = 1_000


@dataclass(slots=True)
class IndexOutcome:
    version_id: str
    status: str
    commit_sha: str
    files: int = 0
    symbols: int = 0
    tests: int = 0
    embeddings: int = 0
    reused_embeddings: int = 0
    skipped_files: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    parse_errors: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    up_to_date: bool = False
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "status": self.status,
            "commit_sha": self.commit_sha,
            "files": self.files,
            "symbols": self.symbols,
            "tests": self.tests,
            "embeddings": self.embeddings,
            "reused_embeddings": self.reused_embeddings,
            "skipped_files": self.skipped_files,
            "skipped_reasons": self.skipped_reasons,
            "parse_errors": self.parse_errors[:20],
            "duration_ms": self.duration_ms,
            "up_to_date": self.up_to_date,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
        }


class RepositoryIndexer:
    """Parses a checkout and writes a versioned index snapshot."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        walker: RepositoryWalker | None = None,
        parser: PythonParser | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedding_provider = embedding_provider
        self.walker = walker or RepositoryWalker(self.settings)
        self.parser = parser or PythonParser()
        if embedding_provider is not None and embedding_provider.dim != self.settings.embedding_dim:
            raise ProviderNotConfiguredError(
                "embedding provider dimension does not match EMBEDDING_DIM",
                details={"provider_dim": embedding_provider.dim, "configured": self.settings.embedding_dim},
            )

    # ------------------------------------------------------------------ public
    def index(
        self,
        session: Session,
        *,
        repository: Repository,
        root: Path,
        commit_sha: str,
        branch: str | None = None,
        force: bool = False,
        progress: ProgressCallback | None = None,
    ) -> IndexOutcome:
        started = time.perf_counter()
        root = root.resolve()
        self._notify(progress, 5, "discovering files", {"root": str(root)})

        report = self.walker.walk(root)
        tree_hash = compute_tree_hash(report)
        version = self._get_or_create_version(session, repository, commit_sha, branch)

        existing_hash = str((version.stats or {}).get("tree_hash") or "")
        if not force and version.status == IndexStatus.INDEXED.value and existing_hash == tree_hash:
            outcome = IndexOutcome(
                version_id=version.id,
                status=version.status,
                commit_sha=commit_sha,
                files=version.file_count,
                symbols=version.symbol_count,
                tests=version.test_count,
                embeddings=version.embedding_count,
                skipped_files=version.skipped_file_count,
                skipped_reasons=dict((version.stats or {}).get("skipped_reasons") or {}),
                duration_ms=int((time.perf_counter() - started) * 1000),
                up_to_date=True,
                embedding_provider=version.embedding_provider,
                embedding_model=version.embedding_model,
                embedding_dim=version.embedding_dim,
            )
            self._notify(progress, 100, "index already up to date", outcome.to_dict())
            return outcome

        version.status = IndexStatus.INDEXING.value
        version.error = None
        version.stats = {"tree_hash": tree_hash, "started_at": utcnow().isoformat()}
        session.flush()
        self._notify(progress, 12, "parsing sources", {"files": report.file_count})

        try:
            parsed = self._parse_all(report)
            symbols, parse_errors = self._collect_symbols(parsed)
            self._clear_version(session, version)
            symbol_ids = self._persist_symbols(session, repository, version, symbols)
            self._notify(progress, 45, "resolved relationships", {"symbols": len(symbol_ids)})
            edges = self._persist_edges(session, repository, version, parsed, symbol_ids)
            embedded, reused = self._embed_and_persist(
                session, repository, version, symbols, symbol_ids, progress=progress
            )
        except Exception as exc:
            session.rollback()
            self._mark_failed(session, repository, version, exc)
            raise

        tests = sum(1 for symbol in symbols if symbol.is_test)
        duration_ms = int((time.perf_counter() - started) * 1000)
        version.status = IndexStatus.INDEXED.value
        version.file_count = report.file_count
        version.symbol_count = len(symbols)
        version.test_count = tests
        version.embedding_count = embedded
        version.skipped_file_count = report.skipped_count
        version.index_duration_ms = duration_ms
        version.indexed_at = utcnow()
        version.embedding_provider = self.embedding_provider.name if self.embedding_provider else None
        version.embedding_model = self.embedding_provider.model if self.embedding_provider else None
        version.embedding_dim = self.embedding_provider.dim if self.embedding_provider else None
        version.stats = {
            "tree_hash": tree_hash,
            "skipped_reasons": report.skipped,
            "parse_errors": len(parse_errors),
            "scanned": report.scanned,
            "total_bytes": report.total_bytes,
            "edges": edges,
            "reused_embeddings": reused,
            "finished_at": utcnow().isoformat(),
        }

        repository.latest_version_id = version.id
        repository.index_status = IndexStatus.INDEXED.value
        repository.indexed_commit_sha = commit_sha
        repository.last_indexed_at = version.indexed_at
        repository.last_index_error = None
        session.flush()

        outcome = IndexOutcome(
            version_id=version.id,
            status=version.status,
            commit_sha=commit_sha,
            files=report.file_count,
            symbols=len(symbols),
            tests=tests,
            embeddings=embedded,
            reused_embeddings=reused,
            skipped_files=report.skipped_count,
            skipped_reasons=report.skipped,
            parse_errors=parse_errors,
            duration_ms=duration_ms,
            embedding_provider=version.embedding_provider,
            embedding_model=version.embedding_model,
            embedding_dim=version.embedding_dim,
        )
        self._notify(progress, 100, "index complete", outcome.to_dict())
        logger.info("indexed repository", extra=log_extra(**outcome.to_dict()))
        return outcome

    # ------------------------------------------------------------------ steps
    def _get_or_create_version(
        self, session: Session, repository: Repository, commit_sha: str, branch: str | None
    ) -> RepositoryVersion:
        existing = session.scalar(
            select(RepositoryVersion).where(
                RepositoryVersion.repository_id == repository.id,
                RepositoryVersion.commit_sha == commit_sha,
            )
        )
        if existing is not None:
            if branch:
                existing.branch = branch
            return existing
        version = RepositoryVersion(
            repository_id=repository.id,
            commit_sha=commit_sha,
            branch=branch,
            status=IndexStatus.INDEXING.value,
        )
        session.add(version)
        session.flush()
        return version

    def _clear_version(self, session: Session, version: RepositoryVersion) -> None:
        """Delete this version's derived rows so a forced reindex cannot duplicate."""
        for model in (CodeEmbedding, SymbolEdge, CodeSymbol):
            session.execute(delete(model).where(model.repository_version_id == version.id))
        session.flush()

    def _parse_all(self, report: DiscoveryReport) -> list[FileParseResult]:
        results: list[FileParseResult] = []
        for item in report.files:
            results.append(
                self.parser.parse(path=item.path, text=item.text, is_test_file=item.looks_like_test)
            )
        return results

    def _collect_symbols(
        self, parsed: Sequence[FileParseResult]
    ) -> tuple[list[ParsedSymbol], list[dict[str, Any]]]:
        symbols: list[ParsedSymbol] = []
        errors: list[dict[str, Any]] = []
        for result in parsed:
            if not result.ok:
                errors.append({"path": result.path, "error": result.error, "line": result.error_line})
                continue
            for symbol in result.symbols:
                if len(symbols) >= self.settings.index_max_symbols:
                    break
                symbols.append(symbol)
        if errors:
            logger.warning(
                "some files failed to parse", extra=log_extra(count=len(errors), sample=errors[:3])
            )
        return symbols, errors

    def _persist_symbols(
        self,
        session: Session,
        repository: Repository,
        version: RepositoryVersion,
        symbols: Sequence[ParsedSymbol],
    ) -> dict[str, str]:
        rows: list[dict[str, Any]] = []
        key_to_id: dict[str, str] = {}
        for symbol in symbols:
            symbol_id = new_id("sym")
            _, summary = build_symbol_document(symbol)
            key_to_id[f"{symbol.path}::{symbol.qualified_name}"] = symbol_id
            rows.append(
                {
                    "id": symbol_id,
                    "repository_id": repository.id,
                    "repository_version_id": version.id,
                    "path": symbol.path,
                    "language": "python",
                    "kind": symbol.kind.value,
                    "name": symbol.name,
                    "qualified_name": symbol.qualified_name,
                    "parent_qualified_name": symbol.parent_qualified_name,
                    "signature": symbol.signature,
                    "docstring": symbol.docstring,
                    "start_line": symbol.start_line,
                    "end_line": symbol.end_line,
                    "is_test": symbol.is_test,
                    "decorators": symbol.decorators,
                    "bases": symbol.bases,
                    "calls": symbol.calls,
                    "imports": symbol.imports,
                    "source": symbol.source,
                    "source_truncated": symbol.source_truncated,
                    "source_hash": symbol.source_hash,
                    "token_estimate": symbol.token_estimate,
                    # The embedded document is regenerated deterministically from
                    # ``source``; only ``content_hash`` is needed for reuse, so we
                    # store the short summary instead of duplicating the body.
                    "embedding_text": summary[:512],
                    "created_at": utcnow(),
                    "updated_at": utcnow(),
                }
            )
        for start in range(0, len(rows), SYMBOL_CHUNK):
            session.execute(insert(CodeSymbol), rows[start : start + SYMBOL_CHUNK])
        session.flush()
        return key_to_id

    def _persist_edges(
        self,
        session: Session,
        repository: Repository,
        version: RepositoryVersion,
        parsed: Sequence[FileParseResult],
        symbol_ids: dict[str, str],
    ) -> int:
        by_qualified: dict[str, str] = {}
        by_leaf: dict[str, list[str]] = {}
        for key, symbol_id in symbol_ids.items():
            _, qualified = key.split("::", 1)
            by_qualified[qualified] = symbol_id
            by_leaf.setdefault(qualified.split(".")[-1], []).append(symbol_id)

        rows: list[dict[str, Any]] = []
        for result in parsed:
            if not result.ok:
                continue
            for edge in result.edges:
                src_id = (
                    symbol_ids.get(f"{edge.path}::{edge.src_qualified_name}")
                    if edge.src_qualified_name
                    else None
                )
                dst_id: str | None = None
                resolved = False
                if edge.dst_name in by_qualified:
                    dst_id = by_qualified[edge.dst_name]
                    resolved = True
                else:
                    candidates = by_leaf.get(edge.dst_name, [])
                    if len(candidates) == 1:
                        dst_id = candidates[0]
                        resolved = True
                rows.append(
                    {
                        "id": f"edge_{content_fingerprint(version.id, edge.path, edge.kind, edge.dst_name, str(edge.line or 0), edge.src_qualified_name or '', length=24)}",
                        "repository_id": repository.id,
                        "repository_version_id": version.id,
                        "src_symbol_id": src_id,
                        "src_qualified_name": edge.src_qualified_name,
                        "dst_name": edge.dst_name[:512],
                        "dst_symbol_id": dst_id,
                        "kind": edge.kind,
                        "path": edge.path,
                        "line": edge.line,
                        "resolved": resolved,
                    }
                )
        seen: set[str] = set()
        unique_rows = []
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            unique_rows.append(row)

        for start in range(0, len(unique_rows), EDGE_CHUNK):
            session.execute(insert(SymbolEdge), unique_rows[start : start + EDGE_CHUNK])
        session.flush()
        return len(unique_rows)

    def _embed_and_persist(
        self,
        session: Session,
        repository: Repository,
        version: RepositoryVersion,
        symbols: Sequence[ParsedSymbol],
        symbol_ids: dict[str, str],
        *,
        progress: ProgressCallback | None,
    ) -> tuple[int, int]:
        provider = self.embedding_provider
        if provider is None:
            return 0, 0

        documents: list[tuple[ParsedSymbol, str, str]] = []
        for symbol in symbols:
            if symbol.kind == SymbolKind.MODULE and not symbol.source.strip():
                continue
            document, _ = build_symbol_document(symbol)
            content_hash = content_fingerprint(provider.name, provider.model, document, length=64)
            documents.append((symbol, document, content_hash))

        reusable = self._load_reusable_embeddings(
            session, repository.id, provider.name, provider.model, [item[2] for item in documents]
        )
        pending = [item for item in documents if item[2] not in reusable]
        self._notify(
            progress,
            55,
            "embedding symbols",
            {
                "total": len(documents),
                "reused": len(documents) - len(pending),
                "provider": provider.name,
                "model": provider.model,
                "is_neural": provider.is_neural,
            },
        )

        # Keyed by row id: two symbols whose embedded document is byte-identical
        # share one vector (the uniqueness constraint reflects that intent), so the
        # dedupe happens here rather than failing the insert.
        rows_by_id: dict[str, dict[str, Any]] = {}
        for symbol, _document, content_hash in documents:
            cached = reusable.get(content_hash)
            if cached is None:
                continue
            row = self._embedding_row(repository, version, symbol, symbol_ids, content_hash, provider, cached)
            rows_by_id.setdefault(str(row["id"]), row)

        batch_size = max(1, self.settings.index_embedding_batch_size)
        for start in range(0, len(pending), batch_size):
            chunk = pending[start : start + batch_size]
            batch = provider.embed_documents([item[1] for item in chunk])
            for (symbol, _document, content_hash), vector in zip(chunk, batch.vectors, strict=True):
                row = self._embedding_row(
                    repository, version, symbol, symbol_ids, content_hash, provider, vector
                )
                rows_by_id.setdefault(str(row["id"]), row)
            done = min(len(pending), start + batch_size)
            self._notify(
                progress,
                55 + int(35 * done / max(1, len(pending))),
                "embedding symbols",
                {"embedded": done, "pending": len(pending)},
            )

        rows = list(rows_by_id.values())
        for start in range(0, len(rows), SYMBOL_CHUNK):
            session.execute(insert(CodeEmbedding), rows[start : start + SYMBOL_CHUNK])
        session.flush()
        return len(rows), len(documents) - len(pending)

    def _embedding_row(
        self,
        repository: Repository,
        version: RepositoryVersion,
        symbol: ParsedSymbol,
        symbol_ids: dict[str, str],
        content_hash: str,
        provider: EmbeddingProvider,
        vector: Sequence[float],
    ) -> dict[str, Any]:
        return {
            # Version-scoped id: identical content in two snapshots shares one
            # vector lookup but must never collide on the primary key.
            "id": f"emb_{content_fingerprint(version.id, content_hash, length=24)}",
            "repository_id": repository.id,
            "repository_version_id": version.id,
            "symbol_id": symbol_ids.get(f"{symbol.path}::{symbol.qualified_name}"),
            "path": symbol.path,
            "kind": symbol.kind.value,
            "content_hash": content_hash,
            "provider": provider.name,
            "model": provider.model,
            "dim": len(vector),
            "is_neural": provider.is_neural,
            "embedding": list(vector),
            "token_estimate": symbol.token_estimate,
            "created_at": utcnow(),
        }

    def _load_reusable_embeddings(
        self,
        session: Session,
        repository_id: str,
        provider: str,
        model: str,
        content_hashes: Sequence[str],
    ) -> dict[str, list[float]]:
        """Fetch vectors already computed for identical content in this repository."""
        if not content_hashes:
            return {}
        reusable: dict[str, list[float]] = {}
        unique_hashes = list(dict.fromkeys(content_hashes))
        chunk_size = 400
        for start in range(0, len(unique_hashes), chunk_size):
            chunk = unique_hashes[start : start + chunk_size]
            statement = select(CodeEmbedding.content_hash, CodeEmbedding.embedding).where(
                CodeEmbedding.repository_id == repository_id,
                CodeEmbedding.provider == provider,
                CodeEmbedding.model == model,
                CodeEmbedding.content_hash.in_(chunk),
            )
            for content_hash, vector in session.execute(statement):
                if vector:
                    reusable[content_hash] = list(vector)
        return reusable

    # ----------------------------------------------------------------- helpers
    def _mark_failed(
        self, session: Session, repository: Repository, version: RepositoryVersion, exc: BaseException
    ) -> None:
        detail = describe_exception(exc)
        message = f"{detail['type']}: {detail['message']}"
        try:
            version.status = IndexStatus.FAILED.value
            version.error = message[:2000]
            repository.index_status = IndexStatus.FAILED.value
            repository.last_index_error = message[:2000]
            session.add(version)
            session.add(repository)
            session.commit()
        except Exception:  # pragma: no cover - the failure path must never mask the cause
            session.rollback()
        logger.error("indexing failed", extra=log_extra(repository=repository.full_name, **detail))

    @staticmethod
    def _notify(progress: ProgressCallback | None, percent: int, stage: str, data: dict[str, Any]) -> None:
        if progress is None:
            return
        progress(percent, stage, data)


def discover_files(
    root: Path, settings: Settings | None = None
) -> tuple[DiscoveryReport, list[DiscoveredFile]]:
    """Convenience helper used by the CLI and tests."""
    walker = RepositoryWalker(settings)
    report = walker.walk(root)
    return report, report.files


__all__ = ["IndexOutcome", "RepositoryIndexer", "discover_files"]
