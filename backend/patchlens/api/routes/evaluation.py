"""Evaluation datasets and runs.

Read endpoints expose exactly what the runner recorded. A dataset with no run is
reported with ``status="not_run"`` and ``latest_run=null`` so a client cannot render
a missing metric as zero. A run that failed is returned with its error and whatever
per-case detail it managed to record.

``POST /runs`` executes synchronously: the bundled datasets are small and the
default provider is local. The response contains the aggregate and every per-case
result, so the numbers can be checked without trusting the summary.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from patchlens.api.deps import (
    PaginationParams,
    load_repository,
    pagination,
    read_session,
    settings_dependency,
    write_session,
)
from patchlens.api.schemas import (
    EvalDatasetDetailOut,
    EvalDatasetListResponse,
    EvalRunCreateRequest,
    EvalRunDetailOut,
    EvalRunListResponse,
    Pagination,
)
from patchlens.api.serializers import (
    eval_dataset_detail_out,
    eval_dataset_out,
    eval_run_detail_out,
    eval_run_out,
)
from patchlens.core.config import Settings
from patchlens.core.enums import RetrievalStrategy
from patchlens.core.errors import NotFoundError, ValidationError
from patchlens.db.models.evaluation import EvalCaseResult, EvalDataset, EvalRun
from patchlens.db.models.repository import Repository
from patchlens.services.evaluation.datasets import load_cases
from patchlens.services.evaluation.runner import (
    run_retrieval_evaluation,
    run_review_evaluation,
)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])

#: A synchronous request is not the place to run an unbounded experiment.
MAX_CASES_PER_REQUEST = 200


def _latest_run(session: Session, dataset_id: str) -> EvalRun | None:
    return session.scalar(
        select(EvalRun).where(EvalRun.dataset_id == dataset_id).order_by(EvalRun.created_at.desc()).limit(1)
    )


def _load_dataset(session: Session, dataset_id: str) -> EvalDataset:
    dataset = session.get(EvalDataset, dataset_id)
    if dataset is None:
        raise NotFoundError(f"evaluation dataset {dataset_id} was not found")
    return dataset


def _resolve_repository(session: Session, dataset: EvalDataset, repository_id: str | None) -> Repository:
    if repository_id:
        return load_repository(session, repository_id)
    repository = session.scalar(select(Repository).where(Repository.full_name == dataset.repository))
    if repository is None:
        raise ValidationError(
            f"dataset {dataset.name!r} refers to {dataset.repository!r}, which is not registered; "
            "register and index it, or pass repository_id explicitly"
        )
    return repository


@router.get("/datasets", response_model=EvalDatasetListResponse, summary="List evaluation datasets")
def list_datasets(session: Session = Depends(read_session)) -> EvalDatasetListResponse:
    datasets = session.scalars(select(EvalDataset).order_by(EvalDataset.name)).all()
    return EvalDatasetListResponse(
        items=[eval_dataset_out(dataset, _latest_run(session, dataset.id)) for dataset in datasets]
    )


@router.get(
    "/datasets/{dataset_id}",
    response_model=EvalDatasetDetailOut,
    summary="Dataset detail with its cases",
)
def get_dataset(dataset_id: str, session: Session = Depends(read_session)) -> EvalDatasetDetailOut:
    dataset = _load_dataset(session, dataset_id)
    return eval_dataset_detail_out(dataset, _latest_run(session, dataset.id), load_cases(session, dataset))


@router.get("/runs", response_model=EvalRunListResponse, summary="List evaluation runs")
def list_runs(
    dataset_id: str | None = Query(default=None),
    kind: str | None = Query(default=None, description="retrieval or review"),
    run_status: str | None = Query(default=None, alias="status"),
    session: Session = Depends(read_session),
    params: PaginationParams = Depends(pagination),
) -> EvalRunListResponse:
    filters = []
    if dataset_id:
        filters.append(EvalRun.dataset_id == dataset_id)
    if kind:
        filters.append(EvalRun.kind == kind)
    if run_status:
        filters.append(EvalRun.status == run_status)
    total = int(session.scalar(select(func.count()).select_from(EvalRun).where(*filters)) or 0)
    runs = session.scalars(
        select(EvalRun)
        .where(*filters)
        .order_by(EvalRun.created_at.desc())
        .limit(params.limit)
        .offset(params.offset)
    ).all()
    datasets = {dataset.id: dataset for dataset in session.scalars(select(EvalDataset)).all()}
    return EvalRunListResponse(
        items=[eval_run_out(run, datasets.get(run.dataset_id)) for run in runs],
        pagination=Pagination(
            total=total,
            limit=params.limit,
            offset=params.offset,
            has_more=params.offset + params.limit < total,
        ),
    )


@router.get("/runs/{run_id}", response_model=EvalRunDetailOut, summary="Run detail with per-case results")
def get_run(run_id: str, session: Session = Depends(read_session)) -> EvalRunDetailOut:
    run = session.get(EvalRun, run_id)
    if run is None:
        raise NotFoundError(f"evaluation run {run_id} was not found")
    dataset = session.get(EvalDataset, run.dataset_id)
    results = session.scalars(
        select(EvalCaseResult)
        .where(EvalCaseResult.run_id == run.id)
        .order_by(EvalCaseResult.case_key, EvalCaseResult.strategy)
    ).all()
    return eval_run_detail_out(run, list(results), dataset)


@router.post(
    "/runs",
    response_model=EvalRunDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Run an evaluation synchronously",
)
def create_run(
    payload: EvalRunCreateRequest,
    session: Session = Depends(write_session),
    settings: Settings = Depends(settings_dependency),
) -> EvalRunDetailOut:
    dataset = _load_dataset(session, payload.dataset_id)
    if dataset.case_count > MAX_CASES_PER_REQUEST:
        raise ValidationError(
            f"dataset has {dataset.case_count} cases; the synchronous endpoint accepts at most "
            f"{MAX_CASES_PER_REQUEST}. Run it from the CLI for larger datasets."
        )
    cases = load_cases(session, dataset)
    if not cases:
        raise ValidationError(f"dataset {dataset.name!r} has no cases to evaluate")
    repository = _resolve_repository(session, dataset, payload.repository_id)

    if dataset.kind == "retrieval":
        requested = payload.strategies or [strategy.value for strategy in RetrievalStrategy]
        allowed = {strategy.value for strategy in RetrievalStrategy}
        unknown = sorted(set(requested) - allowed)
        if unknown:
            raise ValidationError(
                f"unknown retrieval strategy {', '.join(unknown)}; choose from {', '.join(sorted(allowed))}"
            )
        run = run_retrieval_evaluation(
            session,
            dataset=dataset,
            cases=cases,
            repository=repository,
            settings=settings,
            strategies=[RetrievalStrategy(name) for name in requested],
            dataset_commit_sha=dataset.commit_sha,
        )
    elif dataset.kind == "review":
        run = run_review_evaluation(
            session,
            dataset=dataset,
            cases=cases,
            repository=repository,
            settings=settings,
        )
    else:
        raise ValidationError(f"dataset kind {dataset.kind!r} is not supported")

    session.flush()
    results = session.scalars(
        select(EvalCaseResult)
        .where(EvalCaseResult.run_id == run.id)
        .order_by(EvalCaseResult.case_key, EvalCaseResult.strategy)
    ).all()
    return eval_run_detail_out(run, list(results), dataset)


__all__ = ["router"]
