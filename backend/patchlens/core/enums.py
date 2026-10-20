"""Shared enumerations.

These are plain ``str`` enums so they serialise cleanly to JSON and can be stored
as short strings in every database backend. Each value that appears in API
responses is also documented in ``docs/ARCHITECTURE.md``.
"""

from __future__ import annotations

from enum import StrEnum


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class IndexStatus(StrEnum):
    QUEUED = "queued"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"
    STALE = "stale"


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    TEST_FUNCTION = "test_function"
    TEST_CLASS = "test_class"
    DOC = "doc"


class EdgeKind(StrEnum):
    IMPORT = "import"
    CALL = "call"
    INHERIT = "inherit"


class ReviewStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    RELIABILITY = "reliability"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"
    STYLE = "style"


class VerificationStatus(StrEnum):
    """How a finding was established. Ordered from strongest to weakest."""

    #: Produced by a deterministic check and confirmed against the diff (e.g. a
    #: bare ``except:`` really exists on an added line).
    VERIFIED = "verified"
    #: Produced by a deterministic check whose *impact* needs human judgement
    #: (e.g. a hardcoded-looking secret, an unbounded retry loop).
    NEEDS_MANUAL_VERIFICATION = "needs_manual_verification"
    #: Produced by a model. Structurally validated (paths/lines exist in the
    #: diff) but not proven.
    AI_SUGGESTED = "ai_suggested"
    #: Dropped during validation: unverifiable citation, duplicate of a verified
    #: finding, or below the configured evidence bar. Retained for auditability.
    REJECTED = "rejected"


class ConfidenceBasis(StrEnum):
    """Semantics of ``Finding.confidence`` — never presented as calibration."""

    #: Deterministic detection: the pattern is present; value is the check's
    #: fixed detector confidence, not a learned probability.
    DETERMINISTIC = "deterministic_detector"
    #: Model self-reported confidence, subjective and uncalibrated.
    MODEL_SELF_REPORTED = "model_self_reported"
    #: Not supplied; consumers must treat confidence as unknown.
    UNAVAILABLE = "unavailable"


class PublishState(StrEnum):
    NOT_PUBLISHED = "not_published"
    PUBLISHED = "published"
    SKIPPED = "skipped"
    FAILED = "failed"


class FeedbackLabel(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    IRRELEVANT = "irrelevant"
    DUPLICATE = "duplicate"
    NEEDS_INVESTIGATION = "needs_investigation"


class JobKind(StrEnum):
    INDEX_REPOSITORY = "index_repository"
    REVIEW_PULL_REQUEST = "review_pull_request"
    GENERATE_TESTS = "generate_tests"
    RUN_CANDIDATE_TEST = "run_candidate_test"
    EVALUATION_RUN = "evaluation_run"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.DEAD, JobStatus.CANCELED}


class EventStatus(StrEnum):
    RECEIVED = "received"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    PROCESSED = "processed"
    FAILED = "failed"
    REJECTED = "rejected"


class RetrievalStrategy(StrEnum):
    STATIC = "static"
    BM25 = "bm25"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    HYBRID_RERANK = "hybrid_rerank"


class EvidenceKind(StrEnum):
    DIFF_LINE = "diff_line"
    SYMBOL = "symbol"
    TEST = "test"
    CHECK = "check"
    MODEL = "model"


class TestCandidateStatus(StrEnum):
    GENERATED = "generated"
    EXECUTION_DISABLED = "execution_disabled"
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class EvalDatasetKind(StrEnum):
    RETRIEVAL = "retrieval"
    FINDING = "finding"
    TEST_GENERATION = "test_generation"


class EvalRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


__all__ = [
    "EdgeKind",
    "Environment",
    "EvalDatasetKind",
    "EvalRunStatus",
    "EventStatus",
    "EvidenceKind",
    "FeedbackLabel",
    "FindingCategory",
    "IndexStatus",
    "JobKind",
    "JobStatus",
    "PublishState",
    "RetrievalStrategy",
    "ReviewStatus",
    "Severity",
    "SymbolKind",
    "TestCandidateStatus",
    "VerificationStatus",
]
