"""FastAPI dependencies: settings, sessions, auth and pagination."""

from __future__ import annotations

import hmac
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from patchlens.core.config import Settings, get_settings
from patchlens.core.errors import AuthError, NotFoundError
from patchlens.db.models.repository import Repository
from patchlens.db.session import Database, get_database


def settings_dependency(request: Request) -> Settings:
    """Settings for this app instance, falling back to the process singleton.

    ``create_app(settings)`` is authoritative: tests, the CLI and embedded uses
    construct an app with explicit settings, and every request path (auth, schema
    limits, provider summaries) must observe the same object.
    """
    app_settings = getattr(request.app.state, "settings", None)
    if isinstance(app_settings, Settings):
        return app_settings
    return get_settings()


def database_dependency() -> Database:
    return get_database()


def read_session(
    database: Database = Depends(database_dependency),
) -> Iterator[Session]:
    """Session for GET handlers: never commits."""
    with database.read_session() as session:
        yield session


def write_session(
    database: Database = Depends(database_dependency),
) -> Iterator[Session]:
    """Session for mutating handlers: commits on success, rolls back on error."""
    with database.session() as session:
        yield session


def require_auth(
    request: Request,
    settings: Settings = Depends(settings_dependency),
) -> None:
    """Bearer-token check.

    With ``API_AUTH_TOKEN`` unset the instance is an explicitly *local* development
    instance and the check is a no-op. With it set, every protected route requires
    ``Authorization: Bearer <token>``; comparison is constant-time.
    """
    configured = settings.api_auth_token
    if configured is None:
        return
    expected = configured.get_secret_value()
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("missing bearer token")
    if not hmac.compare_digest(token.strip(), expected):
        raise AuthError("invalid bearer token")


@dataclass(slots=True)
class PaginationParams:
    limit: int
    offset: int


def pagination(
    limit: int = Query(default=50, ge=1, le=200, description="Maximum items to return"),
    offset: int = Query(default=0, ge=0, description="Items to skip"),
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)


def load_repository(session: Session, repository_id: str) -> Repository:
    repository = session.get(Repository, repository_id)
    if repository is None:
        raise NotFoundError(f"repository {repository_id} was not found")
    return repository


__all__ = [
    "PaginationParams",
    "database_dependency",
    "load_repository",
    "pagination",
    "read_session",
    "require_auth",
    "settings_dependency",
    "write_session",
]
