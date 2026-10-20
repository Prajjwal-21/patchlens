"""Unit tests for configuration loading and redacted summaries.

Configuration is the contract between the deployment and the code, so these tests
pin down the properties the project promises: it boots with no external services,
secrets never appear in summaries, and production mode refuses unsafe defaults.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from patchlens.core.config import Settings, get_settings, reload_settings


class TestDefaults:
    def test_development_boot_needs_no_external_service(self) -> None:
        settings = Settings(environment="test")
        assert settings.uses_postgres is False
        assert settings.database_url.startswith("sqlite")
        assert settings.llm_provider == "mock"
        assert settings.embedding_provider == "hashing"
        assert settings.test_execution_mode == "disabled"
        assert settings.reranker == "heuristic"
        assert settings.retrieval_strategy == "hybrid"

    def test_mock_provider_is_never_presented_as_real(self) -> None:
        settings = Settings(environment="test")
        summary = settings.llm_summary()
        assert summary["is_mock"] is True
        assert summary["model"] == "deterministic-mock"

    def test_unknown_environment_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            Settings(environment="staging")

    def test_short_form_postgres_url_is_normalised(self) -> None:
        settings = Settings(environment="test", database_url="postgresql://user:pw@localhost/db")
        assert settings.database_url == "postgresql+psycopg://user:pw@localhost/db"
        assert settings.uses_postgres is True


class TestSummaryRedaction:
    def test_summary_never_contains_secret_values(self) -> None:
        settings = Settings(
            environment="test",
            api_auth_token="super-secret-api-token",
            llm_api_key="sk-live-1234567890",  # type: ignore[arg-type]
            github_webhook_secret="hook-secret",
            database_url="postgresql+psycopg://user:very-secret-password@db:5432/patchlens",
        )
        blob = repr(settings.summary())
        for secret in ("super-secret-api-token", "sk-live-1234567890", "hook-secret", "very-secret-password"):
            assert secret not in blob
        assert settings.summary()["auth_required"] is True
        assert settings.summary()["github"]["webhook_secret_configured"] is True
        assert settings.summary()["github"]["fully_configured"] is False

    def test_llm_key_presence_is_reported_without_the_value(self) -> None:
        settings = Settings(environment="test", llm_api_key="sk-live-1234567890")  # type: ignore[arg-type]
        assert settings.llm_configured is True
        assert settings.llm_summary()["api_key_configured"] is True
        assert "sk-live" not in repr(settings.llm_summary())

    def test_secret_values_are_wrapped_in_secret_str(self) -> None:
        settings = Settings(environment="test", api_auth_token="token-value")
        assert settings.api_auth_token is not None
        assert "token-value" not in repr(settings.api_auth_token)


class TestProductionGuards:
    def test_production_flags_host_test_execution_as_unavailable(self) -> None:
        settings = Settings(environment="production", test_execution_mode="subprocess")
        assert settings.test_execution_mode == "disabled", "host execution must never be a silent default"
        assert settings.is_production is True

    def test_production_keeps_docker_execution_available(self) -> None:
        settings = Settings(environment="production", test_execution_mode="docker")
        assert settings.test_execution_mode == "docker"

    def test_subprocess_mode_without_acknowledgement_explains_itself(self) -> None:
        settings = Settings(environment="test", test_execution_mode="subprocess")
        summary = settings.test_execution_summary()
        assert summary["mode"] == "subprocess"
        assert summary["unavailable_reason"] and "ALLOW_LOCAL_TEST_EXECUTION" in summary["unavailable_reason"]

    def test_subprocess_mode_with_acknowledgement_has_no_blocker(self) -> None:
        settings = Settings(
            environment="test", test_execution_mode="subprocess", allow_local_test_execution=True
        )
        assert settings.test_execution_summary()["unavailable_reason"] is None


class TestEnvExample:
    def test_env_example_covers_every_setting_without_leaking_machine_state(self) -> None:
        from patchlens.cli import _env_example

        text = _env_example()
        for name in Settings.model_fields:
            assert f"\n{name.upper()}=" in text, f"{name} is missing from .env.example"

    def test_env_example_is_portable_and_secret_free(self) -> None:
        from patchlens.cli import _env_example

        text = _env_example()
        assert "/Users/" not in text and "/home/" not in text
        lines = {
            line.split("=", 1)[0]: line.split("=", 1)[1]
            for line in text.splitlines()
            if "=" in line and not line.startswith("#")
        }
        for secret in (
            "API_AUTH_TOKEN",
            "LLM_API_KEY",
            "EMBEDDING_API_KEY",
            "GITHUB_WEBHOOK_SECRET",
            "GITHUB_PRIVATE_KEY",
        ):
            assert lines[secret] == "", f"{secret} must ship empty"
        assert lines["DATABASE_URL"].startswith("sqlite+pysqlite:///./")


class TestCsvAndValidation:
    def test_csv_settings_accept_both_strings_and_lists(self) -> None:
        settings = Settings(environment="test", excluded_dir_names="node_modules, vendor ,dist")
        assert settings.excluded_dir_names == ["node_modules", "vendor", "dist"]

    def test_log_level_is_validated_case_insensitively(self) -> None:
        assert Settings(environment="test", log_level="debug").log_level == "DEBUG"
        with pytest.raises(ValidationError):
            Settings(environment="test", log_level="chatty")

    def test_numeric_bounds_are_enforced(self) -> None:
        with pytest.raises(ValidationError):
            Settings(environment="test", review_max_findings=0)
        with pytest.raises(ValidationError):
            Settings(environment="test", worker_concurrency=0)

    def test_singleton_is_cached_until_reloaded(self) -> None:
        first = get_settings()
        assert get_settings() is first
        assert reload_settings() is not first
