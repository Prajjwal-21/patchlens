"""ORM -> API serialisation.

Keeping this in one module means every route reports the same numbers, and any
large internal payload (retrieval results, full check logs) is trimmed in one
place instead of leaking into list responses.
"""

from __future__ import annotations

from typing import Any

from patchlens.api.schemas import (
    EvalCaseOut,
    EvalCaseResultOut,
    EvalDatasetDetailOut,
    EvalDatasetOut,
    EvalRunDetailOut,
    EvalRunOut,
    FindingFeedbackOut,
    FindingOut,
    JobOut,
    RepositoryOut,
    RepositoryVersionOut,
    ReviewOut,
)
from patchlens.db.models.evaluation import EvalCase, EvalCaseResult, EvalDataset, EvalRun
from patchlens.db.models.job import Job
from patchlens.db.models.repository import Repository, RepositoryVersion
from patchlens.db.models.review import Finding, FindingFeedback, Review

#: Keys kept in a review's metadata when it is returned to the UI.
METADATA_KEEP = (
    "pipeline_version",
    "strategy",
    "embedding_provider",
    "embedding_model",
    "symbol_mapping",
    "checks",
    "validation",
    "validation_notes",
    "model",
    "llm_error",
    "degraded",
    "publication",
)


def version_out(version: RepositoryVersion) -> RepositoryVersionOut:
    return RepositoryVersionOut(
        id=version.id,
        commit_sha=version.commit_sha,
        branch=version.branch,
        status=version.status,
        file_count=version.file_count,
        symbol_count=version.symbol_count,
        test_count=version.test_count,
        embedding_count=version.embedding_count,
        skipped_file_count=version.skipped_file_count,
        index_duration_ms=version.index_duration_ms,
        embedding_provider=version.embedding_provider,
        embedding_model=version.embedding_model,
        embedding_dim=version.embedding_dim,
        indexed_at=version.indexed_at,
        error=version.error,
    )


def repository_out(repository: Repository, *, latest: RepositoryVersion | None = None) -> RepositoryOut:
    return RepositoryOut(
        id=repository.id,
        provider=repository.provider,
        owner=repository.owner,
        name=repository.name,
        full_name=repository.full_name,
        default_branch=repository.default_branch,
        is_private=repository.is_private,
        language_primary=repository.language_primary,
        index_status=repository.index_status,
        indexed_commit_sha=repository.indexed_commit_sha,
        last_indexed_at=repository.last_indexed_at,
        last_index_error=repository.last_index_error,
        latest_version=version_out(latest) if latest is not None else None,
    )


