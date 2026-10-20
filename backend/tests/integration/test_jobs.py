"""Integration tests for the durable job queue and the worker.

The queue is the reliability backbone: dedupe, atomic claims, retries with
backoff, leases and terminal states are all exercised here against a real
database, because these are exactly the behaviours a redelivered webhook or a
crashed worker will test in production.
"""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from patchlens.core.config import Settings
from patchlens.db.models.job import Job, JobEvent
from patchlens.jobs import handlers as handlers_module
from patchlens.jobs import queue as queue_module
from patchlens.jobs.queue import (
    cancel,
    claim_next,
    complete,
    enqueue,
    fail,
    heartbeat,
    job_to_dict,
    make_dedupe_key,
    recover_expired_leases,
)
from patchlens.jobs.worker import Worker
from tests.conftest import register_repository


def count_jobs(session, **filters) -> int:
    statement = select(func.count()).select_from(Job)
    for column, value in filters.items():
        statement = statement.where(getattr(Job, column) == value)
    return session.scalar(statement) or 0


class TestEnqueueAndDedupe:
    def test_enqueue_creates_a_queued_job_with_an_event(self, session, app_settings) -> None:
        result = enqueue(
            session, kind="index_repository", payload={"repository_id": "repo_1"}, settings=app_settings
        )
        session.commit()
        assert result.created is True
        assert result.duplicate is False
        assert result.job.status == "queued"
        assert result.job.stage == "queued"
        assert result.job.attempts == 0
        events = session.scalars(select(JobEvent).where(JobEvent.job_id == result.job.id)).all()
        assert [event.stage for event in events] == ["queued"]

    def test_same_dedupe_key_returns_the_active_job(self, session, app_settings) -> None:
        key = make_dedupe_key("index", "repo_1", "commit-1")
        first = enqueue(session, kind="index_repository", payload={}, dedupe_key=key, settings=app_settings)
        second = enqueue(session, kind="index_repository", payload={}, dedupe_key=key, settings=app_settings)
        session.commit()
        assert second.duplicate is True
        assert second.job.id == first.job.id
        assert count_jobs(session) == 1

    def test_dedupe_key_is_stable_and_distinct_per_input(self) -> None:
        assert make_dedupe_key("a", "b") == make_dedupe_key("a", "b")
        assert make_dedupe_key("a", "b") != make_dedupe_key("a", "c")

    def test_terminal_completion_frees_the_dedupe_slot(self, session, app_settings) -> None:
        key = make_dedupe_key("index", "repo_1")
        first = enqueue(session, kind="index_repository", payload={}, dedupe_key=key, settings=app_settings)
        claimed = claim_next(session, worker_id="w1")
        assert claimed is not None
        complete(session, claimed, result={"ok": True})

        again = enqueue(session, kind="index_repository", payload={}, dedupe_key=key, settings=app_settings)
        session.commit()
        assert again.created is True
        assert again.job.id != first.job.id

    def test_terminal_failure_frees_the_dedupe_slot_too(self, session, app_settings) -> None:
        key = make_dedupe_key("index", "repo_1")
        enqueue(session, kind="index_repository", payload={}, dedupe_key=key, settings=app_settings)
        claimed = claim_next(session, worker_id="w1")
        assert claimed is not None
        fail(session, claimed, message="boom", retryable=False, settings=app_settings)
        again = enqueue(session, kind="index_repository", payload={}, dedupe_key=key, settings=app_settings)
        session.commit()
        assert again.created is True


class TestClaiming:
    def test_claim_marks_the_job_running_with_a_lease(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={}, settings=app_settings)
        session.commit()
        job = claim_next(session, worker_id="worker-a")
        session.commit()
        assert job is not None
        assert job.status == "running"
        assert job.worker_id == "worker-a"
        assert job.attempts == 1
        assert job.lease_expires_at is not None
        assert job.heartbeat_at is not None
        events = session.scalars(
            select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at)
        ).all()
        assert [event.stage for event in events] == ["queued", "starting"]

    def test_a_claimed_job_is_not_handed_out_twice(self, session, app_settings, database) -> None:
        enqueue(session, kind="index_repository", payload={}, settings=app_settings)
        session.commit()
        assert claim_next(session, worker_id="worker-a") is not None
        session.commit()
        with database.session() as other:
            assert claim_next(other, worker_id="worker-b") is None

    def test_claim_honours_kind_filters(self, session, app_settings) -> None:
        enqueue(session, kind="review_pull_request", payload={}, settings=app_settings)
        session.commit()
        assert claim_next(session, worker_id="w1", kinds=["index_repository"]) is None
        claimed = claim_next(session, worker_id="w1", kinds=["review_pull_request"])
        session.commit()
        assert claimed is not None and claimed.kind == "review_pull_request"

    def test_future_jobs_are_not_claimed_yet(self, session, app_settings) -> None:
        enqueue(
            session,
            kind="index_repository",
            payload={},
            run_after=queue_module.utcnow() + timedelta(minutes=5),
            settings=app_settings,
        )
        session.commit()
        assert claim_next(session, worker_id="w1") is None

    def test_lower_priority_number_is_served_first(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={"n": "low"}, priority=200, settings=app_settings)
        enqueue(session, kind="index_repository", payload={"n": "high"}, priority=10, settings=app_settings)
        session.commit()
        claimed = claim_next(session, worker_id="w1")
        session.commit()
        assert claimed is not None and claimed.payload["n"] == "high"


