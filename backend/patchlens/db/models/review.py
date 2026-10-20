"""Pull request, review, finding and feedback tables."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from patchlens.core.enums import (
    ConfidenceBasis,
    FeedbackLabel,
    PublishState,
    ReviewStatus,
    Severity,
    VerificationStatus,
)
from patchlens.db.base import Base, TimestampMixin, UTCDateTime, id_default


class PullRequest(Base, TimestampMixin):
    """A pull request mirrored from GitHub (or a local fixture in demo mode)."""

    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repository_id", "number", name="uq_pull_requests_repo_number"),)

    id_prefix = "pr"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    html_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    base_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    head_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``github`` or ``fixture`` (local demo workflow).
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="github")
    changed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    repository: Mapped[Any] = relationship("Repository")
    reviews: Mapped[list[Review]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan", order_by="Review.created_at.desc()"
    )


class Review(Base, TimestampMixin):
    """One end-to-end review run of a PR revision at a specific commit."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("pull_request_id", "commit_sha", "strategy", name="uq_reviews_identity"),
        Index("ix_reviews_status_created", "status", "created_at"),
    )

    id_prefix = "rvw"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pull_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=True, index=True
    )
    repository_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("repository_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ReviewStatus.QUEUED.value)
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: ``static``, ``bm25``, ``semantic``, ``hybrid``, ``hybrid_rerank``
    strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="hybrid")
    #: ``mock`` | ``heuristic`` | ``openai_compatible`` — never inferred from opencode.
    llm_provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mock")
    llm_model: Mapped[str] = mapped_column(String(128), nullable=False, default="deterministic-mock")
    #: True when findings came from the non-neural/deterministic development
    #: provider. Surfaced in the UI so mock output is never mistaken for AI output.
    is_mock_provider: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    diff_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    changed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retrieved_context_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verified_finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_test_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: Publication outcome is a review-level fact as well as a per-finding one: a
    #: review with zero eligible findings still needs an auditable "skipped" state.
    publish_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PublishState.NOT_PUBLISHED.value
    )
    publish_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_comment_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    pull_request: Mapped[PullRequest | None] = relationship(back_populates="reviews")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="review", cascade="all, delete-orphan", order_by="Finding.severity_rank"
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ReviewStatus.SUCCEEDED.value,
            ReviewStatus.FAILED.value,
            ReviewStatus.CANCELED.value,
        }


class Finding(Base, TimestampMixin):
    """A single structured review finding."""

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("review_id", "fingerprint", name="uq_findings_review_fingerprint"),
        Index("ix_findings_review_severity", "review_id", "severity_rank"),
        Index("ix_findings_repo_status", "repository_id", "verification_status"),
    )

    id_prefix = "fnd"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    review_id: Mapped[str] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    repository_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    #: True when the cited lines exist on the RIGHT side of the reviewed diff.
    line_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="correctness")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default=Severity.MEDIUM.value)
    #: Integer used for cheap ORDER BY (critical=4 .. info=0).
    severity_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_test: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=VerificationStatus.AI_SUGGESTED.value, index=True
    )
    #: Semantic definition in ``confidence_basis``; NULL confidence is not zero.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_basis: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConfidenceBasis.UNAVAILABLE.value
    )
    detector: Mapped[str] = mapped_column(String(64), nullable=False, default="llm")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_of_finding_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    publish_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PublishState.NOT_PUBLISHED.value
    )
    publish_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_comment_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    retrieval_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    review: Mapped[Review] = relationship(back_populates="findings")
    feedback: Mapped[list[FindingFeedback]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", order_by="FindingFeedback.created_at.desc()"
    )


class FindingFeedback(Base, TimestampMixin):
    """A human label for a finding. Never used as automatic ground truth."""

    __tablename__ = "finding_feedback"

    id_prefix = "fbk"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    finding_id: Mapped[str] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FeedbackLabel.NEEDS_INVESTIGATION.value
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False, default="local")
    #: ``human_review`` is the only source treated as (candidate) ground truth;
    #: ``import`` marks labels pulled from the evaluation dataset.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="human_review")

    finding: Mapped[Finding] = relationship(back_populates="feedback")

    @property
    def is_positive(self) -> bool:
        return self.label == FeedbackLabel.CORRECT.value
