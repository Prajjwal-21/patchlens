"""Embedding provider interface.

Two implementations ship with PatchLens, and the difference between them is
surfaced everywhere it matters (``is_neural`` on every stored vector, and in the
provider summary shown by the dashboard):

* :class:`~patchlens.services.embedding.hashing.HashingEmbeddingProvider` -
  deterministic feature hashing, runs offline in milliseconds, **not** a neural
  model. With it, "semantic" retrieval is a bag-of-words cosine baseline.
* :class:`~patchlens.services.embedding.openai_compatible.OpenAICompatibleEmbeddingProvider`
  - real embeddings through any OpenAI-compatible ``/embeddings`` endpoint.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from patchlens.core.errors import ProviderError


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    provider: str
    model: str
    dim: int
    is_neural: bool
    token_estimate: int = 0


class EmbeddingProvider(ABC):
    """Abstract embedding backend."""

    name: str = "unknown"
    model: str = "unknown"
    is_neural: bool = False

    def __init__(self, *, dim: int) -> None:
        if dim <= 0:
            raise ValueError("embedding dimension must be positive")
        self.dim = dim

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatch:
        """Embed a batch of documents (indexing path)."""

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query (retrieval path)."""
        batch = self.embed_documents([text])
        if not batch.vectors:
            raise ProviderError("embedding provider returned no vectors")
        return batch.vectors[0]

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "model": self.model,
            "dim": self.dim,
            "is_neural": self.is_neural,
        }

    # ------------------------------------------------------------------ helpers
    def _validate(self, vectors: list[list[float]], expected: int) -> None:
        if len(vectors) != expected:
            raise ProviderError(
                f"embedding provider returned {len(vectors)} vectors for {expected} inputs",
                details={"provider": self.name},
            )
        for vector in vectors:
            if len(vector) != self.dim:
                raise ProviderError(
                    f"embedding dimension mismatch: expected {self.dim}, got {len(vector)}",
                    details={"provider": self.name, "model": self.model},
                )


__all__ = ["EmbeddingBatch", "EmbeddingProvider"]
