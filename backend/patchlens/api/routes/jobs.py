"""Job inspection and cancellation endpoints (used for status polling)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from patchlens.api.deps import (
    PaginationParams,
    pagination,
    read_session,
    require_auth,
    write_session,
)
from patchlens.api.schemas import JobListResponse, JobOut, OkResponse, Pagination
from patchlens.api.serializers import job_out
from patchlens.core.enums import JobStatus
from patchlens.core.errors import ConflictError, NotFoundError
from patchlens.db.models.job import Job
from patchlens.jobs.queue import cancel

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_auth)])


@router.get("", response_model=JobListResponse, summary="List jobs")
def list_jobs(
    job_status: str | None = Query(default=None, alias="status"),
    kind: str | None = Query(default=None),
    session: Session = Depends(read_session),
    params: PaginationParams = Depends(pagination),
) -> JobListResponse:
    filters = []
    if job_status:
        filters.append(Job.status == job_status)
    if kind:
        filters.append(Job.kind == kind)
    total = int(session.scalar(select(func.count()).select_from(Job).where(*filters)) or 0)
    items = session.scalars(
        select(Job).where(*filters).order_by(Job.created_at.desc()).limit(params.limit).offset(params.offset)
    )
    return JobListResponse(
        items=[job_out(item) for item in items],
        pagination=Pagination(
            total=total,
            limit=params.limit,
            offset=params.offset,
            has_more=params.offset + params.limit < total,
        ),
    )


@router.get("/{job_id}", response_model=JobOut, summary="Job detail with events")
def get_job(job_id: str, session: Session = Depends(read_session)) -> JobOut:
    job = session.get(Job, job_id, options=[selectinload(Job.events)])
    if job is None:
        raise NotFoundError(f"job {job_id} was not found")
    return job_out(job, include_events=True)


@router.post("/{job_id}/cancel", response_model=OkResponse, summary="Cancel a queued job")
def cancel_job(job_id: str, session: Session = Depends(write_session)) -> OkResponse:
    job = session.get(Job, job_id, options=[selectinload(Job.events)])
    if job is None:
        raise NotFoundError(f"job {job_id} was not found")
    if JobStatus(job.status).is_terminal:
        raise ConflictError(f"job is already {job.status}")
    if job.status == JobStatus.RUNNING.value:
        raise ConflictError(
            "a running job cannot be cancelled from the API; the worker must observe a cooperative stop"
        )
    cancel(session, job)
    return OkResponse(detail=f"job {job_id} cancelled before it started")


__all__ = ["router"]
