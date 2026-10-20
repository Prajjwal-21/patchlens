"""Integration tests for repository indexing.

These run the real walker, the real AST parser and a real SQLite database against
the on-disk fixture repository. They pin down the incremental behaviours the
product promises: idempotent re-indexing, excluded paths, parse-error reporting
and cross-version embedding reuse.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from patchlens.db.models.repository import CodeEmbedding, CodeSymbol, RepositoryVersion
from tests.conftest import index_repository, register_repository
from tests.helpers import apply_new_state


class TestInitialIndex:
    def test_indexes_python_files_and_symbols(self, session, app_settings, demo_repo: Path) -> None:
        repository = register_repository(session, demo_repo)
        outcome = index_repository(session, repository, demo_repo, app_settings)
        session.commit()

        assert outcome.status == "indexed"
        assert outcome.commit_sha == "commit-1"
        assert outcome.files >= 4
        assert outcome.symbols >= 5
        assert outcome.embeddings > 0
        assert outcome.embedding_provider == "hashing"
        assert outcome.embedding_model == app_settings.embedding_model
        assert outcome.embedding_dim == app_settings.embedding_dim
        assert outcome.up_to_date is False

        symbols = session.scalars(
            select(CodeSymbol).where(CodeSymbol.repository_version_id == outcome.version_id)
        ).all()
        names = {symbol.qualified_name for symbol in symbols}
        assert "add" in names and "divide" in names
        assert any(name.startswith("test_") for name in names)

    def test_excludes_venvs_secrets_and_binary_artefacts(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        outcome = index_repository(session, repository, demo_repo, app_settings)
        session.commit()

        paths = set(
            session.scalars(
                select(CodeSymbol.path).where(CodeSymbol.repository_version_id == outcome.version_id)
            )
        )
        assert ".env" not in paths
        assert not any(path.startswith(".venv/") for path in paths)
        assert not any("__pycache__" in path for path in paths)
        assert "README.md" not in paths, "only configured source extensions are indexed"

    def test_records_parse_errors_without_aborting_the_index(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        outcome = index_repository(session, repository, demo_repo, app_settings)
        session.commit()
        assert outcome.parse_errors, "demoapp/broken.py contains a syntax error"
        assert any("broken.py" in str(error) for error in outcome.parse_errors)

    def test_every_stored_vector_matches_the_configured_dimension(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        outcome = index_repository(session, repository, demo_repo, app_settings)
        session.commit()
        vectors = session.scalars(
            select(CodeEmbedding).where(CodeEmbedding.repository_version_id == outcome.version_id)
        ).all()
        assert vectors
        assert all(len(vector.embedding) == app_settings.embedding_dim for vector in vectors)
        assert all(vector.is_neural is False for vector in vectors)

    def test_version_is_recorded_with_the_commit_it_came_from(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        outcome = index_repository(
            session, repository, demo_repo, app_settings, commit_sha="abc123", branch="main"
        )
        session.commit()
        version = session.get(RepositoryVersion, outcome.version_id)
        assert version is not None
        assert version.commit_sha == "abc123"
        assert version.branch == "main"
        assert version.status == "indexed"
        assert version.stats["tree_hash"]
        assert version.file_count == outcome.files


class TestIncrementalIndexing:
    def test_second_run_on_the_same_commit_is_up_to_date(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        first = index_repository(session, repository, demo_repo, app_settings)
        session.commit()
        second = index_repository(session, repository, demo_repo, app_settings)
        session.commit()

        assert second.up_to_date is True
        assert second.version_id == first.version_id
        versions = session.scalar(
            select(func.count())
            .select_from(RepositoryVersion)
            .where(RepositoryVersion.repository_id == repository.id)
        )
        assert versions == 1

    def test_new_commit_with_unchanged_tree_reuses_the_embeddings(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        first = index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        second = index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-2")
        session.commit()

        assert second.version_id != first.version_id
        assert second.up_to_date is False
        assert second.reused_embeddings > 0
        assert second.symbols == first.symbols

    def test_changed_tree_reindexes_and_keeps_unchanged_vectors_cached(
        self, session, app_settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        first = index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-1")
        session.commit()
        apply_new_state(demo_repo)
        second = index_repository(session, repository, demo_repo, app_settings, commit_sha="commit-2")
        session.commit()

        assert second.version_id != first.version_id
        assert second.files >= first.files
        assert second.reused_embeddings > 0, "legacy.py and its vectors did not change"
        # The new version must not inherit stale rows from the previous snapshot.
        stale = session.scalar(
            select(func.count())
            .select_from(CodeSymbol)
            .where(CodeSymbol.repository_version_id == second.version_id, CodeSymbol.qualified_name == "mean")
        )
        assert stale == 1

    def test_force_reindex_rebuilds_the_same_version(self, session, app_settings, demo_repo: Path) -> None:
        repository = register_repository(session, demo_repo)
        first = index_repository(session, repository, demo_repo, app_settings)
        session.commit()
        second = index_repository(session, repository, demo_repo, app_settings, force=True)
        session.commit()
        assert second.up_to_date is False
        assert second.version_id == first.version_id

    def test_too_large_files_are_skipped_with_a_reason(
        self, session, app_settings, demo_repo: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(app_settings, "index_max_file_bytes", 40)
        repository = register_repository(session, demo_repo)
        outcome = index_repository(session, repository, demo_repo, app_settings)
        session.commit()
        assert outcome.skipped_reasons.get("file_too_large", 0) >= 1


class TestLimitsAndEmptyRepositories:
    def test_file_count_limit_stops_the_walk(self, session, app_settings, demo_repo: Path) -> None:
        app_settings.index_max_files = 2
        repository = register_repository(session, demo_repo)
        outcome = index_repository(session, repository, demo_repo, app_settings)
        session.commit()
        assert outcome.files <= 2

    def test_empty_directory_indexes_cleanly(self, session, app_settings, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        repository = register_repository(session, empty, full_name="patchlens/empty")
        outcome = index_repository(session, repository, empty, app_settings)
        session.commit()
        assert outcome.status == "indexed"
        assert outcome.symbols == 0
        assert outcome.embeddings == 0
        assert outcome.version_id
