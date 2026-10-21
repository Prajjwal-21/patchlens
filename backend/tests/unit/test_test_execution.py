"""Execution tests: real subprocess outcomes, disabled states and Docker argv."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from patchlens.core.config import Settings
from patchlens.db.models.testing import TestCandidate as CandidateModel
from patchlens.services.testing.execution import (
    _run_process,
    build_docker_command,
    execute_candidate,
    resolve_sandbox,
)
from tests.conftest import register_repository


def make_candidate(
    session,
    repository,
    *,
    source: str = "def test_ok() -> None:\n    assert True\n",
    path: str = "tests/test_ok.py",
    **overrides,
) -> CandidateModel:
    candidate = CandidateModel(
        repository_id=repository.id,
        target_path="demoapp/calculator.py",
        target_symbol="add",
        suggested_test_path=path,
        source=source,
        provider="stub",
        model="stub-1",
        is_mock_provider=False,
        status="generated",
        **overrides,
    )
    session.add(candidate)
    session.commit()
    return candidate


def subprocess_settings(settings: Settings, **overrides) -> Settings:
    values = {
        "test_execution_mode": "subprocess",
        "allow_local_test_execution": True,
        "test_execution_timeout_seconds": 15,
        "test_execution_memory_mb": 1024,
    }
    values.update(overrides)
    return settings.model_copy(update=values)


class TestSandboxResolution:
    def test_disabled_explains_why(self, app_settings: Settings) -> None:
        status = resolve_sandbox(app_settings)
        assert status.available is False
        assert status.effective_mode == "disabled"
        assert status.reason and "disabled" in status.reason
        assert status.limits["timeout_seconds"] == app_settings.test_execution_timeout_seconds

    def test_subprocess_requires_explicit_acknowledgement(self, app_settings: Settings) -> None:
        settings = app_settings.model_copy(
            update={"test_execution_mode": "subprocess", "allow_local_test_execution": False}
        )
        status = resolve_sandbox(settings)
        assert status.available is False
        assert status.effective_mode == "disabled"
        assert "ALLOW_LOCAL_TEST_EXECUTION" in (status.reason or "")

    def test_subprocess_available_is_labelled_not_a_boundary(self, app_settings: Settings) -> None:
        status = resolve_sandbox(subprocess_settings(app_settings))
        assert status.available is True
        assert status.effective_mode == "subprocess"
        assert "NOT a security boundary" in status.detail

    def test_docker_without_cli_is_unavailable(
        self, app_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("patchlens.services.testing.execution.shutil.which", lambda _: None)
        settings = app_settings.model_copy(update={"test_execution_mode": "docker"})
        status = resolve_sandbox(settings)
        assert status.available is False
        assert "docker CLI" in (status.reason or "")


class TestExecution:
    def test_disabled_does_not_record_a_run(self, session, app_settings: Settings, demo_repo: Path) -> None:
        repository = register_repository(session, demo_repo)
        candidate = make_candidate(
            session,
            repository,
            stdout_tail="old output",
            executed_at=None,
            exit_code=1,
        )
        outcome = execute_candidate(session, candidate, app_settings)
        assert outcome.executed is False
        assert candidate.status == "execution_disabled"
        assert candidate.executed_at is None
        assert candidate.exit_code is None
        assert candidate.stdout_tail is None
        assert candidate.execution_disabled_reason
        session.rollback()

    def test_subprocess_pass_records_real_output(
        self, session, app_settings: Settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        candidate = make_candidate(session, repository)
        outcome = execute_candidate(session, candidate, subprocess_settings(app_settings))
        session.commit()
        assert outcome.executed is True
        assert candidate.status == "passed"
        assert candidate.executed_at is not None
        assert candidate.exit_code == 0
        assert candidate.sandbox_mode == "subprocess"
        assert candidate.collected_tests == 1
        assert "1 passed" in (candidate.stdout_tail or "")
        assert candidate.output_truncated is False
        assert not (demo_repo / "tests" / "test_ok.py").exists()

    def test_subprocess_failure_is_failed_not_error(
        self, session, app_settings: Settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        candidate = make_candidate(
            session,
            repository,
            source="def test_bad() -> None:\n    assert 1 == 2\n",
        )
        execute_candidate(session, candidate, subprocess_settings(app_settings))
        session.commit()
        assert candidate.status == "failed"
        assert candidate.exit_code == 1
        assert candidate.executed_at is not None
        assert "failed" in (candidate.stdout_tail or "").lower()

    def test_subprocess_environment_does_not_leak_secrets(
        self,
        session,
        app_settings: Settings,
        demo_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PATCHLENS_TEST_SECRET_TOKEN", "must-not-leak")
        repository = register_repository(session, demo_repo)
        candidate = make_candidate(
            session,
            repository,
            source=(
                "import os\n\n"
                "def test_secret_is_absent() -> None:\n"
                "    assert 'PATCHLENS_TEST_SECRET_TOKEN' not in os.environ\n"
            ),
        )
        execute_candidate(session, candidate, subprocess_settings(app_settings))
        session.commit()
        assert candidate.status == "passed"

    def test_unsafe_stored_path_is_rejected_without_execution(
        self, session, app_settings: Settings, demo_repo: Path
    ) -> None:
        repository = register_repository(session, demo_repo)
        candidate = make_candidate(session, repository, path="../escape.py")
        outcome = execute_candidate(session, candidate, subprocess_settings(app_settings))
        session.commit()
        assert outcome.executed is False
        assert candidate.status == "error"
        assert candidate.executed_at is None
        assert "safe relative path" in (candidate.run_metadata or {}).get("error", "")

    def test_missing_checkout_is_not_reported_as_a_test_failure(
        self, session, app_settings: Settings, tmp_path: Path
    ) -> None:
        repository = register_repository(session, tmp_path / "missing")
        candidate = make_candidate(session, repository)
        outcome = execute_candidate(session, candidate, subprocess_settings(app_settings))
        session.commit()
        assert outcome.executed is False
        assert candidate.status == "error"
        assert candidate.executed_at is None
        assert "no local checkout" in (candidate.sandbox_detail or "")

    def test_timeout_kills_the_process_group(self) -> None:
        result = _run_process(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=None,
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=0.5,
            max_output_bytes=1024,
        )
        assert result.timed_out is True
        assert result.duration_ms < 5_000
        assert result.exit_code is not None

    def test_output_is_capped(self) -> None:
        result = _run_process(
            [sys.executable, "-c", "print('x' * 100000)"],
            cwd=None,
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=5,
            max_output_bytes=1024,
        )
        assert result.timed_out is False
        assert len(result.stdout_tail) <= 1024
        assert result.output_truncated is True


class TestDockerCommand:
    def test_container_is_networkless_read_only_and_limited(
        self, app_settings: Settings, tmp_path: Path
    ) -> None:
        settings = app_settings.model_copy(update={"test_execution_mode": "docker"})
        repo = tmp_path / "repo"
        repo.mkdir()
        tests = tmp_path / "tests"
        tests.mkdir()
        test_file = tests / "test_x.py"
        test_file.write_text("def test_x(): assert True\n")
        command = build_docker_command(
            settings=settings,
            repository_root=repo,
            tempdir=tests,
            test_file=test_file,
            name="patchlens-test-abc",
        )
        joined = " ".join(command)
        assert "--network none" in joined
        assert "--cap-drop ALL" in joined
        assert "--security-opt no-new-privileges" in joined
        assert "--read-only" in joined
        assert "--pids-limit 128" in joined
        assert "--memory 1024m" in joined
        assert f"{repo.resolve()}:/repo:ro" in joined
        assert f"{tests.resolve()}:/tests:ro" in joined
        assert "python -m pytest test_x.py" in joined

    def test_explicit_network_setting_is_visible_in_argv(
        self, app_settings: Settings, tmp_path: Path
    ) -> None:
        settings = app_settings.model_copy(
            update={"test_execution_mode": "docker", "test_execution_network_enabled": True}
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        tests = tmp_path / "tests"
        tests.mkdir()
        test_file = tests / "test_x.py"
        test_file.write_text("def test_x(): assert True\n")
        command = build_docker_command(
            settings=settings, repository_root=repo, tempdir=tests, test_file=test_file
        )
        assert command[command.index("--network") + 1] == "bridge"
