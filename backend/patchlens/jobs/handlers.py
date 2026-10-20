"""Job handlers: the only place job kinds are bound to application code."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from patchlens.core.config import Settings
from patchlens.core.enums import IndexStatus, ReviewStatus
from patchlens.core.errors import ProviderError
from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.db.models.job import Job
from patchlens.db.models.repository import Repository
from patchlens.services.ingestion.indexer import RepositoryIndexer
from patchlens.services.providers.factory import build_embedding_provider
from patchlens.services.review.pipeline import run_review

logger = get_logger(__name__)


class JobFailure(Exception):
    """A handler failure; ``retryable`` decides whether to back off and retry."""

    def __init__(self, message: str, *, retryable: bool = True, error_type: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.error_type = error_type or type(self).__name__


class NonRetryableJobError(JobFailure):
    def __init__(self, message: str, *, error_type: str | None = None) -> None:
        super().__init__(message, retryable=False, error_type=error_type or "NonRetryableJobError")


Handler = Callable[[Session, Job, Settings], dict[str, Any]]


def handle_index_repository(session: Session, job: Job, settings: Settings) -> dict[str, Any]:
    """Index a local checkout (or a pre-resolved checkout directory)."""
    payload = job.payload or {}
    repository = session.get(Repository, str(payload.get("repository_id") or ""))
    if repository is None:
        raise NonRetryableJobError("repository not found", error_type="RepositoryNotFound")

    root_value = payload.get("root") or repository.local_path
    if not root_value:
        raise NonRetryableJobError(
            "repository has no local checkout configured; remote cloning is not enabled",
            error_type="MissingCheckout",
        )
    root = Path(str(root_value)).expanduser()
    if not root.is_dir():
        raise NonRetryableJobError(f"checkout directory does not exist: {root}", error_type="MissingCheckout")

    commit_sha = str(payload.get("commit_sha") or repository.indexed_commit_sha or "local")
    branch = payload.get("branch")
    force = bool(payload.get("force"))

    repository.index_status = IndexStatus.INDEXING.value
    repository.last_index_error = None
    session.add(repository)
    session.commit()

    embedding_provider = None
    embedding_warning: str | None = None
    try:
        embedding_provider = build_embedding_provider(settings)
    except ProviderError as exc:
        # Indexing still produces BM25-searchable symbols; be explicit that
        # semantic retrieval is unavailable for this snapshot.
        embedding_warning = f"embeddings unavailable: {exc.message}"
        logger.warning(
            "indexing without embeddings",
            extra=log_extra(repository=repository.full_name, **describe_exception(exc)),
        )

    try:
        outcome = RepositoryIndexer(settings=settings, embedding_provider=embedding_provider).index(
            session,
            repository=repository,
            root=root,
            commit_sha=commit_sha,
            branch=str(branch) if branch else None,
            force=force,
        )
    except Exception as exc:
        session.rollback()
        repository = session.get(Repository, str(payload.get("repository_id") or ""))
        if repository is not None:
            repository.index_status = IndexStatus.FAILED.value
            repository.last_index_error = f"{type(exc).__name__}: {exc}"[:2_000]
            session.add(repository)
            session.commit()
        raise

    repository.index_status = IndexStatus.INDEXED.value
    repository.indexed_commit_sha = commit_sha
    repository.last_indexed_at = datetime.now(UTC)
    repository.last_index_error = None
    session.add(repository)
    session.commit()

    result = outcome.to_dict()
    if embedding_warning:
        result["embedding_warning"] = embedding_warning
    return result


def handle_review_pull_request(session: Session, job: Job, settings: Settings) -> dict[str, Any]:
    """Run the review pipeline for a supplied diff.

    Remote diff retrieval (GitHub API) is wired in by the GitHub integration; the
    payload must contain ``diff_text`` until then, and the handler says so rather
    than silently producing an empty review.
    """
    payload = job.payload or {}
    repository = session.get(Repository, str(payload.get("repository_id") or ""))
    if repository is None:
        raise NonRetryableJobError("repository not found", error_type="RepositoryNotFound")
    diff_text = payload.get("diff_text")
    if not isinstance(diff_text, str) or not diff_text.strip():
        raise NonRetryableJobError(
            "job payload has no diff_text and no GitHub diff fetcher is configured",
            error_type="MissingDiff",
        )
    commit_sha = str(payload.get("commit_sha") or "unknown")

    try:
        result = run_review(
            session,
            repository=repository,
            diff_text=diff_text,
            commit_sha=commit_sha,
            base_sha=payload.get("base_sha"),
            pull_request_id=payload.get("pull_request_id"),
            trigger=str(payload.get("trigger") or "job"),
            settings=settings,
        )
    except ProviderError as exc:
        raise JobFailure(exc.message, retryable=True, error_type=type(exc).__name__) from exc
    except Exception as exc:
        raise JobFailure(f"{type(exc).__name__}: {exc}", retryable=True) from exc

    review = result.review
    if review.status == ReviewStatus.FAILED.value:
        retryable = "provider failed" in (review.error or "")
        raise JobFailure(
            review.error or "review failed",
            retryable=retryable,
            error_type="ReviewFailed",
        )
    return {
        "review_id": review.id,
        "status": review.status,
        "findings": review.finding_count,
        "verified_findings": review.verified_finding_count,
        "is_mock_provider": review.is_mock_provider,
    }


HANDLERS: dict[str, Handler] = {
    "index_repository": handle_index_repository,
    "review_pull_request": handle_review_pull_request,
}


def handler_for(kind: str) -> Handler | None:
    return HANDLERS.get(kind)


__all__ = [
    "HANDLERS",
    "Handler",
    "JobFailure",
    "NonRetryableJobError",
    "handle_index_repository",
    "handle_review_pull_request",
    "handler_for",
]
