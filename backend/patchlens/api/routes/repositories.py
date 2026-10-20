"""Repository registration, inspection and indexing endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from patchlens.api.deps import (
    PaginationParams,
    load_repository,
    pagination,
    read_session,
    require_auth,
    settings_dependency,
    write_session,
)
from patchlens.api.schemas import (
    DeleteResponse,
    IndexRequest,
    JobOut,
    RepositoryCreateRequest,
    RepositoryOut,
    RepositoryVersionOut,
)
from patchlens.api.serializers import job_out, repository_out, review_out, version_out
from patchlens.core.config import Settings
from patchlens.core.errors import ConflictError, ForbiddenError, ValidationError
from patchlens.core.logging import get_logger, log_extra
from patchlens.db.models.repository import Repository, RepositoryVersion
from patchlens.db.models.review import Finding, Review
from patchlens.jobs.queue import enqueue, make_dedupe_key

logger = get_logger(__name__)

router = APIRouter(prefix="/repositories", tags=["repositories"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[RepositoryOut], summary="List repositories")
def list_repositories(
    session: Session = Depends(read_session),
    params: PaginationParams = Depends(pagination),
) -> list[RepositoryOut]:
    repositories = list(
        session.scalars(
            select(Repository).order_by(Repository.full_name).limit(params.limit).offset(params.offset)
        )
    )
    latest_ids = [item.latest_version_id for item in repositories if item.latest_version_id]
    versions: dict[str, RepositoryVersion] = {}
    if latest_ids:
        versions = {
            version.id: version
            for version in session.scalars(
                select(RepositoryVersion).where(RepositoryVersion.id.in_(latest_ids))
            )
        }
    return [repository_out(item, latest=versions.get(item.latest_version_id or "")) for item in repositories]


@router.post(
    "",
    response_model=RepositoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a local repository checkout",
)
def create_repository(
    payload: RepositoryCreateRequest,
    session: Session = Depends(write_session),
    settings: Settings = Depends(settings_dependency),
) -> RepositoryOut:
    if settings.is_production:
        raise ForbiddenError(
            "registering local checkouts is disabled in production; install the GitHub App instead"
        )
    root = Path(payload.local_path).expanduser()
    if not root.is_dir():
        raise ValidationError(
            "local_path must point at an existing directory",
            details={"local_path": payload.local_path},
        )
    full_name = Repository.make_full_name(payload.owner, payload.name)
    existing = session.scalar(select(Repository).where(Repository.full_name == full_name))
    if existing is not None:
        raise ConflictError(f"repository {full_name} is already registered", details={"id": existing.id})
    repository = Repository(
        provider=payload.provider,
        owner=payload.owner,
        name=payload.name,
        full_name=full_name,
        default_branch=payload.default_branch,
        local_path=str(root.resolve()),
    )
    session.add(repository)
    session.flush()
    logger.info("repository registered", extra=log_extra(repository=full_name, repository_id=repository.id))
    return repository_out(repository)


@router.get("/{repository_id}", summary="Repository detail")
def get_repository(
    repository_id: str,
    session: Session = Depends(read_session),
) -> dict[str, object]:
    repository = load_repository(session, repository_id)
    latest = (
        session.get(RepositoryVersion, repository.latest_version_id) if repository.latest_version_id else None
    )
    versions = list(
        session.scalars(
            select(RepositoryVersion)
            .where(RepositoryVersion.repository_id == repository.id)
            .order_by(RepositoryVersion.created_at.desc())
            .limit(20)
        )
    )
    reviews = list(
        session.scalars(
            select(Review)
            .where(Review.repository_id == repository.id)
            .order_by(Review.created_at.desc())
            .limit(5)
        )
    )
    finding_counts: dict[str, int] = {
        str(status): int(count)
        for status, count in session.execute(
            select(Finding.verification_status, func.count())
            .where(Finding.repository_id == repository.id)
            .group_by(Finding.verification_status)
        ).all()
    }
    return {
        "repository": repository_out(repository, latest=latest),
        "versions": [version_out(item) for item in versions],
        "recent_reviews": [review_out(item, include_metadata=False) for item in reviews],
        "finding_counts": finding_counts,
    }


@router.post("/{repository_id}/index", response_model=JobOut, summary="Queue an index run")
def index_repository(
    repository_id: str,
    payload: IndexRequest,
    session: Session = Depends(write_session),
    settings: Settings = Depends(settings_dependency),
) -> JobOut:
    repository = load_repository(session, repository_id)
    if not repository.local_path:
        raise ConflictError(
            "repository has no local checkout; remote cloning is not enabled in this deployment"
        )
    if not repository.indexing_enabled:
        raise ConflictError("indexing is disabled for this repository")
    commit_sha = payload.commit_sha or repository.indexed_commit_sha or "local"
    result = enqueue(
        session,
        kind="index_repository",
        payload={
            "repository_id": repository.id,
            "commit_sha": commit_sha,
            "branch": payload.branch,
            "force": payload.force,
        },
        dedupe_key=make_dedupe_key(
            "index", repository.id, commit_sha, "force" if payload.force else "incremental"
        ),
        priority=50,
        timeout_seconds=max(settings.job_timeout_seconds, 600),
        settings=settings,
    )
    session.commit()
    logger.info(
        "index job requested",
        extra=log_extra(repository_id=repository.id, job_id=result.job.id, duplicate=result.duplicate),
    )
    return job_out(result.job, include_events=True)


@router.delete("/{repository_id}", response_model=DeleteResponse, summary="Delete a repository and its data")
def delete_repository(
    repository_id: str,
    session: Session = Depends(write_session),
) -> DeleteResponse:
    repository = load_repository(session, repository_id)
    full_name = repository.full_name
    session.delete(repository)
    session.flush()
    logger.warning("repository deleted", extra=log_extra(repository=full_name, repository_id=repository_id))
    return DeleteResponse(deleted=True, detail=f"deleted {full_name} and all indexed/review data")


@router.get("/{repository_id}/versions", response_model=list[RepositoryVersionOut], summary="Index snapshots")
def list_versions(
    repository_id: str,
    session: Session = Depends(read_session),
    params: PaginationParams = Depends(pagination),
) -> list[RepositoryVersionOut]:
    repository = load_repository(session, repository_id)
    versions = session.scalars(
        select(RepositoryVersion)
        .where(RepositoryVersion.repository_id == repository.id)
        .order_by(RepositoryVersion.created_at.desc())
        .limit(params.limit)
        .offset(params.offset)
    )
    return [version_out(item) for item in versions]


__all__ = ["router"]
