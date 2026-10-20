"""Configuration for PatchLens.

All knobs are environment variables. The application must be able to boot in a
documented development mode with *no* external services and *no* paid API
credentials, so every external dependency below has a working local default.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LLMProviderName = Literal["mock", "openai_compatible"]
EmbeddingProviderName = Literal["hashing", "openai_compatible"]
RetrievalStrategyName = Literal["static", "bm25", "semantic", "hybrid", "hybrid_rerank"]
RerankerName = Literal["none", "heuristic"]
TestExecutionMode = Literal["disabled", "subprocess", "docker"]

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _default_database_url() -> str:
    """Zero-dependency default: a SQLite file under ``backend/var/``.

    PostgreSQL + pgvector is the intended production backend and is selected by
    setting ``DATABASE_URL`` (see ``.env.example`` and ``docker-compose.yml``).
    """
    var_dir = _REPO_ROOT / "backend" / "var"
    return f"sqlite+pysqlite:///{var_dir / 'patchlens.db'}"


class Settings(BaseSettings):
    """Application settings, loaded from the environment (and ``.env``)."""

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- general
    app_name: str = "PatchLens"
    environment: Environment = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False
    api_docs_enabled: bool = True
    #: Bearer token required for dashboard/mutating API calls when set.
    #: When unset, the API is treated as a trusted local development instance.
    api_auth_token: SecretStr | None = None
    #: Create missing tables at startup. A development/test convenience; in
    #: production the schema is owned by ``alembic upgrade head`` and this is ignored.
    auto_create_schema: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    max_request_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    max_webhook_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    request_timeout_seconds: float = Field(default=60.0, gt=0)

    # ------------------------------------------------------------- persistence
    database_url: str = Field(default_factory=_default_database_url)
    database_echo: bool = False
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_statement_timeout_ms: int = Field(default=30_000, ge=0)
    #: Optional Redis URL. Enables low-latency job wake-ups; never required for
    #: correctness because the durable job queue lives in the database.
    redis_url: str | None = None

    # ------------------------------------------------------------------- LLM
    llm_provider: LLMProviderName = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = Field(default=45.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=6)
    llm_max_output_tokens: int = Field(default=2048, ge=128, le=32_000)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    #: Hard cap on characters of repository context handed to the model. Source
    #: code is untrusted input; this bounds prompt size and blast radius.
    llm_max_context_chars: int = Field(default=24_000, ge=2_000, le=400_000)
    #: Informational: USD per 1M tokens, used for cost reporting when the
    #: provider returns usage. Set to 0 to report "UNKNOWN" instead of a guess.
    llm_cost_per_mtok_input_usd: float = Field(default=0.0, ge=0)
    llm_cost_per_mtok_output_usd: float = Field(default=0.0, ge=0)

    # ------------------------------------------------------------- embeddings
    embedding_provider: EmbeddingProviderName = "hashing"
    embedding_model: str = "hashing-ngram-v2"
    embedding_dim: int = Field(default=512, ge=32, le=4096)
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: SecretStr | None = None
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)
    embedding_batch_size: int = Field(default=96, ge=1, le=2048)
    #: Portable semantic search performs an in-process scan. Guard against
    #: accidentally scanning huge corpora on the non-pgvector backend.
    semantic_scan_limit: int = Field(default=50_000, ge=100)

    # -------------------------------------------------------------- retrieval
    retrieval_strategy: RetrievalStrategyName = "hybrid"
    retrieval_top_k: int = Field(default=5, ge=1, le=100)
    retrieval_candidate_k: int = Field(default=30, ge=1, le=500)
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)
    retrieval_bm25_k1: float = Field(default=1.5, gt=0)
    retrieval_bm25_b: float = Field(default=0.75, ge=0, le=1)
    retrieval_hybrid_weight_bm25: float = Field(default=1.0, gt=0)
    retrieval_hybrid_weight_semantic: float = Field(default=1.0, gt=0)
    retrieval_max_results_per_file: int = Field(default=3, ge=1, le=20)
    reranker: RerankerName = "heuristic"
    retrieval_cache_ttl_seconds: int = Field(default=300, ge=0)

    # --------------------------------------------------------------- indexing
    index_max_file_bytes: int = Field(default=512 * 1024, ge=1024)
    index_max_files: int = Field(default=20_000, ge=1)
    index_max_symbols: int = Field(default=120_000, ge=1)
    index_embedding_batch_size: int = Field(default=64, ge=1, le=512)
    excluded_dir_names: list[str] = Field(
        default_factory=lambda: [
            ".git",
            ".hg",
            ".svn",
            ".venv",
            "venv",
            "env",
            "__pycache__",
            "node_modules",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".tox",
            ".nox",
            "build",
            "dist",
            ".eggs",
            "site-packages",
            ".idea",
            ".vscode",
            "migrations",
            "vendor",
            "third_party",
        ]
    )
    excluded_file_patterns: list[str] = Field(
        default_factory=lambda: [
            "*.pyc",
            "*.pyo",
            "*.so",
            "*.dylib",
            "*.dll",
            "*.min.js",
            "*.lock",
            "*.pdf",
            "*.png",
            "*.jpg",
            "*.jpeg",
            "*.gif",
            "*.ico",
            "*.zip",
            "*.tar",
            "*.gz",
            "*.whl",
            "*.pem",
            "*.key",
            "*.p12",
            "*.pfx",
            "*.crt",
            "*.jks",
            "*.keystore",
            "id_rsa*",
            ".env",
            ".env.*",
            "*.env",
            "secrets.*",
            "*credentials*.json",
        ]
    )
    max_index_snapshot_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)

    # ----------------------------------------------------------------- worker
    worker_id: str | None = None
    worker_concurrency: int = Field(default=2, ge=1, le=32)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    worker_lease_seconds: int = Field(default=900, ge=30)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_backoff_base_seconds: float = Field(default=5.0, gt=0)
    job_backoff_max_seconds: float = Field(default=300.0, gt=0)
    job_timeout_seconds: int = Field(default=900, ge=10)
    review_max_findings: int = Field(default=40, ge=1, le=500)
    review_max_hunks: int = Field(default=300, ge=1, le=5000)

    # ------------------------------------------------------------ test runner
    test_execution_mode: TestExecutionMode = "disabled"
    #: Explicit acknowledgement required to execute code on the host machine.
    allow_local_test_execution: bool = False
    test_execution_timeout_seconds: int = Field(default=120, ge=5, le=1800)
    test_execution_memory_mb: int = Field(default=1024, ge=128, le=8192)
    test_execution_cpu_seconds: int = Field(default=60, ge=5, le=1800)
    test_execution_max_output_bytes: int = Field(default=256 * 1024, ge=1024)
    test_execution_docker_image: str = "python:3.11-slim"
    test_execution_network_enabled: bool = False

    # --------------------------------------------------------------- security
    #: Any file path outside a repository root is rejected before touching disk.
    allowed_file_extensions: list[str] = Field(default_factory=lambda: [".py", ".pyi"])
    #: Automatically enable when ENVIRONMENT=production via validator below.
    trust_proxy_headers: bool = False

    # --------------------------------------------------------------- GitHub
    github_app_id: int | None = None
    github_app_slug: str = "patchlens-dev"
    github_app_client_id: str | None = None
    github_private_key: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None
    github_api_base_url: str = "https://api.github.com"
    #: Safety switch: even with credentials present, PatchLens will not publish
    #: comments to GitHub until this is explicitly set to true.
    github_publish_enabled: bool = False
    github_comment_severities: list[str] = Field(default_factory=lambda: ["critical", "high"])
    github_max_comments_per_review: int = Field(default=10, ge=1, le=50)
    github_require_verified_anchors: bool = True

    # ------------------------------------------------------------------- demo
    demo_mode_enabled: bool = False
    demo_seed_path: str | None = None

    # -------------------------------------------------------------- validators
    @field_validator(
        "cors_origins",
        "excluded_dir_names",
        "excluded_file_patterns",
        "allowed_file_extensions",
        "github_comment_severities",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unsupported log level: {value!r}")
        return level

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        # Alembic/SQLAlchemy use ``postgresql+psycopg://``; accept the short form.
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

    @model_validator(mode="after")
    def _apply_production_defaults(self) -> Settings:
        if self.environment == "production":
            # Never allow host execution of untrusted code in production unless
            # explicitly acknowledged *and* mode is not "disabled".
            if self.test_execution_mode == "subprocess" and not self.allow_local_test_execution:
                self.test_execution_mode = "disabled"
            if self.api_auth_token is None and not os.environ.get("PATCHLENS_ALLOW_UNAUTHENTICATED_API"):
                self.trust_proxy_headers = True
        return self

    # ----------------------------------------------------------------- helpers
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @property
    def uses_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def uses_pgvector(self) -> bool:
        return self.uses_postgres

    @property
    def llm_is_mock(self) -> bool:
        return self.llm_provider == "mock"

    @property
    def embedding_is_neural(self) -> bool:
        return self.embedding_provider == "openai_compatible"

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "openai_compatible":
            return self.llm_api_key is not None
        return True

    @property
    def github_configured(self) -> bool:
        return bool(self.github_app_id and self.github_private_key and self.github_webhook_secret)

    def var_dir(self) -> Path:
        path = _REPO_ROOT / "backend" / "var"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def llm_summary(self) -> dict[str, object]:
        """Redacted provider summary, safe to expose over HTTP or in logs."""
        return {
            "provider": self.llm_provider,
            "model": self.llm_model if self.llm_provider != "mock" else "deterministic-mock",
            "base_url": self.llm_base_url,
            "api_key_configured": self.llm_api_key is not None,
            "is_mock": self.llm_is_mock,
            "timeout_seconds": self.llm_timeout_seconds,
            "max_output_tokens": self.llm_max_output_tokens,
        }

    def embedding_summary(self) -> dict[str, object]:
        return {
            "provider": self.embedding_provider,
            "model": self.embedding_model,
            "dim": self.embedding_dim,
            "base_url": self.embedding_base_url if self.embedding_is_neural else None,
            "api_key_configured": self.embedding_api_key is not None,
            "is_neural": self.embedding_is_neural,
        }

    def github_summary(self) -> dict[str, object]:
        return {
            "app_id_configured": self.github_app_id is not None,
            "app_slug": self.github_app_slug,
            "private_key_configured": self.github_private_key is not None,
            "webhook_secret_configured": self.github_webhook_secret is not None,
            "fully_configured": self.github_configured,
            "publish_enabled": self.github_publish_enabled,
            "api_base_url": self.github_api_base_url,
        }

    def test_execution_summary(self) -> dict[str, object]:
        effective = self.test_execution_mode
        reason: str | None = None
        if effective == "subprocess" and not self.allow_local_test_execution:
            reason = (
                "set ALLOW_LOCAL_TEST_EXECUTION=true to acknowledge that subprocess mode is not a sandbox"
            )
        return {
            "mode": effective,
            "allow_local_test_execution": self.allow_local_test_execution,
            "timeout_seconds": self.test_execution_timeout_seconds,
            "memory_mb": self.test_execution_memory_mb,
            "network_enabled": self.test_execution_network_enabled,
            "docker_image": self.test_execution_docker_image,
            "unavailable_reason": reason,
        }

    def summary(self) -> dict[str, object]:
        """One redacted line of runtime configuration for startup logging."""
        return {
            "environment": self.environment,
            "database": "postgresql" if self.uses_postgres else "sqlite",
            "llm": self.llm_summary(),
            "embedding": self.embedding_summary(),
            "retrieval_strategy": self.retrieval_strategy,
            "reranker": self.reranker,
            "worker_concurrency": self.worker_concurrency,
            "test_execution": self.test_execution_summary(),
            "github": self.github_summary(),
            "auth_required": self.api_auth_token is not None,
            "demo_mode": self.demo_mode_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()


def reload_settings() -> Settings:
    """Clear the cache and reload (used by tests and the CLI)."""
    get_settings.cache_clear()
    return get_settings()


# Convenience alias used across modules.
settings = get_settings()
