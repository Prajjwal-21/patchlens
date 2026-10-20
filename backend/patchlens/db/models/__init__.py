"""SQLAlchemy models. Importing this package registers every mapper."""

from patchlens.db.models.evaluation import EvalCase, EvalCaseResult, EvalDataset, EvalRun
from patchlens.db.models.github import GitHubInstallation, WebhookDelivery
from patchlens.db.models.job import Job, JobEvent
from patchlens.db.models.repository import (
    CodeEmbedding,
    CodeSymbol,
    Repository,
    RepositoryVersion,
    SymbolEdge,
)
from patchlens.db.models.review import Finding, FindingFeedback, PullRequest, Review
from patchlens.db.models.testing import TestCandidate

__all__ = [
    "CodeEmbedding",
    "CodeSymbol",
    "EvalCase",
    "EvalCaseResult",
    "EvalDataset",
    "EvalRun",
    "Finding",
    "FindingFeedback",
    "GitHubInstallation",
    "Job",
    "JobEvent",
    "PullRequest",
    "Repository",
    "RepositoryVersion",
    "Review",
    "SymbolEdge",
    "TestCandidate",
    "WebhookDelivery",
]
