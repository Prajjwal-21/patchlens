"""Worker process: claims jobs and executes handlers.

Design notes:

* One job at a time per worker *process* on SQLite; ``WORKER_CONCURRENCY`` threads
  are used on PostgreSQL, where concurrent claims are safe. The effective
  concurrency is reported at startup, never assumed.
* A job gets a real wall-clock limit. On POSIX the worker enforces it with
  ``SIGALRM``; on platforms without it the limit is cooperative (checked between
  stages) and that limitation is logged.
* Crashed workers are handled by lease expiry (``recover_expired_leases``), so a
  killed process does not strand a RUNNING job.
"""

from __future__ import annotations

import signal
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from patchlens.core.config import Settings, get_settings
from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.db.models.job import Job
from patchlens.db.session import Database, get_database
from patchlens.jobs.handlers import JobFailure, NonRetryableJobError, handler_for
from patchlens.jobs.notify import RedisJobWakeup
from patchlens.jobs.queue import claim_next, complete, fail, recover_expired_leases

logger = get_logger(__name__)


class JobTimeoutError(Exception):
    pass


@contextmanager
def _time_limit(seconds: int) -> Iterator[None]:
    """Best-effort wall-clock limit for a handler call."""
    usable = (
        seconds > 0 and hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()
    )
    if not usable:
        yield
        return

    def _raise_timeout(signum: int, frame: Any) -> None:
        raise JobTimeoutError(f"job exceeded its {seconds}s time limit")

    previous = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)


