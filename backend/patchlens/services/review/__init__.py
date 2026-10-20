"""Diff-aware review: parsing, symbol mapping, checks, prompting, validation and persistence."""

from patchlens.services.review.checks import CheckRunResult, catalogue, run_checks
from patchlens.services.review.diff import DiffFile, DiffHunk, DiffLine, ParsedDiff, parse_diff
from patchlens.services.review.pipeline import (
    ReviewPipeline,
    ReviewRequest,
    ReviewRunResult,
    eligible_for_publication,
    run_review,
)
from patchlens.services.review.types import ChangedSymbol, Evidence, FindingDraft
from patchlens.services.review.validation import ValidationResult, validate_completion

__all__ = [
    "ChangedSymbol",
    "CheckRunResult",
    "DiffFile",
    "DiffHunk",
    "DiffLine",
    "Evidence",
    "FindingDraft",
    "ParsedDiff",
    "ReviewPipeline",
    "ReviewRequest",
    "ReviewRunResult",
    "ValidationResult",
    "catalogue",
    "eligible_for_publication",
    "parse_diff",
    "run_checks",
    "run_review",
    "validate_completion",
]
