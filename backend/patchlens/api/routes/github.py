"""GitHub App webhook receiver and integration observability endpoints.

The webhook route is intentionally the only public write endpoint in the API. GitHub
cannot present a bearer token; authenticity comes from the HMAC signature over the
raw body, and the endpoint is useless without ``GITHUB_WEBHOOK_SECRET``. Everything
else in this router is protected by the normal bearer-token dependency.

A delivery is acknowledged with 202 even when PatchLens decides not to act on it
(unsupported event, unregistered repository). The delivery row records why, which
is more useful than a retry loop for an error GitHub cannot fix. Unexpected
processing errors are allowed to propagate so the transaction rolls back and a
GitHub retry can be processed rather than discarded as a duplicate.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from patchlens.api.deps import (
    PaginationParams,
    pagination,
    read_session,
    require_auth,
    settings_dependency,
    write_session,
)
from patchlens.api.schemas import (
    GitHubInstallationListResponse,
    GitHubWebhookResponse,
    Pagination,
    WebhookDeliveryListResponse,
)
from patchlens.api.serializers import github_installation_out, webhook_delivery_out
from patchlens.core.config import Settings
from patchlens.core.enums import EventStatus
from patchlens.core.errors import PayloadTooLargeError, ProviderNotConfiguredError, ValidationError
from patchlens.core.security import sha256_hex, verify_github_signature
from patchlens.db.models.github import GitHubInstallation, WebhookDelivery
from patchlens.services.github.webhooks import process_webhook

router = APIRouter(prefix="/github", tags=["github"])


@router.post(
    "/webhooks",
    response_model=GitHubWebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive a GitHub webhook (HMAC verified)",
)
async def receive_webhook(
    request: Request,
    session: Session = Depends(write_session),
    settings: Settings = Depends(settings_dependency),
) -> GitHubWebhookResponse:
    secret = settings.github_webhook_secret
    if secret is None:
        raise ProviderNotConfiguredError("GitHub webhooks are not configured: set GITHUB_WEBHOOK_SECRET")

    body = await _read_webhook_body(request, limit=settings.max_webhook_bytes)
    verify_github_signature(
        secret=secret.get_secret_value(),
        body=body,
        signature_header=request.headers.get("x-hub-signature-256"),
    )

    event = (request.headers.get("x-github-event") or "").strip().lower()
    delivery_id = (request.headers.get("x-github-delivery") or "").strip()
    if not event:
        raise ValidationError("missing X-GitHub-Event header")
    if not delivery_id:
        raise ValidationError("missing X-GitHub-Delivery header")
    if len(event) > 64:
        raise ValidationError("X-GitHub-Event header is too long")
    if len(delivery_id) > 128:
        raise ValidationError("X-GitHub-Delivery header is too long")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError("webhook body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("webhook payload must be a JSON object")

    outcome = process_webhook(
        session,
        event=event,
        delivery_id=delivery_id,
        payload=payload,
        body_sha256=sha256_hex(body),
        payload_bytes=len(body),
        settings=settings,
    )
    return GitHubWebhookResponse(**outcome.to_dict())


async def _read_webhook_body(request: Request, *, limit: int) -> bytes:
    """Read the raw body with a hard cap, including chunked requests.

    ``Content-Length`` is checked first so an obviously oversized request never
    streams into the process. The streaming check still applies when the header is
    absent or inaccurate, so the limit is on actual bytes rather than a header the
    client controls.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except ValueError as exc:
            raise ValidationError("invalid Content-Length header") from exc
        if declared_bytes < 0:
            raise ValidationError("invalid Content-Length header")
        if declared_bytes > limit:
            raise PayloadTooLargeError(
                f"webhook body of {declared_bytes} bytes exceeds the {limit}-byte limit",
                details={"max_bytes": limit},
            )

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise PayloadTooLargeError(
                f"webhook body exceeds the {limit}-byte limit",
                details={"max_bytes": limit},
            )
        chunks.append(chunk)
    if total == 0:
        raise ValidationError("webhook body is empty")
    return b"".join(chunks)


@router.get(
    "/installations",
    response_model=GitHubInstallationListResponse,
    dependencies=[Depends(require_auth)],
    summary="List GitHub App installations seen by webhooks",
)
def list_installations(
    params: PaginationParams = Depends(pagination),
    session: Session = Depends(read_session),
) -> GitHubInstallationListResponse:
    total = session.scalar(select(func.count()).select_from(GitHubInstallation)) or 0
    rows = session.scalars(
        select(GitHubInstallation)
        .order_by(GitHubInstallation.created_at.desc())
        .limit(params.limit)
        .offset(params.offset)
    ).all()
    return GitHubInstallationListResponse(
        items=[github_installation_out(row) for row in rows],
        pagination=Pagination(
            total=total,
            limit=params.limit,
            offset=params.offset,
            has_more=params.offset + len(rows) < total,
        ),
    )


@router.get(
    "/deliveries",
    response_model=WebhookDeliveryListResponse,
    dependencies=[Depends(require_auth)],
    summary="List webhook deliveries and their outcomes",
)
def list_deliveries(
    params: PaginationParams = Depends(pagination),
    delivery_status: str | None = Query(default=None, alias="status"),
    session: Session = Depends(read_session),
) -> WebhookDeliveryListResponse:
    if delivery_status is not None:
        allowed = {item.value for item in EventStatus}
        if delivery_status not in allowed:
            raise ValidationError(
                f"unknown delivery status '{delivery_status}'",
                details={"allowed": sorted(allowed)},
            )
    statement = select(WebhookDelivery)
    count_statement = select(func.count()).select_from(WebhookDelivery)
    if delivery_status is not None:
        statement = statement.where(WebhookDelivery.status == delivery_status)
        count_statement = count_statement.where(WebhookDelivery.status == delivery_status)
    total = session.scalar(count_statement) or 0
    rows = session.scalars(
        statement.order_by(WebhookDelivery.received_at.desc()).limit(params.limit).offset(params.offset)
    ).all()
    return WebhookDeliveryListResponse(
        items=[webhook_delivery_out(row) for row in rows],
        pagination=Pagination(
            total=total,
            limit=params.limit,
            offset=params.offset,
            has_more=params.offset + len(rows) < total,
        ),
    )


__all__ = ["router"]
