"""Test-intelligence endpoints: candidates, generation and sandbox execution.

The endpoints never imply execution happened. Generation returns candidates with
the provider and ``is_mock_provider``; execution either returns a real process
result or a 409 ``sandbox_unavailable`` with the probe reason. The status endpoint
reports the effective tier, not just the configured one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from patchlens.api.deps import (
    PaginationParams,
    load_repository,
    pagination,
    read_session,
    require_auth,
    settings_dependency,
    write_session,
)
from patchlens.api.schemas import (
    Pagination,
    TestCandidateDetailOut,
    TestCandidateListResponse,
    TestExecutionRequest,
    TestExecutionResponse,
    TestExecutionStatusOut,
    TestGenerationRequest,
    TestGenerationResponse,
)
from patchlens.api.serializers import test_candidate_out
from patchlens.core.config import Settings
from patchlens.core.enums import TestCandidateStatus
from patchlens.core.errors import ConflictError, NotFoundError, SandboxUnavailableError, ValidationError
from patchlens.core.security import content_fingerprint
from patchlens.db.models.review import Review
from patchlens.db.models.testing import TestCandidate
from patchlens.jobs.queue import enqueue
from patchlens.services.providers.factory import build_llm_provider
from patchlens.services.testing.execution import (
    SandboxStatus,
    execute_candidate,
    mark_execution_disabled,
    resolve_sandbox,
)
from patchlens.services.testing.generator import generate_candidates, targets_from_indexed

router = APIRouter(tags=["test-intelligence"], dependencies=[Depends(require_auth)])


@router.get(
    "/test-execution",
    response_model=TestExecutionStatusOut,
    summary="Effective test-execution sandbox",
)
def execution_status(settings: Settings = Depends(settings_dependency)) -> TestExecutionStatusOut:
    sandbox = resolve_sandbox(settings)
    return TestExecutionStatusOut(**sandbox.to_dict())


def _candidate_query(
    *,
    repository_id: str | None,
    review_id: str | None,
    candidate_status: str | None,
) -> Any:
    statement = select(TestCandidate)
    if repository_id:
        statement = statement.where(TestCandidate.repository_id == repository_id)
    if review_id:
        statement = statement.where(TestCandidate.review_id == review_id)
    if candidate_status:
        statement = statement.where(TestCandidate.status == candidate_status)
    return statement


def _list_candidates(
    session: Session,
    params: PaginationParams,
    *,
    repository_id: str | None = None,
    review_id: str | None = None,
    candidate_status: str | None = None,
) -> TestCandidateListResponse:
    base = _candidate_query(
        repository_id=repository_id, review_id=review_id, candidate_status=candidate_status
    )
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = session.scalars(
        base.order_by(TestCandidate.created_at.desc()).limit(params.limit).offset(params.offset)
    ).all()
    return TestCandidateListResponse(
        items=[test_candidate_out(row) for row in rows],
        pagination=Pagination(
            total=total,
            limit=params.limit,
            offset=params.offset,
            has_more=params.offset + params.limit < total,
        ),
    )


@router.get(
    "/test-candidates",
    response_model=TestCandidateListResponse,
    summary="List generated test candidates",
)
def list_candidates(
    repository_id: str | None = Query(default=None),
    review_id: str | None = Query(default=None),
    candidate_status: str | None = Query(default=None, alias="status"),
    session: Session = Depends(read_session),
    params: PaginationParams = Depends(pagination),
) -> TestCandidateListResponse:
    return _list_candidates(
        session,
        params,
        repository_id=repository_id,
        review_id=review_id,
        candidate_status=candidate_status,
    )


@router.get(
    "/reviews/{review_id}/test-candidates",
    response_model=TestCandidateListResponse,
    summary="Candidate tests generated for a review",
)
def review_candidates(
    review_id: str,
    session: Session = Depends(read_session),
    params: PaginationParams = Depends(pagination),
) -> TestCandidateListResponse:
    if session.get(Review, review_id) is None:
        raise NotFoundError(f"review {review_id} was not found")
    return _list_candidates(session, params, review_id=review_id)


@router.get(
    "/test-candidates/{candidate_id}",
    response_model=TestCandidateDetailOut,
    summary="Candidate test detail, source and run output",
)
def get_candidate(candidate_id: str, session: Session = Depends(read_session)) -> TestCandidateDetailOut:
    candidate = session.get(TestCandidate, candidate_id)
    if candidate is None:
        raise NotFoundError(f"test candidate {candidate_id} was not found")
    return test_candidate_out(candidate, detail=True)


@router.post(
    "/repositories/{repository_id}/test-candidates",
    response_model=TestGenerationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate candidate tests for indexed symbols",
)
def generate_for_repository(
    repository_id: str,
    payload: TestGenerationRequest,
    session: Session = Depends(write_session),
    settings: Settings = Depends(settings_dependency),
) -> TestGenerationResponse:
    if not settings.test_generation_enabled:
        raise ConflictError(
            "test generation is disabled (TEST_GENERATION_ENABLED=false); enable it explicitly "
            "because a real provider may charge for each candidate"
        )
    repository = load_repository(session, repository_id)
    requested = [(item.path, item.qualified_name) for item in payload.targets]
    targets = targets_from_indexed(
        session,
        repository=repository,
        requested=requested,
        limit=settings.test_generation_max_candidates,
    )
    if not targets:
        raise ValidationError(
            "no requested symbol with source was found in the latest indexed snapshot; "
            "index the repository first and check the relative path/qualified name"
        )
    limitation = (
        "generation used the latest indexed snapshot, not necessarily a pull-request head; "
        "review-generated candidates use post-change source"
    )
    if payload.wait:
        provider = build_llm_provider(settings)
        try:
            generated = generate_candidates(
                session,
                repository=repository,
                targets=targets,
                settings=settings,
                llm_provider=provider,
                commit_sha=repository.indexed_commit_sha,
            )
        finally:
            provider.close()
        session.commit()
        return TestGenerationResponse(
            candidates=[test_candidate_out(c) for c in generated.candidates],
            job_id=None,
            executed_synchronously=True,
            note=(
                f"generated {len(generated.candidates)} candidate(s); "
                f"{len(generated.rejected)} rejected by validation. {limitation}"
            ),
        )

    job_result = enqueue(
        session,
        kind="generate_tests",
        payload={
            "repository_id": repository.id,
            "targets": [{"path": item.path, "qualified_name": item.qualified_name} for item in targets],
        },
        dedupe_key=(
            "test-generation:"
            + repository.id
            + ":"
            + content_fingerprint(
                "|".join(f"{path}::{name}" for path, name in requested),
                length=16,
            )
        ),
        priority=50,
        correlation_id=repository.id,
        settings=settings,
    )
    session.commit()
    return TestGenerationResponse(
        candidates=[],
        job_id=job_result.job.id,
        executed_synchronously=False,
        note=(
            "queued for the worker; poll /api/v1/jobs/{id}. " + limitation
            if job_result.created
            else "an identical test-generation job is already queued or running. " + limitation
        ),
    )


def _reset_for_queue(candidate: TestCandidate, sandbox: SandboxStatus) -> None:
    candidate.status = TestCandidateStatus.QUEUED.value
    candidate.sandbox_mode = sandbox.effective_mode
    candidate.sandbox_detail = sandbox.detail[:2_000]
    candidate.execution_disabled_reason = None
    candidate.executed_at = None
    candidate.exit_code = None
    candidate.duration_ms = None
    candidate.stdout_tail = None
    candidate.stderr_tail = None
    candidate.output_truncated = False
    candidate.timed_out = False
    candidate.collected_tests = None
    candidate.run_metadata = {}


@router.post(
    "/test-candidates/{candidate_id}/execute",
    response_model=TestExecutionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Execute a candidate in the configured sandbox",
)
def execute_test_candidate(
    candidate_id: str,
    payload: TestExecutionRequest,
    session: Session = Depends(write_session),
    settings: Settings = Depends(settings_dependency),
) -> TestExecutionResponse:
    candidate = session.get(TestCandidate, candidate_id)
    if candidate is None:
        raise NotFoundError(f"test candidate {candidate_id} was not found")
    sandbox = resolve_sandbox(settings)
    if not sandbox.available:
        outcome = mark_execution_disabled(session, candidate, sandbox)
        session.commit()
        raise SandboxUnavailableError(
            outcome.detail,
            details={"candidate_id": candidate.id, "sandbox": sandbox.to_dict()},
        )

    if payload.wait:
        outcome = execute_candidate(session, candidate, settings)
        session.commit()
        session.refresh(candidate)
        return TestExecutionResponse(
            candidate=test_candidate_out(candidate, detail=True),
            job_id=None,
            executed_synchronously=True,
            note=(
                "executed in the request; the sandbox tier and real exit status are recorded"
                if outcome.executed
                else f"not executed: {outcome.detail}"
            ),
        )

    job_result = enqueue(
        session,
        kind="run_candidate_test",
        payload={"candidate_id": candidate.id},
        dedupe_key=f"test-run:{candidate.id}:{content_fingerprint(candidate.source, length=12)}",
        priority=60,
        correlation_id=candidate.id,
        settings=settings,
    )
    if job_result.created:
        _reset_for_queue(candidate, sandbox)
        session.add(candidate)
    session.commit()
    return TestExecutionResponse(
        candidate=test_candidate_out(candidate, detail=True),
        job_id=job_result.job.id,
        executed_synchronously=False,
        note=(
            "queued for the worker; poll /api/v1/jobs/{id} and /api/v1/test-candidates/{id}"
            if job_result.created
            else "an identical execution job is already queued or running"
        ),
    )


__all__ = ["router"]
