"""Provider factories.

Central place where configuration becomes a concrete provider, so no other module
needs to know which backend is active. Selecting providers is a config change,
never a code change.
"""

from __future__ import annotations

from patchlens.core.config import Settings, get_settings
from patchlens.core.errors import ProviderNotConfiguredError
from patchlens.core.logging import get_logger, log_extra
from patchlens.services.embedding.base import EmbeddingProvider
from patchlens.services.embedding.hashing import HashingEmbeddingProvider
from patchlens.services.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from patchlens.services.llm.base import LLMProvider
from patchlens.services.llm.mock import MockLLMProvider
from patchlens.services.llm.openai_compatible import OpenAICompatibleLLMProvider

logger = get_logger(__name__)


def build_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Instantiate the configured LLM provider."""
    settings = settings or get_settings()
    if settings.llm_provider == "mock":
        return MockLLMProvider()
    if settings.llm_provider == "openai_compatible":
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        if not api_key:
            raise ProviderNotConfiguredError(
                "LLM_PROVIDER=openai_compatible requires LLM_API_KEY",
                details={"provider": "openai_compatible", "model": settings.llm_model},
            )
        logger.info(
            "using openai-compatible LLM provider",
            extra=log_extra(base_url=settings.llm_base_url, model=settings.llm_model),
        )
        return OpenAICompatibleLLMProvider(
            base_url=settings.llm_base_url,
            api_key=api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_output_tokens=settings.llm_max_output_tokens,
            temperature=settings.llm_temperature,
            cost_per_mtok_input_usd=settings.llm_cost_per_mtok_input_usd,
            cost_per_mtok_output_usd=settings.llm_cost_per_mtok_output_usd,
        )
    raise ProviderNotConfiguredError(f"unknown LLM provider {settings.llm_provider!r}")


def build_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """Instantiate the configured embedding provider."""
    settings = settings or get_settings()
    if settings.embedding_provider == "hashing":
        return HashingEmbeddingProvider(dim=settings.embedding_dim, model=settings.embedding_model)
    if settings.embedding_provider == "openai_compatible":
        api_key = settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else None
        if not api_key:
            raise ProviderNotConfiguredError(
                "EMBEDDING_PROVIDER=openai_compatible requires EMBEDDING_API_KEY",
                details={"provider": "openai_compatible", "model": settings.embedding_model},
            )
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=api_key,
            model=settings.embedding_model,
            dim=settings.embedding_dim,
            timeout_seconds=settings.embedding_timeout_seconds,
            batch_size=settings.embedding_batch_size,
            max_retries=settings.llm_max_retries,
        )
    raise ProviderNotConfiguredError(f"unknown embedding provider {settings.embedding_provider!r}")


__all__ = ["build_embedding_provider", "build_llm_provider"]
