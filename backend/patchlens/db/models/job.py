"""Durable job queue and job event tables.

The database is the source of truth for job state. Redis (when configured) only
provides low-latency wake-ups, so losing Redis never loses work.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from patchlens.core.enums import JobStatus
from patchlens.db.base import Base, UTCDateTime, id_default


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Job(Base):
    """A persistent unit of asynchronous work.

    Duplicate-job protection uses two columns:

    * ``dedupe_key``    - the stable identity of the work (audit trail, never cleared);
    * ``dedupe_slot``   - the *active* claim, unique across the table and set back
      to ``NULL`` when the job reaches a terminal state.

    A unique index on a nullable column is portable across PostgreSQL and SQLite
    (NULLs are distinct), so the same commit can legitimately be reviewed again
    after an earlier review finished or failed.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_dedupe_slot", "dedupe_slot", unique=True),
        Index("ix_jobs_kind_status", "kind", "status"),
        Index("ix_jobs_created", "created_at"),
    )

    id_prefix = "job"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=JobStatus.QUEUED.value)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dedupe_slot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    #: Not eligible before this time (implements exponential backoff).
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    #: Set while running so crashed workers can be detected and recovered.
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: Correlation id so repeated deliveries of one GitHub event share a job.
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.created_at.asc()"
    )

    @property
    def is_terminal(self) -> bool:
        return JobStatus(self.status).is_terminal

    @property
    def is_active(self) -> bool:
        return self.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}


class JobEvent(Base):
    """Append-only progress/audit trail for a job (surfaced in the UI)."""

    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_created", "job_id", "created_at"),)

    id_prefix = "jev"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)

    job: Mapped[Job] = relationship(back_populates="events")
