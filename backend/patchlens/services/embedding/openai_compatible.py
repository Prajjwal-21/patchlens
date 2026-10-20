"""OpenAI-compatible embedding provider (``POST /embeddings``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from patchlens.core.errors import ProviderError
from patchlens.core.logging import get_logger, log_extra
from patchlens.services.embedding.base import EmbeddingBatch, EmbeddingProvider
from patchlens.services.providers.http import HttpClient

logger = get_logger(__name__)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Embeddings from any service exposing the OpenAI ``/embeddings`` shape."""

    name = "openai_compatible"
    is_neural = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        dim: int,
        timeout_seconds: float = 30.0,
        batch_size: int = 96,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(dim=dim)
        if not api_key:
            raise ProviderError(
                "embedding provider 'openai_compatible' requires EMBEDDING_API_KEY",
                details={"provider": self.name},
            )
        self.model = model
        self.batch_size = max(1, batch_size)
        self._http = HttpClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            client=client,
        )

    def close(self) -> None:
        self._http.close()

    def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatch:
        inputs = [text if text.strip() else " " for text in texts]
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(inputs), self.batch_size):
            chunk = list(inputs[start : start + self.batch_size])
            payload: dict[str, Any] = {"model": self.model, "input": chunk}
            response = self._http.post_json("/embeddings", payload, provider="embeddings")
            data = response.get("data")
            if not isinstance(data, list) or len(data) != len(chunk):
                raise ProviderError(
                    "embedding provider returned an unexpected payload",
                    details={"provider": self.name, "received": len(data) if isinstance(data, list) else -1},
                )
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            for item in ordered:
                vector = item.get("embedding")
                if not isinstance(vector, list) or not vector:
                    raise ProviderError("embedding provider returned an empty vector")
                vectors.append([float(component) for component in vector])
            usage = response.get("usage")
            if isinstance(usage, dict):
                tokens += int(usage.get("total_tokens") or usage.get("prompt_tokens") or 0)

        self._validate(vectors, len(inputs))
        logger.debug(
            "embedded batch",
            extra=log_extra(count=len(vectors), provider=self.name, model=self.model, tokens=tokens),
        )
        return EmbeddingBatch(
            vectors=vectors,
            provider=self.name,
            model=self.model,
            dim=self.dim,
            is_neural=True,
            token_estimate=tokens,
        )


__all__ = ["OpenAICompatibleEmbeddingProvider"]
