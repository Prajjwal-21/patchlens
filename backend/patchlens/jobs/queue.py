"""Durable job queue.

The database is the queue: a job is a row, and every state transition is a
transaction. Redis, when configured, only shortens the worker's polling delay -
stopping Redis cannot lose a job.

Duplicate protection is two-layered:

* ``dedupe_key`` is the immutable identity of the work (for audit and metrics);
* ``dedupe_slot`` is a unique nullable column that holds the claim only while the
  job is queued/running. It is cleared on a terminal state, so the same commit can
  legitimately be reviewed again after an earlier attempt completed or failed.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from patchlens.core.config import Settings, get_settings
from patchlens.core.enums import JobStatus
from patchlens.core.logging import get_logger, log_extra
from patchlens.db.models.job import Job, JobEvent
from patchlens.jobs.notify import notify_job_available

logger = get_logger(__name__)

_WAKEUP_KEY = "patchlens_job_wakeup"


@event.listens_for(Session, "after_commit")
def _publish_wakeup_after_commit(session: Session) -> None:
    """Tell idle workers about a job only after its row is committed."""
    settings = session.info.pop(_WAKEUP_KEY, None)
    if isinstance(settings, Settings):
        notify_job_available(settings)


@event.listens_for(Session, "after_rollback")
def _discard_wakeup_after_rollback(session: Session) -> None:
    session.info.pop(_WAKEUP_KEY, None)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class EnqueueResult:
    job: Job
    created: bool
    duplicate: bool = False

    @property
    def job_id(self) -> str:
        return self.job.id


def make_dedupe_key(*parts: object) -> str:
    """Stable key from arbitrary parts (bounded to the column width)."""
    joined = ":".join(str(part) for part in parts if part is not None)
    return joined[:240]


def enqueue(
    session: Session,
    *,
    kind: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    priority: int = 100,
    max_attempts: int | None = None,
    timeout_seconds: int | None = None,
    run_after: datetime | None = None,
    correlation_id: str | None = None,
    settings: Settings | None = None,
) -> EnqueueResult:
    """Add a job, or return the active job that already claims ``dedupe_key``."""
    settings = settings or get_settings()
    if dedupe_key:
        existing = session.scalar(select(Job).where(Job.dedupe_slot == dedupe_key))
        if existing is not None:
            return EnqueueResult(job=existing, created=False, duplicate=True)

    job = Job(
        id=f"job_{uuid.uuid4().hex[:24]}",
        kind=kind,
        status=JobStatus.QUEUED.value,
        priority=priority,
        dedupe_key=dedupe_key,
        dedupe_slot=dedupe_key,
        payload=payload,
        max_attempts=max_attempts or settings.job_max_attempts,
        run_after=run_after or utcnow(),
        timeout_seconds=timeout_seconds or settings.job_timeout_seconds,
        correlation_id=correlation_id,
        stage="queued",
    )
    try:
        with session.begin_nested():
            session.add(job)
    except IntegrityError:
        # Another transaction claimed the same dedupe slot between our SELECT and
        # INSERT. The savepoint rolled back only our insert; return the winner.
        existing = session.scalar(select(Job).where(Job.dedupe_slot == dedupe_key)) if dedupe_key else None
        if existing is None:  # pragma: no cover - only reachable on a non-dedupe conflict
            raise
        return EnqueueResult(job=existing, created=False, duplicate=True)

    session.add(
        JobEvent(
            job_id=job.id, stage="queued", message="job queued", data={"kind": kind, "priority": priority}
        )
    )
    session.flush()
    session.info[_WAKEUP_KEY] = settings
    logger.info("job enqueued", extra=log_extra(job_id=job.id, kind=kind, priority=priority))
    return EnqueueResult(job=job, created=True)


def claim_next(
    session: Session,
    *,
    worker_id: str,
    kinds: list[str] | None = None,
    lease_seconds: int = 900,
) -> Job | None:
    """Atomically claim the next eligible job, or return ``None``."""
    now = utcnow()
    statement = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED.value, Job.run_after <= now)
        .order_by(Job.priority.asc(), Job.created_at.asc())
        .limit(1)
    )
    if kinds:
        statement = statement.where(Job.kind.in_(kinds))
    candidate = session.scalar(statement)
    if candidate is None:
        return None

    claim_result = session.execute(
        update(Job)
        .where(Job.id == candidate.id, Job.status == JobStatus.QUEUED.value)
        .values(
            status=JobStatus.RUNNING.value,
            worker_id=worker_id,
            attempts=Job.attempts + 1,
            started_at=func.coalesce(Job.started_at, now),
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            stage="starting",
            progress=0,
            error=None,
            error_type=None,
        )
    )
    # DML results always expose ``rowcount``; the stub type does not.
    if claim_result.rowcount != 1:  # type: ignore[attr-defined]
        # another worker won the race
        session.rollback()
        return None
    session.refresh(candidate)
    session.add(
        JobEvent(
            job_id=candidate.id,
            stage="starting",
            message=f"claimed by worker {worker_id}",
            data={"attempt": candidate.attempts, "max_attempts": candidate.max_attempts},
        )
    )
    session.commit()
    logger.info(
        "job claimed",
        extra=log_extra(
            job_id=candidate.id, kind=candidate.kind, attempt=candidate.attempts, worker=worker_id
        ),
    )
    return candidate


def heartbeat(session: Session, job: Job, *, lease_seconds: int = 900, stage: str | None = None) -> None:
    now = utcnow()
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    if stage:
        job.stage = stage
    session.add(job)
    session.commit()


def complete(session: Session, job: Job, *, result: dict[str, Any] | None = None) -> Job:
    now = utcnow()
    job.status = JobStatus.SUCCEEDED.value
    job.stage = "succeeded"
    job.progress = 100
    job.result = result or {}
    job.error = None
    job.error_type = None
    job.dedupe_slot = None  # release the active claim
    job.lease_expires_at = None
    job.finished_at = now
    job.duration_ms = _duration_ms(job.started_at, now)
    session.add(job)
    session.add(JobEvent(job_id=job.id, stage="succeeded", message="job succeeded", data=result or {}))
    session.commit()
    logger.info("job succeeded", extra=log_extra(job_id=job.id, kind=job.kind, duration_ms=job.duration_ms))
    return job


def fail(
    session: Session,
    job: Job,
    *,
    message: str,
    error_type: str = "JobError",
    retryable: bool = True,
    settings: Settings | None = None,
) -> Job:
    """Record a failure and either schedule a backoff retry or terminate."""
    settings = settings or get_settings()
    now = utcnow()
    job.error = message[:2_000]
    job.error_type = error_type[:128]
    job.lease_expires_at = None
    job.worker_id = None
    job.heartbeat_at = now

    if retryable and job.attempts < job.max_attempts:
        delay = min(
            settings.job_backoff_base_seconds * math.pow(2, max(job.attempts - 1, 0)),
            settings.job_backoff_max_seconds,
        )
        job.status = JobStatus.QUEUED.value
        job.stage = "retrying"
        job.run_after = now + timedelta(seconds=delay)
        session.add(job)
        session.add(
            JobEvent(
                job_id=job.id,
                level="warning",
                stage="retry_scheduled",
                message=f"{message} (retry in {delay:.1f}s)",
                data={"attempt": job.attempts, "max_attempts": job.max_attempts, "delay_seconds": delay},
            )
        )
        session.commit()
        logger.warning(
            "job will retry", extra=log_extra(job_id=job.id, attempt=job.attempts, delay_seconds=delay)
        )
        return job

    exhausted = retryable and job.attempts >= job.max_attempts
    job.status = JobStatus.DEAD.value if exhausted else JobStatus.FAILED.value
    job.stage = "dead" if exhausted else "failed"
    job.dedupe_slot = None
    job.finished_at = now
    job.duration_ms = _duration_ms(job.started_at, now)
    session.add(job)
    session.add(
        JobEvent(
            job_id=job.id,
            level="error",
            stage=job.stage,
            message=message,
            data={"attempt": job.attempts, "max_attempts": job.max_attempts, "retryable": retryable},
        )
    )
    session.commit()
    logger.error(
        "job failed", extra=log_extra(job_id=job.id, kind=job.kind, status=job.status, error=message)
    )
    return job


def cancel(session: Session, job: Job, *, reason: str = "cancelled by request") -> Job:
    job.status = JobStatus.CANCELED.value
    job.stage = "canceled"
    job.dedupe_slot = None
    job.lease_expires_at = None
    job.finished_at = utcnow()
    job.error = reason[:2_000]
    session.add(job)
    session.add(JobEvent(job_id=job.id, level="warning", stage="canceled", message=reason))
    session.commit()
    return job


def recover_expired_leases(session: Session, *, settings: Settings | None = None) -> int:
    """Return jobs whose worker died to the queue (or mark them dead)."""
    settings = settings or get_settings()
    now = utcnow()
    expired = list(
        session.scalars(
            select(Job).where(
                Job.status == JobStatus.RUNNING.value,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at < now,
            )
        )
    )
    for job in expired:
        job.worker_id = None
        job.lease_expires_at = None
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.DEAD.value
            job.stage = "dead"
            job.dedupe_slot = None
            job.finished_at = now
            job.error = job.error or "worker lease expired after the final attempt"
            job.error_type = job.error_type or "LeaseExpired"
        else:
            job.status = JobStatus.QUEUED.value
            job.stage = "requeued"
            job.run_after = now
        session.add(job)
        session.add(
            JobEvent(
                job_id=job.id,
                level="warning",
                stage="lease_expired",
                message="worker lease expired; job recovered",
                data={"attempt": job.attempts, "status": job.status},
            )
        )
    if expired:
        session.commit()
        logger.warning("recovered expired jobs", extra=log_extra(count=len(expired)))
    return len(expired)


def job_to_dict(job: Job, *, include_events: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "priority": job.priority,
        "dedupe_key": job.dedupe_key,
        "error": job.error,
        "error_type": job.error_type,
        "result": job.result,
        "run_after": job.run_after.isoformat() if job.run_after else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "duration_ms": job.duration_ms,
        "worker_id": job.worker_id,
    }
    if include_events:
        payload["events"] = [
            {
                "level": event.level,
                "stage": event.stage,
                "message": event.message,
                "data": event.data,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in job.events
        ]
    return payload


def _duration_ms(started_at: datetime | None, finished_at: datetime) -> int:
    if started_at is None:
        return 0
    return max(0, int((finished_at - started_at).total_seconds() * 1000))


__all__ = [
    "EnqueueResult",
    "cancel",
    "claim_next",
    "complete",
    "enqueue",
    "fail",
    "heartbeat",
    "job_to_dict",
    "make_dedupe_key",
    "recover_expired_leases",
    "utcnow",
]
