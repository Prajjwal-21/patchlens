"""Review creation, listing and detail endpoints.

Reviews run asynchronously through the durable queue by default. ``wait=true``
runs the pipeline in the request instead, which is convenient for small diffs and
for the CLI demo; the response says which happened.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
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
    FindingOut,
    ReviewCreateRequest,
    ReviewCreateResponse,
    ReviewOut,
)
from patchlens.api.serializers import finding_out, job_out, review_out
from patchlens.core.config import Settings
from patchlens.core.enums import ReviewStatus
from patchlens.core.errors import NotFoundError, ValidationError
from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.db.models.job import Job, JobEvent
from patchlens.db.models.repository import Repository
from patchlens.db.models.review import Finding, PullRequest, Review
from patchlens.jobs.queue import enqueue, make_dedupe_key
from patchlens.services.review.pipeline import ReviewPipeline, ReviewRequest

logger = get_logger(__name__)

router = APIRouter(prefix="/reviews", tags=["reviews"], dependencies=[Depends(require_auth)])


@router.post(
    "",
    response_model=ReviewCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a review for a diff",
)
def create_review(
    payload: ReviewCreateRequest,
    session: Session = Depends(write_session),
    settings: Settings = Depends(settings_dependency),
) -> ReviewCreateResponse:
    repository = load_repository(session, payload.repository_id)
    if payload.pull_request_id:
        pull_request = session.get(PullRequest, payload.pull_request_id)
        if pull_request is None:
            raise NotFoundError(f"pull request {payload.pull_request_id} was not found")
        if pull_request.repository_id != repository.id:
            raise ValidationError("pull request does not belong to the given repository")

    pipeline = ReviewPipeline(session, settings=settings)
    review, created = pipeline.get_or_create_review(
        ReviewRequest(
            repository=repository,
            diff_text=payload.diff_text,
            commit_sha=payload.commit_sha,
            base_sha=payload.base_sha,
            pull_request_id=payload.pull_request_id,
            trigger="api",
        )
    )
    if review.status == ReviewStatus.SUCCEEDED.value:
        session.commit()
        return ReviewCreateResponse(
            review=review_out(review, repository_full_name=repository.full_name),
            job_id=review.job_id,
            executed_synchronously=False,
            note="a review for this commit already succeeded; returning the stored result",
        )

    if payload.wait:
        review.job_id = _record_synchronous_run(
            session,
            repository_id=repository.id,
            commit_sha=payload.commit_sha,
            settings=settings,
        )
        session.commit()
        try:
            pipeline.run(
                ReviewRequest(
                    repository=repository,
                    diff_text=payload.diff_text,
                    commit_sha=payload.commit_sha,
                    base_sha=payload.base_sha,
                    pull_request_id=payload.pull_request_id,
                    trigger="api_sync",
                )
            )
        except Exception as exc:  # the review row records the failure; the job too
            detail = describe_exception(exc)
            _finish_synchronous_run(
                session,
                review.job_id,
                review_id=review.id,
                error=f"{detail['type']}: {detail['message']}",
            )
            session.commit()
            logger.error("synchronous review failed", extra=log_extra(review_id=review.id, **detail))
        else:
            _finish_synchronous_run(session, review.job_id, review_id=review.id, error=None)
            session.commit()
        session.refresh(review)
        return ReviewCreateResponse(
            review=review_out(review, repository_full_name=repository.full_name),
            job_id=review.job_id,
            executed_synchronously=True,
            note="executed in the request because wait=true; large diffs should use the async queue",
        )

    job_result = enqueue(
        session,
        kind="review_pull_request",
        payload={
            "repository_id": repository.id,
            "diff_text": payload.diff_text,
            "commit_sha": payload.commit_sha,
            "base_sha": payload.base_sha,
            "pull_request_id": payload.pull_request_id,
            "review_id": review.id,
            "trigger": "api",
        },
        dedupe_key=make_dedupe_key("review", repository.id, payload.commit_sha, review.strategy),
        priority=40,
        correlation_id=review.id,
        settings=settings,
    )
    review.job_id = job_result.job.id
    session.add(review)
    session.flush()
    session.add(
        JobEvent(
            job_id=job_result.job.id,
            stage="queued",
            message="review queued" if job_result.created else "review already queued",
            data={"review_id": review.id, "duplicate": job_result.duplicate},
        )
    )
    session.commit()
    logger.info(
        "review requested",
        extra=log_extra(
            review_id=review.id,
            repository=repository.full_name,
            duplicate=job_result.duplicate,
            created=created,
        ),
    )
    return ReviewCreateResponse(
        review=review_out(review, repository_full_name=repository.full_name),
        job_id=job_result.job.id,
        executed_synchronously=False,
        note=(
            "queued for the worker; poll /api/v1/reviews/{id} or /api/v1/jobs/{id}"
            if job_result.created
            else "an identical review job is already queued or running"
        ),
    )


@router.get("", response_model=list[ReviewOut], summary="List reviews")
def list_reviews(
    repository_id: str | None = Query(default=None),
    review_status: str | None = Query(default=None, alias="status"),
    pull_request_id: str | None = Query(default=None),
    session: Session = Depends(read_session),
    params: PaginationParams = Depends(pagination),
) -> list[ReviewOut]:
    statement = (
        select(Review, Repository.full_name)
        .join(Repository, Repository.id == Review.repository_id)
        .order_by(Review.created_at.desc())
        .limit(params.limit)
        .offset(params.offset)
    )
    if repository_id:
        statement = statement.where(Review.repository_id == repository_id)
    if review_status:
        statement = statement.where(Review.status == review_status)
    if pull_request_id:
        statement = statement.where(Review.pull_request_id == pull_request_id)
    return [
        review_out(review, repository_full_name=full_name) for review, full_name in session.execute(statement)
    ]


@router.get("/{review_id}", summary="Review detail")
def get_review(
    review_id: str,
    session: Session = Depends(read_session),
) -> dict[str, object]:
    review = session.get(Review, review_id)
    if review is None:
        raise NotFoundError(f"review {review_id} was not found")
    repository = session.get(Repository, review.repository_id)
    job = session.get(Job, review.job_id) if review.job_id else None
    severity_counts: dict[str, int] = {
        str(severity): int(count)
        for severity, count in session.execute(
            select(Finding.severity, func.count())
            .where(Finding.review_id == review.id, Finding.verification_status != "rejected")
            .group_by(Finding.severity)
        ).all()
    }
    status_counts: dict[str, int] = {
        str(status): int(count)
        for status, count in session.execute(
            select(Finding.verification_status, func.count())
            .where(Finding.review_id == review.id)
            .group_by(Finding.verification_status)
        ).all()
    }
    category_counts: dict[str, int] = {
        str(category): int(count)
        for category, count in session.execute(
            select(Finding.category, func.count())
            .where(Finding.review_id == review.id, Finding.verification_status != "rejected")
            .group_by(Finding.category)
        ).all()
    }
    return {
        "review": review_out(
            review,
            repository_full_name=repository.full_name if repository else None,
        ),
        "job": job_out(job, include_events=True) if job is not None else None,
        "finding_summary": {
            "by_severity": severity_counts,
            "by_verification_status": status_counts,
            "by_category": category_counts,
        },
    }


@router.get("/{review_id}/findings", response_model=list[FindingOut], summary="Findings for a review")
def review_findings(
    review_id: str,
    include_rejected: bool = Query(default=False),
    session: Session = Depends(read_session),
) -> list[FindingOut]:
    review = session.get(Review, review_id)
    if review is None:
        raise NotFoundError(f"review {review_id} was not found")
    statement = (
        select(Finding)
        .where(Finding.review_id == review_id)
        .order_by(Finding.severity_rank.desc(), Finding.path, Finding.start_line)
    )
    if not include_rejected:
        statement = statement.where(Finding.verification_status != "rejected")
    return [finding_out(item) for item in session.scalars(statement)]


def _record_synchronous_run(
    session: Session,
    *,
    repository_id: str,
    commit_sha: str,
    settings: Settings,
) -> str:
    """Create the job row for a synchronously executed review.

    The row is real (status RUNNING) so the audit trail is identical; it is moved
    to a terminal state by :func:`_finish_synchronous_run`.
    """
    result = enqueue(
        session,
        kind="review_pull_request",
        payload={"repository_id": repository_id, "commit_sha": commit_sha, "mode": "synchronous"},
        dedupe_key=None,
        priority=40,
        settings=settings,
    )
    job = result.job
    job.status = "running"
    job.stage = "running_in_request"
    job.worker_id = "api-sync"
    job.started_at = datetime.now(UTC)
    session.add(job)
    session.add(
        JobEvent(
            job_id=job.id,
            stage="running_in_request",
            message="executed synchronously by the API request (wait=true)",
            data={},
        )
    )
    session.flush()
    return job.id


def _finish_synchronous_run(session: Session, job_id: str, *, review_id: str, error: str | None) -> None:
    job = session.get(Job, job_id)
    if job is None:  # pragma: no cover - defensive
        return
    now = datetime.now(UTC)
    job.finished_at = now
    job.dedupe_slot = None
    job.lease_expires_at = None
    job.duration_ms = int((now - job.started_at).total_seconds() * 1000) if job.started_at else 0
    # The job row links back to the review it ran, so the audit trail is the same
    # whether the work happened in a request or in a worker.
    job.result = {"review_id": review_id, "mode": "synchronous", "in_request": True}
    if error:
        job.status = "failed"
        job.stage = "failed"
        job.error = error[:2_000]
        job.error_type = "ReviewFailed"
        session.add(JobEvent(job_id=job.id, level="error", stage="failed", message=error, data={}))
    else:
        job.status = "succeeded"
        job.stage = "succeeded"
        job.progress = 100
        session.add(
            JobEvent(job_id=job.id, stage="succeeded", message="synchronous review finished", data={})
        )
    session.add(job)


__all__ = ["router"]
