"""Engine and session management.

The same code path serves three deployment shapes:

* **development / test** - SQLite file (or in-memory), no external services;
* **production** - PostgreSQL + pgvector with pooled connections and a server
  side statement timeout;
* optional **Redis** for job wake-ups, never for durability.

``Database`` is instantiable so tests can spin up isolated engines without
mutating global state.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from patchlens.core.config import Settings, get_settings
from patchlens.core.logging import get_logger

logger = get_logger(__name__)


def _sqlite_path(url: str) -> Path | None:
    prefix = "sqlite+pysqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix) :]
    if raw in {"", ":memory:"} or raw.startswith("file:"):
        return None
    return Path(raw)


def build_engine(
    url: str,
    *,
    echo: bool = False,
    pool_size: int = 5,
    max_overflow: int = 10,
    statement_timeout_ms: int = 30_000,
) -> Engine:
    """Create an engine with backend-appropriate safety settings."""
    connect_args: dict[str, Any] = {}
    kwargs: dict[str, Any] = {"echo": echo, "future": True}

    if url.startswith("sqlite"):
        path = _sqlite_path(url)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False
        # A file-backed SQLite database must survive independent connections.
        kwargs["pool_pre_ping"] = True
    else:
        kwargs["pool_size"] = pool_size
        kwargs["max_overflow"] = max_overflow
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 1800
        if statement_timeout_ms > 0:
            connect_args["options"] = f"-c statement_timeout={statement_timeout_ms}"

    engine = create_engine(url, connect_args=connect_args, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

    return engine


class Database:
    """Owns an engine and a session factory."""

    def __init__(self, settings: Settings | None = None, *, url: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.url = url or self.settings.database_url
        self.engine = build_engine(
            self.url,
            echo=self.settings.database_echo,
            pool_size=self.settings.db_pool_size,
            max_overflow=self.settings.db_max_overflow,
            statement_timeout_ms=self.settings.db_statement_timeout_ms,
        )
        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, class_=Session
        )

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    @property
    def supports_pgvector(self) -> bool:
        return self.dialect == "postgresql"

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional session scope: commit on success, rollback on error."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def read_session(self) -> Iterator[Session]:
        """Read-only scope used by GET handlers (no implicit commit)."""
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # pragma: no cover - depends on live services
            logger.warning("database ping failed: %s", type(exc).__name__)
            return False

    def dispose(self) -> None:
        self.engine.dispose()


_database: Database | None = None


def get_database() -> Database:
    """Process-wide database handle."""
    global _database
    if _database is None:
        _database = Database()
    return _database


def set_database(database: Database | None) -> None:
    """Override the process-wide handle (tests, CLI)."""
    global _database
    _database = database


def reset_database() -> None:
    """Dispose and forget the process-wide handle."""
    global _database
    if _database is not None:
        _database.dispose()
        _database = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Convenience wrapper around the process-wide database handle."""
    with get_database().session() as session:
        yield session


def create_all(database: Database | None = None) -> None:
    """Create the schema directly (test fixtures only; production uses Alembic)."""
    from patchlens.db import models as _models  # noqa: F401  (register mappers)
    from patchlens.db.base import Base

    db = database or get_database()
    Base.metadata.create_all(db.engine)


def drop_all(database: Database | None = None) -> None:
    from patchlens.db import models as _models  # noqa: F401
    from patchlens.db.base import Base

    db = database or get_database()
    Base.metadata.drop_all(db.engine)
