"""API request/response schemas.

These are intentionally separate from SQLAlchemy models: the wire format must not
change every time an internal column does, and secret-adjacent fields (local
checkout paths, tokens) are never exposed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from patchlens.core.enums import FeedbackLabel


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    version: str
    environment: str
    database: str
    detail: str | None = None


class MetaResponse(BaseModel):
    app_name: str
    version: str
    environment: str
    demo_mode: bool
    llm: dict[str, Any]
    embedding: dict[str, Any]
    retrieval: dict[str, Any]
    capabilities: dict[str, Any]
    review_rules: list[dict[str, Any]]
    retrieval_strategies: list[str]
    feedback_labels: list[str]


# ---------------------------------------------------------------- repositories
class RepositoryVersionOut(BaseModel):
    id: str
    commit_sha: str
    branch: str | None
    status: str
    file_count: int
    symbol_count: int
    test_count: int
    embedding_count: int
    skipped_file_count: int
    index_duration_ms: int | None
    embedding_provider: str | None
    embedding_model: str | None
    embedding_dim: int | None
    indexed_at: datetime | None
    error: str | None


class RepositoryOut(BaseModel):
    id: str
    provider: str
    owner: str
    name: str
    full_name: str
    default_branch: str
    is_private: bool
    language_primary: str
    index_status: str
    indexed_commit_sha: str | None
    last_indexed_at: datetime | None
    last_index_error: str | None
    latest_version: RepositoryVersionOut | None = None


class RepositoryCreateRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    local_path: str = Field(min_length=1, max_length=1024)
    default_branch: str = Field(default="main", max_length=255)
    provider: Literal["local"] = "local"


class IndexRequest(BaseModel):
    commit_sha: str | None = Field(default=None, max_length=64)
    branch: str | None = Field(default=None, max_length=255)
    force: bool = False


# --------------------------------------------------------------------- reviews
class ReviewCreateRequest(BaseModel):
    repository_id: str = Field(min_length=1, max_length=64)
    diff_text: str = Field(min_length=1, max_length=2_000_000)
    commit_sha: str = Field(min_length=1, max_length=64)
    base_sha: str | None = Field(default=None, max_length=64)
    pull_request_id: str | None = Field(default=None, max_length=64)
    wait: bool = False


class ReviewOut(BaseModel):
    id: str
    repository_id: str
    repository_full_name: str | None = None
    pull_request_id: str | None
    commit_sha: str
    base_sha: str | None
    status: str
    stage: str
    progress: int
    strategy: str
    llm_provider: str
    llm_model: str
    is_mock_provider: bool
    trigger: str
    changed_files: int
    changed_lines: int
    retrieved_context_count: int
    finding_count: int
    verified_finding_count: int
    candidate_test_count: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    usage: dict[str, Any] = Field(default_factory=dict)
    job_id: str | None = None
    metadata: dict[str, Any] | None = None


class ReviewCreateResponse(BaseModel):
    review: ReviewOut
    job_id: str | None
    executed_synchronously: bool
    note: str | None = None


# -------------------------------------------------------------------- findings
class FindingFeedbackOut(BaseModel):
    id: str
    label: str
    comment: str | None
    reviewer: str
    source: str
    created_at: datetime


class FeedbackCreateRequest(BaseModel):
    label: FeedbackLabel
    comment: str | None = Field(default=None, max_length=2_000)
    reviewer: str = Field(default="local", min_length=1, max_length=255)
    source: Literal["human_review", "import"] = "human_review"


class FindingOut(BaseModel):
    id: str
    review_id: str
    repository_id: str
    path: str
    start_line: int
    end_line: int
    line_verified: bool
    rule_id: str
    category: str
    severity: str
    title: str
    explanation: str
    suggestion: str | None
    suggested_test: str | None
    verification_status: str
    confidence: float | None
    confidence_basis: str
    detector: str
    provider: str | None
    model: str | None
    evidence: list[dict[str, Any]]
    retrieval_evidence: list[dict[str, Any]]
    rejection_reason: str | None
    publish_state: str
    publish_detail: str | None
    published_comment_url: str | None
    created_at: datetime
    feedback: list[FindingFeedbackOut] = Field(default_factory=list)


class Pagination(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


class FindingListResponse(BaseModel):
    items: list[FindingOut]
    pagination: Pagination


class FeedbackListResponse(BaseModel):
    items: list[FindingFeedbackOut]
    aggregate: dict[str, Any]


# ------------------------------------------------------------------------ jobs
class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    stage: str | None
    progress: int
    attempts: int
    max_attempts: int
    priority: int
    dedupe_key: str | None
    error: str | None
    error_type: str | None
    result: dict[str, Any]
    run_after: datetime | None
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    worker_id: str | None
    events: list[dict[str, Any]] | None = None


class JobListResponse(BaseModel):
    items: list[JobOut]
    pagination: Pagination


class SettingsResponse(BaseModel):
    app_name: str
    version: str
    environment: str
    demo_mode: bool
    llm: dict[str, Any]
    embedding: dict[str, Any]
    retrieval: dict[str, Any]
    security: dict[str, Any]
    limits: dict[str, Any]
    test_execution: dict[str, Any]
    github: dict[str, Any]


class OkResponse(BaseModel):
    ok: bool = True
    detail: str | None = None


class FeedbackAggregateResponse(BaseModel):
    total_findings: int
    labelled_findings: int
    labels: dict[str, int]
    by_category: dict[str, dict[str, int]]
    by_verification_status: dict[str, dict[str, int]]
    note: str


class HealthDetailResponse(BaseModel):
    status: str
    checks: dict[str, Any]


class DeleteResponse(BaseModel):
    deleted: bool
    detail: str


class FeedbackAggregateItem(BaseModel):
    model_config = ConfigDict(extra="allow")


# ------------------------------------------------------------------ evaluation
class EvalRunOut(BaseModel):
    id: str
    dataset_id: str
    dataset_name: str | None = None
    dataset_kind: str | None = None
    kind: str
    status: str
    sample_count: int
    metrics: dict[str, Any] = Field(default_factory=dict)
    strategy_metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    notes: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


class EvalCaseResultOut(BaseModel):
    id: str
    case_id: str
    case_key: str
    split: str
    strategy: str
    predictions: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
    error: str | None = None


class EvalRunDetailOut(EvalRunOut):
    results: list[EvalCaseResultOut] = Field(default_factory=list)


class EvalRunListResponse(BaseModel):
    items: list[EvalRunOut]
    pagination: Pagination


class EvalDatasetOut(BaseModel):
    id: str
    name: str
    version: str
    kind: str
    repository: str | None = None
    description: str | None = None
    commit_sha: str | None = None
    provenance: str | None = None
    license_note: str | None = None
    limitations: str | None = None
    source_path: str | None = None
    case_count: int
    split_counts: dict[str, int] = Field(default_factory=dict)
    content_hash: str
    created_at: datetime | None = None
    #: ``not_run`` when no run exists; otherwise the latest run's status. A dataset
    #: with no run must render as NOT RUN rather than as a metric of zero.
    status: str = "not_run"
    latest_run: EvalRunOut | None = None


class EvalDatasetListResponse(BaseModel):
    items: list[EvalDatasetOut]


class EvalCaseOut(BaseModel):
    id: str
    case_key: str
    split: str
    query: str
    labels: list[dict[str, Any]] = Field(default_factory=list)
    expected_count: int = 0
    notes: str | None = None


class EvalDatasetDetailOut(EvalDatasetOut):
    cases: list[EvalCaseOut] = Field(default_factory=list)


class EvalRunCreateRequest(BaseModel):
    dataset_id: str
    repository_id: str | None = Field(
        default=None, description="Defaults to the repository named by the dataset"
    )
    strategies: list[str] | None = Field(
        default=None, description="Retrieval strategies to compare; ignored for review datasets"
    )


__all__ = [
    "DeleteResponse",
    "ErrorDetail",
    "ErrorResponse",
    "EvalCaseOut",
    "EvalCaseResultOut",
    "EvalDatasetDetailOut",
    "EvalDatasetListResponse",
    "EvalDatasetOut",
    "EvalRunCreateRequest",
    "EvalRunDetailOut",
    "EvalRunListResponse",
    "EvalRunOut",
    "FeedbackAggregateResponse",
    "FeedbackCreateRequest",
    "FindingFeedbackOut",
    "FindingListResponse",
    "FindingOut",
    "HealthDetailResponse",
    "HealthResponse",
    "IndexRequest",
    "JobListResponse",
    "JobOut",
    "MetaResponse",
    "OkResponse",
    "Pagination",
    "RepositoryCreateRequest",
    "RepositoryOut",
    "RepositoryVersionOut",
    "ReviewCreateRequest",
    "ReviewCreateResponse",
    "ReviewOut",
    "SettingsResponse",
]
