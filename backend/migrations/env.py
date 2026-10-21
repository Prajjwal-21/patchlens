"""Alembic environment.

The URL comes from :class:`patchlens.core.config.Settings` (``DATABASE_URL``), so
``alembic upgrade head`` migrates exactly the database the application uses.
Batch mode is enabled for SQLite so that future ALTER TABLE operations are applied
through table rebuilds automatically.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from patchlens.core.config import get_settings
from patchlens.db.base import Base, UTCDateTime
from patchlens.db.models import *  # noqa: F403 - register every table on Base.metadata
from patchlens.db.types import VectorColumn

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout (``alembic upgrade head --sql``)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    configuration = config.get_section(config.config_ini_section) or {}
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()


def render_item(type_, obj, autogen_context):
    """Render PatchLens custom column types with their full import path."""
    if type_ == "type" and isinstance(obj, VectorColumn):
        autogen_context.imports.add("from patchlens.db.types import VectorColumn")
        return "VectorColumn()"
    if type_ == "type" and isinstance(obj, UTCDateTime):
        autogen_context.imports.add("from patchlens.db.base import UTCDateTime")
        return "UTCDateTime(timezone=True)" if obj.timezone else "UTCDateTime()"
    return False


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
