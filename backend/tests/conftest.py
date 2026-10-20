"""Shared test fixtures and a small fixture repository.

Every test runs against a real SQLite database created in ``tmp_path`` and a real
fixture repository on disk; nothing here fakes the indexer, the review pipeline or
the job queue. The only stub is the deterministic mock LLM provider, which is the
product's documented development provider.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# The application reads configuration from the environment, so the test defaults
# must be in place before any patchlens module builds its settings. conftest is
# imported before test modules, and `reload_settings()` is called per test.
os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_JSON"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMBEDDING_PROVIDER"] = "hashing"
os.environ["RETRIEVAL_STRATEGY"] = "hybrid"
os.environ["AUTO_CREATE_SCHEMA"] = "true"

from fastapi.testclient import TestClient

from patchlens.api.app import create_app, lifespan_context
from patchlens.core.config import Settings, reload_settings
from patchlens.db import session as session_module
from patchlens.db.session import Database, create_all, get_database
from patchlens.jobs.worker import Worker
from patchlens.services.ingestion.indexer import IndexOutcome, RepositoryIndexer
from patchlens.services.providers.factory import build_embedding_provider
from tests.helpers import (
    NEW_FILES,
    OLD_FILES,
    apply_new_state,
    demo_diff,
    write_repo,
)


@pytest.fixture()
def app_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings pointing at a throwaway SQLite database, with the schema created."""
    database_path = tmp_path / "patchlens-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    session_module.reset_database()
    settings = reload_settings()
    database = get_database()
    create_all(database)
    yield settings
    session_module.reset_database()
    reload_settings()


@pytest.fixture()
def database(app_settings: Settings) -> Database:
    return get_database()


@pytest.fixture()
def session(database: Database) -> Iterator[session_module.Session]:
    with database.session() as db_session:
        yield db_session


@pytest.fixture()
def client(app_settings: Settings) -> Iterator[TestClient]:
    """API client whose lifespan startup creates the schema."""
    app = create_app(app_settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def worker(app_settings: Settings, database: Database) -> Worker:
    return Worker(settings=app_settings, database=database, worker_id="test-worker")


@pytest.fixture()
def demo_repo(tmp_path: Path) -> Path:
    """A checkout containing the fixture package, in its pre-change state."""
    root = tmp_path / "demo-repo"
    write_repo(root, OLD_FILES)
    return root


@pytest.fixture()
def new_demo_repo(demo_repo: Path) -> Path:
    """The same checkout with the diff already applied on disk."""
    apply_new_state(demo_repo)
    return demo_repo


@pytest.fixture()
def diff_text() -> str:
    return demo_diff()


def register_repository(session: session_module.Session, root: Path, **overrides: object) -> object:
    """Insert a local repository row for ``root``."""
    from patchlens.db.models.repository import Repository

    defaults: dict[str, object] = {
        "provider": "local",
        "owner": "patchlens",
        "name": "fixture",
        "full_name": "patchlens/fixture",
        "default_branch": "main",
        "local_path": str(root),
    }
    defaults.update(overrides)
    repository = Repository(**defaults)  # type: ignore[arg-type]
    session.add(repository)
    session.flush()
    return repository


def index_repository(
    session: session_module.Session,
    repository: object,
    root: Path,
    settings: Settings,
    *,
    commit_sha: str = "commit-1",
    **kwargs: object,
) -> IndexOutcome:
    """Run the real indexer with the configured embedding provider."""
    indexer = RepositoryIndexer(
        settings=settings,
        embedding_provider=build_embedding_provider(settings),
    )
    return indexer.index(
        session,
        repository=repository,  # type: ignore[arg-type]
        root=root,
        commit_sha=commit_sha,
        **kwargs,  # type: ignore[arg-type]
    )


__all__ = [
    "NEW_FILES",
    "OLD_FILES",
    "apply_new_state",
    "client",
    "demo_diff",
    "demo_repo",
    "diff_text",
    "index_repository",
    "lifespan_context",
    "new_demo_repo",
    "register_repository",
    "write_repo",
]
