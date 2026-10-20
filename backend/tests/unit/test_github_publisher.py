"""Publication policy tests.

The fake client records the exact request PatchLens would send. No network, no
token, and no real repository are involved.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from patchlens.core.config import Settings
from patchlens.core.enums import PublishState, Severity, VerificationStatus
from patchlens.core.errors import ProviderError
from patchlens.db.models.repository import Repository
from patchlens.db.models.review import Finding, PullRequest, Review
from patchlens.services.github.publisher import GitHubPublisher


class FakeClient:
    def __init__(self, *, fail_review: bool = False, fail_check: bool = False) -> None:
        self.fail_review = fail_review
        self.fail_check = fail_check
        self.reviews: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []

    def create_review(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_review:
            raise ProviderError("GitHub API POST returned HTTP 502: upstream error")
        self.reviews.append(kwargs)
        return {"html_url": "https://github.test/acme/widgets/pull/7#pullrequestreview-1"}

    def create_check_run(self, **kwargs: Any) -> dict[str, Any]:
        self.checks.append(kwargs)
        if self.fail_check:
            raise ProviderError("GitHub API POST /check-runs returned HTTP 403: missing checks:write")
        return {"html_url": "https://github.test/acme/widgets/runs/1"}


def settings_for(app_settings: Settings, **updates: object) -> Settings:
    base: dict[str, object] = {
        "github_publish_enabled": True,
        "github_app_id": 123,
        "github_private_key": SecretStr("unused-in-this-test"),
        "github_webhook_secret": SecretStr("unused-in-this-test"),
        "github_comment_severities": ["critical", "high"],
        "github_max_comments_per_review": 10,
        "github_require_verified_anchors": True,
    }
    base.update(updates)
    return app_settings.model_copy(update=base)


def make_review(
    session: Any,
    *,
    installation_id: str | None = "42",
    publish_state: str = PublishState.NOT_PUBLISHED.value,
) -> Review:
    repository = Repository(
        provider="github",
        owner="acme",
        name="widgets",
        full_name="acme/widgets",
        installation_id=installation_id,
        default_branch="main",
    )
    session.add(repository)
    session.flush()
    pull_request = PullRequest(repository_id=repository.id, number=7, title="Change", source="github")
    session.add(pull_request)
    session.flush()
    review = Review(
        repository_id=repository.id,
        pull_request_id=pull_request.id,
        commit_sha="a" * 40,
        strategy="hybrid",
        status="succeeded",
        llm_provider="mock",
        is_mock_provider=True,
        publish_state=publish_state,
    )
    session.add(review)
    session.flush()
    return review


def add_finding(
    session: Any,
    review: Review,
    key: str,
    *,
    severity: str = Severity.HIGH.value,
    status: str = VerificationStatus.VERIFIED.value,
    line_verified: bool = True,
    path: str = "app/calculator.py",
    start_line: int = 10,
    explanation: str = "A finding explanation.",
) -> Finding:
    ranks = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    finding = Finding(
        review_id=review.id,
        repository_id=review.repository_id,
        commit_sha=review.commit_sha,
        path=path,
        start_line=start_line,
        end_line=start_line + 1,
        line_verified=line_verified,
        rule_id=f"rule-{key}",
        category="security",
        severity=severity,
        severity_rank=ranks[severity],
        title=f"Finding {key}",
        explanation=explanation,
        verification_status=status,
        fingerprint=f"fingerprint-{key}",
    )
    session.add(finding)
    session.flush()
    return finding


def test_publishes_eligible_findings_as_a_comment_review(session, app_settings: Settings) -> None:
    review = make_review(session)
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    verified = add_finding(session, review, "verified", explanation=f"token = '{secret}'")
    manual = add_finding(
        session,
        review,
        "manual",
        status=VerificationStatus.NEEDS_MANUAL_VERIFICATION.value,
    )
    filtered = add_finding(session, review, "low", severity=Severity.LOW.value)
    suggested = add_finding(
        session,
        review,
        "suggested",
        status=VerificationStatus.AI_SUGGESTED.value,
    )
    fake = FakeClient()
    publisher = GitHubPublisher(fake, settings_for(app_settings))

    summary = publisher.publish(session, review, [verified, manual, filtered, suggested])

    assert summary["state"] == PublishState.PUBLISHED.value
    assert summary["published"] == 2
    assert len(fake.reviews) == 1
    request = fake.reviews[0]
    assert request["commit_id"] == review.commit_sha
    assert [comment["path"] for comment in request["comments"]] == [
        "app/calculator.py",
        "app/calculator.py",
    ]
    assert all(comment["side"] == "RIGHT" for comment in request["comments"])
    assert all(comment["line"] == 10 for comment in request["comments"])
    assert secret not in request["body"]
    assert secret not in "\n".join(comment["body"] for comment in request["comments"])
    assert any("***REDACTED***" in comment["body"] for comment in request["comments"])
    assert "deterministic mock provider" in request["body"]
    assert verified.publish_state == PublishState.PUBLISHED.value
    assert manual.publish_state == PublishState.PUBLISHED.value
    assert filtered.publish_state == PublishState.SKIPPED.value
    assert "GITHUB_COMMENT_SEVERITIES" in (filtered.publish_detail or "")
    assert suggested.publish_state == PublishState.SKIPPED.value
    assert "not publishable" in (suggested.publish_detail or "")
    assert len(fake.checks) == 1
    assert fake.checks[0]["conclusion"] == "neutral"
    assert len(fake.checks[0]["annotations"]) == 2


def test_comment_cap_is_enforced_and_recorded(session, app_settings: Settings) -> None:
    review = make_review(session)
    first = add_finding(session, review, "one", start_line=10)
    second = add_finding(session, review, "two", start_line=20)
    fake = FakeClient()
    publisher = GitHubPublisher(fake, settings_for(app_settings, github_max_comments_per_review=1))

    summary = publisher.publish(session, review, [first, second])

    assert summary["published"] == 1
    assert len(fake.reviews[0]["comments"]) == 1
    assert {first.publish_state, second.publish_state} == {
        PublishState.PUBLISHED.value,
        PublishState.SKIPPED.value,
    }
    skipped = first if first.publish_state == PublishState.SKIPPED.value else second
    assert "comment limit" in (skipped.publish_detail or "")


def test_unverified_anchor_is_never_published(session, app_settings: Settings) -> None:
    review = make_review(session)
    finding = add_finding(session, review, "anchor", line_verified=False)
    fake = FakeClient()
    publisher = GitHubPublisher(fake, settings_for(app_settings))

    summary = publisher.publish(session, review, [finding])

    assert summary["state"] == PublishState.SKIPPED.value
    assert fake.reviews == []
    assert finding.publish_state == PublishState.SKIPPED.value
    assert "not verified" in (finding.publish_detail or "")


def test_missing_installation_skips_without_calling_github(session, app_settings: Settings) -> None:
    review = make_review(session, installation_id=None)
    finding = add_finding(session, review, "no-install")
    fake = FakeClient()
    publisher = GitHubPublisher(fake, settings_for(app_settings))

    summary = publisher.publish(session, review, [finding])

    assert summary["state"] == PublishState.SKIPPED.value
    assert "installation" in summary["detail"]
    assert fake.reviews == []
    assert finding.publish_state == PublishState.SKIPPED.value


def test_already_published_review_is_idempotent(session, app_settings: Settings) -> None:
    review = make_review(session, publish_state=PublishState.PUBLISHED.value)
    finding = add_finding(session, review, "already")
    fake = FakeClient()
    publisher = GitHubPublisher(fake, settings_for(app_settings))

    summary = publisher.publish(session, review, [finding])

    assert summary["state"] == PublishState.PUBLISHED.value
    assert summary["idempotent"] is True
    assert fake.reviews == []


def test_api_failure_marks_every_selected_finding_failed(session, app_settings: Settings) -> None:
    review = make_review(session)
    finding = add_finding(session, review, "failure")
    publisher = GitHubPublisher(FakeClient(fail_review=True), settings_for(app_settings))

    with pytest.raises(ProviderError):
        publisher.publish(session, review, [finding])

    assert finding.publish_state == PublishState.FAILED.value
    assert "502" in (finding.publish_detail or "")


def test_check_run_failure_does_not_undo_published_comments(session, app_settings: Settings) -> None:
    review = make_review(session)
    finding = add_finding(session, review, "check")
    publisher = GitHubPublisher(FakeClient(fail_check=True), settings_for(app_settings))

    summary = publisher.publish(session, review, [finding])

    assert summary["state"] == PublishState.PUBLISHED.value
    assert summary["check_run"]["state"] == "failed"
    assert finding.publish_state == PublishState.PUBLISHED.value


def test_review_not_attached_to_a_pull_request_is_skipped(session, app_settings: Settings) -> None:
    repository = Repository(provider="local", owner="acme", name="widgets", full_name="acme/widgets-local")
    session.add(repository)
    session.flush()
    review = Review(
        repository_id=repository.id,
        commit_sha="b" * 40,
        strategy="hybrid",
        status="succeeded",
        is_mock_provider=True,
    )
    session.add(review)
    session.flush()
    finding = add_finding(session, review, "no-pr")
    fake = FakeClient()
    publisher = GitHubPublisher(fake, settings_for(app_settings))

    summary = publisher.publish(session, review, [finding])

    assert summary["state"] == PublishState.SKIPPED.value
    assert "pull request" in summary["detail"]
    assert fake.reviews == []