class TestRetriesAndTerminalStates:
    def test_retryable_failure_schedules_backoff(self, session, app_settings) -> None:
        key = make_dedupe_key("index", "repo_1")
        enqueue(session, kind="index_repository", payload={}, dedupe_key=key, settings=app_settings)
        job = claim_next(session, worker_id="w1")
        assert job is not None
        fail(session, job, message="transient outage", settings=app_settings)

        session.refresh(job)
        assert job.status == "queued"
        assert job.stage == "retrying"
        assert job.run_after > queue_module.utcnow()
        assert job.lease_expires_at is None
        assert job.worker_id is None
        assert job.dedupe_slot is not None, "a retrying job still owns its dedupe slot"

    def test_attempts_are_exhausted_into_dead(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={}, max_attempts=2, settings=app_settings)
        for _ in range(2):
            job = claim_next(session, worker_id="w1")
            assert job is not None
            fail(session, job, message="still broken", settings=app_settings)
            session.refresh(job)
            # A retry schedules the next attempt in the future; make it due now.
            job.run_after = queue_module.utcnow() - timedelta(seconds=1)
            session.commit()

        assert job.status == "dead"
        assert job.stage == "dead"
        assert job.finished_at is not None
        assert job.error == "still broken"
        assert job.dedupe_slot is None
        assert count_jobs(session, status="dead") == 1

    def test_non_retryable_failure_is_final_immediately(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={}, settings=app_settings)
        job = claim_next(session, worker_id="w1")
        assert job is not None
        fail(
            session,
            job,
            message="bad payload",
            error_type="ValidationError",
            retryable=False,
            settings=app_settings,
        )
        session.refresh(job)
        assert job.status == "failed"
        assert job.error == "bad payload"
        assert job.dedupe_slot is None

    def test_completion_records_result_and_duration(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={}, settings=app_settings)
        job = claim_next(session, worker_id="w1")
        assert job is not None
        complete(session, job, result={"files": 3})
        session.refresh(job)
        assert job.status == "succeeded"
        assert job.stage == "succeeded"
        assert job.result == {"files": 3}
        assert job.progress == 100
        assert job.finished_at is not None
        assert job.duration_ms is not None and job.duration_ms >= 0


class TestLeases:
    def test_heartbeat_extends_the_lease_and_logs_progress(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={}, settings=app_settings)
        job = claim_next(session, worker_id="w1", lease_seconds=60)
        assert job is not None
        first_expiry = job.lease_expires_at
        heartbeat(session, job, lease_seconds=600, stage="indexing")
        session.refresh(job)
        assert job.stage == "indexing"
        assert job.lease_expires_at is not None and first_expiry is not None
        assert job.lease_expires_at > first_expiry

    def test_expired_lease_is_recovered_for_another_worker(self, session, app_settings, database) -> None:
        enqueue(session, kind="index_repository", payload={}, settings=app_settings)
        job = claim_next(session, worker_id="crashed-worker", lease_seconds=1)
        assert job is not None
        job.lease_expires_at = queue_module.utcnow() - timedelta(seconds=10)
        session.commit()

        recovered = recover_expired_leases(session, settings=app_settings)
        session.commit()
        assert recovered == 1
        session.refresh(job)
        assert job.status == "queued"
        assert job.worker_id is None
        assert job.lease_expires_at is None
        events = session.scalars(
            select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at)
        ).all()
        assert any("lease" in event.message for event in events)

        reclaimed = claim_next(session, worker_id="fresh-worker")
        session.commit()
        assert reclaimed is not None and reclaimed.id == job.id
        assert reclaimed.attempts == 2

    def test_recovery_leaves_running_jobs_with_a_valid_lease_alone(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={}, settings=app_settings)
        job = claim_next(session, worker_id="busy-worker", lease_seconds=600)
        assert job is not None
        assert recover_expired_leases(session, settings=app_settings) == 0
        session.refresh(job)
        assert job.status == "running"


