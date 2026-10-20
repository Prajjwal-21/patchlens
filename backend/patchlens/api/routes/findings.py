"""Finding inspection and human feedback endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from patchlens.api.deps import (
    PaginationParams,
    pagination,
    read_session,
    require_auth,
    write_session,
)
from patchlens.api.schemas import (
    FeedbackAggregateResponse,
    FeedbackCreateRequest,
    FeedbackListResponse,
    FindingFeedbackOut,
    FindingListResponse,
    FindingOut,
    Pagination,
)
from patchlens.api.serializers import feedback_out, finding_out
from patchlens.core.enums import FindingCategory, Severity, VerificationStatus
from patchlens.core.errors import NotFoundError, ValidationError
from patchlens.core.logging import get_logger, log_extra
from patchlens.db.models.review import Finding, FindingFeedback
from patchlens.services.review.checks import catalogue

logger = get_logger(__name__)

router = APIRouter(prefix="/findings", tags=["findings"], dependencies=[Depends(require_auth)])


@router.get("", response_model=FindingListResponse, summary="List findings")
def list_findings(
    repository_id: str | None = Query(default=None),
    review_id: str | None = Query(default=None),
    path: str | None = Query(default=None),
    rule_id: str | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    category: FindingCategory | None = Query(default=None),
    verification_status: VerificationStatus | None = Query(default=None),
    detector: str | None = Query(default=None),
    include_rejected: bool = Query(default=False),
    session: Session = Depends(read_session),
    params: PaginationParams = Depends(pagination),
) -> FindingListResponse:
    filters = []
    if repository_id:
        filters.append(Finding.repository_id == repository_id)
    if review_id:
        filters.append(Finding.review_id == review_id)
    if path:
        filters.append(Finding.path == path)
    if rule_id:
        filters.append(Finding.rule_id == rule_id)
    if severity is not None:
        filters.append(Finding.severity == severity.value)
    if category is not None:
        filters.append(Finding.category == category.value)
    if verification_status is not None:
        filters.append(Finding.verification_status == verification_status.value)
    elif not include_rejected:
        filters.append(Finding.verification_status != VerificationStatus.REJECTED.value)
    if detector:
        filters.append(Finding.detector == detector)

    total = int(session.scalar(select(func.count()).select_from(Finding).where(*filters)) or 0)
    statement = (
        select(Finding)
        .where(*filters)
        .options(selectinload(Finding.feedback))
        .order_by(Finding.created_at.desc(), Finding.severity_rank.desc())
        .limit(params.limit)
        .offset(params.offset)
    )
    items = [finding_out(item) for item in session.scalars(statement)]
    return FindingListResponse(
        items=items,
        pagination=Pagination(
            total=total, limit=params.limit, offset=params.offset, has_more=params.offset + len(items) < total
        ),
    )


@router.get("/feedback/aggregate", response_model=FeedbackAggregateResponse, summary="Feedback aggregate")
def feedback_aggregate(
    repository_id: str | None = Query(default=None),
    session: Session = Depends(read_session),
) -> FeedbackAggregateResponse:
    """Label counts for the Evaluation page.

    This is descriptive only: labels come from people, and nothing here is treated
    as ground truth for training.
    """
    finding_filters = [Finding.verification_status != VerificationStatus.REJECTED.value]
    if repository_id:
        finding_filters.append(Finding.repository_id == repository_id)

    total_findings = int(
        session.scalar(select(func.count()).select_from(Finding).where(*finding_filters)) or 0
    )
    label_rows = session.execute(
        select(FindingFeedback.label, func.count())
        .join(Finding, Finding.id == FindingFeedback.finding_id)
        .where(*finding_filters)
        .group_by(FindingFeedback.label)
    ).all()
    labels = {str(label): int(count) for label, count in label_rows}
    labelled_findings = int(
        session.scalar(
            select(func.count(func.distinct(FindingFeedback.finding_id)))
            .select_from(FindingFeedback)
            .join(Finding, Finding.id == FindingFeedback.finding_id)
            .where(*finding_filters)
        )
        or 0
    )

    by_category: dict[str, dict[str, int]] = {}
    for category, label, count in session.execute(
        select(Finding.category, FindingFeedback.label, func.count())
        .join(FindingFeedback, FindingFeedback.finding_id == Finding.id)
        .where(*finding_filters)
        .group_by(Finding.category, FindingFeedback.label)
    ).all():
        by_category.setdefault(str(category), {})[str(label)] = int(count)

    by_status: dict[str, dict[str, int]] = {}
    for verification, label, count in session.execute(
        select(Finding.verification_status, FindingFeedback.label, func.count())
        .join(FindingFeedback, FindingFeedback.finding_id == Finding.id)
        .where(*finding_filters)
        .group_by(Finding.verification_status, FindingFeedback.label)
    ).all():
        by_status.setdefault(str(verification), {})[str(label)] = int(count)

    return FeedbackAggregateResponse(
        total_findings=total_findings,
        labelled_findings=labelled_findings,
        labels=labels,
        by_category=by_category,
        by_verification_status=by_status,
        note=(
            "Counts of human labels. Not a model metric: precision/recall come from the "
            "evaluation dataset under /api/v1/evaluation."
        ),
    )


@router.get("/{finding_id}", response_model=FindingOut, summary="Finding detail")
def get_finding(finding_id: str, session: Session = Depends(read_session)) -> FindingOut:
    finding = session.get(Finding, finding_id, options=[selectinload(Finding.feedback)])
    if finding is None:
        raise NotFoundError(f"finding {finding_id} was not found")
    return finding_out(finding)


@router.get("/{finding_id}/feedback", response_model=FeedbackListResponse, summary="Feedback for a finding")
def list_feedback(finding_id: str, session: Session = Depends(read_session)) -> FeedbackListResponse:
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise NotFoundError(f"finding {finding_id} was not found")
    items = [feedback_out(item) for item in finding.feedback]
    counts: dict[str, int] = {}
    for item in finding.feedback:
        counts[item.label] = counts.get(item.label, 0) + 1
    return FeedbackListResponse(
        items=items,
        aggregate={
            "finding_id": finding.id,
            "labels": counts,
            "labelled": bool(finding.feedback),
            "note": "labels are stored verbatim; the newest one is shown as the effective label",
        },
    )


@router.post(
    "/{finding_id}/feedback",
    response_model=FindingFeedbackOut,
    status_code=status.HTTP_201_CREATED,
    summary="Label a finding",
)
def create_feedback(
    finding_id: str,
    payload: FeedbackCreateRequest,
    session: Session = Depends(write_session),
) -> FindingFeedbackOut:
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise NotFoundError(f"finding {finding_id} was not found")
    if finding.verification_status == VerificationStatus.REJECTED.value and finding.rejection_reason is None:
        raise ValidationError("rejected findings without a recorded reason cannot be labelled")
    if payload.source == "import" and payload.comment is None:
        raise ValidationError("imported labels must carry a comment explaining their provenance")
    feedback = FindingFeedback(
        finding_id=finding.id,
        label=payload.label.value,
        comment=payload.comment,
        reviewer=payload.reviewer,
        source=payload.source,
    )
    session.add(feedback)
    session.flush()
    logger.info(
        "finding labelled",
        extra=log_extra(finding_id=finding.id, label=payload.label.value, reviewer=payload.reviewer),
    )
    return feedback_out(feedback)


@router.get("/{finding_id}/trace", summary="Why this finding exists")
def finding_trace(finding_id: str, session: Session = Depends(read_session)) -> dict[str, Any]:
    """Everything that produced the finding: rule/check, retrieval, model, validation."""
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise NotFoundError(f"finding {finding_id} was not found")
    rule = next((entry for entry in catalogue() if entry["rule_id"] == finding.rule_id), None)
    return {
        "finding_id": finding.id,
        "rule_id": finding.rule_id,
        "rule": rule,
        "rule_is_deterministic": rule is not None,
        "detector": finding.detector,
        "verification_status": finding.verification_status,
        "confidence": finding.confidence,
        "confidence_basis": finding.confidence_basis,
        "rejection_reason": finding.rejection_reason,
        "dedupe_of_finding_id": finding.dedupe_of_finding_id,
        "evidence": finding.evidence,
        "retrieval_evidence": finding.retrieval_evidence,
        "provider": finding.provider,
        "model": finding.model,
        "path": finding.path,
        "start_line": finding.start_line,
        "end_line": finding.end_line,
        "line_verified": finding.line_verified,
        "not_executed": _not_executed_notes(finding),
        "note": (
            "evidence is recorded at detection time: diff lines, symbols and (for model findings) the "
            "provider that produced them"
        ),
    }


def _not_executed_notes(finding: Finding) -> list[str]:
    """State plainly what this finding does *not* prove."""
    notes: list[str] = []
    if finding.detector == "llm":
        notes.append(
            "the model's explanation was checked against the diff location but not reproduced or executed"
        )
    if finding.rule_id == "changed-code-without-related-test":
        notes.append(
            "test association is structural; a test may still cover this code through dispatch or fixtures"
        )
    if finding.verification_status == VerificationStatus.NEEDS_MANUAL_VERIFICATION.value:
        notes.append("a human must confirm whether the pattern has real impact here")
    if finding.rejection_reason:
        notes.append(f"rejected before publication: {finding.rejection_reason}")
    if not notes:
        notes.append("no test or execution was performed for this finding; verification is static")
    return notes


__all__ = ["router"]