class Worker:
    """Claims and executes jobs from the database queue."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        database: Database | None = None,
        worker_id: str | None = None,
        kinds: list[str] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.database = database or get_database()
        self.worker_id = worker_id or self.settings.worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.kinds = kinds
        self._stop = threading.Event()
        self._wakeup = threading.Event()

    # ------------------------------------------------------------------ public
    @property
    def effective_concurrency(self) -> int:
        if self.database.dialect != "postgresql":
            return 1
        return self.settings.worker_concurrency

    def request_stop(self) -> None:
        self._stop.set()

    def run_once(self) -> bool:
        """Claim and execute one job. Returns ``False`` when the queue is empty."""
        with self.database.session() as session:
            job = claim_next(
                session,
                worker_id=self.worker_id,
                kinds=self.kinds,
                lease_seconds=self.settings.worker_lease_seconds,
            )
            if job is None:
                recover_expired_leases(session, settings=self.settings)
                return False
            self._execute(session, job)
            return True

    def run_forever(self, *, max_jobs: int | None = None, idle_cycles: int | None = None) -> int:
        """Serve jobs until stopped. Returns the number of jobs executed."""
        recover_expired_leases_threadsafe(self)
        concurrency = self.effective_concurrency
        if concurrency == 1 and self.settings.worker_concurrency > 1:
            logger.warning(
                "worker concurrency limited to 1 for this database backend",
                extra=log_extra(dialect=self.database.dialect, requested=self.settings.worker_concurrency),
            )
        logger.info(
            "worker started",
            extra=log_extra(
                worker=self.worker_id,
                dialect=self.database.dialect,
                concurrency=concurrency,
                kinds=self.kinds,
            ),
        )
        wakeup = RedisJobWakeup(self.settings, self._wakeup.set)
        wakeup.start()
        try:
            if concurrency <= 1:
                executed = self._loop(max_jobs=max_jobs, idle_cycles=idle_cycles)
            else:
                executed = self._parallel_loop(
                    concurrency=concurrency, max_jobs=max_jobs, idle_cycles=idle_cycles
                )
        finally:
            wakeup.stop()
        logger.info("worker stopped", extra=log_extra(worker=self.worker_id, executed=executed))
        return executed

    # ----------------------------------------------------------------- internal
    def _loop(self, *, max_jobs: int | None, idle_cycles: int | None) -> int:
        executed = 0
        idle = 0
        while not self._stop.is_set():
            worked = self.run_once()
            if worked:
                executed += 1
                idle = 0
                if max_jobs is not None and executed >= max_jobs:
                    break
            else:
                idle += 1
                if idle_cycles is not None and idle >= idle_cycles:
                    break
                self._sleep()
        return executed

    def _parallel_loop(self, *, concurrency: int, max_jobs: int | None, idle_cycles: int | None) -> int:
        counters = {"jobs": 0}
        lock = threading.Lock()

        def _worker_thread(index: int) -> None:
            thread_worker_id = f"{self.worker_id}-t{index}"
            local_idle = 0
            while not self._stop.is_set():
                with self.database.session() as session:
                    job = claim_next(
                        session,
                        worker_id=thread_worker_id,
                        kinds=self.kinds,
                        lease_seconds=self.settings.worker_lease_seconds,
                    )
                    if job is None:
                        local_idle += 1
                        if idle_cycles is not None and local_idle >= idle_cycles:
                            return
                    else:
                        local_idle = 0
                        self._execute(session, job)
                        with lock:
                            counters["jobs"] += 1
                            if max_jobs is not None and counters["jobs"] >= max_jobs:
                                self._stop.set()
                                return
                self._wait_for_work()

        threads = [
            threading.Thread(target=_worker_thread, args=(i,), name=f"patchlens-worker-{i}")
            for i in range(concurrency)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return counters["jobs"]

    def _execute(self, session: Any, job: Job) -> None:
        handler = handler_for(job.kind)
        if handler is None:
            fail(
                session,
                job,
                message=f"no handler registered for job kind {job.kind!r}",
                error_type="HandlerNotRegistered",
                retryable=False,
                settings=self.settings,
            )
            return
        started = time.perf_counter()
        try:
            with _time_limit(job.timeout_seconds):
                result = handler(session, job, self.settings)
        except JobTimeoutError as exc:
            session.rollback()
            fail(
                session,
                job,
                message=str(exc),
                error_type="JobTimeout",
                retryable=True,
                settings=self.settings,
            )
            logger.error("job timed out", extra=log_extra(job_id=job.id, kind=job.kind))
            return
        except NonRetryableJobError as exc:
            session.rollback()
            fail(
                session,
                job,
                message=exc.message,
                error_type=exc.error_type,
                retryable=False,
                settings=self.settings,
            )
            return
        except JobFailure as exc:
            session.rollback()
            fail(
                session,
                job,
                message=exc.message,
                error_type=exc.error_type,
                retryable=exc.retryable,
                settings=self.settings,
            )
            return
        except Exception as exc:
            session.rollback()
            detail = describe_exception(exc)
            fail(
                session,
                job,
                message=f"{detail['type']}: {detail['message']}",
                error_type=detail["type"],
                retryable=True,
                settings=self.settings,
            )
            logger.exception("job handler crashed", extra=log_extra(job_id=job.id, kind=job.kind))
            return
        complete(
            session,
            job,
            result={**(result or {}), "duration_ms": int((time.perf_counter() - started) * 1000)},
        )

    def _wait_for_work(self) -> None:
        """Wait for the poll interval, or less when Redis reports a new job."""
        deadline = time.monotonic() + self.settings.worker_poll_interval_seconds
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._wakeup.wait(timeout=remaining):
                break
        self._wakeup.clear()

    def _sleep(self) -> None:
        self._wait_for_work()


def recover_expired_leases_threadsafe(worker: Worker) -> int:
    with worker.database.session() as session:
        return recover_expired_leases(session, settings=worker.settings)


def run_worker(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    kinds: list[str] | None = None,
    max_jobs: int | None = None,
    idle_cycles: int | None = None,
) -> int:
    """Construct a worker, install signal handlers and serve jobs."""
    worker = Worker(settings=settings, database=database, kinds=kinds)

    def _stop(signum: int, frame: Any) -> None:
        logger.info("stop signal received", extra=log_extra(worker=worker.worker_id, signal=signum))
        worker.request_stop()

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with suppress(ValueError):  # only the main thread may install handlers
                signal.signal(sig, _stop)
    return worker.run_forever(max_jobs=max_jobs, idle_cycles=idle_cycles)


__all__ = ["JobTimeoutError", "Worker", "recover_expired_leases_threadsafe", "run_worker"]