class TestCancellation:
    def test_queued_job_can_be_cancelled(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={}, settings=app_settings)
        job = claim_next(session, worker_id="w1")
        assert job is not None
        retry_job = enqueue(session, kind="review_pull_request", payload={}, settings=app_settings).job
        cancel(session, retry_job, reason="superseded")
        session.refresh(retry_job)
        assert retry_job.status == "canceled"
        assert retry_job.error == "superseded"
        assert claim_next(session, worker_id="w2", kinds=["review_pull_request"]) is None

    def test_cancelling_a_running_job_releases_its_lease(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={}, settings=app_settings)
        job = claim_next(session, worker_id="w1")
        assert job is not None
        cancel(session, job)
        session.refresh(job)
        assert job.status == "canceled"
        assert job.lease_expires_at is None


class TestSerialization:
    def test_job_dict_includes_progress_and_events_on_request(self, session, app_settings) -> None:
        enqueue(session, kind="index_repository", payload={"repository_id": "repo_1"}, settings=app_settings)
        job = claim_next(session, worker_id="w1")
        assert job is not None
        payload = job_to_dict(job, include_events=True)
        assert payload["kind"] == "index_repository"
        assert payload["status"] == "running"
        assert payload["progress"] == 0
        assert payload["attempts"] == 1
        assert payload["events"], "events are recorded for the progress endpoint"


class TestWorkerExecution:
    def test_worker_runs_an_index_job_end_to_end(
        self, session, app_settings, database, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        draft = register_repository(session, demo_repo, full_name="patchlens/fixture-draft")
        session.delete(draft)  # keep only the repository under test
        session.commit()

        result = enqueue(
            session,
            kind="index_repository",
            payload={"repository_id": repository.id, "commit_sha": "commit-1"},
            settings=app_settings,
        )
        session.commit()

        worker = Worker(settings=app_settings, database=database, worker_id="test-worker")
        assert worker.run_once() is True

        with database.session() as check:
            job = check.get(Job, result.job.id)
            assert job is not None
            assert job.status == "succeeded"
            assert job.result["symbols"] >= 5
            assert job.result["commit_sha"] == "commit-1"
        assert worker.run_once() is False, "the queue is empty afterwards"

    def test_worker_retries_a_crashing_handler_then_marks_it_dead(
        self, session, app_settings, database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def exploding(session_, job, settings_):
            calls.append(1)
            raise RuntimeError("handler exploded")

        monkeypatch.setitem(handlers_module.HANDLERS, "test_exploding", exploding)
        result = enqueue(session, kind="test_exploding", payload={}, max_attempts=2, settings=app_settings)
        session.commit()
        worker = Worker(settings=app_settings, database=database, worker_id="test-worker")

        for _ in range(2):
            assert worker.run_once() is True
            with database.session() as fixing:
                job = fixing.get(Job, result.job.id)
                assert job is not None
                job.run_after = queue_module.utcnow() - timedelta(seconds=1)
                fixing.commit()

        with database.session() as check:
            job = check.get(Job, result.job.id)
            assert job is not None
            assert job.status == "dead"
            assert "RuntimeError" in (job.error or "")
            assert len(calls) == 2

    def test_worker_enforces_the_job_time_limit(
        self, session, app_settings, database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def sleeper(session_, job, settings_):
            time.sleep(5)
            return {}

        monkeypatch.setitem(handlers_module.HANDLERS, "test_sleeping", sleeper)
        result = enqueue(session, kind="test_sleeping", payload={}, timeout_seconds=1, settings=app_settings)
        session.commit()

        started = time.perf_counter()
        worker = Worker(settings=app_settings, database=database, worker_id="test-worker")
        assert worker.run_once() is True
        elapsed = time.perf_counter() - started
        assert elapsed < 4.0, "the handler was cut off by the time limit rather than running to completion"

        with database.session() as check:
            job = check.get(Job, result.job.id)
            assert job is not None
            assert job.error_type == "JobTimeout"
            assert job.status == "queued", "a timed-out job is retryable"
            assert job.stage == "retrying"

    def test_unregistered_kind_fails_without_retrying(self, session, app_settings, database) -> None:
        result = enqueue(session, kind="kind_from_the_future", payload={}, settings=app_settings)
        session.commit()
        worker = Worker(settings=app_settings, database=database, worker_id="test-worker")
        assert worker.run_once() is True
        with database.session() as check:
            job = check.get(Job, result.job.id)
            assert job is not None
            assert job.status == "failed"
            assert job.error_type == "HandlerNotRegistered"

    def test_concurrency_is_limited_on_non_postgres(self, app_settings, database) -> None:
        settings = Settings(environment="test", worker_concurrency=4)
        worker = Worker(settings=settings, database=database, worker_id="test-worker")
        assert database.dialect == "sqlite"
        assert worker.effective_concurrency == 1, "SQLite must not run parallel writers"
