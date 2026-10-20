"""Evaluation datasets, metrics and runners.

The public entry points are re-exported here so callers (CLI, API, tests) do not
need to know which module a helper lives in.
"""

from patchlens.services.evaluation.datasets import (
    Dataset,
    DatasetError,
    RetrievalDataset,
    ReviewDataset,
    content_hash,
    load_cases,
    load_dataset,
    seed_dataset,
)
from patchlens.services.evaluation.runner import (
    dataset_cases,
    run_findings_summary,
    run_retrieval_evaluation,
    run_review_evaluation,
)

__all__ = [
    "Dataset",
    "DatasetError",
    "RetrievalDataset",
    "ReviewDataset",
    "content_hash",
    "dataset_cases",
    "load_cases",
    "load_dataset",
    "run_findings_summary",
    "run_retrieval_evaluation",
    "run_review_evaluation",
    "seed_dataset",
]
