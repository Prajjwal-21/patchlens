"""Test-intelligence tables: generated candidate tests and their real runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from patchlens.core.enums import TestCandidateStatus
from patchlens.db.base import Base, TimestampMixin, UTCDateTime, id_default


class TestCandidate(Base, TimestampMixin):
    """A generated regression test, and the (separate) record of it being run.

    ``status`` distinguishes *generation* from *verified execution*:

    * ``generated``          - produced by a provider, never executed;
    * ``execution_disabled`` - the sandbox is disabled, so it was provably not run;
    * ``passed``/``failed``  - only set by the restricted runner, with real output.
    """

    __tablename__ = "test_candidates"
    __table_args__ = (Index("ix_test_candidates_repo_status", "repository_id", "status"),)

    id_prefix = "tst"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    repository_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    review_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    finding_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_symbol: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    suggested_test_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mock")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="deterministic-mock")
    is_mock_provider: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TestCandidateStatus.GENERATED.value
    )
    #: Populated only by the restricted runner.
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sandbox_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sandbox_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    collected_tests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    execution_disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
