"""Webhook payload models and the processing entry point.

GitHub sends a small, well-known set of events. The models below validate only the
fields PatchLens acts on (and ignore the rest, so a schema addition on GitHub's side
cannot break delivery). An invalid payload is recorded as a failed delivery rather
than raising a 500, because retrying the same malformed body cannot succeed.

Duplicate protection is keyed on ``X-GitHub-Delivery``: the first request wins, and
every retry is a no-op. The payload hash is stored so a delivery id reused for
different content is visible in the audit trail instead of being silently accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from patchlens.core.config import Settings
from patchlens.core.enums import EventStatus, ReviewStatus
from patchlens.core.logging import get_logger, log_extra
from patchlens.db.models.github import GitHubInstallation, WebhookDelivery
from patchlens.db.models.repository import Repository
from patchlens.db.models.review import PullRequest, Review
from patchlens.jobs.queue import enqueue, make_dedupe_key
from patchlens.services.github.webhooks_models import (
    InstallationEvent,
    InstallationRepositoriesEvent,
    PullRequestEvent,
)

logger = get_logger(__name__)

#: Actions that describe a new revision worth reviewing. ``closed`` and label
#: changes are audit-only.
REVIEW_ACTIONS = frozenset({"opened", "reopened", "synchronize"})
SUPPORTED_EVENTS = frozenset({"ping", "pull_request", "installation", "installation_repositories"})


@dataclass(slots=True)
class WebhookOutcome:
    delivery_id: str
    event: str
    action: str | None
    status: str
    detail: str
    job_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "event": self.event,
            "action": self.action,
            "status": self.status,
            "detail": self.detail,
            "job_id": self.job_id,
        }


def process_webhook(
    session: Session,
    *,
    event: str,
    delivery_id: str,
    payload: dict[str, Any],
    body_sha256: str,
    payload_bytes: int,
    settings: Settings,
) -> WebhookOutcome:
    """Record and dispatch one verified webhook delivery.

    The caller has already verified the signature against the raw body. This
    function owns duplicate detection and the audit row; it commits nothing, so the
    route's session scope decides the transaction boundary.
    """
    existing = session.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id))
    if existing is not None:
        return _duplicate_outcome(existing, body_sha256)

    delivery = WebhookDelivery(
        delivery_id=delivery_id,
        event=event,
        action=_action(payload),
        repository_full_name=_repository_full_name(payload),
        sender=_sender(payload),
        signature_verified=True,
        status=EventStatus.RECEIVED.value,
        payload_sha256=body_sha256,
        payload_bytes=payload_bytes,
        received_at=datetime.now(UTC),
    )
    try:
        with session.begin_nested():
            session.add(delivery)
            session.flush()
    except IntegrityError:
        # A concurrent request inserted the same delivery id. The savepoint was
        # rolled back; return the winner without touching its state.
        existing = session.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id))
        if existing is not None:
            return _duplicate_outcome(existing, body_sha256)
        raise

    try:
        status, detail, job_id = _dispatch(session, event, payload, delivery, settings)
    except Exception:
        # Unexpected failures leave the delivery row uncommitted (the route's
        # session rolls back), so a GitHub retry with the same delivery id can be
        # processed instead of being discarded as a duplicate.
        logger.exception(
            "webhook processing failed",
            extra=log_extra(delivery_id=delivery_id, event=event, action=delivery.action),
        )
        raise

    delivery.status = status
    delivery.error = detail if status == EventStatus.FAILED.value else None
    delivery.job_id = job_id
    delivery.processed_at = datetime.now(UTC)
    session.add(delivery)
    session.flush()
    logger.info(
        "webhook processed",
        extra=log_extra(
            delivery_id=delivery_id,
            event=event,
            action=delivery.action,
            status=status,
            job_id=job_id,
        ),
    )
    return WebhookOutcome(
        delivery_id=delivery_id,
        event=event,
        action=delivery.action,
        status=status,
        detail=detail,
        job_id=job_id,
    )


def _dispatch(
    session: Session,
    event: str,
    payload: dict[str, Any],
    delivery: WebhookDelivery,
    settings: Settings,
) -> tuple[str, str, str | None]:
    if event not in SUPPORTED_EVENTS:
        return (
            EventStatus.IGNORED.value,
            f"event '{event}' is not handled by PatchLens",
            None,
        )
    if event == "ping":
        return EventStatus.PROCESSED.value, "ping acknowledged", None
    if event == "installation":
        return _handle_installation(session, payload)
    if event == "installation_repositories":
        return _handle_installation_repositories(session, payload)
    return _handle_pull_request(session, payload, delivery, settings)


def _handle_pull_request(
    session: Session,
    payload: dict[str, Any],
    delivery: WebhookDelivery,
    settings: Settings,
) -> tuple[str, str, str | None]:
    try:
        model = PullRequestEvent.model_validate(payload)
    except ValueError as exc:
        return (
            EventStatus.FAILED.value,
            f"invalid pull_request payload: {_validation_message(exc)}",
            None,
        )

    delivery.action = model.action
    repository = session.scalar(
        select(Repository).where(Repository.full_name == model.repository.full_name.lower())
    )
    if repository is None:
        return (
            EventStatus.FAILED.value,
            f"repository {model.repository.full_name} is not registered; register it with a "
            "local checkout and index it before enabling webhooks",
            None,
        )

    if model.installation is not None:
        repository.installation_id = str(model.installation.id)

    pull_request = session.scalar(
        select(PullRequest).where(
            PullRequest.repository_id == repository.id,
            PullRequest.number == model.number,
        )
    )
    if pull_request is None:
        pull_request = PullRequest(
            repository_id=repository.id,
            number=model.number,
            source="github",
        )
        session.add(pull_request)
    _apply_pull_request_fields(pull_request, model)
    session.flush()

    if model.action not in REVIEW_ACTIONS:
        return (
            EventStatus.IGNORED.value,
            f"pull_request.{model.action} does not start a review",
            None,
        )
    if model.pull_request.draft:
        return EventStatus.IGNORED.value, "draft pull requests are not reviewed", None
    if not repository.local_path:
        return (
            EventStatus.FAILED.value,
            f"repository {repository.full_name} has no local checkout; remote cloning is not "
            "implemented, so the diff cannot be analysed",
            None,
        )
    if repository.index_status != "indexed":
        return (
            EventStatus.FAILED.value,
            f"repository {repository.full_name} is not indexed; index it before enabling webhooks",
            None,
        )

    head_sha = model.pull_request.head.sha
    if not head_sha:
        return EventStatus.FAILED.value, "pull request payload has no head SHA", None

    existing_review = session.scalar(
        select(Review).where(
            Review.pull_request_id == pull_request.id,
            Review.commit_sha == head_sha,
            Review.strategy == settings.retrieval_strategy,
        )
    )
    if existing_review is not None and existing_review.status in {
        ReviewStatus.SUCCEEDED.value,
        ReviewStatus.QUEUED.value,
        ReviewStatus.RUNNING.value,
    }:
        return (
            EventStatus.IGNORED.value,
            f"a review for {head_sha[:12]} already exists with status '{existing_review.status}'",
            existing_review.job_id,
        )

    installation_id = (
        str(model.installation.id) if model.installation is not None else repository.installation_id
    )
    result = enqueue(
        session,
        kind="review_pull_request",
        payload={
            "repository_id": repository.id,
            "pull_request_id": pull_request.id,
            "commit_sha": head_sha,
            "base_sha": model.pull_request.base.sha or None,
            "trigger": "webhook",
            "publish": True,
            "repository_full_name": repository.full_name,
            "pull_request_number": model.number,
            "installation_id": installation_id,
        },
        dedupe_key=make_dedupe_key("github-review", repository.id, head_sha),
        priority=40,
        correlation_id=delivery.id,
        settings=settings,
    )
    detail = (
        f"queued review for {repository.full_name}#{model.number} at {head_sha[:12]}"
        if result.created
        else f"an active review job already exists for {head_sha[:12]}"
    )
    return EventStatus.PROCESSED.value, detail, result.job_id


def _handle_installation(session: Session, payload: dict[str, Any]) -> tuple[str, str, str | None]:
    try:
        model = InstallationEvent.model_validate(payload)
    except ValueError as exc:
        return (
            EventStatus.FAILED.value,
            f"invalid installation payload: {_validation_message(exc)}",
            None,
        )
    installation_id = str(model.installation.id)
    row = session.scalar(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    )
    if model.action == "deleted":
        if row is not None:
            session.delete(row)
        session.execute(
            update(Repository)
            .where(Repository.installation_id == installation_id)
            .values(installation_id=None)
        )
        return (
            EventStatus.PROCESSED.value,
            f"installation {installation_id} deleted and repository links cleared",
            None,
        )

    if row is None:
        row = GitHubInstallation(installation_id=installation_id)
        session.add(row)
    account = model.installation.account
    row.account_login = account.login if account is not None else ""
    row.account_type = account.type if account is not None else "Organization"
    row.repository_selection = model.installation.repository_selection
    row.permissions = model.installation.permissions
    row.events = model.installation.events
    row.suspended = model.action == "suspend" or bool(model.installation.suspended_at)
    if model.action == "unsuspend":
        row.suspended = False
    if model.repositories:
        row.repository_count = len(model.repositories)
        for repository_ref in model.repositories:
            session.execute(
                update(Repository)
                .where(Repository.full_name == repository_ref.full_name.lower())
                .values(installation_id=installation_id)
            )
    session.flush()
    return (
        EventStatus.PROCESSED.value,
        f"installation {installation_id} {model.action}",
        None,
    )


def _handle_installation_repositories(
    session: Session, payload: dict[str, Any]
) -> tuple[str, str, str | None]:
    try:
        model = InstallationRepositoriesEvent.model_validate(payload)
    except ValueError as exc:
        return (
            EventStatus.FAILED.value,
            f"invalid installation_repositories payload: {_validation_message(exc)}",
            None,
        )
    installation_id = str(model.installation.id)
    row = session.scalar(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    )
    if row is None:
        return (
            EventStatus.FAILED.value,
            f"installation {installation_id} is unknown; send the installation event first",
            None,
        )
    added = [reference.full_name.lower() for reference in model.repositories_added]
    removed = [reference.full_name.lower() for reference in model.repositories_removed]
    row.repository_count = max(0, row.repository_count + len(added) - len(removed))
    if added:
        session.execute(
            update(Repository).where(Repository.full_name.in_(added)).values(installation_id=installation_id)
        )
    if removed:
        session.execute(
            update(Repository).where(Repository.full_name.in_(removed)).values(installation_id=None)
        )
    session.flush()
    return (
        EventStatus.PROCESSED.value,
        f"installation {installation_id}: +{len(added)} -{len(removed)} repositories",
        None,
    )


def _apply_pull_request_fields(pull_request: PullRequest, model: PullRequestEvent) -> None:
    data = model.pull_request
    pull_request.title = data.title[:4000]
    pull_request.author = data.user.login if data.user is not None else None
    pull_request.state = data.state
    pull_request.is_draft = data.draft
    pull_request.html_url = data.html_url
    pull_request.base_ref = data.base.ref or None
    pull_request.base_sha = data.base.sha or None
    pull_request.head_ref = data.head.ref or None
    pull_request.head_sha = data.head.sha or None
    pull_request.changed_files = data.changed_files
    pull_request.additions = data.additions
    pull_request.deletions = data.deletions
    pull_request.last_event_at = datetime.now(UTC)


def _duplicate_outcome(existing: WebhookDelivery, body_sha256: str) -> WebhookOutcome:
    same_payload = existing.payload_sha256 == body_sha256
    detail = (
        "duplicate delivery; the stored payload hash matches, so nothing was re-run"
        if same_payload
        else "duplicate delivery id with a DIFFERENT payload; the original result was kept"
    )
    logger.warning(
        "duplicate webhook delivery",
        extra=log_extra(
            delivery_id=existing.delivery_id,
            event=existing.event,
            same_payload=same_payload,
        ),
    )
    return WebhookOutcome(
        delivery_id=existing.delivery_id,
        event=existing.event,
        action=existing.action,
        status=EventStatus.DUPLICATE.value,
        detail=detail,
        job_id=existing.job_id,
    )


def _action(payload: dict[str, Any]) -> str | None:
    value = payload.get("action")
    return str(value)[:64] if value is not None else None


def _repository_full_name(payload: dict[str, Any]) -> str | None:
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        return None
    value = repository.get("full_name")
    return str(value)[:512] if value else None


def _sender(payload: dict[str, Any]) -> str | None:
    sender = payload.get("sender")
    if not isinstance(sender, dict):
        return None
    value = sender.get("login")
    return str(value)[:255] if value else None


def _validation_message(exc: Exception) -> str:
    """A short, safe summary of a Pydantic error (never the full payload)."""
    text = str(exc).replace("\n", " ")
    return text[:500]


__all__ = [
    "REVIEW_ACTIONS",
    "SUPPORTED_EVENTS",
    "WebhookOutcome",
    "process_webhook",
]
