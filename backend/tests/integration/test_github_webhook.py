"""Webhook ingestion tests: signature, dedupe, routing and publication.

The end-to-end case drives the real webhook route, the real queue, the real worker
and the real review pipeline. Only the GitHub HTTP API is replaced (with
``httpx.MockTransport``) because the test environment has no GitHub App.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from patchlens.api.app import create_app
from patchlens.core.config import Settings
from patchlens.core.security import sign_github_payload
from patchlens.db.models.github import GitHubInstallation, WebhookDelivery
from patchlens.db.models.job import Job
from patchlens.db.models.repository import Repository
from patchlens.db.models.review import Finding, PullRequest, Review
from patchlens.jobs.worker import Worker
from patchlens.services.github.client import GitHubClient
from tests.conftest import index_repository, register_repository
from tests.helpers import demo_diff

WEBHOOK_SECRET = "test-webhook-secret"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY_PEM = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def webhook_settings(app_settings: Settings, **updates: object) -> Settings:
    base: dict[str, object] = {"github_webhook_secret": SecretStr(WEBHOOK_SECRET)}
    base.update(updates)
    return app_settings.model_copy(update=base)


@pytest.fixture()
def hook_settings(app_settings: Settings) -> Settings:
    return webhook_settings(app_settings)


@pytest.fixture()
def hook_client(hook_settings: Settings):
    with TestClient(create_app(hook_settings)) as client:
        yield client


@pytest.fixture()
def indexed_repository(session, app_settings: Settings, demo_repo):
    repository = register_repository(session, demo_repo)
    index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
    repository.index_status = "indexed"
    repository.indexed_commit_sha = "commit-1"
    session.commit()
    return repository


def post_webhook(
    client: TestClient,
    *,
    event: str = "pull_request",
    delivery_id: str | None = None,
    payload: dict[str, Any] | None = None,
    body: bytes | None = None,
    secret: str = WEBHOOK_SECRET,
    sign: bool = True,
    signature: str | None = None,
):
    raw = body if body is not None else json.dumps(payload or {}).encode("utf-8")
    headers = {
        "x-github-event": event,
        "x-github-delivery": delivery_id or f"delivery-{uuid4().hex}",
        "content-type": "application/json",
    }
    if sign:
        headers["x-hub-signature-256"] = signature or sign_github_payload(secret=secret, body=raw)
    return client.post("/api/v1/github/webhooks", content=raw, headers=headers)


def pull_request_payload(
    *,
    action: str = "opened",
    head_sha: str = "commit-2",
    full_name: str = "patchlens/fixture",
    number: int = 1,
    draft: bool = False,
    installation_id: int = 42,
) -> dict[str, Any]:
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "title": "Add mean",
            "state": "open",
            "draft": draft,
            "html_url": f"https://github.test/{full_name}/pull/{number}",
            "user": {"login": "dev"},
            "head": {"ref": "feature", "sha": head_sha},
            "base": {"ref": "main", "sha": "commit-1"},
            "changed_files": 2,
            "additions": 10,
            "deletions": 1,
        },
        "repository": {
            "full_name": full_name,
            "name": full_name.split("/")[-1],
            "owner": {"login": full_name.split("/")[0]},
        },
        "installation": {"id": installation_id},
        "sender": {"login": "dev"},
    }


def delivery_rows(database) -> list[WebhookDelivery]:
    with database.session() as db:
        return list(db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.received_at)))


def job_rows(database) -> list[Job]:
    with database.session() as db:
        return list(db.scalars(select(Job).order_by(Job.created_at)))


class TestSignatureAndEnvelope:
    def test_missing_signature_is_rejected_before_any_delivery_is_recorded(
        self, hook_client: TestClient, database
    ) -> None:
        response = post_webhook(hook_client, event="ping", payload={"zen": "hello"}, sign=False)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
        assert delivery_rows(database) == []

    def test_wrong_secret_is_rejected(self, hook_client: TestClient, database) -> None:
        response = post_webhook(hook_client, event="ping", payload={"zen": "hello"}, secret="wrong-secret")
        assert response.status_code == 403
        assert delivery_rows(database) == []

    def test_webhook_endpoint_is_useless_without_a_configured_secret(self, app_settings: Settings) -> None:
        with TestClient(create_app(app_settings)) as client:
            response = post_webhook(client, event="ping", payload={"zen": "hello"})
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "provider_not_configured"

    def test_invalid_json_with_a_valid_signature_is_a_typed_422(
        self, hook_client: TestClient, database
    ) -> None:
        response = post_webhook(hook_client, event="ping", body=b"not-json")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert delivery_rows(database) == []

    def test_oversized_webhook_body_is_rejected_with_413(self, app_settings: Settings) -> None:
        settings = webhook_settings(app_settings, max_webhook_bytes=1024)
        with TestClient(create_app(settings)) as client:
            body = b"{" + b" " * 2_000 + b"}"
            response = post_webhook(client, event="ping", body=body)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"

    def test_chunked_oversized_body_is_stopped_without_a_content_length(self, app_settings: Settings) -> None:
        settings = webhook_settings(app_settings, max_webhook_bytes=1024)
        with TestClient(create_app(settings)) as client:

            def chunks():
                yield b"{"
                yield b" " * 2_000
                yield b"}"

            response = client.post(
                "/api/v1/github/webhooks",
                content=chunks(),
                headers={
                    "x-github-event": "ping",
                    "x-github-delivery": "chunked-delivery",
                    "content-type": "application/json",
                },
            )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"

    def test_ping_is_recorded_and_duplicate_delivery_is_a_noop(
        self, hook_client: TestClient, database
    ) -> None:
        delivery_id = "delivery-ping-1"
        first = post_webhook(
            hook_client,
            event="ping",
            delivery_id=delivery_id,
            payload={"zen": "Keep it logically awesome."},
        )
        assert first.status_code == 202
        assert first.json()["status"] == "processed"
        assert first.json()["job_id"] is None

        second = post_webhook(
            hook_client,
            event="ping",
            delivery_id=delivery_id,
            payload={"zen": "Keep it logically awesome."},
        )
        assert second.status_code == 202
        assert second.json()["status"] == "duplicate"
        assert "matches" in second.json()["detail"]

        rows = delivery_rows(database)
        assert len(rows) == 1
        assert rows[0].signature_verified is True
        assert rows[0].status == "processed"
        assert len(rows[0].payload_sha256) == 64

    def test_duplicate_delivery_with_different_content_is_flagged(self, hook_client: TestClient) -> None:
        delivery_id = "delivery-reused"
        post_webhook(hook_client, event="ping", delivery_id=delivery_id, payload={"zen": "one"})
        response = post_webhook(hook_client, event="ping", delivery_id=delivery_id, payload={"zen": "two"})
        assert response.json()["status"] == "duplicate"
        assert "DIFFERENT payload" in response.json()["detail"]


class TestPullRequestRouting:
    def test_opened_pr_queues_a_review_job(
        self, hook_client: TestClient, database, indexed_repository
    ) -> None:
        response = post_webhook(hook_client, payload=pull_request_payload(), delivery_id="delivery-opened")
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "processed"
        assert body["job_id"]

        jobs = job_rows(database)
        assert len(jobs) == 1
        assert jobs[0].kind == "review_pull_request"
        assert jobs[0].payload["publish"] is True
        assert jobs[0].payload["commit_sha"] == "commit-2"
        assert jobs[0].payload["installation_id"] == "42"

        with database.session() as db:
            delivery = db.scalar(
                select(WebhookDelivery).where(WebhookDelivery.delivery_id == "delivery-opened")
            )
            assert delivery is not None
            assert delivery.job_id == body["job_id"]
            assert delivery.repository_full_name == "patchlens/fixture"

    def test_same_head_sha_deduplicates_active_jobs(
        self, hook_client: TestClient, database, indexed_repository
    ) -> None:
        first = post_webhook(hook_client, payload=pull_request_payload())
        second = post_webhook(
            hook_client,
            payload=pull_request_payload(action="synchronize"),
            delivery_id="delivery-second",
        )
        assert first.json()["status"] == "processed"
        assert second.json()["status"] == "processed"
        assert first.json()["job_id"] == second.json()["job_id"]
        assert "already exists" in second.json()["detail"]
        assert len(job_rows(database)) == 1

    def test_closed_action_is_ignored_but_updates_the_pull_request(
        self, hook_client: TestClient, database, indexed_repository
    ) -> None:
        opened = post_webhook(hook_client, payload=pull_request_payload())
        assert opened.json()["status"] == "processed"
        closed_payload = pull_request_payload(action="closed")
        closed_payload["pull_request"]["state"] = "closed"
        response = post_webhook(hook_client, payload=closed_payload)
        assert response.json()["status"] == "ignored"
        assert "does not start a review" in response.json()["detail"]

        with database.session() as db:
            pull_request = db.scalar(select(PullRequest).where(PullRequest.number == 1))
            assert pull_request is not None
            assert pull_request.state == "closed"
        assert len(job_rows(database)) == 1

    def test_draft_pr_is_ignored(self, hook_client: TestClient, database, indexed_repository) -> None:
        response = post_webhook(hook_client, payload=pull_request_payload(draft=True))
        assert response.json()["status"] == "ignored"
        assert "draft" in response.json()["detail"]
        assert job_rows(database) == []

    def test_unregistered_repository_is_recorded_as_failed(self, hook_client: TestClient, database) -> None:
        response = post_webhook(hook_client, payload=pull_request_payload(full_name="unknown/repository"))
        assert response.status_code == 202
        assert response.json()["status"] == "failed"
        assert "not registered" in response.json()["detail"]
        assert job_rows(database) == []

        listed = hook_client.get("/api/v1/github/deliveries?status=failed")
        assert listed.status_code == 200
        assert listed.json()["pagination"]["total"] == 1
        assert "not registered" in listed.json()["items"][0]["error"]

    def test_invalid_pull_request_payload_is_a_failed_delivery(self, hook_client: TestClient) -> None:
        payload = {"action": "opened", "number": 1, "repository": {"full_name": "patchlens/fixture"}}
        response = post_webhook(hook_client, payload=payload)
        assert response.json()["status"] == "failed"
        assert "invalid pull_request payload" in response.json()["detail"]

    def test_delivery_status_filter_rejects_unknown_values(self, hook_client: TestClient) -> None:
        response = hook_client.get("/api/v1/github/deliveries?status=made-up")
        assert response.status_code == 422


class TestInstallationEvents:
    def test_installation_created_and_repositories_added_are_tracked(self, hook_client: TestClient) -> None:
        created = {
            "action": "created",
            "installation": {
                "id": 77,
                "account": {"login": "acme", "type": "Organization"},
                "repository_selection": "selected",
                "permissions": {"contents": "read", "pull_requests": "write"},
                "events": ["pull_request"],
            },
            "repositories": [{"full_name": "acme/one"}],
        }
        response = post_webhook(hook_client, event="installation", payload=created)
        assert response.json()["status"] == "processed"

        listed = hook_client.get("/api/v1/github/installations")
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["installation_id"] == "77"
        assert items[0]["account_login"] == "acme"
        assert items[0]["repository_count"] == 1
        assert items[0]["permissions"]["pull_requests"] == "write"

        added = {
            "action": "added",
            "installation": {"id": 77},
            "repositories_added": [{"full_name": "acme/two"}, {"full_name": "acme/three"}],
            "repositories_removed": [],
        }
        response = post_webhook(hook_client, event="installation_repositories", payload=added)
        assert response.json()["status"] == "processed"
        listed = hook_client.get("/api/v1/github/installations")
        assert listed.json()["items"][0]["repository_count"] == 3

    def test_installation_deleted_clears_repository_links(self, hook_client: TestClient, database) -> None:
        with database.session() as db:
            repository = Repository(
                provider="github",
                owner="acme",
                name="widgets",
                full_name="acme/widgets",
                installation_id="88",
            )
            db.add(repository)
            db.commit()
        response = post_webhook(
            hook_client,
            event="installation",
            payload={"action": "deleted", "installation": {"id": 88}},
        )
        assert response.json()["status"] == "processed"
        with database.session() as db:
            updated = db.scalar(select(Repository).where(Repository.full_name == "acme/widgets"))
            assert updated is not None
            assert updated.installation_id is None
            assert db.scalar(select(func.count()).select_from(GitHubInstallation)) == 0


class TestEndToEndWebhookReview:
    def test_webhook_job_runs_the_review_and_publishes_comments(
        self,
        app_settings: Settings,
        database,
        demo_repo,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = webhook_settings(
            app_settings,
            github_app_id=12345,
            github_private_key=SecretStr(PRIVATE_KEY_PEM),
            github_publish_enabled=True,
            github_comment_severities=["critical", "high", "medium", "low", "info"],
            github_max_comments_per_review=10,
            github_require_verified_anchors=True,
        )
        # The session fixture is not used here: create and index through a fresh
        # session so the webhook's own session sees committed data.
        with database.session() as db:
            repository = register_repository(db, demo_repo)
            index_repository(db, repository, demo_repo, app_settings, commit_sha="commit-1")
            repository.index_status = "indexed"
            repository.indexed_commit_sha = "commit-1"
            db.commit()

        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/access_tokens"):
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_test_token",
                        "expires_at": "2099-01-01T00:00:00Z",
                    },
                )
            if request.method == "GET" and "/pulls/1" in request.url.path:
                return httpx.Response(200, text=demo_diff())
            if request.url.path.endswith("/reviews"):
                return httpx.Response(
                    200,
                    json={"html_url": "https://github.test/acme/widgets/pull/1#review"},
                )
            if request.url.path.endswith("/check-runs"):
                return httpx.Response(201, json={"html_url": "https://github.test/check/1"})
            return httpx.Response(404, json={"message": "unexpected request"})

        def fake_client(settings: Settings, *, http=None) -> GitHubClient:
            return GitHubClient(
                settings,
                http=httpx.Client(
                    transport=httpx.MockTransport(handler),
                    base_url="https://api.github.test",
                ),
            )

        monkeypatch.setattr("patchlens.jobs.handlers.build_github_client", fake_client)

        with TestClient(create_app(settings)) as client:
            response = post_webhook(
                client,
                payload=pull_request_payload(full_name="patchlens/fixture", installation_id=42),
                delivery_id="delivery-e2e",
            )
            assert response.status_code == 202
            assert response.json()["status"] == "processed"
            job_id = response.json()["job_id"]
            assert job_id

        worker = Worker(settings=settings, database=database, worker_id="webhook-worker")
        assert worker.run_once() is True

        with database.session() as db:
            job = db.get(Job, job_id)
            assert job is not None
            assert job.status == "succeeded", job.error
            review = db.scalar(select(Review).where(Review.job_id == job_id))
            assert review is not None
            assert review.trigger == "webhook"
            assert review.status == "succeeded"
            assert review.publish_state == "published"
            assert review.published_comment_url
            findings = list(db.scalars(select(Finding).where(Finding.review_id == review.id)))
            published = [finding for finding in findings if finding.publish_state == "published"]
            assert published, "the mocked GitHub API should have received comments"
            assert all(finding.publish_detail for finding in published)

        review_posts = [
            request for request in seen if request.method == "POST" and request.url.path.endswith("/reviews")
        ]
        assert len(review_posts) == 1
        body = json.loads(review_posts[0].content)
        assert body["event"] == "COMMENT"
        assert body["commit_id"] == "commit-2"
        assert 1 <= len(body["comments"]) <= 10
        assert all(comment["side"] == "RIGHT" for comment in body["comments"])
        check_posts = [request for request in seen if request.url.path.endswith("/check-runs")]
        assert len(check_posts) == 1
        check_body = json.loads(check_posts[0].content)
        assert check_body["conclusion"] == "neutral"
