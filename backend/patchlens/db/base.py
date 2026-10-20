"""Declarative base and shared column mixins."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from patchlens.core.security import new_id


def id_default(prefix: str) -> Callable[[], str]:
    """Column default producing a prefixed random id (``rvw_1f2e...``)."""
    return lambda: new_id(prefix)


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend.

    SQLite has no timezone support, so naive values are re-attached to UTC on
    read; PostgreSQL gets real ``timestamptz`` values.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[type, Any]] = {datetime: UTCDateTime}


class IdMixin:
    """String primary key with a human-readable prefix (``rvw_...``)."""

    id_prefix: str = "row"

    @classmethod
    def new_id(cls) -> str:
        from patchlens.core.security import new_id

        return new_id(cls.id_prefix)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


def utcnow_sql() -> datetime:
    return datetime.now(UTC)


__all__ = ["NAMING_CONVENTION", "Base", "IdMixin", "TimestampMixin", "UTCDateTime", "func", "utcnow_sql"]
