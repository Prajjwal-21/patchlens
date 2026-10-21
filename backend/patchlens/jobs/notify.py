"""Optional Redis wake-ups for the durable database job queue.

Redis is never a queue here. A job is a row and every state transition is a
transaction; Redis only tells an idle worker that a committed row exists, so it
can stop waiting for the next poll interval. If Redis is down, publishing is
logged and ignored, and workers keep polling the database.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from patchlens.core.config import Settings
from patchlens.core.logging import describe_exception, get_logger, log_extra

logger = get_logger(__name__)

CHANNEL = "patchlens:jobs"


def _client(settings: Settings, *, timeout: float) -> Any:
    # Imported lazily so a deployment that does not configure Redis never needs
    # to import or connect to the client.
    import redis

    if not settings.redis_url:  # pragma: no cover - callers check first
        raise RuntimeError("redis_url is not configured")
    return redis.Redis.from_url(
        settings.redis_url,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
    )


def notify_job_available(settings: Settings) -> bool:
    """Publish one best-effort wake-up. Returns whether Redis accepted it.

    This is called from a SQLAlchemy ``after_commit`` hook, so a notification is
    only sent after the job row is visible to workers. Failures never propagate:
    a lost wake-up costs at most one poll interval.
    """
    if not settings.redis_url:
        return False
    client = None
    try:
        client = _client(settings, timeout=0.5)
        client.publish(CHANNEL, "1")
        return True
    except Exception as exc:
        detail = describe_exception(exc)
        logger.warning(
            "redis job notification failed; durable polling continues",
            extra=log_extra(error_type=detail["type"], error=detail["message"]),
        )
        return False
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()


class RedisJobWakeup:
    """Background subscriber that wakes a worker when a job is committed.

    The subscriber is intentionally best-effort. It retries a broken connection
    with a bounded backoff, logs once per outage, and stops with the worker. The
    polling loop remains the correctness mechanism.
    """

    def __init__(self, settings: Settings, on_job: Callable[[], None]) -> None:
        self.settings = settings
        self.on_job = on_job
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.redis_url)

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, name="patchlens-redis-wakeup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            client = None
            pubsub = None
            try:
                client = _client(self.settings, timeout=2.0)
                pubsub = client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(CHANNEL)
                logger.info("redis job wake-ups enabled", extra=log_extra(channel=CHANNEL))
                delay = 1.0
                while not self._stop.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if message is not None:
                        self.on_job()
            except Exception as exc:
                detail = describe_exception(exc)
                logger.warning(
                    "redis job wake-ups unavailable; falling back to polling",
                    extra=log_extra(error_type=detail["type"], error=detail["message"]),
                )
                self._stop.wait(delay)
                delay = min(delay * 2, 30.0)
            finally:
                if pubsub is not None:
                    with suppress(Exception):
                        pubsub.close()
                if client is not None:
                    with suppress(Exception):
                        client.close()
