"""GitHub integration tables: installations and webhook deliveries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from patchlens.core.enums import EventStatus
from patchlens.db.base import Base, TimestampMixin, UTCDateTime, id_default


class GitHubInstallation(Base, TimestampMixin):
    """A GitHub App installation and the repositories it authorized."""

    __tablename__ = "github_installations"
    __table_args__ = (Index("ix_github_installations_installation", "installation_id", unique=True),)

    id_prefix = "ghi"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    installation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    account_type: Mapped[str] = mapped_column(String(64), nullable=False, default="Organization")
    #: ``all`` or ``selected`` repository access.
    repository_selection: Mapped[str] = mapped_column(String(32), nullable=False, default="selected")
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    repository_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    #: Never store the installation token: they are short-lived and fetched on demand.
    token_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class WebhookDelivery(Base):
    """Audit row for every webhook POST, keyed by ``X-GitHub-Delivery``.

    The unique index on ``delivery_id`` is the duplicate-event guard: GitHub
    retries deliveries, and each retry must be a no-op beyond an audit entry.
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_delivery", "delivery_id", unique=True),
        Index("ix_webhook_deliveries_received", "received_at"),
    )

    id_prefix = "whd"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=id_default(id_prefix))
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repository_full_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=EventStatus.RECEIVED.value)
    #: SHA-256 of the raw payload: lets us detect *different* payloads reusing a
    #: delivery id without storing the payload itself.
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
