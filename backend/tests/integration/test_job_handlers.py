"""Integration tests for job handlers and the worker's control loop.

Handlers are the translation layer between a durable job row and application code,
so the failure classification tested here matters: a transient provider outage must
retry, a missing checkout must not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from patchlens.core.errors import ProviderError
from patchlens.db.models.job import Job
from patchlens.db.models.repository import Repository
from patchlens.db.models.testing import TestCandidate as CandidateModel
from patchlens.jobs import handlers as handlers_module
from patchlens.jobs.handlers import (
    HANDLERS,
    JobFailure,
    NonRetryableJobError,
    handle_generate_tests,
    handle_index_repository,
    handle_review_pull_request,
    handle_run_candidate_test,
    handler_for,
)
from patchlens.jobs.queue import enqueue
from patchlens.jobs.worker import Worker, recover_expired_leases_threadsafe
from tests.conftest import register_repository


def make_job(session, kind: str, payload: dict) -> Job:
    return enqueue(session, kind=kind, payload=payload).job


class TestHandlerRegistry:
    def test_every_registered_kind_resolves(self) -> None:
        assert set(HANDLERS) == {
            "index_repository",
            "review_pull_request",
            "generate_tests",
            "run_candidate_test",
        }
        for kind in HANDLERS:
            assert handler_for(kind) is HANDLERS[kind]

    def test_unknown_kind_has_no_handler(self) -> None:
        assert handler_for("not_a_job") is None


class TestIndexHandler:
    def test_missing_repository_is_a_non_retryable_failure(self, session, app_settings) -> None:
        job = make_job(session, "index_repository", {"repository_id": "repo_missing"})
        with pytest.raises(NonRetryableJobError) as excinfo:
            handle_index_repository(session, job, app_settings)
        assert excinfo.value.error_type == "RepositoryNotFound"
        assert excinfo.value.retryable is False

    def test_repository_without_a_checkout_says_why(self, session, app_settings) -> None:
        repository = register_repository(session, Path(), full_name="patchlens/no-checkout", local_path=None)
        session.commit()
        job = make_job(session, "index_repository", {"repository_id": repository.id})
        with pytest.raises(NonRetryableJobError, match="no local checkout"):
            handle_index_repository(session, job, app_settings)

    def test_missing_directory_is_a_non_retryable_failure(
        self, session, app_settings, tmp_path: Path
    ) -> None:
        repository = register_repository(
            session, tmp_path, full_name="patchlens/vanished", local_path=str(tmp_path / "gone")
        )
        session.commit()
        job = make_job(session, "index_repository", {"repository_id": repository.id})
        with pytest.raises(NonRetryableJobError, match="does not exist"):
            handle_index_repository(session, job, app_settings)

    def test_successful_index_updates_the_repository_row(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        job = make_job(
            session, "index_repository", {"repository_id": repository.id, "commit_sha": "commit-1"}
        )
        result = handle_index_repository(session, job, app_settings)
        session.refresh(repository)

        assert result["status"] == "indexed"
        assert result["symbols"] >= 5
        assert repository.index_status == "indexed"
        assert repository.indexed_commit_sha == "commit-1"
        assert repository.last_index_error is None
        assert repository.latest_version_id == result["version_id"]

    def test_indexing_failure_marks_the_repository_and_reraises(
        self, session, app_settings, demo_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        job = make_job(session, "index_repository", {"repository_id": repository.id, "commit_sha": "c1"})

        def explode(*args, **kwargs):
            raise RuntimeError("parser exploded")

        monkeypatch.setattr("patchlens.jobs.handlers.RepositoryIndexer.index", explode)
        with pytest.raises(RuntimeError, match="parser exploded"):
            handle_index_repository(session, job, app_settings)

        refreshed = session.get(Repository, repository.id)
        assert refreshed is not None
        assert refreshed.index_status == "failed"
        assert "RuntimeError" in (refreshed.last_index_error or "")

    def test_missing_embedding_credentials_do_not_block_indexing(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        job = make_job(session, "index_repository", {"repository_id": repository.id, "commit_sha": "c1"})
        degraded = app_settings.model_copy(
            update={"embedding_provider": "openai_compatible", "embedding_api_key": None}
        )
        result = handle_index_repository(session, job, degraded)

        assert result["symbols"] >= 5, "lexical search still works without embeddings"
        assert result["embeddings"] == 0
        assert "embeddings unavailable" in result["embedding_warning"]


class TestReviewHandler:
    def test_missing_repository_is_non_retryable(self, session, app_settings) -> None:
        job = make_job(session, "review_pull_request", {"repository_id": "repo_missing", "diff_text": "x"})
        with pytest.raises(NonRetryableJobError) as excinfo:
            handle_review_pull_request(session, job, app_settings)
        assert excinfo.value.error_type == "RepositoryNotFound"

    def test_payload_without_a_diff_is_refused_explicitly(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        job = make_job(session, "review_pull_request", {"repository_id": repository.id})
        with pytest.raises(NonRetryableJobError, match="no diff_text"):
            handle_review_pull_request(session, job, app_settings)

    def test_successful_review_returns_counts_and_provider_honesty(
        self, session, app_settings, demo_repo: Path, diff_text: str
    ) -> None:
        from tests.conftest import index_repository
        from tests.helpers import apply_new_state

        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        apply_new_state(demo_repo)

        job = make_job(
            session,
            "review_pull_request",
            {"repository_id": repository.id, "diff_text": diff_text, "commit_sha": "commit-2"},
        )
        result = handle_review_pull_request(session, job, app_settings)
        assert result["status"] == "succeeded"
        assert result["findings"] >= 1
        assert result["verified_findings"] >= 1
        assert result["is_mock_provider"] is True

    def test_provider_error_is_retried(
        self, session, app_settings, demo_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        job = make_job(
            session,
            "review_pull_request",
            {"repository_id": repository.id, "diff_text": "diff", "commit_sha": "c"},
        )

        def explode(*args, **kwargs):
            raise ProviderError("upstream is down", details={"status": 503})

        monkeypatch.setattr("patchlens.jobs.handlers.run_review", explode)
        with pytest.raises(JobFailure) as excinfo:
            handle_review_pull_request(session, job, app_settings)
        assert excinfo.value.retryable is True
        assert excinfo.value.error_type == "ProviderError"

    def test_unexpected_crash_is_retried(
        self, session, app_settings, demo_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        job = make_job(
            session,
            "review_pull_request",
            {"repository_id": repository.id, "diff_text": "diff", "commit_sha": "c"},
        )

        def explode(*args, **kwargs):
            raise ValueError("unexpected")

        monkeypatch.setattr("patchlens.jobs.handlers.run_review", explode)
        with pytest.raises(JobFailure, match="ValueError"):
            handle_review_pull_request(session, job, app_settings)

    def test_failed_review_is_reported_as_a_job_failure(
        self, session, app_settings, demo_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        job = make_job(
            session,
            "review_pull_request",
            {"repository_id": repository.id, "diff_text": "diff", "commit_sha": "c"},
        )

        class FakeReview:
            id = "rvw_1"
            status = "failed"
            error = "the review failed: checks could not run"
            finding_count = 0
            verified_finding_count = 0
            is_mock_provider = False

        class FakeResult:
            review = FakeReview()

        monkeypatch.setattr("patchlens.jobs.handlers.run_review", lambda *args, **kwargs: FakeResult())
        with pytest.raises(JobFailure) as excinfo:
            handle_review_pull_request(session, job, app_settings)
        assert excinfo.value.error_type == "ReviewFailed"
        assert excinfo.value.retryable is False, "a non-provider review failure is not going to fix itself"

    def test_provider_related_review_failure_is_retried(
        self, session, app_settings, demo_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        job = make_job(
            session,
            "review_pull_request",
            {"repository_id": repository.id, "diff_text": "diff", "commit_sha": "c"},
        )

        class FakeReview:
            id = "rvw_1"
            status = "failed"
            error = "model provider failed: upstream returned 503"
            finding_count = 0
            verified_finding_count = 0
            is_mock_provider = False

        class FakeResult:
            review = FakeReview()

        monkeypatch.setattr("patchlens.jobs.handlers.run_review", lambda *args, **kwargs: FakeResult())
        with pytest.raises(JobFailure) as excinfo:
            handle_review_pull_request(session, job, app_settings)
        assert excinfo.value.retryable is True


class TestWorkerLoop:
    def test_run_forever_serves_a_bounded_number_of_jobs(
        self, session, app_settings, database, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        for commit in ("commit-a", "commit-b"):
            enqueue(
                session,
                kind="index_repository",
                payload={"repository_id": repository.id, "commit_sha": commit},
                settings=app_settings,
            )
        session.commit()

        worker = Worker(settings=app_settings, database=database, worker_id="loop-worker")
        executed = worker.run_forever(max_jobs=1)
        assert executed == 1

        with database.session() as check:
            from sqlalchemy import func, select

            done = check.scalar(select(func.count()).select_from(Job).where(Job.status == "succeeded"))
            queued = check.scalar(select(func.count()).select_from(Job).where(Job.status == "queued"))
        assert done == 1
        assert queued == 1, "max_jobs stops the worker instead of draining the queue"

    def test_run_forever_returns_zero_on_an_idle_queue(self, app_settings, database) -> None:
        worker = Worker(settings=app_settings, database=database, worker_id="idle-worker")
        idle_settings = app_settings.model_copy(update={"worker_poll_interval_seconds": 0.01})
        worker.settings = idle_settings
        assert worker.run_forever(idle_cycles=1) == 0

    def test_request_stop_ends_the_loop(
        self, session, app_settings, database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def stopper(session_, job, settings_):
            return {"ok": True}

        monkeypatch.setitem(handlers_module.HANDLERS, "test_stop", stopper)
        enqueue(session, kind="test_stop", payload={}, settings=app_settings)
        session.commit()

        worker = Worker(settings=app_settings, database=database, worker_id="stop-worker")

        def on_execute(session_, job):
            worker.request_stop()
            Worker._execute(worker, session_, job)

        monkeypatch.setattr(worker, "_execute", on_execute)
        assert worker.run_forever() == 1
        assert worker._stop.is_set()

    def test_non_retryable_handler_failure_is_terminal_without_retry(
        self, session, app_settings, database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(session_, job, settings_):
            raise NonRetryableJobError("this input will never become valid")

        monkeypatch.setitem(handlers_module.HANDLERS, "test_refusing", refuse)
        result = enqueue(session, kind="test_refusing", payload={}, settings=app_settings)
        session.commit()

        worker = Worker(settings=app_settings, database=database, worker_id="w")
        assert worker.run_once() is True
        with database.session() as check:
            job = check.get(Job, result.job.id)
            assert job is not None
            assert job.status == "failed"
            assert job.error_type == "NonRetryableJobError"
            assert job.attempts == 1

    def test_jobfailure_retryable_flag_is_honoured(
        self, session, app_settings, database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def soft_fail(session_, job, settings_):
            raise JobFailure("transient", retryable=True, error_type="TransientBoom")

        monkeypatch.setitem(handlers_module.HANDLERS, "test_soft_fail", soft_fail)
        result = enqueue(session, kind="test_soft_fail", payload={}, settings=app_settings)
        session.commit()

        worker = Worker(settings=app_settings, database=database, worker_id="w")
        assert worker.run_once() is True
        with database.session() as check:
            job = check.get(Job, result.job.id)
            assert job is not None
            assert job.status == "queued"
            assert job.stage == "retrying"
            assert job.error_type == "TransientBoom"

    def test_expired_leases_are_recovered_before_the_first_claim(
        self, session, app_settings, database, demo_repo: Path
    ) -> None:
        from datetime import timedelta

        from patchlens.jobs import queue as queue_module
        from patchlens.jobs.queue import claim_next

        repository = register_repository(session, demo_repo)
        session.commit()
        enqueue(
            session,
            kind="index_repository",
            payload={"repository_id": repository.id, "commit_sha": "commit-1"},
            settings=app_settings,
        )
        session.commit()

        crashed = claim_next(session, worker_id="crashed-worker", lease_seconds=1)
        assert crashed is not None
        crashed.lease_expires_at = queue_module.utcnow() - timedelta(seconds=5)
        session.commit()

        worker = Worker(settings=app_settings, database=database, worker_id="fresh-worker")
        assert recover_expired_leases_threadsafe(worker) == 1
        assert worker.run_once() is True

        with database.session() as check:
            job = check.get(Job, crashed.id)
            assert job is not None
            assert job.status == "succeeded"
            assert job.worker_id == "fresh-worker"


class TestTestIntelligenceHandlers:
    def test_generate_tests_from_indexed_symbol(self, session, app_settings, demo_repo: Path) -> None:
        from tests.conftest import index_repository

        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        settings = app_settings.model_copy(update={"test_generation_enabled": True})
        job = make_job(
            session,
            "generate_tests",
            {
                "repository_id": repository.id,
                "targets": [{"path": "demoapp/calculator.py", "qualified_name": "add"}],
            },
        )
        result = handle_generate_tests(session, job, settings)
        assert result["generated"] == 1
        assert result["source"] == "indexed_snapshot"
        candidate = session.get(CandidateModel, result["candidate_ids"][0])
        assert candidate is not None
        assert candidate.status == "execution_disabled"
        assert candidate.executed_at is None

    def test_generate_tests_disabled_after_queue_is_non_retryable(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        session.commit()
        job = make_job(
            session,
            "generate_tests",
            {"repository_id": repository.id, "targets": [{"path": "x.py", "qualified_name": "x"}]},
        )
        with pytest.raises(NonRetryableJobError, match="disabled"):
            handle_generate_tests(session, job, app_settings)

    def test_generate_tests_provider_failure_is_retryable(
        self, session, app_settings, demo_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.conftest import index_repository

        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        settings = app_settings.model_copy(update={"test_generation_enabled": True})

        def explode(*args, **kwargs):
            raise ProviderError("provider unavailable")

        monkeypatch.setattr("patchlens.jobs.handlers.build_llm_provider", explode)
        job = make_job(
            session,
            "generate_tests",
            {
                "repository_id": repository.id,
                "targets": [{"path": "demoapp/calculator.py", "qualified_name": "add"}],
            },
        )
        with pytest.raises(JobFailure) as excinfo:
            handle_generate_tests(session, job, settings)
        assert excinfo.value.retryable is True

    def test_run_candidate_executes_and_records_status(self, session, app_settings, demo_repo: Path) -> None:
        repository = register_repository(session, demo_repo)
        candidate = CandidateModel(
            repository_id=repository.id,
            target_path="demoapp/calculator.py",
            target_symbol="add",
            suggested_test_path="tests/test_job_ok.py",
            source="def test_ok() -> None:\n    assert True\n",
            provider="stub",
            model="stub-1",
            is_mock_provider=False,
            status="queued",
        )
        session.add(candidate)
        session.commit()
        settings = app_settings.model_copy(
            update={"test_execution_mode": "subprocess", "allow_local_test_execution": True}
        )
        job = make_job(session, "run_candidate_test", {"candidate_id": candidate.id})
        result = handle_run_candidate_test(session, job, settings)
        assert result["executed"] is True
        assert result["status"] == "passed"
        refreshed = session.get(CandidateModel, candidate.id)
        assert refreshed is not None
        assert refreshed.status == "passed"
        assert refreshed.executed_at is not None

    def test_run_candidate_without_sandbox_is_recorded_as_disabled(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        candidate = CandidateModel(
            repository_id=repository.id,
            target_path="demoapp/calculator.py",
            target_symbol="add",
            suggested_test_path="tests/test_job_disabled.py",
            source="def test_ok() -> None:\n    assert True\n",
            provider="stub",
            model="stub-1",
            is_mock_provider=False,
            status="queued",
        )
        session.add(candidate)
        session.commit()
        job = make_job(session, "run_candidate_test", {"candidate_id": candidate.id})
        result = handle_run_candidate_test(session, job, app_settings)
        assert result["executed"] is False
        refreshed = session.get(CandidateModel, candidate.id)
        assert refreshed is not None
        assert refreshed.status == "execution_disabled"
        assert refreshed.executed_at is None

    def test_run_candidate_missing_row_is_non_retryable(self, session, app_settings) -> None:
        job = make_job(session, "run_candidate_test", {"candidate_id": "tst_missing"})
        with pytest.raises(NonRetryableJobError, match="not found"):
            handle_run_candidate_test(session, job, app_settings)
