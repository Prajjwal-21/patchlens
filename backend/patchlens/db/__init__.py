"""Persistence layer: declarative models, portable types, engine/session."""

from patchlens.db.base import Base, TimestampMixin, UTCDateTime
from patchlens.db.session import (
    Database,
    create_all,
    drop_all,
    get_database,
    reset_database,
    session_scope,
    set_database,
)
from patchlens.db.types import VectorColumn, cosine_similarity

__all__ = [
    "Base",
    "Database",
    "TimestampMixin",
    "UTCDateTime",
    "VectorColumn",
    "cosine_similarity",
    "create_all",
    "drop_all",
    "get_database",
    "reset_database",
    "session_scope",
    "set_database",
]
