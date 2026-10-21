"""Redis wake-ups are an optimisation, never a correctness dependency."""

from __future__ import annotations

from typing import Any

import pytest

from patchlens.jobs import notify
from patchlens.jobs import queue as queue_module
from patchlens.jobs.notify import CHANNEL, RedisJobWakeup, notify_job_available
from patchlens.jobs.queue import enqueue


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.closed = False

    def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1

    def close(self) -> None:
        self.closed = True


def test_notify_is_skipped_without_a_redis_url(app_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = app_settings.model_copy(update={"redis_url": None})
    monkeypatch.setattr(notify, "_client", lambda *a, **k: pytest.fail("must not connect"))

    assert notify_job_available(settings) is False


def test_notify_publishes_one_wakeup_and_closes_the_client(
    app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = app_settings.model_copy(update={"redis_url": "redis://localhost:6379/0"})
    client = FakeClient()
    monkeypatch.setattr(notify, "_client", lambda *a, **k: client)

    assert notify_job_available(settings) is True
    assert client.published == [(CHANNEL, "1")]
    assert client.closed is True


def test_notify_swallows_redis_failures(app_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = app_settings.model_copy(update={"redis_url": "redis://localhost:6379/0"})

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(notify, "_client", broken)

    # The caller is a commit hook; raising here would turn a successful job
    # enqueue into an API error for an optional dependency.
    assert notify_job_available(settings) is False


def test_enqueue_notifies_only_after_the_job_commits(
    session, app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(queue_module, "notify_job_available", lambda settings: calls.append(settings))
    settings = app_settings.model_copy(update={"redis_url": "redis://localhost:6379/0"})

    enqueue(session, kind="index_repository", payload={}, settings=settings)
    assert calls == []
    session.commit()
    assert calls == [settings]


def test_enqueue_notifies_once_for_a_duplicate(
    session, app_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(queue_module, "notify_job_available", lambda settings: calls.append(settings))
    settings = app_settings.model_copy(update={"redis_url": "redis://localhost:6379/0"})

    enqueue(session, kind="index_repository", payload={}, dedupe_key="k", settings=settings)
    session.commit()
    enqueue(session, kind="index_repository", payload={}, dedupe_key="k", settings=settings)
    session.commit()
    assert len(calls) == 1


def test_rollback_discards_the_pending_wakeup(session, app_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(queue_module, "notify_job_available", lambda settings: calls.append(settings))
    settings = app_settings.model_copy(update={"redis_url": "redis://localhost:6379/0"})

    enqueue(session, kind="index_repository", payload={}, settings=settings)
    session.rollback()
    session.commit()
    assert calls == []


def test_wakeup_is_disabled_without_redis(app_settings) -> None:
    wakeup = RedisJobWakeup(app_settings, lambda: None)
    wakeup.start()
    wakeup.stop()
    assert wakeup.enabled is False
