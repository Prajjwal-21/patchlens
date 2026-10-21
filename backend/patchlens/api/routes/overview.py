"""Dashboard overview aggregates.

The overview is computed from persisted rows on every request. It deliberately
avoids presenting a missing denominator as zero: rates and latency percentiles are
``null`` until there is at least one observation, and the response carries the
definition of "open" so the UI can explain what it is counting.

Definitions
-----------
``open_findings``
    Non-rejected findings that have not received any human feedback. A feedback
    label is the only triage signal PatchLens stores, so this is "awaiting triage",
    not a resolved/open workflow state.
``review_success_rate``
    Succeeded / (succeeded + failed + canceled) terminal reviews. Running and
    queued reviews are excluded from the denominator, and reviews triggered by
    evaluation runs are excluded from every workspace aggregate.
``job_failure_rate``
    (failed + dead) / terminal jobs. A retried job counts once, by its final state.
``latency p50/p95``
    Review wall-clock duration for succeeded reviews only (at most the most recent
    5,000 rows, which is far above this product's local scale).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from patchlens.api.deps import read_session, require_auth
from patchlens.api.schemas import OverviewResponse
from patchlens.api.serializers import finding_out, job_out, review_out
from patchlens.core.enums import JobStatus, ReviewStatus, Severity, VerificationStatus
from patchlens.db.models.evaluation import EvalDataset, EvalRun
from patchlens.db.models.job import Job
from patchlens.db.models.repository import Repository
from patchlens.db.models.review import Finding, Review
from patchlens.db.models.testing import TestCandidate
from patchlens.services.evaluation.metrics import percentile

router = APIRouter(tags=["overview"], dependencies=[Depends(require_auth)])

#: Only recent rows are returned in the "recent activity" lists.
RECENT_LIMIT = 6
#: Bounds the in-process percentile calculation; only succeeded reviews are read.
MAX_LATENCY_SAMPLES = 5_000
#: Evaluation runs persist real review rows for scoring. They are experiments,
#: not workspace activity, so workspace aggregates exclude this trigger.
EVALUATION_TRIGGER = "evaluation"


def _status_counts(
    session: Session, column: Any, model: Any, *, filters: tuple[Any, ...] = ()
) -> dict[str, int]:
    statement = select(column, func.count()).select_from(model)
    if filters:
        statement = statement.where(*filters)
    return {str(status): int(count) for status, count in session.execute(statement.group_by(column))}


def _finding_status_counts(session: Session, column: Any) -> dict[str, int]:
    """Finding counts for workspace reviews; evaluation fixtures are excluded."""
    statement = (
        select(column, func.count())
        .select_from(Finding)
        .join(Review, Review.id == Finding.review_id)
        .where(Review.trigger != EVALUATION_TRIGGER)
        .group_by(column)
    )
    return {str(status): int(count) for status, count in session.execute(statement)}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


@router.get(
    "/overview",
    response_model=OverviewResponse,
    summary="Aggregated dashboard overview",
)
def overview(session: Session = Depends(read_session)) -> OverviewResponse:
    repository_total = int(session.scalar(select(func.count()).select_from(Repository)) or 0)
    repository_status = _status_counts(session, Repository.index_status, Repository)
    indexed_total = int(
        session.scalar(
            select(func.count()).select_from(Repository).where(Repository.index_status == "indexed")
        )
        or 0
    )

    review_filters = (Review.trigger != EVALUATION_TRIGGER,)
    review_status = _status_counts(session, Review.status, Review, filters=review_filters)
    terminal_reviews = sum(review_status.get(status, 0) for status in ("succeeded", "failed", "canceled"))
    review_success_rate = _rate(review_status.get(ReviewStatus.SUCCEEDED.value, 0), terminal_reviews)

    durations = list(
        session.scalars(
            select(Review.duration_ms)
            .where(
                Review.status == ReviewStatus.SUCCEEDED.value,
                Review.duration_ms.is_not(None),
                *review_filters,
            )
            .order_by(Review.created_at.desc())
            .limit(MAX_LATENCY_SAMPLES)
        )
    )
    duration_values = [float(value) for value in durations if value is not None]
    latency = {
        "p50": percentile(duration_values, 0.5),
        "p95": percentile(duration_values, 0.95),
        "sample_size": len(duration_values),
        "scope": "succeeded reviews only",
    }

    finding_status = _finding_status_counts(session, Finding.verification_status)
    finding_severity = _finding_status_counts(session, Finding.severity)
    finding_category = _finding_status_counts(session, Finding.category)
    non_rejected = int(
        session.scalar(
            select(func.count())
            .select_from(Finding)
            .join(Review, Review.id == Finding.review_id)
            .where(
                Finding.verification_status != VerificationStatus.REJECTED.value,
                Review.trigger != EVALUATION_TRIGGER,
            )
        )
        or 0
    )
    labelled_findings = int(
        session.scalar(
            select(func.count(func.distinct(Finding.id)))
            .select_from(Finding)
            .join(Review, Review.id == Finding.review_id)
            .join(Finding.feedback)
            .where(
                Finding.verification_status != VerificationStatus.REJECTED.value,
                Review.trigger != EVALUATION_TRIGGER,
            )
        )
        or 0
    )
    open_findings = max(0, non_rejected - labelled_findings)

    job_status = _status_counts(session, Job.status, Job)
    terminal_jobs = sum(job_status.get(status.value, 0) for status in JobStatus if status.is_terminal)
    failed_jobs = job_status.get(JobStatus.FAILED.value, 0) + job_status.get(JobStatus.DEAD.value, 0)

    candidate_status = _status_counts(session, TestCandidate.status, TestCandidate)
    evaluated_total = int(session.scalar(select(func.count()).select_from(EvalRun)) or 0)
    dataset_total = int(session.scalar(select(func.count()).select_from(EvalDataset)) or 0)
    latest_eval = session.scalar(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(1))

    recent_reviews = list(
        session.execute(
            select(Review, Repository.full_name)
            .join(Repository, Repository.id == Review.repository_id)
            .where(*review_filters)
            .order_by(Review.created_at.desc())
            .limit(RECENT_LIMIT)
        )
    )
    recent_findings = list(
        session.scalars(
            select(Finding)
            .join(Review, Review.id == Finding.review_id)
            .where(
                Finding.verification_status != VerificationStatus.REJECTED.value,
                Review.trigger != EVALUATION_TRIGGER,
            )
            .options(selectinload(Finding.feedback))
            .order_by(Finding.created_at.desc(), Finding.severity_rank.desc())
            .limit(RECENT_LIMIT)
        )
    )
    recent_jobs = list(session.scalars(select(Job).order_by(Job.created_at.desc()).limit(RECENT_LIMIT)))

    return OverviewResponse(
        repositories={
            "total": repository_total,
            "indexed": indexed_total,
            "by_status": repository_status,
        },
        reviews={
            "total": sum(review_status.values()),
            "by_status": review_status,
            "terminal": terminal_reviews,
            "success_rate": review_success_rate,
            "latency_ms": latency,
            "excludes": "reviews triggered by evaluation runs",
        },
        findings={
            "total": sum(finding_status.values()),
            "non_rejected": non_rejected,
            "open_findings": open_findings,
            "labelled_findings": labelled_findings,
            "by_severity": finding_severity,
            "by_verification_status": finding_status,
            "by_category": finding_category,
            "open_definition": ("non-rejected findings with no human feedback label yet (awaiting triage)"),
            "excludes": "findings from evaluation runs",
        },
        jobs={
            "by_status": job_status,
            "active": job_status.get(JobStatus.QUEUED.value, 0) + job_status.get(JobStatus.RUNNING.value, 0),
            "terminal": terminal_jobs,
            "failure_rate": _rate(failed_jobs, terminal_jobs),
        },
        test_intelligence={
            "candidates": sum(candidate_status.values()),
            "by_status": candidate_status,
            "executed": sum(candidate_status.get(status, 0) for status in ("passed", "failed", "error")),
            "execution_disabled": candidate_status.get("execution_disabled", 0),
        },
        evaluation={
            "datasets": dataset_total,
            "runs": evaluated_total,
            "latest_run_id": latest_eval.id if latest_eval is not None else None,
            "latest_run_status": latest_eval.status if latest_eval is not None else None,
            "latest_run_at": latest_eval.created_at if latest_eval is not None else None,
        },
        severity_order=[severity.value for severity in Severity],
        recent_reviews=[
            review_out(review, repository_full_name=full_name, include_metadata=False)
            for review, full_name in recent_reviews
        ],
        recent_findings=[finding_out(item) for item in recent_findings],
        recent_jobs=[job_out(item) for item in recent_jobs],
    )


__all__ = ["router"]
