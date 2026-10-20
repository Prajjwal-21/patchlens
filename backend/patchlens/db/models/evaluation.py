"""Evaluation tables: datasets, cases, runs and per-case results.

Every metric shown in the dashboard is derived from ``eval_case_results`` or
``eval_runs.metrics`` written by an actual run of the evaluation pipeline.
There is no code path that invents a metric.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from patchlens.core.enums import EvalRunStatus
from patchlens.db.base import Base, TimestampMixin, UTCDateTime, id_default


class EvalDataset(Base, TimestampMixin):
    """A versioned, fixed evaluation dataset (see ``docs/EVALUATION.md``)."""

    __tablename__ = "eval_datasets"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_eval_datasets_name_version"),)

    id_prefix = "eds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="retrieval")
    #: ``owner/name`` of the repository the labels were written against.
    repository: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Corpus revision the labels refer to (commit SHA or local content hash). A run
    #: compares it with the indexed version and records a limitation on mismatch.
    commit_sha: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    split_counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    cases: Mapped[list[EvalCase]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True
    )


class EvalCase(Base):
    """One evaluation unit: a query plus its graded labels."""

    __tablename__ = "eval_cases"
    __table_args__ = (
        UniqueConstraint("dataset_id", "case_key", name="uq_eval_cases_dataset_key"),
        Index("ix_eval_cases_split", "dataset_id", "split"),
    )

    id_prefix = "eca"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: ``train`` | ``dev`` | ``test``. Metrics are reported per split; the test
    #: split must not be used for tuning.
    split: Mapped[str] = mapped_column(String(32), nullable=False, default="test")
    query: Mapped[str] = mapped_column(Text, nullable=False)
    #: Case-specific payload (expected findings, injected defect patch, etc.).
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: Graded relevance: ``[{"symbol_key": "pkg.mod.fn", "grade": 2}, ...]``.
    labels: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    dataset: Mapped[EvalDataset] = relationship(back_populates="cases")


class EvalRun(Base, TimestampMixin):
    """One execution of the evaluation pipeline over a dataset."""

    __tablename__ = "eval_runs"

    id_prefix = "evr"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="retrieval")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=EvalRunStatus.PENDING.value)
    #: Full configuration snapshot (strategy list, provider names, top-k, code version)
    #: so a result can be reproduced or invalidated explicitly.
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    strategy_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    split: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    dataset: Mapped[EvalDataset] = relationship()
    case_results: Mapped[list[EvalCaseResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class EvalCaseResult(Base):
    """Per-case, per-strategy prediction and metric contribution."""

    __tablename__ = "eval_case_results"
    __table_args__ = (Index("ix_eval_case_results_run", "run_id", "strategy"),)

    id_prefix = "ecr"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    split: Mapped[str] = mapped_column(String(32), nullable=False, default="test")
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    predictions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[EvalRun] = relationship(back_populates="case_results")
