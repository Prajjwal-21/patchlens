"""Integration tests for test-intelligence generation and execution APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from patchlens.api.app import create_app
from patchlens.core.config import Settings
from patchlens.core.errors import ProviderError
from patchlens.db.models.job import Job
from patchlens.db.models.testing import TestCandidate as CandidateModel
from patchlens.services.llm.base import TASK_GENERATE_TEST, LLMCompletion, LLMRequest
from patchlens.services.llm.mock import MockLLMProvider
from patchlens.services.review.pipeline import ReviewPipeline, ReviewRequest
from tests.conftest import index_repository, register_repository
from tests.helpers import apply_new_state


class GenerationFailsProvider(MockLLMProvider):
    def complete(self, request: LLMRequest) -> LLMCompletion:
        if request.task == TASK_GENERATE_TEST:
            raise ProviderError("simulated test-generation outage")
        return super().complete(request)


def generated_review(session, settings: Settings, repository, diff_text: str) -> Any:
    pipeline = ReviewPipeline(session, settings=settings)
    return pipeline.run(
        ReviewRequest(
            repository=repository,
            diff_text=diff_text,
            commit_sha="commit-2",
            base_sha="commit-1",
            trigger="manual",
        )
    )


def settings_for(settings: Settings, **updates: Any) -> Settings:
    return settings.model_copy(update=updates)


def make_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture()
def indexed_review(session, app_settings: Settings, demo_repo: Path, diff_text: str):
    repository = register_repository(session, demo_repo)
    index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
    session.commit()
    apply_new_state(demo_repo)
    settings = settings_for(app_settings, test_generation_enabled=True)
    result = generated_review(session, settings, repository, diff_text)
    return repository, result, settings


class TestReviewGeneration:
    def test_review_generates_disabled_candidates_and_metadata(
        self, session, app_settings: Settings, demo_repo: Path, diff_text: str
    ) -> None:
        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        apply_new_state(demo_repo)
        settings = settings_for(app_settings, test_generation_enabled=True)
        result = generated_review(session, settings, repository, diff_text)

        candidates = session.scalars(
            select(CandidateModel).where(CandidateModel.review_id == result.review.id)
        ).all()
        assert result.review.status == "succeeded"
        assert result.review.candidate_test_count == len(candidates) == 3
        assert all(candidate.status == "execution_disabled" for candidate in candidates)
        assert all(candidate.executed_at is None for candidate in candidates)
        assert all(candidate.is_mock_provider for candidate in candidates)
        assert all(candidate.review_id == result.review.id for candidate in candidates)
        assert result.review.usage["test_generation"]["requests"] == 3

        metadata = result.review.metadata_json
        assert metadata["model"]["is_mock"] is True
        assert metadata["test_generation"]["state"] == "generated"
        assert metadata["test_generation"]["generated"] == 3
        assert metadata["test_generation"]["statuses"] == {"execution_disabled": 3}
        assert metadata["test_intelligence"]["changed_non_test_symbols"] >= 3
        assert metadata["test_intelligence"]["related_tests"]
        assert metadata["test_generation"]["execution"]["executed"] == 0
        assert "source" not in str(metadata["test_generation"])

    def test_generation_failure_does_not_fail_review(
        self, session, app_settings: Settings, demo_repo: Path, diff_text: str
    ) -> None:
        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        apply_new_state(demo_repo)
        settings = settings_for(app_settings, test_generation_enabled=True)
        pipeline = ReviewPipeline(session, settings=settings, llm_provider=GenerationFailsProvider())
        result = pipeline.run(
            ReviewRequest(
                repository=repository,
                diff_text=diff_text,
                commit_sha="commit-2",
                base_sha="commit-1",
                trigger="manual",
            )
        )
        assert result.review.status == "succeeded"
        assert result.review.candidate_test_count == 0
        assert result.review.metadata_json["test_generation"]["state"] == "failed"
        assert any("test generation" in warning for warning in result.warnings)

    def test_generation_disabled_is_explicitly_recorded(
        self, session, app_settings: Settings, demo_repo: Path, diff_text: str
    ) -> None:
        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        apply_new_state(demo_repo)
        result = generated_review(session, app_settings, repository, diff_text)
        assert result.review.candidate_test_count == 0
        assert result.review.metadata_json["test_generation"]["state"] == "disabled"
        assert "TEST_GENERATION_ENABLED=false" in result.review.metadata_json["test_generation"]["detail"]


class TestCandidateAPI:
    def test_list_and_detail_never_hide_the_mock_label(self, client: TestClient, indexed_review) -> None:
        _, result, _ = indexed_review
        response = client.get("/api/v1/test-candidates", params={"review_id": result.review.id})
        assert response.status_code == 200
        payload = response.json()
        assert payload["pagination"]["total"] == 3
        item = payload["items"][0]
        assert item["is_mock_provider"] is True
        assert item["provider"] == "mock"
        assert item["status"] == "execution_disabled"
        assert "source" not in item

        detail = client.get(f"/api/v1/test-candidates/{item['id']}")
        assert detail.status_code == 200
        assert "def test_" in detail.json()["source"]
        assert detail.json()["executed_at"] is None

        review_list = client.get(f"/api/v1/reviews/{result.review.id}/test-candidates")
        assert review_list.status_code == 200
        assert review_list.json()["pagination"]["total"] == 3

    def test_execute_disabled_returns_409_and_persists_reason(
        self, client: TestClient, session, indexed_review
    ) -> None:
        _, result, _ = indexed_review
        candidate = session.scalars(
            select(CandidateModel).where(CandidateModel.review_id == result.review.id)
        ).first()
        assert candidate is not None
        response = client.post(f"/api/v1/test-candidates/{candidate.id}/execute", json={"wait": True})
        assert response.status_code == 409
        body = response.json()["error"]
        assert body["code"] == "sandbox_unavailable"
        assert body["details"]["sandbox"]["effective_mode"] == "disabled"
        session.expire_all()
        refreshed = session.get(CandidateModel, candidate.id)
        assert refreshed is not None
        assert refreshed.status == "execution_disabled"
        assert refreshed.executed_at is None
        assert refreshed.execution_disabled_reason

    def test_execution_status_reports_effective_tier(self, app_settings: Settings) -> None:
        app = create_app(app_settings)
        with TestClient(app) as client:
            response = client.get("/api/v1/test-execution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["available"] is False
        assert payload["effective_mode"] == "disabled"
        assert payload["reason"]

        settings = subprocess_settings(app_settings)
        with TestClient(create_app(settings)) as client:
            response = client.get("/api/v1/test-execution")
        assert response.status_code == 200
        assert response.json()["available"] is True
        assert response.json()["effective_mode"] == "subprocess"
        assert "NOT a security boundary" in response.json()["detail"]

    def test_wait_execute_runs_a_real_subprocess(
        self, session, app_settings: Settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        candidate = CandidateModel(
            repository_id=repository.id,
            target_path="demoapp/calculator.py",
            target_symbol="add",
            suggested_test_path="tests/test_http_ok.py",
            source="def test_ok() -> None:\n    assert True\n",
            provider="stub",
            model="stub-1",
            is_mock_provider=False,
            status="generated",
        )
        session.add(candidate)
        session.commit()
        settings = subprocess_settings(app_settings)
        with TestClient(create_app(settings)) as client:
            response = client.post(f"/api/v1/test-candidates/{candidate.id}/execute", json={"wait": True})
            assert response.status_code == 202
            payload = response.json()
            assert payload["executed_synchronously"] is True
            assert payload["candidate"]["status"] == "passed"
            assert payload["candidate"]["executed_at"] is not None
            assert payload["candidate"]["exit_code"] == 0
            assert payload["candidate"]["sandbox_mode"] == "subprocess"

    def test_queue_execution_creates_a_durable_job(
        self, session, app_settings: Settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        candidate = CandidateModel(
            repository_id=repository.id,
            target_path="demoapp/calculator.py",
            target_symbol="add",
            suggested_test_path="tests/test_queued.py",
            source="def test_ok() -> None:\n    assert True\n",
            provider="stub",
            model="stub-1",
            is_mock_provider=False,
            status="generated",
        )
        session.add(candidate)
        session.commit()
        settings = subprocess_settings(app_settings)
        with TestClient(create_app(settings)) as client:
            response = client.post(f"/api/v1/test-candidates/{candidate.id}/execute", json={"wait": False})
        assert response.status_code == 202
        assert response.json()["job_id"]
        session.expire_all()
        refreshed = session.get(CandidateModel, candidate.id)
        assert refreshed is not None
        assert refreshed.status == "queued"
        job = session.scalar(select(Job).where(Job.id == response.json()["job_id"]))
        assert job is not None
        assert job.kind == "run_candidate_test"
        assert job.payload["candidate_id"] == candidate.id


class TestOnDemandGeneration:
    def test_generate_from_indexed_snapshot(self, session, app_settings: Settings, demo_repo: Path) -> None:
        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        settings = settings_for(app_settings, test_generation_enabled=True)
        with TestClient(create_app(settings)) as client:
            response = client.post(
                f"/api/v1/repositories/{repository.id}/test-candidates",
                json={
                    "targets": [{"path": "demoapp/calculator.py", "qualified_name": "add"}],
                    "wait": True,
                },
            )
        assert response.status_code == 202
        payload = response.json()
        assert payload["executed_synchronously"] is True
        assert len(payload["candidates"]) == 1
        assert payload["candidates"][0]["status"] == "execution_disabled"
        assert payload["candidates"][0]["is_mock_provider"] is True
        assert "latest indexed snapshot" in payload["note"]

    def test_generation_disabled_is_a_conflict(
        self, session, app_settings: Settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        with TestClient(create_app(app_settings)) as client:
            response = client.post(
                f"/api/v1/repositories/{repository.id}/test-candidates",
                json={
                    "targets": [{"path": "demoapp/calculator.py", "qualified_name": "add"}],
                    "wait": True,
                },
            )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
        assert "TEST_GENERATION_ENABLED=false" in response.json()["error"]["message"]

    def test_unknown_target_is_rejected(self, session, app_settings: Settings, demo_repo: Path) -> None:
        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        settings = settings_for(app_settings, test_generation_enabled=True)
        with TestClient(create_app(settings)) as client:
            response = client.post(
                f"/api/v1/repositories/{repository.id}/test-candidates",
                json={
                    "targets": [{"path": "demoapp/calculator.py", "qualified_name": "missing"}],
                    "wait": True,
                },
            )
        assert response.status_code == 422
        assert "no requested symbol" in response.json()["error"]["message"]


def subprocess_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"test_execution_mode": "subprocess", "allow_local_test_execution": True}
    )