def review_metadata_summary(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Trim review metadata to what the dashboard/settings pages need."""
    if not metadata:
        return None
    summary = {key: metadata[key] for key in METADATA_KEEP if key in metadata}
    retrieval = metadata.get("retrieval")
    if isinstance(retrieval, dict):
        summary["retrieval"] = {
            key: retrieval.get(key)
            for key in (
                "strategy",
                "query_count",
                "result_count",
                "candidate_count",
                "deduplicated",
                "latency_ms",
                "backend",
                "reranker",
                "semantic_is_neural",
                "notes",
            )
        }
    return summary


def review_out(
    review: Review,
    *,
    repository_full_name: str | None = None,
    include_metadata: bool = True,
) -> ReviewOut:
    return ReviewOut(
        id=review.id,
        repository_id=review.repository_id,
        repository_full_name=repository_full_name,
        pull_request_id=review.pull_request_id,
        commit_sha=review.commit_sha,
        base_sha=review.base_sha,
        status=review.status,
        stage=review.stage,
        progress=review.progress,
        strategy=review.strategy,
        llm_provider=review.llm_provider,
        llm_model=review.llm_model,
        is_mock_provider=review.is_mock_provider,
        trigger=review.trigger,
        changed_files=review.changed_files,
        changed_lines=review.changed_lines,
        retrieved_context_count=review.retrieved_context_count,
        finding_count=review.finding_count,
        verified_finding_count=review.verified_finding_count,
        candidate_test_count=review.candidate_test_count,
        started_at=review.started_at,
        finished_at=review.finished_at,
        duration_ms=review.duration_ms,
        error=review.error,
        usage=review.usage or {},
        job_id=review.job_id,
        metadata=review_metadata_summary(review.metadata_json) if include_metadata else None,
    )


def feedback_out(feedback: FindingFeedback) -> FindingFeedbackOut:
    return FindingFeedbackOut(
        id=feedback.id,
        label=feedback.label,
        comment=feedback.comment,
        reviewer=feedback.reviewer,
        source=feedback.source,
        created_at=feedback.created_at,
    )


def finding_out(finding: Finding, *, include_feedback: bool = True) -> FindingOut:
    return FindingOut(
        id=finding.id,
        review_id=finding.review_id,
        repository_id=finding.repository_id,
        path=finding.path,
        start_line=finding.start_line,
        end_line=finding.end_line,
        line_verified=finding.line_verified,
        rule_id=finding.rule_id,
        category=finding.category,
        severity=finding.severity,
        title=finding.title,
        explanation=finding.explanation,
        suggestion=finding.suggestion,
        suggested_test=finding.suggested_test,
        verification_status=finding.verification_status,
        confidence=finding.confidence,
        confidence_basis=finding.confidence_basis,
        detector=finding.detector,
        provider=finding.provider,
        model=finding.model,
        evidence=list(finding.evidence or []),
        retrieval_evidence=list(finding.retrieval_evidence or []),
        rejection_reason=finding.rejection_reason,
        publish_state=finding.publish_state,
        publish_detail=finding.publish_detail,
        published_comment_url=finding.published_comment_url,
        created_at=finding.created_at,
        feedback=[feedback_out(item) for item in (finding.feedback or [])] if include_feedback else [],
    )


def job_out(job: Job, *, include_events: bool = False) -> JobOut:
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        priority=job.priority,
        dedupe_key=job.dedupe_key,
        error=job.error,
        error_type=job.error_type,
        result=job.result or {},
        run_after=job.run_after,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_ms=job.duration_ms,
        worker_id=job.worker_id,
        events=(
            [
                {
                    "level": event.level,
                    "stage": event.stage,
                    "message": event.message,
                    "data": event.data,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
                for event in job.events
            ]
            if include_events
            else None
        ),
    )


def eval_run_out(run: EvalRun, dataset: EvalDataset | None = None) -> EvalRunOut:
    return EvalRunOut(
        id=run.id,
        dataset_id=run.dataset_id,
        dataset_name=dataset.name if dataset is not None else None,
        dataset_kind=dataset.kind if dataset is not None else None,
        kind=run.kind,
        status=run.status,
        sample_count=run.sample_count,
        metrics=run.metrics or {},
        strategy_metrics=run.strategy_metrics or {},
        config=run.config or {},
        limitations=run.limitations or [],
        notes=run.notes,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
    )


def eval_case_result_out(result: EvalCaseResult) -> EvalCaseResultOut:
    return EvalCaseResultOut(
        id=result.id,
        case_id=result.case_id,
        case_key=result.case_key,
        split=result.split,
        strategy=result.strategy,
        predictions=result.predictions or [],
        metrics=result.metrics or {},
        latency_ms=result.latency_ms,
        error=result.error,
    )


def eval_run_detail_out(
    run: EvalRun, results: list[EvalCaseResult], dataset: EvalDataset | None = None
) -> EvalRunDetailOut:
    summary = eval_run_out(run, dataset)
    return EvalRunDetailOut(
        **summary.model_dump(),
        results=[eval_case_result_out(result) for result in results],
    )


def eval_dataset_out(dataset: EvalDataset, latest_run: EvalRun | None) -> EvalDatasetOut:
    return EvalDatasetOut(
        id=dataset.id,
        name=dataset.name,
        version=dataset.version,
        kind=dataset.kind,
        repository=dataset.repository,
        description=dataset.description,
        commit_sha=dataset.commit_sha,
        provenance=dataset.provenance,
        license_note=dataset.license_note,
        limitations=dataset.limitations,
        source_path=dataset.source_path,
        case_count=dataset.case_count,
        split_counts=dataset.split_counts or {},
        content_hash=dataset.content_hash,
        created_at=dataset.created_at,
        status=latest_run.status if latest_run is not None else "not_run",
        latest_run=eval_run_out(latest_run, dataset) if latest_run is not None else None,
    )


def eval_case_out(case: EvalCase) -> EvalCaseOut:
    payload = case.payload or {}
    expected = payload.get("expected") or []
    return EvalCaseOut(
        id=case.id,
        case_key=case.case_key,
        split=case.split,
        query=case.query,
        labels=case.labels or [],
        expected_count=len(expected) if isinstance(expected, list) else 0,
        notes=case.notes,
    )


def eval_dataset_detail_out(
    dataset: EvalDataset, latest_run: EvalRun | None, cases: list[EvalCase]
) -> EvalDatasetDetailOut:
    summary = eval_dataset_out(dataset, latest_run)
    return EvalDatasetDetailOut(**summary.model_dump(), cases=[eval_case_out(case) for case in cases])


__all__ = [
    "METADATA_KEEP",
    "eval_case_out",
    "eval_case_result_out",
    "eval_dataset_detail_out",
    "eval_dataset_out",
    "eval_run_detail_out",
    "eval_run_out",
    "feedback_out",
    "finding_out",
    "job_out",
    "repository_out",
    "review_metadata_summary",
    "review_out",
    "version_out",
]
